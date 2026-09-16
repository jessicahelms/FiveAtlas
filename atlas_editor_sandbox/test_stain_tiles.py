"""Stain tiles: every square loads, fast, and right.

One lock per channel and a cache of channel tiles made stain tiles several times
faster on a network drive; this proves they are still exactly the right pixels,
under heavy concurrency, for both a morphology_focus FOLDER and a .zip, and that
closing a dataset mid-read never produces anything but the retryable "closed".
Then the route: an unexpected read error answers 503 (try again), not a 500.

Offline: synthetic pyramids written to a scratch folder; no server, no real data.

    python test_stain_tiles.py [scratch_dir]
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

scratch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="stains_"))
if scratch.exists():
    shutil.rmtree(scratch, ignore_errors=True)
scratch.mkdir(parents=True)

# BEFORE any backend import: stains imports orientation, which imports config,
# and config fixes WORKDIR the moment it loads. Set later, this line was ignored
# and the app below read the real working folder.
os.environ["ATLAS_WORKDIR"] = str(scratch / "wd")

import config  # noqa: E402
import stains as S  # noqa: E402

if Path(config.WORKDIR).resolve() != (scratch / "wd").resolve():
    raise SystemExit(f"refusing to run: WORKDIR is {config.WORKDIR}, not the scratch folder")

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('  ' + str(detail)) if detail != '' else ''}")
    if not cond:
        fails.append(label)

TILE = 256
H0, W0, LEVELS = 1000, 900, 3
NAMES = ["dapi", "18s", "alphasma vimentin"]
rng = np.random.default_rng(7)
truth = {}   # channel -> [level arrays]


def write_pyramid(path, base):
    levels = [base]
    for _ in range(LEVELS - 1):
        levels.append(levels[-1][::2, ::2].copy())
    with tifffile.TiffWriter(path) as tw:
        tw.write(levels[0], subifds=LEVELS - 1, tile=(128, 128), photometric="minisblack")
        for lv in levels[1:]:
            tw.write(lv, subfiletype=1, tile=(128, 128), photometric="minisblack")
    return levels


folder = scratch / "morphology_focus"
folder.mkdir()
for i, nm in enumerate(NAMES):
    base = rng.integers(0, 60000, size=(H0, W0), dtype=np.uint16)
    truth[i] = write_pyramid(folder / f"ch{i:04d}_{nm.split()[0]}.ome.tif", base)

zpath = scratch / "morphology_focus.zip"
with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
    for p in sorted(folder.iterdir()):
        zf.write(p, arcname=f"morphology_focus/{p.name}")

channels = [{"index": i, "name": nm} for i, nm in enumerate(NAMES)]
MF = {
    "folder": {"kind": "folder", "path": str(folder), "channels": channels},
    "zip": {"kind": "zip", "path": str(zpath), "channels": channels},
}


def spec(colours=None):
    colours = colours or [[0, 0, 255], [255, 255, 0], [0, 255, 0]]
    return [{"index": i, "color": colours[i], "min": 1000.0, "max": 50000.0, "visible": True}
            for i in range(len(NAMES))]


def all_tiles(ss):
    out = []
    for lvl, (h, w) in enumerate(ss.level_shapes):
        out += [(lvl, tx, ty) for ty in range((h + TILE - 1) // TILE)
                for tx in range((w + TILE - 1) // TILE)]
    return out


for kind in ("folder", "zip"):
    print(f"\n[{kind}] the pixels are exactly the file's")
    ss = S.StainStack(MF[kind], tile=TILE)
    check("levels read from the file", ss.nlevels == LEVELS and ss.level_shapes[0] == (H0, W0),
          (ss.nlevels, ss.level_shapes))
    bad = 0
    for lvl, tx, ty in all_tiles(ss):
        for i in range(len(NAMES)):
            got = ss._tile(i, lvl, tx, ty)
            want = truth[i][lvl][ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
            if got is None or got.shape != want.shape or not np.array_equal(got, want):
                bad += 1
    check("every channel tile at every level matches, edges clipped", bad == 0, f"{bad} wrong")
    check("a tile past the edge is empty", ss._tile(0, 0, 99, 0) is None and ss._tile(0, 0, -1, 0) is None)
    check("a window is clipped to the level", np.array_equal(
        ss._window(1, 0, -50, -50, 100, 60), truth[1][0][0:60, 0:100]))
    check("cached arrays cannot be written to", not ss._tile(0, 0, 0, 0).flags.writeable)

    print(f"[{kind}] many requests at once give the same tiles as one at a time")
    tiles = all_tiles(ss)
    one_by_one = {t: ss.composite_tile(spec(), *t) for t in tiles}
    fresh = S.StainStack(MF[kind], tile=TILE)      # cold: nothing cached yet
    with ThreadPoolExecutor(16) as ex:
        together = list(ex.map(lambda t: (t, fresh.composite_tile(spec(), *t)), tiles * 3))
    check("16 threads, every tile 3 times: byte-identical",
          all(png == one_by_one[t] for t, png in together), f"{len(together)} tiles")
    fresh.close()

    print(f"[{kind}] a brightness change re-blends from memory, not the file")
    reads = []
    real_level_of = S.StainStack._level_of

    def counting(entry, level, _real=real_level_of):
        reads.append(level)
        return _real(entry, level)

    ss._level_of = counting
    png_a = ss.composite_tile(spec(), 0, 1, 1)
    n = len(reads)
    png_b = ss.composite_tile(spec([[255, 0, 0], [0, 255, 255], [255, 255, 255]]), 0, 1, 1)
    check("new colours, no new reads", len(reads) == n, f"{len(reads) - n} extra reads")
    check("and the picture did change", png_a != png_b)
    # the re-blended tile is exactly what a cold stack draws for those colours
    cold = S.StainStack(MF[kind], tile=TILE)
    check("identical to a cold read with those colours",
          png_b == cold.composite_tile(spec([[255, 0, 0], [0, 255, 255], [255, 255, 255]]), 0, 1, 1))
    cold.close()
    del ss._level_of

    print(f"[{kind}] the cache stays inside its limit")
    small = S.StainStack(MF[kind], tile=TILE, cache_bytes=4 * TILE * TILE * 2)   # ~4 tiles
    for lvl, tx, ty in all_tiles(small):
        small.composite_tile(spec(), lvl, tx, ty)
    info = small.cache_info()
    check("never over the byte limit", info["bytes"] <= info["limit"], info)
    check("still holds the latest tiles", info["entries"] >= 1, info)
    small.close()
    check("closing empties it", small.cache_info()["entries"] == 0)

    print(f"[{kind}] closing while tiles are being read")
    racer = S.StainStack(MF[kind], tile=TILE)
    errors, stop = [], threading.Event()

    def hammer():
        while not stop.is_set():
            for t in tiles[:12]:
                try:
                    racer.composite_tile(spec(), *t)
                except KeyError:
                    pass                       # "closed": the route's retryable 404
                except Exception as e:         # anything else would be a 500
                    errors.append(f"{type(e).__name__}: {e}")

    workers = [threading.Thread(target=hammer) for _ in range(8)]
    for w in workers:
        w.start()
    threading.Event().wait(0.3)
    racer.close()
    threading.Event().wait(0.2)
    stop.set()
    for w in workers:
        w.join()
    check("only the retryable 'closed', never another error", not errors, errors[:3])
    check("no file reopened behind the closed stack", racer._z == {})
    try:
        racer._tile(0, 0, 0, 0)
        closed_ok = False
    except KeyError:
        closed_ok = True
    check("a request after closing is told it is closed", closed_ok)
    ss.close()

print("\nthe route: a read that breaks answers 503 (try again), not 500")
import app as A  # noqa: E402   (WORKDIR is the scratch folder -- checked at the top)
from fastapi import HTTPException  # noqa: E402


class Broken:
    nlevels = 3

    def composite_tile(self, *a, **k):
        raise OSError("The cloud operation was not completed")


class Closed(Broken):
    def composite_tile(self, *a, **k):
        raise KeyError("stain stack is closed")


real = A.stainstack
try:
    for stack, want in ((Broken(), 503), (Closed(), 404)):
        A.stainstack = lambda ds_id, _s=stack: _s
        try:
            A.stains_tile("any", 0, 0, 0, "")
            code = 200
        except HTTPException as e:
            code = e.status_code
        check(f"{type(stack).__name__} read -> {want}", code == want, code)
finally:
    A.stainstack = real

shutil.rmtree(scratch, ignore_errors=True)
print("\n" + (f"{len(fails)} FAILED: " + "; ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)
