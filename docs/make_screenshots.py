"""Regenerates the screenshots in docs/img with headless Chrome.

Prerequisites: the explorer runs on http://localhost:<port> (default 8765, e.g.
`py dt_explorer.py <workspace> --no-browser`), the RTOS4 lab builds exist (apps/lab0..lab3,
_soltest/apps/lab1_leer) and Chrome is installed.

Most shots are plain Chrome screenshots of a URL. The dialogs (Configure…, New build…)
and the Draft tab need clicks, so they are driven with Playwright (`py -m pip install playwright`).
They save one draft in apps\\lab1\\.dt_explorer and delete it again (and the folder, if it was
created for it). The script refuses to start while that draft file exists.

The Port to board… dialog runs its pipeline (no west, nothing written) and shows the report. Its
"Verify with west" and the Draft tab's "Verify with west" / "Write to app" run west or write into the app,
so those shots are not produced here: they are copied from the evidence images (REUSED below).

    py docs/make_screenshots.py [--port 8765] [--crop-only]
"""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
IMG = Path(__file__).parent / "img"
EVID = Path(__file__).parent / "evidence" / "img"
# docs image name -> evidence image it is copied from (shots that would run west or write into apps\)
REUSED = {
    "draft_verify_ok.png":    "P4.1_ok.png",           # Draft tab, Verify with west, rc 0
    "draft_verify_fail.png":  "P4.1_fail.png",         # Draft tab, Verify with west, rc 1
    "draft_write_dialog.png": "P4.1_write_dialog.png", # Write to app confirmation
    "port_verify.png":        "P5.4_verify.png",       # Port dialog, Verify with west on the target
    # Port to board… > Apply to app…: the plan, the diff and the write run only on a button press (Apply is not
    # clicked here: it would write into apps\). Copied from the P5.6 copy-run evidence (apps\lab1 copy in %TEMP%).
    "apply_plan_new.png":     "P5.6_plan_new.png",          # plan: boards/sam_e54_xpro.overlay NEW
    "apply_plan_diff.png":    "P5.6_plan_exists_diff.png",  # plan: existing file differs, diff shown
    "apply_replace_ticked.png": "P5.6_replace_ticked.png",  # diff with "Replace the existing ..." ticked
    "apply_written.png":      "P5.6_written.png",           # result after Write files, with the west hint
    # Board tab of the SG41 CUlt build (root = the sg41 build folder); P7.4 run, no writes
    "board_sg41_cult.png":    "P7.4_sg41_cult.png",
}
CUT_OLD_HEADER = {"draft_verify_ok.png", "draft_verify_fail.png"}   # Draft tab shots with the old header
WS = Path(os.environ.get("DTX_RTOS4_WS", r"C:\Masters\26020_RTOS4\zephyrproject"))
LAB1 = "build=apps/lab1/build_same54_xpro"
DRAFT_DIR = WS / "apps" / "lab1" / ".dt_explorer"
DRAFT = DRAFT_DIR / "build_same54_xpro.draft.json"

SHOTS = {  # name -> (query, width, height)
    "overview":     ("build=apps/lab3/build_same54_xpro", 1500, 1000),
    "node_led":     ("build=apps/lab1/build_same54_xpro&node=" + quote("/my_led@41008000", safe="")
                     + "&macros=1", 1500, 1700),
    "node_button":  ("build=apps/lab0/build_same54_xpro&node=" + quote("/buttons/button_0", safe=""), 1500, 1000),
    "tree_full":    ("build=apps/lab0/build_same54_xpro", 1500, 3000),
    "include_file": ("build=apps/lab3/build_same54_xpro&file=" + quote(r"zephyr\dts\arm\atmel\same5x.dtsi", safe=""),
                     1500, 1000),
    "include_header": ("build=apps/lab3/build_same54_xpro&file="
                       + quote(r"zephyr\include\zephyr\dt-bindings\gpio\gpio.h", safe=""), 1500, 1000),
    "source":       ("build=apps/lab3/build_same54_xpro&node=" + quote("/soc/sercom@41014000", safe="")
                     + "&src=" + quote(r"apps\lab3\boards\same54_xpro.overlay", safe="") + "&line=20", 1500, 1000),
    "orphan":       ("build=_soltest/apps/lab1_leer/build_same54_xpro", 1500, 700),
    "node_binding": ("build=apps/lab3/build_same54_xpro&node=" + quote("/soc/sercom@41014000/mytemp@4f", safe="")
                     + "&bprops=1", 1500, 1200),
    "node_nobinding": ("build=apps/lab3/build_same54_xpro&node="
                       + quote("/soc/sercom@43000c00/eeprom@5e", safe=""), 1500, 700),
    "board_same54": (LAB1 + "&tab=board", 1500, 1000),
    "pins_tab":     (LAB1 + "&tab=pins", 1500, 1000),
    "package_tab":  (LAB1 + "&tab=package", 1500, 1000),
}


CROPS = {  # element image -> (source screenshot, box (left, top, right, bottom))
    "el_header":        ("node_led", (0, 0, 1500, 74)),
    # boxes are measured below the header, which is 74 px high: one row since P3.7 (the origin chips are in
    # the Legend menu). Before P3.7 the chips wrapped and the header was 116 px high (see GUI.md 1).
    "el_tree":          ("node_led", (0, 74, 515, 620)),
    "el_node_head":     ("node_led", (515, 97, 1490, 342)),
    "el_props":         ("node_led", (515, 758, 1490, 1070)),
    "el_code_macros":   ("node_led", (515, 1075, 1490, 1658)),
    "el_binding":       ("node_binding", (515, 312, 1490, 1122)),
    "el_nobinding":     ("node_nobinding", (515, 97, 1490, 462)),
    "el_coderefs":      ("overview", (515, 228, 1490, 715)),
    "el_child_state":   ("node_button", (515, 95, 1490, 320)),
    "el_include_tree":  ("include_file", (0, 72, 515, 825)),
    "el_include_file":  ("include_file", (515, 95, 1490, 610)),
    "el_header_defs":   ("include_header", (515, 95, 1490, 690)),
    "el_orphan":        ("orphan", (0, 78, 1500, 182)),
}


def legend():
    """The traffic-light legend sits below the (long) node tree - find it in tree_full."""
    from PIL import Image
    im = Image.open(IMG / "tree_full.png")
    gray, (w, h) = im.convert("L").load(), im.size
    last = max(y for y in range(h) if any(gray[x, y] > 90 for x in range(10, 500, 3)))
    im.crop((0, last - 185, 515, last + 15)).save(IMG / "el_legend.png")


def crops():
    from PIL import Image
    for name, (src, box) in CROPS.items():
        Image.open(IMG / f"{src}.png").crop(box).save(IMG / f"{name}.png")
    if (IMG / "tree_full.png").exists():     # a full run deletes it; el_legend.png then stays as it is
        legend()


def ui_shots(base):
    """Configure… dialog (SERCOM1 as I2C, PC22/PC23), Draft tab with its check, New build… dialog."""
    from playwright.sync_api import sync_playwright
    if DRAFT.exists():
        raise SystemExit(f"{DRAFT} exists - move it away first, this script deletes it")
    made_dir = not DRAFT_DIR.exists()
    IMG.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(executable_path=str(CHROME), headless=True)
            pg = br.new_page(viewport={"width": 1500, "height": 1000})
            pg.goto(f"{base}?{LAB1}&node={quote('/soc/sercom@40003400', safe='')}")
            pg.get_by_role("button", name="Configure…").click()
            pg.get_by_role("button", name="I2C").click()
            for pin_label, pad in (("SDA", "PC22"), ("SCL", "PC23")):
                pg.locator(".q-dialog .q-select").filter(has_text=pin_label).first.click()
                pg.get_by_role("option").filter(has_text=pad).first.click()
            pg.wait_for_timeout(800)
            pg.screenshot(path=IMG / "configure_dialog.png")
            pg.locator("#cfg-save").click()
            pg.wait_for_selector("#draft-check-status")
            pg.wait_for_function("() => { const e = document.querySelector('#draft-check-status');"
                                 " return !!e && e.innerText.trim() !== '' && !e.innerText.includes('checking'); }",
                                 timeout=60000)
            pg.wait_for_timeout(800)
            pg.screenshot(path=IMG / "draft_tab.png")
            pg.goto(f"{base}?{LAB1}")
            pg.get_by_role("button", name="New build…").click()
            pg.locator(".q-dialog").wait_for(state="visible")
            pg.wait_for_timeout(1000)
            pg.screenshot(path=IMG / "new_build_dialog.png")
            pg.locator(".q-dialog").get_by_role("button", name="Close").click()
            # "Full build" is not clicked here: it starts a real `west build` in the lab build folder
            # (the dialog starts on open). docs/img/full_build_dialog.png is reused from P2.2.
            br.close()
    finally:
        DRAFT.unlink(missing_ok=True)
        if made_dir and DRAFT_DIR.exists() and not any(DRAFT_DIR.iterdir()):
            DRAFT_DIR.rmdir()
    for name in ("configure_dialog", "draft_tab", "new_build_dialog"):
        print(name, (IMG / f"{name}.png").exists())


def extra_ui_shots(base):
    """Header Legend menu, Edit pins… dialog (enabled SERCOM2, not saved), Port to board… dialog and its report.

    Nothing is saved: Edit pins is cancelled, and "Run port" only reads the two builds (no west, no files).
    """
    from playwright.sync_api import sync_playwright
    IMG.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=str(CHROME), headless=True)
        pg = br.new_page(viewport={"width": 1500, "height": 1000})
        pg.goto(f"{base}?{LAB1}")
        pg.locator("#legend-btn").click()
        pg.wait_for_timeout(600)
        pg.screenshot(path=IMG / "legend_menu.png", clip={"x": 0, "y": 0, "width": 1500, "height": 260})
        pg.keyboard.press("Escape")

        pg.goto(f"{base}?{LAB1}&node={quote('/soc/sercom@41012000', safe='')}")
        pg.locator("#cfg-edit").click()
        pg.locator("#cfg-dialog").wait_for(state="visible")
        pg.wait_for_timeout(800)
        pg.screenshot(path=IMG / "edit_pins_dialog.png")
        pg.locator("#cfg-dialog").get_by_role("button", name="Cancel").click()

        # Source lab0 and target = the SG41 CUlt build of lab0 from the P6.2 run (outside the workspace, so typed
        # into "or a build folder path"). It gives the conflict lines and suggestions of P6.3 in the report.
        target = os.environ.get("DTX_PORT_TARGET", str(Path(tempfile.gettempdir()) / "dtx_p62_lab0_pic32cx_sg41_cult"))
        pg.goto(f"{base}?build=apps/lab0/build_same54_xpro")
        pg.locator("#port-open").click()
        pg.locator("#port-dialog").wait_for(state="visible")
        if Path(target).is_dir():
            pg.locator(".q-dialog [aria-label=Clear]").first.click()     # empty the dropdown, so the screenshot shows one target
            pg.get_by_label("or a build folder path").fill(target)
            pg.keyboard.press("Tab")
        else:
            print("WARNING: target build not found, the dropdown default is used:", target)
        pg.wait_for_timeout(800)
        pg.screenshot(path=IMG / "port_dialog.png")
        pg.locator("#port-run").click()
        pg.wait_for_function("() => { const e = document.querySelector('#port-status');"
                             " return !!e && e.innerText.trim() !== '' && !e.innerText.includes('porting'); }",
                             timeout=300000)
        pg.wait_for_timeout(800)
        pg.locator("#port-report").screenshot(path=IMG / "port_report.png")
        pg.locator("#port-dialog").screenshot(path=IMG / "port_result.png")
        pg.locator("#port-apply").scroll_into_view_if_needed()     # the "Apply to app…" button, scrolled into view; not clicked
        pg.wait_for_timeout(300)
        pg.locator("#port-dialog").screenshot(path=IMG / "port_apply_button.png")
        pg.locator("#port-close").click()
        br.close()
        for name in ("port_dialog", "port_report", "port_result", "port_apply_button"):
            print(name, (IMG / f"{name}.png").exists())


def copy_reused():
    """Shots that must not be re-taken here (they run west or write into apps\\): copied from the evidence.

    Evidence shots of the Draft tab are from before P3.7 and still show the wrapped 116 px header: those are cut below it.
    """
    from PIL import Image
    for dst, src in REUSED.items():
        if dst in CUT_OLD_HEADER:
            im = Image.open(EVID / src)
            im.crop((0, 116, im.size[0], im.size[1])).save(IMG / dst)
        else:
            shutil.copyfile(EVID / src, IMG / dst)
        print("reused", dst, "<-", src)


def main(port):
    base = f"http://localhost:{port}/"
    if DRAFT.exists():          # checked first: the Draft shots need a clean build
        raise SystemExit(f"{DRAFT} exists - move it away first, this script deletes it")
    IMG.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        for name, (query, w, h) in SHOTS.items():
            out = IMG / f"{name}.png"
            subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            f"--window-size={w},{h}", "--virtual-time-budget=8000",
                            f"--user-data-dir={profile}", f"--screenshot={out}", base + "?" + query],
                           check=False, capture_output=True, timeout=120)
            print(name, out.exists())
    extra_ui_shots(base)
    ui_shots(base)
    copy_reused()
    crops()
    (IMG / "tree_full.png").unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765, help="port of the running explorer (default 8765)")
    ap.add_argument("--crop-only", action="store_true", help="only re-cut the element images")
    args = ap.parse_args()
    crops() if args.crop_only else main(args.port)
