# Implementation plan: peripheral configurator

This plan turns [CONFIGURATOR.md](CONFIGURATOR.md) into work cards. A **builder agent** (the
orchestrating Claude Code session) reads this file, picks the next card, briefs a **sub-agent** to do
it, checks the result, and records it here. The design itself lives in CONFIGURATOR.md. This file
says only *in which order*, *by whom*, *with which write scope* and *how it is checked*.

Read first: CONFIGURATOR.md (design and decisions), CLAUDE.md (repo conventions).

---

## 1. Roles

| Role | Model | Does | Never does |
|---|---|---|---|
| **Builder** (main session) | session model | picks cards, writes briefs, reviews the result, updates the status table (§6) and the evidence index, asks the human at checkpoints | writes product code beyond trivial fixes; trusts a report without checking it |
| **Sub-agent** (one fresh agent per card) | `sonnet`; `haiku` for cards marked *light* | implements exactly one card inside its write scope, runs the acceptance commands, writes the evidence file, makes one commit | edits this plan, edits files outside its write scope, pushes, starts the next card |
| **Human** | – | decides at the checkpoints (§5), approves anything outward-facing | – |

The builder keeps its own context small. Sub-agents start fresh and are discarded after their
card. **The state lives in the repo, not in any agent:** this plan (status), CONFIGURATOR.md
(decisions), `docs/evidence/<card>.md` (proof), one commit per card. Any builder session can take
over from any other.

## 2. Rules for every card

1. **One card = one commit** on `master` (or on the worktree branch, see 6). Commit message starts
   with the card ID: `P3.2: SERCOM recipe with lift`.
2. **Stage only explicit paths** from the card's write scope. Never `git add -A` or `git add .`.
3. **No `Co-Authored-By` trailer**, no AI attribution in commits. **No push.**
4. **Write scope is binding.** If the card cannot be done without touching another file, stop
   (see 4) instead of widening the scope.
5. **Evidence file** `docs/evidence/<card>.md`: the acceptance commands and their **complete,
   unfiltered** output (tail only for logs over 200 lines, saying so), plus any number the card
   claims. No summaries in place of output.
6. **Parallel work only in a separate git worktree** (`isolation: "worktree"`) and only for cards
   marked *parallel-safe* whose write scopes are disjoint *for their whole run*. A card that moves
   code is never parallel to a card that touches that code. Remove the worktree before the next agent
   uses the same branch. **The worktree starts from `origin/master`, not from local `master`**
   (found 2026-10-14). Every worktree brief therefore begins with `git reset --hard master` inside
   the worktree; the builder brings the result to `master` with `git cherry-pick`.
7. **Card size:** target ≤ 60 tool calls. A sub-agent that sees the card growing beyond that stops
   after a clean, committed intermediate state and reports what remains. The builder then splits
   the card.
8. **Data that the repo must not contain:** board design files, MPLAB packs, build folders. Tests
   that need them read paths from environment variables (§3) and are skipped with a clear reason
   when the variables are unset.
9. Python on this machine: `py`. Do not use `py -I` for scripts that need user-installed packages
   (`olefile`, `nicegui`); it hides them.

## 3. Environment facts

| What | Where |
|---|---|
| Repo | `C:\Masters\dt_explorer` (branch `master`) |
| RTOS4 workspace (Zephyr v4.3.0, old `atmel` port) | `C:\Masters\26020_RTOS4\zephyrproject`; west: `.venv\Scripts\python.exe -m west` from the workspace root |
| Lab builds (full) | `apps\lab0..lab3\build_same54_xpro`, `_soltest\apps\lab1_leer\build_same54_xpro` |
| `hal_microchip` with 91 ATDF | `…\zephyrproject\modules\hal\microchip\packs\` (fetched 2026-10-14) |
| Installed DFPs | `%USERPROFILE%\.mchp_packs\Microchip\*_DFP\`, `C:\Program Files\Microchip\MPLABX\v6.35\packs\` |
| Board design files (not in repo) | `C:\Masters\board_design_files\same54_xpro\`, `…\pic32cx_sg41_cult\` |
| Current-Zephyr workspace (planned) | `C:\Masters\zephyr-main` (created by the builder at P6.1, pre-approved as HC-3) |
| Toolchain | Zephyr SDK 0.17.4, `C:\Users\M91221\zephyr-sdk-0.17.4` |

Environment variables for tests: `DTX_RTOS4_WS`, `DTX_MAIN_WS`, `DTX_DESIGN_FILES`. The builder sets
them in each brief.

Scratch builds go to the session scratchpad or `%TEMP%`, never into the workspace's app folders,
unless a card says otherwise.

## 4. Stop conditions (complete list)

A sub-agent stops, commits nothing half-done, and reports to the builder **only** when:

1. the card needs a file outside its write scope;
2. an acceptance command fails and the cause is outside the card (environment, another module,
   Zephyr itself);
3. a golden or existing test changes its result and the card did not intend that change;
4. the design in CONFIGURATOR.md is contradictory or silent on something the card must decide;
5. an outward-facing action would be needed (push, upstream PR, download outside the listed
   sources, installing system software);
6. the card exceeds its size limit (rule 2.7).

**Anything else means: keep working.** Context length or cost is not a reason to stop. A result
outside a tolerance is a finding to report, not a reason to retry blindly.

## 5. Human checkpoints and builder stop conditions

### 5.1 Decided in advance (2026-10-14)

The human answered these before the run. The builder does **not** stop for them:

| ID | Topic | Decision |
|---|---|---|
| HC-1 | License | **Apache-2.0.** P1.8 may start whenever its other dependencies are done. |
| HC-3 | Workspace `C:\Masters\zephyr-main` | **Pre-approved.** At P6.1 the builder creates it itself (`west init -m https://github.com/zephyrproject-rtos/zephyr`, `west update`, Python venv with Zephyr's requirements). Download via github.com only. |
| HC-4 | Port test on the real board | **No stop.** P5.5 is accepted on build evidence ("Verify with west" plus a full link). The evidence file states explicitly that it was not tested on hardware. |
| HC-5 | SG61 Curiosity Ultra design files | **The human provides them before phase 7** in `C:\Masters\board_design_files\pic32cx_sg61_cult\`. If they are missing when P7.3 runs, the builder defers only the SG61 board YAML (card P7.3b) and continues. |
| HC-6 | Connector names and types | **The builder confirms them itself** when the silkscreen text *and* the signal pattern agree (pin count, ID/GND/VCC positions, traced signals). Those entries get `reviewed: builder`. Everything else stays `suggested`. |
| – | Failed cards | **Keep going.** A card that fails is deferred (status `blocked`, reason recorded) and the builder continues with other runnable cards. It may retry a deferred card with a new sub-agent or split it whenever it sees a way forward. |
| – | After version 1 | **Continue with phase 8** (see §7). |
| – | Budget | **No limit** per run. |

### 5.2 Remaining human checkpoints

| ID | When | Question |
|---|---|---|
| HC-2 | after P1.7 | Review of the read-only pin map and package view (screenshots): good enough to build the configurator on? |
| HC-7 | when it blocks progress | A design gap or contradiction in CONFIGURATOR.md that the builder cannot settle, or an outward-facing action, **and** no other card can proceed without it |

Decisions the human makes are added to the *Decisions* table in CONFIGURATOR.md (by the builder)
before the dependent card starts.

### 5.3 Builder stop conditions (complete list)

The builder stops the run and reports to the human **only** when:

1. **HC-2** is reached.
2. **No runnable card is left** and work remains: every open card is `blocked` or depends on a
   blocked card. The report lists each blocked card with its reason and what would unblock it.
3. **HC-7:** a design question or outward-facing action blocks progress and no other card can
   proceed meanwhile (a single blocked card is not a stop: defer it and continue).
4. **Phase 8 is done** (end of the planned work; phases 9 and 10 stay in the backlog).
5. **Uncommitted changes from someone else** in the working tree overlap the scope of every runnable
   card. (If they overlap only some cards, defer those and continue.)

Anything else means: keep working. In particular, context length, cost, a failed card, or a long
run are no reasons to stop. Outward-facing actions (push, upstream PR, e-mail) are never done by
the builder; the cards that would need them are deferred.

## 6. Builder loop

For each card:

1. Pick the next `open` card whose dependencies are `done` (§8). Prefer the critical path (§7).
2. Write the brief from the template (§9). Fill in write scope, acceptance, environment variables.
3. Start one sub-agent (`subagent_type: general-purpose`, `model: sonnet` or `haiku`,
   `run_in_background: true`; `isolation: "worktree"` only for parallel-safe cards running next to
   another card).
4. While it runs, the builder may start another *parallel-safe* card with a disjoint scope. The
   builder session must not be cleared while a sub-agent runs; its completion only reaches this
   session.
5. **Review** when it reports. The builder checks, it does not trust:
   - `git show --stat <commit>`: only files in the write scope, only explicit paths;
   - re-run the acceptance commands itself (or at least the fastest one) and compare with the
     evidence file;
   - read the numbers and claims in the evidence file, not only PASS lines; numbers that do not fit
     together are a finding;
   - dependency direction: core never imports `providers/`, `formatters/`, `recipes/`,
     `translations/`, `readers/`, `connectors/`, `ui/`; plugins import only `core`; nothing imports
     `prototypes/`;
   - no vendor terms (ATDF, IOSET, `.mchp_packs`, `PA09`…) in `core/` or `ui/` (CONFIGURATOR.md,
     *What must not happen now*);
   - a negative test that passes is an alarm: check that it really can fail.
6. Update §8: status `done`, commit hash, evidence link. Or `blocked` with the reason, or split the
   card (new IDs `P3.2a`, `P3.2b`).
7. Commit the plan update separately: `plan: P3.2 done`.

## 7. Version 1 and critical path

**Version 1 = phases 0–7** (main use case: port SAME54 projects to PIC32CX SG41/SG61, with board
view). **Then phase 8** (child nodes on buses, generic binding form for further peripherals, their
translations): when version 1 is done, the builder writes the phase 8 cards itself from
CONFIGURATOR.md in the format of §8 (same rules: scope, acceptance, size ≤ 60 tool calls), commits
them as `plan: phase 8 cards`, and continues. Phases 9 and 10 stay in the backlog. New ideas during
the run go to the *Backlog* (§10), not into open cards.

Critical path: P0.1 → P1.1 → P1.2 → P1.4 → P1.5 → P3.1 → P3.2 → P3.3 → P3.4 → P4.1 → P5.0 → P5.2 →
P5.3 → P5.4 → P5.6 → P5.5 → P6.1 → P6.2.
The board branch (P7.x) can run in parallel after P1.2.

## 8. Cards

Legend: **Dep** = dependencies; **Scope** = files the card may create or change (plus its own
`docs/evidence/<ID>.md` and test files under `tests/` named after the module); **PS** =
parallel-safe; *light* = may use `haiku`.

Status values: `open`, `running`, `done`, `blocked`.

### Phase 0: measuring instruments

| ID | Status | Commit |
|---|---|---|
| P0.1 | done | 9b6e8c6 |
| P0.2 | done | afb4cb7 → master |

**P0.1 Golden snapshots of today's explorer.**
- Dep: –. Scope: `tools/snapshot_build.py`, `tests/golden/*.json`, `tests/test_golden.py`,
  `requirements-dev.txt`.
- Do: a script that loads a build with today's `BuildModel` and writes a stable JSON:
  node paths, state per node, origin file:line per property, drivers per node, include-tree file
  count, code refs, orphan drivers. Paths relative to the west workspace. Record goldens for lab0,
  lab1, lab3 and `lab1_leer` (full builds) plus one `--cmake-only` build of lab1 into `%TEMP%`.
  `test_golden.py` compares a fresh snapshot with the golden; it skips when `DTX_RTOS4_WS` is unset.
- Accept: `py -m pytest tests/test_golden.py -v` passes; then deliberately change one state in a
  copy of a golden and show the test fails (evidence: both outputs).
- Why first: every later refactoring card proves "no behaviour change" with it.

**P0.2 Test scaffolding.** *light*
- Dep: –. PS with P0.1 (disjoint scope). Scope: `pyproject.toml` (pytest config only),
  `tests/conftest.py` (env-var fixtures, skip helpers).
- Accept: `py -m pytest -q` runs and reports skips with reasons when the env vars are unset.

### Phase 1: package, plugins, pin map

| ID | Status | Commit |
|---|---|---|
| P1.1 | done | d116ebe |
| P1.2 | done | 92bfd80 |
| P1.3 | done | c214f54 → master |
| P1.4 | done | 151e084 → master |
| P1.5 | done | ea75341 |
| P1.6 | done | 25c2704 → master |
| P1.7 | done | e56bd44 → master |
| P1.8 | done | c0ba67d → master |

**P1.1 Move today's code into the package.** Not parallel to anything.
- Dep: P0.1, P0.2. Scope: `dt_explorer/` (new package), `dt_explorer.py` (thin launcher),
  `dt_explorer.bat`, `CLAUDE.md` (architecture section only), `docs/make_screenshots.py` (paths only).
- Do: `dt_explorer/core/build.py` (parsers, `DriverIndex`, `BuildModel`, `find_builds`),
  `dt_explorer/ui/page.py` (everything NiceGUI). Pure move plus imports; no behaviour change.
- Accept: goldens unchanged (`py -m pytest tests/test_golden.py`); `py dt_explorer.py <ws>
  --no-browser` starts and `http://localhost:8765/?build=apps/lab1/build_same54_xpro&node=/my_led@41008000`
  returns HTTP 200 (curl in evidence); `dt_explorer.bat` still works.

**P1.2 Neutral device model and plugin registry.**
- Dep: P1.1. Scope: `dt_explorer/core/device.py`, `dt_explorer/core/plugins.py`.
- Do: dataclasses `DeviceModel`, `Package`, `Instance`, `Signal`, `Constraint` exactly as in
  CONFIGURATOR.md; protocols for provider, formatter, recipe, translation, board reader, connector
  type; registry with decorators, entry-point discovery (`dt_explorer.providers` etc.) and
  `api_version` check that skips incompatible plugins with a logged reason.
- Accept: tests for registration, entry-point discovery (with a fake distribution) and a rejected
  plugin with the wrong `api_version`.

**P1.3 `BuildInfo`.** *light*, PS with P1.2.
- Dep: P1.1. Scope: `dt_explorer/core/buildinfo.py`.
- Do: read `CONFIG_SOC`, `CONFIG_SOC_SERIES`, `CONFIG_SOC_FAMILY` from `zephyr/.config`, vendor from
  the SoC's `soc.yml`/board's `board.yml`, west workspace and module dirs from `CMakeCache.txt` /
  `zephyr_modules.txt`.
- Accept: for lab1 `CONFIG_SOC == "same54p20a"`; test with a synthetic build folder.

**P1.4 ATDF provider.**
- Dep: P1.2, P1.3. Scope: `dt_explorer/providers/microchip_atdf.py`,
  `tests/fixtures/atdf/` (trimmed excerpts from `hal_microchip`, Apache-2.0, with a README naming the
  source file and revision).
- Do: `matches`, `locate` (order: `hal_microchip/packs`, `.mchp_packs`, MPLAB X packs; pattern
  search, natural version sort, never recursive over pack roots), `load` into the neutral model.
  IOSET becomes `Signal.group` plus a `Constraint` with the warning text.
- Accept: `locate` finds `ATSAME54P20A.atdf` for lab1 in under 1 s (time in evidence); SERCOM2
  PAD0 has exactly the pads PA09, PB25, PA12, PB26; the TQFP128 package has 128 positions; SG61
  lacks PB26–PB29/PC04 (fixture test).

**P1.5 `atmel,sam0-pinctrl` formatter.**
- Dep: P1.2, P1.4. Scope: `dt_explorer/formatters/atmel_sam0.py`.
- Do: `to_overlay`, `from_overlay`, `render_group`; leading-zero conversion (`PA09D` ↔ `PA9D`);
  every generated name checked against the Zephyr header `same54p-pinctrl.h`.
- Accept: round trip for all SERCOM signals of SAME54P20A; `from_overlay` reads the pins of
  `same54_xpro-pinctrl.dtsi` (`sercom2_uart_default` → PB25/PB24); a name missing in the header is an
  error.

**P1.6 Pin map table (UI, read-only).**
- Dep: P1.4, P1.5. Scope: `dt_explorer/ui/pinmap.py`, `dt_explorer/ui/page.py` (tab only),
  `dt_explorer/core/pins.py` (used pins from the devicetree: `pinctrl-0` values via the formatter, GPIO
  phandles).
- Accept: unit test for used pins of lab1 (console PB24/PB25 and the LED pin); screenshot of the tab
  in evidence; goldens unchanged.

**P1.7 Package view (UI, read-only).**
- Dep: P1.6. Scope: `dt_explorer/core/package_layout.py`, `dt_explorer/ui/package_view.py`,
  `dt_explorer/ui/page.py` (tab only).
- Do: layout functions QFP/QFN (four sides, counter-clockwise from pin 1) and BGA (grid from ball
  names); SVG with pins coloured by state.
- Accept: geometry tests (pin 1 position, 32 pins per side for QFP128, BGA A1 top-left); screenshots
  of TQFP128 and TFBGA120 in evidence. Then **HC-2**.

**P1.8 Open-source files.** *light*
- Dep: – (HC-1 decided: Apache-2.0). Scope: `LICENSE`, `CONTRIBUTING.md`, `README.md` (license
  line), `CLAUDE.md`.
- Accept: CONTRIBUTING.md explains how to write a provider, formatter, recipe, translation, board
  reader and connector type, with the Microchip plugins as the reference.

### Phase 2: builds from the tool

| ID | Status | Commit |
|---|---|---|
| P2.1 | done | 35480d1 → master |
| P2.2 | done | 2c90c48 → master |
| P2.3 | done | c5d66c2 → master |

**P2.1 `west_tools`.** PS with P1.4–P1.7.
- Dep: P1.1. Scope: `dt_explorer/core/west_tools.py`.
- Do: background subprocess runner with streamed log; board list from `board.yml` files filtered by
  vendor (`microchip`, `atmel`); `cmake_only_build(app, board, build_dir)`, `full_build(build_dir)`.
- Accept: board list contains `same54_xpro`, `sam_e54_xpro`, `pic32cx_sg61_cult` (27 Microchip/Atmel
  boards in the RTOS4 workspace); a cmake-only build of lab1 into `%TEMP%` succeeds (≈ 25 s).

**P2.2 "New build…" and "Full build" UI.**
- Dep: P2.1. Scope: `dt_explorer/ui/new_build.py`, `dt_explorer/ui/page.py` (header button only).
- Accept: screenshot; after the dialog the new build appears in the build list and shows
  `notlinked` states; goldens unchanged.

**P2.3 Vendor defaults out of core and ui.** *light*
- Dep: P2.2. Scope: `dt_explorer/core/west_tools.py`, `dt_explorer/ui/new_build.py`, `dt_explorer/core/plugins.py`
  (only: an optional `board_vendors` attribute that providers may declare), `dt_explorer/providers/microchip_atdf.py`
  (declare it).
- Do: the board list's default vendor filter comes from the registered providers (`board_vendors`), not from a
  hardcoded `("microchip", "atmel")` in core/ui. (Found at P1.8 review.)
- Accept: the board list still has 27 entries for the RTOS4 workspace; `grep -rnEi "microchip|atmel" dt_explorer/core/west_tools.py dt_explorer/ui/new_build.py` → no hits.

### Phase 3: SERCOM configurator

| ID | Status | Commit |
|---|---|---|
| P3.1 | done | 0546fb7 |
| P3.2 | done | 3876c45 |
| P3.3 | done | 7037d88 |
| P3.4 | done | cc9b28a |
| P3.5 | open | |

**P3.1 Intent-based draft.**
- Dep: P1.2. Scope: `dt_explorer/core/draft.py`.
- Do: draft model as in CONFIGURATOR.md (class from base binding include chain, roles, neutral
  properties, use; instance, pins, vendor properties separate); JSON persistence at
  `<app>/.dt_explorer/<build>.draft.json`.
- Accept: class detection gives `uart` for `atmel,sam0-uart` (via `uart-controller.yaml`) and `i2c`,
  `spi` likewise; JSON round trip.

**P3.2 SERCOM recipe including `lift`.**
- Dep: P3.1, P1.5. Scope: `dt_explorer/recipes/atmel_sam0_sercom.py`.
- Do: roles, `derived_props` (`rxpo`/`txpo`, `dipo`/`dopo`), `lift`, Kconfig table.
- Accept: `lift` on `same54_xpro` SERCOM2 (`rxpo=1`, `txpo=0`, PB25 PAD0, PB24 PAD1) gives TX = PB25,
  RX = PB24; derive → lift round trip for every valid pad combination.

**P3.3 Overlay rendering and Kconfig suggestions.**
- Dep: P3.2. Scope: `dt_explorer/core/render.py`, `dt_explorer/core/kconfig_suggest.py`.
- Do: `render_overlay` through the formatter; Kconfig from the recipe table, otherwise derived with
  the workspace's `kconfiglib` (`DT_HAS_<COMPAT>_ENABLED`, follow `depends on`).
- Accept: the rendered SERCOM2 UART block matches the `same54_xpro.dts` block semantically (edtlib
  compare, not text compare); suggestions for an I2C SERCOM include `CONFIG_I2C`.

**P3.4 Fast check.**
- Dep: P3.3. Scope: `dt_explorer/core/validate.py`; `dt_explorer/core/build.py` (only: construct
  `BuildModel` from an EDT object).
- Do: edtlib on `zephyr.dts.pre` + overlay with resolved macros; pin conflicts; provider
  constraints as warnings; missing required properties.
- Accept: a deliberate conflict (second UART on PB24) is reported; an IOSET mix is a warning; a
  correct overlay yields no findings; goldens unchanged.

**P3.5 Configurator UI.**
- Dep: P3.4, P1.7. Scope: `dt_explorer/ui/configure.py`, `dt_explorer/ui/draft_tab.py`,
  `dt_explorer/ui/package_view.py`, `dt_explorer/ui/page.py` (button, tab, badge only).
- Accept: end-to-end in the browser (Playwright or screenshots): configure an unused SERCOM as I2C,
  the snippet appears in the Draft tab, the package view highlights the pins; README and
  `docs/GUI.md` updated in a follow-up *light* card P3.6 (scope: those two files, plus `docs/make_screenshots.py`). P3.6 also documents the "New build…" and "Full build" buttons from P2.2 and fixes the header wrap (the origin chips break into a second row since P2.2).

### Phase 4: authoritative check

| ID | Status | Commit |
|---|---|---|
| P4.1 | open | |

**P4.1 "Verify with west" and file output.**
- Dep: P3.5, P2.1. Scope: `dt_explorer/core/verify.py`, `dt_explorer/ui/draft_tab.py`.
- Do: `west build --cmake-only` into a scratch dir with `-DEXTRA_DTC_OVERLAY_FILE`/
  `-DEXTRA_CONF_FILE`; optional write to `dt_explorer.overlay`/`.conf` in the app folder.
- Accept: a valid draft verifies; a draft with a misspelled property fails with Zephyr's message in
  the UI; the user's build folder is untouched (mtime check in evidence).

### Phase 5: translate the driver family (core of the main use case)

| ID | Status | Commit |
|---|---|---|
| P5.0 | done | 8b37419 → master |
| P5.1 | done | 11a02eb → master |
| P5.2 | done | adf5271 → master |
| P5.3 | done | cb820bd → master |
| P5.4 | open | |
| P5.6 | open | |
| P5.7 | open | |
| P5.5 | open | |

**P5.0 Research: binding pairs `atmel,sam0-*` ↔ `microchip,*-g1`.** *light* (read-only research).
- Dep: –. PS with everything. Scope: `docs/research/binding_pairs.md` only.
- Do: property-by-property table for SERCOM UART/I2C/SPI, GPIO, pinctrl, clocks (gclk/mclk →
  `microchip,sam-d5x-e5x-clock`), Kconfig symbols, from both binding YAMLs and both board files
  (`same54_xpro`, `sam_e54_xpro`). Note every property without a counterpart.
- Accept: every property of each source binding appears in the table (the evidence lists counts per
  binding: source properties = table rows).

**P5.1 `microchip,port-g1-pinctrl` formatter.** PS with P5.0.
- Dep: P1.5. Scope: `dt_explorer/formatters/microchip_port_g1.py`.
- Accept: as P1.5, against `hal_microchip`'s `atsame54p20a-pinctrl.h` and `sam_e54_xpro-pinctrl.dtsi`.

**P5.2 Translation format and first translations.**
- Dep: P5.0, P1.2. Scope: `dt_explorer/core/translate.py`,
  `dt_explorer/translations/atmel_sam0_to_mchp_g1.yaml`.
- Do: YAML schema (keep/rename/transform/drop/derive per property), loader, Python hook mechanism;
  an unlisted property is reported, never dropped.
- Accept: translating the `same54_xpro` SERCOM2 node gives a node that edtlib accepts against the
  `microchip,sercom-g1-uart` binding.

**P5.3 Lift a whole build into a draft.**
- Dep: P5.2, P3.2. Scope: `dt_explorer/core/lift.py`.
- Accept: lifting lab1, lab3 (old port) gives drafts whose rendered overlays reproduce the
  original nodes (edtlib compare).

**P5.4 "Port to board…" UI and report.**
- Dep: P5.3, P5.1, P4.1. Scope: `dt_explorer/ui/port.py`, `dt_explorer/core/port_report.py`,
  `dt_explorer/ui/page.py` (button only).
- Do: source build → target build; report: untranslatable properties, Kconfig differences,
  `DT_NODELABEL` references in `src/` with suggested `DT_ALIAS`/`DT_CHOSEN` and the matching aliases
  in the target overlay.

**P5.6 "Apply to app".**
- Dep: P5.4, P4.1. Scope: `dt_explorer/core/apply.py`, `dt_explorer/ui/port.py`.
- Do: on a button press, write the port result into the app as the files Zephyr picks up for the
  target board: `<app>/boards/<target board>.overlay` and `<app>/boards/<target board>.conf`.
  **Only new files:** if a target file already exists, write nothing, show a diff against it and let
  the user decide. Never touch the source board's files or `src/`.
- Accept: on a copy of lab1 in `%TEMP%`, apply for `sam_e54_xpro`, then
  `west build -b sam_e54_xpro` on that copy succeeds without extra options; a second apply refuses
  and shows the diff (evidence: both outputs).

**P5.7 "Edit pins…" for enabled nodes.**
- Dep: P5.3, P3.5. Scope: `dt_explorer/ui/configure.py`, `dt_explorer/ui/page.py` (button only).
- Do: for a node that is already `okay`, lift it into a draft (P5.3) and open the configurator on
  it; the result is an overlay that overrides the node (e.g. new pins), rendered as usual.
- Accept: on lab1, move the console SERCOM2 from PB24/PB25 to another valid pad pair; "Verify with
  west" passes and the generated `pinctrl-0` points to the new group; goldens unchanged.

**P5.5 Acceptance: RTOS4 labs to `sam_e54_xpro`.**
- Dep: P5.4, P5.6. Scope: `docs/evidence/P5.5.md`, copies of the labs under `%TEMP%` (not
  committed).
- Accept: lab0–lab3 ported and applied (P5.6) to copies; `west build -b sam_e54_xpro` passes on each
  copy; full build of at least lab1 links. Not tested on hardware (HC-4 decided: no stop); the evidence says so.

### Phase 6: port to PIC32CX SG41/SG61

| ID | Status | Commit |
|---|---|---|
| P6.0 | done | f084991 |
| P6.1 | running | |
| P6.2 | open | |

**P6.0 Research: two Zephyr versions in one process.** *light*, PS.
- Dep: –. Scope: `docs/research/edtlib_versions.md`.
- Question: `edt.pickle` needs the edtlib of its own workspace. Can source (v4.3.0) and target
  (`main`) models live in one process, or must one side load in a subprocess? Test it, record the
  result with output.

**P6.1 Cross-workspace porting.**
- Dep: P6.0 (P5.4 dropped by the builder 2026-10-15: the model host does not need the port UI; the workspace exists since 2026-10-15). First create `C:\Masters\zephyr-main` (HC-3 pre-approved, see §5.1). Scope: as P6.0 concludes (expected: `dt_explorer/core/build.py`,
  `dt_explorer/core/model_host.py`).
- Accept: a source build from `DTX_RTOS4_WS` and a target build from `DTX_MAIN_WS` open together;
  goldens unchanged.

**P6.2 Acceptance: RTOS4 labs to `pic32cx_sg41_cult` and `pic32cx_sg61_cult`.**
- Dep: P6.1. Scope: `docs/evidence/P6.2.md`.
- Accept: lab0–lab3 verify for both boards; the SG61 report lists exactly the uses of PB26–PB29 /
  PC04 in the source (none expected for the labs; check with a synthetic draft that uses EXT1 SPI).

### Phase 7: board view (branch, parallel after P1.2)

| ID | Status | Commit |
|---|---|---|
| P7.1 | done | e5fcf55 → master |
| P7.2 | done | 1d23b26 → master |
| P7.3 | done | 7d3f349 → master |
| P7.3b | done | cb702b2 → master |
| P7.4 | open | |

**P7.1 Altium reader and `BoardNetlist`.** PS with phases 2–6.
- Dep: P1.2. Scope: `dt_explorer/core/board.py` (neutral `BoardNetlist`),
  `dt_explorer/readers/altium_pcbdoc.py`. Start from `prototypes/altium_pcbdoc_trace.py` (do not
  import it).
- Accept: tests skip without `DTX_DESIGN_FILES`; with it, SAME54 Xpro gives 255 nets, 296
  components, 1251 pads; SG41 CUlt 281 nets, 322 components, 1181 pads (numbers from the prototype).

**P7.2 Connector types and detection.** PS with P7.1 (disjoint scope).
- Dep: P1.2. Scope: `dt_explorer/connectors/xplained_pro_ext.py`, `arduino_uno_r3.py`, `mikrobus.py`.
- Accept: detection on synthetic netlists; pin roles match the Zephyr bindings
  `atmel-xplained-pro-header`, `arduino-header-r3`, `mikro-bus`.

**P7.3 Import pipeline and board YAML.**
- Dep: P7.1, P7.2, P1.4. Scope: `dt_explorer/importers/pipeline.py`, `dt_explorer/boards/*.yaml`.
- Do: MCU match against the build's SoC (not "most pads"), tracing, connector type and name
  suggestions (silkscreen proximity), YAML with schema version, Zephyr board name, source package
  and revision, everything marked *suggested*.
- Accept: EXT1–EXT3 of the SAME54 Xpro and EXT1/EXT2 of the SG41 CUlt match the tables in
  CONFIGURATOR.md (*Checked with real design files*); gaps (`TCC4/WO0`, `NetJ602_18`) are reported.
  Connector names: confirmed by the builder per §5.1 (HC-6). SG61 from the files the human
  provides (HC-5); if missing, split off P7.3b and continue.

**P7.4 Board view UI and connector roles in the port report.**
- Dep: P7.3, P5.4. Scope: `dt_explorer/ui/board_view.py`, `dt_explorer/core/port_report.py`,
  `dt_explorer/ui/page.py` (tab only).
- Accept: report line of the form "EXT1 pin 16 (MOSI): PB27 on SAME54 Xpro → PB24 on SG41 CUlt"
  for a synthetic draft using EXT1 SPI; screenshot of both board views.

## 9. Brief template

The builder copies this, fills in the brackets, and passes it as the sub-agent prompt.

```text
You implement card [ID] of docs/IMPLEMENTATION_PLAN.md in C:\Masters\dt_explorer.

Read first: docs/IMPLEMENTATION_PLAN.md sections 2 (rules) and 4 (stop conditions), the card [ID],
and these parts of docs/CONFIGURATOR.md: [sections]. Do not read other cards.

Goal: [one sentence].
Write scope (binding): [paths]. Plus docs/evidence/[ID].md and tests/test_[module].py.
Environment: DTX_RTOS4_WS=[…] DTX_MAIN_WS=[…] DTX_DESIGN_FILES=[…]
Acceptance (run all, paste complete output into the evidence file): [commands and criteria].

Finish with exactly one commit "[ID]: <summary>" staging only explicit paths from the write scope.
No Co-Authored-By trailer, no push, do not edit docs/IMPLEMENTATION_PLAN.md.
Stop only for the conditions in section 4; otherwise keep working.
Report back: commit hash, files changed, acceptance results (numbers, not just PASS), anything
surprising, and anything you were unsure about.
```

## 10. Backlog (not version 1)

- Formatters: `decode(int)` for packed pinmux values, so `core/pins.py` no longer re-reads macro names from source lines (found at P1.6).

- Importer: Arduino Uno R3 and mikroBUS headers (label-keyed pins, merge the four Arduino headers into one connector); found at P7.3.

- Phase 9: custom board wizard.
- Phase 10: class-based porting, other vendors.
- KiCad, IPC-2581/ODB++, IPC-D-356 and Eagle readers.
- Upstream Zephyr PR with connector nodes for `same54_xpro`, `pic32cx_sg41_cult`, `pic32cx_sg61_cult`
  (outward-facing: human only).

## 11. Evidence index

| Card | Evidence | Notes |
|---|---|---|
| P0.1 | [P0.1.md](evidence/P0.1.md) | 5 goldens (lab0 85 nodes, lab1 86, lab3 89, lab1_leer 85, lab1 cmake-only 86); negative test fails on one changed state; `DTX_GOLDEN_DIR` points the test at a copy; builder re-run: 13 passed, 2 skipped |
| P5.0 | [P5.0.md](evidence/P5.0.md) | rows = properties for all 10 source bindings (uart 45, i2c 39, spi 41, sercom 30, gpio 34, pinctrl 34, pinmux 28, gclk 32, mclk 29, osc32kctrl 32). **Finding:** v4.3.0 has no `microchip,sercom-g1-i2c`/`-spi` binding or driver (dtsi references `sercom-g1-i2c` without YAML; builder confirmed). P5.2 therefore covers UART, GPIO, pinctrl and clocks; I2C/SPI translation is checked against `zephyr-main` at P6.1. 92 old-only pin macro names need a rename map. |
| P1.1 | [P1.1.md](evidence/P1.1.md) | core/build.py 472 lines (stdlib only, no nicegui), ui/page.py 568, app.py 21, launcher 17; goldens unchanged; builder re-run: 13 passed, HTTP 200 on a deep link, no traceback |
| P1.2 | [P1.2.md](evidence/P1.2.md) | 8 tests; negative test fails with the check removed; filled gaps: api_version equality, board reader `extensions`/`read`, connector `pin_roles`/`detect`/`zephyr_binding`, translation `source`/`target`/`translate` |
| P1.3 | [P1.3.md](evidence/P1.3.md) | lab1: soc same54p20a, series same54, family atmel_sam0, board same54_xpro, vendor `atmel` (from board.yml; RTOS4 soc.yml has no vendor key); builder re-run 8 passed |
| P2.1 | [P2.1.md](evidence/P2.1.md) | 27 Microchip/Atmel boards (builder reproduced); cmake-only lab1 35.4 s, 35 log lines streamed; bad board → rc 1 with Zephyr's message. **Note:** v4.3.0 has `pic32cx_sg61_cult` but no `pic32cx_sg41_cult` (needed from `zephyr-main`, P6) |
| P3.1 | [P3.1.md](evidence/P3.1.md) | sam0-uart/i2c/spi and sercom-g1-uart → uart/i2c/spi via include chain; gclk → None; JSON round trip byte-identical; standalone `BindingIndex` in draft.py; builder re-run 9 passed |
| P7.1 | [P7.1.md](evidence/P7.1.md) | SAME54 Xpro 255/296/1251, SG41 CUlt 281/322/1181 (= prototype); silkscreen 1600/1973 texts, builder found `EXT1`–`EXT3` / `EXT1`–`EXT2` among them. **For P7.3:** header footprints carry 20 extra pads numbered "0" without a net, ignore them |
| P7.2 | [P7.2.md](evidence/P7.2.md) | own type 1.0, others 0.0; random 20-pin max 0.2 (20 seeds); real EXT with bare MCU-pin nets 0.6 → importer threshold ≥ 0.5. `detect(netlist_pins: {pin: net}, pin_count=None)`; Arduino keys are labels (A0, D13, GND…), the importer merges the four headers. Zephyr gpio-map index ≠ physical pin (Xpro: pin = index + 3), `nexus_map()` added. Builder re-run 33 passed |
| P1.4 | [P1.4.md](evidence/P1.4.md) | locate 5–10 ms (builder: 0.01 s); SERCOM2 PAD0 = PA09 D, PB25 D, PA12 C, PB26 C (builder reproduced); TQFP128 128 positions; SAME54P20A: 2 packages, 71 instances, 772 signals, 1 IOSET constraint. **Finding for P6.2:** in the SG61 ATDF, PB26–PB29/PC04 exist as pins with `type="INTERNAL_n"` (SERCOM2 PAD0–2 still listed on PB26–PB28); the provider leaves them out of `Package.pins`, so they count as "not on the package". hal_microchip is not in lab1's zephyr_modules.txt, `locate` falls back to `<ws>/modules/hal/microchip/packs` |
| P2.2 | [P2.2.md](evidence/P2.2.md) | via Playwright: dialog, board filter, streamed log (cmake-only 46 s), new build auto-selected with "not linked" states, Full build 71 s → 7 `device` nodes; not tested: Cancel, failure path. Temporary build folder deleted (builder checked lab1 folder). README/GUI.md moved to P3.6 |
| P5.2 | [P5.2.md](evidence/P5.2.md) | translated SERCOM2 UART loads in edtlib against `microchip,sercom-g1-uart` with the real atsame54p20a dtsi; dropping `rxpo` → EDTError (negative test); 88/92 pin renames as value map (4 SWD pins kept + reported); I2C/SPI/pinmux `no_target`. **Open for P5.3:** `atmel,assigned-clocks` is only a report line (turn into `&clock { gclkperiph … }` nodes?); target SoC dtsi already sets SERCOM `clocks` (emit or omit?); clock blocks are placeholders. Builder re-run 32 passed |
| P1.5 | [P1.5.md](evidence/P1.5.md) | 134 SERCOM signals, 130 round trip; 4 without Zephyr macro (SERCOM7 on PB30/PB31/PA30/PA31 function C) reported and pinned by a test; `sercom2_uart_default` → PB25/PB24. Header lives in hal_atmel (`modules/hal/atmel/include/dt-bindings/pinctrl/same54p-pinctrl.h`). Formatter needs a header: `AtmelSam0Formatter.for_workspace(ws, soc)`; full parse via `parse()`. Builder follow-up: provider now sets `Signal.attrs["instance"]` |
| P7.3 | [P7.3.md](evidence/P7.3.md) | EXT1–EXT3 (Xpro) and EXT1/EXT2 (SG41) match CONFIGURATOR.md on all 16 signal pins; gaps reported: TCC4/WO0, TCC4/WO1, NetJ602_18, NetJ603_8, NetJ603_18; all 5 connectors `reviewed: builder` (silkscreen + score ≥ 0.5 + GND/VCC + ≥ 75 % traced); MCU match U200 / U401 by package fit 100 %. Arduino and mikroBUS headers are not imported yet (label-keyed pins, four-header merge missing) → backlog. Builder follow-up: `test_plugins` now restores the registry after each test |
| P5.1 | [P5.1.md](evidence/P5.1.md) | 134/134 SERCOM signals round trip against the hal_microchip header (`include/dt-bindings/sam/sam_d5x_e5x/atsame54/atsame54p20a-pinctrl.h`); macro names identical to the old port (130 same, 0 different, 4 only new: SERCOM7 on PB30/PB31/PA30/PA31); `sercom2_uart_default` → PB25/PB24. Builder follow-up: `formatters/` and `providers/` register their modules on package import |
| P3.2 | [P3.2.md](evidence/P3.2.md) | SERCOM2 lift TX=PB25 RX=PB24; exhaustive check accepts exactly UART 6 / SPI 6 / I2C 1 assignments (618/618/23 rejected); board nodes: SPI sercom4 MOSI PB27 SCK PB26 MISO PB29, I2C sercom7 SDA PD08 SCL PD09. Tables from ATDF value groups (binding has no SPI table; E5x offers only dopo 0/2). API extension: `pin_roles(instance, compatible=None)`, `kconfig(compatible=None)` — the Recipe protocol cannot name the class otherwise. Builder re-run 50 passed |
| P1.8 | [P1.8.md](evidence/P1.8.md) | LICENSE = canonical Apache-2.0 (builder diffed against Zephyr's copy: only the `[]`/`{}` placeholder variant differs); CONTRIBUTING covers all 13 protocol methods and six plugin kinds; copyright "The Devicetree Explorer contributors". Builder follow-up: `app.load_plugins()` imports the built-in plugin packages and calls `discover_entry_points()` at startup; `readers/` registers on import. Open: vendor defaults in core/ui (`west_tools.DEFAULT_VENDORS`, `ui/new_build.py`) → card P2.3 |
| P2.3 | [P2.3.md](evidence/P2.3.md) | 27 boards with provider vendors (builder reproduced), 823 with all vendors; no vendor strings in west_tools/new_build |
| P7.3b | [P7.3b.md](evidence/P7.3b.md) | Human provided the SG61 files 2026-10-15; builder found the CAD folder byte-identical to SG41 (same PCB "SGxx" R5); BOM EV09H35A rev 5.0: U401 = PIC32CX1025SG61128-E/XCB. EXT1/EXT2 identical to SG41 (40 pins, 0 differences): the U401 pads at positions 27, 102–105 (PC04, PB26–PB29 on SG41) carry no net on this PCB. So the PB26–PB29/PC04 conflict in P6.2 comes from the source side (SAME54 Xpro EXT1 SPI on PB26–PB29), not from the CUlt connectors. Pipeline marks pins on NC positions `available: false`. Builder re-run 14 passed |
| P1.6 | [P1.6.md](evidence/P1.6.md) | lab1: 128 pins, 23 used, 77 free, 1 conflict (PC18: led_0 gpio + TCC0 WO2 pinctrl); console PB24/PB25 on sercom2, LED PC18. Limits: `my_led@41008000` (custom binding with plain `port`/`pin` ints) is not detected; pinmux ints are decoded by re-reading the macro names from the source line in zephyr.dts (formatters have no int decoder); GPIO controller → pad group by address order (heuristic, documented). Builder: screenshot checked, re-run 7 passed |
| P3.3 | [P3.3.md](evidence/P3.3.md) | rendered SERCOM2 overlay applied to zephyr.dts with sercom2 stripped → edtlib compare equal to the original (compatible, status, rxpo, txpo, current-speed, chosen, pinmux 0x3391/0x3381); Kconfig: recipe path for I2C (CONFIG_I2C, CONFIG_I2C_SAM0), kconfiglib path without recipe (`atmel,sam0-adc` → ADC_SAM0 → ADC; `gpio-leds` → LED_GPIO → LED), first load ≈ 5 s then cached. Agent stopped on one red test from registry pollution between test files; builder fix: autouse fixture in tests/conftest.py restores the registry after every test |
| P3.4 | [P3.4.md](evidence/P3.4.md) | lab1: SERCOM0 on PB24/PB25 → 2 `pin_conflict` errors naming sercom0+sercom2; SERCOM3 on PC23+PA16 → IOSET warning with provider text; SERCOM1 I2C on PC22/PC23 and the board's SERCOM2 → no findings; missing rxpo → `missing_property` error. Preprocessing reproduces `zephyr_dt_preprocess` from build_info.yml. Warm 1.3–1.5 s (binding-file read cache around EDT, not thread-safe). Pre-existing conflicts (PC18) are `info` unless an overlay node is involved. **For P3.5:** the renderer emits no `#address-cells`/`#size-cells` for I2C/SPI unless they are in `vendor_props` — the recipe or the renderer must supply them. Builder re-run 7 passed |
| P5.3 | [P5.3.md](evidence/P5.3.md) | lab1: 3 covered (sercom2 uart, sercom4 spi, sercom7 i2c), 41 uncovered enabled (34 SoC, 6 board, 1 overlay); lab3: 4 covered (+ sercom3 i2c PA22/PA23), 42 uncovered. Per-node and whole-draft edtlib compare: all MATCH (28 MATCH lines, 0 mismatch). Lifts only what board/app wrote (origin comments). Not representable yet: child nodes on buses (`eeprom@5e`, `mytemp@4f`) → phase 8. Builder re-run 3 passed |
| P6.0 | [P6.0.md](evidence/P6.0.md) | `zephyr-main` = Zephyr 4.5.0-rc1, uses SDK 1.0.1 (installed); cmake-only pic32cx_sg41_cult hello_world 161 s. edtlib.py +753/−52 (new `_LocalProps`, `MapEntry`, `HexInt`). Plain sys.path add fails (`_LocalProps`); swap+purge works but is order dependent; **both module sets alive + `Unpickler.find_class` mapping works** (0.17 s), also across Python 3.12/3.14; subprocess JSON 0.45 s as fallback. Recommendation: `core/model_host.py` `EdtlibHost` per src dir, BuildModel changes only the unpickle lines |
| P1.7 | [P1.7.md](evidence/P1.7.md) | 9 geometry tests (pin 1 left/top, 32 per side, pin 33 first on bottom, BGA A1 top-left, TFBGA120 15×15 rows A–R without I/O/Q); screenshots TQFP128/TFBGA120 checked by the builder (pin 100 = PB24, PC18 conflict orange). Builder follow-up: conftest keeps built-in plugins that were first imported during a test; full suite 281 passed, 4 skipped |
| P0.2 | [P0.2.md](evidence/P0.2.md) | 10 tests; skips with reasons; builder re-run: 9 passed, 1 skipped (DTX_MAIN_WS) |
