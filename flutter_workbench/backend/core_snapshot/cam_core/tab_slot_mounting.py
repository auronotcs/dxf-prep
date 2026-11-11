from __future__ import annotations

"""Cutter-aware tab/slot mating samples for routed rail mounting.

The rail plan-view slot and the letter front-view tab root are intentionally
modeled as separate interference problems.  A relieved rail slot does not prove
that a rounded tab shoulder will seat, and a root notch does not prove there is
enough material around the rail opening.
"""

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import math
from typing import Iterable
from uuid import uuid4

from ._vendor import pyclipper
from .bent_tab_mounting import (
    BentTabAnchor, BentTabManualPlacement, BentTabResult, BentTabSettings,
    generate_bent_tab_back, logical_bent_tab_edges, manual_bent_tab_placement,
)
from .model import Entity, Line, Point, Polyline, TextEntity


LAYER_TAB_SLOT_SOURCE = "TAB_SLOT_SOURCE_REFERENCE"
LAYER_TAB_SLOT_RAIL = "TAB_SLOT_RAIL_RELIEF"
LAYER_TAB_SLOT_RAIL_MOUNT_HOLES = "TAB_SLOT_RAIL_MOUNT_HOLES"
LAYER_TAB_SLOT_LETTER = "TAB_SLOT_LETTER_PROFILE"
LAYER_TAB_SLOT_RAIL_PROFILE = "TAB_SLOT_RAIL_PROFILE"
LAYER_TAB_SLOT_GUIDES = "TAB_SLOT_FIT_GUIDES"


@dataclass(frozen=True)
class TabSlotFitSpec:
    sample_id: str
    label: str
    tab_width: float
    letter_thickness: float
    rail_thickness: float
    tab_projection: float
    slot_width: float
    slot_height: float
    cutter_diameter: float
    shoulder_land: float = 0.5
    source_slot_bounds: tuple[float, float, float, float] | None = None
    source_tab_bounds: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class TabSlotFitPreview:
    spec: TabSlotFitSpec
    nominal_slot_outline: tuple[Point, ...]
    relieved_slot_outline: tuple[Point, ...]
    nominal_tab_outline: tuple[Point, ...]
    relieved_letter_outline: tuple[Point, ...]
    width_clearance_total: float
    thickness_clearance_total: float
    root_relief_radius: float
    remaining_seating_land: float
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class TabSlotRailSettings:
    nominal_tab_width: float
    minimum_tab_width: float
    letter_thickness: float
    rail_thickness: float
    rail_width: float
    tab_projection: float
    slot_width_clearance: float
    slot_thickness_clearance: float
    cutter_diameter: float
    root_relief_radius: float
    edge_end_clearance: float
    short_edge_center_threshold: float
    rail_slot_center_y: float
    tolerance: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, value: dict) -> "TabSlotRailSettings":
        return cls(**{name: float(value[name]) for name in cls.__dataclass_fields__})


def split_tab_slot_rail_span(
        origin_x: float,
        length: float,
        slot_bounds: Iterable[tuple[float, float]],
        *,
        maximum_piece_length: float,
        joint_clearance: float,
        manual_cut_offsets: Iterable[float] = (),
        tolerance: float = 1e-7,
) -> tuple[tuple[float, float], ...]:
    """Split a finished rail into stock-length pieces without cutting a slot.

    Returned spans remain in assembly coordinates. Cuts are balanced where
    possible, are at least ``joint_clearance`` from every slot edge, and every
    finished piece is no longer than ``maximum_piece_length``.
    """
    values = (origin_x, length, maximum_piece_length, joint_clearance, tolerance)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Rail split dimensions must be finite.")
    if length <= tolerance:
        raise ValueError("Rail length must be positive before it can be split.")
    if joint_clearance < 0.0 or tolerance <= 0.0:
        raise ValueError("Rail split clearance must be nonnegative and tolerance positive.")
    end_x = origin_x + length

    blocked: list[tuple[float, float]] = []
    for raw_start, raw_end in slot_bounds:
        start, end = sorted((float(raw_start), float(raw_end)))
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Rail slot bounds must be finite.")
        start = max(origin_x, start - joint_clearance)
        end = min(end_x, end + joint_clearance)
        if end >= start:
            blocked.append((start, end))
    blocked.sort()
    merged: list[list[float]] = []
    for start, end in blocked:
        if merged and start <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    manual_offsets = tuple(float(value) for value in manual_cut_offsets)
    if any(not math.isfinite(value) for value in manual_offsets):
        raise ValueError("Manual rail cut offsets must be finite.")
    if manual_offsets:
        ordered_offsets = sorted(manual_offsets)
        if any(
                second - first <= tolerance
                for first, second in zip(ordered_offsets, ordered_offsets[1:])):
            raise ValueError("Manual rail cut offsets must be unique.")
        if ordered_offsets[0] <= tolerance or ordered_offsets[-1] >= length - tolerance:
            raise ValueError("Every manual rail cut must be greater than 0 and less than the full rail length.")
        cuts = tuple(origin_x + value for value in ordered_offsets)
        for cut, offset in zip(cuts, ordered_offsets):
            if any(start + tolerance < cut < end - tolerance for start, end in merged):
                raise ValueError(
                    f"Manual rail cut {offset:g} crosses a slot clearance zone. "
                    "Move the cut farther from the registered slot."
                )
        edges = (origin_x, *cuts, end_x)
        spans = tuple(zip(edges, edges[1:]))
        if maximum_piece_length > tolerance:
            oversized = max((end - start for start, end in spans), default=0.0)
            if oversized > maximum_piece_length + tolerance:
                raise ValueError(
                    f"Manual rail cuts leave a {oversized:g} long piece, exceeding the "
                    f"{maximum_piece_length:g} maximum. Add another manual cut."
                )
        return spans

    if maximum_piece_length <= tolerance or length <= maximum_piece_length + tolerance:
        return ((origin_x, end_x),)

    def allowed_intervals(low: float, high: float) -> list[tuple[float, float]]:
        if high < low - tolerance:
            return []
        cursor = low
        allowed: list[tuple[float, float]] = []
        for block_start, block_end in merged:
            if block_end < cursor - tolerance:
                continue
            if block_start > high + tolerance:
                break
            if block_start >= cursor - tolerance:
                allowed.append((cursor, min(high, block_start)))
            cursor = max(cursor, block_end)
            if cursor > high + tolerance:
                break
        if cursor <= high + tolerance:
            allowed.append((cursor, high))
        return [
            (start, end) for start, end in allowed
            if end >= start - tolerance
        ]

    minimum_piece = max(tolerance * 10.0, min(joint_clearance, maximum_piece_length * 0.02))

    def choose_cuts(piece_count: int) -> tuple[float, ...] | None:
        def search(start: float, remaining_pieces: int) -> tuple[float, ...] | None:
            if remaining_pieces == 1:
                return () if end_x - start <= maximum_piece_length + tolerance else None
            following = remaining_pieces - 1
            low = max(start + minimum_piece, end_x - maximum_piece_length * following)
            high = min(start + maximum_piece_length, end_x - minimum_piece * following)
            ideal = start + (end_x - start) / remaining_pieces
            candidates: list[float] = []
            for interval_start, interval_end in allowed_intervals(low, high):
                candidate = min(max(ideal, interval_start), interval_end)
                candidates.extend((candidate, interval_start, interval_end))
            unique = sorted(
                {round(value, 12): value for value in candidates}.values(),
                key=lambda value: (abs(value - ideal), value),
            )
            for cut in unique:
                if cut <= start + tolerance or cut >= end_x - tolerance:
                    continue
                tail = search(cut, following)
                if tail is not None:
                    return (cut, *tail)
            return None

        return search(origin_x, piece_count)

    minimum_count = max(2, math.ceil((length - tolerance) / maximum_piece_length))
    for piece_count in range(minimum_count, minimum_count + len(merged) + 5):
        cuts = choose_cuts(piece_count)
        if cuts is None:
            continue
        edges = (origin_x, *cuts, end_x)
        spans = tuple(zip(edges, edges[1:]))
        if all(end - start <= maximum_piece_length + tolerance for start, end in spans):
            return spans
    raise ValueError(
        "No safe rail split avoids the registered slots. Reduce the slot-clearance zone "
        "or use longer rail stock."
    )


def tab_slot_rail_mount_hole_positions(
        spans: Iterable[tuple[float, float]],
        *,
        maximum_spacing: float,
        edge_offset: float,
        manual_offsets: Iterable[float] = (),
        automatic: bool = True,
        tolerance: float = 1e-7,
) -> tuple[tuple[float, int, str], ...]:
    """Return rail mounting-hole X positions and owning piece indices.

    Manual offsets are measured from the full rail's left end.  Automatic
    placement puts a hole near every physical piece end, which deliberately
    creates one hole on either side of an internal rail cut, then fills long
    spans so adjacent holes do not exceed ``maximum_spacing``.
    """
    pieces = tuple((float(start), float(end)) for start, end in spans)
    if not pieces:
        raise ValueError("At least one rail piece is required for mounting holes.")
    if tolerance <= 0.0 or not math.isfinite(tolerance):
        raise ValueError("Rail mounting-hole tolerance must be positive and finite.")
    for start, end in pieces:
        if not math.isfinite(start) or not math.isfinite(end) or end - start <= tolerance:
            raise ValueError("Rail mounting-hole pieces must have positive finite spans.")
    if edge_offset < 0.0 or not math.isfinite(edge_offset):
        raise ValueError("Rail mounting-hole edge offset must be finite and nonnegative.")
    if automatic and (maximum_spacing <= tolerance or not math.isfinite(maximum_spacing)):
        raise ValueError("Automatic rail mounting-hole spacing must be positive and finite.")

    positions: list[tuple[float, int, str]] = []
    if automatic:
        for piece_index, (start, end) in enumerate(pieces, start=1):
            length = end - start
            if length <= edge_offset * 2.0 + tolerance:
                candidates = ((start + end) / 2.0,)
            else:
                first = start + edge_offset
                last = end - edge_offset
                intervals = max(1, math.ceil((last - first) / maximum_spacing))
                candidates = tuple(
                    first + (last - first) * index / intervals
                    for index in range(intervals + 1)
                )
            positions.extend((value, piece_index, "automatic") for value in candidates)

    full_start = pieces[0][0]
    full_end = pieces[-1][1]
    manual_values = tuple(float(value) for value in manual_offsets)
    if any(not math.isfinite(value) for value in manual_values):
        raise ValueError("Manual rail mounting-hole offsets must be finite.")
    for offset in manual_values:
        x = full_start + offset
        if x <= full_start + tolerance or x >= full_end - tolerance:
            raise ValueError("Manual rail mounting holes must lie inside the full rail length.")
        owners = [
            index for index, (start, end) in enumerate(pieces, start=1)
            if x >= start - tolerance and x <= end + tolerance
        ]
        if len(owners) != 1:
            raise ValueError(
                f"Manual rail mounting hole {offset:g} lies on a rail cut. "
                "Move it onto one physical rail piece."
            )
        positions.append((x, owners[0], "manual"))

    ordered = sorted(positions, key=lambda item: (item[0], item[1], item[2] != "manual"))
    merged_positions: list[tuple[float, int, str]] = []
    for item in ordered:
        if merged_positions and abs(item[0] - merged_positions[-1][0]) <= tolerance:
            if item[2] == "manual":
                merged_positions[-1] = item
            continue
        merged_positions.append(item)
    return tuple(merged_positions)


def tab_slot_rail_mount_hole_centers(
        positions: Iterable[tuple[float, int, str]],
        *,
        rail_width: float,
        hole_diameter: float,
        row_count: str = "Single",
        placement: str = "Centered",
        row_spacing: float = 1.0,
        row_offset: float = 0.0,
        tolerance: float = 1e-7,
) -> tuple[tuple[float, float, int, str, int], ...]:
    """Expand longitudinal hole stations into local rail-face centers.

    ``positions`` owns the along-rail policy (automatic spacing, manual
    offsets, and piece ownership).  This function owns only the pattern
    across the rail face.  A double centered/offset pattern places a pair at
    each station; a staggered pattern alternates one hole between two rows.
    Returned tuples are ``(x, local_y, piece_index, source, row_index)``.
    """
    if tolerance <= 0.0 or not math.isfinite(tolerance):
        raise ValueError("Rail mounting-hole tolerance must be positive and finite.")
    if not math.isfinite(rail_width) or rail_width <= tolerance:
        raise ValueError("Rail face width must be positive and finite.")
    if not math.isfinite(hole_diameter) or hole_diameter <= tolerance:
        raise ValueError("Rail mounting-hole diameter must be positive and finite.")
    if hole_diameter >= rail_width - tolerance:
        raise ValueError("Rail mounting-hole diameter must be smaller than the rail face width.")

    normalized_rows = str(row_count).strip().casefold()
    if normalized_rows in {"single", "single row", "1", "one"}:
        rows = 1
    elif normalized_rows in {"double", "double row", "2", "two"}:
        rows = 2
    else:
        raise ValueError("Rail mounting-hole rows must be Single or Double.")

    normalized_placement = str(placement).strip().casefold()
    if normalized_placement not in {"centered", "offset", "staggered"}:
        raise ValueError("Rail mounting-hole placement must be Centered, Offset, or Staggered.")
    if normalized_placement == "staggered" and rows != 2:
        raise ValueError("Staggered rail mounting holes require Double rows.")
    if not math.isfinite(row_spacing) or row_spacing < 0.0:
        raise ValueError("Rail mounting-hole row spacing must be finite and nonnegative.")
    if not math.isfinite(row_offset):
        raise ValueError("Rail mounting-hole row offset must be finite.")
    if rows == 2 and row_spacing <= tolerance:
        raise ValueError("Double rail mounting-hole rows require positive row spacing.")

    center_y = rail_width / 2.0
    pattern_center = center_y + (row_offset if normalized_placement == "offset" else 0.0)
    if rows == 1:
        row_values = (pattern_center,)
    else:
        half_spacing = row_spacing / 2.0
        row_values = (pattern_center - half_spacing, pattern_center + half_spacing)
    radius = hole_diameter / 2.0
    for local_y in row_values:
        if local_y - radius < -tolerance or local_y + radius > rail_width + tolerance:
            raise ValueError(
                "Rail mounting-hole rows do not fit inside the rail face. "
                "Reduce row spacing/offset or use a wider rail."
            )

    stations = tuple(positions)
    centers: list[tuple[float, float, int, str, int]] = []
    for station_index, (x, piece_index, source) in enumerate(stations):
        if normalized_placement == "staggered":
            row_index = station_index % 2
            centers.append((x, row_values[row_index], piece_index, source, row_index + 1))
        else:
            centers.extend(
                (x, local_y, piece_index, source, row_index)
                for row_index, local_y in enumerate(row_values, start=1)
            )
    return tuple(centers)


@dataclass(frozen=True)
class TabSlotBaseTrimPlan:
    """Reviewed common baseline and every lower source that needs clipping."""

    baseline_y: float
    reference_owner_ids: tuple[str, ...]
    trim_owner_ids: tuple[str, ...]
    trim_depths: tuple[tuple[str, float], ...]
    manual_baseline: bool = False

    def to_dict(self) -> dict:
        return {
            "baseline_y": self.baseline_y,
            "reference_owner_ids": list(self.reference_owner_ids),
            "trim_owner_ids": list(self.trim_owner_ids),
            "trim_depths": {owner_id: depth for owner_id, depth in self.trim_depths},
            "manual_baseline": self.manual_baseline,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TabSlotBaseTrimPlan":
        raw_depths = value.get("trim_depths", {})
        depth_items = (
            raw_depths.items() if isinstance(raw_depths, dict) else raw_depths
        )
        return cls(
            baseline_y=float(value["baseline_y"]),
            reference_owner_ids=tuple(str(item) for item in value.get("reference_owner_ids", ())),
            trim_owner_ids=tuple(str(item) for item in value.get("trim_owner_ids", ())),
            trim_depths=tuple((str(owner_id), float(depth)) for owner_id, depth in depth_items),
            manual_baseline=bool(value.get("manual_baseline", False)),
        )


@dataclass(frozen=True)
class TabSlotPlacement:
    pair_id: str
    owner_entity_id: str
    owner_part_id: str
    anchor: BentTabAnchor
    assembly_center: Point
    rail_slot_center: Point
    tab_width: float
    auto_centered: bool = False
    manual_width: bool = False
    placement_set_id: str = ""
    placement_set_index: int = 0

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "owner_entity_id": self.owner_entity_id,
            "owner_part_id": self.owner_part_id,
            "anchor": {"edge_index": self.anchor.edge_index, "fraction": self.anchor.fraction},
            "assembly_center": [self.assembly_center.x, self.assembly_center.y],
            "rail_slot_center": [self.rail_slot_center.x, self.rail_slot_center.y],
            "tab_width": self.tab_width,
            "auto_centered": self.auto_centered,
            "manual_width": self.manual_width,
            "placement_set_id": self.placement_set_id,
            "placement_set_index": self.placement_set_index,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TabSlotPlacement":
        anchor = value["anchor"]
        assembly = value["assembly_center"]
        rail = value["rail_slot_center"]
        return cls(
            pair_id=str(value["pair_id"]),
            owner_entity_id=str(value["owner_entity_id"]),
            owner_part_id=str(value["owner_part_id"]),
            anchor=BentTabAnchor(int(anchor["edge_index"]), float(anchor["fraction"])),
            assembly_center=Point(float(assembly[0]), float(assembly[1])),
            rail_slot_center=Point(float(rail[0]), float(rail[1])),
            tab_width=float(value["tab_width"]),
            auto_centered=bool(value.get("auto_centered", False)),
            manual_width=bool(value.get("manual_width", False)),
            placement_set_id=str(value.get("placement_set_id", "")),
            placement_set_index=int(value.get("placement_set_index", 0)),
        )


@dataclass(frozen=True)
class TabSlotPairProposal:
    """One reviewed two-tab action and its measured result.

    ``placements`` always contains exactly two independently owned tab/slot
    records.  ``placement_set_id`` groups them for replacement and undo without
    weakening the existing one-tab/one-slot identity contract.
    """

    placement_set_id: str
    placements: tuple[TabSlotPlacement, TabSlotPlacement]
    arrangement: str  # currently ``continuous_support``
    requested_spacing: float | None
    actual_spacing: float
    spacing_adjusted: bool


@dataclass(frozen=True)
class TabSlotPartResult:
    bent_result: BentTabResult
    placements: tuple[TabSlotPlacement, ...]
    rail_slots: tuple[Polyline, ...]
    slot_references: tuple[Polyline, ...]
    seating_guides: tuple[Line, ...]
    warnings: tuple[str, ...]


def _closed_points(source: Polyline, tolerance: float) -> list[Point]:
    points = list(source.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= tolerance:
        points.pop()
    if not source.closed or len(points) < 3:
        raise ValueError(f"Letter {source.id} must be a closed Polyline for base trimming.")
    return points


def _bottom_flat_run(source: Polyline, tolerance: float) -> tuple[float, float, float]:
    points = _closed_points(source, tolerance)
    xmin = min(point.x for point in points)
    xmax = max(point.x for point in points)
    ymin = min(point.y for point in points)
    width = xmax - xmin
    band = max(tolerance * 10.0, width * 1e-5)
    flat_run = 0.0
    for start, end in zip(points, (*points[1:], points[0])):
        if abs(end.y - start.y) <= band and max(start.y, end.y) <= ymin + band:
            flat_run += abs(end.x - start.x)
    return ymin, width, flat_run


def tab_slot_bottom_support_span(
        sources: Iterable[Polyline], *, tolerance: float) -> tuple[float, float]:
    """Return the horizontal span that actually reaches each letter's base.

    Full entity bounds are a poor rail-sizing proxy for letters such as V, W,
    and Y: their upper strokes can overhang a much narrower bottom foot.  This
    envelope samples each closed outline at its own lowest Y and therefore
    retains a square letter's complete base while reducing pointed/end letters
    to their real lower support.  Rail-slot geometry is still checked
    separately by the caller, so a narrow or curved foot can never make a
    generated slot fall outside the rail.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Bottom-support tolerance must be positive and finite.")
    source_list = tuple(sources)
    if not source_list:
        raise ValueError("Choose one or more letters before sizing the rail.")

    support_x: list[float] = []
    for source in source_list:
        points = _closed_points(source, tolerance)
        xmin = min(point.x for point in points)
        xmax = max(point.x for point in points)
        ymin = min(point.y for point in points)
        ymax = max(point.y for point in points)
        # Imported outlines often carry harmless coordinate noise at a nominal
        # base.  Keep the band microscopic relative to the part so it gathers
        # that base without climbing a V/W slope far enough to recreate the
        # full-outline overhang we are deliberately excluding.
        band = max(tolerance * 20.0, max(xmax - xmin, ymax - ymin) * 1e-7)
        sample_y = ymin + band
        local_x = [point.x for point in points if point.y <= sample_y]
        segments = tuple(zip(points, (*points[1:], points[0])))
        flat_base_x = [
            value
            for start, end in segments
            if abs(end.y - start.y) <= tolerance
            and max(start.y, end.y) <= ymin + band
            for value in (start.x, end.x)
        ]
        if flat_base_x:
            # A real flat foot is authoritative. Sampling its adjoining slope
            # even a microscopic distance upward would make the inferred rail
            # fractionally longer on serif V/W outlines.
            local_x = flat_base_x
        for start, end in (() if flat_base_x else segments):
            low_y, high_y = sorted((start.y, end.y))
            if low_y - tolerance > sample_y or high_y + tolerance < sample_y:
                continue
            dy = end.y - start.y
            if abs(dy) <= tolerance:
                if max(start.y, end.y) <= sample_y + tolerance:
                    local_x.extend((start.x, end.x))
                continue
            fraction = (sample_y - start.y) / dy
            if -tolerance <= fraction <= 1.0 + tolerance:
                local_x.append(start.x + (end.x - start.x) * max(0.0, min(1.0, fraction)))
        if not local_x:
            # ``ymin`` came from these points, so this is defensive only.
            local_x.append(min(points, key=lambda point: point.y).x)
        support_x.extend((min(local_x), max(local_x)))
    return min(support_x), max(support_x)


def analyze_tab_slot_base_trim(
        sources: Iterable[Polyline], *, baseline_y: float | None = None,
        maximum_trim: float, tolerance: float,
        minimum_flat_fraction: float = 0.12) -> TabSlotBaseTrimPlan:
    """Infer the dominant flat-letter base and identify every lower outline."""
    source_list = tuple(sources)
    if not source_list:
        raise ValueError("Choose one or more letters before previewing base trimming.")
    if not math.isfinite(maximum_trim) or maximum_trim <= 0:
        raise ValueError("Maximum base trim must be a positive finite value.")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Base-trim tolerance must be positive and finite.")
    if not 0 < minimum_flat_fraction < 1:
        raise ValueError("Flat-base recognition fraction must be between zero and one.")

    measurements: dict[str, tuple[float, bool]] = {}
    flat_candidates: list[tuple[str, float]] = []
    for source in source_list:
        ymin, width, flat_run = _bottom_flat_run(source, tolerance)
        if width <= tolerance:
            raise ValueError(f"Letter {source.id} is too narrow to evaluate for base trimming.")
        has_substantial_flat = flat_run + tolerance >= width * minimum_flat_fraction
        measurements[source.id] = (ymin, has_substantial_flat)
        if has_substantial_flat:
            flat_candidates.append((source.id, ymin))

    manual = baseline_y is not None
    reference_band = max(tolerance * 10.0, maximum_trim * 0.05)
    if baseline_y is None:
        if not flat_candidates:
            raise ValueError(
                "No substantial aligned flat base was found. Enter a reviewed Baseline Y manually."
            )
        clusters: list[list[tuple[str, float]]] = []
        for candidate in sorted(flat_candidates, key=lambda item: item[1]):
            matching = next((
                cluster for cluster in clusters
                if abs(candidate[1] - sum(value for _owner, value in cluster) / len(cluster)) <= reference_band
            ), None)
            if matching is None:
                clusters.append([candidate])
            else:
                matching.append(candidate)
        # The most common aligned flat base is authoritative. A tie favors the
        # higher base so a single intentionally low square-bottom letter does
        # not pull the normal lettering baseline downward.
        references = max(
            clusters,
            key=lambda cluster: (len(cluster), sum(value for _owner, value in cluster) / len(cluster)),
        )
        baseline_y = sum(value for _owner_id, value in references) / len(references)
    else:
        references = [
            candidate for candidate in flat_candidates
            if abs(candidate[1] - baseline_y) <= reference_band
        ]
    if not math.isfinite(baseline_y):
        raise ValueError("Base-trim Baseline Y must be finite.")

    trim_depths: list[tuple[str, float]] = []
    reference_ids = {owner_id for owner_id, _value in references}
    for source in source_list:
        ymin, _has_substantial_flat = measurements[source.id]
        depth = baseline_y - ymin
        if source.id in reference_ids or depth <= tolerance:
            continue
        if depth > maximum_trim + tolerance:
            raise ValueError(
                f"Letter {source.id} would require {depth:g} units of base trim, above the reviewed "
                f"maximum {maximum_trim:g}. Correct its vertical registration or increase the limit deliberately."
            )
        ymax = max(point.y for point in _closed_points(source, tolerance))
        if baseline_y >= ymax - tolerance:
            raise ValueError(f"Baseline Y would remove all usable geometry from letter {source.id}.")
        trim_depths.append((source.id, depth))

    return TabSlotBaseTrimPlan(
        baseline_y=baseline_y,
        reference_owner_ids=tuple(owner_id for owner_id, _value in references),
        trim_owner_ids=tuple(owner_id for owner_id, _depth in trim_depths),
        trim_depths=tuple(trim_depths),
        manual_baseline=manual,
    )


def trim_tab_slot_letter_base(
        source: Polyline, baseline_y: float, *, maximum_trim: float,
        tolerance: float) -> Polyline:
    """Clip one closed letter outline above a reviewed horizontal baseline."""
    points = _closed_points(source, tolerance)
    ymin = min(point.y for point in points)
    depth = baseline_y - ymin
    if depth <= tolerance:
        return deepcopy(source)
    if not math.isfinite(baseline_y) or depth > maximum_trim + tolerance:
        raise ValueError(
            f"Letter {source.id} base trim {depth:g} exceeds the reviewed maximum {maximum_trim:g}."
        )

    clipped: list[Point] = []

    def inside(point: Point) -> bool:
        return point.y >= baseline_y - tolerance

    def intersection(start: Point, end: Point) -> Point:
        dy = end.y - start.y
        if abs(dy) <= tolerance:
            return Point(end.x, baseline_y)
        fraction = (baseline_y - start.y) / dy
        return Point(start.x + (end.x - start.x) * fraction, baseline_y)

    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(intersection(previous, current))
            clipped.append(Point(current.x, max(current.y, baseline_y)))
        elif previous_inside:
            clipped.append(intersection(previous, current))
        previous, previous_inside = current, current_inside

    deduplicated: list[Point] = []
    for point in clipped:
        if not deduplicated or point.distance_to(deduplicated[-1]) > tolerance:
            deduplicated.append(point)
    if len(deduplicated) > 1 and deduplicated[0].distance_to(deduplicated[-1]) <= tolerance:
        deduplicated.pop()
    if len(deduplicated) < 3:
        raise ValueError(f"Base trimming collapsed letter {source.id}; no geometry was changed.")
    area2 = sum(
        point.x * deduplicated[(index + 1) % len(deduplicated)].y
        - deduplicated[(index + 1) % len(deduplicated)].x * point.y
        for index, point in enumerate(deduplicated)
    )
    if abs(area2) <= tolerance * tolerance:
        raise ValueError(f"Base trimming produced a degenerate outline for letter {source.id}.")
    result = deepcopy(source)
    result.points = deduplicated
    result.closed = True
    forced_breaks = [
        index for index, point in enumerate(deduplicated)
        if abs(point.y - baseline_y) <= tolerance
        and (
            abs(deduplicated[(index - 1) % len(deduplicated)].y - baseline_y) > tolerance
            or abs(deduplicated[(index + 1) % len(deduplicated)].y - baseline_y) > tolerance
        )
    ]
    result.metadata.update({
        "tab_slot_base_trimmed": True,
        "tab_slot_base_trim_baseline": baseline_y,
        "tab_slot_base_trim_depth": depth,
        "logical_edge_break_vertices": forced_breaks,
    })
    return result


def apply_tab_slot_base_trim(
        sources: Iterable[Polyline], plan: TabSlotBaseTrimPlan, *,
        maximum_trim: float, tolerance: float) -> dict[str, Polyline]:
    """Return an owner-keyed preview without mutating any source artwork."""
    trim_ids = set(plan.trim_owner_ids)
    return {
        source.id: (
            trim_tab_slot_letter_base(
                source, plan.baseline_y, maximum_trim=maximum_trim, tolerance=tolerance,
            )
            if source.id in trim_ids else deepcopy(source)
        )
        for source in sources
    }


def _rectangle(width: float, height: float, *, center_y: float = 0.0) -> tuple[Point, ...]:
    half_w, half_h = width / 2.0, height / 2.0
    return (
        Point(-half_w, center_y - half_h), Point(half_w, center_y - half_h),
        Point(half_w, center_y + half_h), Point(-half_w, center_y + half_h),
    )


def _arc(center: Point, radius: float, start: float, end: float, samples: int) -> tuple[Point, ...]:
    return tuple(
        Point(
            center.x + radius * math.cos(math.radians(start + (end - start) * index / samples)),
            center.y + radius * math.sin(math.radians(start + (end - start) * index / samples)),
        )
        for index in range(samples + 1)
    )


def directed_tbone_slot_outline(
        width: float, height: float, cutter_diameter: float, *,
        samples_per_arc: int = 8) -> tuple[Point, ...]:
    """Return a through-slot outline with relief directed along the slot width.

    Keeping the lobe extensions on the two slot ends avoids increasing the
    nominal front/back opening envelope.  Four cutter-radius lobes pass through
    every nominal square corner.
    """
    radius = cutter_diameter / 2.0
    if min(width, height, cutter_diameter) <= 0:
        raise ValueError("Slot, thickness, and cutter dimensions must be positive.")
    if height + 1e-9 < 4.0 * radius:
        raise ValueError(
            "The slot is too thin for four separated directed T-bone lobes with this cutter."
        )
    xmin, xmax = -width / 2.0, width / 2.0
    ymin, ymax = -height / 2.0, height / 2.0
    points: list[Point] = [Point(xmin, ymin), Point(xmax, ymin)]
    points.extend(_arc(Point(xmax, ymin + radius), radius, -90, 90, samples_per_arc)[1:])
    points.append(Point(xmax, ymax - 2.0 * radius))
    points.extend(_arc(Point(xmax, ymax - radius), radius, -90, 90, samples_per_arc)[1:])
    points.append(Point(xmin, ymax))
    points.extend(_arc(Point(xmin, ymax - radius), radius, 90, 270, samples_per_arc)[1:])
    points.append(Point(xmin, ymin + 2.0 * radius))
    points.extend(_arc(Point(xmin, ymin + radius), radius, 90, 270, samples_per_arc)[1:])
    return tuple(points)


def relieved_letter_tab_outline(
        tab_width: float, projection: float, cutter_diameter: float, *,
        shoulder_land: float = 0.5, body_height: float = 1.0,
        samples_per_relief: int = 10) -> tuple[Point, ...]:
    """Return a front-view test coupon with cutter-radius tab-root notches."""
    radius = cutter_diameter / 2.0
    if min(tab_width, projection, cutter_diameter, shoulder_land, body_height) <= 0:
        raise ValueError("Tab, projection, cutter, shoulder, and body dimensions must be positive.")
    tab_half = tab_width / 2.0
    body_half = tab_half + shoulder_land
    if shoulder_land <= 2.0 * radius:
        raise ValueError("The shoulder land is too short to retain a flat seating land after root relief.")
    right_center = Point(tab_half + radius, 0.0)
    left_center = Point(-tab_half - radius, 0.0)
    points: list[Point] = [
        Point(-tab_half, -projection), Point(tab_half, -projection), Point(tab_half, 0.0),
    ]
    points.extend(_arc(right_center, radius, 180, 0, samples_per_relief)[1:])
    points.extend((Point(body_half, 0.0), Point(body_half, body_height), Point(-body_half, body_height), Point(-body_half, 0.0)))
    points.extend(_arc(left_center, radius, 180, 0, samples_per_relief)[1:])
    points.append(Point(-tab_half, -projection))
    return tuple(points[:-1])


def preview_tab_slot_fit(spec: TabSlotFitSpec) -> TabSlotFitPreview:
    values = (
        spec.tab_width, spec.letter_thickness, spec.rail_thickness,
        spec.tab_projection, spec.slot_width, spec.slot_height,
        spec.cutter_diameter, spec.shoulder_land,
    )
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Tab-slot sample dimensions must be finite and positive.")
    width_clearance = spec.slot_width - spec.tab_width
    thickness_clearance = spec.slot_height - spec.letter_thickness
    radius = spec.cutter_diameter / 2.0
    blockers: list[str] = []
    warnings: list[str] = []
    if width_clearance < 0:
        blockers.append(
            f"Slot is {abs(width_clearance):g} in narrower than the tab; preserve the source values for review rather than forcing a fit."
        )
    if thickness_clearance < 0:
        blockers.append(
            f"Slot is {abs(thickness_clearance):g} in thinner than the letter stock."
        )
    if not math.isclose(spec.tab_projection, spec.rail_thickness, rel_tol=0.0, abs_tol=1e-9):
        blockers.append(
            f"Tab projection {spec.tab_projection:g} in does not finish flush with the {spec.rail_thickness:g} in rail underside."
        )
    remaining_land = spec.shoulder_land - 2.0 * radius
    if remaining_land <= 0:
        blockers.append("Cutter-driven root relief consumes the entire proposed seating land.")
    warnings.append(
        "Verify remaining rail material and relief visibility at the actual slot location; edge distance is not present in this isolated sample."
    )
    warnings.append(
        "Measured source slot clearance is preserved for prototype review and is not promoted to a shop default."
    )
    nominal_slot = _rectangle(spec.slot_width, spec.slot_height)
    nominal_tab = _rectangle(spec.tab_width, spec.letter_thickness)
    try:
        relieved_slot = directed_tbone_slot_outline(
            spec.slot_width, spec.slot_height, spec.cutter_diameter,
        )
    except ValueError as exc:
        blockers.append(str(exc))
        relieved_slot = nominal_slot
    try:
        relieved_letter = relieved_letter_tab_outline(
            spec.tab_width, spec.tab_projection, spec.cutter_diameter,
            shoulder_land=spec.shoulder_land,
        )
    except ValueError as exc:
        blockers.append(str(exc))
        relieved_letter = (
            Point(-spec.tab_width / 2.0, -spec.tab_projection),
            Point(spec.tab_width / 2.0, -spec.tab_projection),
            Point(spec.tab_width / 2.0, 1.0),
            Point(-spec.tab_width / 2.0, 1.0),
        )
    return TabSlotFitPreview(
        spec=spec, nominal_slot_outline=nominal_slot,
        relieved_slot_outline=relieved_slot, nominal_tab_outline=nominal_tab,
        relieved_letter_outline=relieved_letter,
        width_clearance_total=width_clearance,
        thickness_clearance_total=thickness_clearance,
        root_relief_radius=radius, remaining_seating_land=remaining_land,
        warnings=tuple(warnings), blockers=tuple(dict.fromkeys(blockers)),
    )


def validate_tab_slot_settings(settings: TabSlotRailSettings) -> None:
    values = tuple(float(value) for value in settings.__dict__.values())
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Tab-and-slot settings must be finite.")
    positive = (
        settings.nominal_tab_width, settings.minimum_tab_width,
        settings.letter_thickness, settings.rail_thickness, settings.rail_width,
        settings.tab_projection, settings.cutter_diameter,
        settings.root_relief_radius, settings.short_edge_center_threshold,
        settings.tolerance,
    )
    if any(value <= 0 for value in positive):
        raise ValueError("Tab, stock, cutter, relief, threshold, and tolerance values must be positive.")
    if settings.minimum_tab_width > settings.nominal_tab_width:
        raise ValueError("Minimum local tab width cannot exceed nominal tab width.")
    if min(settings.slot_width_clearance, settings.slot_thickness_clearance, settings.edge_end_clearance) < 0:
        raise ValueError("Slot clearances and edge clearance cannot be negative.")
    if settings.root_relief_radius + settings.tolerance < settings.cutter_diameter / 2.0:
        raise ValueError("Tab-root relief radius cannot be smaller than the selected cutter radius.")
    if not math.isclose(settings.tab_projection, settings.rail_thickness, rel_tol=0.0, abs_tol=settings.tolerance):
        raise ValueError("Flush underside requires tab projection to equal actual rail thickness.")
    slot_height = settings.letter_thickness + settings.slot_thickness_clearance
    if slot_height + settings.tolerance < settings.cutter_diameter * 2.0:
        raise ValueError("Slot thickness is too small for separated directed T-bone lobes with this cutter.")
    if not 0 <= settings.rail_slot_center_y <= settings.rail_width:
        raise ValueError("Rail slot center must remain within the rail width.")


def _segment_projection(point: Point, start: Point, end: Point) -> tuple[float, Point, float]:
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-24:
        return point.distance_to(start), start, 0.0
    fraction = max(
        0.0,
        min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_squared),
    )
    projected = Point(start.x + dx * fraction, start.y + dy * fraction)
    return point.distance_to(projected), projected, fraction


def _preferred_bottom_click(
        source: Polyline, click: Point, settings: TabSlotRailSettings) -> Point:
    """Favor a valid bottom segment when a corner click is geometrically tied.

    DXF contours may start at a serif corner.  The generic nearest-edge resolver
    consequently used to choose the upright or diagonal segment simply because
    it appeared first in the point list.  Tab-and-slot placement only accepts
    downward-facing edges, so a nearby bottom segment is the unambiguous target
    in this workflow.  The small capture allowance resolves a visible corner;
    it is not large enough to relocate a click to another foot.
    """
    points = _closed_points(source, settings.tolerance)
    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    orientation = 1.0 if area2 > 0 else -1.0
    nearest_distance = math.inf
    bottom_candidates: list[tuple[float, float, int, Point, float]] = []
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        length = start.distance_to(end)
        if length <= settings.tolerance:
            continue
        distance, projected, fraction = _segment_projection(click, start, end)
        nearest_distance = min(nearest_distance, distance)
        ux = (end.x - start.x) / length
        outward_y = -orientation * ux
        if outward_y <= -0.5:
            bottom_candidates.append((distance, outward_y, index, projected, fraction))
    if not bottom_candidates:
        return click
    corner_capture = max(
        settings.tolerance * 20.0,
        min(settings.cutter_diameter, settings.minimum_tab_width) * 0.25,
    )
    nearby = [item for item in bottom_candidates if item[0] <= nearest_distance + corner_capture]
    if not nearby:
        return click
    distance, outward_y, edge_index, projected, fraction = min(
        nearby, key=lambda item: (item[1], item[0]),
    )
    # Move an exact shared-vertex projection an imperceptible distance onto the
    # selected segment.  This only breaks the raw-segment tie; it does not move
    # the eventual tab or bypass endpoint-clearance validation.
    start, end = points[edge_index], points[(edge_index + 1) % len(points)]
    length = start.distance_to(end)
    inset_fraction = min(1e-5, max(settings.tolerance * 20.0 / length, 1e-9))
    if fraction <= inset_fraction:
        fraction = inset_fraction
    elif fraction >= 1.0 - inset_fraction:
        fraction = 1.0 - inset_fraction
    selected = Point(
        start.x + (end.x - start.x) * fraction,
        start.y + (end.y - start.y) * fraction,
    )
    logical_edge = next((
        item for item in logical_bent_tab_edges(source, settings.tolerance)
        if edge_index in item.edge_indices
    ), None)
    if logical_edge is None or not logical_edge.straight or logical_edge.closed_loop:
        return selected
    available = (
        logical_edge.length
        - 4.0 * settings.root_relief_radius
        - 2.0 * settings.edge_end_clearance
    )
    width = min(settings.nominal_tab_width, available)
    if width + settings.tolerance < settings.minimum_tab_width:
        return selected
    safe_inset = (
        width / 2.0
        + 2.0 * settings.root_relief_radius
        + settings.edge_end_clearance
    )
    if logical_edge.length + settings.tolerance < 2.0 * safe_inset:
        return selected
    local_distance = 0.0
    for logical_index in logical_edge.edge_indices:
        logical_start = points[logical_index]
        logical_end = points[(logical_index + 1) % len(points)]
        logical_length = logical_start.distance_to(logical_end)
        if logical_index == edge_index:
            local_distance += logical_length * fraction
            break
        local_distance += logical_length
    clamped_distance = max(safe_inset, min(logical_edge.length - safe_inset, local_distance))
    if math.isclose(clamped_distance, local_distance, abs_tol=settings.tolerance * 10.0):
        return selected
    remaining = clamped_distance
    for logical_index in logical_edge.edge_indices:
        logical_start = points[logical_index]
        logical_end = points[(logical_index + 1) % len(points)]
        logical_length = logical_start.distance_to(logical_end)
        if remaining <= logical_length + settings.tolerance:
            local_fraction = max(0.0, min(1.0, remaining / logical_length))
            return Point(
                logical_start.x + (logical_end.x - logical_start.x) * local_fraction,
                logical_start.y + (logical_end.y - logical_start.y) * local_fraction,
            )
        remaining -= logical_length
    return selected


def prepare_tab_slot_straight_support(
        source: Polyline, click: Point, settings: TabSlotRailSettings) -> Polyline:
    """Isolate a clicked bottom straight from a smoothly tangent curve.

    Letter outlines such as B and serif E commonly contain one exact bottom
    line followed by several curve tessellation segments.  At a tangent join
    there is deliberately no sharp geometric corner, so the general logical
    edge recognizer groups the line and curve together.  A routed tab must use
    the actual straight seating land, not that combined curved run.

    This function adds logical break metadata at the ends of the maximal
    collinear raw run.  It never changes, adds, or removes contour points.
    """
    validate_tab_slot_settings(settings)
    points = _closed_points(source, settings.tolerance)
    targeted = _preferred_bottom_click(source, click, settings)
    nearest: tuple[float, int] | None = None
    for index, start in enumerate(points):
        distance, _projected, _fraction = _segment_projection(
            targeted, start, points[(index + 1) % len(points)],
        )
        candidate = (distance, index)
        if nearest is None or candidate < nearest:
            nearest = candidate
    assert nearest is not None
    edge_index = nearest[1]
    start = points[edge_index]
    end = points[(edge_index + 1) % len(points)]
    target_length = start.distance_to(end)
    if target_length <= settings.tolerance:
        return source

    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    ux = (end.x - start.x) / target_length
    uy = (end.y - start.y) / target_length
    outward_y = -(1.0 if area2 > 0 else -1.0) * ux
    if outward_y > -0.5:
        return source

    line_band = max(settings.tolerance * 20.0, target_length * 1e-9)

    def collinear(index: int) -> bool:
        candidate_start = points[index]
        candidate_end = points[(index + 1) % len(points)]
        length = candidate_start.distance_to(candidate_end)
        if length <= settings.tolerance:
            return False
        candidate_ux = (candidate_end.x - candidate_start.x) / length
        candidate_uy = (candidate_end.y - candidate_start.y) / length
        if ux * candidate_ux + uy * candidate_uy <= 0:
            return False
        if abs(ux * candidate_uy - uy * candidate_ux) > 1e-7:
            return False
        return all(
            abs((point.x - start.x) * uy - (point.y - start.y) * ux) <= line_band
            for point in (candidate_start, candidate_end)
        )

    run = [edge_index]
    previous = (edge_index - 1) % len(points)
    while previous not in run and collinear(previous):
        run.insert(0, previous)
        previous = (previous - 1) % len(points)
    following = (edge_index + 1) % len(points)
    while following not in run and collinear(following):
        run.append(following)
        following = (following + 1) % len(points)

    current = next((
        item for item in logical_bent_tab_edges(source, settings.tolerance)
        if edge_index in item.edge_indices
    ), None)
    if current is not None and current.straight and not current.closed_loop:
        return source

    forced_breaks = {
        int(value) for value in source.metadata.get("logical_edge_break_vertices", ())
        if isinstance(value, int) and not isinstance(value, bool)
        and 0 <= int(value) < len(points)
    }
    forced_breaks.update({run[0], (run[-1] + 1) % len(points)})
    prepared = deepcopy(source)
    prepared.metadata.update({
        "logical_edge_break_vertices": sorted(forced_breaks),
        "tab_slot_target_straight_prepared": True,
    })
    prepared_edge = next((
        item for item in logical_bent_tab_edges(prepared, settings.tolerance)
        if edge_index in item.edge_indices
    ), None)
    if prepared_edge is None or not prepared_edge.straight or prepared_edge.closed_loop:
        return source
    prepared_start = points[prepared_edge.edge_indices[0]]
    prepared_end = points[(prepared_edge.edge_indices[-1] + 1) % len(points)]
    prepared.metadata.update({
        "tab_slot_target_straight_length": prepared_edge.length,
        "tab_slot_target_straight_start": [prepared_start.x, prepared_start.y],
        "tab_slot_target_straight_end": [prepared_end.x, prepared_end.y],
        "tab_slot_target_straight_edge_index": edge_index,
    })
    return prepared


def prepare_tab_slot_point_foot(
        source: Polyline, click: Point, settings: TabSlotRailSettings) -> Polyline:
    """Mark a narrow bottom foot for a direct, non-deforming tab.

    Serif V bottoms commonly arrive as a tiny horizontal segment.  A routed tab
    is wider than that segment, but the adjacent V/N geometry already provides
    open space.  The tab can therefore join directly below the original base;
    widening the legs into an artificial seating land only deforms the letter.
    This preparation records the reviewed direct-tab anchor and leaves every
    source contour point unchanged.
    """
    validate_tab_slot_settings(settings)
    points = _closed_points(source, settings.tolerance)
    ymin = min(point.y for point in points)
    xmin = min(point.x for point in points)
    xmax = max(point.x for point in points)
    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    orientation = 1.0 if area2 > 0 else -1.0
    required_length = (
        settings.minimum_tab_width
        + 4.0 * settings.root_relief_radius
        + 2.0 * settings.edge_end_clearance
    )
    baseline_band = max(settings.tolerance * 20.0, (xmax - xmin) * 1e-7)
    candidates: list[tuple[float, object, Point, Point]] = []
    for logical_edge in logical_bent_tab_edges(source, settings.tolerance):
        if not logical_edge.straight or logical_edge.closed_loop:
            continue
        start_index = logical_edge.edge_indices[0]
        end_index = (logical_edge.edge_indices[-1] + 1) % len(points)
        start, end = points[start_index], points[end_index]
        if abs(start.y - end.y) > baseline_band:
            continue
        if max(start.y, end.y) > ymin + baseline_band:
            continue
        length = start.distance_to(end)
        if length <= settings.tolerance:
            continue
        ux = (end.x - start.x) / length
        outward_y = -orientation * ux
        if outward_y > -0.5:
            continue
        edge_distance = min(
            _segment_projection(click, points[index], points[(index + 1) % len(points)])[0]
            for index in logical_edge.edge_indices
        )
        candidates.append((edge_distance, logical_edge, start, end))
    if not candidates:
        return source
    capture = max(settings.cutter_diameter / 2.0, settings.minimum_tab_width * 0.25)
    distance, logical_edge, start, end = min(candidates, key=lambda item: item[0])
    if distance > capture or logical_edge.length + settings.tolerance >= required_length:
        return source
    if settings.minimum_tab_width > (xmax - xmin) + settings.tolerance:
        raise ValueError(
            "The minimum tab would be wider than this letter. No geometry was changed."
        )
    prepared = deepcopy(source)
    remaining = logical_edge.length / 2.0
    anchor_edge_index = logical_edge.edge_indices[-1]
    anchor_fraction = 1.0
    center = end
    for index in logical_edge.edge_indices:
        edge_start = points[index]
        edge_end = points[(index + 1) % len(points)]
        edge_length = edge_start.distance_to(edge_end)
        if remaining <= edge_length + settings.tolerance:
            anchor_edge_index = index
            anchor_fraction = max(0.0, min(1.0, remaining / edge_length))
            center = Point(
                edge_start.x + (edge_end.x - edge_start.x) * anchor_fraction,
                edge_start.y + (edge_end.y - edge_start.y) * anchor_fraction,
            )
            break
        remaining -= edge_length
    adjustments = list(prepared.metadata.get("tab_slot_point_foot_adjustments", ()))
    adjustments = [
        item for item in adjustments
        if int(item.get("anchor_edge_index", -1)) != anchor_edge_index
    ]
    adjustments.append({
        "edge_index": logical_edge.edge_indices[0],
        "edge_indices": list(logical_edge.edge_indices),
        "anchor_edge_index": anchor_edge_index,
        "anchor_fraction": anchor_fraction,
        "center": [center.x, center.y],
        "original_width": logical_edge.length,
        "prepared_width": settings.minimum_tab_width,
        "baseline_y": ymin,
        "original_start": [start.x, start.y],
        "original_end": [end.x, end.y],
        "changed_bounds": [
            center.x - settings.minimum_tab_width / 2.0,
            ymin - settings.tab_projection,
            center.x + settings.minimum_tab_width / 2.0,
            ymin,
        ],
    })
    prepared.metadata.update({
        "tab_slot_point_foot_prepared": True,
        "tab_slot_point_foot_direct": True,
        "tab_slot_point_foot_adjustments": adjustments,
    })
    return prepared


def resolve_tab_slot_placement(
        source: Polyline, click: Point, settings: TabSlotRailSettings, *,
        existing: Iterable[TabSlotPlacement] = (),
        pair_id: str | None = None) -> tuple[TabSlotPlacement, BentTabManualPlacement]:
    """Resolve one targeted click and reduce width locally only when required."""
    validate_tab_slot_settings(settings)
    prior = tuple(existing)
    base = BentTabSettings(
        width=settings.minimum_tab_width,
        flat_projection=settings.tab_projection,
        edge_end_clearance=settings.edge_end_clearance,
        root_relief_radius=settings.root_relief_radius,
        tip_corner_radius=0.0,
        bend_direction="unspecified",
        tolerance=settings.tolerance,
        curve_tolerance=max(settings.tolerance * 10.0, 0.001),
    )
    direct_adjustments = tuple(source.metadata.get("tab_slot_point_foot_adjustments", ()))
    if source.metadata.get("tab_slot_point_foot_direct") and direct_adjustments:
        adjustment = min(
            direct_adjustments,
            key=lambda item: click.distance_to(Point(*item["center"])),
        )
        center = Point(*adjustment["center"])
        capture = max(settings.cutter_diameter / 2.0, settings.minimum_tab_width * 0.25)
        if click.distance_to(center) <= max(capture, float(adjustment["original_width"]) / 2.0):
            anchor = BentTabAnchor(
                int(adjustment["anchor_edge_index"]),
                float(adjustment["anchor_fraction"]),
            )
            logical_edge = next((
                item for item in logical_bent_tab_edges(source, settings.tolerance)
                if anchor.edge_index in item.edge_indices
            ), None)
            if logical_edge is None:
                raise ValueError("The narrow-foot tab anchor is no longer on this letter.")
            width = settings.minimum_tab_width
            if any(
                    center.distance_to(item.assembly_center)
                    < (width + item.tab_width) / 2.0 - settings.tolerance
                    for item in prior):
                raise ValueError("A tab already occupies this narrow bottom foot; no duplicate was added.")
            owner_part_id = str(
                source.metadata.get("mounting_part_id")
                or source.metadata.get("group_id")
                or source.id
            )
            record = TabSlotPlacement(
                pair_id=pair_id or f"tab_slot_pair_{uuid4().hex}",
                owner_entity_id=source.id, owner_part_id=owner_part_id,
                anchor=anchor, assembly_center=center,
                rail_slot_center=Point(center.x, settings.rail_slot_center_y),
                tab_width=width, auto_centered=True, manual_width=False,
            )
            return record, BentTabManualPlacement(
                anchor, center, logical_edge, click.distance_to(center), True, 0.0,
            )
    try:
        placement = manual_bent_tab_placement(
            source, _preferred_bottom_click(source, click, settings), base, existing_anchors=(),
            short_straight_center_threshold=settings.short_edge_center_threshold,
        )
    except ValueError as exc:
        raise ValueError(f"{exc} No tab was relocated.") from exc
    points = list(source.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= settings.tolerance:
        points.pop()
    edge_start = points[placement.anchor.edge_index]
    edge_end = points[(placement.anchor.edge_index + 1) % len(points)]
    edge_length = edge_start.distance_to(edge_end)
    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    ux, uy = (edge_end.x - edge_start.x) / edge_length, (edge_end.y - edge_start.y) / edge_length
    outward_y = -(1.0 if area2 > 0 else -1.0) * ux
    if outward_y > -0.5:
        raise ValueError("Tab-and-Slot Rail placement requires an outward-facing bottom edge; no tab was relocated.")
    available = (
        placement.logical_edge.length
        - 4.0 * settings.root_relief_radius
        - 2.0 * settings.edge_end_clearance
    )
    width = min(settings.nominal_tab_width, available)
    if width + settings.tolerance < settings.minimum_tab_width:
        raise ValueError(
            f"Selected edge has {max(0.0, available):g} usable units after cutter relief and seating lands; "
            f"minimum tab width is {settings.minimum_tab_width:g}. No tab was relocated."
        )
    width = max(settings.minimum_tab_width, width)
    # ``available`` already reserves the requested seating land explicitly.
    # Passing that preference through a second time would double-count it in
    # the bent-tab footprint validator on a deliberately narrowed short foot.
    chosen = replace(base, width=width, edge_end_clearance=0.0)
    placement = manual_bent_tab_placement(
        source, _preferred_bottom_click(source, click, settings), chosen, existing_anchors=(),
        short_straight_center_threshold=settings.short_edge_center_threshold,
    )
    owner_part_id = str(
        source.metadata.get("mounting_part_id")
        or source.metadata.get("group_id")
        or source.id
    )
    record = TabSlotPlacement(
        pair_id=pair_id or f"tab_slot_pair_{uuid4().hex}",
        owner_entity_id=source.id, owner_part_id=owner_part_id,
        anchor=placement.anchor, assembly_center=placement.point,
        rail_slot_center=Point(placement.point.x, settings.rail_slot_center_y),
        tab_width=width, auto_centered=placement.auto_centered,
        manual_width=False,
    )
    candidate_records = (*prior, record)
    if source.metadata.get("tab_slot_point_foot_direct"):
        generate_tab_slot_part(source, candidate_records, settings)
    else:
        generate_bent_tab_back(
            source, (item.anchor for item in candidate_records), chosen,
            widths=(item.tab_width for item in candidate_records),
        )
    return record, placement


def _logical_edge_point(
        source: Polyline, logical_edge, distance: float,
        tolerance: float) -> Point:
    points = _closed_points(source, tolerance)
    remaining = distance
    for position, edge_index in enumerate(logical_edge.edge_indices):
        start = points[edge_index]
        end = points[(edge_index + 1) % len(points)]
        length = start.distance_to(end)
        if remaining <= length + tolerance or position == len(logical_edge.edge_indices) - 1:
            fraction = max(0.0, min(1.0, remaining / length))
            return Point(
                start.x + (end.x - start.x) * fraction,
                start.y + (end.y - start.y) * fraction,
            )
        remaining -= length
    raise AssertionError("Logical support distance could not be resolved.")


def _logical_edge_click_distance(
        source: Polyline, logical_edge, click: Point,
        tolerance: float) -> float:
    points = _closed_points(source, tolerance)
    best: tuple[float, float] | None = None
    walked = 0.0
    for edge_index in logical_edge.edge_indices:
        start = points[edge_index]
        end = points[(edge_index + 1) % len(points)]
        length = start.distance_to(end)
        distance, _projected, fraction = _segment_projection(click, start, end)
        candidate = (distance, walked + length * fraction)
        if best is None or candidate[0] < best[0]:
            best = candidate
        walked += length
    assert best is not None
    return best[1]


def tab_slot_clicked_edge_length(
        source: Polyline, click: Point, settings: TabSlotRailSettings) -> float:
    """Measure the exact bottom logical flat targeted by a placement click."""
    validate_tab_slot_settings(settings)
    points = _closed_points(source, settings.tolerance)
    targeted = _preferred_bottom_click(source, click, settings)
    nearest: tuple[float, int] | None = None
    for index, start in enumerate(points):
        distance, _projected, _fraction = _segment_projection(
            targeted, start, points[(index + 1) % len(points)],
        )
        candidate = (distance, index)
        if nearest is None or candidate < nearest:
            nearest = candidate
    assert nearest is not None
    edge_index = nearest[1]
    logical_edge = next((
        edge for edge in logical_bent_tab_edges(source, settings.tolerance)
        if edge_index in edge.edge_indices
    ), None)
    if logical_edge is None or not logical_edge.straight or logical_edge.closed_loop:
        raise ValueError("Automatic tab count requires one targeted straight logical edge.")
    start = points[edge_index]
    end = points[(edge_index + 1) % len(points)]
    length = start.distance_to(end)
    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    ux = (end.x - start.x) / length
    outward_y = -(1.0 if area2 > 0 else -1.0) * ux
    if outward_y > -0.5:
        raise ValueError("Tab-and-Slot Rail placement requires an outward-facing bottom edge.")
    return logical_edge.length


def resolve_tab_slot_pair_placements(
        source: Polyline, click: Point, settings: TabSlotRailSettings, *,
        existing: Iterable[TabSlotPlacement] = (),
        requested_spacing: float | None = None,
        center_on_support: bool = True,
        placement_set_id: str | None = None,
) -> TabSlotPairProposal:
    """Resolve one explicit two-tab action without a one-tab fallback.

    The click chooses one logical bottom flat and both tabs remain on that same
    flat. Direct narrow-foot fallback and whole-letter foot discovery are intentionally
    not part of this function: if both complete footprints do not already fit,
    the pair returns a clear no-fit error.
    """
    validate_tab_slot_settings(settings)
    if requested_spacing is not None and (
            not math.isfinite(requested_spacing) or requested_spacing <= 0):
        raise ValueError("Pair center-to-center spacing must be positive, or blank for automatic spacing.")
    group_id = placement_set_id or f"tab_slot_set_{uuid4().hex}"
    pair_ids = (f"{group_id}:1", f"{group_id}:2")
    prior = tuple(existing)
    records: list[TabSlotPlacement] = []
    probe_settings = replace(
        settings, short_edge_center_threshold=max(settings.tolerance, 1e-12),
    )
    try:
        _probe_record, probe = resolve_tab_slot_placement(
            source, click, probe_settings,
        )
    except ValueError as exc:
        raise ValueError(f"The targeted logical flat cannot accept a tab: {exc}") from exc
    support = probe.logical_edge
    if not support.straight or support.closed_loop:
        raise ValueError("Two-tab placement requires one targeted straight logical edge.")
    available = (
        support.length
        - 4.0 * settings.root_relief_radius
        - 2.0 * settings.edge_end_clearance
    )
    width = min(settings.nominal_tab_width, available)
    if width + settings.tolerance < settings.minimum_tab_width:
        raise ValueError(
            "The targeted flat cannot hold even one minimum tab plus cutter relief and end land; no tabs were placed."
        )
    width = max(settings.minimum_tab_width, width)
    safe_inset = (
        width / 2.0
        + 2.0 * settings.root_relief_radius
        + settings.edge_end_clearance
    )
    usable_center_span = support.length - 2.0 * safe_inset
    minimum_spacing = width + 4.0 * settings.root_relief_radius
    if usable_center_span <= minimum_spacing + settings.tolerance:
        raise ValueError(
            f"Two complete tabs do not fit the targeted {support.length:g}-unit flat: "
            f"their centers need more than {minimum_spacing:g} units while the safe span is "
            f"{max(0.0, usable_center_span):g}. No tabs were placed."
        )
    # Prefer the familiar quarter/three-quarter locations on the complete
    # logical flat, then keep them inside the validated endpoint envelope. A
    # narrow but still viable long flat therefore uses the available span
    # instead of producing an avoidable overlap at half that span.
    default_spacing = min(
        usable_center_span,
        max(support.length / 2.0, minimum_spacing + settings.tolerance * 10.0),
    )
    spacing = requested_spacing if requested_spacing is not None else default_spacing
    if spacing <= minimum_spacing + settings.tolerance:
        raise ValueError(
            f"Requested pair spacing {spacing:g} makes the two tab/root footprints touch or overlap; "
            f"use more than {minimum_spacing:g}. No tabs were placed."
        )
    center = support.length / 2.0 if center_on_support else _logical_edge_click_distance(
        source, support, click, settings.tolerance,
    )
    distances = (center - spacing / 2.0, center + spacing / 2.0)
    if (
            distances[0] < safe_inset - settings.tolerance
            or distances[1] > support.length - safe_inset + settings.tolerance
    ):
        maximum = 2.0 * min(center - safe_inset, support.length - safe_inset - center)
        raise ValueError(
            f"Requested pair spacing {spacing:g} does not fit the targeted {support.length:g}-unit flat; "
            f"maximum is {max(0.0, maximum):g}. No tabs were placed."
        )
    for index, distance in enumerate(distances):
        target = _logical_edge_point(source, support, distance, settings.tolerance)
        try:
            record, _manual = resolve_tab_slot_placement(
                source, target, probe_settings,
                existing=(*prior, *records), pair_id=pair_ids[index],
            )
        except ValueError as exc:
            raise ValueError(
                f"Two complete tabs do not fit the targeted {support.length:g}-unit flat at "
                f"{spacing:g} center spacing: {exc}"
            ) from exc
        records.append(replace(
            record, placement_set_id=group_id, placement_set_index=index,
        ))
    arrangement = "continuous_support"

    records.sort(key=lambda item: (item.assembly_center.x, item.assembly_center.y, item.pair_id))
    records = [
        replace(item, placement_set_index=index) for index, item in enumerate(records)
    ]
    actual_spacing = records[0].assembly_center.distance_to(records[1].assembly_center)
    requested_value = requested_spacing
    adjusted = bool(
        requested_value is not None
        and not math.isclose(actual_spacing, requested_value, rel_tol=0.0, abs_tol=settings.tolerance * 10.0)
    )
    return TabSlotPairProposal(
        placement_set_id=group_id,
        placements=(records[0], records[1]),
        arrangement=arrangement,
        requested_spacing=requested_value,
        actual_spacing=actual_spacing,
        spacing_adjusted=adjusted,
    )


def _direct_point_foot_bent_result(
        source: Polyline, records: tuple[TabSlotPlacement, ...],
        settings: TabSlotRailSettings, inner_cutouts: tuple[Polyline, ...],
) -> BentTabResult:
    """Add narrow-foot tabs below the source without widening its legs."""
    adjustments = {
        int(item["anchor_edge_index"]): item
        for item in source.metadata.get("tab_slot_point_foot_adjustments", ())
    }
    direct_records = tuple(
        item for item in records if item.anchor.edge_index in adjustments
    )
    normal_records = tuple(
        item for item in records if item.anchor.edge_index not in adjustments
    )
    bent_settings = BentTabSettings(
        width=settings.nominal_tab_width,
        flat_projection=settings.tab_projection,
        edge_end_clearance=0.0,
        root_relief_radius=settings.root_relief_radius,
        tip_corner_radius=0.0,
        bend_direction="unspecified", tolerance=settings.tolerance,
        curve_tolerance=max(settings.tolerance * 10.0, 0.001),
    )
    if normal_records:
        base = generate_bent_tab_back(
            source, (item.anchor for item in normal_records), bent_settings,
            inner_cutouts=inner_cutouts,
            widths=(item.tab_width for item in normal_records),
        )
        base_outline = base.modified_cut_outline
        normal_guides = iter(base.bend_guides)
    else:
        base_outline = deepcopy(source)
        normal_guides = iter(())
        base = BentTabResult(
            deepcopy(source), (), deepcopy(source), (), (), source.id,
            str(source.metadata.get("group_id") or source.metadata.get("mounting_part_id") or source.id),
            (), tuple(deepcopy(inner_cutouts)),
        )

    scale = 1_000_000.0

    def path(points: Iterable[Point]) -> list[tuple[int, int]]:
        result: list[tuple[int, int]] = []
        for point in points:
            value = (round(point.x * scale), round(point.y * scale))
            if not result or value != result[-1]:
                result.append(value)
        if len(result) > 1 and result[0] == result[-1]:
            result.pop()
        return result

    subject_paths = [path(_closed_points(base_outline, settings.tolerance))]
    direct_guides: dict[str, Line] = {}
    points = _closed_points(source, settings.tolerance)
    area2 = sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )
    orientation = 1.0 if area2 > 0 else -1.0
    # The direct tab and the reviewed narrow foot share their root boundary
    # exactly.  Extending the tab a few clipper units into the letter used to
    # create a microscopic doubled-back loop when the two paths had opposite
    # winding.  That loop looked like an unfused seam in Design and could be
    # interpreted as a real extra segment by downstream geometry consumers.
    # Keep the shared boundary exact and explicitly orient every added tab to
    # match the production outline before the non-zero union.
    overlap = 0.0
    for record in direct_records:
        edge_start = points[record.anchor.edge_index]
        edge_end = points[(record.anchor.edge_index + 1) % len(points)]
        edge_length = edge_start.distance_to(edge_end)
        ux = (edge_end.x - edge_start.x) / edge_length
        uy = (edge_end.y - edge_start.y) / edge_length
        nx, ny = orientation * uy, -orientation * ux
        center = record.assembly_center
        root_start = Point(
            center.x - ux * record.tab_width / 2.0,
            center.y - uy * record.tab_width / 2.0,
        )
        root_end = Point(
            center.x + ux * record.tab_width / 2.0,
            center.y + uy * record.tab_width / 2.0,
        )
        inside_start = Point(root_start.x - nx * overlap, root_start.y - ny * overlap)
        inside_end = Point(root_end.x - nx * overlap, root_end.y - ny * overlap)
        tip_start = Point(
            root_start.x + nx * settings.tab_projection,
            root_start.y + ny * settings.tab_projection,
        )
        tip_end = Point(
            root_end.x + nx * settings.tab_projection,
            root_end.y + ny * settings.tab_projection,
        )
        tab_path = path((inside_start, inside_end, tip_end, tip_start))
        if pyclipper.Orientation(tab_path) != pyclipper.Orientation(subject_paths[0]):
            tab_path.reverse()
        subject_paths.append(tab_path)
        direct_guides[record.pair_id] = Line(
            id=f"{record.pair_id}:direct_point_foot_seat",
            layer=LAYER_TAB_SLOT_GUIDES, start=root_start, end=root_end,
            metadata={
                "role": "reference_only", "machinable": False,
                "tab_slot_point_foot_direct": True,
                # Useful while reviewing the proposed seat, but redundant
                # after the tab is fused into the finished letter outline.
                # Persisting it made the applied V/N joint look detached.
                "tab_slot_preview_only": True,
                "source_edge_index": record.anchor.edge_index,
                "tab_width": record.tab_width,
            },
        )

    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(subject_paths, pyclipper.PT_SUBJECT, True)
    union = clipper.Execute(
        pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO,
    )
    if len(union) != 1:
        raise ValueError(
            "The direct narrow-foot tab did not form one connected letter outline; no tab was placed."
        )
    union_points = [Point(x / scale, y / scale) for x, y in union[0]]
    union_area2 = sum(
        point.x * union_points[(index + 1) % len(union_points)].y
        - union_points[(index + 1) % len(union_points)].x * point.y
        for index, point in enumerate(union_points)
    )
    if union_area2 * area2 < 0:
        union_points.reverse()
    cut = Polyline(
        id=f"{source.id}:bent_tab:cut", layer=source.layer,
        points=union_points, closed=True,
        metadata={
            **deepcopy(base_outline.metadata),
            "bent_tab_generated": True,
            "tab_slot_point_foot_direct": True,
            "owner_entity_id": source.id,
            "group_id": base.group_id,
            "role": "cut_outline",
        },
    )
    ordered_guides: list[Line] = []
    for record in records:
        if record.pair_id in direct_guides:
            ordered_guides.append(direct_guides[record.pair_id])
        else:
            ordered_guides.append(next(normal_guides))
    return BentTabResult(
        deepcopy(source), base.source_references, cut, tuple(ordered_guides), (),
        source.id, base.group_id,
        tuple(dict.fromkeys((*base.warnings, "Narrow-foot tab joins below the original base; the letter legs remain unchanged."))),
        tuple(deepcopy(inner_cutouts)),
    )


def generate_tab_slot_part(
        source: Polyline, placements: Iterable[TabSlotPlacement],
        settings: TabSlotRailSettings, *,
        inner_cutouts: Iterable[Polyline] = ()) -> TabSlotPartResult:
    validate_tab_slot_settings(settings)
    records = tuple(placements)
    if not records:
        raise ValueError("Add at least one tab-and-slot pair to this letter.")
    if any(item.owner_entity_id != source.id for item in records):
        raise ValueError("Every tab-and-slot placement must belong to the selected source letter.")
    bent_settings = BentTabSettings(
        width=settings.nominal_tab_width,
        flat_projection=settings.tab_projection,
        # Per-placement widths were resolved after reserving edge_end_clearance;
        # do not charge that seating land twice during outline construction.
        edge_end_clearance=0.0,
        root_relief_radius=settings.root_relief_radius,
        tip_corner_radius=0.0,
        bend_direction="unspecified", tolerance=settings.tolerance,
        curve_tolerance=max(settings.tolerance * 10.0, 0.001),
    )
    inner_cutouts = tuple(inner_cutouts)
    if source.metadata.get("tab_slot_point_foot_direct"):
        bent = _direct_point_foot_bent_result(
            source, records, settings, inner_cutouts,
        )
    else:
        bent = generate_bent_tab_back(
            source, (item.anchor for item in records), bent_settings,
            inner_cutouts=inner_cutouts, widths=(item.tab_width for item in records),
        )
    bent.modified_cut_outline.metadata.update({
        "tab_slot_generated": True,
        "mounting_workflow": "tab_slot_rail",
        "tab_slot_pair_ids": [item.pair_id for item in records],
        "tab_slot_placement_set_ids": list(dict.fromkeys(
            item.placement_set_id for item in records if item.placement_set_id
        )),
        "mounting_part_id": records[0].owner_part_id,
        "machinable": True,
        "reference_only": False,
    })
    slots: list[Polyline] = []
    references: list[Polyline] = []
    guides: list[Line] = []
    warnings: list[str] = []
    for index, item in enumerate(records):
        slot_width = item.tab_width + settings.slot_width_clearance
        slot_height = settings.letter_thickness + settings.slot_thickness_clearance
        preview = preview_tab_slot_fit(TabSlotFitSpec(
            sample_id=item.pair_id, label=f"Tab/slot {index + 1}",
            tab_width=item.tab_width, letter_thickness=settings.letter_thickness,
            rail_thickness=settings.rail_thickness,
            tab_projection=settings.tab_projection,
            slot_width=slot_width, slot_height=slot_height,
            cutter_diameter=settings.cutter_diameter,
        ))
        if preview.blockers:
            raise ValueError(" ".join(preview.blockers))
        warnings.extend(preview.warnings)
        common = {
            "tab_slot_pair_id": item.pair_id,
            "tab_slot_placement_set_id": item.placement_set_id,
            "tab_slot_placement_set_index": item.placement_set_index,
            "owner_entity_id": item.owner_entity_id,
            "mounting_part_id": item.owner_part_id,
            "assembly_center": [item.assembly_center.x, item.assembly_center.y],
            "tab_width": item.tab_width,
            "slot_width": slot_width,
            "slot_height": slot_height,
            "cutter_diameter": settings.cutter_diameter,
            "mounting_generated": True,
            "mounting_workflow": "tab_slot_rail",
        }
        points = [
            Point(point.x + item.rail_slot_center.x, point.y + item.rail_slot_center.y)
            for point in preview.relieved_slot_outline
        ]
        slots.append(Polyline(
            id=f"{item.pair_id}:rail_slot", layer=LAYER_TAB_SLOT_RAIL,
            points=points, closed=True,
            metadata={**common, "tab_slot_role": "rail_through_slot", "machinable": True, "reference_only": False},
        ))
        nominal = [
            Point(point.x + item.rail_slot_center.x, point.y + item.rail_slot_center.y)
            for point in preview.nominal_slot_outline
        ]
        references.append(Polyline(
            id=f"{item.pair_id}:slot_reference", layer=LAYER_TAB_SLOT_SOURCE,
            points=nominal, closed=True,
            metadata={**common, "tab_slot_role": "nominal_slot_reference", "machinable": False, "reference_only": True},
        ))
        source_guide = bent.bend_guides[index]
        guides.append(Line(
            id=f"{item.pair_id}:seat", layer=LAYER_TAB_SLOT_GUIDES,
            start=deepcopy(source_guide.start), end=deepcopy(source_guide.end),
            metadata={
                **common,
                "tab_slot_role": "seating_baseline_reference",
                "machinable": False,
                "reference_only": True,
                **(
                    {
                        "tab_slot_point_foot_direct": True,
                        "tab_slot_preview_only": True,
                    }
                    if source_guide.metadata.get("tab_slot_preview_only") else {}
                ),
            },
        ))
    return TabSlotPartResult(
        bent_result=bent, placements=records,
        rail_slots=tuple(slots), slot_references=tuple(references),
        seating_guides=tuple(guides), warnings=tuple(dict.fromkeys(warnings)),
    )


def _shift(points: tuple[Point, ...], dx: float, dy: float) -> list[Point]:
    return [Point(point.x + dx, point.y + dy) for point in points]


def tab_slot_fit_entities(
        preview: TabSlotFitPreview, *, origin: Point = Point(0.0, 0.0)) -> list[Entity]:
    """Build isolated reference and production-candidate entities for review."""
    spec = preview.spec
    digest = hashlib.sha1(spec.sample_id.encode("utf-8")).hexdigest()[:16]
    slot_origin = Point(origin.x, origin.y)
    letter_origin = Point(origin.x, origin.y + 2.0)
    common = {
        "tab_slot_sample_id": spec.sample_id,
        "tab_slot_label": spec.label,
        "prototype_only": True,
    }
    entities: list[Entity] = [
        Polyline(
            id=f"tabslot_{digest}_source_slot", layer=LAYER_TAB_SLOT_SOURCE,
            points=_shift(preview.nominal_slot_outline, slot_origin.x, slot_origin.y), closed=True,
            metadata={**common, "tab_slot_role": "source_slot_reference", "machinable": False, "reference_only": True},
        ),
        Polyline(
            id=f"tabslot_{digest}_rail_relief", layer=LAYER_TAB_SLOT_RAIL,
            points=_shift(preview.relieved_slot_outline, slot_origin.x, slot_origin.y), closed=True,
            metadata={**common, "tab_slot_role": "rail_through_slot_candidate", "machinable": True, "reference_only": False},
        ),
        Polyline(
            id=f"tabslot_{digest}_letter", layer=LAYER_TAB_SLOT_LETTER,
            points=_shift(preview.relieved_letter_outline, letter_origin.x, letter_origin.y), closed=True,
            metadata={**common, "tab_slot_role": "letter_root_relief_candidate", "machinable": True, "reference_only": False},
        ),
        Polyline(
            id=f"tabslot_{digest}_tab_reference", layer=LAYER_TAB_SLOT_SOURCE,
            points=_shift(preview.nominal_tab_outline, letter_origin.x, letter_origin.y - spec.letter_thickness / 2.0), closed=True,
            metadata={**common, "tab_slot_role": "nominal_tab_reference", "machinable": False, "reference_only": True},
        ),
        Line(
            id=f"tabslot_{digest}_seat", layer=LAYER_TAB_SLOT_GUIDES,
            start=Point(letter_origin.x - spec.tab_width / 2.0 - spec.shoulder_land, letter_origin.y),
            end=Point(letter_origin.x + spec.tab_width / 2.0 + spec.shoulder_land, letter_origin.y),
            metadata={**common, "tab_slot_role": "seating_baseline_reference", "machinable": False, "reference_only": True},
        ),
        TextEntity(
            id=f"tabslot_{digest}_label", layer=LAYER_TAB_SLOT_GUIDES,
            insertion=Point(origin.x, origin.y + 3.25), text=spec.label, height=0.18,
            alignment="middle_center",
            metadata={**common, "tab_slot_role": "sample_label", "machinable": False, "reference_only": True},
        ),
    ]
    return entities
