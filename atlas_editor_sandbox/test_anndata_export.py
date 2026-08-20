"""AnnData export -- the hand-written .h5ad must open in the REAL anndata.

Two interpreters on purpose: the file is WRITTEN by the app's own h5py-only
writer (repo venv), then READ BACK by the genuine anndata package installed in
a separate throwaway venv (set ATLAS_ANNDATA_PY to its python). If that venv is
missing, the read-back half is skipped with a loud note rather than faked.

Offline: builds a tiny synthetic GeneDensity, nothing written outside scratch.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, r"C:\Users\FIVE\source\repos\Jess\atlas_editor\backend")

import numpy as np                      # noqa: E402
import annexport                        # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label)


class FakeGD:
    """A 20x20-cell 10um grid, 3 genes, CSR exactly like transcripts.zarr's."""
    rows, cols = 20, 20
    grid_x = grid_y = 10.0
    pixel_size = 0.5                      # 1 cell = 20 px
    gene_names = ["Gad1", "NegControlProbe_1", "Slc17a7"]

    def __init__(self):
        dense = np.zeros((3, self.rows, self.cols), np.float32)
        dense[0, 2:8, 2:8] = 2.0          # Gad1: left block   (px 40..160)
        dense[2, 2:8, 12:18] = 5.0        # Slc17a7: right block (px 240..360)
        dense[1, :, :] = 1.0              # control everywhere -- must be dropped
        ip, idx, dat = [0], [], []
        for g in range(3):
            for r in range(self.rows):
                row = dense[g, r]
                nz = np.nonzero(row)[0]
                idx.extend(nz.tolist())
                dat.extend(row[nz].tolist())
                ip.append(len(idx))
        self._indptr = np.array(ip, np.int64)
        self._indices = np.array(idx, np.int64)
        self._data = np.array(dat, np.float32)

    def gene_list(self):
        return ["Gad1", "Slc17a7"]


def box_feat(name, x0, y0, x1, y1):
    return {"type": "Feature", "properties": {"name": name},
            "geometry": {"type": "Polygon", "coordinates":
                         [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


gd = FakeGD()
# LEFT covers the Gad1 block, RIGHT covers the Slc17a7 block (in px: cell*20)
feats = [box_feat("LEFT", 0, 0, 200, 200), box_feat("RIGHT", 200, 0, 400, 200)]

print("\ncounting on the grid")
names, counts, genes, areas, cents = annexport.region_gene_counts(gd, feats, "name")
check("regions in file order", names == ["LEFT", "RIGHT"])
check("control genes are dropped", genes == ["Gad1", "Slc17a7"], str(genes))
check("Gad1 lands in LEFT only",
      counts[0][0] == 72.0 and counts[1][0] == 0.0,
      f"{counts[:, 0].tolist()}")     # 36 cells x 2.0
check("Slc17a7 lands in RIGHT only",
      counts[1][1] == 180.0 and counts[0][1] == 0.0,
      f"{counts[:, 1].tolist()}")     # 36 cells x 5.0
check("areas are the polygon areas", abs(areas[0] - 40000) < 1)

print("\nwriting the .h5ad")
path = os.path.join(tempfile.mkdtemp(prefix="h5ad_"), "regions.h5ad")
annexport.write_h5ad(path, counts, names,
                     [("area_px2", areas, "num"),
                      ("n_transcript_counts", counts.sum(axis=1), "num")],
                     genes, obsm={"spatial": cents},
                     uns={"source": "FiveAtlas", "pixel_size_um": gd.pixel_size})
check("file exists and is HDF5", open(path, "rb").read(4) == b"\x89HDF")

print("\nreading it back with the REAL anndata")
other = os.environ.get(
    "ATLAS_ANNDATA_PY",
    r"C:\Users\FIVE\AppData\Local\Temp\claude\C--Users-FIVE-source-repos-Jess"
    r"\3bd3ff25-45a1-45b7-a49f-d4444ff96a46\scratchpad\venv313\Scripts\python.exe")
if not os.path.exists(other):
    print("  SKIP  no anndata venv at ATLAS_ANNDATA_PY -- round trip NOT verified")
else:
    code = f"""
import anndata, numpy as np
a = anndata.read_h5ad(r"{path}")
assert list(a.obs_names) == ["LEFT", "RIGHT"], list(a.obs_names)
assert list(a.var_names) == ["Gad1", "Slc17a7"], list(a.var_names)
assert a.X.shape == (2, 2) and a.X[0, 0] == 72.0 and a.X[1, 1] == 180.0
assert abs(float(a.obs["area_px2"].iloc[0]) - 40000) < 1
assert a.obsm["spatial"].shape == (2, 2)
assert a.uns["source"] == "FiveAtlas"
print("ANNDATA_OK", a.shape, float(a.X.sum()))
"""
    r = subprocess.run([other, "-c", code], capture_output=True, text=True,
                       timeout=300)
    ok = "ANNDATA_OK" in (r.stdout or "")
    check("anndata.read_h5ad round trip", ok,
          (r.stdout or r.stderr).strip().splitlines()[-1][:100] if (r.stdout or r.stderr) else "")

print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)
