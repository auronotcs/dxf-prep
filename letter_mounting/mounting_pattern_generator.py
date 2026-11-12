from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
from ezdxf import bbox
from ezdxf.enums import TextEntityAlignment
from ezdxf.path import make_path
from PIL import Image, ImageDraw, ImageFont


GUIDE_LAYER = "PATTERN_GUIDES"
LABEL_LAYER = "PATTERN_LABEL"
GUIDE_LINETYPE = "PATTERN_DASHED"


def entity_points(entity, tolerance: float = 0.002):
    try:
        return [(point.x, point.y) for point in make_path(entity).flattening(distance=tolerance)]
    except Exception:
        return []


def geometry_bounds(entities) -> tuple[float, float, float, float]:
    extents = bbox.extents(entities, fast=False)
    if not extents.has_data:
        raise ValueError("The DXF has no drawable geometry.")
    return extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y


def strip_handmade_pattern(doc) -> int:
    """Remove the frame, center cross, and right-side outlined label from the supplied example."""
    modelspace = doc.modelspace()
    records = []
    for entity in list(modelspace):
        points = entity_points(entity)
        if len(points) < 2:
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        records.append((entity, points, min(xs), min(ys), max(xs), max(ys)))

    frame_candidates = []
    for entity, points, min_x, min_y, max_x, max_y in records:
        width, height = max_x - min_x, max_y - min_y
        closed = math.dist(points[0], points[-1]) <= 0.01
        if closed and width >= 20.0 and height >= 20.0:
            frame_candidates.append((width * height, entity, (min_x, min_y, max_x, max_y)))
    if not frame_candidates:
        raise ValueError("No handmade mounting-pattern frame was found to replace.")

    _, frame, frame_bounds = max(frame_candidates, key=lambda item: item[0])
    min_x, min_y, max_x, max_y = frame_bounds
    width, height = max_x - min_x, max_y - min_y
    to_remove = {frame}

    for entity, points, entity_min_x, entity_min_y, entity_max_x, entity_max_y in records:
        entity_width = entity_max_x - entity_min_x
        entity_height = entity_max_y - entity_min_y
        horizontal_guide = (
            entity_width >= width * 0.95
            and entity_height <= 0.01
            and min_y - 0.02 <= entity_min_y <= max_y + 0.02
        )
        vertical_guide = (
            entity_height >= height * 0.95
            and entity_width <= 0.01
            and min_x - 0.02 <= entity_min_x <= max_x + 0.02
        )
        outlined_label = entity_min_x > max_x + 0.05
        if horizontal_guide or vertical_guide or outlined_label:
            to_remove.add(entity)

    for entity in to_remove:
        modelspace.delete_entity(entity)
    return len(to_remove)


def ensure_pattern_resources(doc) -> None:
    if doc.dxfversion < "AC1015":
        doc.dxfversion = "AC1015"
    if GUIDE_LINETYPE not in doc.linetypes:
        doc.linetypes.add(
            GUIDE_LINETYPE,
            description="Mounting pattern dashed line",
            pattern=[0.50, 0.25, -0.25],
        )
    if GUIDE_LAYER not in doc.layers:
        doc.layers.add(GUIDE_LAYER, color=8, linetype="Continuous")
    if LABEL_LAYER not in doc.layers:
        doc.layers.add(LABEL_LAYER, color=7)
    if "ARIAL" not in doc.styles:
        doc.styles.add("ARIAL", font="arial.ttf")
    doc.header["$LTSCALE"] = 1.0


def add_pattern_guides(
    doc,
    label: str,
    label_height: float = 1.0,
    label_offset: float = 0.75,
    dash_length: float = 0.25,
    dash_gap: float = 0.25,
    physical_dashes: bool = True,
) -> tuple[tuple[float, float, float, float], list]:
    modelspace = doc.modelspace()
    min_x, min_y, max_x, max_y = geometry_bounds(modelspace)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    guide_attributes = {
        "layer": GUIDE_LAYER,
        "linetype": "Continuous",
        "ltscale": 1.0,
        "color": 256,
    }

    construction_lines = [
        ((min_x, min_y), (max_x, min_y)),
        ((max_x, min_y), (max_x, max_y)),
        ((max_x, max_y), (min_x, max_y)),
        ((min_x, max_y), (min_x, min_y)),
        ((min_x, center_y), (max_x, center_y)),
        ((center_x, min_y), (center_x, max_y)),
    ]
    guides = []
    for start, end in construction_lines:
        if physical_dashes:
            guides.extend(
                modelspace.add_line(dash_start, dash_end, dxfattribs=guide_attributes)
                for dash_start, dash_end in dashed_segments(start, end, dash_length, dash_gap)
            )
        else:
            guides.append(modelspace.add_line(start, end, dxfattribs=guide_attributes))
    text = modelspace.add_text(
        label.upper(),
        height=label_height,
        dxfattribs={
            "layer": LABEL_LAYER,
            "style": "ARIAL",
            "rotation": 90.0,
            "color": 256,
        },
    )
    text.set_placement(
        (max_x + label_offset + label_height / 2, center_y),
        align=TextEntityAlignment.MIDDLE_CENTER,
    )

    group = doc.groups.get("MOUNTING_PATTERN")
    if group is None:
        group = doc.groups.new(name="MOUNTING_PATTERN")
    group.extend(guides + [text])
    return (min_x, min_y, max_x, max_y), guides + [text]


def dashed_segments(start, end, dash: float = 0.25, gap: float = 0.25):
    if dash <= 0 or gap < 0:
        raise ValueError("Dash length must be positive and dash gap cannot be negative.")
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return []
    ux, uy = dx / length, dy / length
    segments = []
    cursor = 0.0
    while cursor < length:
        finish = min(cursor + dash, length)
        segments.append(
            (
                (start[0] + ux * cursor, start[1] + uy * cursor),
                (start[0] + ux * finish, start[1] + uy * finish),
            )
        )
        cursor += dash + gap
    return segments


def render_preview(
    doc,
    output: Path,
    artwork_bounds: tuple[float, float, float, float],
    label: str,
    label_height: float,
    label_offset: float,
    physical_dashes: bool = True,
    dash_length: float = 0.25,
    dash_gap: float = 0.25,
    scale: float = 5.0,
) -> None:
    min_x, min_y, max_x, max_y = artwork_bounds
    label_allowance = max(label_height * 2.5, 2.5)
    render_max_x = max_x + label_offset + label_allowance
    margin = 30
    width = round((render_max_x - min_x) * scale) + margin * 2
    height = round((max_y - min_y) * scale) + margin * 2
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    def transform(point):
        x, y = point
        return (
            margin + round((x - min_x) * scale),
            margin + round((max_y - y) * scale),
        )

    colors = {
        "KEYHOLES": "#159447",
        "ELECTRICAL_HOLES": "#1769aa",
        "MOUNTING_HOLES": "#7b1fa2",
    }
    for entity in doc.modelspace():
        if entity.dxf.layer in {GUIDE_LAYER, LABEL_LAYER}:
            continue
        points = entity_points(entity)
        if len(points) >= 2:
            draw.line(
                [transform(point) for point in points],
                fill=colors.get(entity.dxf.layer, "#d7191c"),
                width=2,
                joint="curve",
            )

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    guide_lines = [
        ((min_x, min_y), (max_x, min_y)),
        ((max_x, min_y), (max_x, max_y)),
        ((max_x, max_y), (min_x, max_y)),
        ((min_x, max_y), (min_x, min_y)),
        ((min_x, center_y), (max_x, center_y)),
        ((center_x, min_y), (center_x, max_y)),
    ]
    for start, end in guide_lines:
        if physical_dashes:
            for dash_start, dash_end in dashed_segments(start, end, dash_length, dash_gap):
                draw.line((transform(dash_start), transform(dash_end)), fill="#50545a", width=2)
        else:
            draw.line((transform(start), transform(end)), fill="#50545a", width=2)

    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), max(8, round(label_height * scale)))
    text_box = draw.textbbox((0, 0), label.upper(), font=font)
    text_image = Image.new(
        "RGBA",
        (text_box[2] - text_box[0] + 8, text_box[3] - text_box[1] + 8),
        (255, 255, 255, 0),
    )
    text_draw = ImageDraw.Draw(text_image)
    text_draw.text((4 - text_box[0], 4 - text_box[1]), label.upper(), fill="#20252b", font=font)
    text_image = text_image.rotate(90, expand=True)
    label_center = transform((max_x + label_offset + label_height / 2, center_y))
    image.paste(
        text_image,
        (
            round(label_center[0] - text_image.width / 2),
            round(label_center[1] - text_image.height / 2),
        ),
        text_image,
    )
    image.save(output)


def generate_pattern(
    source: Path,
    output: Path,
    preview: Path,
    label: str,
    label_height: float,
    label_offset: float,
    strip_example: bool,
    dash_length: float = 0.25,
    dash_gap: float = 0.25,
    physical_dashes: bool = True,
) -> None:
    doc = ezdxf.readfile(source)
    removed = strip_handmade_pattern(doc) if strip_example else 0
    ensure_pattern_resources(doc)
    artwork_bounds, _ = add_pattern_guides(
        doc,
        label,
        label_height,
        label_offset,
        dash_length,
        dash_gap,
        physical_dashes,
    )
    doc.saveas(output)
    render_preview(
        doc,
        preview,
        artwork_bounds,
        label,
        label_height,
        label_offset,
        physical_dashes,
        dash_length,
        dash_gap,
    )
    min_x, min_y, max_x, max_y = artwork_bounds
    print(f"Wrote: {output}")
    print(f"Preview: {preview}")
    print(f"Artwork bounds: {max_x - min_x:.3f} x {max_y - min_y:.3f}")
    print(f"Center: ({(min_x + max_x) / 2:.3f}, {(min_y + max_y) / 2:.3f})")
    print(f"Label: {label.upper()}")
    if removed:
        print(f"Removed {removed} handmade guide/label entities from the working copy.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a dashed frame, center cross, and right-side Arial label.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--label-height", type=float, default=1.0)
    parser.add_argument("--label-offset", type=float, default=0.75)
    parser.add_argument("--dash-length", type=float, default=0.25)
    parser.add_argument("--dash-gap", type=float, default=0.25)
    parser.add_argument(
        "--compact-guides",
        action="store_true",
        help="Write six continuous guide entities instead of physical dash segments.",
    )
    parser.add_argument("--strip-example", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    output = args.output or args.source.with_name(f"{args.source.stem} - mounting pattern.dxf")
    preview = args.preview or output.with_suffix(".png")
    generate_pattern(
        args.source,
        output,
        preview,
        args.label,
        args.label_height,
        args.label_offset,
        args.strip_example,
        args.dash_length,
        args.dash_gap,
        not args.compact_guides,
    )


if __name__ == "__main__":
    main()
