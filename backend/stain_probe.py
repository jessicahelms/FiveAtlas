"""Probe morphology_focus.zip: entry compression + can we read one channel's
JP2 tiles in-place (no extraction)? Reads each stain from its OWN zip entry."""
import sys, time, zipfile
from pathlib import Path

import numpy as np
import tifffile
import zarr
from PIL import Image

OUT = Path(r"C:\Users\FIVE\AppData\Local\Temp\claude"
           r"\C--Users-FIVE-source-repos-Jess"
           r"\f2be11fb-56f3-4d18-a20c-251cb0d655b6\scratchpad")
ZP = r"S:\Phys\FIV911 Atlas\RealDS\AllenBA 3D\Images from Box\DNMT3A_002_27_38_Het_F\morphology_focus.zip"

zf = zipfile.ZipFile(ZP)
entries = [i for i in zf.infolist() if not i.is_dir()]
print("=== entries ===")
for i in entries:
    print("  %-46s size=%d compress=%s" % (
        i.filename, i.file_size, "STORED" if i.compress_type == 0 else f"deflate({i.compress_type})"))

# Try to read channel from its own entry, in place (seekable stream).
name = "morphology_focus/ch0002_18s.ome.tif"
f = zf.open(name)
print("\nzip stream seekable:", f.seekable())
t = time.time()
tf = tifffile.TiffFile(f)
s = tf.series[0]
print("axes=%s shape=%s levels=%d dtype=%s opened=%.2fs" % (
    s.axes, s.shape, len(s.levels), s.dtype, time.time() - t))

z = zarr.open(s.aszarr(), mode="r")
if isinstance(z, zarr.hierarchy.Group):
    keys = sorted(z.array_keys(), key=lambda k: int(k))
    coarse = z[keys[-1]]
else:
    coarse = z
axes = s.axes
ci = axes.index("C") if "C" in axes else None
print("coarse level shape:", coarse.shape, "C axis idx:", ci)

# read channel index 2 (this file's own channel) at the coarsest level
sl = [slice(None)] * coarse.ndim
if ci is not None:
    sl[ci] = 2
t = time.time()
plane = np.asarray(coarse[tuple(sl)]).squeeze()
print("ch2 coarse read: shape=%s nonzero=%.3f max=%d in %.2fs" % (
    plane.shape, float((plane > 0).mean()), int(plane.max()), time.time() - t))

pos = plane[plane > 0]
lo, hi = (np.percentile(pos, [1, 99.5]) if pos.size else (0, 1))
u8 = (np.clip((plane.astype(np.float32) - lo) / (hi - lo + 1e-6), 0, 1) * 255).astype(np.uint8)
Image.fromarray(u8, "L").save(OUT / "stain_ch2_18s.png")
print("saved stain_ch2_18s.png")

# also time a full-res single-tile read to confirm in-place tile access is fast
if isinstance(z, zarr.hierarchy.Group):
    full = z[keys[0]]
    sl = [slice(None)] * full.ndim
    if ci is not None:
        sl[ci] = 2
    yx = [a for a in range(full.ndim) if a != ci]
    sl[yx[0]] = slice(15000, 16024)
    sl[yx[1]] = slice(13000, 14024)
    t = time.time()
    tile = np.asarray(full[tuple(sl)]).squeeze()
    print("full-res 1024 tile (ch2) read in %.2fs, nonzero=%.3f" % (
        time.time() - t, float((tile > 0).mean())))
