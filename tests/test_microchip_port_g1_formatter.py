"""microchip,port-g1-pinctrl formatter: fixture tests (no env needed) and real-workspace tests (DTX_RTOS4_WS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.buildinfo import read_build_info
from dt_explorer.core.device import Signal
from dt_explorer.formatters import atmel_sam0, microchip_port_g1
from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
from dt_explorer.formatters.microchip_port_g1 import (
    FormatterError, MicrochipPortG1Formatter, neutral_pad, parse_dtsi_groups, pinmux_value, zephyr_pad,
)
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider

FIX = Path(__file__).resolve().parent / "fixtures" / "port_g1_pinctrl"
HEADER = FIX / "atsame54p20a-pinctrl.h"
DTSI = FIX / "sam_e54_xpro-pinctrl.dtsi"

fmt = MicrochipPortG1Formatter(HEADER)


def sig(pad, mux, role, instance=None):
    return Signal(role=role, pad=pad, mux=mux, attrs={"instance": instance} if instance else {})


def test_leading_zero_conversion():
    assert zephyr_pad("PA09") == "PA9"
    assert zephyr_pad("PB25") == "PB25"
    assert zephyr_pad("PA00") == "PA0"
    assert neutral_pad("PA9") == "PA09"
    with pytest.raises(FormatterError):
        zephyr_pad("GND")


def test_to_overlay_names():
    assert fmt.to_overlay(sig("PB25", "D", "PAD0", "SERCOM2")) == "PB25D_SERCOM2_PAD0"
    assert fmt.to_overlay(sig("PA09", "D", "PAD0", "SERCOM2")) == "PA9D_SERCOM2_PAD0"
    assert fmt.to_overlay(sig("PA09", "D", "PAD0"), instance="SERCOM2") == "PA9D_SERCOM2_PAD0"


def test_instance_resolved_from_header_when_unique():
    assert fmt.to_overlay(sig("PA09", "D", "PAD0")) == "PA9D_SERCOM2_PAD0"


def test_missing_name_is_error():
    with pytest.raises(FormatterError, match="PB30D_SERCOM7_PAD0"):
        fmt.to_overlay(sig("PB30", "D", "PAD0", "SERCOM7"))     # PB30 offers SERCOM7 only on C
    with pytest.raises(FormatterError):
        fmt.to_overlay(sig("PB30", "D", "PAD0"))                 # no candidate in header
    with pytest.raises(FormatterError, match="not defined"):
        fmt.parse("PB25D_SERCOM9_PAD0")
    with pytest.raises(FormatterError):
        fmt.parse("not_a_macro")


def test_no_header_is_error():
    with pytest.raises(FormatterError, match="no pinctrl header"):
        MicrochipPortG1Formatter().to_overlay(sig("PB25", "D", "PAD0", "SERCOM2"))


def test_from_overlay_and_parse():
    assert fmt.from_overlay("PB25D_SERCOM2_PAD0") == "PB25"
    assert fmt.from_overlay("<PA9D_SERCOM2_PAD0>") == "PA09"
    p = fmt.parse("PA9D_SERCOM2_PAD0")
    assert (p.pad, p.mux, p.instance, p.role) == ("PA09", "D", "SERCOM2", "PAD0")


def test_render_group_matches_board_style():
    text = fmt.render_group([sig("PB25", "D", "PAD0", "SERCOM2"), sig("PB24", "D", "PAD1", "SERCOM2")])
    assert text == ("group1 {\n\tpinmux = <PB25D_SERCOM2_PAD0>,\n\t\t <PB24D_SERCOM2_PAD1>;\n};")
    with pytest.raises(FormatterError):
        fmt.render_group([])


def test_header_values_match_macro_names():
    # MCHP_PINMUX(port, pin, mux, func) agrees with the name for every non-GPIO define
    defines = microchip_port_g1.load_header(HEADER)
    assert len(defines) > 130
    for name, (port, pin, func, kind) in defines.items():
        if kind == "gpio":
            continue
        p = fmt.parse(name)
        assert (p.pad, p.mux) == (f"P{port}{pin:02d}", func)
        assert kind == "periph"


def test_integer_encoding():
    # PB25D: port b=1, pin 25<<4=400, func periph=1<<9=512, mux d=3<<12=12288
    assert pinmux_value("b", 25, "d", "periph") == 1 | 400 | 512 | 12288 == 13201
    assert pinmux_value("a", 0, "gpio", "gpio") == 0


def test_registered():
    plugins.unregister_all()
    plugins.register_formatter(MicrochipPortG1Formatter)
    assert plugins.get_formatter("microchip,port-g1-pinctrl").name == "microchip_port_g1"


def test_dtsi_groups_fixture():
    groups = parse_dtsi_groups(DTSI.read_text(encoding="utf-8"))
    assert [fmt.from_overlay(m) for m in groups["sercom2_uart_default"]] == ["PB25", "PB24"]
    assert [fmt.from_overlay(m) for m in groups["tcc0_pwm_default"]] == ["PC18"]


# --- real workspace (DTX_RTOS4_WS) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def real(rtos4_ws, rtos4_build):
    info = read_build_info(rtos4_build("apps/lab1/build_same54_xpro"))
    provider = MicrochipAtdfProvider()
    path = provider.locate(info)
    assert path is not None
    return rtos4_ws, info, provider.load(path)


EXPECTED_MISSING: set = set()


def test_sercom_round_trip_all_signals(real):
    ws, info, model = real
    f = MicrochipPortG1Formatter.for_workspace(ws, info.soc)
    checked, missing = 0, []
    for iname, inst in model.instances.items():
        if inst.kind != "SERCOM":
            continue
        for s in inst.signals:
            checked += 1
            try:
                macro = f.to_overlay(s, instance=iname)
            except FormatterError:
                missing.append((iname, s.pad, s.mux, s.role))
                continue
            p = f.parse(macro)
            assert (p.pad, p.mux, p.instance, p.role) == (s.pad, s.mux, iname, s.role)
            assert f.from_overlay(macro) == s.pad
            try:
                assert f.to_overlay(s) == macro
            except FormatterError as e:
                assert "candidates" in str(e)
    print(f"SERCOM signals checked: {checked}; round trip ok: {checked - len(missing)}; "
          f"no header macro: {len(missing)}")
    for m in missing:
        print("  no macro:", m)
    assert checked > 100
    assert set(missing) == EXPECTED_MISSING


def test_board_dtsi_all_groups(real):
    ws, info, _model = real
    f = MicrochipPortG1Formatter.for_workspace(ws, info.soc)
    dtsi = ws / "zephyr/boards/microchip/sam/sam_e54_xpro/sam_e54_xpro-pinctrl.dtsi"
    groups = parse_dtsi_groups(dtsi.read_text(encoding="utf-8"))
    assert groups
    for label, macros in groups.items():
        print(label)
        for m in macros:
            p = f.parse(m)
            print(f"  {p.macro:24} -> pad={p.pad} mux={p.mux} instance={p.instance} role={p.role}")
    assert [f.from_overlay(m) for m in groups["sercom2_uart_default"]] == ["PB25", "PB24"]


def test_cross_check_old_and_new_names(real):
    ws, info, model = real
    new = MicrochipPortG1Formatter.for_workspace(ws, info.soc)
    old = AtmelSam0Formatter.for_workspace(ws, info.soc)
    same, differ, only_old, only_new = 0, [], [], []
    for iname, inst in model.instances.items():
        if inst.kind != "SERCOM":
            continue
        for s in inst.signals:
            try:
                o = old.to_overlay(s, instance=iname)
            except atmel_sam0.FormatterError:
                o = None
            try:
                n = new.to_overlay(s, instance=iname)
            except FormatterError:
                n = None
            if o and n:
                if o == n:
                    same += 1
                else:
                    differ.append((iname, s.pad, s.mux, s.role, o, n))
            elif o:
                only_old.append((iname, s.pad, s.mux, s.role, o))
            elif n:
                only_new.append((iname, s.pad, s.mux, s.role, n))
    print(f"identical: {same}; differing: {len(differ)}; only old: {len(only_old)}; only new: {len(only_new)}")
    for d in differ[:10]:
        print("  differ:", d)
    for d in only_old:
        print("  only old:", d)
    for d in only_new:
        print("  only new:", d)
    # same macro names for the same signals; the new header additionally has the 4 SERCOM7
    # macros that hal_atmel lacks (PB30/PB31/PA30/PA31, function C)
    assert (same, differ, only_old) == (130, [], [])
    assert {(d[0], d[1], d[2], d[3]) for d in only_new} == {
        ("SERCOM7", "PB30", "C", "PAD0"), ("SERCOM7", "PB31", "C", "PAD1"),
        ("SERCOM7", "PA30", "C", "PAD2"), ("SERCOM7", "PA31", "C", "PAD3")}
