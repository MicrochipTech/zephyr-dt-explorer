"""atmel,sam0-pinctrl formatter: fixture tests (no env needed) and real-workspace tests (DTX_RTOS4_WS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.buildinfo import read_build_info
from dt_explorer.core.device import Signal
from dt_explorer.formatters import atmel_sam0
from dt_explorer.formatters.atmel_sam0 import (
    AtmelSam0Formatter, FormatterError, find_header, neutral_pad, parse_dtsi_groups, zephyr_pad,
)
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider

FIX = Path(__file__).resolve().parent / "fixtures" / "sam0_pinctrl"
HEADER = FIX / "same54p-pinctrl.h"
DTSI = FIX / "same54_xpro-pinctrl.dtsi"

fmt = AtmelSam0Formatter(HEADER)


def sig(pad, mux, role, instance=None):
    return Signal(role=role, pad=pad, mux=mux, attrs={"instance": instance} if instance else {})


def test_leading_zero_conversion():
    assert zephyr_pad("PA09") == "PA9"
    assert zephyr_pad("PB25") == "PB25"
    assert zephyr_pad("PA00") == "PA0"
    assert neutral_pad("PA9") == "PA09"
    assert neutral_pad("PB25") == "PB25"
    with pytest.raises(FormatterError):
        zephyr_pad("GND")


def test_to_overlay_names():
    assert fmt.to_overlay(sig("PB25", "D", "PAD0", "SERCOM2")) == "PB25D_SERCOM2_PAD0"
    assert fmt.to_overlay(sig("PA09", "D", "PAD0", "SERCOM2")) == "PA9D_SERCOM2_PAD0"
    assert fmt.to_overlay(sig("PA09", "D", "PAD0"), instance="SERCOM2") == "PA9D_SERCOM2_PAD0"


def test_instance_resolved_from_header_when_unique():
    assert fmt.to_overlay(sig("PA09", "D", "PAD0")) == "PA9D_SERCOM2_PAD0"


def test_missing_name_is_error():
    with pytest.raises(FormatterError, match="PB30C_SERCOM7_PAD0"):
        fmt.to_overlay(sig("PB30", "C", "PAD0", "SERCOM7"))
    with pytest.raises(FormatterError):
        fmt.to_overlay(sig("PB30", "C", "PAD0"))          # no candidate in header
    with pytest.raises(FormatterError, match="not defined"):
        fmt.parse("PB25D_SERCOM9_PAD0")
    with pytest.raises(FormatterError):
        fmt.parse("not_a_macro")


def test_no_header_is_error():
    with pytest.raises(FormatterError, match="no pinctrl header"):
        AtmelSam0Formatter().to_overlay(sig("PB25", "D", "PAD0", "SERCOM2"))


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
    # The pin/function encoded in the SAM_PINMUX() value agrees with the name for every define.
    for name, (port, pin, func, _kind) in atmel_sam0.load_header(HEADER).items():
        p = fmt.parse(name) if "_GPIO" not in name else None
        if p:
            assert (p.pad, p.mux) == (f"P{port}{pin:02d}", func)


def test_registered():
    plugins.unregister_all()
    plugins.register_formatter(AtmelSam0Formatter)
    assert plugins.get_formatter("atmel,sam0-pinctrl").name == "atmel_sam0"


def test_dtsi_groups_fixture():
    groups = parse_dtsi_groups(DTSI.read_text(encoding="utf-8"))
    assert [fmt.from_overlay(m) for m in groups["sercom2_uart_default"]] == ["PB25", "PB24"]
    assert [fmt.from_overlay(m) for m in groups["sercom4_spi_default"]] == ["PB26", "PB27", "PB29"]


# --- real workspace (DTX_RTOS4_WS) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def real(rtos4_ws, rtos4_build):
    info = read_build_info(rtos4_build("apps/lab1/build_same54_xpro"))
    provider = MicrochipAtdfProvider()
    path = provider.locate(info)
    assert path is not None
    return rtos4_ws, info, provider.load(path)


def test_sercom_round_trip_all_signals(real, capsys):
    ws, info, model = real
    f = AtmelSam0Formatter.for_workspace(ws, info.soc)
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
            # without the instance the header lookup must find the same macro (or be ambiguous)
            try:
                assert f.to_overlay(s) == macro
            except FormatterError as e:
                assert "candidates" in str(e)
    print(f"SERCOM signals checked: {checked}; round trip ok: {checked - len(missing)}; "
          f"no header macro: {len(missing)}")
    for m in missing:
        print("  no macro:", m)
    assert checked > 100
    # known, legitimate gaps: pads where this function is not in the Zephyr header
    assert set(missing) == {("SERCOM7", "PB30", "C", "PAD0"), ("SERCOM7", "PB31", "C", "PAD1"),
                            ("SERCOM7", "PA30", "C", "PAD2"), ("SERCOM7", "PA31", "C", "PAD3")}


def test_board_dtsi_all_groups(real, capsys):
    ws, info, _model = real
    f = AtmelSam0Formatter.for_workspace(ws, info.soc)
    dtsi = ws / "zephyr/boards/atmel/sam0/same54_xpro/same54_xpro-pinctrl.dtsi"
    groups = parse_dtsi_groups(dtsi.read_text(encoding="utf-8"))
    assert groups
    for label, macros in groups.items():
        parsed = [f.parse(m) for m in macros]
        print(label)
        for p in parsed:
            print(f"  {p.macro:24} -> pad={p.pad} mux={p.mux} instance={p.instance} role={p.role}")
    assert [f.from_overlay(m) for m in groups["sercom2_uart_default"]] == ["PB25", "PB24"]
