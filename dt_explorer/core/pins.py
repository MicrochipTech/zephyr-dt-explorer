"""Used pins of a build, computed from the final devicetree.

Two sources, both generic:

* ``pinctrl``: every node's ``pinctrl-N`` states. The pinmux values in ``edt.pickle`` are packed
  integers. The formatter registered for the pin controller's compatible only decodes macro *names*
  (``formatter.parse``), so the names are re-read from the devicetree source text at the position
  recorded in ``zephyr.dts`` and parsed by the formatter. A property whose names cannot be matched to
  its values (count differs, name unknown to the formatter) is reported as ``unresolved``, never
  guessed.
* ``gpio``: ``gpios`` / ``*-gpios`` phandle-arrays. The pad of "controller + pin" is derived from the
  device model (see ``_gpio_pad``).

Formatters and providers are only reached through ``dt_explorer.core.plugins``. A formatter that needs
build context can offer an optional ``for_workspace(workspace, soc)`` classmethod; it is used when
present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import plugins
from .buildinfo import BuildInfo, read_build_info
from .device import DeviceModel

_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_CELLS_RE = re.compile(r"<([^>]*)>")
_PAD_RE = re.compile(r"^(\D+)(\d+)$")

_device_cache: dict[Path, DeviceModel] = {}


@dataclass
class PinUse:
    node: str                   # devicetree path of the user (not the pinctrl state node)
    role: str                   # signal role ("PAD0") or property name ("gpios")
    function: str               # "D SERCOM2.PAD0" or "GPIO"
    source: str                 # "pinctrl" | "gpio"
    state: str | None = None    # pinctrl state name ("default")
    pad: str | None = None


@dataclass
class PinUsage:
    device: DeviceModel | None
    uses: dict[str, list[PinUse]] = field(default_factory=dict)    # pad -> uses
    unresolved: list[PinUse] = field(default_factory=list)         # uses without a pad
    notes: list[str] = field(default_factory=list)

    def add(self, use: PinUse) -> None:
        self.uses.setdefault(use.pad, []).append(use)


# --- device model ----------------------------------------------------------------------------

def load_device(info: BuildInfo) -> tuple[DeviceModel | None, str | None]:
    """Device model for the build via the registered provider, or (None, reason)."""
    provider = plugins.find_provider(info)
    if provider is None:
        return None, "no device data provider matches this build"
    path = provider.locate(info)
    if path is None:
        return None, f"provider {provider.name} found no device file for SoC {info.soc}"
    if path not in _device_cache:
        _device_cache[path] = provider.load(path)
    return _device_cache[path], None


def default_package(device: DeviceModel):
    """The package with the most pins (the board's package is not recorded in the build)."""
    return max(device.packages, key=lambda p: p.pin_count) if device.packages else None


def device_pads(device: DeviceModel) -> set[str]:
    """Pad ids that carry at least one signal."""
    return {s.pad for inst in device.instances.values() for s in inst.signals}


def pad_functions(device: DeviceModel) -> dict[str, list[str]]:
    """pad -> all possible functions ``"<mux> <instance>.<role>"``, sorted by mux, instance, role."""
    out: dict[str, set[tuple]] = {}
    for iname, inst in device.instances.items():
        for s in inst.signals:
            out.setdefault(s.pad, set()).add((str(s.mux), s.attrs.get("instance") or iname, s.role))
    return {pad: [f"{m} {i}.{r}" for m, i, r in sorted(v)] for pad, v in out.items()}


# --- pinctrl ---------------------------------------------------------------------------------

def _controller_compats(node) -> list[str]:
    n = node.parent
    while n is not None:
        if n.compats:
            return list(n.compats)
        n = n.parent
    return []


def _formatter_for(compats: list[str], info: BuildInfo, cache: dict):
    for c in compats:
        if c in cache:
            return cache[c]
        f = plugins.get_formatter(c)
        if f is not None and hasattr(f, "for_workspace") and info.workspace and info.soc:
            try:
                f = f.for_workspace(info.workspace, info.soc)
            except Exception:  # noqa: BLE001 - e.g. header not found; reported by the caller
                f = None
        cache[c] = f
        if f is not None:
            return f
    return None


def _source_names(model, prop_info) -> list[str]:
    """Identifiers inside the ``<...>`` cells of the property statement at its source position."""
    if not prop_info or not prop_info.file or not prop_info.line:
        return []
    path = model.resolve(prop_info.file)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    stmt, i = [], prop_info.line - 1
    while i < len(lines):
        stmt.append(lines[i])
        if ";" in lines[i].split("//")[0]:
            break
        i += 1
    text = re.sub(r"/\*.*?\*/", " ", " ".join(stmt))
    text = text.split("=", 1)[-1]
    return [n for cells in _CELLS_RE.findall(text) for n in _IDENT_RE.findall(cells)]


def _pinctrl_uses(model, info: BuildInfo, usage: PinUsage) -> None:
    fmt_cache: dict = {}
    for node in model.edt.nodes:
        if node.status != "okay":
            continue
        for pc in getattr(node, "pinctrls", None) or []:
            for conf in pc.conf_nodes:
                for group in [conf, *conf.children.values()]:
                    prop = group.props.get("pinmux")
                    if prop is None:
                        continue
                    _decode_group(model, info, usage, fmt_cache, node, pc, conf, group, prop)


def _decode_group(model, info, usage, fmt_cache, node, pc, conf, group, prop) -> None:
    state = pc.name if isinstance(pc.name, str) else str(pc.name)
    values = list(prop.val) if isinstance(prop.val, (list, tuple)) else [prop.val]
    fmt = _formatter_for(_controller_compats(conf), info, fmt_cache)
    origin = model.origins.get(group.path)
    names = _source_names(model, origin.props.get("pinmux") if origin else None)
    parsed = []
    if fmt is not None and len(names) == len(values):
        try:
            parsed = [fmt.parse(n) for n in names]
        except Exception as exc:  # noqa: BLE001 - formatter error types are plugin specific
            usage.notes.append(f"{group.path}: pinmux names not understood by formatter: {exc}")
    if not parsed:
        if fmt is None:
            usage.notes.append(f"{group.path}: no pinctrl formatter for {_controller_compats(conf)}")
        for v in values:
            usage.unresolved.append(PinUse(node.path, "pinmux", f"0x{int(v):x}", "pinctrl", state))
        return
    for p in parsed:
        usage.add(PinUse(node.path, p.role, f"{p.mux} {p.instance}.{p.role}", "pinctrl", state, p.pad))


# --- gpio ------------------------------------------------------------------------------------

def _gpio_controllers(model, controller) -> list:
    """All nodes of the same kind as `controller` (same compatible, ``gpio-controller``), by address."""
    same = [n for n in model.edt.nodes
            if "gpio-controller" in n.props and n.compats == controller.compats]
    return sorted(same, key=lambda n: (n.regs[0].addr if n.regs else 0, n.path))


def _gpio_pad(model, device: DeviceModel, controller, pin: int, notes: list[str]) -> str | None:
    """Pad of "GPIO controller + pin number".

    Assumption (documented limit): the controllers of one kind, ordered by register address, map in
    order to the device's pad groups, where a group is the set of pads with the same alphabetic prefix
    (``<prefix><number>``), ordered by prefix. This is only applied when both lists have the same
    length; otherwise the use is reported as unresolved instead of guessed.
    """
    controllers = _gpio_controllers(model, controller)
    groups: dict[str, set[int]] = {}
    for pad in device_pads(device):
        m = _PAD_RE.match(pad)
        if m:
            groups.setdefault(m.group(1), set()).add(int(m.group(2)))
    prefixes = sorted(groups)
    if len(controllers) != len(prefixes):
        notes.append(f"gpio mapping skipped: {len(controllers)} controllers vs {len(prefixes)} pad groups")
        return None
    prefix = prefixes[controllers.index(controller)]
    if pin not in groups[prefix]:
        return None
    width = max(len(pad) - len(prefix) for pad in device_pads(device) if pad.startswith(prefix))
    return f"{prefix}{pin:0{width}d}"


def _gpio_uses(model, usage: PinUsage) -> None:
    for node in model.edt.nodes:
        if node.status != "okay":
            continue
        for name, prop in node.props.items():
            if prop.type != "phandle-array" or not (name == "gpios" or name.endswith("-gpios")):
                continue
            for entry in prop.val:
                ctrl = getattr(entry, "controller", None)
                pin = (getattr(entry, "data", None) or {}).get("pin")
                if ctrl is None or pin is None or "gpio-controller" not in ctrl.props:
                    continue
                pad = _gpio_pad(model, usage.device, ctrl, pin, usage.notes) if usage.device else None
                use = PinUse(node.path, name, "GPIO", "gpio", None, pad)
                if pad:
                    usage.add(use)
                else:
                    use.function = f"GPIO {ctrl.path.rsplit('/', 1)[-1]} pin {pin}"
                    usage.unresolved.append(use)


# --- entry point -----------------------------------------------------------------------------

def used_pins(model, info: BuildInfo | None = None) -> PinUsage:
    """Pins used by the devicetree of `model` (a ``BuildModel``)."""
    info = info or read_build_info(model.build)
    device, reason = load_device(info)
    usage = PinUsage(device)
    if reason:
        usage.notes.append(reason)
    _pinctrl_uses(model, info, usage)
    _gpio_uses(model, usage)
    usage.notes = list(dict.fromkeys(usage.notes))
    return usage


def pin_rows(usage: PinUsage, package_name: str | None = None) -> list[dict]:
    """One row per package pin: position, pad, used_by (list of PinUse), functions."""
    dev = usage.device
    if dev is None or not dev.packages:
        return []
    pkg = next((p for p in dev.packages if p.name == package_name), None) or default_package(dev)
    funcs = pad_functions(dev)
    pads = device_pads(dev)
    rows = []
    for pos, pad in pkg.pins.items():
        is_pad = pad in pads
        rows.append({"position": pos, "pad": pad, "is_pad": is_pad,
                     "uses": usage.uses.get(pad, []), "functions": funcs.get(pad, [])})
    rows.sort(key=lambda r: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", r["position"])])
    return rows
