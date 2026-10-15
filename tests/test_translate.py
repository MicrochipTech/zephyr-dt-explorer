"""Tests for core/translate.py and the built-in atmel,sam0 -> microchip g1 translations (P5.2).

Core tests need nothing outside the repo. The edtlib tests need DTX_RTOS4_WS (Zephyr v4.3.0
workspace with the lab1 build) and a Zephyr SDK compiler for the preprocessor.
"""
from __future__ import annotations

import copy
import glob
import subprocess
from pathlib import Path

import pytest
import yaml

from dt_explorer.core import plugins
from dt_explorer.core import translate as tr
from dt_explorer.core.translate import Ref, Sym, TranslationError
import dt_explorer.translations as builtin

YAML_PATH = Path(builtin.__file__).with_name("atmel_sam0_to_mchp_g1.yaml")


@pytest.fixture(autouse=True)
def _registry():
    plugins.unregister_all()
    yield
    plugins.unregister_all()


def _doc(entries, **extra):
    return {"schema_version": 1, "name": "t", "translations": entries, **extra}


def _one(entry, hooks=None, **extra):
    return tr.translations_from_dict(_doc([entry], **extra), hooks=hooks)[0]


# ---- core: format and engine (no workspace needed) --------------------------------------------

def test_keep_rename_drop_and_unlisted_are_reported():
    t = _one({"source": "a,x", "target": "b,y", "keep": ["k"],
              "properties": {"old": {"rename": "new"}, "gone": {"drop": "not needed"}}})
    out, lines = t.translate({"k": 1, "old": "v", "gone": True, "mystery": 7})
    assert out == {"compatible": ["b,y"], "k": 1, "new": "v"}
    assert any("gone" in l and "not needed" in l for l in lines)
    unlisted = [l for l in lines if "mystery" in l]
    assert len(unlisted) == 1 and "unlisted" in unlisted[0]
    assert not any("'k'" in l for l in lines)


def test_unlisted_property_is_never_in_result():
    t = _one({"source": "a,x", "target": "b,y", "keep": ["k"]})
    out, lines = t.translate({"k": 1, "extra": [1, 2]})
    assert "extra" not in out and any("extra" in l for l in lines)


def test_absent_listed_property_produces_nothing():
    t = _one({"source": "a,x", "target": "b,y", "keep": ["k", "m"], "properties": {"d": {"drop": "r"}}})
    out, lines = t.translate({"k": 1})
    assert out == {"compatible": ["b,y"], "k": 1} and lines == []


def test_transform_map_const_and_to():
    doc = _doc([{"source": "a,x", "target": "b,y",
                 "properties": {"pins": {"transform": {"map": "m"}},
                                "cells": {"transform": {"const": 1}, "to": "cells2"}}}],
               tables={"m": {"OLD": "NEW", "NONE": None}})
    t = tr.translations_from_dict(doc)[0]
    out, lines = t.translate({"pins": [Sym("OLD", 5), Sym("NONE", 6), Sym("SAME", 7)], "cells": 2})
    assert [p.name for p in out["pins"]] == ["NEW", "NONE", "SAME"]
    assert [int(p) for p in out["pins"]] == [5, 6, 7]
    assert out["cells2"] == 1 and "cells" not in out
    assert len(lines) == 1 and "NONE" in lines[0] and "no counterpart" in lines[0]


def test_derive_consumes_silently_whatever_the_order():
    def hook(props, ctx):
        return {"merged": props["a"] + props["b"]}, ["derived"]
    t = _one({"source": "a,x", "target": "b,y",
              "properties": {"a": {"derive": {"hook": "h", "consumes": ["b"]}}}}, hooks={"h": hook})
    out, lines = t.translate({"b": 2, "a": 1})
    assert out["merged"] == 3 and "b" not in out and lines == ["derived"]


def test_hook_missing_is_a_load_error():
    with pytest.raises(TranslationError, match="hook"):
        _one({"source": "a,x", "target": "b,y", "properties": {"p": {"derive": {"hook": "nope"}}}})


@pytest.mark.parametrize("bad", [
    {"properties": {"p": {"rename": "q", "drop": "r"}}},      # two actions
    {"properties": {"p": {"frobnicate": 1}}},                  # unknown action
    {"properties": {"p": {"drop": ""}}},                       # drop without reason
    {"properties": {"p": {"transform": {"map": "t", "const": 1}}}},
    {"properties": {"p": {"transform": {"map": "missing_table"}}}},
    {"bogus": 1},                                              # unknown entry key
])
def test_malformed_entries_are_rejected(bad):
    with pytest.raises(TranslationError):
        _one({"source": "a,x", "target": "b,y", **bad})


def test_schema_version_is_checked():
    with pytest.raises(TranslationError, match="schema_version"):
        tr.translations_from_dict({"schema_version": 2, "translations": []})
    with pytest.raises(TranslationError, match="schema_version"):
        tr.translations_from_dict({"translations": []})


def test_no_target_entry_gives_a_report_line():
    t = _one({"source": "a,x", "no_target": "nothing there"})
    out, lines = t.translate({"p": 1, "q": 2})
    assert out == {} and len(lines) == 1 and "nothing there" in lines[0] and "a,x" in lines[0]


def test_groups_and_emit_compatible():
    doc = _doc([{"source": "a,x", "target": "b,y", "emit_compatible": False, "use": ["g"], "keep": ["own"]}],
               groups={"g": {"keep": ["shared"]}})
    out, lines = tr.translations_from_dict(doc)[0].translate({"shared": 1, "own": 2})
    assert out == {"shared": 1, "own": 2} and lines == []


def test_registration_uses_the_registry(tmp_path):
    f = tmp_path / "t.yaml"
    f.write_text(yaml.safe_dump(_doc([{"source": "a,x", "target": "b,y", "keep": ["k"]}])), encoding="utf-8")
    tr.register_file(f)
    t = plugins.get_translation("a,x", "b,y")
    assert t is not None and t.translate({"k": 1})[0]["k"] == 1


def test_render_node_symbolic_and_numeric():
    props = {"compatible": ["b,y"], "status": "okay", "flag": True, "off": False, "n": 3,
             "clocks": [[Ref("c"), Sym("CLK_A", 0x20)], [Ref("d"), 4]], "phs": [Ref("p")], "arr": [1, 2]}
    sym = tr.render_node("&n", props)
    assert 'compatible = "b,y";' in sym and "flag;" in sym and "off" not in sym
    assert "clocks = <&c CLK_A>, <&d 4>;" in sym and "phs = <&p>;" in sym and "arr = <1 2>;" in sym
    assert "clocks = <&c 32>, <&d 4>;" in tr.render_node("&n", props, symbolic=False)


# ---- the built-in translation set ---------------------------------------------------------------

def test_builtin_set_registers_all_pairs():
    loaded = builtin.register_all()
    assert len(loaded) == len(set((t.source, t.target) for t in loaded))
    assert plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart") is not None
    assert plugins.get_translation("atmel,sam0-gpio", "microchip,port-g1-gpio") is not None
    assert plugins.get_translation("atmel,sam0-pinctrl", "microchip,port-g1-pinctrl") is not None
    assert plugins.get_translation("atmel,sam0-sercom", "microchip,sercom-g1") is not None


def test_builtin_i2c_spi_report_no_target():
    """The no_target fallback entries stay: a consumer picks them for a target workspace without the binding."""
    builtin.register_all()
    for src, tgt in (("atmel,sam0-i2c", "microchip,sercom-g1-i2c"), ("atmel,sam0-spi", "microchip,sercom-g1-spi")):
        t = plugins.get_translation(src, None)
        assert t is not None and t.no_target and "v4.3.0" in t.no_target and tgt in t.no_target
        out, lines = t.translate({"clock-frequency": 400000})
        assert out == {} and len(lines) == 1
        assert "no target binding in this workspace" in lines[0] and "v4.3.0" in lines[0]


def test_builtin_i2c_spi_have_real_translations_too():
    builtin.register_all()
    assert plugins.get_translation("atmel,sam0-i2c", "microchip,sercom-g1-i2c") is not None
    assert plugins.get_translation("atmel,sam0-spi", "microchip,sercom-g1-spi") is not None


def _uart(**over):
    base = {"compatible": ["atmel,sam0-uart"], "status": "okay", "reg": [0x41012000, 0x40],
            "interrupts": [54, 0, 55, 0, 56, 0, 57, 0],
            "clocks": [[Ref("gclk"), 23], [Ref("mclk"), 0x18, 9]], "clock-names": ["GCLK", "MCLK"],
            "atmel,assigned-clocks": [[Ref("gclk"), 0]], "atmel,assigned-clock-names": ["GCLK"],
            "current-speed": 115200, "rxpo": 1, "txpo": 0, "pinctrl-0": [Ref("sercom2_uart_default")],
            "pinctrl-names": ["default"]}
    base.update(over)
    return base


def test_uart_clock_derivation_and_actions():
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart")
    out, lines = t.translate(_uart())
    assert out["compatible"] == ["microchip,sercom-g1-uart"]
    assert out["clock-names"] == ["mclk", "gclk"]
    (mref, mid), (gref, gid) = out["clocks"]
    assert (mref.label, mid.name) == ("mclkperiph", "CLOCK_MCHP_MCLKPERIPH_ID_APBB_SERCOM2")
    assert (gref.label, gid.name) == ("gclkperiph", "CLOCK_MCHP_GCLKPERIPH_ID_SERCOM2_CORE")
    assert "atmel,assigned-clocks" not in out and "atmel,assigned-clock-names" not in out
    assert any('gclkperiph-src = "gclk0"' in l and "sercom2" in l for l in lines)
    assert not any("unlisted" in l for l in lines)


def test_uart_unlisted_property_is_reported():
    t = (builtin.register_all(), plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart"))[1]
    out, lines = t.translate(_uart(**{"vendor,surprise": 5}))
    assert "vendor,surprise" not in out
    assert [l for l in lines if "vendor,surprise" in l and "unlisted" in l]


def test_uart_dmas_kept_with_warning():
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart")
    out, lines = t.translate(_uart(dmas=[[Ref("dma"), 1]], **{"dma-names": ["tx"]}))
    assert out["dmas"] == [[Ref("dma"), 1]] and out["dma-names"] == ["tx"]
    assert sum("DMA" in l for l in lines) == 1


def test_unknown_gclk_channel_is_reported_not_guessed():
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart")
    out, lines = t.translate(_uart(clocks=[[Ref("gclk"), 99], [Ref("mclk"), 0x18, 9]]))
    assert "clocks" not in out and "clock-names" not in out
    assert any("GCLK channel 99" in l for l in lines)


def test_gpio_rename_and_pin_group_macro_renames():
    builtin.register_all()
    g = plugins.get_translation("atmel,sam0-gpio", "microchip,port-g1-gpio")
    out, lines = g.translate({"compatible": ["atmel,sam0-gpio"], "reg": [0x41008100, 0x80],
                              "#atmel,pin-cells": 2, "#gpio-cells": 2, "gpio-controller": True})
    assert out["#microchip,pin-cells"] == 2 and "#atmel,pin-cells" not in out and lines == []
    c = plugins.get_translation("atmel,sam0-pinctrl/child", "microchip,port-g1-pinctrl/child")
    out, lines = c.translate({"pinmux": [Sym("PA10B_PTC_XY8", 5), Sym("PA30H_SWD_CLK", 6), Sym("PB25D_SERCOM2_PAD0", 7)],
                              "drive-strength": 1})
    assert "compatible" not in out
    assert [p.name for p in out["pinmux"]] == ["PA10B_ADC0_X8", "PA30H_SWD_CLK", "PB25D_SERCOM2_PAD0"]
    assert [int(p) for p in out["pinmux"]] == [5, 6, 7]
    assert len(lines) == 1 and "PA30H_SWD_CLK" in lines[0]


def test_pin_rename_table_has_the_92_old_only_names():
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    assert len(doc["tables"]["pin_renames"]) == 92


def test_every_property_gets_exactly_one_action_in_the_uart_entry():
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["translations"] if e["source"] == "atmel,sam0-uart")
    names = list(entry["keep"]) + list(entry["properties"])
    assert len(names) == len(set(names))


def test_core_has_no_vendor_terms():
    import re
    text = Path(tr.__file__).read_text(encoding="utf-8")
    assert not re.search(r"atmel|microchip|sercom|atdf", text, re.I)


# ---- edtlib acceptance (needs DTX_RTOS4_WS) ------------------------------------------------------

def _sdk_gcc() -> Path:
    hits = sorted(glob.glob(str(Path.home() / "zephyr-sdk-*" / "arm-zephyr-eabi" / "bin" / "arm-zephyr-eabi-gcc.exe")))
    if not hits:
        pytest.skip("Zephyr SDK arm-zephyr-eabi-gcc not found (needed as preprocessor)")
    return Path(hits[-1])


@pytest.fixture(scope="module")
def edtlib(rtos4_ws):
    from dt_explorer.core.model_host import EdtlibHost  # noqa: PLC0415
    return EdtlibHost.for_zephyr_base(rtos4_ws / "zephyr").edtlib


@pytest.fixture(scope="module")
def old_edt(rtos4_ws, rtos4_build, edtlib):
    build = rtos4_build("apps/lab1/build_same54_xpro")
    from dt_explorer.core.model_host import EdtlibHost  # noqa: PLC0415
    return EdtlibHost.for_zephyr_base(rtos4_ws / "zephyr").load_edt(build / "zephyr" / "edt.pickle")


def _convert(node) -> dict:
    """edtlib Node -> translator props: only properties written in the devicetree source."""
    props: dict = {}
    for name in node._node.props:
        if name == "phandle":
            continue
        p = node.props.get(name)
        if p is None:
            props[name] = node._node.props[name].to_num() if name.startswith("#") else True
            continue
        v = p.val
        if p.type == "boolean":
            if not v:
                continue
            props[name] = True
        elif p.type == "phandle-array":
            props[name] = [[Ref(e.node.labels[0])] + [int(x) for x in e.data.values()] for e in v]
        elif p.type == "phandles":
            props[name] = [Ref(n.labels[0]) for n in v]
        elif p.type in ("array", "uint8-array"):
            props[name] = [int(x) for x in v]
        elif p.type == "string-array":
            props[name] = list(v)
        else:
            props[name] = v
    return props


def _preprocess(ws: Path, text: str, workdir: Path) -> Path:
    z = ws / "zephyr"
    src = workdir / "in.dts"
    src.write_text(
        "/dts-v1/;\n"
        "#include <microchip/sam/sam_d5x_e5x/atsame54/atsame54p20a.dtsi>\n"
        '#include "sam_e54_xpro-pinctrl.dtsi"\n' + text, encoding="utf-8")
    out = workdir / "out.dts"
    incs = [z / "dts" / "common", z / "dts" / "arm", z / "dts", z / "include",
            z / "boards" / "microchip" / "sam" / "sam_e54_xpro", ws / "modules" / "hal" / "microchip" / "include"]
    cmd = [str(_sdk_gcc()), "-E", "-P", "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__"]
    for i in incs:
        cmd += ["-I", str(i)]
    cmd += ["-o", str(out), str(src)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out


def load_edt(ws: Path, edtlib, overlay: str, workdir: Path):
    """Preprocess the target SoC + pin dtsi plus `overlay` and load it with edtlib (raises EDTError)."""
    dts = _preprocess(ws, overlay, workdir)
    return edtlib.EDT(str(dts), [str(ws / "zephyr" / "dts" / "bindings")], warn_reg_unit_address_mismatch=False)


def overlay_for(label: str, props: dict) -> str:
    return tr.render_node(f"&{label}", props, symbolic=False) + "\n"


def uart_translation():
    builtin.register_all()
    return plugins.get_translation("atmel,sam0-uart", "microchip,sercom-g1-uart")


def test_sercom2_uart_accepted_by_edtlib(rtos4_ws, old_edt, edtlib, tmp_path, capsys):
    src = _convert(old_edt.label2node["sercom2"])
    out, lines = uart_translation().translate(src)
    node = load_edt(rtos4_ws, edtlib, overlay_for("sercom2", out), tmp_path).label2node["sercom2"]
    assert node.matching_compat == "microchip,sercom-g1-uart"
    assert node.status == "okay"
    assert node.props["rxpo"].val == 1 and node.props["current-speed"].val == 115200
    with capsys.disabled():
        print("\n--- source props (from lab1 edt.pickle) ---")
        print(tr.render_node("sercom2", src))
        print("--- translated node ---")
        print(tr.render_node("&sercom2", out))
        print("--- report lines ---")
        print("\n".join(lines))
        print("--- edtlib: matched", node.matching_compat, "binding", Path(node.binding_path).name, "---")


def test_translated_clocks_equal_the_target_soc_values(rtos4_ws, old_edt, edtlib, tmp_path):
    out, _ = uart_translation().translate(_convert(old_edt.label2node["sercom2"]))
    edt = load_edt(rtos4_ws, edtlib, "", tmp_path)
    soc = edt.label2node["sercom2"].props["clocks"].val
    mine = [(e[0].label, int(e[1])) for e in out["clocks"]]
    assert mine == [(e.controller.labels[0], int(e.data["subsystem"])) for e in soc]


def test_negative_dropping_a_required_property_makes_edtlib_fail(rtos4_ws, old_edt, edtlib, tmp_path):
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["translations"] if e["source"] == "atmel,sam0-uart")
    entry["keep"].remove("rxpo")                                   # rxpo is required by the target binding
    entry["properties"]["rxpo"] = {"drop": "negative test"}
    broken = tr.translations_from_dict(doc, YAML_PATH.parent)
    t = next(x for x in broken if x.source == "atmel,sam0-uart")
    out, lines = t.translate(_convert(old_edt.label2node["sercom2"]))
    assert "rxpo" not in out and any("rxpo" in l for l in lines)
    with pytest.raises(edtlib.EDTError, match="rxpo"):
        load_edt(rtos4_ws, edtlib, overlay_for("sercom2", out), tmp_path)


def test_gpio_node_accepted_by_edtlib(rtos4_ws, old_edt, edtlib, tmp_path):
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-gpio", "microchip,port-g1-gpio")
    out, lines = t.translate(_convert(old_edt.label2node["portc"]))
    assert lines == []
    node = load_edt(rtos4_ws, edtlib, overlay_for("portc", out) + '&portc { status = "okay"; };\n',
                    tmp_path).label2node["portc"]
    assert node.matching_compat == "microchip,port-g1-gpio"


def test_pinctrl_state_group_translates(rtos4_ws, old_edt, edtlib, tmp_path):
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-pinctrl/child", "microchip,port-g1-pinctrl/child")
    group = old_edt.label2node["sercom2_uart_default"].children["group1"]
    out, lines = t.translate(_convert(group))
    assert lines == [] and out["pinmux"] == [int(x) for x in group.props["pinmux"].val]


# ---- P5.2b: I2C and SPI against Zephyr main -----------------------------------------------------------

def _i2c(**over):
    base = {"compatible": ["atmel,sam0-i2c"], "status": "okay", "reg": [0x43000C00, 0x40],
            "interrupts": [74, 0, 75, 0, 76, 0, 77, 0],
            "clocks": [[Ref("gclk"), 37], [Ref("mclk"), 0x20, 3]], "clock-names": ["GCLK", "MCLK"],
            "atmel,assigned-clocks": [[Ref("gclk"), 0]], "atmel,assigned-clock-names": ["GCLK"],
            "clock-frequency": 400000, "#address-cells": 1, "#size-cells": 0,
            "pinctrl-0": [Ref("sercom7_i2c_default")], "pinctrl-names": ["default"]}
    base.update(over)
    return base


def _spi(**over):
    base = {"compatible": ["atmel,sam0-spi"], "status": "okay", "reg": [0x43000000, 0x40],
            "interrupts": [62, 0, 63, 0, 64, 0, 65, 0],
            "clocks": [[Ref("gclk"), 34], [Ref("mclk"), 0x20, 0]], "clock-names": ["GCLK", "MCLK"],
            "atmel,assigned-clocks": [[Ref("gclk"), 0]], "atmel,assigned-clock-names": ["GCLK"],
            "dipo": 3, "dopo": 0, "#address-cells": 1, "#size-cells": 0,
            "pinctrl-0": [Ref("sercom4_spi_default")], "pinctrl-names": ["default"]}
    base.update(over)
    return base


def test_i2c_spi_translation_actions_without_a_workspace():
    builtin.register_all()
    ti = plugins.get_translation("atmel,sam0-i2c", "microchip,sercom-g1-i2c")
    out, lines = ti.translate(_i2c(**{"vendor,surprise": 1}))
    assert out["compatible"] == ["microchip,sercom-g1-i2c"] and out["clock-frequency"] == 400000
    assert out["clock-names"] == ["mclk", "gclk"]
    (mref, mid), (gref, gid) = out["clocks"]
    assert (mid.name, gid.name) == ("CLOCK_MCHP_MCLKPERIPH_ID_APBD_SERCOM7", "CLOCK_MCHP_GCLKPERIPH_ID_SERCOM7_CORE")
    assert "atmel,assigned-clocks" not in out and "vendor,surprise" not in out
    assert any('gclkperiph-src = "gclk0"' in l and "sercom7" in l for l in lines)
    assert [l for l in lines if "vendor,surprise" in l and "unlisted" in l]
    assert not any("DMA" in l for l in lines)            # the g1 I2C driver uses DMA, no warning as on UART
    ts = plugins.get_translation("atmel,sam0-spi", "microchip,sercom-g1-spi")
    out, lines = ts.translate(_spi(**{"cs-gpios": [[Ref("portc"), 24, 1]], "overrun-character": 255,
                                      "dmas": [[Ref("dmac"), 3, 4]], "dma-names": ["rx"]}))
    assert out["dipo"] == 3 and out["dopo"] == 0 and out["cs-gpios"] == [[Ref("portc"), 24, 1]]
    assert out["overrun-character"] == 255 and out["dmas"] == [[Ref("dmac"), 3, 4]]
    assert (out["clocks"][0][1].name, out["clocks"][1][1].name) == (
        "CLOCK_MCHP_MCLKPERIPH_ID_APBD_SERCOM4", "CLOCK_MCHP_GCLKPERIPH_ID_SERCOM4_CORE")
    assert not any("unlisted" in l for l in lines)


def test_i2c_spi_entries_list_each_property_once_and_name_the_base_additions():
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    for src in ("atmel,sam0-i2c", "atmel,sam0-spi"):
        entry = next(e for e in doc["translations"] if e["source"] == src and e.get("target"))
        names = list(entry["keep"]) + list(entry.get("properties") or [])
        assert len(names) == len(set(names))
        assert {"ranges", "dma-coherent", "dma-ranges"} <= set(entry["target_only"])


def _edtlib_of(ws: Path):
    from dt_explorer.core.model_host import EdtlibHost  # noqa: PLC0415
    return EdtlibHost.for_zephyr_base(ws / "zephyr").edtlib


@pytest.fixture(scope="module")
def main_edtlib(main_ws):
    return _edtlib_of(main_ws)


def _preprocess_sg41(ws: Path, text: str, workdir: Path) -> Path:
    """The pic32cx_sg41_cult SoC dtsi and board pinctrl dtsi of Zephyr main, plus `text`."""
    z = ws / "zephyr"
    board = z / "boards" / "microchip" / "pic32c" / "pic32cx_sg41_cult"
    src = workdir / "in_sg41.dts"
    src.write_text(
        "/dts-v1/;\n"
        "#include <microchip/pic32c/pic32cx_sg/pic32cx_sg41/pic32cx1025sg41128.dtsi>\n"
        '#include "pic32cx_sg41_cult-pinctrl.dtsi"\n' + text, encoding="utf-8")
    out = workdir / "out_sg41.dts"
    incs = [z / "dts" / "common", z / "dts" / "arm", z / "dts", z / "include", board,
            ws / "modules" / "hal" / "microchip" / "include"]
    cmd = [str(_sdk_gcc()), "-E", "-P", "-x", "assembler-with-cpp", "-nostdinc", "-undef", "-D__DTS__"]
    for i in incs:
        cmd += ["-I", str(i)]
    cmd += ["-o", str(out), str(src)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out


def load_edt_sg41(ws: Path, edtlib, overlay: str, workdir: Path):
    dts = _preprocess_sg41(ws, overlay, workdir)
    return edtlib.EDT(str(dts), [str(ws / "zephyr" / "dts" / "bindings")], warn_reg_unit_address_mismatch=False)


def _pinctrl_state(rtos4_ws: Path, old_edt, label: str) -> str:
    """Old pin state `label` -> pin state node for `&pinctrl` of the target (macro names go through pin_renames)."""
    import re
    text = (rtos4_ws / "zephyr" / "boards" / "atmel" / "sam0" / "same54_xpro" / "same54_xpro-pinctrl.dtsi"
            ).read_text(encoding="utf-8")
    start = text.index(label + ": " + label)
    body = text[start:text.index("\t};", start)]
    names = re.findall(r"\b(P[A-D]\d+[A-P]_\w+)\b", body)
    values = [int(x) for x in old_edt.label2node[label].children["group1"].props["pinmux"].val]
    assert len(names) == len(values), (label, names, values)
    builtin.register_all()
    ct = plugins.get_translation("atmel,sam0-pinctrl/child", "microchip,port-g1-pinctrl/child")
    out, lines = ct.translate({"pinmux": [Sym(n, v) for n, v in zip(names, values)]})
    assert lines == [], lines
    return f"&pinctrl {{\n\t{label}: {label} {{\n\t\tgroup1 {{\n\t\t\t{tr.render_property('pinmux', out['pinmux'])}\n\t\t}};\n\t}};\n}};\n"


def _child_lines(node) -> list[str]:
    """Children are out of the node translation; the caller reports them."""
    return [f"child {c.path.rsplit('/', 1)[-1]} (compatible {', '.join(c.compats) or 'none'}): not part of the "
            f"node translation, carry over by hand" for c in node.children.values()]


def test_lab1_sercom4_spi_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-spi", "microchip,sercom-g1-spi")
    src = _convert(old_edt.label2node["sercom4"])
    out, lines = t.translate(src)
    pins = _pinctrl_state(rtos4_ws, old_edt, "sercom4_spi_default")
    node = load_edt_sg41(main_ws, main_edtlib, pins + overlay_for("sercom4", out), tmp_path).label2node["sercom4"]
    assert node.matching_compat == "microchip,sercom-g1-spi" and node.status == "okay"
    assert node.props["dipo"].val == 3 and node.props["dopo"].val == 0
    assert [int(e.data["subsystem"]) for e in node.props["clocks"].val] == [int(c[1]) for c in out["clocks"]]
    assert node.props["pinctrl-0"].val[0].labels[0] == "sercom4_spi_default"
    with capsys.disabled():
        print("\n--- source props (lab1 sercom4, atmel,sam0-spi) ---")
        print(tr.render_node("sercom4", src))
        print("--- translated node ---")
        print(tr.render_node("&sercom4", out))
        print("--- pin state added to the target overlay ---")
        print(pins)
        print("--- report lines ---")
        print("\n".join(lines + _child_lines(old_edt.label2node["sercom4"])))
        print("--- edtlib (Zephyr main):", node.matching_compat, "binding", Path(node.binding_path).name, "---")


def test_lab1_sercom7_i2c_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-i2c", "microchip,sercom-g1-i2c")
    n7 = old_edt.label2node["sercom7"]
    src = _convert(n7)
    out, lines = t.translate(src)
    pins = _pinctrl_state(rtos4_ws, old_edt, "sercom7_i2c_default")
    node = load_edt_sg41(main_ws, main_edtlib, pins + overlay_for("sercom7", out), tmp_path).label2node["sercom7"]
    assert node.matching_compat == "microchip,sercom-g1-i2c" and node.status == "okay"
    assert node.props["clock-frequency"].val == 400000
    assert node.props["run-in-standby-en"].val == 1                     # target default, no source counterpart
    assert [int(e.data["subsystem"]) for e in node.props["clocks"].val] == [int(c[1]) for c in out["clocks"]]
    kids = _child_lines(n7)
    assert len(kids) == 1 and "eeprom@5e" in kids[0]
    with capsys.disabled():
        print("\n--- source props (lab1 sercom7, atmel,sam0-i2c) ---")
        print(tr.render_node("sercom7", src))
        print("--- translated node ---")
        print(tr.render_node("&sercom7", out))
        print("--- pin state added to the target overlay ---")
        print(pins)
        print("--- report lines ---")
        print("\n".join(lines + kids))
        print("--- edtlib (Zephyr main):", node.matching_compat, "binding", Path(node.binding_path).name, "---")


def test_translated_i2c_spi_values_equal_the_sg41_soc_values(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    builtin.register_all()
    soc = load_edt_sg41(main_ws, main_edtlib, "", tmp_path)
    for label, source, target in (("sercom4", "atmel,sam0-spi", "microchip,sercom-g1-spi"),
                                  ("sercom7", "atmel,sam0-i2c", "microchip,sercom-g1-i2c")):
        src = _convert(old_edt.label2node[label])
        out, _ = plugins.get_translation(source, target).translate(src)
        node = soc.label2node[label]
        assert [(e[0].label, int(e[1])) for e in out["clocks"]] == [
            (e.controller.labels[0], int(e.data["subsystem"])) for e in node.props["clocks"].val]
        assert out["interrupts"] == [int(x) for x in node.props["interrupts"].val]
        assert out["reg"][0] == node.regs[0].addr


def test_negative_dropping_dopo_makes_main_edtlib_fail(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in doc["translations"] if e["source"] == "atmel,sam0-spi" and e.get("target"))
    entry["keep"].remove("dopo")                                   # dopo is required by the target binding
    entry["properties"] = {**(entry.get("properties") or {}), "dopo": {"drop": "negative test"}}
    t = next(x for x in tr.translations_from_dict(doc, YAML_PATH.parent) if x.source == "atmel,sam0-spi" and x.target)
    out, lines = t.translate(_convert(old_edt.label2node["sercom4"]))
    assert "dopo" not in out and any("dopo" in l for l in lines)
    pins = _pinctrl_state(rtos4_ws, old_edt, "sercom4_spi_default")
    with pytest.raises(main_edtlib.EDTError, match="dopo"):
        load_edt_sg41(main_ws, main_edtlib, pins + overlay_for("sercom4", out), tmp_path)


# ---- capability: which of the two entries fits a target workspace -----------------------------------------

def _binding_exists(ws: Path, compatible: str) -> bool:
    """True if a binding YAML for `compatible` exists under the workspace's zephyr/dts/bindings."""
    import re
    rx = re.compile(r"^compatible:\s*[\"']?" + re.escape(compatible) + r"[\"']?\s*$", re.M)
    return any(rx.search(p.read_text(encoding="utf-8", errors="ignore"))
               for p in (ws / "zephyr" / "dts" / "bindings").rglob(f"{compatible}.yaml"))


def select_translation(source: str, target_ws: Path):
    """The translation of `source` that fits the target workspace: a real one whose target binding exists
    there, else the no_target entry of the same source."""
    cands = [t for t in plugins.translations() if t.source == source]
    for t in cands:
        if t.target and _binding_exists(target_ws, t.target):
            return t
    return next((t for t in cands if t.no_target is not None), None)


@pytest.mark.parametrize("source,target", [("atmel,sam0-i2c", "microchip,sercom-g1-i2c"),
                                           ("atmel,sam0-spi", "microchip,sercom-g1-spi")])
def test_i2c_spi_selected_by_target_workspace_capability(rtos4_ws, main_ws, source, target):
    builtin.register_all()
    assert not _binding_exists(rtos4_ws, target) and _binding_exists(main_ws, target)
    on_main = select_translation(source, main_ws)
    assert on_main.target == target and on_main.no_target is None
    on_old = select_translation(source, rtos4_ws)
    assert on_old.no_target is not None
    out, lines = on_old.translate({"clock-frequency": 400000, "dipo": 3})
    assert out == {} and len(lines) == 1
    assert "no target binding in this workspace" in lines[0] and "v4.3.0" in lines[0]
    assert "2 properties not translated" in lines[0]


# ---- property counts: table rows = binding properties (as P5.0) ----------------------------------------------

def _binding_props(ws: Path, rel: str) -> set[str]:
    root = ws / "zephyr" / "dts" / "bindings"
    fname2path: dict[str, str] = {}
    for p in root.rglob("*.yaml"):
        fname2path.setdefault(p.name, str(p))
    b = _edtlib_of(ws).Binding(str(root / rel), fname2path, require_compatible=False, require_description=False)
    return set(b.prop2specs)


def _table_rows(text: str, start: str, end: str) -> list[list[str]]:
    """Cells of the data rows between the headings `start` and `end` (rows start with a backquote)."""
    body = text[text.index(start):text.index(end)]
    return [[c.strip() for c in line.strip().strip("|").split(" | ")]
            for line in body.splitlines() if line.startswith("| `")]


@pytest.mark.parametrize("num,kind,src_rel,tgt_rel,label,end_src,end_tgt", [
    ("1.2", "I2C", "i2c/atmel,sam0-i2c.yaml", "i2c/microchip,sercom-g1-i2c.yaml", "g1-i2c",
     "### 1.3 ", "### 1.T target-only: SERCOM SPI"),
    ("1.3", "SPI", "spi/atmel,sam0-spi.yaml", "spi/microchip,sercom-g1-spi.yaml", "g1-spi",
     "### 1.4 ", "### 1.T target-only: SERCOM parent")])
def test_binding_pair_table_rows_equal_property_counts(rtos4_ws, main_ws, capsys, num, kind, src_rel, tgt_rel,
                                                       label, end_src, end_tgt):
    doc = (Path(__file__).resolve().parent.parent / "docs" / "research" / "binding_pairs.md"
           ).read_text(encoding="utf-8")
    src = _binding_props(rtos4_ws, src_rel)
    tgt = _binding_props(main_ws, tgt_rel)
    rows = _table_rows(doc, f"### {num} SERCOM", end_src)
    names = [r[0].strip("`").split(" (inh:")[0] for r in rows]
    assert len(names) == len(set(names)) == len(src)
    assert set(names) == src
    mapped = {r[3].strip("`").split(": ", 1)[1] for r in rows if r[3].startswith(f"`{label}:")}
    tonly = {r[1].strip("`") for r in _table_rows(doc, f"### 1.T target-only: SERCOM {kind} ({label})", end_tgt)}
    assert mapped | tonly == tgt and not (mapped & tonly)
    with capsys.disabled():
        print(f"\n{label}: source properties {len(src)} = table rows {len(names)}; target properties {len(tgt)} = "
              f"mapped {len(mapped)} + target-only {len(tonly)}; unmapped source rows "
              f"{sum(1 for r in rows if r[3].startswith('—'))} (drop/derive without target)")


# ---- child nodes (P8.4) ------------------------------------------------------------------------------
def _kids():
    from dt_explorer.core.draft import DraftChild
    return [DraftChild("eeprom", "5e", ["old,eeprom"], ["ee"], {"reg": [0x5e], "size": 256, "old-prop": 1}),
            DraftChild("channel", "2", [], [], {"reg": [2], "gain": "G1"},
                       [DraftChild("sub", "0", ["old,sub"], [], {"reg": [0], "x": 1})]),
            DraftChild("gone", "9", ["old,gone"], [], {"reg": [9]})]


def test_children_pass_through_unchanged_without_rules():
    t = _one({"source": "a,b", "target": "c,d"})
    kids = _kids()
    out, lines = t.translate_children(kids)
    assert out == kids and out is not kids and lines == []
    out[0].props["size"] = 1                                   # a copy: the draft's own child is not touched
    assert kids[0].props["size"] == 256


def test_child_rules_translate_by_compatible_and_name_and_drop():
    t = _one({"source": "a,b", "target": "c,d", "children": [
        {"match": {"compatible": "old,eeprom"}, "target": "new,eeprom", "keep": ["size"],
         "properties": {"old-prop": {"rename": "new-prop"}}},
        {"match": {"name": "channel"}, "keep": ["gain"]},
        {"match": {"compatible": "old,sub"}, "target": "new,sub", "properties": {"x": {"drop": "gone in the target"}}},
        {"match": {"compatible": "old,gone"}, "drop_node": "no counterpart"}]})
    out, lines = t.translate_children(_kids())
    assert [c.node_name for c in out] == ["eeprom@5e", "channel@2"]
    ee = out[0]
    assert ee.compatible == ["new,eeprom"] and ee.labels == ["ee"] and ee.unit == "5e"
    assert ee.props == {"reg": [0x5e], "size": 256, "new-prop": 1}          # reg is kept without a rule
    assert out[1].compatible == [] and out[1].props == {"reg": [2], "gain": "G1"}
    assert out[1].children[0].compatible == ["new,sub"] and out[1].children[0].props == {"reg": [0]}
    assert any("child gone@9 dropped" in x and "no counterpart" in x for x in lines)
    assert any("child sub@0" in x and "gone in the target" in x for x in lines)


def test_child_rule_that_changes_reg_changes_the_unit_address():
    t = _one({"source": "a,b", "target": "c,d", "children": [
        {"match": {"name": "channel"}, "properties": {"reg": {"transform": {"const": [6]}}}}]})
    out, _ = t.translate_children(_kids()[1:2])
    assert out[0].unit == "6" and out[0].props["reg"] == [6]


def test_unlisted_child_property_is_reported_and_left_out():
    t = _one({"source": "a,b", "target": "c,d", "children": [{"match": {"name": "eeprom"}, "keep": ["size"]}]})
    out, lines = t.translate_children(_kids()[:1])
    assert "old-prop" not in out[0].props and any("unlisted property 'old-prop'" in x for x in lines)


@pytest.mark.parametrize("bad", [
    {"children": {"match": {}}},                                   # not a list
    {"children": [{"target": "x"}]},                               # no match
    {"children": [{"match": {}}]},                                 # empty match
    {"children": [{"match": {"size": 1}}]},                        # unknown match key
    {"children": [{"match": {"name": "n"}, "bogus": 1}]},          # unknown key
    {"children": [{"match": {"name": "n"}, "use": ["nope"]}]},     # unknown group
    {"children": [{"match": {"name": "n"}, "properties": {"p": {"transform": {"hook": "nope"}}}}]},
])
def test_malformed_child_rules_are_rejected(bad):
    with pytest.raises(TranslationError):
        _one({"source": "a,b", "target": "c,d", **bad})


# ---- P8.10: translations for further peripherals ------------------------------------------------------------
# Pairs: TC counter, TC PWM, TCC PWM, ADC (+ channel children), DAC (+ children), GMAC + MDIO (+ PHY child), RTC
# (two compatibles), WDT, DMAC, TRNG; CAN, USB and the GMAC wrapper are no_target. Tables: docs/research/peripheral_pairs.md.

P810_PAIRS = [   # (section, source, target, source binding, target binding)
    ("6.1", "atmel,sam0-tc32", "microchip,tc-g1-counter", "counter/atmel,sam0-tc32.yaml",
     "counter/microchip,tc-g1-counter.yaml"),
    ("6.2", "atmel,sam0-tc-pwm", "microchip,tc-g1-pwm", "pwm/atmel,sam0-tc-pwm.yaml", "pwm/microchip,tc-g1-pwm.yaml"),
    ("6.3", "atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm", "pwm/atmel,sam0-tcc-pwm.yaml",
     "pwm/microchip,tcc-g1-pwm.yaml"),
    ("6.5", "atmel,sam0-adc", "microchip,adc-g1", "adc/atmel,sam0-adc.yaml", "adc/microchip,adc-g1.yaml"),
    ("6.6", "atmel,samd5x-dac", "microchip,dac-g1", "dac/atmel,samd5x-dac.yaml", "dac/microchip,dac-g1.yaml"),
    ("6.8", "atmel,sam0-gmac", "microchip,gmac-g1-eth", "ethernet/atmel,sam0-gmac.yaml",
     "ethernet/microchip,gmac-g1-eth.yaml"),
    ("6.9", "atmel,sam-mdio", "microchip,gmac-g1-mdio", "mdio/atmel,sam-mdio.yaml",
     "ethernet/mdio/microchip,gmac-g1-mdio.yaml"),
    ("6.12", "atmel,sam0-rtc", "microchip,rtc-g1", "rtc/atmel,sam0-rtc.yaml", "rtc/microchip,rtc-g1.yaml"),
    ("6.13", "atmel,sam0-watchdog", "microchip,wdt-g1", "watchdog/atmel,sam0-watchdog.yaml",
     "watchdog/microchip,wdt-g1.yaml"),
    ("6.14", "atmel,sam0-dmac", "microchip,dmac-g1-dma", "dma/atmel,sam0-dmac.yaml", "dma/microchip,dmac-g1-dma.yaml"),
    ("6.15", "atmel,sam-trng", "microchip,trng-g1-entropy", "rng/atmel,sam-trng.yaml",
     "rng/microchip,trng-g1-entropy.yaml"),
]


def _all_props(ws: Path, rel: str) -> set[str]:
    """Top-level binding properties plus ``child: <name>`` for every child-binding level (as the tables list them)."""
    root = ws / "zephyr" / "dts" / "bindings"
    fname2path: dict[str, str] = {}
    for p in root.rglob("*.yaml"):
        fname2path.setdefault(p.name, str(p))
    b = _edtlib_of(ws).Binding(str(root / rel), fname2path, require_compatible=False, require_description=False)

    def child(x, prefix: str) -> set[str]:
        return set() if x is None else {prefix + p for p in x.prop2specs} | child(x.child_binding, prefix + "child: ")

    return set(b.prop2specs) | child(b.child_binding, "child: ")


def _section_rows(doc: str, num: str) -> tuple[list[list[str]], list[list[str]]]:
    """(source rows, target-only rows) of section `### 6.N ...` of peripheral_pairs.md."""
    start = doc.index(f"### {num} ")
    nxt = doc.find("\n### ", start + 5)
    body = doc[start:nxt if nxt > 0 else len(doc)]
    rows = [[c.strip() for c in line.strip().strip("|").split(" | ")] for line in body.splitlines()
            if line.startswith("| `")]
    return [r for r in rows if len(r) >= 8], [r for r in rows if len(r) == 4]


def test_p810_entries_registered_and_untranslatable_ones_are_no_target():
    loaded = builtin.register_all()
    for _, src, tgt, _, _ in P810_PAIRS:
        assert plugins.get_translation(src, tgt) is not None, (src, tgt)
    assert plugins.get_translation("atmel,sam0-rtc", "microchip,rtc-g1-counter") is not None
    for src, word in (("atmel,sam0-can", "CAN"), ("atmel,sam0-usb", "USB"),
                      ("microchip,sam-ethernet-controller", "wrapper")):
        t = plugins.get_translation(src, None)
        assert t is not None and t.no_target and t.target is None, src
        out, lines = t.translate({"status": "okay", "reg": [1, 2]})
        assert out == {} and len(lines) == 1 and "no target binding in this workspace" in lines[0]
    assert len(loaded) == len({(t.source, t.target) for t in loaded})


def test_base_group_properties_with_a_comma_are_whole_names():
    """Regression: unquoted `zephyr,deferred-init` in a YAML flow list was split into two bogus names (found at P8.10)."""
    doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    keep = doc["groups"]["base"]["keep"]
    for name in ("zephyr,deferred-init", "zephyr,pm-device-runtime-auto", "zephyr,disabling-power-states"):
        assert name in keep
    assert "zephyr" not in keep and "deferred-init" not in keep


def _hooks_ctx():
    builtin.register_all()
    return plugins.get_translation("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm").tables


def test_pwm_cells_2_to_3_gets_a_consumer_report_line():
    for src, tgt in (("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm"), ("atmel,sam0-tc-pwm", "microchip,tc-g1-pwm")):
        builtin.register_all()
        out, lines = plugins.get_translation(src, tgt).translate(
            {"#pwm-cells": 2, "prescaler": 8, "counter-size": 16, "channels": 2, "status": "okay"})
        assert out["#pwm-cells"] == 3 and out["max-bit-width"] == 16 and out["channels"] == 2 and out["prescaler"] == 8
        assert out["compatible"] == [tgt]
        assert len(lines) == 1 and "third cell" in lines[0] and "not rewritten" in lines[0]


def test_tc_clocks_pair_for_the_counter_and_follow_counter_size_for_pwm():
    builtin.register_all()
    cnt = plugins.get_translation("atmel,sam0-tc32", "microchip,tc-g1-counter")
    src = {"clocks": [[Ref("g"), 9], [Ref("m"), 0x14, 14]], "clock-names": ["GCLK", "MCLK"],
           "atmel,assigned-clocks": [[Ref("g"), 0]], "atmel,assigned-clock-names": ["GCLK"]}
    out, lines = cnt.translate(src)
    assert [(e[0].label, e[1].name) for e in out["clocks"]] == [
        ("mclkperiph", "CLOCK_MCHP_MCLKPERIPH_ID_APBA_TC0"), ("gclkperiph", "CLOCK_MCHP_GCLKPERIPH_ID_TC0"),
        ("mclkperiph", "CLOCK_MCHP_MCLKPERIPH_ID_APBA_TC1")]
    assert out["clock-names"] == ["mclk", "gclk", "client_mclk"] and out["max-bit-width"] == 32
    assert any("pairs TC0 with TC1" in l for l in lines)
    assert any("gclkperiph-src" in l and "CLOCK_MCHP_GCLKPERIPH_ID_TC0" in l for l in lines)      # TC0/TC1 share channel 9
    pwm = plugins.get_translation("atmel,sam0-tc-pwm", "microchip,tc-g1-pwm")
    o16, _ = pwm.translate({**src, "counter-size": 16, "#pwm-cells": 2})
    assert o16["clock-names"] == ["mclk", "gclk"] and o16["max-bit-width"] == 16
    o32, _ = pwm.translate({**src, "counter-size": 32, "#pwm-cells": 2})
    assert o32["clock-names"] == ["mclk", "gclk", "client_mclk"] and o32["max-bit-width"] == 32
    odd, ol = pwm.translate({"clocks": [[Ref("g"), 9], [Ref("m"), 0x14, 15]], "clock-names": ["GCLK", "MCLK"],
                             "counter-size": 32})
    assert odd["clock-names"] == ["mclk", "gclk"] and any("odd instance" in l for l in ol)
    assert odd["clocks"][1][1].name == "CLOCK_MCHP_GCLKPERIPH_ID_TC1"                       # same channel 9, other MCLK bit


def test_shared_gclk_channels_resolve_by_the_mclk_entry():
    builtin.register_all()
    tcc = plugins.get_translation("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm")
    out, lines = tcc.translate({"clocks": [[Ref("g"), 25], [Ref("m"), 0x20 - 8, 12]], "clock-names": ["GCLK", "MCLK"]})
    assert out["clocks"][1][1].name == "CLOCK_MCHP_GCLKPERIPH_ID_TCC1", (out, lines)
    # an unknown combination is reported, never guessed
    bad, bl = tcc.translate({"clocks": [[Ref("g"), 3], [Ref("m"), 0x20 - 8, 12]], "clock-names": ["GCLK", "MCLK"]})
    assert "clocks" not in bad and any("no unique peripheral clock" in l for l in bl)


def test_adc_prescaler_and_channel_children_without_workspace():
    from dt_explorer.core.draft import DraftChild
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-adc", "microchip,adc-g1")
    out, lines = t.translate({"prescaler": 4, "calib-offset": 0, "#io-channel-cells": 1})
    assert out["prescaler"] == 4 and "calib-offset" not in out
    assert any("<= 16 MHz" in l for l in lines) and any("dropped 'calib-offset'" in l for l in lines)
    _, bad = t.translate({"prescaler": 3})
    assert any("not in the target enum" in l for l in bad)
    ch = DraftChild("channel", "6", [], [], {"reg": [6], "zephyr,gain": "ADC_GAIN_1", "zephyr,reference": "ADC_REF_INTERNAL",
                                            "zephyr,acquisition-time": 0, "zephyr,input-positive": 6,
                                            "zephyr,resolution": 12, "old-only": 1})
    kids, cl = t.translate_children([ch])
    assert kids[0].props["zephyr,input-positive"] == 6 and kids[0].props["reg"] == [6] and "old-only" not in kids[0].props
    assert any("unlisted property 'old-only'" in l for l in cl)


def test_dac_value_transforms_without_workspace():
    from dt_explorer.core.draft import DraftChild
    builtin.register_all()
    t = plugins.get_translation("atmel,samd5x-dac", "microchip,dac-g1")
    out, lines = t.translate({"reference": "intref", "differential-mode": True, "#io-channel-cells": 1})
    assert out["refsel"] == "int_ref" and out["#address-cells"] == 1 and out["#size-cells"] == 0
    assert "reference" not in out and "differential-mode" not in out
    assert any("DAC_MCHP_G1_DIFFERENTIAL" in l for l in lines)
    for old, new in (("vrefau", "vref_au"), ("vddana", "vdd_ana"), ("vrefab", "vref_ab")):
        assert t.translate({"reference": old})[0]["refsel"] == new
    assert "refsel" not in t.translate({"reference": "bogus"})[0]
    kid = DraftChild("channel_0", None, [], [], {"oversampling": "OSR_16", "refresh-period": 3,
                                                  "current-control": "CC12M", "run-in-standby": True})
    kids, kl = t.translate_children([kid])
    assert kids[0].props == {"reg": [0], "sampling-ratio": 16, "refresh-period": 90, "rate": 1000}
    assert (kids[0].name, kids[0].unit, kids[0].node_name) == ("channel", "0", "channel@0")
    assert any("run-in-standby" in l and "dropped" in l for l in kl)
    assert any("channel_0 renamed channel@0, reg = <0x0>" in l for l in kl)
    k1, _ = t.translate_children([DraftChild("channel_1", None, [], [], {"current-control": "CC100K"})])
    assert k1[0].props == {"reg": [1], "rate": 100} and k1[0].node_name == "channel@1"


def test_gmac_mdio_phy_without_workspace():
    from dt_explorer.core.draft import DraftChild
    builtin.register_all()
    gmac = plugins.get_translation("atmel,sam0-gmac", "microchip,gmac-g1-eth")
    out, lines = gmac.translate({"phy-handle": [Ref("phy")], "mac-eeprom": [Ref("eeprom")], "max-speed": 100,
                                 "max-frame-size": 1518, "ref-clk-source": "internal", "num-queues": 1,
                                 "phy-connection-type": "rmii", "status": "okay"})
    assert out["phy-handle"] == [Ref("phy")] and out["num-queues"] == 1 and out["phy-connection-type"] == "rmii"
    assert not {"mac-eeprom", "max-speed", "max-frame-size", "ref-clk-source"} & set(out)
    mac = next(l for l in lines if l.startswith("mac-eeprom"))
    assert "nvmem-cells = <&mac_address>" in mac and "mac-address@9a" in mac and "&eeprom" in mac
    mdio = plugins.get_translation("atmel,sam-mdio", "microchip,gmac-g1-mdio")
    phy = DraftChild("ethernet-phy", "0", ["ethernet-phy"], ["phy"], {"reg": [0], "status": "okay", "fixed-link": True})
    kids, kl = mdio.translate_children([phy])
    assert kids[0].props == {"reg": [0], "status": "okay"} and kids[0].compatible == ["ethernet-phy"]
    assert kids[0].labels == ["phy"] and any("fixed-link" in l and "dropped" in l for l in kl)


def test_rtc_clocks_source_and_counter_mode_without_workspace():
    builtin.register_all()
    rtc = plugins.get_translation("atmel,sam0-rtc", "microchip,rtc-g1")
    cnt = plugins.get_translation("atmel,sam0-rtc", "microchip,rtc-g1-counter")
    src = {"clocks": [[Ref("osc")], [Ref("m"), 0x14, 9]], "clock-names": ["OSC32KCTRL", "MCLK"],
           "atmel,assigned-clocks": [[Ref("osc"), 4]], "atmel,assigned-clock-names": ["OSC32KCTRL"],
           "counter-mode": "clock", "systimer": True, "event-control-msk": 0, "prescaler": 1024, "alarms-count": 2,
           "cal-constant": 1048576}
    out, lines = rtc.translate(src)
    assert [(e[0].label, e[1].name) for e in out["clocks"]] == [
        ("mclkperiph", "CLOCK_MCHP_MCLKPERIPH_ID_APBA_RTC"), ("rtcclock", "CLOCK_MCHP_RTC_ID")]
    assert int(out["clocks"][1][1]) == (3 << 26) | (0x3F << 20) | (0x3F << 14) | (0x3F << 8)
    assert out["clock-names"] == ["mclk", "rtcclk"] and out["prescaler"] == 1024 and "counter-mode" not in out
    assert any("&rtcclock { rtc-src = <4>; }" in l and "xosc1k" in l for l in lines)
    assert any("systimer" in l and "dropped" in l for l in lines)
    _, l32 = rtc.translate({**src, "counter-mode": "count-32"})
    assert any("rtc-g1-counter" in l for l in l32)
    o32, _ = cnt.translate({**src, "counter-mode": "count-32"})
    assert o32["max-bit-width"] == 32 and o32["compatible"] == ["microchip,rtc-g1-counter"]
    assert cnt.translate({**src, "counter-mode": "count-16"})[0]["max-bit-width"] == 16
    assert "max-bit-width" not in cnt.translate(src)[0]


def test_simple_pairs_keep_their_properties_and_drop_old_style_clocks():
    builtin.register_all()
    dma, dl = plugins.get_translation("atmel,sam0-dmac", "microchip,dmac-g1-dma").translate(
        {"reg": [0x4100A000, 0x50], "interrupts": [31, 0, 32, 0], "#dma-cells": 2, "status": "okay",
         "clocks": [[Ref("g"), 1]], "clock-names": ["MCLK"]})
    assert dma["#dma-cells"] == 2 and "clocks" not in dma and any("dropped 'clocks'" in l for l in dl)
    for src, tgt in (("atmel,sam0-watchdog", "microchip,wdt-g1"), ("atmel,sam-trng", "microchip,trng-g1-entropy")):
        out, lines = plugins.get_translation(src, tgt).translate(
            {"reg": [0x40002000, 13], "interrupts": [10, 0], "status": "okay"})
        assert out == {"compatible": [tgt], "reg": [0x40002000, 13], "interrupts": [10, 0], "status": "okay"} and lines == []


def test_p810_entries_account_for_every_source_property_and_target_only_names_exist(rtos4_ws, main_ws):
    """Property counts per entry: the rules (plus groups, plus properties consumed by a derive) cover the source
    binding exactly - nothing unaccounted, no rule name that the binding does not have - and every target_only name
    is a property of the target binding."""
    builtin.register_all()
    pairs = [(s, t, sr, tr_) for _, s, t, sr, tr_ in P810_PAIRS]
    pairs.append(("atmel,sam0-rtc", "microchip,rtc-g1-counter", "rtc/atmel,sam0-rtc.yaml",
                  "counter/microchip,rtc-g1-counter.yaml"))
    for s, t, sr, tgt_rel in pairs:
        tg = plugins.get_translation(s, t)
        src, tgt = _all_props(rtos4_ws, sr), _all_props(main_ws, tgt_rel)
        cov = set(tg.rules) | {"compatible"} | {c for r in tg.rules.values() for c in r.consumes}
        for cr in tg.child_rules:
            cov |= {"child: " + k for k in cr.rules} | {"child: reg", "child: status"}
        assert src - cov == set(), (s, t, sorted(src - cov))
        assert {x for x in set(tg.rules) if x not in src} == set(), (s, t)
        assert set(tg.target_only) <= tgt, (s, t, sorted(set(tg.target_only) - tgt))


@pytest.mark.parametrize("num,src_name,tgt_name,src_rel,tgt_rel", P810_PAIRS)
def test_p810_table_rows_equal_binding_property_counts(rtos4_ws, main_ws, capsys, num, src_name, tgt_name, src_rel,
                                                       tgt_rel):
    doc = (Path(__file__).resolve().parent.parent / "docs" / "research" / "peripheral_pairs.md"
           ).read_text(encoding="utf-8")
    src, tgt = _all_props(rtos4_ws, src_rel), _all_props(main_ws, tgt_rel)
    rows, tonly_rows = _section_rows(doc, num)
    names = [r[0].strip("`").split(" (inh:")[0] for r in rows]
    assert len(names) == len(set(names)) == len(src) and set(names) == src
    label = next(r[3] for r in rows if r[3].startswith("`") and not r[3].startswith("`—")).strip("`").split(": ", 1)[0]
    mapped = {r[3].strip("`").split(": ", 1)[1] for r in rows if r[3].startswith(f"`{label}:")}
    tonly = {r[0].strip("`").split(": ", 1)[1].split(" (inh:")[0] for r in tonly_rows if r[0].startswith(f"`{label}:")}
    assert mapped | tonly == tgt and not (mapped & tonly), (sorted(tgt - mapped - tonly), sorted((mapped | tonly) - tgt))
    with capsys.disabled():
        print(f"\n{src_name} -> {tgt_name}: source properties {len(src)} = table rows {len(names)}; target properties "
              f"{len(tgt)} = mapped {len(mapped)} + target-only {len(tonly)}; rows without a target "
              f"{sum(1 for r in rows if r[3].startswith('—'))}")


# ---- P8.10 acceptance: lab1 nodes translated and loaded in main's edtlib against the pic32cx_sg41 SoC dtsi ----------

def _convert_ph(node) -> dict:
    """`_convert` plus single phandles (phy-handle, mac-eeprom) as Ref; the 6-byte MAC placeholder is dropped
    (the renderer writes cells, a uint8-array needs bytes)."""
    props = _convert(node)
    for k, v in list(props.items()):
        if hasattr(v, "labels"):
            props[k] = Ref(v.labels[0])
    props.pop("local-mac-address", None)
    return props


def _render_kids(kids, indent: str = "\t") -> str:
    out = []
    for c in kids:
        head = (f"{', '.join(l + ': ' for l in c.labels)}" if c.labels else "") + c.node_name
        body = [tr.render_property("compatible", c.compatible)] if c.compatible else []
        body += [tr.render_property(k, v) for k, v in c.props.items()]
        out.append(f"{indent}{head} {{\n" + "".join(f"{indent}\t{b}\n" for b in body if b) + _render_kids(c.children, indent + "\t")
                   + f"{indent}}};\n")
    return "".join(out)


def _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, label, source, target, *, status="okay", drop=(),
               add=None, pre="", post_children=None, extra="", drop_out=()):
    """Translate lab1's `label` node, load it with Zephyr main's edtlib on the pic32cx_sg41 SoC.
    Returns (source props, translated props, report lines, edt node, overlay text)."""
    builtin.register_all()
    t = plugins.get_translation(source, target)
    src = _convert_ph(old_edt.label2node[label])
    for d in drop:
        src.pop(d, None)
    src.update(add or {})
    out, lines = t.translate(src)
    out = {k: v for k, v in out.items() if k not in drop_out}
    out["status"] = status
    tlabel = {"dac0": "dac", "wdog": "wdt"}.get(label, label)                  # labels renamed by the new SoC dtsi
    node_text = tr.render_node(f"&{tlabel}", out, symbolic=False)
    if post_children:
        node_text = node_text[:node_text.rindex("};")] + post_children + "};"
    overlay = pre + node_text + "\n" + extra
    edt = load_edt_sg41(main_ws, main_edtlib, overlay, tmp_path)
    return src, out, lines, edt.label2node[tlabel], overlay


def _show(capsys, title, src, out, lines, node, extra_lines=()):
    with capsys.disabled():
        print(f"\n--- {title}: source props (lab1, old edtlib, as written in the devicetree incl. SoC) ---")
        print(tr.render_node("src", src))
        print("--- translated node ---")
        print(tr.render_node(f"&{node.labels[0]}", out))
        print("--- report lines ---")
        print("\n".join(lines + list(extra_lines)) or "(none)")
        print(f"--- edtlib (Zephyr main): {node.matching_compat}, binding {Path(node.binding_path).name}, "
              f"status {node.status} ---")


def test_lab1_tcc0_pwm_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    pins = _pinctrl_state(rtos4_ws, old_edt, "pwm_default")
    src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "tcc0",
                                          "atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm", pre=pins)
    assert node.matching_compat == "microchip,tcc-g1-pwm" and node.status == "okay"
    assert node._node.props["#pwm-cells"].to_num() == 3 and node.props["prescaler"].val == 8
    assert node.props["channels"].val == 6 and node.props["max-bit-width"].val == 24     # = SoC values
    assert [e.data["subsystem"] for e in node.props["clocks"].val] == [int(c[1]) for c in out["clocks"]]
    assert node.props["clock-names"].val == ["mclk", "gclk"]
    assert out["clocks"][1][1].name == "CLOCK_MCHP_GCLKPERIPH_ID_TCC0"
    assert any("third cell" in l for l in lines)
    _show(capsys, "lab1 tcc0 (atmel,sam0-tcc-pwm)", src, out, lines, node)


def test_negative_tcc0_with_old_pwm_cells_makes_main_edtlib_fail(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    pins = _pinctrl_state(rtos4_ws, old_edt, "pwm_default")
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm")
    out, _ = t.translate(_convert_ph(old_edt.label2node["tcc0"]))
    out["#pwm-cells"] = 2                                                   # what a plain keep would have produced
    with pytest.raises(main_edtlib.EDTError, match="pwm-cells"):
        load_edt_sg41(main_ws, main_edtlib, pins + tr.render_node("&tcc0", {**out, "status": "okay"}, symbolic=False),
                      tmp_path)


def test_lab1_tc0_counter_pairs_with_tc1_and_loads_in_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "tc0",
                                          "atmel,sam0-tc32", "microchip,tc-g1-counter")
    assert node.matching_compat == "microchip,tc-g1-counter"
    assert node.props["max-bit-width"].val == 32 and node.props["clock-names"].val == ["mclk", "gclk", "client_mclk"]
    soc = load_edt_sg41(main_ws, main_edtlib, "", tmp_path).label2node["tc0"]            # same three clock entries as the SoC
    assert [(e.controller.labels[0], int(e.data["subsystem"])) for e in node.props["clocks"].val] == [
        (e.controller.labels[0], int(e.data["subsystem"])) for e in soc.props["clocks"].val]
    _show(capsys, "lab1 tc0 (atmel,sam0-tc32)", src, out, lines, node)


def test_lab1_tc0_as_pwm_with_16_and_32_bit_counter(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    """lab1 has no TC PWM; the TC0 node is turned into one the way a board does it (compatible, #pwm-cells, counter-size)."""
    for bits, names in ((16, ["mclk", "gclk"]), (32, ["mclk", "gclk", "client_mclk"])):
        add = {"compatible": ["atmel,sam0-tc-pwm"], "#pwm-cells": 2, "channels": 2, "counter-size": bits, "prescaler": 8}
        src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "tc0",
                                              "atmel,sam0-tc-pwm", "microchip,tc-g1-pwm", add=add)
        assert node.matching_compat == "microchip,tc-g1-pwm" and node._node.props["#pwm-cells"].to_num() == 3
        assert node.props["max-bit-width"].val == bits and node.props["clock-names"].val == names
        if bits == 32:
            _show(capsys, "lab1 tc0 as atmel,sam0-tc-pwm with counter-size 32", src, out, lines, node)


def test_lab1_adc0_with_channel_children_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    from dt_explorer.core.draft import DraftChild
    ch = [DraftChild("channel", "6", [], [], {"reg": [6], "zephyr,gain": "ADC_GAIN_1", "zephyr,reference": "ADC_REF_INTERNAL",
                                              "zephyr,acquisition-time": 0, "zephyr,resolution": 12,
                                              "zephyr,input-positive": 6}),
          DraftChild("channel", "12", [], [], {"reg": [12], "zephyr,gain": "ADC_GAIN_1", "zephyr,reference": "ADC_REF_VDD_1",
                                               "zephyr,acquisition-time": 0, "zephyr,resolution": 12,
                                               "zephyr,input-positive": 12, "zephyr,vref-mv": 3300})]
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-adc", "microchip,adc-g1")
    kids, kl = t.translate_children(ch)
    assert kl == [] and [c.props for c in kids] == [c.props for c in ch]
    src, out, lines, node, overlay = _p810_load(
        rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "adc0", "atmel,sam0-adc", "microchip,adc-g1",
        add={"#address-cells": 1, "#size-cells": 0}, post_children="\n" + _render_kids(kids))
    assert node.matching_compat == "microchip,adc-g1" and node.status == "okay" and node.props["prescaler"].val == 4
    assert [int(e.data["subsystem"]) for e in node.props["clocks"].val] == [int(c[1]) for c in out["clocks"]]
    assert node.props["clock-names"].val == ["mclk", "gclk"] and "calib-offset" not in out
    assert sorted(c.regs[0].addr for c in node.children.values()) == [6, 12]
    assert {c.props["zephyr,input-positive"].val for c in node.children.values()} == {6, 12}
    assert node.props["num-channels"].val == 23 and node.props["nvm-calib"].val is not None       # SoC values stay
    _show(capsys, "lab1 adc0 (atmel,sam0-adc) with two channel@N children", src, out, lines, node,
          [f"child {c.node_name}: translated unchanged" for c in kids])


def test_negative_adc_without_prescaler_makes_main_edtlib_fail(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    with pytest.raises(main_edtlib.EDTError, match="prescaler"):
        _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "adc0", "atmel,sam0-adc", "microchip,adc-g1",
                   drop=("prescaler",), drop_out=("prescaler",))                   # what the translation gave before P8.10b


def test_lab1_adc0_without_a_source_prescaler_gets_the_default_and_loads(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    """The source node lacks prescaler (lab1: the old SoC dtsi sets it); the translation's default makes main's edtlib accept it."""
    src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "adc0", "atmel,sam0-adc",
                                          "microchip,adc-g1", drop=("prescaler",))
    assert "prescaler" not in src and out["prescaler"] == 4 and node.props["prescaler"].val == 4
    assert any("default 'prescaler'" in x for x in lines)


def test_lab1_dac0_translates_and_loads_with_renamed_children(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    """lab1's dac0 has no channel children in the build; the board file has channel_0/channel_1 (same54_xpro.dts)."""
    from dt_explorer.core.draft import DraftChild
    builtin.register_all()
    t = plugins.get_translation("atmel,samd5x-dac", "microchip,dac-g1")
    old = [DraftChild("channel_0", None, [], [], {"oversampling": "OSR_1", "refresh-period": 1, "current-control": "CC1M"}),
           DraftChild("channel_1", None, [], [], {"oversampling": "OSR_1", "refresh-period": 1, "current-control": "CC1M"})]
    kids, kl = t.translate_children(old)
    assert [{k: v for k, v in c.props.items() if k != "reg"} for c in kids] == [
        {"sampling-ratio": 1, "refresh-period": 30, "rate": 500}] * 2
    assert [c.node_name for c in kids] == ["channel@0", "channel@1"] and [c.props["reg"] for c in kids] == [[0], [1]]
    named = kids                                                      # the translation renamed them and wrote reg
    src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "dac0",
                                          "atmel,samd5x-dac", "microchip,dac-g1", drop=("pinctrl-0", "pinctrl-names"),
                                          post_children="\n" + _render_kids(named))
    assert node.matching_compat == "microchip,dac-g1" and node.props["refsel"].val == "int_ref"
    assert sorted(c.props["rate"].val for c in node.children.values()) == [500, 500]
    assert {c.props["sampling-ratio"].val for c in node.children.values()} == {1}
    assert sorted(node.children) == ["channel@0", "channel@1"]                       # named by the translation
    assert sorted(c.regs[0].addr for c in node.children.values()) == [0, 1]
    _show(capsys, "lab1 dac0 (atmel,samd5x-dac) with the board's channel_0/channel_1", src, out, lines, node,
          [l for l in kl] + [f"edtlib children of &dac: {sorted(node.children)}, "
                             f"reg {sorted(c.regs[0].addr for c in node.children.values())}"])


def test_negative_dac_without_refsel_makes_main_edtlib_fail(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path):
    with pytest.raises(main_edtlib.EDTError, match="refsel"):
        _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "dac0", "atmel,samd5x-dac", "microchip,dac-g1",
                   drop=("reference", "pinctrl-0", "pinctrl-names"))


def test_lab1_gmac_mdio_and_phy_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    """MAC + MDIO + the ethernet-phy child, as one overlay: &gmac, &mdio with phy: ethernet-phy@0, phy-handle = <&phy>."""
    from dt_explorer.core.draft import DraftChild
    builtin.register_all()
    tg = plugins.get_translation("atmel,sam0-gmac", "microchip,gmac-g1-eth")
    tm = plugins.get_translation("atmel,sam-mdio", "microchip,gmac-g1-mdio")
    g_src = _convert_ph(old_edt.label2node["gmac"])
    m_src = _convert_ph(old_edt.label2node["mdio"])
    g_out, g_lines = tg.translate(g_src)
    m_out, m_lines = tm.translate(m_src)
    phy_old = old_edt.label2node["phy"]
    phy = DraftChild("ethernet-phy", "0", ["ethernet-phy"], ["phy"], {"reg": [0], "status": "okay"})
    kids, k_lines = tm.translate_children([phy])
    assert kids == [phy]
    # both source nodes enable themselves; pinctrl-0 refers to the pin states that the target board file already defines
    overlay = (tr.render_node("&mdio", {**m_out, "status": "okay"}, symbolic=False)[:-3] + _render_kids(kids) + "};\n"
               + tr.render_node("&gmac", {**g_out, "status": "okay"}, symbolic=False) + "\n")
    edt = load_edt_sg41(main_ws, main_edtlib, overlay, tmp_path)
    gm, md = edt.label2node["gmac"], edt.label2node["mdio"]
    assert gm.matching_compat == "microchip,gmac-g1-eth" and md.matching_compat == "microchip,gmac-g1-mdio"
    assert gm.props["phy-handle"].val.labels == ["phy"] and gm.props["phy-handle"].val.parent is md
    assert gm.props["pinctrl-0"].val[0].labels[0] == "gmac_rmii" and md.props["pinctrl-0"].val[0].labels[0] == "mdio_default"
    assert gm.props["num-queues"].val == 1 and "mac-eeprom" not in g_out
    assert any(l.startswith("mac-eeprom") and "nvmem-cells" in l for l in g_lines)
    assert phy_old.props["reg"].val == [0] and list(md.children) and md.children[next(iter(md.children))].regs[0].addr == 0
    with capsys.disabled():
        print("\n--- lab1 gmac (atmel,sam0-gmac) source props ---")
        print(tr.render_node("gmac", g_src))
        print("--- translated gmac ---")
        print(tr.render_node("&gmac", g_out))
        print("--- gmac report lines ---")
        print("\n".join(g_lines))
        print("--- lab1 mdio (atmel,sam-mdio) source props ---")
        print(tr.render_node("mdio", m_src))
        print("--- translated mdio + phy child (overlay) ---")
        print(overlay)
        print("--- mdio report lines ---")
        print("\n".join(m_lines + k_lines) or "(none)")
        print(f"--- edtlib (Zephyr main): {gm.matching_compat} / {md.matching_compat} / phy child "
              f"{[c.matching_compat for c in md.children.values()]} ---")


@pytest.mark.parametrize("label,source,target,status", [
    ("rtc", "atmel,sam0-rtc", "microchip,rtc-g1", "okay"),
    ("wdog", "atmel,sam0-watchdog", "microchip,wdt-g1", "okay"),
    ("dmac", "atmel,sam0-dmac", "microchip,dmac-g1-dma", "okay"),
    ("trng", "atmel,sam-trng", "microchip,trng-g1-entropy", "okay")])
def test_lab1_simple_pairs_accepted_by_main_edtlib(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys, label, source,
                                                   target, status):
    """RTC, WDT, DMAC, TRNG. The old labels wdog/dac0 are wdt/dac on the target: the node is addressed by its new label."""
    tgt_label = {"wdog": "wdt"}.get(label, label)
    builtin.register_all()
    t = plugins.get_translation(source, target)
    src = _convert_ph(old_edt.label2node[label])
    out, lines = t.translate(src)
    out = {**out, "status": status}
    edt = load_edt_sg41(main_ws, main_edtlib, tr.render_node(f"&{tgt_label}", out, symbolic=False) + "\n", tmp_path)
    node = edt.label2node[tgt_label]
    assert node.matching_compat == target and node.status == "okay"
    soc = load_edt_sg41(main_ws, main_edtlib, "", tmp_path).label2node[tgt_label]
    assert out["reg"][0] == node.regs[0].addr == soc.regs[0].addr
    if label == "rtc":
        assert [(e.controller.labels[0], int(e.data["subsystem"])) for e in node.props["clocks"].val] == [
            (e.controller.labels[0], int(e.data["subsystem"])) for e in soc.props["clocks"].val]
        assert node.props["clock-names"].val == ["mclk", "rtcclk"] and node.props["prescaler"].val == 1024
        assert any("rtc-src = <4>" in l for l in lines)
    if label == "dmac":
        assert node._node.props["#dma-cells"].to_num() == 2
    _show(capsys, f"lab1 {label} ({source})", src, out, lines, node)


def test_rtc_counter_variant_loads_with_max_bit_width(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, capsys):
    src, out, lines, node, _ = _p810_load(rtos4_ws, main_ws, old_edt, main_edtlib, tmp_path, "rtc", "atmel,sam0-rtc",
                                          "microchip,rtc-g1-counter", add={"counter-mode": "count-32"})
    assert node.matching_compat == "microchip,rtc-g1-counter" and node.props["max-bit-width"].val == 32
    _show(capsys, "lab1 rtc as counter (counter-mode count-32)", src, out, lines, node)


# ---- P8.10b: child rename with reg, defaults for missing source properties -------------------------------------------
def test_child_rule_renames_the_child_and_writes_reg_from_the_unit_address():
    from dt_explorer.core.draft import DraftChild
    t = _one({"source": "a,b", "target": "c,d", "children": [
        {"match": {"name": "chan_0"}, "rename": "channel@0", "keep": ["gain"]},
        {"match": {"name": "chan_x"}, "rename": "channel@1f", "keep": ["gain"], "properties": {"reg": {"drop": "own"}}},
        {"match": {"name": "plain"}, "rename": "renamed"}]})
    kids = [DraftChild("chan_0", None, [], ["l0"], {"gain": 1}), DraftChild("chan_x", None, [], [], {"gain": 2}),
            DraftChild("plain", "3", [], [], {"reg": [3]})]
    out, lines = t.translate_children(kids)
    assert [c.node_name for c in out] == ["channel@0", "channel@1f", "renamed"]
    assert out[0].props == {"reg": [0], "gain": 1} and out[0].labels == ["l0"]       # reg first, labels kept
    assert out[1].props["reg"] == [0x1f]                                              # the name decides, not the rules
    assert out[2].unit is None                                                        # no unit address in the new name
    assert any("child chan_0 renamed channel@0, reg = <0x0>" in x for x in lines)
    assert kids[0].name == "chan_0" and kids[0].props == {"gain": 1}                  # the draft's child is untouched


@pytest.mark.parametrize("bad", ["@5", "channel@", "channel@zz", ""])
def test_malformed_child_rename_is_rejected(bad):
    with pytest.raises(tr.TranslationError, match="rename"):
        _one({"source": "a,b", "target": "c,d", "children": [{"match": {"name": "n"}, "rename": bad}]})


def test_default_fills_a_property_that_the_source_lacks_and_never_replaces_one():
    t = _one({"source": "a,b", "target": "c,d", "keep": ["k", "p"],
              "default": {"p": {"value": 4, "note": "required in the target"}, "q": {"value": [1, 2]}}})
    out, lines = t.translate({"k": 1})
    assert out == {"compatible": ["c,d"], "k": 1, "p": 4, "q": [1, 2]}
    assert any("default 'p'" in x and "p = <4>;" in x and "required in the target" in x for x in lines)
    assert any("default 'q'" in x and "q = <1 2>;" in x for x in lines)
    out, lines = t.translate({"k": 1, "p": 8})
    assert out["p"] == 8 and not any("default 'p'" in x for x in lines)               # the source value wins


@pytest.mark.parametrize("bad", [[], {"p": 4}, {"p": {"note": "x"}}, {"p": {"value": 1, "bogus": 2}}])
def test_malformed_default_is_rejected(bad):
    with pytest.raises(tr.TranslationError, match="default"):
        _one({"source": "a,b", "target": "c,d", "default": bad})


def test_adc_prescaler_is_defaulted_when_the_source_node_has_none():
    builtin.register_all()
    t = plugins.get_translation("atmel,sam0-adc", "microchip,adc-g1")
    out, lines = t.translate({"#io-channel-cells": 1})
    assert out["prescaler"] == 4 and any("default 'prescaler'" in x and "prescaler = <4>;" in x for x in lines)
    assert "prescaler" not in t.target_only                                          # no longer only a reminder
    out, lines = t.translate({"prescaler": 8})
    assert out["prescaler"] == 8 and not any("default 'prescaler'" in x for x in lines)
