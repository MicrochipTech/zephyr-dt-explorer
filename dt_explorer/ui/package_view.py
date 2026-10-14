"""Package tab: read-only SVG of the chip package with every pin coloured by its state."""
from __future__ import annotations

from html import escape

from nicegui import ui

from ..core import package_layout as pl
from ..core import pins
from ..core.buildinfo import read_build_info
from .pinmap import _row, load_builtin_plugins

COLORS = {          # fill, stroke
    "free": ("#4c566a", "#6b7790"),
    "used": ("#2e9d4a", "#5fd37c"),
    "conflict": ("#e8a317", "#ffd166"),
    "other": ("#2b3a55", "#44587e"),
}
LABELS = {"free": "free", "used": "used", "conflict": "conflict (more than one user)",
          "other": "power / other"}
BG, BODY, TEXT, DIM = "#1b1f27", "#262c38", "#d8dee9", "#8791a5"
SELECT = "#ffffff"

PITCH_EDGE = 16.0        # px per pin pitch of four-sided packages
PITCH_GRID = 36.0
LEAD = 12.0              # lead length of four-sided packages
MARGIN_EDGE = 78.0       # room for the pin labels around the body
MARGIN_GRID = 44.0


def _tooltip(r: dict) -> str:
    lines = [f"pin {r['position']}  pad {r['pad']}  ({r['state']})"]
    lines += [f"{u['path']}  {u['use']}" for u in r["users"]]
    return "\n".join(lines)


def _pin_shape(r: dict, p: pl.PinPos, scale: float, ox: float, oy: float, selected: bool) -> str:
    fill, stroke = COLORS[r["state"]]
    if selected:
        stroke = SELECT
    sw = 2.2 if selected else 1
    x, y = ox + p.x * scale, oy + p.y * scale
    pos = escape(r["position"], quote=True)
    label = escape(r["pad"])
    if p.side == "grid":
        r_ = scale * 0.4
        shape = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_:.1f}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{sw}"/>')
        text = (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
                f'font-size="{scale * (0.26 if len(label) <= 4 else 0.19):.1f}" fill="{TEXT}">{label}</text>')
    else:
        t = scale * 0.62                                   # lead thickness
        dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[p.side]
        if dx:
            rx, ry, rw, rh = (x - LEAD if dx < 0 else x), y - t / 2, LEAD, t
        else:
            rx, ry, rw, rh = x - t / 2, (y - LEAD if dy < 0 else y), t, LEAD
        shape = (f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"/>')
        gap = LEAD + 4
        tx, ty = x + dx * gap, y + dy * gap
        anchor = {"left": "end", "right": "start"}.get(p.side, "middle")
        if p.rotation:
            # vertical labels, reading bottom to top; the end nearest to the pin touches the lead
            text = (f'<text transform="translate({tx:.1f},{ty:.1f}) rotate(-90)" text-anchor="'
                    f'{"start" if p.side == "top" else "end"}" dominant-baseline="central" '
                    f'font-size="{scale * 0.62:.1f}" fill="{TEXT}">{label}</text>')
        else:
            text = (f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" dominant-baseline="central" '
                    f'font-size="{scale * 0.62:.1f}" fill="{TEXT}">{label}</text>')
    return (f'<g class="pkgpin" data-pos="{pos}" onclick="emitEvent(\'pkg_pin\', \'{pos}\')">'
            f'<title>{escape(_tooltip(r))}</title>{shape}{text}</g>')


def build_svg(lay: pl.PackageLayout, rows: list[dict], title: str, selected: str | None = None) -> str:
    """SVG text for one package. `rows` are the dicts of ``pinmap._row`` (state, users, ...)."""
    grid = lay.kind == "BGA"
    scale = PITCH_GRID if grid else PITCH_EDGE
    m = MARGIN_GRID if grid else MARGIN_EDGE
    b = lay.body
    extra_h = (1.6 * scale) if (grid and lay.extras) else 0
    W, H = b.w * scale + 2 * m, b.h * scale + 2 * m + extra_h
    by_pos = {r["position"]: r for r in rows}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="100%" '
           f'style="max-height: calc(100vh - 250px); background:{BG}; font-family: Consolas, monospace">',
           '<style>.pkgpin{cursor:pointer}.pkgpin:hover rect,.pkgpin:hover circle{stroke:#fff;stroke-width:2}'
           '</style>']
    if grid:
        out.append(f'<rect x="{m}" y="{m}" width="{b.w * scale:.1f}" height="{b.h * scale:.1f}" rx="6" '
                   f'fill="{BODY}" stroke="{DIM}"/>')
        for j, c in enumerate(lay.cols):
            x = m + (j + 1) * scale
            out.append(f'<text x="{x:.1f}" y="{m - 12:.1f}" text-anchor="middle" font-size="12" '
                       f'fill="{DIM}">{c}</text>')
        for i, name in enumerate(lay.rows):
            y = m + (i + 1) * scale
            out.append(f'<text x="{m - 14:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="central" '
                       f'font-size="12" fill="{DIM}">{escape(name)}</text>')
    else:
        out.append(f'<rect x="{m}" y="{m}" width="{b.w * scale:.1f}" height="{b.h * scale:.1f}" rx="4" '
                   f'fill="{BODY}" stroke="{DIM}"/>')
        out.append(f'<circle cx="{m + scale * 1.4:.1f}" cy="{m + scale * 1.4:.1f}" r="{scale * 0.45:.1f}" '
                   f'fill="none" stroke="{DIM}"/>')
        out.append(f'<text x="{m + b.w * scale / 2:.1f}" y="{m + b.h * scale / 2 - (30 if lay.extras else 0):.1f}" '
                   f'text-anchor="middle" font-size="20" fill="{DIM}">{escape(title)}</text>')
    for pos, p in lay.pins.items():
        r = by_pos.get(pos)
        if r is not None:
            out.append(_pin_shape(r, p, scale, m, m, pos == selected))
    for pos, rect in lay.extras.items():
        r = by_pos.get(pos)
        state = r["state"] if r else "other"
        fill, stroke = COLORS[state]
        sel = pos == selected
        pe = escape(pos, quote=True)
        x, y, w, h = m + rect.x * scale, m + rect.y * scale, rect.w * scale, rect.h * scale
        if grid:
            x, y = m + rect.x * scale, m + (b.h - 0.2) * scale + 6
            w = h = rect.w * scale
        out.append(f'<g class="pkgpin" onclick="emitEvent(\'pkg_pin\', \'{pe}\')">'
                   f'<title>{escape(_tooltip(r) if r else "pin " + pos)}</title>'
                   f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" fill="{fill}" '
                   f'stroke="{SELECT if sel else stroke}" stroke-width="{2.2 if sel else 1}"/>'
                   f'<text x="{x + w / 2:.1f}" y="{y + h / 2:.1f}" text-anchor="middle" '
                   f'dominant-baseline="central" font-size="11" fill="{TEXT}">'
                   f'{escape(r["pad"] if r else pos)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def _legend(counts: dict[str, int]) -> None:
    with ui.row().classes("items-center gap-4"):
        for st in ("free", "used", "conflict", "other"):
            with ui.row().classes("items-center gap-1"):
                ui.html(f'<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
                        f'background:{COLORS[st][0]};border:1px solid {COLORS[st][1]}"></span>',
                        sanitize=False)
                ui.label(f"{LABELS[st]}: {counts.get(st, 0)}").classes("text-xs text-grey-4")


def render_package_view(container, model, goto) -> None:
    """Fill `container` with the package picture of `model` (a BuildModel); `goto(path)` opens a node."""
    load_builtin_plugins()
    container.clear()
    info = read_build_info(model.build)
    usage = pins.used_pins(model, info)
    with container:
        if usage.device is None or not usage.device.packages:
            ui.label("No device data available for this build, so the package view cannot be shown.") \
                .classes("text-grey-5")
            for n in usage.notes:
                ui.label(n).classes("text-xs text-grey-6")
            return
        dev = usage.device
        default_pkg = pins.default_package(dev).name
        packages = {p.name: p for p in dev.packages}
        state = {"selected": None}

        with ui.row().classes("items-center gap-3 w-full"):
            ui.label(dev.name).classes("text-lg font-bold")
            pkg_sel = ui.select(list(packages), value=default_pkg, label="Package") \
                .props("dense outlined").classes("w-40")
            summary = ui.label().classes("text-xs text-grey-5")
        legend_box = ui.row()
        detail = ui.column().classes("w-full gap-0")
        svg_box = ui.column().classes("w-full items-center")

        def rows_for() -> dict[str, dict]:
            return {r["position"]: r for r in (_row(x) for x in pins.pin_rows(usage, pkg_sel.value))}

        def show_detail() -> None:
            detail.clear()
            r = rows_for().get(state["selected"])
            with detail:
                if r is None:
                    ui.label("Click a pin to see its use.").classes("text-xs text-grey-6")
                    return
                ui.label(f"Pin {r['position']}  -  pad {r['pad']}  -  {r['state']}").classes("font-bold")
                if not r["users"]:
                    ui.label("not used by the devicetree").classes("text-xs text-grey-5")
                for u in r["users"]:
                    with ui.row().classes("items-center gap-2"):
                        ui.link(u["path"], "#").classes("text-blue-4 text-sm") \
                            .on("click.prevent", lambda _e, p=u["path"]: goto(p))
                        ui.label(u["use"]).classes("text-xs text-grey-4")
                if r["functions_text"]:
                    ui.label("Possible functions: " + r["functions_text"]) \
                        .classes("text-xs text-grey-6").style("white-space: normal")

        def refresh() -> None:
            pkg = packages[pkg_sel.value]
            rows = rows_for()
            try:
                lay = pl.layout_for(pkg.kind, pkg.pin_count, pkg.pins)
            except ValueError as e:
                svg_box.clear()
                with svg_box:
                    ui.label(f"No picture for package {pkg.name}: {e}").classes("text-grey-5")
                return
            counts: dict[str, int] = {}
            for r in rows.values():
                counts[r["state"]] = counts.get(r["state"], 0) + 1
            summary.text = f"{len(rows)} positions"
            legend_box.clear()
            with legend_box:
                _legend(counts)
            svg_box.clear()
            with svg_box:
                ui.html(build_svg(lay, list(rows.values()), pkg.name, state["selected"]), sanitize=False) \
                    .classes("w-full")
            show_detail()

        def on_pin(e) -> None:
            state["selected"] = e.args if isinstance(e.args, str) else None
            refresh()

        ui.on("pkg_pin", on_pin)
        pkg_sel.on_value_change(lambda _: (state.update(selected=None), refresh()))
        refresh()
        for n in usage.notes:
            ui.label(n).classes("text-xs text-grey-6")
