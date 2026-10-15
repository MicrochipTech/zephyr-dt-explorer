"""Logging of what the tool does, sees and decides.

Pure core module (standard library only, no UI imports). All loggers live under ``dt_explorer.*``.
`setup()` attaches a rotating file handler (``%USERPROFILE%\\.dt_explorer\\logs\\dtx_<date>.log``,
folder overridable with the env var ``DTX_LOG_DIR``) and a ring buffer that the Log tab reads.
The log contains local paths including the user name; look at it before sending it to someone.
"""

from __future__ import annotations

import collections
import datetime
import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT_NAME = "dt_explorer"
RING_SIZE = 5000
KEEP_FILES = 10                 # day files kept in the log folder
FILE_MAX_BYTES = 5_000_000
FILE_BACKUPS = 10
FOREIGN_LOGGERS = ("nicegui",)
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_lock = threading.Lock()
_file_handler: logging.Handler | None = None
_ring: "RingHandler | None" = None
_commit: str | None = None
_hooks_installed = False
enabled = False                 # True after setup(): build loads then write the context header


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(ROOT_NAME + ("." + name if name else ""))


class RingHandler(logging.Handler):
    """Keeps the last `size` formatted lines; the UI polls `since()`."""

    def __init__(self, size: int = RING_SIZE):
        super().__init__(logging.DEBUG)
        self.buf: collections.deque = collections.deque(maxlen=size)   # (seq, levelno, text)
        self.seq = 0
        self._rlock = threading.Lock()
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:
            return
        with self._rlock:
            for line in text.splitlines() or [""]:       # one entry per physical line
                self.seq += 1
                self.buf.append((self.seq, record.levelno, line))

    def since(self, seq: int = 0, min_level: int = logging.DEBUG) -> list:
        """[(seq, levelno, text)] newer than `seq` with levelno >= min_level."""
        with self._rlock:
            return [e for e in self.buf if e[0] > seq and e[1] >= min_level]

    def lines(self, min_level: int = logging.DEBUG) -> list:
        return [e[2] for e in self.since(0, min_level)]


def default_log_dir() -> Path:
    env = os.environ.get("DTX_LOG_DIR", "").strip()
    if env:
        return Path(env)
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".dt_explorer" / "logs"


def _prune(folder: Path, keep: int) -> None:
    files = sorted(folder.glob("dtx_*.log"), key=lambda p: p.name, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    for extra in folder.glob("dtx_*.log.*"):          # rotated parts of days that are gone
        base = extra.name.rsplit(".", 1)[0]
        if not (folder / base).exists():
            try:
                extra.unlink()
            except OSError:
                pass


def setup(debug: bool = False, log_dir: Path | str | None = None, *,
          max_bytes: int = FILE_MAX_BYTES, backups: int = FILE_BACKUPS,
          keep_files: int = KEEP_FILES) -> Path | None:
    """(Re)configure logging; returns the log file path (None if the file cannot be created).

    Safe to call repeatedly: earlier handlers of this module are removed first.
    """
    global _file_handler, _ring, enabled
    enabled = True
    root = get_logger()
    with _lock:
        for h in (_file_handler, _ring):
            if h is not None:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        root.setLevel(logging.DEBUG if debug else logging.INFO)
        root.propagate = False
        _ring = RingHandler()
        root.addHandler(_ring)
        _file_handler = None
        path: Path | None = None
        folder = Path(log_dir) if log_dir else default_log_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"dtx_{datetime.date.today().isoformat()}.log"
            fh = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups,
                                                      encoding="utf-8")
            fh.setFormatter(logging.Formatter(FORMAT))
            root.addHandler(fh)
            _file_handler = fh
            _prune(folder, keep_files)
        except OSError as exc:
            path = None
            root.warning("cannot write the log file in %s: %s", folder, exc)
    _install_hooks()
    _mirror_foreign()
    return path


def _mirror_foreign() -> None:
    """Errors of the web framework (exceptions in page handlers) also go to the ring and the file."""
    for name in FOREIGN_LOGGERS:
        lg = logging.getLogger(name)
        for h in (_ring, _file_handler):
            if h is not None and h not in lg.handlers:
                lg.addHandler(h)
        for old in [h for h in lg.handlers if isinstance(h, (RingHandler, logging.handlers.RotatingFileHandler))
                    and h not in (_ring, _file_handler)]:
            lg.removeHandler(old)


def file_path() -> Path | None:
    fh = _file_handler
    return Path(fh.baseFilename) if fh is not None else None


def ring() -> RingHandler:
    """The ring buffer (created with defaults if setup() was never called)."""
    global _ring
    if _ring is None:
        _ring = RingHandler()
        get_logger().addHandler(_ring)
        if get_logger().level == logging.NOTSET:
            get_logger().setLevel(logging.INFO)
    return _ring


def set_debug(on: bool) -> None:
    get_logger().setLevel(logging.DEBUG if on else logging.INFO)
    get_logger("log").info("log level: %s", "DEBUG" if on else "INFO")


def is_debug() -> bool:
    return get_logger().isEnabledFor(logging.DEBUG)


def _install_hooks() -> None:
    """Uncaught exceptions (main and worker threads) go to the log with their traceback."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook

    def hook(tp, val, tb):
        get_logger("crash").error("uncaught exception", exc_info=(tp, val, tb))
        old_hook(tp, val, tb)

    def thook(args):
        get_logger("crash").error("uncaught exception in thread %s",
                                  getattr(args.thread, "name", "?"),
                                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        old_thread_hook(args)

    sys.excepthook = hook
    threading.excepthook = thook


# ---- helpers for log calls in core ---------------------------------------------------------------
def tail(text: str | list, n: int = 5, width: int = 200) -> str:
    lines = text.splitlines() if isinstance(text, str) else list(text)
    lines = [ln.rstrip()[:width] for ln in lines if ln.strip()]
    return " | ".join(lines[-n:])


def log_external(logger: logging.Logger, cmd: list, rc: int | None, duration: float,
                 output: str | list = "", cwd=None) -> None:
    """One INFO line for a finished external call; WARNING with the output tail on failure."""
    text = " ".join(str(c) for c in cmd)
    where = f" (cwd {cwd})" if cwd else ""
    if rc == 0:
        logger.info("ran %s%s -> rc=0 in %.1fs", text, where, duration)
    else:
        logger.warning("ran %s%s -> rc=%s in %.1fs; output tail: %s", text, where, rc, duration,
                       tail(output) or "(none)")


# ---- context header ------------------------------------------------------------------------------
def tool_commit() -> str:
    global _commit
    if _commit is None:
        repo = Path(__file__).resolve().parents[2]
        try:
            r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=10", "HEAD"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _commit = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"
        except (OSError, subprocess.SubprocessError):
            _commit = "unknown"
    return _commit


def zephyr_version(zephyr_base: Path | str | None) -> str:
    if not zephyr_base:
        return "unknown"
    vf = Path(zephyr_base) / "VERSION"
    try:
        kv = dict(re.findall(r"^\s*(\w+)\s*=\s*(.*?)\s*$", vf.read_text(encoding="utf-8"), re.M))
    except OSError:
        return "unknown"
    ver = ".".join(kv.get(k, "0") for k in ("VERSION_MAJOR", "VERSION_MINOR", "PATCHLEVEL"))
    return ver + (("-" + kv["EXTRAVERSION"]) if kv.get("EXTRAVERSION") else "")


def atdf_version(path: Path | str) -> str:
    """Best effort: schema-version attribute of the ATDF root plus a DFP version folder in the path."""
    parts = []
    try:
        head = Path(path).read_bytes()[:8192].decode("utf-8", "replace")
        m = re.search(r"<avr-tools-device-file[^>]*?schema-version=\"([^\"]+)\"", head)
        if m:
            parts.append("schema " + m.group(1))
    except OSError:
        pass
    m = re.search(r"[\\/](\d+\.\d+\.\d+)[\\/]", str(path))
    if m:
        parts.append("pack " + m.group(1))
    return ", ".join(parts) or "no version information in the file"


def _nicegui_version() -> str:
    try:
        from importlib.metadata import version
        return version("nicegui")
    except Exception:
        return "not installed"


def _plugin_lines() -> list[str]:
    from . import plugins
    out = []
    for kind in plugins.KINDS:
        names = []
        for key, p in plugins._registry[kind].items():
            names.append(f"{key[0]}->{key[1]}" if kind == "translation" else str(key))
        out.append(f"plugins {kind}: {', '.join(sorted(names)) or '-'}")
    for kind, name, reason in plugins.skipped_plugins():
        out.append(f"plugin skipped: {kind} {name}: {reason}")
    return out


def context_header(build_model=None) -> list[str]:
    """Lines describing tool, environment and (if given) the open build."""
    lines = [
        f"tool commit: {tool_commit()}",
        f"Python: {platform.python_version()} ({sys.executable})",
        f"NiceGUI: {_nicegui_version()}",
    ]
    if build_model is not None:
        bdir = Path(build_model.build)
        zb = getattr(build_model, "zephyr_base", None)
        lines += [f"Zephyr: {zephyr_version(zb)}",
                  f"workspace: {getattr(build_model, 'west_top', None) or (Path(zb).parent if zb else 'unknown')}",
                  f"build folder: {bdir}"]
        info = None
        try:
            from .buildinfo import read_build_info
            info = read_build_info(bdir)
        except Exception as exc:
            lines.append(f"board/SoC: unknown ({exc})")
        if info is not None:
            lines.append(f"board: {info.board_target or info.board or 'unknown'}")
            lines.append(f"SoC: {info.soc or 'unknown'}")
            atdf = None
            try:
                from . import plugins
                prov = plugins.find_provider(info)
                if prov is None:
                    lines.append("ATDF: no provider matches this build")
                else:
                    atdf = prov.locate(info)
                    lines.append(f"ATDF: {atdf} ({atdf_version(atdf)}) via provider {prov.name}"
                                 if atdf else f"ATDF: not found (provider {prov.name})")
            except Exception as exc:
                lines.append(f"ATDF: lookup failed ({exc})")
    lines += _plugin_lines()
    return lines


def log_header(build_model=None) -> list[str]:
    """Write the context header as INFO lines (start of run, build opened); returns the lines."""
    lines = context_header(build_model)
    lg = get_logger("context")
    lg.info("---- context ----")
    for ln in lines:
        lg.info("%s", ln)
    return lines


class Timer:
    """``with Timer() as t: ...`` then ``t.seconds``."""

    def __enter__(self):
        self._t0 = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self._t0
        return False
