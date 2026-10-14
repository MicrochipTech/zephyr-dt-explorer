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
2. **Pack roots**, searched in this order:
   - `%USERPROFILE%\.mchp_packs`, where the pack manager of MPLAB X and the MPLAB VS Code
     extensions downloads packs,
   - `C:\Program Files\Microchip\MPLABX\*\packs`.
3. **Pattern** `<root>\Microchip\*_DFP\<version>\atdf\*<SOC>.atdf`, matched case-insensitively.
   SAM devices carry an `AT` prefix (`ATSAME54P20A.atdf`), PIC32C devices do not.
4. **Version:** take the newest with a *natural* sort (`3.11.261` > `3.9.244`), not an
   alphabetical one.
5. **Search with the pattern, never recursively.** A recursive `find` over the pack roots took
   about 10 s on this machine.
6. **Fallback:** a manual path in `~/.dt_explorer.json` or a file dialog. Downloading the
   `.atpack` (a ZIP file) from `packs.download.microchip.com` would also work, but only on an
   explicit button press, never automatically.

Tested on this machine: for lab1, the lookup finds
`~/.mchp_packs/Microchip/SAME54_DFP/3.11.261/atdf/ATSAME54P20A.atdf`, plus older versions and the
copy in the MPLAB X installation.

Zephyr itself ships no ATDF files, so the configurator needs installed packs.

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
  child nodes. It is plain data, stored per build as JSON so it survives a reload.
- **Recipes per driver compatible.**
  - Without a recipe, the configurator shows the generic form generated from the binding. Pin roles
    then come straight from the ATDF signal names.
  - A recipe such as `atmel,sam0-uart` adds named pin roles, derived properties (`rxpo`/`txpo` from
    the chosen pads; for SPI `dipo`/`dopo`) and Kconfig suggestions (`CONFIG_SERIAL`, `CONFIG_I2C`,
    `CONFIG_SPI`, …).
  - Driver knowledge lives in the recipes. It is in neither the binding nor the ATDF.
- **`render_overlay(draft)`** returns the overlay text. **`suggest_kconfig(draft)`** returns the
  `prj.conf` lines.
- **`validate(draft)`** runs the checks described below.

### `dt_explorer.py`

- UI only: the "Configure…" button, the form, the Draft tab, the pin map and the badge.
- Refactor: `BuildModel` must also accept a ready EDT object, not only `edt.pickle`. The preview
  can then reuse the existing traffic light and detail view unchanged.

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
     - **IOSET.** Pads of one peripheral come from different IOSETs. The SAM D5x/E5x datasheet only
       guarantees timing within one IOSET; check the exact wording before turning this into an
       error rather than a warning. Not every device family has IOSETs.
     - **Missing required properties.**
2. **Authoritative check, "Verify with west" button.**
   - Runs `west build --cmake-only` into a separate scratch build directory with
     `-DEXTRA_DTC_OVERLAY_FILE=draft.overlay -DEXTRA_CONF_FILE=draft.conf`.
   - It takes about 10–20 s and runs exactly the checks Zephyr runs. The user's own build is not
     touched.

**Applying the result.** By default the user copies the snippet. Optionally the tool writes it to
its own files (`dt_explorer.overlay` / `dt_explorer.conf` in the app folder). It never rewrites the
board overlay, which would risk losing comments and formatting.

## Phases

1. **Pin map, read-only.** This tests ATDF discovery, the ATDF parser and the `atmel,sam0-pinctrl`
   formatter against the SAM E54 labs. It also brings the first pytest tests into the repo.
2. **SERCOM configurator for UART/I2C/SPI**, with the Draft tab and the fast check.
3. **Second family as a test that the design is really generic.** Candidates are PIC32CX SG or the
   new `microchip,port-g1-pinctrl` SAM E5x port. A board build for it is needed, because the
   explorer only reads build folders.
4. **"Verify with west"** and writing to a file.
5. **Child nodes on buses** (e.g. a sensor on I2C: `reg`, compatible from the bindings with
   `on-bus: i2c`) and the generic binding form for other peripherals (ADC, TC, …).

## Open questions

- Which Zephyr-supported Microchip families have an ATDF in their DFP? MEC, Mi-V, SAMA7G5 and
  PIC32CX-BZ are still to be checked.
- Do the Zephyr pinctrl names follow the ATDF names consistently in every family, apart from the
  leading zero, or are there exceptions?
- Where to store the draft: next to the build, in the app folder, or in `~/.dt_explorer.json`?
- The board-level pin map: which header pins of the board connect to which pads? This is not in
  the ATDF; it would need the board user guide or a hand-written table.
- Kconfig suggestions: derive them from the driver's `Kconfig` (`depends on`, `default y if
  DT_HAS_…`), or keep a fixed table per recipe?
