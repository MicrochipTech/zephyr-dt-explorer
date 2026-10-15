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
| HC-2 | after P1.7 | **Decided 2026-10-15:** accepted, no changes ("carry on") |
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
them as `plan: phase 8 cards`, and continues. Input for these cards: [research/phase8_input.md](research/phase8_input.md). Phases 9 and 10 stay in the backlog. New ideas during
the run go to the *Backlog* (§10), not into open cards.

Phase 8 critical path: P8.1 → P8.2 → P8.4/P8.5 → P8.6 → P8.13 → P8.14 → P8.19; parallel: P8.3, P8.12, P8.16.

Critical path (version 1): P0.1 → P1.1 → P1.2 → P1.4 → P1.5 → P3.1 → P3.2 → P3.3 → P3.4 → P4.1 → P5.0 → P5.2 →
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
| P3.5 | done | b0d4f98 |
| P3.6 | done | ac29235 → master |
| P3.7 | done | 4f0a2d7 → master |
| P3.8 | done | 3abb921 |

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

**P3.7 Header wrap and noise fields.** *light*
- Dep: P3.6. Scope: `dt_explorer/ui/page.py` (header only), `dt_explorer/ui/configure.py` (property field list only).
- Do: the header fits one row at 1500 px again (origin chips compact, e.g. smaller or in a menu); the Configure
  dialog hides base-binding properties that are not about the peripheral (`sq-size`, `cq-size`: keep a generic rule,
  e.g. hide properties from bindings outside the class base binding's own file, or a small deny list in the UI).
- Accept: screenshot of the header at 1500 px in one row; the I2C dialog without `sq-size`/`cq-size`; suite green.

**P3.8 SERCOM recipe for the g1 port.**
- Dep: P3.2, P5.2b. Scope: `dt_explorer/recipes/atmel_sam0_sercom.py`, `tests/test_atmel_sam0_sercom.py`.
- Why: the recipe covers only `atmel,sam0-uart/-i2c/-spi`; Configure… / Edit pins… on a PIC32CX SG or new-port SAM
  E54 build (`microchip,sercom-g1-uart/-i2c/-spi`) had no roles, derived properties or Kconfig table (hint from side
  session dt-explorer-c7). The pad registers are identical (P5.2b), so the same recipe serves both ports.
- Do: add the g1 compatibles with their own Kconfig table (symbols verified in `zephyr-main` drivers/*/Kconfig*);
  class detection per compatible; no change for the sam0 compatibles.
- Accept: on the main pic32cx_sg41_cult build, a draft SERCOM I2C and a UART get roles, derived props and the g1
  Kconfig symbols; render + validate on that build give no errors; existing recipe tests unchanged; suite green.

### Phase 4: authoritative check

| ID | Status | Commit |
|---|---|---|
| P4.1 | done | f789552 |

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
| P5.2b | done | deffa6b |
| P5.3 | done | cb820bd → master |
| P5.4 | done | edbbc04 |
| P5.4b | done | 0d1cdb6 |
| P5.4c | done | b16e35b → master |
| P5.6 | done | 9998b9e |
| P5.7 | done | fcea955 → master |
| P5.5 | done (lab3 exception) | 71c17c8 |

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

**P5.2b I2C/SPI translations against Zephyr main.**
- Dep: P5.2, P6.1. Scope: `dt_explorer/translations/atmel_sam0_to_mchp_g1.yaml` (+ `.py` hooks),
  `docs/research/binding_pairs.md` (I2C/SPI sections only), `tests/test_translate.py`.
- Do: `zephyr-main` has `microchip,sercom-g1-i2c` and `-spi` (missing in v4.3.0, see P5.0). Add property tables
  for both (same method as P5.0), replace the `no_target` entries by real translations; keep a report line when the
  target workspace lacks the binding (v4.3.0).
- Accept: the lab1 sercom4 SPI and sercom7 I2C nodes translate and load in edtlib against the main bindings with the
  main SoC dtsi of pic32cx_sg41; against v4.3.0 they still report `no_target`; suite green.

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

**P5.4b Carry app-defined nodes over.**
- Dep: P5.4. Scope: `dt_explorer/core/port_report.py`, `tests/test_port_report.py`.
- Do: per the decision *App-defined nodes when porting* in CONFIGURATOR.md: nodes the app overlay defines or
  modifies (origin kind `overlay`, no translation) go verbatim into the target overlay (source text from the app
  overlay, macros as written), when all referenced labels/phandles exist in the target; bus children only when their
  bus is ported and enabled; everything else is reported with the reason.
- Accept: lab1 → sam_e54_xpro: the target overlay contains `my_led0: my_led@41008000 { … }`; a FULL build
  (`west build -b sam_e54_xpro`, not cmake-only) of a copy of lab1 in %TEMP% with that overlay as
  `EXTRA_DTC_OVERLAY_FILE` links and `__device_dts_ord_*` of my_led is in `nm zephyr.elf`; lab3: `mytemp0` is reported
  (bus sercom3 I2C has no target in v4.3.0), `my_http0` handled per the rule; suite green.

**P5.4c Docs for port dialog and compact header.** *light*
- Dep: P5.4, P3.7. Scope: `README.md`, `docs/GUI.md`, `docs/make_screenshots.py`, `docs/img/*.png`.
- Accept: README row and GUI.md section for "Port to board…" and the "Legend" menu (header section no longer describes
  the chip row / wrap issue); element grep in both files; regenerated header and port screenshots.

**P5.6 "Apply to app".**
- Dep: P5.4b, P4.1. Scope: `dt_explorer/core/apply.py`, `dt_explorer/ui/port.py`.
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
| P6.1 | done | b993082 |
| P6.2 | done (findings) | 532396b |
| P6.3 | done | 6d93f0a → master |
| P6.2b | done | 4f7ade9 |

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
- Dep: P6.1, P5.4b, P5.2b. Scope: `docs/evidence/P6.2.md`.
- Accept: lab0–lab3 verify for both boards; the SG61 report lists exactly the uses of PB26–PB29 /
  PC04 in the source (none expected for the labs; check with a synthetic draft that uses EXT1 SPI).

**P6.3 Port conflicts with the target board.**
- Dep: P6.2. Scope: `dt_explorer/core/port_report.py`, `tests/test_port_report.py`, `dt_explorer/ui/port.py`
  (report display only).
- Why (P6.2): the port keeps the source board's instance and pads. On pic32cx_sg41_cult the board uses `&sercom4` as
  console UART, so the ported SPI overlay left `current-speed` and `zephyr,console` on it and Zephyr rejected it; on
  SG61 `&sercom4` was dropped silently (PB27 not on the package) with a misleading message; on SG61 the board's
  `sercom6` I2C already uses PD08/PD09.
- Do: before emitting a node, compare with the target board: (a) the target node is enabled with a different
  class/compatible, or (b) a ported pad is used by another enabled target node, or (c) a pad is not on the target
  package → status `conflict` (not emitted), report line with the reason and the target user; never drop a node
  without a section-1 line; chosen/aliases are not moved. Suggestion only: from section 7, the target pads at the
  same connector pins and the instances that can mux them for the same roles (via provider/recipe), e.g.
  "EXT1 SPI on the target: PC24/PB24/PC25 → SERCOMx … (choose with Configure…)". No automatic remap (phase 8).
- Accept: the eight P6.2 ports produce overlays that Zephyr accepts (verify rc 0) with every skipped node listed as
  `conflict` with its reason; lab1 → sam_e54_xpro unchanged; suite green.

**P6.2b Acceptance re-run after P6.3.** Scope: `docs/evidence/P6.2b.md`. Same matrix as P6.2, plus lab3 →
`sam_e54_xpro` in `zephyr-main` (which has the g1 I2C driver) with apply to a copy and a full build: closes the lab3
exception of P5.5.

### Phase 7: board view (branch, parallel after P1.2)

| ID | Status | Commit |
|---|---|---|
| P7.1 | done | e5fcf55 → master |
| P7.2 | done | 1d23b26 → master |
| P7.3 | done | 7d3f349 → master |
| P7.3b | done | cb702b2 → master |
| P7.4 | done | ccb9d19 → master |
| P7.5 | done | abceb3b → master |

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

**P7.5 Docs for board view, Apply to app, conflicts.** *light*
- Dep: P7.4, P5.6, P6.3. Scope: `README.md`, `docs/GUI.md`, `docs/make_screenshots.py`, `docs/img/*.png`.
- Accept: README rows and GUI.md sections for the Board tab, "Apply to app…" in the port dialog, report section 7
  (connector roles) and `conflict` lines with suggestions; element grep in both files; screenshots (Apply/verify shots
  reused from evidence, nothing that writes into apps\ or runs west).

### Phase 8: child nodes, generic form, more peripherals (written by the builder 2026-10-15)

Input: [research/phase8_input.md](research/phase8_input.md) (items 1–17). Rules as for all cards. **New for
phase 8:** every card that changes the UI also updates README.md and `docs/GUI.md` in its own scope (CLAUDE.md rule)
and, from P8.2 on, adds a simulated UI test for its new UI. Briefs for UI cards tell the agent to redirect drafts
and writes to copies under %TEMP% and never to `taskkill /IM`.

| ID | Status | Commit |
|---|---|---|
| P8.1 | done | ca96f9b |
| P8.2 | open | |
| P8.3 | done | 75e8539 |
| P8.4 | open | |
| P8.5 | open | |
| P8.6 | open | |
| P8.7 | open | |
| P8.8 | open | |
| P8.9 | open | |
| P8.10 | open | |
| P8.11 | open | |
| P8.12 | done | 2396f4b → master |
| P8.13 | open | |
| P8.14 | open | |
| P8.15 | open | |
| P8.16 | done | a09a65a → master |
| P8.17 | done | 051c73d → master |
| P8.18 | open | |
| P8.19 | open | |
| P8.20 | open | |

**P8.1 Log tab and log file.** (input 14)
- Dep: –. Scope: `dt_explorer/core/log.py` (new: logger setup, rotating file handler, ring buffer, context header),
  `dt_explorer/app.py` (`--debug`, setup at start), `dt_explorer/ui/log_tab.py` (new), `dt_explorer/ui/page.py`
  (tab only), log calls in `core/build.py` (load, counts, timings, `state()` decisions at DEBUG),
  `core/west_tools.py`, `core/verify.py`, `core/apply.py`, `core/port_report.py` (external calls, writes, decisions;
  log lines only, no behaviour change), README.md, `docs/GUI.md`.
- Accept: `%USERPROFILE%\.dt_explorer\logs\dtx_<date>.log` written, rotating; header with tool commit, Python,
  NiceGUI, Zephyr version, workspace, build, board, SoC, ATDF file, plugins; tab shows the same lines live with Copy
  and level filter; `state()` reasons visible at DEBUG; goldens unchanged; docs note about local paths in the log.

**P8.2 UI tests.** (input 15)
- Dep: P8.1. Scope: `tests/ui/` (new), `tests/conftest.py` (UI fixtures only), `requirements-dev.txt`,
  `pyproject.toml` (pytest markers only), CLAUDE.md (one line on UI tests).
- Do: simulated tests with `nicegui.testing` (User fixture) for the existing UI: deep links, Configure… → I2C → pads
  → Save → Draft count and snippet (on a copy of `_soltest/apps/lab1_leer` under %TEMP%), Edit pins, Port to board…
  (report sections, no Apply), Pins/Package/Board tabs; a few Playwright smoke tests (Configure → Check → Verify,
  Port, board view) saving screenshots, skipped without Chrome/Playwright or DTX_*.
- Accept: suite green with and without env vars; each smoke test leaves a screenshot path in its output.

**P8.3 Research: further peripherals.** *light*, PS with everything. (inputs 2, 5)
- Dep: –. Scope: `docs/research/peripheral_pairs.md`.
- Do: as P5.0 for TC, TCC, ADC, DAC, EIC, GMAC (+ PHY/mdio), RTC, WDT, DMAC, TRNG, CAN, USB: bindings old ↔ g1
  (RTOS4 and zephyr-main), presence in `pic32cx_sg.dtsi` and the SAM D5x/E5x dtsi on main, drivers, Kconfig, pin
  signals in the ATDF; per peripheral a recommendation: own recipe (pins + derived props) or generic form.
- Accept: property counts = table rows per binding; recommendation table.

**P8.4 Child nodes in the draft model.** (input 3)
- Dep: P8.2. Scope: `dt_explorer/core/draft.py`, `core/render.py`, `core/lift.py`, `core/translate.py` (children
  pass through translations), `core/port_report.py` (bus children ported with their bus), tests.
- Accept: lab1 `eeprom@5e` and lab3 `mytemp@4f` lift into the draft and render back (edtlib compare as P5.3); JSON
  schema version bumped with a loader for version 1; port of lab3 to main sam_e54_xpro carries `mytemp@4f` as a child
  of the ported I2C node; goldens unchanged.

**P8.5 Generic binding form.** (input 1)
- Dep: P8.2. Scope: `dt_explorer/core/generic.py` (new: form spec from a binding chain with type, enum, default,
  required, description per property; pin roles from the provider's signal roles; recipe-less draft and render path),
  `ui/configure.py`, `ui/generic_form.py` (new), README.md, `docs/GUI.md`, tests incl. UI test.
- Accept: on a lab1_leer copy: configure WDT and TC (counter) without a recipe → overlay renders, validate no errors,
  Verify with west rc 0; a required property left empty is flagged in the form.

**P8.6 Child node form and bus checks.** (inputs 3, 6)
- Dep: P8.4, P8.5. Scope: `ui/configure.py` (add child), `core/generic.py` (compatibles with `on-bus`),
  `core/validate.py` (duplicate I2C address, CS GPIO used elsewhere), README.md, `docs/GUI.md`, tests incl. UI test.
- Accept: add a BME280 (`bosch,bme280`) at 0x76 on a configured SERCOM I2C and an SPI device with a CS GPIO; a second
  device at 0x76 → error; CS on a used pad → error; Verify rc 0 for the valid case.

**P8.7 Board-level nodes: LEDs, buttons, PWM LEDs, aliases/chosen.** (input 4)
- Dep: P8.5. Scope: `core/board_nodes.py` (new), `ui/configure.py` or `ui/board_nodes.py`, README.md, `docs/GUI.md`,
  tests incl. UI test.
- Accept: add a `gpio-leds` child and a `gpio-keys` child on free pads, set alias `led1` and a chosen entry; render,
  validate, Verify rc 0 on a lab1_leer copy.

**P8.8 Recipes PWM (TCC/TC) and ADC.** (input 2; split P8.8a/b if large)
- Dep: P8.3, P8.4, P8.5. Scope: `dt_explorer/recipes/` (new modules per P8.3), tests.
- Accept: PWM on a TCC WO pin with `#pwm-cells` and prescaler derived; ADC with `channel@N` children
  (`zephyr,gain`, `zephyr,reference`, `zephyr,input-positive`) and AIN pin roles; both for the old and the g1 port;
  render + validate + Verify rc 0 on SAME54 (RTOS4) and on main SG41.

**P8.9 GMAC + PHY recipe.** (decision in CONFIGURATOR.md: own recipe, RMII pins automatic)
- Dep: P8.3, P8.4. Scope: `dt_explorer/recipes/` (gmac module), tests.
- Do: RMII roles filled from the provider's signals without hand-picking (a role with one candidate is set
  automatically, alternatives are offered); `mdio` child with the PHY (`ethernet-phy`, address); `phy-handle`;
  Kconfig only for the Ethernet driver, TCP/IP and TLS stay in menuconfig.
- Accept: GMAC + PHY on main pic32cx_sg41_cult (pads per the CUlt board file) render, validate, Verify rc 0.

**P8.10 Translations for further peripherals.** (input 5)
- Dep: P8.3, P8.8, P8.9. Scope: `dt_explorer/translations/`, `docs/research/peripheral_pairs.md` (results), tests.
- Accept: TC, TCC, ADC, GMAC (and the others P8.3 marks translatable) translate and load in main's edtlib against
  pic32cx_sg41; CAN and USB are listed as untranslatable in the port report.

**P8.11 Check rules: pin function and channel clashes.** (input 6)
- Dep: P8.6, P8.8. Scope: `core/validate.py`, tests.
- Accept: analog pin used as digital (and vice versa) → warning; two ADC channels on one input or two PWM users of one
  WO channel → error; negative tests can fail.

**P8.12 Importer: mikroBUS and Arduino headers.** PS. (input 11)
- Dep: –. Scope: `dt_explorer/importers/pipeline.py`, `dt_explorer/connectors/arduino_uno_r3.py`,
  `connectors/mikrobus.py` (merge and label keys only), `dt_explorer/boards/*.yaml`, `ui/board_view.py` (render these
  connector types), README.md, `docs/GUI.md`, tests.
- Accept: SG41/SG61 CUlt YAMLs gain J900/J901 (mikroBUS) and the Arduino header set, SAME54 Xpro its headers if
  present; board view shows them; existing EXT entries unchanged.

**P8.13 Draft tab "Apply to app".** (inputs 8, 9)
- Dep: P8.2. Scope: `ui/draft_tab.py`, `core/apply.py` (additions only), README.md, `docs/GUI.md`, tests incl. UI test.
- Accept: on a copy, the current draft is written as `boards/<board>.overlay`/`.conf` of the build's own board
  (new files only, diff otherwise); `west build` of the copy without options picks it up.

**P8.14 Generated device module `dtx_devices/`.** (input 10; decision in CONFIGURATOR.md)
- Dep: P8.6, P8.13. Scope: `core/codegen.py` (new), `dt_explorer/codegen_templates/` (new), `ui/draft_tab.py`
  ("Generate code"), README.md, `docs/GUI.md`, tests.
- Accept: on a copy with a BME280 draft: `dtx_devices/` generated (header + checksum), the CMakeLists line added
  only after the diff confirmation, `prj.conf` lines via Apply; `main.c` including `dtx_devices.h` builds;
  `west build -p` keeps it; a user-edited generated file is not overwritten (diff shown).

**P8.15 "Changes" tab.** (input 13)
- Dep: P8.13. Scope: `core/git_status.py` (new), `ui/changes_tab.py` (new), `ui/page.py` (tab only), README.md,
  `docs/GUI.md`, tests.
- Accept: on a git repo copy: modified/added/untracked files with diffs; "dtx" badge on tool-written files; Refresh
  only on demand with time and HEAD shown; no repo or no git → message, nothing breaks.

**P8.16 Port report fixes.** *light* (inputs 16, 17)
- Dep: –. Scope: `core/port_report.py`, tests.
- Accept: lab3 report section 3 no longer says "missing" for `DT_ALIAS(sercom3)` when the overlay adds it; a note when
  source and target Zephyr versions differ (API drift possible).

**P8.17 Connector-aware remap when porting.** (input 12)
- Dep: P8.16. Scope: `core/port_report.py`, tests.
- Do: for a conflict whose roles all have target pads at the same connector pins and an instance that can mux them,
  emit the remapped node (marked as remapped, mapping in the report) instead of only suggesting it.
- Accept: lab1 → SG41: EXT1 SPI remapped where a free instance exists, otherwise still `conflict`; verify rc 0.

**P8.18 Acceptance: Zephyr samples.** (input 7)
- Dep: P8.10, P8.11. Scope: `docs/evidence/P8.18.md`.
- Accept: `basic/blinky_pwm`, `drivers/adc/adc_dt` and one I2C sensor sample configured with the tool on SAME54
  (RTOS4) and ported to main pic32cx_sg41_cult; verify rc 0 on both; full build of each links.

**P8.19 Acceptance: the user's SG41 scenario.** (addendum to input 11)
- Dep: P8.7, P8.9, P8.12, P8.14, P8.15. Scope: `docs/evidence/P8.19.md`.
- Accept: from a `pic32cx_sg41_cult` blinky copy (main): add a UART, an I2C/SPI sensor on the mikroBUS socket and
  GMAC + PHY with the tool; Apply writes `boards/…overlay/.conf`, `prj.conf` lines and `dtx_devices/`; full build
  links; the Changes tab lists exactly the written files; not tested on hardware.

**P8.20 Phase 8 docs and CONFIGURATOR sync.** *light*
- Dep: all other phase 8 cards. Scope: README.md, `docs/GUI.md`, `docs/CONFIGURATOR.md` (status notes),
  `docs/make_screenshots.py`, `docs/img/`.
- Accept: every phase 8 UI element documented with a screenshot; CONFIGURATOR.md phase 8 marked done with links to
  the evidence.

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

- binding_pairs.md table 1.5 (GPIO) misses 6 `gpio-hog` child rows (P5.0 enumerator ignored child-bindings from includes; found at P8.3).
- Generated device module "Generate code" (from side session dt-explorer-c7, user-approved 2026-10-15; builds on child
  nodes and the Apply path from P5.6; consider for phase 8): `<app>/dtx_devices/` with CMakeLists.txt
  (target_sources/target_include_directories), `include/dtx_devices.h` (umbrella, `dtx_devices_init()` with
  device_is_ready for all), one `dtx_<node>.h/.c` pair per device with a small typed API per class (e.g.
  `dtx_bme280_76_read()`, `dtx_led0_set(bool)`, `dtx_pwm_led0_set(pulse_ns)`, `dtx_adc0_ch1_read_mv()`); sensor
  channels from the driver's channel_get (DriverIndex heuristic, user can edit), fetch/get vs read/decode API by
  Zephyr version; generated files carry a header + checksum (unchanged → regenerate, edited → diff only); Kconfig
  lines via Apply into prj.conf; acceptance idea: BME280 on SERCOM I2C on same54_xpro, builds, survives
  `west build -p`. Decision recorded in CONFIGURATOR.md (CMakeLists exception).
- Kconfig view, device-centred (from side session dt-explorer-c7, user-approved 2026-10-15; not a menuconfig
  clone): (1) node details get a Kconfig section with the symbols tied to the node's compatible
  (`core/kconfig_suggest.py`) and their current value from `zephyr/.config`; (2) "why is this off?" for the
  orange dot: the dependency chain (e.g. I2C_SAM0=n because I2C=n), from the same depends-on walk; (3) value origin
  per symbol (prj.conf, boards/<board>.conf, board _defconfig, -DCONFIG_… on the CMake line, default); (4) writes
  only for device-related symbols via `core/apply.py` (P5.6) into prj.conf or boards/<board>.conf, never into
  build/zephyr/.config, diff when the file exists; (5) a button running `west build -t guiconfig` for everything else.
- Formatters: `decode(int)` for packed pinmux values, so `core/pins.py` no longer re-reads macro names from source lines (found at P1.6).

- Importer: Arduino Uno R3 and mikroBUS headers (label-keyed pins, merge the four Arduino headers into one connector); found at P7.3.
- Draft tab "Apply to app": write the checked draft overlay and the suggested Kconfig lines into the current build's app (`<app>/boards/<board>.overlay`/`.conf`, or the app overlay already in use), reusing `core/apply.py` from P5.6 (new file → write; existing file → show diff, user decides; never touch `src/`). Asked for by the user on 2026-10-15.

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
| P6.1 | [P6.1.md](evidence/P6.1.md) | lab1 (86 nodes, v4.3.0) and main pic32cx_sg41_cult hello_world (111 nodes, 4.5.0-rc1) open together in both orders; lab1 still equals its golden; no `devicetree*` left in sys.modules/sys.path; validate() works on both. `kconfig_suggest.py` still patches sys.path itself (main builds not covered there). Full suite 286 passed, 0 skipped (all three env vars). Builder re-run of test_model_host passed |
| P3.5 | [P3.5.md](evidence/P3.5.md) | Playwright on lab1: SERCOM1 → I2C, SDA PC22, SCL PC23, 100 kHz → snippet with `#address-cells`/`#size-cells` (render now emits binding `const` props), CONFIG_I2C + CONFIG_I2C_SAM0, check 0 errors (PC18 info only), PC22/PC23 purple in the package view; negative: SERCOM0 on PB24/PA05 → 2 pin_conflict errors + IOSET warning. 301 passed. Also fixed: tab clicks delivered strings (Pins/Package were empty on click). Builder: screenshot checked; test_render/test_translate now load edtlib via EdtlibHost (passed only in suite order before). Cosmetic: base-binding props `sq-size`/`cq-size` appear as I2C fields → P3.6 |
| P5.7 | [P5.7.md](evidence/P5.7.md) | lab1 console SERCOM2 moved PB25/PB24 → PA09/PA08 (PA12/13 taken by GMAC); validate 0 errors; west cmake-only rc 0, scratch zephyr.dts `pinctrl-0 = <&sercom2_uart_edit>` with pinmux 0x3290/0x3280, board group unused. Override: group renamed `<label>_<cls>_edit` in ui/configure.py (render.py unchanged). **Finding:** parallel agents shared `apps\lab1\.dt_explorer` (one agent's draft broke another's validate) → evidence runs must redirect drafts to scratch. Builder re-run 21 passed |
| P3.6 | [P3.6.md](evidence/P3.6.md) | README + GUI.md sections for Pins, Package, New build…, Full build, Configure…, Draft/check (element grep PASS in both files); make_screenshots.py got `--port` and Playwright shots; 6 new, ~25 regenerated images (header got taller). Incident: an accidental Full build on lab1 during development; builder checked lab1 `edt.pickle`/`zephyr.elf` still dated 2026-10-13. Header wrap and `sq-size`/`cq-size` → P3.7 |
| P4.1 | [P4.1.md](evidence/P4.1.md) | valid draft (SERCOM1 I2C PC22/PC23) rc 0 in 28.7 s (UI 45.5 s); typo `clock-frequncy` → rc 1 with Zephyr's "devicetree error: … is not declared in 'properties:'" shown in the UI; lab1 build folder identical before/after (416 files, same content hash). Board from `CONFIG_BOARD_TARGET`; scratch in %TEMP%\dt_explorer_verify. Full suite 308 passed, 0 skipped |
| P5.4 | [P5.4.md](evidence/P5.4.md) | lab1/lab3 → sam_e54_xpro: target cmake-only builds ok; overlay empty (target board already sets SERCOM2 console identically), conf comments only; verify rc 0 (28 s / 33 s); report: sercom4 SPI / sercom7 I2C `no_target`, my_led/eeprom/mytemp/http not ported, all 10 lab3 code refs listed; P5.7's moved console ports to a `sercom2_uart_edit` override, rc 0. Gaps: `&clock { gclkperiph … }` children not emitted for SERCOMs the target does not configure (report note only); app-defined overlay nodes not carried → builder decision + card P5.4b; docs → P5.4c |
| P3.7 | [P3.7.md](evidence/P3.7.md) | header one row at 1500 px (chips moved into a "Legend" menu), I2C dialog without sq-size/cq-size (deny list: in v4.3.0 they are declared in i2c-controller.yaml itself) |
| P5.4c | [P5.4c.md](evidence/P5.4c.md) | README/GUI.md: Legend, Edit pins…, Port to board…, Verify with west, Write to app (grep PASS in both); wrap known-issue removed; 8 new, 27 regenerated images; west-running shots reused from evidence |
| P5.4b | [P5.4b.md](evidence/P5.4b.md) | lab1 → sam_e54_xpro overlay carries `my_led0` verbatim; FULL build of a lab1 copy links (98 s), `__device_dts_ord_3` (my_led) in nm; lab3: `mytemp0` not carried (bus sercom3 `no_target`), `my_http0` not carried (references mytemp0, root cause named). **For P5.5/P5.6/P6.2:** the labs set `ZEPHYR_EXTRA_MODULES` relative (`${CMAKE_SOURCE_DIR}/../../modules/led`) → copies need the same relative layout (`<tmp>/apps/labN` + `<tmp>/modules/led`); scripts must call `dt_explorer.app.load_plugins()` or the port is silently empty. 327 passed |
| P5.2b | [P5.2b.md](evidence/P5.2b.md) | lab1 sercom4 SPI / sercom7 I2C translate and load in main's edtlib against pic32cx_sg41 dtsi; v4.3.0 still `no_target`; counts sam0-i2c 39/39, sam0-spi 41/41, g1-i2c 37+5 target-only, g1-spi 39+3; dipo/dopo identical on both sides. 355 passed. Builder follow-up: `port_report._translation_for` prefers a `no_target` entry when the target workspace lacks the binding (v4.3.0 dtsi names `sercom-g1-i2c` without YAML); lab1 → sam_e54_xpro again: sercom4/sercom7 `no_target`, no g1-i2c/spi in the overlay |
| P7.4 | [P7.4.md](evidence/P7.4.md) | Board tab (tab=board) with EXT headers coloured by use; report section 7 on lab1 → main sg41: "EXT1 pin 16 (MOSI): PB27 on same54_xpro → PB24 on pic32cx_sg41_cult", EXT1 pin 18 SCK not available (NetJ602_18), EXT3 has no counterpart; optional board `title:` in the loader (YAMLs have none yet). Agent found `port_build` to SG41 failing in the g1 formatter (`find_header` knew only SAME5x paths) → builder fix: header found by file name below hal_microchip dt-bindings; lab1 → sg41 now ports SPI/I2C, fast check shows a real conflict: the SG41 board uses sercom4 as UART (`current-speed` left over when the overlay turns it into SPI) → P6.2 records it. Board tab docs → next docs card |
| P5.6 | [P5.6.md](evidence/P5.6.md) | apply on a lab1 copy wrote `boards/sam_e54_xpro.overlay` (conf comments-only → skipped); `west build -b sam_e54_xpro` without options rc 0 (94 s), `Found devicetree overlay: …/sam_e54_xpro.overlay`, my_led `__device_dts_ord_3` in nm; second apply: identical → no-op, different → refused with diff (UI: overwrite only via checkbox); real lab1 unchanged. Naming per Zephyr extensions.cmake (short name only for single-SoC boards). Incidents: agent ran `taskkill /IM py.exe` (killed the builder preview, restarted) and amended its commit twice (history checked: linear, nothing lost) |
| P6.2 | [P6.2.md](evidence/P6.2.md) | 8 cmake-only target builds of the unmodified labs on main: all rc 0 (≈ 83 s). Port with 354710c: SG41 verify rc 1 for all labs (board uses `&sercom4` as console UART; ported SPI leaves `current-speed`), SG61 verify rc 0 only because `&sercom4` was dropped silently (PB27 not on package). SG61 pin report: exactly PB26/PB27/PB29 for every lab (same54_xpro.dts itself enables sercom4 SPI there), SG41 none. Also: PD08/PD09 conflict with SG61 board `sercom6`; SDA/SCL swapped at the CUlt connector; lab3 `DT_ALIAS(sercom3)` missing. → fix card P6.3, re-run P6.2b |
| P5.5 | [P5.5.md](evidence/P5.5.md) | lab0/lab1/lab2 ported + applied to copies, plain `west build -b sam_e54_xpro` links (120/105/149 s), my_led `__device_dts_ord_3` in the image; lab3 compile fails: `mytemp0`/`my_http0`/`DT_ALIAS(sercom3)` need the SERCOM I2C bus, which has no g1 driver in Zephyr v4.3.0 (predicted by report sections 3/5b) → workspace limitation, re-checked against zephyr-main in P6.2b. Real apps unchanged (fc.exe). Not tested on hardware (HC-4) |
| P3.8 | [P3.8.md](evidence/P3.8.md) | recipe serves `microchip,sercom-g1-uart/-i2c/-spi`; on the main pic32cx_sg41_cult build SERCOM1 I2C (PA00/PA01) + SERCOM3 UART (PA22/PA23) render and validate with no errors; g1 Kconfig table lists only CONFIG_SERIAL/I2C/SPI (the g1 driver symbols are promptless `default y` via DT_HAS_…, assigning them in a conf would abort the build). sam0 tests untouched (0 removed lines). 372 passed. Builder re-run 58 passed |
| P6.3 | [P6.3.md](evidence/P6.3.md) | all 8 lab ports to SG41/SG61 verify rc 0 (SG41 was rc 1); skipped nodes are `conflict` with one line per reason (target uses the instance as another compatible incl. chosen console; pad used by another target node, e.g. SG61 `&sercom6` on PD08/PD09; pad not on package, e.g. SG61 PB27) plus a connector-based suggestion ("spi on EXT1 of the target: MOSI PB24, MISO PC25; no pad for SCK → SERCOM0 …"); code refs to conflicting nodes reported; lab1 → sam_e54_xpro overlay unchanged. Found: `BuildModel.west_top` must come from `WEST_TOPDIR` for cross-workspace builds → builder fixed it in core/build.py (goldens unchanged, full suite 381 passed) |
| P6.2b | [P6.2b.md](evidence/P6.2b.md) | all 8 lab ports to SG41/SG61 verify rc 0, no silent drops, every conflict with reason + suggestion (SG41: sercom2/4/7 conflict with board sercom0/console/sercom6; SG61: sercom2 emitted, sercom4/7 conflict, PB26/27/29 not on package). lab3 → main sam_e54_xpro: sercom3/4/7 emitted, mytemp0/my_http0 carried, applied to a copy; build fails only on Zephyr 4.5 HTTP-server API renames in the app's main.c (app API drift, not a tool issue); with the two renames in a throw-away copy it links (sercom3 g1 I2C device in the image) → closes the P5.5 lab3 exception. Cosmetic: report section 3 says `DT_ALIAS(sercom3) [missing]` although the overlay adds the alias → phase 8 input. Not tested on hardware (HC-4) |
| P7.5 | [P7.5.md](evidence/P7.5.md) | README/GUI.md: Board tab, report sections 5b and 7, conflicts, Apply to app… (grep PASS in both); 3 new live shots, Apply/verify shots reused; make_screenshots.py takes `DTX_PORT_TARGET` for the port shots. **Version 1 complete.** |
| P8.12 | [P8.12.md](evidence/P8.12.md) | SG41/SG61 CUlt YAMLs +277 lines each (only additions): `mikroBUS` J900/J901 (MOSI PB24, MISO PC25, RX PC13, TX PC12, SCL PD08, SDA PD09 = EXT1/EXT2 pads; SCK no pad, NetJ900_4) and `Arduino` J500/J501/J504/J503 (A0–A5, D0–D15; AREF/RESET gaps); SAME54 Xpro has neither; board view renders both, EXT SVG identical. **Note:** Arduino marked `reviewed: builder` on a mechanical-layer "Arduino" text plus agreeing header texts (no silkscreen text) — looser than HC-6's silkscreen wording; tighten if wanted. 398 passed |
| P8.1 | [P8.1.md](evidence/P8.1.md) | `%USERPROFILE%\.dt_explorer\logs\dtx_<date>.log` + Log tab (tab=log) with Copy/level filter/DEBUG switch, `--debug`; header (commit, Python, NiceGUI, Zephyr, board, SoC, ATDF + schema, plugins); loading counts/timings, nm/west calls with rc and duration, writes, tracebacks; `state()` reasons at DEBUG via a thin wrapper (`_state()` unchanged, identical results tested). 15 tests, 396 passed |
| P8.16 | [P8.16.md](evidence/P8.16.md) | section 3 judged after the overlay: lab3 `DT_ALIAS(sercom3)` no longer missing (main sam_e54_xpro, SG41); version note "source Zephyr 4.3.0, target 4.5.0-rc1: app APIs may have changed" (absent within one workspace). Merged after P8.1 without conflicts; builder re-run 39 passed |
| P8.3 | [P8.3.md](evidence/P8.3.md) | recipes: TC/TCC PWM (one module; `#pwm-cells` 2→3, consumers need a third cell), ADC (input from pad, prescaler required on g1, children), DAC (small), GMAC+MDIO+PHY (each RMII signal has exactly one pad; MDC/MDIO 4 pairs; SG41: PC22/PC23 because PB14/15 are TC5 PWM); generic: TC/TCC counter, RTC (compatible selector), WDT, DMAC, TRNG; EIC check rule only; untranslatable: CAN, USB device (no node/driver on main). Fixed-label drivers (rtc, dac, clock) → labels must not change. Enumerator extended for child-bindings from includes (binding_pairs table 1.5 misses 6 gpio-hog rows → backlog) |
| P8.17 | [P8.17.md](evidence/P8.17.md) | remap path works (synthetic SG41 YAML with EXT1 on free SERCOM3 pads: lab1 `&sercom4` → `[remapped]` to `&sercom3`, dipo/dopo recomputed, DT_NODELABEL consequence noted, verify rc 0); on the real CUlt boards nothing qualifies (EXT1 SCK has no pad, SERCOM0/6 busy, sercom2 pads not on a connector) → still `conflict`, only suggestions sharpened (busy instances no longer offered); 8-port matrix + lab1 → sam_e54_xpro overlays identical to before, all verify rc 0. 426 passed. Builder: port dialog status line counts remapped nodes |
| P0.2 | [P0.2.md](evidence/P0.2.md) | 10 tests; skips with reasons; builder re-run: 9 passed, 1 skipped (DTX_MAIN_WS) |
