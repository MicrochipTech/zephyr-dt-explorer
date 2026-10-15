"""Server launcher for the smoke tests: ``py _serve.py <port> <folder> <drafts folder>``.

Starts the Explorer exactly like ``py dt_explorer.py <folder> --no-browser``, but

* on the given port (``app.main`` hard-codes 8765, which stays untouched),
* without remembering the folder in ``~/.dt_explorer.json``,
* with drafts read and written below <drafts folder> instead of the build's app folder.

Log files go to the folder named by the env var DTX_LOG_DIR (set by the tests).
"""
import sys
from pathlib import Path

port, folder, drafts = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.argv = [sys.argv[0], folder, "--no-browser"]

from nicegui import ui  # noqa: E402

_run = ui.run
ui.run = lambda *a, **k: _run(*a, **{**k, "port": port})

from dt_explorer.core import draft as draft_mod  # noqa: E402
from dt_explorer.ui import configure  # noqa: E402
from dt_explorer.ui import page as P  # noqa: E402

P.remember_folder = lambda p: None
configure.load_draft = lambda app_dir, build_dir: draft_mod.load_draft(drafts, build_dir)
configure.save_draft = lambda d, app_dir, build_dir: draft_mod.save_draft(d, drafts, build_dir)

from dt_explorer.app import main  # noqa: E402

if __name__ in {"__main__", "__mp_main__"}:
    main()
