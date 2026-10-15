r"""Simulated UI test (no browser): Draft tab "Generate code" on a COPY of _soltest/apps/lab1_leer (P8.14).

The draft is SERCOM1 as I2C on PC22/PC23 with a bosch,bme280 at 0x76. The copy's CMakeCache still names the original
app, so draft_tab.apply_app_dir is redirected to the copy. The real app folders are never written.
"""
from __future__ import annotations

import pytest
from ui_helpers import by_id, click, el, page_text, select_value, simulated_app, wait_until

pytestmark = pytest.mark.ui

BUILD = "build_same54_xpro"
SERCOM1 = "/soc/sercom@40003400"


async def _make_draft(user):
    await user.open(f"/?build={BUILD}&node={SERCOM1}")
    click(user, "cfg-open")
    select_value(user, "cfg-class", "i2c")
    select_value(user, "cfg-role-SDA", "PC22")
    select_value(user, "cfg-role-SCL", "PC23")
    click(user, "cfg-child-add")
    select_value(user, "child-compatible", "bosch,bme280")
    select_value(user, "child-address", "76")
    click(user, "child-save")
    click(user, "cfg-save")
    await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")


@pytest.fixture
def leer(lab1_leer_copy, monkeypatch):
    app, build = lab1_leer_copy
    from dt_explorer.ui import draft_tab
    monkeypatch.setattr(draft_tab, "apply_app_dir", lambda b: app)
    return app


def test_generate_code_flow(leer, ui_run):
    app = leer
    cmake_before = (app / "CMakeLists.txt").read_text(encoding="utf-8")
    main_before = (app / "src" / "main.c").read_bytes()
    conf_before = (app / "prj.conf").read_text(encoding="utf-8")

    async def scenario():
        async with simulated_app(app) as user:
            await _make_draft(user)
            click(user, "draft-codegen")
            assert "App: " in page_text(user, "codegen-app")
            assert by_id(user, "codegen-device-bme280_76") and "sensor, bosch,bme280" in page_text(user, "codegen-device-bme280_76")
            # channels come from the driver's channel_get; the user can change them
            chan = el(user, "codegen-chan-bme280_76")
            assert chan.value == ["SENSOR_CHAN_AMBIENT_TEMP", "SENSOR_CHAN_PRESS", "SENSOR_CHAN_HUMIDITY"]
            chan.value = ["SENSOR_CHAN_AMBIENT_TEMP", "SENSOR_CHAN_PRESS"]
            await wait_until(lambda: el(user, "codegen-chan-bme280_76").value == ["SENSOR_CHAN_AMBIENT_TEMP", "SENSOR_CHAN_PRESS"],
                             timeout=10, what="the channel change")
            for fid in ("CMakeLists_txt", "include-dtx_devices_h", "include-dtx_bme280_76_h", "src-dtx_bme280_76_c",
                        "src-dtx_devices_c"):
                assert page_text(user, f"codegen-file-{fid}").startswith("NEW: dtx_devices/")
            assert not (app / "dtx_devices").exists()                         # planning wrote nothing
            assert "CMakeLists.txt" in page_text(user, "codegen-cmake") or "missing" in page_text(user, "codegen-cmake")
            assert "+add_subdirectory(dtx_devices)" in el(user, "codegen-cmake-diff").content
            assert (app / "CMakeLists.txt").read_text(encoding="utf-8") == cmake_before

            click(user, "codegen-write")
            result = el(user, "codegen-result").text
            assert result.count("written:") == 5 and "dtx_bme280_76.c" in result
            hdr = (app / "dtx_devices" / "include" / "dtx_bme280_76.h").read_text(encoding="utf-8")
            assert "ambient_temp" in hdr and "press;" in hdr and "humidity" not in hdr

            # CMakeLists: nothing happens before the dialog is confirmed
            click(user, "codegen-cmake-add")
            assert by_id(user, "codegen-cmake-dialog") and (app / "CMakeLists.txt").read_text(encoding="utf-8") == cmake_before
            click(user, "codegen-cmake-cancel")
            assert (app / "CMakeLists.txt").read_text(encoding="utf-8") == cmake_before
            click(user, "codegen-cmake-add")
            click(user, "codegen-cmake-ok")
            assert el(user, "codegen-cmake-result").text.startswith("added add_subdirectory(dtx_devices)")

            # prj.conf: marked block with the Kconfig lines
            assert "CONFIG_SENSOR=y" in el(user, "codegen-conf-diff").content
            click(user, "codegen-conf-confirm")
            assert el(user, "codegen-conf-result").text.startswith("appended:")

            # a second plan: everything unchanged, line present, nothing to add
            click(user, "draft-codegen")
            assert page_text(user, "codegen-file-CMakeLists_txt").startswith("UNCHANGED: ")
            assert "already in CMakeLists.txt" in page_text(user, "codegen-cmake")
            assert "nothing to add" in page_text(user, "codegen-conf")

            # the user edits a generated file: it is not written, the diff is shown
            mine = app / "dtx_devices" / "src" / "dtx_bme280_76.c"
            mine.write_text(mine.read_text(encoding="utf-8") + "/* my change */\n", encoding="utf-8")
            click(user, "draft-codegen")
            assert page_text(user, "codegen-file-src-dtx_bme280_76_c").startswith("EDITED: ")
            assert "NOT overwritten" in page_text(user, "codegen-file-src-dtx_bme280_76_c")
            assert "my change" in el(user, "codegen-diff-src-dtx_bme280_76_c").content
            assert not el(user, "codegen-write").enabled                      # nothing left to write

    ui_run(scenario)
    assert (app / "CMakeLists.txt").read_text(encoding="utf-8") == cmake_before + "\nadd_subdirectory(dtx_devices)\n"
    assert (app / "src" / "main.c").read_bytes() == main_before and sorted(p.name for p in (app / "src").iterdir()) == ["main.c"]
    conf = (app / "prj.conf").read_text(encoding="utf-8")
    assert conf.startswith(conf_before) and conf.count("dt_explorer codegen begin") == 1 and "CONFIG_BME280=y" in conf
    assert "/* my change */" in (app / "dtx_devices" / "src" / "dtx_bme280_76.c").read_text(encoding="utf-8")


def test_eeprom_child_is_generic_not_a_sensor(leer, ui_run):
    """P8.19b item 6: a bus device whose driver does not use the sensor API gets no sensor read function."""
    app = leer

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={SERCOM1}")
            click(user, "cfg-open")
            select_value(user, "cfg-class", "i2c")
            select_value(user, "cfg-role-SDA", "PC22")
            select_value(user, "cfg-role-SCL", "PC23")
            for compat, addr, props in (("bosch,bme280", "76", {}),
                                        ("atmel,at24", "50", {"size": "256", "pagesize": "16", "address-width": "8", "timeout": "5"})):
                click(user, "cfg-child-add")
                select_value(user, "child-compatible", compat)
                select_value(user, "child-address", addr)
                for name, value in props.items():
                    select_value(user, f"child-prop-{name}", value)
                click(user, "child-save")
            click(user, "cfg-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            click(user, "draft-codegen")
            assert "sensor, bosch,bme280" in page_text(user, "codegen-device-bme280_76")
            assert "generic, atmel,at24" in page_text(user, "codegen-device-at24_50")
            assert not by_id(user, "codegen-chan-at24_50")                  # no channel list for a non-sensor

    ui_run(scenario)


def test_generate_code_without_devices(leer, ui_run):
    app = leer

    async def scenario():
        async with simulated_app(app) as user:
            await user.open(f"/?build={BUILD}&node={SERCOM1}")
            click(user, "cfg-open")
            select_value(user, "cfg-class", "i2c")
            select_value(user, "cfg-role-SDA", "PC22")
            select_value(user, "cfg-role-SCL", "PC23")
            click(user, "cfg-save")
            await wait_until(lambda: by_id(user, "draft-snippet"), timeout=15, what="the Draft tab")
            click(user, "draft-codegen")
            assert "no devices to generate code for" in page_text(user, "codegen-empty")

    ui_run(scenario)
    assert not (app / "dtx_devices").exists()
