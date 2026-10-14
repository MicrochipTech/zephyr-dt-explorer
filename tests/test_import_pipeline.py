"""P7.3: import pipeline (BoardNetlist + DeviceModel packages -> board YAML)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dt_explorer.core.board import BoardNetlist, Component, Pad, Text
from dt_explorer.core.device import Package
from dt_explorer.importers import pipeline

# --- synthetic board (no environment variables) ----------------------------------------


def synthetic():
    """MCU U1 (20 pins), debugger U2 (24 pins, no fit), EXT header J1, series part R1, 3-pad SW1."""
    nl = BoardNetlist(source="synth.PcbDoc")
    pkg_pins = {str(i): f"PA{i:02d}" for i in range(1, 21)}
    nl.components["U1"] = Component("U1", "QFN20")
    nl.components["U2"] = Component("U2", "QFN24")
    nl.components["J1"] = Component("J1", "HDR_2x10", x=100.0, y=50.0)
    nl.components["R1"] = Component("R1", "0603")
    nl.components["SW1"] = Component("SW1", "SW3")
    nl.components["J2"] = Component("J2", "HDR_1x10", x=10.0, y=10.0)
    nets = {  # header pin -> net
        "1": "EXT1_ID", "2": "GND", "3": "PA03", "4": "PA04", "5": "PA05", "6": "PA06",
        "7": "SWNET", "8": "NOWHERE", "9": "PA09", "10": "PA10", "11": "SDA_NET", "12": "PA12",
        "13": "PA13", "14": "PA14", "15": "PA15", "16": "PA16", "17": "PA17", "18": "PA18",
        "19": "GND", "20": "VCC_P3V3"}
    for i in range(1, 21):
        nl.pads.append(Pad("U1", str(i), f"PA{i:02d}"))   # bare net per MCU pin
    for i in range(1, 25):
        nl.pads.append(Pad("U2", str(i), f"DBG{i}"))
    for pin, net in nets.items():
        nl.pads.append(Pad("J1", pin, net, x=100.0 + int(pin), y=50.0))
    nl.pads.append(Pad("J1", "0", None))                   # mounting pads are ignored
    nl.pads.append(Pad("J1", "0", None))
    nl.pads += [Pad("R1", "1", "SDA_NET"), Pad("R1", "2", "PA11")]          # series part
    nl.pads += [Pad("SW1", "1", "SWNET"), Pad("SW1", "2", "X1"), Pad("SW1", "3", "X2")]  # 3 pads
    for i in range(1, 11):                                  # 10-pad header that is no known type
        nl.pads.append(Pad("J2", str(i), f"MISC{i}"))
    nl.texts += [Text("EXT1", 110.0, 60.0, "top_silk"), Text("EXT9", 500.0, 500.0, "top_silk"),
                 Text("hello", 110.0, 61.0, "top_silk")]
    return nl, [Package("QFN20", "QFN", 20, pkg_pins), Package("QFN16", "QFN", 16,
                {str(i): f"PB{i:02d}" for i in range(1, 17)})]


def test_synthetic_pipeline():
    nl, pkgs = synthetic()
    res = pipeline.import_board(nl, pkgs, board="synth_board", source_package="Synth_R3")
    assert res.mcu == "U1" and res.mcu_package == "QFN20"
    assert [c.designator for c in res.connectors] == ["J1"]       # J2 not a known type
    c = res.connectors[0]
    assert (c.type, c.name, c.score >= 0.5) == ("xplained_pro_ext", "EXT1", True)
    t = c.traces
    assert t["3"].pads == ["PA03"]
    assert t["11"].pads == ["PA11"] and t["11"].via == ["R1"]      # through the series resistor
    assert t["7"].pads == [] and "SW1 (3 pads)" in t["7"].gap      # 3-pad part: reported gap
    assert t["8"].pads == [] and "ends without reaching" in t["8"].gap
    assert any("pin 7" in g for g in res.gaps) and any("pin 8" in g for g in res.gaps)
    assert not any("pin 1 " in g for g in res.gaps)                # ID is not a gap
    # 14 of 16 signal pins traced (7, 8 missing) -> still >= 0.75, name fits: reviewed
    assert c.status == "reviewed"
    d = res.data
    assert d["schema_version"] == 1 and d["board"] == "synth_board"
    assert d["source"]["package"] == "Synth_R3" and d["source"]["revision"] == "3"
    assert d["connectors"][0]["reviewed"] == "builder"
    assert d["connectors"][0]["pins"]["11"] == {"role": "I2C_SDA", "pad": "PA11",
                                                "net": "SDA_NET", "via": ["R1"]}
    assert "0" not in d["connectors"][0]["pins"]


def test_unnamed_connector_stays_suggested():
    nl, pkgs = synthetic()
    nl.texts = [t for t in nl.texts if t.text != "EXT1"]
    res = pipeline.import_board(nl, pkgs, board="b")
    c = res.connectors[0]
    assert c.name is None and c.status == "suggested" and "no silkscreen name" in c.reason
    assert "reviewed" not in res.data["connectors"][0]


def test_wrong_name_type_stays_suggested():
    nl, pkgs = synthetic()
    nl.texts = [Text("mikroBUS", 110.0, 60.0, "top_silk")]
    res = pipeline.import_board(nl, pkgs, board="b")
    assert res.connectors[0].status == "suggested" and "does not fit type" in res.connectors[0].reason


def test_mcu_match_uses_package_not_pad_count():
    nl, pkgs = synthetic()                                  # U2 has more pads (24) than U1 (20)
    assert pipeline.find_mcu(nl, pkgs)[0] == "U1"
    assert pipeline.find_mcu(nl, [pkgs[1]]) is None         # nothing fits QFN16


def test_yaml_roundtrip(tmp_path):
    nl, pkgs = synthetic()
    res = pipeline.import_board(nl, pkgs, board="b")
    out = tmp_path / "b.yaml"
    pipeline.write_yaml(res, out)
    assert yaml.safe_load(out.read_text(encoding="utf-8")) == res.data


# --- real boards (DTX_DESIGN_FILES, DTX_RTOS4_WS) -------------------------------------------

PINS = [str(i) for i in range(3, 19)]
# docs/CONFIGURATOR.md, "Checked with real design files"; "-" = gap
XPRO = {
    "EXT1": "PB04 PB05 PA06 PA07 PB08 PB09 PB07 PA27 PA22 PA23 PA05 PA04 PB28 PB27 PB29 PB26",
    "EXT2": "PB00 PA03 PB01 PB06 PB14 PB15 PD00 PB02 PD08 PD09 PB17 PB16 PC06 PC04 PC07 PC05",
    "EXT3": "PC02 PC03 PC01 PC10 PD10 PD11 PC30 PC31 PD08 PD09 PC23 PC22 PC14 PC04 PC07 PC05",
}
SG41 = {
    "EXT1": "PB08 PB09 PD10 PD11 - - PD12 PB05 PD09 PD08 PC13 PC12 PC24 PB24 PC25 -",
    "EXT2": "PB08 PB09 PC05 PC07 - - PB01 PC06 PD09 PD08 PB30 PB31 PA06 PB24 PC25 -",
}
SG41_GAP_NETS = {("EXT1", "7"): "TCC4/WO0", ("EXT1", "8"): "TCC4/WO1", ("EXT1", "18"): "NetJ602_18",
                 ("EXT2", "7"): "TCC4/WO0", ("EXT2", "18"): "NetJ603_18"}
ATDF = {
    "same54": "sam/sam_d5x_e5x/atsame54/atdf/ATSAME54P20A.atdf",
    "sg41": "pic32c/pic32cx_sg/pic32cx_sg41/atdf/PIC32CX1025SG41128.atdf",
}


def table(s):
    return dict(zip(PINS, [None if v == "-" else v for v in s.split()]))


@pytest.fixture(scope="module")
def results(design_files, rtos4_ws):
    pytest.importorskip("olefile")
    packs = Path(rtos4_ws) / "modules/hal/microchip/packs"
    spec = {
        "same54_xpro": ("same54_xpro/CAD_Source_R11/SAM_E54_Xplained_Pro_R11.PcbDoc", ATDF["same54"]),
        "pic32cx_sg41_cult": ("pic32cx_sg41_cult/PIC32CX_SGxx_CULT_R5_CAD/PIC32CX SG_CUltra_R5.PcbDoc",
                              ATDF["sg41"]),
    }
    out = {}
    for board, (pcb, atdf) in spec.items():
        pcb, atdf = Path(design_files) / pcb, packs / atdf
        if not pcb.is_file() or not atdf.is_file():
            pytest.skip(f"missing {pcb} or {atdf}")
        out[board] = pipeline.import_files(pcb, atdf, board=board)
    return out


def pad_table(res, name):
    c = next(c for c in res.data["connectors"] if c["name"] == name)
    return {p: c["pins"][p]["pad"] for p in PINS}


@pytest.mark.parametrize("board,ref", [("same54_xpro", XPRO), ("pic32cx_sg41_cult", SG41)])
def test_connectors_match_documented_tables(results, board, ref, capsys):
    res = results[board]
    lines = [f"{board}: side-by-side, doc (CONFIGURATOR.md) vs import"]
    for name, row in ref.items():
        got, want = pad_table(res, name), table(row)
        for p in PINS:
            lines.append(f"{name} pin {p:>2}: doc {want[p] or '-':5} import {got[p] or '-':5} "
                         f"{'ok' if want[p] == got[p] else 'DIFF'}")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    for name, row in ref.items():
        assert pad_table(res, name) == table(row), name
    assert res.mcu_package == "TQFP128" and res.mcu_fit == 1.0
    assert sorted(c["name"] for c in res.data["connectors"] if c["name"] and
                  c["name"].startswith("EXT")) == sorted(ref)


def test_sg41_gaps_reported(results):
    res = results["pic32cx_sg41_cult"]
    for (name, pin), net in SG41_GAP_NETS.items():
        c = next(c for c in res.data["connectors"] if c["name"] == name)
        assert c["pins"][pin]["net"] == net and c["pins"][pin]["gap"]
        assert any(f"pin {pin} " in g and net in g for g in res.gaps), (name, pin)


def test_connector_names_and_status(results):
    for board, names in (("same54_xpro", {"EXT1", "EXT2", "EXT3"}),
                         ("pic32cx_sg41_cult", {"EXT1", "EXT2"})):
        conns = results[board].data["connectors"]
        assert {c["name"] for c in conns} == names
        assert all(c["status"] == "reviewed" and c["reviewed"] == "builder" for c in conns)


def test_committed_yaml_is_current(results):
    boards = Path(pipeline.__file__).resolve().parent.parent / "boards"
    for board, res in results.items():
        f = boards / f"{board}.yaml"
        assert f.is_file(), f
        assert yaml.safe_load(f.read_text(encoding="utf-8")) == res.data
