# Devicetree Explorer

Shows the final Zephyr devicetree of a build in the browser — where each node and property comes
from, which driver handles it, and whether a device actually ended up in the image.

## Start

```cmd
dt_explorer.bat                         :: scans C:\Masters\26020_RTOS4\zephyrproject
dt_explorer.bat D:\other\zephyrproject  :: different west workspace
```

Opens `http://localhost:8765`. Stop with Ctrl+C in the console window.
Build first (`west build …`) — the explorer reads the build folder, not the sources.
After a rebuild press **Reload**. The UI is English and uses dark mode.

Direct link to a node: `http://localhost:8765/?build=apps/lab1/build_same54_xpro&node=/my_led@41008000`

## What it shows

| Element | Meaning |
|---|---|
| Text colour in the tree | File that defines the node: green = app overlay, blue = board, grey = SoC/Zephyr, purple = module |
| `overlay` badge | Node exists elsewhere but the app overlay changed a property (e.g. `status`) |
| Dot | green = device in image · orange = driver exists but disabled by Kconfig · red = driver built but no device · grey = `status = "disabled"` · blue-grey = no driver (CPU, memory, child nodes) |
| Red banner | A driver from a module/app was compiled but no node with its `compatible` is `okay` — typical cause: overlay not saved or `compatible` misspelled |
| Overview | Every `DT_NODELABEL` / `DT_ALIAS` / `DT_CHOSEN` in the app's `src/`, resolved to its node |
| Node details | Origin file:line per property, raw vs. interpreted value (GPIO flags decoded), binding description, driver file, dependencies, generated C macros |
| **Include tree** tab | Which `.dts`/`.dtsi`/`.h` includes which (from the cpp line markers in `zephyr.dts.pre`); the app overlay comes last. Badge = entries in the final devicetree from that file. Click a file: include chain, contributed nodes/properties, or the constants a `.h` provides. Internal `sys/util_*.h` helpers hidden by default |
| Source viewer | Every blue file:line link opens the file in the browser, target line highlighted, all lines of the same node marked; "Open in VS Code" jumps to the line |

More URL parameters: `&tab=include`, `&src=<file relative to the west workspace>&line=<n>`.

## Data sources (all in the build folder)

`zephyr/zephyr.dts` (origin comments), `zephyr/zephyr.dts.pre` (include tree), `zephyr/edt.pickle` (Zephyr's edtlib), `zephyr/zephyr.elf`
(`__device_dts_ord_N` via `nm`), `compile_commands.json`, `zephyr_modules.txt`, `CMakeCache.txt`.

## Requirements

Python with `nicegui` and `pyyaml` (`py -m pip install nicegui pyyaml`). Zephyr's `edtlib` is
loaded from the workspace (`zephyr/scripts/dts/python-devicetree`).
