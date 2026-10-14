"""Golden snapshot tests: today's explorer analysis must not change by accident.

Needs DTX_RTOS4_WS (west workspace with the RTOS4 lab builds); skipped when unset.
DTX_GOLDEN_DIR (optional) points the comparison at another directory of golden files,
e.g. a modified copy, to prove that the test can fail.
Regenerate a golden on purpose:  py tools/snapshot_build.py <build> -o tests/golden/<name>.json
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
GOLDEN_DIR = Path(os.environ.get("DTX_GOLDEN_DIR") or REPO / "tests" / "golden")
WS_ENV = os.environ.get("DTX_RTOS4_WS")

pytestmark = pytest.mark.skipif(not WS_ENV, reason="DTX_RTOS4_WS not set (RTOS4 west workspace)")

FULL = {
    "lab0": r"apps\lab0\build_same54_xpro",
    "lab1": r"apps\lab1\build_same54_xpro",
    "lab3": r"apps\lab3\build_same54_xpro",
    "lab1_leer": r"_soltest\apps\lab1_leer\build_same54_xpro",
}


def _compare(name: str, build: Path):
    from snapshot_build import snapshot
    golden = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(snapshot(build)))     # same normalisation as the file
    if fresh == golden:
        return
    diffs = []

    def walk(a, b, where):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{where}[{k}]")
        elif a != b:
            diffs.append(f"{where}: golden={b!r} fresh={a!r}")
    walk(fresh, golden, "snapshot")
    pytest.fail(f"snapshot of {name} differs from golden ({len(diffs)} difference(s)):\n"
                + "\n".join(d[:300] for d in diffs[:20]))


@pytest.mark.parametrize("name", sorted(FULL))
def test_full_build(name):
    build = Path(WS_ENV) / FULL[name]
    if not (build / "zephyr" / "edt.pickle").exists():
        pytest.skip(f"build not present: {build}")
    _compare(name, build)


@pytest.fixture(scope="session")
def cmake_only_build(tmp_path_factory):
    ws = Path(WS_ENV)
    py = ws / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        pytest.skip(f"west virtualenv not found: {py}")
    d = tmp_path_factory.mktemp("dtx_cmake") / "lab1"
    r = subprocess.run([str(py), "-m", "west", "build", "-b", "same54_xpro", "--cmake-only",
                        "-d", str(d), r"apps\lab1"], cwd=ws, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("west cmake-only build failed (Zephyr SDK missing?): " + r.stderr[-300:])
    return d


def test_cmake_only_lab1(cmake_only_build):
    _compare("lab1_cmake_only", cmake_only_build)
