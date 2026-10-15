r"""Simulated UI test (no browser): the Changes tab on a COPY of _soltest/apps/lab1_leer, turned into a git repo
under %TEMP%. The real app folders are never touched; changes_tab.app_dir_of is pointed at the copy.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from ui_helpers import by_id, click, el, open_tab, page_text, simulated_app, wait_until

pytestmark = [pytest.mark.ui, pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")]

BUILD = "build_same54_xpro"
MARKED = "\n/* dt_explorer draft begin 2026-10-15 */\n&sercom1 { status = \"okay\"; };\n/* dt_explorer end */\n"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo_app(lab1_leer_copy, monkeypatch):
    """The lab1_leer copy as a committed git repo; returns the app folder."""
    app, _build = lab1_leer_copy
    from dt_explorer.ui import changes_tab
    (app / ".gitignore").write_text(f"{BUILD}/\n", encoding="utf-8")
    git(app, "init", "-q", "-b", "main")
    git(app, "add", "-A")
    git(app, "commit", "-q", "-m", "init")
    monkeypatch.setattr(changes_tab, "app_dir_of", lambda model: app)
    return app


def _edit(app):
    own = app / "boards" / "same54_xpro.overlay"
    own.write_text(own.read_text(encoding="utf-8") + MARKED, encoding="utf-8")        # modified + marked block
    (app / "dt_explorer.overlay").write_text("&sercom1 {};\n", encoding="utf-8")        # untracked, tool file
    (app / "prj.conf").write_text((app / "prj.conf").read_text(encoding="utf-8") + "CONFIG_I2C=y\n", encoding="utf-8")


def test_changes_tab_lists_diffs_and_refreshes_on_demand(repo_app, ui_run):
    app = repo_app
    _edit(app)

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&tab=changes")
            await wait_until(lambda: by_id(user, "changes-info"), timeout=15, what="the Changes tab")
            head = git(app, "rev-parse", "--short", "HEAD").strip()
            info = el(user, "changes-info").text
            assert "branch main" in info and f"HEAD {head}" in info and info.startswith("read ")
            assert el(user, "changes-count").text == "3 changed files"
            assert el(user, "changes-badge").text == "3"
            files = {e.props["id"]: e.text for e in by_id_prefix(user, "changes-file-")}
            names = sorted(t.strip() for t in files.values())
            assert names == ["boards/same54_xpro.overlay", "dt_explorer.overlay", "prj.conf"]
            dtx_rows = {i.rsplit("-", 1)[1] for i in (e.props["id"] for e in by_id_prefix(user, "changes-dtx-"))}
            dtx_names = sorted(files[f"changes-file-{i}"].strip() for i in dtx_rows)
            assert dtx_names == ["boards/same54_xpro.overlay", "dt_explorer.overlay"]      # prj.conf has no badge

            # diff of the modified overlay: the marked block as added lines
            idx = next(i for i, t in ((k.rsplit("-", 1)[1], v) for k, v in files.items())
                       if t.strip() == "boards/same54_xpro.overlay")
            click(user, f"changes-file-{idx}")
            body = el(user, "changes-diff").content
            assert 'class="add"' in body and "dt_explorer draft begin" in body and "sercom1" in body
            assert 'class="hunk"' in body
            assert el(user, "changes-diff-title").text == "boards/same54_xpro.overlay"

            # untracked file: the whole file as added
            idx = next(i for i, t in ((k.rsplit("-", 1)[1], v) for k, v in files.items())
                       if t.strip() == "dt_explorer.overlay")
            click(user, f"changes-file-{idx}")
            body = el(user, "changes-diff").content
            assert "new file (untracked)" in body and "&amp;sercom1 {};" in body and 'class="del"' not in body

            # a change after the read shows up only after Refresh (no polling)
            (app / "late.txt").write_text("late\n", encoding="utf-8")
            await user.should_not_see("late.txt")
            click(user, "changes-refresh")
            assert el(user, "changes-count").text == "4 changed files"
            assert el(user, "changes-badge").text == "4"
            assert "late.txt" in page_text(user)

    ui_run(scenario)


def by_id_prefix(user, prefix):
    from ui_helpers import visible
    return [e for e in visible(user) if str(e.props.get("id", "")).startswith(prefix)]


def test_clean_repo_says_so(repo_app, ui_run):
    async def scenario():
        async with simulated_app(repo_app) as user:
            await user.open(f"/?build={BUILD}&tab=changes")
            await wait_until(lambda: by_id(user, "changes-clean"), timeout=15, what="the clean message")
            assert "No changes" in el(user, "changes-clean").text
            assert el(user, "changes-badge").text == "0"

    ui_run(scenario)


def test_tab_click_reads_git(repo_app, ui_run):
    _edit(repo_app)

    async def scenario():
        async with simulated_app(repo_app) as user:
            await user.open(f"/?build={BUILD}")
            assert not by_id(user, "changes-info") and not by_id(user, "changes-badge")      # nothing read yet
            open_tab(user, "Changes")
            await wait_until(lambda: by_id(user, "changes-count"), timeout=15, what="the list")
            assert el(user, "changes-badge").text == "3"

    ui_run(scenario)


def test_no_repository_message(lab1_leer_copy, monkeypatch, tmp_path, ui_run):
    app, _ = lab1_leer_copy
    plain = tmp_path / "plain_app"
    plain.mkdir()
    from dt_explorer.ui import changes_tab
    monkeypatch.setattr(changes_tab, "app_dir_of", lambda model: plain)

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&tab=changes")
            await wait_until(lambda: by_id(user, "changes-message"), timeout=15, what="the message")
            assert "not inside a git repository" in el(user, "changes-message").text
            assert not by_id(user, "changes-badge")
            assert by_id(user, "changes-refresh")                       # the rest of the page still works

    ui_run(scenario)


def test_git_missing_message(repo_app, monkeypatch, tmp_path, ui_run):
    empty = tmp_path / "nogit"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    async def scenario():
        async with simulated_app(repo_app) as user:
            await user.open(f"/?build={BUILD}&tab=changes")
            await wait_until(lambda: by_id(user, "changes-message"), timeout=15, what="the message")
            assert "git was not found on PATH" in el(user, "changes-message").text
            assert not by_id(user, "changes-badge")

    ui_run(scenario)
