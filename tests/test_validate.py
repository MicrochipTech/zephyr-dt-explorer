"""Fast overlay check (core/validate.py) on the RTOS4 lab1 build."""

from __future__ import annotations

import time

import pytest

from dt_explorer.core.draft import DraftNode
from dt_explorer.core.render import RenderContext, render_overlay
from dt_explorer.core.validate import Finding, validate

BUILD = "apps/lab1/build_same54_xpro"


@pytest.fixture()
def env(rtos4_ws, rtos4_build):
    from dt_explorer.core import plugins
    from dt_explorer.core.buildinfo import read_build_info
    from dt_explorer.core.pins import load_device
    from dt_explorer.formatters.atmel_sam0 import AtmelSam0Formatter
    from dt_explorer.providers.microchip_atdf import MicrochipAtdfProvider
    from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe
    plugins.register_provider(MicrochipAtdfProvider)     # other tests clear the registry
    plugins.register_formatter(AtmelSam0Formatter)
    plugins.register_recipe(AtmelSam0SercomRecipe)
    build = rtos4_build(BUILD)
    device, reason = load_device(read_build_info(build))
    if device is None:
        pytest.skip(reason)
    ctx = RenderContext(device=device, pinctrl_compatible="atmel,sam0-pinctrl",
                        workspace=rtos4_ws, soc="same54p20a")
    return build, ctx


def uart(inst: str, tx: str, rx: str) -> DraftNode:
    return DraftNode(cls="uart", roles=["TX", "RX"], props={"current-speed": 115200},
                     instance=inst, pins={"TX": tx, "RX": rx},
                     compatible="atmel,sam0-uart", label=inst.lower())


def run(build, text: str, name: str):
    timings: dict = {}
    findings = validate(build, text, timings=timings)
    print(f"\n===== {name}: " + ", ".join(f"{k} {v:.2f} s" for k, v in timings.items()))
    print(text)
    for f in findings:
        print(f)
    return findings, timings


def problems(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity in ("error", "warning")]


def test_conflict_on_pb24_names_both_nodes(env):
    build, ctx = env
    findings, _ = run(build, render_overlay(uart("SERCOM0", "PB24", "PB25"), ctx), "second UART on PB24/PB25")
    hits = [f for f in findings if f.kind == "pin_conflict" and f.pad == "PB24"]
    assert len(hits) == 1 and hits[0].severity == "error" and hits[0].overlay
    assert set(hits[0].nodes) == {"/soc/sercom@40003000", "/soc/sercom@41012000"}
    assert "sercom0" in hits[0].message and "sercom2" in hits[0].message
    # the conflict that was in the build before (LED vs TCC0) stays info
    old = [f for f in findings if f.kind == "pin_conflict_existing"]
    assert [f.pad for f in old] == ["PC18"] and old[0].severity == "info"


def test_group_mix_is_warning_with_provider_text(env):
    build, ctx = env
    from dt_explorer.core.pins import load_device
    from dt_explorer.core.buildinfo import read_build_info
    device, _ = load_device(read_build_info(build))
    text_expected = next(c.message for c in device.constraints if c.kind == "shared_group")
    findings, _ = run(build, render_overlay(uart("SERCOM3", "PC23", "PA16"), ctx), "pads from two groups")
    hits = [f for f in findings if f.kind == "provider_constraint"]
    assert len(hits) == 1 and hits[0].severity == "warning"
    assert text_expected in hits[0].message and "PA16" in hits[0].message and "PC23" in hits[0].message
    assert not [f for f in findings if f.severity == "error"]


def test_correct_overlay_has_no_error_or_warning(env):
    build, ctx = env
    i2c = DraftNode(cls="i2c", roles=["SDA", "SCL"], props={"clock-frequency": 100000},
                    vendor_props={"#address-cells": 1, "#size-cells": 0}, instance="SERCOM1",
                    pins={"SDA": "PC22", "SCL": "PC23"}, compatible="atmel,sam0-i2c", label="sercom1")
    findings, _ = run(build, render_overlay(i2c, ctx), "I2C on SERCOM1 PC22/PC23 (same group)")
    assert problems(findings) == []
    # re-applying the board's own console UART changes nothing
    findings, _ = run(build, render_overlay(uart("SERCOM2", "PB25", "PB24"), ctx), "SERCOM2 UART as on the board")
    assert problems(findings) == []


def test_missing_required_property(env):
    build, ctx = env
    text = render_overlay(uart("SERCOM3", "PC23", "PC22"), ctx)
    text = "\n".join(ln for ln in text.splitlines() if "rxpo" not in ln)
    findings, _ = run(build, text, "UART without rxpo")
    assert len(findings) == 1
    f = findings[0]
    assert (f.severity, f.kind, f.node) == ("error", "missing_property", "/soc/sercom@41014000")
    assert "rxpo" in f.message


def test_syntax_and_preprocessor_errors(env):
    build, _ = env
    findings, _ = run(build, '&sercom3 { status = "okay"; foo = <NOPE_X>; };\n', "unknown macro")
    assert [f.severity for f in findings] == ["error"]
    findings, _ = run(build, '#include <no/such/header.h>\n', "missing include")
    assert [(f.severity, f.kind) for f in findings] == [("error", "preprocess")]


def test_warm_call_is_fast(env):
    build, ctx = env
    text = render_overlay(uart("SERCOM3", "PC23", "PA16"), ctx)
    validate(build, text)                       # first call fills the binding text cache
    t0 = time.perf_counter()
    timings: dict = {}
    validate(build, text, timings=timings)
    took = time.perf_counter() - t0
    print("\nwarm validate:", {k: round(v, 2) for k, v in timings.items()}, f"wall {took:.2f} s")
    assert took < 3.0


def test_from_edt_matches_constructor(env):
    from dt_explorer.core.build import BuildModel
    from dt_explorer.core.pins import used_pins
    build, _ = env
    full = BuildModel(build)
    light = BuildModel.from_edt(full.edt, build, full.origins)
    a, b = used_pins(full), used_pins(light)
    assert {p: [(u.node, u.function) for u in v] for p, v in a.uses.items()} == \
           {p: [(u.node, u.function) for u in v] for p, v in b.uses.items()}
    assert light.by_path.keys() == full.by_path.keys()
