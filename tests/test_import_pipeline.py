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


def test_unavailable_pad_is_marked(tmp_path):
    """Footprint positions that are NC on the chosen SoC (ATDF pad "NC") make connector pins unavailable."""
    nl, pkgs = synthetic()
    pins = dict(pkgs[0].pins)
    pins["5"] = "NC"                      # header pin 5 (net PA05) reaches footprint position 5
    pins["6"] = "NC"
    nl.pads = [p for p in nl.pads if not (p.component == "R1")]
    nl.pads += [Pad("R1", "1", "SDA_NET"), Pad("R1", "2", "PA11")]
    res = pipeline.import_board(nl, [Package("QFN20", "QFN", 20, pins)], board="b", soc="SOC_X")
    d = res.data["connectors"][0]["pins"]
    for pin in ("5", "6"):
        assert d[pin]["available"] is False and d[pin]["pad"] is None
        assert f"position {pin}" in d[pin]["unavailable_reason"] and "SOC_X" in d[pin]["unavailable_reason"]
        assert "gap" not in d[pin]                      # not a tracing gap
    assert "available" not in d["3"] and d["3"]["pad"] == "PA03"      # unaffected pins unchanged
    assert any("pin 5 (GPIO1)" in r and "not available on this MCU" in r for r in res.report)
    assert any(r.startswith("NC positions of QFN20 on SOC_X") and "5, 6" in r for r in res.report)
    assert not any("pin 5 " in g for g in res.gaps)
    # 12 of 16 signal pins still traced = 75 %: the connector stays reviewed
    assert res.connectors[0].status == "reviewed"
    out = tmp_path / "u.yaml"
    pipeline.write_yaml(res, out)
    assert yaml.safe_load(out.read_text(encoding="utf-8")) == res.data


def test_no_nc_positions_changes_nothing():
    nl, pkgs = synthetic()
    res = pipeline.import_board(nl, pkgs, board="b")
    assert all("available" not in e for c in res.data["connectors"] for e in c["pins"].values())
    assert any(r.startswith("NC positions") and r.endswith("none") for r in res.report)


def test_yaml_roundtrip(tmp_path):
    nl, pkgs = synthetic()
    res = pipeline.import_board(nl, pkgs, board="b")
    out = tmp_path / "b.yaml"
    pipeline.write_yaml(res, out)
    assert yaml.safe_load(out.read_text(encoding="utf-8")) == res.data


# --- P8.12: mikroBUS and Arduino headers (synthetic) -----------------------------------------

def shield(*, left="J10", right="J11", mikro_texts=True, arduino="none", with_mikro=True, with_arduino=True,
           both_33=False):
    """MCU U1 (20 pads PA01..PA20), a mikroBUS socket (two 1x8 parts) and an Arduino shield (four parts)."""
    nl = BoardNetlist(source="shield.PcbDoc")
    pkg = {str(i): f"PA{i:02d}" for i in range(1, 21)}
    nl.components["U1"] = Component("U1", "QFN20")
    for i in range(1, 21):
        nl.pads.append(Pad("U1", str(i), f"PA{i:02d}"))

    def part(des, nets, x, y, fp):
        nl.components[des] = Component(des, fp, x=x, y=y, rotation=90.0)
        for pin, net in nets.items():
            nl.pads.append(Pad(des, str(pin), net, x=x, y=y + int(pin)))

    def pa(i):
        return f"PA{(i - 1) % 20 + 1:02d}"

    if with_mikro:
        part(left, {1: pa(1), 2: pa(2), 3: pa(3), 4: pa(4), 5: pa(5), 6: pa(6),
                    7: "VCC_3P3V", 8: "GND"}, 100.0, 50.0, "HDR_1x8")
        part(right, {1: pa(7), 2: pa(8), 3: pa(9), 4: pa(10), 5: pa(11), 6: pa(12),
                     7: "VCC_3P3V" if both_33 else "VCC_5P0V", 8: "GND"}, 122.9, 50.0, "HDR_1x8")
        if mikro_texts:
            nl.texts += [Text("mikro", 111.0, 56.0, "top_silk"), Text("BUS", 111.0, 53.0, "top_silk")]
    if with_arduino:
        part("J20", {1: None, 2: "VCC_3P3V", 3: "RESET_N", 4: "VCC_3P3V", 5: "VCC_5P0V", 6: "GND", 7: "GND",
                     8: None}, 300.0, 50.0, "HDR_1x8")
        part("J21", {i: pa(i) for i in range(1, 7)}, 300.0, 80.0, "HDR_1x6")
        part("J22", {i: pa(6 + i) for i in range(1, 9)}, 340.0, 50.0, "HDR_1x8")
        part("J23", {1: pa(15), 2: pa(16), 3: pa(17), 4: pa(18), 5: pa(19), 6: pa(20), 7: "GND",
                     8: "AREF_N", 9: pa(1), 10: pa(2)}, 340.0, 90.0, "HDR_1x10")
        if arduino == "silk":
            nl.texts.append(Text("ARDUINO", 320.0, 70.0, "top_silk"))
        elif arduino == "board":
            nl.texts += [Text("Shield for Arduino", 10.0, 10.0, "other"), Text("J20 Power", 300.0, 60.0, "other"),
                         Text("J21 Analog", 300.0, 90.0, "other"), Text("J22 Digital Low", 340.0, 60.0, "other"),
                         Text("J23 Digital High", 340.0, 100.0, "other")]
    return nl, [Package("QFN20", "QFN", 20, pkg)]


def conn_of(res, name):
    return next(c for c in res.data["connectors"] if c["name"] == name)


def test_mikrobus_pair_is_one_connector():
    nl, pkgs = shield(with_arduino=False)
    res = pipeline.import_board(nl, pkgs, board="b")
    assert [c["name"] for c in res.data["connectors"]] == ["mikroBUS"]
    c = conn_of(res, "mikroBUS")
    assert (c["designator"], c["type"], c["status"], c["zephyr_binding"]) == (
        "J10/J11", "mikrobus", "reviewed", "mikro-bus")
    assert list(c["pins"]) == [str(i) for i in range(1, 17)]
    pins = c["pins"]
    assert [pins[k]["role"] for k in ("1", "6", "7", "8", "9", "13", "14", "15", "16")] == [
        "AN", "MOSI", "3V3", "GND", "PWM", "SCL", "SDA", "5V", "GND"]
    assert pins["1"]["pad"] == "PA01" and pins["9"]["pad"] == "PA07" and pins["14"]["pad"] == "PA12"
    assert pins["9"]["part"] == "J11.1" and pins["6"]["part"] == "J10.6"
    assert c["position"]["x"] == 111.45 and c["position"]["y"] == 54.5     # centre of the two parts
    assert not any("J10 " in r or "J11 " in r for r in res.report if "not written" in r)


def test_mikrobus_rows_follow_the_supply_not_the_designator():
    nl, pkgs = shield(left="J99", right="J11", with_arduino=False)     # left (3.3 V) row has the higher number
    c = conn_of(pipeline.import_board(nl, pkgs, board="b"), "mikroBUS")
    assert c["designator"] == "J99/J11" and c["pins"]["1"]["part"] == "J99.1"
    assert c["pins"]["9"]["part"] == "J11.1"


def test_two_halves_with_the_same_supply_are_no_mikrobus():
    nl, pkgs = shield(both_33=True, with_arduino=False)
    res = pipeline.import_board(nl, pkgs, board="b")
    assert res.data["connectors"] == []
    assert any(r.startswith("J10: no connector type") for r in res.report)


def test_mikrobus_without_name_stays_suggested():
    nl, pkgs = shield(mikro_texts=False, with_arduino=False)
    c = pipeline.import_board(nl, pkgs, board="b").data["connectors"][0]
    assert c["name"] is None and c["status"] == "suggested" and "no silkscreen name" in c["reason"]
    assert "reviewed" not in c


def test_split_silkscreen_name_is_joined():
    texts = [Text("mikro", 10.0, 12.0, "top_silk"), Text("BUS", 10.5, 9.0, "top_silk"),
             Text("BUS", 50.0, 9.0, "top_silk"), Text("mikro", 10.0, 12.0, "bottom_silk")]
    joined = pipeline.join_split_names(texts)
    assert [t.text for t in joined[len(texts):]] == ["mikroBUS"]
    assert joined[-1].layer == "top_silk" and joined[-1].y == 10.5


def test_arduino_headers_merge_into_one_connector():
    nl, pkgs = shield(with_mikro=False, arduino="silk")
    res = pipeline.import_board(nl, pkgs, board="b")
    c = conn_of(res, "ARDUINO")
    assert (c["designator"], c["type"], c["status"], c["zephyr_binding"]) == (
        "J20/J21/J22/J23", "arduino_uno_r3", "reviewed", "arduino-header-r3")
    pins = c["pins"]
    assert list(pins)[:7] == ["IOREF", "RESET", "3V3", "5V", "GND", "GND2", "VIN"]
    assert len(pins) == 7 + 6 + 8 + 10 and "NC" not in pins
    assert (pins["A0"]["pad"], pins["A5"]["pad"]) == ("PA01", "PA06")
    assert (pins["D0"]["pad"], pins["D1"]["role"], pins["D7"]["pad"]) == ("PA07", "UART_TX", "PA14")
    assert (pins["D8"]["pad"], pins["D13"]["role"], pins["D13"]["pad"]) == ("PA15", "SPI_SCK", "PA20")
    assert (pins["D14"]["role"], pins["D14"]["pad"], pins["D15"]["role"]) == ("I2C_SDA", "PA01", "I2C_SCL")
    assert (pins["GND3"]["role"], pins["AREF"]["role"], pins["AREF"]["pad"]) == ("GND", "AREF", None)
    assert pins["D9"]["part"] == "J23.2" and pins["A3"]["part"] == "J21.4" and pins["VIN"]["net"] is None
    assert pins["RESET"]["gap"] and pins["AREF"]["gap"]
    assert not any("VIN" in g or "IOREF" in g for g in res.gaps)       # not expected on the MCU
    assert any("RESET" in g for g in res.gaps) and any("AREF" in g for g in res.gaps)


def test_arduino_name_evidence_levels():
    nl, pkgs = shield(with_mikro=False, arduino="none")
    c = conn_of(pipeline.import_board(nl, pkgs, board="b"), "Arduino")
    assert c["status"] == "suggested" and "no text on the board names these headers Arduino" in c["reason"]
    nl, pkgs = shield(with_mikro=False, arduino="board")
    c = conn_of(pipeline.import_board(nl, pkgs, board="b"), "Arduino")
    assert c["status"] == "reviewed" and "board text 'Shield for Arduino'" in c["reason"]
    assert "J22 Digital low" in c["reason"]
    # a header text that disagrees with the pin pattern (J22 called Analog) withdraws the evidence
    nl, pkgs = shield(with_mikro=False, arduino="board")
    next(t for t in nl.texts if t.text == "J22 Digital Low").text = "J22 Analog"
    assert conn_of(pipeline.import_board(nl, pkgs, board="b"), "Arduino")["status"] == "suggested"


def test_incomplete_arduino_set_is_not_written():
    nl, pkgs = shield(with_mikro=False, arduino="silk")
    nl.pads = [p for p in nl.pads if p.component != "J23"]
    del nl.components["J23"]
    res = pipeline.import_board(nl, pkgs, board="b")
    assert res.data["connectors"] == []


def test_mikrobus_and_arduino_side_by_side():
    nl, pkgs = shield(arduino="silk")
    res = pipeline.import_board(nl, pkgs, board="b")
    assert sorted(c["name"] for c in res.data["connectors"]) == ["ARDUINO", "mikroBUS"]
    assert not any(r.startswith(("J10", "J11", "J20", "J21", "J22", "J23")) and "not written" in r
                   for r in res.report)


def test_header_helpers():
    from dt_explorer.connectors import arduino_uno_r3 as ard
    assert ard.header_kind({str(i): f"PA{i}" for i in range(1, 7)}) == "analog"
    assert ard.header_kind({str(i): f"PA{i}" for i in range(1, 9)}) == "digital_low"
    assert ard.header_kind({"1": None, "2": "3V3", "3": "RST", "4": "3V3", "5": "5V", "6": "GND", "7": "GND",
                            "8": None}) == "power"
    assert ard.header_kind({str(i): f"PA{i}" for i in range(1, 6)}) is None            # 5 pins
    assert ard.header_kind({"1": "A", "2": "B", "3": "C", "4": "D", "5": "E", "6": "F", "7": "GND", "8": "G",
                            "9": "H", "10": "I"}) == "digital_high"
    assert ard.ROLES["GND2"] == ard.ROLES["GND3"] == "GND"


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
    "sg61": "pic32c/pic32cx_sg/pic32cx_sg61/atdf/PIC32CX1025SG61128.atdf",
}
SG61_PCB = "pic32cx_sg61_cult/PIC32CX_SGxx_CULT_R5_CAD/PIC32CX SG_CUltra_R5.PcbDoc"


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
                         ("pic32cx_sg41_cult", {"EXT1", "EXT2", "Arduino", "mikroBUS"})):
        conns = results[board].data["connectors"]
        assert {c["name"] for c in conns} == names
        assert all(c["status"] == "reviewed" and c["reviewed"] == "builder" for c in conns)


def test_committed_yaml_is_current(results):  # SAME54 and SG41; SG61 below
    boards = Path(pipeline.__file__).resolve().parent.parent / "boards"
    for board, res in results.items():
        f = boards / f"{board}.yaml"
        assert f.is_file(), f
        assert yaml.safe_load(f.read_text(encoding="utf-8")) == res.data


# --- P7.3b: SG61 Curiosity Ultra ---------------------------------------------------------------

@pytest.fixture(scope="module")
def sg61(design_files, rtos4_ws):
    pytest.importorskip("olefile")
    pcb = Path(design_files) / SG61_PCB
    atdf = Path(rtos4_ws) / "modules/hal/microchip/packs" / ATDF["sg61"]
    if not pcb.is_file() or not atdf.is_file():
        pytest.skip(f"missing {pcb} or {atdf}")
    # The BOM text is evidence recorded at import time; reuse the committed wording.
    committed = yaml.safe_load((Path(pipeline.__file__).resolve().parent.parent / "boards"
                                / "pic32cx_sg61_cult.yaml").read_text(encoding="utf-8"))
    return pipeline.import_files(pcb, atdf, board="pic32cx_sg61_cult", mcu_part=committed["mcu"]["part"],
                                 bom_evidence=committed["source"]["bom"])


def test_sg61_matches_sg41_except_unavailable(results, sg61):
    a, b = results["pic32cx_sg41_cult"].data, sg61.data
    assert sg61.mcu == "U401" and sg61.mcu_package == "TQFP128" and b["mcu"]["soc"] == "PIC32CX1025SG61128"
    unavailable = []
    for ca in a["connectors"]:
        cb = next(c for c in b["connectors"] if c["name"] == ca["name"])
        for pin, pa in ca["pins"].items():
            pb = cb["pins"][pin]
            if pb.get("available") is False:
                unavailable.append((ca["name"], pin))
                assert pb["pad"] is None and pb["unavailable_reason"]
            else:
                assert pb == pa, (ca["name"], pin)
    # The shared PCB leaves the MCU pads for PB26-PB29/PC04 (SG41 positions 27, 102-105) unconnected,
    # so no connector pin is affected.
    assert unavailable == []
    assert any(r.startswith("NC positions of TQFP128") and "27, 102, 103, 104, 105" in r for r in sg61.report)


def test_sg61_committed_yaml_is_current(sg61):
    f = Path(pipeline.__file__).resolve().parent.parent / "boards" / "pic32cx_sg61_cult.yaml"
    assert yaml.safe_load(f.read_text(encoding="utf-8")) == sg61.data


# --- P8.12: mikroBUS and Arduino on the real boards ---------------------------------------------

MIKRO_SG41 = {  # key: (role, pad); nets are printed, SCK has no trace
    "1": ("AN", "PC03"), "2": ("RST", "PC18"), "3": ("CS", "PC16"), "4": ("SCK", None),
    "5": ("MISO", "PC25"), "6": ("MOSI", "PB24"), "7": ("3V3", None), "8": ("GND", None),
    "9": ("PWM", "PB16"), "10": ("INT", "PC31"), "11": ("RX", "PC13"), "12": ("TX", "PC12"),
    "13": ("SCL", "PD08"), "14": ("SDA", "PD09"), "15": ("5V", None), "16": ("GND", None),
}


def print_table(conn, capsys, title):
    lines = [title, f"{'key':5} {'role':9} {'pad':6} {'net':14} {'part':7} gap"]
    for key, p in conn["pins"].items():
        lines.append(f"{key:5} {p['role'] or '-':9} {p['pad'] or '-':6} {p['net'] or '-':14} "
                     f"{p.get('part', '-'):7} {p.get('gap', '')}")
    with capsys.disabled():
        print("\n" + "\n".join(lines))


def test_sg41_mikrobus_and_arduino(results, capsys):
    res = results["pic32cx_sg41_cult"]
    mb, ard = conn_of(res, "mikroBUS"), conn_of(res, "Arduino")
    print_table(mb, capsys, "pic32cx_sg41_cult mikroBUS (J900 left row = keys 1-8, J901 right row = keys 9-16)")
    print_table(ard, capsys, "pic32cx_sg41_cult Arduino (J500 power, J501 analog, J504 digital low, J503 digital high)")
    assert (mb["designator"], mb["status"], mb["reviewed"]) == ("J900/J901", "reviewed", "builder")
    assert {k: (p["role"], p["pad"]) for k, p in mb["pins"].items()} == MIKRO_SG41
    assert mb["pins"]["4"]["net"] == "NetJ900_4" and mb["pins"]["4"]["gap"]
    assert mb["pins"]["11"]["via"] == ["R463"] and mb["pins"]["12"]["via"] == ["R462"]
    # the same SG41 pads as on EXT1: SPI MOSI/MISO (16, 17), UART RX/TX (13, 14), I2C SCL/SDA (12, 11)
    ext1 = conn_of(res, "EXT1")["pins"]
    for key, ext in (("6", "16"), ("5", "17"), ("11", "13"), ("12", "14"), ("13", "12"), ("14", "11")):
        assert mb["pins"][key]["pad"] == ext1[ext]["pad"], (key, ext)
    assert (ard["designator"], ard["status"], ard["reviewed"]) == (
        "J500/J501/J504/J503", "reviewed", "builder")
    want = {"A0": "PA03", "A1": "PB06", "A2": "PB07", "A3": "PA04", "A4": "PB02", "A5": "PB03",
            "D0": "PB30", "D1": "PB31", "D2": "PD10", "D3": "PD11", "D4": "PD12", "D5": "PB14", "D6": "PB15",
            "D7": "PC10", "D8": "PD21", "D9": "PB16", "D10": "PC17", "D11": "PB24", "D12": "PC25",
            "D13": "PB25", "D14": "PD09", "D15": "PD08"}
    assert {k: ard["pins"][k]["pad"] for k in want} == want
    assert ard["pins"]["D0"]["role"] == "UART_RX" and ard["pins"]["D13"]["role"] == "SPI_SCK"
    assert ard["pins"]["AREF"]["net"] == "NetJ503_8" and ard["pins"]["AREF"]["gap"]
    assert ard["pins"]["RESET"]["net"] == "RESET_UNO"
    # same pads as on the SG41 EXT connectors: UART RX/TX = EXT2 13/14, SPI and I2C = EXT1
    ext2 = conn_of(res, "EXT2")["pins"]
    for key, ext, src in (("D0", "13", ext2), ("D1", "14", ext2), ("D11", "16", ext1), ("D12", "17", ext1),
                          ("D14", "11", ext1), ("D15", "12", ext1)):
        assert ard["pins"][key]["pad"] == src[ext]["pad"], (key, ext)


def test_sg61_has_the_same_mikrobus_and_arduino(results, sg61):
    for name in ("mikroBUS", "Arduino"):
        assert conn_of(sg61, name) == conn_of(results["pic32cx_sg41_cult"], name)


def test_xpro_has_no_mikrobus_or_arduino(results):
    res = results["same54_xpro"]
    assert {c["type"] for c in res.data["connectors"]} == {"xplained_pro_ext"}
    assert not any("merged" in r for r in res.report)
