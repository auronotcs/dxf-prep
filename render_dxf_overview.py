from __future__ import annotations

import sys
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
from ezdxf.path import make_path
from PIL import Image, ImageDraw, ImageFont


TILE_COLUMNS = 4
TILE_WIDTH = 480
TILE_HEIGHT = 390
PIN_PREVIEW_COLORS = {
    "LETTER_PIN_HOLES": "#7b1fa2",
    "BACKER_CLEARANCE_HOLES": "#1769aa",
}


def _pin_center(entity):
    if entity.dxftype() != "CIRCLE" or entity.dxf.layer not in PIN_PREVIEW_COLORS:
        return None
    center = entity.dxf.center
    return (float(center.x), float(center.y))


def _draw_pin_markers(draw, transform, markers, large: bool = False):
    """Keep sub-pixel production holes visible in previews without changing DXF geometry."""
    outer_radius = 5 if large else 3
    inner_radius = 3 if large else 2
    ordered = sorted(
        markers,
        key=lambda marker: 0 if marker[1] == "BACKER_CLEARANCE_HOLES" else 1,
    )
    for point, layer in ordered:
        x, y = transform(point)
        radius = outer_radius if layer == "BACKER_CLEARANCE_HOLES" else inner_radius
        color = PIN_PREVIEW_COLORS[layer]
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            outline=color,
            width=2,
        )
        draw.point((x, y), fill=color)


def render(source: Path, output: Path, scale: float = 4.0) -> dict:
    doc = ezdxf.readfile(source)
    segments = []
    all_points = []
    pin_markers = []
    for entity in doc.modelspace():
        try:
            points = [(point.x, point.y) for point in make_path(entity).flattening(distance=0.002)]
        except Exception:
            continue
        if len(points) >= 2:
            segments.append((points, entity.dxf.layer))
            all_points.extend(points)
            center = _pin_center(entity)
            if center is not None:
                pin_markers.append((center, entity.dxf.layer))
    if not all_points:
        raise ValueError("The DXF has no drawable line or curve geometry to preview.")
    min_x = min(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_x = max(point[0] for point in all_points)
    max_y = max(point[1] for point in all_points)
    margin = 30
    image = Image.new(
        "RGB",
        (round((max_x - min_x) * scale) + margin * 2, round((max_y - min_y) * scale) + margin * 2),
        "white",
    )
    draw = ImageDraw.Draw(image)

    def transform(point):
        x, y = point
        return (margin + round((x - min_x) * scale), margin + round((max_y - y) * scale))

    colors = {
        "KEYHOLES": "#159447",
        "ELECTRICAL_HOLES": "#1769aa",
        "MOUNTING_HOLES": "#7b1fa2",
        "LETTER_PIN_HOLES": "#7b1fa2",
        "BACKER_CLEARANCE_HOLES": "#1769aa",
    }
    for points, layer in segments:
        draw.line(
            [transform(point) for point in points],
            fill=colors.get(layer, "#d7191c"),
            width=2,
            joint="curve",
        )
    _draw_pin_markers(draw, transform, pin_markers)
    image.save(output)
    return {
        "bounds": (min_x, min_y, max_x, max_y),
        "scale": scale,
        "margin": margin,
        "size": image.size,
    }


def render_tile_review(
    source: Path,
    output: Path,
    items,
    details_directory: Path,
    scale: float = 8.0,
    led_guides=None,
) -> dict:
    """Render a contact sheet plus one zoomable image per separated piece."""
    output = Path(output)
    details_directory = Path(details_directory)
    details_directory.mkdir(parents=True, exist_ok=True)
    document = ezdxf.readfile(source)
    groups = sorted(
        (
            (name, group)
            for name, group in document.groups
            if name.upper().startswith(("LETTER_", "BACK_", "BACKER_"))
        ),
        key=lambda value: value[0],
    )
    if len(groups) < len(items):
        raise ValueError(
            f"The generated DXF has {len(groups)} inspectable groups for {len(items)} review tiles."
        )

    rows = max(1, math.ceil(len(items) / TILE_COLUMNS))
    sheet = Image.new("RGB", (TILE_COLUMNS * TILE_WIDTH, rows * TILE_HEIGHT), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=22)
    details = []
    colors = {
        "KEYHOLES": "#159447",
        "ELECTRICAL_HOLES": "#1769aa",
        "MOUNTING_HOLES": "#7b1fa2",
        "LETTER_PIN_HOLES": "#7b1fa2",
        "BACKER_CLEARANCE_HOLES": "#1769aa",
    }

    def group_segments(group):
        segments = []
        markers = []
        for entity in group:
            try:
                points = [(point.x, point.y) for point in make_path(entity).flattening(distance=0.002)]
            except Exception:
                continue
            if len(points) >= 2:
                segments.append((points, entity.dxf.layer))
                center = _pin_center(entity)
                if center is not None:
                    markers.append((center, entity.dxf.layer))
        return segments, markers

    def draw_isolated(target_draw, rectangle, segments, pin_markers, guide_points=None):
        all_points = [point for points, _layer in segments for point in points]
        if not all_points:
            return
        min_x = min(point[0] for point in all_points)
        min_y = min(point[1] for point in all_points)
        max_x = max(point[0] for point in all_points)
        max_y = max(point[1] for point in all_points)
        left, top, right, bottom = rectangle
        available_width = max(1, right - left)
        available_height = max(1, bottom - top)
        item_scale = min(
            available_width / max(max_x - min_x, 0.01),
            available_height / max(max_y - min_y, 0.01),
        )
        offset_x = left + (available_width - (max_x - min_x) * item_scale) / 2
        offset_y = top + (available_height - (max_y - min_y) * item_scale) / 2

        def transform(point):
            return (
                round(offset_x + (point[0] - min_x) * item_scale),
                round(offset_y + (max_y - point[1]) * item_scale),
            )

        width = 3 if available_width > 600 else 2
        guide_radius = 2 if available_width > 600 else 1
        for point in guide_points or ():
            x, y = transform(point)
            target_draw.ellipse(
                (x - guide_radius, y - guide_radius, x + guide_radius, y + guide_radius),
                fill="#d69a00",
            )
        for points, layer in segments:
            target_draw.line(
                [transform(point) for point in points],
                fill=colors.get(layer, "#d7191c"),
                width=width,
                joint="curve",
            )
        _draw_pin_markers(
            target_draw,
            transform,
            pin_markers,
            large=available_width > 600,
        )

    for position, item in enumerate(items):
        label = str(item[0])
        segments, pin_markers = group_segments(groups[position][1])
        guide_points = led_guides[position] if led_guides and position < len(led_guides) else None

        detail = Image.new("RGB", (960, 760), "white")
        detail_draw = ImageDraw.Draw(detail)
        detail_draw.text((24, 18), f"{position + 1:02d}  {label}", fill="#20252b", font=font)
        draw_isolated(
            detail_draw,
            (30, 62, 930, 742),
            segments,
            pin_markers,
            guide_points,
        )
        detail_path = details_directory / f"tile-{position + 1:02d}.png"
        detail.save(detail_path)
        details.append({"index": position + 1, "label": label, "path": detail_path})

        column, row = position % TILE_COLUMNS, position // TILE_COLUMNS
        left, top = column * TILE_WIDTH, row * TILE_HEIGHT
        draw.rectangle((left, top, left + TILE_WIDTH - 1, top + TILE_HEIGHT - 1), outline="#d8dde3", width=2)
        draw.text((left + 14, top + 10), f"{position + 1:02d}  {label}", fill="#20252b", font=font)
        draw_isolated(
            draw,
            (left + 30, top + 58, left + TILE_WIDTH - 30, top + TILE_HEIGHT - 34),
            segments,
            pin_markers,
            guide_points,
        )

    sheet.save(output)
    return {
        "sheet": output,
        "details": details,
        "columns": TILE_COLUMNS,
        "cell_width": TILE_WIDTH,
        "cell_height": TILE_HEIGHT,
    }


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        source = Path(argument)
        render(source, source.with_name(f"{source.stem} - overview.png"))
