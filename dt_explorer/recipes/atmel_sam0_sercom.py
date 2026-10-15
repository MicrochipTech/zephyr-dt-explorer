"""Recipe for the SERCOM drivers of the old Zephyr port: ``atmel,sam0-uart/-i2c/-spi``.

Functional roles <-> SERCOM pads, the derived properties ``rxpo``/``txpo`` (UART) and
``dipo``/``dopo`` (SPI), the reverse direction ``lift`` and a Kconfig table. Pad roles are
``PAD0``..``PAD3`` (the role names of the ATDF provider); pins are opaque pad ids ("PB25").

Valid combinations (SAM D5x/E5x). Sources:
* UART TXPO: ATDF ``SERCOM_USART_CTRLA__TXPO`` of ATSAME54P20A.atdf (value groups 0, 2, 3 only;
  1 is reserved) and the "SAMD5/E5" table in ``dts/bindings/serial/atmel,sam0-uart.yaml``.
      txpo 0: TX PAD0 (PAD1 = XCK, unused in async mode)
      txpo 2: TX PAD0, RTS PAD2, CTS PAD3
      txpo 3: TX PAD0, RTS PAD2 (also called TE; no CTS)
* UART RXPO: ATDF ``SERCOM_USART_CTRLA__RXPO`` and the binding: RX on PAD<rxpo>, 0..3.
  RX must not share a pad with TX, RTS or CTS.
* SPI host DOPO: ATDF ``SERCOM_SPIM_CTRLA__DOPO`` (the E5x offers 0 and 2 only; the binding
  ``atmel,sam0-spi.yaml`` gives no table, only "Data Out Pinout"):
      dopo 0: MOSI PAD0, SCK PAD1, SS PAD2
      dopo 2: MOSI PAD3, SCK PAD1, SS PAD2
  DIPO: ATDF ``SERCOM_SPIM_CTRLA__DIPO``: MISO on PAD<dipo>, 0..3, not a pad used by MOSI/SCK
  (or SS when the hardware chip select role CS is used).
* I2C: fixed, SDA = PAD0, SCL = PAD1; no derived properties.

The SERCOM pad registers are identical on the new port (``microchip,sercom-g1-uart/-i2c/-spi``,
bindings in current Zephyr: ``rxpo``/``txpo``/``dipo``/``dopo`` with the same meaning), so the same
roles, derived properties and ``lift`` serve both; only the Kconfig table differs (``KCONFIG``).

Imports only ``dt_explorer.core.*``.
"""
from __future__ import annotations

from typing import Iterable, Iterator

from dt_explorer.core.device import Instance, Signal
from dt_explorer.core.plugins import register_recipe

PADS = ("PAD0", "PAD1", "PAD2", "PAD3")


class RecipeError(ValueError):
    """An invalid role/pad combination or property set."""


# class -> (compatible, ordered roles)
CLASSES: dict[str, tuple[str, list[str]]] = {
    "uart": ("atmel,sam0-uart", ["TX", "RX", "RTS", "CTS"]),
    "i2c": ("atmel,sam0-i2c", ["SDA", "SCL"]),
    "spi": ("atmel,sam0-spi", ["MOSI", "MISO", "SCK", "CS"]),
}
# new port (g1): compatible -> class; pad registers and role sets are those of the same class above
G1_CLASSES: dict[str, str] = {
    "microchip,sercom-g1-uart": "uart",
    "microchip,sercom-g1-i2c": "i2c",
    "microchip,sercom-g1-spi": "spi",
}
REQUIRED = {"uart": ["TX", "RX"], "i2c": ["SDA", "SCL"], "spi": ["MOSI", "MISO", "SCK"]}

# UART txpo -> {role: pad index}; values from the ATDF value group and the SAMD5/E5 binding table
UART_TXPO: dict[int, dict[str, int]] = {
    0: {"TX": 0},
    2: {"TX": 0, "RTS": 2, "CTS": 3},
    3: {"TX": 0, "RTS": 2},
}
# SPI dopo -> {role: pad index}; ATDF SERCOM_SPIM_CTRLA__DOPO
SPI_DOPO: dict[int, dict[str, int]] = {
    0: {"MOSI": 0, "SCK": 1, "CS": 2},
    2: {"MOSI": 3, "SCK": 1, "CS": 2},
}
I2C_PADS = {"SDA": 0, "SCL": 1}

KCONFIG: dict[str, dict[str, str]] = {
    "atmel,sam0-uart": {"CONFIG_SERIAL": "y", "CONFIG_UART_SAM0": "y"},
    "atmel,sam0-i2c": {"CONFIG_I2C": "y", "CONFIG_I2C_SAM0": "y"},
    "atmel,sam0-spi": {"CONFIG_SPI": "y", "CONFIG_SPI_SAM0": "y"},
    # g1 driver symbols (UART_MCHP_SERCOM_G1, I2C_MCHP_SERCOM_G1, SPI_MCHP_SERCOM_G1; zephyr-main
    # drivers/{serial,i2c,spi}/Kconfig.mchp) are promptless "default y, depends on
    # DT_HAS_MICROCHIP_SERCOM_G1_*_ENABLED": the devicetree switches them on, and assigning a
    # promptless symbol is a fatal Kconfig error in Zephyr (scripts/kconfig/kconfig.py,
    # check_no_promptless_assign). Only the user-visible subsystem menuconfig is listed.
    "microchip,sercom-g1-uart": {"CONFIG_SERIAL": "y"},
    "microchip,sercom-g1-i2c": {"CONFIG_I2C": "y"},
    "microchip,sercom-g1-spi": {"CONFIG_SPI": "y"},
}
SAM0_COMPATIBLES = [c for c, _ in CLASSES.values()]


def _pad_index(value) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3:
        return value
    if isinstance(value, str) and value.upper() in PADS:
        return PADS.index(value.upper())
    raise RecipeError(f"not a SERCOM pad role: {value!r} (expected one of {', '.join(PADS)})")


def _class_of_compatible(compatible: str) -> str:
    for cls, (comp, _) in CLASSES.items():
        if comp == compatible:
            return cls
    if compatible in G1_CLASSES:
        return G1_CLASSES[compatible]
    raise RecipeError(f"unknown compatible {compatible!r}")


def _class_of_roles(roles: Iterable[str]) -> str:
    roles = set(roles)
    hits = [c for c, (_, rr) in CLASSES.items() if roles & set(rr)]
    if not roles or not hits:
        raise RecipeError(f"no known SERCOM role in assignment: {sorted(roles)}")
    if len(hits) > 1:
        raise RecipeError(f"roles of several classes mixed: {sorted(roles)}")
    unknown = roles - set(CLASSES[hits[0]][1])
    if unknown:
        raise RecipeError(f"role(s) {sorted(unknown)} not valid for {hits[0]}")
    return hits[0]


def _first_int(value, name: str) -> int:
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise RecipeError(f"property {name} must be a single cell, got {value!r}")
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecipeError(f"property {name} is not an integer: {value!r}")
    return value


def _normalize_pins(pins) -> dict[int, str]:
    """pins: {"PAD0": "PB25"} or an iterable of Signal / (pad role, pad) -> {pad index: pad}."""
    if isinstance(pins, dict):
        items = list(pins.items())
    else:
        items = [((p.role, p.pad) if isinstance(p, Signal) else tuple(p)) for p in (pins or [])]
    out: dict[int, str] = {}
    for padrole, pad in items:
        idx = _pad_index(padrole)
        if idx in out and out[idx] != pad:
            raise RecipeError(f"{PADS[idx]} given twice: {out[idx]} and {pad}")
        out[idx] = pad
    return out


class AtmelSam0SercomRecipe:
    api_version = 1
    name = "atmel_sam0_sercom"
    compatibles = SAM0_COMPATIBLES + list(G1_CLASSES)

    # ---- roles -------------------------------------------------------------------
    def roles_for(self, compatible: str) -> list[str]:
        return list(CLASSES[_class_of_compatible(compatible)][1])

    def pin_roles(self, instance: Instance, compatible: str | None = None) -> list[str]:
        """Roles a node on `instance` can use. Without `compatible`: all roles of all classes.

        Only SERCOM instances qualify; any other instance gives an empty list.
        """
        if getattr(instance, "kind", "SERCOM") != "SERCOM":
            return []
        if compatible is not None:
            return self.roles_for(compatible)
        seen: list[str] = []
        for _, roles in CLASSES.values():
            seen += [r for r in roles if r not in seen]
        return seen

    # ---- roles -> properties -------------------------------------------------------
    def derived_props(self, assignment: dict) -> dict:
        """assignment: role -> pad role ("PAD0"). Returns the derived properties or raises RecipeError."""
        assignment = {r: p for r, p in assignment.items() if p is not None}
        cls = _class_of_roles(assignment)
        idx = {r: _pad_index(p) for r, p in assignment.items()}
        missing = [r for r in REQUIRED[cls] if r not in idx]
        if missing:
            raise RecipeError(f"{cls}: role(s) {missing} not assigned")
        return getattr(self, f"_derive_{cls}")(idx)

    @staticmethod
    def _check_distinct(idx: dict[str, int]) -> None:
        by_pad: dict[int, str] = {}
        for role, p in idx.items():
            if p in by_pad:
                raise RecipeError(f"{by_pad[p]} and {role} both on {PADS[p]}")
            by_pad[p] = role

    def _derive_i2c(self, idx: dict[str, int]) -> dict:
        for role, want in I2C_PADS.items():
            if idx[role] != want:
                raise RecipeError(f"i2c: {role} is fixed on {PADS[want]}, not {PADS[idx[role]]}")
        return {}

    def _derive_uart(self, idx: dict[str, int]) -> dict:
        self._check_distinct(idx)
        if "CTS" in idx and "RTS" not in idx:
            raise RecipeError("uart: CTS needs RTS (txpo 2 = TX PAD0, RTS PAD2, CTS PAD3)")
        txpo = 2 if "CTS" in idx else 3 if "RTS" in idx else 0
        for role, want in UART_TXPO[txpo].items():
            if role in idx and idx[role] != want:
                raise RecipeError(f"uart: {role} must be on {PADS[want]} "
                                  f"(txpo {txpo}), not {PADS[idx[role]]}")
        return {"rxpo": idx["RX"], "txpo": txpo}

    def _derive_spi(self, idx: dict[str, int]) -> dict:
        self._check_distinct(idx)
        dopo = next((d for d, m in SPI_DOPO.items()
                     if all(idx[r] == m[r] for r in ("MOSI", "SCK"))), None)
        if dopo is None:
            raise RecipeError(
                f"spi: MOSI/SCK on {PADS[idx['MOSI']]}/{PADS[idx['SCK']]} is not offered; "
                "valid are MOSI PAD0 + SCK PAD1 (dopo 0) or MOSI PAD3 + SCK PAD1 (dopo 2)")
        if "CS" in idx and idx["CS"] != SPI_DOPO[dopo]["CS"]:
            raise RecipeError(f"spi: hardware CS must be on PAD2, not {PADS[idx['CS']]}")
        return {"dipo": idx["MISO"], "dopo": dopo}

    # ---- properties + pins -> roles ------------------------------------------------
    def lift(self, props: dict, pins) -> dict[str, str]:
        """Roles of an existing node: role -> pad id.

        `props`: node properties (``compatible`` if present decides the class; otherwise
        ``rxpo``/``txpo`` mean UART, ``dipo``/``dopo`` SPI, anything else I2C).
        `pins`: ``{"PAD0": "PB25", ...}`` or an iterable of Signal / (pad role, pad).
        A role whose pad is not among `pins` is left out. Invalid properties raise RecipeError.
        """
        comp = props.get("compatible")
        if isinstance(comp, (list, tuple)):
            comp = next((c for c in comp if c in self.compatibles), None)
        if comp:
            cls = _class_of_compatible(comp)
        elif "rxpo" in props or "txpo" in props:
            cls = "uart"
        elif "dipo" in props or "dopo" in props:
            cls = "spi"
        else:
            cls = "i2c"
        by_idx = _normalize_pins(pins)
        if cls == "i2c":
            mapping = dict(I2C_PADS)
        elif cls == "uart":
            for k in ("rxpo", "txpo"):
                if k not in props:
                    raise RecipeError(f"uart: property {k} missing")
            rxpo, txpo = _first_int(props["rxpo"], "rxpo"), _first_int(props["txpo"], "txpo")
            if not 0 <= rxpo <= 3:
                raise RecipeError(f"rxpo {rxpo} out of range 0..3")
            if txpo not in UART_TXPO:
                raise RecipeError(f"txpo {txpo} is reserved or unknown on SAM D5x/E5x "
                                  f"(valid: {sorted(UART_TXPO)})")
            mapping = dict(UART_TXPO[txpo], RX=rxpo)
            if len(set(mapping.values())) != len(mapping):
                raise RecipeError(f"uart: rxpo {rxpo} collides with the pads of txpo {txpo}")
        else:
            for k in ("dipo", "dopo"):
                if k not in props:
                    raise RecipeError(f"spi: property {k} missing")
            dipo, dopo = _first_int(props["dipo"], "dipo"), _first_int(props["dopo"], "dopo")
            if dopo not in SPI_DOPO:
                raise RecipeError(f"dopo {dopo} is reserved or unknown on SAM D5x/E5x "
                                  f"(valid: {sorted(SPI_DOPO)})")
            if not 0 <= dipo <= 3:
                raise RecipeError(f"dipo {dipo} out of range 0..3")
            mapping = dict(SPI_DOPO[dopo])
            if dipo in (mapping["MOSI"], mapping["SCK"]):
                raise RecipeError(f"spi: dipo {dipo} collides with MOSI/SCK of dopo {dopo}")
            mapping["MISO"] = dipo
            if dipo == mapping["CS"]:      # the SS pad is taken by MISO, so there is no CS
                del mapping["CS"]
        order = CLASSES[cls][1]
        return {r: by_idx[mapping[r]] for r in order if r in mapping and mapping[r] in by_idx}

    # ---- enumeration of valid combinations ------------------------------------------
    def valid_assignments(self, cls: str) -> Iterator[dict[str, str]]:
        """Every valid role -> pad role assignment of a class (optional roles included)."""
        if cls == "i2c":
            yield {r: PADS[p] for r, p in I2C_PADS.items()}
        elif cls == "uart":
            for m in UART_TXPO.values():
                for rx in range(4):
                    if rx not in m.values():
                        yield {**{r: PADS[p] for r, p in m.items()}, "RX": PADS[rx]}
        elif cls == "spi":
            for m in SPI_DOPO.values():
                for with_cs in (False, True):
                    for miso in range(4):
                        if miso in (m["MOSI"], m["SCK"]) or (with_cs and miso == m["CS"]):
                            continue
                        a = {"MOSI": PADS[m["MOSI"]], "SCK": PADS[m["SCK"]], "MISO": PADS[miso]}
                        if with_cs:
                            a["CS"] = PADS[m["CS"]]
                        yield a
        else:
            raise RecipeError(f"unknown class {cls!r}")

    # ---- Kconfig ---------------------------------------------------------------------
    def kconfig(self, compatible: str | None = None) -> dict[str, str]:
        """Kconfig symbols for a compatible; without argument the union of the three sam0 tables."""
        if compatible is not None:
            _class_of_compatible(compatible)
            return dict(KCONFIG[compatible])
        out: dict[str, str] = {}
        for c in SAM0_COMPATIBLES:
            out.update(KCONFIG[c])
        return out


register_recipe(AtmelSam0SercomRecipe)
