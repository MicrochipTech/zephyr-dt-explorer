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
