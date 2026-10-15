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
