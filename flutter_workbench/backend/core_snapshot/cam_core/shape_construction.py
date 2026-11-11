from __future__ import annotations

"""Previewable, UI-independent construction geometry operations."""

from dataclasses import dataclass
import copy
import math
from typing import Iterable

from ._vendor import pyclipper
from .model import Circle, Entity, Point, Polyline, Rectangle, new_id


BOOLEAN_OPERATIONS = ("union", "subtract", "intersect", "exclude")
_SCALE = 1_000_000.0
_MIN_AREA = 4.0


@dataclass(frozen=True)
class BooleanResult:
    operation: str
    source_entity_ids: tuple[str, ...]
    target_entity_ids: tuple[str, ...]
    cutter_entity_ids: tuple[str, ...]
    entities: tuple[Polyline, ...]


@dataclass(frozen=True)
class CleaveResult:
    """Previewable zero-width division of selected closed geometry."""

    source_entity_ids: tuple[str, ...]
    unaffected_entity_ids: tuple[str, ...]
    entities: tuple[Polyline, ...]
    line_start: Point
    line_end: Point
    infinite_line: bool
    area_before: float
    area_after: float


@dataclass(frozen=True)
class CleaveApplyResult:
    removed_dependent_ids: tuple[str, ...]
    disabled_operation_ids: tuple[str, ...]
    reviewed_led_plan_ids: tuple[str, ...]


def _points(entity: Entity, circle_segments: int) -> list[Point]:
    if isinstance(entity, Polyline):
        if not entity.closed:
            raise ValueError(f"{entity.id} is an open contour. Boolean construction requires closed shapes.")
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
                entity.center.x + entity.radius * math.cos(2 * math.pi * index / circle_segments),
                entity.center.y + entity.radius * math.sin(2 * math.pi * index / circle_segments),
            )
            for index in range(circle_segments)
        ]
    else:
        raise ValueError(
            f"{entity.id} is {entity.kind} geometry. Convert it to a closed contour before using a boolean operation."
        )
    if len(points) > 1 and points[0].distance_to(points[-1]) <= 1e-9:
        points.pop()
    if len(points) < 3:
        raise ValueError(f"{entity.id} requires at least three distinct points.")
    return points


def _path(entity: Entity, circle_segments: int) -> list[tuple[int, int]]:
    raw = [(round(point.x * _SCALE), round(point.y * _SCALE)) for point in _points(entity, circle_segments)]
    path: list[tuple[int, int]] = []
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
            f"{entity.id} crosses itself or contains ambiguous contour topology. Repair it before construction."
        )
    return path


def _contains(container: list[tuple[int, int]], child: list[tuple[int, int]]) -> bool:
    if abs(pyclipper.Area(container)) <= abs(pyclipper.Area(child)):
        return False
    return any(pyclipper.PointInPolygon(point, container) != 0 for point in child)


def _oriented_region(paths: Iterable[list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    values = [list(path) for path in paths]
    for index, path in enumerate(values):
        depth = sum(
            1 for other_index, other in enumerate(values)
            if other_index != index and _contains(other, path)
        )
        desired_orientation = depth % 2 == 0
        if pyclipper.Orientation(path) != desired_orientation:
            path.reverse()
    return values


def _clip(
    operation: int,
    subject: Iterable[list[tuple[int, int]]],
    clip: Iterable[list[tuple[int, int]]] = (),
) -> list[list[tuple[int, int]]]:
    engine = pyclipper.Pyclipper()
    subject_paths = list(subject)
    clip_paths = list(clip)
    if not subject_paths:
        return []
    engine.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)
    if clip_paths:
        engine.AddPaths(clip_paths, pyclipper.PT_CLIP, True)
    return engine.Execute(operation, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _logical_operands(
    entities: list[Entity], paths: dict[str, list[tuple[int, int]]],
) -> list[list[Entity]]:
    explicit: dict[str, list[Entity]] = {}
    singles: list[Entity] = []
    for entity in entities:
        compound_id = str(entity.metadata.get("compound_id", "")).strip()
        if compound_id:
            explicit.setdefault(compound_id, []).append(entity)
        else:
            singles.append(entity)
    operands = list(explicit.values())
    if len(singles) <= 1:
        operands.extend([entity] for entity in singles)
        return operands

    roots: dict[str, list[Entity]] = {}
    for entity in singles:
        containers = [
            candidate for candidate in singles
            if candidate.id != entity.id and _contains(paths[candidate.id], paths[entity.id])
        ]
        if containers:
            root = max(containers, key=lambda item: abs(pyclipper.Area(paths[item.id])))
            roots.setdefault(root.id, []).append(entity)
        else:
            roots.setdefault(entity.id, []).append(entity)
    # Two nested shapes are normally operands (the common subtract case). Once
    # another root is present, nesting is strong evidence of a counter/hole.
    if len(roots) >= 2:
        by_id = {entity.id: entity for entity in singles}
        for root_id, members in roots.items():
            root = by_id[root_id]
            if root not in members:
                members.insert(0, root)
        operands.extend(roots.values())
    else:
        operands.extend([entity] for entity in singles)
    return operands


def _cleave_operands(
    entities: list[Entity], paths: dict[str, list[tuple[int, int]]],
) -> list[list[Entity]]:
    """Group every selected containment family for topology-preserving splits.

    Boolean subtraction must treat a two-contour selection as two operands.
    Cleave has no cutter operand, so nested selected contours are instead the
    outer/counter topology of the shape being divided even when an imported
    file has not yet assigned an explicit ``compound_id``.
    """
    explicit: dict[str, list[Entity]] = {}
    singles: list[Entity] = []
    for entity in entities:
        compound_id = str(entity.metadata.get("compound_id", "")).strip()
        if compound_id:
            explicit.setdefault(compound_id, []).append(entity)
        else:
            singles.append(entity)
    operands = list(explicit.values())
    roots: dict[str, list[Entity]] = {}
    for entity in singles:
        containers = [
            candidate for candidate in singles
            if candidate.id != entity.id and _contains(paths[candidate.id], paths[entity.id])
        ]
        root = max(containers, key=lambda item: abs(pyclipper.Area(paths[item.id]))) if containers else entity
        roots.setdefault(root.id, []).append(entity)
    by_id = {entity.id: entity for entity in singles}
    for root_id, members in roots.items():
        root = by_id[root_id]
        if root not in members:
            members.insert(0, root)
        operands.append(members)
    return operands


def _region_for(entities: Iterable[Entity], paths: dict[str, list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    return _oriented_region(paths[entity.id] for entity in entities)


def _poly_tree_paths(
    paths: list[list[tuple[int, int]]], operation: int = pyclipper.CT_UNION,
) -> list[tuple[list[tuple[int, int]], bool, int]]:
    if not paths:
        return []
    engine = pyclipper.Pyclipper()
    engine.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = engine.Execute2(operation, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    result: list[tuple[list[tuple[int, int]], bool, int]] = []

    def visit(node, depth: int) -> None:
        for child in node.Childs:
            contour = list(child.Contour)
            if len(contour) >= 3 and abs(pyclipper.Area(contour)) >= _MIN_AREA:
                result.append((contour, bool(child.IsHole), depth))
            visit(child, depth + 1)

    visit(tree, 0)
    return result


def _region_area(paths: Iterable[list[tuple[int, int]]]) -> float:
    """Return signed-region area as a positive project-unit value."""
    return abs(sum(pyclipper.Area(path) for path in _oriented_region(paths))) / (_SCALE * _SCALE)


def _cross(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _boundary_crossings(
    paths: Iterable[list[tuple[int, int]]],
    start: Point,
    end: Point,
    *,
    infinite_line: bool,
) -> tuple[list[tuple[float, float]], bool]:
    """Return distinct line/boundary intersections and collinear-overlap state."""
    direction = (end.x - start.x, end.y - start.y)
    length_squared = direction[0] * direction[0] + direction[1] * direction[1]
    epsilon = 1e-9
    intersections: list[tuple[float, float]] = []
    collinear = False
    for path in paths:
        points = [(x / _SCALE, y / _SCALE) for x, y in path]
        for first, second in zip(points, points[1:] + points[:1]):
            edge = (second[0] - first[0], second[1] - first[1])
            offset = (first[0] - start.x, first[1] - start.y)
            denominator = _cross(direction, edge)
            if abs(denominator) <= epsilon:
                if abs(_cross(offset, direction)) <= epsilon:
                    first_t = ((first[0] - start.x) * direction[0] + (first[1] - start.y) * direction[1]) / length_squared
                    second_t = ((second[0] - start.x) * direction[0] + (second[1] - start.y) * direction[1]) / length_squared
                    if infinite_line or max(min(first_t, second_t), 0.0) <= min(max(first_t, second_t), 1.0) + epsilon:
                        collinear = True
                continue
            t = _cross(offset, edge) / denominator
            edge_t = _cross(offset, direction) / denominator
            if (infinite_line or -epsilon <= t <= 1.0 + epsilon) and -epsilon <= edge_t <= 1.0 + epsilon:
                candidate = (start.x + direction[0] * t, start.y + direction[1] * t)
                if not any(math.hypot(candidate[0] - x, candidate[1] - y) <= 2.0 / _SCALE for x, y in intersections):
                    intersections.append(candidate)
    return intersections, collinear


def _half_planes(
    paths: Iterable[list[tuple[int, int]]], start: Point, end: Point,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    points = [(x / _SCALE, y / _SCALE) for path in paths for x, y in path]
    direction_x = end.x - start.x
    direction_y = end.y - start.y
    length = math.hypot(direction_x, direction_y)
    unit_x, unit_y = direction_x / length, direction_y / length
    normal_x, normal_y = -unit_y, unit_x
    reach = max(
        [math.hypot(x - start.x, y - start.y) for x, y in points]
        + [length, 1.0]
    ) * 4.0 + 1.0

    def encoded(values: Iterable[tuple[float, float]]) -> list[tuple[int, int]]:
        return [(round(x * _SCALE), round(y * _SCALE)) for x, y in values]

    negative_tangent = (start.x - unit_x * reach, start.y - unit_y * reach)
    positive_tangent = (start.x + unit_x * reach, start.y + unit_y * reach)
    left = encoded((
        negative_tangent,
        positive_tangent,
        (positive_tangent[0] + normal_x * reach, positive_tangent[1] + normal_y * reach),
        (negative_tangent[0] + normal_x * reach, negative_tangent[1] + normal_y * reach),
    ))
    right = encoded((
        positive_tangent,
        negative_tangent,
        (negative_tangent[0] - normal_x * reach, negative_tangent[1] - normal_y * reach),
        (positive_tangent[0] - normal_x * reach, positive_tangent[1] - normal_y * reach),
    ))
    return left, right


def cleave_shapes(
    entities: Iterable[Entity],
    line_start: Point,
    line_end: Point,
    *,
    infinite_line: bool = False,
    circle_segments: int = 160,
) -> CleaveResult:
    """Split only crossed selected shapes along a zero-width straight line.

    A finite drawn splitter must cross a target boundary at least twice.  A
    ruler-guide splitter is infinite.  Tangencies, misses, and selected shapes
    outside the split remain explicitly listed as unaffected; a collinear
    boundary is rejected because it has no unambiguous two-sided result.
    """
    selected = list(entities)
    if not selected:
        raise ValueError("Select at least one closed shape to cleave.")
    if circle_segments < 32:
        raise ValueError("Circle preview resolution must be at least 32 segments.")
    if not all(math.isfinite(value) for value in (line_start.x, line_start.y, line_end.x, line_end.y)):
        raise ValueError("The splitting line requires finite coordinates.")
    if line_start.distance_to(line_end) <= 1e-9:
        raise ValueError("The splitting line needs two different points.")

    paths = {entity.id: _path(entity, circle_segments) for entity in selected}
    operands = _cleave_operands(selected, paths)
    output: list[Polyline] = []
    affected_ids: list[str] = []
    unaffected_ids: list[str] = []
    area_before = 0.0
    area_after = 0.0

    for operand in operands:
        region = _clip(pyclipper.CT_UNION, _region_for(operand, paths))
        crossings, collinear = _boundary_crossings(
            region, line_start, line_end, infinite_line=infinite_line,
        )
        operand_ids = [entity.id for entity in operand]
        if collinear:
            raise ValueError(
                "The splitting line overlaps a selected contour edge. Move or rotate the line so it crosses cleanly."
            )
        if len(crossings) < 2:
            unaffected_ids.extend(operand_ids)
            continue
        left_plane, right_plane = _half_planes(region, line_start, line_end)
        left_paths = _clip(pyclipper.CT_INTERSECTION, region, (left_plane,))
        right_paths = _clip(pyclipper.CT_INTERSECTION, region, (right_plane,))
        left_area = _region_area(left_paths)
        right_area = _region_area(right_paths)
        if left_area <= _MIN_AREA / (_SCALE * _SCALE) or right_area <= _MIN_AREA / (_SCALE * _SCALE):
            unaffected_ids.extend(operand_ids)
            continue

        source_area = _region_area(region)
        reconstructed_area = left_area + right_area
        tolerance = max(1e-8, source_area * 2e-8)
        if abs(source_area - reconstructed_area) > tolerance:
            raise RuntimeError("Cleave area-conservation check failed; source geometry was not changed.")

        affected_ids.extend(operand_ids)
        area_before += source_area
        area_after += reconstructed_area
        style_source = operand[0]
        preserved_metadata = {
            key: copy.deepcopy(value)
            for key, value in style_source.metadata.items()
            if key in {"color", "fill", "stroke", "stroke_width", "plate_number", "group_id"}
        }
        for side, side_paths in (("left", left_paths), ("right", right_paths)):
            compound_id = ""
            for path, is_hole, depth in _poly_tree_paths(_oriented_region(side_paths)):
                if depth == 0 or not compound_id:
                    compound_id = new_id("compound")
                metadata = {
                    **copy.deepcopy(preserved_metadata),
                    "compound_id": compound_id,
                    "cleave_source_entity_ids": operand_ids,
                    "cleave_side": side,
                    "cleave_hole": is_hole,
                    "cleave_nesting_depth": depth,
                }
                output.append(Polyline(
                    points=[Point(x / _SCALE, y / _SCALE) for x, y in path],
                    closed=True,
                    layer=style_source.layer,
                    metadata=metadata,
                    appearance=copy.deepcopy(style_source.appearance),
                ))

    if not affected_ids:
        raise ValueError(
            "The splitting line did not cross any selected closed shape twice. Tangent touches and misses do not change geometry."
        )
    return CleaveResult(
        source_entity_ids=tuple(affected_ids),
        unaffected_entity_ids=tuple(unaffected_ids),
        entities=tuple(output),
        line_start=line_start,
        line_end=line_end,
        infinite_line=infinite_line,
        area_before=area_before,
        area_after=area_after,
    )


def apply_cleave_result(project, result: CleaveResult) -> CleaveApplyResult:
    """Commit a reviewed cleave result and invalidate dependent work.

    This shared mutation owner is used by both the desktop workflow and
    headless automation.  The preview source must still exist when committing.
    """
    source_ids = set(result.source_entity_ids)
    source_indexes = [
        index for index, entity in enumerate(project.entities) if entity.id in source_ids
    ]
    if len(source_indexes) != len(source_ids):
        raise ValueError("The cleave preview source is no longer complete. Generate a new preview.")
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
    retained[insert_at:insert_at] = list(result.entities)
    project.entities = retained

    disabled: list[str] = []
    for operation in project.operations:
        referenced = set(operation.entity_ids) | set(getattr(operation, "island_entity_ids", []))
        if referenced.intersection(removed_ids):
            operation.enabled = False
            operation.recipe_metadata["geometry_invalidated"] = (
                "Source geometry changed by Cleave / Split. Recreate or reassociate this operation."
            )
            disabled.append(operation.id)
    reviewed: list[str] = []
    plans = project.metadata.get("led_plans", [])
    if isinstance(plans, list):
        for plan in plans:
            linked = set(plan.get("linked_entity_ids", [])) if isinstance(plan, dict) else set()
            if linked.intersection(source_ids):
                plan["geometry_review_required"] = True
                plan["geometry_review_reason"] = (
                    "Linked artwork changed by Cleave / Split. Regenerate the physical layout."
                )
                reviewed.append(str(plan.get("id", "")))
    return CleaveApplyResult(
        removed_dependent_ids=tuple(sorted(dependent_ids)),
        disabled_operation_ids=tuple(disabled),
        reviewed_led_plan_ids=tuple(reviewed),
    )


def boolean_shapes(
    entities: Iterable[Entity],
    operation: str,
    *,
    target_entity_id: str | None = None,
    circle_segments: int = 160,
) -> BooleanResult:
    """Construct a reviewed polygon result without mutating the source entities."""
    selected = list(entities)
    if operation not in BOOLEAN_OPERATIONS:
        raise ValueError(f"Unsupported boolean operation: {operation}.")
    if len(selected) < 2:
        raise ValueError("Select at least two closed shapes for construction.")
    if circle_segments < 32:
        raise ValueError("Circle preview resolution must be at least 32 segments.")
    paths = {entity.id: _path(entity, circle_segments) for entity in selected}
    operands = _logical_operands(selected, paths)
    target_ids: tuple[str, ...] = ()
    cutter_ids: tuple[str, ...] = ()

    if operation == "subtract":
        if target_entity_id is None:
            raise ValueError("Choose which selected object remains as the subtraction target.")
        target_operand = next(
            (operand for operand in operands if any(entity.id == target_entity_id for entity in operand)),
            None,
        )
        if target_operand is None:
            raise ValueError("The subtraction target is not in the selected geometry.")
        cutters = [entity for operand in operands if operand is not target_operand for entity in operand]
        if not cutters:
            raise ValueError("Subtraction needs at least one cutter in addition to the target.")
        target_ids = tuple(entity.id for entity in target_operand)
        cutter_ids = tuple(entity.id for entity in cutters)
        cutter_region = _clip(pyclipper.CT_UNION, _region_for(cutters, paths))
        result_paths = _clip(
            pyclipper.CT_DIFFERENCE,
            _region_for(target_operand, paths),
            _oriented_region(cutter_region),
        )
        style_source = next(entity for entity in selected if entity.id == target_entity_id)
    else:
        operand_regions = [
            _clip(pyclipper.CT_UNION, _region_for(operand, paths))
            for operand in operands
        ]
        result_paths = operand_regions[0]
        clip_type = {
            "union": pyclipper.CT_UNION,
            "intersect": pyclipper.CT_INTERSECTION,
            "exclude": pyclipper.CT_XOR,
        }[operation]
        for region in operand_regions[1:]:
            result_paths = _clip(
                clip_type, _oriented_region(result_paths), _oriented_region(region),
            )
        style_source = selected[0]

    records = _poly_tree_paths(_oriented_region(result_paths))
    if not records:
        raise ValueError(
            "The selected shapes do not leave any closed area for this operation. Nothing was changed."
        )
    compound_id = new_id("compound")
    preserved_metadata = {
        key: copy.deepcopy(value)
        for key, value in style_source.metadata.items()
        if key in {"color", "fill", "stroke", "stroke_width", "plate_number"}
    }
    output: list[Polyline] = []
    for path, is_hole, depth in records:
        metadata = {
            **copy.deepcopy(preserved_metadata),
            "compound_id": compound_id,
            "boolean_operation": operation,
            "boolean_source_entity_ids": [entity.id for entity in selected],
            "boolean_hole": is_hole,
            "boolean_nesting_depth": depth,
        }
        output.append(Polyline(
            points=[Point(x / _SCALE, y / _SCALE) for x, y in path],
            closed=True,
            layer=style_source.layer,
            metadata=metadata,
            appearance=copy.deepcopy(style_source.appearance),
        ))
    return BooleanResult(
        operation=operation,
        source_entity_ids=tuple(entity.id for entity in selected),
        target_entity_ids=target_ids,
        cutter_entity_ids=cutter_ids,
        entities=tuple(output),
    )
