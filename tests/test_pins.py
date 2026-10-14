"""Used pins from the devicetree of the lab1 build (needs DTX_RTOS4_WS)."""

from __future__ import annotations

import pytest

from dt_explorer.core import pins
from dt_explorer.core.build import BuildModel
from dt_explorer.core.buildinfo import read_build_info


def _load_plugins():
    # other tests clear the registry, so register explicitly instead of relying on import side effects
    from dt_explorer.core import plugins
    from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    plugins.register_provider(MicrochipAtdfProvider)
    plugins.register_formatter(AtmelSam0Formatter)


@pytest.fixture(scope="module")
def lab1(rtos4_build):
    _load_plugins()
    build = rtos4_build("apps/lab1/build_same54_xpro")
    return BuildModel(build), read_build_info(build)


def _fmt(uses):
    return sorted((u.node, u.role, u.function, u.source) for u in uses)


def test_lab1_used_pins(lab1, capsys):
    model, info = lab1
    _load_plugins()
    usage = pins.used_pins(model, info)
    with capsys.disabled():
        print("\n--- lab1 used pins ---")
        for pad in sorted(usage.uses):
            for u in usage.uses[pad]:
                print(f"{pad:6} {u.source:8} {u.node}  {u.role}  [{u.function}]  state={u.state}")
        for u in usage.unresolved:
            print(f"UNRESOLVED {u.source} {u.node} {u.role} {u.function}")
        for n in usage.notes:
            print("NOTE", n)

    # console: sercom2 pinctrl = PB25 (PAD0, TX) and PB24 (PAD1, RX)
    sercom2 = "/soc/sercom@41012000"
    assert any(u.node == sercom2 and u.role == "PAD0" and u.source == "pinctrl"
               for u in usage.uses["PB25"])
    assert any(u.node == sercom2 and u.role == "PAD1" and u.source == "pinctrl"
               for u in usage.uses["PB24"])
    # board LED: led_0 gpios = port C pin 18
    assert any(u.node == "/leds/led_0" and u.source == "gpio" for u in usage.uses["PC18"])
    # button_0: port B pin 31
    assert any(u.node == "/buttons/button_0" and u.source == "gpio" for u in usage.uses["PB31"])
    assert len(usage.uses["PC18"]) == 2   # LED and TCC0 PWM (WO2) share PC18
    assert not usage.unresolved


def test_rows_for_default_package(lab1):
    model, info = lab1
    _load_plugins()
    usage = pins.used_pins(model, info)
    rows = pins.pin_rows(usage)
    assert len(rows) == 128
    by_pad = {r["pad"]: r for r in rows}
    assert by_pad["PB25"]["uses"] and any("SERCOM2.PAD0" in f for f in by_pad["PB25"]["functions"])
    assert not by_pad["PA00"]["uses"] if "PA00" in by_pad else True
