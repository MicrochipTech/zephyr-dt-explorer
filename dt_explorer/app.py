"""Startup / CLI of the Devicetree Explorer.

Start:  py dt_explorer.py [zephyrproject folder] [--no-browser] [--debug]
"""
import sys

from nicegui import ui

from .core import log as dtlog
from .core import plugins
from .ui import page as P


def load_plugins():
    """Register the built-in plugins, then external ones via entry points."""
    from . import connectors, formatters, providers, readers, recipes, translations  # noqa: F401
    plugins.discover_entry_points()


def main():
    log_file = dtlog.setup(debug="--debug" in sys.argv)
    load_plugins()
    dtlog.get_logger("app").info("Devicetree Explorer starting, log file: %s", log_file)
    dtlog.log_header()
    if P.ROOT is None:
        P.ROOT = P.ask_folder(P.last_folder())
        if P.ROOT is None:
            print("No folder selected - exiting.")
            sys.exit(0)
    P.remember_folder(P.ROOT)
    P.state.set_root(P.ROOT)
    print(f"Folder: {P.ROOT}  ->  {len(P.state.builds)} build(s) found")
    dtlog.get_logger("app").info("folder %s: %d build(s) found", P.ROOT, len(P.state.builds))
    ui.run(title="Devicetree Explorer", port=8765, reload=False, show=P.SHOW_BROWSER, dark=True)
