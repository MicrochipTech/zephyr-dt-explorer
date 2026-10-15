# Input for the phase 8 cards

Received 2026-10-15 from the side session `dt-explorer-c7` (user-approved), as input for the builder
when it writes the phase 8 cards (§7 of IMPLEMENTATION_PLAN.md). Checked by that session against
both workspaces' SoC dtsi and `drivers/`. The builder decides split and order.

1. **Generic binding form.** Designed (CONFIGURATOR.md "Without a recipe ..."), no cards yet for the
   UI, recipe-less drafts and the render path. Covers WDT, RTC, TRNG, DMAC, TC counter.
2. **Recipes for peripherals with pins and derived properties.** TCC/TC PWM (WO channels,
   `#pwm-cells`, prescaler), ADC (`channel@N` children with `zephyr,gain`/`reference`/
   `input-positive`, AIN pins, reference), DAC, EIC, GMAC with PHY child, CAN. The design does not
   say which of these get their own recipe.
3. **Child nodes in the draft model** ("Child nodes follow later", CONFIGURATOR.md `core/draft.py`):
   extend `core/draft.py`, `render_overlay`, `lift`, `translate` and the form (compatible from
   bindings with `on-bus`, `reg`, IRQ/reset/CS GPIOs). ADC channels and bus sensors depend on it.
   (Also seen at P5.3/P5.4: `eeprom@5e`, `mytemp@4f` not representable.)
4. **Board-level nodes.** `gpio-leds`, `gpio-keys`, `pwm-leds`, setting aliases/chosen. Not yet
   configurable (the board view only shows them).
5. **Translations.** `atmel_sam0_to_mchp_g1.yaml` covers SERCOM UART/I2C/SPI, GPIO, pinctrl, clocks
   only. Missing TC, TCC, ADC, DAC, EIC, GMAC, RTC, WDT, DMAC, TRNG. Suggest a P5.0-style research
   card first. CAN and USB have no node in `pic32cx_sg.dtsi` on main → the port report must list
   them as untranslatable.
6. **Check rules.** Duplicate I2C addresses, ADC/PWM channel clashes, a pin in the wrong function
   (analog vs digital), a CS GPIO also used elsewhere.
7. **Acceptance.** The RTOS4 labs use almost only SERCOM and GPIO. Suggested: Zephyr samples
   `basic/blinky_pwm`, `drivers/adc/adc_dt` and one I2C sensor sample, built on SAME54 and ported
   to SG41.
8. **Backlog entry "Draft tab Apply to app"** (commit 683e37d) belongs with these cards; depends on
   `core/apply.py` from P5.6.

## Addendum 2026-10-15 (user request via dt-explorer-c7)

Plan these into phase 8 instead of leaving them in §10:

9. Draft tab "Apply to app" (backlog entry from 683e37d).
10. Generated device module `dtx_devices/` (backlog entry and decision from af3631b).
11. Importer: mikroBUS (and Arduino Uno R3) headers, so the SG41 Curiosity Ultra board view shows
    J900/J901 (already traced, CONFIGURATOR.md "Checked with real design files").

Target scenario of the user (phase 8 acceptance should cover it): open a `pic32cx_sg41_cult` blinky,
add a UART, an I2C/SPI sensor or actuator on the mikroBUS socket, and Ethernet (GMAC + 100BASE-TX
PHY on MDIO), then get `prj.conf` lines and `dtx_` modules written into the app. TCP/IP and TLS stay
in menuconfig.

Decide in phase 8: GMAC + PHY child as its own recipe (RMII pin roles, `phy-handle`/mdio child) or
generic form. The user prefers it usable without hand-picking every RMII pin.

The Kconfig view (7f94422 entry) may stay in the backlog.

## Found at P6.2 (builder)

12. Connector-aware remap when porting between different boards: map each used pad through the
    connector pin (report section 7) to the target pad, pick an instance that can mux those pads for
    the same roles, and emit that instead of the source instance. P6.3 only suggests it.

## Addendum 2026-10-15 (user request via dt-explorer-c7): "Changes" tab, plan into phase 8

13. Read-only git view of the current build's app folder, shown together with "Apply to app" and
    `dtx_devices/` (it shows what those writes did):
    - content is exactly git's view: `git status --porcelain` (modified, added, deleted, untracked,
      .gitignore respected); on click `git diff` against HEAD (untracked: whole file as added) in
      the existing source viewer with changed lines highlighted;
    - files the tool wrote (overlay, .conf, CMakeLists line, `dtx_devices/`) get a "dtx" badge;
      the list itself stays git's;
    - tab count = number of changed files (like the Draft tab);
    - read-only: no commit, revert or checkout from the tool;
    - git state read when the tab opens and on a "Refresh" button only (no polling); show the time
      of the last read and branch / HEAD short hash;
    - app not in a git repo or git not on PATH: the tab says so, nothing else breaks;
    - git calls in core (e.g. `core/git_status.py`, no NiceGUI), UI in its own module.

## Addendum 2026-10-15 (user request via dt-explorer-c7): "Log" tab + log file, one of the FIRST phase 8 cards

14. Logging of what the tool does, sees and decides, readable by a Claude Code session:
    - Python `logging` in core (no NiceGUI); the UI only attaches a handler for the tab; works
      with `--no-browser`;
    - log file `%USERPROFILE%\.dt_explorer\logs\dtx_<date>.log`, rotating (last few runs); the tab
      shows the same content live with "Copy", level filter and a ring buffer (~5000 lines);
    - context header at the start of each run and of every copied block: tool commit, Python and
      NiceGUI version, Zephyr version and workspace, build folder, board, SoC, ATDF file and
      version, loaded plugins;
    - content: loading (files, counts, timings); decisions with reasons, especially `state()` per
      node; external calls (west, nm, cmake, git: command, exit code, duration, stderr tail);
      every write (file, new vs diff, confirmed or not); errors with full traceback;
    - levels: INFO default, DEBUG via a switch in the tab or `--debug`;
    - docs note: the log contains local paths including the user name.

## Addendum 2026-10-15 (user request via dt-explorer-c7): UI tests, early phase 8 card (right after the Log tab)

15. Every later phase 8 card covers its new UI with a test:
    - simulated UI tests with `nicegui.testing` (User fixture, NiceGUI 3.17): pytest, no browser;
      deep links (e.g. `/?build=_soltest/apps/lab1_leer/build_same54_xpro&node=/soc/sercom@40003400`),
      Configure… → I2C → pads → Save → Draft tab count and snippet; Edit pins; Port to board…
      (report sections, no Apply); Pins/Package/Board tabs; copies under %TEMP% for anything that saves;
    - a few real-browser smoke tests with Playwright (Configure → Check → Verify, Port to board,
      board view), each saving a screenshot; skip without Chrome/Playwright or the DTX_* workspaces;
    - exploratory testing (Claude drives the tool, judges screenshots + Log tab) stays manual.
