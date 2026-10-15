"""Draft tab: the pending changes of one build.

Shows the draft nodes (edit / remove), the combined overlay snippet, the suggested Kconfig lines and
the result of the fast check. The check takes seconds, so it runs in a worker thread with a spinner.
"""
from __future__ import annotations

import queue

from nicegui import run, ui

from ..core import apply as apply_mod
from ..core import codegen as codegen_mod
from ..core import verify as verify_mod
from ..core.build import compat_ident
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
        _render_codegen(session, lines)


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
        ui.label("Write to app (extra files)").classes("font-bold")
        wbtn = ui.button("Write to app", icon="save").props("flat dense no-caps id=draft-write")
        wstatus = ui.label().classes("text-xs text-grey-5").props("id=draft-write-status")
    ui.label(f"Writes {verify_mod.OVERLAY_NAME} and {verify_mod.CONF_NAME} into the app folder ({app}). "
             "Zephyr only reads them if the app's CMakeLists.txt or your west command refers to them, "
             f"e.g. west build -- -DEXTRA_DTC_OVERLAY_FILE={verify_mod.OVERLAY_NAME} "
             f"-DEXTRA_CONF_FILE={verify_mod.CONF_NAME}. "
             "Use \"Apply to app\" below instead if the app should simply build with the draft.") \
        .classes("text-xs text-grey-5")
    _render_apply(build, overlay_text, conf_text)

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


def apply_app_dir(build):
    """App folder that "Apply to app" writes into (the build's APPLICATION_SOURCE_DIR)."""
    return apply_mod.app_dir_of(build)


def _render_apply(build, overlay_text: str, conf_text: str) -> None:
    """'Apply to app': the draft as boards/<board>.overlay / .conf of the build's own board.

    Same flow as in the port dialog: plan (new / exists + diff), explicit "replace" tick, write.
    """
    ui.separator()
    with ui.row().classes("items-center gap-2"):
        ui.label("Apply to app").classes("font-bold")
        abtn = ui.button("Apply to app…", icon="save").props("flat dense no-caps id=draft-apply") \
            .props('title="write boards/<board>.overlay and .conf for the board of this build; '
                   'a new file is created, an existing one gets a marked block appended"')
    ui.label("Writes the draft as boards/<board>.overlay and boards/<board>.conf for the board of this build. "
             "Zephyr picks these files up by itself, so a plain west build -b <board> uses the draft with no "
             "extra options (unlike \"Write to app\" above). A missing file is created. If the app already has "
             "such a file (its own board overlay), it is kept and the draft is APPENDED as a marked block "
             "(you see the diff first; applying again replaces only that block; conf lines are added once). "
             "Tick \"Replace\" to overwrite the file instead.").classes("text-xs text-grey-5")
    box = ui.column().classes("w-full gap-2").props("id=draft-apply-box")

    def show_plan() -> None:
        box.clear()
        try:
            app = apply_app_dir(build)
            plan = apply_mod.draft_plan(build, overlay_text, conf_text, app_dir=app)
        except apply_mod.ApplyError as exc:
            with box:
                ui.label(f"Cannot apply: {exc}").classes("text-sm text-red-4").props("id=draft-apply-error")
            return
        checks: dict[str, ui.checkbox] = {}
        with box:
            ui.label(f"App: {plan.app_dir}   board: {plan.board_target}").classes("text-xs font-mono") \
                .props("id=draft-apply-app")
            for fp in plan.files:
                with ui.column().classes("w-full gap-1").props(f"id=draft-apply-{fp.kind}"):
                    color = {"new": "text-green-4", "exists": "text-orange-4"}.get(fp.action, "text-grey-5")
                    ui.label(f"{fp.action.upper()}: {fp.path}").classes(f"text-sm font-mono {color}")
                    if fp.comment_only:
                        ui.label(f"The {fp.kind} is empty or holds only comments (nothing to apply); "
                                 "it is not written.").classes("text-xs text-grey-5")
                    if fp.note:
                        ui.label(fp.note).classes("text-xs text-orange-4")
                    if fp.identical:
                        ui.label("The existing file already has this content.").classes("text-xs text-grey-5")
                    if fp.exists and fp.merged and not fp.comment_only and not fp.identical:
                        if fp.append_note:
                            ui.label(fp.append_note).classes("text-xs text-grey-5")
                        if fp.append_diff:
                            ui.label("Default: APPEND. The existing file stays as it is; the draft goes into a "
                                     "block marked dt_explorer ... begin / end (applying again replaces only that "
                                     "block). Diff of the file after the append:").classes("text-xs text-green-4") \
                                .props(f"id=draft-apply-append-hint-{fp.kind}")
                            ui.code(fp.append_diff, language="diff").classes("w-full text-xs") \
                                .props(f"id=draft-apply-diff-{fp.kind}")
                        else:
                            ui.label("The marked block in the file is already up to date; appending changes nothing.") \
                                .classes("text-xs text-grey-5")
                    if fp.diff:
                        with ui.expansion("Replace instead: diff existing file -> draft (drops whatever the "
                                          "draft does not contain)").classes("text-xs text-orange-4") \
                                .props(f"id=draft-apply-replace-box-{fp.kind}"):
                            ui.code(fp.diff, language="diff").classes("w-full text-xs") \
                                .props(f"id=draft-apply-diff-replace-{fp.kind}")
                        checks[fp.kind] = ui.checkbox(f"Replace the existing {fp.path.name} instead of appending") \
                            .props(f"dense id=draft-apply-replace-{fp.kind}")
            writable = [f for f in plan.files if not (f.comment_only or f.identical)]
            res_label = ui.label().classes("text-sm font-mono").props("id=draft-apply-result")
            res_label.style("white-space: pre-wrap")
            res_label.set_visibility(False)
            with ui.row().classes("items-center gap-2"):
                go = ui.button("Write files", icon="check").props("no-caps id=draft-apply-confirm")
                go.set_enabled(bool(writable))
                ui.button("Cancel", icon="close", on_click=box.clear).props("flat no-caps id=draft-apply-cancel")
            if not writable:
                ui.label("Nothing to write.").classes("text-sm text-grey-5")

            def do_write() -> None:
                ow = {k for k, c in checks.items() if c.value}
                app_ = {f.kind for f in plan.files if f.exists and f.kind not in ow}   # default: append
                try:
                    res = apply_mod.apply(plan, overwrite=ow, append=app_)
                except (apply_mod.ApplyError, OSError) as exc:
                    res_label.text = f"Write failed: {exc}"
                    res_label.classes(replace="text-sm font-mono text-red-4")
                    res_label.set_visibility(True)
                    return
                lines = res.lines()
                if res.written or res.overwritten or res.appended:
                    lines.append(f"Next: west build -b {plan.board_target} {plan.app_dir}")
                res_label.text = "\n".join(lines)
                res_label.classes(replace="text-sm font-mono " + ("text-green-4" if res.written or res.overwritten or res.appended
                                                                  else "text-orange-4"))
                res_label.set_visibility(True)
                go.set_enabled(False)
                ui.notify("Board files written." if res.written or res.overwritten or res.appended else "Nothing was written.")

            go.on_click(do_write)

    abtn.on_click(show_plan)


def _driver_channels(session, compatible: str) -> list[str]:
    """SENSOR_CHAN_* that the driver of `compatible` handles (heuristic on its channel_get, see core/codegen.py)."""
    ident = compat_ident(compatible)
    files = set(session.model.drivers.get(ident, set()))
    if not files:                       # DT_DRV_COMPAT defined in a header next to the sources
        drv = session.model.zephyr_base / "drivers"
        if drv.is_dir():
            for d in codegen_mod.header_driver_dirs([drv]).get(ident, set()):
                files |= set(d.glob("*.c"))
    texts = []
    for f in sorted(files):
        try:
            texts.append(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return codegen_mod.channels_from_driver(texts)


def _render_codegen(session, conf_lines: list[str]) -> None:
    """'Generate code': the device module <app>/dtx_devices/ for the devices of the draft (core/codegen.py)."""
    ui.separator()
    with ui.row().classes("items-center gap-2"):
        ui.label("Generate code").classes("font-bold")
        gbtn = ui.button("Generate code…", icon="code").props("flat dense no-caps id=draft-codegen") \
            .props('title="plan the C module dtx_devices/ for the devices of this draft; nothing is written before you confirm"')
    ui.label("Generates <app>/dtx_devices/ (CMakeLists.txt, include/dtx_devices.h with dtx_devices_init(), one "
             "dtx_<node>.h/.c per device: sensors get dtx_<node>_read(), LEDs of gpio-leds dtx_<node>_set(bool)). "
             "src/ of the app is never touched. A file you edited by hand is not overwritten (the diff is shown). "
             "The app's CMakeLists.txt gets the one line add_subdirectory(dtx_devices) only after you confirmed "
             "its diff; Kconfig lines go into prj.conf as a marked block.").classes("text-xs text-grey-5")
    box = ui.column().classes("w-full gap-2").props("id=codegen-box")
    channels: dict[str, list[str]] = {}          # per device: the channels the user chose

    def show() -> None:
        box.clear()
        try:
            app = apply_app_dir(session.model.build)
        except apply_mod.ApplyError as exc:
            with box:
                ui.label(f"Cannot generate: {exc}").classes("text-sm text-red-4").props("id=codegen-error")
            return
        devices, notes = codegen_mod.devices_from_draft(session.draft, lambda c: _driver_channels(session, c))
        for d in devices:
            if d.cls == "sensor":
                d.channels = channels.setdefault(d.ident, d.channels)
        plan = codegen_mod.plan_generate(app, devices, notes)
        conf = list(dict.fromkeys(conf_lines + plan.kconfig))
        with box:
            if not devices:
                ui.label("The draft has no devices to generate code for (add a child device such as a sensor on an "
                         "I2C or SPI bus, or LEDs under gpio-leds).").classes("text-sm text-grey-5") \
                    .props("id=codegen-empty")
                for n in notes:
                    ui.label(n).classes("text-xs text-orange-4")
                return
            ui.label(f"App: {app}").classes("text-xs font-mono").props("id=codegen-app")
            for n in notes:
                ui.label(n).classes("text-xs text-orange-4")
            ui.label("Devices").classes("text-sm font-bold")
            for d in devices:
                with ui.row().classes("items-center gap-2 w-full").props(f"id=codegen-device-{d.ident}"):
                    ui.label(f"{d.ident}").classes("font-mono text-sm")
                    ui.label(f"{d.cls}, {d.compatible}, &{d.label}").classes("text-xs text-grey-4")
                    if d.cls == "sensor":
                        opts = list(dict.fromkeys(d.channels + codegen_mod.COMMON_CHANNELS))
                        sel = ui.select(opts, value=d.channels, multiple=True, label="channels") \
                            .props(f"dense use-chips id=codegen-chan-{d.ident}").classes("min-w-64")
                        sel.on_value_change(lambda e, i=d.ident: (channels.__setitem__(i, list(e.value)), show()))
                        ui.label("channels from the driver's channel_get; add or remove as needed") \
                            .classes("text-xs text-grey-5")
            ui.label("Files").classes("text-sm font-bold")
            colors = {"new": "text-green-4", "regenerate": "text-green-4", "unchanged": "text-grey-5",
                      "edited": "text-orange-4", "foreign": "text-orange-4"}
            for f in plan.files:
                fid = f.rel.replace("/", "-").replace(".", "_")
                with ui.column().classes("w-full gap-0").props(f"id=codegen-file-{fid}"):
                    ui.label(f"{f.state.upper()}: dtx_devices/{f.rel}").classes(f"text-xs font-mono {colors[f.state]}")
                    if f.state in ("edited", "foreign"):
                        ui.label("Edited by hand: this file is NOT overwritten. Diff of what would be generated:"
                                 if f.state == "edited" else
                                 "Not a generated file (no dt_explorer header): NOT overwritten. Diff of what would be generated:") \
                            .classes("text-xs text-orange-4")
                        ui.code(f.diff, language="diff").classes("w-full text-xs").props(f"id=codegen-diff-{fid}")
                    elif f.state == "regenerate":
                        ui.code(f.diff, language="diff").classes("w-full text-xs").props(f"id=codegen-diff-{fid}")
            for p in plan.stale:
                ui.label(f"STALE: {p} (its device is no longer in the draft; not deleted)").classes("text-xs text-orange-4")
            res = ui.label().classes("text-sm font-mono").props("id=codegen-result").style("white-space: pre-wrap")
            res.set_visibility(False)
            writable = [f for f in plan.files if f.writes]
            with ui.row().classes("items-center gap-2"):
                go = ui.button("Write files", icon="check").props("no-caps id=codegen-write")
                go.set_enabled(bool(writable))
                ui.button("Close", icon="close", on_click=box.clear).props("flat no-caps id=codegen-cancel")
            if not writable:
                ui.label("Nothing to write (all files are unchanged or edited by hand).").classes("text-xs text-grey-5")

            def do_write() -> None:
                try:
                    lines = codegen_mod.write_files(plan)
                except (ValueError, OSError) as exc:
                    res.text = f"Write failed: {exc}"
                    res.classes(replace="text-sm font-mono text-red-4")
                else:
                    res.text = "\n".join(lines)
                    res.classes(replace="text-sm font-mono text-green-4")
                    go.set_enabled(False)
                res.set_visibility(True)

            go.on_click(do_write)

            # -- the one line in the app's CMakeLists.txt
            ui.separator()
            ui.label("CMakeLists.txt of the app").classes("text-sm font-bold")
            cm_label = ui.label().classes("text-xs").props("id=codegen-cmake")
            if plan.cmake_state == "present":
                cm_label.text = f"{codegen_mod.CMAKE_LINE} is already in CMakeLists.txt; nothing to do."
            elif plan.cmake_state == "no_cmakelists":
                cm_label.text = "The app folder has no CMakeLists.txt; add the line add_subdirectory(dtx_devices) yourself."
            else:
                cm_label.text = "The line is missing. Diff of CMakeLists.txt with the line added at the end:"
                ui.code(plan.cmake_diff, language="diff").classes("w-full text-xs").props("id=codegen-cmake-diff")
                cm_res = ui.label().classes("text-sm font-mono").props("id=codegen-cmake-result")
                cm_res.set_visibility(False)
                cm_btn = ui.button("Add the line…", icon="add").props("no-caps id=codegen-cmake-add")

                def confirm_cmake() -> None:
                    with ui.dialog() as dlg, ui.card().props("id=codegen-cmake-dialog"):
                        ui.label("Add one line to the app's CMakeLists.txt?").classes("font-bold")
                        ui.code(plan.cmake_diff, language="diff").classes("text-xs")
                        with ui.row():
                            ui.button("Cancel", on_click=dlg.close).props("flat no-caps id=codegen-cmake-cancel")
                            ui.button("Add the line", on_click=lambda: do_cmake(dlg)).props("no-caps id=codegen-cmake-ok")
                    dlg.open()

                def do_cmake(dlg) -> None:
                    dlg.close()
                    cm_res.text = codegen_mod.apply_cmake_line(plan)
                    cm_res.set_visibility(True)
                    cm_btn.set_enabled(False)

                cm_btn.on_click(confirm_cmake)

            # -- prj.conf
            ui.separator()
            ui.label("prj.conf of the app").classes("text-sm font-bold")
            try:
                cfp = apply_mod.plan_prj_conf(app, "\n".join(conf) + "\n")
            except apply_mod.ApplyError as exc:
                ui.label(f"Cannot plan prj.conf: {exc}").classes("text-xs text-red-4")
                return
            if cfp.append_note:
                ui.label(cfp.append_note).classes("text-xs text-grey-5")
            conf_res = ui.label().classes("text-sm font-mono").props("id=codegen-conf-result")
            conf_res.set_visibility(False)
            if cfp.append_diff:
                ui.label("Kconfig lines the module needs, appended as a marked block (applying again replaces only "
                         "that block):").classes("text-xs text-green-4").props("id=codegen-conf")
                ui.code(cfp.append_diff, language="diff").classes("w-full text-xs").props("id=codegen-conf-diff")
                cbtn = ui.button("Append to prj.conf", icon="save").props("no-caps id=codegen-conf-confirm")

                def do_conf() -> None:
                    conf_res.text = apply_mod.apply_prj_conf(cfp)
                    conf_res.set_visibility(True)
                    cbtn.set_enabled(False)

                cbtn.on_click(do_conf)
            else:
                ui.label("prj.conf already has these lines (or the block is up to date); nothing to add.") \
                    .classes("text-xs text-grey-5").props("id=codegen-conf")

    gbtn.on_click(show)
