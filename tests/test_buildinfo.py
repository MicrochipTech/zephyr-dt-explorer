"""BuildInfo reader: real lab1 build (needs DTX_RTOS4_WS) and synthetic build folders (no env vars)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dt_explorer.core.buildinfo import BuildInfoError, read_build_info

REPO = Path(__file__).resolve().parent.parent


# --- real build -------------------------------------------------------------------------------


def test_lab1_real_build(rtos4_ws: Path, rtos4_build):
    build = rtos4_build(r"apps\lab1\build_same54_xpro")
    info = read_build_info(build)
    print(
        f"\nlab1: soc={info.soc} series={info.soc_series} family={info.soc_family} "
        f"board={info.board} target={info.board_target} qualifiers={info.board_qualifiers} "
        f"vendor={info.vendor} ({info.vendor_source}) workspace={info.workspace}"
    )
    assert info.soc == "same54p20a"
    assert info.soc_series == "same54"
    assert info.soc_family == "atmel_sam0"
    assert info.board == "same54_xpro"
    assert info.board_target == "same54_xpro/same54p20a"
    assert info.vendor == "atmel"
    assert info.vendor_source == "board.yml"
    assert info.board_yml is not None and info.board_yml.is_file()
    assert info.soc_yml is not None and info.soc_yml.is_file()
    assert info.workspace is not None and info.workspace.samefile(rtos4_ws)
    assert info.zephyr_base == info.workspace / "zephyr"
    assert info.app_source_dir is not None and info.app_source_dir.name == "lab1"
    assert "led" in info.modules and info.modules["led"].is_dir()


# --- synthetic build folder -------------------------------------------------------------------

CONFIG = textwrap.dedent(
    """\
    # Automatically generated file; DO NOT EDIT.
    CONFIG_BOARD="fakeboard"
    CONFIG_BOARD_REVISION=""
    CONFIG_BOARD_TARGET="fakeboard/fakesoc1"
    CONFIG_BOARD_QUALIFIERS="fakesoc1"
    CONFIG_SOC="fakesoc1"
    CONFIG_SOC_SERIES="fakeseries"
    CONFIG_SOC_FAMILY="fakefam"
    # CONFIG_NOT_SET is not set
    CONFIG_SOME_STRING="a \\"quoted\\" value"
    """
)

SOC_YML = textwrap.dedent(
    """\
    family:
    - name: fakefam
      series:
      - name: fakeseries
        socs:
        - name: fakesoc1
        - name: fakesoc2
    """
)

BOARD_YML = textwrap.dedent(
    """\
    board:
      name: fakeboard
      full_name: Fake Board
      vendor: fakevendor
      socs:
        - name: fakesoc1
    """
)


def make_synthetic(root: Path, *, board_yml: str | None = BOARD_YML, soc_yml: str | None = SOC_YML,
                   cache: bool = True, modules: bool = True, config: str | None = CONFIG) -> Path:
    """Fake west workspace under root/ws and build folder under root/ws/apps/app/build."""
    ws = root / "ws"
    zephyr = ws / "zephyr"
    module_dir = ws / "modules" / "extra"
    app = ws / "apps" / "app"
    build = app / "build"
    (build / "zephyr").mkdir(parents=True, exist_ok=True)
    if config is not None:
        (build / "zephyr" / ".config").write_text(config, encoding="utf-8")
    if soc_yml is not None:
        (zephyr / "soc" / "fakevendor_dir" / "fakefam").mkdir(parents=True, exist_ok=True)
        (zephyr / "soc" / "fakevendor_dir" / "fakefam" / "soc.yml").write_text(soc_yml, encoding="utf-8")
    if board_yml is not None:
        (zephyr / "boards" / "fakevendor_dir" / "fakeboard").mkdir(parents=True, exist_ok=True)
        (zephyr / "boards" / "fakevendor_dir" / "fakeboard" / "board.yml").write_text(board_yml, encoding="utf-8")
    if cache:
        (build / "CMakeCache.txt").write_text(
            "# comment line\n"
            f"ZEPHYR_BASE:PATH={zephyr.as_posix()}\n"
            f"APPLICATION_SOURCE_DIR:PATH={app.as_posix()}\n"
            "CMAKE_NM-ADVANCED:INTERNAL=1\n",
            encoding="utf-8",
        )
    if modules:
        module_dir.mkdir(parents=True, exist_ok=True)
        (build / "zephyr_modules.txt").write_text(
            f'"extra":"{module_dir.as_posix()}":"{module_dir.as_posix()}"\n',
            encoding="utf-8",
        )
    return build


def test_synthetic_full(tmp_path: Path):
    build = make_synthetic(tmp_path)
    info = read_build_info(build)
    assert info.soc == "fakesoc1"
    assert info.soc_series == "fakeseries"
    assert info.soc_family == "fakefam"
    assert info.board == "fakeboard"
    assert info.board_target == "fakeboard/fakesoc1"
    assert info.board_qualifiers == "fakesoc1"
    assert info.board_revision is None  # empty value -> None
    assert info.vendor == "fakevendor"
    assert info.vendor_source == "board.yml"
    assert info.board_yml == build.parents[2] / "zephyr" / "boards" / "fakevendor_dir" / "fakeboard" / "board.yml"
    assert info.soc_yml == build.parents[2] / "zephyr" / "soc" / "fakevendor_dir" / "fakefam" / "soc.yml"
    assert info.zephyr_base == build.parents[2] / "zephyr"
    assert info.workspace == build.parents[2]
    assert info.app_source_dir == build.parent
    assert info.modules == {"extra": build.parents[2] / "modules" / "extra"}


def test_config_quoting_and_unset_lines(tmp_path: Path):
    build = make_synthetic(tmp_path)
    from dt_explorer.core.buildinfo import parse_config

    values = parse_config(build / "zephyr" / ".config")
    assert values["CONFIG_SOME_STRING"] == 'a "quoted" value'
    assert "CONFIG_NOT_SET" not in values  # "# ... is not set" is not a value


def test_vendor_falls_back_to_soc_yml(tmp_path: Path):
    soc_yml = SOC_YML.replace("- name: fakefam\n", "- name: fakefam\n  vendor: socvendor\n", 1)
    build = make_synthetic(tmp_path, board_yml=None, soc_yml=soc_yml)
    info = read_build_info(build)
    assert info.board_yml is None
    assert info.vendor == "socvendor"
    assert info.vendor_source == "soc.yml"


def test_no_vendor_when_no_yml(tmp_path: Path):
    build = make_synthetic(tmp_path, board_yml=None, soc_yml=None)
    info = read_build_info(build)
    assert info.soc == "fakesoc1"
    assert info.board_yml is None and info.soc_yml is None
    assert info.vendor is None and info.vendor_source is None


def test_missing_cache_and_modules_leave_fields_empty(tmp_path: Path):
    build = make_synthetic(tmp_path, cache=False, modules=False)
    info = read_build_info(build)
    assert info.zephyr_base is None and info.workspace is None and info.app_source_dir is None
    assert info.modules == {}
    # without ZEPHYR_BASE there is no workspace to search, so board.yml is not found
    assert info.board_yml is None and info.vendor is None


def test_missing_config_raises(tmp_path: Path):
    build = make_synthetic(tmp_path, config=None)
    with pytest.raises(BuildInfoError):
        read_build_info(build)


def test_core_has_no_gui_or_provider_imports():
    import ast

    tree = ast.parse((REPO / "dt_explorer" / "core" / "buildinfo.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    forbidden = ("nicegui", "dt_explorer.ui", "dt_explorer.providers", "edtlib", "dt_explorer.core.build")
    for name in names:
        assert not name.startswith(forbidden), name
