"""Neutral board netlist: what a board design file says, without any EDA vocabulary.

Readers (plugins) produce a ``BoardNetlist``; tracing, connector detection and the
board description work on it. Lengths are in millimetres, angles in degrees.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Component:
    designator: str                 # "J401"
    footprint: str = ""             # land pattern name
    comment: str = ""               # free-text description / value
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    layer: str = "top"              # "top" | "bottom"


@dataclass
class Pad:
    component: str | None           # designator, None for free pads
    number: str                     # pad / pin number as written in the design
    net: str | None                 # net name, None if unconnected
    x: float = 0.0
    y: float = 0.0
    layer: str = "top"              # "top" | "bottom" | "all" (through hole) | "other"


@dataclass
class Text:
    text: str
    x: float
    y: float
    layer: str = ""                 # "top_silk" | "bottom_silk" | "other"
    height: float = 0.0
    rotation: float = 0.0


@dataclass
class BoardNetlist:
    nets: list[str] = field(default_factory=list)
    components: dict[str, Component] = field(default_factory=dict)
    pads: list[Pad] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    units: str = "mm"
    source: str = ""                # file name the netlist was read from

    def __post_init__(self) -> None:
        self._index: tuple | None = None

    def _build(self) -> tuple:
        if self._index is None or self._index[0] != len(self.pads):
            by_comp: dict[str, list[Pad]] = {}
            by_net: dict[str, list[Pad]] = {}
            for p in self.pads:
                if p.component is not None:
                    by_comp.setdefault(p.component, []).append(p)
                if p.net is not None:
                    by_net.setdefault(p.net, []).append(p)
            self._index = (len(self.pads), by_comp, by_net)
        return self._index

    # Queries -----------------------------------------------------------------

    def pads_of(self, designator: str) -> list[Pad]:
        """Pads of one component, in file order."""
        return list(self._build()[1].get(designator, []))

    def net_of(self, designator: str, number: str) -> str | None:
        """Net name of a pad; None if unconnected or the pad does not exist."""
        for p in self._build()[1].get(designator, []):
            if p.number == number:
                return p.net
        return None

    def pin_nets(self, designator: str) -> dict[str, str | None]:
        """Pad number -> net name for one component."""
        return {p.number: p.net for p in self._build()[1].get(designator, [])}

    def pads_on_net(self, net: str) -> list[Pad]:
        return list(self._build()[2].get(net, []))

    def components_on_net(self, net: str) -> list[str]:
        """Designators connected to a net, sorted, without duplicates."""
        return sorted({p.component for p in self._build()[2].get(net, []) if p.component})
