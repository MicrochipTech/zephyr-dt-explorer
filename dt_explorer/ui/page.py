"""Devicetree Explorer UI: NiceGUI page."""

import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

from nicegui import ui

from . import new_build, pinmap
from ..core.build import (DRV_COMPAT_RE, HELPER_HEADER, ORIGIN_STYLE, STATE_STYLE, BuildModel,
                          NodeOrigin, find_builds, fkey, fmt_value, header_defines, node_ident,
                          origin_kind)

DEFAULT_ROOT = Path(r"C:\Masters\26020_RTOS4\zephyrproject")
CONFIG_FILE = Path.home() / ".dt_explorer.json"     # remembers the last chosen folder
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
ROOT: Path | None = Path(_ARGS[0]) if _ARGS else None   # None -> ask with a folder dialog
SHOW_BROWSER = "--no-browser" not in sys.argv


def last_folder() -> Path:
    try:
        p = Path(json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["last_folder"])
        if p.exists():
            return p
    except (OSError, ValueError, KeyError):
        pass
    return DEFAULT_ROOT if DEFAULT_ROOT.exists() else Path.home()


def remember_folder(p: Path):
    try:
        CONFIG_FILE.write_text(json.dumps({"last_folder": str(p)}), encoding="utf-8")
    except OSError:
        pass


def ask_folder(initial: Path) -> Path | None:
    """Native folder dialog (tkinter). Returns None if cancelled."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = filedialog.askdirectory(
        parent=root, initialdir=str(initial), mustexist=True,
        title="Devicetree Explorer - select a Zephyr workspace, app or build folder")
    root.destroy()
    return Path(chosen) if chosen else None


SOURCE_CSS = """
.src { font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre; line-height: 1.45; }
.src .ln { display: inline-block; width: 4.5em; padding-right: 1em; text-align: right; color: #6b7280;
           user-select: none; }
.src .mark { background: #1d3b26; }
.src .hit  { background: #5c4a00; }
"""



class App:
    def __init__(self):
        self.builds: list = []
        self.model: BuildModel | None = None

    def set_root(self, root: Path):
        global ROOT
        ROOT = root
        self.builds = find_builds(root)
        self.model = None

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
         macros: int = 0, bprops: int = 0):
    """Optional via URL: /?build=apps/lab1/build_same54_xpro&node=/my_led@41008000
    plus &src=<file relative to the west workspace>&line=<n> to open a source file,
    &tab=include to start on the include tree, &tab=pins for the pin map, &file=<relative path> to select a file
    in the include tree, &macros=1 to expand the generated-macro section, &bprops=1 to expand
    the list of all properties the binding allows."""
    ui.page_title("Devicetree Explorer")
    ui.dark_mode(True)
    ui.add_css(SOURCE_CSS)
    ui.query("body").style("background-color: #121212")

    def choose_folder():
        chosen = ask_folder(ROOT or last_folder())
        if chosen:
            remember_folder(chosen)
            state.set_root(chosen)
            ui.navigate.to("/")

    if not state.builds:
        with ui.column().classes("p-6 gap-3"):
            ui.label("Devicetree Explorer").classes("text-xl font-bold")
            ui.label(f"No Zephyr build found under {ROOT}.")
            ui.label("A build folder contains zephyr/edt.pickle - run `west build` first, then select the "
                     "workspace, the app folder or the build folder itself.").classes("text-sm text-grey-5")
            ui.button("Open folder", icon="folder_open", on_click=choose_folder)
        return
    if build:
        want = (ROOT / build).resolve()
        if state.model is None or state.model.build.resolve() != want:
            state.load(want)
    if state.model is None:
        state.load(state.builds[0])

    def build_label(p):
        return str(p.relative_to(ROOT)) if ROOT in p.parents else p.name

    with ui.header().classes("items-center gap-4").style("background-color: #1e293b"):
        ui.label("Devicetree Explorer").classes("text-lg font-bold")
        ui.button(icon="folder_open", on_click=choose_folder).props("flat round color=white") \
            .tooltip(f"Open folder (current: {ROOT})")
        sel = ui.select({str(p): build_label(p) for p in state.builds}, value=str(state.model.build),
                        label="Build").classes("w-96").props("dark dense options-dense")
        ui.button("Reload", icon="refresh", on_click=lambda: reload(sel.value)).props("flat color=white")
        ui.button("New build…", icon="add", on_click=lambda: new_build.open_new_build(ROOT, reload)) \
            .props("flat no-caps color=white")
        ui.button("Full build", icon="build",
                  on_click=lambda: new_build.open_full_build(ROOT, state.model.build, reload)) \
            .props("flat no-caps color=white")
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
                tab_pins = ui.tab("Pins")
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
                with ui.tab_panel(tab_pins).classes("p-1"):
                    pins_box = ui.column().classes("w-full gap-1")
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

    def on_tab(_=None):
        # the pin table needs width: widen the left pane while the Pins tab is active
        if tabs.value == tab_pins:
            sp.set_value(78)
            if not pins_box.default_slot.children:
                pinmap.render_pin_map(pins_box, m, goto)
        else:
            sp.set_value(34)
    tabs.on_value_change(on_tab)

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

    def show_binding(n):
        with ui.card().classes("w-full"):
            ui.label("compatible / binding").classes("font-bold")
            if n.compats:
                ui.label(", ".join(f'"{c}"' for c in n.compats)).classes("font-mono text-sm")
            if not n.binding_path:
                ui.label("No binding found for this compatible.").classes("text-deep-orange-4 text-sm")
                return
            b = n._binding
            if n.description:
                ui.label(n.description.strip()).classes("text-sm whitespace-pre-wrap")

            facts = []
            if b.on_bus:
                facts.append(f"on-bus: {b.on_bus}  (this node sits on a {b.on_bus} bus; reg = bus address)")
            if b.buses:
                facts.append(f"bus: {', '.join(b.buses)}  (child nodes are devices on this bus)")
            for space, cells in (b.specifier2cells or {}).items():
                facts.append(f"{space}-cells: {', '.join(cells)}  "
                             f"(a reference <&this {' '.join(cells)}> passes these values)")
            if b.child_binding:
                facts.append("child-binding: child nodes are described by this binding")
            for f in facts:
                ui.label(f).classes("font-mono text-xs")

            ui.label("Binding file and its include: hierarchy").classes("text-sm font-bold mt-1")
            chain = m.binding_chain(n.binding_path)

            def render(c, depth):
                with ui.row().classes("items-center gap-1").style(f"margin-left: {depth * 18}px"):
                    ui.icon("subdirectory_arrow_right" if depth else "description", size="16px") \
                        .classes("text-grey-6")
                    if c.get("missing"):
                        ui.label(f"{c['path']} (not found)").classes("font-mono text-xs text-red-4")
                    else:
                        src_link(m.rel(c["path"]), c["path"])
                for k in c["children"]:
                    render(k, depth + 1)
            render(chain, 0)

            # all properties the binding allows
            rows = []
            own = Path(n.binding_path).resolve()
            o = m.origins.get(n.path, NodeOrigin())
            for name, spec in b.prop2specs.items():
                decl = m.prop_declared_in(chain, name) or own
                if name in o.props:
                    st = "set"
                elif spec.default is not None:
                    st = "default"
                else:
                    st = "not set"
                allowed = ", ".join(map(str, spec.enum)) if spec.enum else \
                    (f"const {spec.const}" if spec.const is not None else "")
                rows.append(dict(name=name, type=spec.type, req="yes" if spec.required else "",
                                 default="" if spec.default is None else str(spec.default),
                                 allowed=allowed, state=st, decl=Path(decl).name, file=str(decl),
                                 line=m.yaml_line(decl, name) or 1, generic=Path(decl).resolve() != own,
                                 desc=(spec.description or "").strip().split("\n")[0]))
            rows.sort(key=lambda r: ({"set": 0, "default": 1, "not set": 2}[r["state"]], r["req"] != "yes"))
            cols = [dict(name="name", label="Property", field="name", align="left"),
                    dict(name="type", label="Type", field="type", align="left"),
                    dict(name="req", label="Required", field="req", align="left"),
                    dict(name="state", label="In this node", field="state", align="left"),
                    dict(name="default", label="Default", field="default", align="left"),
                    dict(name="allowed", label="Allowed values", field="allowed", align="left"),
                    dict(name="decl", label="Declared in (click)", field="decl", align="left"),
                    dict(name="desc", label="Description", field="desc", align="left")]
            with ui.expansion(f"All {len(rows)} properties this binding allows", value=bool(bprops)) \
                    .classes("w-full"):
                show_generic = ui.switch("Include generic properties from base.yaml etc. that are not set") \
                    .props("dense")
                tbl = ui.table(columns=cols, rows=[], row_key="name").classes("w-full") \
                    .props("dense flat wrap-cells")
                tbl.add_slot("body-cell-decl", TABLE_LINK_SLOT)
                tbl.add_slot("body-cell-state", r"""
                    <q-td :props="props"><span :style="{color: {'set': '#81c784', 'default': '#ffd54f',
                      'not set': '#9e9e9e'}[props.value]}">{{ props.value }}</span></q-td>""")
                tbl.on("open", lambda e: open_source(e.args["file"], e.args["line"]))

                def refresh():
                    tbl.rows = [r for r in rows if show_generic.value or not r["generic"]
                                or r["state"] != "not set" or r["req"]]
                    tbl.update()
                show_generic.on_value_change(lambda _: refresh())
                refresh()

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
            show_binding(n)

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
        # values that only come from `default:` in the binding (not written in any .dts file)
        if n.binding_path:
            chain = m.binding_chain(n.binding_path)
            for name, prop in n.props.items():
                if name in o.props or prop.spec.default is None:
                    continue
                decl = m.prop_declared_in(chain, name) or Path(n.binding_path)
                ln = m.yaml_line(decl, name) or 1
                rows.append(dict(name=name, text="(not written)", val=fmt_value(m, n, name),
                                 origin=f"{m.rel(decl)}:{ln}", file=str(decl), line=ln,
                                 kind=ORIGIN_STYLE["default"][1],
                                 desc=(prop.spec.description or "").strip().split("\n")[0]))
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
    if tab == "pins":
        tabs.set_value(tab_pins)
    elif tab == "include" or file:
        tabs.set_value(tab_inc)
    if file:
        fid = next((k for k, v in inc_files.items() if fkey(v["rel"]) == fkey(file)), None)
        if fid:
            show_file(fid)
    if src:
        open_source(src, line, m.node_lines(node, src) if node else ())
