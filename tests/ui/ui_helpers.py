"""Helpers for the simulated UI tests (no browser). Elements are found by their `id=` prop, because the
product code sets ids but no NiceGUI markers."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from nicegui import ElementFilter, ui
from nicegui.testing.user_interaction import UserInteraction

REPO = Path(__file__).resolve().parents[2]
MAIN_FILE = Path(__file__).with_name("_app_main.py")


def page_module():
    """The page module of the running simulation (fresh for every simulation)."""
    return sys.modules["dt_explorer.ui.page"]


def _in_closed_dialog(el) -> bool:
    return any(isinstance(a, ui.dialog) and not a.value for a in el.ancestors())


def visible(user) -> list:
    """All elements that are visible: not hidden, not inside a closed dialog."""
    with user.client:
        return [e for e in ElementFilter(local_scope=False, only_visible=True) if not _in_closed_dialog(e)]


def by_id(user, elem_id: str) -> list:
    return [e for e in visible(user) if e.props.get("id") == elem_id]


def el(user, elem_id: str):
    """The single visible element with `id=elem_id` (AssertionError if none or several)."""
    found = by_id(user, elem_id)
    assert len(found) == 1, f"expected exactly one visible element with id={elem_id!r}, found {len(found)}"
    return found[0]


def click(user, elem_id: str) -> None:
    UserInteraction(user, {el(user, elem_id)}, None).click()


def texts_of(e) -> list[str]:
    """Every text a NiceGUI element carries (text, code content, label props, table rows, tree labels)."""
    out = []
    for attr in ("text", "content"):
        v = getattr(e, attr, None)
        if isinstance(v, str) and v:
            out.append(v)
    for key in ("label", "title", "caption"):
        v = e.props.get(key)
        if isinstance(v, str) and v:
            out.append(v)
    if isinstance(e, ui.table):
        out.append(json.dumps(e.rows, default=str))
    if isinstance(e, ui.tree):
        out.append(json.dumps(e.props.get("nodes", []), default=str))
    if isinstance(e, ui.log):
        out.append("\n".join(str(c.text) for c in e.default_slot.children if hasattr(c, "text")))
    return out


def page_text(user, scope_id: str | None = None) -> str:
    """All visible text of the page (or of the element with id `scope_id`, with its descendants)."""
    els = visible(user)
    if scope_id:
        root = el(user, scope_id)
        els = [e for e in els if e is root or root in set(e.ancestors())]
    return "\n".join(t for e in els for t in texts_of(e))


async def wait_until(pred, timeout: float = 30.0, step: float = 0.1, what: str = "condition"):
    """Poll `pred()` until it is truthy; returns its value. AssertionError after `timeout` seconds."""
    end = time.monotonic() + timeout
    while True:
        value = pred()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"timeout after {timeout:.0f} s waiting for {what}")
        await asyncio.sleep(step)


def select_value(user, elem_id: str, value) -> None:
    """Choose `value` in a ui.select / ui.input / ui.toggle (what a user does with a click or typing)."""
    e = el(user, elem_id)
    with user.client:
        e.set_value(value)


def open_tab(user, name: str) -> None:
    """Click the tab labelled `name`."""
    with user.client:
        tabs = [e for e in ElementFilter(kind=ui.tab, local_scope=False) if e.props.get("label") == name
                or e.props.get("name") == name]
    assert tabs, f"no tab {name!r}"
    UserInteraction(user, {tabs[0]}, None).click()


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def simulated_app(root):
    """A simulated Explorer app whose folder is `root`; yields the NiceGUI ``User``."""
    from nicegui.testing.user_simulation import user_simulation
    async with user_simulation(main_file=MAIN_FILE) as user:
        page_module().state.set_root(Path(root))
        yield user


def button_by_text(user, text: str):
    """The visible button whose label is `text` (several: the one with the highest element id, the newest)."""
    found = [e for e in visible(user) if isinstance(e, ui.button) and (e.text == text or e.props.get("label") == text)]
    assert found, f"no visible button {text!r}"
    return max(found, key=lambda e: e.id)


def click_button(user, text: str) -> None:
    UserInteraction(user, {button_by_text(user, text)}, None).click()
