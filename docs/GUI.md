# Devicetree Explorer — GUI Guide

This guide walks through every part of the Devicetree Explorer user interface. The screenshots
come from the MASTERs 2026 class *26020 RTOS4* (SAM E54 Xplained Pro, Zephyr 4.3). To regenerate
them, run `docs/make_screenshots.py`.

The window has three areas:

![Full window](img/node_led.png)

| Area | Content |
|---|---|
| **Header** (top) | Build selection, reload, colour legend |
| **Left panel** | Two tabs: **Nodes** (the devicetree) and **Include tree** (the source files) |
| **Right panel** | Details of whatever is selected on the left; the overview when nothing is selected |

---

## 1. Header

![Header](img/el_header.png)

- **Build**: lists every Zephyr build folder found under the workspace, i.e. every folder that
  contains `zephyr/edt.pickle`. Picking an entry loads that build.
- **Reload**: scans for builds again and reloads the current one. Press it after every
  `west build`.
- **Colour legend**: the text colour used in both trees and in the *Origin* column. It tells
  you **which kind of file** something comes from:

| Colour | Category | Typical files |
|---|---|---|
| green | Overlay (app) | `apps/<lab>/boards/<board>.overlay` |
| blue | Board | `zephyr/boards/.../<board>.dts`, `...-pinctrl.dtsi` |
| grey | SoC / Zephyr | `zephyr/dts/arm/...dtsi`, `zephyr/include/.../dt-bindings/*.h` |
| purple | Module | `modules/...` (e.g. pinctrl headers from `hal_atmel`) |

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
| dark grey: *Disabled* | `status = "disabled"`. | Only if you expected it to be on. |
| blue-grey: *No driver* | CPU, memory, a container, or a child node that the parent or the app reads directly. | Usually fine. |

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

For child nodes without their own `compatible` (for example a button below `gpio-keys`), the
card explains that the parent's binding describes them and that the app reads them directly:

![Child node](img/el_child_state.png)

### 5.2 compatible / binding

![Binding card](img/el_binding.png)

- **`compatible`** strings of the node.
- **Binding file**: the YAML file Zephyr matched to this `compatible`. Click it to open the file.
- **Description** taken from the binding.
- **Registers**: the decoded `reg` entries (address and size).

### 5.3 Properties

![Properties table](img/el_props.png)

| Column | Meaning |
|---|---|
| Property | Name as in the devicetree |
| As written | Value as it appears in `zephyr.dts`, with references like `&portb` |
| Interpreted | Value as Zephyr understands it through the binding. Numbers are shown in decimal and hex, references are resolved, and GPIO flags are decoded, e.g. `0x11 = ACTIVE_LOW \| PULL_UP` |
| Origin | Category of the file that **set this value last** (see legend) |
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

## 8. Direct links (URL parameters)

Every view can be opened directly, for example from slides or lab instructions:

| Parameter | Example | Effect |
|---|---|---|
| `build` | `build=apps/lab1/build_same54_xpro` | Load this build (path relative to the workspace) |
| `node` | `node=/my_led@41008000` | Select this node |
| `macros=1` | | Expand the generated-macro section |
| `tab=include` | | Start on the include tree |
| `file` | `file=zephyr\dts\arm\atmel\same5x.dtsi` | Select this file in the include tree |
| `src`, `line` | `src=apps\lab3\boards\same54_xpro.overlay&line=20` | Open the source viewer |

URL-encode the values: `/` becomes `%2F`, `@` becomes `%40` and `\` becomes `%5C`.
For example:
`http://localhost:8765/?build=apps/lab1/build_same54_xpro&node=%2Fmy_led%4041008000`
