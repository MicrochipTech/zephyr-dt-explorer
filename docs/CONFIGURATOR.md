# Design: Peripheral configurator (devicetree generation)

Status: proposal, nothing implemented yet.

The explorer currently answers *"where does this devicetree entry come from?"*. The configurator
works in the other direction. You pick a peripheral, choose a mode and pins, and the tool generates
the overlay snippet and the `prj.conf` lines, then checks them.

## Goal and scope

The configurator enables and configures a peripheral that the SoC `.dtsi` already describes. For
example, it turns `sercom2` (disabled) into a UART on PB24/PB25. The result is the same block that a
board file writes by hand. Here is SERCOM2 in `boards/atmel/sam0/same54_xpro/same54_xpro.dts`:

```dts
&sercom2 {
	status = "okay";
	compatible = "atmel,sam0-uart";
	current-speed = <115200>;
	rxpo = <1>;
	txpo = <0>;
	pinctrl-0 = <&sercom2_uart_default>;
	pinctrl-names = "default";
};
```

**Generic means generic across Microchip devices.** The tool must work for every Microchip device
that Zephyr supports and for which an ATDF exists. The SAM E54 is only the first test case.

**Other vendors are not implemented, but the architecture must allow them.** Device data comes
through plugins, so the community can add other vendors' device files later without changing the
core. See *Architecture: vendor plugins*.

**Porting between devices and vendors** becomes possible on the same basis: the draft stores
*what* is wanted (a UART at 115200 baud on TX/RX), not the vendor notation. See *Porting*.

Out of scope for the implementation:
- plugins for other vendors (ST, NXP, Nordic, …),
- clock trees, electrical checks and DMA channel allocation,
- Microchip families without an ATDF. MEC and Mi-V are likely in this group; this still needs to be
  confirmed.

## Main use case: SAME54 projects → PIC32CX SG41/SG61

The PIC32CX SG41 and SG61 are the successors of the SAME54. The tool's main purpose is to port
existing SAME54 Zephyr projects, such as the RTOS4 labs, to them. All facts below were checked on
2026-10-14.

### Hardware: almost identical according to the ATDF

The ATDF files from `hal_microchip` were compared by script:

| | SAME54P20A ↔ PIC32CX1025SG41128 | SAME54P20A ↔ PIC32CX1025SG61128 |
|---|---|---|
| Peripheral instances | identical (71) | identical (71) |
| Module versions, base addresses | identical | identical |
| Pin functions | **identical** | 5 pins missing: PB26–PB29, PC04 |
| Packages | – | TQFP128 only (the E54 also has TFBGA120) |

On the SG61 the missing pins remove some alternatives: one complete SERCOM4 pin set (PB26–PB29), one
alternative pad each for SERCOM2 and SERCOM6, some TCC0/TCC1 outputs, EIC EXTINT4/12–15 and I2S
SCK1/MCK1. The porting report must flag any of these. The SAME54 Xplained Pro console (PB24/PB25)
is not affected.

### Software: the port changes the Zephyr driver family

| | SAME54 in the RTOS4 labs | PIC32CX SG |
|---|---|---|
| Zephyr port | old: `soc/atmel/sam0` | new: `soc/microchip/pic32c` |
| Drivers and bindings | `atmel,sam0-uart`, `atmel,sam0-sercom`, … | `microchip,sercom-g1-uart`, `microchip,sercom-g1`, … |
| pinctrl | `atmel,sam0-pinctrl` | `microchip,port-g1-pinctrl` |
| Clocks | `atmel,sam0-gclk` / `-mclk` | `microchip,sam-d5x-e5x-clock` (the same clock hardware as the E54) |

**Zephyr version matters.** In Zephyr v4.3.0 (the RTOS4 workspace) the PIC32CX SG `.dtsi` only
describes CPU, flash, SRAM, GPIO and pinctrl. `pic32cx_sg61_cult` has no console there.
Upstream `main` (2026-10-13) adds SERCOM (6×), TC, TCC, ADC, DAC, DMAC, EIC, GMAC, RTC, WDT, TRNG
and the clock tree, plus the board `pic32cx_sg41_cult`.

### Consequence: two steps

1. **Translate the driver family** `atmel,sam0-*` → `microchip,*-g1`: compatibles, properties,
   pinctrl values, clock references and Kconfig symbols. This is the real work. The hardware is the
   same, so mapping by device class is not needed here. A **direct translation table per binding
   pair** is more precise (see *Binding translation* below). It can be tested on the existing
   board: `same54_xpro` → `sam_e54_xpro` is exactly this step on the same hardware.
2. **Change the device** E54 → SG41/SG61 within the new family. This is almost only a swap of the
   SoC `.dtsi`. For the SG41 the checks report nothing; for the SG61 the pin check reports the 5
   missing pins.

### Prerequisite: a current Zephyr workspace

The PIC32CX SG support needed here only exists in a current Zephyr. Use a **second workspace**,
planned at `C:\Masters\zephyr-main` (`west init -m https://github.com/zephyrproject-rtos/zephyr`
plus `west update`), so the RTOS4 course workspace stays unchanged. The explorer opens either
workspace through its folder dialog. Porting from a build in one workspace to a build in the other
must work, because source and target live in different Zephyr versions.

### Microchip families in Zephyr

Each family uses its own pinctrl binding, so the overlay notation differs per family:

| Zephyr SoC folder | Families | pinctrl binding |
|---|---|---|
| `soc/atmel/sam0` | SAM D2x, D5x/E5x, L2x, C2x | `atmel,sam0-pinctrl` |
| `soc/atmel/sam` | SAM3/4, SAM E70/S70/V71 | `atmel,sam-pinctrl` |
| `soc/microchip/sam/sam_d5x_e5x`, `soc/microchip/pic32c/*` | SAM D5x/E5x (new port), PIC32CM JH, PIC32CX SG, PIC32CZ CA | `microchip,port-g1-pinctrl` |
| `soc/microchip/sam/sama7*` | SAMA7G5 | `microchip,sama7g5-pinctrl` |
| `soc/microchip/mec` | MEC | `microchip,mec5-pinctrl`, `microchip,xec-pinctrl` |

The SAM E5x appears twice: in the old `atmel,sam0` port, which the RTOS4 labs use
(`CONFIG_SOC_FAMILY="atmel_sam0"`), and in the new `microchip` port. The tool reads the pinctrl
compatible from the build, so it handles both.

## Data sources

| Source | Provides | Specific to |
|---|---|---|
| `edt.pickle` | All nodes, including disabled SoC peripherals with address, IRQ and clocks | – (already used) |
| Bindings (YAML) | Allowed properties: type, required, enum, default, `on-bus` | – (already used) |
| `zephyr/.config` | `CONFIG_SOC` (e.g. `same54p20a`), which selects the ATDF | – |
| **ATDF** from the DFP | Per peripheral instance: possible pins, pad/signal index, mux function, IOSET; package pinouts per order code | all Microchip devices with an ATDF |
| Zephyr pinctrl header or `.dtsi` of the family (e.g. `modules/hal/atmel/include/dt-bindings/pinctrl/same54p-pinctrl.h`) | The names or values an overlay must use, e.g. `PB25D_SERCOM2_PAD0` | per pinctrl binding |

### ATDF

ATDF ("Atmel Device File") is the device description format Atmel created and Microchip still uses
in its DFPs. The installed packs on this machine have ATDF files for SAME54, SAM9X7, PIC32CX-SG41,
PIC32CZ-CA70, PIC32MM, PIC32MZ-W and dsPIC33AK. Older 8/16-bit PIC packs only have `edc/*.PIC`
files. Those parts are not supported by Zephyr anyway.

The file is XML and can be read with `xml.etree`:

```xml
<instance name="SERCOM2">
  <register-group name="SERCOM2" name-in-module="SERCOM" address-space="base" offset="0x41012000"/>
  <signals>
    <signal group="PAD" index="0" function="D" pad="PA09" ioset="3"/>
    <signal group="PAD" index="0" function="D" pad="PB25" ioset="4"/>
    <signal group="PAD" index="0" function="C" pad="PA12" ioset="1"/>
    ...
```

`<variant ordercode=… package="TQFP128" pinout="SAMD51P"/>` together with the `<pinouts>` section
tells which pads exist on which package.

The DFP also contains C headers (`include/pio/same54p20a.h`, e.g. `PINMUX_PA09D_SERCOM2_PAD0`).
They carry the same pin and mux data but no IOSET or package information, and reading them would
need regex. The ATDF is the better source.

### Finding the ATDF automatically

1. **Device name** from the build's `zephyr/.config`: `CONFIG_SOC="same54p20a"`.
2. **First source: the `hal_microchip` module of the workspace** (`modules/hal/microchip/packs`).
   It ships 91 ATDF files for the families of the new Microchip port: SAM D51/E51/E53/E54
   (`packs/sam/sam_d5x_e5x/<series>/atdf/`), PIC32CM JH, PIC32CX SG41/SG60/SG61 and PIC32CZ
   CA70/CA80/CA90/CA91 (`packs/pic32c/...`). These files match the Zephyr version exactly and need no
   MPLAB installation. Search them with `packs/**/atdf/*<SOC>.atdf`; the tree is small, so a glob is
   fine here. Found on 2026-10-14 after `west update hal_microchip`.
3. **Then the installed DFPs**, in this order. They also cover the old `atmel` port families
   (SAM D2x, SAM E70, …), for which `hal_microchip` has no ATDF:
   - `%USERPROFILE%\.mchp_packs`, where the pack manager of MPLAB X and the MPLAB VS Code
     extensions downloads packs,
   - `C:\Program Files\Microchip\MPLABX\*\packs`.

   Pattern: `<root>\Microchip\*_DFP\<version>\atdf\*<SOC>.atdf`, matched case-insensitively.
   SAM devices carry an `AT` prefix (`ATSAME54P20A.atdf`), PIC32C devices do not.
4. **Version (DFPs):** take the newest with a *natural* sort (`3.11.261` > `3.9.244`), not an
   alphabetical one.
5. **Search with the pattern, never recursively.** A recursive `find` over the pack roots took
   about 10 s on this machine.
6. **Fallback:** a manual path in `~/.dt_explorer.json` or a file dialog. Downloading the
   `.atpack` (a ZIP file) from `packs.download.microchip.com` would also work, but only on an
   explicit button press, never automatically.

Tested on this machine: for lab1, the lookup finds
`~/.mchp_packs/Microchip/SAME54_DFP/3.11.261/atdf/ATSAME54P20A.atdf`, plus older versions and the
copy in the MPLAB X installation.

**Checked (2026-10-14): the Zephyr SoC name matches the ATDF file name.** This was compared against
the `soc.yml` files and the installed DFPs. Apart from case and the `AT` prefix on SAM devices, the
names are identical:

| `CONFIG_SOC` (Zephyr) | ATDF in the DFP |
|---|---|
| `same54p20a` | `ATSAME54P20A.atdf` |
| `pic32cx1025sg41128` | `PIC32CX1025SG41128.atdf` |
| `pic32cz2051ca70144` | `PIC32CZ2051CA70144.atdf` |

So no mapping table is needed. The lookup only fails when neither `hal_microchip` nor an installed
DFP has the file. PIC32CX SG60/SG61 and PIC32CZ CA80 have no installed DFP on this machine, but
`hal_microchip` covers them. If nothing is found, the tool reports which pack is missing and offers
the fallback above.

Zephyr itself (the `zephyr` repository) ships no ATDF files; they come from `hal_microchip` or from
installed packs.

## User interface

- **Entry point.** In the node details, the traffic-light card of a node whose status is
  `disabled` gets a **"Configure…"** button. Later, enabled nodes can get **"Edit pins…"**. The
  configurator opens in the detail panel on the right:
  1. Choose the mode, which is the compatible. For a SERCOM that is UART, I2C or SPI.
  2. Fill in a form generated from the binding. It reuses the property table of the binding view
     (type, required, enum as a dropdown, default as the preset value).
  3. Choose a pin for each role (TX/RX, SDA/SCL, MOSI/MISO/SCK/CS). The choices come from the device data provider (for Microchip: the ATDF).
     A pin that is already taken is greyed out and shows what uses it.
  4. Derived properties (`rxpo`/`txpo`, `dipo`/`dopo`) are shown read-only.
- **New tab "Draft"** next to "Nodes" and "Include tree". It collects all pending changes of the
  current build:
  - the combined overlay snippet (`&sercomX { … }` plus `&pinctrl { … }` groups),
  - the suggested `prj.conf` lines,
  - the check results,
  - buttons to copy the result or write it to a file.
- **Tree.** Changed nodes get a `draft` badge, like the existing `overlay` badge. The traffic
  light shows the state the node is expected to have after a rebuild.
- **Pin map (by-product).** A table of the package pins: who uses each pin and which functions are
  possible on it. It is useful even without the generator.

## Architecture: vendor plugins

**Focus now:** Microchip devices described by ATDF files. **Must stay possible:** the community can
add other vendors' device files (STM32 Open Pin Data, NXP MCUXpresso Config Tools data, …) without
changing the core. The rule that keeps this open is that **the core never reads a vendor file
directly.** It only knows a small neutral device model. Microchip ATDF support is plugin no. 1 and
gets no special treatment.

### Three independent plugin kinds

| Kind | Translates | Selected by | First implementations |
|---|---|---|---|
| **Device data provider** | vendor device file → neutral `DeviceModel` | `matches(build_info)` | `microchip_atdf` |
| **Pinctrl formatter** | neutral signal ↔ overlay notation of one Zephyr pinctrl binding | compatible of the build's pinctrl node | `atmel,sam0-pinctrl`, `microchip,port-g1-pinctrl` |
| **Recipe** | driver knowledge: named pin roles, derived properties, Kconfig table | driver compatible (`atmel,sam0-uart`, …) | SERCOM UART/I2C/SPI |
| **Binding translation** | one driver family's binding → another family's binding for the same hardware | pair (source compatible, target compatible) | `atmel,sam0-*` → `microchip,*-g1` |

Providers belong to the vendor's *file format*. Formatters belong to the *Zephyr side*. Keeping the
two apart matters:
- One ATDF provider already serves two formatters: the old `atmel,sam0` and the new
  `microchip,port-g1` port of the SAM E5x.
- A community contribution for ST would be exactly one provider (Open Pin Data) plus one formatter
  (`st,stm32-pinctrl`), without touching the core.

**Provider interface:**

```python
class DeviceDataProvider(Protocol):
    api_version: int
    name: str                                         # "microchip_atdf"
    def matches(self, info: BuildInfo) -> bool: ...   # responsible for this build?
    def locate(self, info: BuildInfo) -> Path | None: ...  # where is the device file?
    def load(self, path: Path) -> DeviceModel: ...
```

- `BuildInfo` holds what the core reads from the build: `CONFIG_SOC`, `CONFIG_SOC_SERIES`,
  `CONFIG_SOC_FAMILY`, the vendor from `soc.yml`/`board.yml`, the west workspace and the module
  directories.
- **Finding the file is entirely the provider's job.** Pack paths such as `.mchp_packs` or
  `hal_microchip/packs` exist only inside the ATDF provider.

**Pinctrl formatter interface:** `compatibles`, `to_overlay(signal) -> str`,
`from_overlay(value) -> pad id` and `render_group(signals) -> dts text`. A formatter checks every
generated name against the pinctrl header or `.dtsi` it targets.

**Recipe interface:** `compatibles`, `pin_roles(instance)`, `derived_props(assignment)`,
`kconfig()`, and the reverse direction `lift(props, pins) -> roles`. For example, `lift` turns
`rxpo = <1>`, `txpo = <0>` plus the chosen pads back into "RX on PB24, TX on PB25". Porting needs
both directions.

### Neutral device model

The model is deliberately small. Everything vendor-specific is either opaque to the core or sits in
`attrs`:

```python
@dataclass
class DeviceModel:
    name: str                       # "ATSAME54P20A"
    packages: list[Package]         # pad id -> package pin/ball
    instances: dict[str, Instance]  # "SERCOM2" -> Instance
    constraints: list[Constraint]

@dataclass
class Instance:
    name: str                       # "SERCOM2"
    kind: str                       # "SERCOM"
    base: int | None
    signals: list[Signal]

@dataclass
class Signal:
    role: str                       # "PAD0", "TX", "SDA", ...
    pad: str                        # opaque pin id: "PA09", "PA9", "GPIO_AD_B0_12", "P0.06"
    mux: object                     # opaque; only the matching formatter interprets it
    group: str | None = None        # e.g. the ATDF IOSET
    attrs: dict = field(default_factory=dict)

@dataclass
class Constraint:                   # e.g. "signals of one instance should share a group"
    kind: str
    severity: str                   # "warning" | "error"
    message: str                    # text supplied by the provider ("pads from different IOSETs …")
```

### What must not happen now

- **No ATDF or Microchip terms in the core or the UI.** IOSET is a generic group constraint in the
  core; the word "IOSET" and the warning text come from the ATDF provider.
- **No assumptions about pin names.** A pad is an opaque id. The provider supplies a display name
  and a sort key.
- **No mux knowledge in the core.** Function "D", AF7 or ALT2 is interpreted only by the formatter.
- **No hard dependency on a plugin.** Without a matching provider, everything that comes from the
  bindings still works: the form, the checks, and pins as free text with completion from the
  pinctrl header macros. So the tool is useful for every vendor before any vendor plugin exists.
- **The plugin API has a version.** Each plugin declares `api_version`. The core skips a plugin
  with an incompatible version and reports why, so community plugins never break silently.

### Binding translation

When source and target hardware are the same IP (SAME54 → PIC32CX SG, or the old → new SAM E5x
port), translating binding to binding is more precise than lifting to a device class and mapping
back. A translation covers one pair, e.g. `atmel,sam0-uart` → `microchip,sercom-g1-uart`:

- **compatible:** the target compatible.
- **Per property:** keep, rename, transform the value, drop, or "derive with the target recipe".
  Each property of the source binding must be listed. An unlisted property is reported, never
  silently dropped.
- **pinctrl:** not in the table. The source formatter's `from_overlay` gives the pads, the target
  formatter writes them again.
- **Clocks:** the source clock references (`atmel,sam0-gclk`/`-mclk`) become the target notation
  (`microchip,sam-d5x-e5x-clock`).
- **Kconfig:** old symbol → new symbol, where a symbol does not follow automatically from
  `DT_HAS_<compat>_ENABLED`.

Translations are mostly data, so they are written as **YAML files** with optional Python hooks for
value transforms. That keeps community contributions simple. If no translation exists for a pair,
porting falls back to the class-based mapping in *Porting*.

### Plugin discovery

1. **Built-in registry:** a decorator (`@register_provider`, `@register_formatter`,
   `@register_recipe`) for the plugins in the repo.
2. **Python entry points** (`dt_explorer.providers`, `dt_explorer.formatters`,
   `dt_explorer.recipes`) via `importlib.metadata.entry_points`. A vendor plugin can then ship as its
   own pip package, without a fork. This costs only a few lines and belongs in the first version.

The UI shows which plugins are loaded and which one handles the current build.

### Open source prerequisites

- A `LICENSE` file. Apache-2.0 matches Zephyr.
- A short `CONTRIBUTING.md`: "How to write a provider / formatter / recipe", with the Microchip
  plugins as the reference.
- Small vendor test files. The ATDF files in `hal_microchip` are Apache-2.0, so a trimmed excerpt
  can be committed as a test fixture.

## Code structure

The single file becomes a package. Update CLAUDE.md when this lands.

```
dt_explorer/
  core/
    build.py        BuildModel (today's code), BuildInfo
    device.py       neutral DeviceModel
    plugins.py      registry, entry points, api_version check
    draft.py        draft, render_overlay, suggest_kconfig
    validate.py     fast checks
    west_tools.py   New build / Full build / Verify with west
  ui/               NiceGUI: today's page plus Configure, Draft tab, pin map, New build
  providers/
    microchip_atdf.py
  formatters/
    atmel_sam0.py
    microchip_port_g1.py
  recipes/
    atmel_sam0_sercom.py
  translations/
    atmel_sam0_to_mchp_g1.yaml
tests/              per plugin, with small fixture files
dt_explorer.py      thin launcher, so dt_explorer.bat keeps working
```

### `core/build.py`

- Today's `BuildModel`. It also accepts a ready EDT object, not only `edt.pickle`. The preview can
  then reuse the existing traffic light and detail view unchanged.
- It produces the `BuildInfo` for the providers.

### `providers/microchip_atdf.py`

- `matches`: the SoC vendor is Microchip/Atmel.
- `locate`: `hal_microchip/packs` first, then the installed DFPs, as described in
  *Finding the ATDF automatically*.
- `load`: parses instances, signals (pad, function, IOSET) and package pinouts into the neutral
  model. The IOSET becomes `Signal.group` plus a `Constraint` with the IOSET warning text.

### `formatters/`

- `atmel_sam0.py`: pad `PB25`, function `D`, `SERCOM2/PAD0` gives `PB25D_SERCOM2_PAD0`. The DFP
  writes `PA09D` while Zephyr writes `PA9D`.
- `microchip_port_g1.py`: the same macro names, but they expand to `MCHP_PINMUX(b, 25, d, periph)`.
- Both check each generated name against the Zephyr pinctrl header. A name that does not exist is an
  error, not a guess.

### `core/draft.py`

- **Draft.** It stores the *intent* separately from the vendor-specific realisation, so it can be
  carried to another device:

  | Part | Example | Portable |
  |---|---|---|
  | Class | `uart` (from the base binding `uart-controller.yaml` in the binding's include chain) | yes |
  | Roles | TX, RX | yes |
  | Neutral properties | `current-speed = 115200` (declared in the base binding) | yes |
  | Use | `zephyr,console` (chosen), alias `my-uart` | yes |
  | Instance | `sercom2` | no |
  | Pin per role | TX → PB25, RX → PB24 | no (board hardware) |
  | Vendor properties | `rxpo`, `txpo` (declared only in the vendor binding) | no, derived again by the target's recipe |

  Child nodes follow later. The draft is plain data, stored as JSON in the app folder:
  `<app>/.dt_explorer/<build folder name>.draft.json` (e.g. `build_same54_xpro.draft.json`). It
  survives a reload and a pristine rebuild (`west build -p`), and it can be committed with the app
  if wanted.
- **Without a recipe**, the configurator shows the generic form generated from the binding. Pin
  roles then come straight from the provider's signal roles.
- **`render_overlay(draft)`** returns the overlay text through the matching formatter.
- **`suggest_kconfig(draft)`** returns the `prj.conf` lines, from two sources:
  1. the recipe's fixed table, where a recipe exists;
  2. otherwise, derived from Kconfig. Load the Kconfig tree with Zephyr's own `kconfiglib`
     (`zephyr/scripts/kconfig/kconfiglib.py`, from the analysed workspace, like edtlib). Find the
     symbol whose `default`/`depends on` references `DT_HAS_<COMPAT>_ENABLED`, and follow its
     `depends on` to the subsystem symbols (e.g. `CONFIG_I2C`) that are not yet `y` in the build's
     `zephyr/.config`.

  When both exist, the recipe wins. The derived suggestions are shown separately for comparison,
  so a wrong recipe table becomes visible.

### `recipes/atmel_sam0_sercom.py`

Named pin roles, derived properties (`rxpo`/`txpo` from the chosen pads; for SPI `dipo`/`dopo`) and
the Kconfig table (`CONFIG_SERIAL`, `CONFIG_I2C`, `CONFIG_SPI`, …) for `atmel,sam0-uart`,
`atmel,sam0-i2c` and `atmel,sam0-spi`. Driver knowledge lives here; it is in neither the binding
nor the device file.

### `core/west_tools.py`

Runs every west call the tool makes: "New build…" and "Full build" (see *Boards and builds*), and
"Verify with west" (see *Checks*). Each call runs as a subprocess in the background, with its
output streamed to a log panel. The board list is read from `board.yml` files and does not call
`west boards`.

## Checks

1. **Fast check, on every change.**
   - Append the overlay text to `zephyr.dts.pre` and evaluate it with edtlib and the known binding
     directories. edtlib reports the same binding errors as `west build`: missing required
     properties, wrong types, unknown compatibles.
   - Macros such as `PB25D_SERCOM2_PAD0` and `GPIO_ACTIVE_LOW` are resolved through the constant
     table the explorer already reads from headers (`header_defines`), extended by the pinctrl
     header.
   - Our own checks:
     - **Pin conflict.** A pin is already used by the `pinmux` of another `pinctrl-0` or by a GPIO
       phandle.
     - **Provider constraints**, e.g. the ATDF IOSET rule: pads of one peripheral come from
       different IOSETs. This is a **warning**, not an error: the pin is highlighted yellow with the
       provider's reason, and the snippet is still generated. The SAM D5x/E5x datasheet only
       guarantees timing within one IOSET; the exact wording has not been checked yet. Make it
       stricter only once it has. Not every device family has IOSETs (no `ioset` attribute in the
       ATDF means no constraint).
     - **Missing required properties.**
2. **Authoritative check, "Verify with west" button.**
   - Runs `west build --cmake-only` into a separate scratch build directory with
     `-DEXTRA_DTC_OVERLAY_FILE=draft.overlay -DEXTRA_CONF_FILE=draft.conf`.
   - It takes about 10–20 s and runs exactly the checks Zephyr runs. The user's own build is not
     touched.

**Applying the result.** By default the user copies the snippet. Optionally the tool writes it to
its own files (`dt_explorer.overlay` / `dt_explorer.conf` in the app folder). It never rewrites the
board overlay, which would risk losing comments and formatting.

## Boards and builds

Today the explorer only shows builds that already exist: every folder with `zephyr/edt.pickle`
appears in the build list. Two extensions are planned.

### Choosing an existing Zephyr board ("New build…")

- **Dialog.** Choose an app and a board. The tool then runs
  `west build -b <board> -d <app>/build_<board> --cmake-only`. Afterwards `find_builds()` picks up
  the new folder on its own.
- **`--cmake-only` is enough (checked 2026-10-14 with lab1/`same54_xpro`, 25 s).** The configure
  step creates every file the explorer reads except `zephyr.elf` and `zephyr.map`: `edt.pickle`,
  `zephyr.dts`, `zephyr.dts.pre`, `.config`, `compile_commands.json`, `devicetree_generated.h`,
  `zephyr_modules.txt` and `CMakeCache.txt`. Compared with the full build of the same app, the
  explorer finds the same 86 nodes and the same 112 compiled files. The only difference is the 7
  nodes that are `device` in the full build, which would be `nodevice` without the ELF.
- **Done (2026-10-14): state "not linked yet".** `BuildModel.linked` is false when neither
  `zephyr.elf` nor `zephyr.map` exists. `state()` then returns `notlinked` (cyan) instead of
  `nodevice`, and `_device_ords()` no longer crashes on the missing map. Checked with the
  cmake-only builds of lab1 for `same54_xpro` and `sam_e54_xpro`.
- **Board list.** Every `board.yml` with `vendor: microchip` or `vendor: atmel`; there are 27 in the
  current Zephyr tree. This matches the scope "generic across Microchip devices".
- **New state "not linked yet".** Without `zephyr.elf` the tool cannot tell whether a device made it
  into the image. `BuildModel.state()` must return its own state for that case instead of red or
  orange. A **"Full build"** button runs the complete `west build` when the user wants the full
  traffic light.
- **App overlays are per board.** Zephyr looks up the app overlay by board name. So
  `apps/lab1/boards/same54_xpro.overlay` does **not** apply to a `sam_e54_xpro` build. The
  configurator can transfer an overlay to the other board, converting the pinctrl notation on the
  way. This is the comparison planned in phase 5.

### Creating a custom board (own hardware)

A Zephyr board consists of `board.yml`, `<board>.dts`, `<board>-pinctrl.dtsi`, `Kconfig.<board>`,
`<board>_defconfig` and usually `board.cmake`. A wizard creates this skeleton in the app folder
under `boards/<vendor>/<board>/`:

1. Choose the SoC. That selects the SoC `.dtsi` and the ATDF.
2. Choose the package, the console UART, the clock and the flash partitions.
3. Configure further peripherals. This is the configurator from phase 3, applied to the board
   `.dts` instead of an overlay.

Zephyr finds the board through `BOARD_ROOT` or the app's `zephyr/module.yml`.

## Porting

Moving an app from one device or vendor to another is the configurator run in both directions. It
works only because the draft stores intent (see `core/draft.py`).

If a *binding translation* exists for a source/target pair (same hardware IP, e.g. SAME54 → PIC32CX
SG), it replaces the class-based mapping in step 2. Lift, the formatters and the report stay the
same. The class-based mapping below is the fallback for pairs without a translation.

1. **Lift.** Read the source build's overlay and board `.dts` back into intent:
   - the formatter's `from_overlay` gives the physical pins,
   - the recipe's `lift` gives the roles,
   - the binding include chain gives the class (`uart-controller.yaml`, `i2c-controller.yaml`,
     `spi-controller.yaml`, …), and the base binding tells which properties are neutral.
2. **Map.** On the target build, find a free instance of the same class (e.g. SERCOM2 → USART1).
   The target's provider offers pins per role, and the target's recipe derives the vendor
   properties.
3. **Report what cannot be mapped automatically:**
   - **Node labels in app code.** `DT_NODELABEL(sercom2)` in `src/` is vendor-specific. The
     explorer already finds these references (the Overview). The tool suggests replacing them with
     `DT_ALIAS`/`DT_CHOSEN` and generates the matching alias in the target overlay, so the code
     becomes portable.
   - **Vendor properties without a counterpart** on the target.
   - **Kconfig differences** (driver symbols, subsystem options).
   - **Pins.** They depend on the target board's hardware, so the tool can only suggest them.

The output is the target overlay, the `prj.conf` lines and the report. Nothing in the source app
is changed.

## Phases

1. **Package layout, plugin registry and pin map (read-only).** First move today's code into the
   package and add the neutral device model, the plugin registry with entry points and the API
   version check. Only then write the ATDF provider and the `atmel,sam0-pinctrl` formatter as the
   first plugins. The pin map tests all of this against the SAM E54 labs. This phase brings the
   first pytest tests into the repo, plus `LICENSE` and `CONTRIBUTING.md`.
2. **"New build…" with board selection**, `--cmake-only`, the "not linked yet" state and
   "Full build". It comes early because phase 5 needs a `sam_e54_xpro` build anyway.
3. **SERCOM configurator for UART/I2C/SPI**, with the Draft tab and the fast check.
4. **"Verify with west"** and writing to a file. It comes before porting, because a port must be
   checked by Zephyr itself.
5. **Translate the driver family (core of the main use case).** `same54_xpro` → `sam_e54_xpro`
   (`boards/microchip/sam/sam_e54_xpro`): the new Microchip port of the same hardware, using
   `microchip,port-g1-pinctrl` and the `microchip,*-g1` drivers. The same physical board is enough;
   the generated result can be compared one to one with phase 3. This phase delivers:
   - the "Port to board…" UI,
   - the first binding translations `atmel,sam0-*` → `microchip,*-g1` for SERCOM (UART/I2C/SPI),
     GPIO, pinctrl and clocks,
   - the `microchip,port-g1-pinctrl` formatter,
   - the transfer of the lab's `same54_xpro.overlay` to `sam_e54_xpro`,
   - the label report (`DT_NODELABEL` → `DT_ALIAS`/`DT_CHOSEN`).

   **Prerequisite:** the module `hal_microchip` (`modules/hal/microchip`). Without it the build
   fails while preprocessing the board's own `sam_e54_xpro-pinctrl.dtsi`
   (`dt-bindings/sam/sam_d5x_e5x/atsame54/atsame54p20a-pinctrl.h: No such file or directory`).
   In the RTOS4 workspace it was fetched on 2026-10-14 with `west update hal_microchip`; the
   cmake-only build of lab1 for `sam_e54_xpro` then succeeds. Its pinctrl names look like the old
   port's (`PB25D_SERCOM2_PAD0`), but expand to `MCHP_PINMUX(b, 25, d, periph)`.
6. **Port to PIC32CX SG41/SG61** in the current workspace `C:\Masters\zephyr-main`: the device
   change within the new family, with source builds from the RTOS4 workspace. Target boards are
   `pic32cx_sg41_cult` and `pic32cx_sg61_cult`. Acceptance test: the RTOS4 labs build for both, and
   the SG61 report lists exactly the conflicts with PB26–PB29/PC04.
7. **Child nodes on buses** (e.g. a sensor on I2C: `reg`, compatible from the bindings with
   `on-bus: i2c`) and the generic binding form for other peripherals (ADC, TC, …). The matching
   translations follow with them.
8. **Custom board wizard** (see *Creating a custom board*).
9. **Class-based porting** for pairs without a translation, up to other vendors (see *Porting*).

## Decisions (2026-10-14)

| Topic | Decision |
|---|---|
| Draft location | App folder: `<app>/.dt_explorer/<build folder name>.draft.json` |
| Kconfig suggestions | Both: recipe table where one exists, otherwise derived with kconfiglib; the recipe wins |
| IOSET violation | Warning, not an error, until the datasheet wording is checked |
| Board for phase 5 | `sam_e54_xpro` (same hardware as `same54_xpro`, `microchip,port-g1-pinctrl`) |
| Other vendors | Not implemented, but possible through plugins (provider, formatter, recipe); the core never reads a vendor file |
| Draft format | Intent-based: class, roles, neutral properties and use, kept apart from instance, pins and vendor properties |
| Main use case | Port SAME54 projects (old `atmel` port) to PIC32CX SG41/SG61: first translate the driver family, then change the device |
| Same-IP porting | Direct binding translation tables (YAML), not class-based mapping |
| Zephyr for PIC32CX SG | Second workspace `C:\Masters\zephyr-main` with current Zephyr; the RTOS4 workspace stays unchanged |

## Open questions

- Which Zephyr-supported Microchip families have an ATDF in their DFP? MEC, Mi-V, SAMA7G5 and
  PIC32CX-BZ are still to be checked.
- Do the Zephyr pinctrl names follow the ATDF names consistently in every family, apart from the
  leading zero, or are there exceptions?
- The board-level pin map: which header pins of the board connect to which pads? This is not in
  the ATDF; it would need the board user guide or a hand-written table.
- "Edit pins…" for nodes that are already enabled is not assigned to a phase yet.
