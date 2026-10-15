"""Intent-based draft: what the user wants a node to be.

The draft keeps the *intent* (class, roles, neutral properties, use) apart from
the vendor-specific realisation (instance, pin per role, vendor properties), so
it can be carried to another device. It is plain data, stored as JSON at
``<app>/.dt_explorer/<build folder name>.draft.json``.

The core knows no vendor terms: pins are opaque ids, vendor properties are
opaque dicts. Class detection walks the ``include:`` chain of a binding YAML
and maps base bindings (``uart-controller.yaml`` ...) to a class name.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

SCHEMA_VERSION = 2          # 2: nodes carry `children` (DraftChild); version 1 files are read as nodes without children
READABLE_VERSIONS = (1, 2)

# Base binding file name -> class. Table-driven; extend with register_class().
CLASS_BASES: dict[str, str] = {
    "uart-controller.yaml": "uart",
    "i2c-controller.yaml": "i2c",
    "spi-controller.yaml": "spi",
}


def register_class(base_binding: str, cls: str) -> None:
    """Map another base binding file name to a class name."""
    CLASS_BASES[base_binding] = cls


# ---- draft model ---------------------------------------------------------------
@dataclass
class Use:
    chosen: list[str] = field(default_factory=list)    # e.g. "zephyr,console"
    aliases: list[str] = field(default_factory=list)   # e.g. "my-uart"


@dataclass
class DraftChild:
    """A child node inside a draft node: a bus device (``eeprom@5e``), an ADC ``channel@0``, a PHY ...

    Plain data, recursive (``children``). `props` hold the values in the model of ``core/translate.py``
    (bool, int, str, list of str / int, ``Ref`` for a phandle, list of ``[Ref, cell, ...]`` for a phandle
    array); in JSON a ``Ref`` is ``{"ref": "<label>"}``. ``compatible`` and the unit address are fields of
    their own, so `props` hold the rest (``reg``, ``status``, ...). A child with no `props` and no
    `compatible` is still a node.
    """
    name: str                                           # node name without unit address: "eeprom"
    unit: str | None = None                             # unit address as written in the node name: "5e"
    compatible: list[str] = field(default_factory=list) # compatible strings, most specific first
    labels: list[str] = field(default_factory=list)     # node labels: ["mytemp0"]
    props: dict = field(default_factory=dict)
    children: list["DraftChild"] = field(default_factory=list)

    @property
    def node_name(self) -> str:
        return f"{self.name}@{self.unit}" if self.unit is not None else self.name

    def walk(self):
        """This child and all its descendants, depth first."""
        yield self
        for c in self.children:
            yield from c.walk()

    def to_dict(self) -> dict:
        return {"name": self.name, "unit": self.unit, "compatible": list(self.compatible),
                "labels": list(self.labels), "props": _enc(self.props),
                "children": [c.to_dict() for c in self.children]}

    @classmethod
    def from_dict(cls, d: dict) -> "DraftChild":
        return cls(name=d["name"], unit=d.get("unit"), compatible=list(d.get("compatible") or []),
                   labels=list(d.get("labels") or []), props=_dec(d.get("props") or {}),
                   children=[cls.from_dict(c) for c in d.get("children") or []])


def _enc(v):
    """Property value -> JSON-safe value (`Ref` becomes ``{"ref": label}``, `Sym` a plain int)."""
    from .translate import Ref
    if isinstance(v, Ref):
        return {"ref": v.label}
    if isinstance(v, bool) or v is None or isinstance(v, (str, float)):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return [_enc(x) for x in v]


def _dec(v):
    from .translate import Ref
    if isinstance(v, dict):
        if set(v) == {"ref"}:
            return Ref(v["ref"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


@dataclass
class DraftNode:
    # portable intent
    cls: str                                            # "uart" | "i2c" | "spi" | ...
    roles: list[str] = field(default_factory=list)      # "TX", "RX"
    props: dict = field(default_factory=dict)           # neutral properties
    use: Use = field(default_factory=Use)
    # not portable
    instance: str | None = None                         # target-specific instance name
    pins: dict[str, str] = field(default_factory=dict)  # role -> opaque pin id
    vendor_props: dict = field(default_factory=dict)    # derived again by the target recipe
    compatible: str | None = None                       # compatible of the realised node
    label: str | None = None                            # devicetree node label
    # child nodes written inside this node (bus devices, channels, a PHY); portable with the node
    children: list[DraftChild] = field(default_factory=list)


_NODE_ORDER = ["cls", "roles", "props", "use", "instance", "pins", "vendor_props",
               "compatible", "label", "children"]


@dataclass
class Draft:
    build: str = ""                                     # build folder name
    nodes: list[DraftNode] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        # stable key order: version, build, nodes; fixed field order per node
        nodes = []
        for node in self.nodes:
            n = asdict(node)
            n["children"] = [c.to_dict() for c in node.children]
            nodes.append({k: n[k] for k in _NODE_ORDER})
        return {"version": self.version, "build": self.build, "nodes": nodes}

    @classmethod
    def from_dict(cls, d: dict) -> "Draft":
        v = d.get("version")
        if v not in READABLE_VERSIONS:
            raise ValueError(f"unsupported draft schema version {v!r} (expected one of {READABLE_VERSIONS})")
        nodes = []
        for n in d.get("nodes", []):
            n = dict(n)
            n["use"] = Use(**(n.get("use") or {}))
            n["children"] = [DraftChild.from_dict(c) for c in n.get("children") or []]   # absent in version 1
            nodes.append(DraftNode(**n))
        # a version 1 file is migrated on load: saving it writes the current version
        return cls(build=d.get("build", ""), nodes=nodes, version=SCHEMA_VERSION)


# ---- persistence ---------------------------------------------------------------
def draft_path(app_dir: Path | str, build_dir: Path | str) -> Path:
    """``<app>/.dt_explorer/<build folder name>.draft.json``."""
    return Path(app_dir) / ".dt_explorer" / f"{Path(build_dir).name}.draft.json"


def save_draft(draft: Draft, app_dir: Path | str, build_dir: Path | str) -> Path:
    path = draft_path(app_dir, build_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(draft.to_dict(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_draft(app_dir: Path | str, build_dir: Path | str) -> Draft | None:
    """Return the stored draft, or None when there is none."""
    path = draft_path(app_dir, build_dir)
    if not path.is_file():
        return None
    return Draft.from_dict(json.loads(path.read_text(encoding="utf-8")))


# ---- binding lookup and class detection ----------------------------------------
class BindingIndex:
    """Binding YAMLs found under a list of binding search dirs.

    Resolves ``include:`` by file name (like Zephyr) and finds the binding file
    for a compatible string. The first directory wins on duplicate names.
    """

    def __init__(self, binding_dirs: Iterable[Path | str]):
        self.by_name: dict[str, Path] = {}
        for d in binding_dirs:
            d = Path(d)
            if d.is_dir():
                for y in sorted(d.rglob("*.yaml")):
                    self.by_name.setdefault(y.name, y.resolve())
        self._cache: dict[Path, dict] = {}
        self._compat: dict[str, Path] | None = None

    def load(self, path: Path) -> dict:
        if path not in self._cache:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                raw = None
            self._cache[path] = raw if isinstance(raw, dict) else {}
        return self._cache[path]

    def find(self, compatible: str) -> Path | None:
        """Binding file for `compatible`: ``<compatible>.yaml`` first, then a scan."""
        hit = self.by_name.get(f"{compatible}.yaml")
        if hit:
            return hit
        if self._compat is None:
            self._compat = {}
            for p in self.by_name.values():
                c = self.load(p).get("compatible")
                if isinstance(c, str):
                    self._compat.setdefault(c, p)
        return self._compat.get(compatible)

    @staticmethod
    def include_names(raw: dict) -> list[str]:
        inc = raw.get("include")
        if not inc:
            return []
        out = []
        for it in inc if isinstance(inc, list) else [inc]:
            if isinstance(it, str):
                out.append(it)
            elif isinstance(it, dict) and "name" in it:
                out.append(it["name"])
        return out

    def chain(self, path: Path, _seen: set | None = None) -> list[Path | str]:
        """Depth-first include chain, own file first. Unresolved includes appear as plain names."""
        seen = set() if _seen is None else _seen
        p = Path(path).resolve()
        if p in seen:
            return []
        seen.add(p)
        out: list[Path | str] = [p]
        for name in self.include_names(self.load(p)):
            inc = self.by_name.get(name)
            out.extend(self.chain(inc, seen) if inc else [name])
        return out


def detect_class(compatible: str, index: BindingIndex,
                 bases: dict[str, str] | None = None) -> tuple[str | None, list[str]]:
    """Return (class or None, include chain as file names).

    With a class, the chain runs from the compatible's own binding to the base
    binding that decided it, e.g. ``["atmel,sam0-uart.yaml", "uart-controller.yaml"]``.
    Without a controller base the class is None and the chain lists every file
    visited. An unknown compatible gives ``(None, [])``.
    """
    bases = CLASS_BASES if bases is None else bases
    start = index.find(compatible)
    if start is None:
        return None, []
    seen: set = set()

    def walk(p: Path, trail: list[str]) -> tuple[str, list[str]] | None:
        p = p.resolve()
        if p in seen:
            return None
        seen.add(p)
        trail = trail + [p.name]
        if p.name in bases:
            return bases[p.name], trail
        for name in index.include_names(index.load(p)):
            inc = index.by_name.get(name)
            if inc:
                r = walk(inc, trail)
                if r:
                    return r
            elif name in bases:
                return bases[name], trail + [name]
        return None

    hit = walk(start, [])
    if hit:
        return hit
    return None, [x.name if isinstance(x, Path) else x for x in index.chain(start)]


def neutral_properties(base_binding: str, index: BindingIndex) -> set[str]:
    """Property names declared in a base binding (e.g. ``uart-controller.yaml``) or what it includes.

    These are the portable properties; anything else in a vendor binding is a vendor property.
    """
    base = index.by_name.get(base_binding)
    if base is None:
        return set()
    names: set[str] = set()
    for item in index.chain(base):
        if isinstance(item, Path):
            names |= set((index.load(item).get("properties") or {}).keys())
    return names


def split_properties(props: dict, neutral: set[str]) -> tuple[dict, dict]:
    """Split a property dict into (neutral, vendor) by the neutral name set."""
    return ({k: v for k, v in props.items() if k in neutral},
            {k: v for k, v in props.items() if k not in neutral})
