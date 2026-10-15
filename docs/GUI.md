# Devicetree Explorer — GUI Guide

This guide walks through every part of the Devicetree Explorer user interface. The screenshots
come from the MASTERs 2026 class *26020 RTOS4* (SAM E54 Xplained Pro, Zephyr 4.3). To regenerate
them, run `docs/make_screenshots.py`.

The window has three areas:

![Full window](img/node_led.png)

| Area | Content |
|---|---|
| **Header** (top) | Build selection, reload, New build…, Full build, Port to board…, Legend |
| **Left panel** | Tabs: **Nodes** (the devicetree), **Include tree** (the source files), **Pins**, **Package** (the chip's pins), **Draft** (pending configurator changes) |
| **Right panel** | Details of whatever is selected on the left; the overview when nothing is selected |

---

## 0. Start

When started without an argument, the tool first opens a **folder dialog**. Select a west
workspace, an app folder or a single build folder; every build below it is offered in the
**Build** list. The dialog starts in the last folder you used. With a path argument
(`dt_explorer.bat D:\ws`), the tool skips the dialog.

## 1. Header

![Header](img/el_header.png)

- **Folder button** (left): opens the folder dialog again and switches to another workspace
  without restarting. Its tooltip shows the current folder.
- **Build**: lists every Zephyr build folder found under the workspace, i.e. every folder that
  contains `zephyr/edt.pickle`. Picking an entry loads that build.
- **Reload**: scans for builds again and reloads the current one. Press it after every
  `west build`.
- **New build…** and **Full build**: start a build from the explorer (see below).
- **Port to board…**: ports the app of the current build to another board (section 1.3).
- **Legend** (right end of the header): a menu with the text colours used in both trees and in the
  *Origin* column. It tells you **which kind of file** something comes from. Click it to open the key:

![Legend menu](img/legend_menu.png)


| Colour | Category | Typical files |
|---|---|---|
| green | Overlay (app) | `apps/<lab>/boards/<board>.overlay` |
| blue | Board | `zephyr/boards/.../<board>.dts`, `...-pinctrl.dtsi` |
| grey | SoC / Zephyr | `zephyr/dts/arm/...dtsi`, `zephyr/include/.../dt-bindings/*.h` |
| purple | Module | `modules/...` (e.g. pinctrl headers from `hal_atmel`) |
| yellow | Binding default | Value written in no `.dts` file; it comes only from `default:` in the binding YAML |

### 1.1 New build…

![New build dialog](img/new_build_dialog.png)

Creates a new build, for example for another app or board, without leaving the explorer.

- **App folder** and **Board** (with a filter): the app to build and the board to build it for.
- **Build folder name**: defaults to `build_<board>`; the full path is shown below it.
- **Start**: runs a cmake-only build, which is quick but does not link the image. Its devices show
  the cyan *Driver built, not linked yet* state until a full build has run (see 1.2).
- When the build finishes, the list is refreshed and the new build is selected.
- **Cancel build** stops a running build; **Close** hides the dialog.

### 1.2 Full build

**Full build** runs `west build -d <build folder>` for the build selected in the header. The log
streams into the dialog. When the build succeeds, the explorer reloads it: the cyan states turn into
green (device created) or red (driver built, no device). Use it after a cmake-only build, or after
changing the app. **Cancel build** stops it.

![Full build dialog](img/full_build_dialog.png)

### 1.3 Port to board…

![Port to board dialog](img/port_dialog.png)

**Port to board…** takes the app of the current build and shows what it would look like on another
board of the same app: the overlay and `.conf` lines for the target, and a report of what could not be
carried over. The source build is not changed, and the dialog writes nothing into the app.

- **Target build**: a build of the same app for another board (the list shows only builds of a different
  board). **Create target build…** opens New build… for it; a cmake-only build is enough. Or type a build
  folder path below the list.
- **Run port**: runs the port. The log shows the steps, and the result appears below the log.

The result has these parts:

- **Target overlay** and **Target .conf**: the text for the target, with **Copy** and **Download**.
- **Port report**: numbered sections, each one expandable:

| Section | Content |
|---|---|
| 1. Nodes: translated and untranslatable properties | Each node of the source and its translation to the target's binding. Lines say what is identical, what is not emitted, or why a node is not ported (`no_target`: the target workspace has no binding or driver for it; `no_node`: the target build has no such node) |
| 2. Kconfig differences | Each `prj.conf` line of the source with its value in the source and in the target `.config`, e.g. a symbol that the target Kconfig tree does not define |
| 3. Node references in src/ that the target does not resolve | Each `DT_NODELABEL(...)` / `DT_ALIAS(...)` of the app that the target lacks, with a suggestion |
| 4. Pins not available on the target SoC | Pins that the source uses but the target package does not have |
| 5. Nodes without translation and not representable children | Nodes that have to be carried over by hand, and child nodes that the draft cannot represent yet |

  Below the sections, **Fast check on the target build** lists the findings of the same check as in the
  Draft tab (section 11) on the ported overlay.

![Port report](img/port_report.png)

The example ports lab1 (SAME54 Xplained Pro) to the PIC32CX SG61 test build in `_soltest`. The report
lists the SERCOMs that have no counterpart there, the `DT_NODELABEL(my_led0)` of the app that the target
does not define (with the alias suggestion), and the PB26/PB27/PB29 pins that are not on the TQFP128 package.

- **Verify with west**: runs Zephyr's own check of the port on the target: a cmake-only build of the target
  board into a scratch folder with the target overlay and `.conf` (about 30 s). The target build is not
  touched. A green line says that Zephyr accepts the port; a red line says it rejects it, and the errors are
  shown under the log.

![Verify with west on the target](img/port_verify.png)

---

## 2. Warning banner: driver without a node

![Orphan driver warning](img/el_orphan.png)

This banner appears above everything else when a driver **from the app or a module** was compiled,
but no devicetree node with that `compatible` has `status = "okay"`. The build succeeds anyway,
but **no device is created**, and the app does nothing.

Typical causes:
- The overlay was edited in the editor but not saved, so it is still empty on disk.
- `compatible` is misspelled.
- The node has `status = "disabled"`.

The file path in the banner opens the driver source.

---

## 3. Left panel — tab **Nodes**

![Node tree](img/el_tree.png)

- **Filter**: shows only nodes whose label contains the text, e.g. `sercom` or `led`.
- **Tree**: the **final** devicetree after board, SoC and overlay have been merged. The entries
  read `label: name@unit-address`, e.g. `my_led0: my_led@41008000`. A label is what C code
  uses in `DT_NODELABEL(...)`.
- **Text colour**: the file that **defines** the node (see legend).
- **Bold + `overlay` badge**: the node is defined somewhere else, but the app overlay **changed**
  it, e.g. set `status = "okay"`.
- **Dot**: traffic light that shows whether the node became a device:

![Traffic light legend](img/el_legend.png)

| Dot | Meaning | Is it a problem? |
|---|---|---|
| green: *Device created* | The image contains `__device_dts_ord_<n>`. | — |
| orange: *Driver not enabled* | A driver exists, but Kconfig did not enable it. | Usually fine. Only a problem if the app needs this hardware. |
| red: *Driver built, no device* | The driver was compiled, but no device exists for this node. | Yes. Check the driver macro and the binding. |
| cyan: *Driver built, not linked yet* | The driver was compiled, but the build has no `zephyr.elf`/`zephyr.map` yet (e.g. `west build --cmake-only`), so it is unknown whether the device is in the image. | No. Run a full build and press Reload. |
| dark grey: *Disabled* | `status = "disabled"`. | Only if you expected it to be on. |
| blue-grey: *No driver* | CPU, memory, a container, or a child node that the parent or the app reads directly. | Usually fine. |
| deep orange: *No binding found* | The node has a `compatible`, but no binding YAML matches it. Zephyr does not evaluate its properties and generates no `DT_PROP()` macros. | Yes, if your code uses the node. Typical causes: a typo in `compatible`, or a missing `dts_root` / `dts/bindings` folder for out-of-tree bindings. |

---

## 4. Right panel — overview (nothing selected)

![Code references](img/el_coderefs.png)

The overview lists every `DT_NODELABEL(...)`, `DT_ALIAS(...)` and `DT_CHOSEN(...)` in the app's
`src/` folder. Each card shows:

- **File:line** (blue): opens the C source at that line.
- **The code line** itself.
- **Macro → node**: the devicetree node that the macro resolves to. Click the node to open it.
  The badge shows its traffic-light state.
- If a macro cannot be resolved, the card says **"not in the devicetree!"** in red. The usual
  cause is an unsaved overlay or a misspelled label or alias.

---

## 5. Right panel — node details

### 5.1 Title, origin and status

![Node header and status](img/el_node_head.png)

- **Title**: all labels (`&my_led0`), the node name, and the full path.
- **Origin badge + "defined in"**: the file and line that create the node. Click it to open the
  source. All lines of this node are marked there.
- **Status card**: the traffic-light state with an explanation. **Driver** shows the C file whose
  `#define DT_DRV_COMPAT` matches this node, and whether it was compiled. Click the path to open
  the driver at its `DT_DRV_COMPAT` line.
- **Configure…** button: shown on a disabled node of a supported peripheral. It opens the
  configurator (section 10).
- **Edit pins…** button: shown on an enabled node of a supported peripheral. It opens the same
  dialog, filled with the node's current pins (section 10.1).

For child nodes without their own `compatible` (for example a button below `gpio-keys`), the
card explains that the parent's binding describes them and that the app reads them directly:

![Child node](img/el_child_state.png)

### 5.2 compatible / binding

![Binding card](img/el_binding.png)

The example shows the I2C temperature sensor of lab3, whose binding lives in the app.

- **`compatible`** strings of the node and the **description** from the binding.
- **Binding facts** (shown when they apply):
  - `on-bus`: the node is a device on that bus, and its `reg` is the bus address (here I2C 0x4f).
  - `bus`: child nodes of this node are devices on that bus.
  - `<space>-cells`: the names of the cells that a reference passes. For example,
    `gpio-cells: pin, flags` explains why `<&portb 31 0x11>` means pin 31 with flags 0x11.
  - `child-binding`: the child nodes are described by this binding.
- **Binding file and its include: hierarchy**: the matched YAML file and every file it includes,
  recursively. Here the chain is `my-temp-sensor.yaml` → `i2c-device.yaml` → `base.yaml` / `pm.yaml`,
  plus `power.yaml`. Click any file to open it.
- **All N properties this binding allows** (expandable table):

| Column | Meaning |
|---|---|
| Type, Required | As declared in the binding |
| In this node | **set** (written in a `.dts` file) · **default** (only the binding's `default:` applies) · **not set** |
| Default, Allowed values | `default:` and `enum:` / `const:` from the binding |
| Declared in | Which YAML file in the hierarchy declares the property. Click it to open the file at the declaration |

  The table shows set, required and own properties. The switch adds the generic, unset
  properties that come from `base.yaml` and similar files.

In this example the binding default of `resolution-bits` is 12, and the overlay sets it to 9.

If no binding matches, the card says so and the status card turns deep orange:

![No binding found](img/el_nobinding.png)

Below the card, **Registers** shows the decoded `reg` entries (address and size).

### 5.3 Properties

![Properties table](img/el_props.png)

| Column | Meaning |
|---|---|
| Property | Name as in the devicetree |
| As written | Value as it appears in `zephyr.dts`, with references like `&portb` |
| Interpreted | Value as Zephyr understands it through the binding. Numbers are shown in decimal and hex, references are resolved, and GPIO flags are decoded, e.g. `0x11 = ACTIVE_LOW \| PULL_UP` |
| Origin | Category of the file that **set this value last** (see legend). **Binding default** rows (yellow) list values that no `.dts` file contains. They still end up in the generated C macros. |
| File:line | That exact location. **Click** to open the source |
| Meaning (binding) | First line of the property description in the binding |

One node can mix several origins. On `button_0` in lab0, for example, `gpios` comes from the
board and `status` comes from the overlay.

### 5.4 References, C code and generated macros

![C code and macros](img/el_code_macros.png)

- **Depends on / Used by**: other nodes that this node references (for example the GPIO port),
  and nodes that reference this one. Click a name to jump to that node.
- **In the app's C code**: the lines in `src/` that use this node.
- **Generated C macros**: the `#define`s that Zephyr created in `devicetree_generated.h` for this
  node: the node identifier (`DT_N_S_...`), its ordinal, and the property macros
  (`..._P_<property>`). This is what `DT_PROP(...)` and the other macros expand to in the end.

---

## 6. Left panel — tab **Include tree**

![Include tree](img/el_include_tree.png)

This tab shows which files include which, in the order the C preprocessor processed them. The tool
rebuilds it from the line markers in `zephyr.dts.pre`.

- The **root** is the board `.dts`. It pulls in the SoC chain (`same5xx20.dtsi` → `same5x.dtsi`
  → `samd5x.dtsi` → `armv7-m.dtsi` → `skeleton.dtsi`) and the board pinctrl file.
- **The app overlay comes last.** That is why it can change everything above it.
- **Badge number**: how many entries (nodes and properties) in the **final** devicetree come from
  this file. A file without a badge contributes no values directly; it only includes other files
  or provides `#define` constants.
- **Show internal helper headers**: also shows Zephyr's internal macro headers
  (`sys/util_*.h`). They are hidden by default.

### 6.1 Selected file: `.dts` / `.dtsi` / overlay

![File details](img/el_include_file.png)

- **File path** (blue): click to open the whole file.
- **Include chain**: how the board file reached this file.
- **Contributes N entries**: one card per node. The node name jumps to the node in the Nodes tab.
  Each `property :line` link opens the source at that line.

### 6.2 Selected file: header (`.h`)

![Header constants](img/el_header_defs.png)

A header contributes no nodes. It provides **constants** that the `.dts` files use, such as
`GPIO_ACTIVE_LOW`, `GPIO_PULL_UP` or the pinctrl names `PA22C_SERCOM3_PAD0`. The table lists each
constant with its value. The **Line** column opens the header at the definition.

---

## 7. Source viewer

![Source viewer](img/source.png)

The source viewer opens when you click any blue *file:line* link.

- **Yellow** line: the line that was clicked.
- **Green** lines: the other lines in this file that belong to the same node.
- **Open in VS Code**: opens the file at the same line in VS Code. This button only appears if
  the `code` command is in your PATH.
- **×** or **Esc** closes the viewer.

---

## 8. Left panel — tab **Pins**

![Pins tab](img/pins_tab.png)

One row per pin of the chip's package, in pin-number order. If the device has several packages,
the **Package** list picks one.

- **Summary line**, e.g. `128 pins, 23 used, 77 free, 1 with more than one user`.
- **Show**: all pins, used pins only, or free pins only.
- **Filter**: any text in the pin, pad, node or function columns.

| Column | Meaning |
|---|---|
| Pin, Pad | Pin number and pad name, e.g. `PC22` |
| State | **free** (nothing uses it) · **used** (one node) · **conflict** (more than one node uses it) · **other** (power, ground, NC, reset) |
| Used by (node) | Node paths that use the pin. Click a path to open the node in the Nodes tab |
| Role / function | What the node does with the pin, e.g. `pinctrl: B DAC.VOUT0 (default)` |
| All possible functions | Every function the pin can take on this chip; the default is marked |

---

## 9. Left panel — tab **Package**

![Package tab](img/package_tab.png)

The same pin states drawn on the package outline. Pin 1 is at the top left of a QFP and ball A1 at
the top left of a BGA; the numbering runs counter-clockwise.

- **Package** list: switch to another package of the device.
- **Legend with counts**: free (grey) · used (green) · conflict (orange) · power / other (dark blue) ·
  draft (purple, not built yet).
- **Click a pin**: the panel below shows its users and function, with links to the nodes.

---

## 10. Configurator — **Configure…** dialog

![Configure dialog](img/configure_dialog.png)

The dialog opens from the **Configure…** button of a disabled node (section 5.1). The example turns
SERCOM1 into an I2C controller: SDA on PC22, SCL on PC23.

1. **Class** (UART, I2C, SPI): the pads and properties below change with it.
2. **Pads**: one pad per role. Pads that other nodes already use are still offered; the check
   (section 11) reports the conflict. *Derived properties* lists the pads that are still to assign
   (orange).
3. **Properties**: the class properties, e.g. `clock-frequency`.
4. **Use**: `chosen` and `aliases` entries for the node, if needed.
5. **Save** adds the node to the draft and opens the Draft tab. **Cancel** closes the dialog.

The draft is kept for this build. Saving the dialog does not build anything; use the check in the
Draft tab, and copy the snippet into your app overlay.

### 10.1 Edit pins… (enabled nodes)

![Edit pins dialog](img/edit_pins_dialog.png)

On an enabled node (`status = "okay"`), **Edit pins…** opens the same dialog for changing the pads and
properties of that node. The example is SERCOM2 of lab1 as UART: TX PB25, RX PB24, filled in from the
node as it is built.

- The change goes to the draft as an edit of the node. Its pinctrl group gets the name
  `<label>_<class>_edit` (for example `sercom2_uart_edit`), so the board's own group is not changed.
- **Cancel** closes the dialog and changes nothing.

---

## 11. Left panel — tab **Draft**

![Draft tab](img/draft_tab.png)

The pending changes of the current build. The number on the tab counts the draft nodes.

- **Draft nodes**: one line per node. The pencil opens it in the Configure dialog; the bin removes
  it from the draft.
- **Overlay snippet**: the devicetree overlay for the draft. **Copy** puts it on the clipboard,
  **Download** saves it as `draft.overlay`. Paste it into the app overlay yourself.
- **Suggested prj.conf lines**: the Kconfig options the class needs, e.g. `CONFIG_I2C=y` and
  `CONFIG_I2C_SAM0=y`.
- **Check**: **Check again** runs the validation on the overlay. It takes a few seconds; a spinner
  shows while it runs. The line above the results counts the errors, warnings and infos. Each
  finding has a badge and a message that names the nodes and pins involved.

| Badge | Meaning |
|---|---|
| error (red) | The overlay would not work, e.g. two nodes use the same pin or a required property is missing |
| warning (orange) | The overlay works but needs attention, e.g. a pinmux IOSET mix |
| info (grey) | For information, e.g. a pin conflict that already exists in the build |

### 11.1 Authoritative check: Verify with west and Write to app

The **Check** above is a fast check in Python. The two buttons below use Zephyr itself.

**Verify with west** runs `west build --cmake-only` of the build's board into a scratch folder in
`%TEMP%`, with the draft overlay and the Kconfig lines (about 15–45 s). The build folder is not touched.
A green line says that Zephyr accepts the draft; a red line says that it rejects it, and the errors are
shown under the log.

![Verify with west accepted](img/draft_verify_ok.png)

![Verify with west rejected](img/draft_verify_fail.png)

**Write to app** copies the draft into the app folder as `dt_explorer.overlay` and `dt_explorer.conf`.
A confirmation first names the folder and lists the files that would be overwritten. Zephyr reads the two
files only if the app's `CMakeLists.txt` or your west command refers to them, for example with
`-DEXTRA_DTC_OVERLAY_FILE=dt_explorer.overlay -DEXTRA_CONF_FILE=dt_explorer.conf`.

![Write to app confirmation](img/draft_write_dialog.png)

The Draft tab opens by itself after Save. Use `tab=draft` in a link to open it directly.

---

## 12. Direct links (URL parameters)

Every view can be opened directly, for example from slides or lab instructions:

| Parameter | Example | Effect |
|---|---|---|
| `build` | `build=apps/lab1/build_same54_xpro` | Load this build (path relative to the workspace) |
| `node` | `node=/my_led@41008000` | Select this node |
| `macros=1` | | Expand the generated-macro section |
| `bprops=1` | | Expand the table of all binding properties |
| `tab` | `tab=pins` | Start on a tab: `include`, `pins`, `package` or `draft` (default: Nodes) |
| `file` | `file=zephyr\dts\arm\atmel\same5x.dtsi` | Select this file in the include tree |
| `src`, `line` | `src=apps\lab3\boards\same54_xpro.overlay&line=20` | Open the source viewer |

URL-encode the values: `/` becomes `%2F`, `@` becomes `%40` and `\` becomes `%5C`.
For example:
`http://localhost:8765/?build=apps/lab1/build_same54_xpro&node=%2Fmy_led%4041008000`
