import json

import pytest

from dt_explorer.core import draft as D


@pytest.fixture(scope="module")
def index(rtos4_ws):
    return D.BindingIndex([rtos4_ws / "zephyr" / "dts" / "bindings"])


@pytest.mark.parametrize("compat,cls,last", [
    ("atmel,sam0-uart", "uart", "uart-controller.yaml"),
    ("atmel,sam0-i2c", "i2c", "i2c-controller.yaml"),
    ("atmel,sam0-spi", "spi", "spi-controller.yaml"),
    ("microchip,sercom-g1-uart", "uart", "uart-controller.yaml"),
])
def test_class_detection(index, compat, cls, last):
    got, chain = D.detect_class(compat, index)
    print(compat, "->", got, chain)
    assert got == cls
    assert chain[0] == f"{compat}.yaml" and chain[-1] == last


def test_no_controller_base_is_none(index):
    got, chain = D.detect_class("atmel,sam0-gclk", index)
    print("atmel,sam0-gclk ->", got, chain)
    assert got is None
    assert D.detect_class("no,such-compatible", index) == (None, [])


def test_neutral_properties(index):
    neutral = D.neutral_properties("uart-controller.yaml", index)
    n, v = D.split_properties({"current-speed": 115200, "x-vendor-prop": 1}, neutral)
    assert n == {"current-speed": 115200} and v == {"x-vendor-prop": 1}


def test_custom_base_table(tmp_path):
    (tmp_path / "foo-controller.yaml").write_text("properties:\n  speed:\n    type: int\n")
    (tmp_path / "v,foo.yaml").write_text("compatible: v,foo\ninclude: [foo-controller.yaml]\n")
    idx = D.BindingIndex([tmp_path])
    assert D.detect_class("v,foo", idx)[0] is None
    assert D.detect_class("v,foo", idx, {"foo-controller.yaml": "foo"})[0] == "foo"


def sample() -> D.Draft:
    return D.Draft(build="build_x", nodes=[D.DraftNode(
        cls="uart", roles=["TX", "RX"], props={"current-speed": 115200},
        use=D.Use(chosen=["zephyr,console"], aliases=["my-uart"]),
        instance="inst2", pins={"TX": "P1", "RX": "P2"}, vendor_props={"vp": 1},
        compatible="v,uart", label="my_uart")])


def test_round_trip(tmp_path):
    d = sample()
    app, build = tmp_path / "app", tmp_path / "app" / "build_x"
    p = D.save_draft(d, app, build)
    assert p == app / ".dt_explorer" / "build_x.draft.json" and p.is_file()
    assert D.load_draft(app, build) == d
    assert list(json.loads(p.read_text(encoding="utf-8"))) == ["version", "build", "nodes"]
    assert D.load_draft(app, tmp_path / "other") is None
    first = p.read_bytes()
    D.save_draft(D.load_draft(app, build), app, build)
    assert p.read_bytes() == first


def test_bad_version(tmp_path):
    p = D.draft_path(tmp_path, "b")
    p.parent.mkdir()
    p.write_text('{"version": 99, "build": "b", "nodes": []}')
    with pytest.raises(ValueError):
        D.load_draft(tmp_path, "b")
