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


def test_list_boards_synthetic(tmp_path):
    d = tmp_path / "zephyr" / "boards" / "x" / "b1"
    d.mkdir(parents=True)
    (d / "board.yml").write_text(
        "board:\n  name: b1\n  vendor: microchip\n  socs:\n  - name: s\n    variants:\n"
        "    - name: smp\n      cpucluster: u54\n")
    o = tmp_path / "zephyr" / "boards" / "x" / "b2"
    o.mkdir()
    (o / "board.yml").write_text("board:\n  name: b2\n  vendor: other\n")
    boards = wt.list_boards(tmp_path)
    assert [b.name for b in boards] == ["b1"]
    assert boards[0].qualifiers == ["s/smp/u54"]


def test_list_boards_rtos4(rtos4_ws):
    names = {b.name for b in wt.list_boards(rtos4_ws)}
    assert {"same54_xpro", "sam_e54_xpro", "pic32cx_sg61_cult"} <= names


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
