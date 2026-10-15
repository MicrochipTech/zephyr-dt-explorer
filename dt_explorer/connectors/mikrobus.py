"""mikroBUS socket: two 1x8 rows (16 pins).

Pin keys are "1".."16": 1..8 is the left row (AN, RST, CS, SCK, MISO, MOSI, +3.3V,
GND), 9..16 the right row (PWM, INT, RX, TX, SCL, SDA, +5V, GND), both top to
bottom as in the mikroBUS standard. Role names are those of the Zephyr binding
``mikro-bus``; its gpio-map index 0..5 is physical pin 1..6, index 6..11 is
physical pin 9..14 (the power pins are not in the nexus).
"""
from __future__ import annotations

import re
from typing import Mapping

from ..core.plugins import API_VERSION, register_connector
from . import fraction, hint_matches, is_gnd, is_vcc, normalize

ROLES = {
    "1": "AN", "2": "RST", "3": "CS", "4": "SCK", "5": "MISO", "6": "MOSI",
    "7": "3V3", "8": "GND",
    "9": "PWM", "10": "INT", "11": "RX", "12": "TX", "13": "SCL", "14": "SDA",
    "15": "5V", "16": "GND",
}
_HINT_PINS = {p: r for p, r in ROLES.items() if r not in ("3V3", "5V", "GND")}

_V33 = re.compile(r"3V3|3P3|3\.3", re.I)
_V50 = re.compile(r"5V|5P0|5\.0", re.I)


def merge_rows(a: Mapping, b: Mapping) -> tuple[dict[str, str | None], dict[str, tuple[str, str]], tuple[int, int]] | None:
    """Merge the two 1x8 headers of one socket into pin keys "1".."16".

    `a` and `b` map header pad "1".."8" to a net. The left row (keys 1..8) is the header whose pin 7 is
    the 3.3 V net, the right row (keys 9..16) the one whose pin 7 is the 5 V net. Returns
    (key -> net, key -> (0|1 = index of the argument, header pad), (index of left, index of right)),
    or None when the supply pins do not identify the rows.
    """
    pa, pb = ({str(k).strip(): v for k, v in x.items()} for x in (a, b))
    if "7" not in pa or "7" not in pb:
        return None
    if _V33.search(pa["7"] or "") and _V50.search(pb["7"] or ""):
        order = (0, 1)
    elif _V33.search(pb["7"] or "") and _V50.search(pa["7"] or ""):
        order = (1, 0)
    else:
        return None
    rows = (pa, pb)
    nets: dict[str, str | None] = {}
    origin: dict[str, tuple[str, str]] = {}
    for offset, idx in zip((0, 8), order):
        for pad in (str(i) for i in range(1, 9)):
            if pad in rows[idx]:
                nets[str(int(pad) + offset)] = rows[idx][pad]
                origin[str(int(pad) + offset)] = (str(idx), pad)
    return nets, origin, order


@register_connector
class MikroBus:
    api_version = API_VERSION
    name = "mikrobus"
    zephyr_binding = "mikro-bus"
    pin_count = 16

    def pin_roles(self) -> dict[str, str]:
        return dict(ROLES)

    def nexus_map(self) -> dict[int, str]:
        return {**{i: str(i + 1) for i in range(6)}, **{i: str(i + 3) for i in range(6, 12)}}

    def detect(self, netlist_pins, pin_count: int | None = None) -> float:
        pins = normalize(netlist_pins)
        count = pin_count if pin_count is not None else len(pins)
        score = 0.2 if count == self.pin_count else 0.0
        score += 0.1 * (is_vcc(pins.get("7", "")) + is_gnd(pins.get("8", ""))
                        + is_vcc(pins.get("15", "")) + is_gnd(pins.get("16", "")))
        # names: saturates at 6 hits out of 12 signal pins
        hits = sum(hint_matches(pins.get(p, ""), r) for p, r in _HINT_PINS.items())
        score += 0.4 * fraction(hits, 6)
        bad = sum(is_gnd(pins.get(p, "")) or is_vcc(pins.get(p, "")) for p in _HINT_PINS)
        return round(max(0.0, min(1.0, score - 0.1 * bad)), 3)
