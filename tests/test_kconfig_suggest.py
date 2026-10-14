"""Kconfig suggestions: recipe table first, otherwise derived with the workspace's kconfiglib."""

from __future__ import annotations

import os
import sys

import pytest

from dt_explorer.core import plugins
from dt_explorer.core.kconfig_suggest import dt_has_symbol, suggest_kconfig
from dt_explorer.recipes.atmel_sam0_sercom import AtmelSam0SercomRecipe

BUILD = "apps/lab1/build_same54_xpro"


@pytest.fixture(autouse=True)
def _recipe():
    plugins.register_recipe(AtmelSam0SercomRecipe)


def test_dt_has_symbol_name():
    assert dt_has_symbol("atmel,sam0-adc") == "DT_HAS_ATMEL_SAM0_ADC_ENABLED"
    assert dt_has_symbol("gpio-leds") == "DT_HAS_GPIO_LEDS_ENABLED"
    assert dt_has_symbol("zephyr,cdc-acm-uart") == "DT_HAS_ZEPHYR_CDC_ACM_UART_ENABLED"


def test_recipe_table_wins_without_any_build():
    s = suggest_kconfig("atmel,sam0-i2c")
    assert s.source == "recipe"
    assert s.symbols["CONFIG_I2C"] == "y" and s.symbols["CONFIG_I2C_SAM0"] == "y"


def test_no_recipe_no_build_gives_symbol_name_and_note():
    s = suggest_kconfig("atmel,sam0-adc")
    assert s.source == "none" and s.symbols == {}
    assert s.dt_symbol == "DT_HAS_ATMEL_SAM0_ADC_ENABLED"
    assert any("by hand" in n for n in s.notes)


def test_unloadable_build_falls_back_with_reason(tmp_path):
    s = suggest_kconfig("atmel,sam0-adc", tmp_path)
    assert s.source == "none" and any("CMakeCache.txt" in n for n in s.notes)


def test_recipe_still_wins_with_a_build(rtos4_build):
    s = suggest_kconfig("atmel,sam0-i2c", rtos4_build(BUILD))
    assert s.source == "recipe" and "CONFIG_I2C" in s.symbols
    assert s.already_enabled == []          # lab1 has no I2C in its .config


def test_derived_without_recipe(rtos4_build):
    env_before, path_before = dict(os.environ), list(sys.path)
    s = suggest_kconfig("atmel,sam0-adc", rtos4_build(BUILD))
    assert s.source == "kconfiglib"
    assert s.symbols == {"CONFIG_ADC": "y", "CONFIG_ADC_SAM0": "y"}
    assert s.chain == ["DT_HAS_ATMEL_SAM0_ADC_ENABLED -> ADC_SAM0 -> ADC"]
    assert dict(os.environ) == env_before and sys.path == path_before


def test_derived_gpio_leds_and_already_enabled(rtos4_build):
    s = suggest_kconfig("gpio-leds", rtos4_build(BUILD))
    assert s.symbols == {"CONFIG_LED": "y", "CONFIG_LED_GPIO": "y"}
    s = suggest_kconfig("zephyr,cdc-acm-uart", rtos4_build(BUILD))
    assert "CONFIG_SERIAL" in s.symbols and "CONFIG_SERIAL" in s.already_enabled
    assert len(s.chain) > 1                 # several drivers, several chains


def test_unknown_compatible(rtos4_build):
    s = suggest_kconfig("nonexistent,foo", rtos4_build(BUILD))
    assert s.source == "none" and any("not defined" in n for n in s.notes)
