"""In-memory LRU of encoded tiles, shared by the morphology and stain routes.

Decoding a JPEG2000 tile costs 0.1-0.5 s, and the browser's own cache does not
help once a URL changes (a stain contrast tweak, a rotation and back, a reload).
Panning back over ground already seen is then free. Bounded by bytes, not
entries: a stain tile is ~1-2 MB, a morphology tile a few hundred KB.

Keys start with the dataset id so closing a dataset can drop its tiles.
ATLAS_TILE_CACHE_MB sets the budget (0 turns the cache off).
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

try:
    _BUDGET = int(float(os.environ.get("ATLAS_TILE_CACHE_MB", "384")) * 1024 * 1024)
except ValueError:
    _BUDGET = 384 * 1024 * 1024

_lock = threading.Lock()
_tiles: "OrderedDict[tuple, bytes]" = OrderedDict()
_size = 0
_gen: dict = {}          # ds_id -> generation, bumped by drop_dataset


def generation(ds_id: str) -> int:
    """Put this in a tile's key, read BEFORE the tile is rendered. A request
    still in flight when its dataset is dropped then stores its tile under the
    old generation, where nothing will ever look it up again."""
    return _gen.get(ds_id, 0)


def get(key: tuple):
    if _BUDGET <= 0:
        return None
    with _lock:
        data = _tiles.get(key)
        if data is not None:
            _tiles.move_to_end(key)
        return data


def put(key: tuple, data: bytes) -> None:
    global _size
    if _BUDGET <= 0 or data is None or len(data) > _BUDGET // 4:
        return
    with _lock:
        old = _tiles.pop(key, None)
        if old is not None:
            _size -= len(old)
        _tiles[key] = data
        _size += len(data)
        while _size > _BUDGET and _tiles:
            _k, v = _tiles.popitem(last=False)
            _size -= len(v)


def drop_dataset(ds_id: str) -> None:
    """Forget every tile of one dataset (closed, or its imagery changed)."""
    global _size
    with _lock:
        _gen[ds_id] = _gen.get(ds_id, 0) + 1
        for k in [k for k in _tiles if k and k[0] == ds_id]:
            _size -= len(_tiles.pop(k))
