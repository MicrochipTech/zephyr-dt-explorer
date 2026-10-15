# P8.3 Research: further peripherals, `atmel,sam0-*` <-> `microchip,*-g1`

Research only (card P8.3). Basis for the translations of card P8.10 and for the recipe cards P8.8 and P8.9. No code is changed. Method and table format follow `docs/research/binding_pairs.md` (P5.0). Every table row is one property of the source binding (own and inherited, including child-binding levels). Evidence, enumerator output and counts: `docs/evidence/P8.3.md`.

Mapping vocabulary as in P5.0: **keep**, **rename**, **transform**, **drop**, **derive** (value computed from another property, the clock tree, the pad or Kconfig), **open** (no target binding exists).

## 0. Recommendations (read first)

"Recipe" and "generic form" are the two ways the configurator can offer a peripheral (CONFIGURATOR.md, "Three independent plugin kinds" and "Without a recipe ..."). A recipe is justified where the peripheral has pins the user must choose or where properties are derived from them. Everything else is served by the generic form, which needs only the binding.

| Peripheral | Old compatible (RTOS4 v4.3.0) | New compatible (main) | On SAM D5x/E5x and pic32cx_sg on main | Recommendation | Reasons |
|---|---|---|---|---|---|
| **TC, PWM** | `atmel,sam0-tc-pwm` | `microchip,tc-g1-pwm` | yes / yes (TC0-TC7 on E5x `_p`, only TC0-TC5 in `pic32cx_sg.dtsi`) | **Own recipe**, one module `timer_pwm` shared with TCC PWM | WO pads from the ATDF (function letter E/F/G depends on the pad); `#pwm-cells` 2 to 3 (consumer `pwms` need a third cell); `max-bit-width` 32 means pairing TCn+TCn+1 and a `client_mclk` clock; prescaler and period limits; GCLK source. |
| **TC, counter** | `atmel,sam0-tc32` | `microchip,tc-g1-counter` | yes / yes | **Generic form** | No pins. Only `prescaler` and, for 32 bit, `max-bit-width`. Translation needs a hook (32-bit pairing). |
| **TCC, PWM** | `atmel,sam0-tcc-pwm` | `microchip,tcc-g1-pwm` | yes / yes (TCC0-TCC4) | **Own recipe** (same module as TC PWM) | WO0..WO5 pads with 4 to 6 candidates each (IOSET), `channels`/`max-bit-width` per instance from the SoC, `#pwm-cells`, prescaler, PWM LED consumers (`pwm-leds`). Already used on both boards. |
| **TCC, counter** | none (no old counter driver) | `microchip,tcc-g1-counter` | yes / yes | **Generic form** | New capability without a source. No pins. |
| **ADC** | `atmel,sam0-adc` | `microchip,adc-g1` | yes / yes (ADC0, ADC1) | **Own recipe**, depends on P8.4 (child nodes) | `channel@N` children with `zephyr,input-positive` = AIN number derived from the pad (ATDF), AIN pad function B, `prescaler` derived from the GCLK rate and **not in the SoC dtsi on main** (required, so a missing value breaks the build), gain/reference enums, the reference pads VREFA/B/C. |
| **DAC** | `atmel,samd5x-dac` | `microchip,dac-g1` | yes / yes (label `dac`, was `dac0`) | **Own recipe, small** (or generic form once children and fixed-pad pinctrl exist) | VOUT0/VOUT1 have exactly one pad each (PA02, PA05), so no pad choice; but `refsel` is required, children are `channel@N` with required `reg` and `rate`, old values need real transforms (`CC1M` to `rate`, `refresh-period` x30, `OSR_n` to `sampling-ratio`), and differential mode becomes a Kconfig symbol. |
| **EIC** | `atmel,sam0-eic` | `microchip,eic-g1-intc` | yes / yes | **No form, hide** (infrastructure) | No user option: all new properties are SoC pin masks. It is on by default on both sides. The EIC only matters as a check (two pads on the same EXTINT line, GPIO interrupt on a pad without a line). |
| **GMAC + MDIO + PHY** | `atmel,sam0-gmac` + `atmel,sam-mdio` (+ wrapper `microchip,sam-ethernet-controller`) | `microchip,gmac-g1-eth` + `microchip,gmac-g1-mdio` + `ethernet-phy` | E54/E53 only: yes (`same54.dtsi`, `same53.dtsi`) / yes (`pic32cx_sg.dtsi`) | **Own recipe, strongly** (as decided in CONFIGURATOR.md) | The 8 RMII signals each have exactly **one** candidate pad in the ATDF (PA12-PA15, PA17-PA19, PC20), so they can be set without asking; MDC/MDIO have 4 pad pairs (IOSET 1-4) and the choice depends on the board (E54 Xplained PC11/PC12, SG41 Curiosity Ultra PC22/PC23); PHY child (`ethernet-phy@N`, address, optional reset GPIO) and `phy-handle`; MAC address as nvmem cell inside the EEPROM node; Kconfig `ETH_MCHP_GMAC_G1`/`MDIO_MCHP_GMAC_G1`/`PHY_GENERIC_MII`. See section 4. |
| **RTC** | `atmel,sam0-rtc` | `microchip,rtc-g1`, `microchip,rtc-g1-counter` | yes / yes | **Generic form** with a compatible selector | No app pins (the tamper pins are not used by Zephyr). `counter-mode` becomes a choice of compatible. The clock source is a translation (`rtc-src`), not a form field. `systimer` has no target. |
| **WDT** | `atmel,sam0-watchdog` | `microchip,wdt-g1` | yes / yes (label `wdog` to `wdt`) | **Generic form** | Only `status` and `run-in-standby` are user-visible; everything required is in the SoC dtsi. |
| **DMAC** | `atmel,sam0-dmac` | `microchip,dmac-g1-dma` | yes / yes | **Generic form** (enable only) | `#dma-cells = <2>` identical, so existing `dmas` carry over. Channel allocation across users is a check rule, not a recipe. |
| **TRNG** | `atmel,sam-trng` | `microchip,trng-g1-entropy` | yes / yes | **Generic form** (enable only) | Only `status` (the new side needs `&trng { status = "okay"; }`) and `run-in-standby-en`. `zephyr,entropy = &trng` is a chosen, not a property. |
| **CAN** | `atmel,sam0-can` (CAN0, CAN1 on E5x) | **none** | **no / no** | **Untranslatable.** No recipe; list in the port report. | No node, no binding, no driver on main. `can_sam0.c` writes `GCLK`/`MCLK` registers directly and cannot be built with the new SoC. The ATDF has the pins (CAN0 PA22-PA25 function I, CAN1 PB12-PB15 function H), so a *board view* could still show them. |
| **USB** | `atmel,sam0-usb` | **none** (`microchip,udphs-g1-udc` is another IP) | **no / no** | **Untranslatable.** No recipe; list in the port report. | Same situation as CAN. The ATDF has DM/DP on PA24/PA25 (function H). On the E54 these pads overlap CAN0 (PA24, PA25), so CAN0 and USB cannot be used together anyway. |

Dependencies between the recommendations: ADC and DAC recipes and the GMAC MAC address need child nodes (P8.4). The PWM recipe needs the consumers (`pwm-leds`, P8.7) to be changed with it, because `#pwm-cells` changes from 2 to 3. The translations of every row marked "yes" except GMAC are plain data plus a clock derive hook; the exceptions needing a real hook are listed in section 2, finding 6.

## 1. Sources

- Old side: Zephyr v4.3.0, `C:\Masters\26020_RTOS4\zephyrproject\zephyr` (`git describe`: v4.3.0). New side: `C:\Masters\zephyr-main\zephyr`, `4.5.0-rc1`, HEAD `bcf21c7f3c7` (author date 2026-10-12) (`git describe`: v4.5.0-rc1-656-gbcf21c7f3c7).
- Bindings: `dts/bindings/**` of both workspaces, enumerated with `enum3.py` (the P5.0 enumerator `enum2.py`, unchanged, plus the child-bindings of **included** files, see finding 1).
- SoC dtsi: old `dts/arm/atmel/samd5x.dtsi`, `same5x.dtsi`; new `dts/arm/microchip/sam/sam_d5x_e5x/common/samd5xe5x*.dtsi`, `same54.dtsi`, `same53.dtsi`; `dts/arm/microchip/pic32c/pic32cx_sg/common/pic32cx_sg.dtsi` (both workspaces).
- Boards: `boards/atmel/sam0/same54_xpro` (old, v4.3.0 and still on main), `boards/microchip/sam/sam_e54_xpro` and `boards/microchip/pic32c/pic32cx_sg41_cult` (main).
- Drivers and Kconfig: `drivers/{adc,dac,counter,pwm,interrupt_controller,ethernet,mdio,rtc,timer,watchdog,dma,entropy,can,usb/udc}/` and their `Kconfig.*`.
- Pins: ATDF files in `hal_microchip` (`...\modules\hal\microchip\packs\`: `ATSAME54P20A.atdf`, `PIC32CX1025SG41128.atdf`, `PIC32CX1025SG61128.atdf`) and the pinctrl headers of `modules/hal/atmel/include/dt-bindings/pinctrl/same54p-pinctrl.h` (old) and `modules/hal/microchip/include/dt-bindings/{sam/sam_d5x_e5x/atsame54,pic32c/pic32cx_sg/pic32cx_sg41}/*-pinctrl.h` (new).

## 2. Findings (read first)

1. **The P5.0 enumerator misses child-bindings that come from an included file.** `enum2.py` reads `child-binding:` only in the binding's own YAML. The ADC channel node (`channel@N`, 12 properties) comes from `adc-controller.yaml`, the DAC channel node from `dac-controller.yaml` (main only) and the GPIO hog node from `gpio-controller.yaml`. `enum3.py` follows the includes. On the P5.0 binding set it gives identical output except two GPIO bindings that gain 6 child properties (`gpio-hog`, `gpios`, `input`, `output-low`, `output-high`, `line-name`) each: `binding_pairs.md` tables 1.5 (`atmel,sam0-gpio`) and the `microchip,port-g1-gpio` target miss these 6 rows (34 should be 40). The mapping would be `keep` for all six. Not changed here (outside the write scope); P5.2 should add them.
2. **The RTOS4 workspace is not a usable target for anything except TCC.** In v4.3.0 `pic32cx_sg.dtsi` has only `gpio`/`pinctrl` nodes, and the new-port `samd5xe5x.dtsi` has only the five `microchip,tcc-g1` nodes (among the peripherals of this card, `PWM_MCHP_G1_TCC` is the only g1 driver in that tree). All other `-g1` bindings, nodes and drivers appear on main. Ports to SG41/SG61 or to `sam_e54_xpro` must use `C:\Masters\zephyr-main`. (The old `atmel` port, with `same54_xpro`, is still present on main.)
3. **`microchip,adc-g1` requires `prescaler`, and the SoC dtsi on main does not set it.** The old `samd5x.dtsi` sets `prescaler = <4>` on both ADCs. On main `sam_e54_xpro.dts` and `pic32cx_sg41_cult.dts` each set `prescaler = <4>` in `&adc0`/`&adc1`. A translation or recipe must write it, derived from the generator rate (ADC maximum 16 MHz; the board comment says 60 MHz / 4 = 15 MHz).
4. **`#pwm-cells` grows from 2 to 3 (channel, period, polarity).** The consumers (`pwm-leds`: `pwms = <&tcc0 2 PWM_MSEC(20)>` on `same54_xpro`, `<&tcc0 2 PWM_MSEC(20) 0>` on `sam_e54_xpro`) must change with the controller. The same applies to application code with its own `pwms`. The translation of the controller alone leaves a build error.
5. **Label and node renames outside the property tables.** Watchdog `wdog` to `wdt`; DAC `dac0` to `dac`; EIC node name `eic@...` to `interrupt-controller@...`; GMAC path `/soc/ethernet@42000800/ethernet` to `/soc/ethernet@42000800`. The new drivers read some nodes through fixed labels: `DT_NODELABEL(rtc)` (`rtc_mchp_g1.c:23`), `DT_NODELABEL(dac)` (`dac_mchp_g1.c:18`), `DT_NODELABEL(clock)` (RTC, WDT, DAC, GMAC), so those labels must not be renamed. `wdt_mchp_g1.c:29` still contains `DT_NODELABEL(wdog)` in a macro that is only tested with `#if defined(...)` (line 421) and never expanded, so it does no harm.
6. **Hooks the translations need (beyond "keep"):** clock derive for every `atmel,assigned-clocks` (as in P5.0), TC 32-bit pairing (`max-bit-width = <32>` on an even TC plus `client_mclk`), `counter-size` to `max-bit-width`, `#pwm-cells` plus consumers, ADC `prescaler`, DAC value transforms and the DT-to-Kconfig step for `differential-mode`, RTC `counter-mode` to compatible choice and `atmel,assigned-clocks` to `rtc-src`, GMAC `mac-eeprom` to an nvmem cell (needs a child inside the EEPROM node).
7. **Lost on the new side (report, do not translate):** RTC as system timer (`systimer`, Kconfig `SAM0_RTC_TIMER` depends on `DT_HAS_ATMEL_SAM0_RTC_ENABLED`; no g1 equivalent), RTC `event-control-msk`, DAC `run-in-standby` per channel, GMAC `max-frame-size`/`max-speed`/`ref-clk-source`, `reg` of the GMAC wrapper node (moves).
8. **The old `atmel` bindings changed between v4.3.0 and main** (relevant when the source build is itself a main build of an old-port board such as `same54_xpro`): `atmel,gmac-common.yaml` lost `mac-eeprom`, `max-frame-size` and `max-speed` (migration-guide-4.4.rst:635-637; Kconfig `ETH_SAM_GMAC_MAC_I2C_EEPROM` is gone too); `atmel,sam0-tc-pwm`/`-tcc-pwm` lost the `type`/`description` of `channels`. The property totals of the same old sources are 3 higher on main (CAN: 2), partly because `base.yaml` gained `ranges`, `dma-coherent`, `dma-ranges` (visible in table 6.4); counts in section 6 use the v4.3.0 sources.
9. **The pic32cx_sg dtsi has TC0-TC5 only**, while the ATDF for PIC32CX1025SG41128 lists TC0-TC7 (with WO pads). `samd5xe5x_p.dtsi` (E54P) has TC0-TC7. TC6/TC7 are therefore not usable on SG41 in the current dtsi although the silicon has them.
10. **Pinctrl header gaps on the old side** (ATDF against `same54p-pinctrl.h`): the old header has no macros for the `ADC0` X/Y (PTC) and VREFA/B/C signals (67 together), `PC7A_EIC_EXTINT9`, `PA16L_GMAC_GCRS` and the two `USB_SOF_1KHZ` pads. The new headers have macros for every ATDF signal that has a PMUX letter (only the RTC IN/OUT signals have the function `RTC`, no macro on either side). Details in section 7.
11. **Pad conflicts that a check must see:** on `pic32cx_sg41_cult` the TC5 PWM pads are PB14/PB15, which are also MDC/MDIO of IOSET 1, so Ethernet has to use PC22/PC23 (IOSET 3); the E54 Xplained uses PC11/PC12 (IOSET 2). CAN0 and USB share PA23-PA25.

## 3. Presence in the SoC dtsi, drivers and Kconfig

Nodes (from `presence_out.txt` in the evidence file; E54P = `atsame54p20a`, SG = `pic32cx_sg.dtsi`). "old port" = `atmel/samd5x.dtsi` + `same5x.dtsi` (v4.3.0 and main), "new port" = `microchip/sam/sam_d5x_e5x/common/*` and `pic32cx_sg`.

| Peripheral | old port node (label, compatible) | new port E5x node | new port SG node | in RTOS4 v4.3.0 new port |
|---|---|---|---|---|
| TC | `tc0 tc2 tc4 tc6`: `atmel,sam0-tc32` | `tc0`-`tc7`: `microchip,tc-g1` | `tc0`-`tc5` | none |
| TCC | `tcc0`-`tcc4`: `atmel,sam0-tcc` | `tcc0`-`tcc4`: `microchip,tcc-g1` | `tcc0`-`tcc4` | `tcc0`-`tcc4` (`samd5xe5x*.dtsi`) |
| ADC | `adc0 adc1`: `atmel,sam0-adc` | `adc0 adc1`: `microchip,adc-g1` | `adc0 adc1` | none |
| DAC | `dac0`: `atmel,samd5x-dac` | `dac`: `microchip,dac-g1` | `dac` | none |
| EIC | `eic`: `atmel,sam0-eic` | `eic`: `microchip,eic-g1-intc` | `eic` | none |
| GMAC | wrapper + `gmac` + `mdio` (`same5x.dtsi`) | `gmac` + `mdio` in `same54.dtsi`, `same53.dtsi` | `gmac` + `mdio` | none |
| RTC | `rtc`: `atmel,sam0-rtc` | `rtc`: `microchip,rtc-g1` | `rtc` | none |
| WDT | `wdog`: `atmel,sam0-watchdog` | `wdt`: `microchip,wdt-g1` | `wdt` | none |
| DMAC | `dmac`: `atmel,sam0-dmac` | `dmac`: `microchip,dmac-g1-dma` | `dmac` | none |
| TRNG | `trng`: `atmel,sam-trng` | `trng`: `microchip,trng-g1-entropy` | `trng` | none |
| CAN | `can0 can1`: `atmel,sam0-can` (`same5x.dtsi`) | **none** | **none** | none |
| USB | `usb0`: `atmel,sam0-usb` | **none** | **none** | none |

Drivers and Kconfig (from `drivers_out.txt`; the symbol is gated by the `DT_HAS_*_ENABLED` shown, so the node's compatible turns the driver on):

| Peripheral | Old driver / symbol | New driver / symbol (main) |
|---|---|---|
| TC counter | `counter_sam0_tc32.c` / `COUNTER_SAM0_TC32` (`ATMEL_SAM0_TC32`) | `counter_mchp_tc_g1.c` / `COUNTER_MCHP_TC_G1` (`MICROCHIP_TC_G1_COUNTER`) |
| TC PWM | `pwm_sam0_tc.c` / `PWM_SAM0_TC` (`ATMEL_SAM0_TC_PWM`) | `pwm_mchp_tc_g1.c` / `PWM_MCHP_TC_G1` (`MICROCHIP_TC_G1_PWM`) |
| TCC PWM | `pwm_sam0_tcc.c` / `PWM_SAM0_TCC` (`ATMEL_SAM0_TCC_PWM`) | `pwm_mchp_tcc_g1.c` / `PWM_MCHP_G1_TCC` (`MICROCHIP_TCC_G1_PWM`; exists in v4.3.0 too) |
| TCC counter | none | `counter_mchp_tcc_g1.c` / `COUNTER_MCHP_G1_TCC` (`MICROCHIP_TCC_G1_COUNTER`) |
| ADC | `adc_sam0.c` / `ADC_SAM0` (`ATMEL_SAM0_ADC`) | `adc_mchp_g1.c` / `ADC_MCHP_G1` (`MICROCHIP_ADC_G1`) |
| DAC | `dac_samd5x.c` / `DAC_SAMD5X` (`ATMEL_SAMD5X_DAC`) | `dac_mchp_g1.c` / `DAC_MCHP_G1` (+ `DAC_MCHP_G1_DIFFERENTIAL`, `_REFRESH`, `_EXTERNAL_FILTER`, `_OVERSAMPLING_RATIO`) |
| EIC | `intc_sam0_eic.c` / `SAM0_EIC` | `intc_mchp_eic_g1.c` / `INTC_MCHP_EIC_G1` |
| GMAC | `eth_sam_gmac.c` / `ETH_SAM_GMAC` (`ATMEL_SAM_GMAC`) | `eth_mchp_gmac_g1.c` / `ETH_MCHP_GMAC_G1` (`MICROCHIP_GMAC_G1_ETH`) |
| MDIO | `drivers/mdio/mdio_sam.c` / `MDIO_ATMEL_SAM` | `drivers/ethernet/mdio/mdio_mchp_gmac_g1.c` / `MDIO_MCHP_GMAC_G1` (default y with the GMAC) |
| PHY | `PHY_GENERIC_MII` (`ETHERNET_PHY`) | `PHY_GENERIC_MII` (`ETHERNET_PHY`) |
| RTC | `rtc_sam0.c` / `RTC_ATMEL_SAM0` | `rtc_mchp_g1.c` / `RTC_MCHP_G1`; counter: `counter_mchp_rtc_g1.c` / `COUNTER_MCHP_RTC_G1` |
| RTC as system timer | `sam0_rtc_timer.c` / `SAM0_RTC_TIMER` | none for the g1 RTC (`mchp_pit_g1_timer.c` and `mchp_sam_pit64b_timer.c` are other IPs) |
| WDT | `wdt_sam0.c` / `WDT_SAM0` | `wdt_mchp_g1.c` / `WDT_MCHP_G1` |
| DMAC | `dma_sam0.c` / `DMA_SAM0` | `dma_mchp_dmac_g1.c` / `DMA_MCHP_DMAC_G1` (`depends on !DMA_64BIT`) |
| TRNG | `entropy_sam.c` / `ENTROPY_SAM_RNG` (`ATMEL_SAM_TRNG`) | `entropy_mchp_trng_g1.c` / `ENTROPY_MCHP_TRNG_G1` |
| CAN | `can_sam0.c` / `CAN_SAM0` (selects `CAN_MCAN`) | **none** (`can_sam0.c` is unchanged and still gated by the old compatible) |
| USB device | `udc_sam0.c` / `UDC_SAM0` | **none** for this IP (`udc_mchp_udphs_g1.c` / `UDC_MCHP_UDPHS_G1` is UDPHS, SAMA5/7) |

Neither `sam_e54_xpro.yaml` nor `pic32cx_sg41_cult.yaml` lists `can` or `usb` under `supported:`; `same54_xpro.yaml` (old port) lists `usb_device` and `usbd`.

## 4. GMAC, MDIO and PHY: how the boards wire them

Measured on the three boards (`boards/**/*.dts`, `*-pinctrl.dtsi`). Pad macros are written `P<port><n><function>_GMAC_<signal>`; the function is always `L`.

| Item | `same54_xpro` (old, v4.3.0) | `sam_e54_xpro` (main) | `pic32cx_sg41_cult` (main) |
|---|---|---|---|
| MAC node | `&gmac { status = "okay"; pinctrl-0 = <&gmac_rmii>; mac-eeprom = <&eeprom>; phy-handle = <&phy>; };` | `&gmac { compatible = "microchip,gmac-g1-eth"; pinctrl-0 = <&gmac_rmii>; nvmem-cells = <&mac_address>; nvmem-cell-names = "mac-address"; phy-handle = <&phy>; status = "okay"; };` | same without the `compatible` line (SoC default is already `gmac-g1-eth`) |
| MDIO node | `&mdio { status = "okay"; pinctrl-0 = <&mdio_default>; phy: ethernet-phy@0 { compatible = "ethernet-phy"; reg = <0>; status = "okay"; }; };` | same plus `compatible = "microchip,gmac-g1-mdio"` | same without `compatible` |
| RMII pads (8) | PA14 GTXCK, PA17 GTXEN, PA18 GTX0, PA19 GTX1, PC20 GRXDV, PA13 GRX0, PA12 GRX1, PA15 GRXER | identical | identical |
| MDC / MDIO pads | PC11 / PC12 (IOSET 2) | PC11 / PC12 | **PC22 / PC23** (IOSET 3) |
| PHY | KSZ8091RNA on the board (`doc/index.rst`), address 0 | same | "RMII interface with modular PHY header" (`doc/index.rst`): the PHY is a plug-in module, address 0 assumed |
| MAC address | I2C EEPROM at 0x5e read by the driver (`mac-eeprom`; `Kconfig.defconfig`: `ETH_SAM_GMAC_MAC_I2C_EEPROM=y`, address 0x9A, `select I2C`) | `eeprom_mac: eeprom@5e` with `nvmem-layout { compatible = "fixed-layout"; mac_address: mac-address@9a { reg = <0x9a 6>; }; }` | same layout (`eeprom_mac: eeprom@5e`) |
| Pinctrl group | one `group1` with 8 (RMII) or 2 (MDIO) `pinmux` entries | identical groups | identical RMII group, MDIO pair differs |

What follows for a recipe (section 7 has the ATDF data):
- The RMII set is **fixed**: every RMII signal has one candidate pad (`GTXCK` PA14, `GTXEN` PA17, `GTX0` PA18, `GTX1` PA19, `GRXDV` PC20, `GRX0` PA13, `GRX1` PA12, `GRXER` PA15), so the recipe can write all eight without asking. MII adds `GTX2` PC16, `GTX3` PC17, `GRX2` PC15, `GRX3` PC14, `GRXCK` PC18, `GTXER` PC19, `GCOL` PC21, `GCRS` PA16 (also one pad each).
- MDC and MDIO come as four pairs (IOSET 1 PB14/PB15, 2 PC11/PC12, 3 PC22/PC23, 4 PA20/PA21). This is the only choice the user has; the check (finding 11) tells which pairs are free.
- The PHY is not a driver of its own: `ethernet-phy` plus `PHY_GENERIC_MII` on both ports; the form needs the address `reg` and, on main, optional `reset-gpios`/`no-reset`.
- The old property `phy-connection-type` defaults to "rmii" on both sides; the new driver accepts "mii" and "rmii".
- Kconfig derived from the compatible chain: `ETH_MCHP_GMAC_G1` (via `DT_HAS_MICROCHIP_GMAC_G1_ETH_ENABLED`), `MDIO_MCHP_GMAC_G1` (default y), `PHY_GENERIC_MII` (default y), `NVMEM` (implied when `nvmem-cells` is present), `NET_BUF_FIXED_DATA_SIZE` as dependency. TCP/IP and TLS stay in menuconfig (CONFIGURATOR.md decision).

## 5. ADC channel child, old and new

Both bindings take the child node from `adc-controller.yaml` (12 properties, table 6.5). Real usage:

| | old (`samples/drivers/adc/adc_dt/boards/same54_xpro.overlay`, v4.3.0) | main (`samples/drivers/adc/adc_sequence/boards/sam_e54_xpro.overlay`) |
|---|---|---|
| ADC node | `&adc1 { #address-cells = <1>; #size-cells = <0>; channel@6 { ... }; };` | `&adc0 { #address-cells = <1>; #size-cells = <0>; prescaler = <16>; status = "okay"; channel@0 { ... }; };` |
| channel | `reg = <6>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_INTERNAL"; zephyr,acquisition-time = <ADC_ACQ_TIME_DEFAULT>; zephyr,resolution = <12>; zephyr,input-positive = <6>;` | `reg = <0>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_VDD_1"; zephyr,acquisition-time = <ADC_ACQ_TIME_DEFAULT>; zephyr,input-positive = <MCHP_ADC_AIN12>; zephyr,resolution = <12>; zephyr,vref-mv = <3300>;` |
| consumer | `zephyr,user { io-channels = <&adc1 6>; };` | alias `adc0 = &adc0;` |

The child properties are identical; the new header gives the AIN names. The pad-to-AIN link is the derived value: ATDF `ADC0 AIN12` is PB00 function B on ADC0 (`PB0B_ADC0_AIN12`); `reg` equals `zephyr,input-positive` by convention in all samples but is not required to.

## 6. Property tables per pair

Each table lists **every** property of the source binding in the order the enumerator printed it (`docs/evidence/P8.3.md`, sections C and D). "inh" marks an inherited property and its origin; `child:` marks a property of the child-binding level. Target columns give `binding: property` with type and required from the target YAML; "—" means no counterpart. After each table, the target properties without a source row are listed ("target-only"), so that every property of the target binding appears once. Variants (other compatibles of the same hardware) are compared against the primary target by name, type and required flag.

### 6.1 TC counter: atmel,sam0-tc32 -> microchip,tc-g1-counter

Source: `counter/atmel,sam0-tc32.yaml` (compatible `atmel,sam0-tc32`). Target: `counter/microchip,tc-g1-counter.yaml` (compatible `microchip,tc-g1-counter`).

Old SoC node compatible is `atmel,sam0-tc32` (counter only). New SoC node compatible is `microchip,tc-g1`; the board overrides it with `microchip,tc-g1-counter` or `microchip,tc-g1-pwm`. `tc-g1` and `tc-g1-counter` declare the same properties.

Source properties: **37** (own + inherited, incl. child-binding levels). Table rows: **37**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `tc-g1-counter: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `tc-g1-counter: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `tc-g1-counter: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `tc-g1-counter: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `tc-g1-counter: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `tc-g1-counter: reg` | - | yes | keep | Same base on TC0 (0x40003800). Size 0x34 (samd5x.dtsi) vs 0x400 (pic32cx_sg.dtsi). A SoC value. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `tc-g1-counter: interrupts` | - | yes | keep | Same line (107 for TC0) on both sides. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `tc-g1-counter: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `tc-g1-counter: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `tc-g1-counter: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `tc-g1-counter: clocks` | - | yes | transform | Old: `<&gclk 9>, <&mclk 0x14 14>` (TC0). New: `<&mclkperiph APBA_TC0>, <&gclkperiph TC0>` and, on the even instances that can pair into 32 bit, a third entry `<&mclkperiph APBA_TC1>` named `client_mclk`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `tc-g1-counter: clock-names` | string-array | no | transform | Values change: old `GCLK, MCLK`, new `mclk, gclk, client_mclk (optional)`. Both drivers look the clocks up by name. |
| `#address-cells (inh: base/base.yaml)` | int | no | `tc-g1-counter: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `tc-g1-counter: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `tc-g1-counter: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `tc-g1-counter: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `tc-g1-counter: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `tc-g1-counter: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `tc-g1-counter: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `tc-g1-counter: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `tc-g1-counter: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `tc-g1-counter: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-counter: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-counter: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-counter: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-counter: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-counter: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `tc-g1-counter: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: GCLK generator that feeds the peripheral (`<&gclk N>`). New: `gclkperiph-src = "gclkN"` on the `tcN` child of `&clock { gclkperiph { ... } }` (subsystem `CLOCK_MCHP_GCLKPERIPH_ID_TCn`); the generator must exist in `gclkgen`. Same derive rule as the SERCOM clocks in binding_pairs.md. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock; the new clock tree has no counterpart. |
| `prescaler` | int | no | `tc-g1-counter: prescaler` | int | no | keep | Same enum (1, 2, 4, 8, 16, 64, 256, 1024). Old SoC leaves it unset, new SoC sets 1 (pic32cx_sg41_cult sets 8). Required on neither. |

Target `microchip,tc-g1-counter.yaml`: **40** properties; referenced by source rows: **35**; target-only rows below: **5**.

| target property | type | required | note |
|---|---|---|---|
| `tc-g1-counter: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tc-g1-counter: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tc-g1-counter: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tc-g1-counter: channels (inh: counter/microchip,tc-g1.yaml)` | int | yes | Required; SoC value 2. Not an option for the user. |
| `tc-g1-counter: max-bit-width (inh: counter/microchip,tc-g1.yaml)` | int | yes | Required; SoC value 16. The old `tc32` is the 32-bit mode, so the translation writes `max-bit-width = <32>` on the even instance (TC0, TC2, TC4, TC6); the odd partner (TC1, ...) is then used up. Enum 8, 16, 32. |

Variant `microchip,tc-g1.yaml` (compatible `microchip,tc-g1`, **40** properties) against `microchip,tc-g1-counter.yaml` (**40**): equal in name, type and required flag: **40**; only in the variant: none; only in the primary target: none; same name, different type or required: none.

### 6.2 TC PWM: atmel,sam0-tc-pwm -> microchip,tc-g1-pwm

Source: `pwm/atmel,sam0-tc-pwm.yaml` (compatible `atmel,sam0-tc-pwm`). Target: `pwm/microchip,tc-g1-pwm.yaml` (compatible `microchip,tc-g1-pwm`).

Old: the board overrides the SoC compatible `atmel,sam0-tc32` with `atmel,sam0-tc-pwm` and adds `channels`/`counter-size`/`prescaler`. New: the board overrides `microchip,tc-g1` with `microchip,tc-g1-pwm`; `channels` and `max-bit-width` come from the SoC node.

Source properties: **40** (own + inherited, incl. child-binding levels). Table rows: **40**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `tc-g1-pwm: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `tc-g1-pwm: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `tc-g1-pwm: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `tc-g1-pwm: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `tc-g1-pwm: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `tc-g1-pwm: reg` | - | yes | keep | Same base; size differs in the SoC dtsi. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `tc-g1-pwm: interrupts` | - | yes | keep | Same line on both sides. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `tc-g1-pwm: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `tc-g1-pwm: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `tc-g1-pwm: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `tc-g1-pwm: clocks` | - | yes | transform | Old: `<&gclk N>, <&mclk OFF BIT>`. New: `<&mclkperiph APBx_TCn>, <&gclkperiph TCn>`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `tc-g1-pwm: clock-names` | string-array | no | transform | Values change: old `GCLK, MCLK`, new `mclk, gclk`. Both drivers look the clocks up by name. |
| `#address-cells (inh: base/base.yaml)` | int | no | `tc-g1-pwm: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `tc-g1-pwm: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `tc-g1-pwm: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `tc-g1-pwm: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `tc-g1-pwm: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `tc-g1-pwm: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `tc-g1-pwm: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `tc-g1-pwm: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `tc-g1-pwm: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `tc-g1-pwm: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#pwm-cells` | - | no | `tc-g1-pwm: #pwm-cells` | - | no | transform | Old binding: `const: 2` (channel, period). New: `const: 3` (channel, period, polarity). Every `pwms = <&tcc0 CH PERIOD>` in the application (pwm-leds, samples) needs a third cell (flags, 0 = normal). That lies outside the controller node, so the translation must report it. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-pwm: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-pwm: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-pwm: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-pwm: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tc-g1-pwm: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `tc-g1-pwm: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: GCLK generator that feeds the peripheral (`<&gclk N>`). New: `gclkperiph-src = "gclkN"` on the `tcN` child of `&clock { gclkperiph { ... } }` (subsystem `CLOCK_MCHP_GCLKPERIPH_ID_TCn`); the generator must exist in `gclkgen`. Same derive rule as the SERCOM clocks in binding_pairs.md. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock; the new clock tree has no counterpart. |
| `channels` | int | yes | `tc-g1-pwm: channels` | int | yes | keep | Required on both. Old enum {2}; new has no enum. SoC value 2 on both sides, so the user does not set it. |
| `counter-size` | int | yes | `tc-g1-pwm: max-bit-width` | int | yes | rename | Same meaning (counter width in bits), same enum 8, 16, 32, required on both. Old value is set by the board; new value (16) by the SoC, 32 on a paired even instance. |
| `prescaler` | int | yes | `tc-g1-pwm: prescaler` | int | no | keep | Same enum. Required on the old side, optional on the new side (SoC sets 1). |

Target `microchip,tc-g1-pwm.yaml`: **41** properties; referenced by source rows: **38**; target-only rows below: **3**.

| target property | type | required | note |
|---|---|---|---|
| `tc-g1-pwm: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tc-g1-pwm: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tc-g1-pwm: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |

### 6.3 TCC PWM: atmel,sam0-tcc-pwm -> microchip,tcc-g1-pwm

Source: `pwm/atmel,sam0-tcc-pwm.yaml` (compatible `atmel,sam0-tcc-pwm`). Target: `pwm/microchip,tcc-g1-pwm.yaml` (compatible `microchip,tcc-g1-pwm`).

Old SoC node compatible `atmel,sam0-tcc` (no binding file; the board must override it with `atmel,sam0-tcc-pwm`). New SoC node `microchip,tcc-g1`; board override `microchip,tcc-g1-pwm` (PWM) or `microchip,tcc-g1-counter` (counter). The old SoC already carries `channels`/`counter-size`.

Source properties: **40** (own + inherited, incl. child-binding levels). Table rows: **40**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `tcc-g1-pwm: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `tcc-g1-pwm: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `tcc-g1-pwm: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `tcc-g1-pwm: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `tcc-g1-pwm: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `tcc-g1-pwm: reg` | - | yes | keep | Same base and size (TCC0 0x41016000, 0x2000). |
| `reg-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `tcc-g1-pwm: interrupts` | - | yes | keep | Same lines (TCC0: 85 to 91). |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `tcc-g1-pwm: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `tcc-g1-pwm: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `tcc-g1-pwm: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `tcc-g1-pwm: clocks` | - | yes | transform | Old: `<&gclk 25>, <&mclk 0x18 11>` (TCC0). New: `<&mclkperiph APBB_TCC0>, <&gclkperiph TCC0>`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `tcc-g1-pwm: clock-names` | string-array | no | transform | Values change: old `GCLK, MCLK`, new `mclk, gclk`. Both drivers look the clocks up by name. |
| `#address-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `tcc-g1-pwm: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#pwm-cells` | - | no | `tcc-g1-pwm: #pwm-cells` | - | no | transform | Old binding: `const: 2` (channel, period). New: `const: 3` (channel, period, polarity). Every `pwms = <&tcc0 CH PERIOD>` in the application (pwm-leds, samples) needs a third cell (flags, 0 = normal). That lies outside the controller node, so the translation must report it. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `tcc-g1-pwm: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: GCLK generator that feeds the peripheral (`<&gclk N>`). New: `gclkperiph-src = "gclkN"` on the `tccN` child of `&clock { gclkperiph { ... } }` (subsystem `CLOCK_MCHP_GCLKPERIPH_ID_TCCn`); the generator must exist in `gclkgen`. Same derive rule as the SERCOM clocks in binding_pairs.md. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock; the new clock tree has no counterpart. |
| `channels` | int | yes | `tcc-g1-pwm: channels` | int | yes | keep | Required on both. Old enum {2, 3, 4, 6}. SoC values on both sides: TCC0 6, TCC1 4, TCC2 3, TCC3 2, TCC4 2. |
| `counter-size` | int | yes | `tcc-g1-pwm: max-bit-width` | int | yes | rename | Same meaning and same enum {16, 24}; required on both. SoC value: TCC0/TCC1 24, TCC2 to TCC4 16. |
| `prescaler` | int | yes | `tcc-g1-pwm: prescaler` | int | no | keep | Same enum. Required on the old side, optional on the new side (driver default 1 for the counter variant). The boards set 8 in both cases. |

Target `microchip,tcc-g1-pwm.yaml`: **41** properties; referenced by source rows: **38**; target-only rows below: **3**.

| target property | type | required | note |
|---|---|---|---|
| `tcc-g1-pwm: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tcc-g1-pwm: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tcc-g1-pwm: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |

Variant `microchip,tcc-g1.yaml` (compatible `microchip,tcc-g1`, **40** properties) against `microchip,tcc-g1-pwm.yaml` (**41**): equal in name, type and required flag: **40**; only in the variant: none; only in the primary target: ['#pwm-cells']; same name, different type or required: none.

Variant `microchip,tcc-g1-counter.yaml` (compatible `microchip,tcc-g1-counter`, **40** properties) against `microchip,tcc-g1-pwm.yaml` (**41**): equal in name, type and required flag: **40**; only in the variant: none; only in the primary target: ['#pwm-cells']; same name, different type or required: none.

### 6.4 TCC PWM drift inside the RTOS4 workspace: microchip,tcc-g1-pwm (v4.3.0) -> microchip,tcc-g1-pwm (main)

Source: `pwm/microchip,tcc-g1-pwm.yaml` (compatible `microchip,tcc-g1-pwm`). Target: `pwm/microchip,tcc-g1-pwm.yaml` (compatible `microchip,tcc-g1-pwm`).

The RTOS4 workspace (v4.3.0) already contains `microchip,tcc-g1` and `microchip,tcc-g1-pwm` (an older revision, with `PWM_MCHP_G1_TCC` in `drivers/pwm/Kconfig.mchp`). This table shows what changed up to main: the YAML files of `tcc-g1-pwm` are byte-identical in both workspaces, `tcc-g1` differs only in its description (one more `module` line), and the three extra target properties (`ranges`, `dma-coherent`, `dma-ranges`) come from `base.yaml`, which grew between the two Zephyr versions.

Source properties: **38** (own + inherited, incl. child-binding levels). Table rows: **38**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `tcc-g1-pwm: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `tcc-g1-pwm: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `tcc-g1-pwm: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `tcc-g1-pwm: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `tcc-g1-pwm: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg (inh: counter/microchip,tcc-g1.yaml)` | - | yes | `tcc-g1-pwm: reg` | - | yes | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: counter/microchip,tcc-g1.yaml)` | - | yes | `tcc-g1-pwm: interrupts` | - | yes | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `tcc-g1-pwm: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `tcc-g1-pwm: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `tcc-g1-pwm: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: counter/microchip,tcc-g1.yaml)` | - | yes | `tcc-g1-pwm: clocks` | - | yes | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `tcc-g1-pwm: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `tcc-g1-pwm: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `tcc-g1-pwm: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `tcc-g1-pwm: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#pwm-cells` | - | no | `tcc-g1-pwm: #pwm-cells` | - | no | keep | Same name on the target. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `tcc-g1-pwm: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `tcc-g1-pwm: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `prescaler (inh: counter/microchip,tcc-g1.yaml)` | int | no | `tcc-g1-pwm: prescaler` | int | no | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |
| `channels (inh: counter/microchip,tcc-g1.yaml)` | int | yes | `tcc-g1-pwm: channels` | int | yes | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |
| `max-bit-width (inh: counter/microchip,tcc-g1.yaml)` | int | yes | `tcc-g1-pwm: max-bit-width` | int | yes | keep | Inherited from counter/microchip,tcc-g1.yaml; same name and type exist on the target side. |

Target `microchip,tcc-g1-pwm.yaml`: **41** properties; referenced by source rows: **38**; target-only rows below: **3**.

| target property | type | required | note |
|---|---|---|---|
| `tcc-g1-pwm: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tcc-g1-pwm: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `tcc-g1-pwm: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |

### 6.5 ADC: atmel,sam0-adc -> microchip,adc-g1

Source: `adc/atmel,sam0-adc.yaml` (compatible `atmel,sam0-adc`). Target: `adc/microchip,adc-g1.yaml` (compatible `microchip,adc-g1`).

Both bindings include `adc-controller.yaml`, whose child-binding is the `channel@N` node (12 properties). The P5.0 enumerator did not read child-bindings of included files; `enum3.py` does (see evidence).

Source properties: **51** (own + inherited, incl. child-binding levels). Table rows: **51**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `adc-g1: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `adc-g1: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `adc-g1: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `adc-g1: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `adc-g1: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `adc-g1: reg` | - | yes | keep | Same base and size (ADC0 0x43001c00, 0x4a). |
| `reg-names (inh: base/base.yaml)` | string-array | no | `adc-g1: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `adc-g1: interrupts` | - | yes | keep | Same lines (118, 119 for ADC0) and same `interrupt-names` "overrun", "resrdy". |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `adc-g1: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `adc-g1: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `adc-g1: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `adc-g1: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `adc-g1: clocks` | - | yes | transform | Old: `<&gclk 40>, <&mclk 0x20 7>` (ADC0). New: `<&mclkperiph APBD_ADC0>, <&gclkperiph ADC0>`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `adc-g1: clock-names` | - | yes | transform | Values change: old `GCLK, MCLK`, new `mclk, gclk`. Both drivers look the clocks up by name. |
| `#address-cells (inh: adc/adc-controller.yaml)` | - | no | `adc-g1: #address-cells` | - | no | keep | Inherited from adc/adc-controller.yaml; same name and type exist on the target side. |
| `#size-cells (inh: adc/adc-controller.yaml)` | - | no | `adc-g1: #size-cells` | - | no | keep | Inherited from adc/adc-controller.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `adc-g1: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `adc-g1: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `adc-g1: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `adc-g1: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `adc-g1: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `adc-g1: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `adc-g1: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `adc-g1: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `adc-g1: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `adc-g1: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `adc-g1: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `adc-g1: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#io-channel-cells` | - | no | `adc-g1: #io-channel-cells` | - | no | keep | `const: 1` on both. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `adc-g1: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `adc-g1: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `adc-g1: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `adc-g1: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `adc-g1: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `adc-g1: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: GCLK generator that feeds the peripheral (`<&gclk N>`). New: `gclkperiph-src = "gclkN"` on the `adcN` child of `&clock { gclkperiph { ... } }` (subsystem `CLOCK_MCHP_GCLKPERIPH_ID_ADCn`); the generator must exist in `gclkgen`. Same derive rule as the SERCOM clocks in binding_pairs.md. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock; the new clock tree has no counterpart. |
| `prescaler` | int | yes | `adc-g1: prescaler` | int | yes | derive | Required on both. Old: set in the SoC dtsi (4, giving 12 MHz from the 48 MHz generator 2). New: **not set in the SoC dtsi**, so every board must set it, enum {2, 4, 8, 16, 32, 64, 128, 256}. The value follows from the GCLK rate (ADC maximum 16 MHz): sam_e54_xpro and pic32cx_sg41_cult use 4 at 60 MHz GCLK1 (15 MHz). |
| `calib-offset` | int | no | `adc-g1: calib-mapping` | array | yes | derive | Old: bit offset of the BIASCOMP field in the NVM calibration row (ADC0 0, ADC1 14). New: `nvm-calib` plus `calib-mapping` (three entries) are SoC properties. The translation drops the old value; the target SoC dtsi already carries the mapping. |
| `child: reg` | array | yes | `adc-g1: child: reg` | array | yes | keep | Channel number. Identical on both sides (child-binding of `adc-controller.yaml`). The node is `channel@N` with `reg = <N>` and `#address-cells = <1>; #size-cells = <0>` on the ADC node. |
| `child: zephyr,gain` | string | yes | `adc-g1: child: zephyr,gain` | string | yes | keep | Required on both. Old driver (adc_sam0.c:183-209) accepts ADC_GAIN_1, 1_2, 2, 4, 8, 16; the new driver (adc_mchp_g1.c:40-43) has its own gain correction table (1/2, 2/3, 4/5, 1). Check the used gain against the new driver. |
| `child: zephyr,reference` | string | yes | `adc-g1: child: zephyr,reference` | string | yes | keep | Required on both. Old driver: INTERNAL, VDD_1, VDD_1_2, EXTERNAL0, EXTERNAL1. New driver adds VDD_1_3 and VDD_1_4 (adc_mchp_g1.c:257-258). Every old value stays valid. |
| `child: zephyr,vref-mv` | int | no | `adc-g1: child: zephyr,vref-mv` | int | no | keep | Same. |
| `child: zephyr,acquisition-time` | int | yes | `adc-g1: child: zephyr,acquisition-time` | int | yes | keep | Required on both. |
| `child: zephyr,differential` | boolean | no | `adc-g1: child: zephyr,differential` | boolean | no | keep | Same. |
| `child: zephyr,input-positive` | int | no | `adc-g1: child: zephyr,input-positive` | int | no | keep | Same number space (ADC INPUTCTRL.MUXPOS): 0 to 15 are AIN0 to AIN15. The new header `dt-bindings/adc/mchp_sam_d5x_e5x_adc.h` gives names (`MCHP_ADC_AIN12`, `MCHP_ADC_PTAT`, ...). The AIN number follows from the pad via the ATDF (derived value for a recipe, section 7). |
| `child: zephyr,input-negative` | int | no | `adc-g1: child: zephyr,input-negative` | int | no | keep | Same. Valid values on the new side: `MCHP_ADC_MUXNEG_VALID_MASK` (AIN0 to AIN7 and the ground input 0x18). |
| `child: zephyr,resolution` | int | no | `adc-g1: child: zephyr,resolution` | int | no | keep | Same. The new driver accepts 8, 10, 12 bit and, with oversampling, 16 bit (adc_set_resolution). |
| `child: zephyr,oversampling` | int | no | `adc-g1: child: zephyr,oversampling` | int | no | keep | Same; both drivers check the maximum. |
| `child: zephyr,current-source-pin` | uint8-array | no | `adc-g1: child: zephyr,current-source-pin` | uint8-array | no | keep | Same declaration; neither driver reads it. |
| `child: zephyr,vbias-pins` | int | no | `adc-g1: child: zephyr,vbias-pins` | int | no | keep | Same declaration; neither driver reads it. |

Target `microchip,adc-g1.yaml`: **55** properties; referenced by source rows: **49**; target-only rows below: **6**.

| target property | type | required | note |
|---|---|---|---|
| `adc-g1: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `adc-g1: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `adc-g1: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `adc-g1: num-channels` | int | yes | Required; SoC value 23. Not user-set. |
| `adc-g1: nvm-calib` | phandle | yes | Required; SoC phandle to `nvm_sw_calib_area`. |
| `adc-g1: calib-mapping-names` | string-array | no | Optional; SoC value. |

### 6.6 DAC: atmel,samd5x-dac -> microchip,dac-g1

Source: `dac/atmel,samd5x-dac.yaml` (compatible `atmel,samd5x-dac`). Target: `dac/microchip,dac-g1.yaml` (compatible `microchip,dac-g1`).

`atmel,sam0-dac` belongs to SAM D2x/C2x and is not used by `samd5x.dtsi`; the SAM D5x/E5x node (`dac0`) uses `atmel,samd5x-dac`. The old child nodes are named `channel_0`, `channel_1` and have **no `reg`**; the new ones are `channel@0` with `reg` and a required `rate`. The old label is `dac0`, the new one `dac`.

Source properties: **43** (own + inherited, incl. child-binding levels). Table rows: **43**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `dac-g1: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `dac-g1: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `dac-g1: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `dac-g1: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `dac-g1: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `dac-g1: reg` | - | yes | keep | Same base (0x43002400). Size 0x20 vs 0x1e. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `dac-g1: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: base/base.yaml)` | array | no | `dac-g1: interrupts` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `dac-g1: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `dac-g1: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `dac-g1: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `dac-g1: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `dac-g1: clocks` | - | yes | transform | Old: `<&gclk 42>, <&mclk 0x20 9>`. New: `<&mclkperiph APBD_DAC>, <&gclkperiph DAC>`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `dac-g1: clock-names` | - | yes | transform | Values change: old `GCLK, MCLK`, new `mclk, gclk`. Both drivers look the clocks up by name. |
| `#address-cells (inh: base/base.yaml)` | int | no | `dac-g1: #address-cells` | - | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `dac-g1: #size-cells` | - | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `dac-g1: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `dac-g1: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `dac-g1: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `dac-g1: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `dac-g1: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `dac-g1: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `dac-g1: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `dac-g1: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `dac-g1: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `dac-g1: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `dac-g1: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `dac-g1: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#io-channel-cells` | - | no | `dac-g1: #io-channel-cells` | int | yes | keep | `const: 1` on both; the new side also has `type: int` and required. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `dac-g1: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `dac-g1: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `dac-g1: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `dac-g1: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `dac-g1: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `dac-g1: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: GCLK generator that feeds the peripheral (`<&gclk N>`). New: `gclkperiph-src = "gclkN"` on the `dac` child of `&clock { gclkperiph { ... } }` (subsystem `CLOCK_MCHP_GCLKPERIPH_ID_DAC`); the generator must exist in `gclkgen`. Same derive rule as the SERCOM clocks in binding_pairs.md. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock; the new clock tree has no counterpart. |
| `reference` | string | no | `dac-g1: refsel` | string | yes | rename | Optional on the old side, **required** on the new side. Enum values change: `vrefau` -> `vref_au`, `vddana` -> `vdd_ana`, `vrefab` -> `vref_ab`, `intref` -> `int_ref`. |
| `differential-mode` | boolean | no | — | — | — | derive | No devicetree property on the new side. Differential mode is the Kconfig symbol `DAC_MCHP_G1_DIFFERENTIAL` (drivers/dac/Kconfig.mchp:14). A DT-to-Kconfig translation. |
| `child: oversampling` | string | no | `dac-g1: child: sampling-ratio` | int | no | transform | Old: string `OSR_1` ... `OSR_32`. New: int 1, 2, 4, 8, 16, 32, default 1. Kconfig `DAC_MCHP_G1_OVERSAMPLING_RATIO` must be on. |
| `child: refresh-period` | int | no | `dac-g1: child: refresh-period` | int | no | transform | Old: int 0 to 15, unit 30 us. New: int in microseconds, enum 0, 30, ..., 300. New value = old value x 30. Kconfig `DAC_MCHP_G1_REFRESH` (default y). |
| `child: run-in-standby` | boolean | no | — | — | — | drop | The new driver has no run-in-standby handling for the DAC. Report as lost. |
| `child: current-control` | string | no | `dac-g1: child: rate` | int | yes | transform | Old: `CC100K`, `CC1M`, `CC12M`. New: conversion speed in kS/s, **required**, enum 100, 500, 1000. Mapping CC100K -> 100, CC1M -> 500, CC12M -> 1000 follows the old description (GCLK_DAC <= 1.2 MHz, <= 6 MHz, <= 12 MHz). |

Target `microchip,dac-g1.yaml`: **51** properties; referenced by source rows: **39**; target-only rows below: **12**.

| target property | type | required | note |
|---|---|---|---|
| `dac-g1: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dac-g1: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dac-g1: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dac-g1: max-channels` | int | no | Default 2. Read by the driver through `DT_NODELABEL(dac)` (dac_mchp_g1.c:18), so the label `dac` must stay. |
| `dac-g1: child: reg` | - | yes | Required; channel number 0 or 1 (old: implied by the order of the child nodes). |
| `dac-g1: child: zephyr,resolution` | int | no | Target-only (no source counterpart). |
| `dac-g1: child: zephyr,buffered` | boolean | no | Target-only (no source counterpart). |
| `dac-g1: child: zephyr,internal` | boolean | no | Target-only (no source counterpart). |
| `dac-g1: child: zephyr,vref-mv` | int | no | Target-only (no source counterpart). |
| `dac-g1: child: ext-filter` | boolean | no | Optional boolean; Kconfig `DAC_MCHP_G1_EXTERNAL_FILTER`. |
| `dac-g1: child: data-adj` | string | no | Optional string "right"/"left". |
| `dac-g1: child: dither-mode` | boolean | no | Optional boolean. |

### 6.7 EIC: atmel,sam0-eic -> microchip,eic-g1-intc

Source: `interrupt-controller/atmel,sam0-eic.yaml` (compatible `atmel,sam0-eic`). Target: `interrupt-controller/microchip,eic-g1-intc.yaml` (compatible `microchip,eic-g1-intc`).

The EIC is an infrastructure node on both sides (no `status = "disabled"`, no user options). Only the SoC dtsi changes; nothing in the application or board needs it. The node name changes from `eic@40002800` to `interrupt-controller@40002800`; the label `eic` stays.

Source properties: **28** (own + inherited, incl. child-binding levels). Table rows: **28**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `eic-g1-intc: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `eic-g1-intc: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `eic-g1-intc: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `eic-g1-intc: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `eic-g1-intc: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `eic-g1-intc: reg` | - | yes | keep | Same base and size (0x40002800, 0x38). |
| `reg-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `eic-g1-intc: interrupts` | - | yes | keep | Same 16 lines (12 to 27). |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `eic-g1-intc: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `eic-g1-intc: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `eic-g1-intc: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: clocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: base/base.yaml)` | int | no | `eic-g1-intc: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `eic-g1-intc: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `eic-g1-intc: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `eic-g1-intc: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `eic-g1-intc: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `eic-g1-intc: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |

Target `microchip,eic-g1-intc.yaml`: **49** properties; referenced by source rows: **28**; target-only rows below: **21**.

| target property | type | required | note |
|---|---|---|---|
| `eic-g1-intc: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `eic-g1-intc: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `eic-g1-intc: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `eic-g1-intc: low-power-mode` | boolean | no | SoC: set. |
| `eic-g1-intc: porta-unsupported-pins` | int | no | SoC bitmask of pins without an EIC line (same values in samd5xe5x.dtsi and pic32cx_sg.dtsi). |
| `eic-g1-intc: portb-unsupported-pins` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portc-unsupported-pins` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portc-supported-pins` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portd-supported-pins` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: porta-special-pins-1` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #porta-special-pins-1-cells` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portb-special-pins-1` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #portb-special-pins-1-cells` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portc-special-pins-1` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #portc-special-pins-1-cells` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portc-special-pins-2` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #portc-special-pins-2-cells` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portd-special-pins-1` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #portd-special-pins-1-cells` | int | no | Target-only (no source counterpart). |
| `eic-g1-intc: portd-special-pins-2` | array | no | Target-only (no source counterpart). |
| `eic-g1-intc: #portd-special-pins-2-cells` | int | no | Target-only (no source counterpart). |

### 6.8 GMAC MAC: atmel,sam0-gmac -> microchip,gmac-g1-eth

Source: `ethernet/atmel,sam0-gmac.yaml` (compatible `atmel,sam0-gmac`). Target: `ethernet/microchip,gmac-g1-eth.yaml` (compatible `microchip,gmac-g1-eth`).

Old structure: wrapper `ethernet@42000800` (`microchip,sam-ethernet-controller`, holds `reg`) with the child `gmac: ethernet` (`atmel,sam0-gmac`) and the child `mdio` (`atmel,sam-mdio`). New structure: one node `gmac: ethernet@42000800` (`microchip,gmac-g1-eth`) with the child `mdio`. Both keep the labels `gmac` and `mdio`; the node paths change.

Source properties: **44** (own + inherited, incl. child-binding levels). Table rows: **44**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `gmac-g1-eth: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `gmac-g1-eth: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `gmac-g1-eth: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `gmac-g1-eth: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `gmac-g1-eth: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg (inh: base/base.yaml)` | array | no | `gmac-g1-eth: reg` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: base/base.yaml)` | array | no | `gmac-g1-eth: interrupts` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `gmac-g1-eth: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `gmac-g1-eth: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `gmac-g1-eth: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: clocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: base/base.yaml)` | int | no | `gmac-g1-eth: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `gmac-g1-eth: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `gmac-g1-eth: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-eth: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-eth: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `gmac-g1-eth: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `local-mac-address (inh: ethernet/ethernet-controller.yaml)` | uint8-array | no | `gmac-g1-eth: local-mac-address` | uint8-array | no | keep | Same name. New: inherited from ethernet-mac-address.yaml (which also adds `zephyr,mac-address-prefix`). |
| `zephyr,random-mac-address (inh: ethernet/ethernet-controller.yaml)` | boolean | no | `gmac-g1-eth: zephyr,random-mac-address` | boolean | no | keep | Inherited from ethernet/ethernet-controller.yaml; same name and type exist on the target side. |
| `phy-handle (inh: ethernet/atmel,gmac-common.yaml)` | - | yes | `gmac-g1-eth: phy-handle` | - | yes | keep | Required on both; points at the `ethernet-phy@N` child of the MDIO node. |
| `phy-connection-type (inh: ethernet/atmel,gmac-common.yaml)` | - | no | `gmac-g1-eth: phy-connection-type` | - | no | keep | Default "rmii" on both. Both drivers read it with DT_INST_ENUM_IDX; the new driver accepts only MII and RMII (BUILD_ASSERT, eth_mchp_gmac_g1.c:183). |
| `ptp-clock (inh: ethernet/ethernet-controller.yaml)` | phandle | no | `gmac-g1-eth: ptp-clock` | phandle | no | keep | Inherited from ethernet/ethernet-controller.yaml; same name and type exist on the target side. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-eth: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-eth: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-eth: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-eth: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-eth: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `gmac-g1-eth: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `num-queues (inh: ethernet/atmel,gmac-common.yaml)` | int | yes | `gmac-g1-eth: num-queues` | int | yes | keep | Required on both; SoC value 1. The new binding states the range 1 to 8. |
| `max-frame-size (inh: ethernet/atmel,gmac-common.yaml)` | int | no | — | — | — | drop | Old default 1518. The new driver has no such option. |
| `max-speed (inh: ethernet/atmel,gmac-common.yaml)` | int | no | — | — | — | drop | Old default 100 (Mbit/s). The new driver has no such option. |
| `mac-eeprom (inh: ethernet/atmel,gmac-common.yaml)` | phandle | no | `gmac-g1-eth: nvmem-cells` | phandle-array | no | transform | Old: `mac-eeprom = <&eeprom>` plus Kconfig `ETH_SAM_GMAC_MAC_I2C_EEPROM` (board Kconfig.defconfig sets address 0x9A). New: `nvmem-cells = <&mac_address>; nvmem-cell-names = "mac-address";` with a cell `mac-address@9a` (`reg = <0x9a 6>`) in an `nvmem-layout { compatible = "fixed-layout"; }` child of the EEPROM node `eeprom_mac: eeprom@5e` (`compatible = "atmel,at24", "atmel,24mac402"`). Needs a child node inside the I2C EEPROM (card P8.4). On main the old binding `atmel,gmac-common.yaml` has already lost `mac-eeprom`, `max-frame-size` and `max-speed` (migration-guide-4.4.rst:635-637), so the old property only exists in the v4.3.0 source. |
| `ref-clk-source (inh: ethernet/atmel,gmac-common.yaml)` | string | no | — | — | — | drop | Old enum "internal"/"external". The new driver does not read it. |

Target `microchip,gmac-g1-eth.yaml`: **46** properties; referenced by source rows: **41**; target-only rows below: **5**.

| target property | type | required | note |
|---|---|---|---|
| `gmac-g1-eth: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-eth: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-eth: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-eth: nvmem-cell-names (inh: nvmem/nvmem-consumer.yaml)` | string-array | no | See `mac-eeprom` above. |
| `gmac-g1-eth: zephyr,mac-address-prefix (inh: ethernet/ethernet-mac-address.yaml)` | uint8-array | no | Optional. |

### 6.9 MDIO: atmel,sam-mdio -> microchip,gmac-g1-mdio

Source: `mdio/atmel,sam-mdio.yaml` (compatible `atmel,sam-mdio`). Target: `ethernet/mdio/microchip,gmac-g1-mdio.yaml` (compatible `microchip,gmac-g1-mdio`).

Both MDIO nodes are children of the GMAC node. The PHY is a child of the MDIO node (`ethernet-phy@0`, `reg = <0>`).

Source properties: **36** (own + inherited, incl. child-binding levels). Table rows: **36**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `gmac-g1-mdio: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `gmac-g1-mdio: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `gmac-g1-mdio: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `gmac-g1-mdio: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `gmac-g1-mdio: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg (inh: base/base.yaml)` | array | no | `gmac-g1-mdio: reg` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: base/base.yaml)` | array | no | `gmac-g1-mdio: interrupts` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `gmac-g1-mdio: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `gmac-g1-mdio: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `gmac-g1-mdio: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | phandle-array | no | — | — | — | drop | Old optional `clocks` on the MDIO node; the old SoC dtsi does not set it. New: the clocks sit on the GMAC node, none on the MDIO node. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: mdio/mdio-controller.yaml)` | - | yes | `gmac-g1-mdio: #address-cells` | - | yes | keep | Inherited from mdio/mdio-controller.yaml; same name and type exist on the target side. |
| `#size-cells (inh: mdio/mdio-controller.yaml)` | - | yes | `gmac-g1-mdio: #size-cells` | - | yes | keep | Inherited from mdio/mdio-controller.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-mdio: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-mdio: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-mdio: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-mdio: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `gmac-g1-mdio: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `gmac-g1-mdio: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `gmac-g1-mdio: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `gmac-g1-mdio: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `suppress-preamble (inh: mdio/mdio-controller.yaml)` | boolean | no | `gmac-g1-mdio: suppress-preamble` | boolean | no | keep | Inherited from mdio/mdio-controller.yaml; same name and type exist on the target side. |
| `clock-frequency (inh: mdio/mdio-controller.yaml)` | int | no | `gmac-g1-mdio: clock-frequency` | int | no | keep | Inherited from mdio/mdio-controller.yaml; same name and type exist on the target side. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-mdio: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-mdio: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-mdio: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-mdio: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `gmac-g1-mdio: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `gmac-g1-mdio: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |

Target `microchip,gmac-g1-mdio.yaml`: **39** properties; referenced by source rows: **35**; target-only rows below: **4**.

| target property | type | required | note |
|---|---|---|---|
| `gmac-g1-mdio: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-mdio: clocks (inh: base/base.yaml)` | phandle-array | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-mdio: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `gmac-g1-mdio: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |

### 6.10 GMAC wrapper: microchip,sam-ethernet-controller -> (none)

Source: `ethernet/microchip,sam-ethernet-controller.yaml` (compatible `microchip,sam-ethernet-controller`). Target: none.

The wrapper node exists only on the old side (`same5x.dtsi:13`). On main the GMAC sits directly at 0x42000800. The wrapper binding is identical in both workspaces.

Source properties: **28** (own + inherited, incl. child-binding levels). Table rows: **28**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `status (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `compatible (inh: base/base.yaml)` | string-array | yes | — | — | — | open | No target binding on main. |
| `reg` | - | yes | — | — | — | transform | Moves to the `gmac` node (`reg = <0x42000800 0x400>`). The new `gmac-g1-eth` binding does not declare `reg`; edtlib reads it from the node. |
| `reg-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `interrupts (inh: base/base.yaml)` | array | no | — | — | — | open | No target binding on main. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | — | — | — | open | No target binding on main. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | — | — | — | open | No target binding on main. |
| `label (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `clock-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `#address-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `#size-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `dma-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | — | — | — | open | No target binding on main. |

### 6.11 PHY: ethernet-phy (v4.3.0) -> ethernet-phy (main)

Source: `ethernet/phy/ethernet-phy.yaml` (compatible `ethernet-phy`). Target: `ethernet/phy/ethernet-phy.yaml` (compatible `ethernet-phy`).

The PHY child uses the generic `ethernet-phy` on both sides (boards: `phy: ethernet-phy@0 { compatible = "ethernet-phy"; reg = <0>; };`). Driver `PHY_GENERIC_MII` (`DT_HAS_ETHERNET_PHY_ENABLED`) on both. Main adds reset-gpios and moves `fixed-link` into its own binding.

Source properties: **31** (own + inherited, incl. child-binding levels). Table rows: **31**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `ethernet-phy: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `ethernet-phy: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `ethernet-phy: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `ethernet-phy: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `ethernet-phy: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg (inh: ethernet/phy/ethernet-phy-common.yaml)` | - | yes | `ethernet-phy: reg` | - | yes | keep | Inherited from ethernet/phy/ethernet-phy-common.yaml; same name and type exist on the target side. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: base/base.yaml)` | array | no | `ethernet-phy: interrupts` | array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `ethernet-phy: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `ethernet-phy: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `ethernet-phy: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: clocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: base/base.yaml)` | int | no | `ethernet-phy: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `ethernet-phy: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `ethernet-phy: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `ethernet-phy: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `ethernet-phy: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `ethernet-phy: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `default-speeds` | - | no | `ethernet-phy: default-speeds` | - | no | keep | Same name on the target. |
| `no-reset` | boolean | no | `ethernet-phy: no-reset` | boolean | no | keep | Same name on the target. |
| `fixed-link` | string | no | — | — | — | drop | Moved to `ethernet-phy-fixed-link.yaml` on main (separate compatible). Not used by the Xplained or Curiosity boards. |

Target `ethernet-phy.yaml`: **36** properties; referenced by source rows: **30**; target-only rows below: **6**.

| target property | type | required | note |
|---|---|---|---|
| `ethernet-phy: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `ethernet-phy: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `ethernet-phy: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `ethernet-phy: reset-gpios (inh: ethernet/phy/common/ethernet-phy-reset-gpios.yaml)` | phandle-array | no | Target-only (no source counterpart). Inherited from ethernet/phy/common/ethernet-phy-reset-gpios.yaml. |
| `ethernet-phy: reset-assert-duration-us (inh: ethernet/phy/common/ethernet-phy-reset-gpios.yaml)` | int | no | Target-only (no source counterpart). Inherited from ethernet/phy/common/ethernet-phy-reset-gpios.yaml. |
| `ethernet-phy: reset-deassertion-timeout-ms (inh: ethernet/phy/common/ethernet-phy-reset-gpios.yaml)` | int | no | Target-only (no source counterpart). Inherited from ethernet/phy/common/ethernet-phy-reset-gpios.yaml. |

### 6.12 RTC: atmel,sam0-rtc -> microchip,rtc-g1

Source: `rtc/atmel,sam0-rtc.yaml` (compatible `atmel,sam0-rtc`). Target: `rtc/microchip,rtc-g1.yaml` (compatible `microchip,rtc-g1`).

Old: one compatible with `counter-mode`. New: two compatibles, `microchip,rtc-g1` (RTC API, calendar) and `microchip,rtc-g1-counter` (counter API, adds `max-bit-width`).

Source properties: **42** (own + inherited, incl. child-binding levels). Table rows: **42**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `rtc-g1: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `rtc-g1: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `rtc-g1: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `rtc-g1: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `rtc-g1: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `rtc-g1: reg` | - | yes | keep | Same base. Size 0x40 vs 0x400. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts (inh: base/base.yaml)` | array | no | `rtc-g1: interrupts` | - | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `rtc-g1: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `rtc-g1: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `rtc-g1: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks` | - | yes | `rtc-g1: clocks` | - | yes | transform | Old: `<&osc32kctrl>, <&mclk 0x14 9>`. New: `<&mclkperiph APBA_RTC>, <&rtcclock RTC_ID>`. Cell formats and order change (binding_pairs.md section 5). |
| `clock-names` | - | yes | `rtc-g1: clock-names` | - | yes | transform | Values change: old `OSC32KCTRL, MCLK`, new `mclk, rtcclk`. Both drivers look the clocks up by name. |
| `#address-cells (inh: base/base.yaml)` | int | no | `rtc-g1: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `rtc-g1: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `rtc-g1: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `rtc-g1: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `rtc-g1: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `rtc-g1: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `rtc-g1: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `rtc-g1: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `rtc-g1: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `rtc-g1: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `alarms-count (inh: rtc/rtc-device.yaml)` | int | no | `rtc-g1: alarms-count` | int | no | keep | Both 2 in the SoC dtsi. The new driver reads it through `DT_NODELABEL(rtc)` (rtc_mchp_g1.c:23), so the label `rtc` must stay. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `rtc-g1: pinctrl-0` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `rtc-g1: pinctrl-1` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `rtc-g1: pinctrl-2` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `rtc-g1: pinctrl-3` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | `rtc-g1: pinctrl-4` | phandles | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | `rtc-g1: pinctrl-names` | string-array | no | keep | Inherited from pinctrl/pinctrl-device.yaml; same name and type exist on the target side. |
| `atmel,assigned-clocks` | - | yes | — | — | — | derive | Old: RTC clock source, `<&osc32kctrl N>` (board value 4, binding_pairs.md finding 5). New: `rtc-src = <N>` in the `rtcclock` node (0 ulp1k, 1 ulp32k, 4 xosc1k, 5 xosc32k, default 0). pic32cx_sg41_cult sets none (default). |
| `atmel,assigned-clock-names` | - | yes | — | — | — | drop | Only names the assigned clock. |
| `systimer` | boolean | no | — | — | — | drop | The RTC as system timer (Kconfig `SAM0_RTC_TIMER`, drivers/timer/Kconfig.sam0_rtc, depends on `DT_HAS_ATMEL_SAM0_RTC_ENABLED`) has no counterpart on main; the new timer drivers are `mchp_pit_g1_timer.c` and `mchp_sam_pit64b_timer.c`. Report as lost. |
| `cal-constant` | int | yes | `rtc-g1: cal-constant` | int | no | keep | Required on the old side, optional on the new side. Value `8192 * 128` = 1048576 on both. |
| `counter-mode` | string | no | — | — | — | transform | "clock" -> compatible `microchip,rtc-g1`. "count-32" or "count-16" -> compatible `microchip,rtc-g1-counter` plus `max-bit-width = <32>` or `<16>`. A compatible choice, not a property. |
| `prescaler` | int | no | `rtc-g1: prescaler` | int | no | keep | Same enum 1 to 1024. The old description is about CLKOUT, the new one about the RTC frequency (clock-frequency / prescaler). pic32cx_sg41_cult sets 1024. |
| `event-control-msk` | int | no | — | — | — | drop | Event output mask; the new driver has no counterpart. |

Target `microchip,rtc-g1.yaml`: **41** properties; referenced by source rows: **37**; target-only rows below: **4**.

| target property | type | required | note |
|---|---|---|---|
| `rtc-g1: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `rtc-g1: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `rtc-g1: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `rtc-g1: clock-frequency (inh: rtc/rtc.yaml)` | int | no | Optional (from rtc.yaml). |

Variant `microchip,rtc-g1-counter.yaml` (compatible `microchip,rtc-g1-counter`, **42** properties) against `microchip,rtc-g1.yaml` (**41**): equal in name, type and required flag: **41**; only in the variant: ['max-bit-width']; only in the primary target: none; same name, different type or required: none.

### 6.13 WDT: atmel,sam0-watchdog -> microchip,wdt-g1

Source: `watchdog/atmel,sam0-watchdog.yaml` (compatible `atmel,sam0-watchdog`). Target: `watchdog/microchip,wdt-g1.yaml` (compatible `microchip,wdt-g1`).

The old SoC label is `wdog`, the new one `wdt` (node name `watchdog@40002000` on both); the main samples use `&wdt`. All new required properties are SoC values.

Source properties: **28** (own + inherited, incl. child-binding levels). Table rows: **28**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `wdt-g1: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `wdt-g1: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `wdt-g1: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `wdt-g1: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `wdt-g1: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `wdt-g1: reg` | - | yes | keep | Same base and size (0x40002000, 13). |
| `reg-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | — | — | — | keep | Not declared by `wdt-g1`, but the SoC node keeps `interrupts = <10 0>` and `interrupt-names = "wdt-ew"`. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `wdt-g1: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `wdt-g1: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `wdt-g1: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: clocks` | - | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: clock-names` | - | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `#address-cells (inh: base/base.yaml)` | int | no | `wdt-g1: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `wdt-g1: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `wdt-g1: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `wdt-g1: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `wdt-g1: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `wdt-g1: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |

Target `microchip,wdt-g1.yaml`: **36** properties; referenced by source rows: **27**; target-only rows below: **9**.

| target property | type | required | note |
|---|---|---|---|
| `wdt-g1: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `wdt-g1: interrupts (inh: base/base.yaml)` | array | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `wdt-g1: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `wdt-g1: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `wdt-g1: max-installable-timeout-count` | int | yes | Required; SoC value 1. |
| `wdt-g1: max-timeout-window` | int | yes | Required; SoC value 0x4000. |
| `wdt-g1: max-timeout-window-mode` | int | yes | Required; SoC value 0x8000. |
| `wdt-g1: min-window-limit` | int | yes | Required; SoC value 8. |
| `wdt-g1: run-in-standby` | boolean | no | Optional boolean (new). |

### 6.14 DMAC: atmel,sam0-dmac -> microchip,dmac-g1-dma

Source: `dma/atmel,sam0-dmac.yaml` (compatible `atmel,sam0-dmac`). Target: `dma/microchip,dmac-g1-dma.yaml` (compatible `microchip,dmac-g1-dma`).

Both use `#dma-cells = <2>` (channel, trigger source), so the users' `dmas = <&dmac CH TRIG>` carry over unchanged. Trigger numbers are hardware constants (SERCOM0 RX 0x4, TX 0x5 on pic32cx_sg41_cult).

Source properties: **35** (own + inherited, incl. child-binding levels). Table rows: **35**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `dmac-g1-dma: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `dmac-g1-dma: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `dmac-g1-dma: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `dmac-g1-dma: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `dmac-g1-dma: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `dmac-g1-dma: reg` | array | yes | keep | Same base and size (0x4100a000, 0x50). The new binding declares `type: array`. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `dmac-g1-dma: interrupts` | array | yes | keep | Same 5 lines (31 to 35). The new binding declares `type: array`. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `dmac-g1-dma: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `dmac-g1-dma: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `dmac-g1-dma: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: clocks` | phandle-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: clock-names` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `#address-cells (inh: base/base.yaml)` | int | no | `dmac-g1-dma: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `dmac-g1-dma: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `dmac-g1-dma: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `dmac-g1-dma: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `dmac-g1-dma: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `dmac-g1-dma: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#dma-cells` | - | no | `dmac-g1-dma: #dma-cells` | int | no | keep | `const: 2` on both; cells `channel`, `trigsrc`. |
| `dma-channel-mask (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-channel-mask` | int | no | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. |
| `dma-channels (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-channels` | int | yes | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. Required: no on the source, yes on the target. |
| `dma-requests (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-requests` | int | no | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. |
| `dma-buf-addr-alignment (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-buf-addr-alignment` | int | no | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. |
| `dma-buf-size-alignment (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-buf-size-alignment` | int | no | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. |
| `dma-copy-alignment (inh: dma/dma-controller.yaml)` | int | no | `dmac-g1-dma: dma-copy-alignment` | int | no | keep | Inherited from dma/dma-controller.yaml; same name and type exist on the target side. |

Target `microchip,dmac-g1-dma.yaml`: **39** properties; referenced by source rows: **35**; target-only rows below: **4**.

| target property | type | required | note |
|---|---|---|---|
| `dmac-g1-dma: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dmac-g1-dma: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dmac-g1-dma: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `dmac-g1-dma: dma-alignment` | int | no | SoC value 16. |

### 6.15 TRNG: atmel,sam-trng -> microchip,trng-g1-entropy

Source: `rng/atmel,sam-trng.yaml` (compatible `atmel,sam-trng`). Target: `rng/microchip,trng-g1-entropy.yaml` (compatible `microchip,trng-g1-entropy`).

The old SoC reuses the SAM3/4/E7 binding `atmel,sam-trng` (driver `ENTROPY_SAM_RNG`). New: `microchip,trng-g1-entropy` (`ENTROPY_MCHP_TRNG_G1`). Board usage is `zephyr,entropy = &trng` on both; on the new side the board also needs `&trng { status = "okay"; };`.

Source properties: **28** (own + inherited, incl. child-binding levels). Table rows: **28**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | `trng-g1: wakeup-source` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | `trng-g1: zephyr,pm-device-runtime-auto` | boolean | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | `trng-g1: zephyr,disabling-power-states` | phandles | no | keep | Inherited from base/pm.yaml; same name and type exist on the target side. |
| `status (inh: base/base.yaml)` | string | no | `trng-g1: status` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `compatible (inh: base/base.yaml)` | string-array | yes | `trng-g1: compatible` | string-array | yes | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `reg` | - | yes | `trng-g1: reg` | - | yes | keep | Same base (0x42002800). Size 0x1e vs 0x24. |
| `reg-names (inh: base/base.yaml)` | string-array | no | `trng-g1: reg-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupts` | - | yes | `trng-g1: interrupts` | - | yes | keep | Same line 131. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | `trng-g1: interrupts-extended` | compound | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | `trng-g1: interrupt-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | `trng-g1: interrupt-parent` | phandle | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `label (inh: base/base.yaml)` | string | no | `trng-g1: label` | string | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | `trng-g1: clocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `clock-names (inh: base/base.yaml)` | string-array | no | `trng-g1: clock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#address-cells (inh: base/base.yaml)` | int | no | `trng-g1: #address-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#size-cells (inh: base/base.yaml)` | int | no | `trng-g1: #size-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | `trng-g1: dmas` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `dma-names (inh: base/base.yaml)` | string-array | no | `trng-g1: dma-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | `trng-g1: io-channels` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | `trng-g1: io-channel-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | `trng-g1: mboxes` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | `trng-g1: mbox-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | `trng-g1: power-domains` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | `trng-g1: power-domain-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | `trng-g1: #power-domain-cells` | int | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | `trng-g1: hwlocks` | phandle-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | `trng-g1: hwlock-names` | string-array | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | `trng-g1: zephyr,deferred-init` | boolean | no | keep | Inherited from base/base.yaml; same name and type exist on the target side. |

Target `microchip,trng-g1-entropy.yaml`: **32** properties; referenced by source rows: **28**; target-only rows below: **4**.

| target property | type | required | note |
|---|---|---|---|
| `trng-g1: ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `trng-g1: dma-coherent (inh: base/base.yaml)` | boolean | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `trng-g1: dma-ranges (inh: base/base.yaml)` | compound | no | Target-only (no source counterpart). Inherited from base/base.yaml. |
| `trng-g1: run-in-standby-en` | int | no | Optional int. |

### 6.16 CAN: atmel,sam0-can -> (no target on main)

Source: `can/atmel,sam0-can.yaml` (compatible `atmel,sam0-can`). Target: none.

No `microchip,*` CAN binding or driver and no CAN node in any SAM D5x/E5x or pic32cx_sg dtsi on main. `drivers/can/can_sam0.c` (`CAN_SAM0`, depends on `DT_HAS_ATMEL_SAM0_CAN_ENABLED`) programs `GCLK`/`MCLK` registers of the CMSIS header directly. All rows are `open`.

Source properties: **47** (own + inherited, incl. child-binding levels). Table rows: **47**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `status (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `compatible (inh: base/base.yaml)` | string-array | yes | — | — | — | open | No target binding on main. |
| `reg` | - | yes | — | — | — | open | No target binding on main. |
| `reg-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `interrupts` | - | yes | — | — | — | open | No target binding on main. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | — | — | — | open | No target binding on main. |
| `interrupt-names` | - | yes | — | — | — | open | No target binding on main. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | — | — | — | open | No target binding on main. |
| `label (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `clocks` | - | yes | — | — | — | open | No target binding on main. |
| `clock-names` | - | yes | — | — | — | open | No target binding on main. |
| `#address-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `#size-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `dma-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `bus-speed (inh: can/can-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `bitrate (inh: can/can-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `sample-point (inh: can/can-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `phys (inh: can/can-controller.yaml)` | phandle | no | — | — | — | open | No target binding on main. |
| `bus-speed-data (inh: can/can-fd-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `bitrate-data (inh: can/can-fd-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `sample-point-data (inh: can/can-fd-controller.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `bosch,mram-cfg (inh: can/bosch,m_can-base.yaml)` | array | yes | — | — | — | open | No target binding on main. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `atmel,assigned-clocks` | - | yes | — | — | — | open | No target binding on main. |
| `atmel,assigned-clock-names` | - | yes | — | — | — | open | No target binding on main. |
| `divider` | int | yes | — | — | — | open | No target binding on main. |
| `child: min-bitrate` | int | no | — | — | — | open | No target binding on main. |
| `child: max-bitrate` | int | yes | — | — | — | open | No target binding on main. |

### 6.17 USB: atmel,sam0-usb -> (no target on main)

Source: `usb/atmel,sam0-usb.yaml` (compatible `atmel,sam0-usb`). Target: none.

No device-controller node for the SAM D5x/E5x USB on main. `udc_sam0.c` (`UDC_SAM0`) pokes `MCLK->APBBMASK` and `GCLK->PCHCTRL` directly. `microchip,udphs-g1-udc` is a different IP (SAMA5/SAMA7 UDPHS). All rows are `open`.

Source properties: **39** (own + inherited, incl. child-binding levels). Table rows: **39**.

| source property | type | required | target property | type | required | mapping | note |
|---|---|---|---|---|---|---|---|
| `wakeup-source (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,pm-device-runtime-auto (inh: base/pm.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `zephyr,disabling-power-states (inh: base/pm.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `status (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `compatible (inh: base/base.yaml)` | string-array | yes | — | — | — | open | No target binding on main. |
| `reg` | - | yes | — | — | — | open | No target binding on main. |
| `reg-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `interrupts` | - | yes | — | — | — | open | No target binding on main. |
| `interrupts-extended (inh: base/base.yaml)` | compound | no | — | — | — | open | No target binding on main. |
| `interrupt-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `interrupt-parent (inh: base/base.yaml)` | phandle | no | — | — | — | open | No target binding on main. |
| `label (inh: base/base.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `clocks (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `clock-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `#address-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `#size-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `dmas (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `dma-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `io-channels (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `io-channel-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `mboxes (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `mbox-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `power-domains (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `power-domain-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `#power-domain-cells (inh: base/base.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `hwlocks (inh: base/base.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `hwlock-names (inh: base/base.yaml)` | string-array | no | — | — | — | open | No target binding on main. |
| `zephyr,deferred-init (inh: base/base.yaml)` | boolean | no | — | — | — | open | No target binding on main. |
| `maximum-speed (inh: usb/usb-controller.yaml)` | string | no | — | — | — | open | No target binding on main. |
| `vbus-gpios (inh: usb/usb-controller.yaml)` | phandle-array | no | — | — | — | open | No target binding on main. |
| `num-bidir-endpoints (inh: usb/usb-ep.yaml)` | int | yes | — | — | — | open | No target binding on main. |
| `num-in-endpoints (inh: usb/usb-ep.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `num-out-endpoints (inh: usb/usb-ep.yaml)` | int | no | — | — | — | open | No target binding on main. |
| `pinctrl-0 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-1 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-2 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-3 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-4 (inh: pinctrl/pinctrl-device.yaml)` | phandles | no | — | — | — | open | No target binding on main. |
| `pinctrl-names (inh: pinctrl/pinctrl-device.yaml)` | string-array | no | — | — | — | open | No target binding on main. |

## 7. Pin signals in the ATDF

Method: `atdf_pins.py` reads every `<instance>/<signals>/<signal group index pad function ioset>` of the ATDF and builds the macro name `P<port><n><function>_<instance>_<group><index>` (pad `PA02` becomes `PA2`, as in the headers); it counts how many of these macros exist in the pinctrl header of each port. `atdf_inst.py` lists the signals of single instances. Output: evidence file, sections G and H.

Result for the three devices (the ATDF of ATSAME54P20A and of PIC32CX1025SG41128 give the same counts for these modules):

| Peripheral | ATDF instances and signals (E54P / SG41 128) | Pad choice for the user | Header macros present: old `same54p-pinctrl.h` / new E54 header / new SG41 header |
|---|---|---|---|
| TC | TC0-TC7, group `WO`: 6, 4, 6, 4, 6, 6, 6, 6 signals; function letter E (e.g. `PB14E_TC5_WO0`) | WO0 and WO1 per TC, 3 candidate pads each (IOSET 1-3) | all present / all / all |
| TCC | TCC0 44, TCC1 34, TCC2 6, TCC3 4, TCC4 4 signals, group `WO`; functions F or G depending on the pad | up to 6 candidates per WOn (IOSET 1-6, e.g. TCC0 WO0: PA08 F io1, PC04 F io2, PC10 F io3, PC16 F io4, PB12 G io5, PA20 G io6) | all present / all / all (SG61: TCC0 43, TCC1 30 signals) |
| ADC | ADC0: AIN 16, X 32, Y 32, VREFA/B/C; ADC1: AIN 16; function B | AINn to pad is fixed per ADC; the same pad serves ADC0 and ADC1 with different AIN numbers (e.g. PB08 is ADC0 AIN2 and ADC1 AIN0) | old: ADC0 16 of 83 (X, Y, VREF missing), ADC1 16 of 16 / new: all 83 and 16 / all |
| DAC | `VOUT0` PA02 B, `VOUT1` PA05 B | none (one pad each) | all / all / all |
| EIC | `EXTINT` 98 + `NMI` 1 (E54P and SG41); SG61: 93 + 1 | none for GPIO interrupts (the driver muxes), only the EXTINT line check | old: 98 of 99 (`PC7A_EIC_EXTINT9` missing) / all / all |
| GMAC | 24 signals, function L: RMII 8, MII extras 8, MDC/MDIO 4 pairs | RMII none (one pad each), MDC/MDIO choose 1 of 4 pairs (IOSET 1-4) | old: 23 of 24 (`PA16L_GMAC_GCRS` missing) / all / all |
| RTC | `IN0`-`IN4`, `OUT`; function letter `RTC` (not a PMUX letter) | not used by Zephyr | none on either side (0 of 6) |
| CAN | CAN0 TX PA22/PA24, RX PA23/PA25 (function I); CAN1 TX PB12/PB14, RX PB13/PB15 (function H) | 2 candidates each | all present on every side, but no Zephyr peripheral on main |
| USB | DM PA24, DP PA25 (H), SOF_1KHZ PA23 (io1) or PB22 (io2) | fixed | old: 2 of 4 (SOF missing) / all / all |
| WDT, DMAC, TRNG | no pin signals | | |

Conclusions for the recipes: (a) the function letter and the IOSET are properties of the *pad and instance* (taken from the ATDF), not of the peripheral, so the recipe asks the provider for candidates per role and writes `P<port><n><function>_<instance>_<role>` through the formatter; (b) TCC/TC WOn and ADC AINn are the roles that need a pad picker, GMAC MDC/MDIO is a pair picker, everything else in this card is fixed; (c) for the old port the header is incomplete (finding 10), so a recipe on the old port has to check the macro exists before offering a pad.

## 8. Peripherals without a target

| Peripheral | Old side | New side (main) | What the port report must say |
|---|---|---|---|
| CAN (CAN0, CAN1) | `atmel,sam0-can` nodes in `same5x.dtsi`, driver `can_sam0.c` (needs the old `soc.h` CMSIS `GCLK`/`MCLK`), `bosch,mram-cfg`, `divider` | no node in `samd5xe5x*.dtsi`, `same54.dtsi`, `same53.dtsi`, `pic32cx_sg*.dtsi`; no binding with a microchip compatible; only `can_sam0.c` (unchanged, gated by `DT_HAS_ATMEL_SAM0_CAN_ENABLED`) | "CAN has no node and no driver on the target; the pins exist (CAN0 PA22-PA25, CAN1 PB12-PB15)". All 47 source properties map to `open` (table 6.16). |
| USB device | `atmel,sam0-usb` (`usb0`), driver `udc_sam0.c` | no node; `udc_mchp_udphs_g1.c` is the UDPHS IP of SAMA5/SAMA7 | same; all 39 properties `open` (table 6.17). |
| RTC as system timer | `systimer` on `atmel,sam0-rtc` | none | "system timer on RTC not available". |
| TC6, TC7 on pic32cx_sg | (the E54P has them) | not in `pic32cx_sg.dtsi` although the ATDF has them | "TC6/TC7 not in the target SoC dtsi". |

Verified against the side session's statement: CAN and USB have no node in `pic32cx_sg.dtsi` on main (confirmed), and in addition none in the SAM D5x/E5x dtsi on main.

## 9. Not checked

- No Zephyr build was run in this card (research only). Whether the translated nodes load in main's edtlib against `pic32cx_sg41` is the acceptance of P8.10.
- Driver reads (`DT_INST_PROP...`) were collected by regular expression on the driver sources, not by compiling; a property read behind a macro could be missing from that list. The binding tables do not depend on it.
- The datasheet values behind SoC numbers (channels, widths, trigger numbers) were not compared with the datasheet; they are taken from the dtsi files.
- Kconfig dependencies were read from the `depends on`/`config` lines, not evaluated with `kconfiglib`.
- The `ethernet-phy` binding is generic; whether a given PHY needs a vendor driver (not the case for the KSZ8091RNA boards) was not examined.
- Other chips of the families (SAM D51, E51/E53) were not looked at beyond the common dtsi.

## 10. Results of P8.10 (translations)

Implemented in `dt_explorer/translations/atmel_sam0_to_mchp_g1.yaml` and `.py`; evidence and complete outputs in `docs/evidence/P8.10.md`. Every entry accounts for every property of its source binding (rules, groups, properties taken over by a derive); the property counts equal the table rows of section 6 (`tests/test_translate.py::test_p810_*`). Each translated lab1 node loads in Zephyr main's edtlib against the `pic32cx_sg41` SoC dtsi.

| Pair (table) | Source props = rows | Target props = mapped + target-only | Hooks and special rules | lab1 node tested |
|---|---|---|---|---|
| TC counter 6.1 | 37 | 40 = 35 + 5 | `tc_counter_clocks`: periph clocks, 32-bit pairing (`client_mclk`, `max-bit-width` 32) | `tc0` (disabled in lab1, enabled in the test) |
| TC PWM 6.2 | 40 | 41 = 38 + 3 | `tc_pwm_clocks` (pairs only for `counter-size` 32), `pwm_cells`, `counter-size` to `max-bit-width` | `tc0` turned into a TC PWM |
| TCC PWM 6.3 | 40 | 41 = 38 + 3 | `pwm_cells`, rename, periph clocks | `tcc0` |
| ADC 6.5 | 51 | 55 = 49 + 6 | `adc_prescaler`, `calib-offset` dropped, children rule for `channel@N` | `adc0` + two synthetic channels |
| DAC 6.6 | 43 | 51 = 39 + 12 | `dac_reference` (`refsel`, cells), child rules (`sampling-ratio`, `refresh-period` x30, `rate`) | `dac0` + `channel_0/1` of the board file |
| GMAC 6.8 | 44 | 46 = 41 + 5 | `gmac_mac_address` (report line), five drops | `gmac` |
| MDIO 6.9 | 36 | 39 = 35 + 4 | child rule for `ethernet-phy` (pass-through, `fixed-link` dropped) | `mdio` + `phy` |
| RTC 6.12 | 42 | 41 = 37 + 4 (counter variant 42) | `rtc_clocks`, `rtc_source` (report), `rtc_counter_mode`; second entry for `microchip,rtc-g1-counter` | `rtc` (both entries) |
| WDT 6.13 | 28 | 36 = 27 + 9 | clocks dropped | `wdog` (addressed as `wdt`) |
| DMAC 6.14 | 35 | 39 = 35 + 4 | clocks dropped | `dmac` |
| TRNG 6.15 | 28 | 32 = 28 + 4 | clocks dropped | `trng` |
| CAN 6.16, USB 6.17, GMAC wrapper 6.10 | 47, 39, 28 | none | `no_target` entries with the reason | listed in the port report (see below) |

Deviations from the mapping columns of section 6 (decided at implementation):

1. **Old-style `clocks`/`clock-names` on WDT, DMAC, TRNG, GMAC, MDIO are dropped with a reason, not kept.** The tables say `keep` because the name and type match, but the old SoC nodes carry no clock rules and an old `<&gclk N>, <&mclk OFF BIT>` entry copied unchanged would be wrong on g1. The target SoC dtsi supplies the clocks.
2. `calib-offset` (ADC) and `differential-mode` (DAC) are dropped with a reason (the table says `derive`): the first lives in `calib-mapping` of the SoC dtsi, the second is the Kconfig symbol `DAC_MCHP_G1_DIFFERENTIAL`.
3. **GMAC `mac-eeprom` is a report line**, not a translation: the nvmem cell lives inside the EEPROM node, a different node. The line gives the two statements to add. Both g1 boards already have them.
4. `#pwm-cells` 2 to 3 is translated on the controller; **the consumers' `pwms` cells are not rewritten** (engine limit, see below). A report line says so.
5. DAC children: the properties are translated; the node names (`channel_0` to `channel@0`) and `reg` are not (the engine cannot rename a child or add `reg`). `dac_reference` writes `#address-cells = <1>; #size-cells = <0>` on the DAC node and reports the rename.
6. The ADC `prescaler` is kept when the source node has it. A source node without it (lab1's `adc0`: the old SoC dtsi sets it, the board does not) yields no `prescaler`; the entry lists it under `target_only` as a reminder, so every port report carries the line.

Findings from the work:

- **Bug in the existing `base` group (fixed here):** `zephyr,deferred-init, ..., zephyr,pm-device-runtime-auto, zephyr,disabling-power-states` stood unquoted in a YAML flow list and was split at the comma into `zephyr`, `deferred-init`, ... (six bogus names), so these three base properties were never kept. They are quoted now (`test_base_group_properties_with_a_comma_are_whole_names`).
- `TC0/TC1`, `TCC0/TCC1`, `TC2/TC3` ... share one GCLK channel; the clock hook now picks the GCLK entry that belongs to the same peripheral as the MCLK entry (`_gclk_choice`), which also makes the `assigned-clocks` report line name the right child.
- The engine renders a `uint8-array` (`local-mac-address`) as cells, which is 4 bytes per element; the tests drop it from the source node. No effect on the port, which only passes board-written properties.
- Engine limits met (not changed, outside the card): consumers' `pwms` rewrite; rename of a child node and `reg` from its name; a default for a property that the source node does not have (ADC `prescaler`); the port report lists a node as untranslatable only when a recipe covers it (draft node): lab1's enabled `usb0` has `soc` origin and no recipe, so it appears nowhere in the report although `atmel,sam0-usb` has a `no_target` entry.
