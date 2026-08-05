import time
from pathlib import Path
import numpy as np
import tifffile
import zarr

d = Path(r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend"
         r"\workdir\DNMT3A_002_27_38_Het_F\morphology_focus")
f = d / "ch0002_18s.ome.tif"


def read(level, is_ome):
    with tifffile.TiffFile(str(f), is_ome=is_ome) as tf:
        s = tf.series[0]
        z = zarr.open(s.aszarr(), mode="r")
        if isinstance(z, zarr.hierarchy.Group):
            keys = sorted(z.array_keys(), key=lambda k: int(k))
            arr = z[keys[level]]
        else:
            arr = z
        ax = s.axes
        sl = [slice(None)] * arr.ndim
        if "C" in ax:
            sl[ax.index("C")] = 2
        plane = np.asarray(arr[tuple(sl)]).squeeze()
        return s.axes, s.shape, len(s.levels), plane


t = time.time()
ax, sh, nl, p = read(4, True)
print("OME(is_ome=True): axes=%s shape=%s levels=%d -> L4 %.2fs nz=%.3f shape=%s"
      % (ax, sh, nl, time.time() - t, (p > 0).mean(), p.shape))

t = time.time()
ax, sh, nl, p = read(4, False)
print("raw(is_ome=False): axes=%s shape=%s levels=%d -> L4 %.2fs nz=%.3f shape=%s"
      % (ax, sh, nl, time.time() - t, (p > 0).mean(), p.shape))
