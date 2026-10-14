"""Non-UI helpers of the New build dialog."""

import pytest

from dt_explorer.ui import new_build as nb


def _app(p):
    p.mkdir(parents=True, exist_ok=True)
    (p / "CMakeLists.txt").write_text("")
    (p / "prj.conf").write_text("")


def test_default_build_name():
    assert nb.default_build_name("same54_xpro") == "build_same54_xpro"


@pytest.mark.parametrize("name,ok", [("build_x", True), ("", False), ("  ", False), ("a b", False),
                                     ("a/b", False), ("..", False), (".hid", False), ("a\\b", False)])
def test_check_build_name(name, ok):
    assert (nb.check_build_name(name) is None) == ok


def test_find_workspace(tmp_path):
    (tmp_path / ".west").mkdir()
    sub = tmp_path / "apps" / "x"
    sub.mkdir(parents=True)
    assert nb.find_workspace(sub) == tmp_path.resolve()
    assert nb.find_workspace(None) is None


def test_find_apps(tmp_path):
    _app(tmp_path / "apps" / "lab1")
    _app(tmp_path / "_soltest" / "apps" / "lab2")
    _app(tmp_path / "modules" / "m" / "app")           # skipped tree
    _app(tmp_path / "apps" / "lab1" / "build_x" / "inner")   # inside an app: leaf, never scanned
    _app(tmp_path / "a" / "b" / "c" / "d" / "e")        # deeper than the depth limit
    (tmp_path / "apps" / "buildjunk").mkdir()
    found = [p.relative_to(tmp_path).as_posix() for p in nb.find_apps(tmp_path)]
    assert found == ["_soltest/apps/lab2", "apps/lab1"]


def test_find_apps_skips_build_dirs(tmp_path):
    _app(tmp_path / "apps" / "out")
    (tmp_path / "apps" / "out" / "zephyr").mkdir()
    (tmp_path / "apps" / "out" / "zephyr" / "edt.pickle").write_text("")
    # a folder that is a build (has zephyr/edt.pickle) is not offered as an app
    root = tmp_path / "apps"
    assert [p.name for p in nb.find_apps(tmp_path)] == []
    assert root.exists()
