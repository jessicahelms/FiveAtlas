"""The scientific colormaps the Xenium Explorer offers for its density maps.

17 anchor colours per map, linearly interpolated to 256 -- indistinguishable
from the originals at heat-map scale, with no matplotlib in the bundle.
Anchors sampled from the canonical matplotlib/turbo definitions.
"""
from __future__ import annotations

import numpy as np

_ANCHORS = {
    "viridis": [
        (68, 1, 84), (72, 26, 108), (71, 47, 125), (65, 68, 135),
        (57, 86, 140), (49, 104, 142), (42, 120, 142), (35, 136, 142),
        (31, 152, 139), (34, 168, 132), (53, 183, 121), (84, 197, 104),
        (122, 209, 81), (165, 219, 54), (210, 226, 27), (253, 231, 37),
        (253, 231, 37)],
    "inferno": [
        (0, 0, 4), (12, 8, 38), (36, 12, 79), (66, 10, 104),
        (93, 18, 110), (120, 28, 109), (147, 38, 103), (174, 48, 92),
        (199, 62, 76), (221, 81, 58), (237, 105, 37), (248, 133, 15),
        (252, 165, 10), (250, 198, 45), (242, 230, 97), (252, 255, 164),
        (252, 255, 164)],
    "magma": [
        (0, 0, 4), (11, 9, 36), (32, 17, 75), (59, 15, 112),
        (87, 21, 126), (114, 31, 129), (140, 41, 129), (168, 50, 125),
        (196, 60, 117), (222, 73, 104), (241, 96, 93), (250, 127, 94),
        (254, 159, 109), (254, 191, 132), (252, 222, 158), (252, 253, 191),
        (252, 253, 191)],
    "plasma": [
        (13, 8, 135), (51, 5, 151), (80, 2, 162), (106, 0, 168),
        (132, 5, 167), (156, 23, 158), (177, 42, 144), (195, 61, 128),
        (211, 81, 113), (225, 100, 98), (237, 121, 83), (246, 143, 68),
        (252, 166, 54), (254, 192, 41), (249, 220, 36), (240, 249, 33),
        (240, 249, 33)],
    "turbo": [
        (48, 18, 59), (65, 69, 171), (70, 117, 237), (57, 162, 253),
        (27, 207, 212), (36, 236, 166), (97, 252, 108), (164, 252, 59),
        (215, 232, 36), (246, 199, 26), (254, 155, 45), (243, 105, 30),
        (216, 60, 9), (175, 24, 1), (127, 4, 0), (122, 4, 2),
        (122, 4, 2)],
}


def lut(name):
    """-> (256, 3) uint8, or None for an unknown name."""
    a = _ANCHORS.get(str(name or "").lower())
    if a is None:
        return None
    anchors = np.asarray(a, np.float32)
    x = np.linspace(0, len(anchors) - 1, 256)
    i = np.clip(x.astype(int), 0, len(anchors) - 2)
    f = (x - i)[:, None]
    out = anchors[i] * (1 - f) + anchors[i + 1] * f
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def names():
    return list(_ANCHORS)
