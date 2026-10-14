"""Microchip ATDF provider: fixture tests (no env vars) and real-workspace tests (DTX_RTOS4_WS)."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.buildinfo import BuildInfo, read_build_info
from dt_explorer.providers import microchip_atdf
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider, atdf_name, natural_key

FIX = Path(__file__).resolve().parent / "fixtures" / "atdf"
E54 = FIX / "ATSAME54P20A.atdf"
SG61 = FIX / "PIC32CX1025SG61128.atdf"

provider = MicrochipAtdfProvider()


def _pkg(model, name):
    return next(p for p in model.packages if p.name == name)


def _pads(model, name="TQFP128"):
    """GPIO pads (PAxx..PDxx) of a package; power pins are named differently per family."""
    return {p for p in _pkg(model, name).pins.values() if re.fullmatch(r"P[A-D]\d\d", p)}


# --- fixtures (no environment needed) ----------------------------------------------------------


def test_sercom2_pad0_pads_and_mux():
    m = provider.load(E54)
    pad0 = [s for s in m.instances["SERCOM2"].signals if s.role == "PAD0"]
    assert {(s.pad, s.mux) for s in pad0} == {("PA09", "D"), ("PB25", "D"), ("PA12", "C"), ("PB26", "C")}
    assert len(pad0) == 4
    assert {s.pad: s.group for s in pad0} == {"PA09": "3", "PB25": "4", "PA12": "1", "PB26": "2"}


def test_instance_fields():
    m = provider.load(E54)
    sercom2 = m.instances["SERCOM2"]
    assert (m.name, sercom2.kind, sercom2.base) == ("ATSAME54P20A", "SERCOM", 0x41012000)
    assert sum(1 for i in m.instances.values() if i.kind == "SERCOM") == 8


def test_tqfp128_has_128_positions():
    m = provider.load(E54)
    pkg = _pkg(m, "TQFP128")
    assert pkg.pin_count == 128 and len(pkg.pins) == 128 and pkg.kind == "QFP"
    assert _pkg(m, "TFBGA120").kind == "BGA" and _pkg(m, "TFBGA120").pin_count == 120


def test_ioset_constraint():
    m = provider.load(E54)
    assert [(c.kind, c.severity) for c in m.constraints] == [("shared_group", "warning")]
    assert "IOSET" in m.constraints[0].message


def test_sg61_lacks_five_pads():
    e54, sg61 = provider.load(E54), provider.load(SG61)
    missing = {"PB26", "PB27", "PB28", "PB29", "PC04"}
    assert [p.name for p in sg61.packages] == ["TQFP128"]
    assert sg61.packages[0].pin_count == 128
    assert missing <= _pads(e54)
    assert missing.isdisjoint(_pads(sg61))
    assert _pads(e54) - _pads(sg61) == missing
    # The pin functions follow, with one oddity of the SG61 ATDF: SERCOM2 PAD0-2 still list
    # PB26-PB28 (those pads are INTERNAL_n entries in the pinout). Everything else is gone.
    def users(m):
        return {(i.name, s.role, s.pad) for i in m.instances.values() for s in i.signals if s.pad in missing}
    assert {n for n, _, _ in users(e54)} == {"EIC", "SERCOM2", "SERCOM4", "SERCOM6"}
    assert users(sg61) == {("SERCOM2", "PAD0", "PB26"), ("SERCOM2", "PAD1", "PB27"), ("SERCOM2", "PAD2", "PB28")}
    # SERCOM4 loses a complete pad set (PB26-PB29).
    assert len(sg61.instances["SERCOM4"].signals) < len(e54.instances["SERCOM4"].signals)
    assert set(e54.instances) == set(sg61.instances)


# --- matches / registration --------------------------------------------------------------------


@pytest.mark.parametrize("vendor,soc,expected", [
    ("atmel", "same54p20a", True),
    ("microchip", "pic32cx1025sg61128", True),
    (None, "same54p20a", True),
    (None, "pic32cz2051ca70144", True),
    ("nordic", "nrf52840", False),
    (None, "stm32f407xx", False),
    (None, None, False),
])
def test_matches(vendor, soc, expected):
    assert provider.matches(BuildInfo(build_dir=Path("."), soc=soc, vendor=vendor)) is expected


def test_registers_as_provider():
    plugins.unregister_all()
    try:
        assert plugins.register("provider", MicrochipAtdfProvider())
        assert plugins.get_provider("microchip-atdf") is not None
    finally:
        plugins.unregister_all()


# --- locate on synthetic trees -----------------------------------------------------------------


def test_atdf_name_and_natural_sort():
    assert atdf_name("same54p20a") == "ATSAME54P20A.atdf"
    assert atdf_name("pic32cx1025sg61128") == "PIC32CX1025SG61128.atdf"
    assert sorted(["3.9.244", "3.11.261", "3.10.1"], key=natural_key, reverse=True) == \
        ["3.11.261", "3.10.1", "3.9.244"]


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<x/>")
    return path


def test_locate_prefers_hal_then_dfp_newest(tmp_path, monkeypatch):
    home = tmp_path / "packs"
    for ver in ("3.9.244", "3.11.261", "3.10.0"):
        _touch(home / "Microchip" / "SAME54_DFP" / ver / "atdf" / "ATSAME54P20A.atdf")
    monkeypatch.setattr(microchip_atdf, "_dfp_roots", lambda: [home])
    info = BuildInfo(build_dir=tmp_path, soc="same54p20a", workspace=tmp_path / "ws")
    assert provider.locate(info).parent.parent.name == "3.11.261"
    hal = _touch(tmp_path / "ws" / "modules" / "hal" / "microchip" / "packs" / "sam" / "fam" / "ser" / "atdf"
                 / "ATSAME54P20A.atdf")
    assert provider.locate(info) == hal
    info.soc = "same70q21b"
    assert provider.locate(info) is None


def test_locate_is_case_insensitive(tmp_path, monkeypatch):
    home = tmp_path / "packs"
    hit = _touch(home / "Microchip" / "SAME54_DFP" / "1.0.0" / "atdf" / "atsame54p20a.ATDF")
    monkeypatch.setattr(microchip_atdf, "_dfp_roots", lambda: [home])
    assert provider.locate(BuildInfo(build_dir=tmp_path, soc="same54p20a")) == hit


# --- real workspace ----------------------------------------------------------------------------


def test_locate_lab1_real(rtos4_build):
    info = read_build_info(rtos4_build(r"apps\lab1\build_same54_xpro"))
    assert provider.matches(info)
    t0 = time.perf_counter()
    path = provider.locate(info)
    elapsed = time.perf_counter() - t0
    print(f"\nlocate: {path} in {elapsed * 1000:.1f} ms")
    assert path is not None and path.name == "ATSAME54P20A.atdf"
    assert elapsed < 1.0
    m = provider.load(path)
    print(f"model: packages={len(m.packages)} instances={len(m.instances)} "
          f"signals={sum(len(i.signals) for i in m.instances.values())}")
    assert len(m.instances) == 71
    assert _pkg(m, "TQFP128").pin_count == 128
    assert {s.pad for s in m.instances["SERCOM2"].signals if s.role == "PAD0"} == {"PA09", "PB25", "PA12", "PB26"}


def test_real_sg61_vs_e54(rtos4_ws):
    base = rtos4_ws / "modules" / "hal" / "microchip" / "packs"
    e54 = provider.load(next(base.glob("sam/*/atsame54/atdf/ATSAME54P20A.atdf")))
    sg61 = provider.load(next(base.glob("pic32c/*/pic32cx_sg61/atdf/PIC32CX1025SG61128.atdf")))
    assert _pads(e54) - _pads(sg61) == {"PB26", "PB27", "PB28", "PB29", "PC04"}
    assert len(sg61.instances) == len(e54.instances) == 71
