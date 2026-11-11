"""Scaled, non-machining shop-drawing PDF rendering.

This renderer consumes project geometry plus the dedicated dimension collection.
It never creates entities or operations, so a printed annotation cannot become
controller motion. Model measurements remain in project units; only the final
paper transform and legibility-constrained annotation graphics use points.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

from .dimensions import dimension_bounds, resolve_dimension
from .layout import entity_bounds
from .model import CamProject, Circle, Line, Point, Polyline, Rectangle, TextEntity
from .appearance import ColorValue
from .units import unit_scale


PAGE_SIZES_IN = {
    "Letter": (8.5, 11.0),
    "Tabloid": (11.0, 17.0),
    "ARCH C": (18.0, 24.0),
    "ARCH D": (24.0, 36.0),
}
SHOP_DRAWING_ORIENTATIONS = ("landscape", "portrait")
SHOP_DRAWING_SCALE_MODES = ("fit", "actual", "custom")


@dataclass(frozen=True)
class ShopDrawingSettings:
    page_size: str = "Tabloid"
    orientation: str = "landscape"
    scale_mode: str = "fit"
    scale_denominator: float = 1.0
    margin_in: float = 0.4

    def validate(self) -> None:
        if self.page_size not in PAGE_SIZES_IN:
            raise ValueError(f"Unsupported shop-drawing page size: {self.page_size!r}.")
        if self.orientation not in SHOP_DRAWING_ORIENTATIONS:
            raise ValueError(f"Unsupported shop-drawing orientation: {self.orientation!r}.")
        if self.scale_mode not in SHOP_DRAWING_SCALE_MODES:
            raise ValueError(f"Unsupported shop-drawing scale mode: {self.scale_mode!r}.")
        if not math.isfinite(self.scale_denominator) or self.scale_denominator <= 0:
            raise ValueError("Shop-drawing scale denominator must be a positive finite number.")
        if not math.isfinite(self.margin_in) or not 0.2 <= self.margin_in <= 2.0:
            raise ValueError("Shop-drawing margin must be between 0.2 and 2 inches.")


@dataclass(frozen=True)
class ShopDrawingPage:
    label: str
    project: CamProject
    width: float
    height: float


@dataclass(frozen=True)
class ShopDrawingPageResult:
    label: str
    page_width_in: float
    page_height_in: float
    scale_denominator: float
    paper_points_per_model_inch: float
    bounds_in: tuple[float, float, float, float]
    unresolved_dimension_count: int


@dataclass(frozen=True)
class ShopDrawingRenderResult:
    path: Path
    pages: tuple[ShopDrawingPageResult, ...]


def page_dimensions_in(settings: ShopDrawingSettings) -> tuple[float, float]:
    settings.validate()
    width, height = PAGE_SIZES_IN[settings.page_size]
    if settings.orientation == "landscape":
        return max(width, height), min(width, height)
    return min(width, height), max(width, height)


def shop_drawing_bounds(page: ShopDrawingPage) -> tuple[float, float, float, float]:
    """Bounds in project units, including the plate and displayed dimensions."""
    boxes: list[tuple[float, float, float, float]] = [(0.0, 0.0, page.width, page.height)]
    visible_entities = [
        entity for entity in page.project.entities
        if not bool(entity.metadata.get("annotation_hidden", False))
    ]
    for entity in visible_entities:
        box = entity_bounds(entity)
        if box is not None:
            boxes.append(box)
    for annotation in page.project.dimensions:
        try:
            resolved = resolve_dimension(annotation, page.project.entities, page.project.units)
        except ValueError:
            continue
        boxes.append(dimension_bounds(resolved, annotation.text_height))
    return (
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    )


def scale_label(denominator: float) -> str:
    if math.isclose(denominator, 1.0, rel_tol=0, abs_tol=1e-6):
        return "1:1"
    if denominator >= 1.0:
        value = f"{denominator:.3f}".rstrip("0").rstrip(".")
        return f"1:{value}"
    value = f"{1.0 / denominator:.3f}".rstrip("0").rstrip(".")
    return f"{value}:1"


def render_shop_drawing_pdf(
    path: Path,
    title: str,
    pages: Iterable[ShopDrawingPage],
    settings: ShopDrawingSettings,
) -> ShopDrawingRenderResult:
    """Write one scaled PDF page per plate and return physical page metrics."""
    settings.validate()
    page_list = tuple(pages)
    if not page_list:
        raise ValueError("A shop drawing requires at least one plate page.")
    try:
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas as reportlab_canvas
    except ImportError as exc:  # pragma: no cover - deployment diagnostic
        raise RuntimeError("Shop-drawing PDF output requires the bundled reportlab package.") from exc

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    page_width_in, page_height_in = page_dimensions_in(settings)
    page_size_points = (page_width_in * 72.0, page_height_in * 72.0)
    pdf = reportlab_canvas.Canvas(str(destination), pagesize=page_size_points, pageCompression=1)
    pdf.setTitle(title)
    pdf.setCreator("CAM Workbench shop-drawing renderer")
    results: list[ShopDrawingPageResult] = []

    for page_number, page in enumerate(page_list, start=1):
        if page.width <= 0 or page.height <= 0:
            raise ValueError(f"{page.label} requires positive drawing dimensions.")
        factor_to_inches = unit_scale(page.project.units, "in")
        model_bounds = shop_drawing_bounds(page)
        bounds_in = tuple(value * factor_to_inches for value in model_bounds)

        left = settings.margin_in * 72.0
        right = page_size_points[0] - settings.margin_in * 72.0
        bottom = (settings.margin_in + 0.28) * 72.0
        top = page_size_points[1] - (settings.margin_in + 0.63) * 72.0
        usable_width = right - left
        usable_height = top - bottom
        bounds_width = max(bounds_in[2] - bounds_in[0], 1e-9)
        bounds_height = max(bounds_in[3] - bounds_in[1], 1e-9)
        fit_ratio = min(1.0, usable_width / (bounds_width * 72.0), usable_height / (bounds_height * 72.0))
        if settings.scale_mode == "fit":
            paper_to_model = fit_ratio
        elif settings.scale_mode == "actual":
            paper_to_model = 1.0
        else:
            paper_to_model = 1.0 / settings.scale_denominator
        required_width = bounds_width * 72.0 * paper_to_model
        required_height = bounds_height * 72.0 * paper_to_model
        if required_width > usable_width + 0.01 or required_height > usable_height + 0.01:
            needed = 1.0 / fit_ratio
            raise ValueError(
                f"{page.label} does not fit {settings.page_size} {settings.orientation} at "
                f"{scale_label(1.0 / paper_to_model)}. Use Fit or at least 1:{needed:.3f}."
            )
        denominator = 1.0 / paper_to_model
        points_per_project_unit = 72.0 * paper_to_model * factor_to_inches
        drawing_width = (model_bounds[2] - model_bounds[0]) * points_per_project_unit
        drawing_height = (model_bounds[3] - model_bounds[1]) * points_per_project_unit
        drawing_left = left + (usable_width - drawing_width) / 2.0
        drawing_bottom = bottom + (usable_height - drawing_height) / 2.0

        def location(point: Point) -> tuple[float, float]:
            return (
                drawing_left + (point.x - model_bounds[0]) * points_per_project_unit,
                drawing_bottom + (point.y - model_bounds[1]) * points_per_project_unit,
            )

        unresolved_count = 0
        for annotation in page.project.dimensions:
            try:
                if not resolve_dimension(annotation, page.project.entities, page.project.units).resolved:
                    unresolved_count += 1
            except ValueError:
                unresolved_count += 1

        pdf.setFillColorRGB(0.10, 0.14, 0.17)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(left, page_size_points[1] - settings.margin_in * 72.0 - 10, title)
        pdf.setFont("Helvetica", 8)
        details = (
            f"{page.label} | {settings.page_size} {settings.orientation} "
            f"({page_width_in:g} x {page_height_in:g} in) | Scale {scale_label(denominator)} "
            f"({settings.scale_mode}) | print at 100%"
        )
        pdf.drawString(left, page_size_points[1] - settings.margin_in * 72.0 - 23, details)
        physical_note = f"1.000 in on paper = {denominator:.3f} in in the model"
        pdf.drawRightString(right, page_size_points[1] - settings.margin_in * 72.0 - 23, physical_note)
        if unresolved_count:
            pdf.setFillColorRGB(0.78, 0.12, 0.12)
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawString(
                left, page_size_points[1] - settings.margin_in * 72.0 - 35,
                f"WARNING: {unresolved_count} unresolved dimension reference(s) - verify before fabrication.",
            )

        # Plate boundary is a shop reference, not modeled geometry.
        pdf.setStrokeColorRGB(0.78, 0.16, 0.16)
        pdf.setLineWidth(0.8)
        plate_a = location(Point(0, 0))
        plate_b = location(Point(page.width, page.height))
        pdf.rect(
            min(plate_a[0], plate_b[0]), min(plate_a[1], plate_b[1]),
            abs(plate_b[0] - plate_a[0]), abs(plate_b[1] - plate_a[1]),
            stroke=1, fill=0,
        )

        for entity in page.project.entities:
            if bool(entity.metadata.get("annotation_hidden", False)):
                continue
            _draw_entity(pdf, entity, location, points_per_project_unit)

        for annotation in page.project.dimensions:
            try:
                resolved = resolve_dimension(annotation, page.project.entities, page.project.units)
            except ValueError:
                continue
            color = (0.78, 0.12, 0.12) if not resolved.resolved else (0.05, 0.34, 0.62)
            pdf.setStrokeColorRGB(*color)
            pdf.setFillColorRGB(*color)
            pdf.setLineWidth(0.75)
            pdf.setDash(4, 2) if not resolved.resolved else pdf.setDash()
            for first, second in resolved.extension_lines:
                pdf.line(*location(first), *location(second))
            line_start = location(resolved.dimension_start)
            line_end = location(resolved.dimension_end)
            paper_path = tuple(location(point) for point in resolved.path)
            if paper_path:
                path = pdf.beginPath()
                path.moveTo(*paper_path[0])
                for point in paper_path[1:]:
                    path.lineTo(*point)
                pdf.drawPath(path, stroke=1, fill=0)
                if resolved.arrow_mode in {"first", "both"} and len(paper_path) > 1:
                    _draw_arrowhead(pdf, paper_path[0], paper_path[1])
                if resolved.arrow_mode in {"last", "both"} and len(paper_path) > 1:
                    _draw_arrowhead(pdf, paper_path[-1], paper_path[-2])
            elif resolved.dimension_start.distance_to(resolved.dimension_end) > 1e-12:
                pdf.line(*line_start, *line_end)
                if resolved.arrow_mode in {"first", "both"}:
                    _draw_arrowhead(pdf, line_start, line_end)
                if resolved.arrow_mode in {"last", "both"}:
                    _draw_arrowhead(pdf, line_end, line_start)

            scaled_font = annotation.text_height * points_per_project_unit
            font_size = max(8.0, min(12.0, scaled_font))
            rotation = resolved.text_rotation
            while rotation > 90:
                rotation -= 180
            while rotation < -90:
                rotation += 180
            text_x, text_y = location(resolved.text_point)
            text_width = stringWidth(resolved.label, "Helvetica-Bold", font_size)
            pdf.saveState()
            pdf.translate(text_x, text_y)
            pdf.rotate(rotation)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.rect(-text_width / 2 - 3, -font_size * 0.38, text_width + 6, font_size * 1.15, fill=1, stroke=0)
            pdf.setFillColorRGB(*color)
            pdf.setFont("Helvetica-Bold", font_size)
            pdf.drawCentredString(0, -font_size * 0.18, resolved.label)
            pdf.restoreState()
            pdf.setDash()

        pdf.setStrokeColorRGB(0.65, 0.68, 0.70)
        pdf.setLineWidth(0.45)
        pdf.line(left, bottom - 10, right, bottom - 10)
        pdf.setFillColorRGB(0.25, 0.29, 0.32)
        pdf.setFont("Helvetica", 7)
        footer = "Reference drawing only. Dimensions are annotations and are never controller motion."
        pdf.drawString(left, bottom - 22, footer)
        pdf.drawRightString(right, bottom - 22, f"Page {page_number} of {len(page_list)}")

        results.append(ShopDrawingPageResult(
            label=page.label,
            page_width_in=page_width_in,
            page_height_in=page_height_in,
            scale_denominator=denominator,
            paper_points_per_model_inch=72.0 * paper_to_model,
            bounds_in=bounds_in,
            unresolved_dimension_count=unresolved_count,
        ))
        pdf.showPage()

    pdf.save()
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise OSError(f"Shop-drawing renderer did not create {destination}.")
    return ShopDrawingRenderResult(destination, tuple(results))


def _draw_entity(pdf, entity, location, points_per_project_unit: float) -> None:
    appearance = entity.appearance
    stroke = appearance is None or appearance.stroke_enabled
    fill = bool(appearance is not None and appearance.fill_enabled)
    if not stroke and not fill:
        return
    if appearance is None:
        pdf.setStrokeColorRGB(0.10, 0.14, 0.17)
        pdf.setFillColorRGB(0.10, 0.14, 0.17)
        pdf.setLineWidth(0.65)
    else:
        _set_pdf_color(pdf, appearance.stroke, stroke=True)
        _set_pdf_color(pdf, appearance.fill, stroke=False)
        pdf.setLineWidth(max(0.25, appearance.stroke_width * points_per_project_unit) if appearance.stroke_width else 0.65)
    pdf.setDash()
    if isinstance(entity, Line):
        if stroke:
            pdf.line(*location(entity.start), *location(entity.end))
    elif isinstance(entity, Rectangle):
        first, second = location(entity.corner_a), location(entity.corner_b)
        pdf.rect(
            min(first[0], second[0]), min(first[1], second[1]),
            abs(second[0] - first[0]), abs(second[1] - first[1]), stroke=int(stroke), fill=int(fill),
        )
    elif isinstance(entity, Circle):
        center = location(entity.center)
        pdf.circle(center[0], center[1], entity.radius * points_per_project_unit, stroke=int(stroke), fill=int(fill))
    elif isinstance(entity, Polyline) and len(entity.points) >= 2:
        path = pdf.beginPath()
        start = location(entity.points[0])
        path.moveTo(*start)
        for point in entity.points[1:]:
            path.lineTo(*location(point))
        if entity.closed:
            path.close()
        pdf.drawPath(path, stroke=int(stroke), fill=int(fill and entity.closed))
    elif isinstance(entity, TextEntity) and entity.text.strip():
        if appearance is not None and not fill and stroke:
            _set_pdf_color(pdf, appearance.stroke, stroke=False)
        size = max(6.0, entity.height * points_per_project_unit)
        pdf.saveState()
        pdf.translate(*location(entity.insertion))
        pdf.rotate(entity.rotation)
        pdf.setFont("Helvetica", size)
        lines = entity.text.splitlines() or [entity.text]
        for index, text in enumerate(lines):
            width = pdf.stringWidth(text, "Helvetica", size) * entity.width_factor
            offset = {"center": -width / 2.0, "right": -width}.get(entity.alignment, 0.0)
            pdf.drawString(offset, -index * size * 1.2, text)
        pdf.restoreState()


def _set_pdf_color(pdf, color: ColorValue, *, stroke: bool) -> None:
    """Preserve device CMYK in PDF; named/RGB colors use stored preview RGB."""
    if color.model == "cmyk":
        values = tuple(value / 100.0 for value in color.channels)
        (pdf.setStrokeColorCMYK if stroke else pdf.setFillColorCMYK)(*values)
        return
    values = tuple(value / 255.0 for value in color.preview_rgb)
    (pdf.setStrokeColorRGB if stroke else pdf.setFillColorRGB)(*values)


def _draw_arrowhead(pdf, tip: tuple[float, float], toward: tuple[float, float]) -> None:
    delta_x, delta_y = toward[0] - tip[0], toward[1] - tip[1]
    length = math.hypot(delta_x, delta_y)
    if length <= 1e-9:
        return
    unit_x, unit_y = delta_x / length, delta_y / length
    normal_x, normal_y = -unit_y, unit_x
    arrow_length, half_width = 6.5, 2.5
    base_x, base_y = tip[0] + unit_x * arrow_length, tip[1] + unit_y * arrow_length
    path = pdf.beginPath()
    path.moveTo(*tip)
    path.lineTo(base_x + normal_x * half_width, base_y + normal_y * half_width)
    path.lineTo(base_x - normal_x * half_width, base_y - normal_y * half_width)
    path.close()
    pdf.drawPath(path, stroke=0, fill=1)
