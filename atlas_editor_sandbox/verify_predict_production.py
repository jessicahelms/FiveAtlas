"""backend/predict.py against the lab's production batch of 30 Oct 2026. Read-only on the batch.

  A  stored canvas -> model -> gate -> rules  vs  regions_canvas_final.png, every section
  B  polygons vs the GeoJSON uploaded to Box on 4 Nov (same pixels -> same outlines)
  C  raw transcripts -> canvas  vs  the stored canvas, for sections whose archive is reachable

Set ATLAS_WORKDIR (a scratch folder) and ATLAS_REGION_MODEL first. On a PC with bad
P-cores, launch it pinned.

  python verify_predict_production.py <batch 1030 dir> <batch 1020 dir> [n_C_sections]
"""
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
if not os.environ.get("ATLAS_WORKDIR"):
    raise SystemExit("set ATLAS_WORKDIR to a scratch folder first")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402
from PIL import Image  # noqa: E402
from shapely.geometry import shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import config  # noqa: E402
import predict as P  # noqa: E402

if str(config.WORKDIR) != str(Path(os.environ["ATLAS_WORKDIR"])):
    raise SystemExit(f"backend picked up {config.WORKDIR}, not the scratch workdir")

BATCH, BATCH0 = Path(sys.argv[1]), Path(sys.argv[2])
N_C = int(sys.argv[3]) if len(sys.argv) > 3 else 3
print("model:", P.status(), flush=True)
_, META = P._load()
GENES = list(META["channel_names"])
PRED = BATCH / "predictions_idx22"
fail = 0


def frame_of(m):
    return {"scale": m["zarr_to_pixel_scale"], "pad_x": m["letterbox"]["pad_x"], "pad_y": m["letterbox"]["pad_y"],
            "x_min": m["zarr_bounds"]["x_min"], "y_max": m["zarr_bounds"]["y_max"], "flip_h": m["bake_flip_h"], "flip_v": m["bake_flip_v"]}


def stored_canvas(d):
    return np.stack([tifffile.imread(str(d / f"{g}.tif")) for g in GENES], 2)


# The production batch ran on one fiber-tract class and one ventricle class, and named
# regions in full. The app now splits off the optic tract (OPT) and the third ventricle
# (V3) after the rules and uses Allen abbreviations. Compare like for like:
#   label maps: OPT -> FT and V3 -> VENT on both sides (production has neither);
#   names: each production name <-> the set of app names it covers.
def like_production(lab):
    lab = np.asarray(lab).copy()
    lab[lab == P.OPT] = P.FT
    lab[lab == P.V3] = P.VENT
    return lab


OLD_TO_NEW = {"Ventricles": {P.NAMES[P.VENT], P.NAMES[P.V3]}, "Isocortex": {P.NAMES[P.ISO]},
              "Hypothalamus": {P.NAMES[P.HY]}, "Hippocampal Formation": {P.NAMES[P.HPF]},
              "Caudoputamen": {P.NAMES[P.CP]}, "Thalamus": {P.NAMES[P.TH]},
              "Striatum-Like Amygdalar Nuclei": {P.NAMES[P.SAMY]}, "Cortical Subplate": {P.NAMES[P.CTXSP]},
              "Olfactory": {P.NAMES[P.OLF]}, "Pallidum": {P.NAMES[P.PAL]},
              "Fiber Tracts": {P.NAMES[P.FT], P.NAMES[P.OPT]}}
NEW_TO_OLD = {new: old for old, news in OLD_TO_NEW.items() for new in news}
if set(NEW_TO_OLD) != set(P.NAMES[1:]):
    raise SystemExit(f"name map out of date with predict.NAMES: {sorted(set(P.NAMES[1:]) ^ set(NEW_TO_OLD))}")


# ---- A ------------------------------------------------------------------------
res = Counter()
worst = []
warn = Counter()
warned = []
for d in sorted(PRED.iterdir()):
    meta = json.load(open(d / "vcut.json"))
    gm = json.load(open(BATCH / "canvases" / d.name / "_grid_meta.json"))
    lab, cut, _ = P.apply_rules(P.segment(stored_canvas(BATCH / "canvases" / d.name)))
    want = np.array(Image.open(d / "regions_canvas_final.png")).astype(int)
    tag = "trimmed" if "trimmed_to_lab_hemi_px" in meta else "plain"
    res[f"{tag}: with opt (mapped to ft)"] += int((lab == P.OPT).any())
    n = int((like_production(lab) != like_production(want)).sum())
    res[f"{tag}: cut matches"] += int(cut == meta["cut_x"])
    res[f"{tag}: sections"] += 1
    res[f"{tag}: map exact"] += int(n == 0)
    res[f"{tag}: <= 10 px differ"] += int(n <= 10)
    res[f"{tag}: <= 50 px differ"] += int(n <= 50)
    if tag == "plain":
        worst.append((n, d.name, cut, meta["cut_x"]))
    w = P.qc(lab, cut, 1.0 / gm["zarr_to_pixel_scale"], [])
    if w:
        warned.append((d.name, w))
    warn.update(x.split(".")[0][:60] for x in w)
worst.sort(reverse=True)
print("\nA  model + rules vs regions_canvas_final.png")
for k in sorted(res):
    print(f"   {k}: {res[k]}")
print("   most-different sections (px of 409,600):", worst[:6], flush=True)
if res["plain: cut matches"] != res["plain: sections"]:
    fail += 1
print("   QC warnings on production sections:", dict(warn), f"({len(warned)} of {res['plain: sections'] + res['trimmed: sections']} sections)")
for name, w in warned[:12]:
    print("     ", name, w)

# ---- B ------------------------------------------------------------------------
print("\nB  polygons vs the 4 Nov Box upload", flush=True)
rows = list(csv.DictReader(open(BATCH / "upload_20261104" / "upload_list.csv", encoding="utf-8")))[:12]
for r in rows:
    key = r["prediction_dir"]
    lab = like_production(np.array(Image.open(PRED / key / "regions_canvas_final.png")).astype(int))
    gm = json.load(open(BATCH / "canvases" / key / "_grid_meta.json"))
    feats = P.to_features(lab, frame_of(gm), 0.2125, "name", "t")
    names = [f["properties"]["name"] for f in feats]
    up = json.load(open(BATCH / "upload_20261104" / r["file"], encoding="utf-8"))["features"]
    by = {}
    for f in up:
        by.setdefault(f["properties"]["name"], []).append(shape(f["geometry"]))
    # app features grouped under the production name they correspond to (ft + opt ->
    # "Fiber Tracts", VL + V3 -> "Ventricles"); a name with no counterpart is a mismatch
    ours = {}
    for f in feats:
        ours.setdefault(NEW_TO_OLD.get(f["properties"]["name"], "?" + f["properties"]["name"]), []).append(f)
    unknown = sorted(set(by) - set(OLD_TO_NEW))
    bad = 0
    tot = 0.0
    sd = 0.0
    for old in sorted(set(ours) | set(by)):
        a = unary_union([shape(f["geometry"]) for f in ours.get(old, [])])
        b = unary_union(by.get(old, []))
        tot += b.area
        sd += a.symmetric_difference(b).area
        bad += sum(not shape(f["geometry"]).is_valid for f in ours.get(old, []))
    dup = len(names) != len(set(names))
    ok = set(ours) == set(by) and not unknown and not bad and not dup and tot > 0 and sd / tot < 1e-3
    if set(ours) != set(by) or unknown:
        print(f"   names: only app {sorted(set(ours) - set(by))}  only upload {sorted(set(by) - set(ours))}  unknown {unknown}")
    fail += not ok
    print(f"   {r['dataset']:30s} regions {len(names):2d}  invalid {bad}  duplicate names {dup}  sym.diff {sd / tot:.2e} of area  {'ok' if ok else 'DIFFERS'}", flush=True)

# ---- C ------------------------------------------------------------------------
print("\nC  raw transcripts -> canvas", flush=True)
arch = json.load(open(BATCH / "archive_map.json"))
done = 0
for s, zp in arch.items():
    if done >= N_C:
        break
    if not os.path.exists(zp):
        continue
    sd = list(BATCH0.glob(f"datasets/*/{s}"))
    cdir = BATCH / "canvases" / s
    if not sd or not (cdir / "_grid_meta.json").exists():
        continue
    src = sd[0] / "source" / "roi.geojson"
    if not src.exists():
        continue
    g = json.load(open(src, encoding="utf-8"))
    geo = (g["features"] if g.get("type") == "FeatureCollection" else [g])[0]["geometry"]
    ring = geo["coordinates"][0] if geo["type"] == "Polygon" else geo["coordinates"][0][0]
    from shapely.geometry import Polygon
    roi = Polygon(np.asarray(ring, float) * 0.2125)
    gm = json.load(open(cdir / "_grid_meta.json"))
    hj = json.load(open(sd[0] / "hemis" / "hemi1_18ch.json"))
    xy, gid, panel, missing, lo, hi = P.read_points(zp, GENES)
    box = P.tissue_box(xy, roi, bool(hj.get("union_blobs")))
    import shapely
    pts = xy.astype(float)
    k = (pts[:, 0] >= roi.bounds[0]) & (pts[:, 0] <= roi.bounds[2]) & (pts[:, 1] >= roi.bounds[1]) & (pts[:, 1] <= roi.bounds[3])
    same_inside = np.array_equal(P._inside(roi, xy[k]), shapely.contains_xy(roi, pts[k, 0], pts[k, 1]))
    P.load_points(zp, GENES)
    c = P.load_points(zp, GENES)
    same_cache = all(np.array_equal(a, b) and a.dtype == b.dtype for a, b in ((c[0], xy), (c[1], gid), (c[4], lo), (c[5], hi))) and c[2] == panel and c[3] == missing
    fail += not (same_inside and same_cache)
    canvas, frame = P.build_canvas(xy, gid, panel, GENES, lo, hi, box, gm["bake_flip_h"], gm["bake_flip_v"])
    want = stored_canvas(cdir)
    same_box = np.allclose(box, gm["crop"]["tissue_bbox_um"])
    exact = np.array_equal(canvas, want)
    d = np.abs(canvas.astype(int) - want.astype(int))
    print(f"   {s:30s} box same {same_box}  inside test same {same_inside}  cache same {same_cache}  canvas exact {exact}  differing values {int((d > 0).sum())} of {d.size}  max {int(d.max())}", flush=True)
    fail += not same_box
    done += 1
print("\nresult:", "PASS" if fail == 0 else f"{fail} problems")
