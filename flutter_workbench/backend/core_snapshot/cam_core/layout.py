from __future__ import annotations

from dataclasses import dataclass
import math

from .model import Circle, Entity, Line, Point, Polyline, Rectangle, TextEntity


SHEET_SIZES: dict[str, tuple[float, float]] = {
    "48 × 96": (96.0, 48.0),
    "48 × 120": (120.0, 48.0),
    "60 × 120": (120.0, 60.0),
}


@dataclass(frozen=True)
class SheetLayout:
    label: str
    sheet_width: float
    sheet_height: float
    columns: int
    rows: int
    origin_x: float
    origin_y: float

    @property
    def count(self) -> int:
        return self.columns * self.rows


def entity_bounds(entity: Entity) -> tuple[float, float, float, float] | None:
    if isinstance(entity, Line):
        points = [entity.start, entity.end]
    elif isinstance(entity, Rectangle):
        points = [entity.corner_a, entity.corner_b]
    elif isinstance(entity, Circle):
        return (
            entity.center.x - entity.radius,
            entity.center.y - entity.radius,
            entity.center.x + entity.radius,
            entity.center.y + entity.radius,
        )
    elif isinstance(entity, Polyline):
        points = entity.points
    elif isinstance(entity, TextEntity):
        # A deliberately conservative visual approximation.  Font-specific
        # glyph extents are resolved by the UI/outline converter, but bounds
        # must remain deterministic in the headless core for hit testing and
        # transforms.
        lines = (entity.text or " ").splitlines() or [" "]
        width = max(len(line) for line in lines) * entity.height * 0.62 * entity.width_factor
        height = entity.height * (1.0 + max(0, len(lines) - 1) * 1.2)
        alignment_offset = {"center": -width / 2.0, "right": -width}.get(entity.alignment, 0.0)
        corners = (
            Point(alignment_offset, -0.2 * entity.height),
            Point(alignment_offset + width, -0.2 * entity.height),
            Point(alignment_offset + width, height - 0.2 * entity.height),
            Point(alignment_offset, height - 0.2 * entity.height),
        )
        angle = math.radians(entity.rotation)
        cosine, sine = math.cos(angle), math.sin(angle)
        points = [
            Point(
                entity.insertion.x + point.x * cosine - point.y * sine,
                entity.insertion.y + point.x * sine + point.y * cosine,
            )
            for point in corners
        ]
    else:
        return None
    if not points:
        return None
    return min(point.x for point in points), min(point.y for point in points), max(point.x for point in points), max(point.y for point in points)


def entity_perimeter(entity: Entity) -> float:
    """Return contour length, including the closing edge for closed geometry."""
    if isinstance(entity, Line):
        return entity.start.distance_to(entity.end)
    if isinstance(entity, Circle):
        return 2.0 * math.pi * entity.radius
    if isinstance(entity, Rectangle):
        return 2.0 * (
            abs(entity.corner_b.x - entity.corner_a.x)
            + abs(entity.corner_b.y - entity.corner_a.y)
        )
    if isinstance(entity, Polyline):
        length = sum(first.distance_to(second) for first, second in zip(entity.points, entity.points[1:]))
        if entity.closed and len(entity.points) > 1:
            length += entity.points[-1].distance_to(entity.points[0])
        return length
    return 0.0


def drawing_bounds(
    entities: list[Entity], *, include_annotations: bool = False,
) -> tuple[float, float, float, float] | None:
    """Bounds of cutter-capable drawing geometry by default.

    Editable text is an annotation, not stock.  Callers concerned with visual
    selection or Fit All may explicitly include it; stock detection, plate
    creation and nesting remain protected by the safe default.
    """
    found = False
    min_x = min_y = max_x = max_y = 0.0
    for entity in entities:
        if not include_annotations and isinstance(entity, TextEntity):
            continue
        box = entity_bounds(entity)
        if box is None:
            continue
        if not found:
            min_x, min_y, max_x, max_y = box
            found = True
        else:
            if box[0] < min_x:
                min_x = box[0]
            if box[1] < min_y:
                min_y = box[1]
            if box[2] > max_x:
                max_x = box[2]
            if box[3] > max_y:
                max_y = box[3]
    return (min_x, min_y, max_x, max_y) if found else None


def recommend_sheet_size(
    entities: list[Entity],
    unit_scale: float = 1.0,
    additional_sizes: dict[str, tuple[float, float]] | None = None,
) -> str:
    bounds = drawing_bounds(entities)
    if bounds is None:
        return "48 × 96"
    boxes = [
        box for entity in entities
        if not isinstance(entity, TextEntity) and (box := entity_bounds(entity)) is not None
    ]
    min_x, min_y, max_x, max_y = bounds
    drawing_width = max(max_x - min_x, 0.001)
    drawing_height = max(max_y - min_y, 0.001)
    candidates = []
    candidates_sizes = {**SHEET_SIZES, **(additional_sizes or {})}
    for label, base_size in candidates_sizes.items():
        sheet_width, sheet_height = base_size[0] * unit_scale, base_size[1] * unit_scale
        if any(box[2] - box[0] > sheet_width + 0.001 or box[3] - box[1] > sheet_height + 0.001 for box in boxes):
            continue
        origin_x = 0.0
        origin_y = 0.0
        columns = max(1, math.ceil((max_x - origin_x) / sheet_width - 1e-9))
        rows = 1
        crossings = 0
        for column in range(1, columns):
            boundary = origin_x + column * sheet_width
            crossings += sum(box[0] + 0.001 < boundary < box[2] - 0.001 for box in boxes)
        for row in range(1, rows):
            boundary = origin_y + row * sheet_height
            crossings += sum(box[1] + 0.001 < boundary < box[3] - 0.001 for box in boxes)
        sheet_area = columns * rows * sheet_width * sheet_height
        drawing_area = drawing_width * drawing_height
        vertical_overflow = max(0.0, drawing_height - sheet_height)
        score = crossings * 1_000_000 + vertical_overflow * 100_000 + (sheet_area - drawing_area) + abs(sheet_height - min(drawing_height, sheet_height))
        candidates.append((score, label))
    return min(candidates)[1] if candidates else "Drawing bounds"


def make_sheet_layout(
    entities: list[Entity],
    label: str,
    custom_size: tuple[float, float] | None = None,
    unit_scale: float = 1.0,
) -> SheetLayout | None:
    bounds = drawing_bounds(entities)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    drawing_width = max(max_x - min_x, 0.001)
    drawing_height = max(max_y - min_y, 0.001)
    if label == "Drawing bounds":
        return SheetLayout(label, max(drawing_width, max_x, 0.001), max(drawing_height, max_y, 0.001), 1, 1, 0.0, 0.0)
    if label == "Custom":
        if custom_size is None or custom_size[0] <= 0 or custom_size[1] <= 0:
            raise ValueError("Custom sheet width and height must be positive.")
        sheet_width, sheet_height = custom_size
    else:
        try:
            base_width, base_height = SHEET_SIZES[label]
            sheet_width, sheet_height = base_width * unit_scale, base_height * unit_scale
        except KeyError as exc:
            raise ValueError(f"Unknown sheet size: {label}") from exc
    origin_x = 0.0
    origin_y = 0.0
    columns = max(1, math.ceil((max_x - origin_x) / sheet_width - 1e-9))
    rows = 1
    return SheetLayout(label, sheet_width, sheet_height, columns, rows, origin_x, origin_y)


def sheet_rectangles(layout: SheetLayout) -> list[tuple[float, float, float, float]]:
    return [
        (
            layout.origin_x + column * layout.sheet_width,
            layout.origin_y + row * layout.sheet_height,
            layout.origin_x + (column + 1) * layout.sheet_width,
            layout.origin_y + (row + 1) * layout.sheet_height,
        )
        for row in range(layout.rows)
        for column in range(layout.columns)
    ]
