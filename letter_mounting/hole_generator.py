from __future__ import annotations

import argparse
import itertools
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
import numpy as np
import shapely
from ezdxf.path import make_path
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import distance_transform_edt, maximum_filter
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union


@dataclass
class Letter:
    index: int
    label: str
    polygon: Polygon
    holes: list[tuple[float, float]]


@dataclass
class AlignmentGroup:
    line: int
    scale: int
    indices: list[int]
    rows: tuple[float, float, float] | None


def entity_line(entity, tolerance: float = 0.002) -> LineString | None:
    points = [(point.x, point.y) for point in make_path(entity).flattening(distance=tolerance)]
    return LineString(points) if len(points) >= 2 else None


def extract_letters(modelspace, precision: float = 0.001) -> list[Polygon]:
    lines = [line for entity in modelspace if (line := entity_line(entity)) is not None]
    noded = unary_union([shapely.set_precision(line, precision) for line in lines])
    faces = list(polygonize(noded))

    # Polygonize returns counter faces as well as the actual letter material.  A
    # face nested one level inside another shell is a counter and is discarded.
    shells = [Polygon(face.exterior) for face in faces]
    material = []
    for index, face in enumerate(faces):
        probe = face.representative_point()
        depth = sum(
            1
            for other_index, shell in enumerate(shells)
            if other_index != index and shell.area > shells[index].area and shell.contains(probe)
        )
        if depth % 2 == 0:
            material.append(face)
    return sorted(material, key=lambda polygon: polygon.bounds[0])


def recover_material_polygons(modelspace) -> list[Polygon]:
    """Recover closed artwork despite Flexi's occasional sub-thousandth endpoint drift."""
    # Most exports polygonize cleanly on the normal 0.001-inch grid.  Some
    # outlines only close on Flexi's original finer grid, though, so retain any
    # additional non-overlapping material recovered there.  This avoids losing
    # a whole letter merely because one pair of endpoints rounded apart.
    polygons = list(extract_letters(modelspace, precision=0.001))
    for candidate in extract_letters(modelspace, precision=0.0001):
        probe = candidate.representative_point()
        nested_in_known_shell = any(
            Polygon(polygon.exterior).buffer(0.01).contains(probe) for polygon in polygons
        )
        overlaps_known_material = any(
            candidate.intersection(polygon).area / max(candidate.area, 0.001) > 0.25
            for polygon in polygons
        )
        if not nested_in_known_shell and not overlaps_known_material:
            polygons.append(candidate)
    return sorted(polygons, key=lambda polygon: polygon.bounds[0])


def layout_lines(polygons: list[Polygon]) -> list[list[int]]:
    """Cluster shapes into visual text lines; small punctuation follows its nearby line."""
    if not polygons:
        return []

    def metrics(index):
        min_x, min_y, max_x, max_y = polygons[index].bounds
        return {
            "index": index,
            "center_y": (min_y + max_y) / 2,
            "height": max_y - min_y,
            "top": max_y,
        }

    rows: list[list[int]] = []
    for item in sorted((metrics(index) for index in range(len(polygons))), key=lambda value: -value["center_y"]):
        matches = []
        for row_index, row in enumerate(rows):
            anchor = max((metrics(index) for index in row), key=lambda value: value["height"])
            difference = abs(item["center_y"] - anchor["center_y"])
            allowance = max(item["height"], anchor["height"]) * 0.62
            if difference <= allowance:
                matches.append((difference / max(allowance, 0.001), row_index))
        if matches:
            rows[min(matches)[1]].append(item["index"])
        else:
            rows.append([item["index"]])

    rows.sort(
        key=lambda row: -max(
            polygons[index].bounds[3]
            for index in row
        )
    )
    for row in rows:
        row.sort(key=lambda index: polygons[index].bounds[0])
    return rows


def order_polygons_for_text(polygons: list[Polygon]) -> tuple[list[Polygon], list[list[int]]]:
    """Return top-to-bottom, left-to-right polygon order and remapped line indices."""
    source_lines = layout_lines(polygons)
    source_order = [index for row in source_lines for index in row]
    ordered = [polygons[index] for index in source_order]
    remapped_lines = []
    cursor = 0
    for row in source_lines:
        remapped_lines.append(list(range(cursor, cursor + len(row))))
        cursor += len(row)
    return ordered, remapped_lines


def labels_with_logos(text_value: str, polygon_count: int, logo_indices=None) -> list[str]:
    """Interleave selected logo labels with the visible text characters."""
    characters = [character for character in text_value if not character.isspace()]
    logos = set(logo_indices or ())
    invalid = sorted(index for index in logos if index < 0 or index >= polygon_count)
    if invalid:
        raise ValueError(f"Logo selection contains invalid shape index(es): {invalid}")
    expected_characters = polygon_count - len(logos)
    if len(characters) != expected_characters:
        raise ValueError(
            f"Text has {len(characters)} non-space characters, but the DXF contains "
            f"{polygon_count} separated shapes ({len(logos)} selected as logo)."
        )
    labels = []
    character_iterator = iter(characters)
    logo_number = 0
    for index in range(polygon_count):
        if index in logos:
            logo_number += 1
            labels.append(f"SHAPE_{logo_number}")
        else:
            labels.append(next(character_iterator))
    return labels


def _detached_dot_pair(first: Polygon, second: Polygon) -> bool:
    """Return True when two adjacent pieces look like a lowercase i/j stem and dot."""
    main, dot = sorted((first, second), key=lambda polygon: polygon.area, reverse=True)
    main_min_x, _main_min_y, main_max_x, main_max_y = main.bounds
    dot_min_x, dot_min_y, dot_max_x, dot_max_y = dot.bounds
    main_width = max(main_max_x - main_min_x, 0.001)
    main_height = max(main_max_y - main.bounds[1], 0.001)
    dot_width = dot_max_x - dot_min_x
    dot_height = dot_max_y - dot_min_y
    dot_center_x = (dot_min_x + dot_max_x) / 2
    horizontal_allowance = max(main_width * 0.55, dot_width * 0.75)
    horizontally_related = (
        main_min_x - horizontal_allowance
        <= dot_center_x
        <= main_max_x + horizontal_allowance
    )
    sits_above = dot_min_y >= main_max_y - main_height * 0.04
    gap = max(0.0, dot_min_y - main_max_y)
    return (
        dot.area <= main.area * 0.35
        and dot_width <= main_width * 0.85
        and dot_height <= main_height * 0.35
        and horizontally_related
        and sits_above
        and gap <= main_height * 0.65
    )


def labels_for_text_components(text_value: str, polygons: list[Polygon]) -> list[str]:
    """Map visible text to closed pieces, grouping detached lowercase i/j dots."""
    characters = [character for character in text_value if not character.isspace()]
    pieces = list(polygons)

    memo: dict[tuple[int, int], list[str] | None] = {}

    def solve(character_index: int, piece_index: int) -> list[str] | None:
        key = (character_index, piece_index)
        if key in memo:
            return memo[key]
        if character_index == len(characters):
            result = [] if piece_index == len(pieces) else None
            memo[key] = result
            return result
        if piece_index >= len(pieces):
            memo[key] = None
            return None

        character = characters[character_index]
        remaining_characters = len(characters) - character_index
        remaining_pieces = len(pieces) - piece_index
        if remaining_pieces < remaining_characters:
            memo[key] = None
            return None

        # Prefer the composite interpretation when the supplied character can
        # legitimately own a detached dot.  Backtracking still allows a normal
        # one-piece i/j in fonts whose dot is joined to the stem.
        if (
            character in {"i", "j"}
            and piece_index + 1 < len(pieces)
            and _detached_dot_pair(pieces[piece_index], pieces[piece_index + 1])
        ):
            tail = solve(character_index + 1, piece_index + 2)
            if tail is not None:
                result = [character, character] + tail
                memo[key] = result
                return result

        tail = solve(character_index + 1, piece_index + 1)
        result = [character] + tail if tail is not None else None
        memo[key] = result
        return result

    labels = solve(0, 0)
    if labels is None:
        raise ValueError(
            f"Text has {len(characters)} non-space characters, but the DXF contains "
            f"{len(pieces)} closed text piece(s). Detached lowercase i/j dots are grouped automatically."
        )
    return labels


def notify_progress(callback: Callable[[dict], None] | None, **payload) -> None:
    if callback is not None:
        callback(payload)


def medial_candidates(
    polygon: Polygon,
    step: float,
    minimum_center_to_edge: float,
) -> tuple[np.ndarray, np.ndarray]:
    min_x, min_y, max_x, max_y = polygon.bounds
    pad = step * 3
    xs = np.arange(min_x - pad, max_x + pad + step, step)
    ys = np.arange(min_y - pad, max_y + pad + step, step)
    xx, yy = np.meshgrid(xs, ys)
    mask = shapely.contains_xy(polygon, xx, yy)
    distance = distance_transform_edt(mask, sampling=(step, step))
    ridge = mask & (distance >= maximum_filter(distance, size=5) - step * 0.15)
    ridge &= distance >= minimum_center_to_edge

    rows, columns = np.nonzero(ridge)
    if len(rows) == 0:
        rows, columns = np.nonzero(mask & (distance >= minimum_center_to_edge))
    points = np.column_stack((xs[columns], ys[rows]))
    # EDT measures to the nearest rejected grid cell, so it can overstate true
    # vector clearance by roughly half a sample. Verify candidates against the
    # actual DXF boundary before any placement strategy sees them.
    clearances = np.asarray(shapely.distance(polygon.boundary, shapely.points(points)))
    retained = clearances + 1e-9 >= minimum_center_to_edge
    points = points[retained]
    clearances = clearances[retained]
    return points, clearances


def nearest_candidate(
    points: np.ndarray,
    clearances: np.ndarray,
    target: tuple[float, float],
    used: list[np.ndarray] | None = None,
    minimum_spacing: float = 0.0,
) -> np.ndarray:
    used = used or []
    allowed = np.ones(len(points), dtype=bool)
    for selected in used:
        allowed &= np.linalg.norm(points - selected, axis=1) >= minimum_spacing
    if not np.any(allowed):
        allowed[:] = True
    candidate_points = points[allowed]
    candidate_clearances = clearances[allowed]
    target_array = np.asarray(target)
    distances = np.linalg.norm(candidate_points - target_array, axis=1)
    # Prefer the desired location, with a modest bias toward the deepest part
    # of the stroke when several centerline locations are nearly equivalent.
    score = distances - np.minimum(candidate_clearances, 1.0) * 0.20
    return candidate_points[int(np.argmin(score))]


def farthest_layout(
    polygon: Polygon,
    points: np.ndarray,
    clearances: np.ndarray,
    count: int,
) -> list[np.ndarray]:
    center = np.asarray((polygon.centroid.x, polygon.centroid.y))
    first_scores = np.linalg.norm(points - center, axis=1) - np.minimum(clearances, 1.25) * 0.35
    selected = [points[int(np.argmin(first_scores))]]
    diagonal = math.hypot(polygon.bounds[2] - polygon.bounds[0], polygon.bounds[3] - polygon.bounds[1])
    while len(selected) < count:
        separations = np.min(
            np.column_stack([np.linalg.norm(points - chosen, axis=1) for chosen in selected]),
            axis=1,
        )
        # Keep points on the medial ridge, then spread them across the entire
        # letter so the mounting pattern resists rotation.
        scores = separations + np.minimum(clearances, 1.25) * diagonal * 0.025
        for chosen in selected:
            scores[np.linalg.norm(points - chosen, axis=1) < 1.25] = -np.inf
        selected.append(points[int(np.argmax(scores))])
    return selected


def place_holes(
    polygon: Polygon,
    label: str,
    diameter: float,
    minimum_center_to_edge: float,
    step: float = 0.04,
    alignment_rows: tuple[float, float, float] | None = None,
    minimum_hole_spacing: float = 1.0,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = polygon.bounds
    width, height = max_x - min_x, max_y - min_y
    # The production templates below describe upright block capitals. Lowercase
    # and script outlines use the geometry-driven medial-axis fallback instead.
    template_label = label if label.isupper() else ""
    single_hole_punctuation = label in {"."}
    two_hole_punctuation = label in {",", "'", "`", ";", ":"}
    count = 1 if single_hole_punctuation or max(width, height) < 6.0 else 3
    if two_hole_punctuation:
        count = 2
    points, clearances = medial_candidates(polygon, step, max(minimum_center_to_edge, diameter / 2))
    if len(points) == 0:
        raise ValueError(f"No safe hole location in shape {label!r}")

    def snap(target, used=None):
        return nearest_candidate(points, clearances, target, used or [], 1.25)

    def target(normalized_x, normalized_y):
        return (min_x + width * normalized_x, min_y + height * normalized_y)

    def guide_y(normalized_y):
        if alignment_rows is None:
            return min_y + height * normalized_y
        bottom_row, middle_row, top_row = alignment_rows
        if normalized_y <= 0.25:
            return bottom_row
        if normalized_y >= 0.75:
            return top_row
        return middle_row

    def line_parts(geometry):
        if geometry.is_empty:
            return []
        if geometry.geom_type == "LineString":
            return [geometry]
        if hasattr(geometry, "geoms"):
            result = []
            for child in geometry.geoms:
                result.extend(line_parts(child))
            return result
        return []

    def horizontal_spans(y):
        margin = max(width, height) + 1.0
        cut = LineString([(min_x - margin, y), (max_x + margin, y)])
        spans = []
        for part in line_parts(polygon.intersection(cut)):
            xs = [coordinate[0] for coordinate in part.coords]
            if not xs:
                continue
            left, right = min(xs), max(xs)
            if right - left < diameter:
                continue
            point = np.array(((left + right) / 2, y))
            spans.append((point, polygon.boundary.distance(Point(*point))))
        return sorted(spans, key=lambda item: item[0][0])

    def component_row(normalized_y, normalized_xs, absolute_y=None, search=0.07, shared=True):
        """Center one hole in each requested stroke crossing at one exact Y."""
        if absolute_y is not None:
            desired_y = absolute_y
        elif shared:
            desired_y = guide_y(normalized_y)
        else:
            desired_y = min_y + height * normalized_y
        desired_xs = [min_x + width * value for value in normalized_xs]

        def evaluate(y):
            spans = horizontal_spans(float(y))
            if len(spans) < len(desired_xs):
                return None
            best = None
            for indices in itertools.permutations(range(len(spans)), len(desired_xs)):
                selected = [spans[index] for index in indices]
                clearances_here = [item[1] for item in selected]
                if min(clearances_here) + 1e-6 < minimum_center_to_edge:
                    continue
                # Do not satisfy a requested outer leg with a safe but unrelated
                # inner stroke. If the desired row cannot reach that leg, the
                # row search below should move vertically until it can.
                if any(
                    abs(item[0][0] - wanted) > width * 0.30
                    for item, wanted in zip(selected, desired_xs)
                ):
                    continue
                x_error = sum(
                    abs(item[0][0] - wanted) / max(width, 0.01)
                    for item, wanted in zip(selected, desired_xs)
                )
                score = x_error - min(clearances_here) * 0.03
                if best is None or score < best[0]:
                    best = (score, [item[0] for item in selected])
            return best

        exact = evaluate(desired_y)
        if exact is not None:
            return exact[1]

        candidates = []
        radius = height * search
        for y in np.linspace(max(min_y, desired_y - radius), min(max_y, desired_y + radius), 161):
            evaluated = evaluate(y)
            if evaluated is not None:
                score, selected = evaluated
                score += abs(y - desired_y) / max(height, 0.01) * 6.0
                candidates.append((score, selected))
        if not candidates:
            raise ValueError(f"No centered horizontal mounting row available in {label!r}")
        return min(candidates, key=lambda item: item[0])[1]

    def horizontal_bar_row(normalized_y, normalized_xs, search=0.10):
        """Place several holes along one bar while centering their shared Y."""
        desired_y = guide_y(normalized_y)
        xs = [min_x + width * value for value in normalized_xs]
        exact_locations = [np.array((x, float(desired_y))) for x in xs]
        exact_distances = [
            polygon.boundary.distance(Point(*point)) if polygon.contains(Point(*point)) else -1.0
            for point in exact_locations
        ]
        if min(exact_distances) + 1e-6 >= minimum_center_to_edge:
            return exact_locations
        candidates = []
        for y in np.linspace(
            max(min_y, desired_y - height * search),
            min(max_y, desired_y + height * search),
            241,
        ):
            locations = [np.array((x, float(y))) for x in xs]
            distances = [
                polygon.boundary.distance(Point(*point)) if polygon.contains(Point(*point)) else -1.0
                for point in locations
            ]
            if min(distances) + 1e-6 < minimum_center_to_edge:
                continue
            score = -min(distances) + abs(y - desired_y) / max(height, 0.01) * 0.08
            candidates.append((score, locations))
        if not candidates:
            raise ValueError(f"No centered horizontal bar row available in {label!r}")
        return min(candidates, key=lambda item: item[0])[1]

    def stem_columns(normalized_xs, probe_y=0.36):
        """Measure isolated vertical stems and return their true center X values."""
        probe = min_y + height * probe_y
        points_on_stems = component_row(
            0.50,
            normalized_xs,
            absolute_y=probe,
            search=0.12,
            shared=False,
        )
        return [float(point[0]) for point in points_on_stems]

    def fixed_shared_row(normalized_y, xs):
        """Lock known stem columns to one of the E-defined horizontal datums."""
        y = guide_y(normalized_y)
        locations = [np.array((float(x), float(y))) for x in xs]
        if any(
            not polygon.contains(Point(*point))
            or polygon.boundary.distance(Point(*point)) + 1e-6 < minimum_center_to_edge
            for point in locations
        ):
            raise ValueError(f"Shared row does not safely cross the required stem in {label!r}")
        return locations

    def safe_vertical_axis(normalized_y):
        center_x = (min_x + max_x) / 2
        desired_y = min_y + height * normalized_y
        samples = np.linspace(min_y, max_y, max(200, int(height / step) + 1))
        valid = []
        for y in samples:
            point = Point(center_x, float(y))
            clearance = polygon.boundary.distance(point)
            if polygon.contains(point) and clearance >= minimum_center_to_edge:
                valid.append((abs(y - desired_y) - min(clearance, 1.0) * 0.08, y))
        if not valid:
            raise ValueError(f"No safe location on the vertical symmetry axis of {label!r}")
        return np.array((center_x, min(valid)[1]))

    def safe_horizontal_axis(normalized_x):
        center_y = (min_y + max_y) / 2
        desired_x = min_x + width * normalized_x
        samples = np.linspace(min_x, max_x, max(200, int(width / step) + 1))
        valid = []
        for x in samples:
            point = Point(float(x), center_y)
            clearance = polygon.boundary.distance(point)
            if polygon.contains(point) and clearance >= minimum_center_to_edge:
                valid.append((abs(x - desired_x) - min(clearance, 1.0) * 0.08, x))
        if not valid:
            raise ValueError(f"No safe location on the horizontal symmetry axis of {label!r}")
        return np.array((min(valid)[1], center_y))

    def vertical_pattern(single_target, pair_target):
        center_x = (min_x + max_x) / 2
        single = safe_vertical_axis(single_target[1])
        left = snap(target(*pair_target), [single])
        if left[0] > center_x:
            left = snap(target(1.0 - pair_target[0], pair_target[1]), [single])
        right = np.array((2 * center_x - left[0], left[1]))
        return [single, left, right]

    def horizontal_pattern(single_target, pair_target):
        center_y = (min_y + max_y) / 2
        single = safe_horizontal_axis(single_target[0])
        upper = snap(target(*pair_target), [single])
        if upper[1] < center_y:
            upper = snap(target(pair_target[0], 1.0 - pair_target[1]), [single])
        lower = np.array((upper[0], 2 * center_y - upper[1]))
        return [single, upper, lower]

    def rotational_pattern(center_target, pair_target):
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        single = snap(target(*center_target))
        single = np.array((center_x, center_y))
        first = snap(target(*pair_target), [single])
        opposite = np.array((2 * center_x - first[0], 2 * center_y - first[1]))
        return [single, first, opposite]

    def vertical_pair(normalized_x, normalized_y, used=None):
        center_x = (min_x + max_x) / 2
        first = snap(target(normalized_x, normalized_y), used or [])
        if first[0] > center_x:
            first = snap(target(1.0 - normalized_x, normalized_y), used or [])
        return [first, np.array((2 * center_x - first[0], first[1]))]

    def horizontal_pair(normalized_x, normalized_y, used=None):
        center_y = (min_y + max_y) / 2
        first = snap(target(normalized_x, normalized_y), used or [])
        if first[1] < center_y:
            first = snap(target(normalized_x, 1.0 - normalized_y), used or [])
        return [first, np.array((first[0], 2 * center_y - first[1]))]

    def rotational_pair(normalized_x, normalized_y, used=None):
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        first = snap(target(normalized_x, normalized_y), used or [])
        return [first, np.array((2 * center_x - first[0], 2 * center_y - first[1]))]

    if count == 1:
        chosen = [nearest_candidate(points, clearances, (polygon.centroid.x, polygon.centroid.y))]
    elif two_hole_punctuation:
        upper = snap(target(0.50, 0.78))
        lower = snap(target(0.50, 0.22), [upper])
        chosen = [upper, lower]
    elif template_label == "B":
        stem_x = stem_columns((0.10,), probe_y=0.72)[0]
        counters = sorted((Polygon(ring).bounds for ring in polygon.interiors), key=lambda bounds: bounds[1])
        bowl_points = []
        for bounds in reversed(counters):
            bowl_y = (bounds[1] + bounds[3]) / 2
            bowl_points += component_row(0.50, (0.88,), absolute_y=bowl_y)
        chosen = fixed_shared_row(0.94, (stem_x,)) + bowl_points + fixed_shared_row(0.06, (stem_x,))
    elif template_label == "D":
        stem_x = stem_columns((0.10,), probe_y=0.50)[0]
        curved_top = component_row(0.84, (0.88,), shared=False)
        curved_bottom = component_row(0.16, (0.88,), shared=False)
        chosen = fixed_shared_row(0.94, (stem_x,)) + curved_top + fixed_shared_row(
            0.06, (stem_x,)
        ) + curved_bottom
    elif template_label == "O":
        if height >= 15.0:
            chosen = component_row(0.84, (0.25, 0.75), shared=False) + component_row(
                0.16, (0.25, 0.75), shared=False
            )
        else:
            # Smaller O: two across the top and one centered at the bottom.
            chosen = vertical_pattern((0.50, 0.20), (0.28, 0.78))
    elif template_label == "I" and width < height * 0.35:
        chosen = component_row(0.80, (0.50,)) + component_row(0.20, (0.50,))
    elif template_label == "A":
        if height < 16.0:
            # Medium channel-letter A: one centered near the apex and one in
            # each lower leg. Four points crowd the narrower upper strokes.
            chosen = [safe_vertical_axis(0.84)] + component_row(0.16, (0.18, 0.82))
        else:
            # Larger A: four centered points distribute the load across both legs.
            chosen = component_row(0.84, (0.28, 0.72)) + component_row(0.16, (0.18, 0.82))
    elif template_label == "M":
        columns = stem_columns((0.10, 0.90), probe_y=0.50)
        chosen = fixed_shared_row(0.94, columns) + fixed_shared_row(0.06, columns)
    elif template_label == "T":
        chosen = horizontal_bar_row(0.94, (0.20, 0.80)) + component_row(0.16, (0.50,))
    elif template_label == "C":
        if height < 16.0:
            chosen = farthest_layout(polygon, points, clearances, 3)
        else:
            chosen = component_row(0.84, (0.25, 0.75), shared=False) + component_row(
                0.16, (0.25, 0.75), shared=False
            )
    elif template_label == "E":
        stem_x = stem_columns((0.10,), probe_y=0.72)[0]
        stem_fraction = (stem_x - min_x) / width
        chosen = (
            horizontal_bar_row(0.94, (stem_fraction, 0.80))
            + horizontal_bar_row(0.50, (0.76,))
            + horizontal_bar_row(0.06, (stem_fraction, 0.80))
        )
    elif template_label == "K":
        chosen = component_row(0.84, (0.10, 0.82)) + component_row(0.16, (0.10, 0.82))
    elif template_label == "L":
        chosen = (
            component_row(0.94, (0.10,))
            + component_row(0.50, (0.10,))
            + horizontal_bar_row(0.06, (0.82,))
        )
    elif template_label == "S":
        if height < 16.0:
            chosen = farthest_layout(polygon, points, clearances, 3)
        else:
            chosen = (
                component_row(0.84, (0.25, 0.75), shared=False)
                + component_row(0.50, (0.50,), shared=False)
                + component_row(0.16, (0.25, 0.75), shared=False)
            )
    elif template_label == "N":
        columns = stem_columns((0.10, 0.90), probe_y=0.50)
        chosen = fixed_shared_row(0.94, columns) + fixed_shared_row(0.06, columns)
    elif template_label == "R":
        counter_center_y = min_y + height * 0.68
        if polygon.interiors:
            counter_bounds = Polygon(polygon.interiors[0]).bounds
            counter_center_y = (counter_bounds[1] + counter_bounds[3]) / 2
        bottom = component_row(0.16, (0.10, 0.82))
        top_left = np.array((bottom[0][0], guide_y(0.94)))
        if (
            not polygon.contains(Point(*top_left))
            or polygon.boundary.distance(Point(*top_left)) + 1e-6 < minimum_center_to_edge
        ):
            top_left = component_row(0.84, (0.10,))[0]
        chosen = (
            [top_left]
            + component_row(0.68, (0.88,), absolute_y=counter_center_y)
            + bottom
        )
    elif template_label == "F":
        stem_x = stem_columns((0.10,), probe_y=0.72)[0]
        stem_fraction = (stem_x - min_x) / width
        chosen = (
            horizontal_bar_row(0.94, (stem_fraction, 0.80))
            + horizontal_bar_row(0.50, (0.76,))
            + fixed_shared_row(0.06, (stem_x,))
        )
    elif template_label == "G":
        if height < 16.0:
            chosen = farthest_layout(polygon, points, clearances, 3)
        else:
            chosen = component_row(0.84, (0.25, 0.75), shared=False) + component_row(
                0.16, (0.25, 0.75), shared=False
            )
    elif template_label == "H":
        columns = stem_columns((0.10, 0.90), probe_y=0.25)
        chosen = fixed_shared_row(0.94, columns) + fixed_shared_row(0.06, columns)
    elif template_label == "J":
        stem_x = stem_columns((0.88,), probe_y=0.72)[0]
        chosen = (
            fixed_shared_row(0.94, (stem_x,))
            + fixed_shared_row(0.50, (stem_x,))
            + component_row(0.16, (0.25,), shared=False)
        )
    elif template_label == "P":
        stem_x = stem_columns((0.10,), probe_y=0.25)[0]
        counter_bounds = Polygon(polygon.interiors[0]).bounds if polygon.interiors else polygon.bounds
        bowl_y = (counter_bounds[1] + counter_bounds[3]) / 2
        chosen = (
            fixed_shared_row(0.94, (stem_x,))
            + component_row(0.65, (0.88,), absolute_y=bowl_y)
            + fixed_shared_row(0.06, (stem_x,))
        )
    elif template_label == "Q":
        if height < 16.0:
            chosen = farthest_layout(polygon, points, clearances, 3)
        else:
            chosen = component_row(0.84, (0.25, 0.75), shared=False) + component_row(
                0.16, (0.25, 0.75), shared=False
            )
    elif template_label == "U":
        columns = stem_columns((0.10, 0.90), probe_y=0.70)
        chosen = fixed_shared_row(0.94, columns) + component_row(
            0.18, (0.25, 0.75), shared=False
        )
    elif template_label == "V":
        chosen = component_row(0.94, (0.15, 0.85)) + component_row(0.06, (0.50,))
    elif template_label == "W":
        chosen = component_row(0.94, (0.12, 0.88)) + component_row(0.06, (0.28, 0.72))
    elif template_label == "X":
        chosen = component_row(0.94, (0.15, 0.85)) + component_row(0.06, (0.15, 0.85))
    elif template_label == "Y":
        chosen = component_row(0.94, (0.15, 0.85)) + component_row(0.06, (0.50,))
    elif template_label == "Z":
        chosen = horizontal_bar_row(0.94, (0.20, 0.80)) + horizontal_bar_row(0.06, (0.20, 0.80))
    else:
        chosen = farthest_layout(polygon, points, clearances, count)

    result = [(round(float(point[0]), 4), round(float(point[1]), 4)) for point in chosen]
    for x, y in result:
        center = Point(x, y)
        if not polygon.contains(center) or polygon.boundary.distance(center) + 1e-6 < minimum_center_to_edge:
            raise ValueError(f"Unsafe calculated hole at ({x}, {y}) in shape {label!r}")
    for index, first in enumerate(result):
        for second in result[index + 1 :]:
            spacing = math.hypot(first[0] - second[0], first[1] - second[1])
            if spacing + 1e-6 < minimum_hole_spacing:
                raise ValueError(
                    f"Stud spacing {spacing:.3f} is below the {minimum_hole_spacing:.3f} minimum in {label!r}"
                )
    return sorted(result, key=lambda point: (-point[1], point[0]))


def assign_entities(modelspace, polygons: list[Polygon]) -> list[list]:
    assigned = [[] for _ in polygons]
    boundaries = [polygon.boundary for polygon in polygons]
    for entity in list(modelspace):
        line = entity_line(entity)
        if line is None:
            continue
        probe = line.interpolate(0.5, normalized=True)
        closest = min(range(len(boundaries)), key=lambda index: boundaries[index].distance(probe))
        assigned[closest].append(entity)
    return assigned


def add_holes_and_groups(doc, letters: list[Letter], diameter: float) -> None:
    modelspace = doc.modelspace()
    if "MOUNTING_HOLES" not in doc.layers:
        doc.layers.add("MOUNTING_HOLES", color=1)
    source_entities = assign_entities(modelspace, [letter.polygon for letter in letters])
    radius = diameter / 2
    for letter, outlines in zip(letters, source_entities):
        circles = [
            modelspace.add_circle(center, radius, dxfattribs={"layer": "MOUNTING_HOLES", "color": 1})
            for center in letter.holes
        ]
        safe_label = re.sub(r"[^A-Za-z0-9]+", "_", letter.label).strip("_") or "PUNCT"
        group = doc.groups.new(name=f"LETTER_{letter.index:02d}_{safe_label}")
        group.extend(outlines + circles)


def canonical_geometry_key(label: str, polygon: Polygon) -> tuple[str, bytes]:
    """Identify equal letter outlines independently of their page position."""
    min_x, min_y, _, _ = polygon.bounds
    local = translate(polygon, xoff=-min_x, yoff=-min_y)
    local = shapely.normalize(shapely.set_precision(local, 0.001))
    return label.upper(), local.wkb


def derive_alignment_rows(labels: list[str], polygons: list[Polygon]) -> tuple[float, float, float] | None:
    """Use an E's horizontal stroke centers as the job-wide straight-letter datums."""
    for label, polygon in zip(labels, polygons):
        if label != "E":
            continue
        min_x, min_y, max_x, max_y = polygon.bounds
        probe_x = min_x + (max_x - min_x) * 0.80
        cut = LineString([(probe_x, min_y - 1.0), (probe_x, max_y + 1.0)])
        intersection = polygon.intersection(cut)
        parts = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
        centers = []
        for part in parts:
            if part.geom_type != "LineString":
                continue
            ys = [coordinate[1] for coordinate in part.coords]
            if ys:
                centers.append((min(ys) + max(ys)) / 2)
        centers.sort()
        if len(centers) >= 3:
            center_y = (min_y + max_y) / 2
            middle = min(centers[1:-1], key=lambda value: abs(value - center_y))
            return centers[0], middle, centers[-1]

    large_polygons = [
        polygon
        for polygon in polygons
        if max(polygon.bounds[2] - polygon.bounds[0], polygon.bounds[3] - polygon.bounds[1]) >= 6.0
    ]
    if not large_polygons:
        return None
    common_min = float(np.median([polygon.bounds[1] for polygon in large_polygons]))
    common_max = float(np.median([polygon.bounds[3] for polygon in large_polygons]))
    height = common_max - common_min
    return (
        common_min + height * 0.059,
        common_min + height * 0.517,
        common_min + height * 0.941,
    )


def build_alignment_groups(
    labels: list[str],
    polygons: list[Polygon],
    lines: list[list[int]],
) -> tuple[list[AlignmentGroup], dict[int, tuple[float, float, float] | None]]:
    """Separate each text line into independent baseline/cap-height/scale groups."""
    punctuation = {index for index, label in enumerate(labels) if not label.isalnum()}
    groups: list[AlignmentGroup] = []

    for line_number, line_indices in enumerate(lines, 1):
        letter_indices = [index for index in line_indices if index not in punctuation]
        scale_clusters: list[list[int]] = []

        for index in sorted(
            letter_indices,
            key=lambda value: polygons[value].bounds[3] - polygons[value].bounds[1],
            reverse=True,
        ):
            min_x, min_y, max_x, max_y = polygons[index].bounds
            height = max_y - min_y
            candidates = []
            for cluster_index, cluster in enumerate(scale_clusters):
                bottoms = [polygons[item].bounds[1] for item in cluster]
                tops = [polygons[item].bounds[3] for item in cluster]
                heights = [top - bottom for bottom, top in zip(bottoms, tops)]
                cluster_bottom = float(np.median(bottoms))
                cluster_top = float(np.median(tops))
                cluster_height = float(np.median(heights))
                reference = max(min(height, cluster_height), 0.001)
                size_ratio = max(height, cluster_height) / reference
                bottom_error = abs(min_y - cluster_bottom) / reference
                top_error = abs(max_y - cluster_top) / reference
                if size_ratio <= 1.25 and bottom_error <= 0.20 and top_error <= 0.20:
                    candidates.append((bottom_error + top_error + abs(math.log(size_ratio)), cluster_index))
            if candidates:
                scale_clusters[min(candidates)[1]].append(index)
            else:
                scale_clusters.append([index])

        # A punctuation-only row still needs a local scale context.
        if not scale_clusters:
            scale_clusters = [list(line_indices)]

        line_groups = []
        for scale_number, cluster in enumerate(scale_clusters, 1):
            cluster.sort(key=lambda index: polygons[index].bounds[0])
            rows = derive_alignment_rows([labels[index] for index in cluster], [polygons[index] for index in cluster])
            line_groups.append(AlignmentGroup(line_number, scale_number, cluster, rows))

        # Attach punctuation to the nearest size group, using actual geometry
        # distance so apostrophes and commas follow the surrounding copy.
        for index in [item for item in line_indices if item in punctuation]:
            center = polygons[index].centroid
            selected = min(
                line_groups,
                key=lambda group: min(polygons[item].distance(center) for item in group.indices),
            )
            selected.indices.append(index)

        groups.extend(line_groups)

    row_by_index: dict[int, tuple[float, float, float] | None] = {}
    for group in groups:
        for index in group.indices:
            row_by_index[index] = group.rows
    return groups, row_by_index


def draw_polygon(draw: ImageDraw.ImageDraw, polygon: Polygon, transform, fill, outline=None, width=1) -> None:
    exterior = [transform(x, y) for x, y in polygon.exterior.coords]
    draw.polygon(exterior, fill=fill)
    if outline:
        draw.line(exterior, fill=outline, width=width, joint="curve")
    for interior in polygon.interiors:
        ring = [transform(x, y) for x, y in interior.coords]
        draw.polygon(ring, fill="white")
        if outline:
            draw.line(ring, fill=outline, width=width, joint="curve")


def render_preview(letters: list[Letter], diameter: float, output: Path) -> None:
    columns = 4
    rows = math.ceil(len(letters) / columns)
    cell_width, cell_height = 480, 390
    margin, caption = 36, 42
    image = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)

    for position, letter in enumerate(letters):
        column, row = position % columns, position // columns
        left, top = column * cell_width, row * cell_height
        draw.rectangle((left, top, left + cell_width - 1, top + cell_height - 1), outline="#d8dde3", width=2)
        min_x, min_y, max_x, max_y = letter.polygon.bounds
        scale = min(
            (cell_width - margin * 2) / max(max_x - min_x, 0.01),
            (cell_height - caption - margin * 2) / max(max_y - min_y, 0.01),
        )

        def transform(x, y):
            px = left + (cell_width - (max_x - min_x) * scale) / 2 + (x - min_x) * scale
            py = top + caption + margin + (max_y - y) * scale
            return (round(px), round(py))

        draw.text((left + 14, top + 10), f"{letter.index:02d}  {letter.label}   {len(letter.holes)} hole(s)", fill="#20252b", font=font)
        draw_polygon(draw, letter.polygon, transform, fill="#cbd2d9", outline="#26313b", width=2)
        radius = max(4, diameter / 2 * scale)
        for x, y in letter.holes:
            px, py = transform(x, y)
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill="#e53935", outline="#8b0000", width=2)
            draw.line((px - radius * 1.5, py, px + radius * 1.5, py), fill="#8b0000", width=1)
            draw.line((px, py - radius * 1.5, px, py + radius * 1.5), fill="#8b0000", width=1)

    image.save(output)


def generate_stud_holes(
    source: Path,
    text_value: str,
    output: Path | None = None,
    preview: Path | None = None,
    diameter: float = 0.230,
    minimum_center_to_edge: float = 0.60,
    minimum_hole_spacing: float = 1.00,
    logo_indices=None,
    logo_edge_clearance: float | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    source = Path(source)
    output = Path(output) if output else source.with_name(f"{source.stem} - holes.dxf")
    preview = Path(preview) if preview else output.with_suffix(".png")
    if diameter <= 0 or minimum_center_to_edge <= 0 or minimum_hole_spacing <= 0:
        raise ValueError("Hole diameter, edge clearance, and hole spacing must all be positive.")
    if logo_edge_clearance is None:
        logo_edge_clearance = minimum_center_to_edge
    if logo_edge_clearance <= 0:
        raise ValueError("Logo edge clearance must be positive.")

    notify_progress(progress_callback, stage="preparing", message="Recovering separated shapes...")
    doc = ezdxf.readfile(source)
    # Flexi commonly writes R12 DXF, but R12 has no OBJECTS section and cannot
    # persist DXF groups.  R2000 keeps the original geometry while allowing each
    # letter and its mounting holes to travel as one named group.
    if doc.dxfversion < "AC1015":
        doc.dxfversion = "AC1015"
    recovered = recover_material_polygons(doc.modelspace())
    preview_order, _ = order_polygons_for_text(recovered)
    selected_preview_indices = set(logo_indices or ())
    invalid = sorted(
        index for index in selected_preview_indices if index < 0 or index >= len(preview_order)
    )
    if invalid:
        raise ValueError(f"Logo selection contains invalid shape index(es): {invalid}")
    selected_object_ids = {id(preview_order[index]) for index in selected_preview_indices}
    logo_polygons = sorted(
        (polygon for polygon in recovered if id(polygon) in selected_object_ids),
        key=lambda polygon: polygon.bounds[0],
    )
    text_polygons, text_lines = order_polygons_for_text(
        [polygon for polygon in recovered if id(polygon) not in selected_object_ids]
    )
    component_labels = labels_for_text_components(text_value, text_polygons)
    polygons = logo_polygons + text_polygons
    labels = [f"SHAPE_{index}" for index in range(1, len(logo_polygons) + 1)] + component_labels
    logo_output_indices = set(range(len(logo_polygons)))
    layout_line_indices = [[index] for index in range(len(logo_polygons))] + [
        [index + len(logo_polygons) for index in line] for line in text_lines
    ]

    alignment_groups, alignment_rows_by_index = build_alignment_groups(
        labels,
        polygons,
        layout_line_indices,
    )

    letters = []
    canonical_holes: dict[tuple[str, bytes], tuple[tuple[float, float], ...]] = {}
    for index, (label, polygon) in enumerate(zip(labels, polygons), 1):
        notify_progress(
            progress_callback,
            stage="piece_started",
            index=index,
            total=len(polygons),
            label=label,
            bounds=polygon.bounds,
        )
        key = canonical_geometry_key(label, polygon)
        min_x, min_y, _, _ = polygon.bounds
        if key in canonical_holes:
            holes = [
                (round(min_x + offset_x, 4), round(min_y + offset_y, 4))
                for offset_x, offset_y in canonical_holes[key]
            ]
        else:
            selected_clearance = (
                logo_edge_clearance if index - 1 in logo_output_indices else minimum_center_to_edge
            )
            try:
                holes = place_holes(
                    polygon,
                    label,
                    diameter,
                    selected_clearance,
                    alignment_rows=alignment_rows_by_index[index - 1],
                    minimum_hole_spacing=minimum_hole_spacing,
                )
            except ValueError:
                if not (len(label) == 1 and label.isalpha() and label.isupper()):
                    raise
                # Decorative/script capitals do not necessarily have the stem
                # layout assumed by the block-font templates. Fall back to the
                # geometry-driven medial layout instead of rejecting the job.
                holes = place_holes(
                    polygon,
                    label.lower(),
                    diameter,
                    selected_clearance,
                    alignment_rows=alignment_rows_by_index[index - 1],
                    minimum_hole_spacing=minimum_hole_spacing,
                )
            canonical_holes[key] = tuple((x - min_x, y - min_y) for x, y in holes)
        letters.append(Letter(index, label, polygon, holes))
        notify_progress(
            progress_callback,
            stage="piece_complete",
            index=index,
            total=len(polygons),
            label=label,
            bounds=polygon.bounds,
            feature_count=len(holes),
        )

    notify_progress(progress_callback, stage="saving", message="Grouping holes and saving DXF...")
    add_holes_and_groups(doc, letters, diameter)
    doc.saveas(output)
    notify_progress(progress_callback, stage="preview", message="Rendering output preview...")
    render_preview(letters, diameter, preview)
    notify_progress(progress_callback, stage="finished", message="Stud-hole file finished.")

    print(f"Wrote: {output}")
    print(f"Preview: {preview}")
    for group in alignment_groups:
        characters = "".join(labels[index] for index in sorted(group.indices))
        rows = "none" if group.rows is None else ", ".join(f"{value:.3f}" for value in group.rows)
        print(f"Alignment line {group.line}, scale {group.scale}: {characters!r}; rows {rows}")
    for letter in letters:
        coordinates = "; ".join(f"({x:.3f}, {y:.3f})" for x, y in letter.holes)
        print(f"{letter.index:02d} {letter.label}: {coordinates}")
    return {
        "output": output,
        "preview": preview,
        "letters": letters,
        "alignment_groups": alignment_groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Place mounting holes on outlined DXF letters.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--text", required=True, help="The lettering, including punctuation; spaces are ignored.")
    parser.add_argument("--diameter", type=float, default=0.230)
    parser.add_argument("--min-center-edge", type=float, default=0.60)
    parser.add_argument("--min-hole-spacing", type=float, default=1.00)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    generate_stud_holes(
        args.source,
        args.text,
        args.output,
        args.preview,
        args.diameter,
        args.min_center_edge,
        args.min_hole_spacing,
    )


if __name__ == "__main__":
    main()
