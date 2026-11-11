from __future__ import annotations

"""Associative, review-first sign mounting feature generation.

The original Letter Mounting Automation project proved the shop rules for
studs, backer pins, keyholes, and installation patterns.  This module keeps
those rules UI-independent while adapting them to CAM Workbench's persistent
entity model.  Proposals are harmless until applied; applied features retain a
stable part identity so ordinary move/copy/rotate/nest operations cannot lose
registration.
"""

from dataclasses import dataclass, field
import math
from typing import Iterable
from uuid import uuid4

from .layout import drawing_bounds, entity_bounds, entity_perimeter
from .model import CamProject, Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity
from .selection import is_closed_entity, is_machinable_entity
from .topology import profile_containers


LAYER_STUD_HOLES = "MOUNTING_HOLES"
LAYER_LETTER_PIN_HOLES = "LETTER_PIN_HOLES"
LAYER_BACKER_CLEARANCE_HOLES = "BACKER_CLEARANCE_HOLES"
LAYER_KEYHOLES = "KEYHOLES"
LAYER_ELECTRICAL_HOLES = "ELECTRICAL_HOLES"
LAYER_PATTERN_GUIDES = "PATTERN_GUIDES"
LAYER_PATTERN_LABEL = "PATTERN_LABEL"

MOUNTING_LAYER_COLORS = {
    LAYER_STUD_HOLES: "#9c36b5",
    LAYER_LETTER_PIN_HOLES: "#9c36b5",
    LAYER_BACKER_CLEARANCE_HOLES: "#1971c2",
    LAYER_KEYHOLES: "#2f9e44",
    LAYER_ELECTRICAL_HOLES: "#1971c2",
    LAYER_PATTERN_GUIDES: "#868e96",
    LAYER_PATTERN_LABEL: "#495057",
}


@dataclass
class MountingPart:
    owner_entity_id: str
    part_id: str
    member_ids: tuple[str, ...]
    loops: tuple[tuple[Point, ...], ...]
    bounds: tuple[float, float, float, float]
    label: str


@dataclass
class MountingFeatureSpec:
    feature_key: str
    owner_entity_id: str
    owner_part_id: str
    kind: str
    center: Point
    layer: str
    diameter: float = 0.0
    points: tuple[Point, ...] = ()
    text: str = ""
    height: float = 0.0
    rotation: float = 0.0
    manual_edit: bool = False
    existing_entity_ids: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass
class MountingProposal:
    workflow: str
    scope_key: str
    parts: tuple[MountingPart, ...]
    features: list[MountingFeatureSpec]
    warnings: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    source_entity_ids: tuple[str, ...] = ()

    @property
    def feature_count(self) -> int:
        return len(self.features)


@dataclass(frozen=True)
class MountingApplyResult:
    added: int
    replaced: int
    preserved_manual: int
    feature_entity_ids: tuple[str, ...]


def _loop_points(entity: Entity, circle_segments: int = 72) -> tuple[Point, ...] | None:
    if isinstance(entity, Circle):
        return tuple(
            Point(
                entity.center.x + math.cos(2.0 * math.pi * index / circle_segments) * entity.radius,
                entity.center.y + math.sin(2.0 * math.pi * index / circle_segments) * entity.radius,
            )
            for index in range(circle_segments)
        )
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return (
            Point(min_x, min_y), Point(max_x, min_y),
            Point(max_x, max_y), Point(min_x, max_y),
        )
    if isinstance(entity, Polyline) and entity.closed and len(entity.points) >= 3:
        return tuple(entity.points)
    return None


def _point_in_loop(point: Point, loop: tuple[Point, ...]) -> bool:
    inside = False
    previous = loop[-1]
    for current in loop:
        if (current.y > point.y) != (previous.y > point.y):
            crossing_x = (
                (previous.x - current.x) * (point.y - current.y)
                / (previous.y - current.y) + current.x
            )
            if point.x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _distance_to_segment(point: Point, first: Point, second: Point) -> float:
    dx = second.x - first.x
    dy = second.y - first.y
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return point.distance_to(first)
    fraction = max(0.0, min(1.0, ((point.x - first.x) * dx + (point.y - first.y) * dy) / length_squared))
    return math.hypot(point.x - (first.x + fraction * dx), point.y - (first.y + fraction * dy))


def _boundary_distance(point: Point, loops: tuple[tuple[Point, ...], ...]) -> float:
    return min(
        _distance_to_segment(point, first, second)
        for loop in loops
        for first, second in zip(loop, loop[1:] + loop[:1])
    )


def _inside_material(point: Point, loops: tuple[tuple[Point, ...], ...]) -> bool:
    # Even/odd fill preserves counters in A/D/O/R and nested islands without
    # requiring Shapely in the main CAM runtime.
    return sum(_point_in_loop(point, loop) for loop in loops) % 2 == 1


def _entity_sample(entity: Entity) -> Point:
    bounds = entity_bounds(entity)
    if bounds is None:
        return Point(0.0, 0.0)
    if isinstance(entity, Circle):
        return entity.center
    loop = _loop_points(entity)
    if loop:
        # A point just inside the first edge is more dependable than the vertex
        # average for strongly concave imported lettering.
        first, second = loop[0], loop[1]
        midpoint = Point((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
        center = Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
        for fraction in (0.01, 0.03, 0.08, 0.20, 0.50):
            candidate = Point(
                midpoint.x + (center.x - midpoint.x) * fraction,
                midpoint.y + (center.y - midpoint.y) * fraction,
            )
            if _point_in_loop(candidate, loop):
                return candidate
    return Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)


def _contains_entity(outer: Entity, inner: Entity) -> bool:
    outer_loop = _loop_points(outer)
    outer_bounds = entity_bounds(outer)
    inner_bounds = entity_bounds(inner)
    if outer_loop is None or outer_bounds is None or inner_bounds is None:
        return False
    if not (
        outer_bounds[0] <= inner_bounds[0] + 1e-7
        and outer_bounds[1] <= inner_bounds[1] + 1e-7
        and outer_bounds[2] >= inner_bounds[2] - 1e-7
        and outer_bounds[3] >= inner_bounds[3] - 1e-7
    ):
        return False
    return _point_in_loop(_entity_sample(inner), outer_loop)


def _part_label(entity: Entity, index: int) -> str:
    for key in ("mounting_label", "text_character", "character", "part_name", "name"):
        value = str(entity.metadata.get(key, "")).strip()
        if value:
            return value
    return f"Part {index}"


def _stable_part_id(entity: Entity) -> str:
    return str(entity.metadata.get("mounting_part_id") or f"mounting_part_{entity.id}")


def discover_mounting_parts(
    entities: Iterable[Entity], selected_ids: set[str],
) -> tuple[MountingPart, ...]:
    """Turn selected outer contours into material regions with their counters."""
    all_entities = list(entities)
    closed = [
        entity for entity in all_entities
        if is_closed_entity(entity)
        and not entity.metadata.get("mounting_generated")
        and not entity.metadata.get("mounting_pattern_generated")
    ]
    selected = [entity for entity in closed if entity.id in selected_ids]
    if not selected:
        raise ValueError("Select one or more closed letter or sign contours first.")

    # A selected inner counter does not suppress a selected outer letter.
    roots = [
        entity for entity in selected
        if not any(other.id != entity.id and _contains_entity(other, entity) for other in selected)
    ]
    roots.sort(key=lambda entity: (entity_bounds(entity) or (0, 0, 0, 0))[0:2])
    parts: list[MountingPart] = []
    for index, root in enumerate(roots, start=1):
        members = [root]
        for candidate in closed:
            if candidate.id == root.id:
                continue
            if _contains_entity(root, candidate):
                members.append(candidate)
        # Do not absorb another selected top-level part merely because its
        # bounds are inside a large sign outline; each selected root owns itself.
        other_root_ids = {item.id for item in roots if item.id != root.id}
        members = [item for item in members if item.id not in other_root_ids]
        loops = tuple(loop for item in members if (loop := _loop_points(item)) is not None)
        bounds = entity_bounds(root)
        if bounds is None or not loops:
            continue
        parts.append(MountingPart(
            owner_entity_id=root.id,
            part_id=_stable_part_id(root),
            member_ids=tuple(item.id for item in members),
            loops=loops,
            bounds=bounds,
            label=_part_label(root, index),
        ))
    if not parts:
        raise ValueError("The selection does not contain a usable closed mounting region.")
    return tuple(parts)


def _candidate_points(
    part: MountingPart, edge_clearance: float, units_per_inch: float = 1.0,
) -> list[tuple[Point, float]]:
    min_x, min_y, max_x, max_y = part.bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        return []
    step = max(0.04 * units_per_inch, min(0.20 * units_per_inch, min(width, height) / 45.0))
    columns = max(1, math.ceil(width / step))
    rows = max(1, math.ceil(height / step))
    result: list[tuple[Point, float]] = []
    for row in range(rows + 1):
        y = min_y + height * row / rows
        for column in range(columns + 1):
            x = min_x + width * column / columns
            point = Point(x, y)
            if not _inside_material(point, part.loops):
                continue
            clearance = _boundary_distance(point, part.loops)
            if clearance + 1e-9 >= edge_clearance:
                result.append((point, clearance))
    return result


def _normalized_targets(label: str, count: int) -> list[tuple[float, float]]:
    upper = label if len(label) == 1 and label.isupper() else ""
    if count == 1:
        return [(0.50, 0.50)]
    if count == 2:
        return [(0.50, 0.76), (0.50, 0.24)] if upper in {"I", "!"} else [(0.32, 0.72), (0.68, 0.28)]
    if count == 3:
        return {
            "A": [(0.50, 0.76), (0.18, 0.20), (0.82, 0.20)],
            "T": [(0.20, 0.78), (0.80, 0.78), (0.50, 0.20)],
            "F": [(0.15, 0.78), (0.80, 0.78), (0.15, 0.20)],
            "C": [(0.72, 0.84), (0.16, 0.50), (0.70, 0.16)],
            "L": [(0.30, 0.82), (0.24, 0.20), (0.78, 0.16)],
        }.get(upper, [(0.24, 0.76), (0.76, 0.76), (0.50, 0.20)])
    columns = math.ceil(count / 2)
    xs = [0.18 + 0.64 * index / max(columns - 1, 1) for index in range(columns)]
    return ([(x, 0.78) for x in xs] + [(x, 0.22) for x in reversed(xs)])[:count]


def _pick_points(
    part: MountingPart,
    count: int,
    edge_clearance: float,
    minimum_spacing: float,
    units_per_inch: float = 1.0,
) -> tuple[list[Point], str | None]:
    candidates = _candidate_points(part, edge_clearance, units_per_inch)
    return _pick_scored_points(part, count, edge_clearance, minimum_spacing, candidates)


def _pick_scored_points(
    part: MountingPart,
    count: int,
    edge_clearance: float,
    minimum_spacing: float,
    candidates: list[tuple[Point, float]],
) -> tuple[list[Point], str | None]:
    # Candidate order and the original score/tie breakers are significant.
    # Callers may reuse scores from this exact part/grid at a lower clearance.
    candidates = [item for item in candidates if item[1] + 1e-9 >= edge_clearance]
    if not candidates:
        return [], f"{part.label}: no point meets the requested edge clearance."
    min_x, min_y, max_x, max_y = part.bounds
    targets = _normalized_targets(part.label, count)
    chosen: list[Point] = []
    for normalized_x, normalized_y in targets:
        target = Point(min_x + (max_x - min_x) * normalized_x, min_y + (max_y - min_y) * normalized_y)
        allowed = [
            item for item in candidates
            if all(item[0].distance_to(existing) + 1e-9 >= minimum_spacing for existing in chosen)
        ]
        if not allowed:
            break
        point, _clearance = min(
            allowed,
            key=lambda item: (item[0].distance_to(target) - min(item[1], edge_clearance * 2.0) * 0.12, item[0].x, item[0].y),
        )
        chosen.append(point)
    warning = None
    if len(chosen) < count:
        warning = f"{part.label}: fit {len(chosen)} of {count} requested mounts at the current clearance and spacing."
    return chosen, warning


def _existing_reviewed_features(
    entities: Iterable[Entity], workflow: str, owner_part_id: str,
) -> dict[str, Entity]:
    result: dict[str, Entity] = {}
    for entity in entities:
        if not (
            entity.metadata.get("mounting_generated")
            and entity.metadata.get("mounting_workflow") == workflow
            and entity.metadata.get("mounting_part_id") == owner_part_id
            and entity.metadata.get("mounting_manual_edit")
            and entity.metadata.get("mounting_feature_key")
        ):
            continue
        key = str(entity.metadata["mounting_feature_key"])
        if key not in result or entity.metadata.get("mounting_component") == "outer":
            result[key] = entity
    return result


def _restore_reviewed(spec: MountingFeatureSpec, existing: Entity | None) -> MountingFeatureSpec:
    if existing is None:
        return spec
    if isinstance(existing, Circle):
        spec.center = existing.center
        spec.diameter = existing.radius * 2.0
    elif isinstance(existing, Polyline):
        bounds = entity_bounds(existing)
        if bounds is not None:
            spec.center = Point((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
            spec.points = tuple(existing.points)
    spec.manual_edit = True
    spec.existing_entity_ids = (existing.id,)
    if "registered_center" in spec.metadata:
        spec.metadata["registered_center"] = [spec.center.x, spec.center.y]
    return spec


def generate_stud_hole_proposal(
    entities: Iterable[Entity],
    selected_ids: set[str],
    *,
    diameter: float = 0.230,
    edge_clearance: float = 0.600,
    minimum_spacing: float = 1.000,
    units_per_inch: float = 1.0,
) -> MountingProposal:
    if diameter <= 0 or edge_clearance < diameter / 2 or minimum_spacing < 0:
        raise ValueError("Stud diameter must be positive and center clearance must be at least the hole radius.")
    all_entities = list(entities)
    parts = discover_mounting_parts(all_entities, selected_ids)
    features: list[MountingFeatureSpec] = []
    warnings: list[str] = []
    canonical: dict[tuple, list[tuple[float, float]]] = {}
    for part in parts:
        width = part.bounds[2] - part.bounds[0]
        height = part.bounds[3] - part.bounds[1]
        largest = max(width, height)
        count = 1 if largest < 6.0 * units_per_inch else 3
        if part.label in {",", "'", "`", ";", ":"}:
            count = 2
        signature = (
            round(width, 3), round(height, 3),
            round(sum(entity_perimeter(item) for item in all_entities if item.id in part.member_ids), 3),
            len(part.member_ids), part.label if len(part.label) == 1 else "",
        )
        if signature in canonical:
            points = [Point(part.bounds[0] + x, part.bounds[1] + y) for x, y in canonical[signature]]
            warning = None
        else:
            points, warning = _pick_points(part, count, edge_clearance, minimum_spacing, units_per_inch)
            canonical[signature] = [(point.x - part.bounds[0], point.y - part.bounds[1]) for point in points]
        if warning:
            warnings.append(warning)
        reviewed = _existing_reviewed_features(all_entities, "stud_holes", part.part_id)
        for index, point in enumerate(points, start=1):
            key = f"{part.part_id}:stud:{index}"
            spec = MountingFeatureSpec(
                feature_key=key,
                owner_entity_id=part.owner_entity_id,
                owner_part_id=part.part_id,
                kind="stud_hole",
                center=point,
                layer=LAYER_STUD_HOLES,
                diameter=diameter,
            )
            features.append(_restore_reviewed(spec, reviewed.get(key)))
    return MountingProposal(
        workflow="stud_holes",
        scope_key="stud_holes:" + ",".join(sorted(part.part_id for part in parts)),
        parts=parts,
        features=features,
        warnings=warnings,
        settings={"diameter": diameter, "edge_clearance": edge_clearance, "minimum_spacing": minimum_spacing},
        source_entity_ids=tuple(dict.fromkeys(member for part in parts for member in part.member_ids)),
    )


def _keyhole_outline(center: Point, units_per_inch: float = 1.0) -> tuple[Point, ...]:
    # Production template: 0.563 wide x 1.063 high, 0.250 stem, with a
    # 0.188 hanger/pilot circle near the top.  The outer outline below matches
    # the proven template footprint used by the standalone generator.
    points = [
        Point(center.x - 0.125 * units_per_inch, center.y + 0.53125 * units_per_inch),
        Point(center.x + 0.125 * units_per_inch, center.y + 0.53125 * units_per_inch),
        Point(center.x + 0.125 * units_per_inch, center.y + 0.002 * units_per_inch),
    ]
    bulb_center = Point(center.x, center.y - 0.250 * units_per_inch)
    start_angle = math.asin(0.125 / 0.2815)
    for index in range(29):
        # Travel around the long/lower arc from the right stem shoulder to the
        # left shoulder; the short upper arc is replaced by the straight stem.
        angle = start_angle + (2.0 * math.pi - 2.0 * start_angle) * index / 28.0
        points.append(Point(
            bulb_center.x + math.sin(angle) * 0.2815 * units_per_inch,
            bulb_center.y + math.cos(angle) * 0.2815 * units_per_inch,
        ))
    points.append(Point(center.x - 0.125 * units_per_inch, center.y + 0.002 * units_per_inch))
    return tuple(points)


def generate_keyhole_proposal(
    entities: Iterable[Entity],
    selected_ids: set[str],
    *,
    edge_clearance: float = 0.625,
    minimum_spacing: float = 1.250,
    round_fallback_diameter: float = 0.250,
    include_electrical: bool = True,
    electrical_diameter: float = 0.375,
    units_per_inch: float = 1.0,
) -> MountingProposal:
    if edge_clearance < 0 or minimum_spacing < 0 or round_fallback_diameter <= 0:
        raise ValueError("Keyhole clearances and spacing must be non-negative; fallback diameter must be positive.")
    all_entities = list(entities)
    parts = discover_mounting_parts(all_entities, selected_ids)
    features: list[MountingFeatureSpec] = []
    warnings: list[str] = []
    for part in parts:
        width = part.bounds[2] - part.bounds[0]
        height = part.bounds[3] - part.bounds[1]
        largest = max(width, height)
        count = 2 if largest < 4.0 * units_per_inch or width < height * 0.35 else 5 if largest >= 60 * units_per_inch else 4 if largest >= 36 * units_per_inch else 3
        # A keyhole needs its full half-width plus the coil/edge gap.  If a
        # letter cannot accept that footprint, retain the standalone tool's
        # explicit round anti-rotation fallback.
        keyhole_center_clearance = edge_clearance + 0.2815 * units_per_inch
        round_clearance = edge_clearance + round_fallback_diameter / 2.0
        electrical_clearance = edge_clearance + electrical_diameter / 2.0
        minimum_clearance = min(keyhole_center_clearance, round_clearance,
                                electrical_clearance if include_electrical else keyhole_center_clearance)
        # Boundary scoring dominates proposal time. Reuse it only inside this
        # part's generation, never across edits, units, owners or proposals.
        candidates = _candidate_points(part, minimum_clearance, units_per_inch)
        points, warning = _pick_scored_points(part, count, keyhole_center_clearance, minimum_spacing, candidates)
        kind = "keyhole"
        round_points, round_warning = _pick_scored_points(
            part, count, round_clearance,
            min(minimum_spacing, units_per_inch if largest < 4 * units_per_inch else minimum_spacing),
            candidates,
        )
        if len(round_points) > len(points):
            kind = "round_mount"
            points, warning = round_points, round_warning
        if warning:
            warnings.append(warning)
        reviewed = _existing_reviewed_features(all_entities, "keyholes", part.part_id)
        for index, point in enumerate(points, start=1):
            feature_kind = kind
            feature_points = _keyhole_outline(point, units_per_inch) if feature_kind == "keyhole" else ()
            spec = MountingFeatureSpec(
                feature_key=f"{part.part_id}:mount:{index}", owner_entity_id=part.owner_entity_id,
                owner_part_id=part.part_id, kind=feature_kind, center=point,
                layer=LAYER_KEYHOLES if feature_kind == "keyhole" else LAYER_STUD_HOLES,
                diameter=round_fallback_diameter if feature_kind == "round_mount" else 0.1875 * units_per_inch,
                points=feature_points, metadata={"keyhole_units_per_inch": units_per_inch},
            )
            features.append(_restore_reviewed(spec, reviewed.get(spec.feature_key)))
        if include_electrical:
            electrical_points, electrical_warning = _pick_scored_points(
                part, 1, electrical_clearance, minimum_spacing=0.0, candidates=candidates,
            )
            if electrical_points:
                # Prefer a candidate away from every mount feature.
                point = max(
                    (item[0] for item in candidates if item[1] + 1e-9 >= electrical_clearance),
                    key=lambda candidate: min((candidate.distance_to(mount) for mount in points), default=0.0),
                )
                spec = MountingFeatureSpec(
                    feature_key=f"{part.part_id}:electrical:1", owner_entity_id=part.owner_entity_id,
                    owner_part_id=part.part_id, kind="electrical_hole", center=point,
                    layer=LAYER_ELECTRICAL_HOLES, diameter=electrical_diameter,
                )
                features.append(_restore_reviewed(spec, reviewed.get(spec.feature_key)))
            elif electrical_warning:
                warnings.append(electrical_warning.replace("mounts", "electrical hole"))
        del candidates  # Do not retain this grid while building the next part's.
    return MountingProposal(
        workflow="keyholes", scope_key="keyholes:" + ",".join(sorted(part.part_id for part in parts)),
        parts=parts, features=features, warnings=warnings,
        settings={
            "edge_clearance": edge_clearance, "minimum_spacing": minimum_spacing,
            "round_fallback_diameter": round_fallback_diameter,
            "include_electrical": include_electrical, "electrical_diameter": electrical_diameter,
        },
        source_entity_ids=tuple(dict.fromkeys(member for part in parts for member in part.member_ids)),
    )


def generate_backer_pin_proposal(
    entities: Iterable[Entity],
    selected_ids: set[str],
    *,
    letter_hole_diameter: float = 0.125,
    backer_clearance_diameter: float = 0.140,
    edge_material: float = 0.125,
    minimum_spacing: float = 0.500,
    units_per_inch: float = 1.0,
) -> MountingProposal:
    sizes = (letter_hole_diameter, backer_clearance_diameter, edge_material, minimum_spacing, units_per_inch)
    if (not all(math.isfinite(value) for value in sizes)
            or min(letter_hole_diameter, backer_clearance_diameter, units_per_inch) <= 0
            or min(edge_material, minimum_spacing) < 0):
        raise ValueError("Pin diameters must be positive and finite; edge material and spacing cannot be negative.")
    all_entities = list(entities)
    closed = [entity for entity in all_entities if is_closed_entity(entity)
              and is_machinable_entity(entity) and not entity.metadata.get("mounting_generated")
              and not entity.metadata.get("mounting_pattern_generated")]
    selected_closed = [
        entity for entity in closed if entity.id in selected_ids
    ]
    if len(selected_closed) < 2:
        raise ValueError("Select a backer outline and the letter outlines registered inside it.")
    containers = profile_containers(selected_closed)
    roots = [entity for entity in selected_closed if not containers.get(entity.id)]
    if not roots:
        raise ValueError("The selected geometry does not contain a recognizable backer outline.")
    if len(roots) != 1:
        raise ValueError("Select one backer and its registered letters at a time; other selected outlines lie outside it.")
    backer = max(roots, key=lambda entity: abs((entity_bounds(entity)[2] - entity_bounds(entity)[0]) * (entity_bounds(entity)[3] - entity_bounds(entity)[1])))
    letter_roots = [
        entity for entity in selected_closed
        if backer.id in containers.get(entity.id, []) and len(containers.get(entity.id, [])) == 1
    ]
    if not letter_roots:
        raise ValueError("No selected letter outlines were found directly inside the selected backer.")
    scope_key = f"backer_pins:{_stable_part_id(backer)}"
    prior_letter_owners = {
        str(entity.metadata.get("mounting_owner_entity_id", "")) for entity in all_entities
        if entity.metadata.get("mounting_scope_key") == scope_key
        and entity.layer == LAYER_LETTER_PIN_HOLES
    }
    if prior_letter_owners - {entity.id for entity in letter_roots}:
        raise ValueError("Select all previously registered letters with this backer before regenerating its pin map.")
    backer_part = MountingPart(
        owner_entity_id=backer.id, part_id=_stable_part_id(backer), member_ids=(backer.id,),
        loops=(_loop_points(backer),), bounds=entity_bounds(backer), label=_part_label(backer, 1),
    )
    letter_parts: list[MountingPart] = []
    all_containers = profile_containers(closed)
    for index, root in enumerate(letter_roots, start=1):
        members = [root] + [
            entity for entity in closed
            if root.id in all_containers.get(entity.id, []) and entity.id != root.id
        ]
        letter_parts.append(MountingPart(
            owner_entity_id=root.id, part_id=_stable_part_id(root),
            member_ids=tuple(item.id for item in members),
            loops=tuple(_loop_points(item) for item in members if _loop_points(item) is not None),
            bounds=entity_bounds(root), label=_part_label(root, index),
        ))
    features: list[MountingFeatureSpec] = []
    warnings: list[str] = []
    for part in letter_parts:
        largest = max(part.bounds[2] - part.bounds[0], part.bounds[3] - part.bounds[1])
        count = 2 if largest < 6.0 * units_per_inch else max(3, min(6, math.ceil(largest / (10.0 * units_per_inch)) + 2))
        points, warning = _pick_points(
            part, count, letter_hole_diameter / 2.0 + edge_material, minimum_spacing, units_per_inch,
        )
        if warning:
            warnings.append(warning)
        reviewed_letter = _existing_reviewed_features(all_entities, "backer_pins", part.part_id)
        reviewed_backer = _existing_reviewed_features(all_entities, "backer_pins", backer_part.part_id)
        for index, point in enumerate(points, start=1):
            pair_id = f"pin_pair_{part.part_id}_{index}"
            common = {"mounting_pair_id": pair_id, "registered_center": [point.x, point.y]}
            letter_spec = MountingFeatureSpec(
                feature_key=f"{part.part_id}:letter_pin:{index}", owner_entity_id=part.owner_entity_id,
                owner_part_id=part.part_id, kind="letter_pin_hole", center=point,
                layer=LAYER_LETTER_PIN_HOLES, diameter=letter_hole_diameter, metadata=dict(common),
            )
            features.append(_restore_reviewed(letter_spec, reviewed_letter.get(letter_spec.feature_key)))
            backer_spec = MountingFeatureSpec(
                feature_key=f"{backer_part.part_id}:backer_pin:{part.part_id}:{index}",
                owner_entity_id=backer_part.owner_entity_id, owner_part_id=backer_part.part_id,
                kind="backer_clearance_hole", center=point, layer=LAYER_BACKER_CLEARANCE_HOLES,
                diameter=backer_clearance_diameter, metadata=dict(common),
            )
            features.append(_restore_reviewed(backer_spec, reviewed_backer.get(backer_spec.feature_key)))
    # Bring manual additions back into the editable review, not just silently
    # preserve them at Apply. Auto-key edits above have already been restored.
    keys = {feature.feature_key for feature in features}
    for part in (backer_part, *letter_parts):
        for key, entity in _existing_reviewed_features(all_entities, "backer_pins", part.part_id).items():
            if key in keys or not isinstance(entity, Circle) or entity.metadata.get("mounting_scope_key") != scope_key:
                continue
            features.append(MountingFeatureSpec(
                feature_key=key, owner_entity_id=part.owner_entity_id, owner_part_id=part.part_id,
                kind="backer_clearance_hole" if part is backer_part else "letter_pin_hole",
                center=entity.center, layer=entity.layer, diameter=entity.radius * 2,
                manual_edit=True, existing_entity_ids=(entity.id,), metadata={
                    "mounting_pair_id": entity.metadata.get("mounting_pair_id", ""),
                    "registered_center": [entity.center.x, entity.center.y],
                },
            ))
    deleted = list(backer.metadata.get("backer_pin_deleted_feature_keys", ()))
    features = [feature for feature in features if feature.feature_key not in deleted]
    proposal = MountingProposal(
        workflow="backer_pins",
        scope_key=scope_key,
        parts=(backer_part, *letter_parts), features=features, warnings=warnings,
        settings={
            "letter_hole_diameter": letter_hole_diameter,
            "backer_clearance_diameter": backer_clearance_diameter,
            "edge_material": edge_material, "minimum_spacing": minimum_spacing,
            "_delete_feature_keys": deleted,
        },
        source_entity_ids=tuple(dict.fromkeys(member for part in (backer_part, *letter_parts) for member in part.member_ids)),
    )
    validate_backer_pin_proposal(proposal)
    return proposal


def validate_backer_pin_proposal(proposal: MountingProposal) -> None:
    """Validate a reviewed pin map before any production geometry is mutated."""
    if proposal.workflow != "backer_pins":
        return
    parts = {part.part_id: part for part in proposal.parts}
    pairs: dict[str, list[MountingFeatureSpec]] = {}
    owner_features: dict[str, list[MountingFeatureSpec]] = {}
    for feature in proposal.features:
        part = parts.get(feature.owner_part_id)
        if (part is None or not all(math.isfinite(value) for value in (
                feature.center.x, feature.center.y, feature.diameter)) or feature.diameter <= 0):
            raise ValueError("Pin holes need a valid owner, finite center, and positive diameter.")
        clearance = feature.diameter / 2 + (
            float(proposal.settings.get("edge_material", 0)) if feature.kind == "letter_pin_hole" else 0)
        if (not _inside_material(feature.center, part.loops)
                or _boundary_distance(feature.center, part.loops) + 1e-7 < clearance):
            raise ValueError(f"{part.label}: pin hole crosses the material edge/counter or leaves insufficient edge material.")
        pair_id = str(feature.metadata.get("mounting_pair_id", ""))
        pairs.setdefault(pair_id, []).append(feature)
        for other in owner_features.setdefault(feature.owner_part_id, []):
            separation = max(float(proposal.settings.get("minimum_spacing", 0)),
                             (feature.diameter + other.diameter) / 2)
            if feature.center.distance_to(other.center) + 1e-7 < separation:
                raise ValueError(f"{part.label}: pin holes overlap or violate the minimum spacing.")
        owner_features[feature.owner_part_id].append(feature)
    for pair_id, pair in pairs.items():
        if (not pair_id or len(pair) != 2
                or {feature.kind for feature in pair} != {"letter_pin_hole", "backer_clearance_hole"}
                or pair[0].center.distance_to(pair[1].center) > 1e-7):
            raise ValueError("Letter/backer pin centers are no longer registered. Restore the assembled positions before regeneration.")


def generate_installation_pattern_proposal(
    entities: Iterable[Entity],
    selected_ids: set[str] | None = None,
    *,
    label: str = "INSTALLATION PATTERN",
    guide_style: str = "continuous",
    margin: float = 0.250,
    units_per_inch: float = 1.0,
) -> MountingProposal:
    all_entities = list(entities)
    selected_ids = set(selected_ids or ())
    selected_part_ids = {
        str(entity.metadata.get("mounting_part_id"))
        for entity in all_entities if entity.id in selected_ids and entity.metadata.get("mounting_part_id")
    }
    if selected_ids:
        source = [
            entity for entity in all_entities
            if entity.id in selected_ids or (
                entity.metadata.get("mounting_part_id")
                and str(entity.metadata.get("mounting_part_id")) in selected_part_ids
            )
        ]
    else:
        mounted_part_ids = {
            str(entity.metadata.get("mounting_part_id"))
            for entity in all_entities if entity.metadata.get("mounting_generated")
        }
        source = [
            entity for entity in all_entities
            if str(entity.metadata.get("mounting_part_id", "")) in mounted_part_ids
        ]
    source = [entity for entity in source if not entity.metadata.get("mounting_pattern_generated")]
    bounds = drawing_bounds(source, include_annotations=False)
    if bounds is None:
        raise ValueError("Generate mounting features, or select mounted geometry, before creating a pattern.")
    min_x, min_y, max_x, max_y = bounds
    min_x -= margin
    min_y -= margin
    max_x += margin
    max_y += margin
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    stable_scope_ids = selected_part_ids or {
        str(entity.metadata.get("mounting_part_id"))
        for entity in source if entity.metadata.get("mounting_part_id")
    } or {entity.id for entity in source}
    scope_key = "installation_pattern:" + ",".join(sorted(stable_scope_ids))
    features = [
        MountingFeatureSpec(
            feature_key=f"{scope_key}:frame", owner_entity_id="", owner_part_id="",
            kind="pattern_frame", center=Point(center_x, center_y), layer=LAYER_PATTERN_GUIDES,
            points=(Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)),
        ),
    ]
    if guide_style == "physical_dashes":
        dash = min(1.0 * units_per_inch, max(0.25 * units_per_inch, (max_x - min_x) / 20.0))
        features.extend([
            MountingFeatureSpec(
                feature_key=f"{scope_key}:h:{index}", owner_entity_id="", owner_part_id="",
                kind="pattern_guide", center=Point(x + dash / 2.0, center_y), layer=LAYER_PATTERN_GUIDES,
                points=(Point(x, center_y), Point(min(x + dash, max_x), center_y)),
            )
            for index, x in enumerate(_frange(min_x, max_x, dash * 2.0), start=1)
        ])
        features.extend([
            MountingFeatureSpec(
                feature_key=f"{scope_key}:v:{index}", owner_entity_id="", owner_part_id="",
                kind="pattern_guide", center=Point(center_x, y + dash / 2.0), layer=LAYER_PATTERN_GUIDES,
                points=(Point(center_x, y), Point(center_x, min(y + dash, max_y))),
            )
            for index, y in enumerate(_frange(min_y, max_y, dash * 2.0), start=1)
        ])
    else:
        features.extend([
            MountingFeatureSpec(
                feature_key=f"{scope_key}:horizontal", owner_entity_id="", owner_part_id="",
                kind="pattern_guide", center=Point(center_x, center_y), layer=LAYER_PATTERN_GUIDES,
                points=(Point(min_x, center_y), Point(max_x, center_y)),
            ),
            MountingFeatureSpec(
                feature_key=f"{scope_key}:vertical", owner_entity_id="", owner_part_id="",
                kind="pattern_guide", center=Point(center_x, center_y), layer=LAYER_PATTERN_GUIDES,
                points=(Point(center_x, min_y), Point(center_x, max_y)),
            ),
        ])
    features.append(MountingFeatureSpec(
        feature_key=f"{scope_key}:label", owner_entity_id="", owner_part_id="",
        kind="pattern_label", center=Point(max_x + margin, min_y), layer=LAYER_PATTERN_LABEL,
        text=label, height=max(0.25 * units_per_inch, min(1.0 * units_per_inch, (max_y - min_y) / 20.0)), rotation=90.0,
        metadata={"pattern_source_entity_ids": [entity.id for entity in source]},
    ))
    return MountingProposal(
        workflow="installation_pattern", scope_key=scope_key, parts=(), features=features,
        settings={"label": label, "guide_style": guide_style, "margin": margin},
        source_entity_ids=tuple(entity.id for entity in source),
    )


def _frange(start: float, stop: float, step: float):
    value = start
    while value < stop - 1e-9:
        yield value
        value += step


def _feature_entities(spec: MountingFeatureSpec, workflow: str, scope_key: str) -> list[Entity]:
    base_metadata = {
        "mounting_generated": True,
        "mounting_workflow": workflow,
        "mounting_scope_key": scope_key,
        "mounting_feature_key": spec.feature_key,
        "mounting_owner_entity_id": spec.owner_entity_id,
        "mounting_part_id": spec.owner_part_id,
        "mounting_manual_edit": bool(spec.manual_edit),
        **spec.metadata,
    }
    if workflow == "installation_pattern":
        base_metadata["mounting_pattern_generated"] = True
    if spec.kind in {"stud_hole", "letter_pin_hole", "backer_clearance_hole", "round_mount", "electrical_hole"}:
        return [Circle(layer=spec.layer, metadata=base_metadata, center=spec.center, radius=spec.diameter / 2.0)]
    if spec.kind == "keyhole":
        units_per_inch = float(spec.metadata.get("keyhole_units_per_inch", 1.0))
        outer_metadata = {**base_metadata, "mounting_component": "outer"}
        pilot_metadata = {**base_metadata, "mounting_component": "hanger_pilot"}
        return [
            Polyline(layer=spec.layer, metadata=outer_metadata, points=list(spec.points or _keyhole_outline(spec.center, units_per_inch)), closed=True),
            Circle(
                layer=spec.layer, metadata=pilot_metadata,
                center=Point(spec.center.x, spec.center.y + 0.4375 * units_per_inch),
                radius=0.09375 * units_per_inch,
            ),
        ]
    if spec.kind == "pattern_frame":
        return [Polyline(layer=spec.layer, metadata=base_metadata, points=list(spec.points), closed=True)]
    if spec.kind == "pattern_guide":
        return [Line(layer=spec.layer, metadata=base_metadata, start=spec.points[0], end=spec.points[1])]
    if spec.kind == "pattern_label":
        return [TextEntity(
            layer=spec.layer, metadata={**base_metadata, "annotation_hidden": False}, insertion=spec.center,
            text=spec.text, height=spec.height, rotation=spec.rotation, font_family="Arial",
        )]
    raise ValueError(f"Unsupported mounting feature kind: {spec.kind}")


def proposal_preview_entities(proposal: MountingProposal) -> list[Entity]:
    return [entity for spec in proposal.features for entity in _feature_entities(spec, proposal.workflow, proposal.scope_key)]


def apply_mounting_proposal(project: CamProject, proposal: MountingProposal) -> MountingApplyResult:
    """Apply one proposal idempotently while retaining reviewed/manual features."""
    validate_backer_pin_proposal(proposal)
    member_to_part = {
        member_id: part.part_id for part in proposal.parts for member_id in part.member_ids
    }
    owner_by_part = {part.part_id: part.owner_entity_id for part in proposal.parts}
    for entity in project.entities:
        part_id = member_to_part.get(entity.id)
        if part_id:
            entity.metadata["mounting_part_id"] = part_id
            entity.metadata["mounting_owner_entity_id"] = owner_by_part[part_id]
        if proposal.workflow == "backer_pins" and entity.id == proposal.parts[0].owner_entity_id:
            entity.metadata["backer_pin_deleted_feature_keys"] = list(proposal.settings.get("_delete_feature_keys", ()))

    deleted_keys = set(proposal.settings.get("_delete_feature_keys", ()))
    replacement_keys = {spec.feature_key for spec in proposal.features}
    auto_old: dict[tuple[str, str], Entity] = {}
    manual_old: dict[tuple[str, str], Entity] = {}
    retained: list[Entity] = []
    preserved_manual = 0
    for entity in project.entities:
        same_scope = (
            entity.metadata.get("mounting_generated")
            and entity.metadata.get("mounting_workflow") == proposal.workflow
            and entity.metadata.get("mounting_scope_key") == proposal.scope_key
        )
        if not same_scope:
            retained.append(entity)
            continue
        key = str(entity.metadata.get("mounting_feature_key", ""))
        component = str(entity.metadata.get("mounting_component", ""))
        lookup = (key, component)
        if entity.metadata.get("mounting_manual_edit"):
            manual_old[lookup] = entity
            if key not in deleted_keys and key not in replacement_keys:
                retained.append(entity)
                preserved_manual += 1
            continue
        auto_old[lookup] = entity

    existing_manual_keys = {key for key, _component in manual_old}
    additions: list[Entity] = []
    reused_ids: set[str] = set()
    for spec in proposal.features:
        if spec.feature_key in deleted_keys:
            continue
        if spec.feature_key in existing_manual_keys and not spec.manual_edit:
            # A reviewed field edit is authoritative on regeneration.
            for (key, _component), entity in manual_old.items():
                if key == spec.feature_key:
                    retained.append(entity)
                    preserved_manual += 1
            continue
        generated = _feature_entities(spec, proposal.workflow, proposal.scope_key)
        for entity in generated:
            component = str(entity.metadata.get("mounting_component", ""))
            lookup = (spec.feature_key, component)
            prior = (manual_old.get(lookup) or auto_old.get(lookup)) if spec.manual_edit else auto_old.get(lookup)
            if prior is not None:
                entity.id = prior.id
                reused_ids.add(prior.id)
            additions.append(entity)

    all_replaced = {entity.id for entity in (*auto_old.values(), *manual_old.values())}
    removed_ids = all_replaced - reused_ids - {entity.id for entity in retained}

    project.entities = retained + additions
    if removed_ids:
        for operation in project.operations:
            operation.entity_ids = [entity_id for entity_id in operation.entity_ids if entity_id not in removed_ids]
    settings = project.metadata.setdefault("layer_settings", {})
    next_order = max((int(value.get("order", -1)) for value in settings.values() if isinstance(value, dict)), default=-1) + 1
    for layer in dict.fromkeys(entity.layer for entity in additions):
        value = settings.setdefault(layer, {})
        value.setdefault("visible", True)
        value.setdefault("locked", False)
        value.setdefault("intentional", True)
        value.setdefault("color", MOUNTING_LAYER_COLORS.get(layer, "#1971c2"))
        if "order" not in value:
            value["order"] = next_order
            next_order += 1
    project.metadata["last_mounting_workflow"] = proposal.workflow
    project.metadata["last_mounting_scope_key"] = proposal.scope_key
    return MountingApplyResult(
        added=len(additions), replaced=len(all_replaced), preserved_manual=preserved_manual,
        feature_entity_ids=tuple(entity.id for entity in additions),
    )
