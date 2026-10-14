"""Devicetree Explorer core: parsers and BuildModel (no NiceGUI)."""

import json
import pickle
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .model_host import EdtlibHost

ORIGIN_STYLE = {          # category -> (colour on dark background, caption)
    "overlay": ("#81c784", "Overlay (app)"),
    "board":   ("#64b5f6", "Board"),
    "soc":     ("#b0bec5", "SoC / Zephyr"),
    "module":  ("#ce93d8", "Module"),
    "default": ("#ffd54f", "Binding default"),   # value not in any .dts, only `default:` in the binding
    "other":   ("#ffb74d", "Other"),
}
STATE_STYLE = {           # traffic light -> (Quasar colour, short text)
    "device":   ("green",  "Device created"),
    "nobuild":  ("orange", "Driver not enabled"),
    "nodevice": ("red",    "Driver built, no device"),
    "notlinked": ("cyan-6", "Driver built, not linked yet"),
    "disabled": ("grey-7", "Disabled"),
    "neutral":  ("blue-grey-4", "No driver"),
    "nobinding": ("deep-orange", "No binding found"),
}
GPIO_FLAGS = [(0x1, "ACTIVE_LOW"), (0x2, "SINGLE_ENDED"), (0x4, "OPEN_DRAIN"),
              (0x10, "PULL_UP"), (0x20, "PULL_DOWN")]
DT_REF_RE = re.compile(r"\bDT_(NODELABEL|ALIAS|CHOSEN)\(\s*(\w+)\s*\)")
DRV_COMPAT_RE = re.compile(r"^\s*#\s*define\s+DT_DRV_COMPAT\s+(\w+)", re.M)
LINE_MARKER_RE = re.compile(r'^# (\d+) "(.+)"((?: \d+)*)\s*$')
DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)(?!\()\s*(.*?)\s*(?:/[/*].*)?$")
HELPER_HEADER = "zephyr\\include\\zephyr\\sys\\"   # internal macro helpers, hidden by default


def origin_kind(path: str) -> str:
    p = path.replace("/", "\\").lower()
    if p.endswith(".overlay"):
        return "overlay"
    if p.startswith("zephyr\\boards\\") or "\\boards\\" in p:
        return "board"
    if p.startswith("zephyr\\"):
        return "soc"
    if p.startswith("modules\\"):
        return "module"
    return "other"


def compat_ident(compat: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", compat.lower())


def node_ident(path: str) -> str:
    """Path -> C identifier as in devicetree_generated.h (DT_N_S_soc_S_...)."""
    if path == "/":
        return "DT_N"
    return "DT_N" + "".join("_S_" + re.sub(r"[^a-z0-9]", "_", part.lower())
                            for part in path.strip("/").split("/"))


def fkey(path: str) -> str:
    """Normalised key for comparing relative file paths."""
    return path.replace("/", "\\").lower()


@dataclass
class PropInfo:
    name: str
    text: str             # value as written in zephyr.dts
    file: str = ""
    line: int = 0


@dataclass
class NodeOrigin:
    file: str = ""
    line: int = 0
    props: dict = field(default_factory=dict)   # name -> PropInfo


def parse_zephyr_dts(dts: Path) -> dict:
    """Reads the origin comments of zephyr.dts: path -> NodeOrigin."""
    node_re = re.compile(r"/\* node '([^']+)' defined in (.+):(\d+) \*/")
    tail_re = re.compile(r"/\*\s*in (.+):(\d+)\s*\*/\s*$")
    result, current, buf = {}, None, []
    for raw in dts.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        m = node_re.search(line)
        if m:
            current = result.setdefault(m.group(1), NodeOrigin())
            current.file, current.line = m.group(2), int(m.group(3))
            buf = []
            continue
        if current is None or not line or line.endswith("{") or line.startswith("}") \
                or (line.startswith("/") and not buf):
            continue
        buf.append(line)
        if ";" not in line:
            continue
        stmt = " ".join(buf)
        buf = []
        t = tail_re.search(stmt)
        body = stmt[:t.start()].strip() if t else stmt
        name = re.match(r"[^\s=;]+", body).group(0)
        value = body.split("=", 1)[1].rstrip(";").strip() if "=" in body else "(present)"
        current.props[name] = PropInfo(name, value, t.group(1) if t else "", int(t.group(2)) if t else 0)
    return result


def parse_includes(pre: Path) -> dict | None:
    """Builds the include tree from the cpp line markers in zephyr.dts.pre.
    Marker flag 1 = entering an included file, flag 2 = returning to a file."""
    root, stack = None, []
    for ln in pre.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.startswith("# "):
            continue
        m = LINE_MARKER_RE.match(ln)
        if not m:
            continue
        fname, flags = m.group(2), m.group(3).split()
        if fname.startswith("<") or fname.endswith("empty_file.c"):
            continue
        path = Path(fname)
        if "1" in flags:
            node = {"path": path, "children": []}
            if root is None:
                root = node
            else:
                (stack[-1] if stack else root)["children"].append(node)
            stack.append(node)
        elif "2" in flags:
            while stack and stack[-1]["path"] != path:
                stack.pop()
    return root


def header_defines(path: Path, limit=300) -> list:
    """Object-like #defines of a header: [(name, value, line)]."""
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for no, ln in enumerate(lines, 1):
        m = DEFINE_RE.match(ln)
        if m and not m.group(1).endswith("_H_") and not m.group(1).startswith("_"):
            out.append((m.group(1), m.group(2), no))
            if len(out) >= limit:
                break
    return out


class DriverIndex:
    """compatible identifier -> driver files (search for '#define DT_DRV_COMPAT')."""
    _cache: dict = {}

    @classmethod
    def get(cls, dirs: list) -> dict:
        key = tuple(str(d) for d in dirs)
        if key not in cls._cache:
            idx: dict = {}
            for d in dirs:
                for f in Path(d).rglob("*.c"):
                    try:
                        txt = f.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if "DT_DRV_COMPAT" not in txt:
                        continue
                    for c in DRV_COMPAT_RE.findall(txt):
                        idx.setdefault(c, set()).add(f.resolve())
            cls._cache[key] = idx
        return cls._cache[key]


class BuildModel:
    def __init__(self, build: Path):
        self.build = build
        zdir = build / "zephyr"
        cache = (build / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
        self.zephyr_base = Path(self._cache_val(cache, "ZEPHYR_BASE"))
        self.app_dir = Path(self._cache_val(cache, "APPLICATION_SOURCE_DIR"))
        self.west_top = self.zephyr_base.parent

        self.edt = EdtlibHost.for_zephyr_base(self.zephyr_base).load_edt(zdir / "edt.pickle")

        self.origins = parse_zephyr_dts(zdir / "zephyr.dts")
        pre = zdir / "zephyr.dts.pre"
        self.includes = parse_includes(pre) if pre.exists() else None
        self.file_contrib = self._file_contrib()
        self.generated = (zdir / "include" / "generated" / "zephyr" / "devicetree_generated.h") \
            .read_text(encoding="utf-8", errors="replace")
        self.linked = (zdir / "zephyr.elf").exists() or (zdir / "zephyr.map").exists()
        self.device_ords = self._device_ords(cache, zdir)
        self.compiled = self._compiled_files(build)

        module_dirs, all_modules = [], []
        mods = build / "zephyr_modules.txt"
        if mods.exists():
            for ln in mods.read_text(encoding="utf-8").splitlines():
                parts = re.findall(r'"([^"]*)"', ln)
                if len(parts) >= 2:
                    all_modules.append(Path(parts[1]).resolve())
                    if parts[0] not in ("cmsis", "cmsis_6", "picolibc", "atmel"):
                        module_dirs.append(Path(parts[1]).resolve())
        scan = [self.zephyr_base / "drivers", self.zephyr_base / "soc", self.app_dir] + module_dirs
        self.drivers = DriverIndex.get([d for d in scan if d.exists()])

        # binding search path (same dts_root order Zephyr uses: app, modules, zephyr)
        roots = [self.app_dir] + all_modules + [self.zephyr_base]
        self.yaml_index: dict = {}
        for r in roots:
            bdir = r / "dts" / "bindings"
            if bdir.exists():
                for y in bdir.rglob("*.yaml"):
                    self.yaml_index.setdefault(y.name, y.resolve())
        self._yaml_cache: dict = {}

        self.by_path = {n.path: n for n in self.edt.nodes}
        # alias2node returns dtlib nodes -> map to edtlib nodes via their path
        self.aliases = {a.replace("-", "_"): self.by_path[n.path]
                        for a, n in self.edt._dt.alias2node.items() if n.path in self.by_path}
        self.chosen = {compat_ident(k): self.by_path.get(n.path, n) for k, n in self.edt.chosen_nodes.items()}
        self.code_refs = self._code_refs()

    @classmethod
    def from_edt(cls, edt, build_dir: Path, origins: dict | None = None) -> "BuildModel":
        """Light model around an EDT object that was not read from the build's edt.pickle.

        Used for what-if trees (build devicetree plus a draft overlay). Only what the pin and
        device analysis needs is filled: paths from CMakeCache.txt, ``edt``, ``origins`` (default
        empty), ``by_path``, ``aliases``, ``chosen``. The build-artifact views (driver index,
        binding index, linked/compiled state, generated header, include tree) stay empty.
        """
        self = cls.__new__(cls)
        build = Path(build_dir)
        cache = (build / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
        self.build = build
        self.zephyr_base = Path(self._cache_val(cache, "ZEPHYR_BASE"))
        self.app_dir = Path(self._cache_val(cache, "APPLICATION_SOURCE_DIR"))
        self.west_top = self.zephyr_base.parent
        self.edt = edt
        self.origins = origins or {}
        self.includes = None
        self.file_contrib = {}
        self.generated = ""
        self.linked = False
        self.device_ords = set()
        self.compiled = set()
        self.drivers = {}
        self.yaml_index = {}
        self._yaml_cache = {}
        self.by_path = {n.path: n for n in edt.nodes}
        self.aliases = {a.replace("-", "_"): self.by_path[n.path]
                        for a, n in edt._dt.alias2node.items() if n.path in self.by_path}
        self.chosen = {compat_ident(k): self.by_path.get(n.path, n) for k, n in edt.chosen_nodes.items()}
        self.code_refs = []
        return self

    @staticmethod
    def _cache_val(cache: str, key: str) -> str:
        m = re.search(rf"^{key}:\w+=(.*)$", cache, re.M)
        return m.group(1).strip() if m else ""

    def _device_ords(self, cache: str, zdir: Path) -> set:
        nm = self._cache_val(cache, "CMAKE_NM")
        elf = zdir / "zephyr.elf"
        if nm and elf.exists():
            try:
                out = subprocess.run([nm, str(elf)], capture_output=True, text=True, timeout=60).stdout
                return {int(x) for x in re.findall(r"__device_dts_ord_(\d+)\s*$", out, re.M)}
            except (OSError, subprocess.SubprocessError):
                pass
        if not (zdir / "zephyr.map").exists():
            return set()        # not linked yet (e.g. west build --cmake-only)
        mp = (zdir / "zephyr.map").read_text(errors="replace")
        mp = mp.split("Linker script and memory map", 1)[-1]
        return {int(x) for x in re.findall(r"__device_dts_ord_(\d+)", mp)}

    @staticmethod
    def _compiled_files(build: Path) -> set:
        cc = build / "compile_commands.json"
        if not cc.exists():
            return set()
        return {Path(e["file"]).resolve() for e in json.loads(cc.read_text(encoding="utf-8"))}

    def _file_contrib(self) -> dict:
        """file key -> [(node path, property name or None, line)] that ended up in the final tree."""
        out: dict = {}
        for path, o in self.origins.items():
            if o.file:
                out.setdefault(fkey(o.file), []).append((path, None, o.line))
            for p in o.props.values():
                if p.file:
                    out.setdefault(fkey(p.file), []).append((path, p.name, p.line))
        return out

    def _code_refs(self) -> list:
        refs = []
        for f in sorted((self.app_dir / "src").rglob("*.[ch]")):
            for no, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for kind, name in DT_REF_RE.findall(ln):
                    node = {"NODELABEL": self.edt.label2node, "ALIAS": self.aliases,
                            "CHOSEN": self.chosen}[kind].get(name)
                    refs.append(dict(file=self.rel(f), line=no, macro=f"DT_{kind}({name})",
                                     node=node, code=ln.strip()))
        return refs

    def rel(self, p) -> str:
        try:
            return str(Path(p).resolve().relative_to(self.west_top.resolve()))
        except ValueError:
            return str(p)

    def resolve(self, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (self.west_top / p)

    def node_lines(self, node_path: str, file: str) -> set:
        """Lines in `file` that contributed to the given node."""
        o = self.origins.get(node_path)
        if not o:
            return set()
        k = fkey(file)
        lines = {o.line} if fkey(o.file) == k else set()
        return lines | {p.line for p in o.props.values() if fkey(p.file) == k}

    # ---- bindings ----------------------------------------------------------------
    def _yaml(self, path: Path) -> dict:
        import yaml
        key = str(Path(path).resolve())
        if key not in self._yaml_cache:
            try:
                self._yaml_cache[key] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                self._yaml_cache[key] = {}
        return self._yaml_cache[key]

    @staticmethod
    def _include_names(raw: dict) -> list:
        inc = raw.get("include")
        if not inc:
            return []
        items = inc if isinstance(inc, list) else [inc]
        names = []
        for it in items:
            if isinstance(it, str):
                names.append(it)
            elif isinstance(it, dict) and "name" in it:
                names.append(it["name"])
        return names

    def binding_chain(self, path, seen=None) -> dict:
        """Binding file with its include: hierarchy -> {'path', 'children'}."""
        seen = set() if seen is None else seen
        p = Path(path).resolve()
        node = {"path": p, "children": []}
        if str(p) in seen:
            return node
        seen.add(str(p))
        for name in self._include_names(self._yaml(p)):
            inc = self.yaml_index.get(name)
            if inc:
                node["children"].append(self.binding_chain(inc, seen))
            else:
                node["children"].append({"path": Path(name), "children": [], "missing": True})
        return node

    def prop_declared_in(self, chain: dict, prop: str):
        """First file in the include hierarchy (own file first) that declares `prop`."""
        if not chain.get("missing") and prop in (self._yaml(chain["path"]).get("properties") or {}):
            return chain["path"]
        for c in chain["children"]:
            hit = self.prop_declared_in(c, prop)
            if hit:
                return hit
        return None

    @staticmethod
    def yaml_line(path: Path, prop: str) -> int | None:
        try:
            for i, ln in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
                if re.match(rf"^\s+{re.escape(prop)}\s*:\s*$", ln):
                    return i
        except OSError:
            pass
        return None

    # ---- node assessment -------------------------------------------------------
    def drivers_for(self, node) -> list:
        files = set()
        for c in node.compats:
            files |= self.drivers.get(compat_ident(c), set())
        return sorted(files)

    def state(self, node) -> tuple:
        if node.dep_ordinal in self.device_ords:
            return "device", f"Device created: __device_dts_ord_{node.dep_ordinal} is in the image."
        if node.status != "okay":
            return "disabled", f'status = "{node.status}" -> no device is created.'
        if not node.compats:
            par = node.parent
            if par is not None and par.compats:
                return "neutral", (f'Child node of "{par.compats[0]}": described by the parent\'s binding, '
                                   "gets no device of its own. The app reads its properties directly, "
                                   "e.g. with GPIO_DT_SPEC_GET().")
            return "neutral", "No compatible - pure structural node (container, group)."
        if node.path == "/" or "simple-bus" in node.compats:
            return "neutral", "Structural node (root / simple-bus) - no binding or driver needed."
        if not node.binding_path:
            return "nobinding", (f'No binding found for compatible "{node.compats[0]}". Its properties are not '
                                 "evaluated and no DT_PROP() macros are generated. Check the spelling, and "
                                 "for out-of-tree bindings the dts_root in zephyr/module.yaml or the "
                                 "app's dts/bindings folder.")
        drv = self.drivers_for(node)
        if not drv:
            return "neutral", ("No driver with a matching DT_DRV_COMPAT found - normal for e.g. CPU, "
                               "memory, or nodes managed by another driver.")
        if not any(d in self.compiled for d in drv):
            return "nobuild", ("A driver exists but is disabled in Kconfig and was not compiled. Normal if "
                               "the app does not need this hardware. Otherwise enable the driver's Kconfig "
                               "symbol in prj.conf (find it with menuconfig).")
        if not self.linked:
            return "notlinked", ("The driver is compiled, but the build is not linked yet (no zephyr.elf / "
                                 "zephyr.map), so it is unknown whether the device made it into the image. "
                                 "Run a full west build and press Reload.")
        return "nodevice", ("The driver was compiled, but there is no device for this node in the image. "
                            "Check the driver macro (DT_INST_FOREACH_STATUS_OKAY) and the binding.")

    def orphan_drivers(self) -> list:
        """Compiled module/app drivers whose compatible has no node with status = okay."""
        okay = {compat_ident(c) for n in self.edt.nodes if n.status == "okay" for c in n.compats}
        out = []
        for c, files in self.drivers.items():
            built = [f for f in files if f in self.compiled]
            if built and c not in okay and self.zephyr_base.resolve() not in built[0].parents:
                out.append((c, built))
        return out

    def node_origin_kind(self, node) -> str:
        o = self.origins.get(node.path)
        return origin_kind(o.file) if o else "other"

    def touched_by_overlay(self, node) -> bool:
        o = self.origins.get(node.path)
        return bool(o) and any(origin_kind(p.file) == "overlay" for p in o.props.values())

    def macros(self, node, limit=25) -> tuple:
        ident = node_ident(node.path)
        lines = [ln for ln in self.generated.splitlines() if ln.startswith(f"#define {ident}_P_")]
        return lines[:limit], len(lines)


def find_builds(root: Path) -> list:
    """Build folders = folders that contain zephyr/edt.pickle. `root` may itself be one."""
    if (root / "zephyr" / "edt.pickle").exists():
        return [root]
    return sorted({p.parent.parent for p in root.glob("**/zephyr/edt.pickle")
                   if "zephyr\\samples" not in str(p) and "\\tests\\" not in str(p)},
                  key=lambda p: str(p).lower())


def fmt_value(model: BuildModel, node, name: str) -> str:
    """Interpreted value from edtlib (e.g. GPIO flags decoded)."""
    prop = node.props.get(name)
    if prop is None:
        return ""
    v = prop.val

    def ref(n):
        return "&" + n.labels[0] if n.labels else n.path

    def cad(x):
        if x is None:
            return "-"
        data = dict(x.data)
        if "gpio-controller" in x.controller.props and "flags" in data:
            fl = data["flags"]
            names = [n for bit, n in GPIO_FLAGS if fl & bit] or ["ACTIVE_HIGH"]
            data["flags"] = f"0x{fl:x} = " + " | ".join(names)
        return f"{ref(x.controller)}  " + ", ".join(f"{k}={val}" for k, val in data.items())

    t = prop.type
    if t in ("phandle", "path"):
        return ref(v)
    if t == "phandles":
        return ", ".join(ref(n) for n in v)
    if t == "phandle-array":
        return " ; ".join(cad(x) for x in v)
    if t == "int":
        return f"{v} (0x{v:x})"
    if t == "array":
        return ", ".join(f"0x{x:x}" for x in v)
    if t == "uint8-array":
        return v.hex(" ")
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


# ---------------------------------------------------------------------------------
