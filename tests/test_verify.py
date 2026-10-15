"""core/verify.py: authoritative `west build --cmake-only` check of a draft.

The two slow tests (about 20-50 s each) run Zephyr's configure step on RTOS4 lab1 (DTX_RTOS4_WS)
(the two `*_verifies_*` / `*_fails_*` tests); they run in the normal suite.
"""
import hashlib
import os

import pytest

from dt_explorer.core import verify

BUILD = "apps/lab1/build_same54_xpro"
GOOD = """&pinctrl {
    sercom1_i2c_default: sercom1_i2c_default {
        group1 { pinmux = <PC22C_SERCOM1_PAD0>, <PC23C_SERCOM1_PAD1>; };
    };
};
&sercom1 {
    status = "okay";
    compatible = "atmel,sam0-i2c";
    #address-cells = <1>;
    #size-cells = <0>;
    clock-frequency = <100000>;
    pinctrl-0 = <&sercom1_i2c_default>;
    pinctrl-names = "default";
};
"""
CONF = "CONFIG_I2C=y\nCONFIG_I2C_SAM0=y\n"


def test_parse_output_failure_picks_error_lines():
    lines = ["-- Found Python", "devicetree error: 'x' appears in /soc/a, but is not declared",
             "CMake Error at dts.cmake:306 (execute_process):", "  noise", "-- Configuring incomplete"]
    errors, warnings = verify.parse_output(lines, 1)
    assert errors == [lines[1], lines[2]] and warnings == []


def test_parse_output_failure_without_marker_shows_tail():
    errors, _ = verify.parse_output([f"l{i}" for i in range(30)], 2, limit=3)
    assert errors == ["l27", "l28", "l29"]


def test_parse_output_success_reports_warnings_only():
    lines = ["-- ok", "warning: CONFIG_FOO was assigned y but got n", "-- done"]
    assert verify.parse_output(lines, 0) == ([], [lines[1]])


def test_write_to_app(tmp_path):
    assert verify.existing_files(tmp_path) == []
    paths = verify.write_to_app(tmp_path, "&a {};\n", CONF)
    assert [p.name for p in paths] == ["dt_explorer.overlay", "dt_explorer.conf"]
    assert (tmp_path / "dt_explorer.conf").read_text() == CONF
    assert len(verify.existing_files(tmp_path)) == 2


def test_unusable_build_raises(tmp_path):
    with pytest.raises(verify.VerifyError):
        verify.verify(tmp_path, GOOD, CONF)


def _snapshot(root):
    h, n = hashlib.sha256(), 0
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            st = os.stat(p)
            h.update(f"{os.path.relpath(p, root)}|{st.st_size}|{st.st_mtime_ns}".encode())
            n += 1
    return n, h.hexdigest(), os.stat(root).st_mtime_ns


def test_valid_draft_verifies_and_build_is_untouched(rtos4_build, tmp_path):
    build = rtos4_build(BUILD)
    before = _snapshot(build)
    seen = []
    res = verify.verify(build, GOOD, CONF, on_line=seen.append, scratch_root=tmp_path, timeout=300)
    assert res.ok and res.returncode == 0, "\n".join(res.log_tail)
    assert seen and any("Build files have been written" in ln for ln in seen)
    assert (res.scratch / "zephyr" / "zephyr.dts").is_file()
    assert "sercom1_i2c_default" in (res.scratch / "zephyr" / "zephyr.dts").read_text()
    assert _snapshot(build) == before
    assert not str(res.scratch).startswith(str(build))


def test_misspelled_property_fails_with_zephyr_message(rtos4_build, tmp_path):
    build = rtos4_build(BUILD)
    res = verify.verify(build, GOOD.replace("clock-frequency", "clock-frequncy"), CONF,
                        scratch_root=tmp_path, timeout=300)
    assert not res.ok and res.returncode != 0
    assert any("devicetree error" in e and "clock-frequncy" in e for e in res.errors), res.errors
