"""Cross-workspace model host (P6.1): a v4.3.0 build and a current-Zephyr build in one process.

Needs DTX_RTOS4_WS and DTX_MAIN_WS. The main build (pic32cx_sg41_cult hello_world, cmake only) is
taken from DTX_MAIN_BUILD or %TEMP%/dtx_main_sg41 when it has an edt.pickle, else built into a session
tmp dir (about 160 s).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))


@pytest.fixture(scope="session")
def main_build(main_ws, tmp_path_factory) -> Path:
    for cand in (os.environ.get("DTX_MAIN_BUILD"), str(Path(tempfile.gettempdir()) / "dtx_main_sg41")):
        if cand and (Path(cand) / "zephyr" / "edt.pickle").is_file():
            return Path(cand)
    py = main_ws / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        pytest.skip(f"main venv not found: {py}")
    d = tmp_path_factory.mktemp("dtx_main") / "sg41"
    r = subprocess.run([str(py), "-m", "west", "build", "-b", "pic32cx_sg41_cult", "--cmake-only",
                        "-d", str(d), r"zephyr\samples\hello_world"], cwd=main_ws,
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("main cmake-only build failed: " + r.stderr[-300:])
    return d


def _check(model, label):
    n = len(model.by_path)
    assert n > 20, label
    node = next(x for x in model.edt.nodes if x.compats and "reg" in x.props)
    assert node.path in model.by_path
    st, _ = model.state(node)
    assert st in {"device", "disabled", "no_driver", "not_compiled", "no_binding", "not_linked"} or st
    return n


def _golden_match(lab1_build):
    from snapshot_build import snapshot
    golden = json.loads((REPO / "tests" / "golden" / "lab1.json").read_text(encoding="utf-8"))
    return json.loads(json.dumps(snapshot(lab1_build))) == golden


@pytest.mark.parametrize("order", ["source_first", "target_first"])
def test_two_workspaces_one_process(rtos4_build, main_build, order):
    from dt_explorer.core.build import BuildModel
    lab1 = rtos4_build("apps/lab1/build_same54_xpro")
    builds = [lab1, main_build] if order == "source_first" else [main_build, lab1]
    models = [BuildModel(b) for b in builds]
    src, tgt = (models if order == "source_first" else models[::-1])
    assert type(src.edt) is not type(tgt.edt)
    assert src.zephyr_base != tgt.zephyr_base
    _check(src, "source")
    _check(tgt, "target")
    # interleaved use after both are loaded
    for m in (src, tgt, src):
        for n in list(m.edt.nodes)[:50]:
            m.state(n)
    assert "/my_led@41008000" in src.by_path or any("my_led" in p for p in src.by_path)
    assert _golden_match(lab1)


def test_no_global_devicetree_after_loading_both(rtos4_build, main_build):
    """Fresh interpreter: other tests may import `devicetree` themselves, so check in isolation."""
    code = "\n".join([
        "import sys; sys.path.insert(0, %r)" % str(REPO),
        "from pathlib import Path",
        "from dt_explorer.core.build import BuildModel",
        "BuildModel(Path(%r)); BuildModel(Path(%r))" % (str(rtos4_build("apps/lab1/build_same54_xpro")),
                                                      str(main_build)),
        "print([k for k in sys.modules if k == 'devicetree' or k.startswith('devicetree.')],"
        " [p for p in sys.path if 'python-devicetree' in p])",
    ])
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.strip() == "[] []", r.stdout


def test_host_cached_and_exposes_modules(rtos4_ws, main_ws):
    from dt_explorer.core.model_host import EdtlibHost
    a = EdtlibHost.for_zephyr_base(rtos4_ws / "zephyr")
    assert EdtlibHost.for_zephyr_base(rtos4_ws / "zephyr") is a
    b = EdtlibHost.for_zephyr_base(main_ws / "zephyr")
    assert a is not b and a.edtlib is not b.edtlib and a.dtlib is not b.dtlib
    assert hasattr(b.edtlib, "EDT") and hasattr(a.dtlib, "DT")


def test_missing_src_raises(tmp_path):
    from dt_explorer.core.model_host import EdtlibHost
    with pytest.raises(FileNotFoundError):
        EdtlibHost.for_zephyr_base(tmp_path)
