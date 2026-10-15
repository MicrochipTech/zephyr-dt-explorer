r"""Simulated UI test (no browser): "Port to board..." lab1 (SAME54 Xplained Pro) -> SAM E54 Xplained Pro.

Runs the report only. "Apply to app..." is neither pressed nor does the test write anything: the target
build is a cmake-only build in %TEMP%\dtx_lab1_e54 (conftest.lab1_e54).
"""
from __future__ import annotations

import pytest
from ui_helpers import by_id, click, el, page_text, select_value, simulated_app, visible, wait_until

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"


def test_port_lab1_to_e54_report(lab1, lab1_e54, ui_run, tmp_path):
    root, build = lab1
    boards = root / "boards"
    before = sorted((f.name, f.stat().st_mtime_ns) for f in boards.iterdir())

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "port-open")
            assert el(user, "port-source").text == f"Source build: {build}  [same54_xpro/same54p20a]"
            assert not by_id(user, "port-report")
            select_value(user, "port-other", str(lab1_e54))
            select_value(user, "port-include-board", True)          # lab1 is all source-board configuration (P8.18b)
            click(user, "port-run")
            await wait_until(lambda: by_id(user, "port-report"), timeout=180, what="the port report")

            status = el(user, "port-status").text
            assert status.startswith("done: ") and "node(s) carried over" in status, status
            assert "0 error(s)" in status
            # the overlay and the .conf are shown
            assert "Port of build_same54_xpro (same54_xpro/same54p20a) to sam_e54_xpro/atsame54p20a" \
                in el(user, "port-overlay").content
            assert "# Kconfig lines for sam_e54_xpro/atsame54p20a" in el(user, "port-conf").content
            # report sections: numbered titles "1. Nodes ..." to "5.", then "5b." ...; "6." is not shown, all expanded
            secs = sorted((e for e in visible(user) if str(e.props.get("id", "")).startswith("port-section-")),
                          key=lambda e: int(e.props["id"].rsplit("-", 1)[1]))
            titles = [e.props["label"] for e in secs]
            assert len(titles) >= 4 and titles[0].startswith("1. Nodes")
            assert [t.split(".")[0] for t in titles][:5] == ["1", "2", "3", "4", "5"]
            assert not any(t.startswith("6.") for t in titles)
            assert all(e.value for e in secs)
            assert by_id(user, "port-findings")
            # the Apply controls exist, but nothing was applied
            assert by_id(user, "port-apply")
            assert not by_id(user, "port-apply-go") and not by_id(user, "port-apply-result")
            assert "Fast check on the target build" in page_text(user, "port-report")

    ui_run(scenario)
    after = sorted((f.name, f.stat().st_mtime_ns) for f in boards.iterdir())
    assert after == before                                              # no board file written into the app


def test_port_include_board_configuration_checkbox(lab1, lab1_e54, ui_run):
    """P8.18b: off (default) leaves the source board's own nodes to the target board; on carries them over."""
    root, _build = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "port-open")
            cb = el(user, "port-include-board")
            assert cb.text == "Include source board configuration" and cb.value is False
            select_value(user, "port-other", str(lab1_e54))
            click(user, "port-run")
            await wait_until(lambda: by_id(user, "port-report"), timeout=180, what="the port report")
            off_status = el(user, "port-status").text
            off_report = page_text(user, "port-report")
            assert "left to the target board" in off_status, off_status
            assert "not ported (the target board's own applies)" in off_report

            select_value(user, "port-include-board", True)
            click(user, "port-run")
            await wait_until(lambda: "left to the target board" not in el(user, "port-status").text
                             and el(user, "port-status").text.startswith("done: "), timeout=180, what="the second port")
            on_status = el(user, "port-status").text
            assert "not ported (the target board's own applies)" not in page_text(user, "port-report")
            assert off_status.split(" node(s) carried")[0] != on_status.split(" node(s) carried")[0]

    ui_run(scenario)
