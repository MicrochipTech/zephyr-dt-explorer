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

import re
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


def _chain_files(compatible: str, index: BindingIndex, bus: str | None = None) -> tuple[list[Path], set[Path]]:
    """(binding files of the compatible's include chain, files of base.yaml's chain).

    With `bus`, the binding file that applies on that bus is used (one compatible can have an I2C and an SPI file).
    """
    start = (binding_for(compatible, index, bus) if bus else index.find(compatible)) if compatible else None
    if start is None:
        return [], set()
    files = [p for p in index.chain(start) if isinstance(p, Path)]
    base = index.by_name.get("base.yaml")
    generic = {p for p in index.chain(base) if isinstance(p, Path)} if base else set()
    return files, generic


def merged_properties(compatible: str, index: BindingIndex, include_base: bool = True,
                      bus: str | None = None) -> dict[str, dict]:
    """Property specs of the whole chain; the own file wins per key, an include fills what is missing."""
    files, generic = _chain_files(compatible, index, bus)
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


def form_spec(compatible: str, index: BindingIndex, current: dict | None = None,
              bus: str | None = None) -> list[FieldSpec]:
    """The form fields of `compatible`, in binding order (own file first). `current` = values the node has now."""
    current = current or {}
    out: list[FieldSpec] = []
    for name, spec in merged_properties(compatible, index, include_base=False, bus=bus).items():
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


# ---- child devices on a bus (P8.6) ---------------------------------------------------------------
# A bus device is a child node of a configured bus controller (I2C, SPI). The compatibles on offer are the
# bindings whose include chain carries ``on-bus: <bus>`` (``i2c-device.yaml`` / ``spi-device.yaml`` set it).
# One compatible can have a file per bus (``bosch,bme280-i2c.yaml`` / ``-spi.yaml``), so the binding file is
# always looked up together with the bus. The unit address is the ``reg`` value: the I2C address (hex) or the
# SPI chip-select index. GPIO phandle properties (``int-gpios``, ``reset-gpios``) are picked as pads; the chip
# select of an SPI device is the controller's ``cs-gpios`` entry with the same index.
BUS_CLASSES = ("i2c", "spi")
GPIO_FLAGS = {"active high": 0, "active low": 1}       # GPIO_ACTIVE_HIGH / GPIO_ACTIVE_LOW of the gpio binding
CS_PROP = "cs-gpios"


@dataclass
class ChildOption:
    compatible: str
    description: str
    path: Path


@dataclass
class GpioSpec:
    name: str                   # "int-gpios"
    required: bool = False
    description: str = ""


def _effective_bus(index: BindingIndex, path: Path) -> str | None:
    for item in index.chain(path):
        if isinstance(item, Path):
            ob = index.load(item).get("on-bus")
            if isinstance(ob, str):
                return ob
    return None


def _bus_table(index: BindingIndex) -> dict[str, dict[str, ChildOption]]:
    """bus -> compatible -> option, for every device binding of the index (cached on the index)."""
    cache = index.__dict__.get("_dtx_bus_table")
    if cache is None:
        cache = {}
        for path in sorted(index.by_name.values(), key=lambda p: p.name):
            raw = index.load(path)
            compat = raw.get("compatible")
            if not isinstance(compat, str) or raw.get("bus"):
                continue                                    # no compatible, or a bus controller itself
            bus = _effective_bus(index, path)
            if bus is None:
                continue
            desc = (raw.get("description") or "").strip().split("\n")[0]
            cache.setdefault(bus, {}).setdefault(compat, ChildOption(compat, desc, path))
        index.__dict__["_dtx_bus_table"] = cache
    return cache


def child_options(index: BindingIndex, bus: str) -> list[ChildOption]:
    """Compatibles whose binding applies on `bus` (``on-bus``), sorted by compatible."""
    return sorted(_bus_table(index).get(bus, {}).values(), key=lambda o: o.compatible)


def binding_for(compatible: str, index: BindingIndex, bus: str) -> Path | None:
    """Binding file of `compatible` for a device on `bus`; falls back to the plain lookup."""
    opt = _bus_table(index).get(bus, {}).get(compatible)
    return opt.path if opt is not None else index.find(compatible)


def child_form(compatible: str, index: BindingIndex, bus: str) -> list[FieldSpec]:
    """Scalar form fields of a bus device (the generic form, bus-aware). ``reg`` is the address field."""
    return form_spec(compatible, index, None, bus=bus)


def gpio_specs(compatible: str, index: BindingIndex, bus: str) -> list[GpioSpec]:
    """GPIO phandle properties of the binding (``gpios`` or ``*-gpios``), in binding order."""
    return [GpioSpec(n, bool(s.get("required")), (s.get("description") or "").strip().split("\n")[0])
            for n, s in merged_properties(compatible, index, include_base=False, bus=bus).items()
            if s.get("type") == "phandle-array" and (n == "gpios" or n.endswith("-gpios")) and n != CS_PROP]


def parse_address(bus: str, text) -> int:
    """`reg` value from the form: I2C address in hex (``76`` or ``0x76``), SPI chip-select index in decimal."""
    t = str(text if text is not None else "").strip()
    if not t:
        raise ValueError("required")
    try:
        v = int(t, 16) if bus == "i2c" else int(t, 10)
    except ValueError:
        raise ValueError("not a hex address" if bus == "i2c" else "not a chip-select index") from None
    if bus == "i2c" and not 0 <= v <= 0x7F:
        raise ValueError("an I2C address has 7 bits (0x00-0x7f)")
    if v < 0:
        raise ValueError("not a chip-select index")
    return v


def unit_of(addr: int) -> str:
    """Unit address text of a ``reg`` value (hex, no prefix)."""
    return format(addr, "x")


def child_node_name(compatible: str) -> str:
    """Node name from the compatible: the part after the vendor prefix, in devicetree name characters."""
    part = compatible.rsplit(",", 1)[-1].lower()
    return re.sub(r"[^a-z0-9-]+", "-", part).strip("-") or "device"


def child_label(name: str, unit: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"{name}_{unit}")


def unique_name(name: str, unit: str, siblings) -> str:
    """`name`, or `name-2`, `name-3` ... so that ``name@unit`` is not a sibling's: equal node names would merge."""
    taken = {f"{c.name}@{c.unit}" for c in siblings}
    cand, n = name, 1
    while f"{cand}@{unit}" in taken:
        n += 1
        cand = f"{name}-{n}"
    return cand


def gpio_pad_map(model, device) -> dict[str, tuple[str, int]]:
    """pad -> (label of the GPIO controller node, pin number), the inverse of the pad lookup in ``core.pins``."""
    from .pins import _gpio_pad
    out: dict[str, tuple[str, int]] = {}
    if device is None:
        return out
    notes: list[str] = []
    for ctrl in model.edt.nodes:
        if "gpio-controller" not in ctrl.props or not ctrl.labels or ctrl.status != "okay":
            continue
        for pin in range(0, 64):
            pad = _gpio_pad(model, device, ctrl, pin, notes)
            if pad and pad not in out:
                out[pad] = (ctrl.labels[0], pin)
    return out


def gpio_entry(gpio_map: dict[str, tuple[str, int]], pad: str, flags: int = 0) -> list:
    """Phandle-array entry ``[Ref(controller), pin, flags]`` of a pad."""
    from .translate import Ref
    if pad not in gpio_map:
        raise ValueError(f"pad {pad} has no GPIO controller")
    label, pin = gpio_map[pad]
    return [Ref(label), pin, int(flags)]


def entry_pad(gpio_map: dict[str, tuple[str, int]], entry) -> str | None:
    """Pad of an entry made by `gpio_entry` (or lifted from a build), None when unknown."""
    try:
        label, pin = entry[0].label, int(entry[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    return next((p for p, (lb, n) in gpio_map.items() if lb == label and n == pin), None)


def check_child(bus: str, compatible: str | None, address_text, specs: list[FieldSpec], texts: dict,
                gpios: list[GpioSpec], pads: dict[str, str | None]) -> dict[str, str]:
    """Problems of the child form per field (``compatible``, ``address``, a property name, a gpio property name)."""
    out: dict[str, str] = {}
    if not compatible:
        out["compatible"] = "choose a device"
    try:
        parse_address(bus, address_text)
    except ValueError as exc:
        out["address"] = str(exc)
    out.update(check_form(specs, texts))
    for g in gpios:
        if g.required and not pads.get(g.name):
            out[g.name] = "required"
    return out


def build_child(*, bus: str, compatible: str, address: int, props: dict | None = None,
                gpio_props: dict | None = None, siblings=(), label: str | None = None):
    """The ``DraftChild`` for the form state. `gpio_props`: property name -> entry (from `gpio_entry`)."""
    from .draft import DraftChild
    unit = unit_of(address)
    name = unique_name(child_node_name(compatible), unit, siblings)
    all_props = {"reg": [address], **{k: v for k, v in (props or {}).items() if v is not None}}
    for k, v in (gpio_props or {}).items():
        all_props[k] = [v]
    return DraftChild(name=name, unit=unit, compatible=[compatible], labels=[label or child_label(name, unit)],
                      props=all_props)


def child_address(child) -> int | None:
    reg = child.props.get("reg")
    return int(reg[0]) if isinstance(reg, list) and reg and isinstance(reg[0], int) else None


def address_clashes(children, addr: int, skip: str | None = None) -> list[str]:
    """Node names among `children` (DraftChild) that already sit at `addr`."""
    return [c.node_name for c in children if child_address(c) == addr and c.node_name != skip]


def set_cs_entry(props: dict, index: int, entry: list) -> dict:
    """`props` with the controller's ``cs-gpios`` entry `index` set. Entries 0..index-1 must exist."""
    entries = [list(e) for e in (props.get(CS_PROP) or [])]
    if index > len(entries):
        raise ValueError(f"chip select {index} needs cs-gpios entries 0..{index - 1} first")
    if index == len(entries):
        entries.append(entry)
    else:
        entries[index] = entry
    return {**props, CS_PROP: entries}


def drop_cs_entry(props: dict, index: int) -> dict:
    """`props` without the last ``cs-gpios`` entry when it is `index` (an inner entry stays)."""
    entries = [list(e) for e in (props.get(CS_PROP) or [])]
    if entries and index == len(entries) - 1:
        entries.pop()
    out = {k: v for k, v in props.items() if k != CS_PROP}
    if entries:
        out[CS_PROP] = entries
    return out
