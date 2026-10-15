"""core/generic.py and the form state of ui/generic_form.py: the generic binding form (P8.5).

Synthetic bindings in tmp_path for the form rules; the lab1_leer build (DTX_RTOS4_WS) for the real nodes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dt_explorer.core import generic as G
from dt_explorer.core.device import DeviceModel, Instance, Signal
from dt_explorer.core.draft import BindingIndex, Draft
from dt_explorer.core.render import RenderContext, render_overlay

BASE = """
properties:
  status: {type: string}
  wakeup-source: {type: boolean}
  label: {type: string}
"""
DEVICE = """
description: test device
compatible: "acme,thing"
include:
  - name: base.yaml
  - name: pinctrl-device.yaml
properties:
  reg: {required: true}
  mode:
    type: string
    required: true
    enum: [fast, slow]
    description: |
      Operating mode.
      Second line is not shown.
  prescaler:
    type: int
    default: 1
    enum: [1, 2, 4, 8]
  width:
    type: int
    required: true
  run-in-standby:
    type: boolean
  "#thing-cells":
    type: int
    const: 2
  fixed:
    type: int
    const: 3
  clocks: {type: phandle-array, required: true}
  names: {type: string-array}
"""
PINCTRL = """
properties:
  pinctrl-0: {type: phandles}
  pinctrl-names: {type: string-array}
"""
NOPINS = """
description: no pins
compatible: "acme,plain"
include: base.yaml
properties:
  interrupts: {required: true}
"""


@pytest.fixture
def index(tmp_path) -> BindingIndex:
    d = tmp_path / "bindings"
    d.mkdir()
    (d / "base.yaml").write_text(BASE)
    (d / "pinctrl-device.yaml").write_text(PINCTRL)
    (d / "acme,thing.yaml").write_text(DEVICE)
    (d / "acme,plain.yaml").write_text(NOPINS)
    return BindingIndex([d])


def by_name(specs):
    return {s.name: s for s in specs}


def test_form_spec_fields_and_exclusions(index):
    specs = by_name(G.form_spec("acme,thing", index, {"prescaler": 2}))
    assert list(specs) == ["mode", "prescaler", "width", "run-in-standby"]    # binding order
    mode = specs["mode"]
    assert (mode.type, mode.required, mode.enum) == ("string", True, ["fast", "slow"])
    assert mode.description == "Operating mode."                               # first line of the description
    assert specs["prescaler"].default == 1 and specs["prescaler"].current == 2
    assert specs["width"].required and not specs["prescaler"].required
    # left out: base.yaml, const, #-cells, reg/compatible/status, non-scalar, pinctrl-*
    for gone in ("status", "wakeup-source", "label", "#thing-cells", "fixed", "reg", "clocks", "names", "pinctrl-0"):
        assert gone not in specs


def test_form_notes_and_pinctrl(index):
    assert G.form_notes("acme,thing", index) == ["clocks", "names"]
    assert G.has_pinctrl("acme,thing", index) and not G.has_pinctrl("acme,plain", index)
    assert G.form_spec("acme,plain", index) == []
    assert G.form_spec("nobody,knows", index) == []


def test_required_empty_is_flagged(index):
    specs = G.form_spec("acme,thing", index)
    problems = G.check_form(specs, {"mode": "", "width": "", "prescaler": "", "run-in-standby": False})
    assert problems == {"mode": "required", "width": "required"}
    assert G.check_form(specs, {"mode": "fast", "width": "16"}) == {}


def test_required_is_met_by_the_board_value(index):
    specs = G.form_spec("acme,thing", index, {"mode": "slow", "width": 8})
    texts = {s.name: s.current for s in specs}
    assert G.check_form(specs, texts) == {}
    texts["width"] = ""                                         # the user clears a prefilled required field
    assert G.check_form(specs, texts) == {"width": "required"}


def test_type_and_enum_problems(index):
    specs = G.form_spec("acme,thing", index)
    p = G.check_form(specs, {"mode": "medium", "width": "abc", "prescaler": "3"})
    assert set(p) == {"mode", "width", "prescaler"}
    assert "not one of fast, slow" in p["mode"] and p["width"] == "not a number" and "not one of 1, 2, 4, 8" in p["prescaler"]
    assert G.check_form(specs, {"mode": "fast", "width": "0x10", "prescaler": "8"}) == {}


def test_boolean_set_by_the_board_cannot_be_switched_off(index):
    specs = G.form_spec("acme,thing", index, {"run-in-standby": True, "mode": "fast", "width": 1})
    assert "run-in-standby" in G.check_form(specs, {"mode": "fast", "width": "1", "run-in-standby": False})
    assert G.check_form(specs, {"mode": "fast", "width": "1", "run-in-standby": True}) == {}


def test_form_props_only_what_differs(index):
    specs = G.form_spec("acme,thing", index, {"mode": "fast", "width": 16, "prescaler": 2})
    texts = {"mode": "fast", "width": "32", "prescaler": "2", "run-in-standby": True}
    assert G.form_props(specs, texts) == {"width": 32, "run-in-standby": True}
    assert G.form_props(specs, {"mode": "fast", "width": "16", "prescaler": ""}) == {}


# ---- pins ----------------------------------------------------------------------------------------
INST = Instance("T0", "T", 0x1000, [Signal("WO0", "PA01", "E"), Signal("WO0", "PB05", "E"),
                                    Signal("WO1", "PA02", "E"), Signal("WO1", "PA01", "F")])


def test_pin_roles_and_candidates():
    assert G.pin_roles(INST) == ["WO0", "WO1"] and G.pin_roles(None) == []
    assert G.pad_candidates(INST, "WO0", {}) == [("PA01", "E"), ("PB05", "E")]
    assert G.pad_candidates(INST, "WO0", {"WO1": "PA02"}) == [("PA01", "E"), ("PB05", "E")]
    assert G.pad_candidates(INST, "WO1", {"WO0": "PA01"}) == [("PA02", "E")]      # PA01 is taken by WO0


def test_pin_problems():
    assert G.pin_problems(INST, {"WO0": "PB05"}) == {}
    assert "offers 2 signals" in G.pin_problems(INST, {"WO0": "PA01"})["WO0"]
    assert "not a signal of T0" in G.pin_problems(INST, {"WO0": "PZ9"})["WO0"]


# ---- draft node and render --------------------------------------------------------------------------
class FakeFormatter:
    def render_group(self, sigs):
        return "group1 { pinmux = " + ", ".join(f"<{s.pad}_{s.mux}>" for s in sigs) + "; };"


def test_build_generic_node_drops_empty_roles_and_values():
    n = G.build_generic_node(label="t0", compatible="acme,thing", instance="T0", props={"width": 8, "x": None},
                             pins={"WO0": "PB05", "WO1": ""}, roles_order=["WO0", "WO1"], chosen=["zephyr,x", ""])
    assert (n.cls, n.roles, n.pins, n.props, n.use.chosen) == ("generic", ["WO0"], {"WO0": "PB05"}, {"width": 8}, ["zephyr,x"])
    assert G.is_generic(n)


def test_generic_node_renders_without_recipe(index):
    n = G.build_generic_node(label="t0", compatible="acme,thing", instance="T0",
                             props={"mode": "fast", "width": 16, "prescaler": 4, "run-in-standby": True},
                             pins={"WO0": "PB05"}, roles_order=["WO0"], aliases=["my-timer"])
    ctx = RenderContext(device=DeviceModel("X", [], {"T0": INST}, []), formatter=FakeFormatter(), bindings=index)
    text = render_overlay(Draft(nodes=[n]), ctx)
    assert "t0_generic_default: t0_generic_default {" in text and "pinmux = <PB05_E>;" in text
    assert ('&t0 {\n\tstatus = "okay";\n\tcompatible = "acme,thing";\n\t#thing-cells = <2>;\n\tfixed = <3>;\n'
            '\tmode = "fast";\n\twidth = <16>;\n\tprescaler = <4>;\n\trun-in-standby;\n'
            '\tpinctrl-0 = <&t0_generic_default>;\n\tpinctrl-names = "default";\n};') in text
    assert "aliases {\n\t\tmy-timer = &t0;\n" in text


def test_node_without_pins_needs_no_device(index):
    n = G.build_generic_node(label="plain", compatible="acme,plain")
    assert render_overlay(n, RenderContext()) == '&plain {\n\tstatus = "okay";\n\tcompatible = "acme,plain";\n};\n'


# ---- real nodes of the lab1_leer build ---------------------------------------------------------------
@pytest.fixture
def leer_session(rtos4_build):
    from dt_explorer.app import load_plugins
    from dt_explorer.core.build import BuildModel
    from dt_explorer.ui import configure as C
    load_plugins()
    return C.ConfigSession(BuildModel(rtos4_build("_soltest/apps/lab1_leer/build_same54_xpro")))


def test_real_nodes_have_the_generic_form(leer_session):
    from dt_explorer.ui import configure as C
    from dt_explorer.ui.generic_form import form_state, generic_available
    s = leer_session
    tc0, wdog, sercom1 = (s.node_for_label(x) for x in ("tc0", "wdog", "sercom1"))
    assert generic_available(s, tc0) and generic_available(s, wdog) and not generic_available(s, sercom1)
    assert C.can_configure(s, tc0) and C.can_configure(s, sercom1)
    assert not C.can_configure(s, wdog)                         # enabled: no Configure... button
    compat, specs, roles, notes = form_state(s, tc0)
    assert compat == "atmel,sam0-tc32"
    f = specs[0]
    assert (f.name, f.type, f.required, f.enum) == ("prescaler", "int", False, [1, 2, 4, 8, 16, 64, 256, 1024])
    assert roles == ["WO0", "WO1"] and "atmel,assigned-clocks" in notes
    compat, specs, roles, notes = form_state(s, wdog)
    assert (compat, specs, roles) == ("atmel,sam0-watchdog", [], [])


def test_real_wdog_and_tc0_render_and_check(leer_session):
    s = leer_session
    tc0, wdog = s.node_for_label("tc0"), s.node_for_label("wdog")
    s.upsert(G.build_generic_node(label="wdog", compatible=wdog.compats[0]))
    s.upsert(G.build_generic_node(label="tc0", compatible=tc0.compats[0], instance="TC0", props={"prescaler": 64}))
    assert s.overlay() == ('&wdog {\n\tstatus = "okay";\n};\n\n'
                           '&tc0 {\n\tstatus = "okay";\n\tcompatible = "atmel,sam0-tc32";\n\tprescaler = <64>;\n};\n')
    assert [f for f in s.check() if f.severity == "error"] == []
