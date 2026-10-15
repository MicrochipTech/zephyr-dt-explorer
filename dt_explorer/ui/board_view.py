"""Board tab: the connectors of the board (from its board description) as pin headers, coloured by use.

Every pin shows its number, its connector role and the SoC pad behind it. Colour = state of that pad in
the current build (used / conflict / free), in the configurator's draft (draft), or "unavailable" when no
pad is traced to the pin; supply and ID pins are "other". A click on a pin lists its uses.

The pure parts (``pin_states``, ``connector_svg``) do not need the GUI toolkit; it is imported only by
``render_board_view``.
"""
from __future__ import annotations

from html import escape

from ..connectors import arduino_uno_r3 as _ard
from ..core import board_desc as bd
from ..core import pins

COLORS = {          # fill, stroke
    "free": ("#4c566a", "#6b7790"),
    "used": ("#2e9d4a", "#5fd37c"),
    "conflict": ("#e8a317", "#ffd166"),
    "other": ("#2b3a55", "#44587e"),
    "draft": ("#a64dff", "#d9a8ff"),
    "unavailable": ("#262c38", "#59627a"),
}
LABELS = {"free": "free", "used": "used", "conflict": "conflict (more than one user)",
          "other": "supply / ID", "draft": "draft (not built yet)",
          "unavailable": "no SoC pad traced"}
BG, BODY, TEXT, DIM, SELECT = "#1b1f27", "#262c38", "#d8dee9", "#8791a5", "#ffffff"

CELL_W, BOX_H, GAP_Y, LABEL_H, MARGIN = 78.0, 46.0, 10.0, 22.0, 16.0
ROW_EXTRA = 38.0     # caption above and role label below a row of a multi-header connector


SUPPLY_ROLES = ("3V3", "5V", "VIN", "IOREF")     # roles of mikroBUS / Arduino supply pins (no pad expected)


def _is_signal(role: str) -> bool:
    return bd.is_signal_role(role) and role.upper() not in SUPPLY_ROLES


def pin_states(conn: bd.Connector, usage: pins.PinUsage | None,
               draft_pads: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """pin key -> {state, users (list of {path, use}), pad, role, net, gap} for one connector."""
    out: dict[str, dict] = {}
    for key, p in conn.pins.items():
        users: list[dict] = []
        if not p.pad:
            state = "other" if not _is_signal(p.role) else "unavailable"
        else:
            uses = usage.uses.get(p.pad, []) if usage is not None else []
            users = [{"path": u.node, "use": f"{u.source}: {u.function}" + (f" ({u.state})" if u.state else "")}
                     for u in uses]
            state = "free" if not uses else ("used" if len(uses) == 1 else "conflict")
            if draft_pads and p.pad in draft_pads:
                state = "draft"
                users += [{"path": "draft", "use": t} for t in draft_pads[p.pad]]
        out[key] = {"state": state, "users": users, "pad": p.pad, "role": p.role, "net": p.net, "gap": p.gap}
    return out


def _rows_of(keys: list[str]) -> list[list[str]]:
    """Two rows (odd pins above even pins, pin 1 top left) for an even pin count of 6 or more."""
    if len(keys) >= 6 and len(keys) % 2 == 0 and all(k.isdigit() for k in keys):
        nums = sorted(keys, key=int)
        return [nums[0::2], nums[1::2]]
    return [keys]


def _layout(conn: bd.Connector) -> list[tuple[str, list[str]]] | None:
    """(caption, pin keys) per drawn row for connector types that are several headers; None otherwise.

    mikroBUS: two 1x8 rows (pins 1..8 with the 3.3 V pin, pins 9..16 with the 5 V pin). Arduino Uno R3:
    the four headers power, analog, digital low, digital high. Pins the file has but the layout does
    not know go to a last row so that nothing is hidden.
    """
    if conn.type == "mikrobus":
        spec = [("Left row (3.3 V)", [str(i) for i in range(1, 9)]),
                ("Right row (5 V)", [str(i) for i in range(9, 17)])]
    elif conn.type == "arduino_uno_r3":
        spec = [(_ard.HEADER_TITLES[k], list(keys)) for k, keys in _ard.HEADER_ROWS]
    else:
        return None
    rows = [(cap, [k for k in keys if k in conn.pins]) for cap, keys in spec]
    known = {k for _c, keys in rows for k in keys}
    rest = [k for k in conn.pins if k not in known]
    if rest:
        rows.append(("Other pins", rest))
    return [(cap, keys) for cap, keys in rows if keys]


def _tooltip(conn: bd.Connector, key: str, st: dict) -> str:
    lines = [f"{conn.name} pin {key}" + (f"  {st['role']}" if st["role"] else "")
             + (f"  pad {st['pad']}" if st["pad"] else "") + f"  ({st['state']})"]
    if st["net"]:
        lines.append(f"net {st['net']}")
    lines += [f"{u['path']}  {u['use']}" for u in st["users"]]
    return "\n".join(lines)


def _cell(conn: bd.Connector, key: str, st: dict, x: float, y: float, selected: str | None,
          role_y: float, square: bool) -> str:
    """One clickable pin box: key (top left), SoC pad (bold), connector role (above or below)."""
    fill, stroke = COLORS[st["state"]]
    sel = key == selected
    w = CELL_W - 8
    dash = ' stroke-dasharray="4 3"' if st["state"] == "unavailable" else ""
    rx = 0 if square else 8                      # pin 1 has square corners
    role = escape(bd.short_role(st["role"]))
    pad = escape(st["pad"] or "-")
    kk = escape(f"{conn.name}:{key}", quote=True)
    return (
        f'<g class="bpin" data-pin="{kk}" onclick="emitEvent(\'board_pin\', \'{kk}\')">'
        f'<title>{escape(_tooltip(conn, key, st))}</title>'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{BOX_H}" rx="{rx}" fill="{fill}" '
        f'stroke="{SELECT if sel else stroke}" stroke-width="{2.4 if sel else 1}"{dash}/>'
        f'<text x="{x + 6:.1f}" y="{y + 13:.1f}" font-size="10" fill="{TEXT}" opacity="0.75">{escape(key)}</text>'
        f'<text x="{x + w / 2:.1f}" y="{y + 32:.1f}" text-anchor="middle" font-size="14" '
        f'font-weight="bold" fill="{TEXT}">{pad}</text>'
        f'<text x="{x + w / 2:.1f}" y="{role_y:.1f}" text-anchor="middle" font-size="11" fill="{DIM}">{role}</text>'
        f'</g>')


def connector_svg(conn: bd.Connector, states: dict[str, dict], selected: str | None = None) -> str:
    """SVG text of one connector. `selected` is a pin key."""
    keys = list(conn.pins)
    layout = _layout(conn)
    if layout:                                   # several headers: one captioned row per header
        rows = [r for _c, r in layout]
        ncol = max(len(r) for r in rows)
        W = ncol * CELL_W + 2 * MARGIN
        pitch = BOX_H + ROW_EXTRA
        H = 2 * MARGIN + len(rows) * pitch
    else:
        rows = _rows_of(keys)
        ncol = max(len(r) for r in rows) if rows else 1
        W = ncol * CELL_W + 2 * MARGIN
        H = 2 * MARGIN + 2 * LABEL_H + len(rows) * BOX_H + (len(rows) - 1) * GAP_Y
    two = len(rows) == 2
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="100%" '
           f'style="max-width:{W * 1.25:.0f}px; background:{BG}; font-family: Consolas, monospace" '
           f'data-connector="{escape(conn.name, quote=True)}">',
           '<style>.bpin{cursor:pointer}.bpin:hover rect{stroke:#fff;stroke-width:2}</style>',
           f'<rect x="{MARGIN / 2}" y="{MARGIN / 2}" width="{W - MARGIN:.1f}" '
           f'height="{H - MARGIN:.1f}" rx="6" fill="{BODY}" stroke="{DIM}"/>']
    for ri, row in enumerate(rows):
        if layout:
            y = MARGIN + 16 + ri * pitch
            out.append(f'<text x="{MARGIN + 4}" y="{y - 5:.1f}" font-size="11" fill="{TEXT}" '
                       f'opacity="0.8">{escape(layout[ri][0])}</text>')
        else:
            y = MARGIN + LABEL_H + ri * (BOX_H + GAP_Y)
        for ci, key in enumerate(row):
            if layout:
                role_y = y + BOX_H + 14
                square = conn.type == "mikrobus" and key in ("1", "9")
            else:
                role_y = y + BOX_H + 14 if (two and ri == 1) else y - 6
                square = key == "1"
            out.append(_cell(conn, key, states[key], MARGIN + ci * CELL_W + 4, y, selected, role_y, square))
    out.append("</svg>")
    return "".join(out)


def _legend(counts: dict[str, int]) -> None:
    from nicegui import ui
    with ui.row().classes("items-center gap-4"):
        for st in ("free", "used", "conflict", "draft", "other", "unavailable"):
            if st in ("draft", "conflict") and not counts.get(st):
                continue
            with ui.row().classes("items-center gap-1"):
                ui.html(f'<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
                        f'background:{COLORS[st][0]};border:1px solid {COLORS[st][1]}"></span>', sanitize=False)
                ui.label(f"{LABELS[st]}: {counts.get(st, 0)}").classes("text-xs text-grey-4")


def render_board_view(container, model, goto, draft_pads=None, dirs=None) -> None:
    """Fill `container` with the connectors of `model`'s board (a BuildModel); `goto(path)` opens a node.

    `draft_pads()` (optional) returns {pad: [text]} of the pads the configurator's draft takes.
    `dirs` overrides the board description directories (tests).
    """
    from nicegui import ui

    from ..core.buildinfo import read_build_info
    from .pinmap import load_builtin_plugins
    load_builtin_plugins()
    container.clear()
    info = read_build_info(model.build)
    try:
        desc = bd.load_board(info.board, dirs)
        err = None
    except ValueError as exc:
        desc, err = None, str(exc)
    usage = pins.used_pins(model, info)
    funcs = pins.pad_functions(usage.device) if usage.device is not None else {}
    with container:
        if desc is None or not desc.connectors:
            ui.label(err or f"No board description for '{info.board or '?'}', so the board view cannot be shown.") \
                .classes("text-grey-5")
            return
        state = {"selected": None}
        with ui.row().classes("items-center gap-3 w-full"):
            ui.label(desc.title).classes("text-lg font-bold")
            ui.label(f"{len(desc.connectors)} connectors").classes("text-xs text-grey-5")
        legend_box = ui.row()
        detail = ui.column().classes("w-full gap-0")
        svg_box = ui.column().classes("w-full gap-2")

        def states_of(conn):
            return pin_states(conn, usage, draft_pads() if draft_pads else None)

        def show_detail() -> None:
            detail.clear()
            with detail:
                sel = state["selected"]
                if not sel:
                    ui.label("Click a pin to see its use.").classes("text-xs text-grey-6")
                    return
                cname, key = sel.split(":", 1)
                conn = desc.connector(cname)
                st = states_of(conn).get(key) if conn else None
                if st is None:
                    return
                ui.label(f"{cname} pin {key}  -  {st['role'] or '-'}  -  pad {st['pad'] or '-'}  -  {st['state']}") \
                    .classes("font-bold")
                if st["net"]:
                    ui.label(f"net {st['net']}").classes("text-xs text-grey-5")
                if st["gap"]:
                    ui.label(f"not traced: {st['gap']}").classes("text-xs text-grey-5")
                if st["pad"] and not st["users"]:
                    ui.label("not used by the devicetree").classes("text-xs text-grey-5")
                for u in st["users"]:
                    with ui.row().classes("items-center gap-2"):
                        if u["path"] != "draft":
                            ui.link(u["path"], "#").classes("text-blue-4 text-sm") \
                                .on("click.prevent", lambda _e, p=u["path"]: goto(p))
                        ui.label(u["use"]).classes("text-xs text-grey-4")
                if st["pad"] and funcs.get(st["pad"]):
                    ui.label("Possible functions: " + ", ".join(funcs[st["pad"]])) \
                        .classes("text-xs text-grey-6").style("white-space: normal")

        def refresh() -> None:
            counts: dict[str, int] = {}
            svg_box.clear()
            with svg_box:
                for conn in desc.connectors:
                    sts = states_of(conn)
                    for s in sts.values():
                        counts[s["state"]] = counts.get(s["state"], 0) + 1
                    ui.label(f"{conn.name}" + (f"  ({conn.designator})" if conn.designator else "")
                             + f"  -  {conn.type}" + (f"  -  {conn.status}" if conn.status else "")) \
                        .classes("text-sm text-grey-4")
                    sel = state["selected"].split(":", 1)[1] if (
                        state["selected"] and state["selected"].startswith(conn.name + ":")) else None
                    ui.html(connector_svg(conn, sts, sel), sanitize=False).classes("w-full")
            legend_box.clear()
            with legend_box:
                _legend(counts)
            show_detail()

        def on_pin(e) -> None:
            state["selected"] = e.args if isinstance(e.args, str) else None
            refresh()

        ui.on("board_pin", on_pin)
        refresh()
        for n in usage.notes[:3]:
            ui.label(n).classes("text-xs text-grey-6")
        if len(usage.notes) > 3:
            ui.label(f"... and {len(usage.notes) - 3} more notes").classes("text-xs text-grey-6")
