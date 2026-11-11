from __future__ import annotations

"""Parametric sign-shape helpers that produce ordinary editable geometry."""

from dataclasses import dataclass
import math

from .model import Point, Polyline


SHAPE_MACRO_KINDS = ("arrow", "double_arrow", "chevron", "star")
SHAPE_MACRO_DIRECTIONS = ("right", "left", "up", "down")


@dataclass(frozen=True)
class ShapeMacroSpec:
    kind: str
    width: float
    height: float
    direction: str = "right"
    detail: float = 0.4
    points: int = 5

    def validate(self) -> None:
        if self.kind not in SHAPE_MACRO_KINDS:
            raise ValueError(f"Unsupported shape macro: {self.kind!r}.")
        if not math.isfinite(self.width) or not math.isfinite(self.height):
            raise ValueError("Shape width and height must be finite numbers.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Shape width and height must be greater than zero.")
        if self.direction not in SHAPE_MACRO_DIRECTIONS:
            raise ValueError(f"Unsupported shape direction: {self.direction!r}.")
        if not math.isfinite(self.detail) or not 0.15 <= self.detail <= 0.75:
            raise ValueError("Shape detail must be between 0.15 and 0.75.")
        if not 3 <= int(self.points) <= 32:
            raise ValueError("Star points must be between 3 and 32.")


def _right_arrow(width: float, height: float, detail: float) -> list[Point]:
    half_width, half_height = width / 2.0, height / 2.0
    neck_x = half_width - width * max(0.25, min(0.65, detail))
    shaft_half = half_height * max(0.2, min(0.7, detail))
    return [
        Point(-half_width, -shaft_half), Point(neck_x, -shaft_half),
        Point(neck_x, -half_height), Point(half_width, 0.0),
        Point(neck_x, half_height), Point(neck_x, shaft_half),
        Point(-half_width, shaft_half),
    ]


def _horizontal_double_arrow(width: float, height: float, detail: float) -> list[Point]:
    half_width, half_height = width / 2.0, height / 2.0
    head_length = width * max(0.18, min(0.4, detail * 0.75))
    shaft_half = half_height * max(0.2, min(0.7, detail))
    left_neck, right_neck = -half_width + head_length, half_width - head_length
    return [
        Point(-half_width, 0.0), Point(left_neck, -half_height),
        Point(left_neck, -shaft_half), Point(right_neck, -shaft_half),
        Point(right_neck, -half_height), Point(half_width, 0.0),
        Point(right_neck, half_height), Point(right_neck, shaft_half),
        Point(left_neck, shaft_half), Point(left_neck, half_height),
    ]


def _right_chevron(width: float, height: float, detail: float) -> list[Point]:
    half_width, half_height = width / 2.0, height / 2.0
    band = width * max(0.16, min(0.42, detail * 0.7))
    return [
        Point(-half_width, -half_height), Point(half_width, 0.0),
        Point(-half_width, half_height), Point(-half_width + band, half_height),
        Point(half_width - band, 0.0), Point(-half_width + band, -half_height),
    ]


def _star(width: float, height: float, detail: float, points: int) -> list[Point]:
    inner_ratio = max(0.2, min(0.7, detail))
    values: list[Point] = []
    for index in range(points * 2):
        angle = math.pi / 2.0 + math.pi * index / points
        radius = 1.0 if index % 2 == 0 else inner_ratio
        values.append(Point(math.cos(angle) * radius, math.sin(angle) * radius))
    # Normalize the discrete point set so the requested width and height are
    # its exact editable bounding box for any point count.
    min_x = min(point.x for point in values)
    max_x = max(point.x for point in values)
    min_y = min(point.y for point in values)
    max_y = max(point.y for point in values)
    return [
        Point(
            (point.x - (min_x + max_x) / 2.0) * width / (max_x - min_x),
            (point.y - (min_y + max_y) / 2.0) * height / (max_y - min_y),
        )
        for point in values
    ]


def _orient(points: list[Point], direction: str) -> list[Point]:
    if direction == "right":
        return points
    if direction == "left":
        return [Point(-point.x, point.y) for point in points]
    if direction == "up":
        return [Point(-point.y, point.x) for point in points]
    return [Point(point.y, -point.x) for point in points]


def make_shape_macro(
    spec: ShapeMacroSpec,
    center: Point = Point(0.0, 0.0),
    *,
    layer: str = "0",
) -> Polyline:
    """Return one closed polyline centered at *center* in drawing units."""
    spec.validate()
    horizontal_width = spec.height if spec.direction in {"up", "down"} else spec.width
    horizontal_height = spec.width if spec.direction in {"up", "down"} else spec.height
    if spec.kind == "arrow":
        points = _orient(_right_arrow(horizontal_width, horizontal_height, spec.detail), spec.direction)
    elif spec.kind == "double_arrow":
        double_direction = "up" if spec.direction in {"up", "down"} else "right"
        points = _orient(
            _horizontal_double_arrow(horizontal_width, horizontal_height, spec.detail),
            double_direction,
        )
    elif spec.kind == "chevron":
        points = _orient(_right_chevron(horizontal_width, horizontal_height, spec.detail), spec.direction)
    else:
        points = _star(spec.width, spec.height, spec.detail, int(spec.points))
    translated = [Point(point.x + center.x, point.y + center.y) for point in points]
    label = spec.kind.replace("_", " ").title()
    return Polyline(
        points=translated,
        closed=True,
        layer=layer,
        metadata={
            "name": label,
            "shape_macro": spec.kind,
            "shape_macro_version": 1,
            "shape_macro_parameters": {
                "width": spec.width,
                "height": spec.height,
                "direction": spec.direction,
                "detail": spec.detail,
                "points": int(spec.points),
            },
        },
    )
