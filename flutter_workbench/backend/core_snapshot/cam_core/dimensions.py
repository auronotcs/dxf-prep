"""Associative, non-machining shop-drawing dimensions.

Dimensions deliberately live beside ``CamProject.entities`` rather than inside
that cutter-capable collection.  A dimension can reference stable entity
features when one is available, or retain a free fallback point when it is not.
The module owns resolution, displayed geometry, formatting, and serialization
so UI drawing and output summaries cannot quietly disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Iterable, Mapping

from .model import Circle, Entity, Line, Point, Polyline, Rectangle, new_id


DIMENSION_KINDS = (
    "horizontal", "vertical", "aligned", "radius", "diameter",
    "angle", "leader", "note",
)


@dataclass(frozen=True)
class DimensionReference:
    """One measured point, associatively tied to geometry when possible."""

    fallback: Point
    entity_id: str = ""
    feature: str = "free"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "feature": self.feature,
            "fallback": {"x": self.fallback.x, "y": self.fallback.y},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DimensionReference":
        point = payload.get("fallback", {})
        return cls(
            fallback=Point(float(point["x"]), float(point["y"])),
            entity_id=str(payload.get("entity_id", "")),
            feature=str(payload.get("feature", "free")),
        )


@dataclass
class DimensionAnnotation:
    """A persistent associative shop annotation with editable display geometry."""

    kind: str
    start: DimensionReference
    end: DimensionReference
    offset: float
    precision: int = 3
    text_height: float = 0.18
    extension_offset: float = 0.0625
    layer: str = "DIMENSIONS"
    id: str = field(default_factory=lambda: new_id("dimension"))
    metadata: dict[str, Any] = field(default_factory=dict)
    vertex: DimensionReference | None = None

    def validate(self) -> None:
        if self.kind not in DIMENSION_KINDS:
            raise ValueError(f"Unsupported dimension kind: {self.kind!r}.")
        numeric = (self.offset, self.text_height, self.extension_offset)
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("Dimension values must be finite numbers.")
        if not 0 <= self.precision <= 6:
            raise ValueError("Dimension precision must be between 0 and 6 decimal places.")
        if self.text_height <= 0:
            raise ValueError("Dimension text height must be greater than zero.")
        if self.extension_offset < 0:
            raise ValueError("Dimension extension offset cannot be negative.")
        if self.kind == "angle" and self.vertex is None:
            raise ValueError("Angular dimensions require an explicit vertex reference.")
        if self.kind == "angle" and str(self.metadata.get("angle_mode", "")).casefold() not in {"minor", "reflex"}:
            raise ValueError("Angular dimensions require an explicit minor or reflex selection.")
        if self.kind in {"leader", "note"} and not str(self.metadata.get("text", "")).strip():
            raise ValueError(f"{self.kind.title()} annotations require text.")
        if self.kind != "note" and self.start.fallback.distance_to(self.end.fallback) <= 1e-12:
            raise ValueError("Dimension endpoints must be different points.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "offset": self.offset,
            "precision": self.precision,
            "text_height": self.text_height,
            "extension_offset": self.extension_offset,
            "layer": self.layer,
            "metadata": dict(self.metadata),
            "vertex": self.vertex.to_dict() if self.vertex is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DimensionAnnotation":
        result = cls(
            id=str(payload.get("id") or new_id("dimension")),
            kind=str(payload.get("kind", "aligned")),
            start=DimensionReference.from_dict(payload["start"]),
            end=DimensionReference.from_dict(payload["end"]),
            offset=float(payload.get("offset", 0.5)),
            precision=int(payload.get("precision", 3)),
            text_height=float(payload.get("text_height", 0.18)),
            extension_offset=float(payload.get("extension_offset", 0.0625)),
            layer=str(payload.get("layer", "DIMENSIONS")),
            metadata=dict(payload.get("metadata", {})),
            vertex=(
                DimensionReference.from_dict(payload["vertex"])
                if isinstance(payload.get("vertex"), Mapping) else None
            ),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class ResolvedDimension:
    annotation_id: str
    start: Point
    end: Point
    dimension_start: Point
    dimension_end: Point
    extension_lines: tuple[tuple[Point, Point], ...]
    text_point: Point
    text_rotation: float
    value: float
    label: str
    resolved: bool
    arrow_mode: str = "both"
    path: tuple[Point, ...] = ()


def dimension_reference_candidates(entity: Entity) -> tuple[tuple[str, Point], ...]:
    """Return stable snap features for one supported geometry entity."""
    if isinstance(entity, Line):
        return (
            ("line:start", entity.start),
            ("line:end", entity.end),
            ("line:mid", _midpoint(entity.start, entity.end)),
        )
    if isinstance(entity, Circle):
        center = entity.center
        return (
            ("circle:center", center),
            ("circle:right", Point(center.x + entity.radius, center.y)),
            ("circle:left", Point(center.x - entity.radius, center.y)),
            ("circle:top", Point(center.x, center.y + entity.radius)),
            ("circle:bottom", Point(center.x, center.y - entity.radius)),
        )
    if isinstance(entity, Rectangle):
        minimum_x, maximum_x = sorted((entity.corner_a.x, entity.corner_b.x))
        minimum_y, maximum_y = sorted((entity.corner_a.y, entity.corner_b.y))
        points = (
            Point(minimum_x, minimum_y), Point(maximum_x, minimum_y),
            Point(maximum_x, maximum_y), Point(minimum_x, maximum_y),
        )
        return tuple(
            (f"rectangle:corner:{index}", point) for index, point in enumerate(points)
        ) + (("rectangle:center", Point(
            (minimum_x + maximum_x) / 2.0, (minimum_y + maximum_y) / 2.0,
        )),)
    if isinstance(entity, Polyline):
        candidates: list[tuple[str, Point]] = [
            (f"polyline:vertex:{index}", point)
            for index, point in enumerate(entity.points)
        ]
        pairs = list(zip(entity.points, entity.points[1:]))
        if entity.closed and len(entity.points) > 1:
            pairs.append((entity.points[-1], entity.points[0]))
        candidates.extend(
            (f"polyline:segment:{index}", _midpoint(first, second))
            for index, (first, second) in enumerate(pairs)
        )
        return tuple(candidates)
    return ()


def dimension_reference(entity: Entity, feature: str, fallback: Point | None = None) -> DimensionReference:
    """Create an associative reference after validating its feature token."""
    by_feature = dict(dimension_reference_candidates(entity))
    if feature not in by_feature:
        raise ValueError(f"{entity.kind} does not provide dimension feature {feature!r}.")
    return DimensionReference(by_feature[feature] if fallback is None else fallback, entity.id, feature)


def resolve_dimension_reference(
    reference: DimensionReference,
    entities: Mapping[str, Entity] | Iterable[Entity],
) -> tuple[Point, bool]:
    """Resolve a reference, preserving its last point when geometry is gone."""
    entity_map = entities if isinstance(entities, Mapping) else {item.id: item for item in entities}
    if not reference.entity_id:
        return reference.fallback, True
    entity = entity_map.get(reference.entity_id)
    if entity is None:
        return reference.fallback, False
    by_feature = dict(dimension_reference_candidates(entity))
    point = by_feature.get(reference.feature)
    if point is not None:
        return point, True
    # Rotating a Rectangle intentionally promotes it to a four-point Polyline.
    # Preserve its recorded corner ownership across that representation change.
    if isinstance(entity, Polyline) and reference.feature.startswith("rectangle:corner:"):
        try:
            index = int(reference.feature.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(entity.points):
            return entity.points[index], True
    if isinstance(entity, Polyline) and reference.feature == "rectangle:center" and entity.points:
        return Point(
            sum(point.x for point in entity.points) / len(entity.points),
            sum(point.y for point in entity.points) / len(entity.points),
        ), True
    return reference.fallback, False


def refresh_dimension_fallbacks(
    dimensions: Iterable[DimensionAnnotation],
    entities: Mapping[str, Entity] | Iterable[Entity],
) -> list[DimensionAnnotation]:
    """Record current resolved points so later broken links remain intelligible."""
    entity_map = entities if isinstance(entities, Mapping) else {item.id: item for item in entities}
    result: list[DimensionAnnotation] = []
    for item in dimensions:
        start, start_ok = resolve_dimension_reference(item.start, entity_map)
        end, end_ok = resolve_dimension_reference(item.end, entity_map)
        vertex, vertex_ok = (
            resolve_dimension_reference(item.vertex, entity_map)
            if item.vertex is not None else (None, False)
        )
        result.append(replace(
            item,
            start=replace(item.start, fallback=start) if start_ok else item.start,
            end=replace(item.end, fallback=end) if end_ok else item.end,
            vertex=(
                replace(item.vertex, fallback=vertex)
                if item.vertex is not None and vertex is not None and vertex_ok else item.vertex
            ),
        ))
    return result


def resolve_dimension(
    annotation: DimensionAnnotation,
    entities: Mapping[str, Entity] | Iterable[Entity],
    units: str,
) -> ResolvedDimension:
    """Resolve displayed extension lines, arrow line, text, and measured value."""
    annotation.validate()
    start, start_ok = resolve_dimension_reference(annotation.start, entities)
    end, end_ok = resolve_dimension_reference(annotation.end, entities)
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    direct_length = math.hypot(delta_x, delta_y)

    def extension(reference: Point, target: Point) -> tuple[Point, Point]:
        vector_x, vector_y = target.x - reference.x, target.y - reference.y
        distance = math.hypot(vector_x, vector_y)
        if distance <= 1e-12:
            return reference, target
        unit_x, unit_y = vector_x / distance, vector_y / distance
        gap = min(annotation.extension_offset, distance * 0.45)
        return (
            Point(reference.x + unit_x * gap, reference.y + unit_y * gap),
            Point(target.x + unit_x * annotation.extension_offset * 0.5,
                  target.y + unit_y * annotation.extension_offset * 0.5),
        )

    extension_lines: tuple[tuple[Point, Point], ...] = ()
    arrow_mode = "both"
    path: tuple[Point, ...] = ()
    all_resolved = start_ok and end_ok

    if annotation.kind == "note":
        dimension_start = end
        dimension_end = end
        text_point = end
        value = 0.0
        rotation = 0.0
        label = str(annotation.metadata["text"]).strip()
        arrow_mode = "none"

    elif annotation.kind == "leader":
        if direct_length <= 1e-12:
            raise ValueError("Leader anchor and text landing must be different points.")
        dimension_start = start
        dimension_end = end
        text_point = end
        value = direct_length
        rotation = 0.0
        label = str(annotation.metadata["text"]).strip()
        arrow_mode = "first"

    elif annotation.kind == "angle":
        vertex, vertex_ok = resolve_dimension_reference(annotation.vertex, entities)
        first_x, first_y = start.x - vertex.x, start.y - vertex.y
        second_x, second_y = end.x - vertex.x, end.y - vertex.y
        first_length = math.hypot(first_x, first_y)
        second_length = math.hypot(second_x, second_y)
        if first_length <= 1e-12 or second_length <= 1e-12:
            raise ValueError("Angular dimension ray points must differ from the vertex.")
        first_angle = math.atan2(first_y, first_x)
        second_angle = math.atan2(second_y, second_x)
        counterclockwise = (second_angle - first_angle) % math.tau
        minor_sweep = counterclockwise if counterclockwise <= math.pi else counterclockwise - math.tau
        if abs(minor_sweep) <= 1e-12:
            raise ValueError("Angular dimension rays must point in different directions.")
        angle_mode = str(annotation.metadata.get("angle_mode", "minor")).casefold()
        if angle_mode not in {"minor", "reflex"}:
            raise ValueError("Angular dimensions must explicitly use the minor or reflex angle.")
        sweep = minor_sweep
        if angle_mode == "reflex":
            sweep = minor_sweep - math.tau if minor_sweep > 0 else minor_sweep + math.tau
        radius = abs(annotation.offset)
        if radius <= 1e-12:
            raise ValueError("Angular dimension arc radius must be greater than zero.")
        segment_count = max(8, int(math.ceil(abs(math.degrees(sweep)) / 5.0)))
        path = tuple(
            Point(
                vertex.x + math.cos(first_angle + sweep * index / segment_count) * radius,
                vertex.y + math.sin(first_angle + sweep * index / segment_count) * radius,
            )
            for index in range(segment_count + 1)
        )
        dimension_start = path[0]
        dimension_end = path[-1]
        first_direction = Point(first_x / first_length, first_y / first_length)
        second_direction = Point(second_x / second_length, second_y / second_length)
        extension_lines = (
            (
                Point(vertex.x + first_direction.x * annotation.extension_offset,
                      vertex.y + first_direction.y * annotation.extension_offset),
                Point(vertex.x + first_direction.x * (radius + annotation.extension_offset * 0.5),
                      vertex.y + first_direction.y * (radius + annotation.extension_offset * 0.5)),
            ),
            (
                Point(vertex.x + second_direction.x * annotation.extension_offset,
                      vertex.y + second_direction.y * annotation.extension_offset),
                Point(vertex.x + second_direction.x * (radius + annotation.extension_offset * 0.5),
                      vertex.y + second_direction.y * (radius + annotation.extension_offset * 0.5)),
            ),
        )
        middle_angle = first_angle + sweep / 2.0
        text_radius = radius + annotation.text_height * 0.9
        text_point = Point(
            vertex.x + math.cos(middle_angle) * text_radius,
            vertex.y + math.sin(middle_angle) * text_radius,
        )
        value = abs(math.degrees(sweep))
        rotation = 0.0
        label = f"{value:.{annotation.precision}f}°"
        all_resolved = all_resolved and vertex_ok

    elif annotation.kind == "horizontal":
        if direct_length <= 1e-12:
            raise ValueError("Dimension endpoints currently resolve to the same point.")
        dimension_y = (start.y + end.y) / 2.0 + annotation.offset
        dimension_start = Point(start.x, dimension_y)
        dimension_end = Point(end.x, dimension_y)
        direction = Point(1.0 if end.x >= start.x else -1.0, 0.0)
        normal = Point(0.0, 1.0)
        value = abs(delta_x)
        rotation = 0.0
        text_point = _midpoint(dimension_start, dimension_end)
        label = f"{value:.{annotation.precision}f} {units}"
        extension_lines = (extension(start, dimension_start), extension(end, dimension_end))
    elif annotation.kind == "vertical":
        if direct_length <= 1e-12:
            raise ValueError("Dimension endpoints currently resolve to the same point.")
        dimension_x = (start.x + end.x) / 2.0 + annotation.offset
        dimension_start = Point(dimension_x, start.y)
        dimension_end = Point(dimension_x, end.y)
        direction = Point(0.0, 1.0 if end.y >= start.y else -1.0)
        normal = Point(-1.0, 0.0)
        value = abs(delta_y)
        rotation = 90.0
        text_point = _midpoint(dimension_start, dimension_end)
        label = f"{value:.{annotation.precision}f} {units}"
        extension_lines = (extension(start, dimension_start), extension(end, dimension_end))
    elif annotation.kind == "aligned":
        if direct_length <= 1e-12:
            raise ValueError("Dimension endpoints currently resolve to the same point.")
        direction = Point(delta_x / direct_length, delta_y / direct_length)
        normal = Point(-direction.y, direction.x)
        dimension_start = Point(
            start.x + normal.x * annotation.offset,
            start.y + normal.y * annotation.offset,
        )
        dimension_end = Point(
            end.x + normal.x * annotation.offset,
            end.y + normal.y * annotation.offset,
        )
        value = direct_length
        rotation = math.degrees(math.atan2(delta_y, delta_x))
        text_point = _midpoint(dimension_start, dimension_end)
        label = f"{value:.{annotation.precision}f} {units}"
        extension_lines = (extension(start, dimension_start), extension(end, dimension_end))

    elif annotation.kind == "radius":
        if direct_length <= 1e-12:
            raise ValueError("Dimension endpoints currently resolve to the same point.")
        direction = Point(delta_x / direct_length, delta_y / direct_length)
        dimension_start = start
        dimension_end = end
        value = direct_length
        rotation = math.degrees(math.atan2(delta_y, delta_x))
        arrow_mode = "last"
        text_point = Point(
            end.x + direction.x * annotation.offset,
            end.y + direction.y * annotation.offset,
        )
        label = f"R {value:.{annotation.precision}f} {units}"

    else:  # diameter
        if direct_length <= 1e-12:
            raise ValueError("Dimension endpoints currently resolve to the same point.")
        direction = Point(delta_x / direct_length, delta_y / direct_length)
        dimension_start = start
        dimension_end = end
        value = direct_length
        rotation = math.degrees(math.atan2(delta_y, delta_x))
        text_point = _midpoint(dimension_start, dimension_end)
        label = f"Ø {value:.{annotation.precision}f} {units}"

    if annotation.kind not in {"note"} and value <= 1e-12:
        axis = "X" if annotation.kind == "horizontal" else "Y"
        raise ValueError(
            f"{annotation.kind.title()} dimension requires endpoints with different {axis} coordinates."
        )

    if not all_resolved:
        label = "UNRESOLVED · " + label
    return ResolvedDimension(
        annotation_id=annotation.id,
        start=start,
        end=end,
        dimension_start=dimension_start,
        dimension_end=dimension_end,
        extension_lines=extension_lines,
        text_point=text_point,
        text_rotation=rotation,
        value=value,
        label=label,
        resolved=all_resolved,
        arrow_mode=arrow_mode,
        path=path,
    )


def dimension_bounds(resolved: ResolvedDimension, text_height: float = 0.18) -> tuple[float, float, float, float]:
    points = [
        resolved.start, resolved.end, resolved.dimension_start, resolved.dimension_end,
        resolved.text_point,
        *resolved.path,
        *(point for line in resolved.extension_lines for point in line),
    ]
    margin = max(0.0, float(text_height))
    return (
        min(point.x for point in points) - margin,
        min(point.y for point in points) - margin,
        max(point.x for point in points) + margin,
        max(point.y for point in points) + margin,
    )


def translate_dimension(annotation: DimensionAnnotation, delta_x: float, delta_y: float) -> DimensionAnnotation:
    def moved(reference: DimensionReference) -> DimensionReference:
        return replace(
            reference,
            fallback=Point(reference.fallback.x + delta_x, reference.fallback.y + delta_y),
        )
    return replace(
        annotation,
        start=moved(annotation.start),
        end=moved(annotation.end),
        vertex=moved(annotation.vertex) if annotation.vertex is not None else None,
    )


def scale_dimension(annotation: DimensionAnnotation, factor: float) -> DimensionAnnotation:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Dimension scale factor must be a positive finite number.")
    def scaled(reference: DimensionReference) -> DimensionReference:
        return replace(
            reference,
            fallback=Point(reference.fallback.x * factor, reference.fallback.y * factor),
        )
    return replace(
        annotation,
        start=scaled(annotation.start),
        end=scaled(annotation.end),
        vertex=scaled(annotation.vertex) if annotation.vertex is not None else None,
        offset=annotation.offset * factor,
        text_height=annotation.text_height * factor,
        extension_offset=annotation.extension_offset * factor,
    )


def _midpoint(first: Point, second: Point) -> Point:
    return Point((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
