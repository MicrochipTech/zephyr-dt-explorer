"""Pinctrl formatter for the new Zephyr port ``microchip,port-g1-pinctrl``.

The macro names have the same style as in the old port: pad ``PB25``, mux ``D``, role ``PAD0`` of
instance ``SERCOM2`` give ``PB25D_SERCOM2_PAD0`` (the DFP writes ``PA09``, Zephyr ``PA9``). What
differs is the header (``hal_microchip``, ``atsame54p20a-pinctrl.h``): every macro is one line,
``#define PB25D_SERCOM2_PAD0 MCHP_PINMUX(b, 25, d, periph)``, instead of ``SAM_PINMUX(B, 25, D, ..)``
with a continuation line. ``MCHP_PINMUX(port, pin, mux, func)`` expands to the integer
``port | pin << 4 | func << 9 | periph << 12`` (see ``pinmux_value``). Every generated name is
checked against the ``#define``s of that header; a name missing there is an error, never a guess.

The neutral Signal does not carry its instance name. It is taken from ``signal.attrs["instance"]``
or the ``instance=`` argument; if both are missing, the instance is looked up in the header and
must be unique for pad, mux and role.

Helpers are copied from ``atmel_sam0`` on purpose (duplication accepted, no cross import).
Imports only ``dt_explorer.core.*``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dt_explorer.core.device import Signal
from dt_explorer.core.plugins import register_formatter

_DEFINE_RE = re.compile(r"^#define\s+(P[A-Z]\d+[A-Z]?_\w+)\s+MCHP_PINMUX\(\s*(\w+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)",
                        re.MULTILINE)
_MACRO_RE = re.compile(r"^(P[A-Z])(\d+)([A-Z])_([A-Za-z0-9]+)_(\w+)$")
_PAD_RE = re.compile(r"^(P[A-Z])(\d+)$")
_TOKEN_RE = re.compile(r"<\s*(P[A-Z]\d+[A-Z]?_\w+)\s*>")

HEADER_REL = Path("modules/hal/microchip/include/dt-bindings/sam/sam_d5x_e5x")


class FormatterError(ValueError):
    """A name cannot be formed, parsed or found in the Zephyr header."""


@dataclass(frozen=True)
class ParsedPin:
    pad: str        # neutral pad, "PB25"
    mux: str        # function letter, "D"
    instance: str   # "SERCOM2"
    role: str       # "PAD0"
    macro: str      # "PB25D_SERCOM2_PAD0"


def zephyr_pad(pad: str) -> str:
    """Neutral pad -> Zephyr spelling: 'PA09' -> 'PA9'."""
    m = _PAD_RE.match(pad)
    if not m:
        raise FormatterError(f"not a pad name: {pad!r}")
    return f"{m.group(1)}{int(m.group(2))}"


def neutral_pad(zpad: str) -> str:
    """Zephyr pad -> neutral spelling: 'PA9' -> 'PA09'."""
    m = _PAD_RE.match(zpad)
    if not m:
        raise FormatterError(f"not a pad name: {zpad!r}")
    return f"{m.group(1)}{int(m.group(2)):02d}"


_header_cache: dict[Path, dict[str, tuple]] = {}


def load_header(path: Path) -> dict[str, tuple]:
    """Parse a pinctrl header once: macro name -> (port, pin, function, kind)."""
    path = Path(path)
    key = path.resolve()
    if key not in _header_cache:
        text = path.read_text(encoding="utf-8", errors="replace")
        _header_cache[key] = {
            m.group(1): (m.group(2).upper(), int(m.group(3)), m.group(4).upper(), m.group(5))
            for m in _DEFINE_RE.finditer(text)
        }
    return _header_cache[key]


_FUNC = {"gpio": 0, "periph": 1, "extra": 2, "system": 3, "lpm": 4, "wakeup": 5}


def pinmux_value(port: str, pin: int, mux: str, func: str) -> int:
    """Integer that ``MCHP_PINMUX(port, pin, mux, func)`` expands to (port a=0, mux a=0, gpio=0)."""
    mux = mux.lower()
    periph = 0 if mux == "gpio" else ord(mux) - ord("a")
    return (ord(port.lower()) - ord("a")) | (pin & 0x1F) << 4 | _FUNC[func] << 9 | periph << 12


def find_header(workspace: Path, soc: str) -> Path:
    """Header of a west workspace, e.g. soc 'same54p20a' -> ``atsame54/atsame54p20a-pinctrl.h``."""
    m = re.match(r"^(?:at)?(same\d\d)([a-z]\d+[a-z]?)", soc.lower())
    if not m:
        raise FormatterError(f"cannot derive a pinctrl header name from SoC {soc!r}")
    chip = f"at{m.group(1)}{m.group(2)}"
    path = Path(workspace) / HEADER_REL / f"at{m.group(1)}" / f"{chip}-pinctrl.h"
    if not path.is_file():
        raise FormatterError(f"pinctrl header not found: {path}")
    return path


class MicrochipPortG1Formatter:
    api_version = 1
    name = "microchip_port_g1"
    compatibles = ["microchip,port-g1-pinctrl"]

    def __init__(self, header: Path | str | None = None):
        self.header = Path(header) if header else None

    @classmethod
    def for_workspace(cls, workspace: Path, soc: str) -> "MicrochipPortG1Formatter":
        return cls(find_header(workspace, soc))

    @property
    def defines(self) -> dict[str, tuple]:
        if self.header is None:
            raise FormatterError("no pinctrl header configured (pass header= or use for_workspace)")
        return load_header(self.header)

    def _check(self, macro: str) -> str:
        if macro not in self.defines:
            raise FormatterError(f"{macro} is not defined in {self.header}")
        return macro

    def to_overlay(self, signal: Signal, instance: str | None = None) -> str:
        zpad = zephyr_pad(signal.pad)
        if not isinstance(signal.mux, str) or not re.fullmatch(r"[A-Za-z]", signal.mux):
            raise FormatterError(f"mux of {signal.pad} is not a function letter: {signal.mux!r}")
        prefix = f"{zpad}{signal.mux.upper()}_"
        instance = instance or signal.attrs.get("instance")
        if instance:
            return self._check(f"{prefix}{instance}_{signal.role}")
        found = [n for n in self.defines
                 if n.startswith(prefix) and n.endswith(f"_{signal.role}")
                 and _MACRO_RE.match(n) and _MACRO_RE.match(n).group(5) == signal.role]
        if len(found) != 1:
            raise FormatterError(
                f"cannot resolve instance for {signal.pad}/{signal.mux}/{signal.role}: "
                f"{len(found)} candidates in header {found}")
        return found[0]

    def parse(self, value: str) -> ParsedPin:
        """Macro name (optionally in ``<...>``) -> ParsedPin; checked against the header."""
        name = value.strip().strip("<>").strip()
        m = _MACRO_RE.match(name)
        if not m:
            raise FormatterError(f"not a pinmux macro name: {value!r}")
        self._check(name)
        return ParsedPin(neutral_pad(f"{m.group(1)}{m.group(2)}"), m.group(3), m.group(4), m.group(5), name)

    def from_overlay(self, value: str) -> str:
        """Macro name -> neutral pad (``PB25D_SERCOM2_PAD0`` -> ``PB25``). Use parse() for the rest."""
        return self.parse(value).pad

    def render_group(self, signals: list[Signal], name: str = "group1") -> str:
        if not signals:
            raise FormatterError("a pinctrl group needs at least one signal")
        macros = [self.to_overlay(s) for s in signals]
        body = ",\n\t\t ".join(f"<{m}>" for m in macros)
        return f"{name} {{\n\tpinmux = {body};\n}};"


def parse_dtsi_groups(text: str) -> dict[str, list[str]]:
    """Pinctrl nodes of a board ``*-pinctrl.dtsi``: node label -> list of macro names (all groups)."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^\t(\w+):\s*\w+\s*\{(.*?)^\t\};", text, re.MULTILINE | re.DOTALL):
        out[m.group(1)] = _TOKEN_RE.findall(m.group(2))
    return out


register_formatter(MicrochipPortG1Formatter)
