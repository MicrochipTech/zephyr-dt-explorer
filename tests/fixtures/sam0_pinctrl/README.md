# sam0 pinctrl fixtures

Used by `tests/test_atmel_sam0_formatter.py` so the tests run without a west workspace.

| Fixture | Source |
|---|---|
| `same54p-pinctrl.h` | trimmed (only the pins used by the tests: PA9, PA12, PB24-26, PB30, PC18, PA14, PD8/9, all SERCOM4/SERCOM7 entries) copy of `C:\Masters\26020_RTOS4\zephyrproject\modules\hal\atmel\include\dt-bindings\pinctrl\same54p-pinctrl.h`, hal_atmel revision `065e57c5013051c8b7f2256271349c6942bd9344` |
| `same54_xpro-pinctrl.dtsi` | unmodified copy of `zephyr\boards\atmel\sam0\same54_xpro\same54_xpro-pinctrl.dtsi`, Zephyr v4.3.0 (revision `3568e1b6d5cdd51a6b964a2a1d6d29200fea2056`) |

Both files are Apache-2.0, Copyright the Zephyr Project contributors / Gerson Fernando Budke.
