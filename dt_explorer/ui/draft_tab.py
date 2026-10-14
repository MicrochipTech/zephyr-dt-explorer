"""Draft tab: the pending changes of one build.

Shows the draft nodes (edit / remove), the combined overlay snippet, the suggested Kconfig lines and
the result of the fast check. The check takes seconds, so it runs in a worker thread with a spinner.
"""
from __future__ import annotations

from nicegui import run, ui

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


def _remove(session, label: str, changed) -> None:
    session.remove(label)
    session.save()
    changed()
