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

import copy
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core import generic as G
from ..core import pins as pins_mod
from ..core import plugins
from ..core.buildinfo import read_build_info
from ..core.draft import (CLASS_BASES, BindingIndex, Draft, DraftChild, DraftNode, Use, detect_class, load_draft,
                          save_draft)
from ..core.lift import lift_build
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
    required: bool = False      # the recipe says the driver needs it; `default` is pre-filled


def find_instance(device, addresses=(), labels=()):
    """Instance of the device model that a node is: by register address, else by label."""
    if device is None:
        return None
    for inst in device.instances.values():
        if inst.base is not None and inst.base in set(addresses):
            return inst
    wanted = {lb.lower() for lb in labels}
    return next((i for n, i in device.instances.items() if n.lower() in wanted), None)


def _family(compat: str) -> str:
    """Vendor prefix of a compatible (text before the first comma)."""
    return compat.split(",", 1)[0]


def class_options(instance, index: BindingIndex, recipes=None, prefer=()) -> list[ClassOption]:
    """Classes the registered recipes offer for `instance`, one option per class.

    `prefer`: compatibles the node already has. Within a recipe, the compatibles of the same family (vendor
    prefix) as one of them come first, so a node of the new port gets the new-port compatible and a node of
    the old port the old one; without a hint the recipe's own order stands.
    """
    out: list[ClassOption] = []
    fams = {_family(c) for c in prefer}
    for recipe in (plugins.recipes() if recipes is None else recipes):
        try:
            if not recipe.pin_roles(instance):
                continue
        except Exception:                                   # noqa: BLE001 - a broken recipe offers nothing
            continue
        compats = sorted(recipe.compatibles, key=lambda c: _family(c) not in fams)   # stable
        for compat in compats:
            cls, _chain = detect_class(compat, index)
            if cls and all(o.cls != cls for o in out):
                out.append(ClassOption(cls, compat, recipe))
    return out


def generic_alternatives(session, node) -> list[str]:
    """Compatibles of the same peripheral that no recipe covers but whose binding exists: other modes.

    The node's own compatibles and the bindings that include the binding of one of them (a counter mode
    ``...-counter`` next to the PWM mode of the same timer); compatibles of a registered recipe are left out.
    """
    covered = {c for r in plugins.recipes() for c in r.compatibles}
    idx = session.index
    own = {c: idx.find(c) for c in node.compats}
    own_files = {p.name for p in own.values() if p is not None}
    # a base binding that the recipes' bindings include (``tc-g1`` under ``tc-g1-pwm``) is no mode of its own
    bases: set[str] = set()
    for c in covered:
        f = idx.find(c)
        if f is not None:
            bases |= set(idx.include_names(idx.load(f)))
    out: list[str] = []
    for c in node.compats:
        if own[c] is not None and c not in covered and own[c].name not in bases and c not in out:
            out.append(c)
    for p in idx.by_name.values():
        raw = idx.load(p)
        c = raw.get("compatible")
        if (isinstance(c, str) and c not in covered and c not in out
                and own_files & set(idx.include_names(raw))):
            out.append(c)
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
        assignment[role] = role if role in prs else prs[0]     # the signal of that name wins if the pad has several
    try:
        return dict(option.recipe.derived_props(assignment)), None
    except ValueError as exc:
        return {}, str(exc)


# Base-binding properties that are no peripheral setting, hidden in the dialog. Zephyr declares them in
# i2c-controller.yaml itself (v4.3.0), so a "only the class base file" rule does not remove them.
NOISE_PROPS = frozenset({"sq-size", "cq-size"})     # RTIO submission/completion queue sizes


def is_cells_prop(name: str) -> bool:
    """``#pwm-cells``, ``#io-channel-cells`` ...: specifier sizes, fixed by the binding, never a user setting."""
    return name.startswith("#") and name.endswith("-cells")


def neutral_specs(option: ClassOption, index: BindingIndex) -> list[PropSpec]:
    """Writable neutral properties of the class: scalar properties of the base binding chain, minus NOISE_PROPS."""
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
            if (not isinstance(spec, dict) or "const" in spec or name in out or name in NOISE_PROPS or is_cells_prop(name)
                    or spec.get("type") not in ("int", "string", "boolean")):
                continue
            out[name] = PropSpec(name, spec["type"], spec.get("enum"), spec.get("default"),
                                 (spec.get("description") or "").strip().split("\n")[0])
    needed = getattr(option.recipe, "default_props", None)
    for name, value in (needed(option.compatible) if needed else {}).items():
        spec = out.get(name)
        if spec is not None:
            spec.required, spec.default = True, value
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
               props: dict | None = None, chosen=(), aliases=(), children=()) -> DraftNode:
    """The draft node for the dialog state. Roles without a pad are dropped. `children`: bus devices (P8.6)."""
    pins = {r: p for r, p in pins.items() if p}
    return DraftNode(
        cls=option.cls, roles=[r for r in roles_order if r in pins],
        props={k: v for k, v in (props or {}).items() if v is not None},
        use=Use(chosen=[c for c in chosen if c], aliases=[a for a in aliases if a]),
        instance=instance.name, pins={r: pins[r] for r in roles_order if r in pins},
        vendor_props={}, compatible=option.compatible, label=label, children=list(children))


def revive_refs(draft: Draft | None) -> Draft | None:
    """Node-level phandle values (``cs-gpios``) come back from JSON as ``{"label": ...}``; make them `Ref` again.

    ``Draft.to_dict`` encodes the references of child nodes (``{"ref": ...}``) but writes node properties with
    ``asdict``, which turns a `Ref` into ``{"label": ...}``.
    """
    from ..core.translate import Ref

    def fix(v):
        if isinstance(v, dict) and set(v) == {"label"} and isinstance(v["label"], str):
            return Ref(v["label"])
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    if draft is not None:
        for n in draft.nodes:
            n.props = fix(n.props)
            n.vendor_props = fix(n.vendor_props)
    return draft


def child_gpio_pads(children, gpio_map, cs_entries=None) -> list[tuple[str, str]]:
    """(pad, what) of every GPIO the children and the controller's ``cs-gpios`` use, e.g. ``("PB24", "bmi160@0 int-gpios")``."""
    out = []
    for i, e in enumerate(cs_entries or []):
        pad = G.entry_pad(gpio_map, e)
        if pad:
            out.append((pad, f"cs-gpios[{i}]"))
    for ch in children:
        for sub in ch.walk():
            for name, val in sub.props.items():
                if name == "gpios" or name.endswith("-gpios"):
                    for e in val if isinstance(val, list) else []:
                        pad = G.entry_pad(gpio_map, e)
                        if pad:
                            out.append((pad, f"{sub.node_name} {name}"))
    return out


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
        self.draft = revive_refs(load_draft(model.app_dir, model.build)) or Draft(build=model.build.name)
        self.findings: list[Finding] | None = None
        self.checked_overlay: str | None = None
        self._lifted: Draft | None = None
        self._gpio_map: dict | None = None

    # -- node lookup
    def node_for_label(self, label: str):
        return next((n for n in self.model.edt.nodes if label in n.labels), None)

    def instance_of(self, node):
        return find_instance(self.device, [r.addr for r in node.regs], node.labels)

    def options_for(self, node) -> list[ClassOption]:
        inst = self.instance_of(node)
        return class_options(inst, self.index, prefer=tuple(node.compats)) if inst is not None else []

    def alternatives_for(self, node) -> list[str]:
        """Other compatibles of the node's peripheral for the generic form (counter mode of a PWM timer)."""
        if not self.options_for(node):
            return []
        from ..core import generic as G
        # only a mode that has something to configure (a bare base binding such as a bus controller has not)
        return [c for c in generic_alternatives(self, node)
                if any(not is_cells_prop(f.name) for f in G.form_spec(c, self.index, {}))
                or G.has_pinctrl(c, self.index)]

    def draft_node(self, label: str) -> DraftNode | None:
        return next((n for n in self.draft.nodes if n.label == label), None)

    # -- enabled nodes: lift (P5.3) the node as it is built now
    def lifted_node(self, label: str) -> DraftNode | None:
        """The enabled node `label` as a draft node (class, pads, neutral props, use), or None.

        The whole build is lifted once (``lift_build``) and kept; the node is picked by label.
        """
        if self._lifted is None:
            self._lifted, _report = lift_build(self.model, self.info)
        return next((n for n in self._lifted.nodes if n.label == label), None)

    # -- bus devices (P8.6)
    def gpio_map(self) -> dict[str, tuple[str, int]]:
        """pad -> (GPIO controller label, pin); computed once per session."""
        if self._gpio_map is None:
            self._gpio_map = G.gpio_pad_map(self.model, self.device)
        return self._gpio_map

    def build_children(self, label: str) -> set[str]:
        """Node names (``name@unit``) of the children the build already has below node `label`."""
        node = self.node_for_label(label)
        return set(node.children) if node is not None else set()

    def build_cs_entries(self, label: str) -> list:
        """The node's ``cs-gpios`` as the build has them, as draft entries ``[Ref, pin, flags]``."""
        from ..core.translate import Ref
        node = self.node_for_label(label)
        prop = node.props.get(G.CS_PROP) if node is not None else None
        out = []
        for e in (prop.val if prop is not None else []):
            ctrl = getattr(e, "controller", None)
            data = getattr(e, "data", None) or {}
            if ctrl is None or not ctrl.labels or "pin" not in data:
                return []
            out.append([Ref(ctrl.labels[0]), int(data["pin"]), int(data.get("flags", 0))])
        return out

    def is_enabled(self, label: str) -> bool:
        node = self.node_for_label(label)
        return node is not None and node.status == "okay"

    # -- who uses a pad
    def used_by(self, skip_path: str | None = None) -> dict[str, list[str]]:
        """pad -> users in the build. `skip_path` leaves out one node (the one being edited)."""
        out: dict[str, list[str]] = {}
        for pad, uses in self.usage.uses.items():
            names = []
            for u in uses:
                if skip_path is not None and u.node == skip_path:
                    continue
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
            for pad, what in child_gpio_pads(n.children, self.gpio_map(), n.props.get(G.CS_PROP)):
                out.setdefault(pad, []).append(f"{n.label} {what} (draft)")
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
        # enabled nodes already carry their compatible: the overlay does not repeat it
        existing = {n.labels[0]: n.compats[0] for n in self.model.edt.nodes
                    if n.status == "okay" and n.labels and n.compats}
        pin_node = next((n for n in self.model.edt.nodes
                         if any(plugins.get_formatter(c) for c in n.compats)), None)
        # enabled nodes that the draft edits: the board's version, so that the overlay carries only the difference
        board = {n.label: b for n in self.draft.nodes
                 if n.label and self.is_enabled(n.label) and (b := self.lifted_node(n.label)) is not None}
        return RenderContext(
            board_nodes=board, device=self.device, pinctrl_compatible=next(
                (c for c in (pin_node.compats if pin_node else []) if plugins.get_formatter(c)), None),
            pinctrl_label=(pin_node.labels[0] if pin_node and pin_node.labels else "pinctrl"),
            workspace=self.info.workspace, soc=self.info.soc, bindings=self.index,
            existing_compatibles=existing)

    def overlay(self) -> str:
        if not self.draft.nodes:
            return ""
        text = render_overlay(self.draft, self.render_context())
        # An enabled node already has a pinctrl group of that name from the board; the same name would merge
        # the new pinmux into the old group. The group of an edited node gets a name of its own.
        for n in self.draft.nodes:
            if n.label and n.pins and self.is_enabled(n.label):
                text = rename_group(text, f"{n.label}_{n.cls}_default", edit_group_name(n))
        return text

    def kconfig(self) -> list[KconfigSuggestion]:
        compats = list(dict.fromkeys(
            [n.compatible for n in self.draft.nodes if n.compatible]
            + [sub.compatible[0] for n in self.draft.nodes for ch in n.children for sub in ch.walk()
               if sub.compatible]))
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
    """A disabled node that a recipe covers, or (P8.5) one without a recipe but with a binding (generic form)."""
    if node.status != "disabled":
        return False
    if session.options_for(node):
        return True
    from .generic_form import generic_available
    return generic_available(session, node)


def can_configure_generic(session: ConfigSession, node) -> bool:
    """An enabled node without a recipe but with a binding: the generic form edits its properties."""
    if node.status != "okay" or session.options_for(node):
        return False
    from .generic_form import generic_available
    return generic_available(session, node)


def can_edit_pins(session: ConfigSession, node) -> bool:
    """An enabled node that a recipe covers and that the lift (P5.3) can turn into a draft node."""
    if node.status != "okay" or not node.labels or not session.options_for(node):
        return False
    return session.lifted_node(node.labels[0]) is not None


def edit_group_name(node: DraftNode) -> str:
    """Name of the pinctrl group of an edited (already enabled) node: not the board's group."""
    return f"{node.label}_{node.cls}_edit"


def rename_group(text: str, old: str, new: str) -> str:
    """Rename a pinctrl group label in overlay text (definition and reference), whole words only."""
    return re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, text)


# ---- the dialog ---------------------------------------------------------------------------------
def open_configure(session: ConfigSession, node, on_saved: Callable[[DraftNode], None],
                   edit: bool = False) -> None:
    """Open the configurator for a node; `on_saved(draft_node)` runs after Save.

    A disabled node starts empty (or from its draft). With ``edit=True`` the node is enabled: the dialog
    starts from the node as it is built now (lifted, P5.3), unless the draft already holds a change of it.
    """
    from nicegui import ui

    edit = edit or node.status == "okay"            # the pencil of an edited node in the Draft tab
    inst = session.instance_of(node)
    options = session.options_for(node)
    if not options:                                 # no recipe: the form comes from the binding (P8.5)
        from .generic_form import open_generic
        open_generic(session, node, on_saved)
        return
    label = node.labels[0] if node.labels else node.name
    existing = session.draft_node(label)
    if existing is None and edit:
        existing = session.lifted_node(label)
    used = session.used_by(skip_path=node.path if edit else None)
    for pad, names in session.draft_pads(skip_label=label).items():
        used.setdefault(pad, []).extend(names)
    st = {"opt": next((o for o in options if existing and o.cls == existing.cls), options[0]),
          "busy": False}
    selects: dict[str, object] = {}
    prop_inputs: dict[str, tuple[PropSpec, object]] = {}
    st.update(children=[], cs=[], cs_dirty=False)

    with ui.dialog() as dlg, ui.card().style("min-width: 640px; max-width: 760px; max-height: 92vh; overflow-y: auto") \
            .props("id=cfg-dialog"):
        ui.label(f"{'Edit pins of' if edit else 'Configure'} &{label}").classes("text-lg font-bold")
        ui.label(f"{node.path}  -  instance {inst.name}").classes("text-xs text-grey-5 font-mono")
        cls_toggle = ui.toggle({o.cls: o.cls.upper() for o in options}, value=st["opt"].cls) \
            .props("id=cfg-class")
        alts = session.alternatives_for(node)
        if alts:                                    # the recipe covers one mode; other modes use the generic form
            with ui.row().classes("w-full items-center gap-2"):
                alt_sel = ui.select(alts, value=alts[0], label="Other compatible (generic form)")                     .props("dense outlined id=cfg-alt-compat").classes("grow")
                ui.button("Use generic form", icon="tune").props("no-caps flat dense id=cfg-open-generic-alt")                     .on_click(lambda: _to_generic(alt_sel.value))
        ui.label("Pads").classes("font-bold")
        roles_box = ui.column().classes("w-full gap-1")
        derived = ui.label().classes("text-sm font-mono").props("id=cfg-derived")
        ui.label("Properties").classes("font-bold")
        props_box = ui.column().classes("w-full gap-1")
        children_section = ui.column().classes("w-full gap-1").props("id=cfg-children")
        with children_section:
            ui.label("Child devices").classes("font-bold")
            children_box = ui.column().classes("w-full gap-0")
            add_child_btn = ui.button("Add child device...", icon="add").props("no-caps flat dense id=cfg-child-add")
        ui.label("Use").classes("font-bold")
        chosen_in = ui.select(list(existing.use.chosen) if existing else [], multiple=True, new_value_mode="add-unique", label="chosen (e.g. zephyr,console)",
                              value=list(existing.use.chosen) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        alias_in = ui.select(list(existing.use.aliases) if existing else [], multiple=True, new_value_mode="add-unique", label="aliases (e.g. my-i2c)",
                             value=list(existing.use.aliases) if existing else []) \
            .props("dense outlined use-chips use-input").classes("w-full")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps id=cfg-cancel")
            save_btn = ui.button("Save", icon="save").props("no-caps id=cfg-save")

    # ---- child devices of a bus controller (P8.6): the list lives in `st`, Save writes it into the draft node
    def init_children() -> None:
        """Children and cs-gpios entries the dialog starts with: the draft's / the lifted node's, for a matching class."""
        same = existing is not None and existing.cls == st["opt"].cls
        st["children"] = [copy.deepcopy(c) for c in existing.children] if same else []
        cs = list(existing.props.get(G.CS_PROP) or []) if same else []
        st["cs"] = cs or (session.build_cs_entries(label) if st["opt"].cls == "spi" and edit else [])
        st["cs_dirty"] = bool(cs)           # entries of the draft are written again; the board's only when changed

    def taken_pads() -> dict[str, list[str]]:
        """pad -> users, for the child dialog: build, other draft nodes, this dialog's pads and children."""
        out = {p: list(v) for p, v in used.items()}
        for role, pad in current_pins().items():
            out.setdefault(pad, []).append(f"{label} {role} (this dialog)")
        for pad, what in child_gpio_pads(st["children"], session.gpio_map(), st["cs"]):
            out.setdefault(pad, []).append(f"{what} (this dialog)")
        return out

    def removable(child: DraftChild) -> bool:
        return child.node_name not in session.build_children(label)      # a child of the board stays in the build

    def remove_child(child: DraftChild) -> None:
        st["children"] = [c for c in st["children"] if c is not child]
        if st["opt"].cls == "spi":
            a = G.child_address(child)
            if a is not None:
                st["cs"] = G.drop_cs_entry({G.CS_PROP: st["cs"]}, a).get(G.CS_PROP, [])
                st["cs_dirty"] = True
        refresh_children()

    def refresh_children() -> None:
        bus = st["opt"].cls
        children_section.set_visibility(bus in G.BUS_CLASSES)
        children_box.clear()
        with children_box:
            if not st["children"]:
                ui.label("none").classes("text-xs text-grey-6")
            for c in st["children"]:
                cid = f"{c.name}-{c.unit}"
                with ui.row().classes("w-full items-center gap-2 no-wrap").props(f"id=cfg-child-{cid}"):
                    ui.label(f"{c.node_name}   {', '.join(c.compatible)}").classes("text-sm font-mono col")
                    extra = [n for n in c.props if n != "reg" and (n == "gpios" or n.endswith("-gpios"))]
                    if extra:
                        ui.label(", ".join(extra)).classes("text-xs text-grey-5")
                    if removable(c):
                        ui.button(icon="delete", on_click=lambda _e, c=c: remove_child(c)) \
                            .props(f"flat dense round size=sm id=cfg-child-remove-{cid}")
                    else:
                        ui.label("from the board").classes("text-xs text-grey-6")

    def on_child_added(child: DraftChild, cs: list | None) -> None:
        if cs is not None:
            try:
                st["cs"] = G.set_cs_entry({G.CS_PROP: st["cs"]}, G.child_address(child), cs)[G.CS_PROP]
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            st["cs_dirty"] = True
        st["children"].append(child)
        refresh_children()

    def open_child_dialog() -> None:
        from .generic_form import open_child
        open_child(session, bus=st["opt"].cls, parent_label=label, children=st["children"],
                   cs_len=len(st["cs"]), taken=taken_pads, on_added=on_child_added)

    add_child_btn.on_click(open_child_dialog)

    def _to_generic(compat: str) -> None:
        from .generic_form import open_generic
        dlg.close()
        open_generic(session, node, on_saved, compat=compat)

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
        init_children()
        refresh_children()
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
                if cur is None and spec.required:
                    cur = spec.default
                hint = spec.description + (f" (default {spec.default})" if spec.default is not None else "")                     + (" - required by the driver" if spec.required else "")
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
            if v is None and spec.required:
                v = spec.default
            if v is not None:
                props[name] = v
        if opt.cls == "spi" and st["cs"] and st["cs_dirty"]:
            props[G.CS_PROP] = st["cs"]
        dn = build_node(option=opt, instance=inst, label=label, pins=current_pins(),
                        roles_order=option_roles(opt, inst), props=props,
                        chosen=chosen_in.value or [], aliases=alias_in.value or [],
                        children=st["children"] if opt.cls in G.BUS_CLASSES else [])
        session.upsert(dn)
        session.save()
        dlg.close()
        on_saved(dn)

    save_btn.on_click(save)
    build_class_form()
    dlg.open()
