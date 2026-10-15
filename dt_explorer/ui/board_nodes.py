"""Board nodes dialog (P8.7): LEDs, buttons, PWM LEDs, aliases and chosen entries.

Edits the one draft node ``{/}`` of class ``board`` (see ``core/board_nodes.py``). The dialog works on a
copy of the stored spec; Save writes it into the draft (an empty spec removes the node).
Ids: ``bn-dialog``, ``bn-kind``, ``bn-name``, ``bn-pad``, ``bn-active-low``, ``bn-pull``, ``bn-text``, ``bn-code``,
``bn-ctrl``, ``bn-channel``, ``bn-period``, ``bn-add``, ``bn-entry-<name>``, ``bn-del-<name>``,
``bn-alias-name``, ``bn-alias-target``, ``bn-alias-add``, ``bn-alias-<name>``, ``bn-alias-del-<name>``,
``bn-chosen-name``, ``bn-chosen-target``, ``bn-chosen-add``, ``bn-chosen-<name>``, ``bn-chosen-del-<name>``,
``bn-user-prop``, ``bn-user-ctrl``, ``bn-user-cells``, ``bn-user-add``, ``bn-user-<prop>-<n>``,
``bn-user-del-<prop>-<n>`` (the ``zephyr,user`` node: phandle-array properties such as ``io-channels``),
``bn-problems``, ``bn-save``.
"""
from __future__ import annotations

import copy
import re
from typing import Callable

from ..core import board_nodes as B

USER_PROP_SUGGESTIONS = ["io-channels", "pwms", "gpios", "dmas"]
CHOSEN_SUGGESTIONS = ["zephyr,console", "zephyr,shell-uart", "zephyr,uart-mcumgr", "zephyr,bt-uart", "zephyr,display"]


def sid(name: str) -> str:
    """Element id part of a name: Quasar's prop parser breaks on ',' and other punctuation."""
    return re.sub(r"[^\w-]", "_", name)


def _natural(pad: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pad)]


def pad_options(pad_map: dict, used: dict[str, list[str]], spec_pads: dict[str, str]) -> dict[str, str]:
    """pad -> text for the pad select: the GPIO pads in natural order, with who uses them."""
    out = {}
    for pad in sorted(pad_map, key=_natural):
        ctrl, pin = pad_map[pad]
        who = list(used.get(pad, [])) + ([f"{spec_pads[pad]} (this dialog)"] if pad in spec_pads else [])
        out[pad] = f"{pad}  ({ctrl} pin {pin})" + ("  - used by " + "; ".join(who) if who else "")
    return out


def controller_options(session) -> dict[str, str]:
    """label -> text for the ``zephyr,user`` controller select: nodes of the build with a label, then labels the draft adds."""
    out: dict[str, str] = {}
    for n in session.model.edt.nodes:
        if n.labels and n.path != "/":
            out.setdefault(n.labels[0], f"&{n.labels[0]}  ({n.path})" + ("" if n.status == "okay" else f"  [{n.status}]"))
    for d in session.draft.nodes:
        if d.label and d.label != B.BOARD_LABEL:
            out[d.label] = f"&{d.label}  (draft node, {d.cls})"
    return out


def parse_cells(text: str) -> tuple[int, ...]:
    """``"6"`` / ``"1 2 3"`` / ``"1, 2"`` -> cells (decimal or 0x..); ValueError for anything else."""
    parts = [t for t in re.split(r"[\s,]+", (text or "").strip()) if t]
    return tuple(int(t, 0) for t in parts)


def target_options(session, spec: B.BoardSpec) -> dict[str, str]:
    """path -> text for the alias / chosen target select: nodes of the build with a label, plus the new board nodes."""
    out = {e.path: f"new: {e.name}  ({e.path})" for e in spec.entries}
    for n in session.model.edt.nodes:
        if n.labels and n.path != "/":
            out.setdefault(n.path, f"&{n.labels[0]}  ({n.path})")
    return out


class BoardContext:
    """What the dialog needs to know about a build (no NiceGUI; the tests use it too)."""

    def __init__(self, session):
        self.session = session
        self.pad_map = B.gpio_pad_map(session.model, session.device)
        self.pwm = B.pwm_controllers(session.model, session.draft)      # includes the PWM nodes the draft enables
        self.pwm_by_label = {p["label"]: p for p in self.pwm}
        self.pwm_cells = {p["label"]: len(p["cells"]) for p in self.pwm}
        self.existing_aliases = set(session.model.edt._dt.alias2node)

    def used(self) -> dict[str, list[str]]:
        """pad -> users in the build and in the other draft nodes."""
        used = {p: list(v) for p, v in self.session.used_by().items()}
        for pad, names in self.session.draft_pads().items():
            used.setdefault(pad, []).extend(names)
        return used

    def stored_spec(self) -> B.BoardSpec:
        return B.read_spec(self.session.draft, self.pad_map)

    def problems(self, spec: B.BoardSpec) -> list[B.Problem]:
        return B.check_spec(spec, model=self.session.model, pad_map=self.pad_map, other_users=self.used(),
                            existing_aliases=self.existing_aliases, pwm_ctrls=self.pwm_by_label,
                            draft=self.session.draft)

    def save(self, spec: B.BoardSpec) -> None:
        """Write the spec into the draft and the draft file."""
        node = B.spec_to_node(spec, self.pad_map, self.pwm_cells)
        if node is None:
            self.session.remove(B.BOARD_LABEL)
        else:
            self.session.upsert(node)
        self.session.save()


def entry_text(e: B.Entry) -> str:
    if e.kind == B.PWM_LEDS:
        return f"{e.ctrl} channel {e.channel}, period {e.period_ns / 1e6:g} ms"
    flags = ["active low"] if e.active_low else []
    if e.pull != "none":
        flags.append(e.pull)
    if e.code is not None:
        flags.append(f"code {e.code}")
    return f"{e.pad}" + (f" ({', '.join(flags)})" if flags else "")


def open_board_nodes(session, on_saved: Callable[[], None]) -> None:
    from nicegui import ui

    ctx = BoardContext(session)
    spec = copy.deepcopy(ctx.stored_spec())
    st = {"busy": False}

    with ui.dialog() as dlg, ui.card().style("min-width: 680px; max-width: 800px; max-height: 92vh; overflow-y: auto") \
            .props("id=bn-dialog"):
        ui.label("Board nodes").classes("text-lg font-bold")
        ui.label("LEDs, buttons and PWM LEDs as nodes under the root, plus alias and chosen entries. "
                 "They are written into the draft overlay as /user_leds, /user_keys, /user_pwm_leds, "
                 "/aliases, /chosen and /zephyr,user.").classes("text-xs text-grey-5")
        problems_lbl = ui.label().classes("text-sm").style("white-space: pre-wrap").props("id=bn-problems")

        ui.label("Entries").classes("font-bold")
        entries_box = ui.column().classes("w-full gap-1").props("id=bn-entries")
        with ui.card().classes("w-full").props("flat bordered"):
            kinds = {B.LEDS: B.KIND_TITLES[B.LEDS], B.KEYS: B.KIND_TITLES[B.KEYS]}
            if ctx.pwm:
                kinds[B.PWM_LEDS] = B.KIND_TITLES[B.PWM_LEDS]
            kind_sel = ui.toggle(kinds, value=B.LEDS).props("id=bn-kind")
            name_in = ui.input("Name (node name and label, e.g. led_user)").props("dense outlined id=bn-name") \
                .classes("w-full")
            gpio_box = ui.column().classes("w-full gap-1")
            with gpio_box:
                pad_sel = ui.select({}, label="GPIO pad", clearable=True, with_input=True) \
                    .props("dense outlined options-dense id=bn-pad").classes("w-full")
                with ui.row().classes("items-center gap-4"):
                    low_sw = ui.switch("active low", value=False).props("dense id=bn-active-low")
                    pull_sel = ui.select(list(B.PULLS), value="none", label="pull") \
                        .props("dense outlined options-dense id=bn-pull")
                    code_in = ui.input("zephyr,code (optional)").props("dense outlined id=bn-code")
            pwm_box = ui.column().classes("w-full gap-1")
            with pwm_box:
                ctrl_sel = ui.select({p["label"]: f"&{p['label']}  ({p['path']})" for p in ctx.pwm},
                                     value=ctx.pwm[0]["label"] if ctx.pwm else None, label="PWM controller") \
                    .props("dense outlined options-dense id=bn-ctrl").classes("w-full")
                with ui.row().classes("items-center gap-4"):
                    chan_in = ui.number("channel", value=0, min=0, precision=0).props("dense outlined id=bn-channel")
                    period_in = ui.number("period (ms)", value=20, min=0, format="%g").props("dense outlined id=bn-period")
            text_in = ui.input("label text (optional, e.g. User LED)").props("dense outlined id=bn-text").classes("w-full")
            add_btn = ui.button("Add", icon="add").props("no-caps id=bn-add")

        ui.label("Aliases").classes("font-bold")
        alias_box = ui.column().classes("w-full gap-1").props("id=bn-aliases")
        with ui.row().classes("items-start gap-2 w-full no-wrap"):
            alias_name = ui.input("alias name (e.g. led1)").props("dense outlined id=bn-alias-name")
            alias_target = ui.select({}, label="points to", with_input=True).props("dense outlined options-dense id=bn-alias-target") \
                .classes("grow")
            alias_btn = ui.button("Set", icon="add").props("no-caps id=bn-alias-add")

        ui.label("zephyr,user").classes("font-bold")
        ui.label("The /zephyr,user node: phandle-array properties for code that reads them with DT_PATH(zephyr_user), "
                 "e.g. io-channels = <&adc1 6> for the adc_dt sample.").classes("text-xs text-grey-5")
        user_box = ui.column().classes("w-full gap-1").props("id=bn-user")
        with ui.row().classes("items-start gap-2 w-full no-wrap"):
            user_prop = ui.select(USER_PROP_SUGGESTIONS, label="property", new_value_mode="add-unique", with_input=True)                 .props("dense outlined options-dense id=bn-user-prop")
            user_ctrl = ui.select({}, label="controller", with_input=True)                 .props("dense outlined options-dense id=bn-user-ctrl").classes("grow")
            user_cells = ui.input("cells (e.g. 6)").props("dense outlined id=bn-user-cells")
            user_btn = ui.button("Add", icon="add").props("no-caps id=bn-user-add")

        ui.label("Chosen").classes("font-bold")
        chosen_box = ui.column().classes("w-full gap-1").props("id=bn-chosen")
        with ui.row().classes("items-start gap-2 w-full no-wrap"):
            chosen_name = ui.select(CHOSEN_SUGGESTIONS, label="chosen name", new_value_mode="add-unique", with_input=True) \
                .props("dense outlined options-dense id=bn-chosen-name")
            chosen_target = ui.select({}, label="points to", with_input=True).props("dense outlined options-dense id=bn-chosen-target") \
                .classes("grow")
            chosen_btn = ui.button("Set", icon="add").props("no-caps id=bn-chosen-add")

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps id=bn-cancel")
            save_btn = ui.button("Save", icon="save").props("no-caps id=bn-save")

    # ---- redraw -----------------------------------------------------------------------------------------
    def refresh() -> None:
        entries_box.clear()
        with entries_box:
            if not spec.entries:
                ui.label("No LED, button or PWM LED yet.").classes("text-xs text-grey-5")
            for e in spec.entries:
                with ui.row().classes("items-center gap-2 w-full").props(f"id=bn-entry-{e.name}"):
                    ui.label(e.name).classes("font-mono")
                    ui.label(B.KIND_TITLES[e.kind]).classes("text-xs text-grey-4")
                    ui.label(entry_text(e)).classes("text-xs font-mono")
                    if e.text:
                        ui.label(f'"{e.text}"').classes("text-xs text-grey-5")
                    ui.space()
                    ui.button(icon="delete", on_click=lambda _e, n=e.name: delete_entry(n)) \
                        .props(f"flat dense round id=bn-del-{e.name}").props("title=Remove")
        for box, items, prefix, drop in ((alias_box, spec.aliases, "alias", delete_alias),
                                         (chosen_box, spec.chosen, "chosen", delete_chosen)):
            box.clear()
            with box:
                if not items:
                    ui.label(f"No {prefix} entry yet.").classes("text-xs text-grey-5")
                for name, path in items.items():
                    with ui.row().classes("items-center gap-2 w-full").props(f"id=bn-{prefix}-{sid(name)}"):
                        ui.label(name).classes("font-mono")
                        ui.label("->").classes("text-xs")
                        ui.label(path).classes("text-xs font-mono")
                        ui.space()
                        ui.button(icon="delete", on_click=lambda _e, n=name, d=drop: d(n)) \
                            .props(f"flat dense round id=bn-{prefix}-del-{sid(name)}").props("title=Remove")
        user_box.clear()
        with user_box:
            if not spec.user:
                ui.label("No zephyr,user property yet.").classes("text-xs text-grey-5")
            for prop, entries in spec.user.items():
                for i, (ctrl, cells) in enumerate(entries):
                    with ui.row().classes("items-center gap-2 w-full").props(f"id=bn-user-{sid(prop)}-{i}"):
                        ui.label(prop).classes("font-mono")
                        ui.label(f"<&{ctrl} {' '.join(str(c) for c in cells)}>".replace(" >", ">")).classes("text-xs font-mono")
                        ui.space()
                        ui.button(icon="delete", on_click=lambda _e, p=prop, k=i: delete_user(p, k))                             .props(f"flat dense round id=bn-user-del-{sid(prop)}-{i}").props("title=Remove")
        user_ctrl.options = controller_options(session)
        user_ctrl.update()
        opts = target_options(session, spec)
        for sel in (alias_target, chosen_target):
            sel.options = opts
            sel.update()
        spec_pads = {e.pad: e.name for e in spec.entries if e.pad}
        pad_sel.options = pad_options(ctx.pad_map, ctx.used(), spec_pads)
        pad_sel.update()
        probs = ctx.problems(spec)
        lines = [str(p) for p in probs]
        if not ctx.pad_map:
            lines.insert(0, "error: the GPIO controllers cannot be matched to pads of the device; LEDs and buttons need that.")
        problems_lbl.text = "\n".join(lines)
        problems_lbl.classes(replace="text-sm " + ("text-red-4" if B.has_errors(probs) else "text-orange-4"))
        save_btn.set_enabled(not B.has_errors(probs))

    def on_kind(_e=None) -> None:
        pwm = kind_sel.value == B.PWM_LEDS
        gpio_box.set_visibility(not pwm)
        pwm_box.set_visibility(pwm)
        pull_sel.set_visibility(kind_sel.value == B.KEYS)
        code_in.set_visibility(kind_sel.value == B.KEYS)
        if kind_sel.value == B.KEYS and pull_sel.value == "none":
            pull_sel.value, low_sw.value = "pull-up", True

    kind_sel.on_value_change(on_kind)

    # ---- actions ----------------------------------------------------------------------------------------
    def add_entry() -> None:
        kind = kind_sel.value
        e = B.Entry(kind=kind, name=(name_in.value or "").strip(), text=(text_in.value or "").strip())
        try:
            if kind == B.PWM_LEDS:
                e.ctrl, e.channel = ctrl_sel.value, int(chan_in.value or 0)
                e.period_ns = int(round(float(period_in.value or 0) * 1e6))
            else:
                e.pad, e.active_low = pad_sel.value, bool(low_sw.value)
                if kind == B.KEYS:
                    e.pull = pull_sel.value or "none"
                    e.code = int(str(code_in.value).strip(), 0) if str(code_in.value or "").strip() else None
        except ValueError:
            ui.notify("zephyr,code: not a number", type="negative")
            return
        trial = copy.deepcopy(spec)
        trial.entries.append(e)
        errs = [p for p in ctx.problems(trial) if p.severity == "error"]
        if errs:
            problems_lbl.text = "\n".join(str(p) for p in errs)
            problems_lbl.classes(replace="text-sm text-red-4")
            return
        spec.entries.append(e)
        name_in.value, text_in.value, code_in.value, pad_sel.value = "", "", "", None
        refresh()

    def delete_entry(name: str) -> None:
        spec.entries = [e for e in spec.entries if e.name != name]
        dead = {p for p in list(spec.aliases.values()) + list(spec.chosen.values())
                if p.startswith("/user_") and p.rsplit("/", 1)[-1] == name}
        spec.aliases = {k: v for k, v in spec.aliases.items() if v not in dead}      # dangling targets go with it
        spec.chosen = {k: v for k, v in spec.chosen.items() if v not in dead}
        refresh()

    def set_alias() -> None:
        name, target = (alias_name.value or "").strip(), alias_target.value
        if not name or not target:
            problems_lbl.text = "error: alias name and target are both needed"
            problems_lbl.classes(replace="text-sm text-red-4")
            return
        spec.aliases[name] = target
        alias_name.value, alias_target.value = "", None
        refresh()

    def set_chosen() -> None:
        name, target = (chosen_name.value or "").strip(), chosen_target.value
        if not name or not target:
            problems_lbl.text = "error: chosen name and target are both needed"
            problems_lbl.classes(replace="text-sm text-red-4")
            return
        spec.chosen[name] = target
        chosen_name.value, chosen_target.value = None, None
        refresh()

    def add_user() -> None:
        prop, ctrl = (user_prop.value or "").strip(), user_ctrl.value
        if not prop or not ctrl:
            problems_lbl.text = "error: zephyr,user needs a property name and a controller"
            problems_lbl.classes(replace="text-sm text-red-4")
            return
        try:
            cells = parse_cells(user_cells.value)
        except ValueError:
            problems_lbl.text = "error: zephyr,user cells must be numbers"
            problems_lbl.classes(replace="text-sm text-red-4")
            return
        spec.user.setdefault(prop, []).append((ctrl, cells))
        user_ctrl.value, user_cells.value = None, ""
        refresh()

    def delete_user(prop: str, k: int) -> None:
        entries = spec.user.get(prop, [])
        if 0 <= k < len(entries):
            del entries[k]
        if not entries:
            spec.user.pop(prop, None)
        refresh()

    def delete_alias(name: str) -> None:
        spec.aliases.pop(name, None)
        refresh()

    def delete_chosen(name: str) -> None:
        spec.chosen.pop(name, None)
        refresh()

    def save() -> None:
        if B.has_errors(ctx.problems(spec)):
            return
        ctx.save(spec)
        dlg.close()
        on_saved()

    add_btn.on_click(add_entry)
    alias_btn.on_click(set_alias)
    chosen_btn.on_click(set_chosen)
    user_btn.on_click(add_user)
    save_btn.on_click(save)
    on_kind()
    refresh()
    dlg.open()
