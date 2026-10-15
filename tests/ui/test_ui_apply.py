r"""Simulated UI test (no browser): Draft tab "Apply to app" on a COPY of _soltest/apps/lab1_leer.

The copy's CMakeCache still names the original app, so draft_tab.apply_app_dir is redirected to the copy.
The real app folders are never written.
"""
from __future__ import annotations

import pytest
from ui_helpers import by_id, click, el, page_text, select_value, simulated_app, wait_until

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"


async def _make_draft(user):
    await user.open(f"/?build={BUILD}&node=/soc/sercom@40003400")
    click(user, "cfg-open")
    select_value(user, "cfg-class", "i2c")
    select_value(user, "cfg-role-SDA", "PC22")
    select_value(user, "cfg-role-SCL", "PC23")
    select_value(user, "cfg-prop-clock-frequency", "100000")
    click(user, "cfg-save")
    await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")


@pytest.fixture
def leer(lab1_leer_copy, monkeypatch):
    app, build = lab1_leer_copy
    from dt_explorer.ui import draft_tab
    monkeypatch.setattr(draft_tab, "apply_app_dir", lambda b: app)
    return app


def test_apply_existing_own_overlay_shows_diff_and_refuses(leer, ui_run):
    app = leer
    own = app / "boards" / "same54_xpro.overlay"
    before = own.read_text(encoding="utf-8")

    async def scenario():
        async with simulated_app(app) as user:
            await _make_draft(user)
            click(user, "draft-apply")
            plan = page_text(user, "draft-apply-box")
            assert "EXISTS: " in plan and "same54_xpro.overlay" in plan
            assert by_id(user, "draft-apply-diff-overlay") and by_id(user, "draft-apply-replace-overlay")
            assert "-> draft" in plan
            click(user, "draft-apply-confirm")                       # box not ticked: overlay is refused
            result = el(user, "draft-apply-result").text
            assert "refused (exists, not overwritten)" in result and "same54_xpro.overlay" in result

    ui_run(scenario)
    assert own.read_text(encoding="utf-8") == before


def test_apply_new_file_then_second_apply_is_identical(leer, ui_run):
    app = leer
    own = app / "boards" / "same54_xpro.overlay"
    own.unlink()                                                     # an app without its own board overlay
    (app / "boards" / "same54_xpro.conf").unlink(missing_ok=True)

    async def scenario():
        async with simulated_app(app) as user:
            await _make_draft(user)
            click(user, "draft-apply")
            assert "NEW: " in page_text(user, "draft-apply-overlay")
            assert not by_id(user, "draft-apply-diff-overlay")
            click(user, "draft-apply-confirm")
            assert "written:" in el(user, "draft-apply-result").text
            # a second apply: the file exists with the same content -> nothing to write
            click(user, "draft-apply")
            assert "IDENTICAL: " in page_text(user, "draft-apply-overlay")

    ui_run(scenario)
    text = own.read_text(encoding="utf-8")
    assert "&sercom1 {" in text and "PC22C_SERCOM1_PAD0" in text
    conf = (app / "boards" / "same54_xpro.conf").read_text(encoding="utf-8")
    assert "CONFIG_I2C=y" in conf
