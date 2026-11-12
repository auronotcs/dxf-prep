from __future__ import annotations

import argparse
import copy
import math
from collections import Counter, defaultdict, deque
from pathlib import Path


TOLERANCE = 0.002
DRILL_DIAMETER = 0.250
DIAMETER_TOLERANCE = 0.006
SHORT_SEAM_LINE_MAX = 0.012

LAYER_INNER = "INNER_SHELF_CUT_070"
LAYER_OUTER = "OUTER_PROFILE"
LAYER_DRILL = "DRILL_250"
LAYER_PROFILE_HOLES = "PROFILE_HOLES"
LAYER_ID_MARKING = "ID_MARKING"
LAYER_REVIEW = "REVIEW_UNKNOWN"
LAYER_CLEANOUT = "CLEANOUT"
LAYER_ACRYLIC = "ACRYLIC CUTOUT"
LAYER_POLY_BACK = "POLY BACK CUTOUT"

LAYER_COLORS = {
    LAYER_INNER: "3",
    LAYER_OUTER: "7",
    LAYER_DRILL: "1",
    LAYER_PROFILE_HOLES: "5",
    LAYER_ID_MARKING: "8",
    LAYER_REVIEW: "2",
    LAYER_CLEANOUT: "7",
    LAYER_ACRYLIC: "11",
    LAYER_POLY_BACK: "7",
}

ACRYLIC_LAYER_COLORS = {
    LAYER_DRILL: "1",
    LAYER_CLEANOUT: "7",
    LAYER_ACRYLIC: "11",
    LAYER_ID_MARKING: "8",
}

POLY_BACK_LAYER_COLORS = {
    LAYER_DRILL: "1",
    LAYER_POLY_BACK: "7",
    LAYER_ID_MARKING: "8",
}


def read_pairs(path: Path) -> list[tuple[int, str, str]]:
    lines = path.read_text(errors="ignore").splitlines()
    pairs: list[tuple[int, str, str]] = []
    for i in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[i].strip())
        except ValueError:
            continue
        pairs.append((code, lines[i], lines[i + 1]))
    return pairs


def parse_entities(pairs: list[tuple[int, str, str]]) -> list[dict]:
    entities: list[dict] = []
    current = None
    in_entities = False
    for code, _raw_code, raw_value in pairs:
        value = raw_value.strip()
        if code == 2 and value == "ENTITIES":
            in_entities = True
            continue
        if in_entities and code == 0 and value == "ENDSEC":
            if current:
                entities.append(current)
            break
        if not in_entities:
            continue
        if code == 0:
            if current:
                entities.append(current)
            current = {"type": value, "groups": []}
        elif current:
            current["groups"].append((code, value))
    return entities


def group_value(entity: dict, code: int, default: str = "") -> str:
    for item_code, value in entity["groups"]:
        if item_code == code:
            return value
    return default


def group_float(entity: dict, code: int, default: float = 0.0) -> float:
    value = group_value(entity, code)
    return float(value) if value else default


def translate_entity(entity: dict, dx: float, dy: float) -> None:
    """Translate the coordinate groups used by the supported DXF geometry."""
    kind = entity["type"]
    x_codes: set[int] = set()
    y_codes: set[int] = set()
    if kind == "LINE":
        x_codes = {10, 11}
        y_codes = {20, 21}
    elif kind in {"ARC", "CIRCLE", "VERTEX", "LWPOLYLINE", "POINT", "TEXT", "MTEXT", "INSERT"}:
        x_codes = {10}
        y_codes = {20}

    translated = []
    for code, value in entity["groups"]:
        try:
            if code in x_codes:
                value = f"{float(value) + dx:.6f}"
            elif code in y_codes:
                value = f"{float(value) + dy:.6f}"
        except ValueError:
            pass
        translated.append((code, value))
    entity["groups"] = translated


def translate_analysis(analysis: dict, dx: float, dy: float) -> None:
    """Move an analyzed drawing without disturbing its component classifications."""
    for entity in analysis["entities"]:
        translate_entity(entity, dx, dy)

    for segment in analysis["segments"]:
        segment["p1"] = (segment["p1"][0] + dx, segment["p1"][1] + dy)
        segment["p2"] = (segment["p2"][0] + dx, segment["p2"][1] + dy)
        if "center" in segment:
            segment["center"] = (segment["center"][0] + dx, segment["center"][1] + dy)
        if "bbox" in segment:
            min_x, min_y, max_x, max_y = segment["bbox"]
            segment["bbox"] = (min_x + dx, min_y + dy, max_x + dx, max_y + dy)

    for component in analysis["components"]:
        min_x, min_y, max_x, max_y = component["bbox"]
        component["bbox"] = (min_x + dx, min_y + dy, max_x + dx, max_y + dy)
        if "outline_points" in component:
            component["outline_points"] = [
                (point[0] + dx, point[1] + dy) for point in component["outline_points"]
            ]


def point_key(point: tuple[float, float]) -> tuple[int, int]:
    return (math.floor(point[0] / TOLERANCE + 0.5), math.floor(point[1] / TOLERANCE + 0.5))


def arc_endpoint(entity: dict, angle_code: int) -> tuple[float, float]:
    cx = group_float(entity, 10)
    cy = group_float(entity, 20)
    radius = group_float(entity, 40)
    angle = math.radians(group_float(entity, angle_code))
    return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))


def arc_bbox(entity: dict) -> tuple[float, float, float, float]:
    cx = group_float(entity, 10)
    cy = group_float(entity, 20)
    radius = group_float(entity, 40)
    start = group_float(entity, 50) % 360
    end = group_float(entity, 51) % 360
    sweep = (end - start) % 360

    def angle_is_on_arc(angle: float) -> bool:
        return (angle - start) % 360 <= sweep + 1e-9

    angles = [start, end]
    angles.extend(angle for angle in (0.0, 90.0, 180.0, 270.0) if angle_is_on_arc(angle))
    points = [
        (cx + radius * math.cos(math.radians(angle)), cy + radius * math.sin(math.radians(angle)))
        for angle in angles
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def entity_color(entity: dict) -> str:
    return group_value(entity, 62, "0")


def entity_geometry_signature(entity: dict) -> tuple | None:
    kind = entity["type"]
    if kind == "LINE":
        start = (group_float(entity, 10), group_float(entity, 20))
        end = (group_float(entity, 11), group_float(entity, 21))
        if math.hypot(start[0] - end[0], start[1] - end[1]) <= TOLERANCE:
            return None
        p1 = point_key(start)
        p2 = point_key(end)
        return (kind, min(p1, p2), max(p1, p2))
    if kind == "ARC":
        return (
            kind,
            point_key((group_float(entity, 10), group_float(entity, 20))),
            round(group_float(entity, 40) / TOLERANCE),
            round(group_float(entity, 50) / TOLERANCE),
            round(group_float(entity, 51) / TOLERANCE),
        )
    if kind == "CIRCLE":
        radius = group_float(entity, 40)
        if radius <= TOLERANCE:
            return None
        return (
            kind,
            point_key((group_float(entity, 10), group_float(entity, 20))),
            round(radius / TOLERANCE),
        )
    return None


def clean_entities(entities: list[dict]) -> tuple[list[dict], Counter]:
    cleaned = []
    seen: set[tuple] = set()
    removed = Counter()
    for entity in entities:
        kind = entity["type"]
        signature = entity_geometry_signature(entity)
        if kind == "LINE" and signature is None:
            removed["degenerate_lines"] += 1
            continue
        if kind == "CIRCLE" and signature is None:
            removed["degenerate_circles"] += 1
            continue
        if signature is not None:
            if signature in seen:
                removed["duplicate_entities"] += 1
                continue
            seen.add(signature)
        cleaned.append(entity)
    return cleaned, removed


def build_segments(entities: list[dict]) -> list[dict]:
    segments: list[dict] = []
    active_polyline = None
    for index, entity in enumerate(entities):
        kind = entity["type"]
        if kind == "LINE":
            p1 = (group_float(entity, 10), group_float(entity, 20))
            p2 = (group_float(entity, 11), group_float(entity, 21))
            if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) <= TOLERANCE:
                continue
            segments.append(
                {
                    "entity": index,
                    "type": "LINE",
                    "color": entity_color(entity),
                    "p1": p1,
                    "p2": p2,
                }
            )
        elif kind == "ARC":
            segments.append(
                {
                    "entity": index,
                    "type": "ARC",
                    "color": entity_color(entity),
                    "p1": arc_endpoint(entity, 50),
                    "p2": arc_endpoint(entity, 51),
                    "center": (group_float(entity, 10), group_float(entity, 20)),
                    "radius": group_float(entity, 40),
                    "start_angle": group_float(entity, 50),
                    "end_angle": group_float(entity, 51),
                    "bbox": arc_bbox(entity),
                }
            )
        elif kind == "CIRCLE":
            cx = group_float(entity, 10)
            cy = group_float(entity, 20)
            radius = group_float(entity, 40)
            if radius <= TOLERANCE:
                continue
            p_right = (cx + radius, cy)
            p_left = (cx - radius, cy)
            bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
            for p1, p2, start_angle, end_angle in (
                (p_right, p_left, 0.0, 180.0),
                (p_left, p_right, 180.0, 360.0),
            ):
                segments.append(
                    {
                        "entity": index,
                        "type": "CIRCLE_SEG",
                        "color": entity_color(entity),
                        "p1": p1,
                        "p2": p2,
                        "center": (cx, cy),
                        "radius": radius,
                        "start_angle": start_angle,
                        "end_angle": end_angle,
                        "bbox": bbox,
                    }
                )
        elif kind == "POLYLINE":
            active_polyline = {
                "entity": index,
                "color": entity_color(entity),
                "closed": bool(int(group_value(entity, 70, "0")) & 1),
                "points": [],
            }
        elif kind == "VERTEX" and active_polyline is not None:
            active_polyline["points"].append((group_float(entity, 10), group_float(entity, 20)))
        elif kind == "SEQEND" and active_polyline is not None:
            points = active_polyline["points"]
            pairs = list(zip(points, points[1:]))
            if active_polyline["closed"] and len(points) > 2 and point_key(points[0]) != point_key(points[-1]):
                pairs.append((points[-1], points[0]))
            for p1, p2 in pairs:
                if point_key(p1) == point_key(p2):
                    continue
                segments.append(
                    {
                        "entity": active_polyline["entity"],
                        "type": "POLYLINE_SEG",
                        "color": active_polyline["color"],
                        "p1": p1,
                        "p2": p2,
                    }
                )
            active_polyline = None
        elif kind == "LWPOLYLINE":
            points = []
            pending_x = None
            for code, value in entity["groups"]:
                if code == 10:
                    pending_x = float(value)
                elif code == 20 and pending_x is not None:
                    points.append((pending_x, float(value)))
                    pending_x = None
            closed = bool(int(group_value(entity, 70, "0")) & 1)
            pairs = list(zip(points, points[1:]))
            if closed and len(points) > 2 and point_key(points[0]) != point_key(points[-1]):
                pairs.append((points[-1], points[0]))
            for p1, p2 in pairs:
                if point_key(p1) == point_key(p2):
                    continue
                segments.append(
                    {
                        "entity": index,
                        "type": "LWPOLYLINE_SEG",
                        "color": entity_color(entity),
                        "p1": p1,
                        "p2": p2,
                    }
                )
    return segments


def endpoint_node_map(segments: list[dict]) -> dict[tuple[int, int, int], int]:
    nodes: list[tuple[float, float]] = []
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    node_by_endpoint: dict[tuple[int, int, int], int] = {}

    for segment_index, segment in enumerate(segments):
        for endpoint_index, point in enumerate((segment["p1"], segment["p2"])):
            cell = (math.floor(point[0] / TOLERANCE), math.floor(point[1] / TOLERANCE))
            node_id = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for candidate_id in grid.get((cell[0] + dx, cell[1] + dy), []):
                        candidate = nodes[candidate_id]
                        if math.hypot(point[0] - candidate[0], point[1] - candidate[1]) <= TOLERANCE:
                            node_id = candidate_id
                            break
                    if node_id is not None:
                        break
                if node_id is not None:
                    break
            if node_id is None:
                node_id = len(nodes)
                nodes.append(point)
                grid[cell].append(node_id)
            node_by_endpoint[(segment_index, endpoint_index, 0)] = node_id
    return node_by_endpoint


def connected_components(segments: list[dict]) -> list[dict]:
    endpoint_nodes = endpoint_node_map(segments)
    adjacency: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for index, segment in enumerate(segments):
        a = endpoint_nodes[(index, 0, 0)]
        b = endpoint_nodes[(index, 1, 0)]
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))

    components = []
    seen: set[int] = set()
    for start in adjacency:
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        nodes = []
        segment_indexes: set[int] = set()
        while queue:
            node = queue.popleft()
            nodes.append(node)
            for neighbor, segment_index in adjacency[node]:
                segment_indexes.add(segment_index)
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)

        points = [
            point
            for segment_index in segment_indexes
            for point in (segments[segment_index]["p1"], segments[segment_index]["p2"])
        ]
        boxes = [segments[segment_index]["bbox"] for segment_index in segment_indexes if "bbox" in segments[segment_index]]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if boxes:
            xs.extend([value for box in boxes for value in (box[0], box[2])])
            ys.extend([value for box in boxes for value in (box[1], box[3])])
        degrees = [len(adjacency[node]) for node in nodes]
        colors = Counter(segments[segment_index]["color"] for segment_index in segment_indexes)
        odd_degrees = [degree for degree in degrees if degree % 2 != 0]
        components.append(
            {
                "segment_indexes": segment_indexes,
                "entity_indexes": {segments[i]["entity"] for i in segment_indexes},
                "closed": not odd_degrees,
                "open_nodes": len(odd_degrees),
                "branch_nodes": sum(1 for degree in degrees if degree > 2),
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "color": colors.most_common(1)[0][0],
            }
        )
    return components


def sampled_segment_points(segment: dict, reverse: bool = False) -> list[tuple[float, float]]:
    if "center" not in segment or "start_angle" not in segment:
        points = [segment["p1"], segment["p2"]]
        return list(reversed(points)) if reverse else points
    cx, cy = segment["center"]
    radius = segment["radius"]
    start = segment["start_angle"]
    end = segment["end_angle"]
    sweep = end - start
    if sweep < 0:
        sweep += 360
    steps = max(4, min(72, int(abs(sweep) / 6) + 1))
    points = [
        (
            cx + radius * math.cos(math.radians(start + sweep * step / steps)),
            cy + radius * math.sin(math.radians(start + sweep * step / steps)),
        )
        for step in range(steps + 1)
    ]
    return list(reversed(points)) if reverse else points


def component_outline_points(component: dict, segments: list[dict]) -> list[tuple[float, float]]:
    cached = component.get("outline_points")
    if cached:
        return cached
    indexes = set(component["segment_indexes"])
    by_endpoint: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index in indexes:
        by_endpoint[point_key(segments[index]["p1"])].append(index)
        by_endpoint[point_key(segments[index]["p2"])].append(index)
    if not indexes:
        return []
    current_index = min(indexes)
    current_key = point_key(segments[current_index]["p1"])
    unused = set(indexes)
    points: list[tuple[float, float]] = []
    while unused:
        candidates = [index for index in by_endpoint.get(current_key, []) if index in unused]
        if not candidates:
            break
        current_index = min(candidates)
        segment = segments[current_index]
        reverse = point_key(segment["p2"]) == current_key
        segment_points = sampled_segment_points(segment, reverse)
        points.extend(segment_points if not points else segment_points[1:])
        current_key = point_key(segment_points[-1])
        unused.remove(current_index)
    component["outline_points"] = points
    return points


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def component_contains(outer: dict, inner: dict, segments: list[dict]) -> bool:
    if not has_strict_container(inner, [outer["bbox"]]):
        return False
    if offset_pair_score(outer, inner, segments) is not None:
        return True
    outer_points = component_outline_points(outer, segments)
    inner_points = component_outline_points(inner, segments)
    if not outer_points or not inner_points:
        return False
    sample_step = max(1, len(inner_points) // 12)
    samples = inner_points[::sample_step]
    return sum(point_in_polygon(point, outer_points) for point in samples) >= max(1, len(samples) - 1)


def point_to_polyline_distance(point: tuple[float, float], points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return math.inf
    px, py = point
    best = math.inf
    for start, end in zip(points, points[1:] + points[:1]):
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            distance = math.hypot(px - x1, py - y1)
        else:
            amount = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_squared))
            distance = math.hypot(px - (x1 + amount * dx), py - (y1 + amount * dy))
        best = min(best, distance)
    return best


def parallel_contour_score(first: dict, second: dict, segments: list[dict]) -> float | None:
    first_points = component_outline_points(first, segments)
    second_points = component_outline_points(second, segments)
    if len(first_points) < 3 or len(second_points) < 3:
        return None
    first_step = max(1, len(first_points) // 72)
    second_step = max(1, len(second_points) // 72)
    distances = [point_to_polyline_distance(point, second_points) for point in first_points[::first_step]]
    distances.extend(point_to_polyline_distance(point, first_points) for point in second_points[::second_step])
    distances = sorted(distance for distance in distances if math.isfinite(distance))
    if not distances:
        return None
    median = distances[len(distances) // 2]
    sixty_percent = distances[min(len(distances) - 1, math.floor(len(distances) * 0.6))]
    if median < 0.015 or median > 0.2 or sixty_percent > 0.35:
        return None
    return median + sixty_percent


def bbox_overlap_ratio(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    overlap_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    overlap_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    overlap = overlap_width * overlap_height
    first_area = max((first[2] - first[0]) * (first[3] - first[1]), TOLERANCE)
    second_area = max((second[2] - second[0]) * (second[3] - second[1]), TOLERANCE)
    return overlap / min(first_area, second_area)


def is_material_frame(component: dict) -> bool:
    min_x, min_y, max_x, max_y = component["bbox"]
    width = max_x - min_x
    height = max_y - min_y
    dimensions = sorted((width, height))
    return len(component["segment_indexes"]) <= 8 and any(
        abs(dimensions[0] - short) <= 0.1 and abs(dimensions[1] - long) <= 0.1
        for short, long in ((48.0, 96.0), (48.0, 120.0), (60.0, 120.0))
    )


def offset_pair_score(outer: dict, inner: dict, segments: list[dict] | None = None) -> float | None:
    margins = bbox_margins(outer["bbox"], inner["bbox"])
    average = sum(margins) / len(margins)
    spread = max(margins) - min(margins)
    if min(margins) >= 0.015 and average <= 0.75 and spread <= max(0.12, average * 0.45):
        return average + spread
    if segments is None:
        return None

    outer_points = component_outline_points(outer, segments)
    inner_points = component_outline_points(inner, segments)
    if len(outer_points) < 3 or len(inner_points) < 3:
        return None
    outer_step = max(1, len(outer_points) // 36)
    inner_step = max(1, len(inner_points) // 36)
    distances = [point_to_polyline_distance(point, inner_points) for point in outer_points[::outer_step]]
    distances.extend(point_to_polyline_distance(point, outer_points) for point in inner_points[::inner_step])
    distances = sorted(distance for distance in distances if math.isfinite(distance))
    if not distances:
        return None
    median = distances[len(distances) // 2]
    upper = distances[min(len(distances) - 1, math.floor(len(distances) * 0.9))]
    lower = distances[math.floor(len(distances) * 0.1)]
    if median < 0.015 or median > 0.8 or upper > 1.05 or upper - lower > 0.4:
        return None
    return median + (upper - lower)


def partial_offset_pair_score(outer: dict, inner: dict) -> float | None:
    """Recognize one section of an inset when adjacent shapes share an exterior."""
    margins = bbox_margins(outer["bbox"], inner["bbox"])
    inset_margins = [margin for margin in margins if 0.015 <= margin <= 0.75]
    if len(inset_margins) < 3:
        return None
    average = sum(inset_margins) / len(inset_margins)
    spread = max(inset_margins) - min(inset_margins)
    if spread > max(0.12, average * 0.45):
        return None
    return average + spread


def is_split_offset_section(outer: dict, inner: dict) -> bool:
    """Identify one of multiple inset sections sharing a single exterior."""
    margins = bbox_margins(outer["bbox"], inner["bbox"])
    inset_count = sum(0.015 <= margin <= 0.75 for margin in margins)
    return inset_count == 3 and partial_offset_pair_score(outer, inner) is not None


def classify_components_topologically(components: list[dict], segments: list[dict]) -> None:
    structural_indexes = []
    for index, component in enumerate(components):
        component.pop("paired_with", None)
        min_x, min_y, max_x, max_y = component["bbox"]
        width = max_x - min_x
        height = max_y - min_y
        diameter = (width + height) / 2
        if not component["closed"]:
            component["layer"] = LAYER_ID_MARKING if max(width, height) <= 1.0 else LAYER_REVIEW
            continue
        if abs(width - height) <= DIAMETER_TOLERANCE and diameter < 0.5:
            component["layer"] = (
                LAYER_DRILL if abs(diameter - DRILL_DIAMETER) <= DIAMETER_TOLERANCE else LAYER_PROFILE_HOLES
            )
            continue
        structural_indexes.append(index)

    parents: dict[int, int] = {}
    for inner_index in structural_indexes:
        inner = components[inner_index]
        containers = []
        for outer_index in structural_indexes:
            if outer_index == inner_index:
                continue
            outer = components[outer_index]
            if is_material_frame(outer):
                continue
            if component_contains(outer, inner, segments):
                min_x, min_y, max_x, max_y = outer["bbox"]
                containers.append(((max_x - min_x) * (max_y - min_y), outer_index))
        if containers:
            parents[inner_index] = min(containers)[1]

    forced_pairs: set[frozenset[int]] = set()
    root_indexes = [index for index in structural_indexes if index not in parents and not is_material_frame(components[index])]
    root_candidates = []
    for position, first_index in enumerate(root_indexes):
        first = components[first_index]
        for second_index in root_indexes[position + 1 :]:
            second = components[second_index]
            if bbox_overlap_ratio(first["bbox"], second["bbox"]) < 0.8:
                continue
            score = parallel_contour_score(first, second, segments)
            if score is not None:
                root_candidates.append((score, first_index, second_index))
    claimed_roots: set[int] = set()
    for _score, first_index, second_index in sorted(root_candidates):
        if first_index in claimed_roots or second_index in claimed_roots:
            continue
        first = components[first_index]
        second = components[second_index]
        first_area = (first["bbox"][2] - first["bbox"][0]) * (first["bbox"][3] - first["bbox"][1])
        second_area = (second["bbox"][2] - second["bbox"][0]) * (second["bbox"][3] - second["bbox"][1])
        parent_index, child_index = (first_index, second_index) if first_area >= second_area else (second_index, first_index)
        parents[child_index] = parent_index
        forced_pairs.add(frozenset((parent_index, child_index)))
        claimed_roots.update((parent_index, child_index))

    children: dict[int, list[int]] = defaultdict(list)
    for child_index, parent_index in parents.items():
        children[parent_index].append(child_index)

    visited: set[int] = set()

    def classify_boundary(index: int, boundary_depth: int) -> None:
        if index in visited:
            return
        visited.add(index)
        component = components[index]
        child_indexes = children.get(index, [])
        pair_candidates = []
        for child_index in child_indexes:
            score = offset_pair_score(component, components[child_index], segments)
            if frozenset((index, child_index)) in forced_pairs:
                score = 0.0
            if score is not None:
                pair_candidates.append((score, child_index))
        pair_indexes = [min(pair_candidates)[1]] if pair_candidates else []
        if pair_indexes and len(child_indexes) >= 2:
            primary_index = pair_indexes[0]
            if is_split_offset_section(component, components[primary_index]):
                split_siblings = [
                    child_index
                    for child_index in child_indexes
                    if child_index != primary_index
                    and is_split_offset_section(component, components[child_index])
                ]
                if split_siblings:
                    pair_indexes.extend(split_siblings)
        if not pair_indexes and boundary_depth % 2 == 1 and len(child_indexes) == 1:
            child_index = child_indexes[0]
            margins = bbox_margins(component["bbox"], components[child_index]["bbox"])
            if min(margins) >= 0.015 and sum(margins) / len(margins) <= 1.75:
                pair_indexes = [child_index]

        if not pair_indexes and len(child_indexes) >= 2:
            split_candidates = [
                child_index
                for child_index in child_indexes
                if partial_offset_pair_score(component, components[child_index]) is not None
            ]
            if len(split_candidates) >= 2:
                pair_indexes = split_candidates

        if pair_indexes:
            component["paired_with"] = pair_indexes[0] if len(pair_indexes) == 1 else tuple(pair_indexes)
            pair_indexes_set = set(pair_indexes)
            for pair_index in pair_indexes:
                pair = components[pair_index]
                pair["paired_with"] = index
                pair["layer"] = LAYER_INNER if boundary_depth % 2 == 0 else LAYER_OUTER
                visited.add(pair_index)
            if boundary_depth % 2 == 0:
                component["layer"] = LAYER_OUTER
            else:
                component["layer"] = LAYER_INNER
            for pair_index in pair_indexes:
                for grandchild_index in children.get(pair_index, []):
                    classify_boundary(grandchild_index, boundary_depth + 1)
            for child_index in child_indexes:
                if child_index not in pair_indexes_set:
                    classify_boundary(child_index, boundary_depth + 1)
        else:
            component["layer"] = LAYER_OUTER if boundary_depth % 2 == 0 else LAYER_PROFILE_HOLES
            for child_index in child_indexes:
                classify_boundary(child_index, boundary_depth + 1)

    roots = [index for index in structural_indexes if index not in parents]
    for root_index in roots:
        classify_boundary(root_index, 0)
    for index in structural_indexes:
        if index not in visited:
            components[index]["layer"] = LAYER_REVIEW


def classify_component(component: dict) -> str:
    min_x, min_y, max_x, max_y = component["bbox"]
    width = max_x - min_x
    height = max_y - min_y
    diameter = (width + height) / 2

    if not component["closed"]:
        if max(width, height) <= 1.0:
            return LAYER_ID_MARKING
        return LAYER_REVIEW
    if abs(width - height) <= DIAMETER_TOLERANCE and diameter < 0.5:
        if abs(diameter - DRILL_DIAMETER) <= DIAMETER_TOLERANCE:
            return LAYER_DRILL
        return LAYER_PROFILE_HOLES
    return LAYER_REVIEW


def replace_layer(entity: dict, layer: str) -> None:
    groups = entity["groups"]
    for index, (code, _value) in enumerate(groups):
        if code == 8:
            groups[index] = (code, layer)
            return
    groups.insert(0, (8, layer))


def write_entities_to_layers(entities: list[dict], layers_by_entity: dict[int, str]) -> None:
    current_polyline_layer = LAYER_REVIEW
    for index, entity in enumerate(entities):
        kind = entity["type"]
        layer = layers_by_entity.get(index, group_value(entity, 8, LAYER_REVIEW))
        if kind == "POLYLINE":
            current_polyline_layer = layer
        elif kind in {"VERTEX", "SEQEND"}:
            layer = current_polyline_layer
        replace_layer(entity, layer)


def render_entity(entity: dict) -> list[str]:
    lines = ["  0", entity["type"]]
    for code, value in entity["groups"]:
        lines.extend([f"{code:3d}", value])
    return lines


def layer_record(name: str, color: str) -> list[str]:
    return [
        "  0",
        "LAYER",
        "  2",
        name,
        " 70",
        "0",
        " 62",
        color,
        "  6",
        "CONTINUOUS",
    ]


def write_dxf(
    original_pairs: list[tuple[int, str, str]],
    entities: list[dict],
    output: Path,
    layer_colors: dict[str, str] | None = None,
) -> None:
    if layer_colors is None:
        layer_colors = LAYER_COLORS
    out: list[str] = []
    in_entities = False
    in_layer_table = False
    inserted_layers = False
    entity_written = False
    i = 0
    while i < len(original_pairs):
        code, raw_code, raw_value = original_pairs[i]
        value = raw_value.strip()

        if code == 0 and value == "TABLE":
            next_pair = original_pairs[i + 1] if i + 1 < len(original_pairs) else None
            in_layer_table = bool(next_pair and next_pair[0] == 2 and next_pair[2].strip() == "LAYER")

        if in_layer_table and code == 70:
            raw_value = str(int(value) + len(layer_colors))

        if in_layer_table and code == 0 and value == "ENDTAB" and not inserted_layers:
            for layer_name, color in layer_colors.items():
                out.extend(layer_record(layer_name, color))
            inserted_layers = True
            in_layer_table = False

        if code == 2 and value == "ENTITIES":
            out.extend([raw_code, raw_value])
            in_entities = True
            i += 1
            continue

        if in_entities and code == 0 and value == "ENDSEC":
            if not entity_written:
                for entity in entities:
                    out.extend(render_entity(entity))
                entity_written = True
            out.extend([raw_code, raw_value])
            in_entities = False
            i += 1
            continue

        if in_entities:
            i += 1
            continue

        out.extend([raw_code, raw_value])
        i += 1

    output.write_text("\n".join(out) + "\n")


def analyze_dxf(input_path: Path) -> dict:
    pairs = read_pairs(input_path)
    raw_entities = parse_entities(pairs)
    entities, cleanup_summary = clean_entities(raw_entities)
    segments = build_segments(entities)
    components = connected_components(segments)

    layers_by_entity: dict[int, str] = {}
    summary = Counter()
    classify_components_topologically(components, segments)
    for component in components:
        layer = component["layer"]
        summary[layer] += 1
        for entity_index in component["entity_indexes"]:
            layers_by_entity[entity_index] = layer

    return {
        "pairs": pairs,
        "raw_entities": raw_entities,
        "entities": entities,
        "segments": segments,
        "components": components,
        "layers_by_entity": layers_by_entity,
        "summary": summary,
        "cleanup_summary": cleanup_summary,
    }


def export_classified_dxf(analysis: dict, output: Path) -> None:
    layers_by_entity: dict[int, str] = {}
    for component in analysis["components"]:
        layer = component.get("layer", classify_component(component))
        for entity_index in component["entity_indexes"]:
            layers_by_entity[entity_index] = layer
    entities = copy.deepcopy(analysis["entities"])
    write_entities_to_layers(entities, layers_by_entity)
    write_dxf(analysis["pairs"], entities, output)


def export_plain_aluminum_dxf(analysis: dict, output: Path) -> None:
    layers_by_entity: dict[int, str] = {}
    for component in analysis["components"]:
        layer = component["layer"] if "layer" in component else classify_component(component)
        if layer == LAYER_PROFILE_HOLES:
            layer = LAYER_OUTER
        for entity_index in component["entity_indexes"]:
            layers_by_entity[entity_index] = layer
    entities = copy.deepcopy(analysis["entities"])
    write_entities_to_layers(entities, layers_by_entity)
    write_dxf(analysis["pairs"], entities, output)


def filter_entities_for_layer_map(entities: list[dict], layer_by_root: dict[int, str]) -> list[dict]:
    filtered: list[dict] = []
    include_polyline = False
    polyline_layer = LAYER_REVIEW
    for index, entity in enumerate(entities):
        kind = entity["type"]
        if kind == "POLYLINE":
            include_polyline = index in layer_by_root
            if include_polyline:
                polyline_layer = layer_by_root[index]
                replace_layer(entity, polyline_layer)
                filtered.append(entity)
        elif kind in {"VERTEX", "SEQEND"}:
            if include_polyline:
                replace_layer(entity, polyline_layer)
                filtered.append(entity)
            if kind == "SEQEND":
                include_polyline = False
        elif index in layer_by_root:
            replace_layer(entity, layer_by_root[index])
            filtered.append(entity)
    return filtered


def acrylic_feature_role(component: dict) -> str | None:
    return acrylic_feature_role_with_context(component, [])


def bbox_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tolerance: float = 0.05,
) -> bool:
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[2] + tolerance >= inner[2]
        and outer[3] + tolerance >= inner[3]
    )


def bbox_margins(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return (
        inner[0] - outer[0],
        inner[1] - outer[1],
        outer[2] - inner[2],
        outer[3] - inner[3],
    )


def is_consistent_inset(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    minimum_inset: float = 0.05,
    inset_tolerance: float = 0.08,
) -> bool:
    if not bbox_contains(outer, inner):
        return False
    margins = bbox_margins(outer, inner)
    if any(margin < minimum_inset for margin in margins):
        return False
    average = sum(margins) / len(margins)
    return all(abs(margin - average) <= inset_tolerance for margin in margins)


def has_consistent_inset_container(
    component: dict,
    outside_bounds: list[tuple[float, float, float, float]],
) -> bool:
    return any(is_consistent_inset(outside, component["bbox"]) for outside in outside_bounds)


def has_strict_container(
    component: dict,
    outside_bounds: list[tuple[float, float, float, float]],
    minimum_margin: float = 0.01,
) -> bool:
    inner = component["bbox"]
    for outer in outside_bounds:
        if not bbox_contains(outer, inner):
            continue
        margins = bbox_margins(outer, inner)
        if max(margins) >= minimum_margin and min(margins) >= -TOLERANCE:
            return True
    return False


def acrylic_feature_role_with_context(
    component: dict,
    outside_bounds: list[tuple[float, float, float, float]],
) -> str | None:
    layer = component.get("layer", classify_component(component))
    if layer == LAYER_DRILL:
        return "drill"
    if layer == LAYER_INNER:
        return "inside"
    if layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
        return "outside"
    return None


def copy_entities_to_layer(entities: list[dict], root_indexes: set[int], layer: str) -> list[dict]:
    copied = copy.deepcopy(entities)
    return filter_entities_for_layer_map(copied, {index: layer for index in root_indexes})


def line_points(entity: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    return (
        (group_float(entity, 10), group_float(entity, 20)),
        (group_float(entity, 11), group_float(entity, 21)),
    )


def set_line_points(entity: dict, p1: tuple[float, float], p2: tuple[float, float]) -> None:
    replacements = {
        10: f"{p1[0]:.6f}",
        20: f"{p1[1]:.6f}",
        11: f"{p2[0]:.6f}",
        21: f"{p2[1]:.6f}",
    }
    for index, (code, value) in enumerate(entity["groups"]):
        if code in replacements:
            entity["groups"][index] = (code, replacements[code])


def point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def line_length(entity: dict) -> float:
    p1, p2 = line_points(entity)
    return point_distance(p1, p2)


def connected_collinear_short_lines(first: dict, second: dict) -> tuple[tuple[float, float], tuple[float, float]] | None:
    first_points = line_points(first)
    second_points = line_points(second)
    connections = []
    for first_index, first_point in enumerate(first_points):
        for second_index, second_point in enumerate(second_points):
            if point_distance(first_point, second_point) <= TOLERANCE:
                connections.append((first_index, second_index))
    if not connections:
        return None

    first_length = line_length(first)
    second_length = line_length(second)
    if first_length > SHORT_SEAM_LINE_MAX or second_length > SHORT_SEAM_LINE_MAX:
        return None

    first_vector = (first_points[1][0] - first_points[0][0], first_points[1][1] - first_points[0][1])
    second_vector = (second_points[1][0] - second_points[0][0], second_points[1][1] - second_points[0][1])
    cross = abs(first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0])
    if cross > 0.00001:
        return None

    first_shared, second_shared = connections[0]
    start = first_points[1 - first_shared]
    end = second_points[1 - second_shared]
    if point_distance(start, end) > SHORT_SEAM_LINE_MAX * 2:
        return None
    return start, end


def collapse_short_seam_lines(entities: list[dict]) -> list[dict]:
    remove_indexes: set[int] = set()
    components = connected_components(build_segments(entities))
    for component in components:
        line_indexes = [
            index
            for index in sorted(component["entity_indexes"])
            if index < len(entities)
            and entities[index]["type"] == "LINE"
            and line_length(entities[index]) <= SHORT_SEAM_LINE_MAX
        ]
        for i, first_index in enumerate(line_indexes):
            if first_index in remove_indexes:
                continue
            for second_index in line_indexes[i + 1 :]:
                if second_index in remove_indexes:
                    continue
                merged = connected_collinear_short_lines(entities[first_index], entities[second_index])
                if not merged:
                    continue
                set_line_points(entities[first_index], merged[0], merged[1])
                remove_indexes.add(second_index)
                break
    if not remove_indexes:
        return entities
    return [entity for index, entity in enumerate(entities) if index not in remove_indexes]


def component_arc_diameter(component: dict, segments: list[dict]) -> float | None:
    radii = [
        segments[index].get("radius", 0.0)
        for index in component["segment_indexes"]
        if index < len(segments) and segments[index]["type"] == "ARC"
    ]
    if not radii:
        return None
    return 2 * max(radii)


def component_drill_circle(component: dict, segments: list[dict], layer: str, color: str | None = None) -> dict:
    arc_segments = [
        segments[index]
        for index in component["segment_indexes"]
        if index < len(segments) and segments[index]["type"] == "ARC"
    ]
    if arc_segments:
        center_x = sum(segment["center"][0] for segment in arc_segments) / len(arc_segments)
        center_y = sum(segment["center"][1] for segment in arc_segments) / len(arc_segments)
        radius = sum(segment["radius"] for segment in arc_segments) / len(arc_segments)
    else:
        min_x, min_y, max_x, max_y = component["bbox"]
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        radius = ((max_x - min_x) + (max_y - min_y)) / 4
    return {
        "type": "CIRCLE",
        "groups": [
            (8, layer),
            (62, color or LAYER_COLORS.get(layer, "7")),
            (10, f"{center_x:.6f}"),
            (20, f"{center_y:.6f}"),
            (30, "0.0"),
            (40, f"{radius:.6f}"),
        ],
    }


def is_poly_back_drill(component: dict, segments: list[dict]) -> bool:
    if not component.get("closed"):
        return False
    arc_diameter = component_arc_diameter(component, segments)
    if arc_diameter is not None:
        return abs(arc_diameter - DRILL_DIAMETER) <= DIAMETER_TOLERANCE

    min_x, min_y, max_x, max_y = component["bbox"]
    width = max_x - min_x
    height = max_y - min_y
    diameter = (width + height) / 2
    return abs(width - height) <= DIAMETER_TOLERANCE and abs(diameter - DRILL_DIAMETER) <= DIAMETER_TOLERANCE


def is_poly_back_exterior(component: dict) -> bool:
    min_x, min_y, max_x, max_y = component["bbox"]
    width = max_x - min_x
    height = max_y - min_y
    size = max(width, height)
    area = width * height
    layer = component.get("layer", classify_component(component))
    if not component.get("closed"):
        return component.get("open_nodes") == 2 and size > 1.0 and area > 1.0
    if layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
        return True
    return layer == LAYER_REVIEW and size > 1.0 and area > 1.0


def component_open_points(component: dict, segments: list[dict]) -> list[tuple[float, float]]:
    endpoints_by_key: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for segment_index in component["segment_indexes"]:
        segment = segments[segment_index]
        endpoints_by_key[point_key(segment["p1"])].append(segment["p1"])
        endpoints_by_key[point_key(segment["p2"])].append(segment["p2"])
    return [points[0] for points in endpoints_by_key.values() if len(points) == 1]


def connector_line(p1: tuple[float, float], p2: tuple[float, float], layer: str) -> dict:
    return {
        "type": "LINE",
        "groups": [
            (8, layer),
            (62, LAYER_COLORS.get(layer, "7")),
            (10, f"{p1[0]:.6f}"),
            (20, f"{p1[1]:.6f}"),
            (30, "0.0"),
            (11, f"{p2[0]:.6f}"),
            (21, f"{p2[1]:.6f}"),
            (31, "0.0"),
        ],
    }


def append_small_gap_connectors(
    entities: list[dict],
    layers: list[str],
    max_gap: float = 0.05,
) -> list[dict]:
    additions: list[dict] = []
    for layer in layers:
        layer_entities = [entity for entity in entities if group_value(entity, 8, "") == layer]
        layer_segments = build_segments(layer_entities)
        open_points: list[tuple[float, float]] = []
        for component in connected_components(layer_segments):
            if component.get("closed"):
                continue
            open_points.extend(component_open_points(component, layer_segments))

        used: set[int] = set()
        pairs = []
        for first_index, first in enumerate(open_points):
            for second_index in range(first_index + 1, len(open_points)):
                second = open_points[second_index]
                distance = point_distance(first, second)
                if distance <= max_gap:
                    pairs.append((distance, first_index, second_index))
        for _distance, first_index, second_index in sorted(pairs):
            if first_index in used or second_index in used:
                continue
            additions.append(connector_line(open_points[first_index], open_points[second_index], layer))
            used.update({first_index, second_index})
    return entities + additions


def polyline_child_indexes(entities: list[dict], root_indexes: set[int]) -> set[int]:
    indexes: set[int] = set()
    include_polyline = False
    for index, entity in enumerate(entities):
        if entity["type"] == "POLYLINE":
            include_polyline = index in root_indexes
        if index in root_indexes or include_polyline:
            indexes.add(index)
        if entity["type"] == "SEQEND":
            include_polyline = False
    return indexes


def export_cleaned_dxf(analysis: dict, output: Path) -> Counter:
    entities = copy.deepcopy(analysis["entities"])
    layers_by_entity: dict[int, str] = {}
    replacements_by_entity: dict[int, dict] = {}
    skip_indexes: set[int] = set()
    cleanup = Counter(analysis.get("cleanup_summary", Counter()))

    for component in analysis["components"]:
        layer = component.get("layer", classify_component(component))
        rebuild_drill = is_poly_back_drill(component, analysis["segments"])
        if rebuild_drill:
            layer = LAYER_DRILL
        for entity_index in component["entity_indexes"]:
            layers_by_entity[entity_index] = layer
        if not rebuild_drill:
            continue
        root_indexes = set(component["entity_indexes"])
        root_and_children = polyline_child_indexes(entities, root_indexes)
        first_index = min(root_and_children)
        replacements_by_entity[first_index] = component_drill_circle(
            component,
            analysis["segments"],
            layer,
            LAYER_COLORS.get(layer, component.get("color", "7")),
        )
        skip_indexes.update(root_and_children)
        cleanup["drill_circles_rebuilt"] += 1

    write_entities_to_layers(entities, layers_by_entity)

    cleaned_entities = []
    for index, entity in enumerate(entities):
        if index in replacements_by_entity:
            cleaned_entities.append(replacements_by_entity[index])
        if index in skip_indexes:
            continue
        cleaned_entities.append(entity)
    cleaned_entities = collapse_short_seam_lines(cleaned_entities)
    write_dxf(analysis["pairs"], cleaned_entities, output)
    return cleanup


def export_poly_back_dxf(analysis: dict, output: Path) -> int:
    drill_circles: list[dict] = []
    exterior_connectors: list[dict] = []
    exterior_roots: set[int] = set()
    id_marking_roots: set[int] = set()
    feature_count = 0

    for component in analysis["components"]:
        layer = component.get("layer", classify_component(component))
        if layer == LAYER_ID_MARKING:
            id_marking_roots.update(component["entity_indexes"])
            feature_count += 1
        elif is_poly_back_drill(component, analysis["segments"]):
            drill_circles.append(component_drill_circle(component, analysis["segments"], LAYER_DRILL))
            feature_count += 1
        elif is_poly_back_exterior(component):
            exterior_roots.update(component["entity_indexes"])
            if not component.get("closed"):
                open_points = component_open_points(component, analysis["segments"])
                if len(open_points) == 2 and point_distance(open_points[0], open_points[1]) <= 0.05:
                    exterior_connectors.append(connector_line(open_points[0], open_points[1], LAYER_POLY_BACK))
            feature_count += 1

    poly_back_entities = []
    poly_back_entities.extend(drill_circles)
    poly_back_entities.extend(copy_entities_to_layer(analysis["entities"], exterior_roots, LAYER_POLY_BACK))
    poly_back_entities.extend(exterior_connectors)
    poly_back_entities.extend(copy_entities_to_layer(analysis["entities"], id_marking_roots, LAYER_ID_MARKING))
    poly_back_entities = collapse_short_seam_lines(poly_back_entities)
    poly_back_entities = append_small_gap_connectors(poly_back_entities, [LAYER_POLY_BACK])
    write_dxf(analysis["pairs"], poly_back_entities, output, POLY_BACK_LAYER_COLORS)
    return feature_count


def export_acrylic_cutout_dxf(analysis: dict, output: Path) -> int:
    drill_roots: set[int] = set()
    inside_roots: set[int] = set()
    outside_roots: set[int] = set()
    cleanout_outline_roots: set[int] = set()
    id_marking_roots: set[int] = set()
    for component in analysis["components"]:
        layer = component.get("layer", classify_component(component))
        if layer == LAYER_ID_MARKING:
            id_marking_roots.update(component["entity_indexes"])
        elif layer == LAYER_DRILL:
            drill_roots.update(component["entity_indexes"])
        elif layer == LAYER_INNER:
            inside_roots.update(component["entity_indexes"])
        elif layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
            outside_roots.update(component["entity_indexes"])
        if component.get("paired_with") is not None and layer in {LAYER_OUTER, LAYER_PROFILE_HOLES}:
            cleanout_outline_roots.update(component["entity_indexes"])

    acrylic_entities = []
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], drill_roots, LAYER_DRILL))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], inside_roots | cleanout_outline_roots, LAYER_CLEANOUT))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], outside_roots, LAYER_ACRYLIC))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], id_marking_roots, LAYER_ID_MARKING))
    acrylic_entities = collapse_short_seam_lines(acrylic_entities)
    acrylic_entities = append_small_gap_connectors(acrylic_entities, [LAYER_CLEANOUT, LAYER_ACRYLIC])
    write_dxf(analysis["pairs"], acrylic_entities, output, ACRYLIC_LAYER_COLORS)
    return len(drill_roots | inside_roots | outside_roots | id_marking_roots)


def process_dxf(input_path: Path, output: Path | None = None) -> tuple[Path, Counter]:
    output_path = output or input_path.with_name(input_path.stem + "_classified.dxf")
    analysis = analyze_dxf(input_path)
    export_classified_dxf(analysis, output_path)
    return output_path, Counter(component["layer"] for component in analysis["components"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Split source DXF geometry into CAM-ready layers.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    output, summary = process_dxf(args.input, args.output)

    print(f"Wrote: {output}")
    for layer in [LAYER_DRILL, LAYER_PROFILE_HOLES, LAYER_INNER, LAYER_OUTER, LAYER_ID_MARKING, LAYER_REVIEW]:
        print(f"{layer}: {summary[layer]} components")


if __name__ == "__main__":
    main()
