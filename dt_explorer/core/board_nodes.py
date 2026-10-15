"""Board-level nodes: LEDs, buttons, PWM LEDs, aliases and chosen entries.

Everything lives in ONE ``DraftNode`` of class ``board`` (label ``{/}``, no instance, no pins). The
renderer writes it as a node override of the root, ``&{/} { ... };`` (the dtc path reference form), so
no new draft or render code is needed. Its children (``DraftChild``) are the board-level nodes::

    &{/} {
        status = "okay";
        user_leds { compatible = "gpio-leds";  <name>: <name> { gpios = <&portb 5 1>; label = "..."; }; };
        user_keys { compatible = "gpio-keys";  <name>: <name> { gpios = <&portb 6 17>; label = ...;
                                                                  zephyr,code = <11>; }; };
        user_pwm_leds { compatible = "pwm-leds"; <name>: <name> { pwms = <&tcc0 2 20000000>; }; };
        aliases { led1 = "/user_leds/<name>"; };
        chosen  { zephyr,shell-uart = "/soc/sercom@41012000"; };
    };

* GPIO flags are plain integers (bit 0 active low = 1, pull-up = 16, pull-down = 32, the devicetree
  flag cells of Zephyr); a draft file cannot carry the symbolic names.
* ``aliases`` and ``chosen`` hold *string paths* (``"/user_leds/<name>"``), which dtc and edtlib accept
  in both blocks (a phandle in angle brackets would not be a valid alias). A node of the draft is
  addressed by its path under the container, a node of the build by its path in the devicetree.
* A container (``user_leds`` ...) is omitted while it has no entry; the carrier node disappears from the
  draft when nothing is left (``apply_spec`` removes it).
* The pad of a GPIO entry is *not* stored: it is derived from "controller + pin" with the same
  mapping ``core.pins`` uses to find the pads that existing nodes occupy (``gpio_pad_map``), so the
  existing pad-conflict check (``validate``: ``pin_conflict``) covers the new uses without help.

The module knows no vendor: controllers are nodes with ``gpio-controller`` / ``#pwm-cells``, pads are
opaque ids of the device model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import pins as pins_mod
from .draft import Draft, DraftChild, DraftNode
from .translate import Ref

BOARD_CLASS = "board"
BOARD_LABEL = "{/}"

LEDS, KEYS, PWM_LEDS = "leds", "keys", "pwm-leds"
CONTAINERS = {                      # kind -> (container node name, compatible)
    LEDS: ("user_leds", "gpio-leds"),
    KEYS: ("user_keys", "gpio-keys"),
    PWM_LEDS: ("user_pwm_leds", "pwm-leds"),
}
KIND_TITLES = {LEDS: "LED (gpio-leds)", KEYS: "Button (gpio-keys)", PWM_LEDS: "PWM LED (pwm-leds)"}

ACTIVE_LOW, PULL_UP, PULL_DOWN = 1, 16, 32
PULLS = {"none": 0, "pull-up": PULL_UP, "pull-down": PULL_DOWN}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")      # dtc: alias names are lower case, digits and '-'
CHOSEN_RE = re.compile(r"^[a-z][a-z0-9,_-]*$")


@dataclass
class Entry:
    """One child of a container."""
    kind: str                       # LEDS | KEYS | PWM_LEDS
    name: str                       # node name and label
    pad: str | None = None          # LEDS / KEYS: the GPIO pad
    active_low: bool = False
    pull: str = "none"              # KEYS: "none" | "pull-up" | "pull-down"
    text: str = ""                  # the `label` property ("" = leave out)
    code: int | None = None         # KEYS: zephyr,code
    ctrl: str | None = None         # PWM_LEDS: label of the PWM controller
    channel: int = 0
    period_ns: int = 20_000_000

    @property
    def path(self) -> str:
        return f"/{CONTAINERS[self.kind][0]}/{self.name}"

    @property
    def flags(self) -> int:
        return (ACTIVE_LOW if self.active_low else 0) | PULLS.get(self.pull, 0)


@dataclass
class BoardSpec:
    entries: list[Entry] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # alias name -> target path
    chosen: dict[str, str] = field(default_factory=dict)       # chosen name -> target path

    def of(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    def is_empty(self) -> bool:
        return not (self.entries or self.aliases or self.chosen)


# ---- GPIO pads <-> controller + pin -------------------------------------------------------------------
def gpio_pad_map(model, device) -> dict[str, tuple[str, int]]:
    """pad -> (label of the GPIO controller, pin number), the inverse of the mapping ``core.pins`` applies.

    Empty when the device model is missing or the controllers cannot be matched to pad groups (the
    limit ``core.pins`` documents); the caller then cannot offer GPIO pads.
    """
    out: dict[str, tuple[str, int]] = {}
    if device is None:
        return out
    seen: set = set()
    notes: list[str] = []
    for ctrl in model.edt.nodes:
        if "gpio-controller" not in ctrl.props or ctrl.status != "okay" or not ctrl.labels:
            continue
        key = tuple(ctrl.compats)
        if key in seen:
            continue
        seen.add(key)
        for c in pins_mod._gpio_controllers(model, ctrl):
            if not c.labels:
                continue
            ng = c.props.get("ngpios")
            for pin in range(int(ng.val) if ng is not None and ng.val else 32):
                pad = pins_mod._gpio_pad(model, device, c, pin, notes)
                if pad:
                    out[pad] = (c.labels[0], pin)
    return out


def pwm_controllers(model) -> list[dict]:
    """The PWM controllers of the build: label, path, cells (names of the specifier cells)."""
    out = []
    for n in model.edt.nodes:
        cells = n._node.props.get("#pwm-cells")             # the cell count is no edtlib property: read dtlib's
        if cells is None or n.status != "okay" or not n.labels:
            continue
        names = ["channel", "period", "flags"][:cells.to_num()]
        out.append({"label": n.labels[0], "path": n.path, "cells": names})
    return sorted(out, key=lambda d: d["path"])


# ---- spec <-> DraftChild -----------------------------------------------------------------------------
def _entry_child(e: Entry, pad_map: dict, pwm_cells: dict[str, int]) -> DraftChild:
    props: dict = {}
    if e.kind == PWM_LEDS:
        cells = [e.channel, e.period_ns]
        if pwm_cells.get(e.ctrl or "", 2) >= 3:
            cells.append(0)                                 # flags: normal polarity
        props["pwms"] = [[Ref(e.ctrl or "?")] + cells]
    else:
        ctrl, pin = pad_map[e.pad] if e.pad in pad_map else ("?", 0)
        props["gpios"] = [[Ref(ctrl), pin, e.flags]]
    if e.text:
        props["label"] = e.text
    if e.kind == KEYS and e.code is not None:
        props["zephyr,code"] = e.code
    return DraftChild(name=e.name, labels=[e.name], props=props)


def spec_to_node(spec: BoardSpec, pad_map: dict, pwm_cells: dict[str, int] | None = None) -> DraftNode | None:
    """The carrier node for a spec, or None when the spec is empty."""
    if spec.is_empty():
        return None
    kids: list[DraftChild] = []
    for kind, (cname, compat) in CONTAINERS.items():
        es = spec.of(kind)
        if es:
            kids.append(DraftChild(name=cname, compatible=[compat],
                                   children=[_entry_child(e, pad_map, pwm_cells or {}) for e in es]))
    if spec.aliases:
        kids.append(DraftChild(name="aliases", props=dict(spec.aliases)))
    if spec.chosen:
        kids.append(DraftChild(name="chosen", props=dict(spec.chosen)))
    return DraftNode(cls=BOARD_CLASS, label=BOARD_LABEL, children=kids)


def carrier(draft: Draft) -> DraftNode | None:
    return next((n for n in draft.nodes if n.cls == BOARD_CLASS and n.label == BOARD_LABEL), None)


def spec_from_node(node: DraftNode | None, pad_map: dict) -> BoardSpec:
    """Read a carrier node back into a spec (inverse of ``spec_to_node``)."""
    spec = BoardSpec()
    if node is None:
        return spec
    by_ctrl_pin = {v: pad for pad, v in pad_map.items()}
    kinds = {cname: kind for kind, (cname, _c) in CONTAINERS.items()}
    for ch in node.children:
        if ch.name in kinds:
            kind = kinds[ch.name]
            for c in ch.children:
                e = Entry(kind=kind, name=c.name, text=str(c.props.get("label", "")))
                if kind == PWM_LEDS:
                    cells = (c.props.get("pwms") or [[Ref("?"), 0, 0]])[0]
                    e.ctrl, e.channel = cells[0].label, int(cells[1])
                    e.period_ns = int(cells[2]) if len(cells) > 2 else e.period_ns
                else:
                    cells = (c.props.get("gpios") or [[Ref("?"), 0, 0]])[0]
                    e.pad = by_ctrl_pin.get((cells[0].label, int(cells[1])))
                    flags = int(cells[2]) if len(cells) > 2 else 0
                    e.active_low = bool(flags & ACTIVE_LOW)
                    e.pull = "pull-up" if flags & PULL_UP else "pull-down" if flags & PULL_DOWN else "none"
                    if kind == KEYS and "zephyr,code" in c.props:
                        e.code = int(c.props["zephyr,code"])
                spec.entries.append(e)
        elif ch.name == "aliases":
            spec.aliases.update({k: str(v) for k, v in ch.props.items()})
        elif ch.name == "chosen":
            spec.chosen.update({k: str(v) for k, v in ch.props.items()})
    return spec


def read_spec(draft: Draft, pad_map: dict) -> BoardSpec:
    return spec_from_node(carrier(draft), pad_map)


def apply_spec(draft: Draft, spec: BoardSpec, pad_map: dict, pwm_cells: dict[str, int] | None = None) -> None:
    """Replace the carrier node of `draft` by the one for `spec` (removed when the spec is empty)."""
    node = spec_to_node(spec, pad_map, pwm_cells)
    old = carrier(draft)
    draft.nodes = [n for n in draft.nodes if n is not old]
    if node is not None:
        draft.nodes.append(node)


# ---- checks ---------------------------------------------------------------------------------------------
@dataclass
class Problem:
    severity: str                   # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.message}"


def target_paths(spec: BoardSpec) -> dict[str, str]:
    """Paths of the board nodes the spec creates, by name (label) - the alias/chosen targets of the draft."""
    return {e.name: e.path for e in spec.entries}


def check_spec(spec: BoardSpec, *, model, pad_map: dict, other_users: dict[str, list[str]] | None = None,
               existing_aliases: set[str] | None = None, pwm_ctrls: dict[str, dict] | None = None) -> list[Problem]:
    """Problems of a spec before it is saved. Pad conflicts are warnings here: ``validate`` reports them as errors.

    `other_users` = pad -> descriptions of what the build and the other draft nodes put on it.
    """
    probs: list[Problem] = []
    other_users = other_users or {}
    labels = {lb for n in model.edt.nodes for lb in n.labels}
    seen_names: set[str] = set()
    seen_pads: dict[str, str] = {}
    for e in spec.entries:
        where = f"{e.name or '(unnamed)'}"
        if not NAME_RE.match(e.name or ""):
            probs.append(Problem("error", f"{where}: name must be lower case letters, digits and '_', starting with a letter"))
            continue
        if e.name in seen_names:
            probs.append(Problem("error", f"{e.name}: name used twice"))
        seen_names.add(e.name)
        if e.name in labels:
            probs.append(Problem("error", f"{e.name}: a node with this label already exists in the build"))
        if e.kind == PWM_LEDS:
            ctrls = pwm_ctrls or {}
            if e.ctrl not in ctrls:
                probs.append(Problem("error", f"{e.name}: no PWM controller {e.ctrl!r} in the build"))
            if e.channel < 0:
                probs.append(Problem("error", f"{e.name}: channel must be 0 or more"))
            if e.period_ns <= 0:
                probs.append(Problem("error", f"{e.name}: period must be positive"))
            key = f"{e.ctrl}:{e.channel}"
            if key in seen_pads:
                probs.append(Problem("error", f"{e.name}: PWM channel {e.channel} of {e.ctrl} is also used by {seen_pads[key]}"))
            seen_pads[key] = e.name
            continue
        if not e.pad:
            probs.append(Problem("error", f"{e.name}: no pad chosen"))
            continue
        if e.pad not in pad_map:
            probs.append(Problem("error", f"{e.name}: pad {e.pad} is not a GPIO pad of this device"))
            continue
        if e.pad in seen_pads:
            probs.append(Problem("error", f"{e.name}: pad {e.pad} is also used by {seen_pads[e.pad]}"))
        seen_pads[e.pad] = e.name
        if other_users.get(e.pad):
            probs.append(Problem("warning", f"{e.name}: pad {e.pad} is already used by "
                                            + "; ".join(other_users[e.pad]) + " (the check reports a conflict)"))
    targets = {n.path for n in model.edt.nodes} | set(target_paths(spec).values())
    for block, items, rx in (("alias", spec.aliases, ALIAS_RE), ("chosen", spec.chosen, CHOSEN_RE)):
        for name, path in items.items():
            if not rx.match(name):
                probs.append(Problem("error", f"{block} '{name}': not a valid {block} name"))
            if path not in targets:
                probs.append(Problem("error", f"{block} '{name}': target {path} does not exist (a board node that is no longer in the draft?)"))
    for name in spec.aliases:
        if name in (existing_aliases or set()):
            probs.append(Problem("warning", f"alias '{name}' exists in the build: this overlay replaces it"))
    for name in spec.chosen:
        if name in {k for k in model.edt.chosen_nodes}:
            probs.append(Problem("warning", f"chosen '{name}' exists in the build: this overlay replaces it"))
    return probs


def has_errors(problems: list[Problem]) -> bool:
    return any(p.severity == "error" for p in problems)


def summary(node: DraftNode) -> str:
    """One line for the Draft tab: what the carrier holds."""
    spec = spec_from_node(node, {})
    parts = []
    for kind, title in (("leds", "LED"), ("keys", "button"), ("pwm-leds", "PWM LED")):
        k = len(spec.of(kind))
        if k:
            parts.append(f"{k} {title}{'s' if k > 1 else ''}")
    if spec.aliases:
        parts.append(f"{len(spec.aliases)} alias{'es' if len(spec.aliases) > 1 else ''}")
    if spec.chosen:
        parts.append(f"{len(spec.chosen)} chosen")
    return ", ".join(parts) or "empty"
