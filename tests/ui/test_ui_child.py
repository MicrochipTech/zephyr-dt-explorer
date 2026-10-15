"""Child devices on a bus (P8.6): "Add child device..." in the Configure dialog of an I2C / SPI node.

Simulated tests (no browser) on a COPY of _soltest/apps/lab1_leer under %TEMP%; one Playwright smoke test with
screenshots (in a child process, see test_ui_generic.py). sercom1 (I2C on PC22/PC23) and sercom5 (SPI on PA21/PA20/
PA22) are disabled in the build; PB24/PB25 are the console pads of sercom2.
"""
from __future__ import annotations

import json

import pytest
from ui_helpers import (by_id, click, click_button, el, page_text, select_value, simulated_app, visible,
                        wait_until)

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"
SERCOM1 = "/soc/sercom@40003400"
SERCOM5 = "/soc/sercom@43000400"


def _ids(user, prefix: str) -> set[str]:
    return {str(e.props["id"]) for e in visible(user) if str(e.props.get("id", "")).startswith(prefix)}


async def _add_i2c_device(user, compat: str, address: str):
    click(user, "cfg-child-add")
    select_value(user, "child-compatible", compat)
    select_value(user, "child-address", address)


async def _findings(user) -> str:
    """Text of the Draft tab's check once it has run."""
    await wait_until(lambda: by_id(user, "draft-check-status") and el(user, "draft-check-status").text
                     not in ("", "checking …"), timeout=60, what="the check")
    return page_text(user, "draft-findings")


def test_add_bme280_to_i2c_and_a_duplicate_address(lab1_leer_copy, rtos4_ws, ui_run):
    app, _ = lab1_leer_copy
    original = rtos4_ws / "_soltest" / "apps" / "lab1_leer"

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={SERCOM1}")
            click(user, "cfg-open")
            select_value(user, "cfg-class", "i2c")
            select_value(user, "cfg-role-SDA", "PC22")
            select_value(user, "cfg-role-SCL", "PC23")
            assert by_id(user, "cfg-child-add") and "none" in page_text(user, "cfg-children")

            # the child dialog offers the I2C devices of the workspace; Add is disabled until it is complete
            await _add_i2c_device(user, "bosch,bme280", "")
            dialog = page_text(user, "child-dialog")
            assert "Add I2C device to &sercom1" in dialog and "bindings with on-bus: i2c" in dialog
            assert "bosch,bme280" in el(user, "child-compatible").options
            assert "bosch,bme680" in el(user, "child-compatible").options
            assert "atmel,sam0-i2c" not in el(user, "child-compatible").options       # a controller is no child
            assert el(user, "child-problems").text == "To fix: address: required" and not el(user, "child-save").enabled
            select_value(user, "child-address", "zz")
            assert el(user, "child-problems").text == "To fix: address: not a hex address"
            select_value(user, "child-address", "80")
            assert "7 bits" in el(user, "child-problems").text
            select_value(user, "child-address", "76")
            assert el(user, "child-problems").text == "" and el(user, "child-save").enabled
            assert "child-gpio-supply-gpios" in _ids(user, "child-gpio-")              # the GPIO property is a pad picker
            click(user, "child-save")

            assert not by_id(user, "child-dialog")
            assert "bme280@76" in page_text(user, "cfg-child-bme280-76") and "bosch,bme280" in page_text(user, "cfg-children")
            assert by_id(user, "cfg-child-remove-bme280-76")                           # a new child can be removed

            # a second device at 0x76: the hint names the clash, the dialog still saves (Check reports the error)
            await _add_i2c_device(user, "bosch,bme680", "76")
            assert "address 0x76 is already used by bme280@76" in el(user, "child-address-hint").text
            click(user, "child-save")
            assert "bme680@76" in page_text(user, "cfg-children")
            click(user, "cfg-save")

            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            snippet = el(user, "draft-snippet").content
            assert "bme280_76: bme280@76 {" in snippet and 'compatible = "bosch,bme280";' in snippet
            assert "bme680_76: bme680@76 {" in snippet and "reg = <0x76>;" in snippet
            assert "#address-cells = <1>;" in snippet and "#size-cells = <0>;" in snippet
            kconfig = el(user, "draft-kconfig").content
            assert "CONFIG_BME280=y" in kconfig and "CONFIG_BME680=y" in kconfig and "CONFIG_I2C_SAM0=y" in kconfig
            findings = await _findings(user)
            assert "bus_address_duplicate" in findings and "I2C address 0x76 is used by 2 devices on &sercom1" in findings

    ui_run(scenario)
    stored = json.loads((app / ".dt_explorer" / f"{BUILD}.draft.json").read_text(encoding="utf-8"))
    kids = stored["nodes"][0]["children"]
    assert [(k["name"], k["unit"], k["compatible"], k["props"]["reg"]) for k in kids] == [
        ("bme280", "76", ["bosch,bme280"], [118]), ("bme680", "76", ["bosch,bme680"], [118])]
    assert not (original / ".dt_explorer").exists()


def test_add_spi_device_with_cs_and_int_gpio(lab1_leer_copy, ui_run):
    app, _ = lab1_leer_copy

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={SERCOM5}")
            click(user, "cfg-open")
            select_value(user, "cfg-class", "spi")
            for role, pad in (("MOSI", "PA21"), ("MISO", "PA20"), ("SCK", "PA22")):
                select_value(user, f"cfg-role-{role}", pad)
            click(user, "cfg-child-add")
            assert "Add SPI device to &sercom5" in page_text(user, "child-dialog")
            assert el(user, "child-address").value == "0"                              # the next free chip select
            select_value(user, "child-compatible", "bosch,bmi160")
            # the SPI form: the required spi-max-frequency is flagged, the GPIO properties are pad pickers
            assert el(user, "child-problems").text == "To fix: spi-max-frequency: required"
            assert {"child-gpio-int-gpios", "child-gpio-supply-gpios"} <= _ids(user, "child-gpio-")
            assert "PB24  - used by sercom2" in el(user, "child-cs-pad").options["PB24"]
            assert el(user, "child-cs-pad").options["PB14"] == "PB14"
            select_value(user, "child-prop-spi-max-frequency", "1000000")
            select_value(user, "child-cs-pad", "PB14")
            select_value(user, "child-gpio-int-gpios", "PB15")
            assert el(user, "child-problems").text == "" and el(user, "child-save").enabled
            click(user, "child-save")
            assert "bmi160@0" in page_text(user, "cfg-child-bmi160-0")
            # PB14 / PB15 are now taken by this dialog: a second device sees them
            click(user, "cfg-child-add")
            assert "this dialog" in el(user, "child-cs-pad").options["PB14"]
            assert "bmi160@0 int-gpios (this dialog)" in el(user, "child-cs-pad").options["PB15"]
            select_value(user, "child-address", "3")                                   # beyond the cs-gpios list
            select_value(user, "child-compatible", "bosch,bmi160")
            select_value(user, "child-prop-spi-max-frequency", "1000000")
            select_value(user, "child-cs-pad", "PB13")
            assert el(user, "child-problems").text == "To fix: cs: chip select 3 needs cs-gpios entries 0..2 first"
            click(user, "child-cancel")
            # remove and add again: removing the only chip select drops its cs-gpios entry
            click(user, "cfg-child-remove-bmi160-0")
            assert not by_id(user, "cfg-child-bmi160-0")
            click(user, "cfg-child-add")
            select_value(user, "child-compatible", "bosch,bmi160")
            select_value(user, "child-prop-spi-max-frequency", "2000000")
            select_value(user, "child-cs-pad", "PB24")                                 # a used pad: allowed, Check flags it
            select_value(user, "child-cs-flags", "active high")
            click(user, "child-save")
            click(user, "cfg-save")

            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            snippet = el(user, "draft-snippet").content
            assert "cs-gpios = <&portb 24 0>;" in snippet and "bmi160_0: bmi160@0 {" in snippet
            assert "spi-max-frequency = <2000000>;" in snippet and "PB14" not in snippet
            assert "CONFIG_BMI160=y" in el(user, "draft-kconfig").content
            findings = await _findings(user)
            assert "pin_conflict" in findings and "pad PB24 is used by 2 nodes" in findings

    ui_run(scenario)
    stored = json.loads((app / ".dt_explorer" / f"{BUILD}.draft.json").read_text(encoding="utf-8"))
    node = stored["nodes"][0]
    assert node["props"]["cs-gpios"] == [[{"ref": "portb"}, 24, 0]]         # Ref encoded like in child nodes (d1401ae)
    from dt_explorer.core.draft import load_draft
    from dt_explorer.core.translate import Ref
    from dt_explorer.ui.configure import revive_refs
    assert revive_refs(load_draft(app, app / BUILD)).nodes[0].props["cs-gpios"] == [[Ref("portb"), 24, 0]]
    assert node["children"][0]["props"]["spi-max-frequency"] == 2000000


def test_edit_pins_keeps_the_boards_child(lab1_leer_copy, ui_run):
    """sercom7 (enabled, I2C) has `eeprom@5e` from the board: listed, not removable; a new device is added beside it."""
    app, _ = lab1_leer_copy

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node=/soc/sercom@43000c00")
            click(user, "cfg-edit")
            assert "eeprom@5e" in page_text(user, "cfg-child-eeprom-5e") and "from the board" in page_text(user, "cfg-children")
            assert not by_id(user, "cfg-child-remove-eeprom-5e")
            await _add_i2c_device(user, "bosch,bme280", "5e")
            assert "address 0x5e is already used by eeprom@5e" in el(user, "child-address-hint").text
            select_value(user, "child-address", "76")
            click(user, "child-save")
            click(user, "cfg-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            snippet = el(user, "draft-snippet").content
            # P8.19b item 5: the board's eeprom@5e is not written again, only the new device
            assert "bme280_76: bme280@76 {" in snippet and "eeprom@5e" not in snippet

    ui_run(scenario)


# ---- real browser: screenshots ---------------------------------------------------------------------------
BROWSER_FLOW = r'''
import json, sys
from playwright.sync_api import sync_playwright

base, shots, build, sercom1, sercom5 = sys.argv[1:6]
out = {}
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception as exc:
        print(json.dumps({"skip": str(exc).splitlines()[0][:150]}))
        sys.exit(0)
    page = browser.new_context(viewport={"width": 1500, "height": 1100}).new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    def shot(name):
        path = f"{shots}/{name}.png"
        page.screenshot(path=path)
        print("SCREENSHOT:", path, flush=True)

    def choose(sel, text):
        page.click(sel)
        page.get_by_role("option").filter(has_text=text).first.click()
        page.wait_for_timeout(500)

    def pick(label, text):
        """A select with search has no DOM id: focus it by its label, type, take the first matching option."""
        box = page.get_by_label(label)
        box.click()
        page.wait_for_timeout(400)
        box.fill(text)
        page.wait_for_timeout(600)
        page.get_by_role("option").filter(has_text=text).first.click()
        page.wait_for_timeout(500)

    page.goto(f"{base}/?build={build}&node={sercom1}", timeout=120000)
    page.click("#cfg-open")
    page.wait_for_selector("#cfg-class")
    page.get_by_role("button", name="I2C", exact=True).click()
    page.wait_for_selector("#cfg-role-SDA")
    choose("#cfg-role-SDA", "PC22")
    choose("#cfg-role-SCL", "PC23")
    page.click("#cfg-child-add")
    page.wait_for_selector("#child-dialog")
    pick("Device (compatible)", "bosch,bme280")
    page.get_by_label("I2C address (hex, e.g. 76)").fill("76")
    page.wait_for_timeout(500)
    shot("P8.6_child_dialog_i2c")
    page.click("#child-save")
    page.wait_for_selector("#cfg-child-bme280-76")
    out["child_row"] = page.inner_text("#cfg-child-bme280-76")
    shot("P8.6_configure_with_child")
    page.click("#cfg-save")
    page.wait_for_selector("#draft-snippet")
    out["snippet"] = page.inner_text("#draft-snippet")
    page.wait_for_function("document.querySelector('#draft-check-status')?.innerText.length > 0 && !document.querySelector('#draft-check-status').innerText.includes('checking')", timeout=60000)
    out["check"] = page.inner_text("#draft-check-status")
    shot("P8.6_draft_with_child")

    page.goto(f"{base}/?build={build}&node={sercom5}", timeout=120000)
    page.get_by_text("/soc/sercom@43000400").first.wait_for()
    page.click("#cfg-open")
    page.wait_for_selector("#cfg-class")
    page.get_by_role("button", name="SPI", exact=True).click()
    page.wait_for_selector("#cfg-role-MOSI")
    choose("#cfg-role-MOSI", "PA21")
    choose("#cfg-role-MISO", "PA20")
    choose("#cfg-role-SCK", "PA22")
    page.click("#cfg-child-add")
    page.wait_for_selector("#child-dialog")
    pick("Device (compatible)", "bosch,bmi160")
    page.get_by_label("spi-max-frequency *").fill("1000000")
    pick("CS pad", "PB24")
    pick("int-gpios (optional)", "PB15")
    page.wait_for_timeout(300)
    shot("P8.6_child_dialog_spi")
    out["spi_problems"] = page.inner_text("#child-problems")
    page.click("#child-save")
    page.wait_for_selector("#cfg-child-bmi160-0")
    page.click("#cfg-save")
    page.wait_for_selector("#draft-snippet")
    out["spi_snippet"] = page.inner_text("#draft-snippet")
    page.wait_for_function("document.querySelector('#draft-findings')?.innerText.includes('pin_conflict')", timeout=60000)
    out["spi_findings"] = page.inner_text("#draft-findings")
    shot("P8.6_draft_spi_pad_conflict")
    out["console_errors"] = errors
    browser.close()
print(json.dumps(out))
'''


@pytest.mark.smoke
def test_smoke_child_dialog(lab1_leer_copy, serve):
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    app, _ = lab1_leer_copy
    base = serve(app, drafts=app)
    shots = Path(tempfile.gettempdir()) / "dtx_ui_shots"
    shots.mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", BROWSER_FLOW, base, str(shots), BUILD, SERCOM1, SERCOM5],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    print(proc.stdout)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(f"Chrome cannot be started through Playwright: {out['skip']}")
    assert "bme280@76" in out["child_row"] and "bme280_76: bme280@76 {" in out["snippet"]
    assert out["spi_problems"] == "" and "cs-gpios = <&portb 24 1>;" in out["spi_snippet"]
    assert "pad PB24 is used by 2 nodes" in out["spi_findings"]
    assert out["console_errors"] == [], out["console_errors"]
