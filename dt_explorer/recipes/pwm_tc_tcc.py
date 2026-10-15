"""Recipes for the PWM modes of the TCC and TC timers, old and new Zephyr port.

Compatibles (research: ``docs/research/peripheral_pairs.md`` sections 6.2, 6.3):

====================  =========================  =========================
module                old port (``atmel``)       new port (g1)
====================  =========================  =========================
TCC                   ``atmel,sam0-tcc-pwm``     ``microchip,tcc-g1-pwm``
TC                    ``atmel,sam0-tc-pwm``      ``microchip,tc-g1-pwm``
====================  =========================  =========================

One module, two recipe objects: the TCC recipe offers only instances of kind ``TCC`` and the TC recipe
only instances of kind ``TC``, so a configurator that picks the first recipe with roles for an instance
never offers the TC compatible for a TCC (and the other way round).

Pin roles
---------
A role is a waveform output, ``WO0`` .. ``WO7`` (TCC) or ``WO0`` .. ``WO1`` (TC). It is the *name of the
ATDF signal* too: the role ``WO3`` can only sit on a pad that offers the signal ``WO3`` of the instance
(PB15 mux G on TCC0, ...). The pad decides the mux letter and the IOSET, nothing else; the recipe has no
derived property that depends on the pad. ``derived_props`` therefore only checks the assignment.
The PWM channel of a consumer is the output number modulo the channel count of the instance
(``pwm_channel``): ``WO6``/``WO7`` of TCC0 (six channels) are the alternative outputs of channels 0 and 1.

Properties
----------
* ``#pwm-cells``: 2 (channel, period) on the old binding, 3 (channel, period, flags) on g1. The bindings
  declare it ``const``; the renderer adds it when the binding index is known, ``default_props`` writes it
  explicitly. Every ``pwms = <&tcc0 CH PERIOD>`` of a consumer needs the third cell on g1 (the consumers
  are not part of this node).
* ``prescaler``: required on the old bindings, optional on g1 (driver default 1). Not derived from the
  pins, so it is a chosen property; ``suggest_prescaler`` computes the smallest divider that lets the
  counter hold the wanted period, ``default_props`` uses 8 (what both boards use).
* old TC only: the SoC node has neither ``channels`` nor ``counter-size``; ``default_props`` supplies
  2 and 16. g1 TCC0/TCC1: ``max-bit-width`` 24 (the SoC dtsi of ``pic32cx_sg`` leaves it to the board).

Imports only ``dt_explorer.core.*``.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Iterator

from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.draft import register_class
from dt_explorer.core.plugins import register_recipe

register_class("pwm-controller.yaml", "pwm")

PRESCALERS = (1, 2, 4, 8, 16, 64, 256, 1024)        # enum of all four bindings
DEFAULT_PRESCALER = 8                                # TCC0 of same54_xpro and pic32cx_sg41_cult
ROLE_RE = re.compile(r"^WO(\d+)$")


class RecipeError(ValueError):
    """An invalid role/pad combination or property set."""


# kind -> (old compatible, g1 compatible, number of outputs of the largest instance)
MODULES: dict[str, tuple[str, str, int]] = {
    "TCC": ("atmel,sam0-tcc-pwm", "microchip,tcc-g1-pwm", 8),
    "TC": ("atmel,sam0-tc-pwm", "microchip,tc-g1-pwm", 2),
}
G1_COMPATIBLES = {"microchip,tcc-g1-pwm", "microchip,tc-g1-pwm"}

# Kconfig: the driver symbols have a prompt (`bool "..."`, `default y`), so assigning them is allowed;
# CONFIG_PWM is the subsystem menuconfig. Sources: drivers/pwm/Kconfig.sam0, Kconfig.sam0_tc (v4.3.0),
# Kconfig.mchp (main).
KCONFIG: dict[str, dict[str, str]] = {
    "atmel,sam0-tcc-pwm": {"CONFIG_PWM": "y", "CONFIG_PWM_SAM0_TCC": "y"},
    "atmel,sam0-tc-pwm": {"CONFIG_PWM": "y", "CONFIG_PWM_SAM0_TC": "y"},
    "microchip,tcc-g1-pwm": {"CONFIG_PWM": "y", "CONFIG_PWM_MCHP_G1_TCC": "y"},
    "microchip,tc-g1-pwm": {"CONFIG_PWM": "y", "CONFIG_PWM_MCHP_TC_G1": "y"},
}


def _kind_of_compatible(compatible: str) -> str:
    for kind, (old, new, _n) in MODULES.items():
        if compatible in (old, new):
            return kind
    raise RecipeError(f"unknown compatible {compatible!r}")


def role_index(role) -> int:
    m = ROLE_RE.match(role) if isinstance(role, str) else None
    if not m:
        raise RecipeError(f"not a waveform output role: {role!r} (expected WO0, WO1, ...)")
    return int(m.group(1))


def pwm_channel(role: str, channels: int) -> int:
    """Channel number a consumer uses for the output `role`: the output number modulo the channel count."""
    if channels < 1:
        raise RecipeError(f"channel count must be positive, got {channels}")
    return role_index(role) % channels


def pwm_cells(compatible: str) -> int:
    """``#pwm-cells`` of the binding: 2 on the old port, 3 on g1."""
    _kind_of_compatible(compatible)
    return 3 if compatible in G1_COMPATIBLES else 2


def suggest_prescaler(gclk_hz: float, period_ns: float, counter_bits: int) -> int:
    """Smallest prescaler of the binding enum for which the counter holds one PWM period.

    ``gclk_hz / prescaler * period_ns * 1e-9 <= 2**counter_bits``. The smallest divider gives the finest
    duty-cycle resolution. Raises RecipeError when even 1024 is too small.
    """
    if gclk_hz <= 0 or period_ns <= 0:
        raise RecipeError("generator rate and period must be positive")
    ticks = gclk_hz * period_ns * 1e-9
    for p in PRESCALERS:
        if ticks / p <= (1 << counter_bits):
            return p
    raise RecipeError(f"a period of {period_ns} ns does not fit a {counter_bits}-bit counter at "
                      f"{gclk_hz} Hz even with prescaler {PRESCALERS[-1]}")


class _PwmRecipe:
    api_version = 1
    kind = ""                       # "TCC" | "TC", set by the subclasses
    name = ""
    compatibles: list[str] = []

    # ---- roles ----------------------------------------------------------------------------
    def roles_for(self, compatible: str | None = None) -> list[str]:
        if compatible is not None and _kind_of_compatible(compatible) != self.kind:
            raise RecipeError(f"{compatible!r} is not a {self.kind} compatible")
        return [f"WO{i}" for i in range(MODULES[self.kind][2])]

    def pin_roles(self, instance: Instance, compatible: str | None = None) -> list[str]:
        """Roles of a TCC (or TC) instance: the waveform outputs it has pads for. Other instances: none."""
        if getattr(instance, "kind", None) != self.kind:
            return []
        if compatible is not None:
            self.roles_for(compatible)                  # raises for a foreign compatible
        have = {s.role for s in instance.signals}
        return [r for r in self.roles_for() if r in have]

    # ---- roles -> properties --------------------------------------------------------------
    def derived_props(self, assignment: dict) -> dict:
        """assignment: role -> signal role of the chosen pad. Checks it; nothing is derived from a pad."""
        n_max = MODULES[self.kind][2]
        if not any(v is not None for v in assignment.values()):
            raise RecipeError("no waveform output assigned")
        for role, padrole in assignment.items():
            if padrole is None:
                continue
            if role_index(role) >= n_max:
                raise RecipeError(f"{role}: a {self.kind} has the outputs WO0..WO{n_max - 1}")
            if padrole != role:
                raise RecipeError(f"{role} must sit on a pad with the signal {role}, not {padrole}")
        return {}

    # ---- properties + pins -> roles -------------------------------------------------------
    def lift(self, props: dict, pins) -> dict[str, str]:
        """Roles of an existing node: ``WOn`` -> pad, for every pad whose signal role is a waveform output."""
        if isinstance(pins, dict):
            items = list(pins.items())
        else:
            items = [((p.role, p.pad) if isinstance(p, Signal) else tuple(p)) for p in (pins or [])]
        n_max = MODULES[self.kind][2]
        out: dict[str, str] = {}
        for padrole, pad in items:
            m = ROLE_RE.match(str(padrole))
            if not m:
                continue
            if int(m.group(1)) >= n_max:
                raise RecipeError(f"{padrole}: a {self.kind} has the outputs WO0..WO{n_max - 1}")
            out[str(padrole)] = pad
        return dict(sorted(out.items(), key=lambda kv: role_index(kv[0])))

    # ---- enumeration of valid combinations -------------------------------------------------
    def valid_assignments(self, cls: str) -> Iterator[dict[str, str]]:
        """Every non-empty subset of the outputs (role -> same signal role). Outputs are independent."""
        if cls != "pwm":
            raise RecipeError(f"unknown class {cls!r}")
        roles = self.roles_for()
        for k in range(1, len(roles) + 1):
            for subset in combinations(roles, k):
                yield {r: r for r in subset}

    # ---- chosen properties -----------------------------------------------------------------
    def default_props(self, compatible: str, instance_name: str | None = None,
                      prescaler: int = DEFAULT_PRESCALER) -> dict:
        """Properties a new PWM node of `compatible` needs besides the pins (merge into ``vendor_props``).

        ``#pwm-cells`` (2 / 3), ``prescaler`` (chosen; see ``suggest_prescaler``), and the counter
        properties the SoC node lacks: old TC ``channels``/``counter-size``, g1 TCC0/TCC1 ``max-bit-width``.
        """
        if _kind_of_compatible(compatible) != self.kind:
            raise RecipeError(f"{compatible!r} is not a {self.kind} compatible")
        if prescaler not in PRESCALERS:
            raise RecipeError(f"prescaler {prescaler} is not one of {list(PRESCALERS)}")
        props: dict = {"#pwm-cells": pwm_cells(compatible), "prescaler": prescaler}
        g1 = compatible in G1_COMPATIBLES
        if self.kind == "TC" and not g1:
            props.update({"channels": 2, "counter-size": 16})
        if self.kind == "TCC" and g1 and (instance_name or "").upper() in ("TCC0", "TCC1"):
            props["max-bit-width"] = 24
        return props

    # ---- Kconfig ---------------------------------------------------------------------------
    def kconfig(self, compatible: str | None = None) -> dict[str, str]:
        if compatible is not None:
            if _kind_of_compatible(compatible) != self.kind:
                raise RecipeError(f"{compatible!r} is not a {self.kind} compatible")
            return dict(KCONFIG[compatible])
        out: dict[str, str] = {}
        for c in self.compatibles:
            out.update(KCONFIG[c])
        return out


class TccPwmRecipe(_PwmRecipe):
    kind = "TCC"
    name = "pwm_tcc"
    compatibles = [MODULES["TCC"][0], MODULES["TCC"][1]]


class TcPwmRecipe(_PwmRecipe):
    kind = "TC"
    name = "pwm_tc"
    compatibles = [MODULES["TC"][0], MODULES["TC"][1]]


register_recipe(TccPwmRecipe)
register_recipe(TcPwmRecipe)
