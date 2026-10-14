# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Devicetree Explorer: a single-file NiceGUI web app ([dt_explorer.py](dt_explorer.py)) that shows the
final Zephyr devicetree of a **build folder** in the browser. It shows where each node and property
comes from, which driver handles it, and whether a device ended up in the image. User docs:
[README.md](README.md) and [docs/GUI.md](docs/GUI.md). The UI text is English and dark-mode only.

## Commands

```cmd
py -m pip install nicegui pyyaml                 :: dependencies
dt_explorer.bat [folder]                         :: wrapper for: py dt_explorer.py [folder]
py dt_explorer.py <folder> --no-browser          :: start without the folder dialog and without opening a browser
py docs/make_screenshots.py                      :: regenerate docs/img (explorer must already be running)
```

- The server runs on `http://localhost:8765` with `reload=False`, so restart it after every code change.
- With no folder argument, a tkinter dialog asks for one. The last folder is stored in
  `%USERPROFILE%\.dt_explorer.json`, and `DEFAULT_ROOT` is the fallback.
- There are no tests, linter or build step. To verify a change, run the app against a real Zephyr
  build and open a deep link. Example:
  `/?build=apps/lab1/build_same54_xpro&node=/my_led@41008000&macros=1&bprops=1`.
  Other parameters are `tab=include`, `file=<rel path>` and `src=<rel path>&line=<n>`.
  They are all parsed in the `page()` signature.
- `docs/make_screenshots.py` uses headless Chrome and hardcoded RTOS4 lab builds (`apps/lab0..lab3`,
  `_soltest/apps/lab1_leer`) under the workspace. If you add or rename a GUI element documented in
  `docs/GUI.md`, update `SHOTS`/`CROPS` there.

## Architecture

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
- Keep the single-file layout. Add new data in `BuildModel` and new rendering in `page()`.
- When a feature is visible to users, update the table in README.md and `docs/GUI.md` in the same change.
