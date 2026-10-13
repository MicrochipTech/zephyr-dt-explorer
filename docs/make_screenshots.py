"""Regenerates the screenshots in docs/img with headless Chrome.

Prerequisites: the explorer runs on http://localhost:8765 (e.g. `py dt_explorer.py --no-browser`)
and the RTOS4 lab builds exist (apps/lab0..lab3, _soltest/apps/lab1_leer).

    py docs/make_screenshots.py
"""
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
BASE = "http://localhost:8765/"
IMG = Path(__file__).parent / "img"

SHOTS = {  # name -> (query, width, height)
    "overview":     ("build=apps/lab3/build_same54_xpro", 1500, 1000),
    "node_led":     ("build=apps/lab1/build_same54_xpro&node=" + quote("/my_led@41008000", safe="")
                     + "&macros=1", 1500, 1500),
    "node_button":  ("build=apps/lab0/build_same54_xpro&node=" + quote("/buttons/button_0", safe=""), 1500, 1000),
    "tree_full":    ("build=apps/lab0/build_same54_xpro", 1500, 3000),
    "include_file": ("build=apps/lab3/build_same54_xpro&file=" + quote(r"zephyr\dts\arm\atmel\same5x.dtsi", safe=""),
                     1500, 1000),
    "include_header": ("build=apps/lab3/build_same54_xpro&file="
                       + quote(r"zephyr\include\zephyr\dt-bindings\gpio\gpio.h", safe=""), 1500, 1000),
    "source":       ("build=apps/lab3/build_same54_xpro&node=" + quote("/soc/sercom@41014000", safe="")
                     + "&src=" + quote(r"apps\lab3\boards\same54_xpro.overlay", safe="") + "&line=20", 1500, 1000),
    "orphan":       ("build=_soltest/apps/lab1_leer/build_same54_xpro", 1500, 700),
}


CROPS = {  # element image -> (source screenshot, box (left, top, right, bottom))
    "el_header":        ("node_led", (0, 0, 1500, 72)),
    "el_tree":          ("node_led", (0, 72, 515, 620)),
    "el_node_head":     ("node_led", (515, 95, 1490, 340)),
    "el_binding":       ("node_led", (515, 345, 1490, 570)),
    "el_props":         ("node_led", (515, 575, 1490, 888)),
    "el_code_macros":   ("node_led", (515, 893, 1490, 1480)),
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
    legend()


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        for name, (query, w, h) in SHOTS.items():
            out = IMG / f"{name}.png"
            subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            f"--window-size={w},{h}", "--virtual-time-budget=8000",
                            f"--user-data-dir={profile}", f"--screenshot={out}", BASE + "?" + query],
                           check=False, capture_output=True, timeout=120)
            print(name, out.exists())
    crops()
    (IMG / "tree_full.png").unlink(missing_ok=True)


if __name__ == "__main__":
    import sys
    crops() if "--crop-only" in sys.argv else main()
