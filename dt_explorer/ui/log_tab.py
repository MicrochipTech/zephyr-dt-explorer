"""The Log tab: the lines of the core log (ring buffer), live, with level filter, DEBUG switch and Copy."""

from __future__ import annotations

import json
import logging

from nicegui import ui

from ..core import log as dtlog

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING}


def copy_text(model, min_level: int) -> str:
    """Context header + the visible lines: what the Copy button puts on the clipboard."""
    head = ["# Devicetree Explorer log"] + ["# " + ln for ln in dtlog.context_header(model)]
    return "\n".join(head + [""] + dtlog.ring().lines(min_level)) + "\n"


def render_log_tab(container, get_model) -> None:
    """Fill `container`. `get_model()` returns the open BuildModel (for the Copy header)."""
    container.clear()
    ring = dtlog.ring()
    start = "DEBUG" if dtlog.is_debug() else "INFO"
    st = {"seq": 0, "level": LEVELS[start]}
    with container:
        with ui.row().classes("items-center gap-2 w-full"):
            level = ui.select(list(LEVELS), value=start, label="Level").props("dense options-dense dark") \
                .classes("w-32").props("id=log-level")
            dbg = ui.switch("DEBUG", value=dtlog.is_debug()).props("dense id=log-debug") \
                .tooltip("Also write DEBUG lines (state() reasons, ...) to the buffer and the log file")
            ui.space()
            ui.button("Copy", icon="content_copy", on_click=lambda: do_copy()).props("flat dense no-caps id=log-copy") \
                .tooltip("Copy the context header and the visible lines")
            ui.button(icon="vertical_align_bottom", on_click=lambda: scroll_end()).props("flat dense round") \
                .tooltip("Scroll to the end")
        path = dtlog.file_path()
        ui.label(f"File: {path}" if path else "No log file (folder not writable)") \
            .classes("text-xs text-grey-5 font-mono").props("id=log-file")
        box = ui.log(max_lines=dtlog.RING_SIZE).classes("w-full font-mono text-xs") \
            .style("height: calc(100vh - 230px); background:#0d1117").props("id=log-box")

    def refill():
        box.clear()
        st["seq"] = 0
        poll()

    def poll():
        for seq, _lvl, text in ring.since(st["seq"], st["level"]):
            box.push(text)
        # new seq even when everything is filtered out
        st["seq"] = ring.seq

    def on_level(_=None):
        st["level"] = LEVELS[level.value]
        refill()

    def on_debug(_=None):
        dtlog.set_debug(bool(dbg.value))
        if dbg.value and level.value != "DEBUG":
            level.set_value("DEBUG")
        elif not dbg.value and level.value == "DEBUG":
            level.set_value("INFO")

    def do_copy():
        text = copy_text(get_model(), st["level"])
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)})")
        ui.notify(f"Copied {text.count(chr(10))} lines", type="positive")

    def scroll_end():
        ui.run_javascript('const e=document.getElementById("log-box"); if(e) e.scrollTop=e.scrollHeight')

    level.on_value_change(on_level)
    dbg.on_value_change(on_debug)
    poll()
    with container:
        ui.timer(1.0, poll)
