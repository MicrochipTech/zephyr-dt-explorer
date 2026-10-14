"""Startup / CLI of the Devicetree Explorer.

Start:  py dt_explorer.py [zephyrproject folder] [--no-browser]
"""
import sys

from nicegui import ui

from .ui import page as P


def main():
    if P.ROOT is None:
        P.ROOT = P.ask_folder(P.last_folder())
        if P.ROOT is None:
            print("No folder selected - exiting.")
            sys.exit(0)
    P.remember_folder(P.ROOT)
    P.state.set_root(P.ROOT)
    print(f"Folder: {P.ROOT}  ->  {len(P.state.builds)} build(s) found")
    ui.run(title="Devicetree Explorer", port=8765, reload=False, show=P.SHOW_BROWSER, dark=True)
