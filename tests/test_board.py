"""BoardNetlist queries on a small synthetic netlist (no design files needed)."""
from dt_explorer.core.board import BoardNetlist, Component, Pad, Text


def make() -> BoardNetlist:
    return BoardNetlist(
        nets=["SDA", "SCL", "GND", "NC1"],
        components={
            "U1": Component("U1", "QFP", "MCU"),
            "J1": Component("J1", "HDR_1x3", "header", x=10, y=20),
            "R1": Component("R1", "0402", "0R"),
        },
        pads=[
            Pad("U1", "5", "SDA"), Pad("U1", "6", "SCL"), Pad("U1", "7", None),
            Pad("J1", "1", "SDA"), Pad("J1", "2", "SCL"), Pad("J1", "3", "GND"),
            Pad("R1", "1", "SDA"), Pad("R1", "2", "GND"),
            Pad(None, "TP", "GND"),
        ],
        texts=[Text("EXT1", 10, 25, "top_silk", 1.0, 0.0)],
    )


def test_pads_of():
    n = make()
    assert [p.number for p in n.pads_of("J1")] == ["1", "2", "3"]
    assert n.pads_of("nope") == []


def test_net_of():
    n = make()
    assert n.net_of("J1", "2") == "SCL"
    assert n.net_of("U1", "7") is None          # unconnected pad
    assert n.net_of("U1", "99") is None         # unknown pad
    assert n.net_of("X9", "1") is None          # unknown component


def test_pin_nets():
    assert make().pin_nets("R1") == {"1": "SDA", "2": "GND"}


def test_components_on_net():
    n = make()
    assert n.components_on_net("SDA") == ["J1", "R1", "U1"]
    assert n.components_on_net("GND") == ["J1", "R1"]     # free pad has no component
    assert n.components_on_net("NC1") == []
    assert len(n.pads_on_net("GND")) == 3


def test_index_follows_new_pads():
    n = make()
    assert n.components_on_net("NC1") == []
    n.pads.append(Pad("R1", "3", "NC1"))
    assert n.components_on_net("NC1") == ["R1"]
