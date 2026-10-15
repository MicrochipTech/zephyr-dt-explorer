"""Recipe for the GMAC Ethernet MAC and its MDIO bus, old and new Zephyr port.

Old port (``atmel`` family): ``atmel,sam0-gmac`` (MAC) and ``atmel,sam-mdio`` (MDIO bus).
New port (``microchip`` family): ``microchip,gmac-g1-eth`` and ``microchip,gmac-g1-mdio``.
The PHY is not a driver of its own: it is a child of the MDIO node, ``ethernet-phy@<addr>`` with
``compatible = "ethernet-phy"`` and ``reg = <addr>`` (driver ``PHY_GENERIC_MII`` on both ports), and
the MAC points at it with ``phy-handle``.

A draft for this recipe has two nodes (both are overrides of a label, so the different tree shape of the
two ports does not matter): the MAC (class ``ethernet``, roles = RMII signals, ``phy-handle``) and the MDIO
bus (class ``mdio``, roles ``MDC``/``MDIO``) with the PHY as a ``DraftChild``. ``make_nodes()`` builds both.

Pins (ATDF, section "GMAC" of ``docs/research/peripheral_pairs.md``):

* the eight RMII signals ``GTXCK GTXEN GTX0 GTX1 GRXDV GRX0 GRX1 GRXER`` each have exactly one
  candidate pad, so they are set without asking (``auto_pins``);
* the MII extras ``GTX2 GTX3 GRX2 GRX3 GRXCK GTXER GCOL GCRS`` also have one pad each; they are only
  used when ``phy-connection-type = "mii"`` is wanted (``derived_props`` derives that property);
* MDC/MDIO come as pairs of one IOSET (four pairs). That is the only real choice: all pairs are
  offered, and the default is the pair that no other enabled node uses, preferring the pair the node
  has now (``current``), else the lowest IOSET.

The role names of the MAC are the ATDF signal roles; the roles of the MDIO node are ``MDC``/``MDIO``
(signals ``GMDC``/``GMDIO``). Pins are opaque pad ids.

Imports only ``dt_explorer.core.*``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.draft import DraftChild, DraftNode, register_class
from dt_explorer.core.plugins import register_recipe
from dt_explorer.core.translate import Ref

# base bindings that make the lift (draft.detect_class) call the nodes "ethernet" and "mdio"
register_class("ethernet-controller.yaml", "ethernet")
register_class("mdio-controller.yaml", "mdio")


class RecipeError(ValueError):
    """An invalid role/pad combination."""


ETH_COMPATIBLES = ["atmel,sam0-gmac", "microchip,gmac-g1-eth"]
MDIO_COMPATIBLES = ["atmel,sam-mdio", "microchip,gmac-g1-mdio"]
MDIO_OF = {"atmel,sam0-gmac": "atmel,sam-mdio", "microchip,gmac-g1-eth": "microchip,gmac-g1-mdio"}
PHY_COMPATIBLE = "ethernet-phy"

RMII_ROLES = ["GTXCK", "GTXEN", "GTX0", "GTX1", "GRXDV", "GRX0", "GRX1", "GRXER"]
# MII needs the two extra data bits per direction and the RX clock; GTXER/GCOL/GCRS are optional
MII_REQUIRED = ["GTX2", "GTX3", "GRX2", "GRX3", "GRXCK"]
MII_OPTIONAL = ["GTXER", "GCOL", "GCRS"]
MII_ROLES = MII_REQUIRED + MII_OPTIONAL
ETH_ROLES = RMII_ROLES + MII_ROLES
MDIO_SIGNAL = {"MDC": "GMDC", "MDIO": "GMDIO"}      # draft role -> ATDF signal role
MDIO_ROLES = list(MDIO_SIGNAL)

# Kconfig. Only symbols with a prompt are listed: assigning a promptless symbol aborts the Zephyr
# configure step (scripts/kconfig/kconfig.py, check_no_promptless_assign). Verified on the workspaces:
#   NETWORKING          menuconfig, prompt "Link layer and networking (including IP)"
#   NET_L2_ETHERNET     menuconfig, prompt "Ethernet support"
#   ETH_DRIVER          menuconfig, prompt "Ethernet drivers" (the Microchip boards `configdefault` it)
#   ETH_MCHP_GMAC_G1    menuconfig, prompt, depends on NET_BUF_FIXED_DATA_SIZE (choice default)
#   MDIO_MCHP_GMAC_G1   prompt, depends on ETH_MCHP_GMAC_G1
#   ETH_SAM_GMAC        menuconfig, prompt (drivers/ethernet/Kconfig.sam_gmac)
#   MDIO_ATMEL_SAM      prompt (drivers/mdio/Kconfig.sam)
# Not listed on purpose: PHY_GENERIC_MII (default y via DT_HAS_ETHERNET_PHY_ENABLED), NVMEM (implied),
# NET_BUF_FIXED_DATA_SIZE (choice default), and everything above the link layer (NET_IPV4, NET_TCP,
# NET_SOCKETS, TLS ...): those stay in menuconfig.
_NET = {"CONFIG_NETWORKING": "y", "CONFIG_NET_L2_ETHERNET": "y", "CONFIG_ETH_DRIVER": "y"}
KCONFIG: dict[str, dict[str, str]] = {
    "atmel,sam0-gmac": {**_NET, "CONFIG_ETH_SAM_GMAC": "y"},
    "microchip,gmac-g1-eth": {**_NET, "CONFIG_ETH_MCHP_GMAC_G1": "y"},
    "atmel,sam-mdio": {"CONFIG_MDIO_ATMEL_SAM": "y"},
    "microchip,gmac-g1-mdio": {"CONFIG_MDIO_MCHP_GMAC_G1": "y"},
}


@dataclass
class PinChoice:
    role: str
    pad: str | None
    how: str                                  # "automatic" | "chosen" | "unresolved"
    alternatives: list[str] = field(default_factory=list)    # every pad the role could use
    note: str = ""


@dataclass
class AutoPins:
    choices: list[PinChoice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def pins(self) -> dict[str, str]:
        return {c.role: c.pad for c in self.choices if c.pad}

    def table(self) -> list[str]:
        out = [f"{'role':7s} {'pad':6s} {'how':10s} alternatives"]
        for c in self.choices:
            alts = ", ".join(c.alternatives) if len(c.alternatives) > 1 else "-"
            out.append(f"{c.role:7s} {str(c.pad):6s} {c.how:10s} {alts}" + (f"   [{c.note}]" if c.note else ""))
        return out


def _normalize_pins(pins) -> dict[str, str]:
    """{"GTXCK": "PA14"} or an iterable of Signal / (signal role, pad) -> dict."""
    if isinstance(pins, dict):
        return dict(pins)
    out: dict[str, str] = {}
    for p in pins or []:
        role, pad = (p.role, p.pad) if isinstance(p, Signal) else tuple(p)
        out[role] = pad
    return out


def _candidates(instance: Instance, signal_role: str) -> list[Signal]:
    return [s for s in instance.signals if s.role == signal_role]


class MicrochipGmacRecipe:
    api_version = 1
    name = "microchip_gmac"
    compatibles = ETH_COMPATIBLES + MDIO_COMPATIBLES

    # ---- roles -------------------------------------------------------------------
    def pin_roles(self, instance: Instance, compatible: str | None = None) -> list[str]:
        """Roles a node on `instance` can use; without `compatible` the MAC roles and MDC/MDIO."""
        if getattr(instance, "kind", "GMAC") != "GMAC":
            return []
        have = {s.role for s in instance.signals}
        if compatible in MDIO_COMPATIBLES:
            return [r for r in MDIO_ROLES if MDIO_SIGNAL[r] in have]
        if compatible is not None and compatible not in ETH_COMPATIBLES:
            raise RecipeError(f"unknown compatible {compatible!r}")
        eth = [r for r in ETH_ROLES if r in have]
        return eth if compatible is not None else eth + [r for r in MDIO_ROLES if MDIO_SIGNAL[r] in have]

    def required_roles(self, compatible: str) -> list[str]:
        if compatible in MDIO_COMPATIBLES:
            return list(MDIO_ROLES)
        if compatible in ETH_COMPATIBLES:
            return list(RMII_ROLES)
        raise RecipeError(f"unknown compatible {compatible!r}")

    # ---- roles -> properties -------------------------------------------------------
    def derived_props(self, assignment: dict) -> dict:
        """assignment: role -> ATDF signal role. ``phy-connection-type = "mii"`` when MII roles are used."""
        assignment = {r: s for r, s in assignment.items() if s is not None}
        roles = set(assignment)
        if roles and roles <= set(MDIO_ROLES):
            for r, s in assignment.items():
                if s != MDIO_SIGNAL[r]:
                    raise RecipeError(f"mdio: {r} is signal {MDIO_SIGNAL[r]}, not {s}")
            missing = [r for r in MDIO_ROLES if r not in assignment]
            if missing:
                raise RecipeError(f"mdio: role(s) {missing} not assigned")
            return {}
        unknown = sorted(roles - set(ETH_ROLES))
        if unknown or not roles:
            raise RecipeError(f"no GMAC role in assignment: {sorted(roles)}" if not unknown
                              else f"not a GMAC role: {unknown}")
        for r, s in assignment.items():
            if s != r:
                raise RecipeError(f"gmac: role {r} is signal {r}, not {s}")
        missing = [r for r in RMII_ROLES if r not in roles]
        if missing:
            raise RecipeError(f"gmac: RMII role(s) {missing} not assigned")
        if roles & set(MII_ROLES):
            lacking = [r for r in MII_REQUIRED if r not in roles]
            if lacking:
                raise RecipeError(f"gmac: MII needs {MII_REQUIRED}; missing {lacking}")
            return {"phy-connection-type": "mii"}
        return {}

    # ---- properties + pins -> roles ------------------------------------------------
    def lift(self, props: dict, pins) -> dict[str, str]:
        """Roles of an existing node: role -> pad id. `pins`: {signal role: pad} (or Signals / pairs)."""
        by_sig = _normalize_pins(pins)
        comp = props.get("compatible")
        if isinstance(comp, (list, tuple)):
            hit = next((c for c in comp if c in self.compatibles), None)
            if hit is None and comp:
                raise RecipeError(f"none of the compatibles {list(comp)} belongs to the GMAC recipe")
            comp = hit
        if comp in MDIO_COMPATIBLES:
            return {r: by_sig[s] for r, s in MDIO_SIGNAL.items() if s in by_sig}
        if comp is not None and comp not in ETH_COMPATIBLES:
            raise RecipeError(f"unknown compatible {comp!r}")
        if comp is None and set(by_sig) <= set(MDIO_SIGNAL.values()) and by_sig:
            return {r: by_sig[s] for r, s in MDIO_SIGNAL.items() if s in by_sig}
        return {r: by_sig[r] for r in ETH_ROLES if r in by_sig}

    # ---- automatic pin choice --------------------------------------------------------
    def auto_pins(self, instance: Instance, compatible: str, used: Iterable[str] = (),
                  current: dict[str, str] | None = None, mii: bool = False) -> AutoPins:
        """Pads for every role of the node without hand-picking.

        `used`: pads that other enabled nodes use (not this node's own). `current`: the pads the node has
        now (role -> pad), preferred when they form a valid free choice. A role with one candidate pad is
        set automatically; MDC/MDIO pairs (one IOSET) are all listed as alternatives and the default is
        the current pair if it is free, else the free pair of the lowest IOSET, else (noted) the lowest.
        """
        if getattr(instance, "kind", "GMAC") != "GMAC":
            raise RecipeError(f"{instance.name} is not a GMAC instance")
        used = set(used)
        current = dict(current or {})
        out = AutoPins()
        if compatible in MDIO_COMPATIBLES:
            self._auto_mdio(instance, used, current, out)
        elif compatible in ETH_COMPATIBLES:
            for role in RMII_ROLES + (MII_ROLES if mii else []):
                cands = sorted({s.pad for s in _candidates(instance, role)})
                if not cands:
                    out.choices.append(PinChoice(role, None, "unresolved", [], f"{instance.name} has no signal {role}"))
                    continue
                pad = current.get(role) if current.get(role) in cands else cands[0]
                how = "automatic" if len(cands) == 1 else "chosen"
                note = f"pad also used by another node" if pad in used else ""
                if note:
                    out.notes.append(f"{role}: pad {pad} is used by another enabled node")
                out.choices.append(PinChoice(role, pad, how, cands, note))
        else:
            raise RecipeError(f"unknown compatible {compatible!r}")
        return out

    def mdio_pairs(self, instance: Instance) -> list[dict]:
        """Every (MDC, MDIO) pad pair of one IOSET: ``[{"ioset": "2", "MDC": "PC11", "MDIO": "PC12"}, ...]``."""
        by_ioset: dict[str, dict] = {}
        for role, sig in MDIO_SIGNAL.items():
            for s in _candidates(instance, sig):
                by_ioset.setdefault(str(s.group), {"ioset": str(s.group)})[role] = s.pad
        pairs = [p for p in by_ioset.values() if "MDC" in p and "MDIO" in p]
        return sorted(pairs, key=lambda p: (len(p["ioset"]), p["ioset"]))

    def _auto_mdio(self, instance: Instance, used: set, current: dict, out: AutoPins) -> None:
        pairs = self.mdio_pairs(instance)
        if not pairs:
            for r in MDIO_ROLES:
                out.choices.append(PinChoice(r, None, "unresolved", [], f"{instance.name} offers no MDC/MDIO pair"))
            return
        free = [p for p in pairs if p["MDC"] not in used and p["MDIO"] not in used]
        cur = next((p for p in pairs if p["MDC"] == current.get("MDC") and p["MDIO"] == current.get("MDIO")), None)
        if cur is not None and cur in free:
            pick, why = cur, f"IOSET {cur['ioset']}: the pair the node has now, no other node uses it"
        elif free:
            pick, why = free[0], f"IOSET {free[0]['ioset']}: lowest IOSET no other node uses"
        else:
            pick, why = pairs[0], f"IOSET {pairs[0]['ioset']}: every pair is used by another node"
            out.notes.append("mdio: every MDC/MDIO pair is in use by other enabled nodes")
        for p in pairs:
            if p not in free:
                busy = sorted({x for x in (p["MDC"], p["MDIO"]) if x in used})
                out.notes.append(f"mdio: IOSET {p['ioset']} ({p['MDC']}/{p['MDIO']}) not offered as default: "
                                 f"{', '.join(busy)} used by another node")
        for r in MDIO_ROLES:
            out.choices.append(PinChoice(r, pick[r], "chosen", [p[r] for p in pairs], why))

    # ---- draft nodes -------------------------------------------------------------------
    def make_nodes(self, eth_compatible: str, instance: str, eth_pins: dict[str, str],
                   mdio_pins: dict[str, str], phy_address: int = 0, mac_label: str = "gmac",
                   mdio_label: str = "mdio", phy_label: str = "phy") -> list[DraftNode]:
        """The MAC with ``phy-handle`` and the MDIO bus with the PHY child, as draft nodes."""
        if eth_compatible not in MDIO_OF:
            raise RecipeError(f"not a GMAC MAC compatible: {eth_compatible!r}")
        phy = DraftChild(name="ethernet-phy", unit=f"{phy_address:x}", compatible=[PHY_COMPATIBLE],
                         labels=[phy_label], props={"reg": [phy_address], "status": "okay"})
        mac = DraftNode(cls="ethernet", roles=[r for r in ETH_ROLES if r in eth_pins], instance=instance,
                        pins={r: eth_pins[r] for r in ETH_ROLES if r in eth_pins},
                        props={"phy-handle": [Ref(phy_label)]}, compatible=eth_compatible, label=mac_label)
        mdio = DraftNode(cls="mdio", roles=[r for r in MDIO_ROLES if r in mdio_pins], instance=instance,
                         pins={r: mdio_pins[r] for r in MDIO_ROLES if r in mdio_pins},
                         compatible=MDIO_OF[eth_compatible], label=mdio_label, children=[phy])
        return [mac, mdio]

    # ---- Kconfig ---------------------------------------------------------------------
    def kconfig(self, compatible: str | None = None) -> dict[str, str]:
        """Kconfig symbols for a compatible; without argument the union of all four tables."""
        if compatible is not None:
            if compatible not in KCONFIG:
                raise RecipeError(f"unknown compatible {compatible!r}")
            return dict(KCONFIG[compatible])
        out: dict[str, str] = {}
        for table in KCONFIG.values():
            out.update(table)
        return out


register_recipe(MicrochipGmacRecipe)
