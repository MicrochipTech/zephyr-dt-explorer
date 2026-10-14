"""Neutral device model.

The core never reads a vendor device file directly; a device data provider
translates the vendor format into these dataclasses. Everything vendor
specific is either opaque to the core (``Signal.mux``, ``Signal.pad``) or
sits in ``attrs``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Package:
    name: str                       # e.g. "TQFP128"
    kind: str                       # "QFP" | "QFN" | "BGA" | ...; selects the layout function
    pin_count: int
    pins: dict[str, str]            # position ("1", "A1") -> pad id or power/other pin name


@dataclass
class Signal:
    role: str                       # "PAD0", "TX", "SDA", ...
    pad: str                        # opaque pin id
    mux: object                     # opaque; only the matching formatter interprets it
    group: str | None = None        # generic group, supplied by the provider
    attrs: dict = field(default_factory=dict)


@dataclass
class Instance:
    name: str                       # "SERCOM2"
    kind: str                       # "SERCOM"
    base: int | None
    signals: list[Signal]


@dataclass
class Constraint:
    kind: str
    severity: str                   # "warning" | "error"
    message: str                    # text supplied by the provider


@dataclass
class DeviceModel:
    name: str                       # "ATSAME54P20A"
    packages: list[Package]
    instances: dict[str, Instance]  # "SERCOM2" -> Instance
    constraints: list[Constraint]
