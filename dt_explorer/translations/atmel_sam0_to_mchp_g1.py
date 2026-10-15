"""Hooks for atmel_sam0_to_mchp_g1.yaml (old SAM0 port -> Microchip g1 port).

Loaded by the generic translation loader from the file named under ``hooks:``.
Every public function is a hook; its signature depends on how the YAML uses it:

* transform hook: ``fn(value, props, ctx) -> (new value, report lines)``
* derive hook:    ``fn(props, ctx) -> (new props, report lines)``

``ctx["tables"]`` is the ``tables:`` section of the YAML. The clock rules are
data (``mclk_bus``, ``mclk_periph``, ``gclk_periph``), generated from
``mchp_sam_d5x_e5x_clock.h`` and verified against both SoC .dtsi files
(see docs/evidence/P5.2.md).
"""
from __future__ import annotations

from dt_explorer.core.translate import Ref, Sym


def _periph(macro: str) -> str:
    """Instance part of a clock macro: ...MCLKPERIPH_ID_APBA_TC0 -> TC0, ...GCLKPERIPH_ID_TC0 -> TC0."""
    tail = macro.rsplit("_ID_", 1)[1]
    return tail.split("_", 1)[1] if tail.startswith(("AHB_", "APB")) else tail


def _gclk_choice(tables: dict, channel: int, mclk_macro: str | None = None):
    """Pick the clock macro for a GCLK peripheral channel: the only one; else the unique *_CORE one; else the
    one that belongs to the same peripheral as the MCLK entry (TC0/TC1 share channel 9, TCC0/TCC1 channel 25)."""
    cands = tables["gclk_periph"].get(str(channel)) or []
    if len(cands) == 1:
        return cands[0]
    core = [c for c in cands if c[0].endswith("_CORE")]
    if len(core) == 1:
        return core[0]
    if mclk_macro:
        same = [c for c in cands if c[0].rsplit("_ID_", 1)[1] == _periph(mclk_macro)]
        if len(same) == 1:
            return same[0]
    return None


def _split_clocks(props: dict, tables: dict):
    """Return (gclk channel | None, (mclk offset, bit) | None, problems)."""
    gch = mclk = None
    problems: list[str] = []
    names = props.get("clock-names") or []
    for i, entry in enumerate(props.get("clocks") or []):
        cells = entry[1:]
        kind = names[i].lower() if i < len(names) else ("gclk" if len(cells) == 1 else "mclk")
        if kind == "gclk" and len(cells) == 1:
            gch = int(cells[0])
        elif kind == "mclk" and len(cells) == 2:
            mclk = (int(cells[0]), int(cells[1]))
        else:
            problems.append(f"clock entry {i} ({kind}, {len(cells)} cells) not understood")
    return gch, mclk, problems


def clocks_to_periph(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """<&gclk N>, <&mclk OFFSET BIT>  ->  <&mclkperiph ID>, <&gclkperiph ID> with new names."""
    t = ctx["tables"]
    labels = t["labels"]
    gch, mclk, problems = _split_clocks(props, t)
    lines = [f"clocks: {p}" for p in problems]
    if gch is None or mclk is None:
        lines.append("clocks: need both a GCLK and an MCLK entry; clocks and clock-names not translated")
        return {}, lines
    off, bit = mclk
    bus = t["mclk_bus"].get(off)
    mname = t["mclk_periph"].get(f"{bus}:{bit}") if bus is not None else None
    gname = _gclk_choice(t, gch, mname[0] if mname else None)
    if mname is None:
        lines.append(f"clocks: MCLK offset {off:#x} bit {bit} has no peripheral clock in the target table")
    if gname is None:
        lines.append(f"clocks: GCLK channel {gch} has no unique peripheral clock in the target table")
    if mname is None or gname is None:
        return {}, lines + ["clocks and clock-names not translated"]
    return {
        "clocks": [[Ref(labels["mclk"]), Sym(*mname)], [Ref(labels["gclk"]), Sym(*gname)]],
        "clock-names": ["mclk", "gclk"],
    }, lines


def assigned_clock_to_gclkperiph(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """atmel,assigned-clocks = <&gclk N> has no consumer-side counterpart; report the controller-side rule."""
    t = ctx["tables"]
    entries = props.get("atmel,assigned-clocks") or []
    if not entries or len(entries[0]) < 2:
        return {}, ["atmel,assigned-clocks: unexpected value; generator selection not translated"]
    gen = int(entries[0][1])
    gch, mclk, _ = _split_clocks(props, t)
    mname = None
    if mclk is not None and t["mclk_bus"].get(mclk[0]) is not None:
        mname = t["mclk_periph"].get(f"{t['mclk_bus'][mclk[0]]}:{mclk[1]}")
    gname = _gclk_choice(t, gch, mname[0] if mname else None) if gch is not None else None
    label = t["labels"]
    child = (gname[0].rsplit("_ID_", 1)[1].rsplit("_CORE", 1)[0].lower() if gname else "<peripheral>")
    return {}, [
        f"atmel,assigned-clocks: generator {gen} moves to the clock controller. Add in the target board file: "
        f"&{label['controller']} {{ {label['gclk']} {{ {child} {{ subsystem = <{gname[0] if gname else '?'}>; "
        f"gclkperiph-src = \"gclk{gen}\"; gclkperiph-en = <1>; }}; }}; "
        f"{label['mclk']} {{ {child} {{ subsystem = <{mname[0] if mname else '?'}>; mclk-en = <1>; }}; }}; }};"
    ]


def warn_unused_dma(value, props: dict, ctx: dict):
    """Kept because the binding allows it; the g1 UART driver has no DMA code."""
    return value, ["dmas/dma-names: kept, but the target UART driver has no DMA support (property is ignored there)"]


# ---- P8.10: further peripherals ---------------------------------------------------------------------

_PAIRED_CLIENT = "client_mclk"


def _tc_clocks(props: dict, ctx: dict, pair: bool) -> tuple[dict, list[str]]:
    """clocks_to_periph for a TC; `pair` adds the MCLK entry of the odd partner (32-bit mode = two instances)."""
    out, lines = clocks_to_periph(props, ctx)
    if not out or not pair:
        return out, lines
    t = ctx["tables"]
    macro = out["clocks"][0][1].name
    n = int(macro.rsplit("_TC", 1)[1])
    if n % 2:
        return out, lines + [f"clocks: TC{n} is an odd instance and cannot be the 32-bit master; no {_PAIRED_CLIENT} added"]
    partner = next((v for v in t["mclk_periph"].values() if v[0].endswith(f"_TC{n + 1}")), None)
    if partner is None:
        return out, lines + [f"clocks: no MCLK entry for the partner TC{n + 1} in the target table"]
    out["clocks"].append([Ref(t["labels"]["mclk"]), Sym(*partner)])
    out["clock-names"].append(_PAIRED_CLIENT)
    return out, lines + [f"clocks: 32-bit mode pairs TC{n} with TC{n + 1}; {_PAIRED_CLIENT} added, TC{n + 1} is used up"]


def tc_counter_clocks(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """atmel,sam0-tc32 is the 32-bit counter: periph clocks plus the partner, and max-bit-width 32."""
    out, lines = _tc_clocks(props, ctx, True)
    if _PAIRED_CLIENT in (out.get("clock-names") or []):
        out["max-bit-width"] = 32
    return out, lines


def tc_pwm_clocks(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """atmel,sam0-tc-pwm: the partner clock only when the board asks for a 32-bit counter."""
    return _tc_clocks(props, ctx, props.get("counter-size") == 32)


def pwm_cells(value, props: dict, ctx: dict):
    """#pwm-cells 2 (channel, period) -> 3 (channel, period, flags); the consumers' pwms are other nodes."""
    lines = []
    if value != 3:
        lines.append("#pwm-cells: 2 (channel, period) becomes 3 (channel, period, flags): every consumer's "
                     "pwms = <&label CHANNEL PERIOD> needs a third cell (0 = normal polarity); consumers are "
                     "other nodes and are not rewritten by this translation")
    return 3, lines


_ADC_PRESCALER = (2, 4, 8, 16, 32, 64, 128, 256)


def adc_prescaler(value, props: dict, ctx: dict):
    """Kept (same enum); required on g1 and not set by the SoC dtsi. ADC clock = generator rate / prescaler <= 16 MHz."""
    lines = [f"prescaler: {value} kept; g1 requires it and the SoC dtsi sets none. The ADC clock (generator rate / "
             f"prescaler) must stay <= 16 MHz: the old SoC used 48 MHz / 4, the target boards use 60 MHz / 4"]
    if value not in _ADC_PRESCALER:
        lines.append(f"prescaler: {value} is not in the target enum {list(_ADC_PRESCALER)}")
    return value, lines


def dac_reference(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """reference -> refsel (value map); the node needs #address-cells/#size-cells for channel@N children."""
    table = ctx["tables"]["dac_refsel"]
    ref = props["reference"]
    new = table.get(ref)
    lines = []
    if new is None:
        return {}, [f"reference: {ref!r} has no counterpart in {sorted(table)}; refsel (required on g1) not set"] + lines
    return {"refsel": new, "#address-cells": 1, "#size-cells": 0}, lines


def dac_refresh_us(value, props: dict, ctx: dict):
    """refresh-period: old int 0..15 in units of 30 us; new value in microseconds."""
    return int(value) * 30, []


def gmac_mac_address(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """mac-eeprom = <&eeprom> has no property counterpart: g1 reads an nvmem cell of the EEPROM node."""
    v = props.get("mac-eeprom")
    label = v[0].label if isinstance(v, list) and v and isinstance(v[0], Ref) else "<eeprom>"
    return {}, [f"mac-eeprom = <&{label}>: no equivalent property. g1 reads the MAC address from an nvmem cell: add "
                f'nvmem-cells = <&mac_address>; nvmem-cell-names = "mac-address"; to the GMAC node and, inside &{label}, '
                f'nvmem-layout {{ compatible = "fixed-layout"; mac_address: mac-address@9a {{ reg = <0x9a 6>; }}; }}; '
                f"(sam_e54_xpro and pic32cx_sg41_cult already have it; Kconfig NVMEM replaces ETH_SAM_GMAC_MAC_I2C_EEPROM)"]


def rtc_clocks(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """<&osc32kctrl>, <&mclk OFFSET BIT> -> <&mclkperiph ID>, <&rtcclock CLOCK_MCHP_RTC_ID> (names mclk, rtcclk)."""
    t = ctx["tables"]
    names = [n.lower() for n in (props.get("clock-names") or [])]
    mclk = None
    for i, entry in enumerate(props.get("clocks") or []):
        if i < len(names) and names[i] == "mclk" and len(entry) == 3:
            mclk = (int(entry[1]), int(entry[2]))
    bus = t["mclk_bus"].get(mclk[0]) if mclk else None
    mname = t["mclk_periph"].get(f"{bus}:{mclk[1]}") if bus is not None else None
    if mname is None:
        return {}, ["clocks: no MCLK entry that maps to a peripheral clock; clocks and clock-names not translated"]
    rtc_id = (3 << 26) | (0x3F << 20) | (0x3F << 14) | (0x3F << 8)          # MCHP_CLOCK_DERIVE_ID(3, 0x3f, 0x3f, 0x3f, 0)
    return {"clocks": [[Ref(t["labels"]["mclk"]), Sym(*mname)], [Ref(t["labels"]["rtc"]), Sym("CLOCK_MCHP_RTC_ID", rtc_id)]],
            "clock-names": ["mclk", "rtcclk"]}, []


_RTC_SRC = {0: "ulp1k", 1: "ulp32k", 4: "xosc1k", 5: "xosc32k"}


def rtc_source(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """atmel,assigned-clocks = <&osc32kctrl N>: the source is rtc-src on the rtcclock node of the target."""
    entries = props.get("atmel,assigned-clocks") or []
    if not entries or len(entries[0]) < 2:
        return {}, ["atmel,assigned-clocks: unexpected value; RTC clock source not translated"]
    n = int(entries[0][1])
    label = ctx["tables"]["labels"]["rtc"]
    return {}, [f"atmel,assigned-clocks = <&osc32kctrl {n}>: RTC source {_RTC_SRC.get(n, '?')}. Add in the target board file: "
                f"&{label} {{ rtc-src = <{n}>; }}; (default 0 = ulp1k)"]


def rtc_counter_mode(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """rtc-g1 entry: counter-mode "clock" is this compatible; the counter modes need the counter variant."""
    mode = props.get("counter-mode")
    if mode == "clock":
        return {}, []
    return {}, [f'counter-mode = "{mode}": needs compatible microchip,rtc-g1-counter with max-bit-width (translation '
                f"entry atmel,sam0-rtc -> microchip,rtc-g1-counter); the node is translated as the calendar RTC here"]


def rtc_counter_mode_counter(props: dict, ctx: dict) -> tuple[dict, list[str]]:
    """rtc-g1-counter entry: count-32/count-16 -> max-bit-width 32/16."""
    mode = props.get("counter-mode")
    if mode in ("count-32", "count-16"):
        return {"max-bit-width": int(mode.split("-")[1])}, []
    return {}, [f'counter-mode = "{mode}" selects microchip,rtc-g1 (calendar RTC), not the counter variant']
