"""Lift a whole build into a draft (core/lift.py, P5.3).

* A synthetic case with a fake build model and vendor-neutral fake plugins needs nothing outside the repo.
* The edtlib compare needs DTX_RTOS4_WS (builds apps/lab1 and apps/lab3, old port) and the Zephyr SDK
  compiler for the preprocessor: the lifted draft is rendered, applied to the build's zephyr.dts with the
  board settings of the covered nodes stripped, and compared with the original edt.pickle.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.build import NodeOrigin, PropInfo, origin_kind
from dt_explorer.core.buildinfo import BuildInfo
from dt_explorer.core.draft import Draft
from dt_explorer.core.lift import lift_build
from dt_explorer.core.render import render_overlay

FIX = Path(__file__).resolve().parent / "fixtures"
ATDF = FIX / "atdf" / "ATSAME54P20A.atdf"


# ---- synthetic case: fake build model, fake vendor-neutral plugins -----------------------------
class FakeFormatter:
    api_version = 1
    name = "acme_fmt"
    compatibles = ["acme,pinctrl"]

    def to_overlay(self, signal): return ""
    def from_overlay(self, value): return self.parse(value).pad
    def render_group(self, signals): return ""

    def parse(self, value):
        m = re.fullmatch(r"(P\d+)_(U\d+)_(PAD\d)", value.strip("<> "))
        if not m:
            raise ValueError(f"not a pinmux name: {value}")
        return NS(pad=m.group(1), instance=m.group(2), role=m.group(3), mux="A", macro=value)


class FakeRecipe:
    api_version = 1
    name = "acme_recipe"
    compatibles = ["acme,uart"]

    def pin_roles(self, instance): return ["TX", "RX"]
    def kconfig(self): return {}

    def derived_props(self, assignment):
        return {"rx": int(assignment["RX"][-1])}

    def lift(self, props, pins):
        return {"TX": pins["PAD0"], "RX": pins[f"PAD{props['rx']}"]}


def _node(path, labels, compats, props=None, status="okay", pinctrls=(), children=None):
    return NS(path=path, labels=labels, compats=compats, status=status,
              props={k: NS(type=t, val=v) for k, (t, v) in (props or {}).items()},
              pinctrls=list(pinctrls), children=children or {}, _node=None)


@pytest.fixture
def fake_model(tmp_path):
    (tmp_path / "base.yaml").write_text("properties:\n  status:\n    type: string\n  reg:\n    type: array\n")
    (tmp_path / "uart-controller.yaml").write_text(
        "include: base.yaml\nproperties:\n  current-speed:\n    type: int\n")
    (tmp_path / "acme,uart.yaml").write_text(
        "compatible: acme,uart\ninclude: uart-controller.yaml\nproperties:\n  rx:\n    type: int\n")
    src = tmp_path / "board.dts"
    src.write_text("pinmux = <P1_U0_PAD0>, <P2_U0_PAD1>;\n")
    plugins.register_recipe(FakeRecipe)
    plugins.register_formatter(FakeFormatter)

    ctrl = NS(path="/pc", labels=["pc"], compats=["acme,pinctrl"], parent=None)
    group = NS(path="/pc/state/g", name="g", labels=[], props={"pinmux": NS(val=[1, 2])},
               children={}, parent=NS(parent=ctrl, compats=[]))
    conf = NS(path="/pc/state", name="state", labels=["state"], props={}, children={"g": group},
              parent=ctrl)
    group.parent = conf
    uart = _node("/soc/uart@0", ["uart0"], ["acme,uart"],
                 {"reg": ("array", [0, 64]), "current-speed": ("int", 9600), "rx": ("int", 1),
                  "zephyr,note": ("phandle", object())},
                 pinctrls=[NS(name="default", conf_nodes=[conf]), NS(name="sleep", conf_nodes=[conf])])
    off = _node("/soc/uart@1", ["uart1"], ["acme,uart"], status="disabled")
    timer = _node("/soc/timer@0", ["t0"], ["acme,timer"])

    board, soc = "zephyr\\boards\\acme\\b.dts", "zephyr\\dts\\arm\\acme\\soc.dtsi"
    pi = lambda n, f: PropInfo(n, "", f, 1)  # noqa: E731
    origins = {
        "/soc/uart@0": NodeOrigin(soc, 1, {
            "reg": pi("reg", soc), "status": pi("status", board), "compatible": pi("compatible", board),
            "current-speed": pi("current-speed", board), "rx": pi("rx", board),
            "zephyr,note": pi("zephyr,note", board),
            "pinctrl-0": pi("pinctrl-0", board), "pinctrl-names": pi("pinctrl-names", board)}),
        "/soc/timer@0": NodeOrigin(soc, 2),
        "/pc/state/g": NodeOrigin(board, 3, {"pinmux": PropInfo("pinmux", "", "board.dts", 1)}),
        "/chosen": NodeOrigin(board, 4, {"zephyr,console": pi("zephyr,console", board),
                                         "zephyr,from-soc": pi("zephyr,from-soc", soc)}),
        "/aliases": NodeOrigin(board, 5, {"my-uart": pi("my-uart", board), "uart-soc": pi("uart-soc", soc)}),
    }
    model = NS(
        build=tmp_path / "build_fake", yaml_index={p.name: p for p in tmp_path.glob("*.yaml")},
        origins=origins, resolve=lambda f: tmp_path / f,
        edt=NS(nodes=[uart, off, timer],
               chosen_nodes={"zephyr,console": uart, "zephyr,from-soc": uart},
               _dt=NS(alias2node={"my-uart": uart, "uart-soc": uart})))
    return model


def test_synthetic_lift(fake_model):
    info = BuildInfo(build_dir=fake_model.build)
    draft, report = lift_build(fake_model, info)

    assert len(draft.nodes) == 1
    n = draft.nodes[0]
    assert (n.cls, n.compatible, n.label, n.instance) == ("uart", "acme,uart", "uart0", "U0")
    assert n.pins == {"TX": "P1", "RX": "P2"} and n.roles == ["TX", "RX"]
    assert n.props == {"current-speed": 9600}                 # reg comes from the SoC and is left out
    assert n.vendor_props == {}                               # rx is derived again, note is a phandle
    assert n.use.chosen == ["zephyr,console"] and n.use.aliases == ["my-uart"]   # SoC entries left out

    assert [c.path for c in report.covered] == ["/soc/uart@0"]
    assert [(u.path, u.compatible, u.origin) for u in report.uncovered] == [("/soc/timer@0", "acme,timer", "soc")]
    assert [(x.what) for x in report.not_representable] == ["zephyr,note"]
    assert any("sleep" in note for note in report.covered[0].notes)
    assert report.pinctrl_compatible == "acme,pinctrl" and report.pinctrl_label == "pc"
    assert Draft.from_dict(draft.to_dict()).nodes[0].pins == n.pins


# ---- edtlib compare on the real builds -----------------------------------------------------------
PLATFORM = ("soc", "module")
PINCTRL_INCLUDE = "#include <dt-bindings/pinctrl/same54p-pinctrl.h>\n"
BUILDS = ["apps/lab1/build_same54_xpro", "apps/lab3/build_same54_xpro"]


def board_names(model, path):
    """Names of the properties of a node that the board or the app wrote (not the SoC)."""
    o = model.origins.get(path)
    return [n for n, p in (o.props if o else {}).items() if origin_kind(p.file) not in PLATFORM]


def child_summary(edt_node, model, lifted) -> dict:
    """Children that the draft holds: compatible, labels, the properties written by the board or app, recursively.

    The property names come from the original build's origins, the values from `edt_node` (original or rebuilt).
    """
    out = {}
    for name, c in edt_node.children.items():
        if c.path not in lifted:
            continue
        names = [n for n in board_names(model, c.path) if n not in ("compatible", "phandle")]
        out[name] = {"compatible": list(c.compats), "labels": sorted(c.labels),
                     "props": {n: (bytes(c._node.props[n].value) if n in c._node.props else None) for n in names},
                     "children": child_summary(c, model, lifted)}
    return out


def summary(edt, model, label: str, names: list[str], lifted=()) -> dict:
    node = edt.label2node[label]
    out = {"compatible": list(node.compats), "status": node.status, "children": child_summary(node, model, lifted)}
    for name in names:
        if name in ("compatible", "status"):
            continue
        if name == "pinctrl-0" and "pinctrl-0" in node.props:
            groups = {}
            for conf in node.props["pinctrl-0"].val:
                for g in conf.children.values():
                    groups[f"{conf.name}/{g.name}"] = sorted(hex(v) for v in g.props["pinmux"].val)   # order inside a group is irrelevant
            out[name] = groups
        else:
            p = node._node.props.get(name)
            out[name] = bytes(p.value) if p is not None else None
    out["chosen"] = sorted(k for k, n in edt.chosen_nodes.items() if n.path == node.path)
    out["aliases"] = sorted(k for k, n in edt._dt.alias2node.items() if n.path == node.path)
    return out


def strip_node(text: str, label: str, names: list[str], uses: list[str], groups: list[str],
               base_compat: str, keep: tuple = (), children: bool = True) -> str:
    """zephyr.dts text with the board settings of one node removed (back to the SoC defaults)."""
    from test_render import _strip_block
    lines = text.splitlines()
    out, i = [], 0
    drop = (set(names) | {"compatible", "status"}) - set(keep)
    head = re.compile(rf"^(\s*){re.escape(label)}:\s*\S+\s*\{{\s*$")
    use_re = re.compile(rf"^\s*({'|'.join(map(re.escape, uses))})\s*=\s*&{re.escape(label)};") if uses else None
    while i < len(lines):
        ln = lines[i]
        m = head.match(ln)
        if not m:
            if not (use_re and use_re.match(ln)):
                out.append(ln)
            i += 1
            continue
        indent = m.group(1)
        out += [ln, f'{indent}\tcompatible = "{base_compat}";', f'{indent}\tstatus = "disabled";']
        i += 1
        while lines[i] != indent + "};":
            ln = lines[i]
            if children and re.match(rf"^{re.escape(indent)}\t[^\t/].*\{{\s*$", ln):   # a child node: whole block goes
                while lines[i] != indent + "\t};":
                    i += 1
                i += 1
                continue
            stmt = re.match(rf"^{re.escape(indent)}\t([^\s\t=;{{]+)\s*[=;]", ln)
            if stmt and stmt.group(1) in drop:
                while ";" not in lines[i].split("/*")[0]:
                    i += 1
                i += 1
                continue
            out.append(ln)
            i += 1
    result = out
    for g in groups:
        result = _strip_block(result, rf"{re.escape(g)}:\s*{re.escape(g)}\s*\{{")
    return "\n".join(result) + "\n"


def _prepare(rtos4_ws, rtos4_build, rel):
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe
    plugins.register_recipe(AtmelSam0SercomRecipe)
    plugins.register_formatter(AtmelSam0Formatter)
    build = rtos4_build(rel)
    model = BuildModel(build)
    draft, report = lift_build(model, read_build_info(build))
    device = MicrochipAtdfProvider().load(ATDF)
    ctx = report.render_context(device)
    return build, model, draft, report, ctx


def _strip_args(model, node):
    path = next(p for p in model.by_path if model.by_path[p].labels[:1] == [node.label])
    edt_node = model.by_path[path]
    names = board_names(model, path)
    groups = [c.labels[0] if c.labels else c.name
              for pc in edt_node.pinctrls for c in pc.conf_nodes]
    uses = list(node.use.chosen)
    return path, names, uses, groups


@pytest.mark.parametrize("rel", BUILDS)
def test_lift_renders_back_to_original_nodes(rtos4_ws, rtos4_build, tmp_path, rel, capsys):
    from test_render import compile_edt
    build, model, draft, report, ctx = _prepare(rtos4_ws, rtos4_build, rel)
    zdts = (build / "zephyr" / "zephyr.dts").read_text(encoding="utf-8")
    results = []
    with capsys.disabled():
        print(f"\n===== {rel} =====")
        print("\n".join(report.lines()))
        print("\n--- draft ---")
        for n in draft.nodes:
            print(n)
        print("\n--- per node compare (apply the node's rendered overlay to zephyr.dts without its board settings) ---")
        for node in draft.nodes:
            path, names, uses, groups = _strip_args(model, node)
            aliases = list(node.use.aliases)
            base = strip_node(zdts, node.label, names, uses, groups, "atmel,sam0-sercom")
            base = _strip_aliases(base, aliases)
            overlay = render_overlay(node, ctx)
            for kid in node.children:       # the children are really gone from the base, so the compare is not vacuous
                assert not re.search(rf"{re.escape(kid.node_name)}\s*\{{", base) and kid.node_name in overlay
            names_all = names + ["compatible", "status"]
            lifted = set(report.children)
            original = summary(model.edt, model, node.label, names_all, lifted)
            # a node with children cannot be parsed without its #address-cells/#size-cells: the reset
            # state keeps them (so it still differs from the original), the rendered state does not
            has_children = bool(model.by_path[path].children)
            reset_base = strip_node(zdts, node.label, names, uses, groups, "atmel,sam0-sercom",
                                    keep=("#address-cells", "#size-cells") if has_children else ())
            reset_base = _strip_aliases(reset_base, aliases)
            try:
                reset = summary(compile_edt(model, PINCTRL_INCLUDE + reset_base, tmp_path), model, node.label,
                                names_all, lifted)
            except Exception as exc:    # noqa: BLE001 - without the children a reference to their label is dangling
                reset = f"reset devicetree does not compile: {str(exc).splitlines()[-1]}"
            rendered = summary(compile_edt(model, PINCTRL_INCLUDE + base + "\n" + overlay, tmp_path),
                               model, node.label, names_all, lifted)
            print(f"{node.label}: {len(node.children)} child node(s) in the draft, "
                  f"{sum(1 for _ in original['children'])} compared (compatible, labels, written properties)")
            ok = rendered == original
            results.append((node.label, ok))
            print(f"{node.label} ({node.compatible}): compare {'MATCH' if ok else 'DIFFERENT'}; "
                  f"reset differs from original: {reset != original}")
            if not ok:
                print("  original:", original)
                print("  rendered:", rendered)
            assert reset != original, f"{node.label}: reset equals original, the compare proves nothing"
            assert rendered == original

        print("\n--- whole draft at once ---")
        base = zdts
        for node in draft.nodes:
            path, names, uses, groups = _strip_args(model, node)
            base = strip_node(base, node.label, names, uses, groups, "atmel,sam0-sercom")
            base = _strip_aliases(base, list(node.use.aliases))
        overlay = render_overlay(draft, ctx)
        edt = compile_edt(model, PINCTRL_INCLUDE + base + "\n" + overlay, tmp_path)
        for node in draft.nodes:
            path, names, _u, _g = _strip_args(model, node)
            names_all = names + ["compatible", "status"]
            same = (summary(edt, model, node.label, names_all, set(report.children))
                    == summary(model.edt, model, node.label, names_all, set(report.children)))
            print(f"{node.label}: {'MATCH' if same else 'DIFFERENT'}")
            assert same
        print(f"result: {sum(ok for _, ok in results)}/{len(results)} nodes match per node, whole draft matches")


def _strip_aliases(text: str, aliases: list[str]) -> str:
    """Remove the alias statements `name = ...;` of a node from the aliases block of zephyr.dts."""
    if not aliases:
        return text
    pat = re.compile(rf"^\s*({'|'.join(map(re.escape, aliases))})\s*=\s*[^;]*;.*$")
    return "\n".join(ln for ln in text.splitlines() if not pat.match(ln)) + "\n"


def test_synthetic_children_are_lifted_into_the_draft(fake_model):
    from dt_explorer.core.translate import Ref
    pi = lambda n, f: PropInfo(n, "", f, 1)  # noqa: E731
    board, soc = "zephyr\\boards\\acme\\b.dts", "zephyr\\dts\\arm\\acme\\soc.dtsi"
    led = _node("/gpio/led", ["led0"], ["acme,led"])
    sub = _node("/soc/uart@0/dev@4f/sub@0", [], [], {"reg": ("array", [0])})
    dev = _node("/soc/uart@0/dev@4f", ["dev0", "dev_alias"], ["acme,dev"], {
        "reg": ("array", [0x4f]), "res": ("int", 9), "mode": ("string", "fast"), "on": ("boolean", True),
        "irq": ("phandle", led), "gp": ("phandle-array", [NS(controller=led, data={"pin": 3, "flags": 1})]),
        "from-soc": ("int", 1), "node-path": ("path", object()), "bad-ref": ("phandle", _node("/x", [], []))},
        children={"sub@0": sub})
    dev.name, sub.name = "dev@4f", "sub@0"
    uart = fake_model.edt.nodes[0]
    uart.children = {"dev@4f": dev}
    fake_model.origins.update({
        dev.path: NodeOrigin(board, 7, {n: pi(n, soc if n == "from-soc" else board) for n in (
            "compatible", "phandle", "reg", "res", "mode", "on", "irq", "gp", "from-soc", "node-path", "bad-ref")}),
        sub.path: NodeOrigin(board, 9, {"reg": pi("reg", board)}),
        "/soc/uart@0/soc-child": NodeOrigin(soc, 1, {"reg": pi("reg", soc)})})
    uart.children["soc-child"] = _node("/soc/uart@0/soc-child", [], ["acme,x"], {"reg": ("array", [1])})
    uart.children["soc-child"].name = "soc-child"

    draft, report = lift_build(fake_model, BuildInfo(build_dir=fake_model.build))
    kids = draft.nodes[0].children
    assert [c.node_name for c in kids] == ["dev@4f"]                       # the SoC's own child is left out
    c = kids[0]
    assert (c.name, c.unit, c.compatible, c.labels) == ("dev", "4f", ["acme,dev"], ["dev0", "dev_alias"])
    assert c.props == {"reg": [0x4f], "res": 9, "mode": "fast", "on": True,
                       "irq": [Ref("led0")], "gp": [[Ref("led0"), 3, 1]]}   # phandle number, SoC prop: left out
    assert c.children[0].node_name == "sub@0" and c.children[0].props == {"reg": [0]}
    assert report.children == ["/soc/uart@0/dev@4f/sub@0", "/soc/uart@0/dev@4f"]
    what = {(x.path, x.what) for x in report.not_representable}
    assert ("/soc/uart@0/dev@4f", "node-path") in what and ("/soc/uart@0/dev@4f", "bad-ref") in what
    assert not any(x.what == "child nodes" for x in report.not_representable)
    assert Draft.from_dict(draft.to_dict()) == draft                       # JSON keeps the references
