"""PWM recipes for TCC and TC (``recipes/pwm_tc_tcc.py``, P8.8a).

* Pure unit tests need nothing outside the repo.
* The edtlib compare (lift of ``tcc0`` of the same54_xpro build, render, compare as in P5.3) needs
  DTX_RTOS4_WS, the Zephyr SDK compiler and the ATDF of the SoC (hal_microchip packs).
* The lift of the main pic32cx_sg41_cult build needs the cmake-only build folder
  ``%TEMP%\\dtx_main_sg41`` (P6.0); it is skipped when that is missing.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.device import DeviceModel, Instance, Signal
from dt_explorer.core.draft import BindingIndex, DraftNode, Use, detect_class
from dt_explorer.core.render import RenderContext, render_overlay
from dt_explorer.recipes import pwm_tc_tcc as mod
from dt_explorer.recipes.pwm_tc_tcc import RecipeError, TcPwmRecipe, TccPwmRecipe

tcc = TccPwmRecipe()
tc = TcPwmRecipe()


def _inst(name, kind, roles, pads=None):
    pads = pads or [f"P{i}" for i in range(len(roles))]
    return Instance(name, kind, 0, [Signal(r, p, "F", "1") for r, p in zip(roles, pads)])


# ---- registration, roles -------------------------------------------------------------------------
def test_registered_for_all_four_compatibles():
    plugins.register_recipe(TccPwmRecipe)
    plugins.register_recipe(TcPwmRecipe)
    for c in ("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm"):
        assert plugins.get_recipe(c).name == "pwm_tcc"
    for c in ("atmel,sam0-tc-pwm", "microchip,tc-g1-pwm"):
        assert plugins.get_recipe(c).name == "pwm_tc"


def test_pin_roles_follow_the_instance_kind_and_its_signals():
    tcc2 = _inst("TCC2", "TCC", ["WO0", "WO1", "WO2"])
    tc1 = _inst("TC1", "TC", ["WO0", "WO1"])
    assert tcc.pin_roles(tcc2) == ["WO0", "WO1", "WO2"]
    assert tcc.pin_roles(tcc2, "microchip,tcc-g1-pwm") == ["WO0", "WO1", "WO2"]
    assert tcc.pin_roles(tc1) == [] and tc.pin_roles(tcc2) == []        # each recipe serves only its own module
    assert tc.pin_roles(tc1, "atmel,sam0-tc-pwm") == ["WO0", "WO1"]
    assert tcc.pin_roles(Instance("SERCOM2", "SERCOM", 0, [])) == []
    assert tcc.roles_for() == [f"WO{i}" for i in range(8)] and tc.roles_for() == ["WO0", "WO1"]
    with pytest.raises(RecipeError):
        tcc.pin_roles(tcc2, "atmel,sam0-tc-pwm")                        # compatible of the other module
    with pytest.raises(RecipeError):
        tc.roles_for("atmel,sam0-uart")


def test_valid_assignments_are_all_nonempty_subsets_of_independent_outputs():
    allv = list(tcc.valid_assignments("pwm"))
    assert len(allv) == 2 ** 8 - 1 and {"WO3": "WO3", "WO6": "WO6"} in allv
    assert all(k == v for a in allv for k, v in a.items())
    assert list(tc.valid_assignments("pwm")) == [{"WO0": "WO0"}, {"WO1": "WO1"}, {"WO0": "WO0", "WO1": "WO1"}]
    with pytest.raises(RecipeError):
        list(tc.valid_assignments("uart"))


# ---- derived properties --------------------------------------------------------------------------
def test_derived_props_checks_but_derives_nothing():
    assert tcc.derived_props({"WO0": "WO0", "WO5": "WO5"}) == {}
    assert tc.derived_props({"WO1": "WO1"}) == {}


def test_derived_props_errors():
    with pytest.raises(RecipeError, match="no waveform output"):
        tcc.derived_props({})
    with pytest.raises(RecipeError, match="must sit on a pad with the signal WO1"):
        tcc.derived_props({"WO1": "WO0"})
    with pytest.raises(RecipeError, match="WO0..WO1"):
        tc.derived_props({"WO2": "WO2"})
    with pytest.raises(RecipeError, match="not a waveform output role"):
        tcc.derived_props({"TX": "TX"})


def test_pwm_channel_is_the_output_modulo_the_channel_count():
    assert [mod.pwm_channel(f"WO{i}", 6) for i in range(8)] == [0, 1, 2, 3, 4, 5, 0, 1]
    assert mod.pwm_channel("WO1", 2) == 1
    with pytest.raises(RecipeError):
        mod.pwm_channel("WO1", 0)


def test_pwm_cells_two_old_three_g1():
    assert (mod.pwm_cells("atmel,sam0-tcc-pwm"), mod.pwm_cells("atmel,sam0-tc-pwm")) == (2, 2)
    assert (mod.pwm_cells("microchip,tcc-g1-pwm"), mod.pwm_cells("microchip,tc-g1-pwm")) == (3, 3)
    with pytest.raises(RecipeError):
        mod.pwm_cells("atmel,sam0-uart")


def test_suggest_prescaler():
    assert mod.suggest_prescaler(120e6, 1e9, 24) == 8            # 120 M ticks: 8 -> 15 M <= 2^24
    assert mod.suggest_prescaler(120e6, 1e6, 24) == 1            # 120 k ticks fit as they are
    assert mod.suggest_prescaler(120e6, 1e8, 16) == 256          # 12 M ticks into 16 bit
    assert mod.suggest_prescaler(48e6, 1e9, 16) == 1024          # 48 M / 1024 = 46875 <= 65536
    with pytest.raises(RecipeError, match="does not fit"):
        mod.suggest_prescaler(120e6, 100e9, 16)
    with pytest.raises(RecipeError):
        mod.suggest_prescaler(0, 1e6, 16)


def test_default_props_per_port_and_module():
    assert tcc.default_props("atmel,sam0-tcc-pwm", "TCC1") == {"#pwm-cells": 2, "prescaler": 8}
    assert tcc.default_props("microchip,tcc-g1-pwm", "TCC1") == {"#pwm-cells": 3, "prescaler": 8, "max-bit-width": 24}
    assert tcc.default_props("microchip,tcc-g1-pwm", "TCC2", prescaler=1) == {"#pwm-cells": 3, "prescaler": 1}
    assert tc.default_props("atmel,sam0-tc-pwm", "TC2") == {"#pwm-cells": 2, "prescaler": 8, "channels": 2,
                                                              "counter-size": 16}
    assert tc.default_props("microchip,tc-g1-pwm", "TC1") == {"#pwm-cells": 3, "prescaler": 8}
    with pytest.raises(RecipeError):
        tcc.default_props("microchip,tc-g1-pwm")
    with pytest.raises(RecipeError, match="prescaler 3"):
        tcc.default_props("atmel,sam0-tcc-pwm", prescaler=3)


def test_kconfig_tables():
    assert tcc.kconfig("atmel,sam0-tcc-pwm") == {"CONFIG_PWM": "y", "CONFIG_PWM_SAM0_TCC": "y"}
    assert tcc.kconfig("microchip,tcc-g1-pwm") == {"CONFIG_PWM": "y", "CONFIG_PWM_MCHP_G1_TCC": "y"}
    assert tc.kconfig("atmel,sam0-tc-pwm") == {"CONFIG_PWM": "y", "CONFIG_PWM_SAM0_TC": "y"}
    assert tc.kconfig("microchip,tc-g1-pwm") == {"CONFIG_PWM": "y", "CONFIG_PWM_MCHP_TC_G1": "y"}
    assert len(tcc.kconfig()) == 3 and len(tc.kconfig()) == 3
    with pytest.raises(RecipeError):
        tcc.kconfig("atmel,sam0-tc-pwm")


# ---- lift (pure) ---------------------------------------------------------------------------------
def test_lift_unit():
    assert tcc.lift({}, {"WO2": "PC18"}) == {"WO2": "PC18"}
    assert tcc.lift({}, {"WO5": "PD12", "WO0": "PD20", "PAD0": "X"}) == {"WO0": "PD20", "WO5": "PD12"}
    assert tc.lift({}, [Signal("WO1", "PB31", "E"), ("WO0", "PB30")]) == {"WO0": "PB30", "WO1": "PB31"}
    assert tcc.lift({}, {}) == {}
    with pytest.raises(RecipeError):
        tc.lift({}, {"WO3": "PA00"})


# ---- render (synthetic device model, no workspace) -----------------------------------------------
class _Fmt:
    compatibles = ["acme,pinctrl"]

    def render_group(self, signals):
        return "group1 {\n\tpinmux = " + ", ".join(f"<{s.pad}{s.mux}_{s.attrs['instance']}_{s.role}>"
                                                    for s in signals) + ";\n};"


def _ctx(inst):
    return RenderContext(device=DeviceModel("acme", [], {inst.name: inst}, []), formatter=_Fmt())


@pytest.mark.parametrize("compat,cells", [("atmel,sam0-tcc-pwm", 2), ("microchip,tcc-g1-pwm", 3)])
def test_render_tcc_pwm_node(compat, cells):
    inst = _inst("TCC1", "TCC", ["WO0", "WO1"], ["PD20", "PD21"])
    node = DraftNode(cls="pwm", roles=["WO0", "WO1"], instance="TCC1", pins={"WO0": "PD20", "WO1": "PD21"},
                     vendor_props=tcc.default_props(compat, "TCC1"), compatible=compat, label="tcc1")
    text = render_overlay(node, _ctx(inst))
    assert f'compatible = "{compat}";' in text and f"#pwm-cells = <{cells}>;" in text
    assert "prescaler = <8>;" in text and "pinctrl-0 = <&tcc1_pwm_default>;" in text
    assert "pinmux = <PD20F_TCC1_WO0>, <PD21F_TCC1_WO1>;" in text
    assert text.count("max-bit-width") == (1 if compat.startswith("microchip") else 0)


def test_render_refuses_a_pad_without_the_signal():
    inst = _inst("TCC1", "TCC", ["WO0"], ["PD20"])
    node = DraftNode(cls="pwm", roles=["WO1"], instance="TCC1", pins={"WO1": "PD20"},
                     compatible="atmel,sam0-tcc-pwm", label="tcc1")
    from dt_explorer.core.render import RenderError
    with pytest.raises(RenderError, match="must sit on a pad with the signal WO1"):
        render_overlay(node, _ctx(inst))


# ---- class detection and lift against a workspace --------------------------------------------------
def test_class_pwm_in_both_workspaces(rtos4_ws, main_ws):
    old = BindingIndex([rtos4_ws / "zephyr" / "dts" / "bindings"])
    new = BindingIndex([main_ws / "zephyr" / "dts" / "bindings"])
    for idx, comps in ((old, ("atmel,sam0-tcc-pwm", "atmel,sam0-tc-pwm")),
                       (new, ("microchip,tcc-g1-pwm", "microchip,tc-g1-pwm", "atmel,sam0-tcc-pwm"))):
        for c in comps:
            cls, chain = detect_class(c, idx)
            assert cls == "pwm", (c, chain)
            assert chain[-1] == "pwm-controller.yaml"


# ---- lift + render back (P5.3 style edtlib compare), shared with test_adc_recipe.py ---------------
def lift_compare(rtos4_ws, rtos4_build, tmp_path, rel, label, base_compat, *, expect_roles, expect_cls,
                 expect_children=0):
    """Lift `label` of an old-port build, render the draft node, apply it to zephyr.dts with the board
    settings of the node stripped, and compare compatible, status, written properties, pinctrl groups and
    children with the original edt.pickle. The reset devicetree must differ (the compare is not vacuous)."""
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.core.lift import lift_build
    from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    from dt_explorer.recipes.adc import AdcRecipe
    from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe
    from test_lift import PINCTRL_INCLUDE, board_names, strip_node, summary
    from test_render import compile_edt
    for r in (AtmelSam0SercomRecipe, TccPwmRecipe, TcPwmRecipe, AdcRecipe):
        plugins.register_recipe(r)
    plugins.register_formatter(AtmelSam0Formatter)
    build = rtos4_build(rel)
    model = BuildModel(build)
    info = read_build_info(build)
    provider = MicrochipAtdfProvider()
    atdf = provider.locate(info)
    if atdf is None:
        pytest.skip(f"no ATDF for {info.soc} (hal_microchip packs / DFP not found)")
    draft, report = lift_build(model, info)
    node = next(n for n in draft.nodes if n.label == label)
    assert node.cls == expect_cls and node.pins == expect_roles and len(node.children) == expect_children
    path = next(p for p in model.by_path if model.by_path[p].labels[:1] == [label])
    names = board_names(model, path)
    groups = [c.labels[0] if c.labels else c.name for pc in model.by_path[path].pinctrls for c in pc.conf_nodes]
    zdts = (build / "zephyr" / "zephyr.dts").read_text(encoding="utf-8")
    base = strip_node(zdts, label, names, [], groups, base_compat)
    overlay = render_overlay(node, report.render_context(provider.load(atdf)))
    names_all = names + ["compatible", "status"]
    lifted = set(report.children)
    original = summary(model.edt, model, label, names_all, lifted)
    try:
        reset = summary(compile_edt(model, PINCTRL_INCLUDE + base, tmp_path), model, label, names_all, lifted)
    except Exception as exc:    # noqa: BLE001 - a consumer (pwm-leds, io-channels) may dangle without the board settings
        reset = f"reset devicetree does not compile: {str(exc).splitlines()[-1][-90:]}"
    rendered = summary(compile_edt(model, PINCTRL_INCLUDE + base + "\n" + overlay, tmp_path),
                       model, label, names_all, lifted)
    # the board names its pinctrl group freely (``pwm_default``), the renderer ``<label>_<class>_default``:
    # compare the pinmux values per group, not the group names
    for d in (original, rendered, reset):
        if isinstance(d, dict) and isinstance(d.get("pinctrl-0"), dict):
            d["pinctrl-0"] = sorted(d["pinctrl-0"].values())
    print(f"===== {rel}: {label} ({node.compatible}) roles={node.pins} props={node.props} vendor={node.vendor_props}")
    print(overlay)
    print(f"{label}: compare {'MATCH' if rendered == original else 'DIFFERENT'}; "
          f"reset differs from original: {reset != original}" + (f" ({reset})" if isinstance(reset, str) else ""))
    assert reset != original, "reset equals original, the compare proves nothing"
    assert rendered == original
    return node, overlay


SAME54_LAB1 = "_soltest/apps/lab1_leer/build_same54_xpro"


def test_lift_tcc0_of_same54_xpro_renders_back(rtos4_ws, rtos4_build, tmp_path, capsys):
    with capsys.disabled():
        print()
        lift_compare(rtos4_ws, rtos4_build, tmp_path, SAME54_LAB1, "tcc0", "atmel,sam0-tcc",
                     expect_roles={"WO2": "PC18"}, expect_cls="pwm")


def test_lift_of_the_main_sg41_build():
    build = Path(tempfile.gettempdir()) / "dtx_main_sg41"
    if not (build / "zephyr" / "edt.pickle").is_file():
        pytest.skip(f"no main build at {build} (recreate per P6.0: west build -b pic32cx_sg41_cult --cmake-only)")
    from dt_explorer.core.lift import lift_build
    from dt_explorer.formatters.microchip_port_g1 import MicrochipPortG1Formatter
    plugins.register_recipe(TccPwmRecipe)
    plugins.register_recipe(TcPwmRecipe)
    plugins.register_formatter(MicrochipPortG1Formatter)
    draft, _report = lift_build(build)
    by = {n.label: n for n in draft.nodes}
    assert (by["tcc0"].compatible, by["tcc0"].cls, by["tcc0"].pins) == ("microchip,tcc-g1-pwm", "pwm", {"WO5": "PC21"})
    assert by["tcc0"].props == {"#pwm-cells": 3} and by["tcc0"].vendor_props["prescaler"] == 8
    assert (by["tc5"].compatible, by["tc5"].pins) == ("microchip,tc-g1-pwm", {"WO0": "PB14", "WO1": "PB15"})
    assert by["tc5"].props == {"#pwm-cells": 3}


# ---- integration into the configurator (P8.8b) ---------------------------------------------------------
def _session(rel_or_path, rtos4_build=None):
    from dt_explorer.app import load_plugins
    from dt_explorer.core.build import BuildModel
    from dt_explorer.ui import configure as C
    load_plugins()
    build = rtos4_build(rel_or_path) if rtos4_build else rel_or_path
    return C.ConfigSession(BuildModel(build))


def test_options_prefer_the_family_of_the_node_old_port(rtos4_build):
    s = _session(SAME54_LAB1, rtos4_build)
    assert [o.compatible for o in s.options_for(s.node_for_label("tcc1"))] == ["atmel,sam0-tcc-pwm"]
    assert [o.compatible for o in s.options_for(s.node_for_label("tc2"))] == ["atmel,sam0-tc-pwm"]
    assert s.options_for(s.node_for_label("sercom1"))[0].compatible == "atmel,sam0-uart"
    assert s.alternatives_for(s.node_for_label("tc2")) == ["atmel,sam0-tc32"]       # counter mode: generic form
    assert s.alternatives_for(s.node_for_label("tcc1")) == [] and s.alternatives_for(s.node_for_label("sercom1")) == []


def test_options_prefer_the_family_of_the_node_new_port():
    build = Path(tempfile.gettempdir()) / "dtx_main_sg41"
    if not (build / "zephyr" / "edt.pickle").is_file():
        pytest.skip(f"no main build at {build} (recreate per P6.0)")
    s = _session(build)
    assert [o.compatible for o in s.options_for(s.node_for_label("tcc2"))] == ["microchip,tcc-g1-pwm"]
    assert [o.compatible for o in s.options_for(s.node_for_label("tc1"))] == ["microchip,tc-g1-pwm"]
    assert [o.compatible for o in s.options_for(s.node_for_label("adc1"))] == ["microchip,adc-g1"]
    assert {o.compatible for o in s.options_for(s.node_for_label("sercom1"))} <= {
        "microchip,sercom-g1-uart", "microchip,sercom-g1-i2c", "microchip,sercom-g1-spi"}
    assert s.alternatives_for(s.node_for_label("tc1")) == ["microchip,tc-g1-counter"]


def test_cells_properties_are_no_editable_fields(rtos4_build):
    from dt_explorer.ui import configure as C
    from dt_explorer.ui.generic_form import form_state
    assert C.is_cells_prop("#pwm-cells") and C.is_cells_prop("#io-channel-cells") and not C.is_cells_prop("prescaler")
    s = _session(SAME54_LAB1, rtos4_build)
    opt = s.options_for(s.node_for_label("tcc1"))[0]
    assert not [x for x in C.neutral_specs(opt, s.index) if x.name.startswith("#")]
    _c, specs, _r, _n = form_state(s, s.node_for_label("tc2"), "atmel,sam0-tc32")
    assert not [f for f in specs if f.name.startswith("#")]
