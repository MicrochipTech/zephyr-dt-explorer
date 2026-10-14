"""Atmel/Microchip Xplained Pro extension header (EXTn): 2x10 pins, 2.54 mm.

Pin keys are the physical pin numbers "1".."20" (odd pins on one row, even on
the other). Roles follow the Zephyr binding ``atmel-xplained-pro-header``;
its gpio-map index n (0..15) is physical pin n + 3.
"""
from __future__ import annotations

from ..core.plugins import API_VERSION, register_connector
from . import fraction, hint_matches, is_gnd, is_vcc, normalize

ROLES = {
    "1": "ID", "2": "GND",
    "3": "ADC+", "4": "ADC-",
    "5": "GPIO1", "6": "GPIO2",          # binding: UART(RTS)/GPIO1, UART(CTS)/GPIO2
    "7": "PWM+", "8": "PWM-",
    "9": "IRQ", "10": "SPI_SS_B",        # binding: IRQ/GPIO3, SPI(CS1)/GPIO4
    "11": "I2C_SDA", "12": "I2C_SCL",
    "13": "UART_RX", "14": "UART_TX",
    "15": "SPI_SS_A", "16": "SPI_MOSI",  # binding: SPI(CS0), SPI(MOSI)
    "17": "SPI_MISO", "18": "SPI_SCK",
    "19": "GND", "20": "VCC",
}
# role keyword checked against the net name at that pin
_HINT_PINS = {"7": "PWM", "8": "PWM", "9": "IRQ", "11": "SDA", "12": "SCL", "13": "RX",
              "14": "TX", "16": "MOSI", "17": "MISO", "18": "SCK"}


@register_connector
class XplainedProExt:
    api_version = API_VERSION
    name = "xplained_pro_ext"
    zephyr_binding = "atmel-xplained-pro-header"
    pin_count = 20

    def pin_roles(self) -> dict[str, str]:
        return dict(ROLES)

    def nexus_map(self) -> dict[int, str]:
        """Zephyr gpio-map index -> physical pin key (pins 3..18)."""
        return {n: str(n + 3) for n in range(16)}

    def detect(self, netlist_pins, pin_count: int | None = None) -> float:
        pins = normalize(netlist_pins)
        count = pin_count if pin_count is not None else len(pins)
        score = 0.2 if count == self.pin_count else 0.0
        # supply and ID positions: 0.1 each
        score += 0.1 if is_gnd(pins.get("2", "")) else 0.0
        score += 0.1 if is_gnd(pins.get("19", "")) else 0.0
        score += 0.1 if is_vcc(pins.get("20", "")) else 0.0
        score += 0.1 if hint_matches(pins.get("1", ""), "ID") else 0.0
        # net names that look like the roles: saturates at 5 hits
        hits = sum(hint_matches(pins.get(p, ""), r) for p, r in _HINT_PINS.items())
        score += 0.4 * fraction(hits, 5)
        # GND/VCC on a signal pin contradicts the standard
        bad = sum(is_gnd(pins.get(p, "")) or is_vcc(pins.get(p, "")) for p in _HINT_PINS)
        return round(max(0.0, min(1.0, score - 0.1 * bad)), 3)
