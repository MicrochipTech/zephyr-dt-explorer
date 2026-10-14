"""Run west in the background and list Zephyr boards.

Pure core module: standard library and pyyaml only, no UI imports.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def default_python(ws: Path) -> Path:
    """Python of the workspace virtual environment (runs `-m west`)."""
    return Path(ws) / ".venv" / "Scripts" / "python.exe"


class WestRun:
    """A subprocess running in a background thread with a line-by-line log.

    `on_line` is called from the worker thread for every output line (stdout and
    stderr merged). All lines are also kept in `lines`. `wait()` returns the
    return code; `cancel()` terminates the process tree (the return code is then
    non-zero and `cancelled` is True).
    """

    def __init__(self, cmd: list, cwd: Path, on_line: Callable[[str], None] | None = None,
                 env: dict | None = None):
        self.cmd = [str(c) for c in cmd]
        self.cwd = Path(cwd)
        self.on_line = on_line
        self.env = env
        self.lines: list[str] = []
        self.returncode: int | None = None
        self.cancelled = False
        self.duration = 0.0
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _emit(self, line: str):
        self.lines.append(line)
        if self.on_line:
            try:
                self.on_line(line)
            except Exception:
                pass

    def _kill_tree(self, proc: subprocess.Popen):
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)

    def _run(self):
        try:
            proc = subprocess.Popen(
                self.cmd, cwd=str(self.cwd), env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError as exc:
            self._emit(f"cannot start {self.cmd[0]}: {exc}")
            self.returncode = -1
            self.duration = time.monotonic() - self._start
            return
        with self._lock:
            self._proc = proc
            cancelled = self.cancelled
        if cancelled:
            self._kill_tree(proc)
        for line in proc.stdout:
            self._emit(line.rstrip("\r\n"))
        self.returncode = proc.wait()
        self.duration = time.monotonic() - self._start

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> int | None:
        self._thread.join(timeout)
        return self.returncode

    def cancel(self):
        with self._lock:
            self.cancelled = True
            proc = self._proc
        if proc:
            self._kill_tree(proc)

    @property
    def log(self) -> str:
        return "\n".join(self.lines)


def run_west(ws: Path, args: Iterable, on_line: Callable[[str], None] | None = None,
             python: Path | None = None) -> WestRun:
    """Start `<python> -m west <args>` in the workspace root; returns immediately."""
    ws = Path(ws)
    py = Path(python) if python else default_python(ws)
    return WestRun([py, "-m", "west", *args], ws, on_line)


def cmake_only_build(ws: Path, app: Path, board: str, build_dir: Path,
                     on_line: Callable[[str], None] | None = None,
                     python: Path | None = None) -> WestRun:
    """`west build -b <board> -d <build_dir> --cmake-only <app>` (configure step only)."""
    return run_west(ws, ["build", "-b", board, "-d", Path(build_dir), "--cmake-only", Path(app)],
                    on_line, python)


def full_build(ws: Path, build_dir: Path,
               on_line: Callable[[str], None] | None = None,
               python: Path | None = None) -> WestRun:
    """`west build -d <build_dir>` in an already configured build folder."""
    return run_west(ws, ["build", "-d", Path(build_dir)], on_line, python)


@dataclass
class BoardInfo:
    name: str
    vendor: str
    full_name: str
    socs: list = field(default_factory=list)        # SoC names
    qualifiers: list = field(default_factory=list)  # e.g. "polarfire/smp/u54"
    path: Path | None = None                        # folder holding board.yml


def _qualifiers(socs: list) -> list:
    out = []
    for soc in socs or []:
        base = str(soc.get("name", ""))
        variants = soc.get("variants") or []
        if not variants:
            out.append(base)
        for v in variants:
            vname = str(v.get("name", ""))
            cc = v.get("cpucluster")
            out.append("/".join(p for p in (base, vname, str(cc) if cc else "") if p))
    return out


def module_board_roots(ws: Path) -> list:
    """`boards` folders of module board roots (`build.settings.board_root` in zephyr/module.yml)."""
    roots = []
    mods = Path(ws) / "modules"
    if not mods.is_dir():
        return roots
    for pattern in ("*/zephyr/module.yml", "*/*/zephyr/module.yml", "*/*/*/zephyr/module.yml"):
        for yml in mods.glob(pattern):
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace")) or {}
            except yaml.YAMLError:
                continue
            br = ((data.get("build") or {}).get("settings") or {}).get("board_root")
            if br:
                root = (yml.parent.parent / str(br).lstrip("/\\")).resolve()
                if (root / "boards").is_dir():
                    roots.append(root / "boards")
    return roots


def list_boards(ws: Path, vendors: Iterable | None = None,
                extra_board_dirs: Iterable = ()) -> list:
    """Boards whose board.yml vendor is one of `vendors`, sorted by name.

    `vendors=None` means every vendor. The vendor strings a UI should default to come from the
    registered providers (`plugins.board_vendors()`), not from this module.

    Searches `zephyr/boards`, the board roots of modules and `extra_board_dirs`.
    Does not call west.
    """
    ws = Path(ws)
    vend = None if vendors is None else {str(v).lower() for v in vendors}
    dirs = [ws / "zephyr" / "boards", *module_board_roots(ws), *map(Path, extra_board_dirs)]
    seen, boards = set(), []
    for d in dirs:
        if not d.is_dir():
            continue
        for yml in d.rglob("board.yml"):
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace")) or {}
            except yaml.YAMLError:
                continue
            b = data.get("board")
            if not isinstance(b, dict):
                continue
            vendor = str(b.get("vendor", "")).lower()
            name = str(b.get("name", ""))
            if (vend is not None and vendor not in vend) or not name or (name, yml.parent) in seen:
                continue
            seen.add((name, yml.parent))
            socs = b.get("socs") or []
            boards.append(BoardInfo(
                name=name, vendor=vendor, full_name=str(b.get("full_name", name)),
                socs=[str(s.get("name", "")) for s in socs], qualifiers=_qualifiers(socs),
                path=yml.parent))
    return sorted(boards, key=lambda x: x.name)
