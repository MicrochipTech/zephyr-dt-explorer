"""Lift a whole build into a draft (intent) plus a report of what was not lifted.

For every enabled node that a registered *recipe* covers (looked up by the node's compatible)
the board settings of the node become one ``DraftNode``:

* class from the binding include chain (``draft.detect_class``);
* roles -> pads: the pins of the default pinctrl state are decoded with the *formatter* of the
  build's pinctrl controller (looked up by its compatible), then ``recipe.lift(props, pins)``
  turns the vendor properties plus pads into roles;
* neutral properties: written by the board or the app and declared in the class base binding;
  vendor properties: the other properties written by the board or the app, minus what the recipe
  derives again from the roles;
* use: the ``chosen`` entries and aliases that point at the node;
* children: the child nodes of the node (bus devices such as ``eeprom@5e``, ``channel@0``) that the board
  or the app wrote become ``DraftChild`` objects, recursively, with compatible, labels and the properties
  that are not from the SoC description. A child property a draft cannot hold (a path, a reference to a node
  without a label) is listed as not representable.

Only what the board or the application wrote counts. A property, chosen entry or alias that comes
from the SoC description (the origin comments of ``zephyr.dts``) belongs to the platform, not to
the board setting, and is left out.

Nothing is dropped silently. The ``LiftReport`` lists the covered nodes, every other enabled
node with its compatible, and everything that a covered node has but a draft cannot hold. Lifting
stays in the source port; no binding translation happens here.

The module knows no vendor: recipes and formatters are reached only through
``dt_explorer.core.plugins``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import plugins
from .build import BuildModel, origin_kind
from .buildinfo import BuildInfo, read_build_info
from .draft import (CLASS_BASES, BindingIndex, Draft, DraftChild, DraftNode, Use, detect_class,
                    neutral_properties, split_properties)
from .pins import _controller_compats, _formatter_for, _source_names
from .render import RenderContext
from .translate import Ref

PLATFORM_ORIGINS = ("soc", "module")      # origin kinds that are not a board or app setting
_SIMPLE_TYPES = ("int", "array", "uint8-array", "string", "string-array", "boolean")
_STRUCTURAL = ("compatible", "status")


@dataclass
class CoveredNode:
    path: str
    label: str | None
    compatible: str
    cls: str
    recipe: str
    roles: dict[str, str]                   # role -> pad
    notes: list[str] = field(default_factory=list)


@dataclass
class UncoveredNode:
    path: str
    labels: list[str]
    compatible: str
    origin: str                             # origin kind of the node: soc | board | overlay | ...
    reason: str


@dataclass
class NotRepresentable:
    path: str
    what: str
    reason: str


@dataclass
class LiftReport:
    build: str = ""
    covered: list[CoveredNode] = field(default_factory=list)
    uncovered: list[UncoveredNode] = field(default_factory=list)
    not_representable: list[NotRepresentable] = field(default_factory=list)
    children: list[str] = field(default_factory=list)       # source paths of the child nodes in the draft
    pinctrl_compatible: str | None = None
    pinctrl_label: str = "pinctrl"
    existing_compatibles: dict[str, str] = field(default_factory=dict)   # label -> platform compatible
    workspace: Path | None = None
    soc: str | None = None

    def render_context(self, device=None) -> RenderContext:
        """Context that renders the lifted draft against the source build (same port)."""
        return RenderContext(device=device, pinctrl_compatible=self.pinctrl_compatible,
                             pinctrl_label=self.pinctrl_label, workspace=self.workspace,
                             soc=self.soc, existing_compatibles=dict(self.existing_compatibles))

    def lines(self) -> list[str]:
        out = [f"lift report for {self.build}"]
        out.append(f"covered nodes ({len(self.covered)}):")
        for c in self.covered:
            out.append(f"  {c.path}  label={c.label}  {c.compatible}  class={c.cls}  roles={c.roles}")
            out += [f"    note: {n}" for n in c.notes]
        by_origin = Counter(u.origin for u in self.uncovered)
        out.append(f"uncovered enabled nodes ({len(self.uncovered)}; by origin {dict(by_origin)}):")
        for u in self.uncovered:
            out.append(f"  {u.path}  {u.compatible}  [{u.origin}]  {u.reason}")
        out.append(f"child nodes in the draft ({len(self.children)}):")
        out += [f"  {p}" for p in self.children]
        out.append(f"not representable ({len(self.not_representable)}):")
        for n in self.not_representable:
            out.append(f"  {n.path}  {n.what}: {n.reason}")
        return out


# ---- helpers --------------------------------------------------------------------------------
def _binding_index(model: BuildModel) -> BindingIndex:
    index = BindingIndex([])
    index.by_name = dict(model.yaml_index)
    return index


def _kind(info) -> str:
    """Origin kind of a PropInfo/NodeOrigin; unknown origin counts as board."""
    return origin_kind(info.file) if info is not None and info.file else "board"


def _base_for(cls: str) -> str | None:
    return next((b for b, c in CLASS_BASES.items() if c == cls), None)


def _recipe_for(compats: list[str]):
    for c in compats:
        r = plugins.get_recipe(c)
        if r is not None:
            return c, r
    return None, None


def _value(prop):
    """Draft-friendly value of an edtlib property, or None when a draft cannot hold it."""
    if prop.type not in _SIMPLE_TYPES:
        return None
    v = prop.val
    if isinstance(v, bytes):
        v = list(v)
    return v


def _raw_value(node, name: str):
    """Value of a property that edtlib does not expose (undeclared in the binding), from dtlib."""
    dt_prop = getattr(getattr(node, "_node", None), "props", {}).get(name)
    if dt_prop is None:
        return None
    kind = getattr(dt_prop.type, "name", str(dt_prop.type)).upper()
    try:
        if kind.endswith("NUM"):
            return dt_prop.to_num()
        if kind.endswith("NUMS"):
            return dt_prop.to_nums()
        if kind.endswith("STRING"):
            return dt_prop.to_string()
        if kind.endswith("STRINGS"):
            return dt_prop.to_strings()
        if kind.endswith("EMPTY"):
            return True
    except Exception:  # noqa: BLE001 - dtlib raises its own error type
        return None
    return None


def _label_of(node):
    return node.labels[0] if getattr(node, "labels", None) else None


def _child_value(prop, node, name: str):
    """Draft value of a property of a child node, or None when a draft cannot hold it.

    Phandles become ``Ref`` (by the first label of the target node), a phandle array becomes
    ``[[Ref, cell, ...], ...]``. Properties without a binding are read from dtlib.
    """
    if prop is None:
        dt_prop = getattr(getattr(node, "_node", None), "props", {}).get(name)
        kind = getattr(getattr(dt_prop, "type", None), "name", "").upper()
        try:
            if kind == "PHANDLE":
                lab = _label_of(dt_prop.to_node())
                return [Ref(lab)] if lab else None
            if kind == "PHANDLES":
                labs = [_label_of(n) for n in dt_prop.to_nodes()]
                return [Ref(x) for x in labs] if all(labs) else None
        except Exception:  # noqa: BLE001 - dtlib raises its own error type
            return None
        return _raw_value(node, name)
    if prop.type == "phandle":
        lab = _label_of(prop.val)
        return [Ref(lab)] if lab else None
    if prop.type == "phandles":
        labs = [_label_of(n) for n in prop.val]
        return [Ref(x) for x in labs] if all(labs) else None
    if prop.type == "phandle-array":
        out = []
        for entry in prop.val:
            ctrl = getattr(entry, "controller", None)
            lab = _label_of(ctrl) if ctrl is not None else None
            if not lab:
                return None
            out.append([Ref(lab), *(int(v) for v in (entry.data or {}).values())])
        return out
    return _value(prop)


def _lift_children(model: BuildModel, node, report: LiftReport) -> list[DraftChild]:
    """Child nodes of `node` that the board or the app wrote, recursively, as ``DraftChild``."""
    out: list[DraftChild] = []
    for child in node.children.values():
        origin = model.origins.get(child.path)
        written = {k: v for k, v in (origin.props if origin else {}).items()
                   if _kind(v) not in PLATFORM_ORIGINS}
        sub = _lift_children(model, child, report)
        if not written and not sub:
            continue
        props: dict = {}
        for name in written:
            if name in ("compatible", "phandle"):       # phandle numbers are made by dtc from the labels
                continue
            v = _child_value(child.props.get(name), child, name)
            if v is None:
                report.not_representable.append(NotRepresentable(
                    child.path, name, "a child property that a draft cannot hold (type, or a reference to a "
                                      "node without a label)"))
                continue
            props[name] = v
        base, _, unit = child.name.partition("@")
        out.append(DraftChild(name=base, unit=unit if "@" in child.name else None,
                              compatible=list(child.compats or []), labels=list(child.labels),
                              props=props, children=sub))
        report.children.append(child.path)
    return out


def _default_state(node):
    pcs = list(getattr(node, "pinctrls", None) or [])
    if not pcs:
        return None, []
    for pc in pcs:
        if pc.name == "default":
            return pc, pcs
    return pcs[0], pcs


def _decode_pins(model: BuildModel, info: BuildInfo, node, cache: dict, notes: list[str]):
    """(pad role -> pad, instance names, controller compatible, controller label) of pinctrl-0."""
    pc, pcs = _default_state(node)
    if pc is None:
        return {}, [], None, None
    for other in pcs:
        if other is not pc:
            notes.append(f"pinctrl state {other.name!r} is not lifted (a draft keeps the default state)")
    pads: dict[str, str] = {}
    instances: list[str] = []
    ctrl_compat = ctrl_label = None
    for conf in pc.conf_nodes:
        ctrl = _controller_compats(conf)
        ctrl_compat = ctrl_compat or (ctrl[0] if ctrl else None)
        n = conf.parent
        while n is not None and not n.compats:
            n = n.parent
        if n is not None and n.labels:
            ctrl_label = ctrl_label or n.labels[0]
        fmt = _formatter_for(ctrl, info, cache)
        for group in [conf, *conf.children.values()]:
            prop = group.props.get("pinmux")
            if prop is None:
                continue
            origin = model.origins.get(group.path)
            extra = sorted(k for k in (origin.props if origin else {}) if k != "pinmux")
            if extra:
                notes.append(f"pin group {group.name}: properties {extra} are not lifted")
            values = list(prop.val) if isinstance(prop.val, (list, tuple)) else [prop.val]
            names = _source_names(model, origin.props.get("pinmux") if origin else None)
            if fmt is None or len(names) != len(values):
                notes.append(f"pin group {group.name}: pinmux not decoded "
                             f"({'no formatter for ' + str(ctrl) if fmt is None else 'names do not match values'})")
                continue
            for name in names:
                try:
                    p = fmt.parse(name)
                except Exception as exc:  # noqa: BLE001 - formatter error types are plugin specific
                    notes.append(f"pin group {group.name}: {name}: {exc}")
                    continue
                pads[p.role] = p.pad
                instances.append(p.instance)
    return pads, instances, ctrl_compat, ctrl_label


def _use(model: BuildModel, node) -> Use:
    """chosen entries and aliases that point at `node` and were written by the board or the app."""
    chosen_o = model.origins.get("/chosen")
    alias_o = model.origins.get("/aliases")
    chosen = sorted(k for k, n in model.edt.chosen_nodes.items() if n.path == node.path
                    and _kind(chosen_o.props.get(k) if chosen_o else None) not in PLATFORM_ORIGINS)
    aliases = sorted(k for k, n in model.edt._dt.alias2node.items() if n.path == node.path
                     and _kind(alias_o.props.get(k) if alias_o else None) not in PLATFORM_ORIGINS)
    return Use(chosen=chosen, aliases=aliases)


# ---- entry point ----------------------------------------------------------------------------
def lift_build(build: Path | str | BuildModel, info: BuildInfo | None = None) -> tuple[Draft, LiftReport]:
    """Lift every enabled node that a recipe covers; list the rest in the report."""
    model = build if hasattr(build, "edt") else BuildModel(Path(build))
    info = info or read_build_info(model.build)
    index = _binding_index(model)
    draft = Draft(build=model.build.name)
    report = LiftReport(build=model.build.name, workspace=info.workspace, soc=info.soc)
    fmt_cache: dict = {}

    for node in model.edt.nodes:
        if node.status != "okay" or not node.compats:
            continue
        origin = model.origins.get(node.path)
        compat, recipe = _recipe_for(list(node.compats))
        if recipe is None:
            report.uncovered.append(UncoveredNode(
                node.path, list(node.labels), node.compats[0], _kind(origin), "no recipe"))
            continue
        cls, _chain = detect_class(compat, index)
        base = _base_for(cls) if cls else None
        if base is None:
            report.not_representable.append(NotRepresentable(
                node.path, node.compats[0], "a recipe covers it but its binding has no known class"))
            continue

        notes: list[str] = []
        pad_by_role, instances, ctrl_compat, ctrl_label = _decode_pins(model, info, node, fmt_cache, notes)
        if ctrl_compat and report.pinctrl_compatible is None:
            report.pinctrl_compatible = ctrl_compat
        if ctrl_label and report.pinctrl_label == "pinctrl" and report.pinctrl_compatible == ctrl_compat:
            report.pinctrl_label = ctrl_label

        written = {k: v for k, v in (origin.props if origin else {}).items()
                   if _kind(v) not in PLATFORM_ORIGINS}
        values: dict = {}
        for name in written:
            if name in _STRUCTURAL or name.startswith("pinctrl-"):
                continue
            prop = node.props.get(name)
            v = _value(prop) if prop is not None else _raw_value(node, name)
            if v is None:
                what = f"property type {prop.type!r}" if prop is not None else "a property undeclared in the binding"
                report.not_representable.append(NotRepresentable(
                    node.path, name, f"{what} cannot be held by a draft"))
                continue
            values[name] = v

        lift_props = {k: p.val for k, p in node.props.items() if p.type in _SIMPLE_TYPES}
        lift_props["compatible"] = list(node.compats)
        try:
            roles = recipe.lift(lift_props, pad_by_role)
            derived: set[str] = set()
            if roles:
                role_to_padrole = {pad: r for r, pad in pad_by_role.items()}
                derived = set(recipe.derived_props({r: role_to_padrole[p] for r, p in roles.items()}))
        except ValueError as exc:
            report.not_representable.append(NotRepresentable(node.path, node.compats[0], str(exc)))
            continue
        unused = sorted(set(pad_by_role.values()) - set(roles.values()))
        if unused:
            notes.append(f"pads without a role in class {cls}: {unused}")

        neutral_names = neutral_properties(base, index)
        neutral, vendor = split_properties(values, neutral_names)
        vendor = {k: v for k, v in vendor.items() if k not in derived}
        label = node.labels[0] if node.labels else None
        if label is None:
            notes.append("node has no label; the overlay cannot refer to it")
        inst = Counter(instances).most_common(1)[0][0] if instances else None
        if len(set(instances)) > 1:
            notes.append(f"pins belong to several instances: {sorted(set(instances))}")
        children = _lift_children(model, node, report)

        draft.nodes.append(DraftNode(
            cls=cls, roles=list(roles), props=neutral, use=_use(model, node), instance=inst,
            pins=dict(roles), vendor_props=vendor, compatible=compat, label=label, children=children))
        report.covered.append(CoveredNode(node.path, label, compat, cls, recipe.name, dict(roles), notes))
        compat_info = (origin.props.get("compatible") if origin else None)
        if label and (compat_info is None or _kind(compat_info) in PLATFORM_ORIGINS):
            report.existing_compatibles[label] = compat

    lifted = set(report.children)                   # a child that the draft holds is not "uncovered"
    report.uncovered = [u for u in report.uncovered if u.path not in lifted]
    return draft, report
