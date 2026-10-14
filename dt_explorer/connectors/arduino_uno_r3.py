"""Arduino Uno R3 headers (power 8, analog 6, digital 8 + 10 pins).

The four headers are separate parts on a PCB, so pin keys are the Arduino
*labels*, not numbers; the importer has to merge the headers into one mapping.
Signal keys are "A0".."A5" and "D0".."D15" (D14/D15 are the SDA/SCL pins next to
AREF, as in the Zephyr binding ``arduino-header-r3``); power keys are "3V3",
"5V", "VIN", "GND", "IOREF", "RESET", "AREF". Several GND pins may be given as
"GND", "GND2", "GND3" (a trailing digit is allowed).

gpio-map index of the binding: 0..5 = A0..A5, 6..21 = D0..D15.
"""
from __future__ import annotations

import re

from ..core.plugins import API_VERSION, register_connector
from . import fraction, hint_matches, is_gnd, is_vcc, normalize

_SPECIAL = {"D0": "UART_RX", "D1": "UART_TX", "D10": "SPI_SS", "D11": "SPI_MOSI",
            "D12": "SPI_MISO", "D13": "SPI_SCK", "D14": "I2C_SDA", "D15": "I2C_SCL"}
ROLES = {
    **{f"A{i}": "ANALOG" for i in range(6)},
    **{f"D{i}": _SPECIAL.get(f"D{i}", "GPIO") for i in range(16)},
    "3V3": "3V3", "5V": "5V", "VIN": "VIN", "GND": "GND", "IOREF": "IOREF",
    "RESET": "RESET", "AREF": "AREF",
}
_SIGNALS = [k for k in ROLES if k[0] in "AD" and k[1:].isdigit()]
_HINT_PINS = {"D0": "RX", "D1": "TX", "D11": "MOSI", "D12": "MISO", "D13": "SCK",
              "D14": "SDA", "D15": "SCL"}


@register_connector
class ArduinoUnoR3:
    api_version = API_VERSION
    name = "arduino_uno_r3"
    zephyr_binding = "arduino-header-r3"

    def pin_roles(self) -> dict[str, str]:
        return dict(ROLES)

    def nexus_map(self) -> dict[int, str]:
        return {**{i: f"A{i}" for i in range(6)}, **{6 + i: f"D{i}" for i in range(16)}}

    def detect(self, netlist_pins, pin_count: int | None = None) -> float:
        pins = normalize(netlist_pins)
        keys = {k.upper() for k in pins}
        # signal labels present (22 of them): 0.45
        score = 0.45 * fraction(len(keys & set(_SIGNALS)), len(_SIGNALS))
        # supply labels with matching nets: 0.1 each
        gnd = [n for k, n in pins.items() if re.fullmatch(r"GND\d*", k.upper())]
        score += 0.1 if any(is_gnd(n) for n in gnd) else 0.0
        score += 0.1 if is_vcc(pins.get("3V3", "")) else 0.0
        score += 0.1 if is_vcc(pins.get("5V", "")) else 0.0
        # net names that look like the fixed-function pins: 0.25
        hits = sum(hint_matches(pins.get(p, ""), r) for p, r in _HINT_PINS.items())
        score += 0.25 * fraction(hits, 4)
        return round(max(0.0, min(1.0, score)), 3)
