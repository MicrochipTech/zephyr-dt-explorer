"""Main file for the NiceGUI user simulation: registers the Explorer page and runs the simulated app.

Executed by ``nicegui.testing.user_simulation`` through ``runpy`` (see ``conftest.py``). The page module
is imported fresh every time, because the simulation drops page modules from ``sys.modules`` on exit.
"""
import sys

sys.modules.pop("dt_explorer.ui.page", None)

from nicegui import ui  # noqa: E402

from dt_explorer.app import load_plugins  # noqa: E402
import importlib  # noqa: E402

# import_module, not "from dt_explorer.ui import page": the package may still carry a stale `page` attribute
importlib.import_module("dt_explorer.ui.page")      # registers @ui.page("/")

load_plugins()
ui.run(storage_secret="dtx-ui-tests", dark=True)
