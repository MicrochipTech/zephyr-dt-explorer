"""Overlay rendering (core/render.py): fixture unit tests and the edtlib compare on the RTOS4 build."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.draft import Draft, DraftNode, Use
from dt_explorer.core.render import RenderContext, RenderError, render_overlay
from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter, parse_dtsi_groups
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe

FIX = Path(__file__).resolve().parent / "fixtures"
HEADER = FIX / "sam0_pinctrl" / "same54p-pinctrl.h"
ATDF = FIX / "atdf" / "ATSAME54P20A.atdf"


@pytest.fixture(autouse=True)
def _plugins():
    plugins.register_recipe(AtmelSam0SercomRecipe)
    plugins.register_formatter(AtmelSam0Formatter)


@pytest.fixture(scope="module")
def device():
    return MicrochipAtdfProvider().load(ATDF)


def ctx_for(device, **kw) -> RenderContext:
    return RenderContext(device=device, pinctrl_compatible="atmel,sam0-pinctrl",
                         formatter=AtmelSam0Formatter(HEADER), **kw)


def uart_node(**kw) -> DraftNode:
    base = dict(cls="uart", roles=["TX", "RX"], props={"current-speed": 115200},
                use=Use(chosen=["zephyr,console"], aliases=["my-uart"]),
                instance="SERCOM2", pins={"TX": "PB25", "RX": "PB24"},
                vendor_props={}, compatible="atmel,sam0-uart", label="sercom2")
    base.update(kw)
    return DraftNode(**base)


UART_EXPECTED = """\
&pinctrl {
\tsercom2_uart_default: sercom2_uart_default {
\t\tgroup1 {
\t\t\tpinmux = <PB25D_SERCOM2_PAD0>,
\t\t\t\t <PB24D_SERCOM2_PAD1>;
\t\t};
\t};
};

&sercom2 {
\tstatus = "okay";
\tcompatible = "atmel,sam0-uart";
\tcurrent-speed = <115200>;
\trxpo = <1>;
\ttxpo = <0>;
\tpinctrl-0 = <&sercom2_uart_default>;
\tpinctrl-names = "default";
};

/ {
\tchosen {
\t\tzephyr,console = &sercom2;
\t};
\taliases {
\t\tmy-uart = &sercom2;
\t};
};
"""


def test_uart_text(device):
    assert render_overlay(uart_node(), ctx_for(device)) == UART_EXPECTED


def test_compatible_left_out_when_node_has_it(device):
    text = render_overlay(uart_node(), ctx_for(device, existing_compatibles={"sercom2": "atmel,sam0-uart"}))
    assert "compatible" not in text


def test_i2c_has_no_derived_props(device):
    n = DraftNode(cls="i2c", roles=["SDA", "SCL"], props={"clock-frequency": 100000},
                  instance="SERCOM7", pins={"SDA": "PD08", "SCL": "PD09"},
                  compatible="atmel,sam0-i2c", label="sercom7")
    text = render_overlay(n, ctx_for(device))
    assert "pinmux = <PD8C_SERCOM7_PAD0>," in text and "<PD9C_SERCOM7_PAD1>;" in text
    assert "clock-frequency = <100000>;" in text
    assert "rxpo" not in text and "dipo" not in text
    assert "/ {" not in text


def test_vendor_props_do_not_override_derived(device):
    text = render_overlay(uart_node(vendor_props={"rxpo": 3, "#size-cells": 0}), ctx_for(device))
    assert "rxpo = <1>;" in text and "rxpo = <3>" not in text and "#size-cells = <0>;" in text


def test_draft_shares_one_pinctrl_block(device):
    i2c = DraftNode(cls="i2c", roles=["SDA", "SCL"], instance="SERCOM7",
                    pins={"SDA": "PD08", "SCL": "PD09"}, compatible="atmel,sam0-i2c", label="sercom7")
    text = render_overlay(Draft(build="b", nodes=[uart_node(), i2c]), ctx_for(device))
    assert text.count("&pinctrl {") == 1
    assert text.index("sercom7_i2c_default:") < text.index("&sercom2 {")
    assert text.count("\n&sercom") == 2


def test_node_without_pins_needs_no_formatter():
    n = DraftNode(cls="uart", props={"current-speed": 9600}, instance="SERCOM3",
                  compatible="atmel,sam0-uart", label="sercom3")
    text = render_overlay(n, RenderContext())
    assert text == '&sercom3 {\n\tstatus = "okay";\n\tcompatible = "atmel,sam0-uart";\n\tcurrent-speed = <9600>;\n};\n'


def test_uart_without_input_carries_current_speed(device):
    """P8.19b item 2: the driver reads current-speed unconditionally, so a UART draft always has it."""
    for comp in ("atmel,sam0-uart", "microchip,sercom-g1-uart"):
        n = uart_node(props={}, compatible=comp)
        assert "current-speed = <115200>;" in render_overlay(n, ctx_for(device)), comp
    n = uart_node(props={"current-speed": 9600})
    text = render_overlay(n, ctx_for(device))
    assert "current-speed = <9600>;" in text and "115200" not in text      # the user's value wins
    i2c = DraftNode(cls="i2c", instance="SERCOM7", compatible="atmel,sam0-i2c", label="sercom7")
    assert "current-speed" not in render_overlay(i2c, ctx_for(device))


def test_errors(device):
    with pytest.raises(RenderError, match="not a signal of SERCOM2"):
        render_overlay(uart_node(pins={"TX": "PA00", "RX": "PB24"}), ctx_for(device))
    with pytest.raises(RenderError, match="both on PAD0|must be on"):
        render_overlay(uart_node(pins={"TX": "PB24", "RX": "PB25"}), ctx_for(device))
    with pytest.raises(RenderError, match="device model"):
        render_overlay(uart_node(), RenderContext(pinctrl_compatible="atmel,sam0-pinctrl"))
    with pytest.raises(RenderError, match="no formatter"):
        render_overlay(uart_node(), RenderContext(device=device, pinctrl_compatible="x,y"))
    with pytest.raises(RenderError, match="instance"):
        render_overlay(uart_node(instance="SERCOM9"), ctx_for(device))


# ---- acceptance: edtlib compare on the real build ------------------------------------------------
SERCOM2_PROPS = ("compatible", "status", "rxpo", "txpo", "current-speed", "pinctrl-0")


def _strip_block(lines: list[str], start_re: str) -> list[str]:
    """Remove a `{ ... };` node whose header line matches start_re."""
    out, depth, skipping = [], 0, False
    for ln in lines:
        if not skipping and re.search(start_re, ln):
            skipping, depth = True, 0
        if skipping:
            depth += ln.count("{") - ln.count("}")
            if depth <= 0 and "};" in ln:
                skipping = False
            continue
        out.append(ln)
    return out


def reset_sercom2(dts_text: str) -> str:
    """zephyr.dts with every board setting of sercom2 removed (back to the SoC defaults)."""
    lines = dts_text.splitlines()
    out, in_node = [], False
    drop = re.compile(r"^\s*(status|compatible|current-speed|rxpo|txpo|#address-cells|#size-cells|"
                      r"pinctrl-0|pinctrl-names)\s*=")
    for ln in lines:
        if re.match(r"\s*sercom2:\s*sercom@", ln):
            in_node = True
            out.append(ln)
            out.append('\t\t\tcompatible = "atmel,sam0-sercom";')
            out.append('\t\t\tstatus = "disabled";')
            continue
        if in_node:
            if drop.match(ln):
                continue
            if ln.strip() == "};":
                in_node = False
        elif re.match(r"\s*zephyr,(console|shell-uart)\s*=\s*&sercom2;", ln):
            continue
        out.append(ln)
    return "\n".join(_strip_block(out, r"sercom2_uart_default:\s*sercom2_uart_default\s*\{")) + "\n"


def edt_summary(edt, label: str) -> dict:
    node = edt.label2node[label]
    props = {}
    for name in SERCOM2_PROPS:
        if name == "compatible":
            props[name] = list(node.compats)
        elif name == "status":
            props[name] = node.status
        elif name not in node.props:
            props[name] = None
        elif name == "pinctrl-0":
            groups = {}
            for conf in node.props["pinctrl-0"].val:
                for g in conf.children.values():
                    groups[f"{conf.name}/{g.name}"] = [hex(v) for v in g.props["pinmux"].val]
            props[name] = groups
        else:
            props[name] = node.props[name].val
    props["chosen"] = sorted(k for k, n in edt.chosen_nodes.items() if n.path == node.path)
    props["aliases"] = sorted(k for k, n in edt._dt.alias2node.items() if n.path == node.path)
    return props


def compile_edt(model, dts_text: str, tmp: Path):
    """cpp + edtlib on a DTS text (pinctrl macros need the Zephyr headers)."""
    from dt_explorer.core.buildinfo import parse_cmake_cache, parse_zephyr_modules
    from dt_explorer.core.model_host import EdtlibHost
    edtlib = EdtlibHost.for_zephyr_base(model.zephyr_base).edtlib
    cache = parse_cmake_cache(model.build / "CMakeCache.txt")
    gcc = Path(cache.get("CMAKE_C_COMPILER", ""))
    if not gcc.is_file():
        pytest.skip(f"C preprocessor not found: {gcc}")
    zb = model.zephyr_base
    incs = [zb / "include", zb / "dts" / "common", zb / "dts", model.zephyr_base.parent / "modules" / "hal" / "atmel" / "include"]
    src = tmp / "overlaid.dts"
    src.write_text(dts_text, encoding="utf-8")
    pre = tmp / "overlaid.dts.pre"
    cmd = [str(gcc), "-E", "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__", "-o", str(pre)]
    for i in incs:
        cmd += ["-I", str(i)]
    subprocess.run(cmd + [str(src)], check=True, capture_output=True, text=True)
    mods = parse_zephyr_modules(model.build / "zephyr_modules.txt")
    roots = [model.app_dir] + list(mods.values()) + [zb]
    bdirs = [str(r / "dts" / "bindings") for r in roots if (r / "dts" / "bindings").is_dir()]
    return edtlib.EDT(str(pre), bdirs, werror=False)


def sercom2_case(rtos4_ws, rtos4_build, tmp_path):
    from dt_explorer.core.build import BuildModel
    build = rtos4_build("apps/lab1/build_same54_xpro")
    model = BuildModel(build)
    original = edt_summary(model.edt, "sercom2")

    fmt = AtmelSam0Formatter.for_workspace(rtos4_ws / "zephyr", "same54p20a") \
        if False else AtmelSam0Formatter.for_workspace(rtos4_ws, "same54p20a")
    recipe = plugins.get_recipe("atmel,sam0-uart")
    node = model.edt.label2node["sercom2"]
    # pins of the node: pinmux macro names of the board pinctrl group -> pad per pad role
    groups = parse_dtsi_groups((FIX / "sam0_pinctrl" / "same54_xpro-pinctrl.dtsi").read_text(encoding="utf-8"))
    pads = {}
    for macro in groups["sercom2_uart_default"]:
        p = fmt.parse(macro)
        pads[p.role] = p.pad
    props = {"compatible": list(node.compats)[0], "rxpo": node.props["rxpo"].val, "txpo": node.props["txpo"].val}
    roles = recipe.lift(props, pads)                     # {"TX": "PB25", "RX": "PB24"}
    chosen = sorted(k for k, n in model.edt.chosen_nodes.items() if n.path == node.path)
    draft_node = DraftNode(
        cls="uart", roles=["TX", "RX"], props={"current-speed": node.props["current-speed"].val},
        use=Use(chosen=chosen), instance="SERCOM2", pins=roles,
        vendor_props={"#address-cells": 1, "#size-cells": 0},
        compatible=props["compatible"], label="sercom2")
    device = MicrochipAtdfProvider().load(ATDF)
    ctx = RenderContext(device=device, pinctrl_compatible="atmel,sam0-pinctrl",
                        workspace=rtos4_ws, soc="same54p20a",
                        existing_compatibles={"sercom2": "atmel,sam0-sercom"})
    overlay = render_overlay(draft_node, ctx)

    zdts = (build / "zephyr" / "zephyr.dts").read_text(encoding="utf-8")
    base = reset_sercom2(zdts)
    text = "#include <dt-bindings/pinctrl/same54p-pinctrl.h>\n" + base + "\n" + overlay
    base_edt = compile_edt(model, "#include <dt-bindings/pinctrl/same54p-pinctrl.h>\n" + base, tmp_path)
    rendered_edt = compile_edt(model, text, tmp_path)
    return roles, overlay, original, edt_summary(base_edt, "sercom2"), edt_summary(rendered_edt, "sercom2")


def test_sercom2_overlay_equals_board_block(rtos4_ws, rtos4_build, tmp_path):
    roles, overlay, original, reset, rendered = sercom2_case(rtos4_ws, rtos4_build, tmp_path)
    print("\nlifted roles:", roles)
    print("--- rendered overlay ---\n" + overlay)
    print("original build edt :", original)
    print("reset (no overlay) :", reset)
    print("base + overlay edt :", rendered)
    assert reset != original, "reset DTS must differ from the original, or the compare proves nothing"
    assert rendered == original


def test_children_are_written_inside_the_node_override():
    from dt_explorer.core.draft import DraftChild
    from dt_explorer.core.translate import Ref
    phy = DraftChild("ethernet-phy", "0", ["ethernet-phy"], ["phy"], {"reg": [0]})
    n = DraftNode(cls="i2c", instance="SERCOM7", compatible="atmel,sam0-i2c", label="sercom7", children=[
        DraftChild("eeprom", "5e", ["atmel,24mac402"], ["eeprom", "ee2"],
                   {"reg": [0x5e], "status": "okay", "leds": [[Ref("led0"), 3, 1]], "ref": [Ref("a")],
                    "name": "x", "flag": True}, [phy])])
    text = render_overlay(n, RenderContext())
    assert text == (
        '&sercom7 {\n\tstatus = "okay";\n\tcompatible = "atmel,sam0-i2c";\n'
        '\t#address-cells = <1>;\n\t#size-cells = <0>;\n\n'
        '\teeprom: ee2: eeprom@5e {\n\t\tcompatible = "atmel,24mac402";\n\t\treg = <0x5e>;\n'
        '\t\tstatus = "okay";\n\t\tleds = <&led0 3 1>;\n\t\tref = <&a>;\n\t\tname = "x";\n\t\tflag;\n\n'
        '\t\tphy: ethernet-phy@0 {\n\t\t\tcompatible = "ethernet-phy";\n\t\t\treg = <0x0>;\n\t\t};\n\t};\n};\n')


def _board_i2c():
    from dt_explorer.core.draft import DraftChild
    return DraftNode(
        cls="i2c", instance="SERCOM7", compatible="atmel,sam0-i2c", label="sercom7", roles=["SDA", "SCL"],
        pins={"SDA": "PD08", "SCL": "PD09"}, props={"clock-frequency": 400000},
        vendor_props={"#address-cells": 1, "#size-cells": 0},
        children=[DraftChild("eeprom", "56", ["atmel,at24"], ["ee"], {"reg": [0x56], "size": 256})])


def test_edited_enabled_node_renders_only_the_difference(device):
    """P8.19b item 5: board children, unchanged board properties and an unchanged pin choice are not re-emitted."""
    import copy
    from dt_explorer.core.draft import DraftChild
    board = _board_i2c()
    ctx = ctx_for(device, board_nodes={"sercom7": board})
    new = copy.deepcopy(board)
    new.children.append(DraftChild("bme280", "76", ["bosch,bme280"], ["bme280_76"], {"reg": [0x76]}))
    assert render_overlay(new, ctx) == (
        '&sercom7 {\n\tbme280_76: bme280@76 {\n\t\tcompatible = "bosch,bme280";\n\t\treg = <0x76>;\n\t};\n};\n')
    new.props["clock-frequency"] = 100000                      # a changed property is written, the others stay out
    text = render_overlay(new, ctx)
    assert "clock-frequency = <100000>;" in text and "#address-cells" not in text and "ee:" not in text
    assert "pinctrl" not in text and "status" not in text
    # without the board's version the whole node is written, as before
    full = render_overlay(new, ctx_for(device))
    assert "ee: eeprom@56" in full and 'status = "okay"' in full and "pinctrl-0" in full
    # a node that is not changed at all gives no override
    assert render_overlay(copy.deepcopy(board), ctx) == ""


def test_edited_enabled_node_with_changed_pins_rederives(device):
    import copy
    board = _board_i2c()
    ctx = ctx_for(device, board_nodes={"sercom7": board})
    new = copy.deepcopy(board)
    new.instance, new.pins = "SERCOM2", {"SDA": "PB25", "SCL": "PB24"}
    text = render_overlay(new, ctx)
    assert "pinctrl-0" in text and "ee:" not in text and "clock-frequency" not in text


def test_children_keep_the_cells_the_node_already_has():
    from dt_explorer.core.draft import DraftChild
    n = DraftNode(cls="i2c", instance="SERCOM7", compatible="atmel,sam0-i2c", label="sercom7",
                  vendor_props={"#address-cells": 1, "#size-cells": 0},
                  children=[DraftChild("t", "4f", [], [], {"reg": [0x4f]})])
    text = render_overlay(n, RenderContext())
    assert text.count("#address-cells") == 1 and text.count("#size-cells") == 1
    nameless = DraftNode(cls="i2c", instance="SERCOM7", compatible="x", label="s", children=[DraftChild("n")])
    assert "#address-cells" not in render_overlay(nameless, RenderContext())     # no unit address, no cells
