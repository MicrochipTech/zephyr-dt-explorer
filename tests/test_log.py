"""Tests of core/log.py: log file, rotation, ring buffer, context header, log calls in core."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import env_path
from dt_explorer.core import apply as ap
from dt_explorer.core import log as dtlog

LOGGER = logging.getLogger("dt_explorer")


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DTX_LOG_DIR", str(d))
    yield d
    for h in list(LOGGER.handlers):
        LOGGER.removeHandler(h)
        h.close()
    LOGGER.setLevel(logging.NOTSET)
    dtlog.enabled = False
    dtlog._file_handler = None
    dtlog._ring = None


def test_core_log_does_not_import_nicegui():
    text = Path(dtlog.__file__).read_text(encoding="utf-8")
    import re
    assert not re.search(r"^\s*(import|from)\s+nicegui", text, re.M)


def test_setup_writes_file_in_env_dir(logdir):
    path = dtlog.setup()
    assert path.parent == logdir and path.name.startswith("dtx_") and path.suffix == ".log"
    dtlog.get_logger("t").info("hello %s", "world")
    for h in LOGGER.handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "INFO" in text and "dt_explorer.t: hello world" in text
    assert dtlog.file_path() == path


def test_default_dir_is_under_userprofile(monkeypatch):
    monkeypatch.delenv("DTX_LOG_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\someone")
    assert dtlog.default_log_dir() == Path(r"C:\Users\someone") / ".dt_explorer" / "logs"


def test_rotation(logdir):
    path = dtlog.setup(max_bytes=2000, backups=3)
    lg = dtlog.get_logger("rot")
    for i in range(200):
        lg.info("line %04d %s", i, "x" * 60)
    names = sorted(p.name for p in logdir.iterdir())
    assert path.name in names and path.name + ".1" in names
    assert not any(n.endswith(".4") for n in names)             # backupCount respected
    assert path.stat().st_size <= 2000 + 200


def test_old_day_files_are_pruned(logdir):
    logdir.mkdir(parents=True)
    for d in range(1, 8):
        (logdir / f"dtx_2020-01-0{d}.log").write_text("old\n")
    dtlog.setup(keep_files=3)
    days = sorted(p.name for p in logdir.glob("dtx_*.log"))
    assert len(days) == 3 and days[-1] == dtlog.file_path().name or dtlog.file_path().name in days


def test_ring_buffer_limit_levels_and_since(logdir):
    dtlog.setup(debug=True)
    ring = dtlog.ring()
    assert ring.buf.maxlen == 5000
    lg = dtlog.get_logger("ring")
    lg.debug("d1")
    lg.info("i1")
    lg.warning("w1")
    lg.error("multi\nline")
    got = ring.lines(logging.WARNING)
    assert len(got) == 3 and got[0].endswith(": w1") and got[1].endswith(": multi") and got[2] == "line"
    seq = ring.seq
    lg.info("later")
    assert [e[2].split(": ", 1)[1] for e in ring.since(seq)] == ["later"]
    for i in range(6000):
        lg.info("n%d", i)
    assert len(ring.buf) == 5000 and ring.buf[-1][2].endswith("n5999")


def test_debug_switch(logdir):
    dtlog.setup(debug=False)
    lg = dtlog.get_logger("sw")
    lg.debug("hidden")
    assert not any("hidden" in l for l in dtlog.ring().lines())
    dtlog.set_debug(True)
    assert dtlog.is_debug()
    lg.debug("shown")
    assert any("shown" in l for l in dtlog.ring().lines())
    dtlog.set_debug(False)
    assert not dtlog.is_debug()


def test_header_without_build():
    lines = dtlog.context_header()
    joined = "\n".join(lines)
    for key in ("tool commit:", "Python:", "NiceGUI:", "plugins provider:", "plugins formatter:",
                "plugins recipe:", "plugins translation:", "plugins connector:"):
        assert key in joined


def test_header_with_build_fields(tmp_path):
    zb = tmp_path / "ws" / "zephyr"
    zb.mkdir(parents=True)
    (zb / "VERSION").write_text("VERSION_MAJOR = 4\nVERSION_MINOR = 3\nPATCHLEVEL = 0\nEXTRAVERSION =\n")
    build = tmp_path / "ws" / "app" / "build"
    (build / "zephyr").mkdir(parents=True)
    (build / "zephyr" / ".config").write_text('CONFIG_SOC="same54p20a"\nCONFIG_BOARD="same54_xpro"\n'
                                              'CONFIG_BOARD_TARGET="same54_xpro/same54p20a"\n')
    model = SimpleNamespace(build=build, zephyr_base=zb, west_top=tmp_path / "ws")
    text = "\n".join(dtlog.context_header(model))
    assert "Zephyr: 4.3.0" in text
    assert f"workspace: {tmp_path / 'ws'}" in text
    assert f"build folder: {build}" in text
    assert "board: same54_xpro/same54p20a" in text and "SoC: same54p20a" in text
    assert "ATDF:" in text


def test_zephyr_version_unknown(tmp_path):
    assert dtlog.zephyr_version(tmp_path) == "unknown"
    assert dtlog.zephyr_version(None) == "unknown"


def test_atdf_version_from_path_and_root(tmp_path):
    f = tmp_path / "SAME54_DFP" / "3.6.4" / "atdf" / "x.atdf"
    f.parent.mkdir(parents=True)
    f.write_text('<?xml version="1.0"?>\n<avr-tools-device-file schema-version="4.4" a="b">\n')
    assert dtlog.atdf_version(f) == "schema 4.4, pack 3.6.4"


def test_log_external_levels(logdir):
    dtlog.setup()
    lg = dtlog.get_logger("ext")
    dtlog.log_external(lg, ["west", "build"], 0, 1.234)
    dtlog.log_external(lg, ["west", "build"], 2, 0.5, "a\nb\nERROR: boom\n")
    lines = dtlog.ring().lines()
    assert "ran west build -> rc=0 in 1.2s" in lines[0] and "INFO" in lines[0]
    assert "WARNING" in lines[1] and "rc=2" in lines[1] and "ERROR: boom" in lines[1]


def test_apply_writes_are_logged(logdir, tmp_path):
    dtlog.setup()
    app = tmp_path / "app"
    app.mkdir()
    plan = ap.plan_apply(app, "b/s", "&x { status = \"okay\"; };\n", "CONFIG_X=y\n")
    ap.apply(plan)
    ap.apply(ap.plan_apply(app, "b/s", "&x { status = \"disabled\"; };\n", "CONFIG_X=y\n"))   # refused
    text = "\n".join(dtlog.ring().lines())
    assert "apply: write" in text and "new file" in text
    assert "refused to overwrite" in text and "WARNING" in text


def test_crash_is_logged_with_traceback(logdir):
    dtlog.setup()
    try:
        raise ValueError("kaputt")
    except ValueError:
        dtlog.get_logger("x").exception("failed")
    text = "\n".join(dtlog.ring().lines())
    assert "Traceback" in text and "ValueError: kaputt" in text


def test_state_reasons_and_unchanged_results(logdir):
    ws = env_path("DTX_RTOS4_WS")
    from dt_explorer.core.build import BuildModel
    build = ws / "apps" / "lab1" / "build_same54_xpro"
    if not build.is_dir():
        pytest.skip(f"no lab1 build at {build}")
    quiet = BuildModel(build)
    before = {p: quiet.state(n) for p, n in quiet.by_path.items()}
    dtlog.setup(debug=True)
    loud = BuildModel(build)
    after = {p: loud.state(n) for p, n in loud.by_path.items()}
    assert before == after                                      # logging changes no return value
    text = "\n".join(dtlog.ring().lines())
    assert "dt_explorer.build: loading build" in text and "build loaded:" in text
    assert "tool commit:" in text and "Zephyr: 4.3.0" in text and "ATDF:" in text
    assert "state my_led0" in text and "device/green" in text
    assert "nobuild/orange" in text and "(compiled: none)" in text
