"""UI-independent validation for router profile parameters.

The desktop dialog remains responsible for parsing text fields and presenting
messages.  This module owns the machining rules so automation, future dialog
layouts, and regression tests all exercise the same decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .workflow import recipe_matches


@dataclass(frozen=True)
class RouterParameterIssue:
    """One user-correctable router parameter problem."""

    field: str | None
    message: str
    title: str = "Invalid cut settings"


@dataclass(frozen=True)
class RouterValidationContext:
    """Job, machine, and cutter facts needed to validate one parameter set."""

    stock_thickness: float
    project_units: str
    open_contour: bool
    outside_cut: bool
    guided_mode: bool = False
    stock_verified: bool = True
    job_material: str = "Anything"
    job_thickness_in: float = 0.0
    process_role: str = "any"
    machine_min_rpm: int = 8_000
    machine_max_rpm: int = 24_000
    station_max_rpm: int | None = None
    tool_min_rpm: int | None = None
    tool_max_rpm: int | None = None


def validate_router_parameters(
    parameters: Mapping[str, Any],
    context: RouterValidationContext,
) -> RouterParameterIssue | None:
    """Return the first parameter problem in the established dialog order."""

    station = int(parameters["station"])
    tabs_enabled = bool(parameters["tabs_enabled"])

    if not 1 <= station <= 12:
        return RouterParameterIssue("station", "Tool station must be between 1 and 12.")
    finite_fields = (
        "diameter", "target_depth", "stepdown", "safe_z", "feed_rate",
        "finish_feed", "plunge_rate", "helix_feed", "spindle_rpm",
        "finish_spindle_rpm", "breakthrough", "allowance", "lead_in",
        "lead_out", "onion_skin", "finish_axial_stock", "helix_diameter",
        "helix_min_diameter", "helix_pitch", "helix_ramp_angle",
        "helix_clearance", "curve_feed_min_percent",
        "curve_feed_transition_distance", "common_line_tolerance",
    )
    invalid_finite = next((
        key for key in finite_fields
        if not math.isfinite(float(parameters[key]))
    ), None)
    if invalid_finite is not None:
        return RouterParameterIssue(invalid_finite, "Enter a finite numeric value.")
    if float(parameters["breakthrough"]) < 0:
        return RouterParameterIssue("breakthrough", "Breakthrough cannot be negative.")
    if (
        parameters["cut_intent"] == "partial"
        and context.stock_thickness > 0
        and float(parameters["target_depth"]) >= context.stock_thickness - 1e-9
    ):
        return RouterParameterIssue(
            "target_depth",
            f"Partial depth must be less than the {context.stock_thickness:g} {context.project_units} "
            "stock thickness. Choose Through cut if the tool should reach or pass the bottom.",
        )

    if context.guided_mode:
        if not context.stock_verified:
            return RouterParameterIssue(None, "Physical stock verification is missing or stale.", "Guided Production")
        recipe = parameters["recipe_metadata"]
        if recipe.get("qualification") != "production_approved" or not recipe_matches(
            recipe, context.job_material, context.job_thickness_in, context.process_role,
        ):
            return RouterParameterIssue(None, "An exact production-approved recipe is required.", "Guided Production")
        if parameters["cut_intent"] != "through":
            return RouterParameterIssue(
                None, "This guided router recipe is qualified only as a through-cut.", "Guided Production",
            )
        if parameters["contour_order"] == "source_order" and not context.open_contour:
            return RouterParameterIssue(
                "contour_order",
                "Guided Production does not allow literal DXF / selection order because it can release an "
                "outside profile before its internal features.",
                "Guided Production",
            )
        expected_depth = context.stock_thickness + float(parameters["breakthrough"])
        if abs(float(parameters["target_depth"]) - expected_depth) > 1e-6:
            return RouterParameterIssue(
                "breakthrough", "Final depth must equal verified thickness plus breakthrough.", "Guided Production",
            )

    if not str(parameters["tool_name"]):
        return RouterParameterIssue("tool_name", "Tool name cannot be empty.")

    positive = (
        "diameter", "target_depth", "stepdown", "safe_z", "feed_rate", "finish_feed",
        "plunge_rate", "helix_feed", "spindle_rpm", "finish_spindle_rpm",
    )
    invalid_positive = next((key for key in positive if float(parameters[key]) <= 0), None)
    if invalid_positive is not None:
        return RouterParameterIssue(invalid_positive, "This value must be greater than zero.")

    for key in ("spindle_rpm", "finish_spindle_rpm"):
        rpm = int(parameters[key])
        if (
            rpm < context.machine_min_rpm
            or rpm > context.machine_max_rpm
            or context.tool_min_rpm is not None and rpm < context.tool_min_rpm
            or context.tool_max_rpm is not None and rpm > context.tool_max_rpm
            or context.station_max_rpm is not None and rpm > context.station_max_rpm
        ):
            return RouterParameterIssue(
                key, f"RPM must remain within the configured machine, T{station}, and cutter limits.",
            )

    allowed_values = (
        ("cut_direction", {"climb", "conventional"}, "Choose climb or conventional cutting."),
        ("curve_feed_mode", {"off", "wall_compensation"}, "Choose Off or Wall-speed compensation."),
    )
    for key, allowed, message in allowed_values:
        if parameters[key] not in allowed:
            return RouterParameterIssue(key, message)
    if not 0 < float(parameters["curve_feed_min_percent"]) <= 100:
        return RouterParameterIssue(
            "curve_feed_min_percent", "Enter a percentage above 0 and no more than 100.",
        )
    if float(parameters["curve_feed_transition_distance"]) <= 0:
        return RouterParameterIssue("curve_feed_transition_distance", "Transition distance must be greater than zero.")
    if parameters["common_line_mode"] not in {"off", "paired_rectangles"}:
        return RouterParameterIssue("common_line_mode", "Choose Off or Paired rectangular edges.")
    if parameters["contour_order"] not in {"inside_first", "part_by_part", "source_order"}:
        return RouterParameterIssue("contour_order", "Choose a valid grouped contour order.")
    if float(parameters["common_line_tolerance"]) <= 0:
        return RouterParameterIssue("common_line_tolerance", "Shared-edge tolerance must be greater than zero.")
    if parameters["entry_mode"] not in {"plunge", "ramp", "helical"}:
        return RouterParameterIssue("entry_mode", "Choose plunge, ramp, or helical entry.")
    if parameters["entry_side"] not in {"automatic_waste", "inside", "outside"}:
        return RouterParameterIssue("entry_side", "Choose Automatic Waste, Inside, or Outside.")
    if parameters["helix_fallback"] not in {"waste_plunge", "strict_error"}:
        return RouterParameterIssue("helix_fallback", "Choose a valid helical-entry fallback.")

    nonnegative = (
        "start_index", "lead_in", "lead_out", "onion_skin", "finish_axial_stock",
        "helix_diameter", "helix_min_diameter", "helix_pitch", "helix_ramp_angle",
        "helix_clearance", "helix_finish_revolutions",
    ) + (
        ("tab_count", "tab_width", "tab_height", "female_tab_min_perimeter", "tab_cleanup_min_perimeter")
        if tabs_enabled else ()
    )
    invalid_nonnegative = next((key for key in nonnegative if float(parameters[key]) < 0), None)
    if invalid_nonnegative is not None:
        return RouterParameterIssue(invalid_nonnegative, "This value cannot be negative.")

    if parameters["entry_mode"] == "helical" and float(parameters["helix_diameter"]) <= 0:
        return RouterParameterIssue("helix_diameter", "Helical entry requires a positive cutter-center diameter.")
    if parameters["entry_mode"] == "helical" and float(parameters["helix_pitch"]) <= 0:
        return RouterParameterIssue("helix_pitch", "Helical entry requires a positive pitch per revolution.")
    if float(parameters["helix_min_diameter"]) > float(parameters["helix_diameter"]):
        return RouterParameterIssue(
            "helix_min_diameter", "Minimum helix diameter cannot exceed the preferred helix diameter.",
        )
    if float(parameters["helix_ramp_angle"]) >= 90:
        return RouterParameterIssue(
            "helix_ramp_angle", "Maximum helix angle must be below 90 degrees; 0 disables the angle limit.",
        )
    if any(float(value) <= 0 for value in parameters["pass_depths"]):
        return RouterParameterIssue(
            "pass_depths", "Every explicit pass increment must be positive, or leave this optional field blank.",
        )

    finish_reserve = float(parameters["finish_axial_stock"]) if parameters["finishing_pass"] else 0.0
    rough_depth = float(parameters["target_depth"]) - max(float(parameters["onion_skin"]), finish_reserve)
    if sum(float(value) for value in parameters["pass_depths"]) > rough_depth + 1e-9:
        return RouterParameterIssue(
            "pass_depths", "The increments cannot total more than the roughing depth above reserved bottom stock.",
        )
    if float(parameters["onion_skin"]) >= float(parameters["target_depth"]) and float(parameters["onion_skin"]) > 0:
        return RouterParameterIssue("onion_skin", "Onion skin must be smaller than final depth.")
    if parameters["finishing_pass"] and float(parameters["finish_axial_stock"]) >= float(parameters["target_depth"]):
        return RouterParameterIssue("finish_axial_stock", "Axial stock to leave must be smaller than final depth.")

    if tabs_enabled and parameters["tab_mode"] not in {"even", "minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "manual", "aligned"}:
        return RouterParameterIssue("tab_mode", "Choose a valid tab placement strategy.")
    if tabs_enabled and parameters["tab_cleanup_order"] not in {"after_each_part", "end_of_operation"}:
        return RouterParameterIssue("tab_cleanup_order", "Choose a valid tab cleanup order.")
    if tabs_enabled and parameters["tab_mode"] == "aligned":
        if not context.outside_cut:
            return RouterParameterIssue("tab_mode", "Aligned Rows / Columns requires an Outside cut. Select neighboring parts together in one operation.")
        if parameters["entry_mode"] != "plunge" or float(parameters["lead_in"]) > 1e-9 or float(parameters["lead_out"]) > 1e-9:
            return RouterParameterIssue("entry_mode", "Aligned tabs require Plunge entry and zero lead-in/lead-out to protect shared bridges.")
        if parameters["remove_tabs_after"] and parameters["tab_cleanup_order"] != "end_of_operation":
            return RouterParameterIssue("tab_cleanup_order", "Aligned tab removal requires End of operation, not After each part. All profile cuts must finish before shared bridges are removed.")
    if tabs_enabled and any(not 0 <= float(value) < 1 for value in parameters["manual_tab_positions"]):
        return RouterParameterIssue(
            "manual_tab_positions", "Percentages must be from 0 up to, but not including, 100.",
        )

    has_tabs = tabs_enabled
    if parameters["common_line_mode"] != "off" and (
        not context.outside_cut
        or abs(float(parameters["allowance"])) > 1e-9
        or parameters["entry_mode"] != "plunge"
        or float(parameters["lead_in"]) > 1e-9
        or float(parameters["lead_out"]) > 1e-9
        or has_tabs
    ):
        return RouterParameterIssue(
            "common_line_mode",
            "The first common-line implementation requires an Outside cut, Plunge entry, zero allowance/leads, "
            "and no tabs.",
        )
    if tabs_enabled and parameters["tab_mode"] == "even" and int(parameters["tab_count"]) <= 0:
        return RouterParameterIssue("tab_count", "Enable tabs with a positive count, or turn the master switch off.")
    if tabs_enabled and parameters["tab_mode"] == "manual" and not parameters["manual_tab_positions"]:
        return RouterParameterIssue(
            "manual_tab_positions", "Manual placement requires at least one tab-center percentage.",
        )
    if has_tabs and (
        float(parameters["tab_width"]) <= 0
        or float(parameters["tab_height"]) <= 0
        or float(parameters["tab_height"]) >= float(parameters["target_depth"]) -
            (float(parameters["breakthrough"]) if parameters["cut_intent"] == "through" else 0.0)
    ):
        if float(parameters["tab_width"]) <= 0:
            return RouterParameterIssue("tab_width", "Tabs require a positive width.")
        return RouterParameterIssue("tab_height", "Tab thickness must be positive and below final depth, excluding breakthrough for through cuts.")
    if (
        tabs_enabled
        and parameters["tab_mode"] in {"minimum_and_spacing", "minimum_avoid_corners", "support_aware", "corners_and_spacing", "aligned"}
        and float(parameters["tab_spacing"]) <= 0
    ):
        return RouterParameterIssue(
            "tab_spacing", "Maximum tab spacing must be positive for this strategy.",
        )
    if (
        tabs_enabled
        and parameters["tab_mode"] == "corners_and_spacing"
        and float(parameters["corner_tab_offset"]) <= 0
    ):
        return RouterParameterIssue(
            "corner_tab_offset", "Corner tab distance must be positive for this strategy.",
        )
    if parameters["remove_tabs_after"] and not has_tabs:
        return RouterParameterIssue(None, "Tab cleanup requires an enabled tab placement method.")
    return None
