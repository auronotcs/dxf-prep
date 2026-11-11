from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import shutil
from typing import Mapping

from .model import CamProject, effective_tool
from .stock import generate_operation_toolpath
from .toolpath import GeneratedToolpath, Motion, MotionKind
from .units import unit_scale
from .workflow import ordered_operations


TOLERANCE = 1e-8


@dataclass(frozen=True)
class CncPostResult:
    path: Path
    block_count: int
    motion_count: int
    tool_stations: tuple[int, ...]


def cnc_output_filename(base_name: str, plate_number: int) -> str:
    clean = base_name.strip()
    if clean.casefold().endswith(".cnc"):
        clean = clean[:-4].rstrip()
    if not clean:
        raise ValueError("CNC output file name cannot be empty.")
    if any(character in clean for character in '<>:"/\\|?*') or clean.endswith((".", " ")):
        raise ValueError("CNC output file name contains characters Windows cannot use.")
    suffix = f" {plate_number:02d}"
    return f"{clean if clean.endswith(suffix) else clean + suffix}.cnc"


def post_enroute_cnc(
    project: CamProject,
    path: Path,
    *,
    require_positive_xy: bool | None = None,
    generated_paths: Mapping[str, GeneratedToolpath] | None = None,
) -> CncPostResult:
    """Post controller-neutral motions using the observed EnRoute router dialect.

    Internal CAM Z uses positive clearance and negative cut depth. The router
    dialect is the inverse: negative clearance and positive cut depth.
    Coordinates and feeds are always posted in inches / inches per second.
    """
    path = Path(path)
    if path.suffix.casefold() != ".cnc":
        path = path.with_suffix(".cnc")
    scale = unit_scale(project.units, "in")
    if require_positive_xy is None:
        require_positive_xy = project.metadata.get("job_setup", {}).get("allow_negative_xy", False) is not True
    lines = ["M90", "G90", "G70", "G75"]
    current_station: int | None = None
    current_rpm: int | None = None
    current_position: tuple[float, float, float] | None = None
    current_feed: float | None = None
    block_count = motion_count = 0
    stations: list[int] = []
    if generated_paths is not None:
        from .model import TabReleaseOperation
        from .full_release import check_prepared_prerequisites
        for stage in project.operations:
            if stage.enabled and isinstance(stage, TabReleaseOperation):
                check_prepared_prerequisites(project, stage, generated_paths)

    for operation in ordered_operations([item for item in project.operations if item.enabled]):
        from .model import DragKnifeOperation
        if isinstance(operation, DragKnifeOperation):
            from .drag_knife import validate_knife_setup
            validate_knife_setup(project, operation)
            if operation.tool_station != 8 or not operation.setup_verified:
                raise ValueError('Drag Knife output requires reviewed setup and the spindle-disabled T8 controller station.')
        tool = effective_tool(project, operation)
        if tool is None:
            raise ValueError(f"{operation.name} references empty tool station T{operation.tool_station}.")
        if tool.spindle_mode != "disabled" and tool.spindle_rpm is None:
            raise ValueError(f"T{tool.station} requires a spindle RPM before CNC output.")
        if tool.spindle_rpm is not None and tool.spindle_rpm != current_rpm:
            lines.append(f"G97 S{int(tool.spindle_rpm)}")
            current_rpm = int(tool.spindle_rpm)
        if tool.station != current_station:
            lines.append(f"G00 T{tool.station} //{_safe_comment(tool.name)}")
            current_station = tool.station
            current_position = None
            current_feed = None
            if tool.station not in stations:
                stations.append(tool.station)

        if generated_paths is None:
            generated = generate_operation_toolpath(project, operation)
        else:
            generated = generated_paths.get(operation.id)
            if generated is None:
                raise ValueError(
                    f"Prepared motion is incomplete for {operation.name}; regenerate Output."
                )
            if getattr(operation, "tab_release_plan", None):
                from .tab_release import check_prepared_tab_cleanup
                check_prepared_tab_cleanup(project, operation, generated)
            from .model import TabReleaseOperation
            if isinstance(operation, TabReleaseOperation):
                from .full_release import check_prepared_release
                check_prepared_release(project, operation, generated)
        from .output_coordinates import toolpath_xy_bounds
        bounds = toolpath_xy_bounds([generated])
        if require_positive_xy and bounds and min(bounds[0], bounds[1]) * scale < -TOLERANCE:
            raise ValueError(
                "Toolpath leaves the positive plate quadrant. In Output, enable Allow negative X/Y "
                "only after verifying the displayed cutter-center range against your machine setup."
            )
        for block in generated.blocks:
            if not block.motions:
                continue
            if block.motions[0].kind != MotionKind.RAPID:
                raise ValueError(f"{block.label or operation.name} does not begin with a safe rapid motion.")
            block_rpm = block.metadata.get("spindle_rpm", tool.spindle_rpm)
            if isinstance(operation, DragKnifeOperation) and block_rpm is not None:
                raise ValueError('Drag Knife prepared motion cannot request spindle RPM.')
            if block_rpm is not None:
                try:
                    block_rpm = int(block_rpm)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{block.label or operation.name} has an invalid spindle RPM.") from exc
                if block_rpm <= 0:
                    raise ValueError(f"{block.label or operation.name} has an invalid spindle RPM.")
                # Every generated block begins after a safe retract and the
                # preceding block has already emitted M22.  Changing the G97
                # setpoint here therefore occurs with the cutter clear and the
                # router disengaged, immediately before this block's M12.
                if block_rpm != current_rpm:
                    if block.metadata.get("low_retract_from_previous"):
                        raise ValueError(
                            f"{block.label or operation.name} attempts an RPM change across a spindle-on low-retract link."
                        )
                    lines.append(f"G97 S{block_rpm}")
                    current_rpm = block_rpm
            first = _machine_point(block.motions[0], scale, require_positive_xy)
            lines.append(_rapid_line(first, current_position))
            current_position = first
            if not block.metadata.get("low_retract_from_previous"):
                lines.append("M12")
            for motion in block.motions[1:]:
                if motion.kind == MotionKind.DWELL:
                    raise ValueError("The verified EnRoute dialect has no qualified dwell command; remove dwell before output.")
                point = _machine_point(motion, scale, require_positive_xy)
                feed = None if motion.feed is None else motion.feed * scale
                line, current_feed = _motion_line(motion, point, current_position, feed, current_feed, scale)
                lines.append(line)
                current_position = point
                motion_count += 1
            if not block.metadata.get("low_retract_to_next"):
                lines.append("M22")
            block_count += 1

    if block_count == 0:
        raise ValueError("CNC output requires at least one enabled operation with generated motion.")
    lines.extend(("G98 P147 D1", "M02"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return CncPostResult(path, block_count, motion_count, tuple(stations))


def _machine_point(motion: Motion, scale: float, require_positive_xy: bool) -> tuple[float, float, float]:
    x, y, z = motion.end.x * scale, motion.end.y * scale, -motion.end.z * scale
    if require_positive_xy and (x < -TOLERANCE or y < -TOLERANCE):
        raise ValueError(
            f"Toolpath leaves the positive plate quadrant at X{_number(x)} Y{_number(y)}; "
            "move the geometry inward or adjust its plate assignment."
        )
    return (0.0 if abs(x) <= TOLERANCE else x, 0.0 if abs(y) <= TOLERANCE else y, 0.0 if abs(z) <= TOLERANCE else z)


def _rapid_line(point: tuple[float, float, float], previous: tuple[float, float, float] | None) -> str:
    if previous is None:
        return f"G00 X{_number(point[0])} Y{_number(point[1])} Z{_number(point[2])}"
    words = []
    for letter, value, old in zip("XYZ", point, previous):
        if not math.isclose(value, old, abs_tol=TOLERANCE):
            words.append(f"{letter}{_number(value)}")
    return "G00 " + " ".join(words or [f"Z{_number(point[2])}"])


def _motion_line(
    motion: Motion,
    point: tuple[float, float, float],
    previous: tuple[float, float, float],
    feed: float | None,
    current_feed: float | None,
    scale: float,
) -> tuple[str, float | None]:
    command = {
        MotionKind.RAPID: "G00",
        MotionKind.LINEAR: "G01",
        MotionKind.ARC_CW: "G02",
        MotionKind.ARC_CCW: "G03",
    }.get(motion.kind)
    if command is None:
        raise ValueError(f"Unsupported controller motion: {motion.kind.value}.")
    if motion.kind == MotionKind.RAPID:
        return _rapid_line(point, previous), current_feed
    words = [command, f"X{_number(point[0])}", f"Y{_number(point[1])}"]
    if not math.isclose(point[2], previous[2], abs_tol=TOLERANCE) or motion.kind == MotionKind.LINEAR:
        words.append(f"Z{_number(point[2])}")
    if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}:
        if motion.center is None:
            raise ValueError("Arc motion is missing its absolute I/J center.")
        words.extend((f"I{_number(motion.center.x * scale)}", f"J{_number(motion.center.y * scale)}"))
    if feed is not None and (current_feed is None or not math.isclose(feed, current_feed, abs_tol=TOLERANCE)):
        words.append(f"F{_number(feed)}")
        current_feed = feed
    return " ".join(words), current_feed


def _number(value: float) -> str:
    if abs(value) <= 0.00000005:
        value = 0.0
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0.0" if text in {"", "-0"} else text


def _safe_comment(value: str) -> str:
    cleaned = value.replace("//", "/").replace("\r", " ").replace("\n", " ")
    return cleaned.encode("ascii", "replace").decode("ascii").strip() or "Tool"
