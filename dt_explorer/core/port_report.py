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

from . import board_desc
from . import pins as pins_mod
from . import plugins
from .build import BuildModel, origin_kind
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
    status: str = "emitted"             # emitted | identical | conflict | no_target | no_translation | no_node | error
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
    carried: list[Carried] = field(default_factory=list)
    not_carried: list[NotCarried] = field(default_factory=list)
    findings: list[Finding] | None = None
    connector_roles: list[str] | None = None     # None: section not computed
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
        cs = [f"carried: {c.desc}  ({c.kind}, {c.source}:{c.line})" for c in self.carried]
        cs += [f"not carried: {n.desc}  ({n.kind}, {n.source}" + (f":{n.line}" if n.line else "") + f"): {n.reason}"
               for n in self.not_carried]
        out.append(("5b. App-defined nodes of the app overlay", cs or ["none"]))
        if self.findings is not None:
            fs = [f"{f.severity}: {f.kind}: {f.message}" for f in self.findings]
            out.append(("6. Fast check on the target build", fs or ["no findings"]))
        if self.connector_roles is not None:
            out.append(("7. Connector roles of the used pads", self.connector_roles or ["none"]))
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


def _translation_for(compat: str, target_node_compats: list[str],
                     has_binding=lambda compat: True) -> Translation | None:
    """Translation for a source compatible. One whose target binding the target workspace lacks
    loses against a ``no_target`` entry for the same source (e.g. an older Zephyr)."""
    cands = [t for t in plugins.translations() if getattr(t, "source", None) == compat]
    if not cands:
        return None
    for t in cands:
        if getattr(t, "target", None) in target_node_compats and has_binding(t.target):
            return t
    withtarget = [t for t in cands if getattr(t, "target", None) and has_binding(t.target)]
    notarget = [t for t in cands if getattr(t, "no_target", None) is not None]
    return (withtarget or notarget or cands)[0]


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
            "(sections 5, 5b), so no alias can point at it yet")


def _strip_status(text: str) -> str:
    return re.sub(r'^\tstatus = "okay";\n', "", text, count=1, flags=re.M)


def _rename(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, text)


def _adopt_west_topdir(model) -> None:
    """Relative file names in zephyr.dts are relative to the west topdir of the build. A build whose app lives in
    another west workspace than its Zephyr (``BuildModel`` assumes ``ZEPHYR_BASE.parent``) would not find the
    board files, so the pads of its nodes would stay unknown; take ``WEST_TOPDIR`` from the CMake cache."""
    cache = getattr(model, "build", None)
    cache = Path(cache) / "CMakeCache.txt" if cache is not None else None
    if cache is None or not cache.is_file():
        return
    m = re.search(r"^WEST_TOPDIR:\w+=(.+)$", cache.read_text(encoding="utf-8", errors="replace"), re.M)
    if m and Path(m.group(1).strip()).is_dir():
        model.west_top = Path(m.group(1).strip())


def _users_of(tgt: BuildModel, tnode) -> str:
    """Chosen entries and aliases of the target build that point at `tnode`, as text."""
    ch = [k for k, c in tgt.edt.chosen_nodes.items() if getattr(c, "path", None) == tnode.path]
    al = [a for a, n in tgt.edt._dt.alias2node.items() if getattr(n, "path", None) == tnode.path]
    return ", ".join([f"chosen {c}" for c in ch] + [f"alias {a}" for a in al])


def _node_name(tgt: BuildModel, path: str) -> str:
    n = tgt.by_path.get(path)
    return f"&{_label(n)}" if n is not None and n.labels else path


def _target_conflicts(dn, tnode, t, tgt: BuildModel, tgt_usage, tgt_dev, pkg) -> list[str]:
    """Reasons why a node must not be put on `tnode` of the target board (empty: no conflict).

    (a) the target node is enabled with another compatible; (b) a ported pad is used by another enabled
    node of the target build; (c) a ported pad is not on the target package (or not a pad of the device).
    """
    out: list[str] = []
    tl = _label(tnode) or dn.label
    if tnode.status == "okay" and t.target not in tnode.compats:
        users = _users_of(tgt, tnode)
        out.append(f"the target board enables &{tl} as {tnode.compats[0] if tnode.compats else '?'}"
                   + (f" ({users})" if users else "") + f"; the port would make it {t.target}")
    on_pkg = set(pkg.pins.values()) if pkg is not None else set()
    known = pins_mod.device_pads(tgt_dev) if tgt_dev is not None else set()
    for role, pad in dn.pins.items():
        if tgt_dev is not None and pad not in known:
            out.append(f"{role} pad {pad} is not a pad of the target device {tgt_dev.name}")
        elif pkg is not None and pad not in on_pkg:
            out.append(f"{role} pad {pad} is not on package {pkg.name} of the target device {tgt_dev.name}")
        others = list(dict.fromkeys(_node_name(tgt, u.node) for u in tgt_usage.uses.get(pad, [])
                                    if u.node != tnode.path))
        if others:
            out.append(f"{role} pad {pad} is used by {', '.join(others)} (enabled in the target board)")
    return out


def _conflict_suggestion(dn, tnode, tgt: BuildModel, tgt_usage, tgt_dev, pkg,
                         src_board: str | None, tgt_board: str | None) -> str:
    """Text only: target pads at the same connector pins, and the instances that can mux them for the same roles."""
    hint = "choose instance and pads with Configure..."
    sb, tb = board_desc.load_board(src_board), board_desc.load_board(tgt_board)
    if sb is None or tb is None or tgt_dev is None:
        return f"no connector data for both boards; {hint}"
    on_pkg = set(pkg.pins.values()) if pkg is not None else set()
    mapped: dict[str, tuple[str, str]] = {}             # role -> (connector, target pad)
    missing: list[str] = []
    for role, pad in dn.pins.items():
        found = None
        for conn in sb.connectors:
            tconn = next((c for c in tb.connectors if c.name == conn.name and c.type == conn.type), None)
            for key, pin in conn.pins.items():
                if pin.pad == pad and tconn is not None:
                    tp = tconn.pins.get(key)
                    ok = tp is not None and tp.pad and (not on_pkg or tp.pad in on_pkg)
                    found = (conn.name, tp.pad if ok else None, key)
                    break
            if found:
                break
        if found and found[1]:
            mapped[role] = (found[0], found[1])
        else:
            missing.append(f"{role} ({pad}, " + (f"{found[0]} pin {found[2]}" if found else "not on a connector") + ")")
    if not mapped:
        return f"no target pad at the connector pins of the source pads; {hint}"
    recipe = plugins.get_recipe(dn.compatible) if dn.compatible else None
    vfn = getattr(recipe, "valid_assignments", None)
    valid = list(vfn(dn.cls)) if vfn is not None else None
    busy = {_first_addr(n) for n in tgt.edt.nodes
            if n.status == "okay" and n.path != tnode.path and _first_addr(n) is not None}
    taken = {p for p, us in tgt_usage.uses.items() if any(u.node != tnode.path for u in us)}
    fits: list[str] = []
    for inst in tgt_dev.instances.values():
        if recipe is not None and not recipe.pin_roles(inst, dn.compatible):
            continue
        if inst.base in busy:
            continue
        ok = True
        for role, (_c, tpad) in mapped.items():
            prs = {s.role for s in inst.signals if s.pad == tpad}
            if not prs or tpad in taken or (valid is not None and not any(a.get(role) in prs for a in valid)):
                ok = False
                break
        if ok:
            fits.append(inst.name)
    conn_names = ", ".join(dict.fromkeys(c for c, _p in mapped.values()))
    text = f"{dn.cls} on {conn_names} of the target: " + ", ".join(f"{r} {p}" for r, (_c, p) in mapped.items())
    if missing:
        text += "; no pad there for " + ", ".join(missing)
    used = [f"{p} by {', '.join(dict.fromkeys(_node_name(tgt, u.node) for u in tgt_usage.uses[p] if u.node != tnode.path))}"
            for _c, p in mapped.values() if p in taken]
    if used:
        text += "; already used in the target build: " + ", ".join(used)
    text += (f" -> instance(s) that can mux them: {', '.join(fits)}" if fits
             else " -> no free instance can mux them for these roles")
    return text + f" ({hint}; the port does not remap)"


# ---- app-defined nodes: carried verbatim from the app overlay -------------------------------------
@dataclass
class Carried:
    desc: str                           # "my_led0: my_led@41008000" or "/aliases foo"
    path: str                           # devicetree path in the source build
    kind: str                           # node | property
    source: str                         # overlay file (relative to the west top)
    line: int


@dataclass
class NotCarried:
    desc: str
    path: str
    kind: str
    source: str
    line: int
    reason: str


@dataclass
class _Item:
    kind: str                           # node | prop | directive
    name: str                           # node name incl. unit address / property name / directive
    labels: list[str]
    text: str                           # original text, starts at the first token, ends at ';'
    line: int
    clean: str = ""                     # same text with comments and strings blanked
    items: list = field(default_factory=list)       # body items of a node


@dataclass
class _Cand:
    item: _Item
    path: str                           # source devicetree path of the node (of the node itself or of its property)
    wrapper: str                        # "/" | "&label" | "&{/path}" in the target overlay
    source: str
    include_lines: list
    covered: bool = False               # handled by a recipe/translation: silently skipped
    reason: str = ""                    # set = not carried
    refs: list = field(default_factory=list)        # ("label", name) | ("path", path)
    defined: list = field(default_factory=list)     # labels defined inside the text


def _blank(arr: list[str], text: str, s: int, e: int) -> None:
    for k in range(s, e):
        if text[k] != "\n":
            arr[k] = " "


def _mask(text: str) -> tuple[str, str]:
    """(comments blanked, comments and string contents blanked); same length, newlines kept."""
    a, b = list(text), list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            _blank(b, text, i + 1, min(j, n))
            i = j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            _blank(a, text, i, j)
            _blank(b, text, i, j)
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            _blank(a, text, i, j)
            _blank(b, text, i, j)
            i = j
        else:
            i += 1
    return "".join(a), "".join(b)


def _preproc(text: str, mask: list[str]) -> list[str]:
    """Blank the preprocessor lines (with continuations) in `mask`; return their text."""
    found: list[str] = []
    raw = text.splitlines(keepends=True)
    pos = i = 0
    while i < len(raw):
        start, body = pos, raw[i]
        pos += len(raw[i])
        if body.lstrip().startswith("#"):
            while body.rstrip("\r\n").endswith("\\") and i + 1 < len(raw):
                i += 1
                body += raw[i]
                pos += len(raw[i])
            found.append(body.strip())
            _blank(mask, text, start, pos)
        i += 1
    return found


def _match(clean: str, i: int, end: int) -> int:
    """Index of the '}' that closes the '{' at i."""
    depth = 0
    for k in range(i, end):
        if clean[k] == "{":
            depth += 1
        elif clean[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return end - 1


def _parse_items(orig: str, clean: str, start: int, end: int) -> list[_Item]:
    out: list[_Item] = []
    i = start
    while i < end:
        if clean[i].isspace():
            i += 1
            continue
        j = i
        while j < end and clean[j] not in ";{":
            j += 1
        if j >= end:
            break
        line = orig.count("\n", 0, i) + 1
        if clean[j] == ";":
            head = clean[i:j].strip()
            m = re.match(r"[^\s=;]+", head)
            name = m.group(0) if m else head
            out.append(_Item("directive" if name.startswith("/") and name != "/" else "prop",
                             name, [], orig[i:j + 1], line, clean[i:j + 1]))
            i = j + 1
            continue
        k = _match(clean, j, end)
        e = k + 1
        while e < end and clean[e].isspace():
            e += 1
        e = e + 1 if e < end and clean[e] == ";" else k + 1
        header = clean[i:j].strip()
        labels = re.findall(r"([A-Za-z_]\w*)\s*:", header)
        name = re.sub(r"^(?:[A-Za-z_]\w*\s*:\s*)+", "", header).strip()
        out.append(_Item("node", name, labels, orig[i:e], line, clean[i:e], _parse_items(orig, clean, j + 1, k)))
        i = e
    return out


def _defined_labels(it: _Item) -> list[str]:
    out = list(it.labels)
    for c in it.items:
        if c.kind == "node":
            out += _defined_labels(c)
    return out


def _refs_of(it: _Item) -> list[tuple[str, str]]:
    body = it.clean
    refs = [("path", m) for m in re.findall(r"&\{([^}]*)\}", body)]
    body = re.sub(r"&\{[^}]*\}", "", body)
    return refs + [("label", m) for m in re.findall(r"&([A-Za-z_]\w*)", body)]


def _body_text(text: str) -> str:
    """The item text re-indented by one tab; deeper lines keep their indentation relative to the closing line."""
    lines = text.rstrip().splitlines()
    if len(lines) == 1:
        return "\t" + lines[0]
    base = len(lines[-1]) - len(lines[-1].lstrip())
    rest = [x for x in lines[1:] if x.strip()]
    cut = min(base, min(len(x) - len(x.lstrip()) for x in rest))
    return "\n".join(["\t" + lines[0]] + [("\t" + x[cut:]) if x.strip() else "" for x in lines[1:]])


def _include_resolves(inc: str, tgt: BuildModel, overlay_dir: Path) -> bool:
    m = re.match(r'#\s*include\s*([<"])([^>"]+)[>"]', inc)
    if not m:
        return True
    zb = Path(tgt.zephyr_base)
    dirs = [zb / "include", zb / "dts", zb / "dts" / "common", Path(tgt.build) / "zephyr" / "include" / "generated",
            Path(tgt.app_dir), Path(tgt.app_dir) / "dts", Path(tgt.app_dir) / "include"]
    dirs += [d for d in (zb / "dts").glob("*") if d.is_dir()]
    for pat in ("modules/*/include", "modules/*/*/include", "modules/*/dts", "modules/*/*/dts"):
        dirs += [d for d in zb.parent.glob(pat) if d.is_dir()]
    if m.group(1) == '"':
        dirs.insert(0, overlay_dir)
    return any((d / m.group(2)).is_file() for d in dirs)


def _overlay_files(src: BuildModel) -> list[Path]:
    """The overlay files that contributed to the source devicetree (origin kind overlay)."""
    files: dict[str, Path] = {}
    for o in (getattr(src, "origins", None) or {}).values():
        for f in [o.file, *[p.file for p in o.props.values()]]:
            if f and origin_kind(f) == "overlay" and f not in files:
                p = src.resolve(f)
                if p.is_file():
                    files[f] = p
    return [files[k] for k in sorted(files)]


def _carry_app_nodes(src: BuildModel, tgt: BuildModel, lift: LiftReport, rep: PortReport,
                     ported_labels: dict[str, str]) -> tuple[list[str], set[str], set[str]]:
    """Copy what the app overlay defines and no translation covers into the target overlay.

    Returns (overlay pieces, source paths that were handled, labels that the carried text defines).
    A node goes over when every label/path it references exists in the target devicetree (or is defined by
    other carried text); a child of a ported bus only when the bus node is ported and enabled there.
    """
    origins = getattr(src, "origins", None) or {}
    cov_paths = {c.path: c for c in lift.covered}
    cov_labels = {c.label for c in lift.covered if c.label}
    res_by_path = {n.source_path: n for n in rep.nodes}
    used_states: list[str] = []                 # pin states of covered nodes: written with the pins, not copied
    for cp in cov_paths:
        node = src.by_path.get(cp)
        for pn, p in (getattr(node, "props", None) or {}).items():
            if pn.startswith("pinctrl-") and pn != "pinctrl-names":
                used_states += [x.path for x in (p.val or []) if getattr(x, "path", None)]
    pin_ctrl = {n.path for n in src.edt.nodes if any(plugins.get_formatter(c) for c in (n.compats or []))}

    cands: list[_Cand] = []
    includes: dict[str, list[str]] = {}

    def add(item: _Item, path: str, wrapper: str, source: str, covered=False, reason="") -> None:
        c = _Cand(item, path, wrapper, source, includes.get(source, []), covered, reason)
        c.refs, c.defined = _refs_of(item), _defined_labels(item)
        cands.append(c)

    def walk(items: list[_Item], parent: str, wrapper: str, source: str, parent_covered: bool) -> None:
        for it in items:
            if it.kind == "directive":
                add(it, parent, wrapper, source, reason=f"{it.name} is not carried over")
                continue
            if it.kind == "prop":
                labels = [r for k, r in _refs_of(it) if k == "label"]
                if parent_covered or parent in cov_paths or (
                        parent in ("/aliases", "/chosen") and labels and all(x in cov_labels for x in labels)):
                    add(it, parent, wrapper, source, covered=True)      # the translation / the use entries do it
                else:
                    add(it, parent, wrapper, source)
                continue
            path = ("/" + it.name) if parent == "/" else f"{parent}/{it.name}"
            origin = origins.get(path)
            defined_here = (origin is None and path not in src.by_path) or \
                (origin is not None and origin_kind(origin.file) == "overlay")
            if not defined_here:                                         # existing node that the overlay edits
                walk(it.items, path, f"&{{{path}}}", source, parent_covered or path in cov_paths)
            elif any(path == s or path.startswith(s + "/") for s in used_states):
                add(it, path, wrapper, source, covered=True)
            elif any(path.startswith(p + "/") for p in pin_ctrl):
                add(it, path, wrapper, source,
                    reason="pin state that no ported node uses; pin groups are written for the ported nodes only")
            elif parent in cov_paths:
                r = res_by_path.get(parent)
                if r is not None and r.status in ("emitted", "identical"):
                    add(it, path, "&" + ported_labels.get(cov_paths[parent].label or "", cov_paths[parent].label or ""),
                        source)
                else:
                    why = (r.lines or r.notes or [""])[0] if r is not None else ""
                    add(it, path, wrapper, source, reason=(
                        f"bus &{cov_paths[parent].label} is not ported and enabled on the target "
                        f"[{r.status if r is not None else 'unknown'}]" + (f": {why}" if why else "")))
            else:
                add(it, path, wrapper, source)

    for f in _overlay_files(src):
        text = f.read_text(encoding="utf-8", errors="replace")
        a, b = _mask(text)
        mask = list(b)
        incl = _preproc(text, mask)
        b = "".join(mask)
        source = src.rel(f)
        includes[source] = [x for x in incl if re.match(r"#\s*include\b", x)]
        for blk in _parse_items(text, b, 0, len(text)):
            if blk.kind != "node":
                if blk.name not in ("/dts-v1/", "/plugin/"):
                    add(blk, "?", "/", source, reason="statement outside a node block is not carried over")
            elif blk.name == "/":
                walk(blk.items, "/", "/", source, False)
            elif blk.name.startswith("&{") and blk.name.endswith("}"):
                walk(blk.items, blk.name[2:-1], blk.name, source, blk.name[2:-1] in cov_paths)
            elif blk.name.startswith("&"):
                node = src.edt.label2node.get(blk.name[1:])
                if node is None:
                    add(blk, "?", blk.name, source, reason=f"label {blk.name[1:]} is not in the source devicetree")
                else:
                    walk(blk.items, node.path, "&" + ported_labels.get(blk.name[1:], blk.name[1:]), source,
                         node.path in cov_paths)
            else:
                add(blk, "?", "/", source, reason="a named node block outside / and &label is not carried over")

    # ---- decide per candidate ---------------------------------------------------------------------
    todo = [c for c in cands if not c.covered]
    tgt_labels = set(tgt.edt.label2node)
    enabled_by_port = set(ported_labels.values())

    for c in todo:
        if c.reason:
            continue
        if c.wrapper != "/":
            node = tgt.by_path.get(c.wrapper[2:-1]) if c.wrapper.startswith("&{") \
                else tgt.edt.label2node.get(c.wrapper[1:])
            if node is None:
                c.reason = f"{c.wrapper} does not exist in the target devicetree"
            elif node.status != "okay" and c.wrapper[1:] not in enabled_by_port:
                c.reason = f"{c.wrapper} is disabled in the target devicetree"
        if not c.reason and c.item.kind == "node" and c.wrapper == "/" and "/" + c.item.name in tgt.by_path:
            c.reason = f"a node /{c.item.name} already exists in the target devicetree"
        if not c.reason:
            clash = [x for x in c.defined if x in tgt_labels]
            if clash:
                c.reason = f"label {clash[0]} already exists in the target devicetree"
    changed = True
    while changed:                      # references must exist in the target or be defined by carried text
        changed = False
        avail = tgt_labels | {x for c in todo if not c.reason for x in c.defined}
        dropped = {x: c for c in todo if c.reason for x in c.defined}
        for c in todo:
            if c.reason:
                continue
            for kind, ref in c.refs:
                if (kind == "label" and (ref in avail or ported_labels.get(ref) in tgt_labels)) \
                        or (kind == "path" and ref in tgt.by_path):
                    continue
                c.reason = f"references {ref} missing in target"
                if ref in dropped:
                    c.reason += f" ({ref} is not carried: {dropped[ref].reason})"
                changed = True
                break

    def desc(c: _Cand) -> str:
        it = c.item
        if it.kind == "node":
            return " ".join([*(f"{x}:" for x in it.labels), it.name])
        return f"{c.path} {it.name}" if it.kind == "prop" else it.name

    pieces: list[str] = []
    handled: set[str] = set()
    carried_labels: set[str] = set()
    ok = [c for c in todo if not c.reason]
    inc_seen = list(dict.fromkeys(i for c in ok for i in c.include_lines))
    for c in todo:
        it = c.item
        kind = {"node": "node", "prop": "property"}.get(it.kind, "statement")
        handled.add(c.path)
        if c.reason:
            rep.not_carried.append(NotCarried(desc(c), c.path, kind, c.source, it.line, c.reason))
            continue
        body = _body_text(it.text)
        for k, ref in c.refs:           # a ported node whose label differs in the target
            if k == "label" and ref not in tgt_labels and ported_labels.get(ref) in tgt_labels:
                body = re.sub(rf"&{re.escape(ref)}(?![\w-])", "&" + ported_labels[ref], body)
        head = "/ {" if c.wrapper == "/" else f"{c.wrapper} {{"
        pieces.append(f"/* carried from {c.source}:{it.line} */\n{head}\n{body}\n}};\n")
        rep.carried.append(Carried(desc(c), c.path, kind, c.source, it.line))
        carried_labels.update(c.defined)
    if pieces:
        good = []
        for inc in inc_seen:
            first = next(c for c in ok if inc in c.include_lines).source
            if _include_resolves(inc, tgt, src.resolve(first).parent):
                good.append(inc)
            else:
                rep.not_carried.append(NotCarried(inc, "-", "include", first, 0,
                                                  "does not resolve in the target workspace; carried text may need it"))
        if good:
            pieces.insert(0, "\n".join(good) + "\n")
    return pieces, handled, carried_labels


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
    _adopt_west_topdir(tgt)
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
    conflicts: dict[str, str] = {}                  # source label -> first reason; nodes that are not emitted
    tgt_pkg = pins_mod.default_package(tgt_dev) if tgt_dev is not None else None

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
        t = _translation_for(dn.compatible or "", list(tnode.compats),
                             has_binding=lambda c: tgt_index.find(c) is not None)
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
        reasons = _target_conflicts(dn, tnode, t, tgt, tgt_usage, tgt_dev, tgt_pkg)
        if reasons:
            res.status = "conflict"
            res.lines += [f"conflict: {x}" for x in reasons]
            moved = dn.use.chosen + dn.use.aliases
            res.lines.append("not emitted; the target board's node, chosen entries and aliases stay as they are"
                             + (f" (not moved: {', '.join(moved)})" if moved else ""))
            res.lines.append("suggestion: " + _conflict_suggestion(
                dn, tnode, tgt, tgt_usage, tgt_dev, tgt_pkg, src_info.board, tgt_info.board))
            conflicts[dn.label or tlabel] = reasons[0]
            continue

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
    step("carrying app-defined nodes ...")
    carried_pieces, handled, carried_labels = _carry_app_nodes(src, tgt, lift, rep, ported_labels)
    pieces += carried_pieces
    for u in lift.uncovered:
        if u.origin in ("soc", "module") or u.path in handled:
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
            if name in carried_labels:
                continue                    # defined by app text that this port carries over
            if name in conflicts:
                ref.problem = "conflict"
                ref.detail = f"the node is not ported ({conflicts[name]}); the label in the target is another node"
                ref.suggestion = "resolve the conflict first (section 1)"
                rep.label_refs.append(ref)
                continue
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
            if snode is not None and _label(snode) in conflicts:
                ref.detail += f"; the node is not ported ({conflicts[_label(snode)]})"
                ref.suggestion = "resolve the conflict first (section 1)"
            elif snode is not None:
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

    # ---- connector roles ----------------------------------------------------------------------------------
    rep.connector_roles = connector_role_lines(src_info.board, tgt_info.board, src_usage, tgt_usage)

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


def connector_role_lines(src_board: str | None, tgt_board: str | None, src_usage, tgt_usage,
                         dirs: list[Path] | None = None) -> list[str] | None:
    """Report section 7: every used pad that sits on a connector pin, moved by connector role.

    One line per used connector pin of the source board, in connector and pin order::

        <connector> pin <n> (<role>): <source pad> on <source board> -> <target pad> on <target board>

    The target pad is the one at the same pin of the same connector (same name and type) on the target
    board. Without a pad there, the line says why (no such connector, no pad behind the pin). A target pad
    that the target build already uses is named. Both boards need a board description (``board_desc``);
    if only one is missing, a single line says which; if both are missing the section is left out (None). `*_usage` are ``pins.PinUsage`` (or anything with a
    ``uses`` mapping pad -> list of uses having ``node``).
    """
    src = board_desc.load_board(src_board, dirs)
    tgt = board_desc.load_board(tgt_board, dirs)
    if src is None and tgt is None:
        return None
    if src is None or tgt is None:
        missing = [f"{n or '?'}" for n, d in ((src_board, src), (tgt_board, tgt)) if d is None]
        return [f"no board description for {', '.join(missing)}: connector roles not reported"]
    out: list[str] = []
    for conn in src.connectors:
        tconn = next((c for c in tgt.connectors if c.name == conn.name and c.type == conn.type), None)
        for key, pin in conn.pins.items():
            if not pin.pad or pin.pad not in src_usage.uses:
                continue
            users = list(dict.fromkeys(u.node.rsplit("/", 1)[-1] for u in src_usage.uses[pin.pad]))
            head = f"{conn.name} pin {key}" + (f" ({board_desc.short_role(pin.role)})" if pin.role else "")
            left = f"{pin.pad} on {src.title}"
            if tconn is None:
                right = f"no such connector ({conn.name}, {conn.type}) on {tgt.title}"
            else:
                tp = tconn.pins.get(key)
                if tp is None or not tp.pad:
                    why = (f"net {tp.net} does not reach the SoC" if tp and tp.net and board_desc.is_signal_role(tp.role)
                           else "no SoC pad behind this pin")
                    right = f"not available on {tgt.title} ({why})"
                else:
                    right = f"{tp.pad} on {tgt.title}"
                    taken = list(dict.fromkeys(u.node.rsplit("/", 1)[-1] for u in tgt_usage.uses.get(tp.pad, [])))
                    if taken:
                        right += f"  (already used in the target build by {', '.join(taken)})"
            out.append(f"{head}: {left} → {right}  [used by {', '.join(users)}]")
    return out


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
