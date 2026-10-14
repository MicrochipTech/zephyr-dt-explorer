# port-g1 pinctrl fixtures

Used by `tests/test_microchip_port_g1_formatter.py` so the tests run without a west workspace.

| Fixture | Source |
|---|---|
| `atsame54p20a-pinctrl.h` | trimmed copy (all 134 `*_SERCOM*` macros plus PA0_GPIO, PC18F_TCC0_WO2, PA2B_DAC_VOUT0, PA2B_ADC0_AIN0, PA14L_GMAC_GTXCK; 756 macros in the original) of `C:\Masters\26020_RTOS4\zephyrproject\modules\hal\microchip\include\dt-bindings\sam\sam_d5x_e5x\atsame54\atsame54p20a-pinctrl.h`, hal_microchip revision `89754d87258945ceb1cff8ecee0ddd8852396ae8` (checked out HEAD, equal to the pin in the workspace's `zephyr/west.yml`) |
| `sam_e54_xpro-pinctrl.dtsi` | unmodified copy of `zephyr\boards\microchip\sam\sam_e54_xpro\sam_e54_xpro-pinctrl.dtsi`, Zephyr v4.3.0 (revision `3568e1b6d5cdd51a6b964a2a1d6d29200fea2056`) |

Both files are Apache-2.0, Copyright (c) 2025 Microchip Technology Inc.
