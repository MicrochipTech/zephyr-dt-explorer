"""Board nodes dialog (P8.7): LEDs, buttons, PWM LEDs, aliases, chosen.

Simulated tests (no browser) on a COPY of _soltest/apps/lab1_leer under %TEMP%; the draft file lands in the copy.
"""
from __future__ import annotations

import json

import pytest
from ui_helpers import by_id, click, el, page_text, select_value, simulated_app, wait_until

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"


def test_add_led_button_alias_chosen_and_save(lab1_leer_copy, rtos4_ws, ui_run):
    app, _ = lab1_leer_copy
    original = rtos4_ws / "_soltest" / "apps" / "lab1_leer"

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "board-nodes-open")
            assert by_id(user, "bn-dialog") and "No LED, button or PWM LED yet." in page_text(user, "bn-dialog")
            assert set(el(user, "bn-kind").options) == {"leds", "keys", "pwm-leds"}     # tcc0 is a PWM controller

            # an LED on a free pad
            select_value(user, "bn-name", "led_user")
            select_value(user, "bn-pad", "PD10")
            select_value(user, "bn-active-low", True)
            select_value(user, "bn-text", "User LED")
            click(user, "bn-add")
            assert "PD10 (active low)" in page_text(user, "bn-entry-led_user")
            assert el(user, "bn-problems").text == "" and el(user, "bn-save").enabled

            # a button: pull-up and active low come as defaults for a button
            select_value(user, "bn-kind", "keys")
            assert el(user, "bn-pull").value == "pull-up" and el(user, "bn-active-low").value is True
            select_value(user, "bn-name", "btn_user")
            select_value(user, "bn-pad", "PD11")
            select_value(user, "bn-code", "11")
            click(user, "bn-add")
            assert "PD11 (active low, pull-up, code 11)" in page_text(user, "bn-entry-btn_user")

            # a PWM LED on the build's PWM controller
            select_value(user, "bn-kind", "pwm-leds")
            select_value(user, "bn-name", "pwm_user")
            select_value(user, "bn-channel", 1)
            click(user, "bn-add")
            assert "tcc0 channel 1, period 20 ms" in page_text(user, "bn-entry-pwm_user")

            # alias and chosen entries; the new LED is a target
            assert "new: led_user" in el(user, "bn-alias-target").options["/user_leds/led_user"]
            select_value(user, "bn-alias-name", "led1")
            select_value(user, "bn-alias-target", "/user_leds/led_user")
            click(user, "bn-alias-add")
            assert "/user_leds/led_user" in page_text(user, "bn-alias-led1")
            select_value(user, "bn-chosen-name", "zephyr,uart-mcumgr")      # a name from the suggestions
            select_value(user, "bn-chosen-target", "/soc/sercom@41012000")
            click(user, "bn-chosen-add")
            assert "/soc/sercom@41012000" in page_text(user, "bn-chosen-zephyr_uart-mcumgr")

            click(user, "bn-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            assert not by_id(user, "bn-dialog") and el(user, "draft-badge").text == "1"
            row = page_text(user, "draft-node-board")
            assert "board nodes" in row and "1 LED, 1 button, 1 PWM LED, 1 alias, 1 chosen" in row
            snippet = el(user, "draft-snippet").content
            for line in ("&{/} {", "gpios = <&portd 10 1>;", 'label = "User LED";', "gpios = <&portd 11 17>;",
                         "zephyr,code = <11>;", "pwms = <&tcc0 1 20000000>;", 'led1 = "/user_leds/led_user";',
                         'zephyr,uart-mcumgr = "/soc/sercom@41012000";'):
                assert line in snippet, line

            # the pencil of the row opens the dialog again with the stored entries
            click(user, "draft-edit-board")
            assert by_id(user, "bn-entry-led_user") and by_id(user, "bn-entry-btn_user") and by_id(user, "bn-alias-led1")
            click(user, "bn-del-led_user")                        # the alias and chosen entries that pointed at it go too
            assert not by_id(user, "bn-entry-led_user") and not by_id(user, "bn-alias-led1") \
                and not by_id(user, "bn-chosen-dtx_user-led")
            click(user, "bn-cancel")

    ui_run(scenario)
    stored = json.loads((app / ".dt_explorer" / f"{BUILD}.draft.json").read_text(encoding="utf-8"))
    assert [(n["label"], n["cls"]) for n in stored["nodes"]] == [("{/}", "board")]        # Cancel kept the saved state
    names = [c["name"] for c in stored["nodes"][0]["children"]]
    assert names == ["user_leds", "user_keys", "user_pwm_leds", "aliases", "chosen"]
    assert not (original / ".dt_explorer").exists()


def test_refusals_and_warnings(lab1_leer_copy, ui_run):
    app, _ = lab1_leer_copy

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "board-nodes-open")
            # a bad name is refused and explained
            select_value(user, "bn-name", "Bad Name")
            select_value(user, "bn-pad", "PD10")
            click(user, "bn-add")
            assert not by_id(user, "bn-entry-Bad Name") and "name must be lower case" in el(user, "bn-problems").text
            # a label that the build has is refused
            select_value(user, "bn-name", "led0")
            click(user, "bn-add")
            assert "a node with this label already exists" in el(user, "bn-problems").text
            # the used pad shows who has it; the entry is accepted with a warning (the check reports the conflict)
            assert "led0 (GPIO)" in el(user, "bn-pad").options["PC18"]
            select_value(user, "bn-name", "led_bad")
            select_value(user, "bn-pad", "PC18")
            click(user, "bn-add")
            assert "warning: led_bad: pad PC18 is already used by" in el(user, "bn-problems").text
            assert el(user, "bn-save").enabled
            # an alias without a target is refused
            select_value(user, "bn-alias-name", "x")
            click(user, "bn-alias-add")
            assert "alias name and target are both needed" in el(user, "bn-problems").text
            # replacing an alias or chosen entry of the build is a warning
            select_value(user, "bn-alias-name", "led0")
            select_value(user, "bn-alias-target", "/user_leds/led_bad")
            click(user, "bn-alias-add")
            assert "alias 'led0' exists in the build: this overlay replaces it" in el(user, "bn-problems").text
            click(user, "bn-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            await wait_until(lambda: "pin_conflict" in page_text(user, "draft-findings"), timeout=60,
                             what="the pad conflict finding")
            assert "led_bad" in page_text(user, "draft-findings")

    ui_run(scenario)


def _draft_with_tcc3(app):
    """Store a draft that enables tcc3 as a PWM node (the Configure flow of the tool, through its core API)."""
    from dt_explorer.app import load_plugins
    from dt_explorer.core import plugins
    from dt_explorer.core.build import BuildModel
    from dt_explorer.ui import configure as C
    load_plugins()
    sess = C.ConfigSession(BuildModel(app / BUILD))
    inst = sess.instance_of(sess.node_for_label("tcc3"))
    compat = "atmel,sam0-tcc-pwm"
    rec = plugins.get_recipe(compat)
    opt = C.ClassOption("pwm", compat, rec)
    roles = C.option_roles(opt, inst)
    node = C.build_node(option=opt, instance=inst, label="tcc3", pins={"WO0": "PB16"}, roles_order=roles)
    node.vendor_props = rec.default_props(compat, inst.name)
    sess.upsert(node)
    sess.save()


def test_pwm_led_on_a_controller_the_draft_enables_and_zephyr_user(lab1_leer_copy, ui_run):
    app, _ = lab1_leer_copy
    _draft_with_tcc3(app)

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}")
            click(user, "board-nodes-open")
            select_value(user, "bn-kind", "pwm-leds")
            assert set(el(user, "bn-ctrl").options) == {"tcc0", "tcc3"}          # tcc3 is only in the draft
            select_value(user, "bn-ctrl", "tcc3")
            select_value(user, "bn-name", "pwm_led_tool")
            click(user, "bn-add")
            assert "tcc3 channel 0, period 20 ms" in page_text(user, "bn-entry-pwm_led_tool")
            assert el(user, "bn-problems").text == "" and el(user, "bn-save").enabled
            select_value(user, "bn-alias-name", "pwm-led0")
            select_value(user, "bn-alias-target", "/user_pwm_leds/pwm_led_tool")
            click(user, "bn-alias-add")

            # zephyr,user with io-channels, no hand edit
            assert "draft node" in el(user, "bn-user-ctrl").options["tcc3"]
            select_value(user, "bn-user-prop", "io-channels")
            click(user, "bn-user-add")                                             # no controller: refused
            assert "needs a property name and a controller" in el(user, "bn-problems").text
            for ch in ("6", "7"):
                select_value(user, "bn-user-prop", "io-channels")
                select_value(user, "bn-user-ctrl", "adc0")
                select_value(user, "bn-user-cells", ch)
                click(user, "bn-user-add")
            assert "<&adc0 6>" in page_text(user, "bn-user-io-channels-0") and "<&adc0 7>" in page_text(user, "bn-user-io-channels-1")
            select_value(user, "bn-user-prop", "pwms")
            select_value(user, "bn-user-ctrl", "tcc0")
            select_value(user, "bn-user-cells", "x")
            click(user, "bn-user-add")
            assert "cells must be numbers" in el(user, "bn-problems").text
            click(user, "bn-user-del-io-channels-1")
            assert not by_id(user, "bn-user-io-channels-1") and by_id(user, "bn-user-io-channels-0")
            select_value(user, "bn-user-prop", "io-channels")
            select_value(user, "bn-user-ctrl", "adc0")
            select_value(user, "bn-user-cells", "7")
            click(user, "bn-user-add")
            click(user, "bn-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            snippet = el(user, "draft-snippet").content
            for line in ("pwms = <&tcc3 0 20000000>;", 'pwm-led0 = "/user_pwm_leds/pwm_led_tool";',
                         "zephyr,user {", "io-channels = <&adc0 6>, <&adc0 7>;"):
                assert line in snippet, line
            assert "1 PWM LED, 1 alias, zephyr,user (io-channels)" in page_text(user, "draft-node-board")

    ui_run(scenario)
    stored = json.loads((app / ".dt_explorer" / f"{BUILD}.draft.json").read_text(encoding="utf-8"))
    board = next(n for n in stored["nodes"] if n["label"] == "{/}")
    assert [c["name"] for c in board["children"]] == ["user_pwm_leds", "aliases", "zephyr,user"]


# ---- real browser: screenshots -------------------------------------------------------------------------
# The Playwright flow runs in a child process: the session-wide `chrome` fixture leaves an event loop running in
# the pytest process, which would break every simulated test that sorts after this file.
BROWSER_FLOW = r'''
import json, re, sys
from playwright.sync_api import sync_playwright

base, shots, build = sys.argv[1:4]
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

    page.goto(f"{base}/?build={build}")
    page.click("#board-nodes-open")
    page.wait_for_selector("#bn-dialog")
    page.get_by_label("Name (node name and label, e.g. led_user)").fill("led_user")
    page.get_by_label("GPIO pad").click()
    page.keyboard.type("PD10")
    page.get_by_role("option", name=re.compile(r"^PD10\b")).click()
    page.get_by_text("active low", exact=True).click()
    page.get_by_label("label text (optional, e.g. User LED)").fill("User LED")
    page.click("#bn-add")
    page.wait_for_selector("#bn-entry-led_user")
    page.get_by_label("alias name (e.g. led1)").fill("led1")
    page.get_by_label("points to").first.click()
    page.keyboard.type("led_user")
    page.get_by_role("option", name=re.compile(r"new: led_user")).click()
    page.click("#bn-alias-add")
    page.wait_for_selector("#bn-alias-led1")
    shot("P8.7_board_nodes_dialog")
    page.click("#bn-save")
    page.wait_for_selector("#draft-snippet")
    out["snippet"] = page.inner_text("#draft-snippet")
    page.wait_for_function("/error\\(s\\)/.test(document.querySelector('#draft-check-status')?.innerText || '')",
                           timeout=60000)
    out["status"] = page.inner_text("#draft-check-status")
    shot("P8.7_board_nodes_draft")
    out["console_errors"] = errors
    browser.close()
print(json.dumps(out))
'''


@pytest.mark.smoke
def test_smoke_board_nodes(lab1_leer_copy, serve):
    """Real browser: the dialog by hand (LED on a free pad, alias), Save, the Draft tab with the overlay and check."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    app, _ = lab1_leer_copy
    base = serve(app, drafts=app)
    shots = Path(tempfile.gettempdir()) / "dtx_ui_shots"
    shots.mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", BROWSER_FLOW, base, str(shots), BUILD],
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-800:]
    print(proc.stdout)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(f"Chrome cannot be started through Playwright: {out['skip']}")
    assert 'led1 = "/user_leds/led_user";' in out["snippet"] and "gpios = <&portd 10 1>;" in out["snippet"]
    assert out["status"].startswith("0 error(s)"), out["status"]
    assert out["console_errors"] == [], out["console_errors"]

