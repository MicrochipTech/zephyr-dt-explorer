"""ADC recipe (``recipes/adc.py``, P8.8b).

* Pure unit tests need nothing outside the repo.
* The edtlib tests need DTX_RTOS4_WS, the Zephyr SDK compiler and the ATDF of the SoC: lift of ``adc0`` of
  the same54_xpro build (P5.3 style compare) and a rendered ADC with two ``channel@N`` children, read back
  by edtlib.
* The lift of the main pic32cx_sg41_cult build needs ``%TEMP%\\dtx_main_sg41`` (P6.0); skipped when missing.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.device import DeviceModel, Instance, Signal
from dt_explorer.core.draft import BindingIndex, DraftChild, DraftNode, detect_class
from dt_explorer.core.render import RenderContext, RenderError, render_overlay
from dt_explorer.recipes import adc as mod
from dt_explorer.recipes.adc import AdcRecipe, RecipeError

adc = AdcRecipe()


def _inst(name, roles, pads):
    return Instance(name, "ADC", 0, [Signal(r, p, "B", None) for r, p in zip(roles, pads)])


# ---- registration, roles -------------------------------------------------------------------------
def test_registered_for_both_compatibles():
    plugins.register_recipe(AdcRecipe)
    for c in ("atmel,sam0-adc", "microchip,adc-g1"):
        assert plugins.get_recipe(c).name == "adc"


def test_pin_roles_are_the_analog_inputs_the_instance_has():
    adc1 = _inst("ADC1", ["AIN0", "AIN1", "AIN9"], ["PB08", "PB09", "PB07"])
    assert adc.pin_roles(adc1) == ["AIN0", "AIN1", "AIN9"]
    assert adc.pin_roles(adc1, "microchip,adc-g1") == ["AIN0", "AIN1", "AIN9"]
    assert adc.pin_roles(Instance("TCC0", "TCC", 0, [])) == []
    assert adc.roles_for() == [f"AIN{i}" for i in range(16)]
    with pytest.raises(RecipeError):
        adc.roles_for("atmel,sam0-uart")


def test_valid_assignments_up_to_four_independent_inputs():
    allv = list(adc.valid_assignments("adc"))
    assert len(allv) == 16 + 120 + 560 + 1820
    assert {"AIN2": "AIN2", "AIN5": "AIN5"} in allv and all(k == v for a in allv for k, v in a.items())
    with pytest.raises(RecipeError):
        list(adc.valid_assignments("pwm"))


def test_derived_props_checks_but_derives_nothing():
    assert adc.derived_props({"AIN2": "AIN2", "AIN15": "AIN15"}) == {}
    with pytest.raises(RecipeError, match="no analog input"):
        adc.derived_props({})
    with pytest.raises(RecipeError, match="must sit on a pad with the signal AIN3"):
        adc.derived_props({"AIN3": "AIN4"})
    with pytest.raises(RecipeError, match="AIN0..AIN15"):
        adc.derived_props({"AIN16": "AIN16"})
    with pytest.raises(RecipeError, match="not an analog input role"):
        adc.derived_props({"WO0": "WO0"})


def test_lift_unit_ignores_other_signals_of_the_pad():
    assert adc.lift({}, {"AIN12": "PB00", "AIN1": "PA03"}) == {"AIN1": "PA03", "AIN12": "PB00"}
    assert adc.lift({}, [Signal("AIN3", "PB09", "B"), ("X2", "PB09"), ("VREFA", "PA03")]) == {"AIN3": "PB09"}
    assert adc.lift({}, {}) == {}


# ---- prescaler, defaults, Kconfig ------------------------------------------------------------------
def test_suggest_prescaler_keeps_the_adc_clock_at_or_below_16_mhz():
    assert mod.suggest_prescaler(60e6) == 4            # 15 MHz, the value of both boards
    assert mod.suggest_prescaler(48e6) == 4            # 12 MHz (old samd5x.dtsi: gclk 2 = 48 MHz, prescaler 4)
    assert mod.suggest_prescaler(16e6) == 2            # smallest value of the enum
    assert mod.suggest_prescaler(120e6) == 8
    assert mod.suggest_prescaler(4e9) == 256
    with pytest.raises(RecipeError, match="too fast"):
        mod.suggest_prescaler(5e9)
    with pytest.raises(RecipeError):
        mod.suggest_prescaler(0)


def test_default_props_g1_needs_the_prescaler_old_port_has_it_in_the_soc():
    assert adc.default_props("microchip,adc-g1") == {"prescaler": 4}
    assert adc.default_props("microchip,adc-g1", 120e6) == {"prescaler": 8}
    assert adc.default_props("atmel,sam0-adc") == {}
    with pytest.raises(RecipeError):
        adc.default_props("atmel,sam0-uart")


def test_kconfig_tables():
    assert adc.kconfig("atmel,sam0-adc") == {"CONFIG_ADC": "y", "CONFIG_ADC_SAM0": "y"}
    assert adc.kconfig("microchip,adc-g1") == {"CONFIG_ADC": "y", "CONFIG_ADC_MCHP_G1": "y"}
    assert len(adc.kconfig()) == 3
    with pytest.raises(RecipeError):
        adc.kconfig("atmel,sam0-uart")


# ---- channel children ------------------------------------------------------------------------------
def test_channel_child_takes_the_input_from_the_role():
    c = mod.channel_child("AIN12")
    assert (c.name, c.unit, c.node_name) == ("channel", "c", "channel@c")          # unit address in hex, like reg
    assert c.props == {"reg": [12], "zephyr,gain": "ADC_GAIN_1", "zephyr,reference": "ADC_REF_VDD_1",
                       "zephyr,acquisition-time": 0, "zephyr,input-positive": 12, "zephyr,resolution": 12}
    d = mod.channel_child(3, reg=0, gain="ADC_GAIN_1_2", reference="ADC_REF_INTERNAL", resolution=None,
                          vref_mv=3300, negative="AIN1", label="ch0")
    assert d.node_name == "channel@0" and d.labels == ["ch0"]
    assert d.props["zephyr,input-positive"] == 3 and d.props["zephyr,input-negative"] == 1
    assert d.props["zephyr,differential"] is True and d.props["zephyr,vref-mv"] == 3300
    assert "zephyr,resolution" not in d.props


def test_channel_child_errors():
    with pytest.raises(RecipeError, match="AIN0..AIN15"):
        mod.channel_child(16)
    with pytest.raises(RecipeError, match="unknown gain"):
        mod.channel_child("AIN0", gain="ADC_GAIN_7")
    with pytest.raises(RecipeError, match="unknown reference"):
        mod.channel_child("AIN0", reference="ADC_REF_NONE")
    with pytest.raises(RecipeError, match="negative input"):
        mod.channel_child("AIN0", negative="AIN9")
    with pytest.raises(RecipeError):
        mod.channel_child("PAD0")


def test_channels_for_one_child_per_input_in_input_order():
    kids = mod.channels_for({"AIN5": "PC03", "AIN2": "PA08", "WO0": "X"})
    assert [k.node_name for k in kids] == ["channel@2", "channel@5"]
    assert [k.props["zephyr,input-positive"] for k in kids] == [2, 5]


def test_channel_problems():
    node = DraftNode(cls="adc", roles=["AIN2"], pins={"AIN2": "PA08"}, children=[
        mod.channel_child("AIN2"), mod.channel_child("AIN7"), mod.channel_child("AIN2", reg=7)])
    got = mod.channel_problems(node)
    assert len(got) == 2
    assert "AIN7 is not one of the pads" in got[0] and "reg [7] is also used by channel@7" in got[1]
    node.children = [mod.channel_child("AIN2")]
    assert mod.channel_problems(node) == []


# ---- render (synthetic device model, no workspace) -----------------------------------------------
class _Fmt:
    compatibles = ["acme,pinctrl"]

    def render_group(self, signals):
        return "group1 {\n\tpinmux = " + ", ".join(f"<{s.pad}{s.mux}_{s.attrs['instance']}_{s.role}>"
                                                    for s in signals) + ";\n};"


def test_render_adc_node_with_two_channels():
    inst = _inst("ADC1", ["AIN2", "AIN5"], ["PA08", "PC03"])
    ctx = RenderContext(device=DeviceModel("acme", [], {"ADC1": inst}, []), formatter=_Fmt())
    pins = {"AIN2": "PA08", "AIN5": "PC03"}
    node = DraftNode(cls="adc", roles=list(pins), instance="ADC1", pins=pins, compatible="microchip,adc-g1",
                     label="adc1", vendor_props=adc.default_props("microchip,adc-g1"),
                     children=mod.channels_for(pins, vref_mv=3300))
    text = render_overlay(node, ctx)
    assert "pinmux = <PA08B_ADC1_AIN2>, <PC03B_ADC1_AIN5>;" in text
    assert '\tcompatible = "microchip,adc-g1";' in text and "\tprescaler = <4>;" in text
    assert "\t#address-cells = <1>;" in text and "\t#size-cells = <0>;" in text
    for n in (2, 5):
        assert f"\tchannel@{n} {{" in text and f"\t\treg = <0x{n}>;" in text
        assert f"\t\tzephyr,input-positive = <{n}>;" in text
    assert text.count('zephyr,gain = "ADC_GAIN_1";') == 2 and text.count("zephyr,vref-mv = <3300>;") == 2


def test_render_refuses_a_pad_with_several_signals_on_the_instance():
    """ADC0: every AIN pad also carries the PTC signals X/Y (same mux). The core renderer refuses such a pad."""
    inst = Instance("ADC0", "ADC", 0, [Signal("AIN12", "PB00", "B"), Signal("X30", "PB00", "B"),
                                        Signal("Y30", "PB00", "B")])
    ctx = RenderContext(device=DeviceModel("acme", [], {"ADC0": inst}, []), formatter=_Fmt())
    node = DraftNode(cls="adc", roles=["AIN12"], instance="ADC0", pins={"AIN12": "PB00"},
                     compatible="atmel,sam0-adc", label="adc0")
    with pytest.raises(RenderError, match="offers 3 signals"):
        render_overlay(node, ctx)


# ---- class detection, lift and render against a workspace ------------------------------------------
def test_class_adc_in_both_workspaces(rtos4_ws, main_ws):
    old = BindingIndex([rtos4_ws / "zephyr" / "dts" / "bindings"])
    new = BindingIndex([main_ws / "zephyr" / "dts" / "bindings"])
    for idx, c in ((old, "atmel,sam0-adc"), (new, "microchip,adc-g1"), (new, "atmel,sam0-adc")):
        cls, chain = detect_class(c, idx)
        assert cls == "adc" and chain[-1] == "adc-controller.yaml", (c, chain)


SAME54_LAB1 = "_soltest/apps/lab1_leer/build_same54_xpro"


def test_lift_adc0_of_same54_xpro_renders_back(rtos4_ws, rtos4_build, tmp_path, capsys):
    from test_pwm_recipe import lift_compare
    with capsys.disabled():
        print()
        node, _overlay = lift_compare(rtos4_ws, rtos4_build, tmp_path, SAME54_LAB1, "adc0", "atmel,sam0-adc",
                                      expect_roles={}, expect_cls="adc")
    assert node.vendor_props == {} and node.props == {}


def test_rendered_channels_are_read_back_by_edtlib(rtos4_ws, rtos4_build, tmp_path):
    """adc1 with two channels on free ADC1 pads, applied to the same54_xpro zephyr.dts: edtlib sees the children."""
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    from test_lift import PINCTRL_INCLUDE
    from test_render import compile_edt
    plugins.register_recipe(AdcRecipe)
    plugins.register_formatter(AtmelSam0Formatter)
    build = rtos4_build(SAME54_LAB1)
    model = BuildModel(build)
    info = read_build_info(build)
    provider = MicrochipAtdfProvider()
    atdf = provider.locate(info)
    if atdf is None:
        pytest.skip(f"no ATDF for {info.soc}")
    device = provider.load(atdf)
    inst = device.instances["ADC1"]
    pad_of = {s.role: s.pad for s in inst.signals}
    pins = {"AIN2": pad_of["AIN2"], "AIN5": pad_of["AIN5"]}
    node = DraftNode(cls="adc", roles=list(pins), instance="ADC1", pins=pins, compatible="atmel,sam0-adc",
                     label="adc1", children=mod.channels_for(pins, reference="ADC_REF_INTERNAL"))
    ctx = RenderContext(device=device, pinctrl_compatible="atmel,sam0-pinctrl", pinctrl_label="pinctrl",
                        workspace=info.workspace, soc=info.soc, bindings=None)
    overlay = render_overlay(node, ctx)
    zdts = (build / "zephyr" / "zephyr.dts").read_text(encoding="utf-8")
    edt = compile_edt(model, PINCTRL_INCLUDE + zdts + "\n" + overlay, tmp_path)
    n = edt.label2node["adc1"]
    assert n.status == "okay" and n.compats[0] == "atmel,sam0-adc"
    kids = {k: c for k, c in n.children.items()}
    assert sorted(kids) == ["channel@2", "channel@5"]
    for num in (2, 5):
        c = kids[f"channel@{num}"]
        assert c.props["reg"].val == [num] and c.props["zephyr,input-positive"].val == num
        assert c.props["zephyr,gain"].val == "ADC_GAIN_1" and c.props["zephyr,reference"].val == "ADC_REF_INTERNAL"
    group = n.props["pinctrl-0"].val[0].children["group1"]
    assert len(group.props["pinmux"].val) == 2          # AIN2 and AIN5 of ADC1


def test_lift_of_the_main_sg41_build():
    build = Path(tempfile.gettempdir()) / "dtx_main_sg41"
    if not (build / "zephyr" / "edt.pickle").is_file():
        pytest.skip(f"no main build at {build} (recreate per P6.0: west build -b pic32cx_sg41_cult --cmake-only)")
    from dt_explorer.core.lift import lift_build
    from dt_explorer.formatters.microchip_port_g1 import MicrochipPortG1Formatter
    plugins.register_recipe(AdcRecipe)
    plugins.register_formatter(MicrochipPortG1Formatter)
    draft, _report = lift_build(build)
    by = {n.label: n for n in draft.nodes}
    assert (by["adc0"].compatible, by["adc0"].cls) == ("microchip,adc-g1", "adc")
    assert by["adc0"].pins == {"AIN1": "PA03", "AIN2": "PB08", "AIN3": "PB09", "AIN4": "PA04",
                               "AIN14": "PB02", "AIN15": "PB03"}
    assert by["adc1"].pins == {"AIN0": "PB08", "AIN1": "PB09", "AIN5": "PC03", "AIN8": "PB06", "AIN9": "PB07"}
    assert by["adc1"].vendor_props == {"prescaler": 4}
