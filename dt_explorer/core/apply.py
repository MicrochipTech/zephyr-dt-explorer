"""Write a port result into the app as the board files that Zephyr picks up by itself.

The files are ``<app>/boards/<name>.overlay`` and ``<app>/boards/<name>.conf``. Only NEW files are
created. A file that exists is never touched unless the caller names it in ``overwrite`` (the UI does
that after the user has seen the diff). Nothing outside ``<app>/boards`` is written, so the source
board's files and ``src/`` stay as they are. Pure core module: no UI imports, no vendor terms.

File naming (Zephyr v4.3.0, RTOS4 workspace, ``zephyr/cmake/modules``):

* ``configuration_files.cmake:42`` (Kconfig) and ``:72`` (overlay) look in ``<app>/boards`` with
  ``zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/boards ... SUFFIX ${FILE_SUFFIX})``, no explicit
  ``NAMES``, so the board build string is used.
* ``extensions.cmake:2807-2813`` builds the names with
  ``zephyr_build_string(filename_list SHORT shortened_filename_list BOARD ... BOARD_REVISION ...
  BOARD_QUALIFIERS ... MERGE REVERSE)``; ``:2823-2829`` append ``.overlay`` / ``.conf``.
* ``extensions.cmake:1716-1719`` (``zephyr_build_string``): the qualifier string is split at ``/`` and
  joined with ``_`` after the board name (``sam_e54_xpro/atsame54p20a`` -> ``sam_e54_xpro_atsame54p20a``);
  a revision is appended with ``.`` -> ``_``. ``MERGE`` (``:1721-1727``) adds the name without revision.
* ``extensions.cmake:1734-1740`` (``SHORT``): the first qualifier (the SoC) is dropped, giving
  ``sam_e54_xpro``. ``extensions.cmake:2857-2858`` (overlay) and ``:2904-2905`` (conf) make that short
  form a fatal error for a board with more than one SoC; ``:2864`` / ``:2911`` make it a fatal error
  when both the long and the short file exist.

So: the long name ``<board>_<soc>[_<cpu>...]`` is always valid; the short name ``<board>`` only for
single-SoC boards. We write the short name for a single-SoC board (the usual ``boards/<board>.overlay``
convention) and the long name otherwise, and treat an existing file under the OTHER name as "exists"
so that a conflicting second file is never created. A revision-less name applies to every revision.
"""
from __future__ import annotations

import difflib
import logging
import re
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .buildinfo import BuildInfoError, read_build_info

log = logging.getLogger("dt_explorer.apply")


class ApplyError(Exception):
    """The port result cannot be applied (unusable target board, source board file, ...)."""


@dataclass
class FilePlan:
    kind: str                       # "overlay" or "conf"
    path: Path                      # file that would be written (or the existing one under the other name)
    text: str                       # the new content
    exists: bool = False
    identical: bool = False         # exists with the same content: nothing to do
    comment_only: bool = False      # the new content has no statements: not written
    diff: str = ""                  # unified diff existing -> new (only when it exists and differs): "replace"
    note: str = ""
    merged: str = ""                # the existing content plus the marked block ("append"), only when it exists
    append_diff: str = ""           # unified diff existing -> merged (empty when nothing would change)
    append_note: str = ""           # e.g. conf lines skipped as duplicates

    @property
    def action(self) -> str:
        if self.comment_only:
            return "skip (comments only)"
        if self.identical:
            return "identical"
        return "exists" if self.exists else "new"


@dataclass
class ApplyPlan:
    app_dir: Path
    board_target: str
    stem: str                       # file name without extension that is written
    files: list[FilePlan] = field(default_factory=list)

    def file(self, kind: str) -> FilePlan:
        return next(f for f in self.files if f.kind == kind)


@dataclass
class ApplyResult:
    written: list[Path] = field(default_factory=list)
    appended: list[Path] = field(default_factory=list)      # existing file, marked block added or replaced
    overwritten: list[Path] = field(default_factory=list)
    refused: list[Path] = field(default_factory=list)       # exist, not named in `overwrite`
    skipped: list[Path] = field(default_factory=list)       # comments only or identical

    def lines(self) -> list[str]:
        out = [f"written:     {p}" for p in self.written]
        out += [f"appended:    {p}" for p in self.appended]
        out += [f"overwritten: {p}" for p in self.overwritten]
        out += [f"refused (exists, not overwritten): {p}" for p in self.refused]
        out += [f"skipped:     {p}" for p in self.skipped]
        return out


# ---- naming ----------------------------------------------------------------------------------
def board_file_stems(board_target: str, single_soc: bool = True) -> tuple[str, list[str]]:
    """``(stem to write, every stem Zephyr accepts for this board)``.

    `board_target` is ``<board>`` or ``<board>/<qualifiers>`` (``CONFIG_BOARD_TARGET``), optionally with
    ``@<revision>`` (ignored: the revision-less file applies to all revisions).
    """
    bt = (board_target or "").strip().split("@", 1)[0].strip("/")
    if not bt or any(c in bt for c in '\\:*?"<>| '):
        raise ApplyError(f"not a board target: {board_target!r}")
    parts = bt.split("/")
    board, quals = parts[0], parts[1:]
    long_stem = "_".join([board, *quals])
    if not quals:
        return long_stem, [long_stem]
    return (board if single_soc else long_stem), [long_stem, board]


def single_soc_of(board_yml: Path | str | None) -> bool:
    """True when the board's ``board.yml`` lists exactly one SoC (unknown -> False, the safe long name)."""
    if not board_yml:
        return False
    try:
        data = yaml.safe_load(Path(board_yml).read_text(encoding="utf-8")) or {}
        socs = (data.get("board") or {}).get("socs") or []
    except (OSError, yaml.YAMLError):
        return False
    return len(socs) == 1


def target_of(build_dir: Path | str) -> tuple[str, bool]:
    """``(board target, single SoC)`` of a build folder."""
    try:
        info = read_build_info(build_dir)
    except (BuildInfoError, OSError) as exc:
        raise ApplyError(f"cannot read the build {build_dir}: {exc}") from exc
    bt = info.board_target or info.board
    if not bt:
        raise ApplyError(f"cannot tell the board of {build_dir}")
    return bt, single_soc_of(info.board_yml)


def app_dir_of(build_dir: Path | str) -> Path:
    """The app (``APPLICATION_SOURCE_DIR``) that a build folder was configured from."""
    try:
        info = read_build_info(build_dir)
    except (BuildInfoError, OSError) as exc:
        raise ApplyError(f"cannot read the build {build_dir}: {exc}") from exc
    if info.app_source_dir is None:
        raise ApplyError(f"CMakeCache.txt of {build_dir} names no APPLICATION_SOURCE_DIR")
    return Path(info.app_source_dir)


# ---- content checks ----------------------------------------------------------------------------
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def is_comment_only(text: str) -> bool:
    """No statement left after removing ``/* */``, ``//`` and ``#`` comment lines and blank lines."""
    t = _BLOCK_COMMENT.sub("", text or "")
    for line in t.splitlines():
        s = line.strip()
        if s and not s.startswith("//") and not s.startswith("#"):
            return False
    return True


def _norm(text: str) -> str:
    return (text or "").replace("\r\n", "\n")


# ---- append mode: a marked block inside the app's own file ------------------------------------------
END_MARK = "dt_explorer end"
_OVL_BLOCK = re.compile(r"/\* dt_explorer [^\n]*? begin [^\n]*? \*/\n.*?/\* " + END_MARK + r" \*/\n?", re.S)
_CONF_BLOCK = re.compile(r"# dt_explorer [^\n]*? begin [^\n]*?\n.*?# " + END_MARK + r"\n?", re.S)


def _marks(kind: str, source: str, today: str) -> tuple[str, str]:
    if kind == "overlay":
        return f"/* dt_explorer {source} begin {today} */\n", f"/* {END_MARK} */\n"
    return f"# dt_explorer {source} begin {today}\n", f"# {END_MARK}\n"


def _statement_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def merge_block(kind: str, existing: str, new: str, *, source: str = "draft",
                today: str | None = None) -> tuple[str, str]:
    """``(merged text, note)``: `existing` with `new` as one marked block.

    A block from an earlier run (same markers, any source/date) is replaced in place, everything else in
    `existing` stays byte for byte. Otherwise the block is appended at the end. For a conf the lines that the
    rest of the file already has are left out (duplicates); no statement left means no block (an old block is
    dropped, the rest stays). `today` is for tests.
    """
    today = today or date.today().isoformat()
    existing, new = _norm(existing), _norm(new)
    rx = _OVL_BLOCK if kind == "overlay" else _CONF_BLOCK
    m = rx.search(existing)
    outside = existing[:m.start()] + existing[m.end():] if m else existing
    note = ""
    body = new.strip("\n")
    if kind == "conf":
        have = set(_statement_lines(outside))
        keep, skipped = [], 0
        for ln in body.splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and s in have:
                skipped += 1
                continue
            keep.append(ln)
        body = "\n".join(keep).strip("\n")
        if skipped:
            note = f"{skipped} line(s) already in the file were left out"
        if not _statement_lines(body):
            body = ""
    elif is_comment_only(body):
        body = ""
    if not body:
        return outside, note
    begin, end = _marks(kind, source, today)
    block = begin + body + "\n" + end
    if m:
        return existing[:m.start()] + block + existing[m.end():], note
    if not existing.strip():
        return block, note
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block, note


# ---- plan and apply ------------------------------------------------------------------------------
def _udiff(old: str, new: str, a: str, b: str) -> str:
    d = "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                     fromfile=a, tofile=b))
    return d if not d or d.endswith("\n") else d + "\n"


def plan_apply(app_dir: Path | str, target_board: str, overlay: str, conf: str, *,
               single_soc: bool = True, source_board: str | None = None,
               new_label: str = "port result", today: str | None = None) -> ApplyPlan:
    """What ``apply`` would do. Reads the app folder, writes nothing. `new_label` names the new content in
    the diff header (the Draft tab passes ``"draft"``)."""
    app = Path(app_dir)
    if not app.is_dir():
        raise ApplyError(f"app folder not found: {app}")
    stem, accepted = board_file_stems(target_board, single_soc)
    if source_board:
        src_stems = set(board_file_stems(source_board, False)[1]) | set(board_file_stems(source_board, True)[1])
        if src_stems & set(accepted):
            raise ApplyError(f"{target_board} names the same board files as the source board "
                             f"{source_board}; the source board's files are never changed")
    plan = ApplyPlan(app, target_board, stem)
    boards = app / "boards"
    for kind, text in (("overlay", overlay), ("conf", conf)):
        text = text or ""
        path = boards / f"{stem}.{kind}"
        fp = FilePlan(kind, path, text)
        for other in accepted:                  # an existing file under the other accepted name counts
            cand = boards / f"{other}.{kind}"
            if cand.is_file():
                if cand != path:
                    fp.note = (f"Zephyr also accepts the name {cand.name}, and it exists; a second file "
                               f"{path.name} next to it would be a fatal 'conflicting file names' error")
                fp.path, fp.exists = cand, True
                break
        fp.comment_only = is_comment_only(text)
        if fp.exists:
            old = _norm(fp.path.read_text(encoding="utf-8", errors="replace"))
            new = _norm(text)
            if not fp.comment_only:
                fp.merged, fp.append_note = merge_block(kind, old, text, source=new_label.split()[0], today=today)
                if fp.merged != old:
                    fp.append_diff = _udiff(old, fp.merged, f"{fp.path} (existing)",
                                            f"{fp.path.name} (existing + {new_label} block)")
            fp.identical = old == new
            if not fp.identical:
                fp.diff = "".join(difflib.unified_diff(
                    old.splitlines(keepends=True), new.splitlines(keepends=True),
                    fromfile=f"{fp.path} (existing)", tofile=f"{fp.path.name} ({new_label})"))
                if fp.diff and not fp.diff.endswith("\n"):
                    fp.diff += "\n"
        plan.files.append(fp)
        log.info("apply plan %s: %s -> %s%s", kind, fp.action, fp.path,
                 f" ({len(fp.diff.splitlines())} diff lines)" if fp.diff else "")
    return plan


def apply(plan: ApplyPlan, overwrite: set | None = None, append: set | None = None) -> ApplyResult:
    """Write the new files of `plan`. A file that exists is replaced only when its path (or its
    kind, ``"overlay"`` / ``"conf"``) is in `overwrite`; when it is in `append` instead, the existing content
    is kept and the marked block is added or replaced (``FilePlan.merged``). Comment-only content is never
    written. `overwrite` wins over `append`."""
    ow = {str(x) for x in (overwrite or ())}
    ap_ = {str(x) for x in (append or ())}
    res = ApplyResult()
    boards = (plan.app_dir / "boards").resolve()
    for fp in plan.files:
        if fp.comment_only or fp.identical:
            log.info("apply: skip %s (%s)", fp.path, fp.action)
            res.skipped.append(fp.path)
            continue
        if fp.path.resolve().parent != boards:                  # defensive: only <app>/boards/<file>
            raise ApplyError(f"refusing to write outside {boards}: {fp.path}")
        if fp.exists and not ({str(fp.path), fp.kind} & ow) and ({str(fp.path), fp.kind} & ap_):
            if fp.merged == _norm(fp.path.read_text(encoding="utf-8", errors="replace")):
                log.info("apply: skip %s (block already up to date)", fp.path)
                res.skipped.append(fp.path)
                continue
            log.info("apply: append block to %s (%d diff lines)", fp.path, len(fp.append_diff.splitlines()))
            fp.path.write_text(fp.merged, encoding="utf-8", newline="\n")
            res.appended.append(fp.path)
            continue
        if fp.exists and not ({str(fp.path), fp.kind} & ow):
            log.warning("apply: refused to overwrite %s (exists, overwrite not confirmed)", fp.path)
            res.refused.append(fp.path)
            continue
        fp.path.parent.mkdir(parents=True, exist_ok=True)
        log.info("apply: write %s (%s, %d bytes%s)", fp.path, "overwrite, confirmed" if fp.exists else "new file",
                 len(fp.text.encode("utf-8")), f", {len(fp.diff.splitlines())} diff lines" if fp.diff else "")
        fp.path.write_text(fp.text, encoding="utf-8", newline="\n")
        (res.overwritten if fp.exists else res.written).append(fp.path)
    return res


def plan_lines(plan: ApplyPlan) -> list[str]:
    """Plain-text rendering of a plan (used by the UI and the evidence scripts)."""
    out = [f"apply to {plan.app_dir} for {plan.board_target}"]
    for fp in plan.files:
        out.append(f"  {fp.action:22s} {fp.path}")
        if fp.note:
            out.append(f"      note: {fp.note}")
        if fp.append_note:
            out.append(f"      note: {fp.append_note}")
        if fp.append_diff:
            out.append("      append (default):")
            out += ["      " + ln for ln in fp.append_diff.rstrip("\n").splitlines()]
        elif fp.exists and fp.merged and not fp.comment_only:
            out.append("      append: block already up to date")
        if fp.diff:
            out.append("      replace (explicit tick):")
            out += ["      " + ln for ln in fp.diff.rstrip("\n").splitlines()]
    return out


def draft_plan(build_dir: Path | str, overlay: str, conf: str, *, app_dir: Path | str | None = None,
               today: str | None = None) -> ApplyPlan:
    """Plan for writing a draft as the board files of the build's OWN board (Draft tab "Apply to app").

    The app is the build's ``APPLICATION_SOURCE_DIR`` unless `app_dir` is given (tests apply to a copy).
    There is no source board: the files belong to the board the build was configured for.
    """
    board, single = target_of(build_dir)
    app = Path(app_dir) if app_dir is not None else app_dir_of(build_dir)
    return plan_apply(app, board, overlay, conf, single_soc=single, new_label="draft", today=today)


# ---- prj.conf for the generated device module (P8.14) -------------------------------------------------
def plan_prj_conf(app_dir: Path | str, conf_text: str, *, source: str = "codegen",
                  today: str | None = None) -> FilePlan:
    """Plan for adding `conf_text` to ``<app>/prj.conf`` as one marked block (append mode only).

    The returned ``FilePlan`` has kind ``"conf"``; ``merged`` is the new file content, ``append_diff`` the diff
    (empty when nothing changes, e.g. every line is already in the file). Writes nothing.
    """
    app = Path(app_dir)
    if not app.is_dir():
        raise ApplyError(f"app folder not found: {app}")
    path = app / "prj.conf"
    fp = FilePlan("conf", path, conf_text or "", exists=path.is_file())
    fp.comment_only = is_comment_only(conf_text or "")
    old = _norm(path.read_text(encoding="utf-8", errors="replace")) if fp.exists else ""
    if not fp.comment_only:
        fp.merged, fp.append_note = merge_block("conf", old, conf_text, source=source, today=today)
        if fp.merged != old:
            fp.append_diff = _udiff(old, fp.merged, f"{path.name} (existing)", f"{path.name} (+ {source} block)")
    return fp


def apply_prj_conf(fp: FilePlan) -> str:
    """Write the merged ``prj.conf`` of :func:`plan_prj_conf`; returns one report line."""
    if fp.comment_only or not fp.append_diff:
        return f"skipped:     {fp.path} (nothing to add)"
    existed = fp.path.is_file()
    fp.path.write_text(fp.merged, encoding="utf-8", newline="\n")
    log.info("apply: prj.conf block written to %s", fp.path)
    return f"{'appended' if existed else 'written'}:    {fp.path}"
