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
