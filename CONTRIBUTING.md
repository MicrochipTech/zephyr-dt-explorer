# Contributing to Devicetree Explorer

Thanks for helping. This file covers the development setup, the rules that keep the core
vendor-neutral, and how to write each of the six plugin kinds. The Microchip/Atmel plugins in this
repository are the reference implementations; copy their structure.

Design background: [docs/CONFIGURATOR.md](docs/CONFIGURATOR.md), "Architecture: vendor plugins".

## Development setup (Windows, `py` launcher)

```cmd
py -m pip install nicegui pyyaml          :: runtime
py -m pip install -r requirements-dev.txt :: pytest
py -m pytest                              :: run the tests
py dt_explorer.py <folder> --no-browser   :: start the app without the folder dialog
```

The optional Altium reader needs `olefile` (its test is skipped without it).

### Environment variables for tests

Tests never need data from the repository. Tests that need data outside the repo read a path from
an environment variable. When the variable is unset or the path is missing, the test is **skipped
with a reason**, never failed.

| Variable | Points to | Used by |
|---|---|---|
| `DTX_RTOS4_WS` | RTOS4 west workspace (Zephyr v4.3.0) | golden tests, real-build tests |
| `DTX_MAIN_WS` | main west workspace (current Zephyr) | `zephyr-main` tests (from P6) |
| `DTX_DESIGN_FILES` | board design files (not in the repo) | design-file reader and importer tests |
| `DTX_GOLDEN_DIR` | optional: a copy of the golden files | golden tests |

Board design files, MPLAB packs and build folders are never committed. A trimmed excerpt of a
vendor file (for example an ATDF extract, Apache-2.0) may be committed as a fixture under
`tests/fixtures/`.

## Repository rules

- **The core stays vendor-neutral.** No vendor or device-family names in `dt_explorer/core/` and
  `dt_explorer/ui/`. Names such as `atmel`, `microchip`, `sercom` or `same54` belong in plugins,
  boards or translation files. An existing hit in core or ui is a defect to fix, not a pattern to
  copy.
- **Dependency direction.** The core never imports the plugin packages (`providers/`, `formatters/`,
  `recipes/`, `translations/`, `readers/`, `connectors/`). Plugins import from `dt_explorer.core`
  only. Plugins talk to the core through the protocols and the registry in
  `dt_explorer/core/plugins.py`.
- **Protocols are versioned.** Every plugin declares `api_version`. A plugin whose version differs
  from `API_VERSION` (currently `1`) is skipped with a logged reason, so it cannot break the tool.
- **Tests first for data rules.** A new mapping (pin name, clock, property) comes with a test that
  fails without it.
- **Documentation follows features.** A visible feature updates `README.md` and `docs/GUI.md` in the
  same change.

## Plugin registry (shared by all kinds)

`dt_explorer/core/plugins.py` holds the registry. Every kind has a decorator and an entry-point
group:

| Kind | Protocol | Decorator | Entry-point group |
|---|---|---|---|
| Provider | `DeviceDataProvider` | `register_provider` | `dt_explorer.providers` |
| Formatter | `PinctrlFormatter` | `register_formatter` | `dt_explorer.formatters` |
| Recipe | `Recipe` | `register_recipe` | `dt_explorer.recipes` |
| Translation | `BindingTranslation` | `register_translation` | `dt_explorer.translations` |
| Board reader | `BoardReader` | `register_board_reader` | `dt_explorer.board_readers` |
| Connector type | `ConnectorType` | `register_connector` | `dt_explorer.connectors` |

Decorator use: a class is instantiated **without arguments** at registration; an instance is
registered as it is. Registration happens when the module is imported, so an in-repo plugin must be
imported by its package `__init__.py` (for example `formatters/__init__.py`).

For an **external package**, declare the entry point in its `pyproject.toml`:

```toml
[project.entry-points."dt_explorer.providers"]
st_open_pin_data = "dt_explorer_st.provider:StOpenPinDataProvider"
```

Entry-point loading is implemented as `discover_entry_points()` in `core/plugins.py`, but the
application does not call it yet. Until it does, an external package must be imported explicitly
where it is needed. A plugin that fails to load is logged and skipped, not raised.

Tests for the registry use the pattern in `tests/test_plugins.py`: an autouse fixture that calls
`plugins.unregister_all()` before and restores the registry afterwards.

## Plugin kinds

### Provider (device data)

Turns a vendor device file into the neutral `DeviceModel`. Finding the file is the provider's job.

```python
class DeviceDataProvider(Protocol):
    api_version: int
    name: str                                         # "microchip-atdf"
    def matches(self, info: BuildInfo) -> bool: ...   # responsible for this build?
    def locate(self, info: BuildInfo) -> Path | None: ...  # where is the device file?
    def load(self, path: Path) -> DeviceModel: ...
```

- Reference: `dt_explorer/providers/microchip_atdf.py` (`MicrochipAtdfProvider`, `name = "microchip-atdf"`).
- Register with `@register_provider`. Keep pack paths (`.mchp_packs`, `hal_microchip/packs`) inside
  the provider.
- Test: parse a small trimmed file from `tests/fixtures/atdf/` without environment variables. Test
  `locate()` with a `tmp_path` workspace. Real-workspace tests use `rtos4_ws` from `conftest.py`.
  See `tests/test_microchip_atdf.py`.

### Formatter (pinctrl notation)

Converts between a neutral signal and the overlay notation of one Zephyr pinctrl binding.

```python
class PinctrlFormatter(Protocol):
    api_version: int
    name: str                                          # "atmel_sam0"
    compatibles: list[str]                             # e.g. ["atmel,sam0-pinctrl"]
    def to_overlay(self, signal: Signal) -> str: ...
    def from_overlay(self, value: str) -> str: ...     # overlay value -> pad id
    def render_group(self, signals: list[Signal]) -> str: ...  # dts text
```

- Reference: `dt_explorer/formatters/atmel_sam0.py` (`AtmelSam0Formatter`, `compatibles = ["atmel,sam0-pinctrl"]`)
  and `dt_explorer/formatters/microchip_port_g1.py` (the same ATDF data, a different Zephyr port).
- Register with `@register_formatter`. Every generated name must be checked against the pinctrl
  header or `.dtsi` the formatter targets. The formatter takes the header from the workspace; it does
  not hard-code a path.
- Test: round trip `to_overlay` → `from_overlay` for every signal, plus a negative test for an
  unknown name. See `tests/test_atmel_sam0_formatter.py`.

### Recipe (driver knowledge)

Names the pin roles of a driver, derives its properties and lists its Kconfig symbols.

```python
class Recipe(Protocol):
    api_version: int
    name: str
    compatibles: list[str]                                  # driver compatibles
    def pin_roles(self, instance: Instance) -> list[str]: ...
    def derived_props(self, assignment: dict) -> dict: ...
    def kconfig(self) -> dict[str, str]: ...
    def lift(self, props: dict, pins: dict) -> dict[str, str]: ...
```

- Reference: `dt_explorer/recipes/atmel_sam0_sercom.py`. This file is being written as part of the
  current work and may not be on the main branch yet. Read it together with the `Recipe` protocol
  above.
- Register with `@register_recipe`. Recipes return data; they do not write files.
- Test: a recipe for one driver family gets one test per pin role and one for `lift` on a
  synthetic `props`/`pins` pair.

### Translation (binding to binding)

Maps the properties of one driver family's binding to another family's binding for the same
hardware. A translation is **data first**: a YAML file, with an optional Python hooks module next
to it.

```python
class BindingTranslation(Protocol):
    api_version: int
    name: str
    source: str                     # source compatible, e.g. "atmel,sam0-uart"
    target: str                     # target compatible, e.g. "microchip,sercom-g1-uart"
    def translate(self, props: dict) -> tuple[dict, list[str]]:
        """Return (target props, report lines for unlisted or dropped properties)."""
```

- Reference: `dt_explorer/translations/atmel_sam0_to_mchp_g1.yaml` with its hooks module
  `atmel_sam0_to_mchp_g1.py`. The YAML schema is in the module docstring of
  `dt_explorer/core/translate.py`.
- Each property of the source binding must appear in the YAML (keep, rename, transform, drop or
  derive). An unlisted property is **reported**, never dropped silently.
- Register: put the YAML next to `dt_explorer/translations/__init__.py`. `register_all()` loads all
  `*.yaml` files there. A translation in another package calls
  `dt_explorer.core.translate.register_file(path)` at import. The entry-point group
  `dt_explorer.translations` expects objects that implement `BindingTranslation`, not YAML files.
- Test: load the YAML with `translations_from_dict` or `load_translations`, translate a synthetic
  node, and check the result against the target binding. Add a negative test with one required
  property removed. See `tests/test_translate.py`.

### Board reader (design file to netlist)

Reads one EDA file format into the neutral `BoardNetlist`.

```python
class BoardReader(Protocol):
    api_version: int
    name: str
    extensions: list[str]           # lower case with dot, e.g. [".pcbdoc"]
    def read(self, path: Path) -> BoardNetlist: ...
```

- Reference: `dt_explorer/readers/altium_pcbdoc.py` (`AltiumPcbDocReader`, `extensions = [".pcbdoc"]`).
- Register with `@register_board_reader`. Use `API_VERSION` from `core.plugins` for `api_version`.
  The `BoardNetlist` dataclasses are in `dt_explorer/core/board.py`.
- Test: a synthetic minimal file if the format allows one. A real design file is read through
  `DTX_DESIGN_FILES` and skipped when it is unset. Optional libraries are guarded with
  `pytest.importorskip`. See `tests/test_altium_pcbdoc.py`.

### Connector type (pin roles)

Describes one connector standard: which connector pin has which role, and how confident the tool is
that a netlist matches it.

```python
class ConnectorType(Protocol):
    api_version: int
    name: str
    zephyr_binding: str             # e.g. "atmel-xplained-pro-header"
    def pin_roles(self) -> dict[str, str]: ...                           # connector pin -> role
    def detect(self, netlist_pins: dict[str, str | None]) -> float: ...  # confidence 0..1
```

- Reference: `dt_explorer/connectors/xplained_pro_ext.py` (`XplainedProExt`) and
  `dt_explorer/connectors/arduino_uno_r3.py`. Shared helpers (`normalize`, `is_gnd`, `is_vcc`,
  `hint_matches`, `fraction`) are in `dt_explorer/connectors/__init__.py`.
- Register with `@register_connector` and import the module from `connectors/__init__.py`.
- Test: `detect()` returns 1.0 (or near it) for the own pinout and below 0.5 for an unrelated
  netlist, and `pin_roles()` covers every pin the binding needs. See `tests/test_connectors.py`.

## Sending a change

1. One change per commit. Start the message with the card or topic, for example `P5.3: ...`.
2. Run `py -m pytest`. Skips are fine when they name a missing variable.
3. Keep the change inside the plugin's own package. A change that needs the core to know a vendor
   name is a design question: open it first.
4. Do not commit board design files, packs or build folders.
