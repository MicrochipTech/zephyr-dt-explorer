"""Fixtures of the UI test layer (P8.2).

Two kinds of tests live here:

* ``ui``    simulated: ``nicegui.testing`` user simulation, no browser, no server, no port.
* ``smoke`` real browser: Playwright + Chrome against an own server on a free port, with screenshots.

Both skip with a reason when NiceGUI's testing support, Playwright/Chrome or the DTX_* variables are
missing. Nothing is ever written into the workspace's app folders: drafts are redirected to copies under
%TEMP%. Elements are found by their ``id=`` prop (see ``ui_helpers.py``).
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))      # ui_helpers

try:                                                            # the testing extra of NiceGUI
    from nicegui.testing import general_fixtures as _ngf
    from nicegui.testing.user_simulation import user_simulation  # noqa: F401
    HAVE_NICEGUI_TESTING = True
except Exception:                                               # noqa: BLE001 - any import problem = skip
    _ngf = None
    HAVE_NICEGUI_TESTING = False


def pytest_configure(config):
    """Markers, and NiceGUI's session storage in a temp folder (keeps `.nicegui/` out of the repo)."""
    if HAVE_NICEGUI_TESTING:
        _ngf.pytest_configure(config)


@pytest.fixture
def ui_run(caplog):
    """Run an async scenario: ``ui_run(scenario)``; fails on ERROR log records (as NiceGUI's own plugin does)."""
    if not HAVE_NICEGUI_TESTING:
        pytest.skip("nicegui.testing is not available (pip install -r requirements-dev.txt)")

    def run(scenario):
        asyncio.run(scenario())
        errors = [r for r in caplog.get_records("call") if r.levelname == "ERROR"]
        if errors:
            pytest.fail("unexpected ERROR log records: " + "; ".join(r.getMessage()[:200] for r in errors),
                        pytrace=False)
    return run


@pytest.fixture(autouse=True)
def _fresh_sessions():
    """Configurator sessions are cached per build folder; every test starts without them."""
    from dt_explorer.ui import configure
    configure._sessions.clear()
    yield
    configure._sessions.clear()


def redirect_drafts(monkeypatch, folder: Path) -> Path:
    """Make the configurator read and write drafts below `folder` instead of the build's app folder."""
    from dt_explorer.core import draft as draft_mod
    from dt_explorer.ui import configure

    monkeypatch.setattr(configure, "load_draft",
                        lambda app_dir, build_dir: draft_mod.load_draft(folder, build_dir))
    monkeypatch.setattr(configure, "save_draft",
                        lambda d, app_dir, build_dir: draft_mod.save_draft(d, folder, build_dir))
    return folder


@pytest.fixture
def lab1(rtos4_build, rtos4_ws, monkeypatch, tmp_path):
    """(root, build) of the real lab1 app, read only. Its drafts are not read and not written."""
    build = rtos4_build("apps/lab1/build_same54_xpro")
    redirect_drafts(monkeypatch, tmp_path / "drafts")           # an empty draft store, never apps\lab1
    return build.parent, build


@pytest.fixture
def lab1_leer_copy(rtos4_build, monkeypatch):
    """Copy of _soltest/apps/lab1_leer (app and build folder) under %TEMP%; drafts land in the copy.

    The copy's CMakeCache still names the original as APPLICATION_SOURCE_DIR, so the draft store is
    redirected to the copy explicitly. Returns (app copy, build copy).
    """
    build = rtos4_build("_soltest/apps/lab1_leer/build_same54_xpro")
    work = Path(tempfile.mkdtemp(prefix="dtx_ui_leer_"))
    app = work / "lab1_leer"
    shutil.copytree(build.parent, app)
    redirect_drafts(monkeypatch, app)
    yield app, app / build.name
    shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="session")
def lab1_e54(rtos4_ws):
    r"""cmake-only build of lab1 for sam_e54_xpro in %TEMP%\dtx_lab1_e54 (recreated when missing, as P5.4 did)."""
    import subprocess
    target = Path(tempfile.gettempdir()) / "dtx_lab1_e54"
    if not (target / "zephyr" / "edt.pickle").is_file():
        python = rtos4_ws / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            pytest.skip(f"cannot recreate {target}: {python} missing")
        proc = subprocess.run([str(python), "-m", "west", "build", "-b", "sam_e54_xpro", "--cmake-only",
                               "-d", str(target), str(Path("apps") / "lab1")],
                              cwd=rtos4_ws, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not (target / "zephyr" / "edt.pickle").is_file():
            pytest.skip(f"cannot recreate {target}: west rc={proc.returncode}: {proc.stderr[-300:]}")
    return target


# ---- smoke tests: own server on a free port + Playwright/Chrome ---------------------------------------
SHOT_DIR = Path(tempfile.gettempdir()) / "dtx_ui_shots"
SHOTS: list[Path] = []


def pytest_terminal_summary(terminalreporter):
    if SHOTS:
        terminalreporter.section("UI smoke screenshots")
        for p in SHOTS:
            terminalreporter.write_line(str(p))


@pytest.fixture(scope="session")
def chrome():
    """A Playwright Chromium driving the installed Chrome; skips when Playwright or Chrome is missing."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (requirements-dev.txt)")
    pw = sync_api.sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                                # noqa: BLE001 - no Chrome / driver problem
            pytest.skip(f"Chrome cannot be started through Playwright: {str(exc).splitlines()[0][:150]}")
        yield browser
        browser.close()
    finally:
        pw.stop()


@pytest.fixture
def page(chrome):
    context = chrome.new_context(viewport={"width": 1500, "height": 1000})
    pg = context.new_page()
    pg.set_default_timeout(20000)
    errors: list[str] = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.console_errors = errors
    yield pg
    context.close()


@pytest.fixture
def shot():
    r"""``shot(page, "name")`` saves %TEMP%\dtx_ui_shots\<name>.png and returns the path."""
    def take(pg, name: str) -> Path:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{name}.png"
        pg.screenshot(path=str(path))
        SHOTS.append(path)
        print(f"SCREENSHOT: {path}")
        return path
    return take


@pytest.fixture
def serve(tmp_path):
    """``serve(folder, drafts=None)`` -> base URL of an own Explorer server on a free port.

    The server is stopped by PID at the end of the test (never by image name); 8765/8766 are not used.
    """
    import os
    import socket
    import subprocess
    import time
    import urllib.request

    procs: list[subprocess.Popen] = []

    def start(folder: Path, drafts: Path | None = None) -> str:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        drafts = drafts or (tmp_path / "drafts")
        env = dict(os.environ, DTX_LOG_DIR=str(tmp_path / "logs"), PYTHONIOENCODING="utf-8")
        env.pop("PYTEST_CURRENT_TEST", None)                    # NiceGUI would take the child for a screen test
        log_path = tmp_path / f"server_{port}.log"
        logf = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("_serve.py")), str(port),
                                 str(folder), str(drafts)], env=env, stdout=logf, stderr=subprocess.STDOUT)
        procs.append(proc)
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if proc.poll() is not None:
                pytest.fail(f"server exited early (rc {proc.returncode}): " + log_path.read_text(encoding="utf-8")[-800:])
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(10)
                return f"http://127.0.0.1:{port}"
            except Exception:                                   # noqa: BLE001 - not up yet
                time.sleep(0.5)
        pytest.fail("server did not answer within 90 s")

    yield start
    for proc in procs:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            proc.wait(timeout=20)
