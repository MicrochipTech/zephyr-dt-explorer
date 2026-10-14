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
* ``provider_constraint``: signals of one instance come from different ``Signal.group`` values; the
  text is the one the device data provider supplied in its ``Constraint``.

The module knows no vendor: pins, groups and texts come from ``core.pins`` and the device model.
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
from .pins import used_pins

GROUP_CONSTRAINT_KIND = "shared_group"


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
    src = zephyr_base / "scripts" / "dts" / "python-devicetree" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from devicetree import dtlib, edtlib
    return dtlib, edtlib


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
        if len(paths) < 2:
            continue
        parts = []
        for path in paths:
            n = model.by_path.get(path)
            fns = ", ".join(dict.fromkeys(u.function for u in uses if u.node == path))
            parts.append(f"{_label(n) if n else path} ({path}: {fns})")
        involved = any(p in touched for p in paths)
        out.append(Finding("error" if involved else "info",
                           "pin_conflict" if involved else "pin_conflict_existing",
                           f"pad {pad} is used by {len(paths)} nodes: " + "; ".join(parts),
                           node=paths[0], pad=pad, nodes=tuple(paths), overlay=involved))
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
    t["total"] = time.perf_counter() - t0
    return findings
