# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Devicetree Explorer: a NiceGUI web app (package [dt_explorer/](dt_explorer/), started by the thin launcher [dt_explorer.py](dt_explorer.py)) that shows the
final Zephyr devicetree of a **build folder** in the browser. It shows where each node and property
comes from, which driver handles it, and whether a device ended up in the image. User docs:
[README.md](README.md) and [docs/GUI.md](docs/GUI.md). The UI text is English and dark-mode only.

## Commands

```cmd
py -m pip install nicegui pyyaml                 :: dependencies
dt_explorer.bat [folder]                         :: wrapper for: py dt_explorer.py [folder]
py dt_explorer.py <folder> --no-browser          :: start without the folder dialog and without opening a browser
py docs/make_screenshots.py [--port N]           :: regenerate docs/img (explorer must already be running)
py -m pip install -r requirements-dev.txt        :: test dependencies (pytest)
py -m pytest -q -rs                              :: test suite (no linter, no build step)
py tools/snapshot_build.py <build dir> [-o f]    :: stable JSON snapshot of today's analysis (goldens)
```

- UI tests (P8.2): `py -m pytest -q -rs -m ui tests/ui` (simulated, no browser) and `-m smoke` (Playwright + Chrome, own server on a free port, screenshots in `%TEMP%\dtx_ui_shots`); they need `DTX_RTOS4_WS` and skip without it. Find elements by `id=` (`tests/ui/ui_helpers.py`); drafts are redirected to copies under %TEMP%.
- The server runs on `http://localhost:8765` with `reload=False`, so restart it after every code change.
- With no folder argument, a tkinter dialog asks for one. The last folder is stored in
  `%USERPROFILE%\.dt_explorer.json`, and `DEFAULT_ROOT` is the fallback.
- **Tests** read real data from environment variables and skip with a reason when one is unset:
  `DTX_RTOS4_WS` (RTOS4 workspace, Zephyr v4.3.0, with the lab builds: goldens, providers,
  formatters, recipes, render/validate/verify, lift, port), `DTX_MAIN_WS` (`C:\Masters\zephyr-main`,
  Zephyr main: cross-workspace models, g1 I2C/SPI translations, PIC32CX ports) and
  `DTX_DESIGN_FILES` (board design files: Altium reader, board import). Without them only the
  synthetic tests run. A full run with all three takes about 5 minutes.
- **Goldens:** `tests/golden/*.json` are snapshots of the analysis of lab0, lab1, lab3, lab1_leer and a
  cmake-only lab1 build; `tests/test_golden.py` proves "no behaviour change" for refactorings. They
  depend on the existing `build_same54_xpro` folders, so do not rebuild those. `DTX_GOLDEN_DIR`
  points the test at another golden directory.
- To check a UI change, run the app against a real Zephyr build and open a deep link. Example:
  `/?build=apps/lab1/build_same54_xpro&node=/my_led@41008000&macros=1&bprops=1`.
  Other parameters are `tab=include|pins|package|draft|board`, `file=<rel path>` and
  `src=<rel path>&line=<n>`. They are all parsed in the `page()` signature.
- Scripts outside the app must call `dt_explorer.app.load_plugins()` first; otherwise the plugin
  registry is empty and lift/port silently find nothing.
- `docs/make_screenshots.py` uses headless Chrome and hardcoded RTOS4 lab builds (`apps/lab0..lab3`,
  `_soltest/apps/lab1_leer`) under the workspace. If you add or rename a GUI element documented in
  `docs/GUI.md`, update `SHOTS`/`CROPS` there.

## Architecture

The code is the package `dt_explorer/`; `dt_explorer.py` only calls `dt_explorer.app.main()` under the
`__main__`/`__mp_main__` guard (the package wins over the same-named script on import). Layout:
`core/build.py` (layers 1 and 2, no NiceGUI, must never import `ui/`), `ui/page.py` (layer 3 plus the
folder dialog, config file and argv globals `ROOT`/`SHOW_BROWSER`), `app.py` (startup, `ui.run`).
`tools/snapshot_build.py` imports `BuildModel`, `origin_kind` from `dt_explorer.core.build`.
The file has three layers, top to bottom:

1. **Parsers (module functions).** Each turns a build artifact into plain data:
   - `parse_zephyr_dts` reads the `/* in file:line */` origin comments in `zephyr/zephyr.dts` and returns
     `NodeOrigin`/`PropInfo` per node path.
   - `parse_includes` reads the cpp line markers in `zephyr/zephyr.dts.pre` and builds the include tree.
     The app overlay comes last.
   - `header_defines` collects constants from `.h` files.
   - `DriverIndex` maps a compatible to the driver `.c` files by grepping `#define DT_DRV_COMPAT`.
     The result is cached per directory set for the whole process.
2. **`BuildModel(build_dir)`** holds everything known about one build and does all the analysis:
   - It reads `ZEPHYR_BASE`, `APPLICATION_SOURCE_DIR` and `CMAKE_NM` from `CMakeCache.txt`. The west
     workspace is `ZEPHYR_BASE.parent`, and all "relative" paths in the UI and URL are relative to it
     (`rel()`/`resolve()`).
   - **`edt.pickle` can only be unpickled after `ZEPHYR_BASE/scripts/dts/python-devicetree/src` is on
     `sys.path`.** It contains edtlib objects, so the edtlib API (`node.compats`, `dep_ordinal`,
     `binding_path`, `edt._dt.alias2node`, …) is the core data model. That edtlib comes from the
     analysed workspace, not from pip.
   - "Device in image" means `__device_dts_ord_<dep_ordinal>` is present in `nm zephyr.elf`.
     "Driver compiled" means a file is listed in `compile_commands.json`.
   - `state(node)` is the traffic-light decision chain. Its keys must match `STATE_STYLE`, and the
     origin categories from `origin_kind()` must match `ORIGIN_STYLE`.
   - The binding search order matches Zephyr's dts_root order: app, then modules from
     `zephyr_modules.txt`, then zephyr. `binding_chain()` resolves YAML `include:` recursively.
3. **UI.** A global `App` instance (`state`) holds the folder, the list of builds and the current
   `BuildModel`. It is shared by every browser tab. The single `@ui.page("/")` function `page()`
   builds the whole UI with nested closures (`show_node`, `show_binding`, `show_file`,
   `open_source`, `render_includes`, …). Add new views there as closures and add new deep-link
   parameters to the `page()` signature. `find_builds()` treats any folder that contains
   `zephyr/edt.pickle` as a build.

## Conventions

- Windows only (paths with backslashes, `py` launcher, `HELPER_HEADER`, `fkey()` normalises paths
  for comparison). Keep `Path` handling compatible with that.
- Keep NiceGUI out of `core/`. Add new data in `BuildModel` (`core/build.py`) and new rendering in `page()` (`ui/page.py`).
- When a feature is visible to users, update the table in README.md and `docs/GUI.md` in the same change.

## License and contributions

Apache-2.0 (`LICENSE`). Writing a provider, formatter, recipe, translation, board reader or connector
type: see `CONTRIBUTING.md`. Keep vendor names out of `core/` and `ui/`.
