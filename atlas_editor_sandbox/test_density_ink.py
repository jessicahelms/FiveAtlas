"""Transcript density: square bins, ink rendering, and the mixing rules.

Offline against a synthetic GeneDensity built the same CSR way as the zarr.
"""
import sys

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

import numpy as np                       # noqa: E402
import transcripts as TR                 # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


def make_gd(dense):
    """A GeneDensity without opening a zarr: fill the fields by hand."""
    gd = TR.GeneDensity.__new__(TR.GeneDensity)
    gd.pixel_size = 0.5
    G, R, C = dense.shape
    gd.gene_names = [f"g{i}" for i in range(G)]
    gd.rows, gd.cols = R, C
    gd.grid_x = gd.grid_y = 10.0
    import threading
    gd._lock = threading.Lock()
    gd._root = None
    ip, idx, dat = [0], [], []
    for g in range(G):
        for r in range(R):
            nz = np.nonzero(dense[g, r])[0]
            idx.extend(nz.tolist()); dat.extend(dense[g, r][nz].tolist())
            ip.append(len(idx))
    gd._indptr = np.array(ip, np.int64)
    gd._indices = np.array(idx, np.int64)
    gd._data = np.array(dat, np.float32)
    gd._cache = {}
    gd.W = C * gd.grid_x / gd.pixel_size
    gd.H = R * gd.grid_y / gd.pixel_size
    return gd


dense = np.zeros((2, 8, 8), np.float32)
dense[0, 0:4, 0:4] = 10.0        # gene0 top-left
dense[1, 0:4, 4:8] = 10.0        # gene1 top-right
dense[0, 0, 0] = 20.0            # a hot corner for contrast range
gd = make_gd(dense)

BLUE = [60, 90, 255]
RED = [255, 60, 60]
spec = [{"gene": "g0", "color": BLUE, "min": 0, "max": 10, "visible": True},
        {"gene": "g1", "color": RED, "min": 0, "max": 10, "visible": True}]

print("\nink mode")
img = gd.composite(spec, mode="ink")
check("output is the native grid", img.shape == (8, 8, 4), str(img.shape))
b = img[2, 2]     # only gene0, s=1
check("dense blue bin is the blue ink itself",
      list(b[:3]) == BLUE and b[3] == 255, str(b.tolist()))
check("empty bin is fully transparent", img[6, 6, 3] == 0)
# lighter where less dense: rescale so s=0.5 somewhere
spec_half = [dict(spec[0], max=20)] + [spec[1]]
img2 = gd.composite(spec_half, mode="ink")
half = img2[2, 2]     # s = 10/20 = 0.5 -> halfway toward white
check("half density reads as LIGHTER blue (toward white)",
      all(int(half[k]) >= int(b[k]) for k in range(3))
      and sum(map(int, half[:3])) > sum(map(int, b[:3])) and half[3] < 255,
      str(half.tolist()))

print("\ntwo inks multiply")
overlap = np.zeros((2, 4, 4), np.float32)
overlap[0, :, :] = 10.0
overlap[1, :, :] = 10.0
gd2 = make_gd(overlap)
img3 = gd2.composite(
    [{"gene": "g0", "color": BLUE, "min": 0, "max": 10},
     {"gene": "g1", "color": RED, "min": 0, "max": 10}], mode="ink")
px = img3[1, 1]
want = [round(BLUE[k] / 255 * RED[k] / 255 * 255) for k in range(3)]
check("dense blue + dense red = their product (dark purple)",
      all(abs(int(px[k]) - want[k]) <= 1 for k in range(3)),
      f"{px[:3].tolist()} vs {want}")

print("\nbinning")
img4 = gd.composite(spec, mode="ink", bin_um=20)
check("20um bins halve the grid", img4.shape == (4, 4, 4), str(img4.shape))
img5 = gd.composite(spec, mode="glow", bin_um=40)
check("glow honours the bin size too", img5.shape == (2, 2, 4), str(img5.shape))
# mean pooling: a 2x2 block averaging 10s stays 10 -> same shade as unpooled
check("pooling keeps density scale (mean, not sum)",
      list(img4[0, 0][:3]) == list(img[1, 1][:3]),
      f"{img4[0, 0].tolist()} vs {img[1, 1].tolist()}")

print("\nglow mode unchanged for old callers")
img6 = gd.composite(spec)
check("additive glow: hot bin is bright", img6[2, 2, 3] > 200)
check("empty bin transparent", img6[6, 6, 3] == 0)

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)
