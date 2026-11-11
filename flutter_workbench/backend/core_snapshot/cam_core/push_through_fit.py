from __future__ import annotations

"""Reviewed local relief for routed acrylic passing through a metal opening.

The operator assigns the two roles explicitly.  The acrylic artwork is never
rewritten: a round-cutter envelope is calculated from it, and only accepted
interference islands are unioned into the metal opening.
"""

from dataclasses import dataclass
import copy
import hashlib
import json
import math
from typing import Iterable

from ._vendor import pyclipper
from .model import Circle, Entity, Point, Polyline, Rectangle, new_id


_SCALE = 1_000_000.0
_MIN_AREA = 4.0


IntPoint = tuple[int, int]
IntPath = tuple[IntPoint, ...]
IntPaths = tuple[IntPath, ...]


@dataclass(frozen=True)
class PushThroughFitSettings:
    cutter_diameter: float
    fit_clearance: float
    circle_segments: int = 192
    local_span_multiplier: float = 4.0


@dataclass(frozen=True)
class PushThroughCornerCandidate:
    candidate_id: str
    paths: IntPaths
    bounds: tuple[float, float, float, float]
    area: float
    recommended: bool
    note: str


@dataclass(frozen=True)
class PushThroughFitProposal:
    proposal_id: str
    metal_entity_ids: tuple[str, ...]
    acrylic_entity_ids: tuple[str, ...]
    source_fingerprint: str
    settings: PushThroughFitSettings
    metal_paths: IntPaths
    actual_acrylic_paths: IntPaths
    actual_acrylic_entities: tuple[Polyline, ...]
    candidates: tuple[PushThroughCornerCandidate, ...]
    metal_layer: str
    metal_metadata: dict
    metal_appearance: object | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PushThroughFitApplyResult:
    proposal_id: str
    replacement_entity_ids: tuple[str, ...]
    disabled_operation_ids: tuple[str, ...]
    removed_dependent_ids: tuple[str, ...]


def _entity_points(entity: Entity, circle_segments: int) -> list[Point]:
    if isinstance(entity, Polyline):
        if not entity.closed:
            raise ValueError(f"{entity.id} is open geometry. Push-Through Fit requires closed contours.")
        points = list(entity.points)
    elif isinstance(entity, Rectangle):
        x1, x2 = sorted((entity.corner_a.x, entity.corner_b.x))
        y1, y2 = sorted((entity.corner_a.y, entity.corner_b.y))
        points = [Point(x1, y1), Point(x2, y1), Point(x2, y2), Point(x1, y2)]
    elif isinstance(entity, Circle):
        if entity.radius <= 0:
            raise ValueError(f"{entity.id} has no usable area.")
        points = [
            Point(
                entity.center.x + entity.radius * math.cos(2.0 * math.pi * index / circle_segments),
                entity.center.y + entity.radius * math.sin(2.0 * math.pi * index / circle_segments),
            )
            for index in range(circle_segments)
        ]
    else:
        raise ValueError(
            f"{entity.id} is {entity.kind} geometry. Convert it to a closed contour before Push-Through Fit."
        )
    if len(points) > 1 and points[0].distance_to(points[-1]) <= 1e-9:
        points.pop()
    if len(points) < 3:
        raise ValueError(f"{entity.id} requires at least three distinct points.")
    return points


def _entity_path(entity: Entity, circle_segments: int) -> list[IntPoint]:
    raw = [(round(point.x * _SCALE), round(point.y * _SCALE)) for point in _entity_points(entity, circle_segments)]
    path: list[IntPoint] = []
    for point in raw:
        if not path or point != path[-1]:
            path.append(point)
    if len(path) > 1 and path[0] == path[-1]:
        path.pop()
    if len(path) < 3 or abs(pyclipper.Area(path)) < _MIN_AREA:
        raise ValueError(f"{entity.id} has no usable closed area.")
    simplified = pyclipper.SimplifyPolygon(path, pyclipper.PFT_NONZERO)
    if len(simplified) != 1 or abs(abs(pyclipper.Area(simplified[0])) - abs(pyclipper.Area(path))) > _MIN_AREA:
        raise ValueError(
            f"{entity.id} crosses itself or has ambiguous topology. Repair it before Push-Through Fit."
        )
    return path


def _contains(container: list[IntPoint], child: list[IntPoint]) -> bool:
    if abs(pyclipper.Area(container)) <= abs(pyclipper.Area(child)):
        return False
    return any(pyclipper.PointInPolygon(point, container) != 0 for point in child)


def _oriented(paths: Iterable[Iterable[IntPoint]]) -> list[list[IntPoint]]:
    values = [list(path) for path in paths]
    for index, path in enumerate(values):
        depth = sum(
            1 for other_index, other in enumerate(values)
            if other_index != index and _contains(other, path)
        )
        if pyclipper.Orientation(path) != (depth % 2 == 0):
            path.reverse()
    return values


def _clip(
    operation: int,
    subject: Iterable[Iterable[IntPoint]],
    clip: Iterable[Iterable[IntPoint]] = (),
) -> list[list[IntPoint]]:
    subject_paths = [list(path) for path in subject]
    clip_paths = [list(path) for path in clip]
    if not subject_paths:
        return []
    engine = pyclipper.Pyclipper()
    engine.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)
    if clip_paths:
        engine.AddPaths(clip_paths, pyclipper.PT_CLIP, True)
    return engine.Execute(operation, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _offset(paths: Iterable[Iterable[IntPoint]], distance: float) -> list[list[IntPoint]]:
    values = _oriented(paths)
    if not values or abs(distance) <= 1e-12:
        return _clip(pyclipper.CT_UNION, values)
    offsetter = pyclipper.PyclipperOffset(8.0, max(1.0, 0.0002 * _SCALE))
    offsetter.AddPaths(values, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return offsetter.Execute(distance * _SCALE)


def _tree(paths: Iterable[Iterable[IntPoint]]):
    values = _oriented(paths)
    if not values:
        return None
    engine = pyclipper.Pyclipper()
    engine.AddPaths(values, pyclipper.PT_SUBJECT, True)
    return engine.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _tree_records(paths: Iterable[Iterable[IntPoint]]) -> list[tuple[list[IntPoint], bool, int, int]]:
    records: list[tuple[list[IntPoint], bool, int, int]] = []
    root_number = -1

    def visit(node, depth: int, owner: int) -> None:
        nonlocal root_number
        for child in node.Childs:
            next_owner = owner
            if depth == 0:
                root_number += 1
                next_owner = root_number
            contour = list(child.Contour)
            if len(contour) >= 3 and abs(pyclipper.Area(contour)) >= _MIN_AREA:
                records.append((contour, bool(child.IsHole), depth, next_owner))
            visit(child, depth + 1, next_owner)

    tree = _tree(paths)
    if tree is not None:
        visit(tree, 0, -1)
    return records


def _components(paths: Iterable[Iterable[IntPoint]]) -> list[list[list[IntPoint]]]:
    grouped: dict[int, list[list[IntPoint]]] = {}
    for path, _is_hole, _depth, owner in _tree_records(paths):
        grouped.setdefault(owner, []).append(path)
    return [grouped[key] for key in sorted(grouped)]


def _path_bounds(paths: Iterable[Iterable[IntPoint]]) -> tuple[float, float, float, float]:
    points = [point for path in paths for point in path]
    return (
        min(x for x, _y in points) / _SCALE,
        min(y for _x, y in points) / _SCALE,
        max(x for x, _y in points) / _SCALE,
        max(y for _x, y in points) / _SCALE,
    )


def _region_area(paths: Iterable[Iterable[IntPoint]]) -> float:
    return abs(sum(pyclipper.Area(path) for path in _oriented(paths))) / (_SCALE * _SCALE)


def _fingerprint(entities: Iterable[Entity]) -> str:
    payload = [entity.to_dict() for entity in sorted(entities, key=lambda item: item.id)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def push_through_fit_source_fingerprint(
    metal_entities: Iterable[Entity], acrylic_entities: Iterable[Entity],
) -> str:
    return _fingerprint([*metal_entities, *acrylic_entities])


def _preview_entities(
    paths: Iterable[Iterable[IntPoint]], *, layer: str, role: str,
) -> tuple[Polyline, ...]:
    output: list[Polyline] = []
    compounds: dict[int, str] = {}
    for path, is_hole, depth, owner in _tree_records(paths):
        compounds.setdefault(owner, new_id("compound"))
        output.append(Polyline(
            points=[Point(x / _SCALE, y / _SCALE) for x, y in path],
            closed=True,
            layer=layer,
            metadata={
                "compound_id": compounds[owner],
                "push_through_preview_role": role,
                "push_through_fit_hole": is_hole,
                "push_through_fit_nesting_depth": depth,
                "reference_only": True,
                "non_machining": True,
            },
        ))
    return tuple(output)


def propose_push_through_fit(
    metal_entities: Iterable[Entity],
    acrylic_entities: Iterable[Entity],
    settings: PushThroughFitSettings,
) -> PushThroughFitProposal:
    """Analyze explicit metal-opening and acrylic-profile selections.

    ``actual_acrylic_paths`` is a diameter-based approximation of the retained
    routed part: closing the requested acrylic solid by the round cutter radius
    fills only concavities the cutter cannot enter.  It is preview data, never a
    replacement for the acrylic artwork.
    """
    metal = list(metal_entities)
    acrylic = list(acrylic_entities)
    if not metal:
        raise ValueError("Use the current selection as the metal opening first.")
    if not acrylic:
        raise ValueError("Use the current selection as the acrylic profile first.")
    if {entity.id for entity in metal}.intersection(entity.id for entity in acrylic):
        raise ValueError("Metal and acrylic roles overlap. Each source contour can have only one role.")
    if not math.isfinite(settings.cutter_diameter) or settings.cutter_diameter <= 0:
        raise ValueError("Cutter diameter must be greater than zero.")
    if not math.isfinite(settings.fit_clearance) or settings.fit_clearance < 0:
        raise ValueError("Fit clearance must be zero or greater.")
    if settings.circle_segments < 32:
        raise ValueError("Circle preview resolution must be at least 32 segments.")
    if len({entity.layer for entity in metal}) != 1:
        raise ValueError("The metal opening selection must be on one layer so its corrected result has an unambiguous destination.")
    plate_numbers = {
        str(entity.metadata.get("plate_number", "")) for entity in metal
        if str(entity.metadata.get("plate_number", ""))
    }
    if len(plate_numbers) > 1:
        raise ValueError("The metal opening selection crosses plates. Analyze one opening at a time.")

    metal_paths = _clip(
        pyclipper.CT_UNION,
        _oriented(_entity_path(entity, settings.circle_segments) for entity in metal),
    )
    acrylic_paths = _clip(
        pyclipper.CT_UNION,
        _oriented(_entity_path(entity, settings.circle_segments) for entity in acrylic),
    )
    radius = settings.cutter_diameter / 2.0
    expanded = _offset(acrylic_paths, radius)
    actual_acrylic = _offset(expanded, -radius)
    required_opening = _offset(actual_acrylic, settings.fit_clearance)
    conflicts = _clip(
        pyclipper.CT_DIFFERENCE,
        _oriented(required_opening),
        _oriented(metal_paths),
    )

    candidates: list[PushThroughCornerCandidate] = []
    warnings: list[str] = []
    local_limit = max(
        settings.cutter_diameter * settings.local_span_multiplier,
        settings.fit_clearance * 8.0,
        0.01,
    )
    for component in _components(conflicts):
        area = _region_area(component)
        if area <= 1e-10:
            continue
        bounds = _path_bounds(component)
        span = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
        recommended = span <= local_limit and area <= max(local_limit * local_limit, 1e-6)
        digest = hashlib.sha1(
            repr(tuple(tuple(point for point in path) for path in component)).encode("ascii")
        ).hexdigest()[:10]
        note = (
            "Local cutter-corner relief"
            if recommended else
            "Broad mismatch — inspect before including; this may not be a corner-only fit issue"
        )
        candidates.append(PushThroughCornerCandidate(
            candidate_id=f"fit_corner_{digest}",
            paths=tuple(tuple(point for point in path) for path in component),
            bounds=bounds,
            area=area,
            recommended=recommended,
            note=note,
        ))
    candidates.sort(key=lambda item: (item.bounds[0], item.bounds[1], item.candidate_id))
    broad_count = sum(not item.recommended for item in candidates)
    if broad_count:
        warnings.append(
            f"{broad_count} interference region(s) are larger than a normal local cutter-corner correction. "
            "They are skipped initially and require deliberate review."
        )

    style = metal[0]
    preserved_metadata = {
        key: copy.deepcopy(value)
        for key, value in style.metadata.items()
        if key in {"color", "fill", "stroke", "stroke_width", "plate_number", "group_id"}
    }
    proposal_id = new_id("push_fit")
    return PushThroughFitProposal(
        proposal_id=proposal_id,
        metal_entity_ids=tuple(entity.id for entity in metal),
        acrylic_entity_ids=tuple(entity.id for entity in acrylic),
        source_fingerprint=push_through_fit_source_fingerprint(metal, acrylic),
        settings=settings,
        metal_paths=tuple(tuple(point for point in path) for path in metal_paths),
        actual_acrylic_paths=tuple(tuple(point for point in path) for path in actual_acrylic),
        actual_acrylic_entities=_preview_entities(
            actual_acrylic, layer=acrylic[0].layer, role="diameter_based_routed_acrylic_approximation",
        ),
        candidates=tuple(candidates),
        metal_layer=style.layer,
        metal_metadata=preserved_metadata,
        metal_appearance=copy.deepcopy(style.appearance),
        warnings=tuple(warnings),
    )


def corrected_metal_entities(
    proposal: PushThroughFitProposal,
    included_candidate_ids: Iterable[str],
) -> tuple[Polyline, ...]:
    included = set(included_candidate_ids)
    known = {candidate.candidate_id for candidate in proposal.candidates}
    unknown = included - known
    if unknown:
        raise ValueError("The selected relief list no longer matches this preview. Analyze again.")
    additions = [
        path for candidate in proposal.candidates if candidate.candidate_id in included
        for path in candidate.paths
    ]
    corrected = _clip(
        pyclipper.CT_UNION,
        _oriented([*proposal.metal_paths, *additions]),
    )
    output: list[Polyline] = []
    compounds: dict[int, str] = {}
    for path, is_hole, depth, owner in _tree_records(corrected):
        compounds.setdefault(owner, new_id("compound"))
        metadata = {
            **copy.deepcopy(proposal.metal_metadata),
            "compound_id": compounds[owner],
            "push_through_fit_id": proposal.proposal_id,
            "push_through_fit_source_entity_ids": list(proposal.metal_entity_ids),
            "push_through_fit_acrylic_entity_ids": list(proposal.acrylic_entity_ids),
            "push_through_fit_cutter_diameter": proposal.settings.cutter_diameter,
            "push_through_fit_clearance": proposal.settings.fit_clearance,
            "push_through_fit_hole": is_hole,
            "push_through_fit_nesting_depth": depth,
        }
        output.append(Polyline(
            points=[Point(x / _SCALE, y / _SCALE) for x, y in path],
            closed=True,
            layer=proposal.metal_layer,
            metadata=metadata,
            appearance=copy.deepcopy(proposal.metal_appearance),
        ))
    return tuple(output)


def push_through_candidate_entities(
    proposal: PushThroughFitProposal,
    candidate_ids: Iterable[str] | None = None,
) -> tuple[Polyline, ...]:
    included = set(candidate_ids) if candidate_ids is not None else {
        candidate.candidate_id for candidate in proposal.candidates
    }
    output: list[Polyline] = []
    for candidate in proposal.candidates:
        if candidate.candidate_id not in included:
            continue
        output.extend(_preview_entities(
            candidate.paths, layer=proposal.metal_layer, role=candidate.candidate_id,
        ))
    return tuple(output)


def apply_push_through_fit(
    project,
    proposal: PushThroughFitProposal,
    included_candidate_ids: Iterable[str],
) -> PushThroughFitApplyResult:
    """Commit reviewed relief to metal only and preserve the original source."""
    included = set(included_candidate_ids)
    if not included:
        raise ValueError("Include at least one interference region before applying.")
    by_id = {entity.id: entity for entity in project.entities}
    try:
        metal = [by_id[entity_id] for entity_id in proposal.metal_entity_ids]
        acrylic = [by_id[entity_id] for entity_id in proposal.acrylic_entity_ids]
    except KeyError as exc:
        raise ValueError("The preview source is no longer complete. Analyze the fit again.") from exc
    if push_through_fit_source_fingerprint(metal, acrylic) != proposal.source_fingerprint:
        raise ValueError("The metal or acrylic source changed after analysis. Analyze the fit again.")

    replacement = corrected_metal_entities(proposal, included)
    source_ids = set(proposal.metal_entity_ids)
    source_indexes = [index for index, entity in enumerate(project.entities) if entity.id in source_ids]
    dependent_ids = {
        entity.id for entity in project.entities
        if entity.id not in source_ids and (
            str(entity.metadata.get("mounting_owner_entity_id", "")) in source_ids
            or str(entity.metadata.get("owner_entity_id", "")) in source_ids
            or str(entity.metadata.get("layer_prep_source_entity_id", "")) in source_ids
            or bool(source_ids.intersection(entity.metadata.get("pattern_source_entity_ids", [])))
        )
    }
    removed_ids = source_ids | dependent_ids
    first_source_index = min(source_indexes)
    retained = [entity for entity in project.entities if entity.id not in removed_ids]
    insert_at = sum(
        1 for index, entity in enumerate(project.entities)
        if index < first_source_index and entity.id not in removed_ids
    )
    retained[insert_at:insert_at] = list(replacement)
    project.entities = retained

    disabled: list[str] = []
    for operation in project.operations:
        referenced = set(operation.entity_ids) | set(getattr(operation, "island_entity_ids", []))
        if referenced.intersection(removed_ids):
            operation.enabled = False
            operation.recipe_metadata["geometry_invalidated"] = (
                "Metal opening changed by Push-Through Fit. Recreate or reassociate this operation."
            )
            disabled.append(operation.id)

    history = project.metadata.setdefault("push_through_fit_history", [])
    if not isinstance(history, list):
        history = []
        project.metadata["push_through_fit_history"] = history
    history.append({
        "proposal_id": proposal.proposal_id,
        "original_metal_entities": [copy.deepcopy(entity.to_dict()) for entity in metal],
        "acrylic_entity_ids": list(proposal.acrylic_entity_ids),
        "replacement_entity_ids": [entity.id for entity in replacement],
        "included_candidate_ids": sorted(included),
        "cutter_diameter": proposal.settings.cutter_diameter,
        "fit_clearance": proposal.settings.fit_clearance,
    })
    return PushThroughFitApplyResult(
        proposal_id=proposal.proposal_id,
        replacement_entity_ids=tuple(entity.id for entity in replacement),
        disabled_operation_ids=tuple(disabled),
        removed_dependent_ids=tuple(sorted(dependent_ids)),
    )
