"""Board view and connector roles in the port report (P7.4): synthetic board files, no environment variables."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
import yaml

from dt_explorer.core import board_desc as bd
from dt_explorer.core import port_report as pr
from dt_explorer.ui import board_view as bv


def _board(name, title=None, pads=None, conn_type="hdr20", conn_name="J1", extra_conn=None):
    pads = pads or {}
    roles = {"1": "ID", "2": "GND", "3": "SPI_MOSI", "4": "SPI_MISO", "5": "SPI_SCK", "6": "GPIO1"}
    pins = {k: {"role": r, "pad": pads.get(k), "net": f"n{k}"} for k, r in roles.items()}
    pins["1"]["gap"] = "no trace"
    conns = [{"name": conn_name, "designator": "X1", "type": conn_type, "status": "reviewed", "pins": pins}]
    if extra_conn:
        conns.append(extra_conn)
    d = {"schema_version": 1, "board": name, "mcu": {"soc": "soc1"}, "connectors": conns}
    if title:
        d["title"] = title
    return d


def _write(tmp_path, *boards):
    for b in boards:
        (tmp_path / f"{b['board']}.yaml").write_text(yaml.safe_dump(b), encoding="utf-8")
    return [tmp_path]


def _usage(**pad_nodes):
    uses = {pad: [NS(node=f"/soc/{n}@1000", source="pinctrl", function="X.PAD0", state="default")
                  for n in nodes] for pad, nodes in pad_nodes.items()}
    return NS(uses=uses)


def test_load_and_title(tmp_path):
    dirs = _write(tmp_path, _board("brd_a", "Board A", {"3": "Q1"}), _board("brd_b"))
    a = bd.load_board("brd_a", dirs)
    assert a.title == "Board A" and a.soc == "soc1"
    assert a.connector("J1").pins["3"].pad == "Q1"
    assert a.connector("J1").pins["2"].pad is None
    assert list(a.connector("J1").pins) == ["1", "2", "3", "4", "5", "6"]
    assert bd.load_board("brd_b", dirs).title == "brd_b"          # title falls back to the board name
    assert bd.load_board("nothing", dirs) is None and bd.load_board(None, dirs) is None


def test_bad_schema_is_reported_with_file_name(tmp_path):
    (tmp_path / "old.yaml").write_text("schema_version: 9\nboard: old\n", encoding="utf-8")
    with pytest.raises(ValueError, match="old.yaml"):
        bd.load_board("old", [tmp_path])


def test_short_role():
    assert bd.short_role("SPI_MOSI") == "MOSI"
    assert bd.short_role("GPIO1") == "GPIO1"
    assert bd.short_role("X_SS_B") == "SS_B"


def test_connector_role_lines(tmp_path):
    src = _board("src_b", "Src Board", {"3": "P10", "4": "P11", "5": "P12", "6": "P13"})
    tgt = _board("tgt_b", "Tgt Board", {"3": "Q10", "4": "Q11", "6": "Q13"})      # pin 5 has no pad
    dirs = _write(tmp_path, src, tgt)
    su = _usage(P10=["spi"], P11=["spi"], P12=["spi"])                  # P13 free, not reported
    tu = _usage(Q11=["led"])
    lines = pr.connector_role_lines("src_b", "tgt_b", su, tu, dirs)
    assert lines[0].startswith("J1 pin 3 (MOSI): P10 on Src Board → Q10 on Tgt Board")
    assert "[used by spi@1000]" in lines[0]
    assert "Q11 on Tgt Board  (already used in the target build by led@1000)" in lines[1]
    assert lines[2].startswith("J1 pin 5 (SCK): P12 on Src Board → not available on Tgt Board "
                               "(net n5 does not reach the SoC)")
    assert len(lines) == 3


def test_no_such_connector_and_other_type(tmp_path):
    src = _board("src_b", pads={"3": "P10"})
    tgt = _board("tgt_b", pads={"3": "Q10"}, conn_name="J9")                 # other name
    tgt2 = _board("tgt_c", pads={"3": "Q10"}, conn_type="other_type")        # same name, other type
    dirs = _write(tmp_path, src, tgt, tgt2)
    su = _usage(P10=["spi"])
    for t in ("tgt_b", "tgt_c"):
        (line,) = pr.connector_role_lines("src_b", t, su, _usage(), dirs)
        assert "no such connector (J1, hdr20) on " + t in line


def test_missing_descriptions(tmp_path):
    dirs = _write(tmp_path, _board("src_b"))
    assert pr.connector_role_lines("src_b", "none_b", _usage(), _usage(), dirs) == \
        ["no board description for none_b: connector roles not reported"]
    assert pr.connector_role_lines("a", "b", _usage(), _usage(), dirs) is None


def test_report_section_only_when_computed():
    rep = pr.PortReport(source="s", target="t")
    assert "7. Connector roles" not in "\n".join(rep.lines())
    rep.connector_roles = []
    assert ("7. Connector roles of the used pads", ["none"]) in rep.sections()
    rep.connector_roles = ["J1 pin 3 (MOSI): a → b"]
    titles = [t for t, _ in rep.sections()]
    assert titles[-1] == "7. Connector roles of the used pads"


def test_pin_states(tmp_path):
    dirs = _write(tmp_path, _board("brd_a", pads={"3": "P10", "4": "P11", "5": "P12"}))
    conn = bd.load_board("brd_a", dirs).connector("J1")
    usage = _usage(P10=["spi"], P11=["a", "b"])
    st = bv.pin_states(conn, usage, {"P12": ["spi9 SCK (draft)"]})
    assert st["3"]["state"] == "used"
    assert st["4"]["state"] == "conflict"
    assert st["5"]["state"] == "draft" and st["5"]["users"][0]["path"] == "draft"
    assert st["6"]["state"] == "unavailable"            # signal role, no pad
    assert st["1"]["state"] == "other" and st["2"]["state"] == "other"
    assert bv.pin_states(conn, usage)["5"]["state"] == "free"


def test_connector_svg(tmp_path):
    dirs = _write(tmp_path, _board("brd_a", pads={"3": "P10"}))
    conn = bd.load_board("brd_a", dirs).connector("J1")
    svg = bv.connector_svg(conn, bv.pin_states(conn, _usage(P10=["spi"])), selected="3")
    assert svg.count('class="bpin"') == 6
    assert "J1:3" in svg and "P10" in svg and ">MOSI<" in svg
    assert svg.count('rx="0"') == 1                      # pin 1 marked by square corners
    assert bv._rows_of(["1", "2", "3", "4", "5", "6"]) == [["1", "3", "5"], ["2", "4", "6"]]
    assert bv._rows_of(["1", "2", "3"]) == [["1", "2", "3"]]


# --- P8.12: mikroBUS and Arduino Uno R3 connectors ---------------------------------------------

MB_ROLES = ["AN", "RST", "CS", "SCK", "MISO", "MOSI", "3V3", "GND",
            "PWM", "INT", "RX", "TX", "SCL", "SDA", "5V", "GND"]


def _mikrobus_conn(tmp_path):
    pins = {str(i + 1): {"role": r, "pad": None if r in ("3V3", "5V", "GND") else f"P{i:02d}", "net": f"n{i}"}
            for i, r in enumerate(MB_ROLES)}
    d = {"schema_version": 1, "board": "brd_m", "mcu": {"soc": "soc1"},
         "connectors": [{"name": "mikroBUS", "designator": "J900/J901", "type": "mikrobus", "pins": pins}]}
    return bd.load_board("brd_m", _write(tmp_path, d)).connector("mikroBUS")


def _arduino_conn(tmp_path):
    from dt_explorer.connectors import arduino_uno_r3 as ard
    roles = ard.ROLES
    keys = [k for _kind, ks in ard.HEADER_ROWS for k in ks]
    pins = {k: {"role": roles[k], "pad": None if roles[k] in ("3V3", "5V", "GND", "VIN", "IOREF") else f"P{i}",
                "net": f"n{i}"} for i, k in enumerate(keys)}
    d = {"schema_version": 1, "board": "brd_a2", "mcu": {"soc": "soc1"},
         "connectors": [{"name": "Arduino", "type": "arduino_uno_r3", "pins": pins}]}
    return bd.load_board("brd_a2", _write(tmp_path, d)).connector("Arduino")


def test_mikrobus_is_drawn_as_two_rows_of_eight(tmp_path):
    conn = _mikrobus_conn(tmp_path)
    assert bv._layout(conn) == [("Left row (3.3 V)", [str(i) for i in range(1, 9)]),
                                ("Right row (5 V)", [str(i) for i in range(9, 17)])]
    sts = bv.pin_states(conn, _usage(P00=["spi"]))
    svg = bv.connector_svg(conn, sts)
    assert svg.count('class="bpin"') == 16 and "Left row (3.3 V)" in svg and "Right row (5 V)" in svg
    for role in ("AN", "RST", "CS", "SCK", "MISO", "MOSI", "PWM", "INT", "RX", "TX", "SCL", "SDA", "3V3", "5V"):
        assert f">{role}<" in svg
    assert svg.count('rx="0"') == 2                       # pin 1 of each header is square
    assert sts["1"]["state"] == "used" and sts["2"]["state"] == "free"
    assert sts["7"]["state"] == sts["8"]["state"] == sts["15"]["state"] == "other"   # 3V3, GND, 5V: no pad expected


def test_arduino_is_drawn_as_four_header_rows(tmp_path):
    conn = _arduino_conn(tmp_path)
    layout = bv._layout(conn)
    assert [cap for cap, _k in layout] == ["Power", "Analog", "Digital low", "Digital high"]
    assert [len(k) for _c, k in layout] == [7, 6, 8, 10]
    sts = bv.pin_states(conn, None)
    svg = bv.connector_svg(conn, sts, selected="D13")
    assert svg.count('class="bpin"') == 31 and 'stroke-width="2.4"' in svg
    assert "Arduino:D13" in svg and ">SCK<" in svg and ">SDA<" in svg and ">RX<" in svg
    for key in ("3V3", "5V", "GND", "GND2", "GND3", "VIN", "IOREF"):
        assert sts[key]["state"] == "other", key
    assert sts["A0"]["state"] == "free" and sts["RESET"]["state"] == "free"


def test_unknown_pins_of_a_multi_header_connector_are_still_drawn(tmp_path):
    conn = _mikrobus_conn(tmp_path)
    conn.pins["X1"] = bd.ConnPin("X1", "GPIO", "P99")
    layout = bv._layout(conn)
    assert layout[-1] == ("Other pins", ["X1"])
    assert bv.connector_svg(conn, bv.pin_states(conn, None)).count('class="bpin"') == 17


def test_other_types_keep_their_layout(tmp_path):
    dirs = _write(tmp_path, _board("brd_a", pads={"3": "P10"}))
    conn = bd.load_board("brd_a", dirs).connector("J1")
    assert bv._layout(conn) is None
