from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
ASSET_DIRECTORY = ROOT / "assets"
PNG_PATH = ASSET_DIRECTORY / "letter_mounting_icon.png"
ICO_PATH = ASSET_DIRECTORY / "letter_mounting_icon.ico"
SVG_PATH = ASSET_DIRECTORY / "letter_mounting_icon.svg"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

CANVAS = 1024
OFF_WHITE = (250, 252, 254, 255)
BLUE = (0, 104, 174, 255)
LIGHT_BLUE = (104, 184, 226, 255)
RED = (224, 33, 45, 255)
CHARCOAL = (24, 31, 38, 255)
WHITE = (255, 255, 255, 255)


SVG_SOURCE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <title>Letter Mounting Automation</title>
  <circle cx="512" cy="512" r="454" fill="#fafcfe" stroke="#0068ae" stroke-width="58"/>
  <g fill="#0068ae">
    <rect x="477" y="54" width="70" height="150" rx="18"/>
    <rect x="477" y="820" width="70" height="150" rx="18"/>
    <rect x="54" y="477" width="150" height="70" rx="18"/>
    <rect x="820" y="477" width="150" height="70" rx="18"/>
  </g>
  <circle cx="512" cy="512" r="185" fill="none" stroke="#68b8e2" stroke-width="34"/>
  <path d="M510 180 L610 180 L885 875 L692 875 Z" fill="#e0212d"/>
  <path d="M294 568 L733 568 L774 684 L254 684 Z" fill="#e0212d"/>
  <path d="M211 882 L360 902 L592 193 L477 166 Z" fill="#181f26"/>
  <path d="M243 727 L359 773 L435 585 L319 541 Z" fill="#181f26"/>
  <path d="M260 736 L192 838 L285 860 L344 770 Z" fill="#181f26"/>
  <path d="M477 166 L591 193 L611 137 L497 112 Z" fill="#181f26"/>
  <g fill="#fff" stroke="#0068ae" stroke-width="20">
    <circle cx="589" cy="377" r="34"/>
    <circle cx="660" cy="562" r="34"/>
    <circle cx="733" cy="751" r="34"/>
  </g>
</svg>
"""


def _scaled(value: float, scale: float) -> int:
    return round(value * scale)


def _points(values: list[tuple[float, float]], scale: float) -> list[tuple[int, int]]:
    return [(_scaled(x, scale), _scaled(y, scale)) for x, y in values]


def _ellipse(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    radius: float,
    scale: float,
    *,
    fill: tuple[int, int, int, int] | None = None,
    outline: tuple[int, int, int, int] | None = None,
    width: float = 1,
) -> None:
    cx, cy = center
    bounds = (
        _scaled(cx - radius, scale),
        _scaled(cy - radius, scale),
        _scaled(cx + radius, scale),
        _scaled(cy + radius, scale),
    )
    draw.ellipse(bounds, fill=fill, outline=outline, width=max(1, _scaled(width, scale)))


def render_icon(size: int) -> Image.Image:
    """Render one icon frame from flat geometry at its final pixel size."""
    oversample = 6 if size <= 24 else 4 if size <= 64 else 2
    work_size = size * oversample
    scale = work_size / CANVAS
    image = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # White badge and target: strong silhouette and contrast at taskbar size.
    _ellipse(draw, (512, 512), 454, scale, fill=OFF_WHITE, outline=BLUE, width=58)
    tick_width = max(1, _scaled(70, scale))
    for start, end in (
        ((512, 54), (512, 204)),
        ((512, 820), (512, 970)),
        ((54, 512), (204, 512)),
        ((820, 512), (970, 512)),
    ):
        draw.line(_points([start, end], scale), fill=BLUE, width=tick_width)

    if size >= 24:
        _ellipse(draw, (512, 512), 185, scale, outline=LIGHT_BLUE, width=34)

    # The red right leg and crossbar remain broad enough to read as an A at 16 px.
    draw.polygon(
        _points([(510, 180), (610, 180), (885, 875), (692, 875)], scale),
        fill=RED,
    )
    draw.polygon(
        _points([(294, 568), (733, 568), (774, 684), (254, 684)], scale),
        fill=RED,
    )

    # A deliberately simplified M2 silhouette forms the left leg of the A.
    draw.polygon(
        _points([(211, 882), (360, 902), (592, 193), (477, 166)], scale),
        fill=CHARCOAL,
    )
    draw.polygon(
        _points([(243, 727), (359, 773), (435, 585), (319, 541)], scale),
        fill=CHARCOAL,
    )
    draw.polygon(
        _points([(260, 736), (192, 838), (285, 860), (344, 770)], scale),
        fill=CHARCOAL,
    )
    draw.polygon(
        _points([(477, 166), (591, 193), (611, 137), (497, 112)], scale),
        fill=CHARCOAL,
    )

    # Larger frames can carry just enough machine detail without muddying 16 px art.
    if size >= 40:
        detail_width = max(1, _scaled(14, scale))
        draw.line(
            _points([(349, 559), (286, 529)], scale),
            fill=CHARCOAL,
            width=max(1, _scaled(24, scale)),
        )
        draw.line(
            _points([(388, 635), (436, 652)], scale),
            fill=OFF_WHITE,
            width=detail_width,
        )
        for center in ((515, 241), (496, 292), (477, 343)):
            _ellipse(draw, center, 10, scale, fill=OFF_WHITE)

    # Three stud holes are retained as simple high-contrast circles at every size.
    hole_radius = 37 if size >= 32 else 42
    hole_outline = 20 if size >= 32 else 24
    for center in ((589, 377), (660, 562), (733, 751)):
        _ellipse(
            draw,
            center,
            hole_radius,
            scale,
            fill=WHITE,
            outline=BLUE,
            width=hole_outline,
        )

    if oversample > 1:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def write_png_ico(path: Path, frames: list[Image.Image]) -> None:
    """Write an ICO with independently rendered PNG frames, not one scaled master."""
    encoded: list[tuple[Image.Image, bytes]] = []
    for frame in frames:
        stream = io.BytesIO()
        frame.save(stream, "PNG", optimize=True)
        encoded.append((frame, stream.getvalue()))

    header_size = 6 + 16 * len(encoded)
    offset = header_size
    directory = bytearray(struct.pack("<HHH", 0, 1, len(encoded)))
    payload = bytearray()

    for frame, data in encoded:
        width = 0 if frame.width == 256 else frame.width
        height = 0 if frame.height == 256 else frame.height
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                width,
                height,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        payload.extend(data)
        offset += len(data)

    path.write_bytes(directory + payload)


def main() -> None:
    ASSET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    frames = [render_icon(size) for size in ICON_SIZES]
    render_icon(1024).save(PNG_PATH, "PNG", optimize=True)
    write_png_ico(ICO_PATH, frames)
    SVG_PATH.write_text(SVG_SOURCE, encoding="utf-8")
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH} with {len(frames)} purpose-rendered frames")
    print(f"Wrote {SVG_PATH}")


if __name__ == "__main__":
    main()
