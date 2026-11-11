"""Process planning helpers shared by the GUI and automation layer.

These functions only analyze or reorder data. They never silently change a
tool assignment, recipe, or hold-down strategy; suggestions remain subject to
operator approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence

from .layout import entity_bounds
from .optimization import PRODUCTION_OPTIMIZATION_BEHAVIORS
from .model import (
    CamProject, Circle, Entity, Operation, Point, Polyline, Rectangle,
    RouterOffsetOperation, OffsetSide, inferred_sequence_role,
    operation_sequence_key,
)


RECIPE_QUALIFICATION_STATES = (
    "archive_observed", "prototype", "shop_tested", "production_approved", "retired",
)
RECIPE_QUALIFICATION_LABELS = {
    "archive_observed": "Archive observed",
    "prototype": "Prototype",
    "shop_tested": "Shop tested",
    "production_approved": "Production approved",
    "retired": "Retired",
}
RECIPE_PROCESS_ROLES = (
    "any", "mark_engrave", "drill_thread", "shelf_recess", "pocket_fill",
    "inside_profile", "outside_profile", "finish_cutoff",
)
ENTRY_STRATEGIES = ("plunge", "ramp", "helical", "pre_drilled", "edge")


def normalize_material(value: str) -> str:
    value = value.casefold().replace("-", " ").replace("_", " ")
    aliases = {"aluminium": "aluminum", "acrylic cast": "cast acrylic", "corroplast": "coroplast"}
    compact = " ".join(value.split())
    return aliases.get(compact, compact)


def recipe_matches(recipe: Mapping[str, Any], material: str, thickness_in: float, process_role: str) -> bool:
    """Return whether a catalog recipe is applicable to this job and process."""
    if str(recipe.get("qualification", "prototype")) == "retired":
        return False
    recipe_material = normalize_material(str(recipe.get("material_family") or recipe.get("material", "Anything")).split("—", 1)[0])
    requested = normalize_material(material)
    material_ok = recipe_material in {"", "anything"} or requested in recipe_material or recipe_material in requested
    roles = recipe.get("process_roles", ["any"])
    if isinstance(roles, str):
        roles = [item.strip() for item in roles.split(",") if item.strip()]
    role_ok = "any" in roles or process_role in roles
    minimum = recipe.get("thickness_min_in")
    maximum = recipe.get("thickness_max_in")
    thickness_ok = (minimum is None or thickness_in >= float(minimum)) and (maximum is None or thickness_in <= float(maximum))
    return material_ok and role_ok and thickness_ok


def ranked_recipes(catalog: Sequence[Mapping[str, Any]], material: str, thickness_in: float, process_role: str) -> list[dict[str, Any]]:
    rank = {"production_approved": 0, "shop_tested": 1, "prototype": 2, "archive_observed": 3, "retired": 9}
    results: list[dict[str, Any]] = []
    for tool in catalog:
        for recipe in tool.get("recipes", []):
            if recipe_matches(recipe, material, thickness_in, process_role):
                results.append({"tool": tool, "recipe": recipe})
    return sorted(results, key=lambda item: (rank.get(str(item["recipe"].get("qualification", "prototype")), 5), str(item["tool"].get("name", "")), str(item["recipe"].get("material", ""))))


def calculated_chip_load(recipe: Mapping[str, Any], flutes: int | None) -> float | None:
    """Return the programmed inches-per-tooth for a recipe, when calculable."""
    rpm, feed = recipe.get("rpm"), recipe.get("feed")
    if not rpm or feed is None or not flutes:
        return None
    rpm_value, feed_value, flute_count = float(rpm), float(feed), int(flutes)
    if rpm_value <= 0 or feed_value < 0 or flute_count <= 0:
        return None
    return feed_value / rpm_value / flute_count


def chip_load_comparison(recipe: Mapping[str, Any], flutes: int | None) -> dict[str, Any]:
    """Compare programmed chip load with optional manufacturer low/target/high data."""
    actual = calculated_chip_load(recipe, flutes)

    def optional_positive(key: str) -> float | None:
        raw = recipe.get(key)
        if raw in {None, ""}:
            return None
        value = float(raw)
        return value if value > 0 else None

    low = optional_positive("manufacturer_chip_load_min_in")
    target = optional_positive("manufacturer_chip_load_target_in")
    high = optional_positive("manufacturer_chip_load_max_in")
    if target is None and low is not None and high is not None:
        target = (low + high) / 2.0

    status = "not_entered"
    if actual is not None and any(value is not None for value in (low, target, high)):
        if low is not None and actual < low:
            status = "below"
        elif high is not None and actual > high:
            status = "above"
        else:
            status = "within"
    delta = None if actual is None or not target else (actual - target) / target * 100.0
    return {
        "actual": actual,
        "recommended_min": low,
        "recommended_target": target,
        "recommended_max": high,
        "status": status,
        "target_delta_percent": delta,
        "source": str(recipe.get("manufacturer_chip_load_source", "")).strip(),
    }


def compare_recipes(recipes: Sequence[Mapping[str, Any]], flutes: int | None = None) -> list[dict[str, Any]]:
    """Return absolute values and deltas from the first recipe."""
    if not recipes:
        return []
    baseline = recipes[0]
    fields = ("rpm", "feed", "plunge", "stepdown", "stepover")
    output = []
    for recipe in recipes:
        row = {"material": recipe.get("material", ""), "qualification": recipe.get("qualification", "prototype")}
        for field in fields:
            value = recipe.get(field)
            base = baseline.get(field)
            row[field] = value
            row[f"{field}_delta_percent"] = None if value is None or not base else (float(value) - float(base)) / float(base) * 100.0
        comparison = chip_load_comparison(recipe, flutes)
        row["chip_load"] = comparison["actual"]
        row["chip_load_recommended_min"] = comparison["recommended_min"]
        row["chip_load_recommended_target"] = comparison["recommended_target"]
        row["chip_load_recommended_max"] = comparison["recommended_max"]
        row["chip_load_status"] = comparison["status"]
        row["chip_load_target_delta_percent"] = comparison["target_delta_percent"]
        output.append(row)
    return output


def ordered_operations(operations: Sequence[Operation]) -> list[Operation]:
    """Sort unlocked operations while preserving every locked list position."""
    output = list(operations)
    unlocked_indexes = [index for index, operation in enumerate(output) if not operation.sequence_locked]
    ordered = sorted((output[index] for index in unlocked_indexes), key=operation_sequence_key)
    for index, operation in zip(unlocked_indexes, ordered):
        output[index] = operation
    # Apply hard prerequisites after role/lock ordering. First reorder only the
    # unlocked slots so unrelated position locks remain exact. A dependency is
    # allowed to override a locked position only if that locked arrangement
    # itself makes the prerequisite impossible to honor.
    known = {operation.id for operation in output}
    prerequisites = {}
    for operation in output:
        values = {value for value in operation.depends_on_ids if value in known}
        source_id = getattr(operation, "source_operation_id", "")
        if source_id in known:
            values.add(source_id)
        prerequisites[operation.id] = values
    def stable_topological(items: list[Operation], outside_satisfied: set[str]) -> list[Operation] | None:
        item_ids = {item.id for item in items}
        pending = list(items)
        result: list[Operation] = []
        emitted = set(outside_satisfied)
        while pending:
            ready_index = next(
                (
                    index for index, operation in enumerate(pending)
                    if (prerequisites[operation.id] & item_ids) <= emitted
                ),
                None,
            )
            if ready_index is None:
                return None
            operation = pending.pop(ready_index)
            result.append(operation)
            emitted.add(operation.id)
        return result

    unlocked = [operation for operation in output if not operation.sequence_locked]
    reordered_unlocked = stable_topological(unlocked, set())
    if reordered_unlocked is not None:
        candidate = list(output)
        for index, operation in zip(unlocked_indexes, reordered_unlocked):
            candidate[index] = operation
        positions = {operation.id: index for index, operation in enumerate(candidate)}
        if all(
            positions[required] < positions[operation.id]
            for operation in candidate
            for required in prerequisites[operation.id]
        ):
            return candidate

    # A locked anchor conflicts with a hard prerequisite, so safety ordering
    # takes precedence. Validation separately reports cycles or bad references.
    result = stable_topological(list(output), set())
    return result if result is not None else output


@dataclass(frozen=True)
class ConsolidationSuggestion:
    keep_station: int
    release_station: int
    operation_ids: tuple[str, ...]
    reason: str


def tool_consolidation_suggestions(project: CamProject) -> list[ConsolidationSuggestion]:
    """Find equivalent physical tools assigned to multiple stations."""
    groups: dict[tuple[Any, ...], list[int]] = {}
    for tool in project.tool_library.tools:
        catalog_id = str(tool.metadata.get("catalog_tool_id", "")).casefold()
        part = str(tool.metadata.get("part_number", "")).casefold()
        identity = (catalog_id or part or tool.name.casefold(), tool.kind.value, round(tool.diameter, 7), tool.spindle_mode)
        groups.setdefault(identity, []).append(tool.station)
    suggestions = []
    for stations in groups.values():
        if len(stations) < 2:
            continue
        keep = min(stations)
        for release in sorted(stations)[1:]:
            operation_ids = tuple(operation.id for operation in project.operations if operation.tool_station == release)
            suggestions.append(ConsolidationSuggestion(keep, release, operation_ids, f"T{keep} and T{release} contain the same catalog tool; process roles can preserve operation order."))
    return suggestions


@dataclass(frozen=True)
class SmallPartRisk:
    operation_id: str
    entity_id: str
    area: float
    minimum_span: float
    recommendation: str


def _polygon_area(points: Sequence[Point]) -> float:
    return abs(sum(a.x * b.y - b.x * a.y for a, b in zip(points, (*points[1:], points[0])))) / 2.0 if len(points) >= 3 else 0.0


def entity_area(entity: Entity) -> float:
    if isinstance(entity, Circle):
        return math.pi * entity.radius * entity.radius
    if isinstance(entity, Rectangle):
        return abs((entity.corner_b.x - entity.corner_a.x) * (entity.corner_b.y - entity.corner_a.y))
    if isinstance(entity, Polyline) and entity.closed:
        return _polygon_area(entity.points)
    return 0.0


def small_part_risks(project: CamProject, *, maximum_area: float, maximum_span: float) -> list[SmallPartRisk]:
    entities = {entity.id: entity for entity in project.entities}
    risks = []
    for operation in project.operations:
        if not operation.enabled or not isinstance(operation, RouterOffsetOperation):
            continue
        for entity_id in operation.entity_ids:
            side = operation.entity_sides.get(entity_id, operation.side.value)
            if side != OffsetSide.OUTSIDE.value:
                continue
            entity = entities.get(entity_id)
            bounds = entity_bounds(entity) if entity is not None else None
            if bounds is None:
                continue
            area = entity_area(entity)
            span = min(bounds[2] - bounds[0], bounds[3] - bounds[1])
            if area > 0 and (area <= maximum_area or span <= maximum_span):
                recommendation = "Add tabs or onion skin, or run this profile before surrounding parts release."
                risks.append(SmallPartRisk(operation.id, entity_id, area, span, recommendation))
    return risks


def output_verification_rows(
    project: CamProject,
    *,
    material: str,
    thickness: float,
    plate_count: int,
    material_detail: str = "",
    material_batch: str = "",
    operations=None,
) -> list[tuple[str, str]]:
    """Build the operator checkpoint, optionally limited to operations actually being output."""
    source_operations = project.operations if operations is None else operations
    enabled = ordered_operations([operation for operation in source_operations if operation.enabled])
    stations = sorted({operation.tool_station for operation in enabled})
    changes = sum(a.tool_station != b.tool_station for a, b in zip(enabled, enabled[1:]))
    maximum_depth = max((operation.target_depth for operation in enabled), default=0.0)
    minimum_safe_z = min((operation.safe_z for operation in enabled), default=0.0)
    disabled = [operation.name for operation in enabled if (project.tool_library.by_station(operation.tool_station) and project.tool_library.by_station(operation.tool_station).spindle_mode == "disabled")]
    rows = [
        ("Material", material or "Not specified"),
        ("Grade / construction", material_detail or "Not specified"),
        ("Batch / lot", material_batch or "Not recorded"),
        ("Thickness", f"{thickness:g} {project.units}"),
        ("Plates", str(plate_count)),
        ("Enabled operations", str(len(enabled))),
        ("Tool stations", ", ".join(f"T{station}" for station in stations) or "None"),
        ("Tool changes", str(changes)),
    ]
    if enabled:
        for index, operation in enumerate(enabled, start=1):
            rows.append((f"Operation {index}", f"T{operation.tool_station} — {operation.name}"))
            rows.append((
                f"Recipe {index}",
                operation.recipe_name.strip() or "Custom / no saved recipe",
            ))
    else:
        rows.append(("Operation order", "None"))
    rows.extend([
        ("Maximum depth", f"{maximum_depth:g} {project.units}"),
        ("Minimum safe Z", f"{minimum_safe_z:g} {project.units}"),
        ("Spindle-off operations", ", ".join(disabled) or "None"),
        (
            "Cycle optimizer",
            _cycle_optimizer_summary(project.metadata.get("cycle_optimization", {})),
        ),
        ("Origin", "Each plate lower-left becomes X0/Y0; output coordinates remain positive"),
    ])
    return rows


def _cycle_optimizer_summary(payload) -> str:
    if not isinstance(payload, dict) or not payload.get("enabled"):
        return "Off"
    route_labels = {
        "generated": "generated profile route",
        "nearest_safe": "nearest-safe profile route",
        "y_serpentine": "Y-column profile route",
    }
    labels = (
        ("depth_linking", "depth links"),
        ("profile_ordering", "profile order"),
        ("drill_ordering", "drill order"),
        ("engraving_ordering", "engraving order"),
        ("minimum_retract", "low retracts"),
        ("motion_compaction", "motion compaction"),
        ("arc_fitting", "arc fitting"),
    )
    enabled = [label for key, label in labels if payload.get(key, True)]
    if payload.get("profile_ordering", True):
        enabled.append(route_labels.get(
            str(payload.get("profile_travel_strategy", "generated")),
            "generated profile route",
        ))
    production = all(
        payload.get(key) == expected
        for key, expected in PRODUCTION_OPTIMIZATION_BEHAVIORS.items()
    )
    prefix = "Production optimized" if production else "Custom optimization"
    return prefix + (f" — {', '.join(enabled)}" if enabled else "")
