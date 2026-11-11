"""Semantic comparison of EnRoute-style controller programs.

Formatting, repeated modal coordinates, and split collinear moves are not
treated as machining differences.  This module is intentionally read-only: it
never posts or modifies a machine file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Iterable


WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
MOTION_CODES = {0: "rapid", 1: "line", 2: "arc_cw", 3: "arc_ccw"}


@dataclass(frozen=True)
class ControllerMotion:
    kind: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    feed: float | None
    center: tuple[float, float] | None
    line_number: int


@dataclass
class ControllerSegment:
    index: int
    motions: list[ControllerMotion] = field(default_factory=list)

    @property
    def maximum_depth(self) -> float:
        return max((motion.end[2] for motion in self.motions), default=0.0)

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        points = [(motion.end[0], motion.end[1]) for motion in self.motions]
        if not points:
            return None
        xs, ys = zip(*points)
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def cutting_feeds(self) -> tuple[float, ...]:
        return tuple(sorted({round(motion.feed, 6) for motion in self.motions if motion.feed is not None and motion.kind != "rapid"}))

    @property
    def arc_count(self) -> int:
        return sum(motion.kind in {"arc_cw", "arc_ccw"} for motion in self.motions)


@dataclass
class ControllerProgram:
    source: str
    units: str = "in"
    absolute: bool = True
    rpm_values: list[int] = field(default_factory=list)
    stations: list[int] = field(default_factory=list)
    tool_names: dict[int, str] = field(default_factory=dict)
    segments: list[ControllerSegment] = field(default_factory=list)
    motions: list[ControllerMotion] = field(default_factory=list)

    @property
    def maximum_depth(self) -> float:
        return max((motion.end[2] for motion in self.motions), default=0.0)

    @property
    def clearance(self) -> float:
        return abs(min((motion.end[2] for motion in self.motions), default=0.0))

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        if not self.motions:
            return None
        xs = [motion.end[0] for motion in self.motions]
        ys = [motion.end[1] for motion in self.motions]
        return min(xs), min(ys), max(xs), max(ys)

    def summary_rows(self) -> tuple[tuple[str, str], ...]:
        counts = {kind: sum(motion.kind == kind for motion in self.motions) for kind in MOTION_CODES.values()}
        bounds = self.bounds
        return (
            ("Source", self.source), ("Units", self.units),
            ("Tool stations", ", ".join(f"T{value}" for value in self.stations) or "None"),
            ("RPM", ", ".join(str(value) for value in self.rpm_values) or "Spindle command absent"),
            ("Cut blocks", str(len(self.segments))),
            ("Motions", str(len(self.motions))),
            ("Linear / CW arc / CCW arc", f"{counts['line']} / {counts['arc_cw']} / {counts['arc_ccw']}"),
            ("Maximum cut depth", f"{self.maximum_depth:g} {self.units}"),
            ("Clearance magnitude", f"{self.clearance:g} {self.units}"),
            ("XY bounds", "None" if bounds is None else f"X {bounds[0]:g}…{bounds[2]:g}  Y {bounds[1]:g}…{bounds[3]:g}"),
        )


@dataclass(frozen=True)
class ComparisonIssue:
    severity: str
    check: str
    message: str
    reference: str = ""
    candidate: str = ""


@dataclass(frozen=True)
class ControllerComparison:
    issues: tuple[ComparisonIssue, ...]
    matched_segments: int
    reference_segments: int
    candidate_segments: int

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def parse_controller_file(path: str | Path) -> ControllerProgram:
    path = Path(path)
    return parse_controller_text(path.read_text(encoding="ascii", errors="replace"), str(path))


def parse_controller_text(text: str, source: str = "<memory>") -> ControllerProgram:
    program = ControllerProgram(source=source)
    x = y = z = 0.0
    feed: float | None = None
    active_segment: ControllerSegment | None = None
    pending_rapid: ControllerMotion | None = None
    for line_number, raw in enumerate(text.splitlines(), start=1):
        code_text, _, comment = raw.partition("//")
        words: dict[str, list[float]] = {}
        for letter, number in WORD.findall(code_text.upper()):
            words.setdefault(letter, []).append(float(number))
        if not words:
            continue
        g_values = [int(round(value)) for value in words.get("G", [])]
        m_values = [int(round(value)) for value in words.get("M", [])]
        if 70 in g_values:
            program.units = "in"
        if 71 in g_values:
            program.units = "mm"
        if 90 in g_values:
            program.absolute = True
        if 91 in g_values:
            program.absolute = False
        for rpm in words.get("S", []):
            value = int(round(rpm))
            if value not in program.rpm_values:
                program.rpm_values.append(value)
        for station_value in words.get("T", []):
            station = int(round(station_value))
            if station not in program.stations:
                program.stations.append(station)
            if comment.strip():
                program.tool_names[station] = comment.strip()
        if 12 in m_values:
            active_segment = ControllerSegment(len(program.segments) + 1)
            program.segments.append(active_segment)
            if pending_rapid is not None:
                active_segment.motions.append(pending_rapid)
        if 22 in m_values:
            active_segment = None
        motion_code = next((value for value in g_values if value in MOTION_CODES), None)
        if motion_code is None or not any(axis in words for axis in "XYZ"):
            continue
        old = (x, y, z)
        values = {axis: entries[-1] for axis, entries in words.items() if axis in "XYZFIJ"}
        if program.absolute:
            x, y, z = values.get("X", x), values.get("Y", y), values.get("Z", z)
        else:
            x, y, z = x + values.get("X", 0.0), y + values.get("Y", 0.0), z + values.get("Z", 0.0)
        feed = values.get("F", feed)
        center = None
        if motion_code in {2, 3}:
            center = (values.get("I", x), values.get("J", y))
        motion = ControllerMotion(MOTION_CODES[motion_code], old, (x, y, z), feed, center, line_number)
        program.motions.append(motion)
        if active_segment is not None:
            active_segment.motions.append(motion)
        pending_rapid = motion if motion.kind == "rapid" else pending_rapid
    if not program.segments and program.motions:
        program.segments.append(ControllerSegment(1, list(program.motions)))
    return program


def compare_controller_programs(
    reference: ControllerProgram,
    candidate: ControllerProgram,
    *,
    xy_tolerance: float = 0.002,
    z_tolerance: float = 0.001,
    feed_tolerance: float = 0.02,
    spindle_disabled_stations: Iterable[int] = (8,),
) -> ControllerComparison:
    issues: list[ComparisonIssue] = []
    disabled = set(spindle_disabled_stations)
    if reference.units != candidate.units:
        issues.append(ComparisonIssue("error", "Units", "Controller unit modes differ.", reference.units, candidate.units))
    if reference.stations != candidate.stations:
        issues.append(ComparisonIssue("error", "Tool stations", "Tool station order differs.", str(reference.stations), str(candidate.stations)))
    if not set(reference.stations).issubset(disabled) and reference.rpm_values != candidate.rpm_values:
        issues.append(ComparisonIssue("error", "RPM", "Spindle RPM sequence differs.", str(reference.rpm_values), str(candidate.rpm_values)))
    if len(reference.segments) != len(candidate.segments):
        issues.append(ComparisonIssue("error", "Cut blocks", "M12/M22 cut-block count differs.", str(len(reference.segments)), str(len(candidate.segments))))
    pairs, unmatched_reference, unmatched_candidate = _match_segments(reference.segments, candidate.segments)
    for reference_segment, candidate_segment in pairs:
        label = f"Block {reference_segment.index} ↔ {candidate_segment.index}"
        if abs(reference_segment.maximum_depth - candidate_segment.maximum_depth) > z_tolerance:
            issues.append(ComparisonIssue("error", "Depth", f"{label} maximum depth differs.", f"{reference_segment.maximum_depth:g}", f"{candidate_segment.maximum_depth:g}"))
        distance = _segment_path_distance(reference_segment, candidate_segment)
        if distance > xy_tolerance:
            issues.append(ComparisonIssue("error", "XY path", f"{label} differs by as much as {distance:.5g} {reference.units}.", f"≤ {xy_tolerance:g}", f"{distance:.5g}"))
        if reference_segment.arc_count != candidate_segment.arc_count:
            issues.append(ComparisonIssue("warning", "Arc structure", f"{label} uses a different number of controller arcs; geometry may still match.", str(reference_segment.arc_count), str(candidate_segment.arc_count)))
        reference_feeds = reference_segment.cutting_feeds
        candidate_feeds = candidate_segment.cutting_feeds
        if not _number_sets_close(reference_feeds, candidate_feeds, feed_tolerance):
            issues.append(ComparisonIssue("warning", "Feeds", f"{label} feed set differs.", str(reference_feeds), str(candidate_feeds)))
    for segment in unmatched_reference:
        issues.append(ComparisonIssue("error", "Missing block", f"Reference block {segment.index} has no candidate match."))
    for segment in unmatched_candidate:
        issues.append(ComparisonIssue("error", "Extra block", f"Candidate block {segment.index} has no reference match."))
    if not issues:
        issues.append(ComparisonIssue("pass", "Semantic comparison", "Tool, depth, feed, cut-block, and XY-path checks are within tolerance."))
    return ControllerComparison(tuple(issues), len(pairs), len(reference.segments), len(candidate.segments))


def _match_segments(reference: list[ControllerSegment], candidate: list[ControllerSegment]):
    remaining = list(candidate)
    pairs = []
    unmatched_reference = []
    for segment in reference:
        bounds = segment.bounds
        if bounds is None or not remaining:
            unmatched_reference.append(segment)
            continue
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        match = min(
            remaining,
            key=lambda item: _segment_match_cost(center, bounds, segment.maximum_depth, item),
        )
        pairs.append((segment, match))
        remaining.remove(match)
    return pairs, unmatched_reference, remaining


def _segment_match_cost(center, bounds, maximum_depth: float, segment: ControllerSegment) -> float:
    other = segment.bounds
    if other is None:
        return float("inf")
    other_center = ((other[0] + other[2]) / 2, (other[1] + other[3]) / 2)
    # Repeated depth passes often share identical XY bounds. Including depth in
    # the assignment cost prevents a reordered multi-pass program from pairing
    # the roughing pass with the finishing pass and reporting false differences.
    return (
        math.dist(center, other_center)
        + 0.25 * sum(abs(a - b) for a, b in zip(bounds, other))
        + 5.0 * abs(maximum_depth - segment.maximum_depth)
    )


def _segment_path_distance(first: ControllerSegment, second: ControllerSegment) -> float:
    first_paths = _flatten_segment(first)
    second_paths = _flatten_segment(second)
    if not first_paths or not second_paths:
        return float("inf") if first_paths != second_paths else 0.0
    return max(_directed_path_distance(first_paths, second_paths), _directed_path_distance(second_paths, first_paths))


def _directed_path_distance(points, target) -> float:
    segments = list(zip(target, target[1:])) or [(target[0], target[0])]
    sample_step = max(1, len(points) // 1200)
    return max(min(_point_segment_distance(point, start, end) for start, end in segments) for point in points[::sample_step])


def _flatten_segment(segment: ControllerSegment) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for motion in segment.motions:
        if motion.kind == "rapid":
            continue
        start, end = motion.start[:2], motion.end[:2]
        if not points or math.dist(points[-1], start) > 1e-9:
            points.append(start)
        if motion.kind in {"arc_cw", "arc_ccw"} and motion.center is not None:
            center = motion.center
            start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
            end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
            delta = end_angle - start_angle
            if motion.kind == "arc_cw":
                while delta >= 0:
                    delta -= 2 * math.pi
            else:
                while delta <= 0:
                    delta += 2 * math.pi
            radius = math.dist(start, center)
            steps = max(2, math.ceil(abs(delta) / math.radians(5)))
            points.extend((center[0] + radius * math.cos(start_angle + delta * index / steps), center[1] + radius * math.sin(start_angle + delta * index / steps)) for index in range(1, steps + 1))
        else:
            points.append(end)
    return points


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.dist(point, start)
    ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    return math.hypot(point[0] - (start[0] + ratio * dx), point[1] - (start[1] + ratio * dy))


def _number_sets_close(first, second, tolerance: float) -> bool:
    if len(first) != len(second):
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))
