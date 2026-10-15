// Stain channels composited on the GPU.
//
// The server used to blend the visible channels into one PNG per tile, so every
// slider move re-blended and re-encoded every tile on screen and sent it again
// -- on a laptop, the same few cores the browser needs, and nothing changed on
// screen until the hand paused and the tiles came back. Now each channel's tile arrives
// once as raw uint16 and this shader does what stains.py composite_tile did:
// per channel clip((v - min) / (max - min)) * colour, summed, clipped, alpha =
// max(r, g, b). A slider move is a uniform update; nothing is fetched.
import { BitmapLayer, TileLayer } from 'deck.gl';
import { fetchTile } from './tileFetch';

// WebGL2 guarantees 16 fragment texture units; a Xenium focus stack has 4-5.
export const MAX_STAIN_CHANNELS = 8;

const SLOTS = Array.from({ length: MAX_STAIN_CHANNELS }, (_, k) => k);

const uniformBlock = `\
layout(std140) uniform stainUniforms {
${SLOTS.map((k) => `  vec4 color${k};`).join('\n')}
${SLOTS.map((k) => `  vec4 range${k};`).join('\n')}
} stain;
`;

const stainUniforms = {
  name: 'stain',
  vs: uniformBlock,
  fs: uniformBlock,
  uniformTypes: Object.fromEntries([
    ...SLOTS.map((k) => [`color${k}`, 'vec4<f32>']),
    ...SLOTS.map((k) => [`range${k}`, 'vec4<f32>']),
  ]),
};

const fs = `\
#version 300 es
#define SHADER_NAME stain-tile-fragment-shader
precision highp float;
precision highp int;
precision highp usampler2D;

${SLOTS.map((k) => `uniform usampler2D stainTex${k};`).join('\n')}

in vec2 vTexCoord;
in vec2 vTexPos;
out vec4 fragColor;

float texel(usampler2D t, ivec2 p) { return float(texelFetch(t, p, 0).r); }

// Integer textures can only be sampled nearest, so filter by hand -- the same
// four-tap bilinear the old PNG tiles got from the GPU, so zoomed-in pixels
// stay smooth rather than turning blocky.
float sampleRaw(usampler2D t, vec2 uv) {
  ivec2 size = textureSize(t, 0);
  ivec2 lim = size - 1;
  vec2 p = uv * vec2(size) - 0.5;
  vec2 f = fract(p);
  ivec2 i = ivec2(floor(p));
  float a = texel(t, clamp(i, ivec2(0), lim));
  float b = texel(t, clamp(i + ivec2(1, 0), ivec2(0), lim));
  float c = texel(t, clamp(i + ivec2(0, 1), ivec2(0), lim));
  float d = texel(t, clamp(i + ivec2(1, 1), ivec2(0), lim));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

vec3 channel(usampler2D t, vec2 uv, vec4 color, vec4 range) {
  if (color.a <= 0.0) return vec3(0.0);       // off / empty slot: no texture reads
  float n = clamp((sampleRaw(t, uv) - range.x) / max(range.y - range.x, 1.0), 0.0, 1.0);
  return n * color.rgb * color.a;
}

void main(void) {
  vec2 uv = vTexCoord;
  vec3 sum = vec3(0.0);
${SLOTS.map((k) => `  sum += channel(stainTex${k}, uv, stain.color${k}, stain.range${k});`).join('\n')}
  vec3 rgb = clamp(sum, 0.0, 1.0);
  fragColor = vec4(rgb, max(max(rgb.r, rgb.g), rgb.b) * layer.opacity);
  geometry.uv = uv;
  DECKGL_FILTER_COLOR(fragColor, geometry);
}
`;

const SAMPLER = {
  minFilter: 'nearest', magFilter: 'nearest',
  addressModeU: 'clamp-to-edge', addressModeV: 'clamp-to-edge',
};

/**
 * One tile of stain channels.
 *   channels: [{index, data: Uint16Array | null}] -- what was fetched, in slot order
 *   settings: {[channelIndex]: {color: [r,g,b], min, max, visible}}
 */
export class StainTileLayer extends BitmapLayer {
  static layerName = 'StainTileLayer';
  static defaultProps = {
    channels: { type: 'object', value: null },
    settings: { type: 'object', value: null },
    tileSize: 1024,
  };

  getShaders() {
    const shaders = super.getShaders();
    return { ...shaders, fs, modules: [...shaders.modules, stainUniforms] };
  }

  updateState(params) {
    super.updateState(params);
    const { props, oldProps } = params;
    if (props.channels !== oldProps.channels || !this.state.textures) {
      this._releaseTextures();
      const { device } = this.context;
      const T = props.tileSize;
      const textures = (props.channels || []).slice(0, MAX_STAIN_CHANNELS).map((ch) => {
        if (!ch.data || ch.data.length !== T * T) return null;
        const tex = device.createTexture({
          format: 'r16uint', width: T, height: T, mipLevels: 1, sampler: SAMPLER,
        });
        tex.writeData(ch.data);
        return tex;
      });
      if (!this.state.emptyTexture) {
        // every declared sampler needs something bound; unused slots get this
        const empty = device.createTexture({
          format: 'r16uint', width: 1, height: 1, mipLevels: 1, sampler: SAMPLER,
        });
        empty.writeData(new Uint16Array(1));
        this.setState({ emptyTexture: empty });
      }
      this.setState({ textures });
    }
  }

  _releaseTextures() {
    (this.state.textures || []).forEach((t) => t && t.destroy());
    this.state.textures = null;
  }

  finalizeState(context) {
    this._releaseTextures();
    if (this.state.emptyTexture) this.state.emptyTexture.destroy();
    super.finalizeState(context);
  }

  draw(opts) {
    const { model, coordinateConversion, bounds, textures, emptyTexture } = this.state;
    if (!model || !textures || !emptyTexture) return;
    if (opts.shaderModuleProps.picking.isActive) return;   // never pickable
    const { channels, settings } = this.props;
    const uniforms = {};
    const bindings = {};
    SLOTS.forEach((k) => {
      const ch = channels && channels[k];
      const tex = textures[k];
      const s = ch && settings && settings[ch.index];
      const on = Boolean(tex && s && s.visible);
      const c = (s && s.color) || [0, 0, 0];
      uniforms[`color${k}`] = on ? [c[0] / 255, c[1] / 255, c[2] / 255, 1] : [0, 0, 0, 0];
      uniforms[`range${k}`] = on ? [Number(s.min) || 0, Number(s.max) || 1, 0, 0] : [0, 1, 0, 0];
      bindings[`stainTex${k}`] = tex || emptyTexture;
    });
    model.shaderInputs.setProps({
      bitmap: {
        bounds, coordinateConversion,
        desaturate: 0, tintColor: [1, 1, 1], transparentColor: [0, 0, 0, 0],
      },
      stain: uniforms,
    });
    model.setBindings(bindings);
    model.draw(this.context.renderPass);
  }
}

// a tile's bounds never change, and a new array per render would make
// BitmapLayer rebuild its mesh on every slider tick
const boundsByTile = new WeakMap();
let warnedTooMany = false;
function tileBounds(tile) {
  let b = boundsByTile.get(tile);
  if (!b) {
    const bb = tile.boundingBox;
    b = [bb[0][0], bb[1][1], bb[1][0], bb[0][1]];   // flipY [left,bottom,right,top]
    boundsByTile.set(tile, b);
  }
  return b;
}

/**
 * The stain pyramid. `fetchIndexes` are the channels to download (the visible
 * ones -- a hidden channel costs nothing to fetch or decode); `settings` is
 * colour/contrast/visibility by channel index and never triggers a fetch.
 */
export function stainTileLayer({
  id, dsId, oKey, levels, tileSize, width, height, opacity, fetchIndexes, settings,
  cacheBytes,
}) {
  const maxLevel = levels - 1;
  if (fetchIndexes.length > MAX_STAIN_CHANNELS && !warnedTooMany) {
    warnedTooMany = true;
    // eslint-disable-next-line no-console
    console.warn(`[FiveAtlas] only the first ${MAX_STAIN_CHANNELS} visible stain channels are drawn`);
  }
  const want = fetchIndexes.slice(0, MAX_STAIN_CHANNELS);
  // Capped by tile COUNT, not maxCacheByteSize: toggling a channel changes the
  // fetch triggers, and deck's reloadAll() drops tiles without subtracting
  // their bytes while re-adding every reloaded tile's -- after a few toggles
  // the byte total sits above the cap for good and nothing stays cached.
  const perTile = Math.max(1, want.length) * tileSize * tileSize * 2;
  const maxCacheSize = Math.max(8, Math.floor(cacheBytes / perTile));
  return new TileLayer({
    id,
    tileSize,
    minZoom: -maxLevel,
    maxZoom: 0,
    extent: [0, 0, width, height],
    opacity,
    maxCacheSize,
    // read by renderSubLayers; a change regenerates the tiles' sublayers
    // (cheap -- new uniforms) without refetching anything
    stainSettings: settings,
    getTileData: ({ index, signal }) => {
      const backendZ = maxLevel + index.z;
      if (backendZ < 0 || backendZ > maxLevel || !want.length) return Promise.resolve(null);
      const expect = tileSize * tileSize * 2;
      const decode = async (blob) => {
        const buf = await blob.arrayBuffer();
        return buf.byteLength === expect ? new Uint16Array(buf) : null;
      };
      // fetchTile retries a failed read (see tileFetch.js). A channel that still
      // fails is left out of this tile rather than blanking the other channels.
      return Promise.all(want.map((ci) =>
        fetchTile(`/api/datasets/${dsId}/stains/raw/${ci}/${backendZ}/${index.x}/${index.y}?o=${oKey}`,
          { signal, decode })
          .catch((e) => { if (signal && signal.aborted) throw e; return null; })))
        .then((arrays) => {
          if (!arrays.some(Boolean)) return null;
          return {
            channels: want.map((ci, k) => ({ index: ci, data: arrays[k] })),
            // deck reads this for its stats; the cap above is by count
            byteLength: arrays.reduce((n, a) => n + (a ? a.byteLength : 0), 0),
          };
        });
    },
    renderSubLayers: (props) => {
      if (!props.data) return null;
      return new StainTileLayer(props, {
        data: null,
        image: null,
        channels: props.data.channels,
        settings: props.stainSettings,
        tileSize,
        bounds: tileBounds(props.tile),
      });
    },
    updateTriggers: { getTileData: [dsId, oKey, want.join(',')] },
  });
}
