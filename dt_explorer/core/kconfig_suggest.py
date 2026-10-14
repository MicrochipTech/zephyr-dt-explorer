"""Kconfig symbols a node of a given compatible needs.

Two sources, in this order:

1. The recipe registered for the compatible (``recipe.kconfig(compatible)``). This is curated
   knowledge and wins.
2. Derivation with the *workspace's own* ``kconfiglib``: the generated symbol
   ``DT_HAS_<COMPAT>_ENABLED`` is looked up, every symbol that ``depends on`` it is a driver
   symbol, and the ``depends on`` chain is followed upward to the user-visible subsystem symbols
   (``I2C``, ``ADC``, ...).

Loading the Kconfig tree of a build folder
------------------------------------------
``<ZEPHYR_BASE>/scripts/kconfig/kconfiglib.py`` parses ``<ZEPHYR_BASE>/Kconfig`` with the
environment that CMake gives ``kconfig.py`` (``srctree``, ``BOARD``, ``BOARD_DIR``,
``KCONFIG_BINARY_DIR``, ``ARCH``, ``ZEPHYR_<module>_MODULE_DIR``, ...). Everything is rebuilt
from files of the build folder: ``CMakeCache.txt``, ``Kconfig/kconfig_module_dirs.env`` and the
generated ``Kconfig/`` tree (including ``Kconfig.dts`` with the ``DT_HAS_*`` symbols). Nothing is
evaluated: the tree is only read for its dependency structure, so the devicetree is not needed
for the derivation (``zephyr/.config`` is read only to tell which suggested symbols the build
already enables). The environment and ``sys.path`` are patched for the duration of the load and
restored afterwards; the parsed tree is cached per build folder.

If the tree cannot be loaded (no build folder, no generated ``Kconfig/``, kconfiglib error) the
result carries ``source == "none"`` and a note saying why; the caller still gets the symbol
name ``DT_HAS_<COMPAT>_ENABLED`` so the user can look it up by hand.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import plugins
from .buildinfo import parse_cmake_cache, parse_config


@dataclass
class KconfigSuggestion:
    compatible: str
    symbols: dict[str, str] = field(default_factory=dict)     # "CONFIG_I2C" -> "y"
    source: str = "none"                                      # "recipe" | "kconfiglib" | "none"
    dt_symbol: str = ""                                       # "DT_HAS_<COMPAT>_ENABLED"
    chain: list[str] = field(default_factory=list)            # dependency chains, driver first
    already_enabled: list[str] = field(default_factory=list)  # suggested and set in the build's .config
    notes: list[str] = field(default_factory=list)


def dt_has_symbol(compatible: str) -> str:
    """``DT_HAS_<COMPAT>_ENABLED`` the way Zephyr's gen_kconfig_dts.py names it."""
    return "DT_HAS_" + re.sub(r"\W", "_", compatible).upper() + "_ENABLED"


# ---- loading the Kconfig tree ---------------------------------------------------------------
_tree_cache: dict[Path, Any] = {}


class KconfigLoadError(RuntimeError):
    pass


def _kconfig_env(build: Path) -> tuple[dict[str, str], Path]:
    cache_file = build / "CMakeCache.txt"
    if not cache_file.is_file():
        raise KconfigLoadError(f"no CMakeCache.txt in {build}")
    cache = parse_cmake_cache(cache_file)
    zb = cache.get("ZEPHYR_BASE")
    if not zb or not (Path(zb) / "Kconfig").is_file():
        raise KconfigLoadError(f"ZEPHYR_BASE of the build has no Kconfig: {zb!r}")
    kbin = build / "Kconfig"
    if not (kbin / "Kconfig.dts").is_file():
        raise KconfigLoadError(f"{kbin / 'Kconfig.dts'} missing (build not configured)")
    app = cache.get("APPLICATION_SOURCE_DIR", "")
    env = {
        "srctree": zb, "ZEPHYR_BASE": zb,
        "KERNELVERSION": "", "APPVERSION": "", "APP_VERSION_EXTENDED_STRING": "",
        "APP_VERSION_TWEAK_STRING": "", "APP_DIR": app, "CONFIG_": "CONFIG_",
        "KCONFIG_CONFIG": str(build / "zephyr" / ".config"),
        "KCONFIG_BOARD_DIR": str(kbin / "boards"),
        "BOARD": cache.get("BOARD", ""), "BOARD_DIR": cache.get("BOARD_DIR", ""),
        "BOARD_REVISION": cache.get("BOARD_REVISION", ""), "BOARD_QUALIFIERS": "",
        "HWM_SCHEME": "v2", "KCONFIG_BINARY_DIR": str(kbin),
        "APPLICATION_SOURCE_DIR": app,
        "ZEPHYR_TOOLCHAIN_VARIANT": cache.get("ZEPHYR_TOOLCHAIN_VARIANT", ""),
        "TOOLCHAIN_KCONFIG_DIR": cache.get("TOOLCHAIN_KCONFIG_DIR", ""),
        "TOOLCHAIN_HAS_NEWLIB": "n", "TOOLCHAIN_HAS_PICOLIBC": "y",
        "EDT_PICKLE": str(build / "zephyr" / "edt.pickle"),
        "ARCH": "*", "ARCH_DIR": str(Path(zb) / "arch"), "SOC_DIR": str(Path(zb) / "soc"),
        "SHIELD_AS_LIST": "",
    }
    env_file = kbin / "kconfig_module_dirs.env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep:
                env[k] = v
    # ZEPHYR_<module>_KCONFIG: CMake sets them from module.yml; here the module's own Kconfig
    # file by convention, else a path that does not exist (the tree uses `osource`).
    mods = kbin / "Kconfig.modules"
    if mods.is_file():
        text = mods.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"\$\((ZEPHYR_\w+_KCONFIG)\)", text):
            if name in env:
                continue
            mdir = env.get(name[:-len("_KCONFIG")] + "_MODULE_DIR", "")
            hit = next((str(Path(mdir) / rel) for rel in ("zephyr/Kconfig", "Kconfig")
                        if mdir and (Path(mdir) / rel).is_file()), None)
            env[name] = hit or str(kbin / "no-such-module-kconfig")
    return env, Path(zb)


@contextlib.contextmanager
def _patched(env: dict[str, str], paths: list[str]):
    old_env = {k: os.environ.get(k) for k in env}
    old_path = list(sys.path)
    os.environ.update(env)
    sys.path[:0] = paths
    try:
        yield
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path[:] = old_path
        sys.modules.pop("kconfigfunctions", None)


def load_kconfig(build_dir: Path | str):
    """The workspace's kconfiglib ``Kconfig`` object for a build folder (cached)."""
    build = Path(build_dir).resolve()
    if build in _tree_cache:
        return _tree_cache[build]
    env, zb = _kconfig_env(build)
    kdir = zb / "scripts" / "kconfig"
    if not (kdir / "kconfiglib.py").is_file():
        raise KconfigLoadError(f"{kdir / 'kconfiglib.py'} not found")
    # edtlib is needed by the workspace's kconfigfunctions.py
    paths = [str(kdir), str(zb / "scripts" / "dts" / "python-devicetree" / "src")]
    with _patched(env, paths):
        sys.modules.pop("kconfiglib", None)     # the workspace copy, not one imported earlier
        sys.modules.pop("kconfigfunctions", None)
        try:
            import kconfiglib  # type: ignore
            tree = kconfiglib.Kconfig(str(zb / "Kconfig"), warn=False, warn_to_stderr=False)
        except Exception as exc:  # noqa: BLE001 - report, never crash the caller
            raise KconfigLoadError(f"kconfiglib could not load the tree: {exc}") from exc
    _tree_cache[build] = tree
    return tree


# ---- derivation -----------------------------------------------------------------------------
def _visible(sym) -> bool:
    return any(n.prompt for n in sym.nodes)


def derive(tree, compatible: str) -> tuple[dict[str, str], list[str], list[str]]:
    """(symbols, chains, notes) for a compatible from a loaded Kconfig tree."""
    kl = types.SimpleNamespace(**type(tree).__init__.__globals__)   # module of the kconfiglib that built `tree`
    dt_name = dt_has_symbol(compatible)
    dt_sym = tree.syms.get(dt_name)
    if dt_sym is None or not dt_sym.nodes:
        return {}, [], [f"{dt_name} is not defined: no binding with this compatible"]

    def dep_syms(expr) -> list:
        return [s for s in kl.expr_items(expr) if isinstance(s, kl.Symbol) and not s.is_constant]

    drivers = [s for s in tree.unique_defined_syms
               if s is not dt_sym and dt_sym in kl.expr_items(s.direct_dep)]
    if not drivers:
        return {}, [], [f"no symbol depends on {dt_name}: the compatible has no driver in this workspace"]

    symbols: dict[str, str] = {}
    chains: list[str] = []

    def climb(sym, trail: list[str], seen: set) -> None:
        """Follow `depends on` upward; collect user-visible bool symbols as suggestions."""
        parents = [p for p in dep_syms(sym.direct_dep)
                   if not p.name.startswith("DT_HAS_") and p.name not in seen]
        visible = [p for p in parents if _visible(p) and p.orig_type == kl.BOOL]
        if not visible:
            chains.append(" -> ".join(trail))
            return
        for p in visible:
            symbols.setdefault("CONFIG_" + p.name, "y")
            climb(p, trail + [p.name], seen | {p.name})

    for d in drivers:
        if _visible(d):
            symbols["CONFIG_" + d.name] = "y"
        climb(d, [dt_sym.name, d.name], {d.name})
    return dict(sorted(symbols.items())), sorted(set(chains)), []


def suggest_kconfig(compatible: str, build_dir: Path | str | None = None) -> KconfigSuggestion:
    """Kconfig symbols for `compatible`: recipe table first, otherwise derived from the build's Kconfig tree."""
    out = KconfigSuggestion(compatible=compatible, dt_symbol=dt_has_symbol(compatible))
    recipe = plugins.get_recipe(compatible)
    if recipe is not None:
        try:
            table = recipe.kconfig(compatible)
        except Exception as exc:  # noqa: BLE001 - a recipe without a table falls through to derivation
            out.notes.append(f"recipe {recipe.name} has no Kconfig table for {compatible}: {exc}")
        else:
            out.symbols, out.source = dict(table), "recipe"
            out.notes.append(f"from the table of recipe {recipe.name}")
    if out.source != "recipe":
        if build_dir is None:
            out.notes.append(f"no recipe and no build folder given; look up {out.dt_symbol} by hand")
        else:
            try:
                tree = load_kconfig(build_dir)
            except KconfigLoadError as exc:
                out.notes.append(f"{exc}; look up {out.dt_symbol} by hand")
            else:
                out.symbols, out.chain, notes = derive(tree, compatible)
                out.notes += notes
                if out.symbols:
                    out.source = "kconfiglib"
    if build_dir is not None and out.symbols:
        cfg = Path(build_dir) / "zephyr" / ".config"
        if cfg.is_file():
            have = parse_config(cfg)
            out.already_enabled = sorted(k for k, v in out.symbols.items() if have.get(k) == v)
    return out
