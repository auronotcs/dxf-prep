"""Reviewed splitting of profile selections into drill and contour sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math

from .feature_recognition import circular_polyline_to_circle
from .selection import is_machinable_entity
from .model import Circle, DrillCirclesOperation, Polyline
from .units import unit_scale


@dataclass(frozen=True)
class ProfileDrillingSelection:
    circles: tuple[Circle, ...]
    profiles: tuple
    excluded_ids: tuple[str, ...]
    diameter_tolerance: float


def classify_profile_drilling(entities, diameter: float, units: str) -> ProfileDrillingSelection:
    """Keep exact IDs; recognize only a complete, ordered circular boundary.

    Use the existing circle selector's 0.001 inch diameter tolerance and drill
    recognition tolerances, scaled with the job. Nonmatches stay as profiles.
    """
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError("Choose a cutter with a positive finite diameter.")
    tolerance = .001 * unit_scale("in", units)
    circles, profiles, excluded = [], [], []
    seen = set()
    for entity in entities:
        if entity.id in seen:
            continue
        seen.add(entity.id)
        if not is_machinable_entity(entity):
            excluded.append(entity.id)
            continue
        candidate = entity if isinstance(entity, Circle) else None
        if isinstance(entity, Polyline) and entity.closed:
            candidate = circular_polyline_to_circle(
                entity, absolute_tolerance=tolerance, relative_tolerance=.005,
                minimum_vertices=8,
            )
            if candidate is not None:
                points = list(entity.points)
                if points[0].distance_to(points[-1]) <= 1e-12:
                    points.pop()
                angles = [math.atan2(p.y - candidate.center.y, p.x - candidate.center.x) for p in points]
                turns = [
                    (b - a + math.pi) % math.tau - math.pi
                    for a, b in zip(angles, angles[1:] + angles[:1])
                ]
                # A star, backtracking, or repeated traversal is not a hole.
                if not (all(t > 1e-10 for t in turns) or all(t < -1e-10 for t in turns)) or not math.isclose(
                        abs(sum(turns)), math.tau, abs_tol=1e-7):
                    candidate = None
        if candidate is not None and all(math.isfinite(v) for v in (
                candidate.radius, candidate.center.x, candidate.center.y
        )) and abs(candidate.radius * 2 - diameter) <= tolerance + 1e-12:
            circles.append(deepcopy(candidate))
        else:
            profiles.append(entity)
    return ProfileDrillingSelection(tuple(circles), tuple(profiles), tuple(excluded), tolerance)


def drilling_capability_error(tool, catalog_record=None) -> str:
    """Respect explicitly recorded restrictions; unknown capability stays unknown."""
    if tool is None:
        return "Load a cutter before including drilling."
    if tool.spindle_mode == "disabled":
        return "The selected tool has its spindle disabled."
    records = [tool.metadata, catalog_record or {}]
    for record in tuple(records):
        if isinstance(record.get("capabilities"), dict):
            records.append(record["capabilities"])
    for record in records:
        for key in ("center_cutting", "center_cut", "can_plunge", "plunge_capable", "supports_plunge"):
            value = record.get(key)
            if value is False or (value is not None and str(value).strip().casefold() in {"false", "no", "0", "disabled"}):
                return f"{tool.name} is recorded as unable to center-cut/plunge ({key})."
    return ""


def drill_operation_from_values(entity_ids, values) -> DrillCirclesOperation:
    """One factory shared by standalone Drill and Include drilling."""
    return DrillCirclesOperation(
        name=values.get("recipe_name") or "Drill Circles",
        tool_station=values["station"], entity_ids=list(entity_ids),
        target_depth=values["target_depth"], safe_z=values["safe_z"],
        peck_depth=values["peck_depth"], peck_definition=values["peck_definition"],
        peck_count=values["peck_count"], peck_mode=values["peck_mode"],
        retract_height=values["retract_height"], reentry_clearance=values["reentry_clearance"],
        dwell_seconds=values["dwell_seconds"], top_dwell_seconds=values["top_dwell_seconds"],
        surface_feed_override=values["surface_feed"], retract_feed_override=values["retract_feed"],
        controlled_retract=values["controlled_retract"], return_to_surface=values["return_to_surface"],
        plunge_rate_override=values["plunge_rate"], spindle_rpm_override=values["spindle_rpm"],
        sequence_role="drill_thread", recipe_name=values.get("recipe_name", ""),
        recipe_metadata=deepcopy(values.get("recipe_metadata", {})),
    )
