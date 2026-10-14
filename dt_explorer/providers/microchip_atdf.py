"""Microchip ATDF device data provider.

Finds the ATDF ("Atmel Device File") of a build's SoC and loads it into the neutral
DeviceModel. All Microchip terms (ATDF, IOSET, DFP packs) live in this module.

Lookup order: hal_microchip packs of the workspace, ``%USERPROFILE%\\.mchp_packs``, MPLAB X
packs. Every root is searched by pattern at a known depth, never recursively.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from dt_explorer.core.device import Constraint, DeviceModel, Instance, Package, Signal
from dt_explorer.core.plugins import register_provider

_SOC_RE = re.compile(r"^(sam|pic32c)", re.IGNORECASE)
_VENDORS = {"atmel", "microchip"}

IOSET_WARNING = (
    "Pads of one peripheral instance should come from the same IOSET; pads from "
    "different IOSETs may violate the device's timing/electrical specification."
)


def natural_key(text: str) -> list:
    """Sort key splitting digit runs: '3.11.261' > '3.9.244'."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in re.split(r"(\d+)", text) if p]


def atdf_name(soc: str) -> str:
    """CONFIG_SOC ('same54p20a') -> ATDF file name ('ATSAME54P20A.atdf')."""
    up = soc.upper()
    return ("AT" + up if up.startswith("SAM") else up) + ".atdf"


def _subdirs(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(path) if e.is_dir()]
    except OSError:
        return []


def _find_file(directory: Path, filename: str) -> Path | None:
    """Case-insensitive lookup of one file in one directory (no recursion)."""
    wanted = filename.lower()
    try:
        for e in os.scandir(directory):
            if e.name.lower() == wanted and e.is_file():
                return Path(e.path)
    except OSError:
        pass
    return None


def _natural_desc(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: natural_key(p.name), reverse=True)


def _hal_roots(info) -> list[Path]:
    roots: list[Path] = []
    for name in ("hal_microchip", "microchip"):
        mod = getattr(info, "modules", {}).get(name)
        if mod:
            roots.append(Path(mod) / "packs")
    ws = getattr(info, "workspace", None)
    if ws:
        roots.append(Path(ws) / "modules" / "hal" / "microchip" / "packs")
    return roots


def _search_hal(root: Path, filename: str) -> Path | None:
    # packs/<sam|pic32c>/<family>/<series>/atdf/<file>
    for d1 in _subdirs(root):
        for d2 in _subdirs(d1):
            for d3 in _subdirs(d2):
                hit = _find_file(d3 / "atdf", filename)
                if hit:
                    return hit
    return None


def _dfp_roots() -> list[Path]:
    """Pack roots (the folders that contain 'Microchip'), in lookup order."""
    roots: list[Path] = []
    home = os.environ.get("USERPROFILE") or str(Path.home())
    roots.append(Path(home) / ".mchp_packs")
    mplab = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microchip" / "MPLABX"
    for v in _natural_desc(_subdirs(mplab)):
        if v.name.lower().startswith("v"):
            roots.append(v / "packs")
    return roots


def _search_dfp(root: Path, filename: str) -> Path | None:
    # <root>/Microchip/*_DFP/<version>/atdf/<file>
    for dfp in sorted(_subdirs(root / "Microchip"), key=lambda p: p.name):
        if not dfp.name.upper().endswith("_DFP"):
            continue
        for ver in _natural_desc(_subdirs(dfp)):
            hit = _find_file(ver / "atdf", filename)
            if hit:
                return hit
    return None


def _package_kind(name: str) -> str:
    n = name.upper()
    if "BGA" in n:
        return "BGA"
    if "QFN" in n or "VQFN" in n or "WLCSP" in n:
        return "QFN"
    if "QFP" in n:
        return "QFP"
    return "OTHER"


def _int(text: str | None) -> int | None:
    try:
        return int(text, 0) if text else None
    except ValueError:
        return None


@register_provider
class MicrochipAtdfProvider:
    api_version = 1
    name = "microchip-atdf"

    def matches(self, info) -> bool:
        vendor = (getattr(info, "vendor", None) or "").lower()
        if vendor in _VENDORS:
            return True
        return bool(info.soc and _SOC_RE.match(info.soc))

    def locate(self, info) -> Path | None:
        if not info.soc:
            return None
        filename = atdf_name(info.soc)
        for root in _hal_roots(info):
            hit = _search_hal(root, filename)
            if hit:
                return hit
        for root in _dfp_roots():
            hit = _search_dfp(root, filename)
            if hit:
                return hit
        return None

    def load(self, path: Path) -> DeviceModel:
        root = ET.parse(path).getroot()
        device = root.find("devices/device")
        name = device.get("name") if device is not None else Path(path).stem

        packages = self._packages(root)
        instances: dict[str, Instance] = {}
        has_ioset = False
        if device is not None:
            for module in device.findall("peripherals/module"):
                for inst in module.findall("instance"):
                    signals = []
                    for s in inst.findall("signals/signal"):
                        group = s.get("group", "")
                        index = s.get("index")
                        ioset = s.get("ioset")
                        has_ioset = has_ioset or ioset is not None
                        signals.append(Signal(
                            role=f"{group}{index}" if index is not None else group,
                            pad=s.get("pad", ""),
                            mux=s.get("function"),
                            group=ioset,
                            attrs={"signal_group": group, "index": _int(index), "instance": inst.get("name")},
                        ))
                    rg = inst.find("register-group")
                    base = _int(rg.get("offset")) if rg is not None else None
                    kind = (rg.get("name-in-module") if rg is not None else None) or module.get("name")
                    instances[inst.get("name")] = Instance(inst.get("name"), kind, base, signals)

        constraints = []
        if has_ioset:
            constraints.append(Constraint("shared_group", "warning", IOSET_WARNING))
        return DeviceModel(name, packages, instances, constraints)

    @staticmethod
    def _packages(root) -> list[Package]:
        pinouts = {p.get("name"): p for p in root.findall("pinouts/pinout")}
        seen: set[tuple[str, str]] = set()
        packages = []
        for v in root.findall("variants/variant"):
            key = (v.get("package"), v.get("pinout"))
            if key in seen or key[1] not in pinouts:
                continue
            seen.add(key)
            # Pins with a "type" attribute (INTERNAL_n) are not on the outside of the package.
            pins = {p.get("position"): p.get("pad") for p in pinouts[key[1]].findall("pin")
                    if p.get("type") is None}
            packages.append(Package(key[0], _package_kind(key[0]), len(pins), pins))
        return packages
