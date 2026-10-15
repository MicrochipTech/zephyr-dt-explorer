# Devicetree Explorer

Shows the final Zephyr devicetree of a build in the browser — where each node and property comes
from, which driver handles it, and whether a device actually ended up in the image.

## Start

```cmd
dt_explorer.bat                         :: asks for the folder with a folder dialog
dt_explorer.bat D:\other\zephyrproject  :: no dialog, use this folder
```

The folder can be a west workspace, an app folder or a single build folder; every folder below it
that contains `zephyr/edt.pickle` is offered as a build. The last choice is remembered in
`%USERPROFILE%\.dt_explorer.json` and pre-selected next time. The folder button in the header
switches to another folder without restarting.

Opens `http://localhost:8765`. Stop with Ctrl+C in the console window.
Build first (`west build …`) — the explorer reads the build folder, not the sources.
After a rebuild press **Reload**. The UI is English and uses dark mode.

Direct link to a node: `http://localhost:8765/?build=apps/lab1/build_same54_xpro&node=/my_led@41008000`

**Full GUI guide with screenshots: [docs/GUI.md](docs/GUI.md)**

## What it shows

| Element | Meaning |
|---|---|
| Text colour in the tree | File that defines the node: green = app overlay, blue = board, grey = SoC/Zephyr, purple = module |
| `overlay` badge | Node exists elsewhere but the app overlay changed a property (e.g. `status`) |
| **Legend** menu (header) | Colour key of the trees and of the Origin column: green = overlay (app), blue = board, grey = SoC/Zephyr, purple = module, yellow = binding default |
| Dot | green = device in image · orange = driver exists but disabled by Kconfig · red = driver built but no device · cyan = driver built, build not linked yet (`--cmake-only`) · grey = `status = "disabled"` · blue-grey = no driver (CPU, memory, child nodes) |
| Red banner | A driver from a module/app was compiled but no node with its `compatible` is `okay` — typical cause: overlay not saved or `compatible` misspelled |
| Overview | Every `DT_NODELABEL` / `DT_ALIAS` / `DT_CHOSEN` in the app's `src/`, resolved to its node |
| Node details | Origin file:line per property, raw vs. interpreted value (GPIO flags decoded), binding description, driver file, dependencies, generated C macros |
| Binding | Matched YAML with its `include:` hierarchy, `on-bus`/`bus`, cell names, child-binding; table of all allowed properties (type, required, default, enum, set/default/not set, declaring file). Values that only come from a binding `default:` appear as yellow "Binding default" rows. Nodes whose `compatible` has no binding are flagged deep orange |
| **Include tree** tab | Which `.dts`/`.dtsi`/`.h` includes which (from the cpp line markers in `zephyr.dts.pre`); the app overlay comes last. Badge = entries in the final devicetree from that file. Click a file: include chain, contributed nodes/properties, or the constants a `.h` provides. Internal `sys/util_*.h` helpers hidden by default |
| Source viewer | Every blue file:line link opens the file in the browser, target line highlighted, all lines of the same node marked; "Open in VS Code" jumps to the line |
| **Pins** tab | Every pin of the chip's package with its state: free, used, conflict (more than one user), or power/other. Shows which node and function uses a pin; node paths open the node. Filter by pin, pad, node or function; Show = all, used or free |
| **Package** tab | The same pin states drawn on the package outline (QFP, BGA). Click a pin to see its users. Draft pins are purple until the draft is built |
| **Board** tab | The connectors of the board (e.g. EXT1–EXT3 of the SAME54 Xplained Pro) with each pin coloured by use: used = green, conflict = orange, free = grey, draft = purple, supply / ID = dark blue. Click a pin for its uses and the possible functions of its pad. Needs a board description `dt_explorer/boards/<board>.yaml`; without one the tab says so |
| **Configure…** button | On a `status = "disabled"` node of a supported peripheral (SERCOM as UART, I2C or SPI): pick the class, pads and properties, then Save adds the node to the draft |
| **Generic form** (Configure… without a recipe) | On a disabled node whose `compatible` has a binding but no recipe (TC counter, DMAC, ADC, CAN, ...): the dialog is generated from the binding chain. One field per scalar property (type, enum, default, required flag, description), pin roles from the device data when the binding takes `pinctrl-0`. A required property left empty is flagged and Save stays off. Save adds a recipe-less draft node (class `generic`) that renders, checks and verifies like any other. Properties that are not scalar (clocks, interrupts, ...) stay as the board file has them |
| **Edit pins…** button | On an enabled node (`status = "okay"`) of a supported peripheral: the same dialog with the current pads and properties; Save puts the change into the draft under its own pinctrl group, the board's group stays as it is |
| **Draft** tab | The draft nodes (edit or remove), the overlay snippet (Copy, Download), the suggested `prj.conf` lines and **Check**: validates the draft overlay and lists errors, warnings and infos (pin conflicts, missing properties). **Verify with west** runs Zephyr's own configure step on the draft in a scratch folder (the build folder is not touched); **Write to app** copies the overlay and `.conf` into the app folder after a confirmation |
| **Apply to app…** (Draft tab) | Writes the draft as `boards/<board>.overlay` and `.conf` of the build's own board, the names Zephyr reads by itself (a plain `west build -b <board>` uses them; **Write to app** needs extra west options). New files are written after confirmation. An existing file (e.g. the app's own board overlay) is **kept**: the draft is **appended** as a marked block (`/* dt_explorer draft begin <date> */ … /* dt_explorer end */`) after you have seen the diff, and applying again replaces only that block; the `.conf` gets the lines appended once (duplicates left out). Replacing the whole file stays behind an explicit tick, `src/` is never touched |
| **Changes** tab | What git sees in the app folder of the open build, read only: the list is `git status` (modified, added, deleted, renamed, untracked; `.gitignore` respected; only this app folder). Click a file for its `git diff` against HEAD in the source viewer, changed lines highlighted (untracked file: the whole file as added). A purple **dtx** badge marks files the tool writes (Apply to app, Write to app, files with a `dt_explorer` marked block, `dtx_devices/`). The tab shows the number of changed files. git is read when the tab opens and on **Refresh** only, with the time of the read, branch and HEAD hash. No git repository or no git on PATH: the tab says so. There is no commit, revert or checkout |
| **New build…** and **Full build** | Header buttons. New build: choose app folder and board, run a cmake-only build (fast; devices show "not linked yet"). Full build: `west build` of the current build folder with a streamed log, reloads the build when it finishes |
| **Port to board…** | Header button. Ports the app of the current build to another board: pick a build of the same app for the other board (or create one), then **Run port** shows the target overlay, the `.conf` lines and a port report (what translates, Kconfig differences, references and pins the target does not resolve, nodes to carry over by hand) and a fast check. **Verify with west** checks the port on the target in a scratch folder. The report has section 5b (app-defined nodes), section 7 (connector roles of the used pads, e.g. `EXT1 pin 16 (MOSI): PB27 on same54_xpro → PB24 on pic32cx_sg41_cult`) and `conflict` nodes with suggestions. **Apply to app…** writes the port as `boards/<target board>.overlay` and `.conf` into the app: new files are written after confirmation, an existing file is kept and gets the port result appended as a marked block (diff shown first; applying again replaces only that block), replacing it stays behind an explicit tick, `src/` is never touched. Nothing is written before that button and its confirmation |

More URL parameters: `&tab=include|pins|package|board|draft|log|changes`, `&src=<file relative to the west workspace>&line=<n>`.

## Log

The tool writes a log of what it does, sees and decides: files read, node/binding/driver counts and
timings, the `state()` reason behind each traffic-light dot (DEBUG), every external call (`west`, `nm`,
`cmake`, `git`: command, exit code, duration, last output lines), every file written and errors with
their traceback. The same lines go to a file and to the **Log** tab.

| What | Where |
|---|---|
| Log file | `%USERPROFILE%\.dt_explorer\logs\dtx_<date>.log` (rotating, the last 10 days are kept; folder overridable with the environment variable `DTX_LOG_DIR`) |
| **Log** tab | Live lines (last 5000), **Level** filter (DEBUG / INFO / WARNING), **DEBUG** switch, **Copy** (context header plus the visible lines, ready to paste into a Claude Code session) |
| `--debug` | `py dt_explorer.py <folder> --debug` starts with DEBUG on; works with `--no-browser` |
| Context header | Tool commit, Python, NiceGUI, Zephyr version, workspace, build folder, board, SoC, ATDF file and version, loaded plugins; written at start and whenever a build is loaded |

**Privacy:** the log contains local paths including your user name. Read it before you send it to someone.

## Data sources (all in the build folder)

`zephyr/zephyr.dts` (origin comments), `zephyr/zephyr.dts.pre` (include tree), `zephyr/edt.pickle` (Zephyr's edtlib), `zephyr/zephyr.elf`
(`__device_dts_ord_N` via `nm`), `compile_commands.json`, `zephyr_modules.txt`, `CMakeCache.txt`.

## Requirements

Python with `nicegui` and `pyyaml` (`py -m pip install nicegui pyyaml`). Zephyr's `edtlib` is
loaded from the workspace (`zephyr/scripts/dts/python-devicetree`).

## License

Copyright 2026 The Devicetree Explorer contributors. Licensed under the Apache License, Version 2.0;
see [LICENSE](LICENSE). Contributions are accepted under the same license. To write a plugin, see
[CONTRIBUTING.md](CONTRIBUTING.md).
