"""'Port to board...' dialog: source build -> target build, overlay, .conf and port report.

The pipeline lives in ``core/port_report.py``; this module is the glue. It runs the pipeline in a worker
thread (progress lines go through a queue into a log), shows the target overlay, the .conf lines and the
report sections, and can run 'Verify with west' on the TARGET build. Nothing is written into the app
(card P5.6 does that).
"""
from __future__ import annotations

import queue
from pathlib import Path

from nicegui import run, ui

from ..core import verify as verify_mod
from ..core.buildinfo import BuildInfoError, read_build_info
from ..core.port_report import PortResult, port_build
from . import new_build
from .draft_tab import SEVERITY_STYLE, check_summary


# ---- non-UI helpers --------------------------------------------------------------------------
def board_of(build: Path | str) -> str | None:
    """Board target of a build folder (``CONFIG_BOARD_TARGET``), or None when it cannot be read."""
    try:
        info = read_build_info(build)
    except (BuildInfoError, OSError):
        return None
    return info.board_target or info.board


def eligible_targets(builds, source: Path | str) -> dict[str, str]:
    """Builds from the list whose board differs from the source build's: ``{path: "<name>  [board]"}``."""
    src = Path(source).resolve()
    src_board = board_of(src)
    out: dict[str, str] = {}
    for b in builds:
        if Path(b).resolve() == src:
            continue
        board = board_of(b)
        if board is None or board == src_board:
            continue
        out[str(b)] = f"{b}  [{board}]"
    return out


def report_text(result: PortResult) -> str:
    return "\n".join(result.report.lines()) + "\n"


# ---- the dialog ------------------------------------------------------------------------------
def open_port(model, builds, root: Path | None) -> ui.dialog | None:
    """Open the dialog for the current build `model`; `builds` is the explorer's build list."""
    if model is None:
        ui.notify("Open a build first.", type="warning")
        return None
    source = model.build
    src_board = board_of(source) or "?"
    options = eligible_targets(builds, source)
    st: dict = {"result": None, "target": None}

    with ui.dialog().props("persistent id=port-dialog") as dlg, \
            ui.card().style("width: 1100px; max-width: 96vw; max-height: 94vh; overflow:auto"):
        ui.label("Port to board").classes("text-lg font-bold")
        ui.label(f"Source build: {source}  [{src_board}]").classes("text-xs text-grey-5 font-mono") \
            .props("id=port-source")
        ui.label("The target build is a build of the same app for the new board (a cmake-only build is enough). "
                 "The result is the target overlay, the .conf lines and a report; nothing is written into the app.") \
            .classes("text-xs text-grey-5")
        with ui.row().classes("w-full items-end gap-3 no-wrap"):
            sel = ui.select(options, value=next(iter(options), None), label="Target build (other board)",
                            with_input=True, clearable=True).classes("grow") \
                .props("dark dense options-dense id=port-target")
            ui.button("Create target build…", icon="add",
                      on_click=lambda: new_build.open_new_build(root, on_created)) \
                .props("flat no-caps id=port-create")
        other = ui.input("or a build folder path", placeholder="e.g. C:\\...\\build_sam_e54_xpro") \
            .classes("w-full").props("dark dense clearable id=port-other")
        with ui.row().classes("items-center gap-3"):
            run_btn = ui.button("Run port", icon="play_arrow").props("no-caps id=port-run")
            spin = ui.spinner(size="sm").props("id=port-spinner")
            spin.set_visibility(False)
            status = ui.label("").classes("text-sm").props("id=port-status")
        log = ui.log(max_lines=200).classes("w-full h-20 font-mono text-xs").style("background:#0d1117") \
            .props("id=port-log")
        out = ui.column().classes("w-full gap-2").props("id=port-result")
        with ui.row().classes("w-full justify-end"):
            ui.button("Close", icon="close", on_click=dlg.close).props("flat no-caps id=port-close")

    def on_created(path: Path) -> None:
        p = str(path)
        options[p] = f"{p}  [{board_of(path) or '?'}]"
        sel.set_options(dict(options), value=p)
        other.set_value("")
        ui.notify(f"Target build selected: {path}")

    def target_path() -> Path | None:
        text = (other.value or "").strip().strip('"')
        chosen = text or sel.value
        return Path(chosen) if chosen else None

    async def do_run() -> None:
        tgt = target_path()
        if tgt is None:
            ui.notify("Choose a target build.", type="warning")
            return
        if not (tgt / "zephyr" / "edt.pickle").is_file():
            ui.notify(f"{tgt} is not a configured build (zephyr/edt.pickle missing).", type="negative")
            return
        run_btn.set_enabled(False)
        spin.set_visibility(True)
        status.text = "porting …"
        status.classes(replace="text-sm text-grey-5")
        log.clear()
        out.clear()
        q: queue.SimpleQueue = queue.SimpleQueue()

        def drain() -> None:
            for _ in range(100):
                try:
                    log.push(q.get_nowait())
                except queue.Empty:
                    break

        timer = ui.timer(0.2, drain)
        try:
            result: PortResult = await run.io_bound(lambda: port_build(source, tgt, on_step=q.put))
        except Exception as exc:                         # noqa: BLE001 - show, do not crash the page
            timer.cancel()
            drain()
            spin.set_visibility(False)
            run_btn.set_enabled(True)
            status.text = f"The port failed: {type(exc).__name__}: {exc}"
            status.classes(replace="text-sm text-red-4")
            return
        timer.cancel()
        drain()
        spin.set_visibility(False)
        run_btn.set_enabled(True)
        st["result"], st["target"] = result, tgt
        errs = sum(1 for f in (result.report.findings or []) if f.severity == "error")
        status.text = (f"done: {len(result.draft.nodes)} node(s) carried over, "
                       f"{len(result.report.label_refs)} node reference(s) to fix, "
                       f"{len(result.report.pin_issues)} pin issue(s); fast check: "
                       f"{check_summary(result.report.findings or [])}")
        status.classes(replace="text-sm " + ("text-red-4" if errs else "text-green-4"))
        with out:
            render_result(result, tgt)

    run_btn.on_click(do_run)
    dlg.open()
    return dlg


def _code_block(title: str, text: str, language: str, file_name: str, elem_id: str) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.label(title).classes("font-bold")
        ui.button("Copy", icon="content_copy",
                  on_click=lambda: (ui.clipboard.write(text), ui.notify(f"{title} copied"))) \
            .props("flat dense no-caps")
        ui.button("Download", icon="download",
                  on_click=lambda: ui.download.content(text.encode("utf-8"), file_name)) \
            .props("flat dense no-caps")
    ui.code(text, language=language).classes("w-full text-xs").props(f"id={elem_id}")


def render_result(result: PortResult, target: Path) -> None:
    """Overlay, conf, report sections and the verify controls (call inside the result container)."""
    rep = result.report
    _code_block("Target overlay", result.overlay, "c", "port.overlay", "port-overlay")
    _code_block("Target .conf", result.conf, "ini", "port.conf", "port-conf")

    text = report_text(result)
    with ui.row().classes("items-center gap-2"):
        ui.label("Port report").classes("font-bold")
        ui.button("Copy", icon="content_copy",
                  on_click=lambda: (ui.clipboard.write(text), ui.notify("Report copied"))) \
            .props("flat dense no-caps")
        ui.button("Download", icon="download",
                  on_click=lambda: ui.download.content(text.encode("utf-8"), "port_report.txt")) \
            .props("flat dense no-caps")
    with ui.column().classes("w-full gap-1").props("id=port-report"):
        for i, (title, lines) in enumerate(rep.sections(), 1):
            if title.startswith("6."):
                continue
            with ui.expansion(title, value=True).classes("w-full").props(f"dense id=port-section-{i}"):
                ui.label("\n".join(lines)).classes("text-xs font-mono").style(
                    "white-space: pre-wrap; word-break: break-word")
        ui.label("Fast check on the target build").classes("font-bold")
        with ui.column().classes("w-full gap-1").props("id=port-findings"):
            findings = rep.findings or []
            if not findings:
                ui.label("No findings.").classes("text-green-4 text-sm")
            for f in findings:
                _, color = SEVERITY_STYLE.get(f.severity, SEVERITY_STYLE["info"])
                with ui.row().classes("items-start gap-2 no-wrap").props(f'data-severity="{f.severity}"'):
                    ui.badge(f.severity, color=color).classes("text-xs")
                    ui.label(f"{f.kind}: {f.message}").classes("text-xs").style("white-space: normal")
    _render_verify(target, result.overlay, result.conf)


def _render_verify(target: Path, overlay_text: str, conf_text: str) -> None:
    """'Verify with west' on the TARGET build (scratch folder, the build is not touched)."""
    ui.separator()
    with ui.row().classes("items-center gap-2"):
        ui.label("Authoritative check on the target").classes("font-bold")
        vbtn = ui.button("Verify with west", icon="verified").props("flat dense no-caps id=port-verify") \
            .props('title="west build --cmake-only of the target board into a scratch folder with the target overlay and .conf (about 30 s)"')
        vspin = ui.spinner(size="sm").props("id=port-verify-spinner")
        vspin.set_visibility(False)
        vstatus = ui.label().classes("text-sm").props("id=port-verify-status")
    vlog = ui.log(max_lines=3000).classes("w-full h-48 font-mono text-xs").style("background:#0d1117") \
        .props("id=port-verify-log")
    vlog.set_visibility(False)
    verr = ui.label().classes("w-full text-xs font-mono p-2").props("id=port-verify-errors") \
        .style("white-space: pre-wrap; word-break: break-all; background:#0d1117")
    verr.set_visibility(False)

    def finish(run_, plan_) -> None:
        res = verify_mod.result_of(run_, plan_)
        vspin.set_visibility(False)
        vbtn.set_enabled(True)
        if res.ok:
            vstatus.text = f"Zephyr accepts the port (west rc 0, {res.duration:.0f} s)"
            vstatus.classes(replace="text-sm text-green-4")
            shown = res.warnings
        else:
            vstatus.text = f"Zephyr rejects the port (west rc {res.returncode}, {res.duration:.0f} s)"
            vstatus.classes(replace="text-sm text-red-4")
            shown = res.errors
        verr.text = "\n".join(shown)
        verr.set_visibility(bool(shown))

    async def run_verify() -> None:
        vbtn.set_enabled(False)
        vspin.set_visibility(True)
        vstatus.text = "west is configuring …"
        vstatus.classes(replace="text-sm text-grey-5")
        vlog.clear()
        vlog.set_visibility(True)
        verr.set_visibility(False)
        q: queue.SimpleQueue = queue.SimpleQueue()
        try:
            run_, plan_ = await run.io_bound(verify_mod.start, target, overlay_text, conf_text, q.put)
        except verify_mod.VerifyError as exc:
            vspin.set_visibility(False)
            vbtn.set_enabled(True)
            vstatus.text = f"Cannot verify: {exc}"
            vstatus.classes(replace="text-sm text-red-4")
            return

        def drain() -> None:
            for _ in range(200):
                try:
                    vlog.push(q.get_nowait())
                except queue.Empty:
                    break
            if not run_.running and q.empty():
                timer.cancel()
                finish(run_, plan_)

        timer = ui.timer(0.2, drain)

    vbtn.on_click(run_verify)
