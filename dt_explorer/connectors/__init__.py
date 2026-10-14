"""Connector type plugins: standard pinouts (pin roles) and detection.

``detect()`` input format (all connector types): a mapping *connector pin ->
net name* (``None`` or ``""`` for an unconnected pin). Pin keys may be ``str``
or ``int``; they are normalised with ``str(key).strip()``. The pin key is the
physical pin label of the connector type (see each module). The optional
``pin_count`` argument overrides ``len(netlist_pins)`` when the caller knows the
connector has more pads than listed.

Each connector type also offers ``nexus_map()``: Zephyr gpio-map index (the
``<n 0 &gpio ...>`` entries of the standard binding) -> key in ``pin_roles()``.

The result is a deterministic score in 0..1 from pin count, positions of
GND/VCC/ID pins and net names that look like the pin roles.
"""
from __future__ import annotations

import re
from typing import Mapping

_GND = re.compile(r"GND|VSS|^[+-]?0V$")
_VCC = re.compile(r"VCC|VDD|3V3|3\.3V|5V|VBUS|VTG|VTARGET|P3V3|VIN|PWR")

# role hint -> accepted spellings. Short hints (<= 3 chars) are matched as
# whole tokens of the net name, longer ones as substrings.
_HINTS = {
    "SDA": ("SDA", "TWD"),
    "SCL": ("SCL", "TWCK", "TWCLK"),
    "MOSI": ("MOSI",),
    "MISO": ("MISO",),
    "SCK": ("SCK", "SCLK"),
    "RX": ("RX", "RXD"),
    "TX": ("TX", "TXD"),
    "PWM": ("PWM",),
    "IRQ": ("IRQ", "INT"),
    "INT": ("INT", "IRQ"),
    "RST": ("RST", "RESET", "NRST"),
    "CS": ("CS", "SS", "CSN", "NSS"),
    "AN": ("AN", "AIN", "ADC"),
    "ID": ("ID",),
}


def normalize(netlist_pins: Mapping) -> dict[str, str]:
    """Pin keys as stripped strings, net names upper-cased; unconnected pins -> ''."""
    return {str(k).strip(): (str(v).strip().upper() if v else "") for k, v in netlist_pins.items()}


def is_gnd(net: str) -> bool:
    return bool(net) and bool(_GND.search(net.upper()))


def is_vcc(net: str) -> bool:
    n = net.upper()
    return bool(n) and not is_gnd(n) and bool(_VCC.search(n))


def _tokens(net: str) -> list[str]:
    toks = [t for t in re.split(r"[^A-Z0-9]+", net.upper()) if t]
    return toks + [t.rstrip("0123456789") for t in toks if t.rstrip("0123456789")]


def hint_matches(net: str, role: str) -> bool:
    """True if the net name looks like `role` (SDA, MOSI, TX, ...)."""
    if not net:
        return False
    toks = _tokens(net)
    for h in _HINTS.get(role, (role,)):
        if len(h) <= 3:
            if h in toks:
                return True
        elif h in net.upper():
            return True
    return False


def fraction(hits: int, of: int) -> float:
    return min(1.0, hits / of) if of else 0.0


from . import xplained_pro_ext, arduino_uno_r3, mikrobus  # noqa: E402,F401  (register on import)
