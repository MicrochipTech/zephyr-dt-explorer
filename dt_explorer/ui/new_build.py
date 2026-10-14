"""'New build...' and 'Full build' dialogs (west runs with a streamed log).

Non-UI helpers (workspace and app detection, naming) come first so they can be tested without
NiceGUI state. The dialogs run west in a background thread (`core.west_tools.WestRun`); its lines go
through a queue and a `ui.timer` copies them into a `ui.log` in the UI context.
"""

from __future__ import annotations

import os
import queue
import re
from pathlib import Path
from typing import Callable

from nicegui import ui

from ..core import plugins, west_tools

APP_SCAN_DEPTH = 4
_SKIP_DIRS = {".git", ".venv", ".west", ".vscode", "modules", "zephyr", "bootloader", "node_modules",
              "__pycache__", "tests", "boards"}
_BAD_NAME = re.compile(r'[\\/:*?"<>|\s]')
_STATUS_CLASS = {"positive": "text-positive", "negative": "text-negative", "warning": "text-warning"}


# ---- non-UI helpers ----------------------------------------------------------------------
def find_workspace(root: Path | None) -> Path | None:
    """West workspace containing `root` (the nearest folder with `.west`), or None."""
    if root is None:
        return None
    p = Path(root).resolve()
    for cand in (p, *p.parents):
        if (cand / ".west").is_dir():
            return cand
    return None


def is_app_dir(p: Path) -> bool:
    return (p / "CMakeLists.txt").is_file() and (p / "prj.conf").is_file()


def find_apps(ws: Path, max_depth: int = APP_SCAN_DEPTH) -> list:
    """App folders (CMakeLists.txt + prj.conf) below `ws`, depth-limited; skips build folders and
    the usual big trees (modules, zephyr, .venv, ...). Sorted by path."""
    ws = Path(ws)
    found = []
    for dirpath, dirnames, _files in os.walk(ws):
        d = Path(dirpath)
        depth = len(d.relative_to(ws).parts)
        if is_app_dir(d):
            found.append(d)
            dirnames[:] = []            # an app folder is a leaf for the scan
            continue
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [n for n in dirnames
                       if n not in _SKIP_DIRS and not n.startswith("build")
                       and not (d / n / "zephyr" / "edt.pickle").exists()]
    return sorted(found, key=lambda p: str(p).lower())


def default_build_name(board: str) -> str:
    return f"build_{board}"


def check_build_name(name: str) -> str | None:
    """None if `name` is a usable folder name for a build inside the app folder, else the reason."""
    if not name.strip():
        return "Build folder name is empty."
    if _BAD_NAME.search(name) or name in (".", "..") or name.startswith("."):
        return "Build folder name must be a plain folder name (no spaces or path characters)."
    return None


# ---- UI helpers --------------------------------------------------------------------------
def _stream(start: Callable[[Callable[[str], None]], west_tools.WestRun], log: ui.log,
            on_done: Callable[[west_tools.WestRun], None]):
    """Start a run with `start(on_line)`; copy its lines into `log` from a UI timer; call `on_done`
    in the UI context once the process ended and the queue is drained. Returns the WestRun."""
    q: queue.SimpleQueue = queue.SimpleQueue()
    run = start(q.put)

    def drain():
        n = 0
        while n < 200:
            try:
                log.push(q.get_nowait())
            except queue.Empty:
                break
            n += 1
        if not run.running and q.empty():
            timer.cancel()
            on_done(run)

    timer = ui.timer(0.2, drain)
    return run


def _finish_text(run: west_tools.WestRun) -> tuple[str, str]:
    if run.cancelled:
        return "Cancelled.", "warning"
    if run.returncode == 0:
        return f"Finished successfully in {run.duration:.0f} s.", "positive"
    return f"Failed (exit code {run.returncode}) after {run.duration:.0f} s.", "negative"


def open_new_build(root: Path | None, on_built: Callable[[Path], None]):
    """Dialog: choose app folder, board and build folder name; run a cmake-only build."""
    ws = find_workspace(root)
    if ws is None:
        ui.notify("No west workspace found (a folder with .west above the opened folder).", type="negative")
        return
    py = west_tools.default_python(ws)
    if not py.exists():
        ui.notify(f"Workspace Python not found: {py}", type="negative")
        return
    apps = find_apps(ws)
    vendors = plugins.board_vendors()           # default filter from the providers; () = all
    boards = west_tools.list_boards(ws, vendors=vendors or None)
    vendor_note = f" {'/'.join(v.capitalize() for v in vendors)}" if vendors else ""
    app_opts = {str(a): str(a.relative_to(ws)) for a in apps}
    all_names = [b.name for b in boards]
    by_name = {b.name: b for b in boards}
    st = {"run": None, "name_edited": False, "target": None}

    with ui.dialog().props("persistent") as dlg, ui.card().style("width: 900px; max-width: 95vw"):
        ui.label("New build (cmake only)").classes("text-lg font-bold")
        ui.label(f"Workspace: {ws}").classes("text-xs text-grey-5")
        if not apps:
            ui.label("No app folder (CMakeLists.txt + prj.conf) found in the workspace.") \
                .classes("text-negative")
        app_sel = ui.select(app_opts, value=next(iter(app_opts), None), label="App folder",
                            with_input=True).classes("w-full").props("dark dense options-dense")
        with ui.row().classes("w-full items-end gap-3 no-wrap"):
            flt = ui.input("Filter boards", placeholder="e.g. same54, pic32cx").classes("w-48") \
                .props("dark dense clearable")
            board_sel = ui.select(all_names, value=None, label=f"Board ({len(all_names)}{vendor_note})",
                                  with_input=True).classes("grow").props("dark dense options-dense")
        board_info = ui.label("").classes("text-xs text-grey-5")
        name_in = ui.input("Build folder name", value="").classes("w-full").props("dark dense")
        target_lbl = ui.label("").classes("text-xs text-grey-5 font-mono")
        log = ui.log(max_lines=3000).classes("w-full h-72 font-mono text-xs").style("background:#0d1117")
        status = ui.label("").classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2"):
            cancel_btn = ui.button("Cancel build", icon="stop", color="warning").props("flat no-caps")
            cancel_btn.set_visibility(False)
            start_btn = ui.button("Start", icon="play_arrow").props("no-caps")
            close_btn = ui.button("Close", icon="close", on_click=dlg.close).props("flat no-caps")

    def refresh_target():
        app, board = app_sel.value, board_sel.value
        if board and not st["name_edited"]:
            name_in.set_value(default_build_name(board))
        if app and name_in.value:
            st["target"] = Path(app) / name_in.value
            target_lbl.set_text(f"Build folder: {st['target'].relative_to(ws)}")
        else:
            st["target"] = None
            target_lbl.set_text("")

    def on_filter(e):
        text = (flt.value or "").lower().strip()
        names = [n for n in all_names if text in n.lower() or text in by_name[n].full_name.lower()]
        board_sel.set_options(names, value=board_sel.value if board_sel.value in names else
                              (names[0] if len(names) == 1 else None))

    def on_board(e):
        b = by_name.get(board_sel.value)
        board_info.set_text(f"{b.full_name} - SoC {', '.join(b.socs)} - {b.vendor}" if b else "")
        st["name_edited"] = False
        refresh_target()

    def on_name(e):
        if name_in.value != default_build_name(board_sel.value or ""):
            st["name_edited"] = True
        refresh_target()

    flt.on_value_change(on_filter)
    board_sel.on_value_change(on_board)
    app_sel.on_value_change(lambda e: refresh_target())
    name_in.on_value_change(on_name)

    def done(run):
        text, kind = _finish_text(run)
        status.set_text(text)
        status.classes(replace="text-sm " + _STATUS_CLASS[kind])
        cancel_btn.set_visibility(False)
        close_btn.enable()
        if run.returncode == 0 and (st["target"] / "zephyr" / "edt.pickle").exists():
            status.set_text(text + " Loading the new build ...")
            target = st["target"]

            def finish():           # short pause so the finished log stays readable
                dlg.close()
                on_built(target)
            ui.timer(2.0, finish, once=True)
        else:
            if run.returncode == 0:
                status.set_text(text + " But zephyr/edt.pickle is missing in the build folder.")
            start_btn.enable()

    def start():
        refresh_target()
        if not app_sel.value:
            problem = "Choose an app folder."
        elif not board_sel.value:
            problem = "Choose a board."
        else:
            problem = check_build_name(name_in.value or "")
        if problem:
            ui.notify(problem, type="warning")
            return
        target = st["target"]
        log.clear()
        status.set_text("Running ...")
        status.classes(replace="text-sm")
        start_btn.disable()
        close_btn.disable()
        cancel_btn.set_visibility(True)
        st["run"] = _stream(
            lambda cb: west_tools.cmake_only_build(ws, Path(app_sel.value), board_sel.value, target,
                                                   on_line=cb, python=py),
            log, done)

    start_btn.on_click(start)
    cancel_btn.on_click(lambda: st["run"] and st["run"].cancel())
    board_sel.set_value("same54_xpro" if "same54_xpro" in by_name else None)
    refresh_target()
    dlg.open()
    return dlg


def open_full_build(root: Path | None, build_dir: Path | None, on_built: Callable[[Path], None]):
    """Dialog: run `west build -d <build_dir>` for the current build, then reload it."""
    ws = find_workspace(root) or find_workspace(build_dir)
    if ws is None or build_dir is None:
        ui.notify("No west workspace found for this build.", type="negative")
        return
    py = west_tools.default_python(ws)
    if not py.exists():
        ui.notify(f"Workspace Python not found: {py}", type="negative")
        return
    st = {"run": None}
    with ui.dialog().props("persistent") as dlg, ui.card().style("width: 900px; max-width: 95vw"):
        ui.label("Full build").classes("text-lg font-bold")
        ui.label(f"west build -d {build_dir}").classes("text-xs text-grey-5 font-mono")
        log = ui.log(max_lines=3000).classes("w-full h-72 font-mono text-xs").style("background:#0d1117")
        status = ui.label("Running ...").classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2"):
            cancel_btn = ui.button("Cancel build", icon="stop", color="warning").props("flat no-caps")
            close_btn = ui.button("Close", icon="close", on_click=dlg.close).props("flat no-caps")
            close_btn.disable()

    def done(run):
        text, kind = _finish_text(run)
        status.set_text(text)
        status.classes(replace="text-sm " + _STATUS_CLASS[kind])
        cancel_btn.set_visibility(False)
        close_btn.enable()
        if run.returncode == 0:
            status.set_text(text + " Reloading the build ...")

            def finish():
                dlg.close()
                on_built(build_dir)
            ui.timer(2.0, finish, once=True)

    cancel_btn.on_click(lambda: st["run"] and st["run"].cancel())
    st["run"] = _stream(lambda cb: west_tools.full_build(ws, build_dir, on_line=cb, python=py), log, done)
    dlg.open()
    return dlg
