from __future__ import annotations

"""Non-destructive DXF preparation rules for the in-project Layers workspace.

The older DXF preparation utility classified raw DXF components immediately
before writing another file.  CAM Workbench needs the same shop knowledge
without replacing geometry IDs or silently changing a job.  This module
therefore produces a reviewable proposal: existing entities receive proposed
layer assignments and any genuinely required duplicate geometry is returned
separately for the caller to apply in one undoable transaction.
"""

from dataclasses import dataclass, replace
import copy
import math
from typing import Iterable

from .feature_recognition import fit_circular_polyline
from .layout import entity_bounds
from .model import Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity, new_id
from .selection import is_closed_entity
from .topology import profile_containers


LAYER_INNER = "INNER_SHELF_CUT_070"
LAYER_OUTER = "OUTER_PROFILE"
LAYER_DRILL = "DRILL_250"
LAYER_PROFILE_HOLES = "PROFILE_HOLES"
LAYER_ID_MARKING = "ID_MARKING"
LAYER_REVIEW = "REVIEW_UNKNOWN"
LAYER_CLEANOUT = "CLEANOUT"
LAYER_ACRYLIC = "ACRYLIC CUTOUT"
LAYER_POLY_BACK = "POLY BACK CUTOUT"
LAYER_BOTH = "ACRYLIC CUTOUT + CLEANOUT"

LAYER_COLORS: dict[str, str] = {
    LAYER_INNER: "#2f9e44",
    LAYER_OUTER: "#263238",
    LAYER_DRILL: "#e03131",
    LAYER_PROFILE_HOLES: "#7048e8",
    LAYER_ID_MARKING: "#868e96",
    LAYER_REVIEW: "#f08c00",
    LAYER_CLEANOUT: "#00a6c7",
    LAYER_ACRYLIC: "#e8590c",
    LAYER_POLY_BACK: "#343a40",
}

PRESET_ALUMINUM_SHELF = "aluminum_shelf"
PRESET_PLAIN_ALUMINUM = "plain_aluminum"
PRESET_ACRYLIC_CLEAN_CUT = "acrylic_clean_cut"
PRESET_POLY_BACKS = "poly_backs"
PRESET_CLEAN_DXF = "clean_dxf"

LAYER_PREP_PRESETS: dict[str, str] = {
    "Aluminum — Shelf / Recess": PRESET_ALUMINUM_SHELF,
    "Plain Aluminum — Profiles": PRESET_PLAIN_ALUMINUM,
    "Acrylic — Cleanout + Cut": PRESET_ACRYLIC_CLEAN_CUT,
    "Polycarbonate — Letter Backs": PRESET_POLY_BACKS,
    "Clean & Sort DXF": PRESET_CLEAN_DXF,
}


@dataclass(frozen=True)
class LayerAssignment:
    entity_id: str
    current_layer: str
    proposed_layer: str
    role: str
    reason: str
    confidence: str

    @property
    def changed(self) -> bool:
        return self.current_layer != self.proposed_layer


@dataclass(frozen=True)
class LayerPreparationProposal:
    preset: str
    assignments: tuple[LayerAssignment, ...]
    additions: tuple[Entity, ...] = ()
    manual_choices: tuple[tuple[str, str], ...] = ()
    removed_ids: tuple[str, ...] = ()

    @property
    def changed_count(self) -> int:
        return sum(item.changed for item in self.assignments)

    @property
    def review_count(self) -> int:
        return sum(item.proposed_layer == LAYER_REVIEW or item.confidence == "Review" for item in self.assignments)

    def layer_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.assignments:
            counts[item.proposed_layer] = counts.get(item.proposed_layer, 0) + 1
        for entity in self.additions:
            counts[entity.layer] = counts.get(entity.layer, 0) + 1
        return counts


def _closed_profiles(entities: Iterable[Entity]) -> list[Entity]:
    return [entity for entity in entities if is_closed_entity(entity)]


def _area_from_bounds(bounds: tuple[float, float, float, float]) -> float:
    return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])


def _is_material_frame(entity: Entity) -> bool:
    bounds = entity_bounds(entity)
    if bounds is None:
        return False
    dimensions = sorted((bounds[2] - bounds[0], bounds[3] - bounds[1]))
    simple = isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and len(entity.points) <= 8
    return simple and any(
        abs(dimensions[0] - short) <= 0.1 and abs(dimensions[1] - long) <= 0.1
        for short, long in ((48.0, 96.0), (48.0, 120.0), (60.0, 120.0))
    )


def _outline_points(entity: Entity, circle_samples: int = 72) -> list[Point]:
    if isinstance(entity, Line):
        return [entity.start, entity.end]
    if isinstance(entity, Circle):
        return [
            Point(
                entity.center.x + entity.radius * math.cos(math.tau * index / circle_samples),
                entity.center.y + entity.radius * math.sin(math.tau * index / circle_samples),
            )
            for index in range(circle_samples)
        ]
    if isinstance(entity, Rectangle):
        min_x, max_x = sorted((entity.corner_a.x, entity.corner_b.x))
        min_y, max_y = sorted((entity.corner_a.y, entity.corner_b.y))
        return [Point(min_x, min_y), Point(max_x, min_y), Point(max_x, max_y), Point(min_x, max_y)]
    if isinstance(entity, Polyline):
        return list(entity.points)
    return []


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared <= 1e-18:
        return point.distance_to(start)
    amount = max(
        0.0,
        min(1.0, ((point.x - start.x) * delta_x + (point.y - start.y) * delta_y) / length_squared),
    )
    return math.hypot(
        point.x - (start.x + amount * delta_x),
        point.y - (start.y + amount * delta_y),
    )


def _point_outline_distance(point: Point, outline: list[Point]) -> float:
    if len(outline) < 2:
        return math.inf
    return min(
        _point_segment_distance(point, start, end)
        for start, end in zip(outline, outline[1:] + outline[:1])
    )


def _parallel_contour_score(first: Entity, second: Entity) -> float | None:
    first_points = _outline_points(first)
    second_points = _outline_points(second)
    if len(first_points) < 3 or len(second_points) < 3:
        return None
    first_step = max(1, len(first_points) // 72)
    second_step = max(1, len(second_points) // 72)
    distances = [_point_outline_distance(point, second_points) for point in first_points[::first_step]]
    distances.extend(_point_outline_distance(point, first_points) for point in second_points[::second_step])
    distances = sorted(value for value in distances if math.isfinite(value))
    if not distances:
        return None
    median = distances[len(distances) // 2]
    sixty_percent = distances[min(len(distances) - 1, math.floor(len(distances) * 0.6))]
    if median < 0.015 or median > 0.2 or sixty_percent > 0.35:
        return None
    return median + sixty_percent


def _bbox_overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    overlap_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    overlap_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    overlap = overlap_width * overlap_height
    return overlap / max(min(_area_from_bounds(first), _area_from_bounds(second)), 1e-12)


def _bbox_margins(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return inner[0] - outer[0], inner[1] - outer[1], outer[2] - inner[2], outer[3] - inner[3]


def _offset_pair_score(outer: Entity, inner: Entity) -> float | None:
    outer_bounds = entity_bounds(outer)
    inner_bounds = entity_bounds(inner)
    if outer_bounds is None or inner_bounds is None:
        return None
    # Matching boxes are not evidence that the actual outlines are offsets
    # (a triangle inside a rectangle can have identical inset margins).

    outer_points = _outline_points(outer)
    inner_points = _outline_points(inner)
    if len(outer_points) < 3 or len(inner_points) < 3:
        return None
    distances = [_point_outline_distance(point, inner_points) for point in outer_points[::max(1, len(outer_points) // 36)]]
    distances.extend(_point_outline_distance(point, outer_points) for point in inner_points[::max(1, len(inner_points) // 36)])
    distances = sorted(value for value in distances if math.isfinite(value))
    if not distances:
        return None
    median = distances[len(distances) // 2]
    upper = distances[min(len(distances) - 1, math.floor(len(distances) * 0.9))]
    lower = distances[math.floor(len(distances) * 0.1)]
    if median < 0.015 or median > 0.8 or upper > 1.05 or upper - lower > 0.4:
        return None
    return median + upper - lower


def _partial_offset_pair_score(outer: Entity, inner: Entity) -> float | None:
    outer_bounds = entity_bounds(outer)
    inner_bounds = entity_bounds(inner)
    if outer_bounds is None or inner_bounds is None:
        return None
    inset = [margin for margin in _bbox_margins(outer_bounds, inner_bounds) if 0.015 <= margin <= 0.75]
    if len(inset) < 3:
        return None
    average = sum(inset) / len(inset)
    spread = max(inset) - min(inset)
    return average + spread if spread <= max(0.12, average * 0.45) else None


def _uniform_outline_samples(outline: list[Point], count: int = 240) -> list[Point]:
    """Bounded length-weighted sampling, independent of DXF tessellation."""
    edges = [(a, b, a.distance_to(b)) for a, b in zip(outline, outline[1:] + outline[:1])
             if a.distance_to(b) > 1e-10]
    if not edges:
        return []
    perimeter = sum(length for _, _, length in edges)
    edge_index = 0
    traversed = 0.0
    samples = []
    for index in range(count):
        distance = index * perimeter / count
        while edge_index + 1 < len(edges) and traversed + edges[edge_index][2] < distance:
            traversed += edges[edge_index][2]
            edge_index += 1
        a, b, length = edges[edge_index]
        amount = (distance - traversed) / length
        samples.append(Point(a.x + (b.x-a.x)*amount, a.y + (b.y-a.y)*amount))
    return samples


def _shared_logo_outline_match(outer: Entity, artwork: Entity) -> bool:
    """Recognize an offset envelope whose deep artwork recesses merged.

    Containment is checked by the caller. Almost the entire outside boundary
    must follow the artwork at one consistent inset; matching boxes alone do
    not qualify. This asymmetric fallback is acrylic-only and review-required.
    """
    inner = _outline_points(artwork)
    samples = _uniform_outline_samples(_outline_points(outer))
    if len(inner) < 3 or not samples:
        return False
    distances = sorted(_point_outline_distance(point, inner) for point in samples)
    median = distances[len(distances)//2]
    if not 0.015 <= median <= 0.75:
        return False
    tolerance = max(0.03, median * 0.24)
    return sum(abs(value-median) <= tolerance for value in distances) >= len(distances)*0.95


def _split_offset_match(
    outer: Entity, children: list[Entity], *, shared_backer: bool = False,
    nested_artwork: bool = False,
) -> bool:
    """Recognize jointly inset fragments, never from bounding boxes alone.

    A joined word or decorative shape can have disconnected inner artwork.
    Require collectively consistent boundary coverage. Ordinary split offsets
    require every candidate to contribute. Multi-letter backers with several
    nested counters can also enclose dots/details which never reach the edge.
    These are review suggestions, not certain offset pairs.
    All coordinates here have already been normalized to inches.
    """
    if len(children) < 2:
        return False
    outline = _outline_points(outer)
    inner = [_outline_points(child) for child in children]
    if len(outline) < 3 or any(len(points) < 3 for points in inner):
        return False
    # Midpoints as well as vertices prevent sparse rectangles from matching
    # several unrelated counters just because their corners line up.
    samples = []
    perimeter = sum(a.distance_to(b) for a, b in zip(outline, outline[1:] + outline[:1]))
    if shared_backer:
        # Imported arcs often have far more vertices than long straight edges.
        # Weight coverage by physical boundary length, not exporter tessellation.
        samples = _uniform_outline_samples(outline)
        if not samples:
            return False
    else:
        spacing = max(0.2, perimeter / 240)
        for a, b in zip(outline, outline[1:] + outline[:1]):
            length = a.distance_to(b)
            count = max(1, math.ceil(length / spacing))
            samples.extend(Point(a.x + (b.x-a.x)*i/count, a.y + (b.y-a.y)*i/count) for i in range(count))
        samples = samples[::max(1, len(samples)//240)]
    distances = [[_point_outline_distance(point, points) for points in inner] for point in samples]
    nearest = sorted(min(row) for row in distances)
    median = nearest[len(nearest)//2]
    if not 0.015 <= median <= 0.75:
        return False
    tolerance = max(0.03, median * 0.24)
    # Multiple separate letters with counters supply additional structural
    # evidence. Permit short bridges across word/line gaps, but still require
    # most of the perimeter to follow a common inset and at least three bodies
    # to explain the bubble. Containment alone is never sufficient.
    artwork_group = shared_backer and nested_artwork and len(children) >= 3
    coverage = sum(abs(value-median) <= tolerance for value in nearest)/len(nearest)
    if coverage < (0.70 if artwork_group else 0.8):
        return False
    contributions = [
        sum(abs(row[index]-median) <= tolerance and row[index] <= min(row)+0.01 for row in distances)
        for index in range(len(inner))
    ]
    if artwork_group:
        return sum(count >= 3 for count in contributions) >= 3
    return all(
        count >= max(3, len(distances)*(min(0.08,0.5/len(inner)) if shared_backer else 0.08))
        for count in contributions
    )


def _circle_diameter(entity: Entity) -> float | None:
    if isinstance(entity, Circle):
        return entity.radius * 2.0
    if isinstance(entity, Polyline):
        fit = fit_circular_polyline(entity)
        return fit.radius * 2.0 if fit is not None else None
    return None


def _scaled_for_analysis(entity: Entity, factor: float) -> Entity:
    """Return an ID-preserving coordinate-scaled copy for inch-based rules."""
    clone = copy.deepcopy(entity)
    if isinstance(clone, Line):
        clone.start = Point(clone.start.x * factor, clone.start.y * factor)
        clone.end = Point(clone.end.x * factor, clone.end.y * factor)
    elif isinstance(clone, Circle):
        clone.center = Point(clone.center.x * factor, clone.center.y * factor)
        clone.radius *= factor
    elif isinstance(clone, Rectangle):
        clone.corner_a = Point(clone.corner_a.x * factor, clone.corner_a.y * factor)
        clone.corner_b = Point(clone.corner_b.x * factor, clone.corner_b.y * factor)
    elif isinstance(clone, Polyline):
        clone.points = [Point(point.x * factor, point.y * factor) for point in clone.points]
    elif isinstance(clone, TextEntity):
        clone.insertion = Point(clone.insertion.x * factor, clone.insertion.y * factor)
        clone.height *= factor
    return clone


def _generic_classification(
    entities: list[Entity],
    *,
    drill_diameter: float,
    diameter_tolerance: float,
    shared_cleanouts: dict[str, set[str]] | None = None,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, set[str]]]:
    layers: dict[str, str] = {}
    reasons: dict[str, str] = {}
    confidence: dict[str, str] = {}
    structural: list[Entity] = []

    for entity in entities:
        bounds = entity_bounds(entity)
        width = 0.0 if bounds is None else bounds[2] - bounds[0]
        height = 0.0 if bounds is None else bounds[3] - bounds[1]
        size = max(width, height)
        if entity.metadata.get("layer_prep_generated"):
            layers[entity.id] = entity.layer or LAYER_REVIEW
            reasons[entity.id] = "Previously generated workflow geometry retains its assigned preparation layer."
            confidence[entity.id] = "High"
            continue
        if isinstance(entity, TextEntity):
            layers[entity.id] = LAYER_ID_MARKING
            reasons[entity.id] = "Editable DXF text is documentation/marking, never an automatic cut."
            confidence[entity.id] = "High"
            continue
        if not is_closed_entity(entity):
            layers[entity.id] = LAYER_ID_MARKING if size <= 1.0 else LAYER_REVIEW
            reasons[entity.id] = (
                "Short open geometry is treated as an identification mark."
                if size <= 1.0 else
                "Open geometry over 1 in needs operator review before machining."
            )
            confidence[entity.id] = "Medium" if size <= 1.0 else "Review"
            continue
        diameter = _circle_diameter(entity)
        if diameter is not None and diameter < 0.5:
            if abs(diameter - drill_diameter) <= diameter_tolerance:
                layers[entity.id] = LAYER_DRILL
                reasons[entity.id] = f"Circular feature is {diameter:g} in diameter, within drill tolerance."
                confidence[entity.id] = "High"
            else:
                layers[entity.id] = LAYER_PROFILE_HOLES
                reasons[entity.id] = f"Small circular feature is {diameter:g} in diameter but not the drill size."
                confidence[entity.id] = "High"
            continue
        structural.append(entity)

    # Build immediate containment parents.  Material-frame rectangles are not
    # allowed to make every job feature appear nested inside a part.
    raw_containers = profile_containers(structural) if structural else {}
    by_id = {entity.id: entity for entity in structural}
    areas = {
        entity.id: _area_from_bounds(entity_bounds(entity) or (0.0, 0.0, 0.0, 0.0))
        for entity in structural
    }
    parents: dict[str, str] = {}
    for entity in structural:
        candidates = [
            container_id for container_id in raw_containers.get(entity.id, [])
            if not _is_material_frame(by_id[container_id])
        ]
        if candidates:
            parents[entity.id] = min(candidates, key=lambda entity_id: areas[entity_id])

    # Some DXF producers create independent, nearly coincident offset outlines
    # whose polygon sample is ambiguous.  Pair those roots by parallel distance.
    roots = [entity for entity in structural if entity.id not in parents and not _is_material_frame(entity)]
    root_candidates: list[tuple[float, str, str]] = []
    for index, first in enumerate(roots):
        first_bounds = entity_bounds(first)
        if first_bounds is None:
            continue
        for second in roots[index + 1:]:
            second_bounds = entity_bounds(second)
            if second_bounds is None or _bbox_overlap_ratio(first_bounds, second_bounds) < 0.8:
                continue
            score = _parallel_contour_score(first, second)
            if score is not None:
                root_candidates.append((score, first.id, second.id))
    claimed_roots: set[str] = set()
    forced_pairs: set[frozenset[str]] = set()
    for _score, first_id, second_id in sorted(root_candidates):
        if first_id in claimed_roots or second_id in claimed_roots:
            continue
        parent_id, child_id = (first_id, second_id) if areas[first_id] >= areas[second_id] else (second_id, first_id)
        parents[child_id] = parent_id
        forced_pairs.add(frozenset((parent_id, child_id)))
        claimed_roots.update((parent_id, child_id))

    children: dict[str, list[str]] = {}
    for child_id, parent_id in parents.items():
        children.setdefault(parent_id, []).append(child_id)
    paired: dict[str, set[str]] = {}
    visited: set[str] = set()

    def classify_shared_group(entity_id: str, child_ids: list[str], *, logo=False, remnants=()) -> None:
        descendants = set()
        pending = list(child_ids)
        while pending:
            key = pending.pop()
            if key in descendants:
                continue
            descendants.add(key)
            pending.extend(children.get(key, ()))
        shared_cleanouts[entity_id] = descendants - set(remnants)
        paired[entity_id] = set(child_ids)  # One outer cleanout boundary copy only.
        layers[entity_id] = LAYER_OUTER
        reasons[entity_id] = (
            "Shared logo/backer follows a consistent offset of its detailed interior artwork; deep recesses merge in the outside envelope. "
            if logo else "Shared bubble/backer follows the combined interior artwork. "
        ) + "Only this outer boundary is a cutout; a cleanout boundary copy will enclose the artwork. Review this inferred group before Apply."
        confidence[entity_id] = "Review"
        for key in descendants:
            visited.add(key)
            layers[key] = LAYER_INNER
            reasons[key] = "Interior boundary of a shared backer cleanout: preserve artwork islands and their nested counters in CLEANOUT, not as independent through-cut parts. Review the group before Apply."
            confidence[key] = "Review"
        for key in remnants:
            layers[key] = LAYER_REVIEW
            reasons[key] = "Tiny closed DXF remnant (under 0.01 in across) inside an inferred logo backer. Original preserved; inspect before assigning any machining role."

    def classify_boundary(entity_id: str, boundary_depth: int) -> None:
        if entity_id in visited:
            return
        visited.add(entity_id)
        entity = by_id[entity_id]
        child_ids = children.get(entity_id, [])
        if (shared_cleanouts is not None and boundary_depth == 0
                and not _is_material_frame(entity) and len(child_ids) >= 3
                and _split_offset_match(entity, [by_id[key] for key in child_ids], shared_backer=True,
                    nested_artwork=sum(bool(children.get(key)) for key in child_ids) >= 2)):
            # Several separate letter bodies collectively explain this outer
            # bubble. Their counters are pocket boundaries too, not independent
            # through-cut parts. Do not recursively reinterpret them as offsets.
            classify_shared_group(entity_id, child_ids)
            return
        candidates: list[tuple[float, str]] = []
        split_review = False
        for child_id in child_ids:
            score = _offset_pair_score(entity, by_id[child_id])
            if frozenset((entity_id, child_id)) in forced_pairs:
                score = 0.0
            if score is not None:
                candidates.append((score, child_id))
        pair_ids = [min(candidates)[1]] if candidates else []
        if shared_cleanouts is not None and boundary_depth == 0 and not pair_ids and not _is_material_frame(entity):
            remnants = []
            artwork = []
            for key in child_ids:
                bounds = entity_bounds(by_id[key])
                # Tiny closed DXF debris is not evidence of a second letter.
                # Never delete it or silently turn it into a machining pocket.
                if bounds is not None and math.hypot(bounds[2]-bounds[0], bounds[3]-bounds[1]) < 0.01 and not children.get(key):
                    remnants.append(key)
                else:
                    artwork.append(key)
            if len(artwork) == 1 and _shared_logo_outline_match(entity, by_id[artwork[0]]):
                classify_shared_group(entity_id, child_ids, logo=True, remnants=remnants)
                return
        if not pair_ids and boundary_depth % 2 == 1 and len(child_ids) == 1:
            child_id = child_ids[0]
            outer_bounds = entity_bounds(entity)
            inner_bounds = entity_bounds(by_id[child_id])
            if outer_bounds is not None and inner_bounds is not None:
                margins = _bbox_margins(outer_bounds, inner_bounds)
                if min(margins) >= 0.015 and sum(margins) / 4 <= 1.75:
                    pair_ids = [child_id]
        if not pair_ids and len(child_ids) >= 2:
            split = [child_id for child_id in child_ids if _partial_offset_pair_score(entity, by_id[child_id]) is not None]
            if len(split) >= 2 and _split_offset_match(entity, [by_id[child_id] for child_id in split]):
                pair_ids = split
                split_review = True
            elif _split_offset_match(entity, [by_id[child_id] for child_id in child_ids]):
                pair_ids = child_ids
                split_review = True

        if pair_ids:
            layers[entity_id] = LAYER_OUTER if boundary_depth % 2 == 0 else LAYER_INNER
            reasons[entity_id] = "Recognized as the outer member of a consistent offset-outline pair."
            confidence[entity_id] = "High"
            if split_review:
                reasons[entity_id] = "Several inner fragments jointly follow this boundary at a consistent inset. Review this split-outline group before Apply."
                confidence[entity_id] = "Review"
            paired.setdefault(entity_id, set()).update(pair_ids)
            for pair_id in pair_ids:
                visited.add(pair_id)
                paired.setdefault(pair_id, set()).add(entity_id)
                layers[pair_id] = LAYER_INNER if boundary_depth % 2 == 0 else LAYER_OUTER
                reasons[pair_id] = "Recognized as the inner member of a consistent offset-outline pair."
                confidence[pair_id] = "High"
                if split_review:
                    reasons[pair_id] = "Part of a split inner-outline group, matched collectively to its surrounding contour. Review the proposed cleanout/cut role."
                    confidence[pair_id] = "Review"
                for grandchild_id in children.get(pair_id, []):
                    classify_boundary(grandchild_id, boundary_depth + 1)
            for child_id in child_ids:
                if child_id not in pair_ids:
                    classify_boundary(child_id, boundary_depth + 1)
        else:
            layers[entity_id] = LAYER_OUTER if boundary_depth % 2 == 0 else LAYER_PROFILE_HOLES
            reasons[entity_id] = (
                "Top-level closed contour is an exterior profile."
                if boundary_depth % 2 == 0 else
                "Nested closed contour is a profile opening, not an offset-outline mate."
            )
            confidence[entity_id] = "Medium"
            for child_id in child_ids:
                classify_boundary(child_id, boundary_depth + 1)

    for root in structural:
        if root.id not in parents:
            classify_boundary(root.id, 0)
    for entity in structural:
        if entity.id not in visited:
            layers[entity.id] = LAYER_REVIEW
            reasons[entity.id] = "Containment was ambiguous; no machining role was guessed."
            confidence[entity.id] = "Review"
    return layers, reasons, confidence, paired


def prepare_layer_sorting(
    entities: Iterable[Entity],
    preset: str,
    *,
    drill_diameter: float = 0.25,
    diameter_tolerance: float = 0.006,
    units_per_inch: float = 1.0,
    all_entities: Iterable[Entity] | None = None,
) -> LayerPreparationProposal:
    """Analyze entities and return a non-mutating layer preparation proposal."""
    source = list(entities)
    context = source if all_entities is None else list(all_entities)
    if preset not in set(LAYER_PREP_PRESETS.values()):
        raise ValueError(f"Unsupported layer preparation preset: {preset!r}.")
    if not all(math.isfinite(v) for v in (drill_diameter, diameter_tolerance, units_per_inch)) or drill_diameter <= 0 or diameter_tolerance < 0 or units_per_inch <= 0:
        raise ValueError("Drill diameter and unit scale must be positive; tolerance cannot be negative.")

    # Shop thresholds and the public drill inputs are expressed in inches.
    # Normalize a temporary analysis copy so an mm/cm project classifies the
    # same physical geometry without touching its stored coordinates.
    analysis_source = (
        source if math.isclose(units_per_inch, 1.0)
        else [_scaled_for_analysis(entity, 1.0 / units_per_inch) for entity in source]
    )

    shared_cleanouts: dict[str, set[str]] = {}
    generic, reasons, confidence, paired = _generic_classification(
        analysis_source,
        drill_diameter=drill_diameter,
        diameter_tolerance=diameter_tolerance,
        shared_cleanouts=shared_cleanouts if preset == PRESET_ACRYLIC_CLEAN_CUT else None,
    )
    proposed = dict(generic)
    additions: list[Entity] = []
    shared_interior_ids = set().union(*shared_cleanouts.values()) if shared_cleanouts else set()

    if preset == PRESET_PLAIN_ALUMINUM:
        for entity_id, layer in list(proposed.items()):
            if layer == LAYER_PROFILE_HOLES:
                proposed[entity_id] = LAYER_OUTER
                reasons[entity_id] = "Plain-aluminum preset routes profile openings with the normal profile layer."
    elif preset == PRESET_ACRYLIC_CLEAN_CUT:
        by_id = {entity.id: entity for entity in source}
        existing_cleanout_sources = {
            str(entity.metadata.get("layer_prep_source_entity_id", ""))
            for entity in context
            if entity.metadata.get("layer_prep_generated")
        }
        for entity_id, layer in list(proposed.items()):
            if layer == LAYER_INNER:
                proposed[entity_id] = LAYER_CLEANOUT
                if entity_id not in shared_interior_ids:
                    reasons[entity_id] += " Inner offset outline becomes acrylic cleanout geometry."
            elif layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
                proposed[entity_id] = LAYER_ACRYLIC
                reasons[entity_id] += " Exterior/profile outline becomes acrylic cutout geometry."
                if paired.get(entity_id) and entity_id not in existing_cleanout_sources:
                    duplicate = copy.deepcopy(by_id[entity_id])
                    duplicate.id = new_id("entity")
                    duplicate.layer = LAYER_CLEANOUT
                    duplicate.metadata = {
                        **duplicate.metadata,
                        "layer_prep_generated": True,
                        "layer_prep_source_entity_id": entity_id,
                        "layer_prep_role": "cleanout_outline",
                    }
                    additions.append(duplicate)
    elif preset == PRESET_POLY_BACKS:
        for entity_id, layer in list(proposed.items()):
            if layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
                proposed[entity_id] = LAYER_POLY_BACK
                reasons[entity_id] = "Exterior/profile outline becomes a polycarbonate letter-back cutout."
            elif layer == LAYER_INNER:
                proposed[entity_id] = LAYER_REVIEW
                reasons[entity_id] = "Shelf/recess geometry is not normally part of a poly back; review before use."
                confidence[entity_id] = "Review"

    assignments = tuple(
        LayerAssignment(
            entity_id=entity.id,
            current_layer=entity.layer or "0",
            proposed_layer=proposed.get(entity.id, LAYER_REVIEW),
            role=generic.get(entity.id, LAYER_REVIEW),
            reason=reasons.get(entity.id, "No safe rule matched this geometry."),
            confidence=confidence.get(entity.id, "Review"),
        )
        for entity in source
    )
    proposal = LayerPreparationProposal(preset, assignments, tuple(additions))
    if shared_cleanouts:
        # Reanalysis of an older applied sort must not leave duplicate pocket
        # boundaries at letter counters. Retire only exact, unedited derived
        # cleanout copies; original geometry and explicit choices remain intact.
        interior_ids = shared_interior_ids
        originals = {entity.id:entity for entity in source}
        obsolete = set()
        for entity in context:
            owner = originals.get(str(entity.metadata.get("layer_prep_source_entity_id", "")))
            if (owner is not None and owner.id in interior_ids
                    and entity.metadata.get("layer_prep_generated")
                    and not entity.metadata.get("layer_prep_manual_override")
                    and not owner.metadata.get("layer_prep_manual_choice")
                    and entity.layer == LAYER_CLEANOUT
                    and entity.appearance == owner.appearance
                    and _outline_points(entity) == _outline_points(owner)):
                obsolete.add(entity.id)
        proposal = replace(proposal, assignments=tuple(a for a in assignments if a.entity_id not in obsolete),
                           removed_ids=tuple(sorted(obsolete)))
    for entity in source:
        choice = entity.metadata.get("layer_prep_manual_choice", {})
        if isinstance(choice, dict) and choice.get("preset") == preset and choice.get("target"):
            proposal = override_layer_preparation(proposal, context, {entity.id}, str(choice["target"]))
    return proposal


def override_layer_preparation(
    proposal: LayerPreparationProposal,
    entities: Iterable[Entity],
    selected_rows: Iterable[str],
    target: str,
) -> LayerPreparationProposal:
    """Correct intent and its derived copies together, without mutating a job."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Choose a destination role.")
    if target == LAYER_BOTH and proposal.preset != PRESET_ACRYLIC_CLEAN_CUT:
        raise ValueError("Cutout + cleanout is only available for the acrylic preset.")
    by_id = {entity.id: entity for entity in entities}
    assignments = {item.entity_id: item for item in proposal.assignments}
    additions = list(copy.deepcopy(proposal.additions))
    choices = dict(proposal.manual_choices)
    removed = set(proposal.removed_ids)
    for row in selected_rows:
        if row.startswith("addition:"):
            if target == LAYER_BOTH:
                raise ValueError("Select the original outline, not its generated copy, to use both layers.")
            for addition in additions:
                if row == f"addition:{addition.id}":
                    addition.layer = target
                    addition.metadata["layer_prep_manual_override"] = True
            continue
        if row not in assignments or row not in by_id:
            continue
        source = by_id[row]
        if target == LAYER_BOTH and (source.metadata.get("layer_prep_generated") or not is_closed_entity(source)):
            raise ValueError("Cutout + cleanout requires an original closed outline.")
        actual_layer = LAYER_ACRYLIC if target == LAYER_BOTH else target
        assignments[row] = replace(assignments[row], proposed_layer=actual_layer,
            reason=f"Operator chose {target}. Automatic classification will retain this choice.", confidence="Manual")
        choices[row] = target
        if source.metadata.get("layer_prep_generated"):
            continue
        owned = [entity for entity in by_id.values() if entity.metadata.get("layer_prep_generated")
                 and entity.metadata.get("layer_prep_source_entity_id") == row]
        pending = [entity for entity in additions if entity.metadata.get("layer_prep_source_entity_id") == row]
        if target != LAYER_BOTH:
            for entity in owned + pending:
                if (entity.metadata.get("layer_prep_manual_override") or entity.layer != LAYER_CLEANOUT
                    or entity.appearance != source.appearance or _outline_points(entity) != _outline_points(source)):
                    raise ValueError("This outline has an edited generated copy. Review that copy separately; it will not be deleted automatically.")
            removed.update(entity.id for entity in owned)
            for entity in owned:
                assignments.pop(entity.id, None)
            additions = [entity for entity in additions if entity not in pending]
        elif not any(entity.layer == LAYER_CLEANOUT for entity in owned + pending):
            duplicate = copy.deepcopy(source)
            duplicate.id = new_id("entity")
            duplicate.layer = LAYER_CLEANOUT
            duplicate.metadata = {**duplicate.metadata, "layer_prep_generated": True,
                "layer_prep_source_entity_id": row, "layer_prep_role": "cleanout_outline"}
            duplicate.metadata.pop("layer_prep_manual_choice", None)
            additions.append(duplicate)
        elif owned:
            removed.difference_update(entity.id for entity in owned)
            for entity in owned:
                if entity.id not in assignments:
                    assignments[entity.id] = LayerAssignment(entity.id, entity.layer, entity.layer,
                        "cleanout_outline", "Retained existing generated copy.", "Manual")
    return LayerPreparationProposal(proposal.preset, tuple(assignments.values()), tuple(additions),
        tuple(choices.items()), tuple(sorted(removed)))


def apply_layer_preparation(
    all_entities: Iterable[Entity],
    proposal: LayerPreparationProposal,
) -> list[Entity]:
    """Apply a proposal while preserving existing entity IDs and list order."""
    layer_by_id = {item.entity_id: item.proposed_layer for item in proposal.assignments}
    choices = dict(proposal.manual_choices)
    result: list[Entity] = []
    additions_by_source: dict[str, list[Entity]] = {}
    trailing_additions: list[Entity] = []
    for addition in proposal.additions:
        source_id = str(addition.metadata.get("layer_prep_source_entity_id", ""))
        if source_id:
            additions_by_source.setdefault(source_id, []).append(copy.deepcopy(addition))
        else:
            trailing_additions.append(copy.deepcopy(addition))
    for entity in all_entities:
        if entity.id in proposal.removed_ids:
            continue
        clone = copy.deepcopy(entity)
        if entity.id in layer_by_id:
            clone.layer = layer_by_id[entity.id]
        if entity.id in choices:
            clone.metadata["layer_prep_manual_choice"] = {"preset": proposal.preset, "target": choices[entity.id]}
            if clone.metadata.get("layer_prep_generated"):
                clone.metadata["layer_prep_manual_override"] = True
        result.append(clone)
        result.extend(additions_by_source.get(entity.id, ()))
    result.extend(trailing_additions)
    return result
