"""Simulated UI tests (no browser): Configure..., Edit pins... and the Draft tab.

The save test runs on a COPY of _soltest/apps/lab1_leer under %TEMP% (conftest.lab1_leer_copy); the draft
file lands in the copy, never in the workspace.
"""
from __future__ import annotations

import json

import pytest
from ui_helpers import (by_id, click, click_button, el, page_text, select_value, simulated_app, wait_until)

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"


def test_configure_i2c_save_and_draft(lab1_leer_copy, rtos4_ws, ui_run):
    app, build = lab1_leer_copy
    original = rtos4_ws / "_soltest" / "apps" / "lab1_leer"

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node=/soc/sercom@40003400")
            assert el(user, "draft-badge").text == "0"
            assert not by_id(user, "cfg-edit")                    # a disabled node: Configure..., not Edit pins
            click(user, "cfg-open")
            assert "Configure &sercom1" in page_text(user, "cfg-dialog")
            assert el(user, "cfg-class").value == "uart"
            # the class toggle offers UART, I2C, SPI
            assert set(el(user, "cfg-class").options) >= {"uart", "i2c", "spi"}
            select_value(user, "cfg-class", "i2c")
            assert {"cfg-role-SDA", "cfg-role-SCL"} <= {e.props["id"] for e in _roles(user)}
            assert set(el(user, "cfg-role-SDA").options) == {"PA00", "PA16", "PC22", "PC27"}
            select_value(user, "cfg-role-SDA", "PC22")
            # SCL is narrowed to the PAD1 pads; PA17 is offered but marked as used by gmac
            scl = el(user, "cfg-role-SCL").options
            assert set(scl) == {"PA01", "PA17", "PC23", "PC28"} and "used by gmac" in scl["PA17"]
            select_value(user, "cfg-role-SCL", "PC23")
            assert el(user, "cfg-derived").text == "Derived properties: none"
            select_value(user, "cfg-prop-clock-frequency", "100000")
            click(user, "cfg-save")

            # Save closes the dialog, updates the badge and switches to the Draft tab
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            assert not by_id(user, "cfg-dialog")
            assert el(user, "draft-badge").text == "1"
            node = page_text(user, "draft-node-sercom1")
            assert "&sercom1" in node and "i2c on SERCOM1" in node and "SDA PC22" in node and "SCL PC23" in node
            snippet = el(user, "draft-snippet").content
            assert "sercom1_i2c_default: sercom1_i2c_default {" in snippet
            assert "pinmux = <PC22C_SERCOM1_PAD0>," in snippet and "<PC23C_SERCOM1_PAD1>;" in snippet
            assert '&sercom1 {\n\tstatus = "okay";\n\tcompatible = "atmel,sam0-i2c";' in snippet
            assert "clock-frequency = <100000>;" in snippet
            assert "pinctrl-0 = <&sercom1_i2c_default>;" in snippet
            kconfig = el(user, "draft-kconfig").content
            assert "CONFIG_I2C=y" in kconfig and "CONFIG_I2C_SAM0=y" in kconfig

    ui_run(scenario)

    # the draft file is in the copy ...
    stored = app / ".dt_explorer" / f"{BUILD}.draft.json"
    data = json.loads(stored.read_text(encoding="utf-8"))
    assert [n["label"] for n in data["nodes"]] == ["sercom1"]
    assert data["nodes"][0]["pins"] == {"SDA": "PC22", "SCL": "PC23"}
    # ... and the original app folder got nothing
    assert not (original / ".dt_explorer").exists()


def _roles(user):
    from ui_helpers import visible
    return [e for e in visible(user) if str(e.props.get("id", "")).startswith("cfg-role-")]


def test_edit_pins_prefill_and_cancel(lab1, ui_run, tmp_path):
    root, _ = lab1

    async def scenario():
        async with simulated_app(root) as user:
            await user.open(f"/?build={BUILD}&node=/soc/sercom@41012000")
            assert not by_id(user, "cfg-open")
            click(user, "cfg-edit")
            dialog = page_text(user, "cfg-dialog")
            assert "Edit pins of &sercom2" in dialog and "instance SERCOM2" in dialog
            # prefilled from the lifted node: the board's console pads PB25 (TX) and PB24 (RX)
            assert el(user, "cfg-class").value == "uart"
            assert el(user, "cfg-role-TX").value == "PB25" and el(user, "cfg-role-RX").value == "PB24"
            assert el(user, "cfg-derived").text == "Derived properties: rxpo = 1, txpo = 0"
            assert el(user, "cfg-prop-current-speed").value == "115200"
            # Cancel: dialog gone, nothing drafted, nothing saved
            click_button(user, "Cancel")
            assert not by_id(user, "cfg-dialog")
            assert el(user, "draft-badge").text == "0"

    ui_run(scenario)
    assert not list((tmp_path / "drafts").glob("**/*.json"))        # the redirected draft store is still empty
