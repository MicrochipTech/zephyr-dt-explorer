"""Configurator dialog: turn a disabled peripheral node into a draft node.

Two layers:

* **Helpers without NiceGUI** (testable): which classes a node offers, which pads can carry a role
  (device model, narrowed by the recipe's valid combinations), derived properties, neutral
  properties of the class binding, building a ``DraftNode`` from the dialog state, and
  ``ConfigSession``, which holds draft, render context, rendering, Kconfig suggestions and the check
  for one build.
* **The dialog** (``open_configure``), thin glue around those helpers.

The module knows no vendor: classes come from the recipes registered for the instance, pads and
their roles from the device model, pad names are opaque text.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core import pins as pins_mod
from ..core import plugins
from ..core.buildinfo import read_build_info
from ..core.draft import (CLASS_BASES, BindingIndex, Draft, DraftNode, Use, detect_class, load_draft,
                          save_draft)
from ..core.kconfig_suggest import KconfigSuggestion, suggest_kconfig
from ..core.render import RenderContext, render_overlay
from ..core.validate import Finding, validate


# ---- helpers: classes, roles, pads (no NiceGUI) ------------------------------------------------
@dataclass
class ClassOption:
    cls: str                    # "uart" | "i2c" | "spi" | ...
    compatible: str             # compatible of the realised node
    recipe: object              # the recipe that offers it


@dataclass
class Candidate:
    pad: str
    pad_role: str               # role of the pad on the instance, as the device model names it
    mux: str
    used_by: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        text = f"{self.pad}  ({self.pad_role}, mux {self.mux})"
        return text + ("  - used by " + "; ".join(self.used_by) if self.used_by else "")


@dataclass
class PropSpec:
    name: str
    type: str                   # "int" | "string" | "boolean"
    enum: list | None = None
    default: object = None
    description: str = ""


def find_instance(device, addresses=(), labels=()):
    """Instance of the device model that a node is: by register address, else by label."""
    if device is None:
        return None
    for inst in device.instances.values():
        if inst.base is not None and inst.base in set(addresses):
            return inst
    wanted = {lb.lower() for lb in labels}
    return next((i for n, i in device.instances.items() if n.lower() in wanted), None)


def class_options(instance, index: BindingIndex, recipes=None) -> list[ClassOption]:
    """Classes the registered recipes offer for `instance`, one option per class."""
    out: list[ClassOption] = []
    for recipe in (plugins.recipes() if recipes is None else recipes):
        try:
            if not recipe.pin_roles(instance):
                continue
        except Exception:                                   # noqa: BLE001 - a broken recipe offers nothing
            continue
        for compat in recipe.compatibles:
            cls, _chain = detect_class(compat, index)
            if cls and all(o.cls != cls for o in out):
                out.append(ClassOption(cls, compat, recipe))
    return out


def option_roles(option: ClassOption, instance) -> list[str]:
    """Roles of the class in the recipe's order."""
    roles_for = getattr(option.recipe, "roles_for", None)
    if roles_for is not None:
        return list(roles_for(option.compatible))
    try:
        return list(option.recipe.pin_roles(instance, option.compatible))
    except TypeError:
        return list(option.recipe.pin_roles(instance))


def valid_assignments(option: ClassOption) -> list[dict[str, str]] | None:
    """Every valid role -> pad role assignment of the class, or None if the recipe cannot list them."""
    fn = getattr(option.recipe, "valid_assignments", None)
    return list(fn(option.cls)) if fn is not None else None


def required_roles(option: ClassOption, roles: list[str]) -> list[str]:
    """Roles that occur in every valid assignment (the rest is optional). Without a list: none."""
    allv = valid_assignments(option)
    if not allv:
        return []
    return [r for r in roles if all(r in a for a in allv)]


def pad_roles(instance, pad: str) -> list[str]:
    return sorted({s.role for s in instance.signals if s.pad == pad})


def role_candidates(instance, option: ClassOption, roles: list[str], chosen: dict[str, str],
                    used: dict[str, list[str]] | None = None) -> dict[str, list[Candidate]]:
    """For each role, the pads that can carry it given the roles already chosen.

    A pad qualifies when the instance has a signal on it whose pad role appears for this role in at
    least one valid assignment that agrees with every other chosen role. Pads chosen for another role
    are left out. Without a list of valid assignments every signal of the instance qualifies.
    """
    used = used or {}
    allv = valid_assignments(option)
    out: dict[str, list[Candidate]] = {}
    for role in roles:
        fixed = {r: set(pad_roles(instance, p)) for r, p in chosen.items() if r != role and p}
        taken = {p for r, p in chosen.items() if r != role and p}
        if allv is None:
            ok = {s.role for s in instance.signals}
        else:
            ok = {a[role] for a in allv
                  if role in a and all(a.get(r) in prs for r, prs in fixed.items())}
        cands = [Candidate(s.pad, s.role, str(s.mux), list(used.get(s.pad, [])))
                 for s in instance.signals if s.role in ok and s.pad not in taken]
        out[role] = sorted(cands, key=lambda c: (c.pad, c.pad_role))
    return out


def derive_props(option: ClassOption, instance, pins: dict[str, str],
                 roles: list[str]) -> tuple[dict, str | None]:
    """(derived properties, problem). The problem is a short text, or None when the pins are complete and valid."""
    pins = {r: p for r, p in pins.items() if p}
    missing = [r for r in required_roles(option, roles) if r not in pins]
    if missing:
        return {}, "still to assign: " + ", ".join(missing)
    if not pins:
        return {}, "no pins assigned"
    assignment = {}
    for role, pad in pins.items():
        prs = pad_roles(instance, pad)
        if not prs:
            return {}, f"{role}: pad {pad} is not on {instance.name}"
        assignment[role] = prs[0]
    try:
        return dict(option.recipe.derived_props(assignment)), None
    except ValueError as exc:
        return {}, str(exc)


def neutral_specs(option: ClassOption, index: BindingIndex) -> list[PropSpec]:
    """Writable neutral properties of the class: scalar properties of the base binding chain."""
    base = next((b for b, c in CLASS_BASES.items() if c == option.cls), None)
    start = index.by_name.get(base) if base else None
    if start is None:
        return []
    generic = {x for x in index.chain(index.by_name["base.yaml"])} if "base.yaml" in index.by_name else set()
    out: dict[str, PropSpec] = {}
    for item in index.chain(start):
        if not isinstance(item, Path) or item in generic:
            continue
        for name, spec in (index.load(item).get("properties") or {}).items():
            if (not isinstance(spec, dict) or "const" in spec or name in out
                    or spec.get("type") not in ("int", "string", "boolean")):
                continue
            out[name] = PropSpec(name, spec["type"], spec.get("enum"), spec.get("default"),
                                 (spec.get("description") or "").strip().split("\n")[0])
    return list(out.values())


def parse_prop(spec: PropSpec, text) -> object | None:
    """Value of a neutral property from the dialog (text, bool or None). None = leave unset. Raises ValueError."""
    if spec.type == "boolean":
        return True if text else None
    if text is None or str(text).strip() == "":
        return None
    if spec.type == "int":
        return int(str(text).strip(), 0)
    return str(text).strip()


def build_node(*, option: ClassOption, instance, label: str, pins: dict[str, str], roles_order: list[str],
               props: dict | None = None, chosen=(), aliases=()) -> DraftNode:
    """The draft node for the dialog state. Roles without a pad are dropped."""
    pins = {r: p for r, p in pins.items() if p}
    return DraftNode(
        cls=option.cls, roles=[r for r in roles_order if r in pins],
        props={k: v for k, v in (props or {}).items() if v is not None},
        use=Use(chosen=[c for c in chosen if c], aliases=[a for a in aliases if a]),
        instance=instance.name, pins={r: pins[r] for r in roles_order if r in pins},
        vendor_props={}, compatible=option.compatible, label=label)


# ---- session: draft, rendering, Kconfig, check for one build -----------------------------------
_check_lock = threading.Lock()      # validate() shares an unsynchronised binding-read cache


class ConfigSession:
    """Everything the configurator knows about one build."""

    def __init__(self, model):
        self.model = model
        self.info = read_build_info(model.build)
        self.usage = pins_mod.used_pins(model, self.info)
        self.device = self.usage.device
        self.index = BindingIndex([])
        self.index.by_name = dict(model.yaml_index)
        self.draft = load_draft(model.app_dir, model.build) or Draft(build=model.build.name)
        self.findings: list[Finding] | None = None
        self.checked_overlay: str | None = None

    # -- node lookup
    def node_for_label(self, label: str):
        return next((n for n in self.model.edt.nodes if label in n.labels), None)

    def instance_of(self, node):
        return find_instance(self.device, [r.addr for r in node.regs], node.labels)

    def options_for(self, node) -> list[ClassOption]:
        inst = self.instance_of(node)
        return class_options(inst, self.index) if inst is not None else []

    def draft_node(self, label: str) -> DraftNode | None:
        return next((n for n in self.draft.nodes if n.label == label), None)

    # -- who uses a pad
    def used_by(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for pad, uses in self.usage.uses.items():
            names = []
            for u in uses:
                n = self.model.by_path.get(u.node)
                names.append(f"{n.labels[0] if n and n.labels else u.node} ({u.function})")
            out[pad] = list(dict.fromkeys(names))
        return out

    def draft_pads(self, skip_label: str | None = None) -> dict[str, list[str]]:
        """pad -> descriptions of the draft nodes that take it."""
        out: dict[str, list[str]] = {}
        for n in self.draft.nodes:
            if n.label == skip_label:
                continue
            for role, pad in n.pins.items():
                out.setdefault(pad, []).append(f"{n.label} {role} (draft)")
        return out

    # -- draft
    def upsert(self, node: DraftNode) -> None:
        self.draft.nodes = [n for n in self.draft.nodes if n.label != node.label] + [node]
        self.findings, self.checked_overlay = None, None

    def remove(self, label: str) -> None:
        self.draft.nodes = [n for n in self.draft.nodes if n.label != label]
        self.findings, self.checked_overlay = None, None

    def save(self) -> Path:
        return save_draft(self.draft, self.model.app_dir, self.model.build)

    # -- render, Kconfig, check
    def render_context(self) -> RenderContext:
        pin_node = next((n for n in self.model.edt.nodes
                         if any(plugins.get_formatter(c) for c in n.compats)), None)
        return RenderContext(
            device=self.device, pinctrl_compatible=next(
                (c for c in (pin_node.compats if pin_node else []) if plugins.get_formatter(c)), None),
            pinctrl_label=(pin_node.labels[0] if pin_node and pin_node.labels else "pinctrl"),
            workspace=self.info.workspace, soc=self.info.soc, bindings=self.index)

    def overlay(self) -> str:
        return render_overlay(self.draft, self.render_context()) if self.draft.nodes else ""

    def kconfig(self) -> list[KconfigSuggestion]:
        compats = list(dict.fromkeys(n.compatible for n in self.draft.nodes if n.compatible))
        return [suggest_kconfig(c, self.model.build) for c in compats]

    def check(self) -> list[Finding]:
        """Run validate() on the current overlay. Blocking (1.5-3 s): call from a worker thread."""
        text = self.overlay()
        with _check_lock:
            findings = validate(self.model.build, text) if text else []
        self.findings, self.checked_overlay = findings, text
        return findings


_sessions: dict[str, ConfigSession] = {}


def session_for(model) -> ConfigSession:
    s = _sessions.get(str(model.build))
    if s is None or s.model is not model:
        s = _sessions[str(model.build)] = ConfigSession(model)
    return s


def draft_count(model) -> int:
    """Number of draft nodes of a build without building a session."""
    s = _sessions.get(str(model.build))
    if s is not None and s.model is model:
        return len(s.draft.nodes)
    d = load_draft(model.app_dir, model.build)
    return len(d.nodes) if d else 0


def can_configure(session: ConfigSession, node) -> bool:
    return node.status == "disabled" and bool(session.options_for(node))


# ---- the dialog ---------------------------------------------------------------------------------
def open_configure(session: ConfigSession, node, on_saved: Callable[[DraftNode], None]) -> None:
    """Open the configurator for a (disabled) node; `on_saved(draft_node)` runs after Save."""
    from nicegui import ui

    inst = session.instance_of(node)
    options = session.options_for(node)
    label = node.labels[0] if node.labels else node.name
    existing = session.draft_node(label)
    used = session.used_by()
    for pad, names in session.draft_pads(skip_label=label).items():
        used.setdefault(pad, []).extend(names)
    st = {"opt": next((o for o in options if existing and o.cls == existing.cls), options[0]),
          "busy": False}
    selects: dict[str, object] = {}
    prop_inputs: dict[str, tuple[PropSpec, object]] = {}

    with ui.dialog() as dlg, ui.card().style("min-width: 640px; max-width: 760px").props("id=cfg-dialog"):
        ui.label(f"Configure &{label}").classes("text-lg font-bold")
        ui.label(f"{node.path}  -  instance {inst.name}").classes("text-xs text-grey-5 font-mono")
        cls_toggle = ui.toggle({o.cls: o.cls.upper() for o in options}, value=st["opt"].cls) \
            .props("id=cfg-class")
        ui.label("Pads").classes("font-bold")
        roles_box = ui.column().classes("w-full gap-1")
        derived = ui.label().classes("text-sm font-mono").props("id=cfg-derived")
        ui.label("Properties").classes("font-bold")
        props_box = ui.column().classes("w-full gap-1")
        ui.label("Use").classes("font-bold")
        chosen_in = ui.select([], multiple=True, new_value_mode="add-unique", label="chosen (e.g. zephyr,console)",
                              value=list(existing.use.chosen) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        alias_in = ui.select([], multiple=True, new_value_mode="add-unique", label="aliases (e.g. my-i2c)",
                             value=list(existing.use.aliases) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
            save_btn = ui.button("Save", icon="save").props("no-caps id=cfg-save")

    def current_pins() -> dict[str, str]:
        return {r: s.value for r, s in selects.items() if s.value}

    def refresh_roles() -> None:
        if st["busy"]:
            return
        st["busy"] = True
        try:
            opt = st["opt"]
            roles = option_roles(opt, inst)
            for _ in range(len(roles)):             # settle: drop choices that no longer fit
                cands = role_candidates(inst, opt, roles, current_pins(), used)
                stale = [r for r, s in selects.items() if s.value and
                         s.value not in {c.pad for c in cands[r]}]
                if not stale:
                    break
                for r in stale:
                    selects[r].value = None
            for r, s in selects.items():
                s.options = {c.pad: c.label for c in cands[r]}
                s.update()
            props, problem = derive_props(opt, inst, current_pins(), roles)
            if problem:
                derived.text = "Derived properties: " + problem
                derived.classes(replace="text-sm font-mono text-orange-4")
            else:
                derived.text = "Derived properties: " + (
                    ", ".join(f"{k} = {v}" for k, v in props.items()) or "none")
                derived.classes(replace="text-sm font-mono text-green-4")
            save_btn.set_enabled(problem is None)
        finally:
            st["busy"] = False

    def build_class_form() -> None:
        opt = st["opt"]
        roles = option_roles(opt, inst)
        req = set(required_roles(opt, roles))
        roles_box.clear()
        selects.clear()
        start = dict(existing.pins) if existing and existing.cls == opt.cls else {}
        cands = role_candidates(inst, opt, roles, start, used)
        with roles_box:
            for r in roles:
                options_r = {c.pad: c.label for c in cands[r]}
                sel = ui.select(options_r, label=r + ("" if r in req else " (optional)"), clearable=True,
                                value=(start.get(r) if start.get(r) in options_r else None)) \
                    .props(f"dense outlined options-dense id=cfg-role-{r}").classes("w-full")
                sel.on_value_change(lambda _e: refresh_roles())
                selects[r] = sel
        props_box.clear()
        prop_inputs.clear()
        with props_box:
            for spec in neutral_specs(opt, session.index):
                cur = existing.props.get(spec.name) if existing and existing.cls == opt.cls else None
                hint = spec.description + (f" (default {spec.default})" if spec.default is not None else "")
                if spec.type == "boolean":
                    w = ui.switch(spec.name, value=bool(cur)).props("dense")
                elif spec.enum:
                    w = ui.select([""] + [str(x) for x in spec.enum], label=spec.name,
                                  value="" if cur is None else str(cur)).props("dense outlined clearable") \
                        .classes("w-full")
                else:
                    w = ui.input(spec.name, value="" if cur is None else str(cur)) \
                        .props(f"dense outlined stack-label id=cfg-prop-{spec.name}").classes("w-full")
                if spec.type != "boolean":
                    w.tooltip(hint)
                prop_inputs[spec.name] = (spec, w)
        refresh_roles()

    def on_class(_e=None) -> None:
        st["opt"] = next(o for o in options if o.cls == cls_toggle.value)
        build_class_form()

    cls_toggle.on_value_change(on_class)

    def save() -> None:
        opt = st["opt"]
        props = {}
        for name, (spec, w) in prop_inputs.items():
            try:
                v = parse_prop(spec, w.value)
            except ValueError:
                ui.notify(f"{name}: not a valid {spec.type}", type="negative")
                return
            if v is not None and spec.type == "int" and spec.enum:
                v = int(v)
            if v is not None:
                props[name] = v
        dn = build_node(option=opt, instance=inst, label=label, pins=current_pins(),
                        roles_order=option_roles(opt, inst), props=props,
                        chosen=chosen_in.value or [], aliases=alias_in.value or [])
        session.upsert(dn)
        session.save()
        dlg.close()
        on_saved(dn)

    save_btn.on_click(save)
    build_class_form()
    dlg.open()
