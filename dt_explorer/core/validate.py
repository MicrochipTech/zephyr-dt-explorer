"""Fast in-process check of a draft overlay against a build.

The overlay is added to the devicetree sources of the build the way Zephyr adds a user overlay (one
more ``-include`` of the same preprocessor call that produced ``zephyr.dts.pre``), so macros in the
overlay are resolved by the build's own include paths. edtlib then parses the result with the
build's binding directories. Nothing is written into the build folder.

Findings:

* ``preprocess``: the C preprocessor rejected the overlay.
* ``edtlib``: edtlib rejected the merged tree (``missing_property`` when a binding's required
  property is absent; the message names the node and the property). edtlib stops at the first error.
* ``pin_conflict``: one pad is used by two or more nodes. Error when a node touched by the overlay
  takes part, otherwise ``info`` (``pin_conflict_existing``): the build already had it.
* ``bus_address_duplicate``: two devices on one I2C bus with the same address, or two SPI devices with the
  same chip-select index (error when one of them comes from the overlay, otherwise ``info``,
  ``bus_address_duplicate_existing``). Two nodes with equal name and unit address would be merged by the
  compiler, so the configurator never writes those.
* ``spi_cs_missing``: an SPI device whose chip-select index is beyond the controller's ``cs-gpios`` list.
* a GPIO pad (``cs-gpios``, ``int-gpios``, ``reset-gpios`` ...) that another node, or the same node's pinctrl,
  also uses is a ``pin_conflict`` like any other pad clash.
* ``analog_digital_pad``: a pad selected as analog input by an ADC channel (``zephyr,input-positive`` /
  ``-negative``; the converter needs no pinctrl for it) that another node uses digitally (GPIO, pinctrl
  digital function). Warning when a node touched by the overlay takes part, otherwise ``info`` (``..._existing``).
* ``adc_channel_duplicate``: two channels of one ADC with the same ``reg`` or the same
  ``zephyr,input-positive`` / ``zephyr,input-negative`` pair (error, ``info`` when already in the build).
* ``pwm_channel_duplicate``: two ``pwms`` entries on the same channel of one PWM controller (error, ``info``
  when already in the build).
* ``interrupt_line_shared``: two interrupt GPIOs (``int-gpios``, ``irq-gpios``, ``interrupt-gpios``, the
  ``gpios`` of ``gpio-keys`` children) on pads that share one external-interrupt line (warning, ``info``
  when already in the build).
* ``provider_constraint``: signals of one instance come from different ``Signal.group`` values; the
  text is the one the device data provider supplied in its ``Constraint``.

The module knows no vendor: pins, groups and texts come from ``core.pins`` and the device model. Which
``Signal.attrs["signal_group"]`` names mean "analog" or "interrupt line" is a short table of generic names
here (``ANALOG_SIGNAL_GROUPS``, ``INTERRUPT_LINE_GROUPS``); a provider with other names would extend them.
"""
from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from .build import BuildModel, NodeOrigin, PropInfo
from .buildinfo import parse_cmake_cache, read_build_info
from .model_host import EdtlibHost
from .pins import used_pins

GROUP_CONSTRAINT_KIND = "shared_group"

# Signal groups (Signal.attrs["signal_group"]) that are analog functions of a pad, and the group that
# names an external-interrupt line (its Signal.attrs["index"] is the line number).
ANALOG_SIGNAL_GROUPS = frozenset({"AIN", "X", "Y", "VOUT", "VREFA", "VREFB", "VREFC"})
ANALOG_INPUT_GROUP = "AIN"          # its index is what an ADC channel's zephyr,input-positive/-negative selects
INTERRUPT_LINE_GROUPS = frozenset({"EXTINT"})
INTERRUPT_GPIO_PROPS = frozenset({"int-gpios", "irq-gpios", "interrupt-gpios"})


@dataclass
class Finding:
    severity: str                       # "error" | "warning" | "info"
    kind: str
    message: str
    node: str | None = None             # devicetree path
    pad: str | None = None
    nodes: tuple[str, ...] = ()         # all nodes involved (conflicts)
    overlay: bool = False               # the overlay touches a node involved


@dataclass
class _Spec:
    compiler: str
    files: list[str]
    include_dirs: list[str]
    bindings_dirs: list[str]
    extra_flags: list[str]
    app_dir: str
    empty_file: str
    zephyr_base: Path


_spec_cache: dict[Path, tuple[float, _Spec]] = {}


def _spec(build: Path) -> _Spec:
    """How the build preprocessed its devicetree, from build_info.yml and CMakeCache.txt."""
    info_yml = build / "build_info.yml"
    stamp = info_yml.stat().st_mtime
    hit = _spec_cache.get(build)
    if hit and hit[0] == stamp:
        return hit[1]
    cache = parse_cmake_cache(build / "CMakeCache.txt")
    dt = (yaml.safe_load(info_yml.read_text(encoding="utf-8")) or {}).get("cmake", {}).get("devicetree", {})
    zb = Path(cache.get("ZEPHYR_BASE", ""))
    extra = cache.get("DTS_EXTRA_CPPFLAGS") or ""
    spec = _Spec(
        compiler=cache.get("CMAKE_DTS_PREPROCESSOR") or cache.get("CMAKE_C_COMPILER", ""),
        files=list(dt.get("files") or []),
        include_dirs=list(dt.get("include-dirs") or []),
        bindings_dirs=list(dt.get("bindings-dirs") or []),
        extra_flags=[f for f in extra.split(";") if f],
        app_dir=cache.get("APPLICATION_SOURCE_DIR", ""),
        empty_file=str(zb / "misc" / "empty_file.c"),
        zephyr_base=zb,
    )
    _spec_cache[build] = (stamp, spec)
    return spec


def _edtlib(zephyr_base: Path):
    host = EdtlibHost.for_zephyr_base(zephyr_base)
    return host.dtlib, host.edtlib


def preprocess(build: Path, overlay_text: str, out_dir: Path) -> tuple[Path, Path]:
    """Run the build's preprocessor on its sources plus the overlay. Returns (overlay file, .pre file).

    Same call as Zephyr's ``zephyr_dt_preprocess``: every source file is a ``-include`` of the empty
    file; the overlay is appended as the last ``-include``.
    """
    spec = _spec(build)
    ov = out_dir / "draft.overlay"
    ov.write_text(overlay_text, encoding="utf-8")
    pre = out_dir / "draft.dts.pre"
    ov_arg = ov.as_posix()      # no backslashes: cpp would double them in its line markers
    cmd = [spec.compiler, "-x", "assembler-with-cpp", "-nostdinc"]
    for d in spec.include_dirs:
        cmd += ["-isystem", d]
    for f in [*spec.files, ov_arg]:
        cmd += ["-include", f]
    cmd += ["-undef", "-D__DTS__", *spec.extra_flags, "-E", "-o", str(pre), spec.empty_file]
    proc = subprocess.run(cmd, cwd=spec.app_dir or None, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"preprocessor exit code {proc.returncode}")
    return ov, pre


_text_cache: dict[str, tuple[int, str]] = {}


def _cached_open(path, mode="r", encoding=None, **kw):
    """Stand-in for ``open`` inside edtlib only: binding files by path and mtime from memory.

    edtlib opens every binding file (about 3400 for a Zephyr tree) to find the compatibles; on
    Windows that costs 2-3 s per run. Only plain text reads are served from the cache.
    """
    if mode != "r" or kw:
        return open(path, mode, encoding=encoding, **kw)
    stamp = os.stat(path).st_mtime_ns
    hit = _text_cache.get(path)
    if hit is None or hit[0] != stamp:
        with open(path, encoding=encoding or "utf-8") as f:
            hit = _text_cache[path] = (stamp, f.read())
    return io.StringIO(hit[1])


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _origins(edt) -> dict:
    """pinmux property positions (file, line) from dtlib, for core.pins to re-read macro names."""
    out = {}
    for node in edt.nodes:
        prop = node._node.props.get("pinmux")
        if prop is not None and prop.filename:
            out[node.path] = NodeOrigin(props={"pinmux": PropInfo("pinmux", "", prop.filename, prop.lineno)})
    return out


def _norm(p) -> str:
    return re.sub(r"[\\/]+", "/", str(p)).lower()


def _touched(edt, overlay_file: Path) -> set[str]:
    """Paths of nodes with a property from the overlay, plus nodes whose pinctrl state nodes have one."""
    key = _norm(overlay_file)
    own = {n.path for n in edt.nodes
           if any(_norm(p.filename or "") == key for p in n._node.props.values())}
    touched = set(own)
    for node in edt.nodes:
        for pc in getattr(node, "pinctrls", None) or []:
            if any(c.path in own or any(ch.path in own for ch in c.children.values())
                   for c in pc.conf_nodes):
                touched.add(node.path)
    return touched


def _label(node) -> str:
    return node.labels[0] if node.labels else node.path


_REQUIRED_RE = re.compile(r"'([^']+)' is marked as required in 'properties:' in (\S+), "
                          r"but does not appear in <Node (/\S*)")


def _edtlib_error(msg: str) -> Finding:
    m = _REQUIRED_RE.search(msg)
    if m:
        prop, binding, node = m.groups()
        name = re.split(r"[\\/]", binding)[-1]
        return Finding("error", "missing_property",
                       f"required property '{prop}' is missing on {node} (binding {name})",
                       node=node, overlay=True)
    m = re.search(r"<Node (/\S*)", msg)
    return Finding("error", "edtlib", msg.strip(), node=m.group(1) if m else None, overlay=True)


def _conflicts(usage, model, touched: set[str]) -> list[Finding]:
    out = []
    for pad, uses in sorted(usage.uses.items()):
        paths = list(dict.fromkeys(u.node for u in uses))
        if len(paths) < 2 and len({u.source for u in uses}) < 2:      # one node: a pinctrl pad that is also a GPIO
            continue
        parts = []
        for path in paths:
            n = model.by_path.get(path)
            fns = ", ".join(dict.fromkeys(u.function for u in uses if u.node == path))
            parts.append(f"{_label(n) if n else path} ({path}: {fns})")
        involved = any(p in touched for p in paths)
        what = f"{len(paths)} nodes" if len(paths) > 1 else "one node as pinctrl pin and as GPIO"
        out.append(Finding("error" if involved else "info",
                           "pin_conflict" if involved else "pin_conflict_existing",
                           f"pad {pad} is used by {what}: " + "; ".join(parts),
                           node=paths[0], pad=pad, nodes=tuple(paths), overlay=involved))
    return out


def _bus_checks(edt, touched: set[str]) -> list[Finding]:
    """Duplicate I2C addresses / SPI chip-select indexes on one bus, SPI chip selects without a cs-gpios entry."""
    out = []
    for ctrl in edt.nodes:
        kids = [c for c in ctrl.children.values()
                if c.status == "okay" and c.on_buses and c.regs and c.on_buses[0] in ("i2c", "spi")]
        by_reg: dict[tuple[str, int], list] = {}
        for c in kids:
            by_reg.setdefault((c.on_buses[0], c.regs[0].addr), []).append(c)
        for (bus, addr), group in sorted(by_reg.items()):
            if len(group) < 2:
                continue
            involved = any(c.path in touched for c in group)
            what = f"I2C address 0x{addr:02x}" if bus == "i2c" else f"SPI chip select {addr}"
            names = ", ".join(f"{c.name} ({c.path})" for c in group)
            out.append(Finding("error" if involved else "info",
                               "bus_address_duplicate" if involved else "bus_address_duplicate_existing",
                               f"{what} is used by {len(group)} devices on &{_label(ctrl)}: {names}",
                               node=group[0].path, nodes=tuple(c.path for c in group), overlay=involved))
        cs = ctrl.props.get("cs-gpios")
        if cs is not None:
            n_cs = len(cs.val)
            for c in kids:
                if c.on_buses[0] == "spi" and c.regs[0].addr >= n_cs and (c.path in touched or ctrl.path in touched):
                    out.append(Finding("error", "spi_cs_missing",
                                       f"{c.name} uses chip select {c.regs[0].addr} but &{_label(ctrl)} has only "
                                       f"{n_cs} cs-gpios entr{'y' if n_cs == 1 else 'ies'}",
                                       node=c.path, nodes=(c.path, ctrl.path), overlay=True))
    return out


def _signal_of(device, pad: str, function: str):
    """The device-model signal behind a pinctrl use (``"<mux> <instance>.<role>"``), or None."""
    try:
        mux, rest = function.split(" ", 1)
        inst_name, role = rest.rsplit(".", 1)
    except ValueError:
        return None
    inst = device.instances.get(inst_name)
    if inst is None:
        return None
    return next((s for s in inst.signals if s.pad == pad and s.role == role and str(s.mux) == mux), None)


def _who(model, uses) -> str:
    return "; ".join(f"{_label(model.by_path[u.node]) if u.node in model.by_path else u.node} "
                     f"({u.node}: {u.function})" for u in uses)


def _adc_channels(edt):
    """(controller, [channel child]) for every enabled node whose children carry ``zephyr,input-positive``."""
    for ctrl in edt.nodes:
        if ctrl.status != "okay":
            continue
        kids = [c for c in ctrl.children.values() if c.status == "okay" and "zephyr,input-positive" in c.props]
        if kids:
            yield ctrl, kids


def _analog_digital(edt, usage, model, touched: set[str]) -> list[Finding]:
    """A pad that an ADC channel selects as analog input while another node uses it digitally.

    The converter's channel children (``zephyr,input-positive`` / ``-negative``) pick the input by number;
    the converter itself needs no pinctrl entry for it, so the pin conflict check does not see the pad.
    A digital user on the same pad is reported here (pinctrl states that already claim the pad as analog and
    digital show up as ``pin_conflict``).
    """
    device = usage.device
    if device is None:
        return []
    out = []
    for ctrl, kids in _adc_channels(edt):
        inst = next((i for i in device.instances.values() if ctrl.regs and i.base == ctrl.regs[0].addr), None)
        if inst is None:
            continue
        pad_of = {s.attrs["index"]: s.pad for s in inst.signals
                  if s.attrs.get("signal_group") == ANALOG_INPUT_GROUP and s.attrs.get("index") is not None}
        for kid in kids:
            for prop in ("zephyr,input-positive", "zephyr,input-negative"):
                p = kid.props.get(prop)
                pad = pad_of.get(p.val) if p is not None else None
                digital = [u for u in usage.uses.get(pad, ())
                           if u.node != ctrl.path and (u.source == "gpio" or getattr(
                               _signal_of(device, pad, u.function), "attrs", {}).get("signal_group")
                               not in ANALOG_SIGNAL_GROUPS)]
                if not digital:
                    continue
                paths = list(dict.fromkeys([kid.path, *(u.node for u in digital)]))
                involved = any(x in touched for x in [*paths, ctrl.path])
                out.append(Finding("warning" if involved else "info",
                                   "analog_digital_pad" if involved else "analog_digital_pad_existing",
                                   f"pad {pad} is analog input {p.val} of &{_label(ctrl)} ({kid.name}: {prop}) "
                                   f"and is used digitally by {_who(model, digital)}",
                                   node=kid.path, pad=pad, nodes=tuple(paths), overlay=involved))
    return out


def _adc_checks(edt, touched: set[str]) -> list[Finding]:
    """Two channels of one converter with the same reg or the same input pair."""
    out = []
    for ctrl, kids in _adc_channels(edt):
        seen: dict[tuple, list] = {}
        for c in kids:
            if c.regs:
                seen.setdefault(("reg", c.regs[0].addr, None), []).append(c)
            neg = c.props.get("zephyr,input-negative")
            seen.setdefault(("input", c.props["zephyr,input-positive"].val, neg.val if neg is not None else None),
                            []).append(c)
        for key, group in sorted(seen.items(), key=lambda kv: str(kv[0])):
            if len(group) < 2:
                continue
            if key[0] == "reg":
                what = f"channel number {key[1]}"
            else:
                what = f"input {key[1]}" + (f" (negative input {key[2]})" if key[2] is not None else "")
            involved = any(c.path in touched for c in group) or ctrl.path in touched
            names = ", ".join(f"{c.name} ({c.path})" for c in group)
            out.append(Finding("error" if involved else "info",
                               "adc_channel_duplicate" if involved else "adc_channel_duplicate_existing",
                               f"{what} is used by {len(group)} channels of &{_label(ctrl)}: {names}",
                               node=group[0].path, nodes=tuple(c.path for c in group), overlay=involved))
    return out


def _pwm_checks(edt, touched: set[str]) -> list[Finding]:
    """Two ``pwms`` entries on the same channel of one PWM controller."""
    users: dict[tuple[str, int], list] = {}
    ctrls = {}
    for node in edt.nodes:
        prop = node.props.get("pwms")
        if node.status != "okay" or prop is None or prop.type != "phandle-array":
            continue
        for entry in prop.val:
            ctrl = getattr(entry, "controller", None)
            ch = (getattr(entry, "data", None) or {}).get("channel")
            if ctrl is None or ch is None:
                continue
            ctrls[ctrl.path] = ctrl
            users.setdefault((ctrl.path, ch), []).append(node)
    out = []
    for (cpath, ch), nodes in sorted(users.items()):
        if len(nodes) < 2:
            continue
        involved = any(n.path in touched for n in nodes)
        names = ", ".join(f"{n.name} ({n.path})" for n in nodes)
        out.append(Finding("error" if involved else "info",
                           "pwm_channel_duplicate" if involved else "pwm_channel_duplicate_existing",
                           f"PWM channel {ch} of &{_label(ctrls[cpath])} is used by {len(nodes)} consumers: {names}",
                           node=nodes[0].path, nodes=tuple(n.path for n in nodes), overlay=involved))
    return out


def _is_interrupt_use(model, use) -> bool:
    if use.source != "gpio":
        return False
    if use.role in INTERRUPT_GPIO_PROPS:
        return True
    parent = getattr(model.by_path.get(use.node), "parent", None)
    return use.role == "gpios" and parent is not None and "gpio-keys" in (parent.compats or [])


def _interrupt_lines(usage, model, touched: set[str]) -> list[Finding]:
    """Interrupt GPIOs on different pads that share one external-interrupt line."""
    device = usage.device
    if device is None:
        return []
    line_of: dict[str, set[int]] = {}
    for inst in device.instances.values():
        for s in inst.signals:
            if s.attrs.get("signal_group") in INTERRUPT_LINE_GROUPS and s.attrs.get("index") is not None:
                line_of.setdefault(s.pad, set()).add(s.attrs["index"])
    by_line: dict[int, list] = {}
    for pad, uses in usage.uses.items():
        for u in uses:
            if _is_interrupt_use(model, u):
                for line in line_of.get(pad, ()):
                    by_line.setdefault(line, []).append(u)
    out = []
    for line, uses in sorted(by_line.items()):
        distinct = list(dict.fromkeys((u.node, u.pad) for u in uses))
        if len({pad for _, pad in distinct}) < 2:
            continue                # one pad twice is a pin_conflict, not a shared line
        paths = list(dict.fromkeys(n for n, _ in distinct))
        involved = any(p in touched for p in paths)
        parts = "; ".join(f"{_label(model.by_path[n]) if n in model.by_path else n} ({n}: {pad})"
                          for n, pad in distinct)
        out.append(Finding("warning" if involved else "info",
                           "interrupt_line_shared" if involved else "interrupt_line_shared_existing",
                           f"interrupt line {line} is shared by {len(distinct)} interrupt GPIOs "
                           f"on different pads: {parts}",
                           node=paths[0], nodes=tuple(paths), overlay=involved))
    return out


def _group_mixes(usage, model, touched: set[str]) -> list[Finding]:
    device = usage.device
    if device is None:
        return []
    constraint = next((c for c in device.constraints if c.kind == GROUP_CONSTRAINT_KIND), None)
    seen: dict[tuple[str, str], list[tuple[str, set[str]]]] = {}
    for pad, uses in usage.uses.items():
        for u in uses:
            if u.source != "pinctrl" or u.node not in touched:
                continue
            try:
                mux, rest = u.function.split(" ", 1)
                inst_name, role = rest.rsplit(".", 1)
            except ValueError:
                continue
            inst = device.instances.get(inst_name)
            if inst is None:
                continue
            sig = next((s for s in inst.signals
                        if s.pad == pad and s.role == role and str(s.mux) == mux), None)
            if sig is None or sig.group is None:
                continue
            seen.setdefault((u.node, inst_name), []).append((pad, set(str(sig.group).split())))
    out = []
    for (path, inst_name), sigs in sorted(seen.items()):
        if len(sigs) < 2 or set.intersection(*(g for _, g in sigs)):
            continue        # fewer than two grouped signals, or one group common to all of them
        desc = ", ".join(f"{pad}: group {' or '.join(sorted(g))}" for pad, g in sorted(sigs))
        text = constraint.message if constraint else "signals of one instance come from different groups."
        n = model.by_path.get(path)
        out.append(Finding(constraint.severity if constraint else "warning", "provider_constraint",
                           f"{_label(n) if n else path} ({inst_name}): {desc}. {text}",
                           node=path, nodes=(path,), overlay=True))
    return out


def validate(build_dir, overlay_text: str, *, timings: dict | None = None) -> list[Finding]:
    """Check `overlay_text` against the build in `build_dir`. Fills `timings` (seconds) when given."""
    build = Path(build_dir)
    t: dict = timings if timings is not None else {}
    t0 = time.perf_counter()
    spec = _spec(build)
    dtlib, edtlib = _edtlib(spec.zephyr_base)
    with tempfile.TemporaryDirectory(prefix="dtx_validate_") as tmp:
        try:
            ov, pre = preprocess(build, overlay_text, Path(tmp))
        except (RuntimeError, OSError) as exc:
            return [Finding("error", "preprocess", str(exc))]
        t["preprocess"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        cap = _Capture()
        log = logging.getLogger("devicetree.edtlib")
        log.addHandler(cap)
        edtlib.open = _cached_open      # module global: shadows the builtin for edtlib only
        try:
            edt = edtlib.EDT(str(pre), spec.bindings_dirs, warn_reg_unit_address_mismatch=False,
                             default_prop_types=True, infer_binding_for_paths=["/zephyr,user", "/cpus"],
                             werror=False)
        except (edtlib.EDTError, dtlib.DTError) as exc:
            t["edtlib"] = time.perf_counter() - t1
            return [_edtlib_error(str(exc))]
        finally:
            del edtlib.open
            log.removeHandler(cap)
        t["edtlib"] = time.perf_counter() - t1
        t2 = time.perf_counter()
        touched = _touched(edt, ov)
        model = BuildModel.from_edt(edt, build, _origins(edt))
        usage = used_pins(model, read_build_info(build))
        t["pins"] = time.perf_counter() - t2
    findings = [Finding("info", "edtlib_warning", m) for m in dict.fromkeys(cap.records)]
    findings += _conflicts(usage, model, touched)
    findings += _group_mixes(usage, model, touched)
    findings += _bus_checks(edt, touched)
    findings += _analog_digital(edt, usage, model, touched)
    findings += _adc_checks(edt, touched)
    findings += _pwm_checks(edt, touched)
    findings += _interrupt_lines(usage, model, touched)
    t["total"] = time.perf_counter() - t0
    return findings
