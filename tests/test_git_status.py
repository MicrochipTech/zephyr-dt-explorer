"""core/git_status.py on temporary git repositories (no real repository is touched)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dt_explorer.core import git_status as gs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

MARKED_OVL = "/* dt_explorer draft begin 2026-10-15 */\n&sercom1 { status = \"okay\"; };\n/* dt_explorer end */\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, sub: str = "") -> Path:
    """A repo with a committed app folder (<root>/<sub>); returns the app folder."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    app = root / sub if sub else root
    (app / "src").mkdir(parents=True, exist_ok=True)
    (app / "boards").mkdir(exist_ok=True)
    (app / "prj.conf").write_text("CONFIG_GPIO=y\n", encoding="utf-8")
    (app / "src" / "main.c").write_text("int main(void)\n{\n\treturn 0;\n}\n", encoding="utf-8")
    (app / "boards" / "same54_xpro.overlay").write_text("/ {\n};\n", encoding="utf-8")
    (app / "junk.log").write_text("x\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return app


def test_clean_repo(tmp_path):
    app = make_repo(tmp_path / "r")
    st = gs.read_status(app)
    assert st.ok and st.files == [] and st.branch == "main" and len(st.head) >= 7 and st.read_at is not None


def test_modified_added_deleted_untracked_and_ignored(tmp_path):
    app = make_repo(tmp_path / "r")
    (app / "prj.conf").write_text("CONFIG_GPIO=y\nCONFIG_I2C=y\n", encoding="utf-8")
    (app / "src" / "extra.c").write_text("/* new */\n", encoding="utf-8")
    git(app, "add", "src/extra.c")                                       # added (staged)
    (app / "src" / "main.c").unlink()                                    # deleted
    (app / "boards" / "new.overlay").write_text("&adc0 {};\n", encoding="utf-8")   # untracked
    (app / "ignored.log").write_text("zzz\n", encoding="utf-8")          # ignored
    st = gs.read_status(app)
    kinds = {c.rel: c.kind for c in st.files}
    assert kinds == {"boards/new.overlay": "untracked", "prj.conf": "modified", "src/extra.c": "added",
                     "src/main.c": "deleted"}
    assert st.count == 4 and "ignored.log" not in kinds and "junk.log" not in kinds


def test_limited_to_app_folder_in_a_bigger_repo(tmp_path):
    app = make_repo(tmp_path / "r", sub="apps/lab1")
    (tmp_path / "r" / "other.txt").write_text("outside\n", encoding="utf-8")
    (tmp_path / "r" / "apps" / "lab2").mkdir()
    (tmp_path / "r" / "apps" / "lab2" / "x.c").write_text("x\n", encoding="utf-8")
    (app / "prj.conf").write_text("CONFIG_X=y\n", encoding="utf-8")
    st = gs.read_status(app)
    assert [c.rel for c in st.files] == ["prj.conf"]
    assert st.files[0].repo_rel == "apps/lab1/prj.conf"
    diff = gs.read_diff(st, st.files[0])
    assert any(d.kind == "add" and d.text == "CONFIG_X=y" for d in diff)
    assert any(d.kind == "del" and d.text == "CONFIG_GPIO=y" for d in diff)


def test_diff_numbers_and_untracked_whole_file(tmp_path):
    app = make_repo(tmp_path / "r")
    (app / "src" / "main.c").write_text("int main(void)\n{\n\tinit();\n\treturn 0;\n}\n", encoding="utf-8")
    st = gs.read_status(app)
    d = gs.read_diff(st, st.files[0])
    added = [x for x in d if x.kind == "add"]
    assert [(x.new, x.text) for x in added] == [(3, "\tinit();")]
    assert [x.kind for x in d if x.kind == "hunk"] == ["hunk"]
    (app / "new.overlay").write_text("a\nb\nc\n", encoding="utf-8")
    st = gs.read_status(app)
    u = next(c for c in st.files if c.untracked)
    ud = gs.read_diff(st, u)
    assert [(x.kind, x.new, x.text) for x in ud if x.kind == "add"] == [("add", 1, "a"), ("add", 2, "b"), ("add", 3, "c")]
    assert not [x for x in ud if x.kind == "del"]
    (app / "bin.dat").write_bytes(b"\0\1\2")
    st = gs.read_status(app)
    b = next(c for c in st.files if c.rel == "bin.dat")
    assert "Binary" in gs.read_diff(st, b)[-1].text


def test_deleted_file_diff_and_rename(tmp_path):
    app = make_repo(tmp_path / "r")
    git(app, "mv", "prj.conf", "prj2.conf")
    (app / "boards" / "same54_xpro.overlay").unlink()
    st = gs.read_status(app)
    kinds = {c.rel: c.kind for c in st.files}
    assert kinds == {"prj2.conf": "renamed", "boards/same54_xpro.overlay": "deleted"}
    dele = next(c for c in st.files if c.kind == "deleted")
    assert [x.text for x in gs.read_diff(st, dele) if x.kind == "del"] == ["/ {", "};"]


def test_dtx_marks(tmp_path):
    app = make_repo(tmp_path / "r")
    (app / "boards" / "same54_xpro.overlay").write_text("/ {\n};\n" + MARKED_OVL, encoding="utf-8")   # marked block
    (app / "boards" / "same54_xpro.conf").write_text("CONFIG_I2C=y\n", encoding="utf-8")             # name
    (app / "dt_explorer.overlay").write_text("&a {};\n", encoding="utf-8")                           # Write to app
    (app / "dtx_devices").mkdir()
    (app / "dtx_devices" / "dtx_devices.h").write_text("/* gen */\n", encoding="utf-8")
    (app / "other.overlay").write_text(MARKED_OVL, encoding="utf-8")                                 # marker only
    (app / "prj.conf").write_text("CONFIG_GPIO=y\nCONFIG_X=y\n", encoding="utf-8")                  # user file
    (app / "src" / "main.c").write_text("int main(void) { return 1; }\n", encoding="utf-8")
    st = gs.read_status(app, ["same54_xpro"])
    dtx = {c.rel for c in st.files if c.dtx}
    assert dtx == {"boards/same54_xpro.overlay", "boards/same54_xpro.conf", "dt_explorer.overlay",
                   "dtx_devices/dtx_devices.h", "other.overlay"}
    assert {c.rel for c in st.files if not c.dtx} == {"prj.conf", "src/main.c"}
    # without the board stem, the plain .conf has no marker and no known name
    assert not next(c for c in gs.read_status(app).files if c.rel == "boards/same54_xpro.conf").dtx


def test_marked_block_detection():
    assert gs.has_marked_block(MARKED_OVL)
    assert gs.has_marked_block("# dt_explorer port begin 2026-10-15\nCONFIG_A=y\n# dt_explorer end\n")
    assert not gs.has_marked_block("/* my comment */\n")
    assert not gs.has_marked_block("/* dt_explorer draft begin x */\n")          # no end


def test_read_is_stable_until_asked_again(tmp_path):
    app = make_repo(tmp_path / "r")
    first = gs.read_status(app)
    (app / "prj.conf").write_text("CONFIG_Z=y\n", encoding="utf-8")
    assert first.files == []                                  # a state is a snapshot
    assert [c.rel for c in gs.read_status(app).files] == ["prj.conf"]


def test_no_commits_yet(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    st = gs.read_status(root)
    assert st.ok and st.head == "" and st.branch == "main"
    assert {c.rel: c.kind for c in st.files} == {"a.txt": "added", "b.txt": "untracked"}
    a = next(c for c in st.files if c.rel == "a.txt")
    assert [x.text for x in gs.read_diff(st, a) if x.kind == "add"] == ["a"]


def test_not_a_repository(tmp_path):
    app = tmp_path / "plain"
    app.mkdir()
    st = gs.read_status(app)
    assert not st.ok and "not inside a git repository" in st.message and st.files == []


def test_missing_folder(tmp_path):
    st = gs.read_status(tmp_path / "nope")
    assert not st.ok and "does not exist" in st.message


def test_git_missing_from_path(tmp_path, monkeypatch):
    app = make_repo(tmp_path / "r")
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert gs.git_exe() is None
    st = gs.read_status(app)
    assert not st.ok and "git was not found on PATH" in st.message


def test_git_timeout_becomes_a_message(tmp_path, monkeypatch):
    app = make_repo(tmp_path / "r")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)
    monkeypatch.setattr(gs.subprocess, "run", boom)
    st = gs.read_status(app)
    assert not st.ok and "timed out" in st.message


def test_only_read_only_git_commands_are_used(tmp_path, monkeypatch):
    app = make_repo(tmp_path / "r")
    seen = []
    real = gs.subprocess.run

    def spy(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)
    monkeypatch.setattr(gs.subprocess, "run", spy)
    (app / "prj.conf").write_text("CONFIG_Q=y\n", encoding="utf-8")
    st = gs.read_status(app)
    gs.read_diff(st, st.files[0])
    verbs = {c[3] for c in seen}                    # git -c core.quotepath=off <verb>
    assert verbs <= {"rev-parse", "symbolic-ref", "status", "diff"}, verbs
