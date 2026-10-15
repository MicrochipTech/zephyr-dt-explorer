"""Generic binding form dialog (P8.5): configure a node whose compatible has no recipe.

The form comes from ``core.generic``: one field per scalar property of the binding chain (type, enum,
default, required flag and description as hint), pin roles from the provider's signals when the binding
takes a ``pinctrl-0`` and the device model knows the instance. Saving gives a recipe-less draft node
(class ``generic``). Ids: ``gen-prop-<name>``, ``gen-role-<role>``, ``gen-problems``, ``gen-save``.
"""
from __future__ import annotations

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


def form_state(session, node) -> tuple[str, list, list[str], list[str]]:
    """(compatible, fields, pin roles, notes) of a node's generic form (no NiceGUI)."""
    compat = generic_compatible(session, node)
    specs = G.form_spec(compat, session.index, current_values(node))
    inst = session.instance_of(node)
    roles = G.pin_roles(inst) if inst is not None and G.has_pinctrl(compat, session.index) else []
    return compat, specs, roles, G.form_notes(compat, session.index)


def _text(value) -> str:
    return "" if value is None else str(value)


def open_generic(session, node, on_saved: Callable[[DraftNode], None]) -> None:
    from nicegui import ui

    compat, specs, roles, notes = form_state(session, node)
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
