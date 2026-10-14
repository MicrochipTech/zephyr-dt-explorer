"""Binding translation: a generic, data-driven engine.

A translation maps the properties of one node (described by a source binding)
to the properties of a node described by a target binding. Translations are
written as YAML files with optional Python hooks (see ``docs/CONFIGURATOR.md``,
"Binding translation"). This module knows the file format and nothing about any
vendor, family or device; everything specific lives in the translation files
and in their hook modules.

Property values
---------------
A node's properties are a plain ``dict`` name -> value:

* ``bool``                    boolean property (``True`` = present)
* ``int``                     single cell
* ``str``                     string
* ``list[str]``               string array
* ``list[int | Ref | Sym]``   cell array; a list of ``Ref`` only is a phandle list
* ``list[list]``              phandle array: each entry is ``[Ref, cell, ...]``

``Ref`` is a reference to a labelled node. ``Sym`` is an ``int`` that remembers
the macro name it came from, so a rename keeps the number and changes the name.

YAML schema (``schema_version: 1``)
-----------------------------------
::

    schema_version: 1
    name: <translation set name>
    hooks: <file.py>                  # optional; hook functions, next to the YAML
    tables:                           # named value maps / data for transforms and hooks
      <table>: {<key>: <value>, ...}
    groups:                           # reusable property rules
      <group>: <rules>
    translations:
      - source: <source compatible>
        target: <target compatible>   # null together with no_target: <reason>
        no_target: <reason>           # optional; the pair has no target binding
        emit_compatible: true         # false for nodes without a compatible
        use: [<group>, ...]           # optional
        note: <free text>
        target_only: {<prop>: <note>} # target props that have no source counterpart
        <rules>

``<rules>`` is ``keep: [<prop>, ...]`` plus ``properties: {<prop>: <action>}``.
Exactly one action per property:

* ``keep``                                   same name, same value
* ``{rename: <new name>}``
* ``{transform: {hook: <fn>}}``              ``fn(value, props, ctx) -> (value, lines)``
* ``{transform: {map: <table>}}``            replace each element found in the table
* ``{transform: {const: <value>}}``          fixed new value
  (``transform`` also takes ``to: <new name>`` to rename at the same time)
* ``{drop: <reason>}``                       reported when the property is present
* ``{derive: {hook: <fn>, consumes: [..]}}`` ``fn(props, ctx) -> (new props, lines)``;
  ``consumes`` lists further source properties the hook takes over silently

A property present in the input but not listed is reported and left out of the
result; it is never dropped silently.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from . import plugins

SCHEMA_VERSION = 1

_ACTIONS = ("rename", "transform", "drop", "derive")
_TRANSFORMS = ("hook", "map", "const")
_ENTRY_KEYS = {"source", "target", "no_target", "emit_compatible", "use", "note",
               "target_only", "keep", "properties"}
_DOC_KEYS = {"schema_version", "name", "hooks", "tables", "groups", "translations"}


class TranslationError(Exception):
    """A translation file is malformed or a hook cannot be found."""


# Values -----------------------------------------------------------------------

@dataclass(frozen=True)
class Ref:
    """Reference to a labelled node (rendered ``&label``)."""
    label: str


class Sym(int):
    """An integer that remembers the symbolic name it was written with."""
    name: str

    def __new__(cls, name: str, value: int):
        obj = super().__new__(cls, value)
        obj.name = name
        return obj

    def __repr__(self) -> str:
        return f"Sym({self.name!r}, {int(self)})"


# Rule model -------------------------------------------------------------------

@dataclass
class Rule:
    action: str                      # keep | rename | transform | drop | derive
    to: str | None = None            # new property name (rename / transform)
    arg: Any = None                  # reason, transform spec, derive spec
    consumes: tuple[str, ...] = ()


def _parse_rule(prop: str, spec: Any, where: str) -> Rule:
    if spec == "keep":
        return Rule("keep")
    if not isinstance(spec, dict):
        raise TranslationError(f"{where}: property {prop!r}: bad action {spec!r}")
    acts = [k for k in spec if k in _ACTIONS]
    extra = [k for k in spec if k not in _ACTIONS and k != "to"]
    if len(acts) != 1 or extra:
        raise TranslationError(
            f"{where}: property {prop!r}: need exactly one of keep/{'/'.join(_ACTIONS)}, got {sorted(spec)}")
    act = acts[0]
    val = spec[act]
    if act == "rename":
        if not isinstance(val, str) or not val:
            raise TranslationError(f"{where}: property {prop!r}: rename needs a name")
        return Rule("rename", to=val)
    if act == "drop":
        if not isinstance(val, str) or not val:
            raise TranslationError(f"{where}: property {prop!r}: drop needs a reason")
        return Rule("drop", arg=val)
    if act == "transform":
        if not isinstance(val, dict) or len(val) != 1 or next(iter(val)) not in _TRANSFORMS:
            raise TranslationError(
                f"{where}: property {prop!r}: transform needs exactly one of {_TRANSFORMS}")
        return Rule("transform", to=spec.get("to"), arg=val)
    if not isinstance(val, dict) or "hook" not in val or set(val) - {"hook", "consumes"}:
        raise TranslationError(f"{where}: property {prop!r}: derive needs {{hook, [consumes]}}")
    return Rule("derive", arg=val["hook"], consumes=tuple(val.get("consumes", ())))


def _parse_rules(body: dict, where: str) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for prop in body.get("keep", []) or []:
        rules[prop] = Rule("keep")
    for prop, spec in (body.get("properties") or {}).items():
        if prop in rules:
            raise TranslationError(f"{where}: property {prop!r} listed twice")
        rules[prop] = _parse_rule(prop, spec, where)
    return rules


# The translation object ---------------------------------------------------------

@dataclass
class Translation:
    """One source compatible -> target compatible pair; implements BindingTranslation."""
    source: str
    target: str | None
    rules: dict[str, Rule]
    name: str = ""
    api_version: int = plugins.API_VERSION
    no_target: str | None = None
    emit_compatible: bool = True
    note: str = ""
    target_only: dict[str, str] = field(default_factory=dict)
    tables: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Callable] = field(default_factory=dict)

    def translate(self, props: dict, ctx: dict | None = None) -> tuple[dict, list[str]]:
        """Return (target props, report lines). ``ctx`` is passed on to the hooks."""
        pair = f"{self.source} -> {self.target}"
        if self.no_target is not None:
            return {}, [f"{self.source}: no target binding in this workspace ({self.no_target}); "
                        f"{len(props)} properties not translated"]
        full_ctx = {"tables": self.tables, "translation": self, **(ctx or {})}
        out: dict = {}
        lines: list[str] = []
        if self.emit_compatible and self.target:
            out["compatible"] = [self.target]

        consumed: set[str] = set()
        for p in props:
            rule = self.rules.get(p)
            if rule is not None and rule.action == "derive":
                consumed.update(rule.consumes)

        def put(name: str, value: Any, origin: str) -> None:
            if name in out:
                lines.append(f"conflict: '{origin}' produces '{name}' again ({pair}); later value wins")
            out[name] = value

        for p, value in props.items():
            if p == "compatible" and self.emit_compatible:
                continue
            if p in consumed:
                continue
            rule = self.rules.get(p)
            if rule is None:
                lines.append(f"unlisted property '{p}' ({pair}): not translated, left out")
                continue
            if rule.action == "keep":
                put(p, value, p)
            elif rule.action == "rename":
                put(rule.to, value, p)
            elif rule.action == "drop":
                lines.append(f"dropped '{p}' ({pair}): {rule.arg}")
            elif rule.action == "transform":
                (kind, arg), = rule.arg.items()
                if kind == "const":
                    new = arg
                elif kind == "map":
                    new, extra = self._map(p, value, arg)
                    lines.extend(extra)
                else:
                    new, extra = self._hook(arg)(value, props, full_ctx)
                    lines.extend(extra)
                put(rule.to or p, new, p)
            elif rule.action == "derive":
                new_props, extra = self._hook(rule.arg)(props, full_ctx)
                lines.extend(extra)
                for k, v in new_props.items():
                    put(k, v, p)
        return out, lines

    def _hook(self, name: str) -> Callable:
        fn = self.hooks.get(name)
        if fn is None:
            raise TranslationError(f"{self.source}: hook {name!r} not found")
        return fn

    def _map(self, prop: str, value: Any, table_name: str) -> tuple[Any, list[str]]:
        table = self.tables.get(table_name)
        if not isinstance(table, dict):
            raise TranslationError(f"{self.source}: table {table_name!r} not found")
        lines: list[str] = []

        def one(v: Any) -> Any:
            key = v.name if isinstance(v, Sym) else v if isinstance(v, str) else None
            if key is None or key not in table:
                return v
            new = table[key]
            if new is None:
                lines.append(f"'{prop}': '{key}' has no counterpart in the target ({self.source}); kept as is")
                return v
            return Sym(new, int(v)) if isinstance(v, Sym) else new

        if isinstance(value, list):
            return [one(v) for v in value], lines
        return one(value), lines


# Loading ------------------------------------------------------------------------

def _load_hooks(path: Path) -> dict[str, Callable]:
    if not path.is_file():
        raise TranslationError(f"hook file not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_dtx_hooks_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {n: f for n, f in vars(mod).items() if callable(f) and not n.startswith("_")}


def translations_from_dict(doc: dict, base_dir: Path | None = None,
                           hooks: dict[str, Callable] | None = None) -> list[Translation]:
    """Build translations from a parsed YAML document; ``hooks`` adds or overrides hook functions."""
    if not isinstance(doc, dict):
        raise TranslationError("translation document must be a mapping")
    ver = doc.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise TranslationError(f"unsupported schema_version {ver!r} (this core reads {SCHEMA_VERSION})")
    unknown = set(doc) - _DOC_KEYS
    if unknown:
        raise TranslationError(f"unknown top-level keys: {sorted(unknown)}")
    set_name = doc.get("name") or "translation"
    tables = doc.get("tables") or {}
    hook_fns: dict[str, Callable] = {}
    if doc.get("hooks"):
        if base_dir is None:
            raise TranslationError("'hooks' needs a base directory")
        hook_fns = _load_hooks((base_dir / doc["hooks"]).resolve())
    hook_fns.update(hooks or {})
    groups = {g: _parse_rules(body or {}, f"group {g}") for g, body in (doc.get("groups") or {}).items()}

    result: list[Translation] = []
    for i, entry in enumerate(doc.get("translations") or []):
        where = f"translations[{i}]"
        bad = set(entry) - _ENTRY_KEYS
        if bad:
            raise TranslationError(f"{where}: unknown keys {sorted(bad)}")
        if "source" not in entry:
            raise TranslationError(f"{where}: 'source' missing")
        no_target = entry.get("no_target")
        target = entry.get("target")
        if target is None and no_target is None:
            raise TranslationError(f"{where}: needs 'target' or 'no_target'")
        rules: dict[str, Rule] = {}
        for g in entry.get("use", []) or []:
            if g not in groups:
                raise TranslationError(f"{where}: unknown group {g!r}")
            rules.update(groups[g])
        rules.update(_parse_rules(entry, where))
        t = Translation(
            source=entry["source"], target=target, rules=rules,
            name=f"{set_name}:{entry['source']}", no_target=no_target,
            emit_compatible=entry.get("emit_compatible", True),
            note=entry.get("note", ""), target_only=dict(entry.get("target_only") or {}),
            tables=tables, hooks=hook_fns)
        for p, r in rules.items():
            if r.action == "derive":
                t._hook(r.arg)
            elif r.action == "transform":
                kind, arg = next(iter(r.arg.items()))
                if kind == "hook":
                    t._hook(arg)
                elif kind == "map" and arg not in tables:
                    raise TranslationError(f"{where}: property {p!r}: table {arg!r} not found")
        result.append(t)
    return result


def load_translations(path: Path | str) -> list[Translation]:
    """Read a translation YAML file (hooks are loaded from the file it names)."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TranslationError(f"{path}: invalid YAML: {exc}") from exc
    return translations_from_dict(doc, path.parent)


def register_file(path: Path | str) -> list[Translation]:
    """Load a translation file and put every pair into the plugin registry."""
    loaded = load_translations(path)
    for t in loaded:
        plugins.register_translation(t)
    return loaded


# Rendering ----------------------------------------------------------------------

def _cell(v: Any, symbolic: bool) -> str:
    if isinstance(v, Ref):
        return f"&{v.label}"
    if isinstance(v, Sym) and symbolic:
        return v.name
    return str(int(v))


def render_property(name: str, value: Any, symbolic: bool = True) -> str | None:
    """One devicetree property statement; ``None`` for an absent (False) boolean."""
    if value is False:
        return None
    if value is True:
        return f"{name};"
    if isinstance(value, str):
        return f'{name} = "{value}";'
    if isinstance(value, int):
        return f"{name} = <{_cell(value, symbolic)}>;"
    if value and all(isinstance(v, str) for v in value):
        return f"{name} = " + ", ".join(f'"{v}"' for v in value) + ";"
    if value and all(isinstance(v, list) for v in value):
        return f"{name} = " + ", ".join(
            "<" + " ".join(_cell(c, symbolic) for c in e) + ">" for e in value) + ";"
    return f"{name} = <" + " ".join(_cell(c, symbolic) for c in value) + ">;"


def render_node(header: str, props: dict, symbolic: bool = True, indent: str = "\t") -> str:
    """``header { prop; ... };`` as devicetree source text."""
    body = [s for s in (render_property(n, v, symbolic) for n, v in props.items()) if s]
    return header + " {\n" + "".join(f"{indent}{s}\n" for s in body) + "};"
