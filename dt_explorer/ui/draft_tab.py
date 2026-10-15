"""Draft tab: the pending changes of one build.

Shows the draft nodes (edit / remove), the combined overlay snippet, the suggested Kconfig lines and
the result of the fast check. The check takes seconds, so it runs in a worker thread with a spinner.
"""
from __future__ import annotations

import queue

from nicegui import run, ui

from ..core import verify as verify_mod
from ..core.render import RenderError

SEVERITY_STYLE = {"error": ("error", "red-5"), "warning": ("warning", "orange-5"),
                  "info": ("info", "blue-grey-4")}


def kconfig_lines(suggestions) -> list[str]:
    """``CONFIG_X=y`` lines of all suggestions, deduplicated, in first-seen order."""
    seen: dict[str, str] = {}
    for s in suggestions:
        for k, v in s.symbols.items():
            seen.setdefault(k, v)
    return [f"{k}={v}" for k, v in seen.items()]


def check_summary(findings) -> str:
    n = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        n[f.severity] = n.get(f.severity, 0) + 1
    return f"{n['error']} error(s), {n['warning']} warning(s), {n['info']} info"


def render_draft_tab(container, session, edit, changed) -> None:
    """Fill `container` with the draft of `session`.

    `edit(label)` opens the configurator for a draft node; `changed()` is called after a removal.
    """
    container.clear()
    with container:
        if not session.draft.nodes:
            ui.label("The draft is empty. Open a disabled peripheral node and press Configure…") \
                .classes("text-sm text-grey-5")
            return
        ui.label("Draft nodes").classes("font-bold")
        for n in session.draft.nodes:
            with ui.row().classes("items-center gap-2 w-full").props(f"id=draft-node-{n.label}"):
                ui.label(f"&{n.label}").classes("font-mono")
                ui.label(f"{n.cls} on {n.instance}").classes("text-xs text-grey-4")
                ui.label(", ".join(f"{r} {p}" for r, p in n.pins.items())).classes("text-xs font-mono")
                ui.space()
                ui.button(icon="edit", on_click=lambda _e, lb=n.label: edit(lb)).props("flat dense round") \
                    .tooltip("Edit")
                ui.button(icon="delete", on_click=lambda _e, lb=n.label: _remove(session, lb, changed)) \
                    .props("flat dense round").tooltip("Remove from the draft")

        try:
            text = session.overlay()
        except RenderError as exc:
            ui.label(f"Cannot render the draft: {exc}").classes("text-red-4")
            return
        with ui.row().classes("items-center gap-2"):
            ui.label("Overlay snippet").classes("font-bold")
            ui.button("Copy", icon="content_copy",
                      on_click=lambda: (ui.clipboard.write(text), ui.notify("Snippet copied"))) \
                .props("flat dense no-caps")
            ui.button("Download", icon="download",
                      on_click=lambda: ui.download.content(text.encode("utf-8"), "draft.overlay")) \
                .props("flat dense no-caps")
        ui.code(text, language="c").classes("w-full text-xs").props("id=draft-snippet")

        ui.label("Suggested prj.conf lines").classes("font-bold")
        lines = kconfig_lines(session.kconfig())
        enabled = {a for s in session.kconfig() for a in s.already_enabled}
        if lines:
            ui.code("\n".join(lines), language="ini").classes("w-full text-xs").props("id=draft-kconfig")
            if enabled:
                ui.label("already enabled in this build: " + ", ".join(sorted(enabled))) \
                    .classes("text-xs text-grey-5")
        else:
            ui.label("No suggestions available.").classes("text-xs text-grey-5")

        with ui.row().classes("items-center gap-2"):
            ui.label("Check").classes("font-bold")
            btn = ui.button("Check again", icon="rule").props("flat dense no-caps id=draft-recheck")
            spinner = ui.spinner(size="sm").props("id=draft-spinner")
            status = ui.label().classes("text-xs text-grey-5").props("id=draft-check-status")
        results = ui.column().classes("w-full gap-1").props("id=draft-findings")

        async def run_check() -> None:
            btn.set_enabled(False)
            spinner.set_visibility(True)
            status.text = "checking …"
            results.clear()
            try:
                findings = await run.io_bound(session.check)
            except Exception as exc:                         # noqa: BLE001 - show, do not crash the page
                findings = None
                with results:
                    ui.label(f"The check failed: {exc}").classes("text-red-4")
            spinner.set_visibility(False)
            btn.set_enabled(True)
            if findings is None:
                status.text = "check failed"
                return
            status.text = check_summary(findings)
            with results:
                if not findings:
                    ui.label("No findings.").classes("text-green-4 text-sm")
                for f in findings:
                    _, color = SEVERITY_STYLE.get(f.severity, SEVERITY_STYLE["info"])
                    with ui.row().classes("items-start gap-2 no-wrap").props(f'data-severity="{f.severity}"'):
                        ui.badge(f.severity, color=color).classes("text-xs")
                        ui.label(f"{f.kind}: {f.message}").classes("text-xs").style("white-space: normal")

        btn.on_click(run_check)
        ui.timer(0.05, run_check, once=True)

        _render_verify(session, text, "\n".join(lines) + "\n" if lines else "")


def _remove(session, label: str, changed) -> None:
    session.remove(label)
    session.save()
    changed()


def _render_verify(session, overlay_text: str, conf_text: str) -> None:
    """'Verify with west' (Zephyr's own configure step, streamed) and 'Write to app'."""
    build, app = session.model.build, session.model.app_dir
    ui.separator()
    with ui.row().classes("items-center gap-2"):
        ui.label("Authoritative check").classes("font-bold")
        vbtn = ui.button("Verify with west", icon="verified").props("flat dense no-caps id=draft-verify") \
            .props('title="west build --cmake-only into a scratch folder with the overlay and the Kconfig lines (about 15-45 s). Your build folder is not touched."')
        vspin = ui.spinner(size="sm").props("id=draft-verify-spinner")
        vspin.set_visibility(False)
        vstatus = ui.label().classes("text-sm").props("id=draft-verify-status")
    vlog = ui.log(max_lines=3000).classes("w-full h-48 font-mono text-xs").style("background:#0d1117") \
        .props("id=draft-verify-log")
    vlog.set_visibility(False)
    verr = ui.label().classes("w-full text-xs font-mono p-2").props("id=draft-verify-errors")         .style("white-space: pre-wrap; word-break: break-all; background:#0d1117")
    verr.set_visibility(False)

    def finish(run_, plan_) -> None:
        res = verify_mod.result_of(run_, plan_)
        vspin.set_visibility(False)
        vbtn.set_enabled(True)
        if res.ok:
            vstatus.text = f"Zephyr accepts the draft (west rc 0, {res.duration:.0f} s)"
            vstatus.classes(replace="text-sm text-green-4")
            shown = res.warnings
        else:
            vstatus.text = f"Zephyr rejects the draft (west rc {res.returncode}, {res.duration:.0f} s)"
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
            run_, plan_ = await run.io_bound(verify_mod.start, build, overlay_text, conf_text, q.put)
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

    with ui.row().classes("items-center gap-2"):
        ui.label("Write to app").classes("font-bold")
        wbtn = ui.button("Write to app", icon="save").props("flat dense no-caps id=draft-write")
        wstatus = ui.label().classes("text-xs text-grey-5").props("id=draft-write-status")
    ui.label(f"Writes {verify_mod.OVERLAY_NAME} and {verify_mod.CONF_NAME} into the app folder ({app}). "
             "Zephyr only reads them if the app's CMakeLists.txt or your west command refers to them, "
             f"e.g. west build -- -DEXTRA_DTC_OVERLAY_FILE={verify_mod.OVERLAY_NAME} "
             f"-DEXTRA_CONF_FILE={verify_mod.CONF_NAME}").classes("text-xs text-grey-5")

    def do_write(dlg) -> None:
        dlg.close()
        try:
            paths = verify_mod.write_to_app(app, overlay_text, conf_text)
        except OSError as exc:
            wstatus.text = f"Write failed: {exc}"
            return
        wstatus.text = "Wrote " + ", ".join(p.name for p in paths)
        ui.notify("Wrote " + ", ".join(str(p) for p in paths))

    def ask_write() -> None:
        old = verify_mod.existing_files(app)
        with ui.dialog() as dlg, ui.card():
            ui.label("Write the draft into the app folder?").classes("font-bold")
            ui.label(str(app)).classes("text-xs font-mono")
            ui.label(f"{verify_mod.OVERLAY_NAME}, {verify_mod.CONF_NAME}").classes("text-xs font-mono")
            if old:
                ui.label("Overwrites: " + ", ".join(p.name for p in old)).classes("text-xs text-orange-5")
            with ui.row():
                ui.button("Cancel", on_click=dlg.close).props("flat no-caps id=draft-write-cancel")
                ui.button("Write", on_click=lambda: do_write(dlg)).props("no-caps id=draft-write-confirm")
        dlg.open()

    wbtn.on_click(ask_write)
