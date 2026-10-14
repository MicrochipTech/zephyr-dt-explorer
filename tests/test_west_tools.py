"""Tests for dt_explorer.core.west_tools (workspace tests need DTX_RTOS4_WS)."""

import sys
import time

from dt_explorer.core import west_tools as wt


def test_runner_streams_and_returns_code(tmp_path):
    lines = []
    r = wt.WestRun([sys.executable, "-c", "print('a'); print('b'); raise SystemExit(3)"],
                   tmp_path, lines.append)
    assert r.wait(30) == 3
    assert lines == ["a", "b"]


def test_runner_cancel(tmp_path):
    r = wt.WestRun([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path)
    time.sleep(1.0)
    r.cancel()
    assert r.wait(30) not in (0, None)
    assert r.cancelled


def test_runner_missing_executable(tmp_path):
    r = wt.WestRun([tmp_path / "nope.exe"], tmp_path)
    assert r.wait(10) == -1
    assert "cannot start" in r.log


def _two_vendor_ws(tmp_path):
    d = tmp_path / "zephyr" / "boards" / "x" / "b1"
    d.mkdir(parents=True)
    (d / "board.yml").write_text(
        "board:\n  name: b1\n  vendor: microchip\n  socs:\n  - name: s\n    variants:\n"
        "    - name: smp\n      cpucluster: u54\n")
    o = tmp_path / "zephyr" / "boards" / "x" / "b2"
    o.mkdir()
    (o / "board.yml").write_text("board:\n  name: b2\n  vendor: other\n")
    return tmp_path


def test_list_boards_synthetic(tmp_path):
    ws = _two_vendor_ws(tmp_path)
    boards = wt.list_boards(ws, vendors=("microchip",))
    assert [b.name for b in boards] == ["b1"]
    assert boards[0].qualifiers == ["s/smp/u54"]


def test_list_boards_vendors_none_means_all(tmp_path):
    ws = _two_vendor_ws(tmp_path)
    assert [b.name for b in wt.list_boards(ws)] == ["b1", "b2"]
    assert [b.name for b in wt.list_boards(ws, vendors=None)] == ["b1", "b2"]
    assert wt.list_boards(ws, vendors=()) == []


def test_list_boards_rtos4(rtos4_ws):
    names = {b.name for b in wt.list_boards(rtos4_ws)}
    assert {"same54_xpro", "sam_e54_xpro", "pic32cx_sg61_cult"} <= names


def test_list_boards_rtos4_default_vendors_from_providers(rtos4_ws):
    from dt_explorer.app import load_plugins
    from dt_explorer.core import plugins
    load_plugins()
    vendors = plugins.board_vendors()
    assert vendors == ("atmel", "microchip")
    assert len(wt.list_boards(rtos4_ws, vendors=vendors)) == 27
    assert len(wt.list_boards(rtos4_ws)) >= 27


def test_cmake_only_build_and_failure(rtos4_ws, tmp_path):
    lines = []
    r = wt.cmake_only_build(rtos4_ws, rtos4_ws / "apps" / "lab1", "same54_xpro",
                            tmp_path / "b", lines.append)
    assert r.wait(300) == 0, r.log[-2000:]
    assert (tmp_path / "b" / "zephyr" / "edt.pickle").is_file()
    assert lines
    bad = wt.cmake_only_build(rtos4_ws, rtos4_ws / "apps" / "lab1", "no_such_board_xyz",
                              tmp_path / "bad")
    assert bad.wait(300) != 0
    assert "no_such_board_xyz" in bad.log


def test_board_vendors_is_sorted_union_of_providers():
    from dt_explorer.core import plugins

    class _P:
        api_version = plugins.API_VERSION

        def __init__(self, name, vendors):
            self.name, self.board_vendors = name, vendors

    saved = dict(plugins._registry["provider"])
    try:
        plugins._registry["provider"].clear()
        assert plugins.board_vendors() == ()
        plugins.register("provider", _P("a", ("Zeta", "alpha")))
        plugins.register("provider", _P("b", ("alpha",)))
        plugins.register("provider", _P("c", None))          # declares nothing
        assert plugins.board_vendors() == ("alpha", "zeta")
    finally:
        plugins._registry["provider"].clear()
        plugins._registry["provider"].update(saved)
