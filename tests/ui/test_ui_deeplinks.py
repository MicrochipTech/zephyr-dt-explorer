"""Simulated UI tests (no browser): deep links of the Explorer page on lab1.

lab1 = apps/lab1/build_same54_xpro (SAME54 Xplained Pro). Read only; drafts are redirected (conftest.lab1).
"""
from __future__ import annotations

import logging

import pytest
from nicegui import ui
from ui_helpers import by_id, el, page_text, simulated_app, visible, wait_until

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"


def test_node_deep_link(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&node=/soc/sercom@41012000&macros=1")
            text = page_text(user)
            assert "&sercom2" in text and "/soc/sercom@41012000" in text
            assert "atmel,sam0-uart" in text
            assert "Node identifier: DT_N_S_soc_S_sercom_41012000" in text       # macros=1 expands the section
            assert "Used by:" in text or "Depends on:" in text
            # enabled node covered by a recipe: "Edit pins...", no "Configure..."
            assert len(by_id(user, "cfg-edit")) == 1 and not by_id(user, "cfg-open")
            assert el(user, "draft-badge").text == "0"
            tables = [e for e in visible(user) if isinstance(e, ui.table)]
            names = {r["name"] for t in tables for r in t.rows if "name" in r}
            assert {"compatible", "status", "current-speed"} <= names
    ui_run(scenario)


def test_unknown_node_shows_overview(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&node=/no/such/node")
            assert "Overview" in page_text(user)
            assert not by_id(user, "cfg-edit")
    ui_run(scenario)


def test_pins_tab(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&tab=pins")
            tables = [e for e in visible(user) if isinstance(e, ui.table) and e.rows and "pad" in e.rows[0]]
            assert len(tables) == 1
            rows = {r["pad"]: r for r in tables[0].rows}
            assert len(rows) >= 100                                              # TQFP128 package
            assert rows["PB24"]["state"] != "free" and "/soc/sercom@41012000" in rows["PB24"]["users_text"]
            assert rows["PA00"]["state"] == "free"
            assert "PORT.P0" in rows["PA00"]["functions_text"]
    ui_run(scenario)


def test_package_tab(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&tab=package")
            svg = [e.content for e in visible(user) if isinstance(e, ui.html) and "<svg" in e.content]
            assert len(svg) == 1
            assert svg[0].count('class="pkgpin') >= 100                          # one rect/circle per pin
            assert "PB24" in svg[0] and "PA00" in svg[0]
            assert "package" in page_text(user).lower()
    ui_run(scenario)


def test_board_tab(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&tab=board")
            svg = [e.content for e in visible(user) if isinstance(e, ui.html) and "data-connector=" in e.content]
            connectors = sorted(s.split('data-connector="')[1].split('"')[0] for s in svg)
            assert connectors[:3] == ["EXT1", "EXT2", "EXT3"]
            assert all(s.count("bpin") > 10 for s in svg)
    ui_run(scenario)


def test_draft_tab_empty(lab1, ui_run):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&tab=draft")
            assert "The draft is empty" in page_text(user)
            assert el(user, "draft-badge").text == "0"
            assert not by_id(user, "draft-snippet")
    ui_run(scenario)


def test_log_tab(lab1, ui_run, tmp_path, monkeypatch):
    from dt_explorer.core import log as dtlog
    root, _ = lab1
    monkeypatch.setenv("DTX_LOG_DIR", str(tmp_path / "logs"))
    file = dtlog.setup()
    try:
        async def scenario():
            async with simulated_app(root) as user:
                await user.open(f"/?build={BUILD}&tab=log")
                assert el(user, "log-file").text == f"File: {file}"
                assert el(user, "log-level").value == "INFO"
                assert el(user, "log-box") is not None and el(user, "log-copy") is not None
                dtlog.get_logger("ui_test").info("hello from the UI test")
                box = el(user, "log-box")
                await wait_until(lambda: any("hello from the UI test" in str(getattr(c, "text", ""))
                                             for c in box.default_slot.children),
                                 timeout=5, what="the log line in the box")
        ui_run(scenario)
        assert "hello from the UI test" in file.read_text(encoding="utf-8")
    finally:
        lg = dtlog.get_logger()
        for h in list(lg.handlers):
            lg.removeHandler(h)
            h.close()
        lg.setLevel(logging.NOTSET)
        dtlog.enabled = False
        dtlog._file_handler = None
        dtlog._ring = None


def test_include_tab(lab1, ui_run):
    root, _ = lab1

    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n.get("children", []))

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&tab=include")
            trees = [e for e in visible(user) if isinstance(e, ui.tree)
                     and e.props.get("nodes") and "rel" in e.props["nodes"][0]]
            assert len(trees) == 1
            files = list(walk(trees[0].props["nodes"]))
            labels = [n["label"] for n in files]
            assert "zephyr.dts" in " ".join(labels) or any(l.endswith(".dts") for l in labels)
            assert any("same54_xpro" in n["rel"].replace("\\", "/") for n in files)     # the board's own files
            assert any(n["count"] for n in files)                                  # files that contribute entries
    ui_run(scenario)
