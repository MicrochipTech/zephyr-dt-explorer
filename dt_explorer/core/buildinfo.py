"""What a Zephyr build folder says about its SoC, board and west workspace.

Reads only plain files: `zephyr/.config`, `CMakeCache.txt`, `zephyr_modules.txt`, and the
`soc.yml` / `board.yml` files of the workspace. No edtlib, no GUI toolkit, no providers.

Later code (providers, plugins) takes a `BuildInfo` and decides for itself what to do with it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_CONFIG_LINE = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
_CACHE_LINE = re.compile(r"^([^:=#]+)(?::[A-Za-z]+)?=(.*)$")
_MODULE_LINE = re.compile(r'^"([^"]*)":"([^"]*)":"([^"]*)"\s*$')


@dataclass
class BuildInfo:
    build_dir: Path
    soc: str | None = None                  # CONFIG_SOC, e.g. "same54p20a"
    soc_series: str | None = None           # CONFIG_SOC_SERIES, e.g. "same54"
    soc_family: str | None = None           # CONFIG_SOC_FAMILY, e.g. "atmel_sam0"
    board: str | None = None                # CONFIG_BOARD, e.g. "same54_xpro"
    board_target: str | None = None         # CONFIG_BOARD_TARGET, e.g. "same54_xpro/same54p20a"
    board_qualifiers: str | None = None     # CONFIG_BOARD_QUALIFIERS, e.g. "same54p20a"
    board_revision: str | None = None       # CONFIG_BOARD_REVISION, None when empty
    vendor: str | None = None               # from board.yml, else soc.yml
    vendor_source: str | None = None        # "board.yml", "soc.yml" or None
    soc_yml: Path | None = None             # the soc.yml that lists `soc`
    board_yml: Path | None = None           # the board.yml of `board`
    zephyr_base: Path | None = None         # CMakeCache ZEPHYR_BASE
    workspace: Path | None = None           # zephyr_base.parent (west workspace root)
    app_source_dir: Path | None = None      # CMakeCache APPLICATION_SOURCE_DIR
    modules: dict[str, Path] = field(default_factory=dict)  # zephyr_modules.txt name -> path


class BuildInfoError(Exception):
    """The build folder is not a usable Zephyr build (e.g. no zephyr/.config)."""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def parse_config(path: Path) -> dict[str, str]:
    """Return every `CONFIG_X=value` line of a Kconfig .config file (value unquoted)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _CONFIG_LINE.match(line.strip())
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def parse_cmake_cache(path: Path) -> dict[str, str]:
    """Return the `NAME:TYPE=value` entries of CMakeCache.txt as NAME -> value."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _CACHE_LINE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def parse_zephyr_modules(path: Path) -> dict[str, Path]:
    """Return the module directories of zephyr_modules.txt (lines: "name":"path":"cmake path")."""
    modules: dict[str, Path] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _MODULE_LINE.match(line.strip())
        if match:
            modules[match.group(1)] = Path(os.path.normpath(match.group(2)))
    return modules


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _find_soc_yml(soc: str, roots: list[Path]) -> Path | None:
    """First soc.yml under the given soc/ roots whose family/series/socs list names `soc`."""
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob("soc.yml")):
            # Cheap pre-check: most soc.yml files do not mention the SoC at all.
            if soc not in candidate.read_text(encoding="utf-8", errors="replace"):
                continue
            if _soc_vendor_entry(_load_yaml(candidate), soc) is not None:
                return candidate
    return None


def _soc_vendor_entry(data: dict, soc: str) -> dict | None:
    """Return the family entry of soc.yml whose series lists `soc`, or None."""
    for family in data.get("family") or []:
        for series in (family or {}).get("series") or []:
            for entry in (series or {}).get("socs") or []:
                if isinstance(entry, dict) and entry.get("name") == soc:
                    return family
    return None


def _find_board_yml(board: str, roots: list[Path]) -> Path | None:
    """First board.yml whose board/name is `board`, searched under the given boards/ roots."""
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob("board.yml")):
            if candidate.parent.name != board:
                continue
            data = _load_yaml(candidate).get("board") or {}
            if isinstance(data, dict) and data.get("name") == board:
                return candidate
    return None


def read_build_info(build_dir: str | os.PathLike) -> BuildInfo:
    """Collect SoC, board, vendor and workspace facts of one Zephyr build folder.

    Raises BuildInfoError when `zephyr/.config` is missing. Every other source is optional:
    a missing CMakeCache, module list or soc.yml/board.yml leaves the matching fields None.
    """
    build = Path(build_dir)
    config_path = build / "zephyr" / ".config"
    if not config_path.is_file():
        raise BuildInfoError(f"no zephyr/.config in {build} (build not configured yet?)")

    config = parse_config(config_path)
    info = BuildInfo(
        build_dir=build,
        soc=config.get("CONFIG_SOC") or None,
        soc_series=config.get("CONFIG_SOC_SERIES") or None,
        soc_family=config.get("CONFIG_SOC_FAMILY") or None,
        board=config.get("CONFIG_BOARD") or None,
        board_target=config.get("CONFIG_BOARD_TARGET") or None,
        board_qualifiers=config.get("CONFIG_BOARD_QUALIFIERS") or None,
        board_revision=config.get("CONFIG_BOARD_REVISION") or None,
    )

    cache_path = build / "CMakeCache.txt"
    cache = parse_cmake_cache(cache_path) if cache_path.is_file() else {}
    if cache.get("ZEPHYR_BASE"):
        info.zephyr_base = Path(os.path.normpath(cache["ZEPHYR_BASE"]))
        info.workspace = info.zephyr_base.parent
    if cache.get("APPLICATION_SOURCE_DIR"):
        info.app_source_dir = Path(os.path.normpath(cache["APPLICATION_SOURCE_DIR"]))

    modules_path = build / "zephyr_modules.txt"
    if modules_path.is_file():
        info.modules = parse_zephyr_modules(modules_path)

    module_dirs = list(info.modules.values())
    zephyr_root = [info.zephyr_base] if info.zephyr_base else []

    if info.soc:
        soc_roots = [p / "soc" for p in zephyr_root + module_dirs]
        info.soc_yml = _find_soc_yml(info.soc, soc_roots)

    if info.board:
        board_dir = cache.get("BOARD_DIR")
        if board_dir and (Path(board_dir) / "board.yml").is_file():
            candidate = Path(os.path.normpath(board_dir)) / "board.yml"
            if (_load_yaml(candidate).get("board") or {}).get("name") == info.board:
                info.board_yml = candidate
        if info.board_yml is None:
            board_roots = [p / "boards" for p in zephyr_root + module_dirs]
            info.board_yml = _find_board_yml(info.board, board_roots)

    if info.board_yml is not None:
        board = _load_yaml(info.board_yml).get("board") or {}
        if board.get("vendor"):
            info.vendor = str(board["vendor"])
            info.vendor_source = "board.yml"
    if info.vendor is None and info.soc_yml is not None:
        data = _load_yaml(info.soc_yml)
        family = _soc_vendor_entry(data, info.soc) or {}
        vendor = family.get("vendor") or data.get("vendor")
        if vendor:
            info.vendor = str(vendor)
            info.vendor_source = "soc.yml"

    return info
