"""Port report (core/port_report.py, P5.4): synthetic inputs only, no environment variables.

A fake source and a fake target build (plain namespaces), fake vendor-neutral plugins (recipe, formatter,
translation from a small dict) and a fake device model drive ``port_build``. The cases cover what the
report promises: a node the target board already configures identically emits nothing; a disabled target
node is enabled with the translated properties and the new pins; a node without translation or without
a counterpart is reported; node references that the target does not resolve get a suggestion and an alias
entry; a pad that is not on the target package is reported; Kconfig differences.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dt_explorer.core import plugins
from dt_explorer.core import port_report as pr
from dt_explorer.core.build import NodeOrigin
from dt_explorer.core.buildinfo import BuildInfo
from dt_explorer.core.device import DeviceModel, Instance, Package, Signal
from dt_explorer.core.draft import Draft, DraftNode, Use
from dt_explorer.core.kconfig_suggest import KconfigSuggestion
from dt_explorer.core.lift import CoveredNode, LiftReport, NotRepresentable, UncoveredNode
from dt_explorer.core.translate import Ref, translations_from_dict


# ---- helpers without any model ---------------------------------------------------------------
def test_alias_name_is_dtc_safe():
    assert pr._alias_name("My_Uart0") == "my-uart0"
    assert pr._alias_name("__") == "node"


def test_same_value_ignores_representation():
    assert pr._same_value(115200, [115200])
    assert pr._same_value([1, 2], (1, 2))
    assert pr._same_value(b"\x01\x02", [1, 2])
    assert not pr._same_value(1, 2)
    assert pr._same_value("none", "none")


def test_strip_status_and_rename():
    text = '&u1 {\n\tstatus = "okay";\n\trx = <1>;\n\tpinctrl-0 = <&u1_uart_default>;\n};\n'
    assert pr._strip_status(text) == "&u1 {\n\trx = <1>;\n\tpinctrl-0 = <&u1_uart_default>;\n};\n"
    assert "u1_uart_edit" in pr._rename(text, "u1_uart_default", "u1_uart_edit")
    assert pr._rename("a_default b", "a_default", "x") == "x b"
    assert pr._rename("aa_default", "a_default", "x") == "aa_default"     # whole words only


def test_report_sections_are_always_present():
    text = "\n".join(pr.PortReport(source="s", target="t").lines())
    for title in ("1. Nodes", "2. Kconfig differences", "3. Node references", "4. Pins not available",
                  "5. Nodes without translation"):
        assert title in text
    assert text.count("none") >= 3


# ---- fake plugins and builds -------------------------------------------------------------------
class FakeRecipe:
    api_version = plugins.API_VERSION
    name = "acme_recipe"
    compatibles = ["acme,uart", "acme,i2c"]

    def pin_roles(self, instance, compatible=None): return ["TX", "RX"]
    def kconfig(self, compatible=None): return {}
    def lift(self, props, pins): return {}
    def derived_props(self, assignment): return {"rx": int(assignment["RX"][-1])}


class FakeFormatter:
    api_version = plugins.API_VERSION
    name = "beta_fmt"
    compatibles = ["beta,pinctrl"]

    def to_overlay(self, signal): return f"{signal.pad}_{signal.attrs.get('instance')}_{signal.role}"
    def from_overlay(self, value): return value

    def render_group(self, signals):
        return "group1 {\n\tpinmux = " + ", ".join(f"<{self.to_overlay(s)}>" for s in signals) + ";\n};"


DOC = {
    "schema_version": 1, "name": "acme_to_beta",
    "translations": [
        {"source": "acme,uart", "target": "beta,uart", "keep": ["current-speed"],
         "properties": {"rx": {"rename": "rxsel"}, "acme,legacy": {"drop": "no equivalent in beta"}},
         "target_only": {"beta,standby": "target default 1"}},
        {"source": "acme,i2c", "no_target": "beta has no i2c driver yet"},
    ],
}


def _dev() -> DeviceModel:
    sigs = lambda a, b: [Signal("PAD0", a, "A"), Signal("PAD1", b, "A")]     # noqa: E731
    return DeviceModel(
        "BETA1", [Package("QFP8", "QFP", 8, {"1": "P1", "2": "P2", "3": "P3", "4": "P4", "5": "VDD"})],
        {"U0": Instance("U0", "UART", 0x1000, sigs("P1", "P2")),
         "U1": Instance("U1", "UART", 0x2000, sigs("P3", "P4") + [Signal("PAD2", "P8", "A")])}, [])


def _node(path, label, compats, status="okay", addr=None, props=None):
    return NS(path=path, labels=[label], compats=compats, status=status, children={},
              regs=[NS(addr=addr)] if addr is not None else [],
              props={k: NS(val=v, type="int") for k, v in (props or {}).items()})


def _build(tmp: Path, name: str, board: str, config: str, nodes, aliases=None, chosen=None, refs=()):
    b = tmp / name
    (b / "zephyr").mkdir(parents=True)
    (b / "zephyr" / ".config").write_text(config)
    app = tmp / "app"
    (app / "src").mkdir(parents=True, exist_ok=True)
    (app / "prj.conf").write_text("CONFIG_SERIAL=y\nCONFIG_ONLY_ACME=y # comment\n")
    chosen = dict(chosen or {})
    return NS(build=b, app_dir=app, board=board, yaml_index={}, code_refs=list(refs),
              edt=NS(nodes=nodes, label2node={lb: n for n in nodes for lb in n.labels},
                     chosen_nodes=chosen, _dt=NS(alias2node=dict(aliases or {}))),
              by_path={n.path: n for n in nodes}, chosen={k.replace(",", "_"): v for k, v in chosen.items()})


@pytest.fixture
def world(tmp_path, monkeypatch):
    plugins.register_recipe(FakeRecipe)
    plugins.register_formatter(FakeFormatter)
    for t in translations_from_dict(DOC):
        plugins.register_translation(t)

    s_u0 = _node("/s/u0", "u0", ["acme,uart"], addr=0x1000)
    s_u1 = _node("/s/u1", "u1", ["acme,uart"], addr=0x2000)
    s_u2 = _node("/s/u2", "u2", ["acme,uart"], addr=0x3000)
    s_i0 = _node("/s/i0", "i0", ["acme,i2c"], addr=0x4000)
    s_led = _node("/s/led", "ledx", ["acme,led"])
    s_misc = _node("/s/misc", "misc", ["acme,misc"])
    t_u0 = _node("/t/u0", "u0", ["beta,uart"], addr=0x1000, props={"current-speed": 9600, "rxsel": 1})
    t_u1 = _node("/t/u1", "u1", ["beta,generic"], status="disabled", addr=0x2000)
    t_i0 = _node("/t/i0", "i0", ["beta,generic"], status="disabled", addr=0x4000)
    t_pc = _node("/t/pc", "pinctrl", ["beta,pinctrl"])

    ref = lambda line, macro, node: {"file": "src/main.c", "line": line, "macro": macro, "node": node, "code": ""}  # noqa: E731
    refs = [ref(3, "DT_NODELABEL(u1)", s_u1),        # resolves: the port enables the node
            ref(4, "DT_NODELABEL(ledx)", s_led),     # no counterpart in the target
            ref(5, "DT_NODELABEL(u2)", s_u2),        # label gone, node not carried over
            ref(6, "DT_ALIAS(my_uart)", s_u1),       # alias missing in the target
            ref(7, "DT_NODELABEL(u0)", s_u0)]        # resolves
    src = _build(tmp_path, "build_src", "acme_board", "CONFIG_SERIAL=y\nCONFIG_UART_ACME=y\nCONFIG_ONLY_ACME=y\n",
                 [s_u0, s_u1, s_u2, s_i0, s_led, s_misc], refs=refs,
                 aliases={"my-uart": s_u1}, chosen={"zephyr,console": s_u1})
    tgt = _build(tmp_path, "build_tgt", "beta_board", "CONFIG_SERIAL=y\nCONFIG_UART_BETA=y\n",
                 [t_u0, t_u1, t_i0, t_pc])

    draft = Draft(build="build_src", nodes=[
        DraftNode("uart", ["TX", "RX"], {"current-speed": 9600}, Use(), "U0", {"TX": "P1", "RX": "P2"},
                  {}, "acme,uart", "u0"),
        DraftNode("uart", ["TX", "RX"], {"current-speed": 19200}, Use(["zephyr,console"], ["my-uart"]), "U1",
                  {"TX": "P3", "RX": "P4"}, {"acme,legacy": 1}, "acme,uart", "u1"),
        DraftNode("uart", ["TX", "RX"], {}, Use(), "U2", {"TX": "P5", "RX": "P6"}, {}, "acme,uart", "u2"),
        DraftNode("i2c", ["SDA"], {}, Use(), "U9", {"SDA": "P1"}, {}, "acme,i2c", "i0"),
    ])
    lift = LiftReport(build="build_src", covered=[
        CoveredNode(f"/s/{x}", x, c, "uart" if c == "acme,uart" else "i2c", "acme_recipe", {}, [])
        for x, c in (("u0", "acme,uart"), ("u1", "acme,uart"), ("u2", "acme,uart"), ("i0", "acme,i2c"))],
        uncovered=[UncoveredNode("/s/led", ["ledx"], "acme,led", "overlay", "no recipe"),
                   UncoveredNode("/s/misc", ["misc"], "acme,misc", "soc", "no recipe")],
        not_representable=[NotRepresentable("/s/i0", "child nodes", "stay in the source build")])

    dev = _dev()
    tgt_uses = {"P1": [NS(node="/t/u0", source="pinctrl")], "P2": [NS(node="/t/u0", source="pinctrl")]}
    src_uses = {"P1": [NS(node="/s/u0", source="pinctrl")], "P2": [NS(node="/s/u0", source="pinctrl")],
                "P8": [NS(node="/s/led", source="gpio")], "P9": [NS(node="/s/led", source="gpio")]}
    monkeypatch.setattr(pr, "lift_build", lambda model, info: (draft, lift))
    monkeypatch.setattr(pr, "read_build_info", lambda b: BuildInfo(
        build_dir=Path(b), board_target=src.board if Path(b) == src.build else tgt.board))
    monkeypatch.setattr(pr.pins_mod, "used_pins", lambda model, info=None: NS(
        device=dev, uses=src_uses if model is src else tgt_uses))

    def fake_suggest(compat, build):
        sym = {"acme,uart": {"CONFIG_SERIAL": "y", "CONFIG_UART_ACME": "y"},
               "beta,uart": {"CONFIG_SERIAL": "y", "CONFIG_UART_BETA": "y"}}.get(compat, {})
        cfg = (Path(build) / "zephyr" / ".config").read_text()
        return KconfigSuggestion(compat, dict(sym), "recipe",
                                 already_enabled=[k for k, v in sym.items() if f"{k}={v}" in cfg])
    monkeypatch.setattr(pr, "suggest_kconfig", fake_suggest)
    return NS(src=src, tgt=tgt, draft=draft, tgt_uses=tgt_uses, lift=lift, tmp=tmp_path)


def test_port_build_synthetic(world):
    res = pr.port_build(world.src, world.tgt, check=False)
    rep = res.report
    by = {n.label: n for n in rep.nodes}

    # u0: the target board already has the same node -> nothing emitted
    assert by["u0"].status == "identical"
    assert "&u0 {" not in res.overlay
    assert "current-speed" in by["u0"].same_in_target and "pinctrl-0 (same pads)" in by["u0"].same_in_target

    # u1: disabled in the target -> enabled with the translated properties, new pins, chosen and alias
    assert by["u1"].status == "emitted" and by["u1"].target_compatible == "beta,uart"
    assert "&u1 {" in res.overlay
    assert 'compatible = "beta,uart";' in res.overlay and 'status = "okay";' in res.overlay
    assert "current-speed = <19200>;" in res.overlay and "rxsel = <1>;" in res.overlay
    assert "u1_uart_default" in res.overlay and "<P3_U1_PAD0>, <P4_U1_PAD1>" in res.overlay
    assert "zephyr,console = &u1;" in res.overlay and "my-uart = &u1;" in res.overlay
    assert any("acme,legacy" in x and "no equivalent in beta" in x for x in by["u1"].lines)
    assert any("beta,standby" in x for x in by["u1"].lines)                  # target-only property listed

    # u2 has no counterpart; i0 has no target binding
    assert by["u2"].status == "no_node" and by["i0"].status == "no_target"
    assert any("no i2c driver" in x for x in by["i0"].lines)

    # node references: u0/u1 resolve (u1 only because the port enables it); ledx, u2 and the alias do not
    macros = {r.macro: r for r in rep.label_refs}
    assert "DT_NODELABEL(u0)" not in macros and "DT_NODELABEL(u1)" not in macros
    assert macros["DT_NODELABEL(ledx)"].problem == "missing"
    assert "not carried over" in macros["DT_NODELABEL(ledx)"].suggestion
    assert macros["DT_NODELABEL(u2)"].problem == "missing"
    assert "DT_ALIAS(my_uart)" not in macros                               # the port's own alias for &u1 resolves it
    assert res.overlay.count("my-uart = &u1;") == 1                        # the node's own alias, not repeated

    # pins: P8 is a pad of the device but not on the package, P9 is no pad of the device at all
    assert [(p.pad, p.ported) for p in rep.pin_issues] == [("P8", False), ("P9", False)]
    assert "not on package QFP8" in rep.pin_issues[0].problem
    assert "not a pad of the target device BETA1" in rep.pin_issues[1].problem

    # nodes without translation and children
    assert [u.path for u in rep.unported] == ["/s/led"] and rep.unported[0].in_target is False
    assert rep.not_representable == ["/s/i0  child nodes: stay in the source build"]

    # Kconfig: driver symbol of the source has no counterpart; conf lists the target symbols
    rows = {r.source_compatible: r for r in rep.kconfig}
    assert rows["acme,uart"].target_compatible == "beta,uart"
    assert any("CONFIG_UART_ACME" in n for n in rows["acme,uart"].notes)
    assert rows["acme,i2c"].target_compatible is None
    assert "# CONFIG_UART_BETA=y  (already set in the target build)" in res.conf
    assert [d.symbol for d in rep.conf_diffs] == ["CONFIG_ONLY_ACME"]


def test_pin_override_on_an_enabled_node(world):
    """The target node is enabled but on other pads: override in a group of its own name."""
    world.tgt_uses.clear()
    world.tgt_uses.update({"P3": [NS(node="/t/u0", source="pinctrl")], "P4": [NS(node="/t/u0", source="pinctrl")]})
    res = pr.port_build(world.src, world.tgt, check=False)
    assert "u0_uart_edit" in res.overlay and "u0_uart_default" not in res.overlay.split("&u1")[0]
    u0_block = res.overlay.split("&u0 {")[1].split("};")[0]
    assert 'status = "okay"' not in u0_block                              # already enabled in the target
    assert any("other pads" in n for n in {x.label: x for x in res.report.nodes}["u0"].notes)


def test_pad_that_the_instance_lacks_is_reported_not_raised(world):
    world.tgt_uses.clear()                                                # no conflict with the target's users
    world.draft.nodes[1].pins = {"TX": "P1", "RX": "P2"}                  # U1 has P3/P4 only
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "error" and "pad P1 is not a signal" in " ".join(n.notes)
    assert any(p.ported and "&u1" in p.nodes for p in res.report.pin_issues)


# ---- app-defined nodes from the app overlay (P5.4b) -------------------------------------------------
OVERLAY = """#include <dt-bindings/acme/gone.h>
/ {
    /* a custom node that only references labels of the target */
    mine: led@1000 {
        compatible = "acme,led";
        owner = <&u0>;
        status = "okay";
    };
    /* references a label that the target lacks */
    lost: led@2000 {
        compatible = "acme,led";
        owner = <&nolabel>;
    };
    /* references another app node that is not carried */
    chain: led@3000 {
        compatible = "acme,led";
        other = <&lost>;
    };
    aliases {
        my-owner = &mine;      // goes over
        console0 = &u1;       // u1 is ported by its translation
    };
};

&u1 { status = "okay"; current-speed = <1>; };      /* covered node: left to the translation */

&i0 {
    status = "okay";
    sens: sensor@4f { reg = <0x4f>; };              /* child of a bus without a target driver */
};

&u0 {
    probe: probe@1 { reg = <1>; };                  /* child of an enabled, identical node */
};
"""


def test_mask_and_parse_items():
    text = '/* c */ / {\n\ta: n@1 { p = "x;{"; }; // t\n\tq = <1>;\n};\n'
    _a, b = pr._mask(text)
    assert "c" not in b.split("/")[1] and '"x;{"' not in b and len(b) == len(text)
    top = pr._parse_items(text, b, 0, len(text))
    assert [(i.kind, i.name) for i in top] == [("node", "/")]
    body = top[0].items
    assert [(i.kind, i.name, i.labels, i.line) for i in body] == [("node", "n@1", ["a"], 2), ("prop", "q", [], 3)]
    assert body[0].text == 'a: n@1 { p = "x;{"; };' and pr._defined_labels(body[0]) == ["a"]
    s = "&x { p = <&y 1>, &{/a/b}; };"
    assert pr._refs_of(pr._parse_items(s, s, 0, len(s))[0].items[0]) == [("path", "/a/b"), ("label", "y")]


def test_body_text_reindents_by_one_tab():
    assert pr._body_text("a: n {\n        p = <1>;\n    };") == "\ta: n {\n\t    p = <1>;\n\t};"
    assert pr._body_text("q = <1>;") == "\tq = <1>;"


def _overlay_world(world, text=OVERLAY):
    tmp = world.tmp
    f = tmp / "app.overlay"
    f.write_text(text)
    src, tgt = world.src, world.tgt
    plain = lambda p: NS(path=p, labels=[], compats=[], status="okay", children={}, regs=[], props={})   # noqa: E731
    src.by_path["/aliases"], tgt.by_path["/aliases"] = plain("/aliases"), plain("/aliases")
    src.origins = {"/aliases": NodeOrigin("zephyr\\dts\\skeleton.dtsi", 1),
                   "/led@1000": NodeOrigin("apps\\x\\boards\\b.overlay", 4)}
    src.resolve = lambda p: f
    src.rel = lambda p: "boards\\b.overlay"
    src.edt.label2node.update({"i0": src.by_path["/s/i0"], "u0": src.by_path["/s/u0"], "u1": src.by_path["/s/u1"]})
    tgt.zephyr_base = tmp / "zb"
    tgt.app_dir = tmp / "app"
    world.lift.uncovered[:] = [UncoveredNode("/led@1000", ["mine"], "acme,led", "overlay", "no recipe")]
    return f


def test_app_defined_nodes_are_carried_or_reported(world):
    _overlay_world(world)
    res = pr.port_build(world.src, world.tgt, check=False)
    rep, ov = res.report, res.overlay

    # carried: the node that references only target labels, verbatim, and the alias of an uncovered node
    assert "/* carried from boards\\b.overlay:4 */\n/ {\n\tmine: led@1000 {" in ov
    assert '\t    compatible = "acme,led";' in ov and "owner = <&u0>;" in ov
    assert "&{/aliases} {\n\tmy-owner = &mine;\n};" in ov
    # a bus child of an enabled ported node goes over wrapped in the target label
    assert "&u0 {\n\tprobe: probe@1 { reg = <1>; };\n};" in ov
    assert [c.desc for c in rep.carried] == ["mine: led@1000", "/aliases my-owner", "probe: probe@1"]

    # not carried: dangling reference, reference to a node that is not carried, bus without target driver
    nc = {n.desc: n.reason for n in rep.not_carried}
    assert nc["lost: led@2000"] == "references nolabel missing in target"
    assert nc["chain: led@3000"].startswith("references lost missing in target (lost is not carried: references nolabel")
    assert nc["sens: sensor@4f"].startswith("bus &i0 is not ported and enabled on the target [no_target]")
    assert "does not resolve in the target workspace" in nc["#include <dt-bindings/acme/gone.h>"]
    # the covered node's own properties and the alias of a ported node are left to the translation
    assert "console0" not in ov and ov.count("current-speed") == 1
    assert not any("console0" in k or "current-speed" in k for k in nc)

    # not listed as 'carry by hand' any more; references in src/ to a carried label resolve
    assert rep.unported == []
    text = "\n".join(rep.lines())
    assert "5b. App-defined nodes" in text and "carried: mine: led@1000" in text
    assert "not carried: lost: led@2000" in text


def test_include_that_resolves_is_carried(world):
    _overlay_world(world)
    inc = world.tmp / "zb" / "include" / "dt-bindings" / "acme"
    inc.mkdir(parents=True)
    (inc / "gone.h").write_text("")
    res = pr.port_build(world.src, world.tgt, check=False)
    assert res.overlay.index("#include <dt-bindings/acme/gone.h>") < res.overlay.index("/* carried from")
    assert not any(n.kind == "include" for n in res.report.not_carried)


def test_existing_label_or_node_in_target_is_not_overwritten(world):
    _overlay_world(world, "/ {\n\tu0: led@1000 { x = <1>; };\n\tled@2000 { y = <1>; };\n};\n")
    world.tgt.by_path["/led@2000"] = NS(path="/led@2000", labels=[], compats=[], status="okay", regs=[], props={})
    res = pr.port_build(world.src, world.tgt, check=False)
    why = sorted(n.reason for n in res.report.not_carried)
    assert why == ["a node /led@2000 already exists in the target devicetree",
                   "label u0 already exists in the target devicetree"]
    assert not res.report.carried


# ---- conflicts with the target board (P6.3) -----------------------------------------------------------
def _by_label(world):
    return {x.label: x for x in pr.port_build(world.src, world.tgt, check=False).report.nodes}


def _enable_u1_as_other(world):
    t_u1 = world.tgt.by_path["/t/u1"]
    t_u1.status, t_u1.compats = "okay", ["beta,other"]
    return t_u1


def test_conflict_target_node_enabled_with_another_compatible(world):
    """(a) the target enables u1 as another class: not emitted, the reason names the target's users."""
    t_u1 = _enable_u1_as_other(world)
    world.tgt.edt.chosen_nodes["zephyr,console"] = t_u1
    world.tgt.edt._dt.alias2node["tty"] = t_u1
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "conflict" and n.emitted == []
    text = "\n".join(n.lines)
    assert "enables &u1 as beta,other (chosen zephyr,console, alias tty)" in text
    assert "not moved: zephyr,console, my-uart" in text and "suggestion:" in text
    assert "&u1 {" not in res.overlay and "my-uart = &u1;" not in res.overlay
    assert "zephyr,console" not in res.overlay
    assert "[conflict]" in "\n".join(res.report.lines())
    ref = {r.macro: r for r in res.report.label_refs}["DT_NODELABEL(u1)"]       # the label is another node now
    assert ref.problem == "conflict" and ref.alias_entry == ""


def test_conflict_pad_used_by_another_enabled_target_node(world):
    """(b) P3 is used by a different enabled node of the target build."""
    world.tgt_uses["P3"] = [NS(node="/t/i0", source="pinctrl")]
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "conflict"
    assert any("TX pad P3 is used by &i0" in x for x in n.lines)
    assert "&u1 {" not in res.overlay and "P3_U1" not in res.overlay


def test_conflict_pad_not_on_target_package(world):
    """(c) P8 is a pad of the device but not on the package."""
    world.draft.nodes[1].pins = {"TX": "P3", "RX": "P8"}
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "conflict" and not any("cannot render" in x for x in n.notes)
    assert any("RX pad P8 is not on package QFP8 of the target device BETA1" in x for x in n.lines)
    assert "&u1 {" not in res.overlay


def test_conflict_pad_unknown_to_the_device(world):
    world.draft.nodes[1].pins = {"TX": "P3", "RX": "P9"}
    n = _by_label(world)["u1"]
    assert n.status == "conflict" and any("not a pad of the target device BETA1" in x for x in n.lines)


def test_no_node_is_dropped_silently(world):
    """Every covered node of the source has a section-1 line; a node that is not emitted says why."""
    world.draft.nodes[1].pins = {"TX": "P3", "RX": "P8"}
    res = pr.port_build(world.src, world.tgt, check=False)
    rep = res.report
    assert [n.label for n in rep.nodes] == [d.label for d in world.draft.nodes]
    sec1 = dict(rep.sections())["1. Nodes: translated and untranslatable properties"]
    for n in rep.nodes:
        assert any(x.startswith(f"&{n.label} ") for x in sec1)
        if n.status not in ("emitted", "identical", "remapped"):
            assert n.lines or n.notes, n.label
        if f"&{n.label} {{" in res.overlay:
            assert n.status in ("emitted", "identical")


class _RecipeWithAssignments(FakeRecipe):
    def valid_assignments(self, cls):
        yield {"TX": "PAD0", "RX": "PAD1"}


def _boards(monkeypatch, world, src_pins, tgt_pins):
    """Fake board descriptions with one connector X1 (pin 1 = TX, pin 2 = RX); a None pad is an untraced pin."""
    from dt_explorer.core import board_desc as bd
    mk = lambda pads: {str(i + 1): bd.ConnPin(str(i + 1), r, p) for i, (r, p) in enumerate(zip(("UART_TX", "UART_RX"), pads))}  # noqa: E731
    boards = {"acme_board": bd.BoardDesc("acme_board", "Acme", [bd.Connector("X1", "hdr", mk(src_pins))]),
              "beta_board": bd.BoardDesc("beta_board", "Beta", [bd.Connector("X1", "hdr", mk(tgt_pins))])}
    monkeypatch.setattr(pr.board_desc, "load_board", lambda b, dirs=None: boards.get(b))
    monkeypatch.setattr(pr, "read_build_info", lambda b: BuildInfo(
        build_dir=Path(b), board=world.src.board if Path(b) == world.src.build else world.tgt.board))
    plugins.register_recipe(_RecipeWithAssignments)
    world.draft.nodes[1].pins = {"TX": src_pins[0], "RX": src_pins[1]}


def test_conflict_suggestion_names_connector_pads_and_instances(world, monkeypatch):
    """One role has no pad at the target connector pin: the instance that could mux the others is named, no remap."""
    _boards(monkeypatch, world, ("P5", "P6"), ("P3", None))        # the source pads are unknown to the target device
    n = _by_label(world)["u1"]
    assert n.status == "conflict"
    sug = next(x for x in n.lines if x.startswith("suggestion:"))
    assert "uart on X1 of the target: TX P3" in sug and "no pad there for RX (P6, X1 pin 2)" in sug
    assert "instance(s) that can mux them: U1" in sug and "no remap: a role has no pad" in sug


def test_conflict_suggestion_names_busy_instance(world, monkeypatch):
    """The pads exist on the target, but the instance behind them is enabled as another node."""
    _boards(monkeypatch, world, ("P5", "P6"), ("P3", "P4"))
    _enable_u1_as_other(world)
    n = _by_label(world)["u1"]
    sug = next(x for x in n.lines if x.startswith("suggestion:"))
    assert "uart on X1 of the target: TX P3, RX P4" in sug
    assert "no free instance" in sug and "U1 (enabled as &u1)" in sug and "no remap" in sug
    assert n.status == "conflict"


def test_conflict_suggestion_without_board_data_is_still_given(world):
    _enable_u1_as_other(world)
    n = _by_label(world)["u1"]
    assert any(x.startswith("suggestion: no connector data") for x in n.lines)


def test_west_topdir_is_taken_from_the_cmake_cache(tmp_path):
    """A build whose app is in another west workspace than its Zephyr resolves board files via WEST_TOPDIR."""
    top = tmp_path / "top"
    top.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    (b / "CMakeCache.txt").write_text(f"WEST_TOPDIR:UNINITIALIZED={top.as_posix()}\n")
    m = NS(build=b, west_top=tmp_path / "elsewhere")
    pr._adopt_west_topdir(m)
    assert m.west_top == top
    m2 = NS(build=tmp_path / "nocache", west_top=tmp_path / "keep")
    pr._adopt_west_topdir(m2)
    assert m2.west_top == tmp_path / "keep"


def test_conflict_suggestion_says_which_target_pads_are_taken(world, monkeypatch):
    _boards(monkeypatch, world, ("P5", "P6"), ("P3", "P4"))
    world.tgt_uses["P3"] = [NS(node="/t/i0", source="pinctrl")]
    n = _by_label(world)["u1"]
    assert n.status == "conflict"
    sug = next(x for x in n.lines if x.startswith("suggestion:"))
    assert "already used in the target build: P3 by &i0" in sug and "no free instance" in sug


def test_alias_reference_is_judged_after_the_overlay(world):
    s_u0, s_led = world.src.by_path["/s/u0"], world.src.by_path["/s/led"]
    world.src.code_refs += [
        {"file": "src/main.c", "line": 20, "macro": "DT_ALIAS(my_u0)", "node": s_u0, "code": ""},
        {"file": "src/main.c", "line": 21, "macro": "DT_ALIAS(my_led)", "node": s_led, "code": ""},
        {"file": "src/main.c", "line": 22, "macro": "DT_CHOSEN(zephyr_console)", "node": world.src.by_path["/s/u1"], "code": ""}]
    res = pr.port_build(world.src, world.tgt, check=False)
    macros = {r.macro: r for r in res.report.label_refs}
    # &u0 exists and is okay in the target: the overlay adds the alias, so the reference resolves after it
    assert "DT_ALIAS(my_u0)" not in macros and "my-u0 = &u0;" in res.overlay
    # the node of the second alias is not ported: still missing
    assert macros["DT_ALIAS(my_led)"].problem == "missing"
    # the chosen entry is added by the overlay for &u1
    assert "zephyr,console = &u1;" in res.overlay and "DT_CHOSEN(zephyr_console)" not in macros


def test_zephyr_version_text(tmp_path):
    assert pr._zephyr_version(tmp_path) is None
    (tmp_path / "VERSION").write_text("VERSION_MAJOR = 4\nVERSION_MINOR = 5\nPATCHLEVEL = 0\nVERSION_TWEAK = 0\nEXTRAVERSION = rc1\n")
    assert pr._zephyr_version(tmp_path) == "4.5.0-rc1"
    (tmp_path / "VERSION").write_text("VERSION_MAJOR = 4\nVERSION_MINOR = 3\nPATCHLEVEL = 0\nEXTRAVERSION =\n")
    assert pr._zephyr_version(tmp_path) == "4.3.0"


def test_note_when_zephyr_versions_differ(world, monkeypatch, tmp_path):
    for name, minor in (("zs", 3), ("zt", 5), ("zt2", 3)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "VERSION").write_text(f"VERSION_MAJOR = 4\nVERSION_MINOR = {minor}\nPATCHLEVEL = 0\nEXTRAVERSION =\n")

    def info(target_dir):
        return lambda b: BuildInfo(build_dir=Path(b), board_target="x",
                                   zephyr_base=tmp_path / ("zs" if Path(b) == world.src.build else target_dir))
    monkeypatch.setattr(pr, "read_build_info", info("zt"))
    notes = pr.port_build(world.src, world.tgt, check=False).report.notes
    assert any("source Zephyr 4.3.0, target Zephyr 4.5.0" in n and "app APIs may have changed" in n for n in notes)
    monkeypatch.setattr(pr, "read_build_info", info("zt2"))
    assert not any("app APIs" in n for n in pr.port_build(world.src, world.tgt, check=False).report.notes)


# ---- P8.17: remap to another instance at the same connector pins --------------------------------------
class _RecipeBothWays(FakeRecipe):
    def valid_assignments(self, cls):
        yield {"TX": "PAD0", "RX": "PAD1"}
        yield {"TX": "PAD1", "RX": "PAD0"}


def _remap_world(world, monkeypatch, tgt_pins=("P6", "P7"), recipe=_RecipeWithAssignments):
    """u1 of the source sits on connector X1 (P5, P6) and conflicts on the target (its instance is enabled as
    another node); the target has a free instance U5 (node &u5) behind the pads at the same connector pins."""
    _boards(monkeypatch, world, ("P5", "P6"), tgt_pins)
    plugins.register_recipe(recipe)
    dev = pr.pins_mod.used_pins(world.src).device
    dev.instances["U5"] = Instance("U5", "UART", 0x5000, [Signal("PAD0", "P6", "A"), Signal("PAD1", "P7", "A")])
    dev.packages[0].pins.update({"6": "P6", "7": "P7"})
    t_u5 = _node("/t/u5", "u5", ["beta,generic"], status="disabled", addr=0x5000)
    world.tgt.edt.nodes.append(t_u5)
    world.tgt.edt.label2node["u5"] = t_u5
    world.tgt.by_path["/t/u5"] = t_u5
    _enable_u1_as_other(world)
    return t_u5


def test_conflict_is_remapped_to_a_free_instance(world, monkeypatch):
    _remap_world(world, monkeypatch)
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "remapped" and n.target_path == "/t/u5" and n.target_compatible == "beta,uart"
    text = "\n".join(n.lines)
    assert "remapped: uart U1 -> U5 (&u1 -> &u5)" in text
    assert "remap TX: P5 (X1 pin 1) -> P6" in text and "remap RX: P6 (X1 pin 2) -> P7" in text
    assert "remap because: " in text and "enables &u1 as beta,other" in text
    # the overlay configures the target instance, not the conflicting one; derived property recomputed on the target
    assert "&u5 {" in res.overlay and "&u1 {" not in res.overlay
    assert "<P6_U5_PAD0>, <P7_U5_PAD1>" in res.overlay and "rxsel = <1>;" in res.overlay
    assert "current-speed = <19200>;" in res.overlay and 'status = "okay";' in res.overlay
    # chosen entry and alias follow the node; the report names the consequence for code
    assert "zephyr,console = &u5;" in res.overlay and "my-uart = &u5;" in res.overlay
    assert any("DT_NODELABEL(u1) must use DT_NODELABEL(u5)" in x for x in n.notes)
    assert "[remapped]" in "\n".join(res.report.lines())
    ref = {r.macro: r for r in res.report.label_refs}["DT_NODELABEL(u1)"]
    assert ref.problem == "remapped" and "DT_NODELABEL(u5)" in ref.suggestion and ref.alias_entry == "u1 = &u5;"
    assert "u1 = &u5;" in res.overlay                           # alias entry for code that wants a stable name
    assert [d.label for d in res.draft.nodes if d.instance == "U5"] == ["u5"]


def test_remap_recomputes_derived_properties_for_the_target_pads(world, monkeypatch):
    """Target pads on the other pad roles of the instance: the derived property follows the new assignment."""
    dev_pads = ("P7", "P6")
    _remap_world(world, monkeypatch, tgt_pins=dev_pads, recipe=_RecipeBothWays)
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "remapped"
    assert "rxsel = <0>;" in res.overlay and "<P7_U5_PAD1>, <P6_U5_PAD0>" in res.overlay


def test_remap_keeps_target_chosen_and_alias(world, monkeypatch):
    """A chosen entry that the target board already defines stays; the report says so."""
    _remap_world(world, monkeypatch)
    world.tgt.edt.chosen_nodes["zephyr,console"] = world.tgt.by_path["/t/u0"]
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "remapped"
    assert "zephyr,console = &u5;" not in res.overlay and "my-uart = &u5;" in res.overlay
    assert any("chosen zephyr,console not moved" in x for x in n.notes)


def test_no_remap_when_a_role_has_no_target_pad(world, monkeypatch):
    _remap_world(world, monkeypatch, tgt_pins=("P6", None))
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "conflict" and "&u5 {" not in res.overlay
    assert any("no pad there for RX (P6, X1 pin 2)" in x and "no remap" in x for x in n.lines)


def test_no_remap_when_the_instance_is_not_free(world, monkeypatch):
    t_u5 = _remap_world(world, monkeypatch)
    t_u5.status = "okay"
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "conflict" and "&u5 {" not in res.overlay
    assert any("U5 (enabled as &u5)" in x for x in n.lines)


def test_no_remap_when_the_target_pads_are_used(world, monkeypatch):
    _remap_world(world, monkeypatch)
    world.tgt_uses["P7"] = [NS(node="/t/i0", source="pinctrl")]
    n = _by_label(world)["u1"]
    assert n.status == "conflict" and any("already used in the target build: P7 by &i0" in x for x in n.lines)


def test_no_remap_when_no_valid_assignment(world, monkeypatch):
    """The instance offers the pads, but not for these roles (TX on PAD1 is not a valid assignment here)."""
    _remap_world(world, monkeypatch, tgt_pins=("P7", "P6"))        # TX -> PAD1, RX -> PAD0: only _RecipeBothWays allows it
    n = _by_label(world)["u1"]
    assert n.status == "conflict" and any("no remap: no free instance with a valid role assignment" in x for x in n.lines)


def test_remap_does_not_take_pads_of_another_ported_node(world, monkeypatch):
    """A node that the port carries over on its own instance keeps its pads."""
    _remap_world(world, monkeypatch, tgt_pins=("P1", "P2"))
    n = _by_label(world)["u1"]
    assert n.status == "conflict"                                   # P1/P2 belong to u0 (identical in the target)


def test_second_conflict_cannot_take_the_instance_of_the_first(world, monkeypatch):
    _remap_world(world, monkeypatch)
    # a second source node on the same connector pins competes for U5: only the first (u1) gets it
    world.draft.nodes[2].pins = {"TX": "P5", "RX": "P6"}
    t_u2 = world.tgt.by_path.get("/t/u2") or _node("/t/u2", "u2", ["beta,other"], status="okay", addr=0x3000)
    world.tgt.edt.nodes.append(t_u2)
    world.tgt.edt.label2node["u2"] = t_u2
    world.tgt.by_path["/t/u2"] = t_u2
    by = _by_label(world)
    assert by["u1"].status == "remapped" and by["u2"].status == "conflict"


# ---- child nodes go with their bus (P8.4) -------------------------------------------------------------
def _children_world(world):
    from dt_explorer.core.draft import DraftChild
    kid = lambda name, unit, labels=(), props=None, kids=(): DraftChild(     # noqa: E731
        name, unit, ["acme,dev"], list(labels), {"reg": [int(unit, 16)], **(props or {})}, list(kids))
    d = world.draft.nodes
    d[1].children = [                                                     # u1: enabled by the port
        kid("sens", "4f", ["sens0"], {"mode": "fast"}, [kid("sub", "0")]),
        kid("old", "70"),                                                 # the target has it already
        kid("other", "70", ["o2"]),                                       # ... and a unit address clash
        kid("lost", "71", ["l1"], {"ref": [Ref("nolabel")]}),             # reference the target lacks
        kid("clash", "72", ["u0"]),                                       # label of the target
        kid("peer", "73", ["p1"], {"ref": [Ref("sens0")]})]               # refers to a sibling: fine
    d[3].children = [kid("sens", "4f", ["isens"])]                        # i0: bus without target binding
    world.tgt.by_path["/t/u1"].children = {"old@70": NS(name="old@70")}
    world.lift.children[:] = ["/s/u1/sens@4f", "/s/u1/sens@4f/sub@0"]


def test_children_of_a_ported_bus_are_written_inside_it(world):
    _children_world(world)
    res = pr.port_build(world.src, world.tgt, check=False)
    rep, ov = res.report, res.overlay
    assert "\tsens0: sens@4f {\n\t\tcompatible = \"acme,dev\";\n\t\treg = <0x4f>;\n\t\tmode = \"fast\";" in ov
    assert "\t\tsub@0 {" in ov and "p1: peer@73 {" in ov and "ref = <&sens0>;" in ov
    assert "#address-cells = <1>;" in ov                                  # the bus gets the cells for its children
    assert ov.index("sens0: sens@4f") > ov.index("&u1 {") and ov.index("sens0: sens@4f") < ov.index("p1: peer@73")
    assert [n.node_name for n in res.draft.nodes[1].children] == ["sens@4f", "peer@73"]
    carried = {c.desc: c for c in rep.carried}
    assert "sens0: sens@4f" in carried and carried["sens0: sens@4f"].path == "/s/u1/sens@4f"
    nc = {n.desc: n.reason for n in rep.not_carried}
    assert nc["o2: other@70"] == "unit address 70 is used by old@70 in the target node"
    assert nc["l1: lost@71"] == "references &nolabel, which does not exist in the target devicetree"
    assert nc["u0: clash@72"] == "label u0 already exists in the target devicetree"
    by = {n.label: n for n in rep.nodes}
    assert any("old@70 already exists in &u1" in x for x in by["u1"].notes)
    assert not any("old@70" in k for k in nc)                              # not a loss: the target keeps its own
    assert "o2:" not in ov and "l1:" not in ov


def test_children_of_a_bus_that_is_not_ported_are_reported(world):
    _children_world(world)
    rep = pr.port_build(world.src, world.tgt, check=False).report
    nc = {n.desc: n for n in rep.not_carried}
    assert nc["isens: sens@4f"].reason.startswith("bus &i0 is not ported and enabled on the target [no_target]")
    assert nc["isens: sens@4f"].path == "/s/i0/sens@4f"


def test_app_overlay_child_of_a_ported_bus_is_written_once(world):
    _children_world(world)
    _overlay_world(world, "&u1 {\n\tsens0: sens@4f { compatible = \"acme,dev\"; reg = <0x4f>; };\n};\n")
    res = pr.port_build(world.src, world.tgt, check=False)
    assert res.overlay.count("sens@4f") == 1                               # no second copy from the carry path
    assert [c.desc for c in res.report.carried].count("sens0: sens@4f") == 1
    assert not any(n.desc == "sens0: sens@4f" for n in res.report.not_carried)


# ---- P8.10: the new peripheral translations in the port ------------------------------------------------------
def test_can_and_usb_are_untranslatable_in_the_port_when_they_are_in_the_draft(world):
    """CAN and USB have no_target entries (peripheral_pairs.md 6.16, 6.17): a draft node of either kind is reported
    as no_target with the reason, nothing is emitted for it, its pads are named, and the Kconfig table says so."""
    import dt_explorer.translations as builtin
    builtin.register_all()
    for label, compat, addr in (("usb0", "atmel,sam0-usb", 0x41000000), ("can0", "atmel,sam0-can", 0x42000000)):
        sn = _node(f"/s/{label}", label, [compat], addr=addr)
        world.src.edt.nodes.append(sn)
        world.src.edt.label2node[label] = sn
        world.src.by_path[sn.path] = sn
        tn = _node(f"/t/{label}", label, ["beta,generic"], status="disabled", addr=addr)
        world.tgt.edt.nodes.append(tn)
        world.tgt.edt.label2node[label] = tn
        world.tgt.by_path[tn.path] = tn
        world.draft.nodes.append(DraftNode("x", [], {}, Use(), None, {"DM": "P1", "DP": "P2"}, {}, compat, label))
        world.lift.covered.append(CoveredNode(sn.path, label, compat, "x", "acme_recipe", {}, []))
    res = pr.port_build(world.src, world.tgt, check=False)
    by = {n.label: n for n in res.report.nodes}
    assert by["usb0"].status == "no_target" and by["can0"].status == "no_target"
    assert any("udphs" in x or "UDPHS" in x for x in by["usb0"].lines) and any("can_sam0.c" in x for x in by["can0"].lines)
    assert any("pins stay to be handled by hand: DM P1, DP P2" in x for x in by["usb0"].notes)
    assert "&usb0" not in res.overlay and "&can0" not in res.overlay
    text = "\n".join(res.report.lines())
    assert "&usb0  atmel,sam0-usb -> -  [no_target]" in text and "&can0  atmel,sam0-can -> -  [no_target]" in text
    rows = {r.source_compatible: r for r in res.report.kconfig}
    assert rows["atmel,sam0-usb"].target_compatible is None and rows["atmel,sam0-can"].target_compatible is None


def test_lab1_to_sg41_new_pairs_are_translated_in_the_real_port(rtos4_ws, main_ws, capsys):
    """lab1 (SAME54 Xplained, RTOS4 v4.3.0) -> the P6.2 target build for pic32cx_sg41_cult (Zephyr main): ADC, TCC PWM, GMAC
    and MDIO were 'no_translation' before P8.10. Needs the target build of P6.2 under %TEMP%."""
    import tempfile
    import dt_explorer.app as app
    src = rtos4_ws / "apps" / "lab1" / "build_same54_xpro"
    tgt = Path(tempfile.gettempdir()) / "dtx_p62_lab1_pic32cx_sg41_cult"
    for b in (src, tgt):
        if not (b / "zephyr" / "edt.pickle").is_file():
            pytest.skip(f"no build at {b} (zephyr/edt.pickle missing)")
    app.load_plugins()
    res = pr.port_build(src, tgt, check=False)
    by = {n.label: n for n in res.report.nodes}
    for label, compat, target in (("adc0", "atmel,sam0-adc", "microchip,adc-g1"),
                                  ("tcc0", "atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm"),
                                  ("gmac", "atmel,sam0-gmac", "microchip,gmac-g1-eth"),
                                  ("mdio", "atmel,sam-mdio", "microchip,gmac-g1-mdio")):
        n = by[label]
        assert n.source_compatible == compat and n.target_compatible == target
        assert n.status in ("emitted", "identical", "conflict"), (label, n.status)
        assert n.lines or n.notes, label                                   # every outcome comes with its reasons
    assert by["adc0"].status == "identical" and any("prescaler" in x for x in by["adc0"].lines)
    assert by["tcc0"].status == "emitted" and by["tcc0"].emitted == ["#pwm-cells"]
    assert by["tcc0"].translated["#pwm-cells"] == 3 and "#pwm-cells = <3>;" in res.overlay
    assert any("third cell" in x for x in by["tcc0"].lines)                # the consumers' pwms are reported, not rewritten
    assert by["gmac"].emitted == ["phy-handle"] and "phy-handle = <&phy>;" in res.overlay
    assert any(x.startswith("mac-eeprom") and "nvmem-cells" in x for x in by["gmac"].lines)
    assert any("ethernet-phy@0" in x for x in by["mdio"].notes)            # the PHY child is the target board's own
    rows = {r.source_compatible: r.target_compatible for r in res.report.kconfig}
    assert rows["atmel,sam0-adc"] == "microchip,adc-g1" and rows["atmel,sam-mdio"] == "microchip,gmac-g1-mdio"
    assert "CONFIG_ADC_MCHP_G1=y" in res.conf and "CONFIG_ETH_MCHP_GMAC_G1=y" in res.conf
    with capsys.disabled():
        print("\n".join(res.report.lines()).encode("ascii", "replace").decode())   # arrows: the console may be cp1252
        print("=== overlay ===")
        print(res.overlay)
        print("=== conf ===")
        print(res.conf)
