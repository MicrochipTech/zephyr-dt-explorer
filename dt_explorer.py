"""Devicetree Explorer - makes the Zephyr devicetree of a build understandable in the browser.

Reads from a build folder (e.g. apps/lab1/build_same54_xpro):
  zephyr/zephyr.dts           merged devicetree incl. origin (file:line) of every property
  zephyr/zephyr.dts.pre       preprocessed devicetree; its line markers give the include tree
  zephyr/edt.pickle           Zephyr's evaluated devicetree (bindings, ordinals, references)
  zephyr/zephyr.elf           which devices actually made it into the image (__device_dts_ord_N)
  compile_commands.json       which driver source files were compiled
  zephyr_modules.txt          module directories (for the driver search)

Start:  py dt_explorer.py [zephyrproject folder] [--no-browser]
"""
import html
import json
import pickle
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import ui

DEFAULT_ROOT = Path(r"C:\Masters\26020_RTOS4\zephyrproject")
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
ROOT = Path(_ARGS[0]) if _ARGS else DEFAULT_ROOT
SHOW_BROWSER = "--no-browser" not in sys.argv

ORIGIN_STYLE = {          # category -> (colour on dark background, caption)
    "overlay": ("#81c784", "Overlay (app)"),
    "board":   ("#64b5f6", "Board"),
    "soc":     ("#b0bec5", "SoC / Zephyr"),
    "module":  ("#ce93d8", "Module"),
    "other":   ("#ffb74d", "Other"),
}
STATE_STYLE = {           # traffic light -> (Quasar colour, short text)
    "device":   ("green",  "Device created"),
    "nobuild":  ("orange", "Driver not enabled"),
    "nodevice": ("red",    "Driver built, no device"),
    "disabled": ("grey-7", "Disabled"),
    "neutral":  ("blue-grey-4", "No driver"),
}
GPIO_FLAGS = [(0x1, "ACTIVE_LOW"), (0x2, "SINGLE_ENDED"), (0x4, "OPEN_DRAIN"),
              (0x10, "PULL_UP"), (0x20, "PULL_DOWN")]
DT_REF_RE = re.compile(r"\bDT_(NODELABEL|ALIAS|CHOSEN)\(\s*(\w+)\s*\)")
DRV_COMPAT_RE = re.compile(r"^\s*#\s*define\s+DT_DRV_COMPAT\s+(\w+)", re.M)
LINE_MARKER_RE = re.compile(r'^# (\d+) "(.+)"((?: \d+)*)\s*$')
DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)(?!\()\s*(.*?)\s*(?:/[/*].*)?$")
HELPER_HEADER = "zephyr\\include\\zephyr\\sys\\"   # internal macro helpers, hidden by default

SOURCE_CSS = """
.src { font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre; line-height: 1.45; }
.src .ln { display: inline-block; width: 4.5em; padding-right: 1em; text-align: right; color: #6b7280;
           user-select: none; }
.src .mark { background: #1d3b26; }
.src .hit  { background: #5c4a00; }
"""


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

        src = self.zephyr_base / "scripts" / "dts" / "python-devicetree" / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        with open(zdir / "edt.pickle", "rb") as f:
            self.edt = pickle.load(f)

        self.origins = parse_zephyr_dts(zdir / "zephyr.dts")
        pre = zdir / "zephyr.dts.pre"
        self.includes = parse_includes(pre) if pre.exists() else None
        self.file_contrib = self._file_contrib()
        self.generated = (zdir / "include" / "generated" / "zephyr" / "devicetree_generated.h") \
            .read_text(encoding="utf-8", errors="replace")
        self.device_ords = self._device_ords(cache, zdir)
        self.compiled = self._compiled_files(build)

        module_dirs = []
        mods = build / "zephyr_modules.txt"
        if mods.exists():
            for ln in mods.read_text(encoding="utf-8").splitlines():
                parts = re.findall(r'"([^"]*)"', ln)
                if len(parts) >= 2 and parts[0] not in ("cmsis", "cmsis_6", "picolibc", "atmel"):
                    module_dirs.append(Path(parts[1]).resolve())
        scan = [self.zephyr_base / "drivers", self.zephyr_base / "soc", self.app_dir] + module_dirs
        self.drivers = DriverIndex.get([d for d in scan if d.exists()])

        self.by_path = {n.path: n for n in self.edt.nodes}
        # alias2node returns dtlib nodes -> map to edtlib nodes via their path
        self.aliases = {a.replace("-", "_"): self.by_path[n.path]
                        for a, n in self.edt._dt.alias2node.items() if n.path in self.by_path}
        self.chosen = {compat_ident(k): self.by_path.get(n.path, n) for k, n in self.edt.chosen_nodes.items()}
        self.code_refs = self._code_refs()

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
        drv = self.drivers_for(node)
        if not drv:
            return "neutral", ("No driver with a matching DT_DRV_COMPAT found - normal for e.g. CPU, "
                               "memory, or nodes managed by another driver.")
        if not any(d in self.compiled for d in drv):
            return "nobuild", ("A driver exists but is disabled in Kconfig and was not compiled. Normal if "
                               "the app does not need this hardware. Otherwise enable the driver's Kconfig "
                               "symbol in prj.conf (find it with menuconfig).")
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
    return sorted({p.parent.parent for p in root.glob("**/build*/zephyr/edt.pickle")
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
class App:
    def __init__(self):
        self.builds = find_builds(ROOT)
        self.model: BuildModel | None = None

    def load(self, build: Path):
        self.model = BuildModel(build)

    def tree_nodes(self):
        m = self.model

        def mk(n):
            st = m.state(n)[0]
            label = (n.labels[0] + ": " if n.labels else "") + (n.name if n.path != "/" else "/")
            return {"id": n.path, "label": label,
                    "color": ORIGIN_STYLE[m.node_origin_kind(n)][0],
                    "dot": STATE_STYLE[st][0], "ovl": m.touched_by_overlay(n),
                    "children": [mk(c) for c in n.children.values()]}
        return [mk(m.by_path["/"])]

    def include_nodes(self, show_helpers: bool):
        """Include tree for ui.tree; ids are unique even if a file is included twice."""
        m, counter, files = self.model, [0], {}

        def mk(inc, chain):
            rel = m.rel(inc["path"])
            if not show_helpers and fkey(rel).startswith(HELPER_HEADER.lower()):
                return None
            counter[0] += 1
            nid = f"f{counter[0]}"
            n_contrib = len(m.file_contrib.get(fkey(rel), []))
            files[nid] = dict(path=inc["path"], rel=rel, chain=chain + [rel])
            kids = [k for k in (mk(c, chain + [rel]) for c in inc["children"]) if k]
            return {"id": nid, "label": inc["path"].name, "rel": rel,
                    "color": ORIGIN_STYLE[origin_kind(rel)][0], "count": n_contrib,
                    "children": kids}
        root = mk(m.includes, []) if m.includes else None
        return ([root] if root else []), files


state = App()

TABLE_KIND_SLOT = "<q-td :props=\"props\"><span :style=\"{color: " + \
    "{" + ", ".join(f"'{cap}': '{col}'" for col, cap in ORIGIN_STYLE.values()) + \
    "}[props.value]}\">{{ props.value }}</span></q-td>"
TABLE_LINK_SLOT = """<q-td :props="props"><a href="#" style="color:#90caf9; text-decoration:none"
    @click.prevent="$parent.$emit('open', props.row)">{{ props.value }}</a></q-td>"""


@ui.page("/")
def page(build: str | None = None, node: str | None = None, src: str | None = None,
         line: int | None = None, tab: str | None = None, file: str | None = None,
         macros: int = 0):
    """Optional via URL: /?build=apps/lab1/build_same54_xpro&node=/my_led@41008000
    plus &src=<file relative to the west workspace>&line=<n> to open a source file,
    &tab=include to start on the include tree, &file=<relative path> to select a file
    in the include tree, &macros=1 to expand the generated-macro section."""
    ui.page_title("Devicetree Explorer")
    ui.dark_mode(True)
    ui.add_css(SOURCE_CSS)
    ui.query("body").style("background-color: #121212")
    if not state.builds:
        ui.label(f"No builds found under {ROOT} (searched: **/build*/zephyr/edt.pickle).")
        return
    if build:
        want = (ROOT / build).resolve()
        if state.model is None or state.model.build.resolve() != want:
            state.load(want)
    if state.model is None:
        state.load(state.builds[0])

    def build_label(p):
        return str(p.relative_to(ROOT)) if ROOT in p.parents else str(p)

    with ui.header().classes("items-center gap-4").style("background-color: #1e293b"):
        ui.label("Devicetree Explorer").classes("text-lg font-bold")
        sel = ui.select({str(p): build_label(p) for p in state.builds}, value=str(state.model.build),
                        label="Build").classes("w-96").props("dark dense options-dense")
        ui.button("Reload", icon="refresh", on_click=lambda: reload(sel.value)).props("flat color=white")
        ui.space()
        for kind, (col, txt) in ORIGIN_STYLE.items():
            if kind != "other":
                ui.label(txt).style(f"color:{col}; border:1px solid {col}; padding:1px 6px; border-radius:4px")

    def reload(path):
        state.builds = find_builds(ROOT)
        state.load(Path(path))
        ui.navigate.to("/")

    sel.on_value_change(lambda e: reload(e.value))
    m = state.model

    # ---- source viewer -----------------------------------------------------------
    with ui.dialog() as src_dlg, ui.card().classes("w-full").style("max-width: 95vw; width: 95vw"):
        src_head = ui.row().classes("w-full items-center gap-2")
        src_box = ui.element("div").classes("w-full").style(
            "max-height: 78vh; overflow: auto; background:#0d1117; padding:8px; border-radius:4px")

    def open_source(path, line: int | None = None, marks=()):
        p = m.resolve(path)
        if not p.exists():
            ui.notify(f"File not found: {p}", type="negative")
            return
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
        marks = set(marks)
        rows = []
        for i, ln in enumerate(text, 1):
            cls = "hit" if i == line else ("mark" if i in marks else "")
            rows.append(f'<div id="L{i}" class="{cls}"><span class="ln">{i}</span>{html.escape(ln) or " "}</div>')
        src_head.clear()
        with src_head:
            ui.label(m.rel(p) + (f" : {line}" if line else "")).classes("font-mono text-sm")
            col = ORIGIN_STYLE[origin_kind(m.rel(p))][0]
            ui.label(ORIGIN_STYLE[origin_kind(m.rel(p))][1]).style(
                f"color:{col}; border:1px solid {col}; padding:0 6px; border-radius:4px").classes("text-xs")
            ui.space()
            code = shutil.which("code")
            if code:
                ui.button("Open in VS Code", icon="open_in_new",
                          on_click=lambda: subprocess.Popen([code, "-g", f"{p}:{line or 1}"])) \
                    .props("flat dense no-caps")
            ui.button(icon="close", on_click=src_dlg.close).props("flat dense round")
        src_box.clear()
        with src_box:
            ui.html("".join(rows), sanitize=False).classes("src")
        src_dlg.open()
        if line:
            ui.timer(0.25, lambda: ui.run_javascript(
                f'document.getElementById("L{line}")?.scrollIntoView({{block: "center"}})'), once=True)

    def src_link(text, path, line=None, marks=()):
        return ui.button(text, on_click=lambda: open_source(path, line, marks)) \
            .props("flat dense no-caps size=sm color=light-blue-3").classes("font-mono")

    # ---- page layout -------------------------------------------------------------
    for c, files in m.orphan_drivers():
        with ui.card().classes("w-full").style("background-color: #4a1515"):
            ui.label(f"⚠ The driver for '{c}' was compiled, but no node with this compatible has "
                     f'status = "okay" -> NO device is created. Overlay saved? compatible spelled '
                     f"correctly?").classes("text-red-2")
            for f in files:
                src_link(m.rel(f), f)

    with ui.splitter(value=34).classes("w-full").style("height: calc(100vh - 90px)") as sp:
        with sp.before:
            with ui.tabs().classes("w-full").props("dense align=left") as tabs:
                tab_nodes = ui.tab("Nodes")
                tab_inc = ui.tab("Include tree")
            with ui.tab_panels(tabs, value=tab_nodes).classes("w-full").style("background: transparent"):
                with ui.tab_panel(tab_nodes).classes("p-1"):
                    flt = ui.input("Filter (name/label)").props("dense clearable").classes("w-full")
                    tree = ui.tree(state.tree_nodes(), node_key="id", label_key="label",
                                   on_select=lambda e: select(e.value)).classes("w-full")
                    tree.add_slot("default-header", r"""
                        <span :style="{color: props.node.color, fontWeight: props.node.ovl ? 'bold' : 'normal'}">
                          <q-icon name="circle" size="10px" :color="props.node.dot" class="q-mr-xs"/>
                          {{ props.node.label }}
                          <q-badge v-if="props.node.ovl" color="green-8" class="q-ml-xs">overlay</q-badge>
                        </span>""")
                    flt.bind_value_to(tree, "filter")
                    tree.expand(["/", "/soc"])
                    ui.separator()
                    ui.label("Traffic light").classes("text-xs text-grey-5")
                    for st, (col, txt) in STATE_STYLE.items():
                        with ui.row().classes("items-center gap-1"):
                            ui.icon("circle", size="10px", color=col)
                            ui.label(txt).classes("text-xs")
                with ui.tab_panel(tab_inc).classes("p-1"):
                    ui.label("Files in include order. The overlay is applied last, so it can change "
                             "everything above it. Number = entries in the final devicetree that come "
                             "from this file.").classes("text-xs text-grey-5")
                    helpers = ui.switch("Show internal helper headers (sys/util_*.h)").props("dense")
                    inc_box = ui.column().classes("w-full gap-0")
        with sp.after:
            detail = ui.column().classes("w-full p-3 gap-3")

    inc_files: dict = {}

    def render_includes():
        nodes, files = state.include_nodes(helpers.value)
        inc_files.clear()
        inc_files.update(files)
        inc_box.clear()
        with inc_box:
            if not nodes:
                ui.label("zephyr.dts.pre not found - include tree unavailable.").classes("text-sm")
                return
            t = ui.tree(nodes, node_key="id", label_key="label",
                        on_select=lambda e: show_file(e.value) if e.value else None).classes("w-full")
            t.add_slot("default-header", r"""
                <span :style="{color: props.node.color}" :title="props.node.rel">
                  {{ props.node.label }}
                  <q-badge v-if="props.node.count" color="blue-grey-8" class="q-ml-xs">{{ props.node.count }}</q-badge>
                </span>""")
            t.expand()
    helpers.on_value_change(lambda _: render_includes())

    def goto(path):
        tabs.set_value(tab_nodes)
        parts, acc = path.strip("/").split("/"), ""
        anc = ["/"]
        for p in parts[:-1]:
            acc += "/" + p
            anc.append(acc)
        tree.expand(anc)
        tree.select(path)  # triggers on_select -> select()

    def select(path):
        detail.clear()
        with detail:
            if path is None:
                show_overview()
            else:
                show_node(m.by_path[path])

    def show_overview():
        ui.label("Overview").classes("text-xl font-bold")
        ui.label(f"Build: {m.rel(m.build)}   ·   {len(m.edt.nodes)} nodes   ·   "
                 f"{len(m.device_ords)} devices in the image").classes("text-grey-5")
        ui.label("Select a node on the left. Text colour = file in which the node is defined, "
                 "dot = does it become a device? Bold / 'overlay' = changed by the app's overlay. "
                 "Blue file:line links open the source.").classes("text-sm")
        ui.label("Devicetree references in the app's C code").classes("text-lg font-bold mt-2")
        if not m.code_refs:
            ui.label("No DT_NODELABEL / DT_ALIAS / DT_CHOSEN calls found in src/.").classes("text-sm")
        for r in m.code_refs:
            with ui.card().classes("w-full"):
                src_link(f"{r['file']}:{r['line']}", r["file"], r["line"])
                ui.label(r["code"]).classes("font-mono text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.label(r["macro"]).classes("font-mono font-bold")
                    ui.icon("arrow_forward")
                    if r["node"] is None:
                        ui.label("not in the devicetree! -> overlay saved? label/alias correct?") \
                            .classes("text-red-4 font-bold")
                    else:
                        n = r["node"]
                        ui.button(n.path, on_click=lambda p=n.path: goto(p)).props("flat dense no-caps")
                        st = m.state(n)[0]
                        ui.badge(STATE_STYLE[st][1], color=STATE_STYLE[st][0])

    def show_file(fid):
        info = inc_files.get(fid)
        if not info:
            return
        rel, path = info["rel"], info["path"]
        detail.clear()
        with detail:
            ui.label(path.name).classes("text-xl font-bold font-mono")
            with ui.row().classes("items-center gap-2"):
                col, cap = ORIGIN_STYLE[origin_kind(rel)]
                ui.label(cap).style(f"color:{col}; border:1px solid {col}; padding:0 6px; border-radius:4px") \
                    .classes("text-xs")
                src_link(rel, path)
            ui.label("Include chain").classes("font-bold text-sm")
            ui.label("  →  ".join(Path(c).name for c in info["chain"])).classes("font-mono text-sm")

            contrib = m.file_contrib.get(fkey(rel), [])
            if contrib:
                ui.label(f"Contributes {len(contrib)} entries to the final devicetree").classes("text-lg font-bold")
                by_node: dict = {}
                for npath, prop, line in contrib:
                    by_node.setdefault(npath, []).append((prop, line))
                for npath, items in sorted(by_node.items()):
                    with ui.card().classes("w-full").style("padding: 6px 10px"):
                        with ui.row().classes("items-center gap-1"):
                            ui.button(npath, on_click=lambda p=npath: goto(p)).props("flat dense no-caps")
                        with ui.row().classes("gap-1 flex-wrap"):
                            for prop, line in sorted(items, key=lambda x: x[1]):
                                src_link(f"{prop or '(node)'} :{line}", path, line, m.node_lines(npath, rel))
            elif path.suffix == ".h":
                defs = header_defines(path)
                ui.label(f"Provides {len(defs)} constants/macros used in the .dts/.dtsi files") \
                    .classes("text-lg font-bold")
                rows = [dict(name=n, value=v, line=ln, file=str(path)) for n, v, ln in defs]
                cols = [dict(name="name", label="Name", field="name", align="left"),
                        dict(name="value", label="Value", field="value", align="left"),
                        dict(name="line", label="Line", field="line", align="left")]
                t = ui.table(columns=cols, rows=rows, row_key="name", pagination=25).classes("w-full") \
                    .props("dense flat wrap-cells")
                t.add_slot("body-cell-line", TABLE_LINK_SLOT)
                t.on("open", lambda e: open_source(e.args["file"], e.args["line"]))
            else:
                ui.label("Nothing from this file survives in the final devicetree "
                         "(everything was overridden or it only includes other files).").classes("text-sm")

    def show_node(n):
        o = m.origins.get(n.path, NodeOrigin())
        st, why = m.state(n)
        ui.label((" ".join("&" + lb for lb in n.labels) + "  " if n.labels else "") + n.name) \
            .classes("text-xl font-bold font-mono")
        ui.label(n.path).classes("font-mono text-grey-5")
        kind = origin_kind(o.file) if o.file else "other"
        with ui.row().classes("items-center gap-2"):
            col, cap = ORIGIN_STYLE[kind]
            ui.label(cap).style(f"color:{col}; border:1px solid {col}; padding:0 6px; border-radius:4px") \
                .classes("text-xs")
            ui.label("defined in").classes("text-sm")
            if o.file:
                src_link(f"{o.file}:{o.line}", o.file, o.line, m.node_lines(n.path, o.file))

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("circle", color=STATE_STYLE[st][0])
                ui.label(STATE_STYLE[st][1]).classes("font-bold")
            ui.label(why).classes("text-sm")
            for d in m.drivers_for(n):
                built = "compiled" if d in m.compiled else "not compiled"
                txt = d.read_text(encoding="utf-8", errors="ignore").splitlines()
                dline = next((i for i, ln in enumerate(txt, 1) if DRV_COMPAT_RE.match(ln)), None)
                with ui.row().classes("items-center gap-1"):
                    ui.label("Driver:").classes("text-xs")
                    src_link(m.rel(d), d, dline)
                    ui.label(f"({built})").classes("text-xs")

        if n.compats or n.binding_path:
            with ui.card().classes("w-full"):
                ui.label("compatible / binding").classes("font-bold")
                if n.compats:
                    ui.label(", ".join(f'"{c}"' for c in n.compats)).classes("font-mono text-sm")
                if n.binding_path:
                    src_link(m.rel(n.binding_path), n.binding_path)
                if n.description:
                    ui.label(n.description.strip()).classes("text-sm whitespace-pre-wrap")

        if n.regs:
            ui.label("Registers: " + ", ".join(
                f"0x{r.addr:08x}" + (f" (size 0x{r.size:x})" if r.size else "") for r in n.regs
                if r.addr is not None)).classes("font-mono text-sm")

        ui.label("Properties").classes("text-lg font-bold")
        rows = []
        for name, p in o.props.items():
            spec = n.props[name].spec if name in n.props else None
            rows.append(dict(name=name, text=p.text, val=fmt_value(m, n, name),
                             origin=f"{p.file}:{p.line}", file=p.file, line=p.line,
                             kind=ORIGIN_STYLE[origin_kind(p.file)][1],
                             desc=(spec.description or "").strip().split("\n")[0] if spec else ""))
        cols = [dict(name="name", label="Property", field="name", align="left"),
                dict(name="text", label="As written", field="text", align="left"),
                dict(name="val", label="Interpreted", field="val", align="left"),
                dict(name="kind", label="Origin", field="kind", align="left"),
                dict(name="origin", label="File:line (click = source)", field="origin", align="left"),
                dict(name="desc", label="Meaning (binding)", field="desc", align="left")]
        tbl = ui.table(columns=cols, rows=rows, row_key="name").classes("w-full") \
            .props("dense flat wrap-cells")
        tbl.add_slot("body-cell-kind", TABLE_KIND_SLOT)
        tbl.add_slot("body-cell-origin", TABLE_LINK_SLOT)
        tbl.on("open", lambda e: open_source(e.args["file"], e.args["line"],
                                             m.node_lines(n.path, e.args["file"])))

        def links(title, nodes):
            if nodes:
                with ui.row().classes("items-center gap-1"):
                    ui.label(title).classes("font-bold text-sm")
                    for d in nodes:
                        ui.button(d.labels[0] if d.labels else d.path,
                                  on_click=lambda p=d.path: goto(p)).props("flat dense no-caps size=sm")
        links("Depends on:", [d for d in n.depends_on if d.path != "/"])
        links("Used by:", n.required_by)

        uses = [r for r in m.code_refs if r["node"] is n]
        if uses:
            ui.label("In the app's C code").classes("text-lg font-bold")
            for r in uses:
                with ui.row().classes("items-center gap-2"):
                    src_link(f"{r['file']}:{r['line']}", r["file"], r["line"])
                    ui.label(r["code"]).classes("font-mono text-sm")

        with ui.expansion("Generated C macros (devicetree_generated.h)", value=bool(macros)).classes("w-full"):
            ident = node_ident(n.path)
            ui.label(f"Node identifier: {ident}   ·   Ordinal: {n.dep_ordinal}") \
                .classes("font-mono text-sm")
            lines, total = m.macros(n)
            ui.code("\n".join(lines) + (f"\n... ({total - len(lines)} more)" if total > len(lines) else ""),
                    language="c").classes("w-full text-xs")

    render_includes()
    if node and node in m.by_path:
        goto(node)
        select(node)
    else:
        select(None)
    if tab == "include" or file:
        tabs.set_value(tab_inc)
    if file:
        fid = next((k for k, v in inc_files.items() if fkey(v["rel"]) == fkey(file)), None)
        if fid:
            show_file(fid)
    if src:
        open_source(src, line, m.node_lines(node, src) if node else ())


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Devicetree Explorer", port=8765, reload=False, show=SHOW_BROWSER, dark=True)
