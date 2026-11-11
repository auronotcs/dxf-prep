"""Exact dimensions for a temporary axis-aligned rectangle draft."""
from dataclasses import dataclass
import math

from .model import Point


@dataclass
class RectangleDraft:
    start: Point
    pointer: Point

    @property
    def dimensions(self) -> tuple[float, float]:
        return abs(self.pointer.x - self.start.x), abs(self.pointer.y - self.start.y)

    def endpoint(self, width: str, height: str) -> Point:
        values = []
        for name, text in (("Width", width), ("Height", height)):
            try:
                value = float(text)
            except (ValueError, TypeError):
                raise ValueError(f"{name} must be a positive finite number.") from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number.")
            values.append(value)
        end = Point(
            self.start.x + math.copysign(values[0], self.pointer.x - self.start.x or 1),
            self.start.y + math.copysign(values[1], self.pointer.y - self.start.y or 1),
        )
        if not all(math.isfinite(v) for v in (end.x, end.y)):
            raise ValueError("Rectangle coordinates must be finite.")
        return end
