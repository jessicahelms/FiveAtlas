"""Rotate / flip a whole dataset, image and regions together.

The slide itself is sometimes mounted or imaged the wrong way up. The image and
the regions agree with each other -- they are just both upside down -- so this is
ONE transform applied to everything: morphology tiles, stain tiles, the gene
layer and the region coordinates.

The transform is applied to the DATA rather than to the deck.gl view. A view
transform would leave the editing layer working in the untransformed frame, so
every drag would land in the wrong place; transforming the data means the whole
app, editing included, simply works in the rotated frame with no special cases.

Convention, all in full-resolution pixel space with the origin at the top left:
flips are applied first, then a CLOCKWISE rotation. A 90 or 270 degree rotation
swaps the width and height.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import config

IDENTITY = {"rot": 0, "flipH": False, "flipV": False}


def normalise(o) -> dict:
    o = o or {}
    try:
        rot = int(o.get("rot", 0)) % 360
    except Exception:
        rot = 0
    if rot not in (0, 90, 180, 270):
        rot = min((0, 90, 180, 270), key=lambda r: abs(r - rot))
    return {"rot": rot, "flipH": bool(o.get("flipH")), "flipV": bool(o.get("flipV"))}


def is_identity(o) -> bool:
    o = normalise(o)
    return o["rot"] == 0 and not o["flipH"] and not o["flipV"]


def _file(ds_id: str) -> Path:
    return Path(config.WORKDIR) / ds_id / "orientation.json"


def get(ds_id: str) -> dict:
    try:
        p = _file(ds_id)
        if p.exists():
            return normalise(json.load(open(p, encoding="utf-8")))
    except Exception:
        pass
    return dict(IDENTITY)


def put(ds_id: str, o) -> dict:
    o = normalise(o)
    p = _file(ds_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f)
    return o


def out_size(W: float, H: float, o) -> tuple:
    """Displayed size. A quarter turn swaps the axes."""
    return (H, W) if normalise(o)["rot"] in (90, 270) else (W, H)


def fwd(x: float, y: float, o, W: float, H: float) -> tuple:
    """Source pixel -> displayed pixel. W/H are the SOURCE dimensions."""
    o = normalise(o)
    if o["flipH"]:
        x = W - x
    if o["flipV"]:
        y = H - y
    r = o["rot"]
    if r == 90:
        return H - y, x
    if r == 180:
        return W - x, H - y
    if r == 270:
        return y, W - x
    return x, y


def inv(X: float, Y: float, o, W: float, H: float) -> tuple:
    """Displayed pixel -> source pixel. W/H are still the SOURCE dimensions."""
    o = normalise(o)
    r = o["rot"]
    if r == 90:
        x, y = Y, H - X
    elif r == 180:
        x, y = W - X, H - Y
    elif r == 270:
        x, y = W - Y, X
    else:
        x, y = X, Y
    if o["flipH"]:
        x = W - x
    if o["flipV"]:
        y = H - y
    return x, y


def compose(a, b) -> dict:
    """The single orientation equivalent to applying `a` then `b`.

    Used to go from the orientation a file was saved in to the one now wanted,
    without ever transforming the geometry twice.
    """
    a, b = normalise(a), normalise(b)
    # Track where the transform sends the unit square's axes rather than
    # case-splitting 64 combinations: compose the 2x2 sign/swap matrices.
    def mat(o):
        m = np.array([[-1.0 if o["flipH"] else 1.0, 0.0],
                      [0.0, -1.0 if o["flipV"] else 1.0]])
        t = np.deg2rad(o["rot"])
        c, s = round(np.cos(t)), round(np.sin(t))
        # clockwise rotation in a y-down frame
        r = np.array([[c, s], [-s, c]], dtype=float)
        return r @ m

    M = mat(b) @ mat(a)
    # Read the result back out as flips + a rotation.
    for rot in (0, 90, 180, 270):
        for fh in (False, True):
            for fv in (False, True):
                if np.allclose(mat({"rot": rot, "flipH": fh, "flipV": fv}), M):
                    return {"rot": rot, "flipH": fh, "flipV": fv}
    return dict(IDENTITY)


def invert(o) -> dict:
    """The orientation that undoes `o`."""
    o = normalise(o)
    for cand_rot in (0, 90, 180, 270):
        for fh in (False, True):
            for fv in (False, True):
                cand = {"rot": cand_rot, "flipH": fh, "flipV": fv}
                if is_identity(compose(o, cand)):
                    return cand
    return dict(IDENTITY)


# ---- geometry ------------------------------------------------------------------


def transform_coords(coords, o, W, H):
    """Recursively rewrite a GeoJSON coordinate tree."""
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            x, y = fwd(float(coords[0]), float(coords[1]), o, W, H)
            return [x, y] + list(coords[2:])
        return [transform_coords(c, o, W, H) for c in coords]
    return coords


def transform_features(features, o, W, H):
    """A new feature list in the displayed frame. Properties are untouched."""
    if is_identity(o):
        return [dict(f) for f in (features or [])]
    out = []
    for f in features or []:
        g = f.get("geometry") or {}
        nf = dict(f)
        if g.get("coordinates") is not None:
            nf["geometry"] = {**g, "coordinates": transform_coords(g["coordinates"], o, W, H)}
        out.append(nf)
    return out


# ---- images --------------------------------------------------------------------


def transform_image(a: np.ndarray, o) -> np.ndarray:
    """Apply the same transform to a pixel array (rows = y, cols = x)."""
    o = normalise(o)
    if o["flipH"]:
        a = np.fliplr(a)
    if o["flipV"]:
        a = np.flipud(a)
    r = o["rot"]
    if r:
        # np.rot90 turns anticlockwise; k = -1 is one clockwise quarter turn.
        a = np.rot90(a, k=-(r // 90))
    return np.ascontiguousarray(a)


def source_window(tx: int, ty: int, tile: int, o, W: int, H: int):
    """Which SOURCE rectangle feeds displayed tile (tx, ty)?

    The displayed tile's corners are mapped back through the inverse transform
    and the bounding box taken, so this works for all eight orientations without
    a special case per rotation. Returns (x0, y0, x1, y1) clipped to the source,
    or None when the tile lies entirely outside it.
    """
    DW, DH = out_size(W, H, o)
    X0, Y0 = tx * tile, ty * tile
    X1, Y1 = min(X0 + tile, DW), min(Y0 + tile, DH)
    if X0 >= DW or Y0 >= DH or X1 <= X0 or Y1 <= Y0:
        return None
    pts = [inv(X, Y, o, W, H) for X in (X0, X1) for Y in (Y0, Y1)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = max(0, int(np.floor(min(xs))))
    y0 = max(0, int(np.floor(min(ys))))
    x1 = min(W, int(np.ceil(max(xs))))
    y1 = min(H, int(np.ceil(max(ys))))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1
