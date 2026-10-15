"""Authoritative check of a draft: let Zephyr itself configure the app with the draft applied.

Runs `west build --cmake-only` of the build's app and board into a scratch folder with
`-DEXTRA_DTC_OVERLAY_FILE=<overlay>` and `-DEXTRA_CONF_FILE=<conf>`. The user's build folder is only
read (CMakeCache.txt, zephyr/.config), never written. Pure core module: no UI imports.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import west_tools
from .buildinfo import BuildInfoError, parse_cmake_cache, read_build_info

log = logging.getLogger("dt_explorer.verify")

OVERLAY_NAME = "dt_explorer.overlay"
CONF_NAME = "dt_explorer.conf"

# Lines worth showing when a configure run fails or warns.
_ERROR_LINE = re.compile(
    r"devicetree error|^\s*error:|\bCMake Error|^\s*ERROR:|"
    r"\bundefined symbol\b|\bKconfig\b.*\b(error|warning)\b|\.conf:\d+:|"
    r"\bwarning:.*\b(assigned|CONFIG_)|\bdts\.pre:\d+|\.overlay:\d+", re.I)


class VerifyError(Exception):
    """The build folder does not say enough to rerun its configure step."""


@dataclass
class VerifyResult:
    ok: bool
    returncode: int | None
    errors: list[str] = field(default_factory=list)     # the first lines that explain a failure
    warnings: list[str] = field(default_factory=list)   # Kconfig/devicetree warnings of a good run
    log_tail: list[str] = field(default_factory=list)
    duration: float = 0.0
    scratch: Path | None = None
    cmd: list[str] = field(default_factory=list)


@dataclass
class VerifyPlan:
    """Everything needed to start the run (derived from the build, nothing started yet)."""
    workspace: Path
    python: Path
    app: Path
    board: str
    work: Path            # scratch folder of this build
    build: Path           # work/build
    overlay: Path
    conf: Path


def _fwd(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def plan(build_dir: Path | str, overlay_text: str, conf_text: str,
         scratch_root: Path | str | None = None) -> VerifyPlan:
    """Derive app, board, workspace and python from the build; write the two input files."""
    build_dir = Path(build_dir)
    try:
        info = read_build_info(build_dir)
    except BuildInfoError as exc:
        raise VerifyError(str(exc)) from exc
    cache_path = build_dir / "CMakeCache.txt"
    cache = parse_cmake_cache(cache_path) if cache_path.is_file() else {}
    if info.workspace is None or info.app_source_dir is None:
        raise VerifyError("CMakeCache.txt of the build names no ZEPHYR_BASE / APPLICATION_SOURCE_DIR")
    board = info.board_target or cache.get("BOARD") or cache.get("CACHED_BOARD") or info.board
    if not board:
        raise VerifyError("cannot tell the board of this build")
    if info.board_revision and "@" not in board:
        board += "@" + info.board_revision
    python = west_tools.default_python(info.workspace)
    if not python.is_file():
        raise VerifyError(f"no virtual environment python at {python}")
    root = Path(scratch_root) if scratch_root else Path(tempfile.gettempdir()) / "dt_explorer_verify"
    key = hashlib.sha1(os.path.normcase(str(build_dir.resolve())).encode("utf-8")).hexdigest()[:10]
    work = root / key
    build = work / "build"
    if build.exists():
        shutil.rmtree(build, ignore_errors=True)       # our own scratch, never the user's build
    work.mkdir(parents=True, exist_ok=True)
    overlay, conf = work / OVERLAY_NAME, work / CONF_NAME
    overlay.write_text(overlay_text or "", encoding="utf-8", newline="\n")
    conf.write_text(conf_text or "", encoding="utf-8", newline="\n")
    log.info("verify plan for %s: board=%s app=%s workspace=%s; scratch files %s (%d chars), %s (%d chars)",
             build_dir, board, info.app_source_dir, info.workspace, overlay, len(overlay_text or ""),
             conf, len(conf_text or ""))
    return VerifyPlan(info.workspace, python, info.app_source_dir, board, work, build, overlay, conf)


def start(build_dir: Path | str, overlay_text: str, conf_text: str,
          on_line: Callable[[str], None] | None = None,
          scratch_root: Path | str | None = None) -> tuple[west_tools.WestRun, VerifyPlan]:
    """Start the cmake-only run in the background; returns the WestRun and the plan."""
    p = plan(build_dir, overlay_text, conf_text, scratch_root)
    args = ["build", "-b", p.board, "-d", p.build, "--cmake-only", p.app, "--",
            f"-DEXTRA_DTC_OVERLAY_FILE={_fwd(p.overlay)}", f"-DEXTRA_CONF_FILE={_fwd(p.conf)}"]
    return west_tools.run_west(p.workspace, args, on_line, p.python), p


def parse_output(lines: list[str], returncode: int | None, limit: int = 12) -> tuple[list[str], list[str]]:
    """(errors, warnings): on a failed run the first `limit` explaining lines, else the warnings."""
    hits = [ln.rstrip() for ln in lines if _ERROR_LINE.search(ln)]
    if returncode == 0:
        return [], [h for h in hits if "warning" in h.lower()][:limit]
    if not hits:                                        # no known marker: show the log tail
        hits = [ln for ln in lines if ln.strip()][-limit:]
    return list(dict.fromkeys(hits))[:limit], []


def result_of(run: west_tools.WestRun, p: VerifyPlan | None = None) -> VerifyResult:
    errors, warnings = parse_output(run.lines, run.returncode)
    log.info("verify result: %s (rc=%s, %.1fs), %d error line(s), %d warning(s)",
             "ok" if run.returncode == 0 else "FAILED", run.returncode, run.duration,
             len(errors), len(warnings))
    for e in errors:
        log.warning("verify error line: %s", e)
    return VerifyResult(ok=run.returncode == 0, returncode=run.returncode, errors=errors,
                        warnings=warnings, log_tail=run.lines[-15:], duration=run.duration,
                        scratch=p.build if p else None, cmd=run.cmd)


def verify(build_dir: Path | str, overlay_text: str, conf_text: str,
           on_line: Callable[[str], None] | None = None,
           scratch_root: Path | str | None = None, timeout: float | None = None) -> VerifyResult:
    """Blocking variant: start, wait, parse. Raises VerifyError when the build is unusable."""
    run, p = start(build_dir, overlay_text, conf_text, on_line, scratch_root)
    run.wait(timeout)
    if run.running:
        log.warning("verify timed out after %ss, cancelling", timeout)
        run.cancel()
        run.wait(30)
    return result_of(run, p)


def write_to_app(app_dir: Path | str, overlay_text: str, conf_text: str) -> list[Path]:
    """Write `<app>/dt_explorer.overlay` and `<app>/dt_explorer.conf`; returns the paths written.

    Zephyr does not pick these names up by itself: the app's CMakeLists.txt or the west command line
    has to reference them (`-DEXTRA_DTC_OVERLAY_FILE=dt_explorer.overlay -DEXTRA_CONF_FILE=dt_explorer.conf`).
    """
    app = Path(app_dir)
    out = []
    for name, text in ((OVERLAY_NAME, overlay_text), (CONF_NAME, conf_text)):
        path = app / name
        log.info("write %s (%s, %d bytes, confirmed by the caller)", path,
                 "overwrites existing file" if path.exists() else "new file", len((text or "").encode("utf-8")))
        path.write_text(text or "", encoding="utf-8", newline="\n")
        out.append(path)
    return out


def existing_files(app_dir: Path | str) -> list[Path]:
    """Files `write_to_app` would overwrite."""
    return [p for p in (Path(app_dir) / OVERLAY_NAME, Path(app_dir) / CONF_NAME) if p.exists()]
