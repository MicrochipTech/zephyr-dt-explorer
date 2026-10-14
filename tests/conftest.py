"""Shared pytest fixtures.

Tests that need data outside the repo (west workspaces, board design files,
build folders) read their paths from environment variables. When a variable is
unset or its path is missing, the test is skipped with the reason, never failed.

    DTX_RTOS4_WS      RTOS4 west workspace (Zephyr v4.3.0), e.g. C:\\Masters\\26020_RTOS4\\zephyrproject
    DTX_MAIN_WS       main west workspace (current Zephyr), created at P6.1
    DTX_DESIGN_FILES  board design files (not in repo), e.g. C:\\Masters\\board_design_files
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PURPOSE = {
    "DTX_RTOS4_WS": "RTOS4 west workspace (Zephyr v4.3.0)",
    "DTX_MAIN_WS": "main west workspace (current Zephyr, created at P6.1)",
    "DTX_DESIGN_FILES": "board design files (not in the repo)",
}


def env_path(var: str) -> Path:
    """Return the path named by env var `var`, or skip the calling test with a reason."""
    purpose = ENV_PURPOSE[var]
    value = os.environ.get(var, "").strip()
    if not value:
        pytest.skip(f"{var} not set: {purpose}")
    path = Path(value)
    if not path.exists():
        pytest.skip(f"{var} points to a missing path: {path} ({purpose})")
    return path


@pytest.fixture(scope="session")
def rtos4_ws() -> Path:
    """RTOS4 west workspace root (env DTX_RTOS4_WS)."""
    return env_path("DTX_RTOS4_WS")


@pytest.fixture(scope="session")
def main_ws() -> Path:
    """Main west workspace root (env DTX_MAIN_WS)."""
    return env_path("DTX_MAIN_WS")


@pytest.fixture(scope="session")
def design_files() -> Path:
    """Board design files folder (env DTX_DESIGN_FILES)."""
    return env_path("DTX_DESIGN_FILES")


@pytest.fixture(scope="session")
def rtos4_build(rtos4_ws: Path):
    """Callable: rtos4_build("apps/lab1/build_same54_xpro") -> build folder Path.

    Skips when the build folder has no zephyr/edt.pickle (not configured/built).
    """

    def get(rel: str) -> Path:
        build = rtos4_ws / rel
        pickle = build / "zephyr" / "edt.pickle"
        if not pickle.is_file():
            pytest.skip(f"no build at {build} (zephyr/edt.pickle missing)")
        return build

    return get


@pytest.fixture(autouse=True)
def _restore_plugin_registry():
    """Tests may clear the plugin registry; restore it so test order does not matter."""
    from dt_explorer.core import plugins
    saved = {k: dict(d) for k, d in plugins._registry.items()}
    saved_skipped = list(plugins._skipped)
    yield
    for k, d in saved.items():
        # built-in plugins registered during the test (first import) stay; test fakes go
        builtin = {key: p for key, p in plugins._registry[k].items()
                   if type(p).__module__.startswith("dt_explorer.")}
        plugins._registry[k].clear()
        plugins._registry[k].update(builtin)
        plugins._registry[k].update(d)
    plugins._skipped[:] = saved_skipped
