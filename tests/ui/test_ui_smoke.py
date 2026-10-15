r"""Real-browser smoke tests: Playwright + Chrome against an own server on a free port.

Each test saves a screenshot to %TEMP%\dtx_ui_shots (the paths are listed at the end of the pytest run).
Skipped without Playwright/Chrome or without the DTX_* variables. Drafts go to a scratch folder (_serve.py);
the lab1_leer app is a copy under %TEMP%.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.smoke

BUILD = "build_same54_xpro"
CHECK_DONE = ("/error\\(s\\)/.test(document.querySelector('#draft-check-status')?.innerText || '')")


def _no_console_errors(page):
    assert page.console_errors == [], page.console_errors


def _configure_i2c(page, base):
    page.goto(f"{base}/?build={BUILD}&node=/soc/sercom@40003400")
    page.click("#cfg-open")
    page.wait_for_selector("#cfg-dialog")
    page.get_by_role("button", name="I2C", exact=True).click()
    for role, pad in (("SDA", "PC22"), ("SCL", "PC23")):
        page.click(f"#cfg-role-{role}")
        page.get_by_role("option", name=re.compile(rf"^{pad}\b")).click()
    page.wait_for_function("document.querySelector('#cfg-derived')?.innerText.includes('Derived properties: none')")
    page.click("#cfg-save")
    page.wait_for_selector("#draft-snippet")


def test_smoke_configure_check(lab1_leer_copy, serve, page, shot):
    app, _ = lab1_leer_copy
    base = serve(app, drafts=app)
    _configure_i2c(page, base)
    assert page.inner_text("#draft-badge").strip() == "1"
    assert "sercom1_i2c_default" in page.inner_text("#draft-snippet")
    page.wait_for_function(CHECK_DONE, timeout=60000)
    status = page.inner_text("#draft-check-status")
    shot(page, "configure_check")
    assert status.startswith("0 error(s)"), status
    _no_console_errors(page)


@pytest.mark.slow
def test_smoke_configure_check_verify(lab1_leer_copy, serve, page, shot):
    """Configure -> Check -> Verify with west (Zephyr's own cmake-only configure, 15-45 s)."""
    app, _ = lab1_leer_copy
    base = serve(app, drafts=app)
    _configure_i2c(page, base)
    page.wait_for_function(CHECK_DONE, timeout=60000)
    page.click("#draft-verify")
    page.wait_for_function("/^Zephyr (accepts|rejects)|^Cannot verify/.test("
                           "document.querySelector('#draft-verify-status')?.innerText || '')", timeout=240000)
    status = page.inner_text("#draft-verify-status")
    shot(page, "configure_check_verify")
    assert status.startswith("Zephyr accepts the draft"), status
    _no_console_errors(page)


def test_smoke_port(lab1, lab1_e54, serve, page, shot):
    root, _ = lab1
    base = serve(root)
    page.goto(f"{base}/?build={BUILD}")
    page.click("#port-open")
    page.wait_for_selector("#port-dialog")
    page.get_by_label("or a build folder path").fill(str(lab1_e54))   # the id=port-other prop is not on the native input
    page.click("#port-run")
    page.wait_for_selector("#port-report", timeout=180000)
    shot(page, "port_report")
    assert page.inner_text("#port-status").startswith("done:")
    assert "1. Nodes" in page.inner_text("#port-report")
    _no_console_errors(page)


def test_smoke_board_view(lab1, serve, page, shot):
    root, _ = lab1
    base = serve(root)
    page.goto(f"{base}/?build={BUILD}&tab=board")
    page.wait_for_selector("svg[data-connector='EXT1']", timeout=60000)
    shot(page, "board_view")
    assert page.locator("svg[data-connector]").count() >= 3
    _no_console_errors(page)
