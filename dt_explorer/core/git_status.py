"""Read-only git view of an app folder: what ``git status`` and ``git diff`` show for it.

Nothing here changes a repository: only ``rev-parse``, ``status`` and ``diff`` are run (with
``GIT_OPTIONAL_LOCKS=0``, so not even the index is refreshed). No commit, revert or checkout exists in this
module. Pure core module: no UI imports. Every git call has a timeout and is logged through ``core/log``.

The list is git's own (``status --porcelain``: modified, added, deleted, renamed, untracked; ``.gitignore``
respected; limited to the app folder). The only addition is :func:`dtx_paths`, which marks the files that the
tool writes (overlay and conf names, files with a ``dt_explorer`` marked block, ``dtx_devices/``).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import log as dtlog

log = logging.getLogger("dt_explorer.git_status")

TIMEOUT = 20                    # seconds per git call
MAX_UNTRACKED_LINES = 5000      # an untracked file is shown as "all added" up to this many lines
OVERLAY_NAME = "dt_explorer.overlay"     # core/verify.py "Write to app"
CONF_NAME = "dt_explorer.conf"
DEVICES_DIR = "dtx_devices"              # generated device module (later card)
DRAFT_DIR = ".dt_explorer"               # draft store: <app>/.dt_explorer/<build>.draft.json


class GitError(Exception):
    """git could not be run or failed in a way that is not 'not a repository'."""


class GitMissing(GitError):
    """git is not on PATH."""


@dataclass
class Change:
    rel: str                    # path relative to the app folder, forward slashes
    repo_rel: str               # path relative to the repository root, forward slashes
    xy: str                     # the two porcelain status letters
    orig: str = ""              # repo-relative old path of a rename
    dtx: bool = False           # the tool writes this file

    @property
    def untracked(self) -> bool:
        return self.xy == "??"

    @property
    def kind(self) -> str:
        """modified / added / deleted / renamed / untracked / conflict."""
        x, y = self.xy[0], self.xy[1]
        if self.xy == "??":
            return "untracked"
        if "U" in self.xy or self.xy in ("AA", "DD"):
            return "conflict"
        if "R" in self.xy or "C" in self.xy:
            return "renamed"
        if "D" in self.xy:
            return "deleted"
        if x == "A":
            return "added"
        return "modified"

    @property
    def staged(self) -> bool:
        return self.xy[0] not in " ?"


@dataclass
class GitState:
    app: Path
    ok: bool = False
    message: str = ""           # why not ok (shown instead of the list)
    root: Path | None = None
    branch: str = ""
    head: str = ""              # short hash; empty in a repository without commits
    files: list[Change] = field(default_factory=list)
    read_at: datetime | None = None

    @property
    def count(self) -> int:
        return len(self.files)


@dataclass
class DiffLine:
    kind: str                   # "meta", "hunk", "add", "del", "ctx"
    text: str
    old: int | None = None
    new: int | None = None


# ---- running git ---------------------------------------------------------------------------------
def git_exe() -> str | None:
    return shutil.which("git")


def _git(args: list[str], cwd: Path | str, *, timeout: float = TIMEOUT) -> tuple[int, str, str]:
    """Run git read-only; returns (rc, stdout, stderr). Raises GitMissing / GitError."""
    exe = git_exe()
    if not exe:
        raise GitMissing("git was not found on PATH")
    cmd = [exe, "-c", "core.quotepath=off", *args]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, timeout=timeout, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        dtlog.log_external(log, cmd, None, time.perf_counter() - t0, "timeout", cwd)
        raise GitError(f"git timed out after {timeout:.0f} s") from exc
    except OSError as exc:
        dtlog.log_external(log, cmd, None, time.perf_counter() - t0, str(exc), cwd)
        raise GitError(f"git cannot be started: {exc}") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    dtlog.log_external(log, ["git", *args], proc.returncode, time.perf_counter() - t0, err or out, cwd)
    return proc.returncode, out, err


# ---- status ----------------------------------------------------------------------------------------
def parse_porcelain_z(text: str) -> list[tuple[str, str, str]]:
    """``status --porcelain=v1 -z`` output -> [(xy, path, orig path)]."""
    parts = text.split("\0")
    out, i = [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path, orig = entry[:2], entry[3:], ""
        if ("R" in xy or "C" in xy) and i < len(parts):
            orig = parts[i]
            i += 1
        out.append((xy, path, orig))
    return out


def read_status(app_dir: Path | str, stems: list[str] | tuple = ()) -> GitState:
    """The git state of `app_dir`. Never raises: a problem becomes ``ok=False`` with a message."""
    app = Path(app_dir)
    st = GitState(app=app, read_at=datetime.now())
    if not app.is_dir():
        st.message = f"The app folder {app} does not exist."
        return st
    try:
        rc, out, err = _git(["rev-parse", "--show-toplevel", "--show-prefix"], app)
        if rc != 0:
            if "not a git repository" in err.lower():
                st.message = f"The app folder {app} is not inside a git repository."
            else:
                st.message = f"git could not read {app}: {err.strip().splitlines()[0] if err.strip() else 'rc ' + str(rc)}"
            return st
        lines = out.splitlines()
        st.root = Path(lines[0]) if lines else app
        prefix = lines[1].strip() if len(lines) > 1 else ""
        rc, out, _ = _git(["symbolic-ref", "--short", "-q", "HEAD"], app)
        st.branch = out.strip() if rc == 0 and out.strip() else "(detached)"
        rc, out, _ = _git(["rev-parse", "--short", "HEAD"], app)
        st.head = out.strip() if rc == 0 else ""
        rc, out, err = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."], app)
        if rc != 0:
            st.message = f"git status failed: {err.strip().splitlines()[0] if err.strip() else 'rc ' + str(rc)}"
            return st
    except GitMissing:
        st.message = ("git was not found on PATH. The Changes tab reads the state of the app folder with git; "
                      "install git or add it to PATH.")
        return st
    except GitError as exc:
        st.message = f"git failed: {exc}"
        return st
    for xy, path, orig in parse_porcelain_z(out):
        rel = path[len(prefix):] if prefix and path.startswith(prefix) else path
        st.files.append(Change(rel=rel, repo_rel=path, xy=xy, orig=orig))
    st.files.sort(key=lambda c: c.rel.lower())
    marked = dtx_paths(app, [c.rel for c in st.files], stems)
    for c in st.files:
        c.dtx = c.rel in marked
    st.ok = True
    return st


# ---- files the tool writes -------------------------------------------------------------------------
_MARK_BEGIN = re.compile(r"(?:/\*|#) dt_explorer [^\n]*? begin ")


def has_marked_block(text: str) -> bool:
    """True when the text holds a ``dt_explorer ... begin`` ... ``dt_explorer end`` block (core/apply.py)."""
    return bool(_MARK_BEGIN.search(text)) and "dt_explorer end" in text


def dtx_paths(app_dir: Path | str, rels: list[str], stems: list[str] | tuple = ()) -> set[str]:
    """The subset of `rels` (app-relative, forward slashes) that the tool writes.

    * ``boards/<stem>.overlay`` / ``.conf`` for the board stems of the build (Apply to app),
    * ``dt_explorer.overlay`` / ``dt_explorer.conf`` in the app folder (Write to app),
    * everything below ``dtx_devices/`` and ``.dt_explorer/`` (generated module, draft store),
    * any file that contains a ``dt_explorer`` marked block, or a ``CMakeLists.txt`` that names ``dtx_devices``.
    """
    app = Path(app_dir)
    names = set()
    for s in stems:
        names |= {f"boards/{s}.overlay".lower(), f"boards/{s}.conf".lower()}
    names |= {OVERLAY_NAME, CONF_NAME}
    out = set()
    for rel in rels:
        low = rel.lower()
        if low in names or low.startswith(DEVICES_DIR + "/") or low.startswith(DRAFT_DIR + "/"):
            out.add(rel)
            continue
        p = app / rel
        try:
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if has_marked_block(text) or (low.endswith("cmakelists.txt") and DEVICES_DIR in text):
            out.add(rel)
    return out


# ---- diff ------------------------------------------------------------------------------------------
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(text: str) -> list[DiffLine]:
    """Unified diff text -> lines with kind and old/new line numbers."""
    out: list[DiffLine] = []
    old = new = 0
    in_hunk = False
    for raw in text.splitlines():
        m = _HUNK.match(raw)
        if m:
            old, new, in_hunk = int(m.group(1)), int(m.group(2)), True
            out.append(DiffLine("hunk", raw))
        elif not in_hunk or raw.startswith("diff --git"):
            in_hunk = in_hunk and not raw.startswith("diff --git")
            out.append(DiffLine("meta", raw))
        elif raw.startswith("+"):
            out.append(DiffLine("add", raw[1:], new=new))
            new += 1
        elif raw.startswith("-"):
            out.append(DiffLine("del", raw[1:], old=old))
            old += 1
        elif raw.startswith("\\"):
            out.append(DiffLine("meta", raw))
        else:
            out.append(DiffLine("ctx", raw[1:], old=old, new=new))
            old += 1
            new += 1
    return out


def untracked_diff(path: Path, repo_rel: str) -> list[DiffLine]:
    """A file git does not know yet, shown as one hunk of added lines."""
    if repo_rel.endswith("/"):
        return [DiffLine("meta", f"{repo_rel} is an untracked directory (another repository or empty)")]
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [DiffLine("meta", f"cannot read {path}: {exc}")]
    out = [DiffLine("meta", f"new file (untracked): {repo_rel}")]
    if b"\0" in data[:8192]:
        return out + [DiffLine("meta", f"Binary file, {len(data)} bytes, not shown")]
    lines = data.decode("utf-8", errors="replace").splitlines()
    shown = lines[:MAX_UNTRACKED_LINES]
    out.append(DiffLine("hunk", f"@@ -0,0 +1,{len(shown)} @@"))
    out += [DiffLine("add", ln, new=i) for i, ln in enumerate(shown, 1)]
    if len(lines) > len(shown):
        out.append(DiffLine("meta", f"... {len(lines) - len(shown)} more lines not shown"))
    return out


def read_diff(state: GitState, change: Change) -> list[DiffLine]:
    """``git diff`` of one changed file against HEAD (untracked: the whole file as added). Never raises."""
    if change.untracked:
        return untracked_diff(state.app / change.rel, change.repo_rel)
    root = state.root or state.app
    paths = [change.repo_rel] + ([change.orig] if change.orig else [])
    base = ["diff", "--no-color", "--no-ext-diff", "-M", "HEAD" if state.head else "--cached", "--"]
    try:
        rc, out, err = _git([*base, *paths], root)
    except GitError as exc:
        return [DiffLine("meta", f"git diff failed: {exc}")]
    if rc != 0:
        return [DiffLine("meta", f"git diff failed: {err.strip() or 'rc ' + str(rc)}")]
    if not out.strip():
        return [DiffLine("meta", "git shows no difference against HEAD for this file.")]
    return parse_diff(out)
