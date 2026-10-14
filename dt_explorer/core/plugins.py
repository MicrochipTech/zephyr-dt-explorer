"""Plugin registry: protocols, decorators, lookup, entry-point discovery.

Plugins declare ``api_version``. A plugin whose version differs from
``API_VERSION`` is skipped with a logged reason, never raised, so community
plugins cannot break the tool.

The core imports nothing from the plugin packages; plugins import this module
and register themselves.
"""
from __future__ import annotations

import logging
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from .device import DeviceModel, Instance, Signal

if TYPE_CHECKING:  # pragma: no cover - names only used in annotations
    BuildInfo = Any
    BoardNetlist = Any

log = logging.getLogger("dt_explorer.plugins")

API_VERSION = 1

KINDS = ("provider", "formatter", "recipe", "translation", "board_reader", "connector")

ENTRY_POINT_GROUPS = {
    "provider": "dt_explorer.providers",
    "formatter": "dt_explorer.formatters",
    "recipe": "dt_explorer.recipes",
    "translation": "dt_explorer.translations",
    "board_reader": "dt_explorer.board_readers",
    "connector": "dt_explorer.connectors",
}


@runtime_checkable
class DeviceDataProvider(Protocol):
    api_version: int
    name: str
    def matches(self, info: BuildInfo) -> bool: ...
    def locate(self, info: BuildInfo) -> Path | None: ...
    def load(self, path: Path) -> DeviceModel: ...


@runtime_checkable
class PinctrlFormatter(Protocol):
    api_version: int
    name: str
    compatibles: list[str]
    def to_overlay(self, signal: Signal) -> str: ...
    def from_overlay(self, value: str) -> str: ...
    def render_group(self, signals: list[Signal]) -> str: ...


@runtime_checkable
class Recipe(Protocol):
    api_version: int
    name: str
    compatibles: list[str]
    def pin_roles(self, instance: Instance) -> list[str]: ...
    def derived_props(self, assignment: dict) -> dict: ...
    def kconfig(self) -> dict[str, str]: ...
    def lift(self, props: dict, pins: dict) -> dict[str, str]: ...


@runtime_checkable
class BindingTranslation(Protocol):
    api_version: int
    name: str
    source: str                     # source compatible
    target: str                     # target compatible
    def translate(self, props: dict) -> tuple[dict, list[str]]:
        """Return (target props, report lines for unlisted or dropped properties)."""
        ...


@runtime_checkable
class BoardReader(Protocol):
    api_version: int
    name: str
    extensions: list[str]           # lower case with dot, e.g. [".pcbdoc"]
    def read(self, path: Path) -> BoardNetlist: ...


@runtime_checkable
class ConnectorType(Protocol):
    api_version: int
    name: str
    zephyr_binding: str             # e.g. "arduino-header-r3"
    def pin_roles(self) -> dict[str, str]: ...                             # connector pin -> role
    def detect(self, netlist_pins: dict[str, str | None]) -> float: ...    # confidence 0..1


_registry: dict[str, dict[Any, Any]] = {k: {} for k in KINDS}
_skipped: list[tuple[str, str, str]] = []   # (kind, plugin name, reason)


def _key(kind: str, plugin: Any) -> Any:
    if kind == "translation":
        return (plugin.source, plugin.target)
    return plugin.name


def register(kind: str, plugin: Any) -> bool:
    """Register a plugin instance. Returns False (and logs why) if it is skipped."""
    if kind not in _registry:
        raise ValueError(f"unknown plugin kind {kind!r}")
    name = getattr(plugin, "name", None) or type(plugin).__name__
    ver = getattr(plugin, "api_version", None)
    if ver != API_VERSION:
        reason = f"api_version {ver!r} incompatible with core api_version {API_VERSION}"
        log.warning("skipping %s plugin %s: %s", kind, name, reason)
        _skipped.append((kind, name, reason))
        return False
    _registry[kind][_key(kind, plugin)] = plugin
    return True


def _decorator(kind: str) -> Callable:
    def deco(obj):
        # Classes are instantiated without arguments; instances are taken as is.
        register(kind, obj() if isinstance(obj, type) else obj)
        return obj
    return deco


register_provider = _decorator("provider")
register_formatter = _decorator("formatter")
register_recipe = _decorator("recipe")
register_translation = _decorator("translation")
register_board_reader = _decorator("board_reader")
register_connector = _decorator("connector")


def unregister_all() -> None:
    """Clear the registry (for tests)."""
    for d in _registry.values():
        d.clear()
    _skipped.clear()


def discover_entry_points() -> int:
    """Load plugins from the dt_explorer.* entry-point groups; return how many registered.

    A plugin that fails to load is logged and skipped, never raised.
    """
    count = 0
    for kind, group in ENTRY_POINT_GROUPS.items():
        for ep in metadata.entry_points(group=group):
            try:
                obj = ep.load()
                plugin = obj() if isinstance(obj, type) else obj
            except Exception as exc:  # noqa: BLE001 - a bad plugin must not break the core
                reason = f"failed to load: {exc!r}"
                log.warning("skipping %s plugin %s: %s", kind, ep.name, reason)
                _skipped.append((kind, ep.name, reason))
                continue
            if register(kind, plugin):
                count += 1
    return count


# Lookup -----------------------------------------------------------------------

def providers() -> list[DeviceDataProvider]:
    return list(_registry["provider"].values())

def formatters() -> list[PinctrlFormatter]:
    return list(_registry["formatter"].values())

def recipes() -> list[Recipe]:
    return list(_registry["recipe"].values())

def translations() -> list[BindingTranslation]:
    return list(_registry["translation"].values())

def board_readers() -> list[BoardReader]:
    return list(_registry["board_reader"].values())

def connector_types() -> list[ConnectorType]:
    return list(_registry["connector"].values())


def get_provider(name: str) -> DeviceDataProvider | None:
    return _registry["provider"].get(name)

def find_provider(info: BuildInfo) -> DeviceDataProvider | None:
    """First registered provider whose matches(info) is true."""
    for p in providers():
        if p.matches(info):
            return p
    return None

def get_formatter(compatible: str) -> PinctrlFormatter | None:
    for f in formatters():
        if compatible in f.compatibles:
            return f
    return None

def get_recipe(compatible: str) -> Recipe | None:
    for r in recipes():
        if compatible in r.compatibles:
            return r
    return None

def get_translation(source: str, target: str) -> BindingTranslation | None:
    return _registry["translation"].get((source, target))

def get_board_reader(path: Path | str) -> BoardReader | None:
    ext = Path(path).suffix.lower()
    for r in board_readers():
        if ext in r.extensions:
            return r
    return None

def get_connector_type(name: str) -> ConnectorType | None:
    return _registry["connector"].get(name)


def skipped_plugins() -> list[tuple[str, str, str]]:
    """(kind, name, reason) of rejected plugins; for the UI."""
    return list(_skipped)
