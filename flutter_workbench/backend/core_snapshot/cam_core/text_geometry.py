"""Font resolution and explicit text-to-machining geometry conversion.

Editable :class:`~cam_core.model.TextEntity` objects are annotations.  These
helpers are the only bridge that turns them into cutter-capable geometry, so a
DXF note can never become motion merely because it was imported or selected.
"""

from __future__ import annotations

import copy

from collections import defaultdict
from dataclasses import dataclass
import math
import os
from pathlib import Path

from .model import Line, Point, Polyline, TextEntity, new_id


@dataclass(frozen=True)
class FontResolution:
    requested_family: str
    resolved_family: str
    path: Path | None
    substituted: bool


_WINDOWS_FONT_FILES = {
    "arial": "arial.ttf",
    "arial narrow": "arialn.ttf",
    "calibri": "calibri.ttf",
    "cambria": "cambria.ttc",
    "courier new": "cour.ttf",
    "segoe ui": "segoeui.ttf",
    "tahoma": "tahoma.ttf",
    "times new roman": "times.ttf",
    "trebuchet ms": "trebuc.ttf",
    "verdana": "verdana.ttf",
}


def resolve_font(family: str) -> FontResolution:
    """Resolve a common Windows font and report any explicit substitution."""
    requested = (family or "Arial").strip() or "Arial"
    direct = Path(requested)
    if direct.is_file():
        return FontResolution(requested, direct.stem, direct, False)
    font_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    preferred = _WINDOWS_FONT_FILES.get(requested.casefold())
    if preferred and (font_root / preferred).is_file():
        return FontResolution(requested, requested, font_root / preferred, False)
    normalized = "".join(character for character in requested.casefold() if character.isalnum())
    if font_root.is_dir():
        for path in font_root.iterdir():
            if path.suffix.casefold() not in {".ttf", ".ttc", ".otf"}:
                continue
            stem = "".join(character for character in path.stem.casefold() if character.isalnum())
            if normalized and (stem == normalized or stem.startswith(normalized)):
                return FontResolution(requested, path.stem, path, False)
    for fallback_family in ("Arial", "Segoe UI", "Calibri"):
        filename = _WINDOWS_FONT_FILES[fallback_family.casefold()]
        path = font_root / filename
        if path.is_file():
            return FontResolution(requested, fallback_family, path, True)
    return FontResolution(requested, "TkDefaultFont", None, True)


def _transform_local(entity: TextEntity, x: float, y: float) -> Point:
    alignment = entity.alignment.casefold()
    angle = math.radians(entity.rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    return Point(
        entity.insertion.x + x * cosine - y * sine,
        entity.insertion.y + x * sine + y * cosine,
    )


def _rdp(points: list[tuple[int, int]], tolerance: float) -> list[tuple[int, int]]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    best_distance = -1.0
    best_index = 0
    for index, point in enumerate(points[1:-1], start=1):
        if length_squared <= 1e-12:
            distance = math.hypot(point[0] - start[0], point[1] - start[1])
        else:
            distance = abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / math.sqrt(length_squared)
        if distance > best_distance:
            best_distance, best_index = distance, index
    if best_distance > tolerance:
        return _rdp(points[: best_index + 1], tolerance)[:-1] + _rdp(points[best_index:], tolerance)
    return [start, end]


def _mask_contours(mask, threshold: int = 96) -> list[list[tuple[int, int]]]:
    """Trace the exact exterior edges of a Pillow L mask."""
    pixels = mask.load()
    width, height = mask.size
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    def filled(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and pixels[x, y] >= threshold

    for y in range(height):
        for x in range(width):
            if not filled(x, y):
                continue
            if not filled(x, y - 1):
                edges.add(((x, y), (x + 1, y)))
            if not filled(x + 1, y):
                edges.add(((x + 1, y), (x + 1, y + 1)))
            if not filled(x, y + 1):
                edges.add(((x + 1, y + 1), (x, y + 1)))
            if not filled(x - 1, y):
                edges.add(((x, y + 1), (x, y)))

    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for start, end in edges:
        outgoing[start].append(end)
    contours: list[list[tuple[int, int]]] = []
    while edges:
        start, first_end = next(iter(edges))
        edges.remove((start, first_end))
        contour = [start, first_end]
        current = first_end
        while current != start:
            choices = [end for end in outgoing.get(current, ()) if (current, end) in edges]
            if not choices:
                break
            following = choices[0]
            edges.remove((current, following))
            contour.append(following)
            current = following
        if len(contour) >= 4 and contour[-1] == contour[0]:
            simplified = _rdp(contour[:-1] + [contour[0]], 1.25)
            if len(simplified) >= 4:
                contours.append(simplified[:-1])
    return contours


def text_to_outline_polylines(entity: TextEntity) -> list[Polyline]:
    """Rasterize a TrueType font at high resolution and trace closed glyphs."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ValueError("Text outline conversion requires Pillow.") from exc
    resolution = resolve_font(entity.font_family)
    if resolution.path is None:
        raise ValueError(
            f"Font {entity.font_family!r} is unavailable and no outline-capable fallback font was found."
        )
    font_pixels = 192
    try:
        font = ImageFont.truetype(str(resolution.path), font_pixels)
    except OSError as exc:
        raise ValueError(f"Font {resolution.path.name!r} could not be opened for outline conversion.") from exc
    world_per_pixel = entity.height / font_pixels
    output: list[Polyline] = []
    group_id = new_id("text_outline")
    lines = (entity.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line_index, line in enumerate(lines):
        if not line:
            continue
        probe = Image.new("L", (1, 1), 0)
        draw = ImageDraw.Draw(probe)
        bbox = draw.textbbox((0, 0), line, font=font, anchor="ls", stroke_width=0)
        padding = 3
        width = max(1, bbox[2] - bbox[0] + padding * 2)
        height = max(1, bbox[3] - bbox[1] + padding * 2)
        image = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(image)
        baseline_x = padding - bbox[0]
        baseline_y = padding - bbox[1]
        draw.text((baseline_x, baseline_y), line, font=font, fill=255, anchor="ls")
        advance = float(draw.textlength(line, font=font)) * world_per_pixel * entity.width_factor
        x_align = {"center": -advance / 2.0, "right": -advance}.get(entity.alignment.casefold(), 0.0)
        baseline_offset_y = -line_index * entity.height * 1.2
        for contour in _mask_contours(image):
            points = [
                _transform_local(
                    entity,
                    x_align + (x - baseline_x) * world_per_pixel * entity.width_factor,
                    baseline_offset_y + (baseline_y - y) * world_per_pixel,
                )
                for x, y in contour
            ]
            if len(points) >= 3:
                output.append(Polyline(
                    layer=entity.layer,
                    appearance=copy.deepcopy(entity.appearance),
                    points=points,
                    closed=True,
                    metadata={
                        "derived_from_text": entity.id,
                        "text_conversion": "outline",
                        "text_outline_group": group_id,
                        "source_text": entity.text,
                        "font_family": resolution.resolved_family,
                        "font_substituted": resolution.substituted,
                    },
                ))
    if not output:
        raise ValueError("The selected text contains no visible glyphs to convert.")
    return output


# A compact fourteen-segment shop font.  It deliberately favors clear,
# machinable centerlines over typographic fidelity and requires no installed
# font engine in controller-facing automation or packaged builds.
_SEGMENTS = {
    "a": ((0.12, 1.0), (0.88, 1.0)), "b": ((0.88, 1.0), (0.88, 0.52)),
    "c": ((0.88, 0.48), (0.88, 0.0)), "d": ((0.12, 0.0), (0.88, 0.0)),
    "e": ((0.12, 0.48), (0.12, 0.0)), "f": ((0.12, 1.0), (0.12, 0.52)),
    "g": ((0.12, 0.5), (0.88, 0.5)), "h": ((0.12, 1.0), (0.5, 0.5)),
    "i": ((0.88, 1.0), (0.5, 0.5)), "j": ((0.5, 0.5), (0.12, 0.0)),
    "k": ((0.5, 0.5), (0.88, 0.0)), "l": ((0.5, 1.0), (0.5, 0.5)),
    "m": ((0.5, 0.5), (0.5, 0.0)),
}
_GLYPHS = {
    "0": "ab cdef".replace(" ", ""), "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "A": "abcefg", "B": "abcdefg", "C": "adef", "D": "abcdef", "E": "adefg",
    "F": "aefg", "G": "acdefg", "H": "bcefg", "I": "adlm", "J": "bcde",
    "K": "efhik", "L": "def", "M": "befhi", "N": "befik", "O": "abcdef",
    "P": "abefg", "Q": "abcdefk", "R": "abefgk", "S": "acdfg", "T": "alm",
    "U": "bcdef", "V": "efjk", "W": "bcejk", "X": "hijk", "Y": "him", "Z": "adij",
    "-": "g", "_": "d", "/": "ij", "\\": "hk", "+": "glm", "=": "gd",
}


def text_to_single_line_geometry(entity: TextEntity) -> list[Line]:
    """Convert text to a deterministic single-line engraving font."""
    lines = (entity.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[Line] = []
    group_id = new_id("text_stroke")
    advance = entity.height * 0.72 * entity.width_factor
    gap = entity.height * 0.18 * entity.width_factor
    for row, text_line in enumerate(lines):
        line_width = max(0.0, len(text_line) * (advance + gap) - gap)
        x_align = {"center": -line_width / 2.0, "right": -line_width}.get(entity.alignment.casefold(), 0.0)
        y_offset = -row * entity.height * 1.25
        for column, character in enumerate(text_line.upper()):
            if character == " ":
                continue
            base_x = x_align + column * (advance + gap)
            if character == ".":
                local_segments = [((0.48, 0.02), (0.52, 0.02))]
            else:
                local_segments = [_SEGMENTS[name] for name in _GLYPHS.get(character, "abcdefg")]
            for start, end in local_segments:
                first = _transform_local(entity, base_x + start[0] * advance, y_offset + start[1] * entity.height)
                second = _transform_local(entity, base_x + end[0] * advance, y_offset + end[1] * entity.height)
                output.append(Line(
                    layer=entity.layer,
                    appearance=copy.deepcopy(entity.appearance),
                    start=first,
                    end=second,
                    metadata={
                        "derived_from_text": entity.id,
                        "text_conversion": "single_line",
                        "text_stroke_group": group_id,
                        "source_text": entity.text,
                    },
                ))
    if not output:
        raise ValueError("The selected text contains no visible glyphs to engrave.")
    return output
