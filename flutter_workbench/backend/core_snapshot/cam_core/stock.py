from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

try:
    import numpy as _np
except ImportError:  # pragma: no cover - bundled runtime includes NumPy
    _np = None

from .model import (
    CamProject,
    DrillCirclesOperation,
    EngraveOperation,
    DragKnifeOperation,
    IslandFillOperation,
    OffsetSide,
    RestMachiningOperation,
    ProfileRestMachiningOperation,
    RouterOffsetOperation,
    ThreadMillOperation,
    Tool,
    ToolKind,
)
from .toolpath import (
    GeneratedToolpath,
    MotionKind,
    ToolPoint,
    generate_closed_router_offset,
    generate_drill_circles,
    generate_drag_engraving,
    generate_open_router_offset,
    router_offset_uses_open_geometry,
    generate_rectangular_island_fill,
    generate_rest_machining,
    generate_profile_rest_machining,
    generate_thread_mill,
)
from .optimization import (
    CycleComparison,
    cycle_optimization_settings,
    estimate_cycle_metrics,
    optimize_generated_toolpath,
    project_with_cycle_optimization,
)
from .workflow import ordered_operations


@dataclass
class StockGrid:
    bounds: tuple[float, float, float, float]
    cell_size: float
    thickness: float
    columns: int
    rows: int
    removed_depths: Sequence[float] = field(default_factory=list)
    intended_depths: Sequence[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        count = self.columns * self.rows
        if len(self.removed_depths) == 0:
            self.removed_depths = _np.zeros(count, dtype=float) if _np is not None else [0.0] * count
        if len(self.intended_depths) == 0:
            self.intended_depths = _np.zeros(count, dtype=float) if _np is not None else [0.0] * count

    def index(self, column: int, row: int) -> int:
        return row * self.columns + column

    def removed_depth(self, column: int, row: int) -> float:
        return self.removed_depths[self.index(column, row)]

    def remaining_thickness(self, column: int, row: int) -> float:
        return max(0.0, self.thickness - self.removed_depth(column, row))

    def intended_depth(self, column: int, row: int) -> float:
        return float(self.intended_depths[self.index(column, row)])

    @property
    def removed_cell_count(self) -> int:
        if _np is not None and isinstance(self.removed_depths, _np.ndarray):
            return int(_np.count_nonzero(self.removed_depths > 1e-9))
        return sum(depth > 1e-9 for depth in self.removed_depths)

    @property
    def maximum_removed_depth(self) -> float:
        return float(max(self.removed_depths, default=0.0))

    def comparison_counts(self, tolerance: float = 0.001) -> tuple[int, int, int]:
        """Return (remaining, on-target, gouged) cells in the planned cut footprint."""
        remaining = on_target = gouged = 0
        for actual, intended in zip(self.removed_depths, self.intended_depths):
            if intended <= tolerance:
                continue
            if actual < intended - tolerance:
                remaining += 1
            elif actual > intended + tolerance:
                gouged += 1
            else:
                on_target += 1
        return remaining, on_target, gouged


@dataclass(frozen=True)
class StockSimulationResult:
    grid: StockGrid
    processed_operation_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def generate_operation_toolpath(project: CamProject, operation, *, optimize: bool = True) -> GeneratedToolpath:
    # Validate the complete retention group even on a saved cleanout-cache hit.
    from .profile_second_tool import validate_retention
    validate_retention(project,operation)
    if not optimize:
        result = _generate_operation_toolpath(project, operation, optimize=False)
    else:
        from .calculation_cache import reuse_cleanout_path
        result = reuse_cleanout_path(project, operation, optimize,
            lambda: _generate_operation_toolpath(project, operation, optimize=optimize))
    from .hybrid_profile import verify_finish_tabs
    verify_finish_tabs(operation,result)
    return result


def _generate_operation_toolpath(project: CamProject, operation, *, optimize: bool = True) -> GeneratedToolpath:
    generated: GeneratedToolpath
    if isinstance(operation, RouterOffsetOperation):
        generated = (
            generate_open_router_offset(project, operation)
            if router_offset_uses_open_geometry(project, operation)
            else generate_closed_router_offset(project, operation)
        )
    elif isinstance(operation, IslandFillOperation):
        generated = generate_rectangular_island_fill(project, operation)
    elif isinstance(operation, DrillCirclesOperation):
        generated = generate_drill_circles(project, operation)
    elif isinstance(operation, DragKnifeOperation):
        from .drag_knife import generate_drag_knife
        generated = generate_drag_knife(project, operation)
    elif isinstance(operation, EngraveOperation):
        generated = generate_drag_engraving(project, operation)
    elif isinstance(operation, ThreadMillOperation):
        generated = generate_thread_mill(project, operation)
    elif isinstance(operation, RestMachiningOperation):
        generated = generate_rest_machining(project, operation)
    elif isinstance(operation, ProfileRestMachiningOperation):
        generated = generate_profile_rest_machining(project, operation)
    else:
        raise ValueError(f"Stock simulation does not support operation type {type(operation).__name__}.")
    return optimize_generated_toolpath(project, operation, generated) if optimize else generated


def compare_project_cycle_time(project: CamProject) -> CycleComparison:
    """Generate baseline and optimized paths and compare estimated machine time."""
    baseline_project = project_with_cycle_optimization(project, False)
    optimized_project = project_with_cycle_optimization(project, True)
    baseline_operations = ordered_operations([item for item in baseline_project.operations if item.enabled])
    optimized_operations = ordered_operations([item for item in optimized_project.operations if item.enabled])
    baseline_paths = [
        (operation, generate_operation_toolpath(baseline_project, operation))
        for operation in baseline_operations
    ]
    optimized_paths = [
        (operation, generate_operation_toolpath(optimized_project, operation))
        for operation in optimized_operations
    ]
    settings = cycle_optimization_settings(optimized_project)
    return CycleComparison(
        estimate_cycle_metrics(baseline_project, baseline_paths, settings),
        estimate_cycle_metrics(optimized_project, optimized_paths, settings),
    )


def simulate_remaining_stock(
    project: CamProject,
    bounds: tuple[float, float, float, float],
    thickness: float,
    cell_size: float,
    *,
    operation_ids: set[str] | None = None,
    max_cells: int = 500_000,
) -> StockSimulationResult:
    """Rasterize cutter sweeps into a deterministic top-down depth map."""
    min_x, min_y, max_x, max_y = bounds
    if max_x <= min_x or max_y <= min_y:
        raise ValueError("Stock simulation bounds must have positive width and height.")
    if thickness <= 0 or cell_size <= 0:
        raise ValueError("Stock thickness and simulation cell size must be positive.")
    columns = max(1, math.ceil((max_x - min_x) / cell_size))
    rows = max(1, math.ceil((max_y - min_y) / cell_size))
    if columns * rows > max_cells:
        minimum = math.sqrt((max_x - min_x) * (max_y - min_y) / max_cells)
        raise ValueError(f"Stock resolution is too fine; use a cell size of at least {minimum:.4g} project units.")
    grid = StockGrid(bounds, cell_size, thickness, columns, rows)
    processed: list[str] = []
    warnings: list[str] = []

    for operation in project.operations:
        if not operation.enabled or operation_ids is not None and operation.id not in operation_ids:
            continue
        if isinstance(operation, (EngraveOperation, DragKnifeOperation)):
            processed.append(operation.id)
            description = 'knife slits' if isinstance(operation, DragKnifeOperation) else 'drag-engraver compression'
            warnings.append(f"{operation.name}: {description} are not treated as volumetric stock removal.")
            continue
        try:
            from .depth_intent import resolve_fill_depth
            operation = resolve_fill_depth(project, operation)
            path = generate_operation_toolpath(project, operation)
            tool = project.tool_library.by_station(operation.tool_station)
            if tool is None:
                raise ValueError(f"T{operation.tool_station} is empty.")
            _rasterize_toolpath(grid, path, tool, operation.target_depth)
            processed.append(operation.id)
            if isinstance(operation, ThreadMillOperation):
                warnings.append(f"{operation.name}: thread-form stock is represented by the cutter envelope, not a 3D thread profile.")
            if tool.cutting_edge_length is not None and operation.target_depth > tool.cutting_edge_length + 1e-9:
                warnings.append(f"{operation.name}: target depth exceeds T{tool.station} CEL; shank contact is possible.")
        except ValueError as exc:
            warnings.append(f"{operation.name}: {exc}")
    return StockSimulationResult(grid, tuple(processed), tuple(warnings))


def _rasterize_toolpath(grid: StockGrid, path: GeneratedToolpath, tool: Tool, intended_depth: float) -> None:
    for block in path.blocks:
        current: ToolPoint | None = None
        for motion in block.motions:
            if current is not None and motion.kind not in {MotionKind.RAPID, MotionKind.DWELL}:
                if motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW} and motion.center is not None:
                    points = _flatten_arc(current, motion, grid.cell_size)
                    for start, end in zip(points, points[1:]):
                        _rasterize_segment(grid, start, end, tool, intended_depth)
                else:
                    _rasterize_segment(grid, current, motion.end, tool, intended_depth)
            current = motion.end


def _flatten_arc(start: ToolPoint, motion, cell_size: float) -> list[ToolPoint]:
    center = motion.center
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(motion.end.y - center.y, motion.end.x - center.x)
    if motion.kind == MotionKind.ARC_CCW:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
    else:
        sweep = -((start_angle - end_angle) % (2.0 * math.pi))
    if abs(sweep) <= 1e-12:
        sweep = 2.0 * math.pi if motion.kind == MotionKind.ARC_CCW else -2.0 * math.pi
    radius = math.hypot(start.x - center.x, start.y - center.y)
    arc_length = abs(sweep) * radius
    count = max(4, math.ceil(arc_length / max(cell_size / 2.0, 1e-9)), math.ceil(abs(sweep) / (math.pi / 36.0)))
    return [
        ToolPoint(
            center.x + radius * math.cos(start_angle + sweep * index / count),
            center.y + radius * math.sin(start_angle + sweep * index / count),
            start.z + (motion.end.z - start.z) * index / count,
        )
        for index in range(count + 1)
    ]


def cutter_depth_at_radius(tool: Tool, center_depth: float, radial_distance: float) -> float:
    """Depth cut by the tool profile at a radial distance from its centerline."""
    radius = tool.diameter / 2.0
    if center_depth <= 0 or radial_distance > radius + 1e-12:
        return 0.0
    if tool.kind in {ToolKind.V_BIT, ToolKind.ENGRAVER, ToolKind.DRILL}:
        default_angle = 118.0 if tool.kind == ToolKind.DRILL else 60.0
        angle = float(tool.metadata.get("included_angle_degrees", tool.metadata.get("tip_angle_degrees", default_angle)))
        tip_radius = max(0.0, float(tool.metadata.get("tip_diameter", 0.0))) / 2.0
        if radial_distance <= tip_radius:
            return center_depth
        return max(0.0, center_depth - (radial_distance - tip_radius) / math.tan(math.radians(angle / 2.0)))
    if tool.kind == ToolKind.BALL_NOSE:
        return max(0.0, center_depth - (radius - math.sqrt(max(0.0, radius * radius - radial_distance * radial_distance))))
    if tool.kind == ToolKind.BULL_NOSE:
        corner_radius = min(radius, max(0.0, float(tool.metadata.get("corner_radius", 0.0))))
        flat_radius = radius - corner_radius
        if corner_radius > 0 and radial_distance > flat_radius:
            delta = radial_distance - flat_radius
            return max(0.0, center_depth - (corner_radius - math.sqrt(max(0.0, corner_radius * corner_radius - delta * delta))))
    return center_depth


def _rasterize_segment(grid: StockGrid, start: ToolPoint, end: ToolPoint, tool: Tool, intended_depth: float) -> None:
    radius = tool.diameter / 2.0
    maximum_depth = max(0.0, -start.z, -end.z)
    if maximum_depth <= 1e-12:
        return
    min_x, min_y, _max_x, _max_y = grid.bounds
    column_min = max(0, math.floor((min(start.x, end.x) - radius - min_x) / grid.cell_size))
    column_max = min(grid.columns - 1, math.floor((max(start.x, end.x) + radius - min_x) / grid.cell_size))
    row_min = max(0, math.floor((min(start.y, end.y) - radius - min_y) / grid.cell_size))
    row_max = min(grid.rows - 1, math.floor((max(start.y, end.y) + radius - min_y) / grid.cell_size))
    dx, dy = end.x - start.x, end.y - start.y
    length_squared = dx * dx + dy * dy
    if _np is not None and isinstance(grid.removed_depths, _np.ndarray) and (column_max - column_min + 1) * (row_max - row_min + 1) >= 64:
        _rasterize_segment_numpy(
            grid, start, end, tool, intended_depth, radius,
            column_min, column_max, row_min, row_max, dx, dy, length_squared,
        )
        return
    for row in range(row_min, row_max + 1):
        y = min_y + (row + 0.5) * grid.cell_size
        for column in range(column_min, column_max + 1):
            x = min_x + (column + 0.5) * grid.cell_size
            if length_squared <= 1e-18:
                parameter = 0.0
            else:
                parameter = max(0.0, min(1.0, ((x - start.x) * dx + (y - start.y) * dy) / length_squared))
            closest_x = start.x + parameter * dx
            closest_y = start.y + parameter * dy
            radial_distance = math.hypot(x - closest_x, y - closest_y)
            if radial_distance <= radius + 1e-12:
                center_depth = maximum_depth if length_squared <= 1e-18 else max(0.0, -(start.z + parameter * (end.z - start.z)))
                depth = cutter_depth_at_radius(tool, center_depth, radial_distance)
                planned = cutter_depth_at_radius(tool, min(grid.thickness, intended_depth), radial_distance)
                index = grid.index(column, row)
                grid.removed_depths[index] = min(grid.thickness, max(grid.removed_depths[index], depth))
                grid.intended_depths[index] = min(grid.thickness, max(grid.intended_depths[index], planned))


def _rasterize_segment_numpy(
    grid: StockGrid, start: ToolPoint, end: ToolPoint, tool: Tool, intended_depth: float,
    radius: float, column_min: int, column_max: int, row_min: int, row_max: int,
    dx: float, dy: float, length_squared: float,
) -> None:
    min_x, min_y, _max_x, _max_y = grid.bounds
    columns = _np.arange(column_min, column_max + 1)
    rows = _np.arange(row_min, row_max + 1)
    x, y = _np.meshgrid(min_x + (columns + 0.5) * grid.cell_size, min_y + (rows + 0.5) * grid.cell_size)
    if length_squared <= 1e-18:
        parameter = _np.zeros_like(x)
        center_depth = _np.full_like(x, max(0.0, -start.z, -end.z))
    else:
        parameter = _np.clip(((x - start.x) * dx + (y - start.y) * dy) / length_squared, 0.0, 1.0)
        center_depth = _np.maximum(0.0, -(start.z + parameter * (end.z - start.z)))
    closest_x = start.x + parameter * dx
    closest_y = start.y + parameter * dy
    radial = _np.hypot(x - closest_x, y - closest_y)
    mask = radial <= radius + 1e-12
    actual = _profile_depth_array(tool, center_depth, radial)
    planned = _profile_depth_array(tool, _np.full_like(radial, min(grid.thickness, intended_depth)), radial)
    actual = _np.where(mask, _np.minimum(grid.thickness, actual), 0.0)
    planned = _np.where(mask, _np.minimum(grid.thickness, planned), 0.0)
    removed = grid.removed_depths.reshape((grid.rows, grid.columns))
    intended = grid.intended_depths.reshape((grid.rows, grid.columns))
    removed_patch = removed[row_min:row_max + 1, column_min:column_max + 1]
    intended_patch = intended[row_min:row_max + 1, column_min:column_max + 1]
    _np.maximum(removed_patch, actual, out=removed_patch)
    _np.maximum(intended_patch, planned, out=intended_patch)


def _profile_depth_array(tool: Tool, center_depth, radial):
    radius = tool.diameter / 2.0
    if tool.kind in {ToolKind.V_BIT, ToolKind.ENGRAVER, ToolKind.DRILL}:
        default_angle = 118.0 if tool.kind == ToolKind.DRILL else 60.0
        angle = float(tool.metadata.get("included_angle_degrees", tool.metadata.get("tip_angle_degrees", default_angle)))
        tip_radius = max(0.0, float(tool.metadata.get("tip_diameter", 0.0))) / 2.0
        result = _np.where(radial <= tip_radius, center_depth, center_depth - (radial - tip_radius) / math.tan(math.radians(angle / 2.0)))
    elif tool.kind == ToolKind.BALL_NOSE:
        result = center_depth - (radius - _np.sqrt(_np.maximum(0.0, radius * radius - radial * radial)))
    elif tool.kind == ToolKind.BULL_NOSE:
        corner_radius = min(radius, max(0.0, float(tool.metadata.get("corner_radius", 0.0))))
        flat_radius = radius - corner_radius
        if corner_radius > 0:
            result = _np.where(radial <= flat_radius, center_depth, center_depth - (corner_radius - _np.sqrt(_np.maximum(0.0, corner_radius * corner_radius - (radial - flat_radius) ** 2))))
        else:
            result = center_depth
    else:
        result = center_depth
    return _np.maximum(0.0, _np.where(radial <= radius + 1e-12, result, 0.0))
