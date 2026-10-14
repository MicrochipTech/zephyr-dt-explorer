"""Several edtlib versions in one process (P6.0 approach c).

``edt.pickle`` stores edtlib objects, and a source build (Zephyr v4.3.0) and a target build (current
Zephyr) need different edtlib code. An ``EdtlibHost`` imports the ``devicetree`` package of one
workspace, keeps its module objects, and removes every ``devicetree*`` entry from ``sys.modules`` and
its ``src`` dir from ``sys.path`` again. Unpickling maps ``devicetree.*`` classes to the host's own
modules, so hosts of different workspaces coexist and nothing global named ``devicetree`` is left.
"""

from __future__ import annotations

import importlib
import pickle
import sys
import threading
from pathlib import Path

_SUBMODULES = ("devicetree", "devicetree._private", "devicetree.dtlib", "devicetree.grutils",
               "devicetree.edtlib")
_LOCK = threading.RLock()
_HOSTS: dict[Path, "EdtlibHost"] = {}


def _is_dt(name: str) -> bool:
    return name == "devicetree" or name.startswith("devicetree.")


class EdtlibHost:
    """The ``devicetree`` package of one workspace plus an unpickler bound to it."""

    def __init__(self, src: Path):
        self.src = src
        self.modules: dict[str, object] = {}
        with _LOCK:
            saved = {k: sys.modules.pop(k) for k in [k for k in sys.modules if _is_dt(k)]}
            sys.path.insert(0, str(src))
            try:
                for name in _SUBMODULES:
                    importlib.import_module(name)
                self.modules = {k: v for k, v in sys.modules.items() if _is_dt(k)}
            finally:
                sys.path.remove(str(src))
                for k in [k for k in sys.modules if _is_dt(k)]:
                    del sys.modules[k]
                sys.modules.update(saved)    # whatever a caller had before stays as it was

    @classmethod
    def for_zephyr_base(cls, zephyr_base: Path) -> "EdtlibHost":
        """Host for ``<zephyr_base>/scripts/dts/python-devicetree/src``, cached per directory."""
        src = (Path(zephyr_base) / "scripts" / "dts" / "python-devicetree" / "src").resolve()
        with _LOCK:
            host = _HOSTS.get(src)
            if host is None:
                if not (src / "devicetree" / "edtlib.py").is_file():
                    raise FileNotFoundError(f"no devicetree/edtlib.py under {src}")
                host = _HOSTS[src] = cls(src)
            return host

    @property
    def edtlib(self):
        return self.modules["devicetree.edtlib"]

    @property
    def dtlib(self):
        return self.modules["devicetree.dtlib"]

    def load_edt(self, pickle_path: Path):
        """Unpickle ``edt.pickle`` with this host's edtlib classes."""
        modules = self.modules

        class _Unpickler(pickle.Unpickler):
            def find_class(self, module, name):
                if _is_dt(module):
                    return getattr(modules[module], name)
                return super().find_class(module, name)

        with open(pickle_path, "rb") as f:
            return _Unpickler(f).load()
