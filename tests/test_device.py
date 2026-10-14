import dataclasses

from dt_explorer.core.device import Constraint, DeviceModel, Instance, Package, Signal


def test_fields_as_designed():
    names = lambda c: [f.name for f in dataclasses.fields(c)]
    assert names(DeviceModel) == ["name", "packages", "instances", "constraints"]
    assert names(Package) == ["name", "kind", "pin_count", "pins"]
    assert names(Instance) == ["name", "kind", "base", "signals"]
    assert names(Signal) == ["role", "pad", "mux", "group", "attrs"]
    assert names(Constraint) == ["kind", "severity", "message"]


def test_build_model_and_defaults():
    s = Signal(role="TX", pad="X1", mux=object())
    assert s.group is None and s.attrs == {}
    assert Signal("TX", "X2", 1).attrs is not s.attrs
    m = DeviceModel("dev", [Package("P", "QFP", 2, {"1": "X1", "2": "GND"})],
                    {"I0": Instance("I0", "K", None, [s])},
                    [Constraint("group", "warning", "msg")])
    assert m.instances["I0"].signals[0] is s
    assert m.packages[0].pins["2"] == "GND"
