"""The Changes tab: what git sees in the app folder of the open build (read only).

The list is ``git status --porcelain`` limited to the app folder, the diff is ``git diff`` against HEAD
(untracked files: the whole file as added) in the source viewer. Files that the tool writes carry a "dtx"
badge. The state is read when the tab opens and on **Refresh**, never by polling. No commit, revert or checkout.
"""

from __future__ import annotations

import html

from nicegui import ui

from ..core import apply as apply_mod
from ..core import git_status as gs

CSS = """
.src .add  { background: #1d3b26; }
.src .del  { background: #4a1f1f; }
.src .hunk { color: #79c0ff; background: #14253a; }
.src .meta { color: #8b949e; }
"""

KIND_STYLE = {"modified": ("M", "#ffd54f"), "added": ("A", "#81c784"), "untracked": ("?", "#81c784"),
              "deleted": ("D", "#e57373"), "renamed": ("R", "#90caf9"), "conflict": ("U", "#ff8a65")}


def app_dir_of(model):
    """App folder of the open build (APPLICATION_SOURCE_DIR); tests replace this."""
    return apply_mod.app_dir_of(model.build)


def board_stems(model) -> list[str]:
    """File stems Apply to app uses for the board of this build (``boards/<stem>.overlay``)."""
    try:
        target, single = apply_mod.target_of(model.build)
        return list(apply_mod.board_file_stems(target, single)[1])
    except apply_mod.ApplyError:
        return []


def diff_html(lines: list[gs.DiffLine]) -> str:
    """The diff as rows for the source viewer (classes add / del / hunk / meta / plain)."""
    rows = []
    for d in lines:
        num = d.new if d.new is not None else d.old
        sign = {"add": "+", "del": "-", "ctx": " "}.get(d.kind, "")
        cls = d.kind if d.kind != "ctx" else ""
        rows.append(f'<div class="{cls}"><span class="ln">{"" if num is None else num}</span>'
                    f'{html.escape(sign + d.text) or " "}</div>')
    return "".join(rows)


def render_changes_tab(container, get_model, open_diff, set_count) -> gs.GitState | None:
    """(Re)fill `container`; reads git now. `open_diff(title, html, kind, dtx)` shows the diff in the source
    viewer, `set_count(n | None)` updates the count on the tab. Returns the state read."""
    model = get_model()
    if not getattr(container, "_dtx_changes_css", False):
        ui.add_css(CSS)
        container._dtx_changes_css = True
    container.clear()
    state: gs.GitState | None = None
    with container:
        with ui.row().classes("items-center gap-2 w-full"):
            ui.button("Refresh", icon="refresh",
                      on_click=lambda: render_changes_tab(container, get_model, open_diff, set_count)) \
                .props('flat dense no-caps id=changes-refresh '
                       'title="Read git again (the tab reads only when it opens and here)"')
            info = ui.label("").classes("text-xs text-grey-5 font-mono").props("id=changes-info")
        try:
            app = app_dir_of(model)
        except apply_mod.ApplyError as exc:
            set_count(None)
            info.text = "not read"
            ui.label(f"The app folder of this build is unknown: {exc}").classes("text-sm text-orange-4") \
                .props("id=changes-message")
            return None
        state = gs.read_status(app, board_stems(model))
        if not state.ok:
            set_count(None)
            info.text = f"read {state.read_at:%H:%M:%S}"
            ui.label(state.message).classes("text-sm text-orange-4").props("id=changes-message")
            ui.label(f"App folder: {app}").classes("text-xs text-grey-5 font-mono")
            return state
        set_count(state.count)
        head = state.head or "(no commits yet)"
        info.text = f"read {state.read_at:%H:%M:%S} · branch {state.branch or '(none)'} · HEAD {head}"
        ui.label(f"App folder: {app}" + (f"  ·  repository: {state.root}" if state.root else "")) \
            .classes("text-xs text-grey-5 font-mono break-all").props("id=changes-app")
        if not state.files:
            ui.label("No changes: the app folder matches HEAD (ignored files are not listed).") \
                .classes("text-sm").props("id=changes-clean")
            return state
        ui.label(f"{state.count} changed file{'s' if state.count != 1 else ''}") \
            .classes("text-sm font-bold").props("id=changes-count")
        for i, ch in enumerate(state.files):
            letter, color = KIND_STYLE.get(ch.kind, ("?", "#ffffff"))
            with ui.row().classes("items-center gap-1 w-full no-wrap").props(f"id=changes-row-{i}"):
                ui.label(letter).style(f"color:{color}; width:1.2em").classes("font-mono font-bold") \
                    .props(f'id=changes-kind-{i} title="{ch.kind}{" (staged)" if ch.staged else ""}"')
                ui.button(ch.rel + (f"  (from {ch.orig})" if ch.orig else ""),
                          on_click=lambda _e, c=ch: _show(state, c, open_diff)) \
                    .props(f"flat dense no-caps size=sm color=light-blue-3 id=changes-file-{i}") \
                    .classes("font-mono")
                if ch.dtx:
                    ui.badge("dtx", color="purple-6").props(
                        f'id=changes-dtx-{i} title="A file the tool writes (overlay / conf / marked block / dtx_devices)"')
                ui.label(ch.kind).classes("text-xs text-grey-6")
    return state


def _show(state: gs.GitState, change: gs.Change, open_diff) -> None:
    open_diff(change.rel, diff_html(gs.read_diff(state, change)), change.kind, change.dtx)
