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

from typing import Mapping

from ..core.plugins import API_VERSION, register_connector
from . import fraction, hint_matches, is_gnd, is_vcc, normalize

_SPECIAL = {"D0": "UART_RX", "D1": "UART_TX", "D10": "SPI_SS", "D11": "SPI_MOSI",
            "D12": "SPI_MISO", "D13": "SPI_SCK", "D14": "I2C_SDA", "D15": "I2C_SCL"}
ROLES = {
    **{f"A{i}": "ANALOG" for i in range(6)},
    **{f"D{i}": _SPECIAL.get(f"D{i}", "GPIO") for i in range(16)},
    "3V3": "3V3", "5V": "5V", "VIN": "VIN", "GND": "GND", "GND2": "GND", "GND3": "GND",
    "IOREF": "IOREF", "RESET": "RESET", "AREF": "AREF",
}
_SIGNALS = [k for k in ROLES if k[0] in "AD" and k[1:].isdigit()]
_HINT_PINS = {"D0": "RX", "D1": "TX", "D11": "MOSI", "D12": "MISO", "D13": "SCK",
              "D14": "SDA", "D15": "SCL"}

# The four headers of the shield footprint; pin 1 of each header is the first entry. The label of a
# header pin is the pin key of the merged connector (None = pin without a function in the standard).
HEADER_LABELS = {
    "power": [None, "IOREF", "RESET", "3V3", "5V", "GND", "GND2", "VIN"],
    "analog": [f"A{i}" for i in range(6)],
    "digital_low": [f"D{i}" for i in range(8)],
    "digital_high": [f"D{i}" for i in range(8, 14)] + ["GND3", "AREF", "D14", "D15"],
}
HEADER_TITLES = {"power": "Power", "analog": "Analog", "digital_low": "Digital low",
                 "digital_high": "Digital high"}
HEADER_ROWS = [  # row layout for drawing: (kind, keys) in the order of the shield
    ("power", ["IOREF", "RESET", "3V3", "5V", "GND", "GND2", "VIN"]),
    ("analog", HEADER_LABELS["analog"]),
    ("digital_low", HEADER_LABELS["digital_low"]),
    ("digital_high", HEADER_LABELS["digital_high"]),
]


def header_kind(pins: Mapping) -> str | None:
    """Which of the four headers a 1xN part is, from its pad -> net map; None if it is none of them.

    Power: 8 pins, GND on pins 6 and 7, a supply net on pin 5. Analog: 6 pins. Digital high: 10 pins.
    Digital low: 8 pins without any supply or ground net.
    """
    nets = {str(k).strip(): (str(v).strip().upper() if v else "") for k, v in pins.items()}
    n = len(nets)
    if set(nets) != {str(i) for i in range(1, n + 1)}:
        return None
    supply = [v for v in nets.values() if is_gnd(v) or is_vcc(v)]
    if n == 8 and is_gnd(nets["6"]) and is_gnd(nets["7"]) and is_vcc(nets["5"]):
        return "power"
    if n == 8 and not supply:
        return "digital_low"
    if n == 6 and not supply:
        return "analog"
    if n == 10 and is_gnd(nets["7"]) and sum(map(bool, supply)) == 1:
        return "digital_high"
    return None


def merge_headers(headers: Mapping[str, Mapping]) -> tuple[dict[str, str | None], dict[str, tuple[str, str]]]:
    """Merge the four headers (kind -> pad -> net) into label -> net, plus label -> (kind, header pin).

    Missing headers simply leave their labels out; `detect()` scores the result.
    """
    nets: dict[str, str] = {}
    origin: dict[str, tuple[str, str]] = {}
    for kind, pins in headers.items():
        by_pad = {str(k).strip(): v for k, v in pins.items()}
        for i, label in enumerate(HEADER_LABELS[kind], 1):
            if label is not None and str(i) in by_pad:
                nets[label] = by_pad[str(i)]
                origin[label] = (kind, str(i))
    return nets, origin


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
