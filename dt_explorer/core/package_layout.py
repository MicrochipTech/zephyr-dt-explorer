"""Geometry of chip packages (pure functions, no UI).

Unit coordinate system: one pin pitch is 1.0, x grows to the right, y grows downwards. Every layout
returns the body rectangle and, per package position, the centre of the pin on the body edge (for
leaded and flat packages) or of the ball (for grids), the side it sits on, and the rotation in degrees
that a text label for this pin should have so that it fits between neighbours.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# JEDEC row letters of ball grids: I, O, Q, S, X and Z are not used.
_JEDEC_SINGLE = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOQSXZ"]
_JEDEC_ROWS = _JEDEC_SINGLE + [a + b for a in _JEDEC_SINGLE for b in _JEDEC_SINGLE]

_BALL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


@dataclass
class PinPos:
    x: float
    y: float
    side: str           # "left" | "bottom" | "right" | "top" | "grid"
    rotation: int       # label rotation in degrees (0 = horizontal)


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float


@dataclass
class PackageLayout:
    kind: str                                   # "QFP" | "QFN" | "BGA"
    body: Rect
    pins: dict[str, PinPos] = field(default_factory=dict)
    extras: dict[str, Rect] = field(default_factory=dict)   # positions without a place on the outline
    per_side: int = 0                           # edge packages
    rows: list[str] = field(default_factory=list)           # grids: row names, top to bottom
    cols: list[int] = field(default_factory=list)           # grids: column numbers, left to right


def edge_layout(pin_count: int, positions, kind: str = "QFP") -> PackageLayout:
    """Four-sided package. Pin 1 is the top pin of the left side; numbering runs counter-clockwise:
    left side top to bottom, bottom side left to right, right side bottom to top, top side right to
    left. Positions that are not a number in 1..pin_count (exposed pad, "129") go to ``extras``."""
    if pin_count < 4 or pin_count % 4:
        raise ValueError(f"pin count {pin_count} is not a multiple of four")
    n = pin_count // 4
    size = n + 1
    lay = PackageLayout(kind, Rect(0, 0, size, size), per_side=n)
    extra = []
    for pos in positions:
        if not (pos.isdigit() and 1 <= int(pos) <= pin_count):
            extra.append(pos)
            continue
        s, k = divmod(int(pos) - 1, n)
        if s == 0:
            lay.pins[pos] = PinPos(0, k + 1, "left", 0)
        elif s == 1:
            lay.pins[pos] = PinPos(k + 1, size, "bottom", 90)
        elif s == 2:
            lay.pins[pos] = PinPos(size, n - k, "right", 0)
        else:
            lay.pins[pos] = PinPos(n - k, 0, "top", 90)
    if extra:
        # exposed pads share the middle of the body, side by side
        w = (size * 0.5) / len(extra)
        for j, pos in enumerate(sorted(extra)):
            lay.extras[pos] = Rect(size * 0.25 + j * w, size * 0.25, w, size * 0.5)
    return lay


def _row_index(names: list[str]) -> dict[str, int]:
    """Row name -> grid row. JEDEC order keeps gaps; unknown names fall back to sorted order."""
    if all(n.upper() in _JEDEC_ROWS for n in names):
        idx = {n: _JEDEC_ROWS.index(n.upper()) for n in names}
        base = min(idx.values())
        return {n: i - base for n, i in idx.items()}
    ordered = sorted(set(names), key=lambda s: (len(s), s))
    return {n: i for i, n in enumerate(ordered)}


def grid_layout(positions) -> PackageLayout:
    """Ball grid from the ball names (letters = row, number = column); A1 is top-left. Rows and
    columns are derived from the names that are present, not from a pin count."""
    balls, extra = {}, []
    for pos in positions:
        m = _BALL_RE.match(pos)
        if m:
            balls[pos] = (m.group(1), int(m.group(2)))
        else:
            extra.append(pos)
    if not balls:
        raise ValueError("no ball names in the package")
    rmap = _row_index([r for r, _ in balls.values()])
    cmin = min(c for _, c in balls.values())
    cmax = max(c for _, c in balls.values())
    nrows = max(rmap.values()) + 1
    ncols = cmax - cmin + 1
    lay = PackageLayout("BGA", Rect(0, 0, ncols + 1, nrows + 1))
    for pos, (r, c) in balls.items():
        lay.pins[pos] = PinPos(c - cmin + 1, rmap[r] + 1, "grid", 0)
    by_idx = {i: n for n, i in rmap.items()}
    base = _JEDEC_ROWS.index(min(by_idx.values(), key=lambda n: rmap[n]).upper())         if all(n.upper() in _JEDEC_ROWS for n in by_idx.values()) else None
    lay.rows = [by_idx.get(i) or (_JEDEC_ROWS[base + i] if base is not None else "") for i in range(nrows)]
    lay.cols = list(range(cmin, cmax + 1))
    for j, pos in enumerate(sorted(extra)):
        lay.extras[pos] = Rect(0.2 + j * 0.9, nrows + 1.2, 0.8, 0.8)
    return lay


def layout_for(kind: str, pin_count: int, positions) -> PackageLayout:
    """Layout for a package kind; raises ValueError for kinds without a layout function."""
    k = kind.upper()
    if k in ("QFP", "QFN"):
        return edge_layout(pin_count, positions, k)
    if k == "BGA":
        return grid_layout(positions)
    raise ValueError(f"no layout for package kind {kind!r}")
