"""Recipe for the ADC, old and new Zephyr port: ``atmel,sam0-adc`` and ``microchip,adc-g1``.

Both bindings include ``adc-controller.yaml``; their child node is ``channel@N`` (research:
``docs/research/peripheral_pairs.md`` sections 5 and 6.5).

Pin roles
---------
A role is an analog input, ``AIN0`` .. ``AIN15``. It is the *name of the ATDF signal* too: the role
``AIN12`` sits on the pad that offers ``AIN12`` of the instance (PB00 on ADC0, PC30 on ADC1). The
number of the input is therefore known as soon as the pad is chosen, and it is what the channel child
needs as ``zephyr,input-positive`` (``channel_child`` / ``channels_for``). ``derived_props`` only
checks the assignment, the node itself has no property that depends on the pins.

Channel children
----------------
``channel@N`` with ``reg = <N>``, ``zephyr,gain``, ``zephyr,reference``, ``zephyr,acquisition-time``
(all required by the base binding) and ``zephyr,input-positive`` (the AIN number; by convention equal to
``reg``, though the binding does not demand it), optionally ``zephyr,resolution``, ``zephyr,vref-mv``
and, for a differential input, ``zephyr,input-negative`` (AIN0..AIN7, MUXNEG). The draft holds them as
``DraftNode.children``; the renderer adds ``#address-cells = <1>; #size-cells = <0>`` to the ADC node.
The numbers are the same on the old and the new port (header
``dt-bindings/adc/mchp_sam_d5x_e5x_adc.h``: ``MCHP_ADC_AIN12`` = 0x0C).

Properties
----------
* ``prescaler``: ``required`` on both bindings. The old SoC dtsi sets 4, the g1 dtsi
  (``pic32cx_sg.dtsi``, ``samd5xe5x.dtsi``) sets none, so a g1 node must carry it. The ADC clock may
  not exceed 16 MHz: ``suggest_prescaler(gclk_hz)`` is the smallest divider of the enum that keeps it
  below; ``default_props`` uses it (default generator 60 MHz, as the sam_e54_xpro and pic32cx_sg41_cult
  boards: 60 / 4 = 15 MHz).
* The clock tree entry ``gclkperiph { adcN { gclkperiph-src = ...; }; }`` of g1 is not written; the
  boards leave the ADC on generator 1 and the defaults suffice for configuration.

Pad limitation: on ADC0 every AIN pad also carries the PTC signals ``X<n>``/``Y<n>`` (and some VREF)
with the same mux letter. ``core.render`` refuses a pad that offers several signals on one instance,
so pins of ADC0 cannot be rendered; ADC1 has the AIN signals only.

Imports only ``dt_explorer.core.*``.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Iterator

from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.draft import DraftChild, DraftNode, register_class
from dt_explorer.core.plugins import register_recipe

register_class("adc-controller.yaml", "adc")

OLD_COMPATIBLE = "atmel,sam0-adc"
G1_COMPATIBLE = "microchip,adc-g1"
ROLE_RE = re.compile(r"^AIN(\d+)$")
N_INPUTS = 16                                       # AIN0..AIN15 have pads
MAX_ENUMERATED = 4                                  # valid_assignments lists subsets up to this size
PRESCALERS = (2, 4, 8, 16, 32, 64, 128, 256)        # enum of microchip,adc-g1
ADC_MAX_HZ = 16_000_000
DEFAULT_GCLK_HZ = 60_000_000
NEGATIVE_INPUTS = tuple(range(8))                   # MUXNEG: AIN0..AIN7 (and GND, 0x18, not offered here)

GAINS = ("ADC_GAIN_1_6", "ADC_GAIN_1_5", "ADC_GAIN_1_4", "ADC_GAIN_2_7", "ADC_GAIN_1_3", "ADC_GAIN_2_5",
         "ADC_GAIN_1_2", "ADC_GAIN_2_3", "ADC_GAIN_4_5", "ADC_GAIN_1", "ADC_GAIN_2", "ADC_GAIN_3",
         "ADC_GAIN_4", "ADC_GAIN_6", "ADC_GAIN_8", "ADC_GAIN_12", "ADC_GAIN_16", "ADC_GAIN_24")
REFERENCES = ("ADC_REF_VDD_1", "ADC_REF_VDD_1_2", "ADC_REF_VDD_1_3", "ADC_REF_VDD_1_4", "ADC_REF_INTERNAL",
              "ADC_REF_EXTERNAL0", "ADC_REF_EXTERNAL1")
ACQ_TIME_DEFAULT = 0                                # ADC_ACQ_TIME_DEFAULT in dt-bindings/adc/adc.h

# Kconfig: the symbols have a prompt, `CONFIG_ADC` is the subsystem menuconfig.
# Sources: drivers/adc/Kconfig.sam0 (v4.3.0), drivers/adc/Kconfig.mchp (main).
KCONFIG: dict[str, dict[str, str]] = {
    OLD_COMPATIBLE: {"CONFIG_ADC": "y", "CONFIG_ADC_SAM0": "y"},
    G1_COMPATIBLE: {"CONFIG_ADC": "y", "CONFIG_ADC_MCHP_G1": "y"},
}


class RecipeError(ValueError):
    """An invalid role/pad combination or property set."""


def role_index(role) -> int:
    m = ROLE_RE.match(role) if isinstance(role, str) else None
    if not m:
        raise RecipeError(f"not an analog input role: {role!r} (expected AIN0, AIN1, ...)")
    return int(m.group(1))


def suggest_prescaler(gclk_hz: float = DEFAULT_GCLK_HZ) -> int:
    """Smallest prescaler of the enum for which ``gclk_hz / prescaler`` does not exceed 16 MHz."""
    if gclk_hz <= 0:
        raise RecipeError("generator rate must be positive")
    for p in PRESCALERS:
        if gclk_hz / p <= ADC_MAX_HZ:
            return p
    raise RecipeError(f"{gclk_hz} Hz is too fast for the ADC even with prescaler {PRESCALERS[-1]}")


def channel_child(ain, *, reg: int | None = None, gain: str = "ADC_GAIN_1", reference: str = "ADC_REF_VDD_1",
                  acquisition_time: int = ACQ_TIME_DEFAULT, resolution: int | None = 12,
                  vref_mv: int | None = None, negative=None, label: str | None = None) -> DraftChild:
    """The ``channel@N`` child for an analog input.

    `ain`: the role (``"AIN12"``) or the number. ``zephyr,input-positive`` is that number, `reg` defaults
    to it. `negative`: role or number (AIN0..AIN7) for a differential input.
    """
    n = ain if isinstance(ain, int) and not isinstance(ain, bool) else role_index(ain)
    if not 0 <= n < N_INPUTS:
        raise RecipeError(f"AIN{n}: the pads offer AIN0..AIN{N_INPUTS - 1}")
    reg = n if reg is None else reg
    if gain not in GAINS:
        raise RecipeError(f"unknown gain {gain!r}")
    if reference not in REFERENCES:
        raise RecipeError(f"unknown reference {reference!r}")
    props: dict = {"reg": [reg], "zephyr,gain": gain, "zephyr,reference": reference,
                   "zephyr,acquisition-time": acquisition_time, "zephyr,input-positive": n}
    if negative is not None:
        neg = negative if isinstance(negative, int) and not isinstance(negative, bool) else role_index(negative)
        if neg not in NEGATIVE_INPUTS:
            raise RecipeError(f"the negative input must be AIN0..AIN7, not AIN{neg}")
        props["zephyr,differential"] = True
        props["zephyr,input-negative"] = neg
    if resolution is not None:
        props["zephyr,resolution"] = resolution
    if vref_mv is not None:
        props["zephyr,vref-mv"] = vref_mv
    return DraftChild(name="channel", unit=f"{reg:x}", labels=[label] if label else [], props=props)


def channels_for(pins: dict[str, str], **kwargs) -> list[DraftChild]:
    """One ``channel@N`` child per analog input role of a draft node's ``pins`` (role -> pad), in input order."""
    roles = sorted((r for r in pins if ROLE_RE.match(r)), key=role_index)
    return [channel_child(r, **kwargs) for r in roles]


def channel_problems(node: DraftNode) -> list[str]:
    """Texts for channel children whose input is not one of the node's pads, or whose reg is used twice."""
    out: list[str] = []
    have = {role_index(r) for r in node.pins if ROLE_RE.match(r)}
    seen: dict[tuple, str] = {}
    for c in node.children:
        if c.name != "channel":
            continue
        pos = c.props.get("zephyr,input-positive")
        reg = tuple(c.props.get("reg") or ())
        if pos is not None and pos not in have:
            out.append(f"{c.node_name}: zephyr,input-positive AIN{pos} is not one of the pads of the node")
        if reg in seen:
            out.append(f"{c.node_name}: reg {list(reg)} is also used by {seen[reg]}")
        seen[reg] = c.node_name
    return out


class AdcRecipe:
    api_version = 1
    name = "adc"
    compatibles = [OLD_COMPATIBLE, G1_COMPATIBLE]

    # ---- roles -------------------------------------------------------------------------
    def roles_for(self, compatible: str | None = None) -> list[str]:
        if compatible is not None and compatible not in self.compatibles:
            raise RecipeError(f"unknown compatible {compatible!r}")
        return [f"AIN{i}" for i in range(N_INPUTS)]

    def pin_roles(self, instance: Instance, compatible: str | None = None) -> list[str]:
        """Analog inputs an ADC instance has pads for. Other instances: none."""
        if getattr(instance, "kind", None) != "ADC":
            return []
        roles = self.roles_for(compatible)
        have = {s.role for s in instance.signals}
        return [r for r in roles if r in have]

    # ---- roles -> properties -----------------------------------------------------------
    def derived_props(self, assignment: dict) -> dict:
        """assignment: role -> signal role of the chosen pad. Checks it; the node has nothing derived from pads."""
        if not any(v is not None for v in assignment.values()):
            raise RecipeError("no analog input assigned")
        for role, padrole in assignment.items():
            if padrole is None:
                continue
            if not 0 <= role_index(role) < N_INPUTS:
                raise RecipeError(f"{role}: the pads offer AIN0..AIN{N_INPUTS - 1}")
            if padrole != role:
                raise RecipeError(f"{role} must sit on a pad with the signal {role}, not {padrole}")
        return {}

    # ---- properties + pins -> roles ----------------------------------------------------
    def lift(self, props: dict, pins) -> dict[str, str]:
        """Roles of an existing node: ``AINn`` -> pad, for every pad whose signal role is an analog input."""
        if isinstance(pins, dict):
            items = list(pins.items())
        else:
            items = [((p.role, p.pad) if isinstance(p, Signal) else tuple(p)) for p in (pins or [])]
        out = {str(r): pad for r, pad in items if ROLE_RE.match(str(r))}
        return dict(sorted(out.items(), key=lambda kv: role_index(kv[0])))

    # ---- enumeration of valid combinations ---------------------------------------------
    def valid_assignments(self, cls: str) -> Iterator[dict[str, str]]:
        """Every non-empty set of up to MAX_ENUMERATED inputs (role -> same signal role).

        All 65535 subsets of 16 inputs would make the pad lists of the configurator slow; the inputs
        are independent, and ``derived_props`` accepts any number.
        """
        if cls != "adc":
            raise RecipeError(f"unknown class {cls!r}")
        roles = self.roles_for()
        for k in range(1, MAX_ENUMERATED + 1):
            for subset in combinations(roles, k):
                yield {r: r for r in subset}

    # ---- chosen properties -------------------------------------------------------------
    def default_props(self, compatible: str, gclk_hz: float = DEFAULT_GCLK_HZ) -> dict:
        """Properties a new ADC node needs besides pins and channels: g1 needs ``prescaler`` (the SoC lacks it)."""
        if compatible not in self.compatibles:
            raise RecipeError(f"unknown compatible {compatible!r}")
        return {"prescaler": suggest_prescaler(gclk_hz)} if compatible == G1_COMPATIBLE else {}

    # ---- Kconfig -----------------------------------------------------------------------
    def kconfig(self, compatible: str | None = None) -> dict[str, str]:
        if compatible is not None:
            if compatible not in KCONFIG:
                raise RecipeError(f"unknown compatible {compatible!r}")
            return dict(KCONFIG[compatible])
        out: dict[str, str] = {}
        for table in KCONFIG.values():
            out.update(table)
        return out


register_recipe(AdcRecipe)
