"""Generic binding form (P8.5): Configure... on nodes without a recipe.

Simulated tests (no browser) on a COPY of _soltest/apps/lab1_leer under %TEMP%; one Playwright smoke test
with screenshots. tc0 (`atmel,sam0-tc32`, disabled) has an enum property and optional pin roles; adc1
(`atmel,sam0-adc`, disabled) has the required property `prescaler`, which the SoC dtsi sets.
"""
from __future__ import annotations

import json

import pytest
from ui_helpers import (by_id, click, click_button, el, page_text, select_value, simulated_app, visible,
                        wait_until)

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"
TC0 = "/soc/tc@40003800"
ADC1 = "/soc/adc@43002000"
WDOG = "/soc/watchdog@40002000"


def _ids(user, prefix: str) -> set[str]:
    return {str(e.props["id"]) for e in visible(user) if str(e.props.get("id", "")).startswith(prefix)}


def test_generic_form_for_tc0(lab1_leer_copy, rtos4_ws, ui_run):
    app, _ = lab1_leer_copy
    original = rtos4_ws / "_soltest" / "apps" / "lab1_leer"

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={TC0}")
            click(user, "cfg-open")                               # no recipe, but Configure... is offered
            dialog = page_text(user, "cfg-dialog")
            assert "Configure &tc0" in dialog and "atmel,sam0-tc32" in dialog and "generic form" in dialog
            # the form: one field with type, enum and description; pin roles from the provider (optional)
            assert _ids(user, "gen-prop-") == {"gen-prop-prescaler"}
            assert set(el(user, "gen-prop-prescaler").options) == {"", "1", "2", "4", "8", "16", "64", "256", "1024"}
            assert "Timer prescaler  [optional, int]" in dialog
            assert _ids(user, "gen-role-") == {"gen-role-WO0", "gen-role-WO1"}
            assert "Not editable here" in dialog and "atmel,assigned-clocks" in dialog
            assert el(user, "gen-problems").text == "" and el(user, "gen-save").enabled
            assert not by_id(user, "cfg-class")                   # no class toggle: there is no recipe
            select_value(user, "gen-prop-prescaler", "64")
            click(user, "gen-save")

            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            assert not by_id(user, "cfg-dialog") and el(user, "draft-badge").text == "1"
            assert "generic on TC0" in page_text(user, "draft-node-tc0")
            assert ('&tc0 {\n\tstatus = "okay";\n\tcompatible = "atmel,sam0-tc32";\n\tprescaler = <64>;\n};'
                    in el(user, "draft-snippet").content)
            kconfig = el(user, "draft-kconfig").content
            assert "CONFIG_COUNTER=y" in kconfig and "CONFIG_COUNTER_SAM0_TC32=y" in kconfig

    ui_run(scenario)
    stored = json.loads((app / ".dt_explorer" / f"{BUILD}.draft.json").read_text(encoding="utf-8"))
    assert [(n["label"], n["cls"], n["props"]) for n in stored["nodes"]] == [("tc0", "generic", {"prescaler": 64})]
    assert not (original / ".dt_explorer").exists()


def test_required_property_left_empty_is_flagged(lab1_leer_copy, ui_run):
    app, _ = lab1_leer_copy

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={ADC1}")
            click(user, "cfg-open")
            dialog = page_text(user, "cfg-dialog")
            assert "Configure &adc1" in dialog and "prescaler *" in dialog and "[required, int]" in dialog
            assert el(user, "gen-prop-prescaler").value == "4"        # prefilled from the SoC dtsi
            assert el(user, "gen-problems").text == "" and el(user, "gen-save").enabled
            select_value(user, "gen-prop-prescaler", "")              # the user empties the required field
            assert el(user, "gen-problems").text == "To fix: prescaler: required"
            assert el(user, "gen-prop-prescaler").error == "required" and el(user, "gen-prop-prescaler").props["error"]
            assert not el(user, "gen-save").enabled
            select_value(user, "gen-prop-prescaler", "abc")
            assert el(user, "gen-problems").text == "To fix: prescaler: not a number"
            select_value(user, "gen-prop-prescaler", "8")
            assert el(user, "gen-problems").text == "" and el(user, "gen-save").enabled
            click_button(user, "Cancel")
            assert not by_id(user, "cfg-dialog") and el(user, "draft-badge").text == "0"

    ui_run(scenario)


def test_enabled_node_without_recipe_has_no_button(lab1_leer_copy, ui_run):
    """wdog is enabled by the board: no Configure... and no Edit pins... (its generic draft is made in core/tests)."""
    app, _ = lab1_leer_copy

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={WDOG}")
            assert not by_id(user, "cfg-open") and not by_id(user, "cfg-edit")

    ui_run(scenario)




# ---- real browser: screenshots -------------------------------------------------------------------------
# The Playwright flow runs in a child process: the session-wide `chrome` fixture leaves an event loop running in
# the pytest process, which would break every simulated test that sorts after this file (test_ui_port.py).
BROWSER_FLOW = r'''
import json, sys
from playwright.sync_api import sync_playwright

base, shots, build, tc0, adc1 = sys.argv[1:6]
out = {}
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception as exc:
        print(json.dumps({"skip": str(exc).splitlines()[0][:150]}))
        sys.exit(0)
    page = browser.new_context(viewport={"width": 1500, "height": 1000}).new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    def shot(name):
        path = f"{shots}/{name}.png"
        page.screenshot(path=path)
        print("SCREENSHOT:", path, flush=True)

    page.goto(f"{base}/?build={build}&node={tc0}")
    page.click("#cfg-open")
    page.wait_for_selector("#gen-prop-prescaler")
    page.click("#gen-prop-prescaler")
    page.get_by_role("option", name="64", exact=True).click()
    page.wait_for_timeout(600)                              # let the closing menu finish its fade
    page.click("#gen-role-WO0")
    page.get_by_role("option").filter(has_text="WO0").first.click()
    page.wait_for_timeout(600)
    out["wo0"] = page.inner_text("#gen-role-WO0").strip()
    shot("P8.5_generic_form_tc0")
    page.click("#gen-save")
    page.wait_for_selector("#draft-snippet")
    out["snippet"] = page.inner_text("#draft-snippet")
    shot("P8.5_generic_draft_tc0")

    page.goto(f"{base}/?build={build}&node={adc1}")
    page.get_by_text("/soc/adc@43002000").first.wait_for()
    page.click("#cfg-open")
    page.wait_for_selector("#cfg-dialog")
    page.get_by_label("prescaler *").fill("")               # the id= prop is not on the native input
    page.wait_for_function("document.querySelector('#gen-problems')?.innerText.includes('required')")
    shot("P8.5_generic_form_required")
    page.get_by_label("prescaler *").scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    shot("P8.5_generic_form_required_field")
    out["save_disabled"] = page.is_disabled("#gen-save")
    out["problems"] = page.inner_text("#gen-problems")
    out["console_errors"] = errors
    browser.close()
print(json.dumps(out))
'''


@pytest.mark.smoke
def test_smoke_generic_form(lab1_leer_copy, serve):
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    app, _ = lab1_leer_copy
    base = serve(app, drafts=app)
    shots = Path(tempfile.gettempdir()) / "dtx_ui_shots"
    shots.mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", BROWSER_FLOW, base, str(shots), BUILD, TC0, ADC1],
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-800:]
    print(proc.stdout)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(f"Chrome cannot be started through Playwright: {out['skip']}")
    assert out["wo0"] != "" and "prescaler = <64>;" in out["snippet"]
    assert "pinctrl-0 = <&tc0_generic_default>;" in out["snippet"]
    assert out["problems"] == "To fix: prescaler: required" and out["save_disabled"] is True
    assert out["console_errors"] == [], out["console_errors"]
