"""Import pipeline: ``BoardNetlist`` + ``DeviceModel`` -> board YAML.

Steps (docs/CONFIGURATOR.md, "Generic import pipeline"):
1. find the MCU: the component whose pad numbers fit a package of the device model;
2. trace every connector pin to an MCU pad (through 2-pad R/J/FB parts, never through
   supply rails or busy nets); report gaps instead of guessing;
3. detect the connector type with the connector plugins (score >= THRESHOLD);
4. suggest the connector name from silkscreen text near the connector;
5. build the board YAML. Everything is ``suggested``; an entry is ``reviewed: builder``
   only when silkscreen name and signal pattern agree (decision HC-6).

Only ``dt_explorer.core.*`` and the plugin registries (connector helpers) are used. The
design files are never copied into the result.

Command line::

    py -m dt_explorer.importers.pipeline --pcb X.PcbDoc --atdf Y.atdf --board same54_xpro --out out.yaml
"""
from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dt_explorer import connectors as _connectors  # registers the connector types
from dt_explorer.connectors import is_gnd, is_vcc
from dt_explorer.core import plugins
from dt_explorer.core.board import BoardNetlist, Component, Text
from dt_explorer.core.device import Package

SCHEMA_VERSION = 1
THRESHOLD = 0.5                  # minimum connector detection score (P7.2)
MIN_CONNECTOR_PADS = 6           # smaller parts are not connectors
MAX_TRACE_DEPTH = 2              # series parts passed on the way to the MCU
BUSY_NET = 4                     # nets with more pads are not walked (pull-ups etc.)
MAX_NAME_DISTANCE = 25.0         # mm, connector centre to silkscreen text
MIN_TRACED_FRACTION = 0.75       # HC-6: share of signal pins that must reach the MCU
MCU_MIN_FIT = 0.9                # share of package positions present as pad numbers
MCU_MAX_EXTRA_PADS = 2           # pads beyond the package positions (exposed pad)
NC_PAD = "NC"                    # ATDF pad name of a position that has no usable pin on this SoC
NC_MARK = "NC@"                  # internal marker: MCU footprint position that is NC on this SoC

POWER_RE = re.compile(
    r"^(GND\w*|AGND|DGND|VCC\w*|VDD\w*|VBUS\w*|\+?\d+V\d*\w*|P\d+V\d+\w*|\d+V\d+\w*|NC)$", re.I)
CONNECTOR_DESIGNATOR = re.compile(r"^(J|P|CN|EXT)\d")
SERIES_PART = re.compile(r"^(R|J|JP|SJ|FB)\d")

# Silkscreen texts that look like connector names, and which connector type each one fits.
NAME_PATTERNS = {
    "xplained_pro_ext": re.compile(r"^EXT\d+$"),
    "mikrobus": re.compile(r"^mikro\s?bus\d*$", re.I),
    "arduino_uno_r3": re.compile(r"^arduino", re.I),
}
ANY_NAME = re.compile("|".join(p.pattern for p in NAME_PATTERNS.values()), re.I)


@dataclass
class Trace:
    pin: str
    net: str | None
    pads: list[str] = field(default_factory=list)       # MCU pad ids reached
    via: list[str] = field(default_factory=list)        # series parts passed
    gap: str | None = None                              # reason if no MCU pad was reached
    unavailable: list[str] = field(default_factory=list)  # footprint positions that are NC on this SoC


@dataclass
class Connector:
    designator: str
    type: str
    score: float
    name: str | None
    name_text: str | None
    status: str                      # "suggested" | "reviewed"
    reason: str
    traces: dict[str, Trace]
    component: Component | None = None
    zephyr_binding: str | None = None


@dataclass
class ImportResult:
    mcu: str
    mcu_package: str
    mcu_fit: float
    connectors: list[Connector]
    report: list[str]
    gaps: list[str]
    data: dict = field(default_factory=dict)            # the YAML document


def ensure_builtin_plugins() -> None:
    """Re-register the built-in connector types if the registry was cleared (tests do that)."""
    from dt_explorer.connectors.arduino_uno_r3 import ArduinoUnoR3
    from dt_explorer.connectors.mikrobus import MikroBus
    from dt_explorer.connectors.xplained_pro_ext import XplainedProExt
    for cls in (XplainedProExt, ArduinoUnoR3, MikroBus):
        if plugins.get_connector_type(cls.name) is None:
            plugins.register("connector", cls())


# --- step 1: MCU --------------------------------------------------------------

def _significant(netlist: BoardNetlist, designator: str):
    """Pads of a component without the "0" pads (mounting/shield pads without a net)."""
    return [p for p in netlist.pads_of(designator) if p.number != "0"]


def find_mcu(netlist: BoardNetlist, packages: list[Package]) -> tuple[str, Package, float] | None:
    """Component + package of the device model that fit best; None if nothing fits.

    Fit = share of package positions that exist as pad numbers of the component, and the
    component has at most a few more pads than the package (exposed pad).
    """
    best = None
    for des in netlist.components:
        pads = {p.number for p in _significant(netlist, des)}
        if len(pads) < 16:
            continue
        for pkg in packages:
            if not pkg.pins or len(pads - set(pkg.pins)) > MCU_MAX_EXTRA_PADS:
                continue
            fit = len(pads & set(pkg.pins)) / len(pkg.pins)
            if fit >= MCU_MIN_FIT and (best is None or fit > best[2]):
                best = (des, pkg, fit)
    return best


# --- step 2: tracing ----------------------------------------------------------

class Tracer:
    def __init__(self, netlist: BoardNetlist, mcu: str, position_to_pad: dict[str, str]):
        self.nl = netlist
        self.mcu = mcu
        self.net2mcu: dict[str, list[str]] = {}
        for p in _significant(netlist, mcu):
            if p.net:
                pad = position_to_pad.get(p.number, p.number)
                if pad == NC_PAD:             # pad exists on the PCB footprint, not on this SoC
                    pad = NC_MARK + p.number
                self.net2mcu.setdefault(p.net, []).append(pad)

    def _walk(self, net: str, depth: int, seen: set[str]) -> list[tuple[str, list[str]]]:
        if net in seen or depth > MAX_TRACE_DEPTH:
            return []
        if POWER_RE.match(net) or (depth > 0 and len(self.nl.pads_on_net(net)) > BUSY_NET):
            return []
        seen.add(net)
        if net in self.net2mcu:
            return [(p, []) for p in self.net2mcu[net]]
        hits: list[tuple[str, list[str]]] = []
        for pad in self.nl.pads_on_net(net):
            des = pad.component
            if not des or not SERIES_PART.match(des):
                continue
            own = self.nl.pads_of(des)
            if len(own) != 2:
                continue
            for other in own:
                if other.number != pad.number and other.net:
                    for p, via in self._walk(other.net, depth + 1, seen):
                        hits.append((p, [des] + via))
        return hits

    def trace(self, connector: str, pin: str, net: str | None) -> Trace:
        t = Trace(pin, net)
        if not net:
            t.gap = "pad has no net"
            return t
        if POWER_RE.match(net):
            t.gap = "supply/NC net"
            return t
        for pad, via in self._walk(net, 0, set()):
            if pad.startswith(NC_MARK):
                if pad[len(NC_MARK):] not in t.unavailable:
                    t.unavailable.append(pad[len(NC_MARK):])
                continue
            if pad not in t.pads:
                t.pads.append(pad)
            for v in via:
                if v not in t.via:
                    t.via.append(v)
        if not t.pads and t.unavailable:
            return t                          # reaches only pads that this SoC does not have
        if not t.pads:
            others = [d for d in self.nl.components_on_net(net) if d not in (connector, self.mcu)]
            multi = [d for d in others if len(self.nl.pads_of(d)) > 2]
            if multi:
                t.gap = ("routed through part with more than 2 pads: "
                         + ", ".join(f"{d} ({len(self.nl.pads_of(d))} pads)" for d in multi))
            else:
                t.gap = "net ends without reaching the MCU" + (
                    f" (also on {', '.join(others)})" if others else " (no other part)")
        return t


# --- step 3/4: type and name ----------------------------------------------------

def detect_type(pins: dict[str, str | None], pin_count: int):
    """(plugin, score) of the best connector type, scores of all types for the report."""
    scores = []
    for ct in plugins.connector_types():
        scores.append((ct.detect(pins, pin_count), ct.name, ct))
    scores.sort(key=lambda s: (-s[0], s[1]))
    return scores


def _centre(netlist: BoardNetlist, des: str, comp: Component | None) -> tuple[float, float]:
    pads = _significant(netlist, des)
    if pads:
        return (sum(p.x for p in pads) / len(pads), sum(p.y for p in pads) / len(pads))
    return (comp.x, comp.y) if comp else (0.0, 0.0)


def assign_names(centres: dict[str, tuple[float, float]], layers: dict[str, str],
                 texts: list[Text]) -> dict[str, Text]:
    """Greedy nearest-first assignment of connector-like silkscreen texts to connectors.

    Each text and each connector is used once. Texts on the silkscreen layer of the
    component's side win over the opposite side at equal distance.
    """
    cands = [t for t in texts if ANY_NAME.match(t.text.strip())]
    pairs = []
    for des, (cx, cy) in centres.items():
        for t in cands:
            d = math.hypot(t.x - cx, t.y - cy)
            if d <= MAX_NAME_DISTANCE:
                wrong_side = not t.layer.startswith(layers.get(des, "top"))
                pairs.append((d + (0.001 if wrong_side else 0.0), des, t))
    pairs.sort(key=lambda p: (p[0], p[1], p[2].text))
    out: dict[str, Text] = {}
    used: set[str] = set()
    for _d, des, t in pairs:
        if des in out or t.text.strip() in used:
            continue
        out[des] = t
        used.add(t.text.strip())
    return out


# --- step 5: pipeline -----------------------------------------------------------

def _revision(text: str) -> str | None:
    m = re.search(r"[_\s-]R(?:ev)?\.?(\d+)", text, re.I)
    return m.group(1) if m else None


def import_board(netlist: BoardNetlist, packages: list[Package], *, board: str,
                 source_package: str | None = None, revision: str | None = None,
                 soc: str | None = None, mcu_part: str | None = None,
                 bom_evidence: str | None = None) -> ImportResult:
    """Run the pipeline. `packages` come from the build's DeviceModel."""
    ensure_builtin_plugins()
    report: list[str] = []
    gaps: list[str] = []
    source_package = source_package or netlist.source
    revision = revision or _revision(source_package or "")

    found = find_mcu(netlist, packages)
    if found is None:
        raise ValueError("no component fits a package of the device model")
    mcu, pkg, fit = found
    report.append(f"MCU: {mcu} ({netlist.components[mcu].footprint}) fits package {pkg.name} "
                  f"{fit:.0%} ({len(_significant(netlist, mcu))} pads, package {pkg.pin_count})")
    nc_positions = sorted((k for k, v in pkg.pins.items() if v == NC_PAD), key=lambda k: (len(k), k))
    report.append(f"NC positions of {pkg.name} on {soc or 'this SoC'} (footprint pads without a pin): "
                  f"{', '.join(nc_positions) or 'none'}")
    tracer = Tracer(netlist, mcu, pkg.pins)

    candidates = [d for d, c in netlist.components.items()
                  if d != mcu and CONNECTOR_DESIGNATOR.match(d)
                  and len(_significant(netlist, d)) >= MIN_CONNECTOR_PADS]
    candidates.sort(key=lambda d: (len(d), d))

    found_conn: list[tuple[str, str, float, dict, dict]] = []
    for des in candidates:
        sig = _significant(netlist, des)
        pins = {p.number: p.net for p in sig}
        scores = detect_type(pins, len(pins))
        best_score, best_name, best_ct = scores[0]
        if best_score < THRESHOLD:
            report.append(f"{des}: no connector type >= {THRESHOLD} "
                          f"(best {best_name} {best_score}); not written")
            continue
        found_conn.append((des, best_name, best_score, pins, {s[1]: s[0] for s in scores}))

    centres = {d: _centre(netlist, d, netlist.components.get(d)) for d, *_ in found_conn}
    layers = {d: netlist.components[d].layer for d, *_ in found_conn}
    names = assign_names(centres, layers, netlist.texts)

    connectors: list[Connector] = []
    for des, tname, score, pins, allscores in found_conn:
        ct = plugins.get_connector_type(tname)
        roles = ct.pin_roles()
        traces = {pin: tracer.trace(des, pin, net) for pin, net in
                  sorted(pins.items(), key=lambda kv: (len(kv[0]), kv[0]))}
        text = names.get(des)
        name = text.text.strip() if text else None
        for pin, t in traces.items():
            role = roles.get(pin, "?")
            if t.via:
                report.append(f"{des} pin {pin} ({role}): {t.net} -> {', '.join(t.pads)} via {', '.join(t.via)}")
            if t.unavailable and not t.pads:
                report.append(f"{des} pin {pin} ({role}): net {t.net} reaches MCU footprint position "
                              f"{', '.join(t.unavailable)}, which is NC on {soc or pkg.name}: "
                              f"not available on this MCU")
            if len(t.pads) > 1:
                gaps.append(f"{des} pin {pin} ({role}): net {t.net} reaches several MCU pads: {', '.join(t.pads)}")
            if role == "ID":
                continue                      # ID chips are not on the MCU by design
            if t.gap and t.net and not POWER_RE.match(t.net):
                gaps.append(f"{des} pin {pin} ({role}): net {t.net}: {t.gap}")
            elif t.gap == "pad has no net" and roles.get(pin) not in (None, "ID"):
                gaps.append(f"{des} pin {pin} ({role}): {t.gap}")

        status, reason = review(ct, name, score, pins, traces, roles)
        connectors.append(Connector(des, tname, score, name, text.text if text else None,
                                    status, reason, traces, netlist.components.get(des),
                                    getattr(ct, "zephyr_binding", None)))
        report.append(f"{des}: type {tname} ({score}), name {name or '-'}, {status}: {reason}")

    connectors.sort(key=lambda c: (c.name or "~", c.designator))
    res = ImportResult(mcu, pkg.name, fit, connectors, report, gaps)
    res.data = build_yaml(res, board=board, soc=soc, source_package=source_package,
                          revision=revision, source_file=netlist.source, mcu_part=mcu_part,
                          bom_evidence=bom_evidence)
    return res


def review(ct, name: str | None, score: float, pins: dict, traces: dict[str, Trace],
           roles: dict[str, str]) -> tuple[str, str]:
    """HC-6: `reviewed` only when silkscreen name and signal pattern agree."""
    problems = []
    pattern = NAME_PATTERNS.get(ct.name)
    if not name:
        problems.append("no silkscreen name near the connector")
    elif pattern is None or not pattern.match(name):
        problems.append(f"silkscreen text {name!r} does not fit type {ct.name}")
    if score < THRESHOLD:
        problems.append(f"detection score {score} < {THRESHOLD}")
    gnd = [p for p, r in roles.items() if r == "GND"]
    vcc = [p for p, r in roles.items() if r in ("VCC", "3V3", "5V")]
    if not all(is_gnd(pins.get(p) or "") for p in gnd):
        problems.append("GND pins " + "/".join(gnd) + " are not ground nets")
    if vcc and not any(is_vcc(pins.get(p) or "") for p in vcc):
        problems.append("VCC pin " + "/".join(vcc) + " is not a supply net")
    signal = [p for p, r in roles.items() if r not in ("GND", "VCC", "3V3", "5V", "ID")]
    reached = [p for p in signal if traces.get(p) and traces[p].pads]
    if signal and len(reached) / len(signal) < MIN_TRACED_FRACTION:
        problems.append(f"only {len(reached)}/{len(signal)} signal pins reach the MCU")
    if problems:
        return "suggested", "; ".join(problems)
    return "reviewed", (f"silkscreen {name!r} fits type {ct.name}, score {score}, GND"
                        f"{'/VCC' if vcc else ''} pins as in the standard, "
                        f"{len(reached)}/{len(signal)} signal pins traced to the MCU")


def build_yaml(res: ImportResult, *, board: str, soc: str | None, source_package: str | None,
               revision: str | None, source_file: str, mcu_part: str | None = None,
               bom_evidence: str | None = None) -> dict:
    conns = []
    for c in res.connectors:
        ct = plugins.get_connector_type(c.type)
        roles = ct.pin_roles()
        pins = {}
        for pin, t in c.traces.items():
            entry = {"role": roles.get(pin)}
            entry["pad"] = t.pads[0] if t.pads else None
            if len(t.pads) > 1:
                entry["pad_alternatives"] = t.pads[1:]
            entry["net"] = t.net
            if t.unavailable and not t.pads:
                entry["available"] = False
                entry["unavailable_reason"] = (
                    f"net reaches MCU footprint position {', '.join(t.unavailable)}, which is NC on "
                    f"{soc or res.mcu_package}; the pad exists on the PCB only")
            if t.via:
                entry["via"] = t.via
            if t.gap and t.net and not POWER_RE.match(t.net):
                entry["gap"] = t.gap
            pins[pin] = entry
        item = {
            "name": c.name, "designator": c.designator, "type": c.type,
            "zephyr_binding": c.zephyr_binding, "detection_score": c.score,
            "status": c.status, "reason": c.reason,
        }
        if c.status == "reviewed":
            item["reviewed"] = "builder"
        if c.component:
            item["position"] = {"x": round(c.component.x, 2), "y": round(c.component.y, 2),
                                "rotation": round(c.component.rotation, 1), "layer": c.component.layer}
        item["pins"] = pins
        conns.append(item)
    source = {"package": source_package, "file": source_file, "revision": revision,
              "units": "mm", "generator": "dt_explorer.importers.pipeline"}
    if bom_evidence:
        source["bom"] = bom_evidence
    mcu = {"soc": soc, "designator": res.mcu, "package": res.mcu_package, "fit": round(res.mcu_fit, 3)}
    if mcu_part:
        mcu["part"] = mcu_part
    return {
        "schema_version": SCHEMA_VERSION,
        "board": board,
        "source": source,
        "mcu": mcu,
        "connectors": conns,
        "gaps": list(res.gaps),
    }


def write_yaml(res: ImportResult, path: str | Path) -> None:
    text = yaml.safe_dump(res.data, sort_keys=False, allow_unicode=True, width=100)
    Path(path).write_text(text, encoding="utf-8", newline="\n")


# --- files ---------------------------------------------------------------------

def import_files(pcb: str | Path, atdf: str | Path, *, board: str, source_package: str | None = None,
                 revision: str | None = None, soc: str | None = None, mcu_part: str | None = None,
                 bom_evidence: str | None = None) -> ImportResult:
    from dt_explorer.readers import altium_pcbdoc
    from dt_explorer.providers import microchip_atdf  # noqa: F401  (registers the provider)
    reader = plugins.get_board_reader(pcb)
    if reader is None and Path(pcb).suffix.lower() == ".pcbdoc":
        plugins.register("board_reader", altium_pcbdoc.AltiumPcbDocReader())
        reader = plugins.get_board_reader(pcb)
    if reader is None:
        raise ValueError(f"no board reader for {pcb}")
    netlist = reader.read(Path(pcb))
    device = microchip_atdf.MicrochipAtdfProvider().load(Path(atdf))
    return import_board(netlist, device.packages, board=board, soc=soc or device.name,
                        source_package=source_package or Path(pcb).stem, revision=revision,
                        mcu_part=mcu_part, bom_evidence=bom_evidence)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--atdf", required=True)
    ap.add_argument("--board", required=True, help="Zephyr board name, e.g. same54_xpro")
    ap.add_argument("--out", help="board YAML to write")
    ap.add_argument("--revision")
    ap.add_argument("--mcu-part", help="populated MCU part number (from the BOM)")
    ap.add_argument("--bom-evidence", help="where the MCU part comes from, e.g. BOM file and row")
    a = ap.parse_args(argv)
    res = import_files(a.pcb, a.atdf, board=a.board, revision=a.revision,
                       mcu_part=a.mcu_part, bom_evidence=a.bom_evidence)
    print("\n".join(res.report))
    print("\nGAPS")
    print("\n".join(res.gaps) or "(none)")
    if a.out:
        write_yaml(res, a.out)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
