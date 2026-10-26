// The morphology (DAPI reference) image, windowed on the GPU.
//
// It used to arrive as an 8-bit PNG with one fixed contrast baked in by the
// server: no way to brighten it or adjust the window without a re-fetch, and the
// full 16-bit range never reached the browser. Now it arrives once as raw uint16
// and this shader windows it on the GPU -- like the stain channels, and like
// Xenium Explorer's intensity/contrast sliders. A brightness/contrast change is a
// uniform update; nothing is fetched. gamma (win.z) is the Xenium "contrast"
// curve, wired here and defaulting to 1 (linear) until a slider drives it.
import { BitmapLayer, TileLayer } from 'deck.gl';
import { fetchTile } from './tileFetch';

const SAMPLER = {
  minFilter: 'nearest', magFilter: 'nearest',
  addressModeU: 'clamp-to-edge', addressModeV: 'clamp-to-edge',
};

const uniformBlock = `\
layout(std140) uniform morphUniforms {
  vec4 win;     // x=lo, y=hi, z=gamma, w=unused  (raw uint16 units)
  vec4 tint;    // rgb tint (1,1,1 = grey), w=unused
} morph;
`;

const morphUniforms = {
  name: 'morph',
  vs: uniformBlock,
  fs: uniformBlock,
  uniformTypes: { win: 'vec4<f32>', tint: 'vec4<f32>' },
};

const fs = `\
#version 300 es
#define SHADER_NAME morph-tile-fragment-shader
precision highp float;
precision highp int;
precision highp usampler2D;

uniform usampler2D morphTex;

in vec2 vTexCoord;
out vec4 fragColor;

float texel(ivec2 p) { return float(texelFetch(morphTex, p, 0).r); }

// Integer textures can only be sampled nearest, so filter by hand. Bilinear when
// zoomed out (no shimmer); nearest when magnifying past native, so full-res
// pixels stay crisp instead of smearing into a blur (matches the stain layer).
float sampleRaw(vec2 uv) {
  ivec2 size = textureSize(morphTex, 0);
  ivec2 lim = size - 1;
  vec2 duv = fwidth(uv) * vec2(size);
  if (max(duv.x, duv.y) < 1.0) {
    return texel(clamp(ivec2(floor(uv * vec2(size))), ivec2(0), lim));
  }
  vec2 p = uv * vec2(size) - 0.5;
  vec2 f = fract(p);
  ivec2 i = ivec2(floor(p));
  float a = texel(clamp(i, ivec2(0), lim));
  float b = texel(clamp(i + ivec2(1, 0), ivec2(0), lim));
  float c = texel(clamp(i + ivec2(0, 1), ivec2(0), lim));
  float d = texel(clamp(i + ivec2(1, 1), ivec2(0), lim));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void main(void) {
  vec2 uv = vTexCoord;
  float n = clamp((sampleRaw(uv) - morph.win.x) / max(morph.win.y - morph.win.x, 1.0), 0.0, 1.0);
  if (morph.win.z > 0.0 && morph.win.z != 1.0) n = pow(n, 1.0 / morph.win.z);  // gamma
  // Brightness lives in ALPHA only. deck blends SRC_ALPHA / ONE_MINUS_SRC_ALPHA,
  // so over the dark canvas this shows n * tint -- the windowed value itself.
  // Putting n in the colour as well applied it twice: mid-grey 0.5 showed as
  // 0.25, a hidden gamma of 0.5 that made the morphology darker than the PNG.
  fragColor = vec4(morph.tint.rgb, n * layer.opacity);
  geometry.uv = uv;
  DECKGL_FILTER_COLOR(fragColor, geometry);
}
`;

/** One morphology tile: a raw uint16 array windowed to grey on the GPU. */
export class MorphTileLayer extends BitmapLayer {
  static layerName = 'MorphTileLayer';

  static defaultProps = {
    raw: { type: 'object', value: null },     // Uint16Array, tile*tile
    win: { type: 'array', value: [0, 1, 1] }, // [lo, hi, gamma]
    tint: { type: 'array', value: [255, 255, 255] },
    tileSize: 1024,
  };

  getShaders() {
    const shaders = super.getShaders();
    return { ...shaders, fs, modules: [...shaders.modules, morphUniforms] };
  }

  updateState(params) {
    super.updateState(params);
    const { props, oldProps } = params;
    if (props.raw !== oldProps.raw || !this.state.tex) {
      this._release();
      const { device } = this.context;
      const T = props.tileSize;
      if (props.raw && props.raw.length === T * T) {
        const tex = device.createTexture({
          format: 'r16uint', width: T, height: T, mipLevels: 1, sampler: SAMPLER,
        });
        tex.writeData(props.raw);
        this.setState({ tex });
      } else {
        this.setState({ tex: null });
      }
      if (!this.state.emptyTex) {
        // the declared sampler always needs something bound
        const empty = device.createTexture({
          format: 'r16uint', width: 1, height: 1, mipLevels: 1, sampler: SAMPLER,
        });
        empty.writeData(new Uint16Array(1));
        this.setState({ emptyTex: empty });
      }
    }
  }

  _release() {
    if (this.state.tex) this.state.tex.destroy();
    this.state.tex = null;
  }

  finalizeState(context) {
    this._release();
    if (this.state.emptyTex) this.state.emptyTex.destroy();
    super.finalizeState(context);
  }

  draw(opts) {
    const { model, coordinateConversion, bounds, tex, emptyTex } = this.state;
    if (!model || !emptyTex) return;
    if (opts.shaderModuleProps.picking.isActive) return;   // never pickable
    const w = this.props.win || [0, 1, 1];
    const c = this.props.tint || [255, 255, 255];
    model.shaderInputs.setProps({
      bitmap: {
        bounds, coordinateConversion,
        desaturate: 0, tintColor: [1, 1, 1], transparentColor: [0, 0, 0, 0],
      },
      morph: {
        win: [Number(w[0]) || 0, Number(w[1]) || 1, Number(w[2]) || 1, 0],
        tint: [(c[0] || 255) / 255, (c[1] || 255) / 255, (c[2] || 255) / 255, 1],
      },
    });
    model.setBindings({ morphTex: tex || emptyTex });
    model.draw(this.context.renderPass);
  }
}

// a tile's bounds never change; a new array per render would rebuild the mesh
const boundsByTile = new WeakMap();
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
 * The morphology pyramid, windowed on the GPU. `win` is [lo, hi, gamma] in raw
 * uint16 units; it is a uniform, so changing it re-tints the cached tiles with no
 * fetch. `urlFor(backendZ, x, y)` builds the raw-tile URL.
 */
export function morphTileLayer({
  id, levels, tileSize, width, height, opacity, win, tint, urlFor, refetchKey,
  cacheBytes,
}) {
  const maxLevel = levels - 1;
  const perTile = tileSize * tileSize * 2;
  const maxCacheSize = Math.max(8, Math.floor((cacheBytes || 256 * 1024 * 1024) / perTile));
  const expect = perTile;
  return new TileLayer({
    id,
    tileSize,
    minZoom: -maxLevel,
    maxZoom: 0,
    extent: [0, 0, width, height],
    opacity,
    maxRequests: 10,
    maxCacheSize,
    // read by renderSubLayers; a change makes new uniforms without refetching
    morphWin: win,
    morphTint: tint,
    getTileData: ({ index, signal }) => {
      const backendZ = maxLevel + index.z;
      if (backendZ < 0 || backendZ > maxLevel) return Promise.resolve(null);
      const decode = async (blob) => {
        const buf = await blob.arrayBuffer();
        return buf.byteLength === expect ? new Uint16Array(buf) : null;
      };
      return fetchTile(urlFor(backendZ, index.x, index.y), { signal, decode })
        .then((data) => (data ? { data, byteLength: data.byteLength } : null))
        .catch((e) => { if (signal && signal.aborted) throw e; return null; });
    },
    renderSubLayers: (props) => {
      if (!props.data) return null;
      return new MorphTileLayer(props, {
        data: null,
        image: null,
        raw: props.data.data,
        win: props.morphWin,
        tint: props.morphTint,
        tileSize,
        bounds: tileBounds(props.tile),
      });
    },
    updateTriggers: {
      getTileData: [refetchKey],
      renderSubLayers: [win[0], win[1], win[2], tint[0], tint[1], tint[2], opacity],
    },
  });
}
