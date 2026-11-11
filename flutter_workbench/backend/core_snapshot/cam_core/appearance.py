"""Durable, document-owned object appearance.

Appearance is intentionally independent from layers, selection/theme chrome,
and CAM intent.  A color keeps its authored model and channels as well as a
separate screen-preview RGB value; CMYK preview conversion is not presented as
ICC-calibrated print conversion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .colors import cmyk_to_rgb


COLOR_MODELS = ("rgb", "cmyk", "named")


@dataclass(frozen=True)
class ColorValue:
    model: str = "rgb"
    channels: tuple[float, ...] = (0.0, 0.0, 0.0)
    preview_rgb: tuple[int, int, int] = (0, 0, 0)
    profile: str = ""
    name: str = ""
    # Optional palette provenance is copied into the document with the color.
    # It is deliberately descriptive rather than a live library reference:
    # replacing an imported palette must never recolor previously saved work.
    palette_id: str = ""
    swatch_id: str = ""
    manufacturer: str = ""
    collection: str = ""
    product_code: str = ""
    source: str = ""
    source_version: str = ""

    def validate(self) -> None:
        if self.model not in COLOR_MODELS:
            raise ValueError(f"Unsupported object color model: {self.model!r}.")
        expected = 4 if self.model == "cmyk" else 3 if self.model == "rgb" else 0
        if expected and len(self.channels) != expected:
            raise ValueError(f"{self.model.upper()} object color requires {expected} channels.")
        if self.model == "named" and self.channels:
            raise ValueError("A named object color does not use numeric channels.")
        limit = 100.0 if self.model == "cmyk" else 255.0
        if expected and any(not math.isfinite(value) or not 0 <= value <= limit for value in self.channels):
            raise ValueError(f"{self.model.upper()} object color channels are outside 0-{limit:g}.")
        if self.model == "named" and not self.name.strip():
            raise ValueError("A named object color requires a swatch name.")
        if len(self.preview_rgb) != 3 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in self.preview_rgb):
            raise ValueError("Object color preview RGB must contain three integers from 0-255.")

    @property
    def preview_hex(self) -> str:
        return "#" + "".join(f"{value:02x}" for value in self.preview_rgb)

    @property
    def calibrated(self) -> bool:
        return bool(self.profile.strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ColorValue":
        value = cls(
            model=str(payload.get("model", "rgb")).casefold(),
            channels=tuple(float(item) for item in payload.get("channels", (0, 0, 0))),
            preview_rgb=tuple(int(item) for item in payload.get("preview_rgb", (0, 0, 0))),
            profile=str(payload.get("profile", "")),
            name=str(payload.get("name", "")),
            palette_id=str(payload.get("palette_id", "")),
            swatch_id=str(payload.get("swatch_id", "")),
            manufacturer=str(payload.get("manufacturer", "")),
            collection=str(payload.get("collection", "")),
            product_code=str(payload.get("product_code", "")),
            source=str(payload.get("source", "")),
            source_version=str(payload.get("source_version", "")),
        )
        value.validate()
        return value

    @classmethod
    def rgb(
        cls, red: float, green: float, blue: float, *, profile: str = "", name: str = "",
        palette_id: str = "", swatch_id: str = "", manufacturer: str = "",
        collection: str = "", product_code: str = "", source: str = "",
        source_version: str = "",
    ) -> "ColorValue":
        channels = (float(red), float(green), float(blue))
        value = cls(
            "rgb", channels, tuple(round(item) for item in channels), profile, name,
            palette_id, swatch_id, manufacturer, collection, product_code, source,
            source_version,
        )
        value.validate()
        return value

    @classmethod
    def cmyk(
        cls, cyan: float, magenta: float, yellow: float, black: float, *,
        profile: str = "", name: str = "", palette_id: str = "", swatch_id: str = "",
        manufacturer: str = "", collection: str = "", product_code: str = "",
        source: str = "", source_version: str = "",
    ) -> "ColorValue":
        channels = (float(cyan), float(magenta), float(yellow), float(black))
        preview = tuple(int(item) for item in cmyk_to_rgb(*channels))
        value = cls(
            "cmyk", channels, preview, profile, name, palette_id, swatch_id,
            manufacturer, collection, product_code, source, source_version,
        )
        value.validate()
        return value

    @classmethod
    def named(
        cls, name: str, preview_rgb: tuple[int, int, int], *, profile: str = "",
        palette_id: str = "", swatch_id: str = "", manufacturer: str = "",
        collection: str = "", product_code: str = "", source: str = "",
        source_version: str = "",
    ) -> "ColorValue":
        value = cls(
            "named", (), tuple(int(item) for item in preview_rgb), profile, name,
            palette_id, swatch_id, manufacturer, collection, product_code, source,
            source_version,
        )
        value.validate()
        return value


@dataclass
class ObjectAppearance:
    fill_enabled: bool = False
    fill: ColorValue = field(default_factory=ColorValue)
    stroke_enabled: bool = True
    stroke: ColorValue = field(default_factory=ColorValue)
    # Project units. Zero is a device hairline/default display stroke.
    stroke_width: float = 0.0

    def validate(self) -> None:
        self.fill.validate()
        self.stroke.validate()
        if not math.isfinite(self.stroke_width) or self.stroke_width < 0:
            raise ValueError("Object stroke width cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_enabled": bool(self.fill_enabled),
            "fill": self.fill.to_dict(),
            "stroke_enabled": bool(self.stroke_enabled),
            "stroke": self.stroke.to_dict(),
            "stroke_width": float(self.stroke_width),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ObjectAppearance":
        value = cls(
            fill_enabled=bool(payload.get("fill_enabled", False)),
            fill=ColorValue.from_dict(payload.get("fill", {})),
            stroke_enabled=bool(payload.get("stroke_enabled", True)),
            stroke=ColorValue.from_dict(payload.get("stroke", {})),
            stroke_width=float(payload.get("stroke_width", 0.0)),
        )
        value.validate()
        return value


def appearance_from_layer_color(color: str) -> ObjectAppearance:
    """Materialize inherited layer ink without changing its layer owner."""
    text = str(color).strip().lstrip("#")
    if len(text) != 6:
        text = "202830"
    try:
        rgb = tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        rgb = (32, 40, 48)
    return ObjectAppearance(stroke=ColorValue.rgb(*rgb))


def scaled_appearance(appearance: ObjectAppearance | None, factor: float) -> ObjectAppearance | None:
    if appearance is None:
        return None
    value = ObjectAppearance.from_dict(appearance.to_dict())
    value.stroke_width *= factor
    return value
