"""Pins tab: read-only table of all package pins and what the devicetree does with them."""
from __future__ import annotations

import pkgutil
from importlib import import_module

from nicegui import ui

from ..core import pins
from ..core.buildinfo import read_build_info

_loaded = False


def load_builtin_plugins() -> None:
    """Import every module of the providers/formatters packages so that they register themselves."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .. import formatters, providers
    for pkg in (providers, formatters):
        for m in pkgutil.iter_modules(pkg.__path__):
            import_module(f"{pkg.__name__}.{m.name}")


COLUMNS = [
    {"name": "position", "label": "Pin", "field": "position", "align": "left", "sortable": True},
    {"name": "pad", "label": "Pad", "field": "pad", "align": "left", "sortable": True},
    {"name": "state", "label": "State", "field": "state", "align": "left", "sortable": True},
    {"name": "users", "label": "Used by (node)", "field": "users_text", "align": "left"},
    {"name": "use", "label": "Role / function", "field": "use_text", "align": "left"},
    {"name": "functions", "label": "All possible functions", "field": "functions_text", "align": "left"},
]

BODY_SLOT = r"""
<q-tr :props="props" :class="'pin-' + props.row.state">
  <q-td key="position" :props="props">{{ props.row.position }}</q-td>
  <q-td key="pad" :props="props"><b>{{ props.row.pad }}</b></q-td>
  <q-td key="state" :props="props">
    <q-badge :color="props.row.state === 'conflict' ? 'orange-8' : props.row.state === 'used' ? 'green-8'
                     : props.row.state === 'free' ? 'grey-8' : 'blue-grey-9'">{{ props.row.state }}</q-badge>
  </q-td>
  <q-td key="users" :props="props">
    <div v-for="u in props.row.users" :key="u.path + u.use">
      <a class="cursor-pointer text-blue-4" @click="$parent.$emit('goto', u.path)">{{ u.path }}</a>
    </div>
  </q-td>
  <q-td key="use" :props="props">
    <div v-for="u in props.row.users" :key="u.path + u.use">{{ u.use }}</div>
  </q-td>
  <q-td key="functions" :props="props" class="text-grey-5" style="white-space: normal; font-size: 11px">
    {{ props.row.functions_text }}
  </q-td>
</q-tr>
"""


CSS = """
.pin-used td { background: #14301a !important; }
.pin-conflict td { background: #4a3410 !important; }
.pin-other td { color: #9e9e9e; }
"""


def _row(r: dict) -> dict:
    uses = r["uses"]
    if not r["is_pad"]:
        state = "other"
    elif not uses:
        state = "free"
    elif len(uses) > 1:
        state = "conflict"
    else:
        state = "used"
    users = [{"path": u.node, "use": f"{u.source}: {u.function}" + (f" ({u.state})" if u.state else "")}
             for u in uses]
    return {"position": r["position"], "pad": r["pad"], "state": state, "users": users,
            "users_text": " ".join(u["path"] for u in users),
            "use_text": " ".join(u["use"] for u in users),
            "functions_text": ", ".join(r["functions"])}


def render_pin_map(container, model, goto) -> None:
    """Fill `container` with the pin table of `model` (a BuildModel); `goto(path)` opens a node."""
    load_builtin_plugins()
    ui.add_css(CSS)
    container.clear()
    info = read_build_info(model.build)
    usage = pins.used_pins(model, info)
    with container:
        if usage.device is None or not usage.device.packages:
            ui.label("No device data available for this build, so the pin map cannot be shown.") \
                .classes("text-grey-5")
            for n in usage.notes:
                ui.label(n).classes("text-xs text-grey-6")
            return
        dev = usage.device
        default_pkg = pins.default_package(dev).name

        def build_rows(pkg_name: str, only: str) -> list[dict]:
            rows = [_row(r) for r in pins.pin_rows(usage, pkg_name)]
            return [r for r in rows if only == "all" or r["state"] == only
                    or (only == "used" and r["state"] == "conflict")]

        with ui.row().classes("items-center gap-3 w-full"):
            ui.label(f"{dev.name}").classes("text-lg font-bold")
            pkg = ui.select([p.name for p in dev.packages], value=default_pkg, label="Package") \
                .props("dense outlined").classes("w-40")
            only = ui.select({"all": "all pins", "used": "used", "free": "free"}, value="all",
                             label="Show").props("dense outlined").classes("w-32")
            flt = ui.input("Filter (pin, pad, node, function)").props("dense clearable outlined") \
                .classes("w-80")
            summary = ui.label().classes("text-xs text-grey-5")
        table = ui.table(columns=COLUMNS, rows=[], row_key="position", pagination={"rowsPerPage": 0}) \
            .classes("w-full").props("dense flat virtual-scroll").style("max-height: calc(100vh - 230px)")
        table.add_slot("body", BODY_SLOT)
        table.on("goto", lambda e: goto(e.args))
        flt.bind_value_to(table, "filter")

        def refresh():
            table.rows = build_rows(pkg.value, only.value)
            table.update()
            all_rows = [_row(r) for r in pins.pin_rows(usage, pkg.value)]
            used = sum(1 for r in all_rows if r["state"] in ("used", "conflict"))
            free = sum(1 for r in all_rows if r["state"] == "free")
            conf = sum(1 for r in all_rows if r["state"] == "conflict")
            summary.text = f"{len(all_rows)} pins, {used} used, {free} free, {conf} with more than one user"

        pkg.on_value_change(lambda _: refresh())
        only.on_value_change(lambda _: refresh())
        refresh()
        if usage.unresolved:
            with ui.expansion(f"{len(usage.unresolved)} use(s) without a pad", icon="warning") \
                    .classes("w-full text-orange-4"):
                for u in usage.unresolved:
                    ui.label(f"{u.node}  {u.role}  {u.function}").classes("text-xs")
        for n in usage.notes:
            ui.label(n).classes("text-xs text-grey-6")
