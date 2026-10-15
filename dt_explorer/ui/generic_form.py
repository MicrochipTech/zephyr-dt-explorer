"""Generic binding form dialog (P8.5): configure a node whose compatible has no recipe.

The form comes from ``core.generic``: one field per scalar property of the binding chain (type, enum,
default, required flag and description as hint), pin roles from the provider's signals when the binding
takes a ``pinctrl-0`` and the device model knows the instance. Saving gives a recipe-less draft node
(class ``generic``). Ids: ``gen-prop-<name>``, ``gen-role-<role>``, ``gen-problems``, ``gen-save``.
"""
from __future__ import annotations

import re
from typing import Callable

from ..core import generic as G
from ..core.draft import DraftNode
from . import configure as C

SCALARS = ("int", "string", "boolean")


def current_values(node) -> dict:
    """Scalar property values the node has now (from edtlib), by name."""
    out = {}
    for name, p in node.props.items():
        t = getattr(p.spec, "type", None)
        if t in SCALARS and p.val is not None:
            out[name] = p.val
    return out


def generic_compatible(session, node) -> str | None:
    """First compatible of `node` that has a binding in the build, or None."""
    for c in node.compats:
        if session.index.find(c) is not None:
            return c
    return None


def generic_available(session, node) -> bool:
    """A node without a recipe option but with a binding: the generic form applies."""
    return bool(generic_compatible(session, node)) and not session.options_for(node)


def form_state(session, node, compat: str | None = None) -> tuple[str, list, list[str], list[str]]:
    """(compatible, fields, pin roles, notes) of a node's generic form (no NiceGUI).

    `compat`: another compatible of the peripheral (a mode the recipe does not cover); default: the node's own.
    ``#...-cells`` properties are fixed by the binding and not fields.
    """
    compat = compat or generic_compatible(session, node)
    specs = [f for f in G.form_spec(compat, session.index, current_values(node)) if not C.is_cells_prop(f.name)]
    inst = session.instance_of(node)
    roles = G.pin_roles(inst) if inst is not None and G.has_pinctrl(compat, session.index) else []
    return compat, specs, roles, G.form_notes(compat, session.index)


def _text(value) -> str:
    return "" if value is None else str(value)


def open_generic(session, node, on_saved: Callable[[DraftNode], None], compat: str | None = None) -> None:
    from nicegui import ui

    compat, specs, roles, notes = form_state(session, node, compat)
    inst = session.instance_of(node)
    label = node.labels[0] if node.labels else node.name
    existing = session.draft_node(label)
    existing = existing if existing is not None and G.is_generic(existing) else None
    used = session.used_by(skip_path=node.path)
    for pad, names in session.draft_pads(skip_label=label).items():
        used.setdefault(pad, []).extend(names)
    widgets: dict[str, tuple[G.FieldSpec, object]] = {}
    selects: dict[str, object] = {}

    with ui.dialog() as dlg, ui.card().style("min-width: 640px; max-width: 760px; max-height: 92vh; overflow-y: auto").props("id=cfg-dialog"):
        ui.label(f"Configure &{label}").classes("text-lg font-bold")
        ui.label(f"{node.path}  -  {compat}  -  no recipe: generic form from the binding") \
            .classes("text-xs text-grey-5 font-mono")
        problems_lbl = ui.label().classes("text-sm text-orange-4").props("id=gen-problems")
        if roles:
            ui.label("Pads (optional)").classes("font-bold")
            roles_box = ui.column().classes("w-full gap-1")
        ui.label("Properties").classes("font-bold")
        props_box = ui.column().classes("w-full gap-1")
        if not specs:
            ui.label("The binding has no editable scalar property: Save enables the node.") \
                .classes("text-sm text-grey-5").props("id=gen-empty")
        if notes:
            ui.label("Not editable here (stay as the board file has them): " + ", ".join(notes)) \
                .classes("text-xs text-grey-6").props("id=gen-notes")
        ui.label("Use").classes("font-bold")
        chosen_in = ui.select(list(existing.use.chosen) if existing else [], multiple=True,
                              new_value_mode="add-unique", label="chosen (e.g. zephyr,entropy)",
                              value=list(existing.use.chosen) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        alias_in = ui.select(list(existing.use.aliases) if existing else [], multiple=True,
                             new_value_mode="add-unique", label="aliases (e.g. my-counter)",
                             value=list(existing.use.aliases) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
            save_btn = ui.button("Save", icon="save").props("no-caps id=gen-save")

    def texts() -> dict:
        return {n: w.value for n, (_s, w) in widgets.items()}

    def pins() -> dict[str, str]:
        return {r: s.value for r, s in selects.items() if s.value}

    def refresh(_e=None) -> None:
        errs = G.check_form(specs, texts())
        for name, (_spec, w) in widgets.items():
            if not isinstance(w, ui.switch):
                w.validate(return_result=False)   # NiceGUI shows the message under the field
        perr = G.pin_problems(inst, pins()) if inst is not None else {}
        for r, s in selects.items():
            chosen = pins()
            s.options = {pad: C.Candidate(pad, r, mux, used.get(pad, [])).label
                         for pad, mux in G.pad_candidates(inst, r, chosen)}
            s.update()
        lines = [f"{n}: {m}" for n, m in {**errs, **perr}.items()]
        problems_lbl.text = "To fix: " + "; ".join(lines) if lines else ""
        save_btn.set_enabled(not lines)

    with props_box:
        for spec in specs:
            cur = existing.props.get(spec.name, spec.current) if existing else spec.current
            tag = ("required" if spec.required else "optional") + f", {spec.type}" + \
                  (f", default {spec.default}" if spec.default is not None else "")
            hint = f"{spec.description}  [{tag}]".strip()
            title = spec.name + (" *" if spec.required else "")
            if spec.type == "boolean":
                w = ui.switch(title, value=bool(cur)).props(f"dense id=gen-prop-{spec.name}")
            elif spec.enum:
                w = ui.select([""] + [str(x) for x in spec.enum], label=title, value=_text(cur)) \
                    .props(f"dense outlined options-dense id=gen-prop-{spec.name}").classes("w-full")
            else:
                w = ui.input(title, value=_text(cur)) \
                    .props(f"dense outlined stack-label id=gen-prop-{spec.name}").classes("w-full")
            if spec.type != "boolean":
                w.validation = lambda v, n=spec.name: G.check_form(specs, {**texts(), n: v}).get(n)
            ui.label(hint).classes("text-xs text-grey-6").style("margin-top:-6px")
            w.on_value_change(refresh)
            widgets[spec.name] = (spec, w)
    if roles:
        start = dict(existing.pins) if existing else {}
        with roles_box:
            for r in roles:
                opts = {pad: C.Candidate(pad, r, mux, used.get(pad, [])).label
                        for pad, mux in G.pad_candidates(inst, r, start)}
                s = ui.select(opts, label=f"{r} (optional)", clearable=True,
                              value=start.get(r) if start.get(r) in opts else None) \
                    .props(f"dense outlined options-dense id=gen-role-{r}").classes("w-full")
                s.on_value_change(refresh)
                selects[r] = s

    def save() -> None:
        if G.check_form(specs, texts()):
            return
        dn = G.build_generic_node(
            label=label, compatible=compat, instance=inst.name if inst is not None else None,
            props=G.form_props(specs, texts()), pins=pins(), roles_order=roles,
            chosen=chosen_in.value or [], aliases=alias_in.value or [])
        session.upsert(dn)
        session.save()
        dlg.close()
        on_saved(dn)

    save_btn.on_click(save)
    refresh()
    dlg.open()


# ---- child device on a bus (P8.6) ---------------------------------------------------------------
def pad_options(gpio_map: dict, taken: dict[str, list[str]]) -> dict[str, str]:
    """pad -> select label for every pad that has a GPIO controller; users of the pad are named (not excluded)."""
    def key(p: str):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)]
    return {p: p + (f"  - used by {'; '.join(taken[p])}" if taken.get(p) else "") for p in sorted(gpio_map, key=key)}


def open_child(session, *, bus: str, parent_label: str, children: list, cs_len: int,
               taken: Callable[[], dict[str, list[str]]], on_added: Callable[[object, list | None], None]) -> None:
    """Dialog "Add child device" for a bus controller. ``on_added(child, cs_entry)`` runs after Add.

    `children`: the parent's children as the dialog has them (sibling names, address clashes). `cs_len`: number of
    ``cs-gpios`` entries the parent has now. `taken`: pad -> users, asked when the dialog opens. Ids:
    ``child-compatible``, ``child-address``, ``child-address-hint``, ``child-prop-<name>``, ``child-gpio-<prop>``
    (+ ``-flags``), ``child-cs-pad`` / ``child-cs-flags`` (SPI), ``child-problems``, ``child-save``, ``child-cancel``.
    """
    from nicegui import ui

    options = G.child_options(session.index, bus)
    gmap = session.gpio_map()
    pads = pad_options(gmap, taken())
    compat_labels = {o.compatible: (o.compatible + (" - " + o.description if o.description else ""))[:110]
                     for o in options}
    st: dict = {"specs": [], "gpios": [], "widgets": {}, "gsel": {}, "gflag": {}}
    addr_label = "I2C address (hex, e.g. 76)" if bus == "i2c" else "chip-select index"

    with ui.dialog() as dlg, ui.card().style("min-width: 640px; max-width: 760px; max-height: 92vh; overflow-y: auto") \
            .props("id=child-dialog"):
        ui.label(f"Add {bus.upper()} device to &{parent_label}").classes("text-lg font-bold")
        ui.label(f"{len(options)} bindings with on-bus: {bus}").classes("text-xs text-grey-5")
        compat_in = ui.select(compat_labels, label="Device (compatible)", with_input=True, clearable=True, value=None) \
            .props("dense outlined options-dense id=child-compatible").classes("w-full")
        addr_in = ui.input(addr_label, value="" if bus == "i2c" else str(cs_len)) \
            .props("dense outlined stack-label id=child-address").classes("w-full")
        addr_hint = ui.label().classes("text-xs text-orange-4").props("id=child-address-hint")
        if bus == "spi":
            ui.label("Chip select (cs-gpios of the controller, optional)").classes("font-bold")
            with ui.row().classes("w-full items-center"):
                cs_sel = ui.select(pads, label="CS pad", clearable=True, value=None, with_input=True) \
                    .props("dense outlined options-dense id=child-cs-pad").classes("col")
                cs_flag = ui.select(list(G.GPIO_FLAGS), label="level", value="active low") \
                    .props("dense outlined options-dense id=child-cs-flags").style("width: 9rem")
        form_box = ui.column().classes("w-full gap-1")
        problems = ui.label().classes("text-sm text-orange-4").props("id=child-problems")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps id=child-cancel")
            save_btn = ui.button("Add", icon="add").props("no-caps id=child-save")

    def texts() -> dict:
        return {n: w.value for n, (_s, w) in st["widgets"].items()}

    def gpio_pads() -> dict[str, str | None]:
        return {n: s.value for n, s in st["gsel"].items()}

    def address() -> int | None:
        try:
            return G.parse_address(bus, addr_in.value)
        except ValueError:
            return None

    def refresh(_e=None) -> None:
        errs = G.check_child(bus, compat_in.value, addr_in.value, st["specs"], texts(), st["gpios"], gpio_pads())
        for _n, (_s, w) in st["widgets"].items():
            if not isinstance(w, ui.switch):
                w.validate(return_result=False)
        a = address()
        clash = G.address_clashes(children, a) if a is not None else []
        addr_hint.text = (f"{'address' if bus == 'i2c' else 'chip select'} {a:#x} is already used by "
                          f"{', '.join(clash)}: Check will report an error") if clash else ""
        if bus == "spi" and cs_sel.value and a is not None and a > cs_len:
            errs["cs"] = f"chip select {a} needs cs-gpios entries 0..{a - 1} first"
        lines = [f"{n}: {m}" for n, m in errs.items()]
        problems.text = "To fix: " + "; ".join(lines) if lines else ""
        save_btn.set_enabled(not lines)

    def build_form(_e=None) -> None:
        form_box.clear()
        st.update(specs=[], gpios=[], widgets={}, gsel={}, gflag={})
        compat = compat_in.value
        if compat:
            st["specs"] = G.child_form(compat, session.index, bus)
            st["gpios"] = G.gpio_specs(compat, session.index, bus)
            with form_box:
                for g in st["gpios"]:
                    with ui.row().classes("w-full items-center"):
                        sel = ui.select(pads, label=g.name + (" *" if g.required else " (optional)"), clearable=True,
                                        value=None, with_input=True) \
                            .props(f"dense outlined options-dense id=child-gpio-{g.name}").classes("col")
                        fl = ui.select(list(G.GPIO_FLAGS), label="level", value="active high") \
                            .props(f"dense outlined options-dense id=child-gpio-{g.name}-flags").style("width: 9rem")
                        sel.on_value_change(refresh)
                        st["gsel"][g.name], st["gflag"][g.name] = sel, fl
                    if g.description:
                        ui.label(g.description).classes("text-xs text-grey-6").style("margin-top:-6px")
                for spec in st["specs"]:
                    tag = ("required" if spec.required else "optional") + f", {spec.type}" + \
                          (f", default {spec.default}" if spec.default is not None else "")
                    title = spec.name + (" *" if spec.required else "")
                    if spec.type == "boolean":
                        w = ui.switch(title, value=False).props(f"dense id=child-prop-{spec.name}")
                    elif spec.enum:
                        w = ui.select([""] + [str(x) for x in spec.enum], label=title, value="") \
                            .props(f"dense outlined options-dense id=child-prop-{spec.name}").classes("w-full")
                    else:
                        w = ui.input(title, value="").props(f"dense outlined stack-label id=child-prop-{spec.name}") \
                            .classes("w-full")
                    if spec.type != "boolean":
                        w.validation = lambda v, n=spec.name: G.check_form(st["specs"], {**texts(), n: v}).get(n)
                    ui.label(f"{spec.description}  [{tag}]".strip()).classes("text-xs text-grey-6") \
                        .style("margin-top:-6px")
                    w.on_value_change(refresh)
                    st["widgets"][spec.name] = (spec, w)
        refresh()

    def save() -> None:
        if G.check_child(bus, compat_in.value, addr_in.value, st["specs"], texts(), st["gpios"], gpio_pads()):
            return
        a = G.parse_address(bus, addr_in.value)
        gprops = {n: G.gpio_entry(gmap, p, G.GPIO_FLAGS[st["gflag"][n].value])
                  for n, p in gpio_pads().items() if p}
        child = G.build_child(bus=bus, compatible=compat_in.value, address=a,
                              props=G.form_props(st["specs"], texts()), gpio_props=gprops, siblings=children)
        cs = None
        if bus == "spi" and cs_sel.value:
            cs = G.gpio_entry(gmap, cs_sel.value, G.GPIO_FLAGS[cs_flag.value])
        dlg.close()
        on_added(child, cs)

    compat_in.on_value_change(build_form)
    addr_in.on_value_change(refresh)
    if bus == "spi":
        cs_sel.on_value_change(refresh)
    save_btn.on_click(save)
    build_form()
    dlg.open()
