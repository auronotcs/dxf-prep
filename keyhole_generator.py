from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
import numpy as np
import shapely
from ezdxf import bbox
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from hole_generator import (
    assign_entities,
    canonical_geometry_key,
    entity_line,
    extract_letters,
    medial_candidates,
    notify_progress,
    order_polygons_for_text,
    labels_for_text_components,
    recover_material_polygons,
)


@dataclass(frozen=True)
class MountFeature:
    kind: str
    center: tuple[float, float]


@dataclass(frozen=True)
class ChannelLetterMass:
    face_area_sq_in: float
    perimeter_in: float
    polycarbonate_lb: float
    aluminum_face_lb: float
    coil_lb: float
    led_length_ft: float
    led_lb: float
    total_lb: float


POLYCARBONATE_DENSITY_LB_IN3 = 0.04335
ALUMINUM_DENSITY_LB_IN3 = 0.0975
POLYCARBONATE_FACE_THICKNESS_IN = 4.5 / 25.4
ALUMINUM_FACE_THICKNESS_IN = 0.125
COIL_THICKNESS_IN = 0.063
COIL_WIDTH_IN = 2.0
COIL_PERIMETER_MULTIPLIER = 2.0
LED_CENTERLINE_PERIMETER_FRACTION = 0.5
DEFAULT_LED_PATH_CLEARANCE = 0.750
TINY_BACK_MINIMUM_SPACING = 1.000


def estimate_channel_letter_mass(
    polygon: Polygon,
    led_weight_per_foot: float = 0.030,
) -> ChannelLetterMass:
    """Estimate the finished mass from the production construction recipe."""
    if led_weight_per_foot < 0:
        raise ValueError("LED weight per foot cannot be negative.")
    face_area = float(polygon.area)
    perimeter = float(polygon.length)
    polycarbonate = (
        face_area * POLYCARBONATE_FACE_THICKNESS_IN * POLYCARBONATE_DENSITY_LB_IN3
    )
    aluminum_face = face_area * ALUMINUM_FACE_THICKNESS_IN * ALUMINUM_DENSITY_LB_IN3
    coil = (
        perimeter
        * COIL_PERIMETER_MULTIPLIER
        * COIL_THICKNESS_IN
        * COIL_WIDTH_IN
        * ALUMINUM_DENSITY_LB_IN3
    )
    led_length_ft = perimeter * LED_CENTERLINE_PERIMETER_FRACTION / 12.0
    led = led_length_ft * led_weight_per_foot
    total = polycarbonate + aluminum_face + coil + led
    return ChannelLetterMass(
        face_area_sq_in=face_area,
        perimeter_in=perimeter,
        polycarbonate_lb=polycarbonate,
        aluminum_face_lb=aluminum_face,
        coil_lb=coil,
        led_length_ft=led_length_ft,
        led_lb=led,
        total_lb=total,
    )


def approximate_led_centerline_points(
    polygon: Polygon,
    step: float = 0.20,
) -> list[tuple[float, float]]:
    """Return a review/avoidance guide along the likely LED module path.

    Channel-letter modules normally follow the medial ridge of each stroke.
    This is intentionally an approximate guide rather than a populated LED
    drawing: it is used to discourage a mount from landing directly under a
    module while still allowing narrow strokes to use their only safe area.
    """
    points, _clearances = medial_candidates(polygon, step, minimum_center_to_edge=0.18)
    return [
        (round(float(point[0]), 4), round(float(point[1]), 4))
        for point in points
    ]


def polygonized_faces(modelspace, precision: float = 0.001) -> list[Polygon]:
    lines = [entity_line(entity) for entity in modelspace]
    lines = [line for line in lines if line is not None]
    noded = unary_union([shapely.set_precision(line, precision) for line in lines])
    return list(polygonize(noded))


def robust_back_polygons(modelspace) -> list[Polygon]:
    """Recover back outlines across the endpoint precisions emitted by Flexi."""
    return recover_material_polygons(modelspace)


def keyhole_template_geometry(template_doc):
    faces = polygonized_faces(template_doc.modelspace(), precision=0.0001)
    if not faces:
        raise ValueError("The keyhole template does not contain a closed footprint.")
    footprint = max((Polygon(face.exterior) for face in faces), key=lambda polygon: polygon.area)
    return footprint, footprint.centroid


def calibrated_features(reference_doc, template_footprint: Polygon):
    template_width = template_footprint.bounds[2] - template_footprint.bounds[0]
    template_height = template_footprint.bounds[3] - template_footprint.bounds[1]
    keyholes = []
    electrical = []
    for face in polygonized_faces(reference_doc.modelspace()):
        min_x, min_y, max_x, max_y = face.bounds
        width, height = max_x - min_x, max_y - min_y
        if abs(width - template_width) <= 0.012 and abs(height - template_height) <= 0.012:
            keyholes.append((face.centroid.x, face.centroid.y))
            continue
        circularity = 4 * math.pi * face.area / max(face.length * face.length, 1e-9)
        if 0.30 <= width <= 0.75 and abs(width - height) <= 0.012 and circularity >= 0.98:
            electrical.append((face.centroid.x, face.centroid.y, width))
    return keyholes, electrical


def choose_electrical_diameter(
    reference_electrical,
    explicit_diameter: float | None,
    default_diameter: float = 0.375,
    consistency_tolerance: float = 0.012,
) -> tuple[float, str]:
    """Use an explicit override, otherwise preserve a consistent reference size."""
    if explicit_diameter is not None:
        if explicit_diameter <= 0:
            raise ValueError("Electrical diameter must be greater than zero.")
        return explicit_diameter, "explicit override"

    diameters = [diameter for _, _, diameter in reference_electrical]
    if diameters and max(diameters) - min(diameters) <= consistency_tolerance:
        return statistics.median(diameters), "reference artwork"
    return default_diameter, "production default"


def layout_translation(source_doc, reference_doc) -> tuple[float, float]:
    source_box = bbox.extents(source_doc.modelspace())
    reference_box = bbox.extents(reference_doc.modelspace())
    source_size = source_box.size
    reference_size = reference_box.size
    if abs(source_size.x - reference_size.x) > 0.05 or abs(source_size.y - reference_size.y) > 0.05:
        raise ValueError("Reference and source layout dimensions do not match closely enough.")
    return (
        source_box.extmin.x - reference_box.extmin.x,
        source_box.extmin.y - reference_box.extmin.y,
    )


def copy_keyhole(template_doc, target_modelspace, target, template_center, layer: str):
    dx = target[0] - template_center.x
    dy = target[1] - template_center.y
    copied = []
    for entity in template_doc.modelspace():
        clone = entity.copy()
        clone.dxf.layer = layer
        clone.translate(dx, dy, 0)
        target_modelspace.add_entity(clone)
        copied.append(clone)
    return copied


def logo_indices_from_bounds(backs: list[Polygon], logo_bounds=None) -> set[int]:
    """Match preview selections back to recovered outlines without relying on sort order."""
    targets = list(logo_bounds or ())
    selected: set[int] = set()
    for target in targets:
        if len(target) != 4:
            raise ValueError("A selected logo has invalid geometry bounds.")
        candidates = []
        for index, back in enumerate(backs):
            if index in selected:
                continue
            differences = [abs(float(left) - float(right)) for left, right in zip(back.bounds, target)]
            candidates.append((max(differences), sum(differences), index))
        if not candidates or min(candidates)[0] > 0.05:
            raise ValueError("A selected logo could not be matched to a recovered DXF shape.")
        selected.add(min(candidates)[2])
    return selected


def procedural_back_labels(
    backs: list[Polygon],
    text_value: str,
    logo_bounds=None,
) -> tuple[list[Polygon], list[str]]:
    """Pair ordered text outlines with labels and retain obvious extra logo shapes."""
    characters = [character for character in text_value if not character.isspace()]
    explicit_shapes = logo_indices_from_bounds(backs, logo_bounds)
    shape_indices: set[int] = set(explicit_shapes)
    if explicit_shapes:
        text_backs = [back for index, back in enumerate(backs) if index not in shape_indices]
        ordered_text_backs, _ = order_polygons_for_text(text_backs)
        component_labels = labels_for_text_components(text_value, ordered_text_backs)
    else:
        ordered_text_backs, _ = order_polygons_for_text(backs)
        try:
            component_labels = labels_for_text_components(text_value, ordered_text_backs)
        except ValueError:
            # Preserve the command-line convenience for a single obvious logo
            # even when it was not selected in the UI.
            extra_count = len(backs) - len(characters)
            if extra_count <= 0:
                raise
            ranked = sorted(range(len(backs)), key=lambda index: backs[index].area, reverse=True)
            shape_indices = set(ranked[:extra_count])
            remaining_areas = [backs[index].area for index in ranked[extra_count:]]
            if remaining_areas and min(backs[index].area for index in shape_indices) < max(remaining_areas) * 1.8:
                raise ValueError(
                    "The extra non-text backs are not clear size outliers; select the logo in the preview."
                )
            text_backs = [back for index, back in enumerate(backs) if index not in shape_indices]
            ordered_text_backs, _ = order_polygons_for_text(text_backs)
            component_labels = labels_for_text_components(text_value, ordered_text_backs)

    shapes = [backs[index] for index in sorted(shape_indices, key=lambda item: backs[item].bounds[0])]
    shape_labels = [f"SHAPE_{index}" for index in range(1, len(shapes) + 1)]
    return shapes + ordered_text_backs, shape_labels + component_labels


def desired_keyhole_count(
    label: str,
    polygon: Polygon,
    estimated_mass_lb: float = 0.0,
    pounds_per_mount: float = 5.0,
) -> int:
    min_x, min_y, max_x, max_y = polygon.bounds
    width, height = max_x - min_x, max_y - min_y
    largest = max(width, height)
    if pounds_per_mount <= 0:
        raise ValueError("Pounds per mount must be greater than zero.")
    upper = label.upper()
    if upper.startswith("SHAPE_"):
        geometry_count = max(4, min(8, round(largest / 10)))
        return max(geometry_count, math.ceil(estimated_mass_lb / pounds_per_mount))
    if label in {",", "'", "`", ";", ":"}:
        return 2
    if label == "g":
        # A script g needs both sides of its bowl supported plus two points
        # along the descending leg/tail.
        return max(4, math.ceil(estimated_mass_lb / pounds_per_mount))
    if largest < 4.0:
        # Even a detached dot or small circular back needs two mounting points
        # so it cannot rotate on the wall. Round fallbacks are allowed when a
        # full keyhole footprint will not fit twice.
        return 2
    if width < height * 0.35 or upper in {"I", "!"}:
        geometry_count = 2
    elif largest >= 60.0:
        geometry_count = 5
    elif largest >= 36.0:
        geometry_count = 4
    else:
        geometry_count = 3
    return max(geometry_count, math.ceil(estimated_mass_lb / pounds_per_mount))


def effective_mount_spacing(
    polygon: Polygon,
    requested_spacing: float,
    label: str | None = None,
) -> float:
    """Permit the one-inch anti-rotation spacing on dots and punctuation."""
    width = polygon.bounds[2] - polygon.bounds[0]
    height = polygon.bounds[3] - polygon.bounds[1]
    if max(width, height) < 4.0 or label in {",", "'", "`", ";", ":"}:
        return min(requested_spacing, TINY_BACK_MINIMUM_SPACING)
    return requested_spacing


def normalized_keyhole_targets(label: str, count: int) -> list[tuple[float, float]]:
    # These templates describe upright block capitals. Script/lowercase pieces
    # are distributed from their actual safe medial candidates instead.
    upper = label if label.isupper() else ""
    if upper.startswith("SHAPE_") and count == 6:
        return [(0.04, 0.95), (0.50, 0.95), (0.94, 0.90), (0.04, 0.05), (0.50, 0.05), (0.90, 0.08)]
    if count == 1:
        return [(0.50, 0.50)]
    if count == 2:
        if upper in {"I", "!"}:
            return [(0.50, 0.78), (0.50, 0.20)]
        return [(0.35, 0.78), (0.65, 0.22)]
    if count == 3:
        if upper == "A":
            return [(0.50, 0.76), (0.18, 0.20), (0.82, 0.20)]
        if upper == "L":
            # Follow the center of a slanted serif stem instead of forcing both
            # stem mounts onto the same global X coordinate.  Keep them near
            # the ends of the stem for better anti-rocking leverage.
            return [(0.52, 0.84), (0.28, 0.22), (0.80, 0.14)]
        if upper == "T":
            return [(0.20, 0.78), (0.80, 0.78), (0.50, 0.20)]
        if upper == "F":
            return [(0.15, 0.78), (0.80, 0.78), (0.15, 0.20)]
        if upper in {"N", "S", "V", "W", "X", "Y"}:
            return [(0.25, 0.76), (0.78, 0.76), (0.78, 0.20)]
        if upper == "C":
            return [(0.72, 0.84), (0.16, 0.50), (0.70, 0.16)]
        if upper in {"D", "E", "G", "K", "O", "R", "U", "Z"}:
            return [(0.25, 0.76), (0.78, 0.76), (0.20, 0.20)]
        return [(0.25, 0.76), (0.78, 0.76), (0.58, 0.20)]
    if count == 4:
        if upper == "F":
            return [(0.15, 0.78), (0.80, 0.78), (0.76, 0.50), (0.15, 0.22)]
        if upper == "E":
            return [(0.15, 0.78), (0.80, 0.78), (0.76, 0.50), (0.15, 0.22)]
        return [(0.22, 0.78), (0.78, 0.78), (0.20, 0.22), (0.80, 0.22)]

    # General logo fallback: alternate around upper and lower rows.
    columns = math.ceil(count / 2)
    xs = [0.18 + 0.64 * index / max(columns - 1, 1) for index in range(columns)]
    targets = [(x, 0.78) for x in xs]
    targets.extend((x, 0.22) for x in reversed(xs))
    return targets[:count]


def anatomical_keyhole_targets(label: str, count: int) -> list[tuple[float, float]] | None:
    """Targets for common script/lowercase anatomy in channel-letter backs."""
    if label == "i" and count == 2:
        # The body of a lowercase i is a stem.  Its detached dot uses the same
        # pair of targets, but hard clearance filtering keeps both mounts in
        # the dot's usable center instead of scattering them along the edge.
        return [(0.50, 0.76), (0.50, 0.24)]
    if label == "g" and count == 4:
        # Left/right sides of the bowl, the middle of the descending leg, and
        # the leg terminal.
        return [(0.25, 0.72), (0.72, 0.72), (0.30, 0.40), (0.72, 0.12)]
    if count != 3:
        return None
    return {
        # Upper-left branch, upper-right branch, descending tail.
        "y": [(0.20, 0.80), (0.82, 0.80), (0.15, 0.12)],
        # Two points in the main stem, one in the bowl.
        "b": [(0.25, 0.78), (0.22, 0.22), (0.76, 0.48)],
        # Substantial upper and lower terminals plus the central spine.
        "s": [(0.78, 0.78), (0.50, 0.50), (0.20, 0.22)],
        # Two points in the main stem, then one in the opposite shoulder/stem.
        "n": [(0.27, 0.78), (0.27, 0.22), (0.73, 0.78)],
        # Two across the upper bowl; the thicker lower-right side gets the third.
        "o": [(0.26, 0.72), (0.72, 0.76), (0.70, 0.28)],
        # Upper/lower stem plus the center of the bowl (counter surround).
        "a": [(0.72, 0.78), (0.64, 0.22), (0.25, 0.50)],
    }.get(label)


def safe_keyhole_candidates(
    polygon: Polygon,
    footprint: Polygon,
    template_center,
    step: float = 0.05,
    full_interior: bool = False,
) -> tuple[list[tuple[float, float]], list[float]]:
    template_width = footprint.bounds[2] - footprint.bounds[0]
    minimum_clearance = template_width / 2 + 0.02
    if full_interior:
        min_x, min_y, max_x, max_y = polygon.bounds
        scan_step = max(step, 0.20)
        xs = np.arange(min_x, max_x + scan_step, scan_step)
        ys = np.arange(min_y, max_y + scan_step, scan_step)
        xx, yy = np.meshgrid(xs, ys)
        mask = shapely.contains_xy(polygon, xx, yy)
        points = np.column_stack((xx[mask], yy[mask]))
        point_geometries = shapely.points(points[:, 0], points[:, 1])
        clearances = np.asarray(shapely.distance(polygon.boundary, point_geometries))
        retained = clearances >= minimum_clearance
        points = points[retained]
        clearances = clearances[retained]
    else:
        points, clearances = medial_candidates(polygon, step, minimum_clearance)
    safe_points = []
    safe_clearances = []
    host = polygon.buffer(0.005)
    for point, clearance in zip(points, clearances):
        translated = translate(
            footprint,
            xoff=float(point[0]) - template_center.x,
            yoff=float(point[1]) - template_center.y,
        )
        if host.covers(translated):
            safe_points.append((float(point[0]), float(point[1])))
            safe_clearances.append(float(polygon.boundary.distance(translated)))
    if not safe_points:
        raise ValueError("No location can contain the supplied keyhole footprint inside this back.")
    return safe_points, safe_clearances


def can_fit_spaced_mounts(
    points: list[tuple[float, float]],
    count: int,
    minimum_spacing: float,
) -> bool:
    """Quickly determine whether a dense candidate cloud can support a layout."""
    if count <= 0:
        return True
    if len(points) < count:
        return False
    if count == 1:
        return True

    point_array = np.asarray(points, dtype=float)
    centroid = np.mean(point_array, axis=0)
    seed_indices = {
        int(np.argmin(point_array[:, 0])),
        int(np.argmax(point_array[:, 0])),
        int(np.argmin(point_array[:, 1])),
        int(np.argmax(point_array[:, 1])),
        int(np.argmax(np.linalg.norm(point_array - centroid, axis=1))),
    }
    # Candidate sets are sampled on a dense grid; a few evenly distributed
    # additional seeds prevent an unlucky concave extreme from hiding a valid
    # arrangement without turning this into an expensive combinatorial search.
    stride = max(1, len(points) // 24)
    seed_indices.update(range(0, len(points), stride))

    for seed_index in seed_indices:
        chosen = [point_array[seed_index]]
        while len(chosen) < count:
            selected_array = np.asarray(chosen)
            separations = np.min(
                np.linalg.norm(
                    point_array[:, None, :] - selected_array[None, :, :],
                    axis=2,
                ),
                axis=1,
            )
            next_index = int(np.argmax(separations))
            if separations[next_index] + 1e-6 < minimum_spacing:
                break
            chosen.append(point_array[next_index])
        if len(chosen) >= count:
            return True
    return False


def targeted_keyhole_layout(
    polygon: Polygon,
    label: str,
    footprint: Polygon,
    template_center,
    minimum_spacing: float = 1.25,
    preferred_edge_clearance: float = 0.625,
    estimated_mass_lb: float = 0.0,
    pounds_per_mount: float = 5.0,
    circular_mount_diameter: float = 0.250,
    led_guide_points: list[tuple[float, float]] | None = None,
    led_path_clearance: float = DEFAULT_LED_PATH_CLEARANCE,
) -> list[tuple[float, float]]:
    count = desired_keyhole_count(label, polygon, estimated_mass_lb, pounds_per_mount)
    anatomy_targets = anatomical_keyhole_targets(label, count)
    min_x, min_y, max_x, max_y = polygon.bounds
    width, height = max_x - min_x, max_y - min_y
    use_full_interior = (
        anatomy_targets is not None
        or label in {"C", "L"}
        or label in {",", "'", "`", ";", ":"}
        or max(width, height) < 4.0
    )
    try:
        points, keyhole_clearances = safe_keyhole_candidates(
            polygon,
            footprint,
            template_center,
            step=0.20 if use_full_interior else 0.05,
            full_interior=use_full_interior,
        )
    except ValueError:
        points, keyhole_clearances = [], []
    layout_spacing = effective_mount_spacing(polygon, minimum_spacing, label)
    if count >= 4 and points:
        candidate_width = max(point[0] for point in points) - min(point[0] for point in points)
        candidate_height = max(point[1] for point in points) - min(point[1] for point in points)
        if candidate_width < width * 0.45 or candidate_height < height * 0.45:
            points, keyhole_clearances = safe_keyhole_candidates(
                polygon,
                footprint,
                template_center,
                step=0.20,
                full_interior=True,
            )

    selected: list[tuple[float, float]] = []
    # Edge/coil clearance is a hard production constraint for every mount.
    # Anatomy and LED avoidance only rank candidates that already satisfy it.
    keyhole_gap_by_point = {
        (round(float(point[0]), 4), round(float(point[1]), 4)): float(clearance)
        for point, clearance in zip(points, keyhole_clearances)
        if float(clearance) + 1e-6 >= preferred_edge_clearance
    }
    round_radius = circular_mount_diameter / 2
    round_gap_by_point = {}
    for point in safe_round_candidates(
        polygon,
        circular_mount_diameter,
        step=0.20 if use_full_interior else 0.05,
        full_interior=use_full_interior,
    ):
        rounded = (round(float(point[0]), 4), round(float(point[1]), 4))
        gap = float(polygon.boundary.distance(Point(*rounded))) - round_radius
        if gap + 1e-6 >= preferred_edge_clearance:
            round_gap_by_point[rounded] = gap

    keyhole_layout_available = can_fit_spaced_mounts(
        list(keyhole_gap_by_point),
        count,
        layout_spacing,
    )
    if keyhole_layout_available:
        points = list(keyhole_gap_by_point)
    else:
        points = list(round_gap_by_point)
        if not can_fit_spaced_mounts(points, count, layout_spacing):
            # Medial candidates are ideal for normal strokes but can miss the
            # two separated lobes of compact punctuation.  Search its complete
            # usable interior before declaring a compliant fallback impossible.
            for point in safe_round_candidates(
                polygon,
                circular_mount_diameter,
                step=0.20,
                full_interior=True,
            ):
                rounded = (round(float(point[0]), 4), round(float(point[1]), 4))
                gap = float(polygon.boundary.distance(Point(*rounded))) - round_radius
                if gap + 1e-6 >= preferred_edge_clearance:
                    round_gap_by_point[rounded] = gap
            points = list(round_gap_by_point)
    if not can_fit_spaced_mounts(points, count, layout_spacing):
        raise ValueError(
            f"Cannot fit {count} mounting points in {label!r} at {layout_spacing:.3f} spacing "
            f"while preserving the {preferred_edge_clearance:.3f} edge clearance."
        )
    keyhole_gaps = [keyhole_gap_by_point.get(point, 0.0) for point in points]
    mount_edge_gaps = [
        keyhole_gap_by_point.get(point, round_gap_by_point.get(point, 0.0))
        for point in points
    ]
    center_clearances = [float(polygon.boundary.distance(Point(*point))) for point in points]
    led_array = np.asarray(led_guide_points or approximate_led_centerline_points(polygon))

    def rebuild_candidate_metrics(candidate_points: list[tuple[float, float]]) -> None:
        nonlocal points, keyhole_gaps, mount_edge_gaps, center_clearances
        points = sorted(
            {
                (round(float(point[0]), 4), round(float(point[1]), 4))
                for point in candidate_points
            }
        )
        keyhole_gaps = [keyhole_gap_by_point.get(point, 0.0) for point in points]
        mount_edge_gaps = [
            keyhole_gap_by_point.get(point, round_gap_by_point.get(point, 0.0))
            for point in points
        ]
        center_clearances = [float(polygon.boundary.distance(Point(*point))) for point in points]

    def available_indices():
        return [
            index
            for index, point in enumerate(points)
            if all(math.dist(point, existing) + 1e-6 >= layout_spacing for existing in selected)
        ]

    def target_distance(index, normalized_x, normalized_y):
        target_x = min_x + width * normalized_x
        target_y = min_y + height * normalized_y
        if len(led_array):
            led_distance = float(
                np.min(np.linalg.norm(led_array - np.asarray(points[index]), axis=1))
            )
            led_shortfall = max(0.0, led_path_clearance - led_distance)
        else:
            led_shortfall = 0.0
        return (
            math.hypot(points[index][0] - target_x, points[index][1] - target_y)
            + led_shortfall * 4.0
            - min(mount_edge_gaps[index], 1.50) * 0.85
        )

    def select_one(normalized_x, normalized_y):
        allowed = available_indices()
        if not allowed:
            raise ValueError(f"Cannot fit {count} keyholes at the required spacing in {label!r}.")
        keyhole_allowed = [
            index
            for index in allowed
            if keyhole_gaps[index] + 1e-6 >= preferred_edge_clearance
        ] if keyhole_layout_available else []
        # A compliant keyhole always wins over a circular fallback.  Target
        # anatomy and LED avoidance decide only among candidates of that tier.
        allowed = keyhole_allowed or allowed
        chosen = min(
            allowed,
            key=lambda index: (
                target_distance(index, normalized_x, normalized_y),
                points[index][0],
                points[index][1],
            ),
        )
        selected.append(points[chosen])
        return chosen

    if len(label) == 1 and label.isalpha() and label.islower():
        # Script and lowercase outlines often have no relationship to their
        # block-capital target map. Seed at a distant terminal, then repeatedly
        # fill the most isolated safe branch. For a script y this naturally
        # produces upper-left, upper-right, and lower-tail mounts.
        # Include circular-only locations so a narrow script terminal can still
        # receive the permitted round fallback even when the keyhole footprint
        # itself cannot fit there.
        rebuild_candidate_metrics(points)
        selected.clear()
        if anatomy_targets is not None:
            for normalized_target in anatomy_targets:
                select_one(*normalized_target)
            return [(round(x, 4), round(y, 4)) for x, y in selected]
        centroid = np.asarray((polygon.centroid.x, polygon.centroid.y))
        point_array = np.asarray(points)
        clearance_array = np.asarray(center_clearances)
        first_pool = [
            index
            for index in range(len(points))
            if keyhole_gaps[index] + 1e-6 >= preferred_edge_clearance
        ] if keyhole_layout_available else []
        first_pool = first_pool or list(range(len(points)))
        first_scores = np.linalg.norm(point_array[first_pool] - centroid, axis=1) + np.minimum(
            clearance_array[first_pool], preferred_edge_clearance
        ) * 0.10
        selected.append(points[first_pool[int(np.argmax(first_scores))]])
        while len(selected) < count:
            available = available_indices()
            if not available:
                raise ValueError(f"Cannot fit {count} keyholes at the required spacing in {label!r}.")
            available_keyholes = [
                index
                for index in available
                if keyhole_gaps[index] + 1e-6 >= preferred_edge_clearance
            ] if keyhole_layout_available else []
            available = available_keyholes or available
            selected_array = np.asarray(selected)
            candidate_array = point_array[available]
            separations = np.min(
                np.linalg.norm(candidate_array[:, None, :] - selected_array[None, :, :], axis=2),
                axis=1,
            )
            scores = separations + np.minimum(
                clearance_array[available], preferred_edge_clearance
            ) * 0.10
            selected.append(points[available[int(np.argmax(scores))]])
        return [(round(x, 4), round(y, 4)) for x, y in selected]

    targets = normalized_keyhole_targets(label, count)

    # Straight letters benefit from exact paired rows, matching the pattern and
    # making field adjustment around beams or grout lines practical. Curved
    # letters retain independent centering on their strokes.
    row_aligned_letters = {"A", "E", "F", "H", "K", "M", "N", "R", "T", "U", "V", "W", "X", "Y", "Z"}
    cursor = 0
    while cursor < len(targets):
        first = targets[cursor]
        can_pair = (
            label in row_aligned_letters
            and cursor + 1 < len(targets)
            and abs(first[1] - targets[cursor + 1][1]) < 1e-9
        )
        if not can_pair:
            select_one(*first)
            cursor += 1
            continue

        second = targets[cursor + 1]
        allowed = available_indices()
        first_pool = allowed
        second_pool = allowed
        pairs = []
        for first_index in first_pool:
            for second_index in second_pool:
                if first_index == second_index:
                    continue
                first_point, second_point = points[first_index], points[second_index]
                if abs(first_point[1] - second_point[1]) > 0.051:
                    continue
                if math.dist(first_point, second_point) + 1e-6 < layout_spacing:
                    continue
                score = target_distance(first_index, *first) + target_distance(second_index, *second)
                round_count = sum(
                    keyhole_gaps[index] + 1e-6 < preferred_edge_clearance
                    for index in (first_index, second_index)
                )
                pairs.append((round_count, score, first_index, second_index))
        if pairs:
            _, _, first_index, second_index = min(pairs)
            selected.extend((points[first_index], points[second_index]))
        else:
            select_one(*first)
            select_one(*second)
        cursor += 2
    return [(round(x, 4), round(y, 4)) for x, y in selected]


def choose_mount_features(
    polygon: Polygon,
    keyhole_centers: list[tuple[float, float]],
    footprint: Polygon,
    template_center,
    coil_clearance: float = 0.625,
    circular_diameter: float = 0.250,
) -> list[MountFeature]:
    """Substitute a round mount wherever the keyhole enters the coil zone."""
    radius = circular_diameter / 2
    host = polygon.buffer(0.005)
    features = []
    for center in keyhole_centers:
        placed_keyhole = translate(
            footprint,
            xoff=center[0] - template_center.x,
            yoff=center[1] - template_center.y,
        )
        keyhole_gap = float(polygon.boundary.distance(placed_keyhole))
        if host.covers(placed_keyhole) and keyhole_gap + 1e-6 >= coil_clearance:
            features.append(MountFeature("keyhole", center))
            continue
        circular_mount = Point(*center).buffer(radius, quad_segs=24)
        circular_gap = float(polygon.boundary.distance(circular_mount))
        if not host.covers(circular_mount) or circular_gap + 1e-6 < coil_clearance:
            raise ValueError(
                f"Neither a keyhole nor a {circular_diameter:.3f} circular mount at {center} "
                f"satisfies the {coil_clearance:.3f} edge clearance."
            )
        features.append(MountFeature("round", center))
    return features


def safe_round_candidates(
    polygon: Polygon,
    diameter: float,
    step: float = 0.05,
    full_interior: bool = False,
) -> list[tuple[float, float]]:
    minimum_clearance = diameter / 2 + 0.02
    if full_interior:
        min_x, min_y, max_x, max_y = polygon.bounds
        scan_step = max(step, 0.20)
        xs = np.arange(min_x, max_x + scan_step, scan_step)
        ys = np.arange(min_y, max_y + scan_step, scan_step)
        xx, yy = np.meshgrid(xs, ys)
        mask = shapely.contains_xy(polygon, xx, yy)
        points = np.column_stack((xx[mask], yy[mask]))
        clearances = np.asarray(
            shapely.distance(polygon.boundary, shapely.points(points[:, 0], points[:, 1]))
        )
        points = points[clearances + 1e-9 >= minimum_clearance]
    else:
        points, _ = medial_candidates(polygon, step, minimum_clearance)
    return [(float(point[0]), float(point[1])) for point in points]


def sampled_boundary_points(polygon: Polygon, spacing: float = 0.20) -> np.ndarray:
    samples = []
    rings = [LineString(polygon.exterior.coords)] + [LineString(ring.coords) for ring in polygon.interiors]
    for ring in rings:
        count = max(2, math.ceil(ring.length / spacing))
        for index in range(count):
            point = ring.interpolate(index / count, normalized=True)
            samples.append((point.x, point.y))
    return np.asarray(samples)


def maximum_boundary_reach(
    polygon: Polygon,
    mounts: list[MountFeature],
) -> tuple[float, tuple[float, float]]:
    boundary_points = sampled_boundary_points(polygon)
    centers = np.asarray([feature.center for feature in mounts])
    distances = np.linalg.norm(boundary_points[:, None, :] - centers[None, :, :], axis=2)
    nearest = np.min(distances, axis=1)
    index = int(np.argmax(nearest))
    point = boundary_points[index]
    return float(nearest[index]), (float(point[0]), float(point[1]))


def validate_mount_layout(
    polygon: Polygon,
    label: str,
    mounts: list[MountFeature],
    minimum_center_spacing: float,
    maximum_reach: float,
    enforce_reach: bool = True,
) -> tuple[float, float]:
    centers = [feature.center for feature in mounts]
    pair_spacings = [
        math.dist(first, second)
        for index, first in enumerate(centers)
        for second in centers[index + 1 :]
    ]
    closest_spacing = min(pair_spacings) if pair_spacings else math.inf
    if closest_spacing + 1e-6 < minimum_center_spacing:
        raise ValueError(
            f"Mount spacing {closest_spacing:.3f} in {label!r} is below "
            f"the {minimum_center_spacing:.3f} minimum."
        )
    reach, _ = maximum_boundary_reach(polygon, mounts)
    if enforce_reach and reach > maximum_reach + 1e-6:
        raise ValueError(
            f"Unsupported reach {reach:.3f} in {label!r} exceeds the {maximum_reach:.3f} maximum."
        )
    return closest_spacing, reach


def augment_mounts_for_coverage(
    polygon: Polygon,
    label: str,
    mounts: list[MountFeature],
    footprint: Polygon,
    template_center,
    coil_clearance: float,
    circular_diameter: float,
    minimum_center_spacing: float,
    maximum_reach: float,
) -> list[MountFeature]:
    """Add at most a small safeguard for genuinely long unsupported strokes."""
    result = list(mounts)
    largest = max(
        polygon.bounds[2] - polygon.bounds[0],
        polygon.bounds[3] - polygon.bounds[1],
    )
    is_logo = label.upper().startswith("SHAPE_")
    additional_budget = 2 if is_logo else (1 if largest > maximum_reach * 3.0 else 0)
    if additional_budget == 0:
        return result
    large_panel = is_logo or largest >= 24.0
    try:
        keyhole_points, keyhole_gaps = safe_keyhole_candidates(
            polygon,
            footprint,
            template_center,
            step=0.20 if large_panel else 0.05,
            full_interior=large_panel,
        )
        keyhole_points = [
            point
            for point, gap in zip(keyhole_points, keyhole_gaps)
            if gap + 1e-6 >= coil_clearance
        ]
    except ValueError:
        keyhole_points = []
    round_points = [] if large_panel else [
        point
        for point in safe_round_candidates(polygon, circular_diameter)
        if float(polygon.boundary.distance(Point(*point))) - circular_diameter / 2 + 1e-6
        >= coil_clearance
    ]
    candidate_points = np.asarray(keyhole_points + round_points)
    if len(candidate_points) == 0:
        raise ValueError(f"No mounting candidates are available to satisfy the reach limit in {label!r}.")

    for _ in range(additional_budget):
        reach, unsupported_point = maximum_boundary_reach(polygon, result)
        if reach <= maximum_reach + 1e-6:
            return result

        centers = np.asarray([feature.center for feature in result])
        separations = np.linalg.norm(candidate_points[:, None, :] - centers[None, :, :], axis=2)
        allowed = np.min(separations, axis=1) + 1e-6 >= minimum_center_spacing
        allowed_indices = np.flatnonzero(allowed)
        if len(allowed_indices) == 0:
            return result

        target = np.asarray(unsupported_point)
        ordered = allowed_indices[
            np.argsort(np.linalg.norm(candidate_points[allowed_indices] - target, axis=1))
        ]
        keyhole_options = []
        round_options = []
        for candidate_index in ordered[:750]:
            center = tuple(float(value) for value in candidate_points[candidate_index])
            feature = choose_mount_features(
                polygon,
                [center],
                footprint,
                template_center,
                coil_clearance,
                circular_diameter,
            )[0]
            distance = math.dist(center, unsupported_point)
            option = (distance, feature)
            if feature.kind == "keyhole":
                keyhole_options.append(option)
            else:
                round_options.append(option)
        options = keyhole_options or round_options
        if not options:
            return result
        result.append(min(options, key=lambda item: item[0])[1])

    return result


def normalized_electrical_target(label: str) -> tuple[float, float]:
    """Prefer a lower stroke terminal so LED wiring starts at the end of a run."""
    upper = label.upper()
    if upper.startswith("SHAPE_"):
        return 0.50, 0.03
    if upper in {"A", "F", "H", "M", "N", "S", "V", "W", "X", "Y"}:
        return 0.18, 0.08
    if upper in {"C", "E", "G", "K", "L", "R", "Z"}:
        return 0.84, 0.08
    if upper in {"D", "O", "Q", "U"}:
        return 0.68, 0.08
    return 0.50, 0.08


def electrical_center_for_back(
    polygon: Polygon,
    label: str,
    mount_features: list[MountFeature],
    footprint: Polygon,
    template_center,
    diameter: float,
    circular_mount_diameter: float,
    minimum_feature_gap: float = 0.20,
) -> tuple[float, float]:
    radius = diameter / 2
    min_x, min_y, max_x, max_y = polygon.bounds
    if label.upper().startswith("SHAPE_"):
        scan_step = 0.20
        xs = np.arange(min_x, max_x + scan_step, scan_step)
        ys = np.arange(min_y, max_y + scan_step, scan_step)
        xx, yy = np.meshgrid(xs, ys)
        mask = shapely.contains_xy(polygon, xx, yy)
        point_array = np.column_stack((xx[mask], yy[mask]))
        point_geometries = shapely.points(point_array[:, 0], point_array[:, 1])
        clearance_array = np.asarray(shapely.distance(polygon.boundary, point_geometries))
        retained = clearance_array >= radius + 0.001
        point_array = point_array[retained]
        clearance_array = clearance_array[retained]
    else:
        points, clearances = medial_candidates(polygon, 0.04, radius + 0.04)
        point_array = np.asarray(points)
        clearance_array = np.asarray(clearances)

    mounting_shapes = []
    for feature in mount_features:
        x, y = feature.center
        if feature.kind == "keyhole":
            shape = translate(footprint, xoff=x - template_center.x, yoff=y - template_center.y)
        else:
            shape = Point(x, y).buffer(circular_mount_diameter / 2, quad_segs=24)
        mounting_shapes.append(shape.buffer(minimum_feature_gap))
    if len(point_array) == 0:
        raise ValueError("No safe electrical-hole candidate exists in this back.")
    forbidden_centers = unary_union(mounting_shapes).buffer(radius)
    allowed = ~shapely.contains_xy(forbidden_centers, point_array[:, 0], point_array[:, 1])
    point_array = point_array[allowed]
    clearance_array = clearance_array[allowed]
    if len(point_array) == 0:
        # Small detached pieces (notably an i/j dot) may have only one medial
        # ridge, occupied by their mount, while still having safe room beside
        # it. Search the full interior before declaring the electrical opening
        # impossible.
        scan_step = 0.05
        xs = np.arange(min_x, max_x + scan_step, scan_step)
        ys = np.arange(min_y, max_y + scan_step, scan_step)
        xx, yy = np.meshgrid(xs, ys)
        interior = shapely.contains_xy(polygon, xx, yy)
        point_array = np.column_stack((xx[interior], yy[interior]))
        point_geometries = shapely.points(point_array)
        clearance_array = np.asarray(shapely.distance(polygon.boundary, point_geometries))
        retained = clearance_array >= radius + 0.001
        point_array = point_array[retained]
        clearance_array = clearance_array[retained]
        allowed = ~shapely.contains_xy(forbidden_centers, point_array[:, 0], point_array[:, 1])
        point_array = point_array[allowed]
        clearance_array = clearance_array[allowed]
        if len(point_array) == 0:
            raise ValueError("No safe electrical-hole location remains after keyhole placement.")

    normalized_x, normalized_y = normalized_electrical_target(label)
    target_x = min_x + (max_x - min_x) * normalized_x
    target_y = min_y + (max_y - min_y) * normalized_y
    edge_gaps = clearance_array - radius
    coil_shortfalls = np.maximum(0.0, 0.625 - edge_gaps)
    scores = (
        np.hypot(point_array[:, 0] - target_x, point_array[:, 1] - target_y)
        + coil_shortfalls * 8.0
        - np.minimum(edge_gaps, 0.625) * 0.08
    )
    chosen = int(np.argmin(scores))
    x, y = point_array[chosen]
    return round(float(x), 4), round(float(y), 4)


def generate_procedural(
    source: Path,
    template: Path,
    output: Path,
    text_value: str,
    electrical_diameter: float | None,
    minimum_keyhole_spacing: float,
    minimum_feature_gap: float,
    coil_clearance: float,
    circular_mount_diameter: float,
    maximum_unsupported_reach: float,
    include_electrical: bool = True,
    logo_bounds=None,
    progress_callback=None,
    logo_edge_clearance: float | None = None,
    pounds_per_mount: float = 5.0,
    led_weight_per_foot: float = 0.030,
    led_path_clearance: float = DEFAULT_LED_PATH_CLEARANCE,
) -> dict:
    notify_progress(progress_callback, stage="preparing", message="Recovering letter backs...")
    source_doc = ezdxf.readfile(source)
    template_doc = ezdxf.readfile(template)
    if source_doc.dxfversion < "AC1015":
        source_doc.dxfversion = "AC1015"
    electrical_diameter = 0.375 if electrical_diameter is None else electrical_diameter
    if include_electrical and electrical_diameter <= 0:
        raise ValueError("Electrical diameter must be greater than zero.")
    if pounds_per_mount <= 0:
        raise ValueError("Pounds per mount must be greater than zero.")
    if led_weight_per_foot < 0:
        raise ValueError("LED weight per foot cannot be negative.")
    if led_path_clearance < 0:
        raise ValueError("LED path clearance cannot be negative.")

    footprint, template_center = keyhole_template_geometry(template_doc)
    recovered = robust_back_polygons(source_doc.modelspace())
    backs, labels = procedural_back_labels(recovered, text_value, logo_bounds)
    if "KEYHOLES" not in source_doc.layers:
        source_doc.layers.add("KEYHOLES", color=3)
    if include_electrical and "ELECTRICAL_HOLES" not in source_doc.layers:
        source_doc.layers.add("ELECTRICAL_HOLES", color=5)
    if "MOUNTING_HOLES" not in source_doc.layers:
        source_doc.layers.add("MOUNTING_HOLES", color=6)

    modelspace = source_doc.modelspace()
    source_entities = assign_entities(modelspace, backs)
    canonical_layouts: dict[
        tuple[str, bytes],
        tuple[list[MountFeature], tuple[float, float] | None],
    ] = {}
    total_keyholes = 0
    total_round_mounts = 0
    mass_estimates = [estimate_channel_letter_mass(back, led_weight_per_foot) for back in backs]
    led_guides = [approximate_led_centerline_points(back) for back in backs]
    mounts_by_back: list[list[MountFeature]] = []
    for index, (label, back, outlines, mass_estimate, led_guide) in enumerate(
        zip(labels, backs, source_entities, mass_estimates, led_guides), 1
    ):
        notify_progress(
            progress_callback,
            stage="piece_started",
            index=index,
            total=len(backs),
            label=label,
            bounds=back.bounds,
        )
        min_x, min_y, _, _ = back.bounds
        key = canonical_geometry_key(label, back)
        if key in canonical_layouts:
            local_mounts, local_electrical = canonical_layouts[key]
            mounts = [
                MountFeature(
                    feature.kind,
                    (round(min_x + feature.center[0], 4), round(min_y + feature.center[1], 4)),
                )
                for feature in local_mounts
            ]
            electrical = (
                (round(min_x + local_electrical[0], 4), round(min_y + local_electrical[1], 4))
                if local_electrical is not None
                else None
            )
        else:
            selected_edge_clearance = (
                logo_edge_clearance
                if label.upper().startswith("SHAPE_") and logo_edge_clearance is not None
                else coil_clearance
            )
            keyhole_centers = targeted_keyhole_layout(
                back,
                label,
                footprint,
                template_center,
                minimum_keyhole_spacing,
                selected_edge_clearance,
                mass_estimate.total_lb,
                pounds_per_mount,
                circular_mount_diameter,
                led_guide,
                led_path_clearance,
            )
            mounts = choose_mount_features(
                back,
                keyhole_centers,
                footprint,
                template_center,
                selected_edge_clearance,
                circular_mount_diameter,
            )
            mounts = augment_mounts_for_coverage(
                back,
                label,
                mounts,
                footprint,
                template_center,
                selected_edge_clearance,
                circular_mount_diameter,
                effective_mount_spacing(back, minimum_keyhole_spacing, label),
                maximum_unsupported_reach,
            )
            validate_mount_layout(
                back,
                label,
                mounts,
                effective_mount_spacing(back, minimum_keyhole_spacing, label),
                maximum_unsupported_reach,
                False,
            )
            electrical = None
            if include_electrical:
                electrical = electrical_center_for_back(
                    back,
                    label,
                    mounts,
                    footprint,
                    template_center,
                    electrical_diameter,
                    circular_mount_diameter,
                    minimum_feature_gap,
                )
            canonical_layouts[key] = (
                [
                    MountFeature(feature.kind, (feature.center[0] - min_x, feature.center[1] - min_y))
                    for feature in mounts
                ],
                (electrical[0] - min_x, electrical[1] - min_y) if electrical is not None else None,
            )

        features = []
        mounts_by_back.append(list(mounts))
        for mount in mounts:
            if mount.kind == "keyhole":
                features.extend(
                    copy_keyhole(template_doc, modelspace, mount.center, template_center, "KEYHOLES")
                )
                total_keyholes += 1
            else:
                features.append(
                    modelspace.add_circle(
                        mount.center,
                        circular_mount_diameter / 2,
                        dxfattribs={"layer": "MOUNTING_HOLES", "color": 6},
                    )
                )
                total_round_mounts += 1
        if electrical is not None:
            features.append(
                modelspace.add_circle(
                    electrical,
                    electrical_diameter / 2,
                    dxfattribs={"layer": "ELECTRICAL_HOLES", "color": 5},
                )
            )
        safe_label = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_") or "BACK"
        group = source_doc.groups.new(name=f"BACK_{index:02d}_{safe_label}")
        group.extend(outlines + features)
        mount_description = "; ".join(
            f"{feature.kind}@({feature.center[0]:.3f}, {feature.center[1]:.3f})"
            for feature in mounts
        )
        electrical_description = electrical if electrical is not None else "disabled"
        print(
            f"{index:02d} {label}: {mass_estimate.total_lb:.2f} lb; "
            f"{mount_description}; electrical {electrical_description}"
        )
        notify_progress(
            progress_callback,
            stage="piece_complete",
            index=index,
            total=len(backs),
            label=label,
            bounds=back.bounds,
            feature_count=len(mounts) + (1 if electrical is not None else 0),
        )

    notify_progress(progress_callback, stage="saving", message="Grouping mounts and saving DXF...")
    source_doc.saveas(output)
    notify_progress(progress_callback, stage="finished", message="Keyhole file finished.")
    print(f"Wrote: {output}")
    print(
        f"Backs: {len(backs)}; keyholes: {total_keyholes}; "
        f"round mounts: {total_round_mounts} at diameter {circular_mount_diameter:.3f}; "
        f"electrical: {len(backs) if include_electrical else 0}"
        + (f" at diameter {electrical_diameter:.3f}" if include_electrical else "")
    )
    return {
        "backs": backs,
        "labels": labels,
        "mass_estimates": mass_estimates,
        "mounts_by_back": mounts_by_back,
        "led_guides": led_guides,
        "total_mass_lb": sum(estimate.total_lb for estimate in mass_estimates),
        "total_led_length_ft": sum(estimate.led_length_ft for estimate in mass_estimates),
    }


def containing_back(point, backs: list[Polygon]) -> int:
    probe = Point(*point)
    containing = [
        (index, back.area)
        for index, back in enumerate(backs)
        if Polygon(back.exterior).buffer(0.02).contains(probe)
    ]
    if not containing:
        return min(range(len(backs)), key=lambda index: backs[index].distance(probe))
    return min(containing, key=lambda item: item[1])[0]


def generate_from_reference(
    source: Path,
    reference: Path,
    template: Path,
    output: Path,
    electrical_diameter: float | None,
    include_electrical: bool = True,
    progress_callback=None,
) -> dict:
    notify_progress(progress_callback, stage="preparing", message="Reading calibrated keyhole reference...")
    source_doc = ezdxf.readfile(source)
    reference_doc = ezdxf.readfile(reference)
    template_doc = ezdxf.readfile(template)
    if source_doc.dxfversion < "AC1015":
        source_doc.dxfversion = "AC1015"

    footprint, template_center = keyhole_template_geometry(template_doc)
    reference_keyholes, reference_electrical = calibrated_features(reference_doc, footprint)
    if include_electrical:
        electrical_diameter, diameter_source = choose_electrical_diameter(
            reference_electrical,
            electrical_diameter,
        )
    else:
        electrical_diameter, diameter_source = 0.375, "disabled"
    offset_x, offset_y = layout_translation(source_doc, reference_doc)
    keyhole_centers = [(x + offset_x, y + offset_y) for x, y in reference_keyholes]
    electrical_centers = (
        [(x + offset_x, y + offset_y) for x, y, _ in reference_electrical]
        if include_electrical
        else []
    )

    backs = robust_back_polygons(source_doc.modelspace())
    if not backs:
        raise ValueError("No closed back outlines could be recovered from the source DXF.")
    if "KEYHOLES" not in source_doc.layers:
        source_doc.layers.add("KEYHOLES", color=3)
    if include_electrical and "ELECTRICAL_HOLES" not in source_doc.layers:
        source_doc.layers.add("ELECTRICAL_HOLES", color=5)

    modelspace = source_doc.modelspace()
    source_entities = assign_entities(modelspace, backs)
    feature_entities = [[] for _ in backs]
    keyhole_counts = [0 for _ in backs]
    electrical_counts = [0 for _ in backs]

    for center in keyhole_centers:
        back_index = containing_back(center, backs)
        footprint_at_target = translate(
            footprint,
            xoff=center[0] - template_center.x,
            yoff=center[1] - template_center.y,
        )
        if not Polygon(backs[back_index].exterior).buffer(0.01).contains(footprint_at_target):
            raise ValueError(f"Calibrated keyhole at {center} does not fit its source back.")
        feature_entities[back_index].extend(
            copy_keyhole(template_doc, modelspace, center, template_center, "KEYHOLES")
        )
        keyhole_counts[back_index] += 1

    radius = electrical_diameter / 2
    for center in electrical_centers:
        back_index = containing_back(center, backs)
        opening = Point(*center).buffer(radius, quad_segs=32)
        if not Polygon(backs[back_index].exterior).buffer(0.01).contains(opening):
            raise ValueError(f"Electrical opening at {center} does not fit its source back.")
        circle = modelspace.add_circle(
            center,
            radius,
            dxfattribs={"layer": "ELECTRICAL_HOLES", "color": 5},
        )
        feature_entities[back_index].append(circle)
        electrical_counts[back_index] += 1

    for index, (outlines, features) in enumerate(zip(source_entities, feature_entities), 1):
        notify_progress(
            progress_callback,
            stage="piece_started",
            index=index,
            total=len(backs),
            label=f"BACK_{index}",
            bounds=backs[index - 1].bounds,
        )
        group = source_doc.groups.new(name=f"BACK_{index:02d}")
        group.extend(outlines + features)
        notify_progress(
            progress_callback,
            stage="piece_complete",
            index=index,
            total=len(backs),
            label=f"BACK_{index}",
            bounds=backs[index - 1].bounds,
            feature_count=len(features),
        )

    notify_progress(progress_callback, stage="saving", message="Saving calibrated keyhole DXF...")
    source_doc.saveas(output)
    notify_progress(progress_callback, stage="finished", message="Keyhole file finished.")
    print(f"Wrote: {output}")
    print(f"Backs: {len(backs)}")
    print(f"Keyholes: {len(keyhole_centers)}; per back: {keyhole_counts}")
    print(
        f"Electrical: {len(electrical_centers)} at diameter {electrical_diameter:.3f} "
        f"({diameter_source}); per back: {electrical_counts}"
    )
    return {"backs": backs, "labels": [f"BACK_{index}" for index in range(1, len(backs) + 1)]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate calibrated keyholes and electrical openings in letter backs.")
    parser.add_argument("source", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reference", type=Path)
    mode.add_argument("--text", help="Reference-free lettering; spaces only describe layout and are ignored.")
    parser.add_argument("--keyhole-template", type=Path, required=True)
    parser.add_argument(
        "--electrical-diameter",
        type=float,
        help="Explicit diameter override. If omitted, a consistent reference size is preserved; fallback is 0.375.",
    )
    parser.add_argument("--no-electrical", action="store_true", help="Do not add electrical openings.")
    parser.add_argument(
        "--min-keyhole-spacing",
        type=float,
        default=2.70,
        help="Minimum center-to-center spacing between mounting points.",
    )
    parser.add_argument(
        "--min-feature-gap",
        type=float,
        default=0.20,
        help="Minimum material web between a keyhole and the electrical opening.",
    )
    parser.add_argument(
        "--coil-clearance",
        type=float,
        default=0.625,
        help="Preferred clear perimeter band; undersized keyhole positions become round mounts.",
    )
    parser.add_argument("--round-mount-diameter", type=float, default=0.250)
    parser.add_argument("--max-unsupported-reach", type=float, default=10.0)
    parser.add_argument("--led-path-clearance", type=float, default=DEFAULT_LED_PATH_CLEARANCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.source.with_name(f"{args.source.stem} - keyholes.dxf")
    if args.reference:
        generate_from_reference(
            args.source,
            args.reference,
            args.keyhole_template,
            output,
            args.electrical_diameter,
            not args.no_electrical,
        )
    else:
        generate_procedural(
            args.source,
            args.keyhole_template,
            output,
            args.text,
            args.electrical_diameter,
            args.min_keyhole_spacing,
            args.min_feature_gap,
            args.coil_clearance,
            args.round_mount_diameter,
            args.max_unsupported_reach,
            not args.no_electrical,
            led_path_clearance=args.led_path_clearance,
        )


if __name__ == "__main__":
    main()
