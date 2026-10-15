"""Generic binding form: a peripheral without a recipe.

When no recipe covers a compatible, the configurator generates the form from the binding YAML chain:
every scalar property (``int``, ``string``, ``boolean``) with its type, enum, default, required flag and
description, and, when the node has pins, the signal roles of the device model. The result is a
*recipe-less* ``DraftNode`` (class ``generic``): it renders through ``render_overlay`` and is checked like
any other node. Nothing here knows a vendor; the property list is the binding, the pin roles are the
provider's.

What the form leaves out on purpose:

* properties of ``base.yaml`` and what it includes (``status``, ``wakeup-source``, ``zephyr,...``);
* ``const`` properties (the renderer adds them), ``label``, ``compatible``, ``status``, cell counts
  (``#...-cells``), ``pinctrl-*``;
* every non-scalar type (``phandle``, ``phandle-array``, ``array``, ``string-array``, ``compound`` ...).
  They stay as the SoC or board file has them; ``form_notes`` names them so the dialog can say so.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .draft import BindingIndex, DraftNode, Use

GENERIC_CLASS = "generic"
SCALAR_TYPES = ("int", "string", "boolean")
SKIP_NAMES = frozenset({"label", "compatible", "status", "reg", "reg-names"})


@dataclass
class FieldSpec:
    name: str
    type: str                   # "int" | "string" | "boolean"
    required: bool = False
    enum: list | None = None
    default: object = None
    description: str = ""
    current: object = None      # value the node has now (SoC/board file), None when unset


def _skip(name: str) -> bool:
    return name in SKIP_NAMES or name.startswith("#") or name.startswith("pinctrl-")


def _chain_files(compatible: str, index: BindingIndex) -> tuple[list[Path], set[Path]]:
    """(binding files of the compatible's include chain, files of base.yaml's chain)."""
    start = index.find(compatible) if compatible else None
    if start is None:
        return [], set()
    files = [p for p in index.chain(start) if isinstance(p, Path)]
    base = index.by_name.get("base.yaml")
    generic = {p for p in index.chain(base) if isinstance(p, Path)} if base else set()
    return files, generic


def merged_properties(compatible: str, index: BindingIndex, include_base: bool = True) -> dict[str, dict]:
    """Property specs of the whole chain; the own file wins per key, an include fills what is missing."""
    files, generic = _chain_files(compatible, index)
    out: dict[str, dict] = {}
    for p in files:
        if not include_base and p in generic:
            continue
        for name, spec in (index.load(p).get("properties") or {}).items():
            cur = out.setdefault(name, {})
            if isinstance(spec, dict):
                for k, v in spec.items():
                    cur.setdefault(k, v)
    return out


def form_spec(compatible: str, index: BindingIndex, current: dict | None = None) -> list[FieldSpec]:
    """The form fields of `compatible`, in binding order (own file first). `current` = values the node has now."""
    current = current or {}
    out: list[FieldSpec] = []
    for name, spec in merged_properties(compatible, index, include_base=False).items():
        if _skip(name) or "const" in spec or spec.get("type") not in SCALAR_TYPES:
            continue
        out.append(FieldSpec(
            name=name, type=spec["type"], required=bool(spec.get("required")), enum=spec.get("enum"),
            default=spec.get("default"), current=current.get(name),
            description=(spec.get("description") or "").strip().split("\n")[0]))
    return out


def form_notes(compatible: str, index: BindingIndex) -> list[str]:
    """Names of the binding's properties that the form cannot edit (non-scalar types)."""
    return [n for n, s in merged_properties(compatible, index, include_base=False).items()
            if not _skip(n) and "const" not in s and s.get("type") not in SCALAR_TYPES
            and s.get("type") is not None]


def has_pinctrl(compatible: str, index: BindingIndex) -> bool:
    """The binding chain declares ``pinctrl-0`` (``pinctrl-device.yaml``)."""
    return "pinctrl-0" in merged_properties(compatible, index)


def parse_value(spec: FieldSpec, text) -> object | None:
    """Value from the form (text or bool). None = empty. Raises ValueError when the text does not fit the type."""
    if spec.type == "boolean":
        return True if text else None
    if text is None or str(text).strip() == "":
        return None
    if spec.type == "int":
        try:
            v = int(str(text).strip(), 0)
        except ValueError:
            raise ValueError("not a number") from None
        if spec.enum and v not in [int(x) for x in spec.enum]:
            raise ValueError(f"{v} is not one of {', '.join(str(x) for x in spec.enum)}")
        return v
    s = str(text).strip()
    if spec.enum and s not in [str(x) for x in spec.enum]:
        raise ValueError(f"{s!r} is not one of {', '.join(str(x) for x in spec.enum)}")
    return s


def check_form(specs: list[FieldSpec], texts: dict) -> dict[str, str]:
    """Problems per field name for the form state `texts` (name -> widget value). Empty dict = all fine.

    * a required field without a value (neither typed nor set by the board) is flagged ``required``;
    * a text that does not fit the type or the enum;
    * a boolean that the board file sets cannot be switched off by an overlay.
    """
    problems: dict[str, str] = {}
    for spec in specs:
        text = texts.get(spec.name)
        try:
            v = parse_value(spec, text)
        except ValueError as exc:
            problems[spec.name] = f"not a valid {spec.type}: {exc}" if spec.type == "int" and "invalid" in str(exc) \
                else str(exc)
            continue
        if spec.type == "boolean":
            if spec.current is True and v is None:
                problems[spec.name] = "set by the board; an overlay cannot switch it off"
            continue
        if v is None and spec.required:
            problems[spec.name] = "required"
    return problems


def form_props(specs: list[FieldSpec], texts: dict) -> dict:
    """Draft properties for a valid form state: only values that differ from what the node has now."""
    out: dict = {}
    for spec in specs:
        v = parse_value(spec, texts.get(spec.name))
        if v is not None and v != spec.current:
            out[spec.name] = v
    return out


# ---- pins ---------------------------------------------------------------------------------------
def pin_roles(instance) -> list[str]:
    """Signal roles of the instance in first-seen order (``WO0``, ``PAD0`` ...): the provider's names."""
    return list(dict.fromkeys(s.role for s in instance.signals)) if instance is not None else []


def pad_candidates(instance, role: str, chosen: dict[str, str]) -> list[tuple[str, str]]:
    """(pad, mux) of the signals with `role`; pads chosen for another role are left out."""
    taken = {p for r, p in chosen.items() if r != role and p}
    return sorted({(s.pad, str(s.mux)) for s in instance.signals if s.role == role and s.pad not in taken})


def pin_problems(instance, pins: dict[str, str]) -> dict[str, str]:
    """Role -> problem. A pad that carries several signals of the instance cannot be rendered without a recipe."""
    out: dict[str, str] = {}
    for role, pad in pins.items():
        sigs = [s for s in instance.signals if s.pad == pad]
        if not sigs:
            out[role] = f"pad {pad} is not a signal of {instance.name}"
        elif len(sigs) > 1:
            out[role] = (f"pad {pad} offers {len(sigs)} signals on {instance.name} "
                         f"({', '.join(str(s.mux) + '/' + s.role for s in sigs)})")
    return out


# ---- draft node ---------------------------------------------------------------------------------
def build_generic_node(*, label: str, compatible: str, instance: str | None = None, props: dict | None = None,
                       pins: dict[str, str] | None = None, roles_order: list[str] | None = None,
                       chosen=(), aliases=()) -> DraftNode:
    """Recipe-less draft node. Roles without a pad are dropped."""
    pins = {r: p for r, p in (pins or {}).items() if p}
    order = [r for r in (roles_order or list(pins)) if r in pins]
    return DraftNode(
        cls=GENERIC_CLASS, roles=order, props={k: v for k, v in (props or {}).items() if v is not None},
        use=Use(chosen=[c for c in chosen if c], aliases=[a for a in aliases if a]),
        instance=instance, pins={r: pins[r] for r in order}, vendor_props={}, compatible=compatible, label=label)


def is_generic(node: DraftNode) -> bool:
    return node.cls == GENERIC_CLASS
