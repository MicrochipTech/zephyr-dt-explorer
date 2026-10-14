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


def _gclk_choice(tables: dict, channel: int):
    """Pick the clock macro for a GCLK peripheral channel (the *_CORE one if several)."""
    cands = tables["gclk_periph"].get(str(channel)) or []
    if len(cands) == 1:
        return cands[0]
    core = [c for c in cands if c[0].endswith("_CORE")]
    return core[0] if len(core) == 1 else None


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
    gname = _gclk_choice(t, gch)
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
    gname = _gclk_choice(t, gch) if gch is not None else None
    mname = None
    if mclk is not None and t["mclk_bus"].get(mclk[0]) is not None:
        mname = t["mclk_periph"].get(f"{t['mclk_bus'][mclk[0]]}:{mclk[1]}")
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
