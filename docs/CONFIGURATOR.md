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
that Zephyr supports and for which a DFP with an ATDF exists. The SAM E54 is only the first test
case.

Out of scope:
- other vendors (ST, NXP, Nordic, …),
- clock trees, electrical checks and DMA channel allocation,
- Microchip families without an ATDF. MEC and Mi-V are likely in this group; this still needs to be
  confirmed.

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
  3. Choose a pin for each role (TX/RX, SDA/SCL, MOSI/MISO/SCK/CS). The choices come from the ATDF.
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

## Code structure

The configurator is a separate concern that can be tested without the GUI, so it goes into its own
modules. This breaks the current single-file layout, so update CLAUDE.md when it lands. The core
contains no device-specific code. Family and driver knowledge sit in two small, clearly separated
places: the pinctrl formatters and the recipes.

### `atdf.py` (new, no NiceGUI, the same for every Microchip device)

- Finds the ATDF as described above.
- Parses instances, signals (group, index, function, pad, IOSET) and package pinouts. The result
  is a device-independent model: *instance → signal → possible pins*.

### `pinctrl_fmt.py` (new, one function per pinctrl binding)

- The function is selected by the compatible of the build's pinctrl node (`atmel,sam0-pinctrl`,
  `atmel,sam-pinctrl`, `microchip,port-g1-pinctrl`, …).
- It answers two questions:
  - **ATDF signal → overlay notation**, e.g. pad `PB25`, function `D`, `SERCOM2/PAD0` gives
    `PB25D_SERCOM2_PAD0`. Note that the DFP writes `PA09D` while Zephyr writes `PA9D`.
  - **Existing overlay value → physical pin**, which the pin map and the conflict check need.
- It checks each generated name against the family's Zephyr pinctrl header or `.dtsi`. A name that
  does not exist there is an error, not a guess.

### `configurator.py` (new, no NiceGUI)

- **Draft.** For each node: the chosen compatible, property values, the pin for each role, and later
  child nodes. It is plain data, stored as JSON in the app folder:
  `<app>/.dt_explorer/<build folder name>.draft.json` (e.g. `build_same54_xpro.draft.json`). It
  survives a reload and a pristine rebuild (`west build -p`), and it can be committed with the app
  if wanted.
- **Recipes per driver compatible.**
  - Without a recipe, the configurator shows the generic form generated from the binding. Pin roles
    then come straight from the ATDF signal names.
  - A recipe such as `atmel,sam0-uart` adds named pin roles, derived properties (`rxpo`/`txpo` from
    the chosen pads; for SPI `dipo`/`dopo`) and Kconfig suggestions (`CONFIG_SERIAL`, `CONFIG_I2C`,
    `CONFIG_SPI`, …).
  - Driver knowledge lives in the recipes. It is in neither the binding nor the ATDF.
- **`render_overlay(draft)`** returns the overlay text. **`suggest_kconfig(draft)`** returns the
  `prj.conf` lines, from two sources:
  1. the recipe's fixed table, where a recipe exists;
  2. otherwise, derived from Kconfig. Load the Kconfig tree with Zephyr's own `kconfiglib`
     (`zephyr/scripts/kconfig/kconfiglib.py`, from the analysed workspace, like edtlib). Find the
     symbol whose `default`/`depends on` references `DT_HAS_<COMPAT>_ENABLED`, and follow its
     `depends on` to the subsystem symbols (e.g. `CONFIG_I2C`) that are not yet `y` in the build's
     `zephyr/.config`.

  When both exist, the recipe wins. The derived suggestions are shown separately for comparison,
  so a wrong recipe table becomes visible.
- **`validate(draft)`** runs the checks described below.

### `west_tools.py` (new, no NiceGUI)

Runs every west call the tool makes: "New build…" and "Full build" (see *Boards and builds*), and
"Verify with west" (see *Checks*). Each call runs as a subprocess in the background, with its
output streamed to a log panel. The board list is read from `board.yml` files and does not call
`west boards`.

### `dt_explorer.py`

- UI only: the "Configure…" button, the form, the Draft tab, the pin map, the badge and the
  "New build…" dialog.
- Refactor: `BuildModel` must also accept a ready EDT object, not only `edt.pickle`. The preview
  can then reuse the existing traffic light and detail view unchanged.
- New traffic-light state "not linked yet" for builds without `zephyr.elf` (see below).

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
     - **IOSET.** Pads of one peripheral come from different IOSETs. This is a **warning**, not an
       error: the pin is highlighted yellow with the reason, and the snippet is still generated.
       The SAM D5x/E5x datasheet only guarantees timing within one IOSET; the exact wording has not
       been checked yet. Make it stricter only once it has. Not every device family has IOSETs
       (no `ioset` attribute in the ATDF means no check).
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
  way. This is the comparison planned in phase 4.

### Creating a custom board (own hardware)

A Zephyr board consists of `board.yml`, `<board>.dts`, `<board>-pinctrl.dtsi`, `Kconfig.<board>`,
`<board>_defconfig` and usually `board.cmake`. A wizard creates this skeleton in the app folder
under `boards/<vendor>/<board>/`:

1. Choose the SoC. That selects the SoC `.dtsi` and the ATDF.
2. Choose the package, the console UART, the clock and the flash partitions.
3. Configure further peripherals. This is the configurator from phase 3, applied to the board
   `.dts` instead of an overlay.

Zephyr finds the board through `BOARD_ROOT` or the app's `zephyr/module.yml`.

## Phases

1. **Pin map, read-only.** This tests ATDF discovery, the ATDF parser and the `atmel,sam0-pinctrl`
   formatter against the SAM E54 labs. It also brings the first pytest tests into the repo.
2. **"New build…" with board selection**, `--cmake-only`, the "not linked yet" state and
   "Full build". It comes early because phase 4 needs a `sam_e54_xpro` build anyway.
3. **SERCOM configurator for UART/I2C/SPI**, with the Draft tab and the fast check.
4. **Second family as a test that the design is really generic.** Use the board **`sam_e54_xpro`**
   (`boards/microchip/sam/sam_e54_xpro`). It is the new Microchip port of the same hardware as
   `same54_xpro`, but it uses `microchip,port-g1-pinctrl`. So the same physical board and the
   installed SAME54 pack are enough; only a second build of a lab with `-b sam_e54_xpro` is
   needed. **Prerequisite:** the module `hal_microchip` (`modules/hal/microchip`). Without it the
   build fails while preprocessing the board's own `sam_e54_xpro-pinctrl.dtsi`
   (`dt-bindings/sam/sam_d5x_e5x/atsame54/atsame54p20a-pinctrl.h: No such file or directory`).
   In the RTOS4 workspace it was fetched on 2026-10-14 with `west update hal_microchip`; the
   cmake-only build of lab1 for `sam_e54_xpro` then succeeds. Its pinctrl names look like the old
   port's (`PB25D_SERCOM2_PAD0`), but expand to `MCHP_PINMUX(b, 25, d, periph)`. Because the hardware is the same, the generated pins can be compared one to one with
   phase 3. This phase includes transferring the lab's `same54_xpro.overlay` to `sam_e54_xpro`.
   A different device (`pic32cx_sg61_cult`, `pic32cm_jh01_cnano`) can follow later; the matching
   packs are not installed yet.
5. **"Verify with west"** and writing to a file.
6. **Child nodes on buses** (e.g. a sensor on I2C: `reg`, compatible from the bindings with
   `on-bus: i2c`) and the generic binding form for other peripherals (ADC, TC, …).
7. **Custom board wizard** (see *Creating a custom board*).

## Decisions (2026-10-14)

| Topic | Decision |
|---|---|
| Draft location | App folder: `<app>/.dt_explorer/<build folder name>.draft.json` |
| Kconfig suggestions | Both: recipe table where one exists, otherwise derived with kconfiglib; the recipe wins |
| IOSET violation | Warning, not an error, until the datasheet wording is checked |
| Board for phase 4 | `sam_e54_xpro` (same hardware as `same54_xpro`, `microchip,port-g1-pinctrl`) |

## Open questions

- Which Zephyr-supported Microchip families have an ATDF in their DFP? MEC, Mi-V, SAMA7G5 and
  PIC32CX-BZ are still to be checked.
- Do the Zephyr pinctrl names follow the ATDF names consistently in every family, apart from the
  leading zero, or are there exceptions?
- The board-level pin map: which header pins of the board connect to which pads? This is not in
  the ATDF; it would need the board user guide or a hand-written table.
- "Edit pins…" for nodes that are already enabled is not assigned to a phase yet.
