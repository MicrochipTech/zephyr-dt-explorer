"""mikroBUS socket: two 1x8 rows (16 pins).

Pin keys are "1".."16": 1..8 is the left row (AN, RST, CS, SCK, MISO, MOSI, +3.3V,
GND), 9..16 the right row (PWM, INT, RX, TX, SCL, SDA, +5V, GND), both top to
bottom as in the mikroBUS standard. Role names are those of the Zephyr binding
``mikro-bus``; its gpio-map index 0..5 is physical pin 1..6, index 6..11 is
physical pin 9..14 (the power pins are not in the nexus).
"""
from __future__ import annotations

from ..core.plugins import API_VERSION, register_connector
from . import fraction, hint_matches, is_gnd, is_vcc, normalize

ROLES = {
    "1": "AN", "2": "RST", "3": "CS", "4": "SCK", "5": "MISO", "6": "MOSI",
    "7": "3V3", "8": "GND",
    "9": "PWM", "10": "INT", "11": "RX", "12": "TX", "13": "SCL", "14": "SDA",
    "15": "5V", "16": "GND",
}
_HINT_PINS = {p: r for p, r in ROLES.items() if r not in ("3V3", "5V", "GND")}


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
