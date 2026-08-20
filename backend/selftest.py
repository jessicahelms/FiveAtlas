"""Prove the native stack works in THIS build -- not just that the server answers.

`/api/health` says the process is up. It says nothing about whether the
JPEG2000 codec dylib loaded, whether blosc can round-trip a block, whether GEOS
is wired to shapely, or whether the YAML round-tripper is whole. Those are the
things a colleague discovers only when they open a real dataset -- which is the
one thing the developer cannot try on a platform they do not own. So this runs
each of them on tiny embedded data and reports per feature, and CI refuses to
publish a build where any of them fails.
"""
from __future__ import annotations

import base64
import sys

# 8x8 greyscale ramp, lossless JPEG2000 (.jp2), 250 bytes. Values 0,4,8..252.
_J2K = base64.b64decode(
    "AAAADGpQICANCocKAAAAFGZ0eXBqcDIgAAAAAGpwMiAAAAAtanAyaAAAABZpaGRyAAAACAAAAAgAAQcH"
    "AAAAAAAPY29scgEAAAAAABEAAACtanAyY/9P/1EAKQAAAAAACAAAAAgAAAAAAAAAAAAAAAgAAAAIAAAA"
    "AAAAAAAAAQcBAf9SAAwAAAABAAAEBAAB/1wABEBA/2QAJQABQ3JlYXRlZCBieSBPcGVuSlBFRyB2ZXJz"
    "aW9uIDIuNS4z/5AACgAAAAAAOwAB/5PfgVASKwMLCxPkuS+FPzhQgqEU/1Zxw3N9JM4wvb1yYz7qxGrz"
    "TDo5fYsu9lf/2Q=="
)


def _check(name, fn):
    try:
        detail = fn()
        return {"ok": True, "detail": detail}
    except Exception as e:   # report, never raise: the caller wants every row
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def _jpeg2k():
    import numpy as np
    import imagecodecs
    img = imagecodecs.jpeg2k_decode(_J2K)
    want = (np.arange(64, dtype=np.uint8).reshape(8, 8) * 4)
    if img.shape != (8, 8) or not (img == want).all():
        raise RuntimeError(f"decoded shape {img.shape}, content mismatch")
    return f"imagecodecs {imagecodecs.__version__}, openjpeg {imagecodecs.jpeg2k_version()}"


def _tiff():
    import imagecodecs
    return f"libtiff {imagecodecs.tiff_version()}"


def _blosc():
    import numpy as np
    import numcodecs
    import zarr
    codec = numcodecs.Blosc(cname="zstd", clevel=1)
    arr = np.arange(4096, dtype=np.uint16)
    back = np.frombuffer(codec.decode(codec.encode(arr)), dtype=np.uint16)
    if not (back == arr).all():
        raise RuntimeError("blosc round trip mismatch")
    # zarr 2.x at import time needs numcodecs.blosc.cbuffer_sizes -- the exact
    # break a loose numcodecs pin caused once. Touch it on purpose.
    from numcodecs.blosc import cbuffer_sizes  # noqa: F401
    return f"zarr {zarr.__version__}, numcodecs {numcodecs.__version__}"


def _shapely():
    import shapely
    from shapely.geometry import box
    from shapely.ops import unary_union
    u = unary_union([box(0, 0, 10, 10), box(5, 0, 15, 10)])
    if abs(u.area - 150.0) > 1e-6:
        raise RuntimeError(f"union area {u.area}")
    return f"shapely {shapely.__version__}, geos {shapely.geos_version_string}"


def _yaml():
    import io
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    # ruamel's own default block-sequence style -- the one metadata.yml uses --
    # so an exact round trip is the expectation, not an indentation preference.
    src = 'a: "x"\nb:\n- 1\n'
    data = y.load(src)
    buf = io.StringIO()
    y.dump(data, buf)
    if buf.getvalue() != src:
        raise RuntimeError("round trip changed the text")
    import ruamel.yaml
    return f"ruamel.yaml {ruamel.yaml.__version__}"


def _pillow():
    import io
    from PIL import Image
    import numpy as np
    im = Image.fromarray(np.zeros((4, 4, 4), np.uint8), "RGBA")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    if len(buf.getvalue()) < 20:
        raise RuntimeError("png came out empty")
    from PIL import __version__ as v
    return f"Pillow {v}"


def _h5ad():
    # The AnnData export writes HDF5 through h5py. Round-trip a tiny file in
    # memory so a missing HDF5 dylib fails HERE, in CI, not when a colleague
    # first clicks "AnnData (.h5ad)".
    import io as _io
    import h5py
    import numpy as np
    buf = _io.BytesIO()
    with h5py.File(buf, "w") as f:
        f.create_dataset("X", data=np.arange(6, dtype=np.float32).reshape(2, 3))
    buf.seek(0)
    with h5py.File(buf, "r") as f:
        if f["X"].shape != (2, 3):
            raise RuntimeError("round trip lost the shape")
    return f"h5py {h5py.__version__}, hdf5 {h5py.version.hdf5_version}"


def _tk():
    # The folder picker on macOS uses osascript first; tkinter is the fallback
    # and the only picker on Windows. Importing is enough: creating a root would
    # need a display.
    import tkinter  # noqa: F401
    return f"tk {tkinter.TkVersion}"


def run():
    import config
    rows = {
        "jpeg2k": _check("jpeg2k", _jpeg2k),
        "tiff": _check("tiff", _tiff),
        "blosc_zarr": _check("blosc_zarr", _blosc),
        "shapely": _check("shapely", _shapely),
        "yaml": _check("yaml", _yaml),
        "pillow": _check("pillow", _pillow),
        "h5ad": _check("h5ad", _h5ad),
        "tkinter": _check("tkinter", _tk),
    }
    return {
        "ok": all(r["ok"] for r in rows.values()),
        "version": config.VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "frozen": bool(getattr(sys, "frozen", False)),
        "checks": rows,
    }
