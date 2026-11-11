"""In-memory LRU of encoded tiles, shared by the morphology and stain routes.

Decoding a JPEG2000 tile costs 0.1-0.5 s, and the browser's own cache does not
help once a URL changes (a stain contrast tweak, a rotation and back, a reload).
Panning back over ground already seen is then free. Bounded by bytes, not
entries: a stain tile is ~1-2 MB, a morphology tile a few hundred KB.

Keys start with the dataset id so closing a dataset can drop its tiles.
ATLAS_TILE_CACHE_MB sets the budget (0 turns the cache off). Behind it, the
morphology tiles are also kept on the local disk (disk_get / disk_put, below),
so they survive the app closing.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

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


# ---- on the local disk, behind the memory cache ---------------------------------
# The memory cache starts empty every time the app starts, so every tile was
# decoded again from the image file -- 0.25 s a tile off a network drive against
# 0.004 s from the cache. Decoded tiles are also kept in the working folder,
# under the image file's identity (path, size, modified time): a changed or
# replaced image is a different source, and its old tiles are never looked up.
# A shortcut only: a full disk or a locked file just means decoding again.
# ATLAS_TILE_DISK_MB sets the budget (0 turns it off); oldest-used go first.

try:
    _DISK_BUDGET = int(float(os.environ.get("ATLAS_TILE_DISK_MB", "4096")) * 1024 * 1024)
except ValueError:
    _DISK_BUDGET = 4096 * 1024 * 1024
_disk_lock = threading.Lock()
_disk_used = None        # bytes on disk, counted on first use


def _disk_root():
    import config
    return Path(config.WORKDIR) / "tile_cache"


def disk_source(path) -> str | None:
    """The image file's identity, as a folder name. None if it can't be read."""
    try:
        p = os.path.abspath(str(path))
        st = os.stat(p)
    except OSError:
        return None
    return hashlib.sha1(repr((os.path.normcase(p), st.st_size, st.st_mtime_ns)).encode()).hexdigest()[:20]


def _disk_file(source: str, key: tuple) -> Path:
    return _disk_root() / source / (hashlib.sha1(repr(key).encode()).hexdigest() + ".bin")


def disk_get(source, key: tuple):
    if _DISK_BUDGET <= 0 or not source:
        return None
    f = _disk_file(source, key)
    try:
        data = f.read_bytes()
    except OSError:
        return None
    try:
        os.utime(f)                         # marks it most recently used
    except OSError:
        pass
    return data


def disk_drop(source, key: tuple) -> None:
    """Forget one tile file (found damaged)."""
    if not source:
        return
    try:
        _disk_file(source, key).unlink()
    except OSError:
        pass


def disk_put(source, key: tuple, data: bytes) -> None:
    global _disk_used
    if _DISK_BUDGET <= 0 or not source or not data:
        return
    f = _disk_file(source, key)
    # process AND thread: two copies of the app can share one working folder
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, f)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return
    with _disk_lock:
        if _disk_used is None:
            _disk_used = sum(_sizes())
        _disk_used += len(data)
        if _disk_used > _DISK_BUDGET:
            _disk_trim()


def _sizes():
    for g in _disk_root().glob("*/*.bin"):
        try:
            yield g.stat().st_size
        except OSError:
            pass


def _disk_trim():
    """Oldest-used first, down to 90% of the budget. Called with _disk_lock held."""
    global _disk_used
    import time
    now = time.time()
    for g in _disk_root().glob("*/*.tmp"):     # left by a crash mid-write
        try:
            if now - g.stat().st_mtime > 3600:
                g.unlink()
        except OSError:
            pass
    files = []
    for g in _disk_root().glob("*/*.bin"):
        try:
            st = g.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, g))
    files.sort()
    total = sum(s for _, s, _ in files)
    for _, s, g in files:
        if total <= 0.9 * _DISK_BUDGET:
            break
        try:
            g.unlink()
            total -= s
        except OSError:
            pass
    _disk_used = total
