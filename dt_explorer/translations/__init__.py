"""Built-in binding translations.

Each ``*.yaml`` file next to this module is a translation set in the format
defined by ``dt_explorer.core.translate``. ``register_all()`` loads them and
puts every pair into the plugin registry; it also runs on import.
"""
from __future__ import annotations

from pathlib import Path

from dt_explorer.core.translate import Translation, register_file

HERE = Path(__file__).resolve().parent


def register_all() -> list[Translation]:
    """Load and register all built-in translation files; safe to call again."""
    loaded: list[Translation] = []
    for path in sorted(HERE.glob("*.yaml")):
        loaded.extend(register_file(path))
    return loaded


register_all()
