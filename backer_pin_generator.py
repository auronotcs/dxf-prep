from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from hole_generator import entity_line, medial_candidates, notify_progress, order_polygons_for_text
from keyhole_generator import polygonized_faces


LETTER_PIN_LAYER = "LETTER_PIN_HOLES"
BACKER_PIN_LAYER = "BACKER_CLEARANCE_HOLES"


@dataclass(frozen=True)
class BackerPanel:
    index: int
    polygon: Polygon
    letters: tuple[Polygon, ...]


def _polygon_parts(geometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        result = []
        for child in geometry.geoms:
            result.extend(_polygon_parts(child))
        return result
    return []


def discover_backer_panels(modelspace, precision: float = 0.001) -> list[BackerPanel]:
    """Recover large outer panels and the physical letter components nested in them."""
    faces = polygonized_faces(modelspace, precision)
    if not faces:
        return []
    shells = [Polygon(face.exterior) for face in faces]
    depths = []
    for index, face in enumerate(faces):
        probe = face.representative_point()
        depths.append(
            sum(
                1
                for other_index, shell in enumerate(shells)
                if other_index != index
                and shell.area > shells[index].area
                and shell.contains(probe)
            )
        )

    candidates: list[tuple[Polygon, tuple[Polygon, ...]]] = []
    depth_one = [face for face, depth in zip(faces, depths) if depth == 1]
    for face, depth in zip(faces, depths):
        if depth != 0:
            continue
        shell = Polygon(face.exterior)
        min_x, min_y, max_x, max_y = shell.bounds
        if (max_x - min_x) * (max_y - min_y) < 100.0:
            continue
        nested = [
            letter_face
            for letter_face in depth_one
            if shell.contains(letter_face.representative_point())
        ]
        if len(nested) < 2:
            continue
        # Noding can divide touching outlined copy into several faces.  Unioning
        # the depth-one material reconstructs the actual pieces to be drilled.
        material = unary_union(nested)
        letters = tuple(
            sorted(
                (polygon.buffer(0) for polygon in _polygon_parts(material) if polygon.area > 0.02),
                key=lambda polygon: (-polygon.bounds[3], polygon.bounds[0]),
            )
        )
        if letters:
            candidates.append((shell, letters))

    if not candidates:
        return []
    ordered_shells, _lines = order_polygons_for_text([candidate[0] for candidate in candidates])
    by_bounds = {
        tuple(round(value, 4) for value in shell.bounds): letters
        for shell, letters in candidates
    }
    return [
        BackerPanel(
            index,
            shell,
            by_bounds[tuple(round(value, 4) for value in shell.bounds)],
        )
        for index, shell in enumerate(ordered_shells, 1)
    ]


def match_selected_panels(panels: list[BackerPanel], selected_bounds=None) -> list[BackerPanel]:
    targets = list(selected_bounds or ())
    if not targets:
        return list(panels)
    selected = []
    used = set()
    for target in targets:
        if len(target) != 4:
            raise ValueError("A selected backer has invalid geometry bounds.")
        choices = []
        for index, panel in enumerate(panels):
            if index in used:
                continue
            differences = [abs(float(left) - float(right)) for left, right in zip(panel.polygon.bounds, target)]
            choices.append((max(differences), sum(differences), index))
        if not choices or min(choices)[0] > 0.05:
            raise ValueError("A selected backer could not be matched to the recovered DXF geometry.")
        panel_index = min(choices)[2]
        used.add(panel_index)
        selected.append(panels[panel_index])
    return sorted(selected, key=lambda panel: panel.index)


def _full_interior_candidates(
    polygon: Polygon,
    minimum_center_clearance: float,
    step: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    min_x, min_y, max_x, max_y = polygon.bounds
    xs = np.arange(min_x, max_x + step, step)
    ys = np.arange(min_y, max_y + step, step)
    xx, yy = np.meshgrid(xs, ys)
    mask = shapely.contains_xy(polygon, xx, yy)
    points = np.column_stack((xx[mask], yy[mask]))
    if len(points) == 0:
        return np.empty((0, 2)), np.empty((0,))
    geometries = shapely.points(points[:, 0], points[:, 1])
    clearances = np.asarray(shapely.distance(polygon.boundary, geometries))
    retained = clearances + 1e-9 >= minimum_center_clearance
    return points[retained], clearances[retained]


def _best_pair(
    points: np.ndarray,
    clearances: np.ndarray,
    minimum_spacing: float,
) -> tuple[int, int] | None:
    if len(points) < 2:
        return None
    centroid = np.mean(points, axis=0)
    seed_indices = {
        int(np.argmin(points[:, 0])),
        int(np.argmax(points[:, 0])),
        int(np.argmin(points[:, 1])),
        int(np.argmax(points[:, 1])),
        int(np.argmax(clearances)),
        int(np.argmin(np.linalg.norm(points - centroid, axis=1))),
    }
    stride = max(1, len(points) // 32)
    seed_indices.update(range(0, len(points), stride))
    best = None
    for first_index in seed_indices:
        separations = np.linalg.norm(points - points[first_index], axis=1)
        allowed = np.flatnonzero(separations + 1e-6 >= minimum_spacing)
        if len(allowed) == 0:
            continue
        scores = separations[allowed] + np.minimum(clearances[allowed], 0.75) * 0.30
        second_index = int(allowed[int(np.argmax(scores))])
        separation = float(separations[second_index])
        substance = min(float(clearances[first_index]), float(clearances[second_index]))
        score = separation + min(substance, 0.75) * 0.50
        candidate = (score, -first_index, -second_index, first_index, second_index)
        if best is None or candidate > best:
            best = candidate
    return None if best is None else (best[3], best[4])


def place_roll_pin_holes(
    polygon: Polygon,
    hole_diameter: float,
    material_edge_clearance: float,
    minimum_spacing: float,
) -> tuple[list[tuple[float, float]], int]:
    """Place stable roll-pin centers; return centers and the requested count."""
    if hole_diameter <= 0 or material_edge_clearance < 0 or minimum_spacing <= 0:
        raise ValueError("Pin diameter and spacing must be positive; edge clearance cannot be negative.")
    source_bounds = polygon.bounds
    source_min_x, source_min_y, _source_max_x, _source_max_y = source_bounds
    # Solve in component-local coordinates so identical copy has an identical
    # pin map regardless of its sheet position or floating-point grid phase.
    polygon = translate(polygon, xoff=-source_min_x, yoff=-source_min_y)
    min_x, min_y, local_max_x, local_max_y = polygon.bounds
    largest = max(local_max_x - min_x, local_max_y - min_y)
    requested_count = 2 if largest < 6.0 else max(3, min(6, math.ceil(largest / 10.0) + 2))
    minimum_center_clearance = hole_diameter / 2 + material_edge_clearance

    medial_points, medial_clearances = medial_candidates(
        polygon,
        0.04,
        minimum_center_clearance,
    )
    points = np.asarray(medial_points, dtype=float)
    clearances = np.asarray(medial_clearances, dtype=float)
    pair = _best_pair(points, clearances, minimum_spacing)
    if pair is None:
        points, clearances = _full_interior_candidates(
            polygon,
            minimum_center_clearance,
            step=0.04,
        )
        pair = _best_pair(points, clearances, minimum_spacing)

    if len(points) == 0:
        raise ValueError(
            "No roll-pin center fits the configured hole diameter and letter edge clearance "
            f"inside component bounds {tuple(round(value, 3) for value in source_bounds)}."
        )

    if pair is None:
        centroid = np.asarray((polygon.centroid.x, polygon.centroid.y))
        scores = clearances * 1.5 - np.linalg.norm(points - centroid, axis=1) * 0.05
        selected = [int(np.argmax(scores))]
    else:
        selected = [pair[0], pair[1]]
        while len(selected) < requested_count:
            selected_points = points[selected]
            separations = np.min(
                np.linalg.norm(points[:, None, :] - selected_points[None, :, :], axis=2),
                axis=1,
            )
            allowed = np.flatnonzero(separations + 1e-6 >= minimum_spacing)
            if len(allowed) == 0:
                break
            scores = separations[allowed] + np.minimum(clearances[allowed], 0.75) * 0.30
            selected.append(int(allowed[int(np.argmax(scores))]))

    centers = sorted(
        {
            (
                round(float(points[index][0]) + source_min_x, 4),
                round(float(points[index][1]) + source_min_y, 4),
            )
            for index in selected
        },
        key=lambda point: (-point[1], point[0]),
    )
    return centers, requested_count


def _local_component_geometry(polygon: Polygon) -> Polygon:
    min_x, min_y, _max_x, _max_y = polygon.bounds
    return translate(polygon, xoff=-min_x, yoff=-min_y)


def _canonical_component_signature(polygon: Polygon) -> tuple:
    min_x, min_y, max_x, max_y = polygon.bounds
    return (
        round(max_x - min_x, 2),
        round(max_y - min_y, 2),
        round(polygon.area, 2),
        round(polygon.length, 2),
        len(polygon.interiors),
    )


def equivalent_component_geometry(left: Polygon, right: Polygon) -> bool:
    """Match translated copies despite harmless DXF tessellation differences."""
    if _canonical_component_signature(left) != _canonical_component_signature(right):
        return False
    left_local = _local_component_geometry(left)
    right_local = _local_component_geometry(right)
    if left_local.hausdorff_distance(right_local) > 0.003:
        return False
    difference_area = left_local.symmetric_difference(right_local).area
    return difference_area <= max(0.01, min(left.area, right.area) * 0.002)


def _new_output_document(source_document) -> ezdxf.document.Drawing:
    document = ezdxf.new("R2000")
    try:
        document.header["$INSUNITS"] = source_document.header.get("$INSUNITS", 0)
    except Exception:
        pass
    for layer in source_document.layers:
        name = layer.dxf.name
        if name in document.layers:
            continue
        attributes = {"color": int(layer.dxf.get("color", 7))}
        document.layers.add(name, dxfattribs=attributes)
    if LETTER_PIN_LAYER not in document.layers:
        document.layers.add(LETTER_PIN_LAYER, color=6)
    if BACKER_PIN_LAYER not in document.layers:
        document.layers.add(BACKER_PIN_LAYER, color=5)
    return document


def _panel_for_probe(probe: Point, panels: list[BackerPanel]) -> int | None:
    containing = [
        (index, panel.polygon.area)
        for index, panel in enumerate(panels)
        if panel.polygon.buffer(0.02).covers(probe)
    ]
    return min(containing, key=lambda item: item[1])[0] if containing else None


def generate_backer_pin_job(
    source: Path,
    master_output: Path,
    letters_output: Path,
    backers_output: Path,
    letter_hole_diameter: float,
    backer_hole_diameter: float,
    letter_edge_clearance: float,
    minimum_pin_spacing: float,
    selected_backer_bounds=None,
    progress_callback=None,
) -> dict:
    source = Path(source)
    if letter_hole_diameter <= 0 or backer_hole_diameter <= 0:
        raise ValueError("Letter and backer hole diameters must be greater than zero.")
    if letter_edge_clearance < 0 or minimum_pin_spacing <= 0:
        raise ValueError("Letter edge clearance cannot be negative and pin spacing must be positive.")

    notify_progress(progress_callback, stage="preparing", message="Recovering backers and nested lettering...")
    source_document = ezdxf.readfile(source)
    recovered_panels = discover_backer_panels(source_document.modelspace())
    if not recovered_panels:
        raise ValueError("No backer panels containing nested lettering were detected in this DXF.")
    panels = match_selected_panels(recovered_panels, selected_backer_bounds)
    if not panels:
        raise ValueError("Select at least one backer panel.")

    layouts: list[list[list[tuple[float, float]]]] = []
    single_pin_components = []
    canonical_layouts: dict[
        tuple,
        list[tuple[Polygon, tuple[tuple[float, float], ...], int]],
    ] = {}
    component_counter = 0
    total_components = sum(len(panel.letters) for panel in panels)
    for panel in panels:
        panel_layouts = []
        for letter in panel.letters:
            component_counter += 1
            notify_progress(
                progress_callback,
                stage="piece_started",
                index=component_counter,
                total=total_components,
                label=f"Panel {panel.index} component {len(panel_layouts) + 1}",
                bounds=letter.bounds,
            )
            signature = _canonical_component_signature(letter)
            min_x, min_y, _max_x, _max_y = letter.bounds
            match = next(
                (
                    (offsets, requested)
                    for reference, offsets, requested in canonical_layouts.get(signature, ())
                    if equivalent_component_geometry(reference, letter)
                ),
                None,
            )
            if match is not None:
                offsets, requested_count = match
                centers = [
                    (round(min_x + offset_x, 4), round(min_y + offset_y, 4))
                    for offset_x, offset_y in offsets
                ]
            else:
                centers, requested_count = place_roll_pin_holes(
                    letter,
                    letter_hole_diameter,
                    letter_edge_clearance,
                    minimum_pin_spacing,
                )
                canonical_layouts.setdefault(signature, []).append(
                    (
                        letter,
                        tuple((x - min_x, y - min_y) for x, y in centers),
                        requested_count,
                    )
                )
            if len(centers) < requested_count:
                single_pin_components.append(
                    {
                        "panel": panel.index,
                        "component": len(panel_layouts) + 1,
                        "bounds": letter.bounds,
                        "pins": len(centers),
                        "requested": requested_count,
                    }
                )
            panel_layouts.append(centers)
            notify_progress(
                progress_callback,
                stage="piece_complete",
                index=component_counter,
                total=total_components,
                label=f"Panel {panel.index} component {len(panel_layouts)}",
                bounds=letter.bounds,
                feature_count=len(centers),
            )
        layouts.append(panel_layouts)

    notify_progress(progress_callback, stage="writing", message="Building registered master and separated DXFs...")
    master_document = _new_output_document(source_document)
    letters_document = _new_output_document(source_document)
    backers_document = _new_output_document(source_document)
    master_space = master_document.modelspace()
    letters_space = letters_document.modelspace()
    backers_space = backers_document.modelspace()
    master_groups = [[] for _panel in panels]
    letter_groups = [[] for _panel in panels]
    backer_groups = [[] for _panel in panels]
    letter_boundaries = [unary_union([letter.boundary for letter in panel.letters]) for panel in panels]

    for entity in source_document.modelspace():
        line = entity_line(entity)
        if line is None:
            continue
        probe = line.interpolate(0.5, normalized=True)
        panel_index = _panel_for_probe(probe, panels)
        if panel_index is None:
            continue
        panel = panels[panel_index]
        letter_distance = float(letter_boundaries[panel_index].distance(probe))
        backer_distance = float(panel.polygon.boundary.distance(probe))
        if backer_distance <= 0.03 and backer_distance < letter_distance:
            master_clone = entity.copy()
            backer_clone = entity.copy()
            master_space.add_entity(master_clone)
            backers_space.add_entity(backer_clone)
            master_groups[panel_index].append(master_clone)
            backer_groups[panel_index].append(backer_clone)
        else:
            # Every non-panel contour enclosed by a selected backer belongs to
            # its applied copy.  This also preserves tiny degree/star outlines
            # that may disappear when touching paths are topologically unioned.
            master_clone = entity.copy()
            letter_clone = entity.copy()
            master_space.add_entity(master_clone)
            letters_space.add_entity(letter_clone)
            master_groups[panel_index].append(master_clone)
            letter_groups[panel_index].append(letter_clone)

    total_pins = 0
    for panel_index, (panel, panel_layouts) in enumerate(zip(panels, layouts)):
        for centers in panel_layouts:
            for center in centers:
                total_pins += 1
                master_backer = master_space.add_circle(
                    center,
                    backer_hole_diameter / 2,
                    dxfattribs={"layer": BACKER_PIN_LAYER, "color": 5},
                )
                master_letter = master_space.add_circle(
                    center,
                    letter_hole_diameter / 2,
                    dxfattribs={"layer": LETTER_PIN_LAYER, "color": 6},
                )
                letter_circle = letters_space.add_circle(
                    center,
                    letter_hole_diameter / 2,
                    dxfattribs={"layer": LETTER_PIN_LAYER, "color": 6},
                )
                backer_circle = backers_space.add_circle(
                    center,
                    backer_hole_diameter / 2,
                    dxfattribs={"layer": BACKER_PIN_LAYER, "color": 5},
                )
                master_groups[panel_index].extend((master_backer, master_letter))
                letter_groups[panel_index].append(letter_circle)
                backer_groups[panel_index].append(backer_circle)

        safe_panel = re.sub(r"[^A-Za-z0-9]+", "_", str(panel.index)).strip("_") or "PANEL"
        master_group = master_document.groups.new(name=f"BACKER_{panel_index + 1:02d}_{safe_panel}_MASTER")
        master_group.extend(master_groups[panel_index])
        letter_group = letters_document.groups.new(name=f"BACKER_{panel_index + 1:02d}_{safe_panel}_LETTERS")
        letter_group.extend(letter_groups[panel_index])
        backer_group = backers_document.groups.new(name=f"BACKER_{panel_index + 1:02d}_{safe_panel}_PANEL")
        backer_group.extend(backer_groups[panel_index])

    for document, output in (
        (master_document, master_output),
        (letters_document, letters_output),
        (backers_document, backers_output),
    ):
        document.saveas(output)

    notify_progress(progress_callback, stage="finished", message="Backer pin package finished.")
    return {
        "panels": panels,
        "layouts": layouts,
        "total_components": total_components,
        "total_pins": total_pins,
        "single_pin_components": single_pin_components,
        "master_output": Path(master_output),
        "letters_output": Path(letters_output),
        "backers_output": Path(backers_output),
    }
