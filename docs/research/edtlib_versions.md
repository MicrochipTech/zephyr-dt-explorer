# Two edtlib versions in one process (P6.0)

Date 2026-10-15. Evidence with commands, sources and complete output: `docs/evidence/P6.0.md`.

## Question

`edt.pickle` stores edtlib objects. Source builds (RTOS4, Zephyr v4.3.0) and target builds
(`C:\Masters\zephyr-main`, Zephyr 4.5.0-rc1) need different edtlib code. Can both models live in one
Python process, or must one side load in a subprocess?

## Findings

- **The main build works.** `west build -b pic32cx_sg41_cult --cmake-only` on `samples/hello_world`
  succeeded in 161 s (first run, mostly CMake). Main picks Zephyr SDK **1.0.1**
  (`C:\Users\M91221\zephyr-sdk-1.0.1`, already installed), not 0.17.4. Main venv is Python 3.14,
  RTOS4 venv Python 3.12.
- **The edtlib versions differ a lot.** `edtlib.py`: +753/-52 lines; `dtlib.py`: +65/-11;
  `grutils.py`, `_private.py`, `__init__.py` identical. Main adds e.g. `_LocalProps`, `MapEntry`,
  `HexInt`, new Node/Binding attributes (`_included_binding_paths`, `_local_props`, `dma_ranges`,
  `_warn_bus_mismatch`).
- **Today's code breaks.** `BuildModel.__init__` only inserts the workspace's `src` into `sys.path`.
  With a second workspace the cached `devicetree.edtlib` of the first one stays, and unpickling the
  main build fails: `AttributeError: Can't get attribute '_LocalProps' on <module 'devicetree.edtlib'
  from '...26020_RTOS4...'>`.
- **(a)/(b) swap `sys.path` + purge `sys.modules['devicetree*']` works in both orders**, with both
  Pythons. Objects loaded before the swap keep working (`props`, `compats`, `dep_ordinal`,
  `binding_path`, `_dt.alias2node`), because their data sits in the instances and their classes keep
  the globals of their own module. It is fragile: `sys.modules['devicetree.edtlib']` always names
  the last loaded version, so any `isinstance(x, edtlib.Node)` or lazy import in a later call hits
  the wrong class.
- **(c) Keeping both module sets alive and mapping classes in `Unpickler.find_class` works** with
  both Pythons and needs no global `devicetree` entry in `sys.modules` at all. Class identity is
  distinct per version, `Node` methods (`regs`, `pinctrls`, `parent`, `props[...].val`) work on
  both models interleaved. edtlib has no function-local imports, so it does not look up
  `sys.modules` at run time. Importing both takes 0.17 s.
- **(d) Subprocess** (the workspace's own Python plus a helper that dumps a JSON model):
  0.44 s / 94 KB for lab1, 0.46 s / 132 KB for the main build, nodes/aliases/chosen all correct. An
  in-process unpickle of lab1 is 0.15 s, so the subprocess costs about 0.3 s more per build.

## Recommendation for P6.1

Use **approach (c), in-process, isolated module sets**, as the default. It is the smallest change,
keeps live edtlib objects (so `state()`, `fmt_value()`, `pins.py`, `validate.py` and the UI stay
unchanged and goldens are not at risk), and was proven with both Pythons. Keep (d) as the fallback
for the one case (c) cannot cover: an `edt.pickle` written by a Python or a pickle protocol that the
running interpreter cannot read. That did not occur here (3.12 and 3.14 read each other's pickles).
It also becomes the way to go if a future edtlib imports its siblings lazily.

Do not use (a)/(b): it works but the global `sys.modules` state makes it order dependent.

### `core/model_host.py` sketch

```python
class EdtlibHost:
    """Loads edt.pickle with the edtlib of its own workspace; several hosts coexist."""
    _by_src: dict[Path, "EdtlibHost"] = {}

    @classmethod
    def for_zephyr_base(cls, zephyr_base: Path) -> "EdtlibHost":   # cached per src dir
        ...
    def __init__(self, src: Path):
        # import devicetree{,._private,.dtlib,.grutils,.edtlib} with src first on sys.path,
        # keep the module objects in self.modules, then remove every 'devicetree*' key from
        # sys.modules and src from sys.path again (exp_c.py: import_version)
    def load_edt(self, pickle_path: Path):
        # pickle.Unpickler subclass; find_class maps 'devicetree' / 'devicetree.*' to self.modules
        # (exp_c.py: class U)
```

`BuildModel.__init__` replaces the `sys.path.insert` + `pickle.load` lines (build.py lines 186-190)
by `self.edt = EdtlibHost.for_zephyr_base(self.zephyr_base).load_edt(zdir / "edt.pickle")`. Nothing
else changes. Risk to check in P6.1: `edt.pickle` objects also reference `yaml`-free plain data only,
so no further modules are needed; PyYAML must be importable in the running interpreter (edtlib
imports it). Add a test that loads lab1 and the main build in one process and compares
`state()` results with the goldens, in both orders.

### Live edtlib objects in BuildModel (matter only for the subprocess fallback)

If (d) ever becomes necessary, a plain-data model must replace these live uses:

- `__init__` / `from_edt`: `edt.nodes`, `edt._dt.alias2node`, `edt.chosen_nodes`, `edt.label2node`
  (`_code_refs`), `by_path`, `aliases`, `chosen`.
- `state()`: `node.compats`, `dep_ordinal`, `status`, `parent`, `binding_path`.
- `_device_ords`, `orphan_drivers()`: `edt.nodes[*].status/compats`.
- `drivers_for()`, `node_origin_kind()`, `touched_by_overlay()`, `macros()`, `node_lines()`: only
  `node.compats` / `node.path`, trivially replaceable.
- `fmt_value()`: needs `prop.type`, `prop.val` including phandle targets and
  `controller/data` of phandle-array entries (the most demanding part).
- Outside `build.py`: `core/pins.py` (`model.edt.nodes`, `pinctrls`, `props`), `core/validate.py`,
  `ui/page.py` (about 40 attribute uses: `children`, `props`, `regs`, `labels`, `binding`).

The helper in the evidence (`helper_dump.py`) already covers path, status, compats, dep_ordinal,
binding_path, labels, props (type + value), regs, pinctrls, aliases and chosen; phandle-array
values and `binding` details would have to be added. That is the reason to prefer (c).
