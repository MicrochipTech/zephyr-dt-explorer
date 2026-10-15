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
from dt_explorer.core.translate import Ref

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
    import sys
    load_plugins()
    saved = {k: v for k, v in sys.modules.items() if k == "devicetree" or k.startswith("devicetree.")}
    path = list(sys.path)
    yield C.ConfigSession(BuildModel(rtos4_build("_soltest/apps/lab1_leer/build_same54_xpro")))
    # check() imports this workspace's edtlib into the process; later tests (GMAC, main workspace) need theirs
    for k in [k for k in sys.modules if k == "devicetree" or k.startswith("devicetree.")]:
        del sys.modules[k]
    sys.modules.update(saved)
    sys.path[:] = path


def test_real_nodes_have_the_generic_form(leer_session):
    from dt_explorer.ui import configure as C
    from dt_explorer.ui.generic_form import form_state, generic_available
    s = leer_session
    can0, wdog, sercom1, tc0 = (s.node_for_label(x) for x in ("can0", "wdog", "sercom1", "tc0"))
    assert generic_available(s, can0) and generic_available(s, wdog) and not generic_available(s, sercom1)
    assert C.can_configure(s, can0) and C.can_configure(s, sercom1)
    assert not C.can_configure(s, wdog)                         # enabled: no Configure... button
    compat, specs, roles, notes = form_state(s, can0)
    assert compat == "atmel,sam0-can"
    f = next(x for x in specs if x.name == "divider")
    assert (f.type, f.required) == ("int", True) and "RX" in roles and "TX" in roles
    # a timer: the recipe covers PWM, the counter mode is the generic form (other compatible)
    assert not generic_available(s, tc0) and C.can_configure(s, tc0)
    assert s.alternatives_for(tc0) == ["atmel,sam0-tc32"] and s.alternatives_for(sercom1) == []
    compat, specs, roles, notes = form_state(s, tc0, "atmel,sam0-tc32")
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


# ---- child devices on a bus (P8.6) -----------------------------------------------------------------------
I2C_DEVICE = """
on-bus: i2c
properties:
  reg: {required: true}
"""
SPI_DEVICE = """
on-bus: spi
properties:
  reg: {required: true}
  spi-max-frequency: {type: int, required: true}
  spi-cpol: {type: boolean}
"""
SENSOR_I2C = """
description: Acme sensor on I2C
compatible: "acme,sensor"
include: [i2c-device.yaml]
properties:
  range:
    type: int
    enum: [2, 4, 8]
  int-gpios: {type: phandle-array, required: true}
  reset-gpios: {type: phandle-array}
  friendly-name: {type: string}
"""
SENSOR_SPI = """
description: Acme sensor on SPI
compatible: "acme,sensor"
include: [spi-device.yaml]
properties:
  int-gpios: {type: phandle-array}
  mode: {type: int, required: true}
"""
CONTROLLER = """
description: an I2C controller
compatible: "acme,i2c"
include: [i2c-device.yaml]
bus: i2c
"""
NOBUS = """
description: no bus
compatible: "acme,none"
properties:
  x: {type: int}
"""


@pytest.fixture
def bus_index(tmp_path) -> BindingIndex:
    d = tmp_path / "bus"
    d.mkdir()
    for name, text in {"i2c-device.yaml": I2C_DEVICE, "spi-device.yaml": SPI_DEVICE, "acme,sensor-i2c.yaml": SENSOR_I2C,
                       "acme,sensor-spi.yaml": SENSOR_SPI, "acme,i2c.yaml": CONTROLLER, "acme,none.yaml": NOBUS}.items():
        (d / name).write_text(text)
    return BindingIndex([d])


def test_child_options_follow_on_bus(bus_index):
    assert [o.compatible for o in G.child_options(bus_index, "i2c")] == ["acme,sensor"]    # the controller is no child
    assert [o.compatible for o in G.child_options(bus_index, "spi")] == ["acme,sensor"]
    assert G.child_options(bus_index, "i2c")[0].description == "Acme sensor on I2C"
    assert G.child_options(bus_index, "uart") == []
    assert G.binding_for("acme,sensor", bus_index, "spi").name == "acme,sensor-spi.yaml"
    assert G.binding_for("acme,sensor", bus_index, "i2c").name == "acme,sensor-i2c.yaml"


def test_child_form_is_bus_aware(bus_index):
    i2c = {s.name: s for s in G.child_form("acme,sensor", bus_index, "i2c")}
    spi = {s.name: s for s in G.child_form("acme,sensor", bus_index, "spi")}
    assert list(i2c) == ["range", "friendly-name"]                       # reg and the gpio phandles are no scalar fields
    assert list(spi) == ["mode", "spi-max-frequency", "spi-cpol"] and spi["spi-max-frequency"].required
    assert [(g.name, g.required) for g in G.gpio_specs("acme,sensor", bus_index, "i2c")] == \
        [("int-gpios", True), ("reset-gpios", False)]
    assert [(g.name, g.required) for g in G.gpio_specs("acme,sensor", bus_index, "spi")] == [("int-gpios", False)]


def test_parse_address():
    assert G.parse_address("i2c", "76") == 0x76 and G.parse_address("i2c", "0x76") == 0x76
    assert G.parse_address("spi", "2") == 2 and G.unit_of(0x76) == "76" and G.unit_of(0) == "0"
    for bus, text, why in (("i2c", "", "required"), ("i2c", "zz", "not a hex address"), ("i2c", "80", "7 bits"),
                           ("spi", "x", "not a chip-select index"), ("spi", "-1", "not a chip-select index")):
        with pytest.raises(ValueError, match=why):
            G.parse_address(bus, text)


def test_check_child_problems(bus_index):
    specs = G.child_form("acme,sensor", bus_index, "i2c")
    gpios = G.gpio_specs("acme,sensor", bus_index, "i2c")
    assert G.check_child("i2c", None, "", specs, {}, gpios, {}) == {
        "compatible": "choose a device", "address": "required", "int-gpios": "required"}
    assert G.check_child("i2c", "acme,sensor", "76", specs, {"range": "4"}, gpios, {"int-gpios": "PB14"}) == {}
    assert set(G.check_child("i2c", "acme,sensor", "76", specs, {"range": "5"}, gpios, {"int-gpios": "PB14"})) == {"range"}


def test_child_names_labels_and_unique_names(bus_index):
    assert G.child_node_name("bosch,bme280") == "bme280" and G.child_node_name("jedec,spi-nor") == "spi-nor"
    assert G.child_node_name("nodash") == "nodash" and G.child_label("spi-nor", "0") == "spi_nor_0"
    a = G.build_child(bus="i2c", compatible="acme,sensor", address=0x76, props={"range": 4, "friendly-name": None},
                      gpio_props={"int-gpios": [Ref("portb"), 14, 0]})
    assert (a.node_name, a.labels, a.compatible) == ("sensor@76", ["sensor_76"], ["acme,sensor"])
    assert a.props == {"reg": [0x76], "range": 4, "int-gpios": [[Ref("portb"), 14, 0]]}
    b = G.build_child(bus="i2c", compatible="acme,sensor", address=0x76, siblings=[a])      # same name@unit would merge
    assert b.node_name == "sensor-2@76" and b.labels == ["sensor_2_76"]
    c = G.build_child(bus="i2c", compatible="acme,sensor", address=0x77, siblings=[a])
    assert c.node_name == "sensor@77"
    assert G.address_clashes([a, c], 0x76) == ["sensor@76"] and G.address_clashes([a, c], 0x76, skip="sensor@76") == []
    # the child renders with its reg in hex, the labelled node and the gpio entry
    from dt_explorer.core.render import render_child
    assert render_child(a) == ('sensor_76: sensor@76 {\n\tcompatible = "acme,sensor";\n\treg = <0x76>;\n'
                               '\trange = <4>;\n\tint-gpios = <&portb 14 0>;\n};')


def test_cs_entries():
    e0, e1 = [Ref("portb"), 14, 1], [Ref("portb"), 15, 1]
    p = G.set_cs_entry({"x": 1}, 0, e0)
    assert p == {"x": 1, "cs-gpios": [e0]}
    p = G.set_cs_entry(p, 1, e1)
    assert p["cs-gpios"] == [e0, e1]
    with pytest.raises(ValueError, match="needs cs-gpios entries 0..2"):
        G.set_cs_entry(p, 3, e0)
    assert G.drop_cs_entry(p, 0)["cs-gpios"] == [e0, e1]            # an inner entry stays: later ones would shift
    assert G.drop_cs_entry(p, 1)["cs-gpios"] == [e0]
    assert "cs-gpios" not in G.drop_cs_entry({"cs-gpios": [e0]}, 0)


def test_gpio_entry_and_pad(bus_index):
    gmap = {"PB14": ("portb", 14), "PA00": ("porta", 0)}
    e = G.gpio_entry(gmap, "PB14", G.GPIO_FLAGS["active low"])
    assert e == [Ref("portb"), 14, 1] and G.entry_pad(gmap, e) == "PB14"
    assert G.entry_pad(gmap, [Ref("portc"), 1, 0]) is None and G.entry_pad(gmap, "junk") is None
    with pytest.raises(ValueError, match="no GPIO controller"):
        G.gpio_entry(gmap, "PZ99")


def test_real_bus_devices_of_the_workspace(leer_session):
    s = leer_session
    i2c = {o.compatible for o in G.child_options(s.index, "i2c")}
    spi = {o.compatible for o in G.child_options(s.index, "spi")}
    assert {"bosch,bme280", "bosch,bme680"} <= i2c and {"bosch,bme280", "bosch,bmi160", "jedec,spi-nor"} <= spi
    assert "atmel,sam0-i2c" not in i2c                                        # a controller is no child
    assert G.binding_for("bosch,bme280", s.index, "spi").name == "bosch,bme280-spi.yaml"
    assert G.binding_for("bosch,bme280", s.index, "i2c").name == "bosch,bme280-i2c.yaml"
    assert [g.name for g in G.gpio_specs("bosch,bmi160", s.index, "spi")] == ["supply-gpios", "int-gpios"]
    assert {f.name: f.required for f in G.child_form("bosch,bmi160", s.index, "spi")}["spi-max-frequency"]


def test_real_gpio_map_inverts_the_pad_lookup(leer_session):
    s = leer_session
    gmap = s.gpio_map()
    assert gmap["PB24"] == ("portb", 24) and gmap["PA00"] == ("porta", 0) and gmap["PC22"] == ("portc", 22)
    from dt_explorer.core.pins import _gpio_pad
    ctrl = next(n for n in s.model.edt.nodes if n.labels and n.labels[0] == "portb")
    assert _gpio_pad(s.model, s.device, ctrl, 24, []) == "PB24"


def test_real_children_render_and_check(leer_session):
    """SERCOM1 I2C with a bme280; the overlay is the one from the evidence run."""
    from dt_explorer.ui import configure as C
    s = leer_session
    n = s.node_for_label("sercom1")
    opt = next(o for o in s.options_for(n) if o.cls == "i2c")
    kid = G.build_child(bus="i2c", compatible="bosch,bme280", address=0x76)
    s.upsert(C.build_node(option=opt, instance=s.instance_of(n), label="sercom1", pins={"SDA": "PC22", "SCL": "PC23"},
                          roles_order=C.option_roles(opt, s.instance_of(n)), children=[kid]))
    text = s.overlay()
    assert "\tbme280_76: bme280@76 {\n\t\tcompatible = \"bosch,bme280\";\n\t\treg = <0x76>;\n\t};" in text
    assert "#address-cells = <1>;" in text and "#size-cells = <0>;" in text
    assert [f for f in s.check() if f.severity == "error"] == []
    assert {"CONFIG_BME280=y"} <= {f"{k}={v}" for k in [] for v in []} | {
        f"{k}={v}" for sug in s.kconfig() for k, v in sug.symbols.items()}
