"""Prototype: read an Altium .PcbDoc (OLE) and trace connector pins to the MCU.

Usage: py prototypes/altium_pcbdoc_trace.py <file.PcbDoc> <file.atdf> [package-pinout-name]
"""
import re
import struct
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

import olefile


POWER_RE = re.compile(r"^(GND\w*|AGND|DGND|VCC\w*|VDD\w*|VBUS\w*|\+?\d+V\d*\w*|P\d+V\d+\w*|\d+V\d+\w*|NC)$", re.I)


def text_records(data: bytes) -> list[dict]:
    out, i = [], 0
    while i + 4 <= len(data):
        n = struct.unpack_from("<I", data, i)[0]
        i += 4
        raw = data[i:i + n].rstrip(b"\x00").decode("latin-1")
        i += n
        rec = {}
        for part in raw.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                rec[k.upper()] = v
        out.append(rec)
    return out


def pads(data: bytes) -> list[tuple]:
    """(pad name, net index or None, component index or None) per pad record."""
    out, i = [], 0
    while i < len(data):
        rtype = data[i]
        i += 1
        if rtype != 2:
            raise ValueError(f"unexpected record type {rtype} at {i - 1}")
        n = struct.unpack_from("<I", data, i)[0]; i += 4
        name = data[i + 1:i + 1 + data[i]].decode("latin-1"); i += n
        for _ in range(3):                                   # subrecords 2-4
            n = struct.unpack_from("<I", data, i)[0]; i += 4 + n
        n = struct.unpack_from("<I", data, i)[0]; i += 4
        s5 = data[i:i + n]; i += n
        net, comp = struct.unpack_from("<H", s5, 3)[0], struct.unpack_from("<H", s5, 7)[0]
        if len(s5) > 0:                                      # optional subrecord 6
            pass
        out.append((name, None if net == 0xFFFF else net, None if comp == 0xFFFF else comp))
        # subrecord 6 exists only in some files: peek for a plausible next record
        if i < len(data) and data[i] != 2:
            n = struct.unpack_from("<I", data, i)[0]; i += 4 + n
    return out


def main(pcb, atdf, pinout=None):
    o = olefile.OleFileIO(pcb)
    nets = [r.get("NAME", "?") for r in text_records(o.openstream("Nets6/Data").read())]
    comps = text_records(o.openstream("Components6/Data").read())
    pl = pads(o.openstream("Pads6/Data").read())

    by_comp = defaultdict(dict)        # designator -> {pad: net}
    by_net = defaultdict(list)         # net -> [(designator, pad)]
    for name, net, ci in pl:
        if ci is None:
            continue
        des = comps[ci].get("SOURCEDESIGNATOR", f"#{ci}")
        nn = nets[net] if net is not None else None
        by_comp[des][name] = nn
        if nn:
            by_net[nn].append((des, name))

    mcu = max(by_comp, key=lambda d: len(by_comp[d]) if d.startswith("U") else 0)
    print(f"{len(nets)} nets, {len(comps)} components, {len(pl)} pads; MCU = {mcu} "
          f"({len(by_comp[mcu])} pads, pattern {next(c.get('PATTERN') for c in comps if c.get('SOURCEDESIGNATOR') == mcu)})")

    root = ET.parse(atdf).getroot()
    pos2pad = {}
    for po in root.iter("pinout"):
        if pinout is None or po.get("name") == pinout:
            if len(list(po.iter("pin"))) == len(by_comp[mcu]) or pinout:
                pos2pad = {p.get("position"): p.get("pad") for p in po.iter("pin")}
                print("pinout", po.get("name"))
                break
    net2mcu = {}
    for pad, nn in by_comp[mcu].items():
        if nn:
            net2mcu.setdefault(nn, []).append(pos2pad.get(pad, pad))

    def trace(net, depth=0, seen=None):
        """MCU pads reachable from net, passing through 2-pad R/J/TP parts."""
        seen = seen or set()
        if net in seen or depth > 2:
            return []
        if POWER_RE.match(net) or (depth > 0 and len(by_net[net]) > 4):
            return []       # never walk through supply rails or busy nets (pull-ups etc.)
        seen.add(net)
        hits = [(p, depth) for p in net2mcu.get(net, [])]
        if hits:
            return hits
        for des, pad in by_net[net]:
            if re.match(r"^(R|J|JP|SJ|FB)\d", des) and len(by_comp[des]) == 2:
                other = [n for p, n in by_comp[des].items() if p != pad and n]
                for on in other:
                    hits += [(p, d) for p, d in trace(on, depth + 1, seen)]
        return hits

    headers = sorted((d for d in by_comp if re.match(r"^(J|P|CN|EXT)\d", d) and len(by_comp[d]) >= 6),
                     key=lambda d: (len(d), d))
    for h in headers:
        c = next(c for c in comps if c.get("SOURCEDESIGNATOR") == h)
        row = []
        for pad in sorted(by_comp[h], key=lambda p: (len(p), p)):
            nn = by_comp[h][pad]
            hit = trace(nn) if nn else []
            row.append(f"{pad}:{','.join(p + ('*' * d) for p, d in hit) or (nn or '-')}")
        print(f"\n{h} [{c.get('PATTERN')}] x={c.get('X')} y={c.get('Y')}\n  " + "  ".join(row))


if __name__ == "__main__":
    main(*sys.argv[1:])
