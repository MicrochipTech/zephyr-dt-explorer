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
from dt_explorer.core.buildinfo import BuildInfo
from dt_explorer.core.device import DeviceModel, Instance, Package, Signal
from dt_explorer.core.draft import Draft, DraftNode, Use
from dt_explorer.core.kconfig_suggest import KconfigSuggestion
from dt_explorer.core.lift import CoveredNode, LiftReport, NotRepresentable, UncoveredNode
from dt_explorer.core.translate import translations_from_dict


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
    return NS(src=src, tgt=tgt, draft=draft, tgt_uses=tgt_uses)


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
    assert macros["DT_ALIAS(my_uart)"].alias_entry == "my-uart = &u1;"
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
    world.draft.nodes[1].pins = {"TX": "P1", "RX": "P2"}                  # U1 has P3/P4 only
    res = pr.port_build(world.src, world.tgt, check=False)
    n = {x.label: x for x in res.report.nodes}["u1"]
    assert n.status == "error" and "pad P1 is not a signal" in " ".join(n.notes)
    assert any(p.ported and "&u1" in p.nodes for p in res.report.pin_issues)
