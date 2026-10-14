import logging
import sys

import pytest

from dt_explorer.core import plugins


@pytest.fixture(autouse=True)
def clean_registry():
    plugins.unregister_all()
    yield
    plugins.unregister_all()


def test_register_by_decorator():
    @plugins.register_provider
    class P:
        api_version = plugins.API_VERSION
        name = "fake"
        def matches(self, info): return info == "yes"
        def locate(self, info): return None
        def load(self, path): return None

    @plugins.register_formatter
    class F:
        api_version = plugins.API_VERSION
        name = "fmt"
        compatibles = ["a,b-pinctrl"]
        def to_overlay(self, s): return ""
        def from_overlay(self, v): return ""
        def render_group(self, signals): return ""

    assert plugins.get_provider("fake") is not None
    assert isinstance(plugins.get_provider("fake"), plugins.DeviceDataProvider)
    assert plugins.find_provider("yes").name == "fake"
    assert plugins.find_provider("no") is None
    assert plugins.get_formatter("a,b-pinctrl").name == "fmt"
    assert plugins.get_formatter("x") is None
    assert plugins.get_recipe("x") is None


def test_translation_keyed_by_pair():
    @plugins.register_translation
    class T:
        api_version = plugins.API_VERSION
        name = "t"
        source, target = "a,x", "b,y"
        def translate(self, props): return props, []

    assert plugins.get_translation("a,x", "b,y").name == "t"
    assert plugins.get_translation("b,y", "a,x") is None


def test_wrong_api_version_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="dt_explorer.plugins"):
        @plugins.register_recipe
        class R:
            api_version = plugins.API_VERSION + 1
            name = "future"
            compatibles = ["c,d"]

    assert plugins.get_recipe("c,d") is None
    assert plugins.recipes() == []
    assert "future" in caplog.text and "api_version" in caplog.text
    assert plugins.skipped_plugins()[0][1] == "future"


def _make_dist(tmp_path, group, ep_name, module_name, attr, source):
    (tmp_path / f"{module_name}.py").write_text(source)
    di = tmp_path / "fakeplug-1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: fakeplug\nVersion: 1.0\n")
    (di / "entry_points.txt").write_text(f"[{group}]\n{ep_name} = {module_name}:{attr}\n")


GOOD = """
class Prov:
    api_version = 1
    name = "ep_prov"
    def matches(self, info): return True
    def locate(self, info): return None
    def load(self, path): return None
"""

BAD = """
class Prov:
    api_version = 99
    name = "ep_old"
    def matches(self, info): return True
    def locate(self, info): return None
    def load(self, path): return None
"""


def test_entry_point_discovery(tmp_path, monkeypatch):
    _make_dist(tmp_path, "dt_explorer.providers", "ep_prov", "fakeplug_good", "Prov", GOOD)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert plugins.discover_entry_points() == 1
        assert plugins.get_provider("ep_prov") is not None
    finally:
        sys.modules.pop("fakeplug_good", None)


def test_entry_point_with_wrong_api_version_rejected(tmp_path, monkeypatch, caplog):
    _make_dist(tmp_path, "dt_explorer.providers", "ep_old", "fakeplug_bad", "Prov", BAD)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with caplog.at_level(logging.WARNING, logger="dt_explorer.plugins"):
            assert plugins.discover_entry_points() == 0
        assert plugins.get_provider("ep_old") is None
        assert "ep_old" in caplog.text and "api_version 99" in caplog.text
    finally:
        sys.modules.pop("fakeplug_bad", None)


def test_broken_entry_point_is_skipped(tmp_path, monkeypatch, caplog):
    _make_dist(tmp_path, "dt_explorer.formatters", "broken", "fakeplug_broken", "Nope",
               "x = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with caplog.at_level(logging.WARNING, logger="dt_explorer.plugins"):
            assert plugins.discover_entry_points() == 0
        assert "broken" in caplog.text
    finally:
        sys.modules.pop("fakeplug_broken", None)
