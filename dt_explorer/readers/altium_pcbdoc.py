"""Board reader for Altium ``.PcbDoc`` files (OLE compound documents).

Reads four streams: ``Nets6``, ``Components6``, ``Pads6`` and ``Texts6``. Needs the
``olefile`` package. Native coordinates are mil (pads/texts: 1/10000 mil); the
result is converted to millimetres.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

from dt_explorer.core.board import BoardNetlist, Component, Pad, Text
from dt_explorer.core.plugins import API_VERSION, register_board_reader

MIL = 0.0254            # mm per mil
RAW = MIL / 10000       # mm per native pad/text unit
NONE16 = 0xFFFF

_PAD_LAYER = {1: "top", 32: "bottom", 74: "all"}
_TEXT_LAYER = {33: "top_silk", 34: "bottom_silk"}


def _text_records(data: bytes) -> list[dict]:
    """Records of ``u32 length`` + ``|KEY=VALUE|...``; keys upper-cased."""
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


def _mil(value: str | None) -> float:
    """'8597.64mil' -> mm."""
    m = re.match(r"\s*(-?[\d.]+(?:[eE][+-]?\d+)?)", value or "")
    return float(m.group(1)) * MIL if m else 0.0


def _float(value: str | None) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _pads(data: bytes) -> list[tuple]:
    """(name, net idx|None, component idx|None, x mm, y mm, layer) per pad record."""
    out, i = [], 0
    while i < len(data):
        rtype = data[i]
        i += 1
        if rtype != 2:
            raise ValueError(f"unexpected pad record type {rtype} at {i - 1}")
        n = struct.unpack_from("<I", data, i)[0]
        i += 4
        name = data[i + 1:i + 1 + data[i]].decode("latin-1")
        i += n
        for _ in range(3):                                   # subrecords 2-4
            n = struct.unpack_from("<I", data, i)[0]
            i += 4 + n
        n = struct.unpack_from("<I", data, i)[0]
        i += 4
        s5 = data[i:i + n]
        i += n
        net = struct.unpack_from("<H", s5, 3)[0]
        comp = struct.unpack_from("<H", s5, 7)[0]
        x, y = struct.unpack_from("<ii", s5, 13)
        out.append((name, None if net == NONE16 else net, None if comp == NONE16 else comp,
                    x * RAW, y * RAW, _PAD_LAYER.get(s5[0], "other")))
        # Subrecord 6 exists only in some files: a following non-0x02 byte means it is present.
        if i < len(data) and data[i] != 2:
            n = struct.unpack_from("<I", data, i)[0]
            i += 4 + n
    return out


def _texts(data: bytes) -> list[Text]:
    """Free texts: record type 5, block (layer, position, height, rotation), then the string."""
    out, i = [], 0
    while i + 5 <= len(data):
        if data[i] != 5:
            break
        n = struct.unpack_from("<I", data, i + 1)[0]
        blk = data[i + 5:i + 5 + n]
        i += 5 + n
        if i + 4 > len(data):
            break
        n2 = struct.unpack_from("<I", data, i)[0]
        s = data[i + 4:i + 4 + n2]
        i += 4 + n2
        if len(blk) < 35 or not s:
            continue
        text = s[1:1 + s[0]].decode("latin-1")
        x, y, height = struct.unpack_from("<iii", blk, 13)
        rot = struct.unpack_from("<d", blk, 27)[0]
        out.append(Text(text, x * RAW, y * RAW, _TEXT_LAYER.get(blk[0], "other"),
                        height * RAW, rot))
    return out


@register_board_reader
class AltiumPcbDocReader:
    api_version = API_VERSION
    name = "altium_pcbdoc"
    extensions = [".pcbdoc"]

    def read(self, path: Path) -> BoardNetlist:
        import olefile                                       # optional dependency

        ole = olefile.OleFileIO(str(path))
        try:
            nets = [r.get("NAME", "?") for r in _text_records(ole.openstream("Nets6/Data").read())]
            comp_recs = _text_records(ole.openstream("Components6/Data").read())
            pad_recs = _pads(ole.openstream("Pads6/Data").read())
            texts = _texts(ole.openstream("Texts6/Data").read()) if ole.exists("Texts6/Data") else []
        finally:
            ole.close()

        components: dict[str, Component] = {}
        desig: list[str] = []
        for idx, r in enumerate(comp_recs):
            d = r.get("SOURCEDESIGNATOR") or f"#{idx}"
            desig.append(d)
            components[d] = Component(
                designator=d,
                footprint=r.get("PATTERN", ""),
                comment=r.get("SOURCEDESCRIPTION", ""),
                x=_mil(r.get("X")), y=_mil(r.get("Y")),
                rotation=_float(r.get("ROTATION")),
                layer="bottom" if r.get("LAYER", "").upper() == "BOTTOM" else "top",
            )
        pads = [Pad(component=None if ci is None else desig[ci], number=name,
                    net=None if ni is None else nets[ni], x=x, y=y, layer=layer)
                for name, ni, ci, x, y, layer in pad_recs]
        return BoardNetlist(nets=nets, components=components, pads=pads, texts=texts,
                            units="mm", source=Path(path).name)
