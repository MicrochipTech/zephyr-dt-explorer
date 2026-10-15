"""GMAC + MDIO + PHY recipe (recipes/microchip_gmac.py, P8.9).

* Unit tests need only the repo (ATSAME54P20A ATDF fixture, stub formatter).
* The main-workspace tests need DTX_MAIN_WS and a pic32cx_sg41_cult cmake-only build
  (DTX_MAIN_BUILD or %TEMP%/dtx_main_sg41); the west verify run needs the workspace's virtual environment.
* The lab1 round trip needs DTX_RTOS4_WS and the Zephyr SDK compiler (old port, same54_xpro enables the
  gmac with a PHY).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.draft import CLASS_BASES, Draft, DraftNode
from dt_explorer.core.render import RenderContext, render_overlay
from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
from dt_explorer.recipes import microchip_gmac as mod
from dt_explorer.recipes.microchip_gmac import (ETH_COMPATIBLES, KCONFIG, MDIO_COMPATIBLES, MII_ROLES,
                                                RMII_ROLES, MicrochipGmacRecipe, RecipeError)

ATDF = Path(__file__).resolve().parent / "fixtures" / "atdf" / "ATSAME54P20A.atdf"
OLD, NEW = "atmel,sam0-gmac", "microchip,gmac-g1-eth"
RMII_PADS = {"GTXCK": "PA14", "GTXEN": "PA17", "GTX0": "PA18", "GTX1": "PA19",
             "GRXDV": "PC20", "GRX0": "PA13", "GRX1": "PA12", "GRXER": "PA15"}


# GMAC instance of the SAME54/PIC32CX SG41 ATDF (the repo fixture is trimmed to SERCOM/EIC, so the signals are
# written out here; test_synthetic_gmac_equals_atdf_on_main compares them with the real ATDF of the SG41).
GMAC_SIGNALS = (
    [(r, p, None) for r, p in RMII_PADS.items()]
    + [("GTX2", "PC16", None), ("GTX3", "PC17", None), ("GRX2", "PC15", None), ("GRX3", "PC14", None),
       ("GRXCK", "PC18", None), ("GTXER", "PC19", None), ("GCOL", "PC21", None), ("GCRS", "PA16", None)]
    + [("GMDC", "PB14", "1"), ("GMDIO", "PB15", "1"), ("GMDC", "PC11", "2"), ("GMDIO", "PC12", "2"),
       ("GMDC", "PC22", "3"), ("GMDIO", "PC23", "3"), ("GMDC", "PA20", "4"), ("GMDIO", "PA21", "4")])


@pytest.fixture(scope="module")
def device():
    dev = MicrochipAtdfProvider().load(ATDF)
    dev.instances["GMAC"] = Instance("GMAC", "GMAC", 0x42000800,
                                     [Signal(role=r, pad=p, mux="L", group=g) for r, p, g in GMAC_SIGNALS])
    return dev


@pytest.fixture
def recipe():
    return plugins.get_recipe(OLD)


# ---- registration, roles ---------------------------------------------------------------------
def test_registered_for_all_four_compatibles():
    for c in ETH_COMPATIBLES + MDIO_COMPATIBLES:
        r = plugins.get_recipe(c)
        assert r is not None and r.name == "microchip_gmac", c
    assert ETH_COMPATIBLES == [OLD, NEW]
    assert MDIO_COMPATIBLES == ["atmel,sam-mdio", "microchip,gmac-g1-mdio"]
    assert CLASS_BASES["ethernet-controller.yaml"] == "ethernet" and CLASS_BASES["mdio-controller.yaml"] == "mdio"


def test_pin_roles(recipe, device):
    gmac = device.instances["GMAC"]
    assert recipe.pin_roles(gmac, OLD)[:8] == RMII_ROLES
    assert recipe.pin_roles(gmac, NEW) == recipe.pin_roles(gmac, OLD) == RMII_ROLES + MII_ROLES
    assert recipe.pin_roles(gmac, "atmel,sam-mdio") == ["MDC", "MDIO"] == recipe.pin_roles(gmac, "microchip,gmac-g1-mdio")
    assert recipe.pin_roles(gmac)[-2:] == ["MDC", "MDIO"]
    assert recipe.pin_roles(device.instances["SERCOM2"], OLD) == []
    with pytest.raises(RecipeError):
        recipe.pin_roles(gmac, "atmel,sam0-uart")
    assert recipe.required_roles(OLD) == RMII_ROLES and recipe.required_roles(NEW) == RMII_ROLES
    assert recipe.required_roles("atmel,sam-mdio") == ["MDC", "MDIO"]


# ---- derived properties ----------------------------------------------------------------------
def test_derived_props_rmii_none_mii_property(recipe):
    rmii = {r: r for r in RMII_ROLES}
    assert recipe.derived_props(rmii) == {}
    mii = {**rmii, **{r: r for r in MII_ROLES}}
    assert recipe.derived_props(mii) == {"phy-connection-type": "mii"}
    optional_missing = {**rmii, **{r: r for r in mod.MII_REQUIRED}}
    assert recipe.derived_props(optional_missing) == {"phy-connection-type": "mii"}
    assert recipe.derived_props({"MDC": "GMDC", "MDIO": "GMDIO"}) == {}


@pytest.mark.parametrize("assignment,msg", [
    ({r: r for r in RMII_ROLES[:-1]}, "not assigned"),
    ({**{r: r for r in RMII_ROLES}, "GTX2": "GTX2"}, "MII needs"),
    ({**{r: r for r in RMII_ROLES}, "GTXCK": "GTX0"}, "role GTXCK is signal"),
    ({**{r: r for r in RMII_ROLES}, "BOGUS": "BOGUS"}, "not a GMAC role"),
    ({"MDC": "GMDC"}, "not assigned"),
    ({"MDC": "GMDIO", "MDIO": "GMDC"}, "is signal"),
    ({}, "no GMAC role"),
])
def test_derived_props_rejects(recipe, assignment, msg):
    with pytest.raises(RecipeError, match=msg):
        recipe.derived_props(assignment)


# ---- lift ------------------------------------------------------------------------------------
def test_lift_roles_both_families(recipe):
    sig = {**RMII_PADS, "GMDC": "PC11", "GMDIO": "PC12"}
    for comp in (OLD, NEW):
        assert recipe.lift({"compatible": [comp]}, {k: v for k, v in sig.items() if k in RMII_PADS}) == RMII_PADS
    for comp in ("atmel,sam-mdio", "microchip,gmac-g1-mdio"):
        assert recipe.lift({"compatible": [comp]}, [("GMDC", "PC11"), ("GMDIO", "PC12")]) == {"MDC": "PC11", "MDIO": "PC12"}
    assert recipe.lift({"compatible": [OLD]}, {"GTXCK": "PA14"}) == {"GTXCK": "PA14"}      # a role without a pad is left out
    with pytest.raises(RecipeError):
        recipe.lift({"compatible": ["atmel,sam0-uart"]}, {})


# ---- automatic pin choice --------------------------------------------------------------------
def test_rmii_roles_are_automatic_one_candidate_each(recipe, device):
    auto = recipe.auto_pins(device.instances["GMAC"], OLD)
    assert auto.pins == RMII_PADS
    assert [c.how for c in auto.choices] == ["automatic"] * 8
    assert all(len(c.alternatives) == 1 for c in auto.choices)
    # each RMII role really has exactly one candidate in the ATDF (the premise of "automatic")
    for role in RMII_ROLES:
        assert len({s.pad for s in device.instances["GMAC"].signals if s.role == role}) == 1, role


def test_mii_roles_only_on_request(recipe, device):
    gmac = device.instances["GMAC"]
    assert set(recipe.auto_pins(gmac, NEW).pins) == set(RMII_ROLES)
    mii = recipe.auto_pins(gmac, NEW, mii=True)
    assert set(mii.pins) == set(RMII_ROLES + MII_ROLES) and {c.how for c in mii.choices} == {"automatic"}
    assert recipe.derived_props({r: r for r in mii.pins}) == {"phy-connection-type": "mii"}


def test_mdio_alternatives_and_default(recipe, device):
    gmac = device.instances["GMAC"]
    pairs = recipe.mdio_pairs(gmac)
    assert [(p["ioset"], p["MDC"], p["MDIO"]) for p in pairs] == [
        ("1", "PB14", "PB15"), ("2", "PC11", "PC12"), ("3", "PC22", "PC23"), ("4", "PA20", "PA21")]
    a = recipe.auto_pins(gmac, "atmel,sam-mdio")                    # nothing used: lowest IOSET
    assert a.pins == {"MDC": "PB14", "MDIO": "PB15"} and {c.how for c in a.choices} == {"chosen"}
    assert a.choices[0].alternatives == ["PB14", "PC11", "PC22", "PA20"]
    assert a.choices[1].alternatives == ["PB15", "PC12", "PC23", "PA21"]
    # IOSET 1 is used by another node (SG41: TC5 PWM): the next free pair becomes the default
    b = recipe.auto_pins(gmac, "microchip,gmac-g1-mdio", used={"PB14", "PB15"})
    assert b.pins == {"MDC": "PC11", "MDIO": "PC12"} and any("IOSET 1" in n for n in b.notes)
    # one pad of a pair in use is enough to drop the pair
    assert recipe.auto_pins(gmac, "atmel,sam-mdio", used={"PB15", "PC11"}).pins == {"MDC": "PC22", "MDIO": "PC23"}
    # the pair the node has now wins when it is free
    cur = {"MDC": "PC22", "MDIO": "PC23"}
    assert recipe.auto_pins(gmac, "atmel,sam-mdio", used={"PB14"}, current=cur).pins == cur
    # ... but not when another node took it
    assert recipe.auto_pins(gmac, "atmel,sam-mdio", used={"PB14", "PC22"}, current=cur).pins == {"MDC": "PC11", "MDIO": "PC12"}
    # a "current" mix of two pairs is not a pair
    mix = {"MDC": "PC11", "MDIO": "PC23"}
    assert recipe.auto_pins(gmac, "atmel,sam-mdio", current=mix).pins == {"MDC": "PB14", "MDIO": "PB15"}
    # every pair used: the lowest is returned with a note, never silence
    allbusy = recipe.auto_pins(gmac, "atmel,sam-mdio", used={"PB14", "PC12", "PC22", "PA21"})
    assert allbusy.pins == {"MDC": "PB14", "MDIO": "PB15"} and any("every" in n for n in allbusy.notes)


def test_rmii_pad_used_elsewhere_is_noted(recipe, device):
    auto = recipe.auto_pins(device.instances["GMAC"], OLD, used={"PA14"})
    assert auto.pins == RMII_PADS and any("PA14" in n for n in auto.notes)


def test_auto_pins_wrong_instance(recipe, device):
    with pytest.raises(RecipeError):
        recipe.auto_pins(device.instances["SERCOM2"], OLD)
    with pytest.raises(RecipeError):
        recipe.auto_pins(device.instances["GMAC"], "atmel,sam0-uart")


# ---- Kconfig ---------------------------------------------------------------------------------
def test_kconfig_tables(recipe):
    for comp in ETH_COMPATIBLES:
        assert {"CONFIG_NETWORKING", "CONFIG_NET_L2_ETHERNET", "CONFIG_ETH_DRIVER"} <= set(recipe.kconfig(comp))
        assert all(v == "y" for v in recipe.kconfig(comp).values())
    assert recipe.kconfig(OLD)["CONFIG_ETH_SAM_GMAC"] == "y"
    assert recipe.kconfig(NEW)["CONFIG_ETH_MCHP_GMAC_G1"] == "y"
    assert recipe.kconfig("atmel,sam-mdio") == {"CONFIG_MDIO_ATMEL_SAM": "y"}
    assert recipe.kconfig("microchip,gmac-g1-mdio") == {"CONFIG_MDIO_MCHP_GMAC_G1": "y"}
    union = recipe.kconfig()
    # TCP/IP, sockets, TLS, DHCP stay in menuconfig; the PHY driver follows the devicetree (default y)
    for sym in union:
        assert not re.match(r"CONFIG_(NET_(IPV|TCP|UDP|SOCKETS|DHCP)|MBEDTLS|TLS|PHY_|NVMEM|NET_BUF)", sym), sym
    with pytest.raises(RecipeError):
        recipe.kconfig("atmel,sam0-uart")


def _tree_symbols(build):
    from dt_explorer.core.kconfig_suggest import _visible, load_kconfig
    tree = load_kconfig(build)
    return tree, _visible


def _assert_all_have_prompt(build, compats, capsys=None):
    tree, visible = _tree_symbols(build)
    rows = []
    for comp in compats:
        for key in KCONFIG[comp]:
            sym = tree.syms.get(key[len("CONFIG_"):])
            assert sym is not None and sym.nodes, f"{key} is not defined in {build}"
            assert visible(sym), f"{key} has no prompt (promptless symbols must not be assigned)"
            rows.append((comp, key, "prompt" if visible(sym) else "PROMPTLESS"))
    return rows


def test_kconfig_symbols_have_prompts_on_main(main_sg41_build, capsys):
    rows = _assert_all_have_prompt(main_sg41_build, [NEW, "microchip,gmac-g1-mdio"])
    with capsys.disabled():
        print("\n--- Kconfig symbols of the g1 tables, checked in the main workspace tree ---")
        for r in rows:
            print("%-26s %-28s %s" % r)


def test_kconfig_symbols_have_prompts_on_rtos4(rtos4_build, capsys):
    build = rtos4_build("apps/lab1/build_same54_xpro")
    rows = _assert_all_have_prompt(build, [OLD, "atmel,sam-mdio"])
    with capsys.disabled():
        print("\n--- Kconfig symbols of the old-port tables, checked in the RTOS4 workspace tree ---")
        for r in rows:
            print("%-26s %-28s %s" % r)


def test_kconfig_table_covers_what_derivation_finds_on_main(main_sg41_build):
    from dt_explorer.core.kconfig_suggest import derive
    tree, _ = _tree_symbols(main_sg41_build)
    ours = set(KCONFIG[NEW]) | set(KCONFIG["microchip,gmac-g1-mdio"])
    for comp in (NEW, "microchip,gmac-g1-mdio"):
        symbols, _chains, _notes = derive(tree, comp)
        assert symbols, comp
        # derive() also climbs through `depends on !NET_TEST`, the buffer-allocator choice default and the
        # MDIO subsystem (PHY_GENERIC_MII selects it): none of them is something to set for the Ethernet driver
        extra = set(symbols) - ours - {"CONFIG_NET_BUF_FIXED_DATA_SIZE", "CONFIG_NET_TEST", "CONFIG_MDIO"}
        assert not extra, (comp, extra, symbols)
    assert set(derive(tree, NEW)[0]) - {"CONFIG_NET_BUF_FIXED_DATA_SIZE", "CONFIG_NET_TEST"} <= set(KCONFIG[NEW])


# ---- draft nodes and render (stub formatter) ---------------------------------------------------
class StubFormatter:
    api_version = 1
    name = "stub"
    compatibles = ["stub,pinctrl"]

    def render_group(self, signals):
        return "group1 {\n\tpinmux = " + ",\n\t\t ".join(f"<{s.pad}L_GMAC_{s.role}>" for s in signals) + ";\n};"


def test_make_nodes_render(recipe, device):
    auto = recipe.auto_pins(device.instances["GMAC"], OLD)
    mdio = recipe.auto_pins(device.instances["GMAC"], "atmel,sam-mdio", used={"PB14"})
    nodes = recipe.make_nodes(OLD, "GMAC", auto.pins, mdio.pins, phy_address=1)
    mac, bus = nodes
    assert (mac.cls, mac.compatible, mac.label) == ("ethernet", OLD, "gmac")
    assert (bus.cls, bus.compatible, bus.label) == ("mdio", "atmel,sam-mdio", "mdio")
    phy = bus.children[0]
    assert (phy.node_name, phy.compatible, phy.labels, phy.props["reg"]) == ("ethernet-phy@1", ["ethernet-phy"], ["phy"], [1])
    text = render_overlay(Draft(nodes=nodes), RenderContext(device=device, formatter=StubFormatter()))
    assert "phy-handle = <&phy>;" in text
    assert "phy: ethernet-phy@1 {" in text and "reg = <0x1>;" in text and 'compatible = "ethernet-phy";' in text
    assert "<PC11L_GMAC_GMDC>" in text and "<PC12L_GMAC_GMDIO>" in text
    assert text.count("<P") == 8 + 2
    assert "phy-connection-type" not in text                    # rmii is the default of both bindings
    # new family: the MDIO compatible follows the MAC compatible
    assert recipe.make_nodes(NEW, "GMAC", auto.pins, mdio.pins)[1].compatible == "microchip,gmac-g1-mdio"
    with pytest.raises(RecipeError):
        recipe.make_nodes("atmel,sam0-uart", "GMAC", {}, {})


def test_mii_node_renders_connection_type(recipe, device):
    auto = recipe.auto_pins(device.instances["GMAC"], NEW, mii=True)
    mac = recipe.make_nodes(NEW, "GMAC", auto.pins, {})[0]
    text = render_overlay(mac, RenderContext(device=device, formatter=StubFormatter()))
    assert 'phy-connection-type = "mii";' in text and text.count("<P") == 16


# ---- the main workspace: pic32cx_sg41_cult -----------------------------------------------------
@pytest.fixture(scope="module")
def main_sg41_build(main_ws):
    for cand in (os.environ.get("DTX_MAIN_BUILD"), str(Path(tempfile.gettempdir()) / "dtx_main_sg41")):
        if cand and (Path(cand) / "zephyr" / "edt.pickle").is_file():
            return Path(cand)
    pytest.skip("no pic32cx_sg41_cult cmake-only build (DTX_MAIN_BUILD or %TEMP%/dtx_main_sg41)")


GMAC_PATH = "/soc/ethernet@42000800"
MDIO_ROLE = {"GMDC": "MDC", "GMDIO": "MDIO"}


def _sg41_inputs(main_sg41_build):
    from dt_explorer import app
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.core.draft import BindingIndex
    from dt_explorer.core.pins import load_device, used_pins
    app.load_plugins()
    info = read_build_info(main_sg41_build)
    device, reason = load_device(info)
    if device is None:
        pytest.skip(reason)
    model = BuildModel(main_sg41_build)
    uses = used_pins(model, info).uses
    # pads of other enabled nodes (not the gmac or its mdio, which this draft configures)
    others = {pad for pad, us in uses.items() if any(not u.node.startswith(GMAC_PATH) for u in us)}
    current = {MDIO_ROLE[u.role]: pad for pad, us in uses.items() for u in us
               if u.node == f"{GMAC_PATH}/mdio" and u.role in MDIO_ROLE}
    index = BindingIndex([])
    index.by_name = dict(model.yaml_index)
    return info, device, others, current, index


def test_synthetic_gmac_equals_atdf_on_main(main_sg41_build):
    info, device, *_ = _sg41_inputs(main_sg41_build)
    real = sorted((s.role, s.pad, str(s.group)) for s in device.instances["GMAC"].signals)
    assert real == sorted((r, p, str(g)) for r, p, g in GMAC_SIGNALS)


def test_sg41_auto_pins_default_pairs(main_sg41_build, capsys):
    info, device, others, current, index = _sg41_inputs(main_sg41_build)
    rec = plugins.get_recipe(NEW)
    assert rec.mdio_pairs(device.instances["GMAC"]) == MicrochipGmacRecipe().mdio_pairs(device.instances["GMAC"])
    assert {"PB14", "PB15"} <= others                       # TC5 PWM takes IOSET 1 on the CUlt board
    assert current == {"MDC": "PC22", "MDIO": "PC23"}       # the board file's own wiring
    fresh = rec.auto_pins(device.instances["GMAC"], "microchip,gmac-g1-mdio", used=others)
    board = rec.auto_pins(device.instances["GMAC"], "microchip,gmac-g1-mdio", used=others, current=current)
    assert board.pins == {"MDC": "PC22", "MDIO": "PC23"}
    assert fresh.pins == {"MDC": "PC11", "MDIO": "PC12"}    # without the board hint: lowest free IOSET
    with capsys.disabled():
        print("\n--- SG41 MDC/MDIO without a hint (used by other nodes: %s) ---" % sorted(others & {"PB14", "PB15", "PC11", "PC12", "PC22", "PC23", "PA20", "PA21"}))
        print("\n".join(fresh.table()), *fresh.notes, sep="\n")


def test_draft_gmac_phy_on_main_sg41(main_sg41_build, main_ws, capsys):
    from dt_explorer.core import verify
    from dt_explorer.core.kconfig_suggest import suggest_kconfig
    from dt_explorer.core.validate import validate
    info, device, others, current, index = _sg41_inputs(main_sg41_build)
    rec = plugins.get_recipe(NEW)
    gmac = device.instances["GMAC"]
    eth = rec.auto_pins(gmac, NEW, used=others)
    mdio = rec.auto_pins(gmac, "microchip,gmac-g1-mdio", used=others, current=current)
    nodes = rec.make_nodes(NEW, "GMAC", eth.pins, mdio.pins, phy_address=0)
    ctx = RenderContext(device=device, pinctrl_compatible="microchip,port-g1-pinctrl",
                        workspace=info.workspace, soc=info.soc, bindings=index)
    text = render_overlay(Draft(nodes=nodes), ctx)

    conf_syms: dict[str, str] = {}
    for comp in (NEW, "microchip,gmac-g1-mdio"):
        sug = suggest_kconfig(comp, main_sg41_build)
        assert sug.source == "recipe" and sug.symbols == KCONFIG[comp], sug
        conf_syms.update(sug.symbols)
    conf = "".join(f"{k}={v}\n" for k, v in conf_syms.items())

    findings = validate(main_sg41_build, text)
    res = verify.verify(main_sg41_build, text, conf, scratch_root=Path(tempfile.gettempdir()) / "dtx_p89_verify")
    cfg = (res.scratch / "zephyr" / ".config").read_text(encoding="utf-8") if res.scratch else ""
    with capsys.disabled():
        print("\n--- RMII/MDIO role -> pad (GMAC on pic32cx_sg41_cult) ---")
        print("\n".join(eth.table() + mdio.table()))
        print("notes:", *(eth.notes + mdio.notes), sep="\n  ")
        print("\n--- overlay ---\n" + text)
        print("--- conf ---\n" + conf)
        print("--- validate: %d finding(s) ---" % len(findings))
        for f in findings:
            print(f)
        print("--- verify: ok=%s rc=%s %.1fs ---" % (res.ok, res.returncode, res.duration))
        for k in ("NETWORKING", "NET_L2_ETHERNET", "ETH_DRIVER", "ETH_MCHP_GMAC_G1", "MDIO_MCHP_GMAC_G1", "PHY_GENERIC_MII"):
            m = re.search(rf"^CONFIG_{k}=.*$", cfg, re.M)
            print("   ", m.group(0) if m else f"CONFIG_{k} not set")
    assert eth.pins == RMII_PADS and {c.how for c in eth.choices} == {"automatic"}
    assert mdio.pins == {"MDC": "PC22", "MDIO": "PC23"}
    assert not [f for f in findings if f.severity == "error"], findings
    assert res.ok and res.returncode == 0, res.errors
    assert "CONFIG_ETH_MCHP_GMAC_G1=y" in cfg and "CONFIG_MDIO_MCHP_GMAC_G1=y" in cfg


# ---- the old port: lab1 (same54_xpro enables the gmac with a PHY) -----------------------------------
LAB1 = "apps/lab1/build_same54_xpro"
PINCTRL_INCLUDE = "#include <dt-bindings/pinctrl/same54p-pinctrl.h>\n"
BASE_COMPAT = {"gmac": OLD, "mdio": "atmel,sam-mdio"}


def _norm(summ: dict) -> dict:
    """Pin groups without the name of the pinctrl node: the board calls it gmac_rmii, the draft gmac_ethernet_default."""
    out = dict(summ)
    if "pinctrl-0" in out:
        out["pinctrl-0"] = {k.split("/", 1)[1]: v for k, v in out["pinctrl-0"].items()}
    return out


def _targets(edt, label):
    """Path of the node each node-level phandle property of `label` points at (phandle numbers differ between dtc runs)."""
    node = edt.label2node[label]
    out = {}
    for name in ("phy-handle", "mac-eeprom"):
        p = node._node.props.get(name)
        out[name] = p.to_node().path if p is not None else None
    return out


def test_lab1_lift_render_edtlib_compare(rtos4_ws, rtos4_build, tmp_path, capsys):
    """Lift gmac + mdio + phy of lab1 (incl. the node-level phandles phy-handle and mac-eeprom), render them
    back, compare with the original edt.pickle (phandle targets compared by node path)."""
    from test_lift import PINCTRL_INCLUDE as PI, _prepare, _strip_args, strip_node, summary
    from test_render import compile_edt
    build, model, draft, report, ctx = _prepare(rtos4_ws, rtos4_build, LAB1)
    plugins.register_recipe(MicrochipGmacRecipe)
    from dt_explorer.core.lift import lift_build
    from dt_explorer.core.buildinfo import read_build_info
    draft, report = lift_build(model, read_build_info(build))
    from dt_explorer.core.pins import load_device
    info = read_build_info(build)
    device, reason = load_device(info)         # the repo fixture ATDF has no GMAC; the workspace pack has
    if device is None:
        pytest.skip(reason)
    ctx = report.render_context(device)
    nodes = {n.label: n for n in draft.nodes if n.cls in ("ethernet", "mdio")}
    assert set(nodes) == {"gmac", "mdio"}
    assert [c.name for c in nodes["mdio"].children] == ["ethernet-phy"]
    assert not [x for x in report.not_representable if x.path.startswith(GMAC_PATH)], report.not_representable
    got = {**nodes["gmac"].props, **nodes["gmac"].vendor_props}                          # neutral or vendor, as the binding says
    assert [r.label for r in got["phy-handle"]] == ["phy"] and [r.label for r in got["mac-eeprom"]] == ["eeprom"]
    lost = {"phy-handle", "mac-eeprom"}      # compared by target path (_targets), not by phandle number
    zdts = (build / "zephyr" / "zephyr.dts").read_text(encoding="utf-8")
    lifted = set(report.children)
    results = []
    with capsys.disabled():
        print("\n--- lab1 lift of gmac + mdio + phy ---")
        for c in report.covered:
            if c.cls in ("ethernet", "mdio"):
                print(f"  {c.path}  label={c.label}  {c.compatible}  class={c.cls}  roles={c.roles}")
        print("  child nodes in the draft:", [p for p in report.children if p.startswith(GMAC_PATH)])
        print("  gmac props:", nodes["gmac"].props, nodes["gmac"].vendor_props)
        for label in ("gmac", "mdio"):
            node = nodes[label]
            path, names, uses, groups = _strip_args(model, node)
            base = strip_node(zdts, label, names, uses, groups, BASE_COMPAT[label])
            overlay = render_overlay(node, ctx)
            cmp_names = [n for n in names if n not in lost] + ["compatible", "status"]
            original = _norm(summary(model.edt, model, label, cmp_names, lifted))
            edt_r = compile_edt(model, PI + base + "\n" + overlay, tmp_path)
            rendered = _norm(summary(edt_r, model, label, cmp_names, lifted))
            ok = rendered == original and _targets(edt_r, label) == _targets(model.edt, label)
            results.append(ok)
            print(f"{label} ({node.compatible}): compared {cmp_names} + phandle targets {_targets(edt_r, label)}; children {list(original['children'])}: "
                  f"{'MATCH' if ok else 'DIFFERENT'}")
            if not ok:
                print("  original:", original)
                print("  rendered:", rendered)
            if label == "mdio":
                assert original["children"] and "ethernet-phy@0" in original["children"]
            if label == "mdio":     # the PHY block is really gone from the base, so the compare is not vacuous
                assert not re.search(r"ethernet-phy@0\s*\{", base) and "ethernet-phy@0 {" in overlay
            assert ok
        print("--- whole draft at once ---")
        base = zdts
        for label in ("gmac", "mdio"):
            path, names, uses, groups = _strip_args(model, nodes[label])
            base = strip_node(base, label, names, uses, groups, BASE_COMPAT[label])
        both = Draft(nodes=[nodes["gmac"], nodes["mdio"]])
        edt = compile_edt(model, PI + base + "\n" + render_overlay(both, ctx), tmp_path)
        for label in ("gmac", "mdio"):
            path, names, _u, _g = _strip_args(model, nodes[label])
            cmp_names = [n for n in names if n not in lost] + ["compatible", "status"]
            same = (_norm(summary(edt, model, label, cmp_names, lifted)) == _norm(summary(model.edt, model, label, cmp_names, lifted))
                    and _targets(edt, label) == _targets(model.edt, label))
            print(f"{label}: {'MATCH' if same else 'DIFFERENT'}")
            assert same
        print(f"result: {sum(results)}/{len(results)} nodes match per node, whole draft matches")
