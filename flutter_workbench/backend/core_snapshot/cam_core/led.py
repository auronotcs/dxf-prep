from __future__ import annotations

"""LED product records and power-loading calculations for sign work.

Product specifications are intentionally explicit snapshots.  A populated
number is not treated as manufacturer-verified unless its field name appears
in ``verified_fields``; custom and label-dependent products therefore remain
visibly incomplete instead of inheriting a plausible but unsafe value.
"""

from dataclasses import dataclass, field
import math
from typing import Any, Iterable
from uuid import uuid4


QWIK_MOD_24V_G2_SOURCE = (
    "Principal Sloan — Qwik Mod 24V Gen 2 Datasheet, Rev F 2026-09-03",
    "https://principalsloan.com/uploads/principal-sloan-qwik-mod-24v-gen-2-datasheet.pdf",
)
PRISM_SYNERGY_SOURCE = (
    "Principal Sloan — Prism Synergy Datasheet",
    "https://principalsloan.com/uploads/prism-synergy-datasheet.pdf",
)
SPECTRUM_RGBW_24V_SOURCE = (
    "Principal Sloan — Spectrum Module Gen 2 Datasheet, Rev C 2026-06-22",
    "https://principalsloan.com/uploads/spectrum-module-gen-2-datasheet.pdf",
)
SPECTRUM_RGBW_12V_LEGACY_SOURCE = (
    "Principal Sloan — Spectrum Module Gen 1 Datasheet, Rev E 2026-05-07",
    "https://principalsloan.com/uploads/spectrum-module-gen-1-datasheet.pdf",
)


@dataclass
class LEDProductPreset:
    preset_id: str
    manufacturer: str = ""
    family: str = ""
    exact_variant: str = ""
    color: str = ""
    voltage: float = 24.0
    product_type: str = "module"  # module, strip, or rope
    watts_per_module: float | None = None
    modules_per_foot: float | None = None
    shop_spacing_in: float | None = None
    footprint_width_in: float | None = None
    footprint_height_in: float | None = None
    watts_per_foot: float | None = None
    cut_increment_in: float | None = None
    max_units_per_run: int | None = None
    channel_count: int | None = None
    channel_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    controller_total_limit_watts: float | None = None
    controller_channel_limits_watts: dict[str, float] = field(default_factory=dict)
    output_leg_limit_amps: float | None = None
    source_title: str = ""
    source_url: str = ""
    verified_fields: tuple[str, ...] = ()
    notes: str = ""
    built_in: bool = False

    @property
    def label(self) -> str:
        identity = self.exact_variant or self.family or "Unnamed LED product"
        return f"{identity} — {self.color}" if self.color else identity

    @property
    def required_fields(self) -> tuple[str, ...]:
        common = ("exact_variant", "color", "voltage", "shop_spacing_in")
        if self.product_type == "module":
            return (*common, "watts_per_module")
        return (*common, "watts_per_foot", "cut_increment_in")

    @property
    def incomplete_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        verified = set(self.verified_fields)
        for name in self.required_fields:
            value = getattr(self, name)
            if value in (None, "") or name not in verified:
                missing.append(name)
        return tuple(missing)

    @property
    def layout_incomplete_fields(self) -> tuple[str, ...]:
        """Physical module dimensions still needed for collision-safe layout."""
        if self.product_type != "module":
            return ()
        verified = set(self.verified_fields)
        return tuple(
            name for name in ("footprint_width_in", "footprint_height_in")
            if getattr(self, name) in (None, "") or name not in verified
        )

    @property
    def verification_label(self) -> str:
        if not self.incomplete_fields:
            if self.layout_incomplete_fields:
                readable = ", ".join(name.replace("_", " ") for name in self.layout_incomplete_fields)
                return f"Electrical specs verified; physical layout incomplete / verify: {readable}"
            return "Manufacturer verified"
        readable = ", ".join(name.replace("_", " ") for name in self.incomplete_fields)
        return f"Incomplete / verify: {readable}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "manufacturer": self.manufacturer,
            "family": self.family,
            "exact_variant": self.exact_variant,
            "color": self.color,
            "voltage": self.voltage,
            "product_type": self.product_type,
            "watts_per_module": self.watts_per_module,
            "modules_per_foot": self.modules_per_foot,
            "shop_spacing_in": self.shop_spacing_in,
            "footprint_width_in": self.footprint_width_in,
            "footprint_height_in": self.footprint_height_in,
            "watts_per_foot": self.watts_per_foot,
            "cut_increment_in": self.cut_increment_in,
            "max_units_per_run": self.max_units_per_run,
            "channel_count": self.channel_count,
            "channel_specs": self.channel_specs,
            "controller_total_limit_watts": self.controller_total_limit_watts,
            "controller_channel_limits_watts": self.controller_channel_limits_watts,
            "output_leg_limit_amps": self.output_leg_limit_amps,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "verified_fields": list(self.verified_fields),
            "notes": self.notes,
            "built_in": self.built_in,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LEDProductPreset":
        def optional_float(name: str) -> float | None:
            raw = value.get(name)
            return None if raw in (None, "") else float(raw)

        raw_limits = value.get("controller_channel_limits_watts", {})
        return cls(
            preset_id=str(value.get("preset_id") or f"led_{uuid4().hex}"),
            manufacturer=str(value.get("manufacturer", "")),
            family=str(value.get("family", "")),
            exact_variant=str(value.get("exact_variant", "")),
            color=str(value.get("color", "")),
            voltage=float(value.get("voltage", 24.0) or 24.0),
            product_type=str(value.get("product_type", "module")),
            watts_per_module=optional_float("watts_per_module"),
            modules_per_foot=optional_float("modules_per_foot"),
            shop_spacing_in=optional_float("shop_spacing_in"),
            footprint_width_in=optional_float("footprint_width_in"),
            footprint_height_in=optional_float("footprint_height_in"),
            watts_per_foot=optional_float("watts_per_foot"),
            cut_increment_in=optional_float("cut_increment_in"),
            max_units_per_run=(
                None if value.get("max_units_per_run") in (None, "")
                else int(value["max_units_per_run"])
            ),
            channel_count=(None if value.get("channel_count") in (None, "") else int(value["channel_count"])),
            channel_specs=dict(value.get("channel_specs", {})),
            controller_total_limit_watts=optional_float("controller_total_limit_watts"),
            controller_channel_limits_watts={
                str(key): float(item) for key, item in raw_limits.items()
            } if isinstance(raw_limits, dict) else {},
            output_leg_limit_amps=optional_float("output_leg_limit_amps"),
            source_title=str(value.get("source_title", "")),
            source_url=str(value.get("source_url", "")),
            verified_fields=tuple(str(item) for item in value.get("verified_fields", ())),
            notes=str(value.get("notes", "")),
            built_in=bool(value.get("built_in", False)),
        )


@dataclass
class PowerSupplyPreset:
    preset_id: str
    display_name: str
    rated_watts: float
    voltage: float = 24.0
    exact_model: str = ""
    usable_percent: float = 90.0
    bank_count: int = 1
    bank_limit_watts: float | None = None
    source_title: str = ""
    source_url: str = ""
    verified_fields: tuple[str, ...] = ()
    built_in: bool = False

    @property
    def usable_watts(self) -> float:
        return self.rated_watts * self.usable_percent / 100.0

    @property
    def bank_capacity_watts(self) -> float | None:
        if self.bank_limit_watts is None:
            return None
        return self.bank_count * self.bank_limit_watts

    @property
    def effective_watts(self) -> float:
        bank_capacity = self.bank_capacity_watts
        return self.usable_watts if bank_capacity is None else min(self.usable_watts, bank_capacity)

    @property
    def verification_label(self) -> str:
        required = {"exact_model", "voltage", "rated_watts", "usable_percent"}
        missing = required - set(self.verified_fields)
        if not self.exact_model:
            missing.add("exact_model")
        if not missing:
            return "Model verified"
        return "Model/spec incomplete: " + ", ".join(sorted(name.replace("_", " ") for name in missing))

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "display_name": self.display_name,
            "rated_watts": self.rated_watts,
            "voltage": self.voltage,
            "exact_model": self.exact_model,
            "usable_percent": self.usable_percent,
            "bank_count": self.bank_count,
            "bank_limit_watts": self.bank_limit_watts,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "verified_fields": list(self.verified_fields),
            "built_in": self.built_in,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PowerSupplyPreset":
        bank_limit = value.get("bank_limit_watts")
        return cls(
            preset_id=str(value.get("preset_id") or f"psu_{uuid4().hex}"),
            display_name=str(value.get("display_name") or "Custom power supply"),
            rated_watts=float(value.get("rated_watts", 60.0)),
            voltage=float(value.get("voltage", 24.0)),
            exact_model=str(value.get("exact_model", "")),
            usable_percent=float(value.get("usable_percent", 90.0)),
            bank_count=max(1, int(value.get("bank_count", 1))),
            bank_limit_watts=None if bank_limit in (None, "") else float(bank_limit),
            source_title=str(value.get("source_title", "")),
            source_url=str(value.get("source_url", "")),
            verified_fields=tuple(str(item) for item in value.get("verified_fields", ())),
            built_in=bool(value.get("built_in", False)),
        )


@dataclass
class SupplyGroupAllocation:
    group_name: str
    supply: PowerSupplyPreset
    quantity: int = 1
    allocated_watts: float | None = None


@dataclass(frozen=True)
class SupplyGroupResult:
    group_name: str
    supply_label: str
    quantity: int
    rated_capacity_watts: float
    usable_capacity_watts: float
    bank_capacity_watts: float | None
    effective_capacity_watts: float
    allocated_load_watts: float
    remaining_capacity_watts: float


@dataclass(frozen=True)
class LEDPlanCalculation:
    module_count: int | None
    entered_length_in: float
    effective_length_in: float
    nominal_load_watts: float | None
    total_rated_capacity_watts: float
    total_usable_capacity_watts: float
    total_effective_capacity_watts: float
    remaining_capacity_watts: float | None
    supply_groups: tuple[SupplyGroupResult, ...]
    channel_load_watts: dict[str, float]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_count": self.module_count,
            "entered_length_in": self.entered_length_in,
            "effective_length_in": self.effective_length_in,
            "nominal_load_watts": self.nominal_load_watts,
            "total_rated_capacity_watts": self.total_rated_capacity_watts,
            "total_usable_capacity_watts": self.total_usable_capacity_watts,
            "total_effective_capacity_watts": self.total_effective_capacity_watts,
            "remaining_capacity_watts": self.remaining_capacity_watts,
            "supply_groups": [result.__dict__ for result in self.supply_groups],
            "channel_load_watts": dict(self.channel_load_watts),
            "warnings": list(self.warnings),
        }


def _verified_product(
    *, preset_id: str, family: str, exact_variant: str, color: str, voltage: float,
    watts_per_module: float, modules_per_foot: float, max_units_per_run: int | None,
    source: tuple[str, str], notes: str = "", channel_count: int | None = None,
    channel_specs: dict[str, dict[str, Any]] | None = None,
    output_leg_limit_amps: float | None = None,
) -> LEDProductPreset:
    fields = (
        "exact_variant", "color", "voltage", "watts_per_module",
        "modules_per_foot", "shop_spacing_in",
    )
    return LEDProductPreset(
        preset_id=preset_id, manufacturer="Principal Sloan", family=family,
        exact_variant=exact_variant, color=color, voltage=voltage,
        watts_per_module=watts_per_module, modules_per_foot=modules_per_foot,
        shop_spacing_in=12.0 / modules_per_foot, max_units_per_run=max_units_per_run,
        channel_count=channel_count, channel_specs=channel_specs or {},
        output_leg_limit_amps=output_leg_limit_amps,
        source_title=source[0], source_url=source[1], verified_fields=fields,
        notes=notes, built_in=True,
    )


def builtin_led_product_presets() -> list[LEDProductPreset]:
    products: list[LEDProductPreset] = []
    qwik_colors = {
        "7100K White": ("M-QMG22-24-71", 0.68),
        "6500K White": ("M-QMG22-24-65", 0.68),
        "5000K White": ("M-QMG22-24-50", 0.68),
        "4000K White": ("M-QMG22-24-40", 0.68),
        "3000K White": ("M-QMG22-24-30", 0.68),
        "Red 625nm": ("M-QMG22-24-RD", 0.71),
        "Green 525nm": ("M-QMG22-24-GR", 0.71),
        "Blue 460nm": ("M-QMG22-24-BL", 0.71),
    }
    for color, (sku, watts) in qwik_colors.items():
        products.append(_verified_product(
            preset_id=f"qwik_mod_2_g2_24v_{sku.rsplit('-', 1)[-1].casefold()}",
            family="Qwik Mod 2 G2", exact_variant=f"Qwik Mod 2 G2 / {sku}",
            color=color, voltage=24.0, watts_per_module=watts,
            modules_per_foot=1.50, max_units_per_run=65,
            source=QWIK_MOD_24V_G2_SOURCE,
            notes="Primary shop module. Manufacturer nominal density is 1.50 modules/ft; edit shop spacing per construction.",
        ))
    prism_colors = {
        "7100K White": "M-SYHO0-71",
        "6500K White": "M-SYHO0-65",
        "5000K White": "M-SYHO0-50",
        "4100K White": "M-SYHO0-41",
        "3000K White": "M-SYHO0-30",
    }
    for color, sku in prism_colors.items():
        products.append(_verified_product(
            preset_id=f"prism_synergy_ho_{sku.rsplit('-', 1)[-1].casefold()}",
            family="Prism Synergy", exact_variant=f"Prism Synergy HO / {sku}",
            color=color, voltage=24.0, watts_per_module=1.330,
            modules_per_foot=1.20, max_units_per_run=36,
            source=PRISM_SYNERGY_SOURCE,
            notes="High-output module used for deeper cabinets; manufacturer coverage varies with cabinet depth and face material.",
        ))
    spectrum_3_channels = {
        "Red": {"lumens_per_module": 11, "wavelength_nm": "615-625", "watts_per_module": None},
        "Green": {"lumens_per_module": 19, "wavelength_nm": "520-530", "watts_per_module": None},
        "Blue": {"lumens_per_module": 4, "wavelength_nm": "460-470", "watts_per_module": None},
        "White": {"lumens_per_module": 19, "cct": "6500K", "watts_per_module": None},
    }
    products.append(_verified_product(
        preset_id="spectrum_rgbw_g2_3d_24v", family="Spectrum RGBW Gen 2 3-Diode",
        exact_variant="Spectrum RGBW Gen 2 3-Diode / M-SPTC03-24-4C", color="RGBW + 6500K White",
        voltage=24.0, watts_per_module=0.92, modules_per_foot=1.50,
        max_units_per_run=50, source=SPECTRUM_RGBW_24V_SOURCE,
        channel_count=4, channel_specs=spectrum_3_channels,
        notes="Current 24V PWM product. Principal Sloan Superior Series Class 2 power supplies are required; controller limits depend on the selected PWM controller.",
    ))
    spectrum_1_channels = {
        "Red": {"lumens_per_module": 4, "wavelength_nm": "615-625", "watts_per_module": None},
        "Green": {"lumens_per_module": 10, "wavelength_nm": "520-530", "watts_per_module": None},
        "Blue": {"lumens_per_module": 2, "wavelength_nm": "460-470", "watts_per_module": None},
        "White": {"lumens_per_module": 8.5, "cct": "6500K", "watts_per_module": None},
    }
    products.append(_verified_product(
        preset_id="spectrum_rgbw_g2_1d_24v", family="Spectrum RGBW Gen 2 1-Diode",
        exact_variant="Spectrum RGBW Gen 2 1-Diode / M-SPTC01-24-4C", color="RGBW + 6500K White",
        voltage=24.0, watts_per_module=0.32, modules_per_foot=2.0,
        max_units_per_run=90, source=SPECTRUM_RGBW_24V_SOURCE,
        channel_count=4, channel_specs=spectrum_1_channels,
        notes="Current narrow-channel 24V PWM variant. Principal Sloan Superior Series Class 2 power supplies are required.",
    ))
    legacy_channels = {
        "Red": {"lumens_per_module": 10, "wavelength_nm": 622, "watts_per_module": None},
        "Green": {"lumens_per_module": 26, "wavelength_nm": 526, "watts_per_module": None},
        "Blue": {"lumens_per_module": 6, "wavelength_nm": 469, "watts_per_module": None},
        "White": {"lumens_per_module": 26, "cct": "6500K", "watts_per_module": None},
    }
    products.append(_verified_product(
        preset_id="spectrum_rgbw_g1_legacy_12v", family="Spectrum Module Gen 1 (Legacy)",
        exact_variant="Spectrum RGBW Gen 1 / 701269-RGBW3M1-MB", color="RGBW + 6500K White",
        voltage=12.0, watts_per_module=0.92, modules_per_foot=1.50,
        max_units_per_run=30, source=SPECTRUM_RGBW_12V_LEGACY_SOURCE,
        channel_count=4, channel_specs=legacy_channels,
        notes="Legacy 12V product retained only for old stock/service work; do not substitute it for the current 24V Gen 2 module.",
    ))
    products.append(_verified_product(
        preset_id="qwik_mod_3_g2_24v_65", family="Qwik Mod 3 G2",
        exact_variant="Qwik Mod 3 G2 / M-QMG23-24-65", color="6500K White",
        voltage=24.0, watts_per_module=1.0, modules_per_foot=1.35,
        max_units_per_run=45, source=QWIK_MOD_24V_G2_SOURCE,
        notes="Special-order QM3 preset; confirm that this exact SKU is the product received.",
    ))
    products.extend((
        LEDProductPreset(
            preset_id="custom_micro", family="Micro module", exact_variant="",
            color="", voltage=24.0, product_type="module",
            notes="Custom micro preset. Enter the exact label/SKU, wattage, color, and shop spacing before use.",
        ),
        LEDProductPreset(
            preset_id="custom_special_module", family="Special-order module", exact_variant="",
            color="", voltage=24.0, product_type="module",
            notes="Blank 24V module template; unverified values intentionally remain incomplete.",
        ),
        LEDProductPreset(
            preset_id="custom_strip_rope", family="Rope / strip lighting", exact_variant="",
            color="", voltage=24.0, product_type="strip",
            notes="Enter watts per foot and the manufacturer cut increment from the label or datasheet.",
        ),
    ))
    return products


def builtin_power_supply_presets() -> list[PowerSupplyPreset]:
    # These are the shop's stock wattage classes, not claims about a specific
    # power-supply model.  Exact model and bank data intentionally remain blank.
    return [
        PowerSupplyPreset(
            preset_id=f"shop_psu_{watts}", display_name=f"Shop {watts} W PSU",
            rated_watts=float(watts), voltage=24.0, usable_percent=90.0,
            exact_model="", verified_fields=(), built_in=True,
        )
        for watts in (60, 96, 192, 288)
    ]


def calculate_led_plan(
    product: LEDProductPreset,
    *,
    run_length_in: float = 0.0,
    spacing_in: float | None = None,
    manual_module_count: int | None = None,
    supplies: Iterable[SupplyGroupAllocation] = (),
) -> LEDPlanCalculation:
    if run_length_in < 0:
        raise ValueError("LED run length cannot be negative.")
    if spacing_in is not None and spacing_in <= 0:
        raise ValueError("LED spacing must be positive.")
    if manual_module_count is not None and manual_module_count < 0:
        raise ValueError("Module count cannot be negative.")

    warnings: list[str] = []
    effective_length = run_length_in
    module_count: int | None = None
    nominal_load: float | None = None
    if product.product_type == "module":
        chosen_spacing = spacing_in if spacing_in is not None else product.shop_spacing_in
        if manual_module_count is not None:
            module_count = manual_module_count
        elif run_length_in > 0 and chosen_spacing:
            module_count = math.ceil(run_length_in / chosen_spacing)
        if module_count is None:
            warnings.append("Enter a module count or a lit run length and spacing.")
        if product.watts_per_module is None:
            warnings.append("Rated watts per module is unverified or missing.")
        elif module_count is not None:
            nominal_load = module_count * product.watts_per_module
        if product.max_units_per_run and module_count and module_count > product.max_units_per_run:
            runs = math.ceil(module_count / product.max_units_per_run)
            warnings.append(
                f"Split into at least {runs} runs; this product allows {product.max_units_per_run} modules per run."
            )
    elif product.product_type in {"strip", "rope"}:
        if run_length_in <= 0:
            warnings.append("Enter the required rope/strip length.")
        if product.cut_increment_in:
            effective_length = math.ceil(run_length_in / product.cut_increment_in) * product.cut_increment_in
        else:
            warnings.append("Cut increment is unverified or missing; length has not been rounded to a legal cut point.")
        if product.watts_per_foot is None:
            warnings.append("Rated watts per foot is unverified or missing.")
        elif effective_length > 0:
            nominal_load = effective_length / 12.0 * product.watts_per_foot
    else:
        raise ValueError(f"Unsupported LED product type: {product.product_type}")

    channel_loads: dict[str, float] = {}
    if module_count is not None and product.channel_specs:
        for channel, specification in product.channel_specs.items():
            watts = specification.get("watts_per_module") if isinstance(specification, dict) else None
            if watts not in (None, ""):
                channel_loads[channel] = module_count * float(watts)
        expected_channels = product.channel_count or len(product.channel_specs)
        if len(channel_loads) < expected_channels:
            warnings.append("RGBW per-channel wattage is incomplete; total module load is still calculated.")
        if product.channel_count and not product.controller_total_limit_watts:
            warnings.append("Controller total/channel limits are not verified for the selected RGBW controller.")
        if product.controller_total_limit_watts and nominal_load is not None and nominal_load > product.controller_total_limit_watts:
            warnings.append("LED load exceeds the selected product/controller total limit.")
        for channel, load in channel_loads.items():
            limit = product.controller_channel_limits_watts.get(channel)
            if limit is not None and load > limit:
                warnings.append(f"{channel} channel load exceeds its controller limit.")

    allocations = list(supplies)
    for allocation in allocations:
        if allocation.quantity <= 0:
            raise ValueError("Power-supply quantity must be at least one.")
        if allocation.supply.usable_percent <= 0 or allocation.supply.usable_percent > 100:
            raise ValueError("Power-supply usable capacity must be greater than 0% and no more than 100%.")
        if not math.isclose(allocation.supply.voltage, product.voltage, abs_tol=1e-9):
            warnings.append(
                f"{allocation.group_name}: {allocation.supply.voltage:g}V supply does not match {product.voltage:g}V LEDs."
            )

    remaining_load = nominal_load or 0.0
    group_results: list[SupplyGroupResult] = []
    for allocation in allocations:
        supply = allocation.supply
        rated = supply.rated_watts * allocation.quantity
        usable = supply.usable_watts * allocation.quantity
        bank = None if supply.bank_capacity_watts is None else supply.bank_capacity_watts * allocation.quantity
        effective = supply.effective_watts * allocation.quantity
        allocated = (
            float(allocation.allocated_watts)
            if allocation.allocated_watts is not None
            else min(remaining_load, effective)
        )
        remaining_load = max(0.0, remaining_load - allocated)
        group_results.append(SupplyGroupResult(
            group_name=allocation.group_name,
            supply_label=supply.display_name,
            quantity=allocation.quantity,
            rated_capacity_watts=rated,
            usable_capacity_watts=usable,
            bank_capacity_watts=bank,
            effective_capacity_watts=effective,
            allocated_load_watts=allocated,
            remaining_capacity_watts=effective - allocated,
        ))
        if allocated > effective + 1e-9:
            warnings.append(f"{allocation.group_name}: allocated load exceeds usable/bank capacity.")
        if not supply.exact_model:
            warnings.append(f"{allocation.group_name}: exact power-supply model and output-bank data are unverified.")

    total_rated = sum(item.rated_capacity_watts for item in group_results)
    total_usable = sum(item.usable_capacity_watts for item in group_results)
    total_effective = sum(item.effective_capacity_watts for item in group_results)
    remaining_capacity = None if nominal_load is None else total_effective - nominal_load
    if nominal_load is not None and nominal_load > total_effective + 1e-9:
        warnings.append(f"Add {nominal_load - total_effective:.2f} W of usable supply capacity.")
    if product.incomplete_fields:
        warnings.append(product.verification_label)

    return LEDPlanCalculation(
        module_count=module_count,
        entered_length_in=run_length_in,
        effective_length_in=effective_length,
        nominal_load_watts=nominal_load,
        total_rated_capacity_watts=total_rated,
        total_usable_capacity_watts=total_usable,
        total_effective_capacity_watts=total_effective,
        remaining_capacity_watts=remaining_capacity,
        supply_groups=tuple(group_results),
        channel_load_watts=channel_loads,
        warnings=tuple(dict.fromkeys(warnings)),
    )
