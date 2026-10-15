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


def test_apply_existing_own_overlay_appends_by_default(leer, ui_run):
    app = leer
    own = app / "boards" / "same54_xpro.overlay"
    before = own.read_text(encoding="utf-8")

    async def scenario():
        async with simulated_app(app) as user:
            await _make_draft(user)
            click(user, "draft-apply")
            plan = page_text(user, "draft-apply-box")
            assert "EXISTS: " in plan and "same54_xpro.overlay" in plan and "Default: APPEND" in plan
            assert by_id(user, "draft-apply-diff-overlay") and by_id(user, "draft-apply-replace-overlay")
            assert by_id(user, "draft-apply-append-hint-overlay") and by_id(user, "draft-apply-diff-replace-overlay")
            assert not el(user, "draft-apply-replace-overlay").value          # replace stays behind the tick
            assert "dt_explorer draft begin" in el(user, "draft-apply-diff-overlay").content
            click(user, "draft-apply-confirm")                       # box not ticked: the block is appended
            result = el(user, "draft-apply-result").text
            assert "appended:" in result and "same54_xpro.overlay" in result and "overwritten" not in result
            click(user, "draft-apply")                               # the same draft again: block up to date
            assert "already up to date" in page_text(user, "draft-apply-overlay")

    ui_run(scenario)
    text = own.read_text(encoding="utf-8")
    assert text.startswith(before)                                   # the app's own content is untouched
    assert text.count("dt_explorer draft begin") == 1 and "&sercom1 {" in text and text.rstrip().endswith("/* dt_explorer end */")


def test_apply_replace_tick_overwrites(leer, ui_run):
    app = leer
    own = app / "boards" / "same54_xpro.overlay"

    async def scenario():
        async with simulated_app(app) as user:
            await _make_draft(user)
            click(user, "draft-apply")
            el(user, "draft-apply-replace-overlay").value = True
            click(user, "draft-apply-confirm")
            assert "overwritten:" in el(user, "draft-apply-result").text

    ui_run(scenario)
    text = own.read_text(encoding="utf-8")
    # replaced, not appended: the app's own content is gone, the draft is one marked block (P8.19b item 3)
    assert text.startswith("/* dt_explorer draft begin") and text.count("dt_explorer end") == 1 and "&sercom1 {" in text


def test_apply_new_file_then_second_apply_is_up_to_date(leer, ui_run):
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
            result = el(user, "draft-apply-result").text
            assert "written:" in result
            # P8.19b item 4: a build folder configured before the files existed needs a pristine build
            assert "west build -p always -b" in result and "configured before these files existed" in result
            # P8.19b item 3: the new file carries the marked block, so a second apply knows it
            assert "dt_explorer draft begin" in own.read_text(encoding="utf-8")
            click(user, "draft-apply")
            assert "already up to date" in page_text(user, "draft-apply-overlay")

    ui_run(scenario)
    text = own.read_text(encoding="utf-8")
    assert "&sercom1 {" in text and "PC22C_SERCOM1_PAD0" in text
    conf = (app / "boards" / "same54_xpro.conf").read_text(encoding="utf-8")
    assert "CONFIG_I2C=y" in conf


def test_port_dialog_offers_the_same_append_option(lab1, lab1_e54, ui_run, tmp_path, monkeypatch):
    r"""Port dialog "Apply to app...": an existing target-board overlay in a COPY of the lab1 app is appended to."""
    import shutil
    from dt_explorer.core import apply as apply_mod
    root, build = lab1
    app = tmp_path / "lab1_app"
    shutil.copytree(root, app, ignore=shutil.ignore_patterns("build*"))
    monkeypatch.setattr(apply_mod, "app_dir_of", lambda b: app)      # the real app folder is never written
    own = app / "boards" / "sam_e54_xpro.overlay"
    own.parent.mkdir(exist_ok=True)
    own.write_text("/ { app_own { status = \"okay\"; }; };" + chr(10), encoding="utf-8")
    before = own.read_text(encoding="utf-8")

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "port-open")
            select_value(user, "port-other", str(lab1_e54))
            click(user, "port-run")
            await wait_until(lambda: by_id(user, "port-report"), timeout=180, what="the port report")
            click(user, "port-apply")
            box = page_text(user, "port-apply-box")
            assert "EXISTS: " in box and "sam_e54_xpro.overlay" in box and "Default: APPEND" in box
            assert by_id(user, "port-apply-diff-overlay") and by_id(user, "port-apply-replace-overlay")
            assert by_id(user, "port-apply-diff-replace-overlay")
            assert not el(user, "port-apply-replace-overlay").value
            click(user, "port-apply-go")
            result = el(user, "port-apply-result").text
            assert "appended:" in result and "west build -p always -b" in result

    ui_run(scenario)
    text = own.read_text(encoding="utf-8")
    assert text.startswith(before) and "dt_explorer port begin" in text and text.count("dt_explorer end") == 1
