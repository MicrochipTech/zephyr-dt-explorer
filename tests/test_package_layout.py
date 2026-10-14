"""Package geometry: pure tests plus the real SAME54 packages (needs DTX_RTOS4_WS)."""
from __future__ import annotations

import pytest

from dt_explorer.core import package_layout as pl

QFP = [str(i) for i in range(1, 129)]


def test_qfp128_pin1_left_top():
    lay = pl.edge_layout(128, QFP)
    p1 = lay.pins["1"]
    assert p1.side == "left" and p1.x == 0
    left = [p for p in lay.pins.values() if p.side == "left"]
    assert p1.y == min(p.y for p in left)
    assert p1.y == 1      # first pitch below the top edge of the body


def test_qfp128_32_per_side():
    lay = pl.edge_layout(128, QFP)
    for side in ("left", "bottom", "right", "top"):
        assert sum(1 for p in lay.pins.values() if p.side == side) == 32
    assert lay.per_side == 32 and len(lay.pins) == 128


def test_qfp128_counter_clockwise():
    P = pl.edge_layout(128, QFP).pins
    assert P["32"].side == "left" and P["32"].y > P["1"].y
    assert P["33"].side == "bottom"
    assert P["33"].x == min(p.x for p in P.values() if p.side == "bottom")
    assert P["64"].x == max(p.x for p in P.values() if p.side == "bottom")
    assert P["65"].side == "right" and P["65"].y == max(p.y for p in P.values() if p.side == "right")
    assert P["96"].y == min(p.y for p in P.values() if p.side == "right")
    assert P["97"].side == "top" and P["97"].x == max(p.x for p in P.values() if p.side == "top")
    assert P["128"].side == "top" and P["128"].x == min(p.x for p in P.values() if p.side == "top")


def test_pins_inside_body_outline():
    lay = pl.edge_layout(128, QFP)
    b = lay.body
    assert all(0 <= p.x <= b.w and 0 <= p.y <= b.h for p in lay.pins.values())


def test_exposed_pad_goes_to_extras():
    lay = pl.layout_for("QFN", 32, [str(i) for i in range(1, 33)] + ["33"])
    assert "33" in lay.extras and "33" not in lay.pins and len(lay.pins) == 32


def test_bad_input():
    with pytest.raises(ValueError):
        pl.edge_layout(30, [])
    with pytest.raises(ValueError):
        pl.layout_for("SOT", 5, [])


def test_bga_a1_top_left_and_jedec_gap():
    names = [f"{r}{c}" for r in "ABCDEFGHJK" for c in range(1, 5)]    # no row I
    lay = pl.grid_layout(names)
    a1 = lay.pins["A1"]
    assert (a1.x, a1.y) == (1, 1)
    assert a1.x == min(p.x for p in lay.pins.values()) and a1.y == min(p.y for p in lay.pins.values())
    assert lay.rows == list("ABCDEFGHJK") and lay.cols == [1, 2, 3, 4]
    assert lay.pins["J1"].y == lay.pins["H1"].y + 1


def test_bga_rows_skip_jedec_letters():
    lay = pl.grid_layout(["A1", "H1", "J1", "N1", "P1", "R1"])
    assert lay.rows == list("ABCDEFGHJKLMNPR")


def test_real_packages(rtos4_build, capsys):
    from dt_explorer.core import pins
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.core import plugins
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    plugins.register_provider(MicrochipAtdfProvider)    # other tests clear the registry
    dev, _ = pins.load_device(read_build_info(rtos4_build("apps/lab1/build_same54_xpro")))
    pk = {p.name: p for p in dev.packages}
    q = pk["TQFP128"]
    lay = pl.layout_for(q.kind, q.pin_count, q.pins)
    assert lay.per_side == 32 and not lay.extras
    b = pk["TFBGA120"]
    g = pl.layout_for(b.kind, b.pin_count, b.pins)
    rows = sorted({n[0] for n in b.pins})
    cols = sorted({int(n[1:]) for n in b.pins})
    with capsys.disabled():
        print(f"\nTFBGA120: rows={g.rows} ({len(g.rows)}), cols={g.cols} ({len(g.cols)}), "
              f"balls={len(g.pins)}, extras={sorted(g.extras)}")
    assert g.rows == rows and g.cols == list(range(cols[0], cols[-1] + 1))
    assert (g.pins["A1"].x, g.pins["A1"].y) == (1, 1)
    assert len(g.pins) + len(g.extras) == len(b.pins)
