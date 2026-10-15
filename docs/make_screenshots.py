"""Regenerates the screenshots in docs/img with headless Chrome.

Prerequisites: the explorer runs on http://localhost:<port> (default 8765, e.g.
`py dt_explorer.py <workspace> --no-browser`), the RTOS4 lab builds exist (apps/lab0..lab3,
_soltest/apps/lab1_leer) and Chrome is installed.

Most shots are plain Chrome screenshots of a URL. The dialogs (Configure…, New build…)
and the Draft tab need clicks, so they are driven with Playwright (`py -m pip install playwright`).
They save one draft in apps\\lab1\\.dt_explorer and delete it again (and the folder, if it was
created for it). The script refuses to start while that draft file exists.

    py docs/make_screenshots.py [--port 8765] [--crop-only]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
IMG = Path(__file__).parent / "img"
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
    "pins_tab":     (LAB1 + "&tab=pins", 1500, 1000),
    "package_tab":  (LAB1 + "&tab=package", 1500, 1000),
}


CROPS = {  # element image -> (source screenshot, box (left, top, right, bottom))
    "el_header":        ("node_led", (0, 0, 1500, 116)),
    # boxes are measured below the header, which is 116 px high since the origin chips wrap (see GUI.md 1)
    "el_tree":          ("node_led", (0, 116, 515, 662)),
    "el_node_head":     ("node_led", (515, 139, 1490, 384)),
    "el_props":         ("node_led", (515, 800, 1490, 1112)),
    "el_code_macros":   ("node_led", (515, 1117, 1490, 1700)),
    "el_binding":       ("node_binding", (515, 354, 1490, 1164)),
    "el_nobinding":     ("node_nobinding", (515, 139, 1490, 504)),
    "el_coderefs":      ("overview", (515, 270, 1490, 757)),
    "el_child_state":   ("node_button", (515, 137, 1490, 362)),
    "el_include_tree":  ("include_file", (0, 114, 515, 867)),
    "el_include_file":  ("include_file", (515, 137, 1490, 652)),
    "el_header_defs":   ("include_header", (515, 137, 1490, 732)),
    "el_orphan":        ("orphan", (0, 120, 1500, 224)),
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
    ui_shots(base)
    crops()
    (IMG / "tree_full.png").unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765, help="port of the running explorer (default 8765)")
    ap.add_argument("--crop-only", action="store_true", help="only re-cut the element images")
    args = ap.parse_args()
    crops() if args.crop_only else main(args.port)
