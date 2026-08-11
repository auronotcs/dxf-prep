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

LAYER_COLORS = {
    LAYER_INNER: "3",
    LAYER_OUTER: "7",
    LAYER_DRILL: "1",
    LAYER_PROFILE_HOLES: "5",
    LAYER_ID_MARKING: "8",
    LAYER_REVIEW: "2",
    LAYER_CLEANOUT: "7",
    LAYER_ACRYLIC: "11",
}

ACRYLIC_LAYER_COLORS = {
    LAYER_DRILL: "1",
    LAYER_CLEANOUT: "7",
    LAYER_ACRYLIC: "11",
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


def point_key(point: tuple[float, float]) -> tuple[int, int]:
    return (math.floor(point[0] / TOLERANCE + 0.5), math.floor(point[1] / TOLERANCE + 0.5))


def arc_endpoint(entity: dict, angle_code: int) -> tuple[float, float]:
    cx = group_float(entity, 10)
    cy = group_float(entity, 20)
    radius = group_float(entity, 40)
    angle = math.radians(group_float(entity, angle_code))
    return (cx + radius * math.cos(angle), cy + radius * math.sin(angle))


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
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        degrees = [len(adjacency[node]) for node in nodes]
        colors = Counter(segments[segment_index]["color"] for segment_index in segment_indexes)
        bad_degrees = [degree for degree in degrees if degree != 2]
        components.append(
            {
                "segment_indexes": segment_indexes,
                "entity_indexes": {segments[i]["entity"] for i in segment_indexes},
                "closed": all(degree == 2 for degree in degrees),
                "open_nodes": len(bad_degrees),
                "branch_nodes": sum(1 for degree in bad_degrees if degree > 2),
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
                "color": colors.most_common(1)[0][0],
            }
        )
    return components


def classify_component(component: dict) -> str:
    min_x, min_y, max_x, max_y = component["bbox"]
    width = max_x - min_x
    height = max_y - min_y
    diameter = (width + height) / 2

    if not component["closed"]:
        if component["color"] == "0" and max(width, height) <= 1.0:
            return LAYER_ID_MARKING
        small_branched_contour = component.get("branch_nodes", 0) > 0 and max(width, height) < 8.0
        if component["open_nodes"] <= 2 and not small_branched_contour and max(width, height) > 1.0:
            if component["color"] == "3":
                return LAYER_INNER
            if component["color"] == "7":
                return LAYER_OUTER
        return LAYER_REVIEW
    if abs(width - height) <= DIAMETER_TOLERANCE and diameter < 0.5:
        if abs(diameter - DRILL_DIAMETER) <= DIAMETER_TOLERANCE:
            return LAYER_DRILL
        return LAYER_PROFILE_HOLES
    if component["color"] == "7":
        return LAYER_OUTER
    if component["color"] == "3":
        return LAYER_INNER
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
    layer_colors = layer_colors or LAYER_COLORS
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
    for component in components:
        component["layer"] = classify_component(component)
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


def acrylic_feature_role_with_context(
    component: dict,
    outside_bounds: list[tuple[float, float, float, float]],
) -> str | None:
    layer = component.get("layer", classify_component(component))
    min_x, min_y, max_x, max_y = component["bbox"]
    size = max(max_x - min_x, max_y - min_y)
    if layer == LAYER_DRILL:
        return "drill"
    if component["color"] == "1" and size > 1.0:
        return "outside"
    if component.get("closed") and has_consistent_inset_container(component, outside_bounds):
        return "inside"
    if component["color"] == "5" and component.get("closed") and any(
        bbox_contains(outside, component["bbox"]) for outside in outside_bounds
    ):
        return "inside"
    if layer == LAYER_OUTER:
        return "outside"
    if layer == LAYER_PROFILE_HOLES:
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


def export_acrylic_cutout_dxf(analysis: dict, output: Path) -> int:
    drill_roots: set[int] = set()
    inside_roots: set[int] = set()
    outside_roots: set[int] = set()
    cleanout_outline_roots: set[int] = set()
    id_marking_roots: set[int] = set()
    outside_bounds = [
        component["bbox"]
        for component in analysis["components"]
        if component.get("color") == "1"
        and max(component["bbox"][2] - component["bbox"][0], component["bbox"][3] - component["bbox"][1]) > 1.0
    ]
    roles_by_component = []
    for component in analysis["components"]:
        layer = component.get("layer", classify_component(component))
        if layer == LAYER_ID_MARKING:
            id_marking_roots.update(component["entity_indexes"])
            continue
        role = acrylic_feature_role_with_context(component, outside_bounds)
        roles_by_component.append((component, role))
        if not role:
            continue
        if role == "drill":
            drill_roots.update(component["entity_indexes"])
        elif role == "inside":
            inside_roots.update(component["entity_indexes"])
        elif role == "outside":
            outside_roots.update(component["entity_indexes"])

    inside_bounds = [component["bbox"] for component, role in roles_by_component if role == "inside"]
    for component, role in roles_by_component:
        if role != "outside":
            continue
        has_cleanout_inside = any(bbox_contains(component["bbox"], inside) for inside in inside_bounds)
        if component.get("color") == "1" or has_cleanout_inside:
            cleanout_outline_roots.update(component["entity_indexes"])

    acrylic_entities = []
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], drill_roots, LAYER_DRILL))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], inside_roots | cleanout_outline_roots, LAYER_CLEANOUT))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], outside_roots, LAYER_ACRYLIC))
    acrylic_entities.extend(copy_entities_to_layer(analysis["entities"], id_marking_roots, LAYER_ID_MARKING))
    acrylic_entities = collapse_short_seam_lines(acrylic_entities)
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
