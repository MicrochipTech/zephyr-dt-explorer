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
