"""Board description files: connectors of an evaluation board and the SoC pad behind each pin.

One YAML file per Zephyr board (schema version 1), found by the Zephyr board name in a list of
directories. Generic: no connector standard, SoC family or pad naming is known here; a pad is an opaque
string. ``dt_explorer/boards`` is the built-in directory; further directories can be appended to
``BOARD_DIRS`` (or passed to the functions).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

BOARD_DIRS: list[Path] = [Path(__file__).resolve().parent.parent / "boards"]
SUPPORTED_SCHEMA = 1


@dataclass
class ConnPin:
    number: str                  # pin key as written in the file ("1".."20")
    role: str = ""               # connector role as written ("SPI_MOSI"), "" if none
    pad: str | None = None       # SoC pad behind the pin; None for supply, ID or untraced pins
    net: str = ""
    gap: str = ""                # why the pad could not be traced (importer note)


@dataclass
class Connector:
    name: str                    # "EXT1"
    type: str                    # connector type id, as the connector plugins name it
    pins: dict[str, ConnPin] = field(default_factory=dict)
    designator: str = ""
    status: str = ""             # "reviewed" | "suggested"
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0


@dataclass
class BoardDesc:
    board: str                   # Zephyr board name
    title: str                   # display name: optional ``title`` key of the file, else the board name
    connectors: list[Connector] = field(default_factory=list)
    soc: str = ""
    path: Path | None = None

    def connector(self, name: str) -> Connector | None:
        return next((c for c in self.connectors if c.name == name), None)


def _natural(key: str):
    return (0, int(key), "") if key.isdigit() else (1, 0, key)


def parse_board(data: dict, path: Path | None = None) -> BoardDesc:
    """Build a ``BoardDesc`` from the loaded YAML mapping. Raises ``ValueError`` on a wrong schema."""
    if not isinstance(data, dict) or "board" not in data:
        raise ValueError("not a board description (no 'board' key)")
    ver = data.get("schema_version")
    if ver != SUPPORTED_SCHEMA:
        raise ValueError(f"board description schema_version {ver!r} not supported (expected {SUPPORTED_SCHEMA})")
    board = str(data["board"])
    desc = BoardDesc(board=board, title=str(data.get("title") or board),
                     soc=str((data.get("mcu") or {}).get("soc") or ""), path=path)
    for c in data.get("connectors") or []:
        pos = c.get("position") or {}
        conn = Connector(name=str(c["name"]), type=str(c.get("type") or ""),
                         designator=str(c.get("designator") or ""), status=str(c.get("status") or ""),
                         x=float(pos.get("x") or 0), y=float(pos.get("y") or 0),
                         rotation=float(pos.get("rotation") or 0))
        raw = c.get("pins") or {}
        for key in sorted((str(k) for k in raw), key=_natural):
            p = raw.get(key) if key in raw else raw.get(int(key))
            p = p or {}
            pad = p.get("pad")
            conn.pins[key] = ConnPin(key, str(p.get("role") or ""), str(pad) if pad else None,
                                     str(p.get("net") or ""), str(p.get("gap") or ""))
        desc.connectors.append(conn)
    return desc


def load_board(board: str | None, dirs: list[Path] | None = None) -> BoardDesc | None:
    """The description of Zephyr board `board` (``<board>.yaml`` in the first directory that has it).

    Returns None when `board` is empty or no directory has a file for it. A file that cannot be
    parsed raises ``ValueError`` with the file name.
    """
    if not board:
        return None
    for d in (dirs if dirs is not None else BOARD_DIRS):
        for ext in (".yaml", ".yml"):
            p = Path(d) / f"{board}{ext}"
            if p.is_file():
                try:
                    return parse_board(yaml.safe_load(p.read_text(encoding="utf-8")), p)
                except (yaml.YAMLError, ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"{p}: {exc}") from exc
    return None


def short_role(role: str) -> str:
    """Display form of a connector role: the leading function token is dropped (``X_MOSI`` -> ``MOSI``)."""
    return role.split("_", 1)[1] if "_" in role else role


def is_signal_role(role: str) -> bool:
    """False for supply and ID pins (no pad is expected behind them)."""
    return role.upper() not in ("GND", "VCC", "ID", "")
