from __future__ import annotations

from dataclasses import dataclass
import math

from .model import CamProject
from .stock import generate_operation_toolpath
from .toolpath import MotionKind, ToolPoint


@dataclass(frozen=True)
class FixtureZone:
    """Rectangular clamp/fixture keep-out with height above the stock top."""

    name: str
    bounds: tuple[float, float, float, float]
    height: float


@dataclass(frozen=True)
class CollisionIssue:
    severity: str
    operation_id: str
    fixture_name: str
    message: str


def check_fixture_collisions(
    project: CamProject,
    fixtures: list[FixtureZone],
    *,
    operation_ids: set[str] | None = None,
) -> tuple[CollisionIssue, ...]:
    """Conservatively check holder envelopes against rectangular fixture zones.

    Tools need holder_diameter and stickout values for a meaningful holder check.
    Missing geometry is reported once per used tool instead of being guessed.
    """
    issues: list[CollisionIssue] = []
    missing_reported: set[int] = set()
    collision_keys: set[tuple[str, str]] = set()
    for operation in project.operations:
        if not operation.enabled or operation_ids is not None and operation.id not in operation_ids:
            continue
        tool = project.tool_library.by_station(operation.tool_station)
        if tool is None:
            continue
        if not tool.holder_diameter or not tool.stickout:
            if tool.station not in missing_reported:
                missing_reported.add(tool.station)
                issues.append(CollisionIssue(
                    "warning", operation.id, "",
                    f"T{tool.station} has no holder diameter/stickout; holder collision checking was skipped.",
                ))
            continue
        path = generate_operation_toolpath(project, operation)
        holder_radius = tool.holder_diameter / 2.0
        for block in path.blocks:
            current: ToolPoint | None = None
            for motion in block.motions:
                if current is not None and motion.kind != MotionKind.DWELL:
                    depth = max(0.0, -current.z, -motion.end.z)
                    holder_bottom_clearance = tool.stickout - depth
                    for fixture in fixtures:
                        key = (operation.id, fixture.name)
                        if key in collision_keys or holder_bottom_clearance > fixture.height + 1e-9:
                            continue
                        if _segment_hits_expanded_box(current, motion.end, fixture.bounds, holder_radius):
                            collision_keys.add(key)
                            issues.append(CollisionIssue(
                                "error", operation.id, fixture.name,
                                f"{operation.name}: T{tool.station} holder envelope may contact {fixture.name}; "
                                f"holder bottom clearance {holder_bottom_clearance:.4g}, fixture height {fixture.height:.4g}.",
                            ))
                current = motion.end
    return tuple(issues)


def _segment_hits_expanded_box(start: ToolPoint, end: ToolPoint, bounds: tuple[float, float, float, float], radius: float) -> bool:
    min_x, min_y, max_x, max_y = bounds
    min_x -= radius
    min_y -= radius
    max_x += radius
    max_y += radius
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return min_x <= start.x <= max_x and min_y <= start.y <= max_y
    # Liang-Barsky clipping; intersection with the expanded box is sufficient.
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, start.x - min_x), (dx, max_x - start.x), (-dy, start.y - min_y), (dy, max_y - start.y)):
        if abs(p) <= 1e-18:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            t0 = max(t0, ratio)
        else:
            t1 = min(t1, ratio)
        if t0 > t1:
            return False
    return True
