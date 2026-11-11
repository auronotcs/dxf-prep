"""Derived 2D job thumbnails and file details; never generates toolpaths."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .layout import entity_bounds
from .model import CamProject, Circle, Line, Polyline, Rectangle, TextEntity
from .project_storage import write_project_payload


def file_signature(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def thumbnail_key(path, signature):
    return hashlib.sha256(repr((2, os.path.abspath(str(path)).casefold(), signature)).encode()).hexdigest()


def render_thumbnail(project, size=(96, 72)):
    from PIL import Image, ImageDraw, ImageFont
    zoom = 3
    width, height = size[0] * zoom, size[1] * zoom
    image = Image.new("RGB", (width, height), "#fafbfc")
    draw = ImageDraw.Draw(image)
    boxes = [box for entity in project.entities if (box := entity_bounds(entity)) is not None]
    boxes = [box for box in boxes if all(math.isfinite(v) for v in box)]
    if not boxes:
        draw.text((12, height / 2 - 8), "Empty drawing", fill="#617080")
        return image.resize(size, Image.Resampling.LANCZOS)
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    scale = min((width - 24) / max(x1 - x0, .001), (height - 24) / max(y1 - y0, .001))
    def xy(point):
        return width / 2 + (point.x - (x0 + x1) / 2) * scale, height / 2 - (point.y - (y0 + y1) / 2) * scale
    ink = "#28546e"
    for entity in project.entities:
        box = entity_bounds(entity)
        if box is None or not all(math.isfinite(v) for v in box):
            continue
        if isinstance(entity, Line):
            draw.line([xy(entity.start), xy(entity.end)], fill=ink, width=3)
        elif isinstance(entity, Rectangle):
            a, b = xy(entity.corner_a), xy(entity.corner_b)
            draw.rectangle((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), outline=ink, width=3)
        elif isinstance(entity, Circle):
            x, y = xy(entity.center)
            r = abs(entity.radius * scale)
            draw.ellipse((x-r, y-r, x+r, y+r), outline=ink, width=3)
        elif isinstance(entity, Polyline) and len(entity.points) > 1:
            points = [xy(p) for p in entity.points]
            if entity.closed:
                points.append(points[0])
            draw.line(points, fill=ink, width=3)
        elif isinstance(entity, TextEntity):
            font = ImageFont.load_default(size=max(8, min(200, abs(entity.height * scale))))
            text = entity.text[:2000]
            bbox = draw.multiline_textbbox((0, 0), text, font=font)
            label = Image.new('RGBA', (max(1, int(bbox[2]-bbox[0]+4)), max(1, int(bbox[3]-bbox[1]+4))))
            ImageDraw.Draw(label).multiline_text((2-bbox[0], 2-bbox[1]), text, fill=ink, font=font)
            label = label.rotate(entity.rotation, expand=True, resample=Image.Resampling.BICUBIC)
            x, y = xy(entity.insertion)
            image.paste(label, (int(x), int(y-label.height)), label)
    return image.resize(size, Image.Resampling.LANCZOS)


def load_recent_preview(path, cache_root, folder=False):
    """Worker-only I/O. Returns plain details and a PIL image, never Tk widgets."""
    path = Path(path)
    result = {"available": False, "image": None, "details": "", "path": str(path)}
    try:
        if folder:
            result['available'] = path.is_dir()
            result['details'] = "Folder" if result['available'] else "Unavailable folder"
            return result
        signature = file_signature(path)
        result.update(available=True, bytes=signature[0], modified_at=datetime.fromtimestamp(signature[1]/1e9, timezone.utc).isoformat())
        from PIL import Image
        cache = Path(cache_root) / 'recent_data'
        key = thumbnail_key(path, signature)
        png, metadata = cache / (key + '.png'), cache / (key + '.json')
        if png.is_file() and metadata.is_file():
            result.update(json.loads(metadata.read_text(encoding='utf-8')))
            with Image.open(png) as image:
                result['image'] = image.copy()
            return result
        if signature[0] > 50 * 1024 * 1024:
            raise ValueError('Preview unavailable for files over 50 MB; Open remains available.')
        if path.suffix.casefold() == '.dxf':
            from .dxf import import_dxf
            project = import_dxf(path).project
            kind = 'DXF'
        else:
            project = CamProject.load(path)
            kind = 'CAM'
        image = render_thumbnail(project)
        if file_signature(path) != signature:
            raise ValueError('File changed during preview; refresh to retry.')
        result.update(kind=kind, units=project.units, objects=len(project.entities), details=f"{kind} · {len(project.entities)} objects · {project.units}")
        cache.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=cache, suffix='.png', delete=False) as stream:
                temporary = Path(stream.name)
            image.save(temporary, format='PNG')
            os.replace(temporary, png)
            write_project_payload(metadata, {k:v for k,v in result.items() if k != 'image'})
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        result['image'] = image
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        result['details'] = 'Preview unavailable: ' + str(exc) if result['available'] else 'Unavailable — file moved, deleted, or inaccessible'
    return result
