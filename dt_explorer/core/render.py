"""Render a draft node into a devicetree overlay snippet.

The core asks the plugin registry; it knows no vendor:

* the *recipe* is looked up by the compatible of the node (``get_recipe``) and supplies the
  derived properties (``derived_props``: pad roles of the chosen signals -> property values);
* the *formatter* is looked up by the compatible of the build's pinctrl node
  (``get_formatter``) and writes the pinctrl group (``render_group``);
* the neutral device model supplies, for each chosen pad of the instance, the ``Signal``
  (pad role and mux) the formatter needs.

Result for one node (parts that do not apply are left out)::

    &pinctrl {
        <name>_default: <name>_default { group1 { pinmux = <...>; }; };
    };

    &<label> {
        status = "okay";
        compatible = "...";            // only if the node does not have it yet (ctx)
        <neutral props>; <derived props>; <vendor props>;
        pinctrl-0 = <&<name>_default>;
        pinctrl-names = "default";
    };

    / { chosen { zephyr,console = &<label>; }; aliases { my-uart = &<label>; }; };

Child nodes of a draft node (``DraftNode.children``) are written inside the node override, after its
properties, with their labels, compatible, ``reg`` (hex) and properties; ``#address-cells = <1>`` and
``#size-cells = <0>`` are added when the node has children with a unit address and neither the
properties nor the binding give them::

    &<label> {
        ...
        <child label>: <name>@<unit> { compatible = "..."; reg = <0x5e>; };
    };

For a whole ``Draft`` the pinctrl groups share one ``&pinctrl`` block, then come the node
overrides, then one ``/`` block.
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from pathlib import Path

from . import plugins
from .device import DeviceModel, Signal
from .draft import Draft, DraftChild, DraftNode
from .translate import Ref, render_node, render_property


class RenderError(ValueError):
    """The draft cannot be rendered (missing plugin, pad not on the instance, invalid roles)."""


@dataclass
class RenderContext:
    device: DeviceModel | None = None           # neutral device model: pad -> Signal per instance
    pinctrl_compatible: str | None = None       # compatible of the build's pinctrl node
    pinctrl_label: str = "pinctrl"              # its label
    formatter: object | None = None             # explicit formatter; overrides the registry
    workspace: Path | None = None               # with `soc`: formatter.for_workspace() if it has one
    soc: str | None = None
    existing_compatibles: dict[str, str] = field(default_factory=dict)  # label -> compatible now
    bindings: object | None = None              # draft.BindingIndex; adds `const` properties of the binding chain


def _formatter(ctx: RenderContext):
    if ctx.formatter is not None:
        return ctx.formatter
    if not ctx.pinctrl_compatible:
        raise RenderError("pins need a pinctrl formatter: ctx.pinctrl_compatible is not set")
    fmt = plugins.get_formatter(ctx.pinctrl_compatible)
    if fmt is None:
        raise RenderError(f"no formatter registered for pinctrl compatible {ctx.pinctrl_compatible!r}")
    make = getattr(type(fmt), "for_workspace", None)
    if make is not None and ctx.workspace is not None and ctx.soc:
        return make(Path(ctx.workspace), ctx.soc)
    return fmt


def _node_label(node: DraftNode) -> str:
    label = node.label or (node.instance or "").lower()
    if not label:
        raise RenderError("draft node has neither label nor instance")
    return label


def _signals(node: DraftNode, ctx: RenderContext) -> list[tuple[str, Signal]]:
    """(role, Signal) of every chosen pad, in role order, each Signal tagged with its instance name."""
    if ctx.device is None:
        raise RenderError("pins need a device model: ctx.device is not set")
    inst = next((i for n, i in ctx.device.instances.items()
                 if n.lower() == (node.instance or "").lower()), None)
    if inst is None:
        raise RenderError(f"instance {node.instance!r} is not in device {ctx.device.name}")
    roles = [r for r in node.roles if r in node.pins] + [r for r in node.pins if r not in node.roles]
    out = []
    for role in roles:
        pad = node.pins[role]
        hits = [s for s in inst.signals if s.pad == pad]
        if not hits:
            raise RenderError(f"{role}: pad {pad} is not a signal of {inst.name}")
        if len(hits) > 1:
            raise RenderError(f"{role}: pad {pad} offers {len(hits)} signals on {inst.name} "
                              f"({', '.join(str(s.mux) + '/' + s.role for s in hits)})")
        sig = copy(hits[0])
        sig.attrs = {**sig.attrs, "instance": inst.name}
        out.append((role, sig))
    return out


def const_props(compatible: str | None, index) -> dict:
    """Properties that the binding chain of `compatible` declares with a ``const`` value.

    A node needs them (e.g. the cell counts of a bus controller) but the user never chooses them.
    `index` is a ``draft.BindingIndex``; without one, or for an unknown compatible, the result is empty.
    """
    if index is None or not compatible:
        return {}
    start = index.find(compatible)
    if start is None:
        return {}
    out: dict = {}
    for item in index.chain(start):
        if not isinstance(item, Path):
            continue
        for name, spec in (index.load(item).get("properties") or {}).items():
            if (isinstance(spec, dict) and "const" in spec and name not in out
                    and name not in ("compatible", "status")):
                out[name] = spec["const"]
    return out


def _indent(text: str, tabs: int) -> str:
    pad = "\t" * tabs
    return "\n".join(pad + ln if ln else ln for ln in text.splitlines())


def _prop_line(name: str, value) -> str | None:
    if name == "reg" and isinstance(value, list) and value and all(
            isinstance(v, int) and not isinstance(v, bool) for v in value):
        return "reg = <" + " ".join(hex(int(v)) for v in value) + ">;"
    return render_property(name, value)


def render_child(child: DraftChild) -> str:
    """One child node as devicetree text (children nested, one tab per level)."""
    head = " ".join([f"{lab}:" for lab in child.labels] + [child.node_name])
    lines: list[str] = []
    if child.compatible:
        lines.append(render_property("compatible", list(child.compatible)))
    for name, value in child.props.items():
        if name == "compatible":
            continue
        line = _prop_line(name, value)
        if line:
            lines.append(line)
    body = "".join(f"\t{ln}\n" for ln in lines)
    kids = "\n\n".join(_indent(render_child(c), 1) for c in child.children)
    if kids:
        body += ("\n" if lines else "") + kids + "\n"
    return f"{head} {{\n{body}}};"


def _with_children(text: str, children: list[DraftChild]) -> str:
    """Insert the child nodes before the closing ``};`` of a node override."""
    kids = "\n\n".join(_indent(render_child(c), 1) for c in children)
    return text[:-2].rstrip("\n") + "\n\n" + kids + "\n};"


def _render_one(node: DraftNode, ctx: RenderContext) -> tuple[str | None, str, list[str], list[str]]:
    """(pinctrl group node or None, node override, chosen lines, alias lines)."""
    label = _node_label(node)
    recipe = plugins.get_recipe(node.compatible) if node.compatible else None
    props: dict = {"status": "okay"}
    if node.compatible and ctx.existing_compatibles.get(label) != node.compatible:
        props["compatible"] = node.compatible
    props.update(const_props(node.compatible, ctx.bindings))
    props.update(node.props)

    group = None
    pairs = _signals(node, ctx) if node.pins else []
    sigs = [s for _, s in pairs]
    if recipe is not None and pairs:
        assignment = {role: sig.role for role, sig in pairs}
        try:
            props.update(recipe.derived_props(assignment))
        except ValueError as exc:
            raise RenderError(f"{label}: {exc}") from exc
    derived = set(props)
    for k, v in node.vendor_props.items():
        if k not in derived:
            props[k] = v
    if any(c.unit is not None for c in node.children):      # a child with a unit address needs the cells
        props.setdefault("#address-cells", 1)
        props.setdefault("#size-cells", 0)
    if sigs:
        fmt = _formatter(ctx)
        name = f"{label}_{node.cls}_default"
        body = _indent(fmt.render_group(sigs), 2)
        group = f"\t{name}: {name} {{\n{body}\n\t}};"
        props["pinctrl-0"] = [[Ref(name)]]
        props["pinctrl-names"] = "default"
    text = render_node(f"&{label}", props)
    if node.children:
        text = _with_children(text, node.children)
    chosen = [f"{c} = &{label};" for c in node.use.chosen]
    aliases = [f"{a} = &{label};" for a in node.use.aliases]
    return group, text, chosen, aliases


def render_overlay(target: Draft | DraftNode, ctx: RenderContext) -> str:
    """Overlay text for one draft node or a whole draft."""
    nodes = target.nodes if isinstance(target, Draft) else [target]
    groups: list[str] = []
    blocks: list[str] = []
    chosen: list[str] = []
    aliases: list[str] = []
    for n in nodes:
        g, text, c, a = _render_one(n, ctx)
        if g:
            groups.append(g)
        blocks.append(text)
        chosen += c
        aliases += a
    parts: list[str] = []
    if groups:
        parts.append(f"&{ctx.pinctrl_label} {{\n" + "\n\n".join(groups) + "\n};")
    parts += blocks
    if chosen or aliases:
        top = []
        if chosen:
            top.append("\tchosen {\n" + "".join(f"\t\t{c}\n" for c in chosen) + "\t};")
        if aliases:
            top.append("\taliases {\n" + "".join(f"\t\t{a}\n" for a in aliases) + "\t};")
        parts.append("/ {\n" + "\n".join(top) + "\n};")
    return "\n\n".join(parts) + "\n"
