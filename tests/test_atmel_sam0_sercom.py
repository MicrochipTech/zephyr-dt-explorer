"""SERCOM recipe (atmel,sam0-uart/i2c/spi): pure unit tests, fixture tests, real-board tests (DTX_RTOS4_WS)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.device import Instance, Signal
from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter, parse_dtsi_groups
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
from dt_explorer.recipes import atmel_sam0_sercom as mod
from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe, PADS, RecipeError

FIX = Path(__file__).resolve().parent / "fixtures"
recipe = AtmelSam0SercomRecipe()



def _pins_for(assignment):
    """Synthetic pads for an assignment: {"PADn": "X<n>"}."""
    return {p: "X" + p[3] for p in assignment.values()}


# ---- registration and roles -----------------------------------------------------------------
def test_registered_for_all_three_compatibles():
    # other tests call unregister_all(), so register again; registration is idempotent by name
    plugins.register_recipe(AtmelSam0SercomRecipe)
    for c in ("atmel,sam0-uart", "atmel,sam0-i2c", "atmel,sam0-spi"):
        assert plugins.get_recipe(c).name == "atmel_sam0_sercom"


def test_pin_roles_per_class():
    inst = Instance("SERCOM2", "SERCOM", 0x41012000, [])
    assert recipe.pin_roles(inst, "atmel,sam0-uart") == ["TX", "RX", "RTS", "CTS"]
    assert recipe.pin_roles(inst, "atmel,sam0-i2c") == ["SDA", "SCL"]
    assert recipe.pin_roles(inst, "atmel,sam0-spi") == ["MOSI", "MISO", "SCK", "CS"]
    assert recipe.pin_roles(inst) == ["TX", "RX", "RTS", "CTS", "SDA", "SCL", "MOSI", "MISO", "SCK", "CS"]
    assert recipe.pin_roles(Instance("TCC0", "TCC", 0, [])) == []


def test_kconfig_table():
    assert recipe.kconfig("atmel,sam0-uart") == {"CONFIG_SERIAL": "y", "CONFIG_UART_SAM0": "y"}
    assert recipe.kconfig("atmel,sam0-i2c") == {"CONFIG_I2C": "y", "CONFIG_I2C_SAM0": "y"}
    assert recipe.kconfig("atmel,sam0-spi") == {"CONFIG_SPI": "y", "CONFIG_SPI_SAM0": "y"}
    assert len(recipe.kconfig()) == 6
    with pytest.raises(RecipeError):
        recipe.kconfig("atmel,sam0-gpio")


# ---- acceptance 1 (pure) -----------------------------------------------------------------
def test_lift_sercom2_unit():
    got = recipe.lift({"rxpo": 1, "txpo": 0}, {"PAD0": "PB25", "PAD1": "PB24"})
    assert got == {"TX": "PB25", "RX": "PB24"}


def test_lift_accepts_cell_lists_signals_and_pairs():
    sigs = [Signal("PAD0", "PB25", "D"), Signal("PAD1", "PB24", "D")]
    assert recipe.lift({"rxpo": [1], "txpo": [0]}, sigs) == {"TX": "PB25", "RX": "PB24"}
    assert recipe.lift({"compatible": ["atmel,sam0-uart"], "rxpo": 1, "txpo": 0},
                       [("PAD0", "PB25"), ("PAD1", "PB24")]) == {"TX": "PB25", "RX": "PB24"}


def test_lift_omits_roles_without_pad():
    assert recipe.lift({"rxpo": 1, "txpo": 0}, {"PAD0": "PB25"}) == {"TX": "PB25"}


# ---- derive ------------------------------------------------------------------------------
def test_derive_known_values():
    assert recipe.derived_props({"TX": "PAD0", "RX": "PAD1"}) == {"rxpo": 1, "txpo": 0}
    assert recipe.derived_props({"TX": "PAD0", "RX": "PAD1", "RTS": "PAD2", "CTS": "PAD3"}) == {"rxpo": 1, "txpo": 2}
    assert recipe.derived_props({"TX": "PAD0", "RX": "PAD1", "RTS": "PAD2"}) == {"rxpo": 1, "txpo": 3}
    assert recipe.derived_props({"MOSI": "PAD0", "SCK": "PAD1", "MISO": "PAD3"}) == {"dipo": 3, "dopo": 0}
    assert recipe.derived_props({"MOSI": "PAD3", "SCK": "PAD1", "MISO": "PAD0"}) == {"dipo": 0, "dopo": 2}
    assert recipe.derived_props({"SDA": "PAD0", "SCL": "PAD1"}) == {}


INVALID = [
    ({"TX": "PAD0", "RX": "PAD0"}, "both on PAD0"),
    ({"TX": "PAD1", "RX": "PAD0"}, "TX must be on PAD0"),
    ({"TX": "PAD0"}, "not assigned"),
    ({"TX": "PAD0", "RX": "PAD2", "RTS": "PAD2"}, "both on PAD2"),
    ({"TX": "PAD0", "RX": "PAD1", "RTS": "PAD3"}, "RTS must be on PAD2"),
    ({"TX": "PAD0", "RX": "PAD1", "CTS": "PAD3"}, "CTS needs RTS"),
    ({"TX": "PAD0", "RX": "PAD1", "RTS": "PAD2", "CTS": "PAD1"}, "both on PAD1"),
    ({"TX": "PAD0", "RX": "PAD3", "RTS": "PAD2", "CTS": "PAD3"}, "both on PAD3"),
    ({"TX": "PAD0", "RX": "PAD1", "RTS": "PAD2", "CTS": "PAD0"}, "both on PAD0"),
    ({"TX": "PAD0", "RX": "PAD7"}, "not a SERCOM pad role"),
    ({"MOSI": "PAD0", "SCK": "PAD2", "MISO": "PAD3"}, "not offered"),
    ({"MOSI": "PAD1", "SCK": "PAD0", "MISO": "PAD3"}, "not offered"),
    ({"MOSI": "PAD0", "SCK": "PAD1", "MISO": "PAD1"}, "both on PAD1"),
    ({"MOSI": "PAD0", "SCK": "PAD1", "MISO": "PAD3", "CS": "PAD3"}, "both on PAD3"),
    ({"MOSI": "PAD0", "SCK": "PAD1", "MISO": "PAD3", "CS": "PAD1"}, "both on PAD1"),
    ({"MOSI": "PAD3", "SCK": "PAD1", "MISO": "PAD0", "CS": "PAD0"}, "both on PAD0"),
    ({"MOSI": "PAD0", "SCK": "PAD1"}, "not assigned"),
    ({"SDA": "PAD1", "SCL": "PAD0"}, "fixed on"),
    ({"SDA": "PAD0", "SCL": "PAD2"}, "fixed on PAD1"),
    ({"TX": "PAD0", "SDA": "PAD1"}, "several classes"),
    ({"FOO": "PAD0"}, "no known SERCOM role"),
    ({}, "no known SERCOM role"),
]


@pytest.mark.parametrize("assignment,msg", INVALID)
def test_invalid_assignments_raise(assignment, msg):
    with pytest.raises(RecipeError, match=msg):
        recipe.derived_props(assignment)


def test_spi_cs_on_wrong_pad():
    with pytest.raises(RecipeError, match="CS must be on PAD2"):
        recipe.derived_props({"MOSI": "PAD0", "SCK": "PAD1", "MISO": "PAD2", "CS": "PAD3"})


@pytest.mark.parametrize("props", [
    {"rxpo": 1, "txpo": 1},          # reserved on D5x/E5x
    {"rxpo": 4, "txpo": 0},
    {"rxpo": 0, "txpo": 0},          # RX collides with TX
    {"rxpo": 2, "txpo": 3},          # RX collides with RTS
    {"rxpo": 3, "txpo": 2},          # RX collides with CTS
    {"rxpo": 1},
    {"rxpo": "1", "txpo": 0},
    {"rxpo": [1, 2], "txpo": 0},
])
def test_lift_invalid_uart_props_raise(props):
    with pytest.raises(RecipeError):
        recipe.lift(props, {"PAD0": "A", "PAD1": "B"})


@pytest.mark.parametrize("props", [
    {"dipo": 3, "dopo": 1}, {"dipo": 3, "dopo": 3},   # reserved
    {"dipo": 0, "dopo": 0},                           # MISO collides with MOSI
    {"dipo": 1, "dopo": 2},                           # MISO collides with SCK
    {"dipo": 4, "dopo": 0}, {"dipo": 3},
])
def test_lift_invalid_spi_props_raise(props):
    with pytest.raises(RecipeError):
        recipe.lift(props, {})


# ---- acceptance 2: derive -> lift round trip --------------------------------------------
@pytest.mark.parametrize("cls", ["uart", "spi", "i2c"])
def test_round_trip_every_valid_combination(cls, capsys):
    count = 0
    for a in recipe.valid_assignments(cls):
        props = recipe.derived_props(a)
        pins = _pins_for(a)
        got = recipe.lift(props, pins)
        assert got == {r: "X" + p[3] for r, p in a.items()}, (a, props, got)
        count += 1
    with capsys.disabled():
        print(f"\nround trip {cls}: {count} valid combinations checked")
    assert count == {"uart": 6, "spi": 6, "i2c": 1}[cls]


def test_exhaustive_invalid_rejected_and_valid_accepted(capsys):
    """All role -> pad assignments (each role: unassigned or one of 4 pads) split into valid and invalid."""
    import itertools
    stats = {}
    for cls, (_, roles) in mod.CLASSES.items():
        valid = {tuple(sorted(a.items())) for a in recipe.valid_assignments(cls)}
        ok = bad = 0
        for combo in itertools.product([None, *PADS], repeat=len(roles)):
            a = {r: p for r, p in zip(roles, combo) if p}
            if not a:
                continue
            try:
                recipe.derived_props(a)
                accepted = True
            except RecipeError:
                accepted = False
            assert accepted == (tuple(sorted(a.items())) in valid), a
            ok += accepted
            bad += not accepted
        stats[cls] = (ok, bad)
    with capsys.disabled():
        for cls, (ok, bad) in stats.items():
            print(f"exhaustive {cls}: {ok} accepted, {bad} rejected")
    assert stats["uart"][0] == 6 and stats["spi"][0] == 6 and stats["i2c"][0] == 1


# ---- acceptance 1 and 3 on the board ----------------------------------------------------
def _board_pins(parser, groups, label, atdf):
    """Pinctrl node -> {instance, pads {PADn: pad}} via formatter.parse and the ATDF provider."""
    pads, inst_name = {}, None
    for macro in groups[label]:
        p = parser.parse(macro)
        sigs = [s for s in atdf.instances[p.instance].signals
                if s.pad == p.pad and s.mux == p.mux and s.role == p.role]
        assert len(sigs) == 1, f"{macro} not in ATDF"
        pads[sigs[0].role] = sigs[0].pad
        inst_name = p.instance
    return inst_name, pads


def _board_lifts(dtsi_text, dts_text, header, atdf_path):
    fmt = AtmelSam0Formatter(header)
    atdf = MicrochipAtdfProvider().load(atdf_path)
    groups = parse_dtsi_groups(dtsi_text)
    out = {}
    for sercom, label in (("sercom2", "sercom2_uart_default"), ("sercom4", "sercom4_spi_default"),
                          ("sercom7", "sercom7_i2c_default")):
        node = re.search(r"&%s\s*\{(.*?)^\};" % sercom, dts_text, re.S | re.M).group(1)
        props = {"compatible": re.search(r'compatible = "([^"]+)"', node).group(1)}
        for k in ("rxpo", "txpo", "dipo", "dopo"):
            m = re.search(r"\b%s = <(\d+)>" % k, node)
            if m:
                props[k] = int(m.group(1))
        inst, pads = _board_pins(fmt, groups, label, atdf)
        out[sercom] = (inst, props, pads, recipe.lift(props, pads))
    return out


def _print_board(lifts, capsys):
    with capsys.disabled():
        for k, (inst, props, pads, roles) in lifts.items():
            print(f"{k} {props} pads={pads} -> {roles}")


def test_lift_board_fixtures(capsys):
    """Fixture copies of the board pinctrl dtsi and ATDF; node properties as in same54_xpro.dts."""
    dtsi = (FIX / "sam0_pinctrl" / "same54_xpro-pinctrl.dtsi").read_text(encoding="utf-8")
    dts = ('&sercom2 { compatible = "atmel,sam0-uart"; rxpo = <1>; txpo = <0>; };\n'
           '&sercom4 { compatible = "atmel,sam0-spi"; dipo = <3>; dopo = <0>; };\n'
           '&sercom7 { compatible = "atmel,sam0-i2c"; };\n').replace("};\n", "\n};\n")
    lifts = _board_lifts(dtsi, dts, FIX / "sam0_pinctrl" / "same54p-pinctrl.h", FIX / "atdf" / "ATSAME54P20A.atdf")
    _print_board(lifts, capsys)
    assert lifts["sercom2"][3] == {"TX": "PB25", "RX": "PB24"}
    assert lifts["sercom4"][3] == {"MOSI": "PB27", "SCK": "PB26", "MISO": "PB29"}
    assert lifts["sercom7"][3] == {"SDA": "PD08", "SCL": "PD09"}


def test_lift_board_real_workspace(rtos4_ws, capsys):
    """Real same54_xpro board files and the real header/ATDF of the RTOS4 workspace."""
    from dt_explorer.formatters.atmel_sam0 import find_header
    board = rtos4_ws / "zephyr" / "boards" / "atmel" / "sam0" / "same54_xpro"
    dtsi = (board / "same54_xpro-pinctrl.dtsi").read_text(encoding="utf-8")
    dts = (board / "same54_xpro.dts").read_text(encoding="utf-8")
    atdfs = sorted((rtos4_ws / "modules" / "hal" / "microchip" / "packs").rglob("ATSAME54P20A.atdf"))
    if not atdfs:
        pytest.skip("ATSAME54P20A.atdf not found in hal_microchip packs")
    lifts = _board_lifts(dtsi, dts, find_header(rtos4_ws, "same54p20a"), atdfs[0])
    _print_board(lifts, capsys)
    assert lifts["sercom2"][3] == {"TX": "PB25", "RX": "PB24"}
    assert lifts["sercom2"][1] == {"compatible": "atmel,sam0-uart", "rxpo": 1, "txpo": 0}
    assert lifts["sercom4"][3] == {"MOSI": "PB27", "SCK": "PB26", "MISO": "PB29"}
    assert lifts["sercom7"][3] == {"SDA": "PD08", "SCL": "PD09"}
