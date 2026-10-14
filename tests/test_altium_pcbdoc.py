"""Altium .PcbDoc reader against the real design files (DTX_DESIGN_FILES)."""
import math
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.readers.altium_pcbdoc import AltiumPcbDocReader

BOARDS = {
    "same54_xpro": ("same54_xpro/CAD_Source_R11/SAM_E54_Xplained_Pro_R11.PcbDoc", 255, 296, 1251),
    "sg41_cult": ("pic32cx_sg41_cult/PIC32CX_SGxx_CULT_R5_CAD/PIC32CX SG_CUltra_R5.PcbDoc", 281, 322, 1181),
}


@pytest.fixture(scope="module")
def netlists(design_files):
    pytest.importorskip("olefile")
    out = {}
    for key, (rel, *_rest) in BOARDS.items():
        path = Path(design_files) / rel
        if not path.is_file():
            pytest.skip(f"design file missing: {path}")
        out[key] = AltiumPcbDocReader().read(path)
    return out


def test_plugin_registered():
    assert any(r.name == "altium_pcbdoc" for r in plugins.board_readers()) or \
        plugins.register("board_reader", AltiumPcbDocReader())
    assert plugins.get_board_reader("x/Board.PcbDoc") is not None


@pytest.mark.parametrize("key", list(BOARDS))
def test_counts(netlists, key):
    _, nets, comps, pads = BOARDS[key]
    n = netlists[key]
    assert (len(n.nets), len(n.components), len(n.pads)) == (nets, comps, pads)
    assert n.units == "mm"


def test_connector_pins_and_positions(netlists):
    e54, sg41 = netlists["same54_xpro"], netlists["sg41_cult"]
    j401, j602 = e54.components["J401"], sg41.components["J602"]
    # besides the 20 numbered pins the footprints carry unnumbered ("0") mechanical pads
    for n, d in ((e54, "J401"), (sg41, "J602")):
        numbered = sorted(int(p.number) for p in n.pads_of(d) if p.number != "0")
        assert numbered == list(range(1, 21))
    # EXT1 of both boards sits at the same place (CONFIGURATOR.md: 8597.64 / 6390.55 mil)
    assert j401.x == pytest.approx(8597.64 * 0.0254, abs=0.01)
    assert j401.y == pytest.approx(6390.55 * 0.0254, abs=0.01)
    assert (j602.x, j602.y) == pytest.approx((j401.x, j401.y), abs=0.01)
    # pad positions lie on the connector
    for p in e54.pads_of("J401"):
        assert math.hypot(p.x - j401.x, p.y - j401.y) < 30
    assert e54.net_of("J401", "11") is not None


def test_silkscreen_texts_present(netlists):
    for n in netlists.values():
        assert any(t.layer == "top_silk" for t in n.texts)
        assert any(t.layer == "bottom_silk" for t in n.texts)
