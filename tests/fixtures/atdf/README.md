# ATDF fixtures

Trimmed excerpts of Microchip device files, used by `tests/test_microchip_atdf.py` so the
tests run without a west workspace. Source: `hal_microchip`
(https://github.com/zephyrproject-rtos/hal_microchip), Apache-2.0, Copyright Microchip Technology Inc.
Revision: `89754d87258945ceb1cff8ecee0ddd8852396ae8` (`git rev-parse HEAD` of
`C:\Masters\26020_RTOS4\zephyrproject\modules\hal\microchip`).

| Fixture | Source file in hal_microchip |
|---|---|
| `ATSAME54P20A.atdf` | `packs/sam/sam_d5x_e5x/atsame54/atdf/ATSAME54P20A.atdf` |
| `PIC32CX1025SG61128.atdf` | `packs/pic32c/pic32cx_sg/pic32cx_sg61/atdf/PIC32CX1025SG61128.atdf` |

Trimming: the `<modules>` register definitions, all `<device>` children except `<peripherals>`, and
all peripheral modules except SERCOM and EIC were removed; per instance only `register-group` and
`signals` remain. `variants` and `pinouts` are complete. No values were changed.
