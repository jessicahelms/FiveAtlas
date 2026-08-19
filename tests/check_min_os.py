#!/usr/bin/env python3
"""Refuse to ship a .app whose binaries need a newer macOS than its plist claims.

v0.5.1 said LSMinimumSystemVersion 11.0 while every numpy extension inside it
was built for macOS 14 (pip on a macos-14 runner prefers the `macosx_14_0`
wheel over the `macosx_11_0` one). Finder happily launched it on Monterey and
it died importing numpy. Nothing in the build would have noticed; this does.

Usage:  check_min_os.py FiveAtlas.app           (exit 1 on any violation)
Pure stdlib: parses Mach-O headers directly, no otool needed.
"""
import plistlib
import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32


def _ver(v):
    return f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}"


def _minos_thin(data, off=0):
    magic, = struct.unpack_from("<I", data, off)
    if magic == MH_MAGIC_64:
        end = "<"
    elif magic == MH_CIGAM_64:
        end = ">"
    else:
        return None, None
    _, cputype, _, _, ncmds, _, _, _ = struct.unpack_from(end + "IiiIIIII", data, off)
    p = off + 32
    minos = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from(end + "II", data, p)
        if cmd == LC_BUILD_VERSION:
            _, _, m, _, _ = struct.unpack_from(end + "IIIII", data, p)
            minos = m
        elif cmd == LC_VERSION_MIN_MACOSX and minos is None:
            _, _, m, _ = struct.unpack_from(end + "IIII", data, p)
            minos = m
        p += size
    return cputype, minos


def minos_of(path):
    """-> list of (arch, minos) per slice; [] if not Mach-O."""
    try:
        data = path.read_bytes()
    except Exception:
        return []
    if len(data) < 8:
        return []
    magic, = struct.unpack_from("<I", data, 0)
    out = []
    if magic in (FAT_MAGIC, FAT_CIGAM):
        n, = struct.unpack_from(">I", data, 4)
        for i in range(n):
            cpu, _, off, _, _ = struct.unpack_from(">iiIII", data, 8 + i * 20)
            c, m = _minos_thin(data, off)
            if m is not None:
                out.append((c, m))
    else:
        c, m = _minos_thin(data, 0)
        if m is not None:
            out.append((c, m))
    return out


def main(app):
    app = Path(app)
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    claimed = plist.get("LSMinimumSystemVersion", "0")
    cl = [int(x) for x in claimed.split(".")] + [0, 0]
    claimed_v = (cl[0] << 16) | (cl[1] << 8) | cl[2]
    worst = []
    n = 0
    for p in app.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        for _, m in minos_of(p):
            n += 1
            if m > claimed_v:
                worst.append((m, str(p.relative_to(app))))
    worst.sort(reverse=True)
    print(f"checked {n} Mach-O slices; plist LSMinimumSystemVersion = {claimed}")
    if worst:
        print(f"FAIL: {len(worst)} binaries need a newer macOS than the plist claims:")
        for m, rel in worst[:25]:
            print(f"  {_ver(m):>8}  {rel}")
        if len(worst) > 25:
            print(f"  ... and {len(worst) - 25} more")
        return 1
    top = max((m for p in app.rglob('*') if p.is_file() and not p.is_symlink()
               for _, m in minos_of(p)), default=0)
    print(f"OK: highest minimum among binaries is {_ver(top)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
