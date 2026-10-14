"""P7.2: connector types, pin roles against the Zephyr bindings, detection scores."""
from __future__ import annotations

import random
import re

import pytest

from dt_explorer.core import plugins
from dt_explorer.connectors.arduino_uno_r3 import ArduinoUnoR3
from dt_explorer.connectors.mikrobus import MikroBus
from dt_explorer.connectors.xplained_pro_ext import XplainedProExt
from conftest import env_path

XPRO, ARD, MIKRO = XplainedProExt(), ArduinoUnoR3(), MikroBus()
ALL = [XPRO, ARD, MIKRO]

# --- synthetic netlists ------------------------------------------------------------

def xpro_netlist(named=True):
    """Typical EXT header: ID, GND, VCC fixed; signals named like the roles."""
    nets = {"1": "EXT1_ID", "2": "GND", "3": "PB04", "4": "PB05", "5": "PA06", "6": "PA07",
            "7": "PWM_P", "8": "PWM_N", "9": "EXT1_IRQ", "10": "PA27",
            "11": "SDA", "12": "SCL", "13": "UART_RXD", "14": "UART_TXD",
            "15": "PB28", "16": "SPI_MOSI", "17": "SPI_MISO", "18": "SPI_SCK",
            "19": "GND", "20": "VCC_P3V3"}
    if not named:   # real boards: signal nets are bare MCU pin names
        nets.update({k: f"P{k}" for k in "7 8 9 11 12 13 14 16 17 18".split()})
    return nets


def mikro_netlist():
    return {"1": "AN0", "2": "MB_RST", "3": "MB_CS", "4": "MB_SCK", "5": "MB_MISO",
            "6": "MB_MOSI", "7": "+3V3", "8": "GND", "9": "MB_PWM", "10": "MB_INT",
            "11": "MB_RX", "12": "MB_TX", "13": "MB_SCL", "14": "MB_SDA", "15": "+5V",
            "16": "GND"}


def arduino_netlist():
    n = {f"A{i}": f"ARD_A{i}" for i in range(6)}
    n.update({f"D{i}": f"ARD_D{i}" for i in range(16)})
    n.update({"D0": "UART_RX", "D1": "UART_TX", "D11": "SPI_MOSI", "D12": "SPI_MISO",
              "D13": "SPI_SCK", "D14": "I2C_SDA", "D15": "I2C_SCL"})
    n.update({"3V3": "3V3", "5V": "5V", "GND": "GND", "GND2": "GND", "VIN": "VIN",
              "IOREF": "IOREF", "RESET": "NRST", "AREF": "AREF"})
    return n


def random_20pin(seed):
    rnd = random.Random(seed)
    names = ["PA01", "PB22", "NET_17", "LED", "BTN", "X1", "Y2", "CLK_OUT", "D_P", "D_N", None]
    return {str(i): rnd.choice(names) for i in range(1, 21)}


NETLISTS = {"xplained_pro_ext": xpro_netlist(), "arduino_uno_r3": arduino_netlist(),
            "mikrobus": mikro_netlist()}

LOW = 0.35    # a random connector must stay below this for every type
HIGH = 0.8    # a typical connector of its own type must reach this


def scores(netlist):
    return {c.name: c.detect(netlist) for c in ALL}


@pytest.mark.parametrize("kind", list(NETLISTS))
def test_own_type_scores_highest(kind):
    s = scores(NETLISTS[kind])
    print(kind, s)
    assert max(s, key=s.get) == kind
    assert s[kind] >= HIGH
    assert all(v < LOW for k, v in s.items() if k != kind)


def test_xpro_without_signal_names_still_beats_others():
    s = scores(xpro_netlist(named=False))
    print("xpro, bare MCU-pin signal nets:", s)
    assert max(s, key=s.get) == "xplained_pro_ext"
    assert s["xplained_pro_ext"] >= 0.5
    assert all(v < LOW for k, v in s.items() if k != "xplained_pro_ext")


@pytest.mark.parametrize("seed", range(20))
def test_random_20pin_is_low(seed):
    s = scores(random_20pin(seed))
    assert all(v < LOW for v in s.values()), s


def test_random_20pin_scores_summary():
    worst = max(max(scores(random_20pin(s)).values()) for s in range(20))
    print("max score over 20 random 20-pin connectors and 3 types:", worst)
    assert worst < LOW


def test_gnd_vcc_swapped_positions_score_low():
    n = xpro_netlist()
    n["2"], n["20"], n["19"] = "VCC_P3V3", "GND", "PA01"
    assert XPRO.detect(n) <= XPRO.detect(xpro_netlist()) - 0.3   # lost GND/VCC positions


def test_int_keys_none_nets_and_pin_count():
    n = {int(k): v for k, v in xpro_netlist().items()}
    n[3] = None
    assert XPRO.detect(n) == pytest.approx(XPRO.detect(xpro_netlist()))
    # a partial netlist counts as 20 pins when the caller says so
    part = {k: v for k, v in xpro_netlist().items() if int(k) <= 10}
    assert XPRO.detect(part, pin_count=20) > XPRO.detect(part)


def test_deterministic():
    n = xpro_netlist()
    assert [XPRO.detect(n) for _ in range(3)] == [XPRO.detect(n)] * 3
    for c in ALL:
        assert c.detect({}) == 0.0


def test_registration():
    plugins.unregister_all()
    import importlib
    import dt_explorer.connectors.arduino_uno_r3 as a
    import dt_explorer.connectors.mikrobus as m
    import dt_explorer.connectors.xplained_pro_ext as x
    for mod in (x, a, m):
        importlib.reload(mod)
    try:
        assert {c.name for c in plugins.connector_types()} == set(NETLISTS)
        for c in plugins.connector_types():
            assert isinstance(c, plugins.ConnectorType)
        assert plugins.get_connector_type("mikrobus").zephyr_binding == "mikro-bus"
    finally:
        plugins.unregister_all()


# --- pin roles vs. Zephyr bindings --------------------------------------------------

def bindings_dir(ws):
    d = ws / "zephyr" / "dts" / "bindings" / "gpio"
    if not d.is_dir():
        pytest.skip(f"no gpio bindings in {d}")
    return d


# binding text of an Xplained Pro pin name -> our role
XPRO_NAMES = {
    "ID": "ID", "GND": "GND", "ADC(+)": "ADC+", "ADC(-)": "ADC-",
    "UART(RTS)/GPIO1": "GPIO1", "UART(CTS)/GPIO2": "GPIO2",
    "PWM(+)": "PWM+", "PWM(-)": "PWM-", "IRQ/GPIO3": "IRQ", "SPI(CS1)/GPIO4": "SPI_SS_B",
    "I2C(SDA)": "I2C_SDA", "I2C(SCL)": "I2C_SCL", "UART(RX)": "UART_RX", "UART(TX)": "UART_TX",
    "SPI(CS0)": "SPI_SS_A", "SPI(MOSI)": "SPI_MOSI", "SPI(MISO)": "SPI_MISO",
    "SPI(SCK)": "SPI_SCK", "VDD(+3.3V)": "VCC",
}


def parse_xpro_table(text):
    """pin -> (binding pin name, binding nexus index or None) from the description table."""
    out = {}
    for line in text.splitlines():
        m6 = re.match(r"^\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$", line)
        m4 = re.match(r"^\s+([A-Za-z]\S*)\s+(\d+)\s+(\d+)\s+(\S+)\s*$", line)
        if m6:
            b1, n1, p1, p2, n2, b2 = m6.groups()
            out[p1], out[p2] = (n1, int(b1)), (n2, int(b2))
        elif m4:
            n1, p1, p2, n2 = m4.groups()
            out[p1], out[p2] = (n1, None), (n2, None)
    return out


def test_xplained_pro_roles_match_binding():
    ws = env_path("DTX_RTOS4_WS")
    f = bindings_dir(ws) / "atmel-xplained-pro-header.yaml"
    text = f.read_text(encoding="utf-8")
    assert f'compatible: "{XPRO.zephyr_binding}"' in text
    table = parse_xpro_table(text)
    roles = XPRO.pin_roles()
    print(f"compared {len(table)} pins from {f.name}: pin: binding name -> role (nexus index)")
    assert len(table) == 20 and set(table) == set(roles)
    for pin, (name, idx) in sorted(table.items(), key=lambda kv: int(kv[0])):
        print(f"  {pin:>2}: {name:<16} -> {roles[pin]:<9} (index {idx})")
        assert XPRO_NAMES[name] == roles[pin], (pin, name)
        if idx is not None:
            assert XPRO.nexus_map()[idx] == pin
    assert sorted(XPRO.nexus_map()) == list(range(16))


# Zephyr gpio-map comment in samr21_xpro.dts (index -> expected role) for ext1_header
SAMR21_ROLE_COMMENT = {0: ("ADC", "ADC+"), 1: ("ADC", "ADC-"), 8: ("TWD", "I2C_SDA"),
                       9: ("TWCK", "I2C_SCL"), 10: ("RXD", "UART_RX"), 11: ("TXD", "UART_TX"),
                       12: ("SPI5(SS)", "SPI_SS_A"), 13: ("SPI5(MOSI)", "SPI_MOSI"),
                       14: ("SPI5(MISO)", "SPI_MISO"), 15: ("SPI5(SCK)", "SPI_SCK")}


def gpio_map_comments(dts_text, node_compatible):
    """index -> comment text of the first gpio-map of a node with `compatible`."""
    start = dts_text.index(f'compatible = "{node_compatible}"')
    block = dts_text[start:dts_text.index("};", start)]
    return {int(m.group(1)): m.group(2).strip()
            for m in re.finditer(r"<(\d+)\s+0\s+&\w+\s+\d+\s+0>[,;]\s*/\*\s*(.*?)\s*\*/", block)}


def test_xplained_pro_roles_match_board_dts_comments():
    ws = env_path("DTX_RTOS4_WS")
    dts = ws / "zephyr" / "boards" / "atmel" / "sam0" / "samr21_xpro" / "samr21_xpro.dts"
    if not dts.is_file():
        pytest.skip(f"{dts} missing")
    comments = gpio_map_comments(dts.read_text(encoding="utf-8"), XPRO.zephyr_binding)
    nexus, roles = XPRO.nexus_map(), XPRO.pin_roles()
    print(f"compared {len(SAMR21_ROLE_COMMENT)} indices of {dts.name} ext1_header:")
    for idx, (word, role) in SAMR21_ROLE_COMMENT.items():
        print(f"  index {idx:>2} comment {comments[idx]!r:<22} -> pin {nexus[idx]:>2} {roles[nexus[idx]]}")
        assert word in comments[idx]
        assert roles[nexus[idx]] == role


MIKRO_ROW = {"AN": 0, "RST": 1, "CS": 2, "SCK": 3, "MISO": 4, "MOSI": 5,
             "PWM": 6, "INT": 7, "RX": 8, "TX": 9, "SCL": 10, "SDA": 11}


def test_mikrobus_roles_match_binding_and_board_dts():
    ws = env_path("DTX_RTOS4_WS")
    f = bindings_dir(ws) / "mikro-bus.yaml"
    text = f.read_text(encoding="utf-8")
    assert f'compatible: "{MIKRO.zephyr_binding}"' in text
    # binding text: left pins numbered 0 - 5 (AN - MOSI), right pins 6 - 11 (PWM - SDA)
    assert "numbered 0 - 5 (AN - MOSI)" in text and "6 - 11" in text and "(PWM - SDA)" in text
    nexus, roles = MIKRO.nexus_map(), MIKRO.pin_roles()
    print("compared binding text 0-5 (AN - MOSI), 6-11 (PWM - SDA):")
    for role, idx in MIKRO_ROW.items():
        print(f"  index {idx:>2} {role:<4} -> pin {nexus[idx]:>2} role {roles[nexus[idx]]}")
        assert roles[nexus[idx]] == role
    # power pins are not in the nexus: bottom 2 pins on each side
    assert [roles[p] for p in ("7", "8", "15", "16")] == ["3V3", "GND", "5V", "GND"]
    assert set(roles) - {nexus[i] for i in nexus} == {"7", "8", "15", "16"}
    # a real board: gpio-map comments of mikroe_quail.dts, first socket
    dts = ws / "zephyr" / "boards" / "mikroe" / "quail" / "mikroe_quail.dts"
    if dts.is_file():
        comments = gpio_map_comments(dts.read_text(encoding="utf-8"), MIKRO.zephyr_binding)
        print(f"compared {len(comments)} comments of {dts.name}:", comments)
        assert len(comments) == 12
        for role, idx in MIKRO_ROW.items():
            assert comments[idx].upper().startswith(role)


def test_arduino_roles_match_binding_header_and_board():
    ws = env_path("DTX_RTOS4_WS")
    f = bindings_dir(ws) / "arduino-header-r3.yaml"
    assert f'compatible: "{ARD.zephyr_binding}"' in f.read_text(encoding="utf-8")
    h = ws / "zephyr" / "include" / "zephyr" / "dt-bindings" / "gpio" / "arduino-header-r3.h"
    if not h.is_file():
        pytest.skip(f"{h} missing")
    defs = {m.group(1): int(m.group(2))
            for m in re.finditer(r"#define\s+ARDUINO_HEADER_R3_(\w+)\s+(\d+)", h.read_text(encoding="utf-8"))}
    nexus, roles = ARD.nexus_map(), ARD.pin_roles()
    print(f"compared {len(defs)} ARDUINO_HEADER_R3_* constants of {h.name} with nexus_map():")
    print("  ", defs)
    assert len(defs) == 22
    for label, idx in defs.items():
        assert nexus[idx] == label and label in roles
    assert len(nexus) == 22
    # the standard roles of the Uno R3 (binding text: D14/D15 on top, next to AREF)
    assert [roles[k] for k in ("D0", "D1", "D10", "D11", "D12", "D13", "D14", "D15")] == [
        "UART_RX", "UART_TX", "SPI_SS", "SPI_MOSI", "SPI_MISO", "SPI_SCK", "I2C_SDA", "I2C_SCL"]
    yaml_text = f.read_text(encoding="utf-8")
    for label in ("A0", "A5", "D0", "D8", "D13", "D14", "D15", "AREF", "IOREF", "3V3", "5V", "VIN"):
        assert label in yaml_text and label in roles
