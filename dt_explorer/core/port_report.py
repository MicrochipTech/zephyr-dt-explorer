"""Port a build to another board of a different driver family: target overlay, .conf and a report.

Pipeline (``port_build``), generic; vendor knowledge comes only from plugins and translations:

1. **Lift** the source build (``lift.lift_build``): every enabled node that a recipe covers becomes a
   draft node (class, roles, pads, board-written properties, chosen/aliases).
2. **Match** each covered node to a node of the *target* build (same node label, else same register
   address) and look up the binding translation ``source compatible -> target compatible``
   (``plugins.translations()``). The properties of the draft node, plus the ones the source recipe
   derives again from the roles (``rxpo``/``txpo``), are translated (``Translation.translate``). Its
   report lines are kept per node.
3. **Pins**: the pads stay the same; the group is written again by the *target* build's pinctrl
   formatter against the target device model. A pad that is not a signal of the instance, or not on the
   target package, is reported.
4. **Render** the target overlay. What the target board already configures is not repeated: a node that
   is ``okay`` in the target build gets only the properties, chosen entries, aliases and pins that
   differ (pins in a group of its own name, as ``Edit pins...`` does).
5. **Kconfig**: suggestions for the target compatibles (``kconfig_suggest`` on the target build) against
   the symbols of the source compatibles in the source ``.config``; the app's ``prj.conf`` symbols that
   the target build handles differently.
6. **Report** (``PortReport``): translated and untranslatable properties per node, Kconfig differences,
   ``DT_NODELABEL``/``DT_ALIAS``/``DT_CHOSEN`` references in ``src/`` that the target does not resolve
   (with a suggestion and the alias entry for the overlay), pins that are not available on the target
   SoC, nodes without translation and child nodes that a draft cannot hold.

Nothing is written into the app. ``check`` (fast check) and ``verify.verify`` (Zephyr itself) run on the
*target* build.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import pins as pins_mod
from . import plugins
from .build import BuildModel
from .buildinfo import BuildInfo, parse_config, read_build_info
from .draft import BindingIndex, Draft, DraftNode, Use
from .kconfig_suggest import KconfigLoadError, KconfigSuggestion, load_kconfig, suggest_kconfig
from .lift import LiftReport, lift_build
from .render import RenderContext, RenderError, _signals, render_overlay
from .translate import Ref, Sym, Translation
from .validate import Finding, validate

_check_lock = threading.Lock()          # validate() shares an unsynchronised binding-read cache
_NO_EMIT = ("compatible", "status")


# ---- report model ------------------------------------------------------------------------------
@dataclass
class NodeResult:
    label: str | None
    source_path: str
    source_compatible: str
    target_compatible: str | None = None
    target_path: str | None = None
    status: str = "emitted"             # emitted | identical | no_target | no_translation | no_node | error
    translated: dict = field(default_factory=dict)       # target property -> value (all of them)
    emitted: list[str] = field(default_factory=list)     # properties that went into the overlay
    same_in_target: list[str] = field(default_factory=list)   # translated but already so in the target
    lines: list[str] = field(default_factory=list)       # report lines of the translation
    notes: list[str] = field(default_factory=list)


@dataclass
class KconfigRow:
    source_compatible: str
    target_compatible: str | None
    source_symbols: dict[str, str] = field(default_factory=dict)    # the source compatible's driver symbols
    source_enabled: list[str] = field(default_factory=list)         # of them set in the source .config
    target_symbols: dict[str, str] = field(default_factory=dict)    # suggestions for the target compatible
    target_enabled: list[str] = field(default_factory=list)         # of them already set in the target .config
    notes: list[str] = field(default_factory=list)


@dataclass
class ConfDiff:
    symbol: str
    source: str | None                  # value in the source .config (None = not set)
    target: str | None
    note: str


@dataclass
class LabelRef:
    file: str
    line: int
    macro: str                          # "DT_NODELABEL(sercom3)"
    kind: str                           # NODELABEL | ALIAS | CHOSEN
    name: str
    problem: str                        # missing | disabled
    detail: str
    suggestion: str = ""                # e.g. "DT_ALIAS(sercom3)"
    alias_entry: str = ""               # e.g. "sercom3 = &sercom3;" for the target overlay


@dataclass
class PinIssue:
    pad: str
    nodes: list[str]
    ported: bool                        # a node that this port carries over uses the pad
    problem: str


@dataclass
class Unported:
    path: str
    labels: list[str]
    compatible: str
    origin: str
    reason: str
    in_target: bool


@dataclass
class PortReport:
    source: str = ""
    target: str = ""
    source_board: str = ""
    target_board: str = ""
    nodes: list[NodeResult] = field(default_factory=list)
    kconfig: list[KconfigRow] = field(default_factory=list)
    conf_diffs: list[ConfDiff] = field(default_factory=list)
    label_refs: list[LabelRef] = field(default_factory=list)
    pin_issues: list[PinIssue] = field(default_factory=list)
    unported: list[Unported] = field(default_factory=list)
    not_representable: list[str] = field(default_factory=list)
    findings: list[Finding] | None = None
    notes: list[str] = field(default_factory=list)

    def sections(self) -> list[tuple[str, list[str]]]:
        """(title, lines) in report order; empty sections say so."""
        out: list[tuple[str, list[str]]] = []
        ls: list[str] = []
        for n in self.nodes:
            head = f"&{n.label}  {n.source_compatible} -> {n.target_compatible or '-'}  [{n.status}]"
            ls.append(head)
            if n.emitted:
                ls.append("    emitted: " + ", ".join(n.emitted))
            if n.same_in_target:
                ls.append("    already so in the target board: " + ", ".join(n.same_in_target))
            ls += [f"    {x}" for x in n.lines]
            ls += [f"    note: {x}" for x in n.notes]
        out.append(("1. Nodes: translated and untranslatable properties", ls or ["no covered node in the source build"]))
        ks: list[str] = []
        for r in self.kconfig:
            ks.append(f"{r.source_compatible} -> {r.target_compatible or '-'}")
            ks.append("    source: " + (", ".join(f"{k}={v}" for k, v in r.source_symbols.items()) or "-")
                      + ("  (set in the source .config: " + ", ".join(r.source_enabled) + ")" if r.source_enabled else ""))
            ks.append("    target: " + (", ".join(f"{k}={v}" for k, v in r.target_symbols.items()) or "-")
                      + ("  (already set in the target .config: " + ", ".join(r.target_enabled) + ")" if r.target_enabled else ""))
            ks += [f"    note: {x}" for x in r.notes]
        for d in self.conf_diffs:
            ks.append(f"prj.conf {d.symbol}: source .config {d.source!r}, target .config {d.target!r}  {d.note}")
        out.append(("2. Kconfig differences", ks or ["none"]))
        rs: list[str] = []
        for r in self.label_refs:
            rs.append(f"{r.file}:{r.line}  {r.macro}  [{r.problem}] {r.detail}")
            if r.suggestion:
                rs.append(f"    suggestion: {r.suggestion}" + (f"   overlay: aliases {{ {r.alias_entry} }}" if r.alias_entry else ""))
        out.append(("3. Node references in src/ that the target does not resolve", rs or ["none"]))
        ps = [f"{p.pad}  {'(ported node) ' if p.ported else ''}used by {', '.join(p.nodes)}: {p.problem}"
              for p in self.pin_issues]
        out.append(("4. Pins not available on the target SoC", ps or ["none"]))
        us: list[str] = []
        for u in self.unported:
            us.append(f"{u.path}  {u.compatible}  [{u.origin}]  {u.reason}"
                      f"  ({'a node of that path/label exists in the target' if u.in_target else 'NOT in the target'})")
        us += [f"not representable: {x}" for x in self.not_representable]
        out.append(("5. Nodes without translation and not representable children", us or ["none"]))
        if self.findings is not None:
            fs = [f"{f.severity}: {f.kind}: {f.message}" for f in self.findings]
            out.append(("6. Fast check on the target build", fs or ["no findings"]))
        if self.notes:
            out.append(("Notes", list(self.notes)))
        return out

    def lines(self) -> list[str]:
        out = [f"port report: {self.source} ({self.source_board}) -> {self.target} ({self.target_board})"]
        for title, ls in self.sections():
            out.append("")
            out.append(title)
            out += [f"  {x}" for x in ls]
        return out


@dataclass
class PortResult:
    overlay: str
    conf: str
    report: PortReport
    draft: Draft                        # the draft in the target port (nodes that are carried over)


# ---- small helpers -----------------------------------------------------------------------------
def _alias_name(name: str) -> str:
    """A name that dtc accepts as an alias: lower case letters, digits and '-'."""
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-") or "node"


def _norm(v):
    """Comparable form of a property value (Sym -> int, bytes -> list, tuples -> lists)."""
    if isinstance(v, Ref):
        return ("ref", v.label)
    if isinstance(v, (bytes, bytearray)):
        return [int(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return v


def _same_value(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    if isinstance(a, list) and len(a) == 1 and not isinstance(b, list):
        a = a[0]
    if isinstance(b, list) and len(b) == 1 and not isinstance(a, list):
        b = b[0]
    return a == b


def _first_addr(node) -> int | None:
    regs = getattr(node, "regs", None) or []
    return regs[0].addr if regs else None


def _label(node) -> str | None:
    return node.labels[0] if node.labels else None


def _match_target(src_node, label: str | None, tgt: BuildModel):
    """Node of the target build for `src_node`: same label, else same first register address."""
    if label and label in tgt.edt.label2node:
        return tgt.edt.label2node[label]
    addr = _first_addr(src_node)
    if addr is not None:
        for n in tgt.edt.nodes:
            if _first_addr(n) == addr and n.compats and not n.path.startswith("/cpus"):
                return n
    return None


def _node_pads(usage, path: str) -> set[str]:
    return {pad for pad, uses in usage.uses.items() for u in uses
            if u.node == path and u.source == "pinctrl" and pad}


def _instance_name(device, node, fallback: str | None) -> str | None:
    addr = _first_addr(node) if node is not None else None
    if device is not None and addr is not None:
        for inst in device.instances.values():
            if inst.base == addr:
                return inst.name
    return fallback


def _translation_for(compat: str, target_node_compats: list[str]) -> Translation | None:
    cands = [t for t in plugins.translations() if getattr(t, "source", None) == compat]
    if not cands:
        return None
    for t in cands:
        if getattr(t, "target", None) in target_node_compats:
            return t
    withtarget = [t for t in cands if getattr(t, "target", None)]
    return (withtarget or cands)[0]


def _edt_phandles(prop) -> list[list]:
    """phandle-array property of an edtlib node as ``[[Ref, cell, ...], ...]``."""
    out = []
    for e in prop.val or []:
        ctrl = getattr(e, "controller", None)
        lbl = _label(ctrl) if ctrl is not None else None
        out.append([Ref(lbl or "?"), *[int(v) for v in e.data.values()]])
    return out


def _clock_notes(src_node, t: Translation) -> list[str]:
    """Lines of the translation's clock rules for a node whose clocks the target board has not enabled."""
    props: dict = {}
    for name in ("clocks", "atmel,assigned-clocks"):
        p = src_node.props.get(name)
        if p is not None and p.type == "phandle-array":
            props[name] = _edt_phandles(p)
    cn = src_node.props.get("clock-names")
    if cn is not None:
        props["clock-names"] = list(cn.val)
    if not props:
        return []
    try:
        _out, lines = t.translate(props)
    except Exception as exc:  # noqa: BLE001 - hooks are plugin code; the report stays usable
        return [f"clock rules could not be evaluated: {exc}"]
    return [x for x in lines if x.startswith(("atmel,assigned-clocks", "clocks"))]


def _not_ported_hint(snode) -> str:
    return (f"no enabled counterpart of {snode.path} in the target build; the node is not carried over "
            "(section 5), so no alias can point at it yet")


def _strip_status(text: str) -> str:
    return re.sub(r'^\tstatus = "okay";\n', "", text, count=1, flags=re.M)


def _rename(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, text)


# ---- the pipeline ------------------------------------------------------------------------------
def port_build(source: Path | str | BuildModel, target: Path | str | BuildModel, *,
               check: bool = True, on_step=None) -> PortResult:
    """Port `source` to `target` (two build folders of the same app, built for different boards).

    `on_step(text)` reports progress. The fast check runs on the target build unless `check` is False.
    """
    def step(text: str) -> None:
        if on_step:
            on_step(text)

    src = source if hasattr(source, "edt") else BuildModel(Path(source))
    tgt = target if hasattr(target, "edt") else BuildModel(Path(target))
    src_info, tgt_info = read_build_info(src.build), read_build_info(tgt.build)
    rep = PortReport(source=src.build.name, target=tgt.build.name,
                     source_board=src_info.board_target or src_info.board or "?",
                     target_board=tgt_info.board_target or tgt_info.board or "?")
    if src.build.resolve() == tgt.build.resolve():
        rep.notes.append("source and target are the same build folder")

    step("lifting the source build ...")
    draft, lift = lift_build(src, src_info)
    src_usage = pins_mod.used_pins(src, src_info)
    tgt_usage = pins_mod.used_pins(tgt, tgt_info)
    src_dev, tgt_dev = src_usage.device, tgt_usage.device
    if tgt_dev is None:
        rep.notes.append("no device model for the target SoC: pins cannot be rendered or checked")
    tgt_index = BindingIndex([])
    tgt_index.by_name = dict(tgt.yaml_index)

    pin_node = next((n for n in tgt.edt.nodes if any(plugins.get_formatter(c) for c in n.compats)), None)
    ctx = RenderContext(
        device=tgt_dev,
        pinctrl_compatible=next((c for c in (pin_node.compats if pin_node else []) if plugins.get_formatter(c)), None),
        pinctrl_label=(_label(pin_node) if pin_node and pin_node.labels else "pinctrl"),
        workspace=tgt_info.workspace, soc=tgt_info.soc, bindings=tgt_index,
        existing_compatibles={_label(n): n.compats[0] for n in tgt.edt.nodes if n.labels and n.compats})
    ctx_okay = replace(ctx, bindings=None)          # an enabled node already has its constant properties

    step("translating the nodes ...")
    pieces: list[str] = []
    ported_draft = Draft(build=tgt.build.name)
    ported_labels: dict[str, str] = {}              # source label -> target label of nodes that end up okay
    ported_pads: dict[str, list[str]] = {}
    ported_compats: list[tuple[str, str]] = []      # (source compatible, target compatible)
    cov_by_label = {c.label: c for c in lift.covered}

    for dn in draft.nodes:
        cov = cov_by_label.get(dn.label)
        src_node = src.by_path.get(cov.path) if cov else None
        res = NodeResult(dn.label, cov.path if cov else "?", dn.compatible or "?")
        rep.nodes.append(res)
        if cov is not None:
            res.notes += cov.notes
        tnode = _match_target(src_node, dn.label, tgt) if src_node is not None else None
        res.status = "emitted"
        if tnode is None:
            res.status = "no_node"
            res.notes.append("no node with this label or register address in the target build; nothing emitted")
            continue
        res.target_path = tnode.path
        tlabel = _label(tnode) or dn.label
        t = _translation_for(dn.compatible or "", list(tnode.compats))
        if t is None:
            res.status = "no_translation"
            res.lines.append(f"no translation registered for {dn.compatible}; node not ported")
            continue
        res.target_compatible = t.target
        if t.no_target is not None:
            res.status = "no_target"
            res.lines = [f"{dn.compatible}: no target binding in the target workspace ({t.no_target.strip()}); "
                         f"the node is not ported"]
            res.notes.append("pins stay to be handled by hand: "
                             + ", ".join(f"{r} {p}" for r, p in dn.pins.items()))
            continue
        if tgt_index.find(t.target) is None:
            res.notes.append(f"binding for {t.target} not found in the target workspace")

        # source properties = board-written ones + what the source recipe derives from the roles
        props: dict = {"compatible": [dn.compatible], **dn.props, **dn.vendor_props}
        recipe = plugins.get_recipe(dn.compatible) if dn.compatible else None
        if recipe is not None and dn.pins and src_dev is not None:
            try:
                sigs = _signals(dn, RenderContext(device=src_dev))
                props.update(recipe.derived_props({r: s.role for r, s in sigs}))
            except (RenderError, ValueError) as exc:
                res.notes.append(f"derived properties not recomputed: {exc}")
        translated, res.lines = t.translate(props)
        for pname, note in t.target_only.items():
            res.lines.append(f"target-only property '{pname}': {note}")
        res.translated = {k: v for k, v in translated.items()}

        okay = tnode.status == "okay"
        vendor: dict = {}
        for k, v in translated.items():
            if k in _NO_EMIT or k.startswith("pinctrl-"):
                continue
            if k in ("#address-cells", "#size-cells") and not tnode.children and not src_node.children:
                res.lines.append(f"'{k}' not emitted: neither the source nor the target node has child nodes")
                continue
            cur = tnode.props.get(k)
            if okay and cur is not None and _same_value(cur.val, v):
                res.same_in_target.append(k)
            else:
                vendor[k] = v
        res.emitted = list(vendor)

        # pins: unchanged when the target node already uses exactly these pads
        pins = dict(dn.pins)
        if okay and pins and set(pins.values()) == _node_pads(tgt_usage, tnode.path):
            res.same_in_target.append("pinctrl-0 (same pads)")
            pins = {}
        elif okay and pins:
            res.notes.append("the target board puts this node on other pads; the overlay overrides them "
                             f"(group {tlabel}_{dn.cls}_edit)")

        # use: chosen entries and aliases that the target does not have yet
        chosen = [c for c in dn.use.chosen
                  if getattr(tgt.edt.chosen_nodes.get(c), "path", None) != tnode.path]
        aliases = [a for a in dn.use.aliases
                   if getattr(tgt.edt._dt.alias2node.get(a), "path", None) != tnode.path]
        res.emitted += [f"chosen {c}" for c in chosen] + [f"alias {a}" for a in aliases]
        if not okay:
            res.notes += _clock_notes(src_node, t)
            res.notes.append("the target board does not enable this node: the overlay enables it")

        tdn = DraftNode(cls=dn.cls, roles=list(dn.roles) if pins else [], props={}, use=Use(chosen, aliases),
                        instance=_instance_name(tgt_dev, tnode, dn.instance), pins=pins,
                        vendor_props=vendor, compatible=t.target, label=tlabel)
        needs = bool(vendor or pins or chosen or aliases or not okay or
                     (tnode.compats and tnode.compats[0] != t.target))
        ported_compats.append((dn.compatible, t.target))
        for pad in dn.pins.values():
            ported_pads.setdefault(pad, []).append(f"&{tlabel}")
        if not needs:
            res.status = "identical"
            res.notes.append("the target board already configures this node the same way; nothing to emit")
            ported_draft.nodes.append(tdn)
            ported_labels[dn.label or tlabel] = tlabel
            continue
        try:
            text = render_overlay(Draft(nodes=[tdn]), ctx if not okay else ctx_okay)
        except RenderError as exc:
            res.status = "error"
            res.notes.append(f"cannot render: {exc}")
            rep.pin_issues.append(PinIssue(", ".join(dn.pins.values()) or "-", [f"&{tlabel}"], True, str(exc)))
            continue
        if okay:
            text = _strip_status(text)
            if pins:
                text = _rename(text, f"{tlabel}_{dn.cls}_default", f"{tlabel}_{dn.cls}_edit")
        ported_draft.nodes.append(tdn)
        ported_labels[dn.label or tlabel] = tlabel
        pieces.append(f"/* &{tlabel}: {dn.compatible} -> {t.target} */\n{text.rstrip()}\n")

    # ---- other nodes of the source build that were written by the board or the app ------------
    for u in lift.uncovered:
        if u.origin in ("soc", "module"):
            continue
        tn = tgt.by_path.get(u.path) or next((tgt.edt.label2node[x] for x in u.labels if x in tgt.edt.label2node), None)
        rep.unported.append(Unported(u.path, list(u.labels), u.compatible, u.origin,
                                     "no recipe or translation covers it; carry it over by hand", tn is not None))
    for nr in lift.not_representable:
        rep.not_representable.append(f"{nr.path}  {nr.what}: {nr.reason}")

    # ---- node references in src/ ----------------------------------------------------------------
    step("checking node references in src/ ...")
    alias_entries: dict[str, str] = {}
    chosen_src = {n.path: [k for k, c in src.edt.chosen_nodes.items() if c.path == n.path] for n in src.edt.nodes}
    alias_src = {}
    for a, n in src.edt._dt.alias2node.items():
        alias_src.setdefault(n.path, []).append(a)
    for r in src.code_refs:
        m = re.match(r"DT_(\w+)\((\w+)\)", r["macro"])
        kind, name = m.group(1), m.group(2)
        snode = r["node"]
        ref = LabelRef(r["file"], r["line"], r["macro"], kind, name, "missing", "")
        if kind == "NODELABEL":
            tn = tgt.edt.label2node.get(name)
            if tn is not None and (tn.status == "okay" or name in ported_labels):
                continue                    # resolves; a disabled node is enabled by this overlay
            if tn is not None:
                ref.problem, ref.detail = "disabled", f"label exists in the target but the node {tn.path} is disabled"
            else:
                ref.detail = "label does not exist in the target devicetree"
            if snode is None:
                ref.detail += " (nor in the source build)"
            else:
                tc = tn or _match_target(snode, None, tgt)
                tlbl = ported_labels.get(name) or (_label(tc) if tc is not None else None)
                tgt_ok = tlbl is not None and (name in ported_labels or
                                               (tc is not None and tc.status == "okay"))
                ch = [c for c in chosen_src.get(snode.path, []) if c in tgt.edt.chosen_nodes]
                al = alias_src.get(snode.path, [])
                if ch and tgt_ok:
                    ref.suggestion = f"DT_CHOSEN({re.sub(r'[^0-9a-zA-Z]', '_', ch[0])})"
                elif al:
                    ref.suggestion = f"DT_ALIAS({al[0].replace('-', '_')})"
                    if tgt_ok:
                        ref.alias_entry = f"{al[0]} = &{tlbl};"
                elif tgt_ok:
                    new = _alias_name(name)
                    ref.suggestion = f"DT_ALIAS({new.replace('-', '_')})"
                    ref.alias_entry = f"{new} = &{tlbl};"
                else:
                    ref.suggestion = _not_ported_hint(snode)
        elif kind == "ALIAS":
            tn = tgt.edt._dt.alias2node.get(name.replace("_", "-")) or tgt.edt._dt.alias2node.get(name)
            if tn is not None:
                continue
            ref.detail = "alias does not exist in the target devicetree"
            if snode is not None:
                tlbl = ported_labels.get(_label(snode) or "")
                tc = _match_target(snode, None, tgt)
                if tlbl is None and tc is not None and tc.status == "okay":
                    tlbl = _label(tc)
                if tlbl:
                    ref.alias_entry = f"{name.replace('_', '-')} = &{tlbl};"
                    ref.suggestion = f"keep DT_ALIAS({name}); add the alias to the target overlay"
                else:
                    ref.suggestion = _not_ported_hint(snode)
        else:
            if name in tgt.chosen:
                continue
            ref.detail = "chosen entry does not exist in the target devicetree"
        if ref.alias_entry:
            a_name, a_val = (x.strip() for x in ref.alias_entry.rstrip(";").split("=", 1))
            alias_entries.setdefault(a_name, a_val)
        rep.label_refs.append(ref)
    new_aliases = {a: v for a, v in alias_entries.items()
                   if a not in tgt.edt._dt.alias2node and not any(a in n.use.aliases for n in ported_draft.nodes)}
    if new_aliases:
        body = "".join(f"\t\t{a} = {v};\n" for a, v in new_aliases.items())
        pieces.append("/* aliases for node references in src/ (DT_ALIAS keeps the code board independent) */\n"
                      "/ {\n\taliases {\n" + body + "\t};\n};\n")

    # ---- pins -------------------------------------------------------------------------------------
    step("checking pins ...")
    if tgt_dev is not None:
        pkg = pins_mod.default_package(tgt_dev)
        on_pkg = {v for v in pkg.pins.values()} if pkg else set()
        known = pins_mod.device_pads(tgt_dev)
        for pad, uses in sorted(src_usage.uses.items()):
            if not pad:
                continue
            nodes = list(dict.fromkeys(
                (f"&{_label(src.by_path[u.node])}" if u.node in src.by_path and src.by_path[u.node].labels else u.node)
                for u in uses))
            problem = None
            if pad not in known:
                problem = f"not a pad of the target device {tgt_dev.name}"
            elif pkg is not None and pad not in on_pkg:
                problem = f"not on package {pkg.name} of the target device {tgt_dev.name}"
            if problem:
                rep.pin_issues.append(PinIssue(pad, nodes, pad in ported_pads, problem))

    # ---- Kconfig ------------------------------------------------------------------------------------
    step("comparing Kconfig ...")
    conf_lines: list[str] = []
    src_cfg = parse_config(src.build / "zephyr" / ".config") if (src.build / "zephyr" / ".config").is_file() else {}
    tgt_cfg = parse_config(tgt.build / "zephyr" / ".config") if (tgt.build / "zephyr" / ".config").is_file() else {}
    pairs = list(dict.fromkeys(ported_compats))
    for n in rep.nodes:
        if n.status == "no_target" and (n.source_compatible, None) not in pairs:
            pairs.append((n.source_compatible, None))
    seen_conf: dict[str, str] = {}
    for sc, tc in pairs:
        row = KconfigRow(sc, tc)
        s_sug: KconfigSuggestion = suggest_kconfig(sc, src.build)
        row.source_symbols = dict(s_sug.symbols)
        row.source_enabled = sorted(k for k, v in s_sug.symbols.items() if src_cfg.get(k) == v)
        row.notes += [f"source: {x}" for x in s_sug.notes]
        if tc:
            t_sug = suggest_kconfig(tc, tgt.build)
            row.target_symbols = dict(t_sug.symbols)
            row.target_enabled = list(t_sug.already_enabled)
            row.notes += [f"target: {x}" for x in t_sug.notes]
            for k, v in t_sug.symbols.items():
                seen_conf.setdefault(k, v)
            gone = sorted(set(row.source_symbols) - set(row.target_symbols))
            if gone:
                row.notes.append("symbols of the source driver that the target does not use: " + ", ".join(gone))
        else:
            row.notes.append("no target driver: the source symbols have no counterpart")
        rep.kconfig.append(row)
    for k, v in seen_conf.items():
        if tgt_cfg.get(k) == v:
            conf_lines.append(f"# {k}={v}  (already set in the target build)")
        else:
            conf_lines.append(f"{k}={v}")
    rep.conf_diffs = _conf_diffs(src, tgt, src_cfg, tgt_cfg)

    # ---- fast check -------------------------------------------------------------------------------------
    overlay = "".join(p + "\n" for p in pieces).rstrip() + "\n" if pieces else ""
    header = (f"/* Port of {src.build.name} ({rep.source_board}) to {rep.target_board}\n"
              f" * generated by Devicetree Explorer; apply as boards/{tgt_info.board or 'board'}.overlay */\n")
    overlay = header + ("\n" + overlay if overlay else "/* nothing to add: the target board already covers the ported nodes */\n")
    conf = ("# Kconfig lines for " + rep.target_board + "\n" + "\n".join(conf_lines) + "\n") if conf_lines \
        else "# no Kconfig lines needed for " + rep.target_board + "\n"
    if check:
        step("fast check on the target build ...")
        with _check_lock:
            rep.findings = validate(tgt.build, overlay)
    return PortResult(overlay=overlay, conf=conf, report=rep, draft=ported_draft)


_PRJ_LINE = re.compile(r'^\s*(CONFIG_\w+)\s*=\s*("[^"]*"|[^\s#]+)')


def _conf_diffs(src: BuildModel, tgt: BuildModel, src_cfg: dict, tgt_cfg: dict) -> list[ConfDiff]:
    """prj.conf symbols that the source build honours and the target build does not."""
    prj = Path(src.app_dir) / "prj.conf"
    if not prj.is_file():
        return []
    tree = None
    try:
        tree = load_kconfig(tgt.build)
    except (KconfigLoadError, OSError, KeyError, ValueError):
        tree = None
    out: list[ConfDiff] = []
    for line in prj.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _PRJ_LINE.match(line)
        if not m:
            continue
        sym, want = m.group(1), m.group(2).strip('"')
        s, t = src_cfg.get(sym), tgt_cfg.get(sym)
        if s == t:
            continue
        if tree is not None and sym[len("CONFIG_"):] not in tree.syms:
            note = "symbol does not exist in the target Kconfig tree"
        elif t is None:
            note = "not set in the target build (a dependency is not met there)"
        else:
            note = "different value in the target build"
        out.append(ConfDiff(sym, s, t, note))
    return out
