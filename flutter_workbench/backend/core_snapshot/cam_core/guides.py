from __future__ import annotations

"""Persistent drafting guides which never participate in manufacturing geometry."""

from dataclasses import asdict, dataclass, field
import math

from .model import new_id


GUIDE_ORIENTATIONS = ("horizontal", "vertical")


@dataclass
class Guide:
    """One infinite document-space ruler guide.

    ``position`` is Y for a horizontal guide and X for a vertical guide.  A
    guide intentionally has no Entity base class: that type boundary keeps it
    out of selection, nesting, toolpaths, DXF export, and controller output.
    """

    orientation: str
    position: float
    visible: bool = True
    locked: bool = False
    name: str = ""
    id: str = field(default_factory=lambda: new_id("guide"))

    def validate(self) -> None:
        if self.orientation not in GUIDE_ORIENTATIONS:
            raise ValueError(f"Unsupported guide orientation: {self.orientation!r}.")
        if not math.isfinite(self.position):
            raise ValueError("Guide position must be a finite number.")

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Guide":
        guide = cls(
            orientation=str(payload.get("orientation", "")),
            position=float(payload.get("position", 0.0)),
            visible=bool(payload.get("visible", True)),
            locked=bool(payload.get("locked", False)),
            name=str(payload.get("name", "")),
            id=str(payload.get("id") or new_id("guide")),
        )
        guide.validate()
        return guide

    def scaled(self, factor: float) -> "Guide":
        return Guide(
            orientation=self.orientation,
            position=self.position * factor,
            visible=self.visible,
            locked=self.locked,
            name=self.name,
            id=self.id,
        )

    def localized(self, *, delta_x: float = 0.0, delta_y: float = 0.0) -> "Guide":
        delta = delta_y if self.orientation == "horizontal" else delta_x
        return Guide(
            orientation=self.orientation,
            position=self.position + delta,
            visible=self.visible,
            locked=self.locked,
            name=self.name,
            id=self.id,
        )
