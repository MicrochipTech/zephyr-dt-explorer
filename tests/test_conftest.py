"""Checks for the env-var fixtures in conftest.py.

The skip-path tests use monkeypatch, so they behave the same whatever the
environment is. The positive tests use the real fixtures and skip when unset.
"""

from pathlib import Path

import pytest

from conftest import ENV_PURPOSE, env_path


def test_env_path_unset_skips_with_reason(monkeypatch):
    monkeypatch.delenv("DTX_DESIGN_FILES", raising=False)
    with pytest.raises(pytest.skip.Exception, match="DTX_DESIGN_FILES not set: board design files"):
        env_path("DTX_DESIGN_FILES")


def test_env_path_blank_counts_as_unset(monkeypatch):
    monkeypatch.setenv("DTX_MAIN_WS", "   ")
    with pytest.raises(pytest.skip.Exception, match="DTX_MAIN_WS not set"):
        env_path("DTX_MAIN_WS")


def test_env_path_missing_path_skips_with_reason(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist"
    monkeypatch.setenv("DTX_RTOS4_WS", str(missing))
    with pytest.raises(pytest.skip.Exception, match="points to a missing path"):
        env_path("DTX_RTOS4_WS")


def test_env_path_existing_path_is_returned(monkeypatch, tmp_path):
    monkeypatch.setenv("DTX_RTOS4_WS", str(tmp_path))
    assert env_path("DTX_RTOS4_WS") == tmp_path


def test_all_env_vars_have_a_purpose():
    assert set(ENV_PURPOSE) == {"DTX_RTOS4_WS", "DTX_MAIN_WS", "DTX_DESIGN_FILES"}


def test_rtos4_ws_fixture(rtos4_ws):
    assert isinstance(rtos4_ws, Path)
    assert rtos4_ws.is_dir()


def test_design_files_fixture(design_files):
    assert isinstance(design_files, Path)
    assert design_files.is_dir()


def test_main_ws_fixture(main_ws):
    assert isinstance(main_ws, Path)
    assert main_ws.is_dir()


def test_rtos4_build_fixture(rtos4_build):
    build = rtos4_build("apps/lab1/build_same54_xpro")
    assert isinstance(build, Path)
    assert (build / "zephyr" / "edt.pickle").is_file()


def test_rtos4_build_missing_skips(rtos4_build):
    with pytest.raises(pytest.skip.Exception, match="zephyr/edt.pickle missing"):
        rtos4_build("apps/no_such_app/build_nothing")
