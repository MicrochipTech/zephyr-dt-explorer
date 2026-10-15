"""Configurator helpers (ui/configure.py, draft_tab.py, package_view.mark_draft) and render.const_props.

No browser and no NiceGUI page: the helpers are plain functions on the neutral device model.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.draft import BindingIndex
from dt_explorer.core.render import const_props
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe
from dt_explorer.ui import configure as C
from dt_explorer.ui.draft_tab import check_summary, kconfig_lines
from dt_explorer.ui.package_view import mark_draft

FIX = Path(__file__).resolve().parent / "fixtures"
ATDF = FIX / "atdf" / "ATSAME54P20A.atdf"


@pytest.fixture(autouse=True)
def _plugins():
    plugins.register_recipe(AtmelSam0SercomRecipe)


@pytest.fixture(scope="module")
def device():
    return MicrochipAtdfProvider().load(ATDF)


def opt(cls: str) -> C.ClassOption:
    r = AtmelSam0SercomRecipe()
    compat = {"uart": "atmel,sam0-uart", "i2c": "atmel,sam0-i2c", "spi": "atmel,sam0-spi"}[cls]
    return C.ClassOption(cls, compat, r)


def pads_of(cands) -> set[str]:
    return {c.pad for c in cands}


def pads_with_role(inst, role: str) -> set[str]:
    return {s.role and s.pad for s in inst.signals if s.role == role}


# ---- instance, classes, roles --------------------------------------------------------------------
def test_find_instance_by_address_then_label(device):
    s1 = device.instances["SERCOM1"]
    assert C.find_instance(device, [s1.base], []) is s1
    assert C.find_instance(device, [], ["sercom3"]).name == "SERCOM3"
    assert C.find_instance(device, [0x1234], ["nothing"]) is None
    assert C.find_instance(None, [1], []) is None


def test_required_and_optional_roles(device):
    assert C.required_roles(opt("uart"), ["TX", "RX", "RTS", "CTS"]) == ["TX", "RX"]
    assert C.required_roles(opt("i2c"), ["SDA", "SCL"]) == ["SDA", "SCL"]
    assert C.required_roles(opt("spi"), ["MOSI", "MISO", "SCK", "CS"]) == ["MOSI", "MISO", "SCK"]
    assert C.option_roles(opt("uart"), device.instances["SERCOM1"]) == ["TX", "RX", "RTS", "CTS"]


# ---- role -> candidate pads ------------------------------------------------------------------------
def test_i2c_candidates_follow_the_fixed_pads(device):
    inst = device.instances["SERCOM1"]
    cands = C.role_candidates(inst, opt("i2c"), ["SDA", "SCL"], {})
    assert pads_of(cands["SDA"]) == pads_with_role(inst, "PAD0")
    assert pads_of(cands["SCL"]) == pads_with_role(inst, "PAD1")
    assert pads_of(cands["SDA"]).isdisjoint(pads_of(cands["SCL"]))


def test_uart_roles_are_filtered_by_recipe_validity(device):
    inst = device.instances["SERCOM2"]
    roles = ["TX", "RX", "RTS", "CTS"]
    free = C.role_candidates(inst, opt("uart"), roles, {})
    assert pads_of(free["TX"]) == pads_with_role(inst, "PAD0")      # TX is always PAD0
    assert pads_of(free["RTS"]) == pads_with_role(inst, "PAD2")
    assert pads_of(free["CTS"]) == pads_with_role(inst, "PAD3")
    # TX always takes PAD0, so RX can never be on PAD0
    assert pads_of(free["RX"]) == set().union(*(pads_with_role(inst, f"PAD{i}") for i in (1, 2, 3)))
    tx = sorted(pads_of(free["TX"]))[0]
    after_tx = C.role_candidates(inst, opt("uart"), roles, {"TX": tx})
    assert tx not in pads_of(after_tx["RX"])
    assert pads_of(after_tx["RX"]) == set().union(*(pads_with_role(inst, f"PAD{i}") for i in (1, 2, 3)))
    # CTS chosen: txpo 2 needs RTS on PAD2 and RX can only use PAD1
    cts = sorted(pads_of(free["CTS"]))[0]
    after_cts = C.role_candidates(inst, opt("uart"), roles, {"CTS": cts})
    assert pads_of(after_cts["RX"]) == pads_with_role(inst, "PAD1")
    assert pads_of(after_cts["RTS"]) == pads_with_role(inst, "PAD2")


def test_spi_mosi_choice_narrows_miso(device):
    inst = device.instances["SERCOM4"]
    roles = ["MOSI", "MISO", "SCK", "CS"]
    free = C.role_candidates(inst, opt("spi"), roles, {})
    assert pads_of(free["MOSI"]) == pads_with_role(inst, "PAD0") | pads_with_role(inst, "PAD3")
    assert pads_of(free["SCK"]) == pads_with_role(inst, "PAD1")
    mosi3 = sorted(pads_with_role(inst, "PAD3"))[0]
    after = C.role_candidates(inst, opt("spi"), roles, {"MOSI": mosi3})
    assert pads_of(after["MISO"]) == pads_with_role(inst, "PAD0") | pads_with_role(inst, "PAD2")
    assert pads_of(after["CS"]) == pads_with_role(inst, "PAD2")


def test_taken_pads_stay_selectable_but_say_who_uses_them(device):
    inst = device.instances["SERCOM2"]
    pad = sorted(pads_with_role(inst, "PAD0"))[0]
    cands = C.role_candidates(inst, opt("uart"), ["TX", "RX"], {}, used={pad: ["led0 (GPIO)"]})
    hit = next(c for c in cands["TX"] if c.pad == pad)
    assert hit.used_by == ["led0 (GPIO)"]
    assert "used by led0 (GPIO)" in hit.label
    assert all(not c.used_by for c in cands["TX"] if c.pad != pad)


def test_recipe_without_valid_assignments_offers_every_signal():
    inst = Instance("X1", "X", 0, [Signal("A", "p1", "m"), Signal("B", "p2", "m")])
    recipe = SimpleNamespace(compatibles=["x,y"], pin_roles=lambda i: ["TX"], name="stub")
    o = C.ClassOption("uart", "x,y", recipe)
    cands = C.role_candidates(inst, o, ["TX"], {})
    assert pads_of(cands["TX"]) == {"p1", "p2"}
    assert C.required_roles(o, ["TX"]) == []


# ---- derived properties -----------------------------------------------------------------------------
def test_derive_props(device):
    inst = device.instances["SERCOM2"]
    props, problem = C.derive_props(opt("uart"), inst, {"TX": "PB25", "RX": "PB24"}, ["TX", "RX", "RTS", "CTS"])
    assert problem is None and props == {"rxpo": 1, "txpo": 0}
    props, problem = C.derive_props(opt("uart"), inst, {"TX": "PB25"}, ["TX", "RX", "RTS", "CTS"])
    assert props == {} and "RX" in problem
    props, problem = C.derive_props(opt("uart"), inst, {"TX": "PB25", "RX": "PA00"}, ["TX", "RX"])
    assert problem and "PA00" in problem                         # pad not on this instance
    props, problem = C.derive_props(opt("uart"), inst, {}, ["TX", "RX"])
    assert problem
    i1 = device.instances["SERCOM1"]
    props, problem = C.derive_props(opt("i2c"), i1, {"SDA": "PC22", "SCL": "PC23"}, ["SDA", "SCL"])
    assert (props, problem) == ({}, None)
    props, problem = C.derive_props(opt("i2c"), i1, {"SDA": "PC23", "SCL": "PC22"}, ["SDA", "SCL"])
    assert problem and "fixed" in problem                         # roles on swapped pads


# ---- draft node, properties --------------------------------------------------------------------------
def test_build_node_drops_empty_roles_and_values(device):
    inst = device.instances["SERCOM1"]
    n = C.build_node(option=opt("i2c"), instance=inst, label="sercom1",
                     pins={"SCL": "PC23", "SDA": "PC22"}, roles_order=["SDA", "SCL"],
                     props={"clock-frequency": 100000, "sq-size": None}, chosen=["", "x,y"], aliases=["my-i2c"])
    assert (n.cls, n.instance, n.compatible, n.label) == ("i2c", "SERCOM1", "atmel,sam0-i2c", "sercom1")
    assert n.roles == ["SDA", "SCL"] and list(n.pins) == ["SDA", "SCL"]
    assert n.props == {"clock-frequency": 100000}
    assert n.use.chosen == ["x,y"] and n.use.aliases == ["my-i2c"]
    assert n.vendor_props == {}
    n2 = C.build_node(option=opt("uart"), instance=inst, label="s", pins={"TX": "PB25", "RX": None},
                      roles_order=["TX", "RX", "RTS", "CTS"])
    assert n2.roles == ["TX"] and n2.pins == {"TX": "PB25"}


def test_parse_prop():
    i = C.PropSpec("current-speed", "int")
    assert C.parse_prop(i, "115200") == 115200
    assert C.parse_prop(i, "0x10") == 16
    assert C.parse_prop(i, " ") is None and C.parse_prop(i, None) is None
    with pytest.raises(ValueError):
        C.parse_prop(i, "fast")
    assert C.parse_prop(C.PropSpec("parity", "string", ["none", "odd"]), "odd") == "odd"
    b = C.PropSpec("hw-flow-control", "boolean")
    assert C.parse_prop(b, True) is True and C.parse_prop(b, False) is None


def write_binding(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8")


def test_neutral_specs_skip_generic_and_const(tmp_path):
    write_binding(tmp_path, "base.yaml", "properties:\n  status:\n    type: string\n  wakeup-source:\n    type: boolean\n")
    write_binding(tmp_path, "uart-controller.yaml", """include: base.yaml
properties:
  current-speed:
    type: int
    description: Baud rate
  parity:
    type: string
    enum: [none, odd]
    default: none
  "#address-cells":
    const: 1
  cs-gpios:
    type: phandle-array
""")
    index = BindingIndex([tmp_path])
    specs = {s.name: s for s in C.neutral_specs(opt("uart"), index)}
    assert set(specs) == {"current-speed", "parity"}
    assert specs["parity"].enum == ["none", "odd"] and specs["parity"].default == "none"
    assert specs["current-speed"].description == "Baud rate"


def test_neutral_specs_hide_rtio_queue_sizes(tmp_path):
    """sq-size/cq-size sit in i2c-controller.yaml itself; the dialog hides them, clock-frequency stays."""
    write_binding(tmp_path, "base.yaml", "properties:\n  status:\n    type: string\n")
    write_binding(tmp_path, "i2c-controller.yaml", """include: base.yaml
properties:
  clock-frequency:
    type: int
  sq-size:
    type: int
    default: 4
  cq-size:
    type: int
    default: 4
""")
    index = BindingIndex([tmp_path])
    assert [s.name for s in C.neutral_specs(opt("i2c"), index)] == ["clock-frequency"]


def test_class_options_come_from_the_recipe(tmp_path, device):
    write_binding(tmp_path, "base.yaml", "properties: {}\n")
    for base in ("uart", "i2c", "spi"):
        write_binding(tmp_path, f"{base}-controller.yaml", "include: base.yaml\n")
        write_binding(tmp_path, f"atmel,sam0-{base}.yaml",
                      f'compatible: "atmel,sam0-{base}"\ninclude:\n  - name: {base}-controller.yaml\n')
    index = BindingIndex([tmp_path])
    options = C.class_options(device.instances["SERCOM3"], index)
    assert {o.cls: o.compatible for o in options} == {
        "uart": "atmel,sam0-uart", "i2c": "atmel,sam0-i2c", "spi": "atmel,sam0-spi"}
    other = Instance("TCC0", "TCC", 0, [])
    assert C.class_options(other, index) == []                     # no recipe knows this kind


# ---- render: const properties of the binding chain ---------------------------------------------------
def test_const_props_of_binding_chain(tmp_path):
    write_binding(tmp_path, "base.yaml", "properties:\n  status:\n    const: okay\n")
    write_binding(tmp_path, "bus.yaml", """include: base.yaml
properties:
  "#address-cells": {required: true, const: 1}
  "#size-cells": {required: true, const: 0}
  clock-frequency: {type: int}
""")
    write_binding(tmp_path, "dev.yaml", 'compatible: "x,dev"\ninclude: bus.yaml\n')
    index = BindingIndex([tmp_path])
    assert const_props("x,dev", index) == {"#address-cells": 1, "#size-cells": 0}
    assert const_props("x,unknown", index) == {}
    assert const_props("x,dev", None) == {}
    assert const_props(None, index) == {}


# ---- Draft tab / package view helpers ------------------------------------------------------------------
def test_kconfig_lines_and_summary():
    a = SimpleNamespace(symbols={"CONFIG_I2C": "y", "CONFIG_I2C_SAM0": "y"})
    b = SimpleNamespace(symbols={"CONFIG_I2C": "y", "CONFIG_SERIAL": "y"})
    assert kconfig_lines([a, b]) == ["CONFIG_I2C=y", "CONFIG_I2C_SAM0=y", "CONFIG_SERIAL=y"]
    f = [SimpleNamespace(severity=s) for s in ("error", "error", "warning", "info")]
    assert check_summary(f) == "2 error(s), 1 warning(s), 1 info"
    assert check_summary([]) == "0 error(s), 0 warning(s), 0 info"


def test_mark_draft_colours_only_pad_rows():
    rows = {
        "1": {"position": "1", "pad": "P1", "state": "free", "users": []},
        "2": {"position": "2", "pad": "P2", "state": "used", "users": [{"path": "/a", "use": "x"}]},
        "3": {"position": "3", "pad": "GND", "state": "other", "users": []},
    }
    out = mark_draft(rows, {"P1": ["n SDA (draft)"], "P2": ["n SCL (draft)"], "GND": ["n X (draft)"]})
    assert out["1"]["state"] == "draft" and out["2"]["state"] == "draft"
    assert out["3"]["state"] == "other"                              # power pins are never claimed
    assert out["2"]["users"][0]["path"] == "/a" and out["2"]["users"][-1]["path"] == "draft"
    assert rows["1"]["state"] == "free"                              # input not modified


# ---- P5.7: Edit pins for enabled nodes -----------------------------------------------------------
def test_rename_group_whole_words_only():
    text = ("&pinctrl {\n\tsercom2_uart_default: sercom2_uart_default {\n\t};\n};\n"
            "&sercom2 { pinctrl-0 = <&sercom2_uart_default>; };\n"
            "&x { pinctrl-0 = <&sercom2_uart_default_2>; };\n")
    out = C.rename_group(text, "sercom2_uart_default", "sercom2_uart_edit")
    assert out.count("sercom2_uart_edit") == 3
    assert "sercom2_uart_default_2" in out                       # a longer name is left alone
    assert "sercom2_uart_default:" not in out and "&sercom2_uart_default>" not in out


def test_edit_group_name_differs_from_board_group():
    n = C.DraftNode(cls="uart", label="sercom2")
    assert C.edit_group_name(n) == "sercom2_uart_edit"
    assert C.edit_group_name(n) != f"{n.label}_{n.cls}_default"


@pytest.fixture
def lab1_session(rtos4_ws, rtos4_build):
    """ConfigSession of lab1 with an empty draft (never reads or writes the app's real draft file)."""
    from dt_explorer.app import load_plugins
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.draft import Draft
    load_plugins()
    model = BuildModel(rtos4_build("apps/lab1/build_same54_xpro"))
    s = C.ConfigSession(model)
    s.draft = Draft(build=model.build.name)
    return s


def _edit_console(s, tx, rx):
    n = s.node_for_label("sercom2")
    inst = s.instance_of(n)
    o = next(x for x in s.options_for(n) if x.cls == "uart")
    lifted = s.lifted_node("sercom2")
    new = C.build_node(option=o, instance=inst, label="sercom2", pins={"TX": tx, "RX": rx},
                       roles_order=C.option_roles(o, inst), props=dict(lifted.props),
                       chosen=lifted.use.chosen, aliases=lifted.use.aliases)
    s.upsert(new)
    return n, inst, o, new


def test_enabled_node_can_be_edited_and_is_lifted(lab1_session):
    s = lab1_session
    n = s.node_for_label("sercom2")
    assert C.can_edit_pins(s, n) and not C.can_configure(s, n)
    lifted = s.lifted_node("sercom2")
    assert lifted.cls == "uart" and lifted.pins == {"TX": "PB25", "RX": "PB24"}
    assert lifted.props["current-speed"] == 115200
    assert "zephyr,console" in lifted.use.chosen
    assert s.lifted_node("nothing") is None
    disabled = next(x for x in s.model.edt.nodes if "sercom1" in x.labels)
    assert not C.can_edit_pins(s, disabled) and C.can_configure(s, disabled)


def test_own_pads_are_not_reported_as_taken_while_editing(lab1_session):
    s = lab1_session
    n = s.node_for_label("sercom2")
    assert any("sercom2" in u for u in s.used_by().get("PB24", []))
    assert not s.used_by(skip_path=n.path).get("PB24")


def test_edit_overlay_uses_a_new_group_and_recomputes_derived_props(lab1_session):
    s = lab1_session
    _edit_console(s, "PA09", "PA08")
    text = s.overlay()
    assert "sercom2_uart_edit: sercom2_uart_edit {" in text
    assert "pinctrl-0 = <&sercom2_uart_edit>;" in text
    assert "sercom2_uart_default" not in text                    # the board's group is not touched
    assert "PA9D_SERCOM2_PAD0" in text and "PA8D_SERCOM2_PAD1" in text
    assert "compatible" not in text                              # the node already has it
    assert "rxpo = <1>;" in text and "txpo = <0>;" in text
    assert "zephyr,console = &sercom2;" in text
    _edit_console(s, "PA09", "PA10")                             # RX on PAD2: derived rxpo changes
    assert "rxpo = <2>;" in s.overlay()


def test_edit_overlay_checks_without_errors(lab1_session):
    s = lab1_session
    _edit_console(s, "PA09", "PA08")
    findings = s.check()
    assert not [f for f in findings if f.severity == "error"]
