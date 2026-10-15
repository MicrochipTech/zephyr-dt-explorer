"""core/board_nodes.py (P8.7): the draft node for LEDs, buttons, PWM LEDs, aliases and chosen.

Pure tests with a fake model, plus the real lab1_leer build (DTX_RTOS4_WS) for the pad map, the overlay,
the pad-conflict check of `validate` and the draft file round trip.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dt_explorer.core import board_nodes as B
from dt_explorer.core.draft import Draft, load_draft, save_draft
from dt_explorer.core.render import RenderContext, render_overlay

PAD_MAP = {"PD10": ("portd", 10), "PD11": ("portd", 11), "PB05": ("portb", 5)}


def fake_model(labels=("led0",), paths=("/leds/led_0", "/soc/sercom@41012000"), chosen=("zephyr,console",)):
    nodes = [SimpleNamespace(labels=[lb], path=p) for lb, p in zip(labels, paths)]
    nodes.append(SimpleNamespace(labels=[], path="/soc/sercom@41012000"))
    return SimpleNamespace(edt=SimpleNamespace(nodes=nodes, chosen_nodes={c: None for c in chosen}))


def spec() -> B.BoardSpec:
    return B.BoardSpec(
        entries=[B.Entry(B.LEDS, "led_user", pad="PD10", active_low=True, text="User LED"),
                 B.Entry(B.KEYS, "btn_user", pad="PD11", active_low=True, pull="pull-up", text="SW", code=11),
                 B.Entry(B.PWM_LEDS, "pwm_user", ctrl="tcc0", channel=1, period_ns=1_000_000)],
        aliases={"led1": "/user_leds/led_user"},
        chosen={"dtx,led": "/user_leds/led_user"})


def test_overlay_text():
    node = B.spec_to_node(spec(), PAD_MAP, {"tcc0": 2})
    assert node.cls == B.BOARD_CLASS and node.label == B.BOARD_LABEL and not node.pins and node.instance is None
    text = render_overlay(Draft(nodes=[node]), RenderContext())
    assert text.startswith('&{/} {\n\tstatus = "okay";\n')
    for line in ('compatible = "gpio-leds";', "led_user: led_user {", "gpios = <&portd 10 1>;", 'label = "User LED";',
                 'compatible = "gpio-keys";', "gpios = <&portd 11 17>;", "zephyr,code = <11>;",
                 'compatible = "pwm-leds";', "pwms = <&tcc0 1 1000000>;",
                 'led1 = "/user_leds/led_user";', 'dtx,led = "/user_leds/led_user";'):
        assert line in text, line


def test_three_cell_pwm_controller_gets_a_flags_cell():
    node = B.spec_to_node(B.BoardSpec(entries=[B.Entry(B.PWM_LEDS, "p", ctrl="pwm0", channel=2, period_ns=5)]),
                          PAD_MAP, {"pwm0": 3})
    assert "pwms = <&pwm0 2 5 0>;" in render_overlay(Draft(nodes=[node]), RenderContext())


def test_round_trip_through_the_draft_file(tmp_path):
    d = Draft(build="b")
    B.apply_spec(d, spec(), PAD_MAP)
    save_draft(d, tmp_path, "b")
    back = load_draft(tmp_path, "b")
    assert B.read_spec(back, PAD_MAP) == spec()
    assert B.summary(back.nodes[0]) == "1 LED, 1 button, 1 PWM LED, 1 alias, 1 chosen"


def test_apply_spec_replaces_and_removes_the_carrier():
    d = Draft()
    B.apply_spec(d, spec(), PAD_MAP)
    B.apply_spec(d, B.BoardSpec(entries=[B.Entry(B.LEDS, "a", pad="PB05")]), PAD_MAP)
    assert len(d.nodes) == 1 and [e.name for e in B.read_spec(d, PAD_MAP).entries] == ["a"]
    B.apply_spec(d, B.BoardSpec(), PAD_MAP)
    assert d.nodes == [] and B.carrier(d) is None


def test_empty_containers_are_left_out():
    text = render_overlay(Draft(nodes=[B.spec_to_node(B.BoardSpec(aliases={"x": "/y"}), PAD_MAP)]), RenderContext())
    assert "user_leds" not in text and "aliases" in text and "chosen" not in text


def check(sp, **kw):
    return B.check_spec(sp, model=fake_model(), pad_map=PAD_MAP, **kw)


def msgs(problems, severity):
    return [p.message for p in problems if p.severity == severity]


def test_valid_spec_has_no_problem():
    assert check(spec(), pwm_ctrls={"tcc0": {}}) == []


def test_name_pad_and_label_problems():
    sp = B.BoardSpec(entries=[B.Entry(B.LEDS, "Bad Name", pad="PD10"), B.Entry(B.LEDS, "led0", pad="PD11"),
                              B.Entry(B.LEDS, "x", pad="PD10"), B.Entry(B.LEDS, "y", pad="PD10"),
                              B.Entry(B.LEDS, "z", pad="PA99"), B.Entry(B.LEDS, "w")])
    errs = msgs(check(sp), "error")
    assert any("Bad Name: name must be" in m for m in errs)
    assert any("led0: a node with this label already exists" in m for m in errs)
    assert any("y: pad PD10 is also used by x" in m for m in errs)
    assert any("z: pad PA99 is not a GPIO pad" in m for m in errs)
    assert any("w: no pad chosen" in m for m in errs)


def test_duplicate_name_and_pwm_problems():
    sp = B.BoardSpec(entries=[B.Entry(B.LEDS, "a", pad="PD10"), B.Entry(B.KEYS, "a", pad="PD11"),
                              B.Entry(B.PWM_LEDS, "p", ctrl="nope"), B.Entry(B.PWM_LEDS, "q", ctrl="tcc0", channel=1),
                              B.Entry(B.PWM_LEDS, "r", ctrl="tcc0", channel=1)])
    errs = msgs(check(sp, pwm_ctrls={"tcc0": {}}), "error")
    assert "a: name used twice" in errs
    assert any("no PWM controller 'nope'" in m for m in errs)
    assert any("r: PWM channel 1 of tcc0 is also used by q" in m for m in errs)


def test_used_pad_is_a_warning_here_validate_reports_the_error():
    sp = B.BoardSpec(entries=[B.Entry(B.LEDS, "a", pad="PD10")])
    probs = check(sp, other_users={"PD10": ["led0 (GPIO)"]})
    assert msgs(probs, "error") == [] and "already used by led0 (GPIO)" in msgs(probs, "warning")[0]


def test_alias_and_chosen_problems():
    sp = B.BoardSpec(entries=[B.Entry(B.LEDS, "a", pad="PD10")],
                     aliases={"Bad_Alias": "/user_leds/a", "ok": "/user_leds/gone", "led0": "/user_leds/a"},
                     chosen={"zephyr,console": "/soc/sercom@41012000", "x y": "/user_leds/a"})
    probs = check(sp, existing_aliases={"led0"})
    errs, warns = msgs(probs, "error"), msgs(probs, "warning")
    assert any("alias 'Bad_Alias': not a valid alias name" in m for m in errs)
    assert any("alias 'ok': target /user_leds/gone does not exist" in m for m in errs)
    assert any("chosen 'x y': not a valid chosen name" in m for m in errs)
    assert "alias 'led0' exists in the build: this overlay replaces it" in warns
    assert "chosen 'zephyr,console' exists in the build: this overlay replaces it" in warns


# ---- the real build ----------------------------------------------------------------------------------
@pytest.fixture
def leer(rtos4_build):
    from dt_explorer.app import load_plugins
    from dt_explorer.core.build import BuildModel
    from dt_explorer.ui import configure as C
    load_plugins()
    build = rtos4_build("_soltest/apps/lab1_leer/build_same54_xpro")
    work = Path(tempfile.mkdtemp(prefix="dtx_bn_"))
    app = work / "lab1_leer"
    shutil.copytree(build.parent, app)
    m = BuildModel(app / build.name)
    sess = C.ConfigSession(m)
    sess.draft = Draft(build=m.build.name)             # never read or write a stored draft
    yield sess
    shutil.rmtree(work, ignore_errors=True)


def test_pad_map_and_pwm_controllers(leer):
    pm = B.gpio_pad_map(leer.model, leer.device)
    assert pm["PD10"] == ("portd", 10) and pm["PC18"] == ("portc", 18) and pm["PA00"] == ("porta", 0)
    assert [c["label"] for c in B.pwm_controllers(leer.model)] == ["tcc0"]
    assert B.pwm_controllers(leer.model)[0]["cells"] == ["channel", "period"]


def test_board_nodes_validate_without_error_and_a_used_pad_conflicts(leer):
    pm = B.gpio_pad_map(leer.model, leer.device)
    good = B.BoardSpec(entries=[B.Entry(B.LEDS, "led_user", pad="PD10", active_low=True),
                                B.Entry(B.KEYS, "btn_user", pad="PD11", active_low=True, pull="pull-up", code=11),
                                B.Entry(B.PWM_LEDS, "pwm_user", ctrl="tcc0", channel=1)],
                       aliases={"led1": "/user_leds/led_user"}, chosen={"dtx,led": "/user_leds/led_user"})
    B.apply_spec(leer.draft, good, pm, {"tcc0": 2})
    assert [f for f in leer.check() if f.severity == "error"] == []
    bad = B.BoardSpec(entries=[B.Entry(B.LEDS, "led_bad", pad="PC18")])        # PC18 is the board's led0
    B.apply_spec(leer.draft, bad, pm)
    errs = [f for f in leer.check() if f.severity == "error"]
    assert [f.kind for f in errs] == ["pin_conflict"] and "led_bad" in errs[0].message and "led0" in errs[0].message
