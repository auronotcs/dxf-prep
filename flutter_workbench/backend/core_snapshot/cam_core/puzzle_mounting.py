from __future__ import annotations

"""UI-independent identity and registration model for segmented assemblies.

The model deliberately separates immutable installation coordinates from the
current drawing/nesting pose.  It does not choose production joint dimensions:
the coupon builder requires those values explicitly and full-pad generation is
blocked while any shop decision remains provisional.
"""

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Iterable
from uuid import uuid4

from .layout import entity_bounds
from .model import CamProject, Entity, Point, Polyline, TextEntity
from .units import unit_scale


PUZZLE_ASSEMBLIES_KEY = "puzzle_mounting_assemblies"
PUZZLE_SCHEMA = "cam-workbench-puzzle-mounting-v1"
LAYER_PUZZLE_PIECES = "PUZZLE_PIECES"
LAYER_PUZZLE_TUBE_OPENINGS = "PUZZLE_TUBE_OPENINGS"
LAYER_PUZZLE_TUBE_NOMINAL = "PUZZLE_TUBE_NOMINAL"
LAYER_PUZZLE_SEAMS = "PUZZLE_SEAM_GUIDES"
LAYER_PUZZLE_IDS = "PUZZLE_PART_IDS"
PUZZLE_POSE_ORIGIN_KEY = "puzzle_pose_origin"
PUZZLE_POSE_X_KEY = "puzzle_pose_x"
PUZZLE_POSE_Y_KEY = "puzzle_pose_y"
PUZZLE_POSE_PROJECT_UNITS_KEY = "puzzle_pose_project_units"


def _point_dict(point: Point) -> list[float]:
    return [float(point.x), float(point.y)]


def _point(value) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Expected a two-coordinate puzzle point.")
    return Point(float(value[0]), float(value[1]))


def _points(values) -> tuple[Point, ...]:
    return tuple(_point(value) for value in values)


def _polygon_area(points: tuple[Point, ...]) -> float:
    return 0.5 * sum(
        start.x * end.y - end.x * start.y
        for start, end in zip(points, points[1:] + points[:1])
    )


def _bounds(points: Iterable[Point]) -> tuple[float, float, float, float]:
    values = tuple(points)
    if not values:
        raise ValueError("Puzzle geometry requires at least one point.")
    return (
        min(point.x for point in values), min(point.y for point in values),
        max(point.x for point in values), max(point.y for point in values),
    )


def _point_on_segment(point: Point, start: Point, end: Point, tolerance: float = 1e-7) -> bool:
    cross = (point.x - start.x) * (end.y - start.y) - (point.y - start.y) * (end.x - start.x)
    if abs(cross) > tolerance:
        return False
    return (
        min(start.x, end.x) - tolerance <= point.x <= max(start.x, end.x) + tolerance
        and min(start.y, end.y) - tolerance <= point.y <= max(start.y, end.y) + tolerance
    )


def point_in_puzzle_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        if _point_on_segment(point, start, end):
            return True
        if (start.y > point.y) != (end.y > point.y):
            intersect_x = start.x + (point.y - start.y) * (end.x - start.x) / (end.y - start.y)
            if point.x < intersect_x:
                inside = not inside
    return inside


@dataclass(frozen=True)
class PuzzleRigidTransform:
    translation_x: float = 0.0
    translation_y: float = 0.0
    rotation_degrees: float = 0.0

    def apply(self, point: Point) -> Point:
        radians = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(radians), math.sin(radians)
        return Point(
            point.x * cosine - point.y * sine + self.translation_x,
            point.x * sine + point.y * cosine + self.translation_y,
        )

    def inverse(self, point: Point) -> Point:
        radians = math.radians(-self.rotation_degrees)
        relative_x = point.x - self.translation_x
        relative_y = point.y - self.translation_y
        return Point(
            relative_x * math.cos(radians) - relative_y * math.sin(radians),
            relative_x * math.sin(radians) + relative_y * math.cos(radians),
        )

    def to_dict(self) -> dict:
        return {
            "translation": [self.translation_x, self.translation_y],
            "rotation_degrees": self.rotation_degrees,
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "PuzzleRigidTransform":
        value = value or {}
        translation = value.get("translation", (0.0, 0.0))
        point = _point(translation)
        return cls(point.x, point.y, float(value.get("rotation_degrees", 0.0)))


@dataclass(frozen=True)
class PuzzlePieceRecord:
    piece_id: str
    label: str
    assembly_outline: tuple[Point, ...]
    seam_ids: tuple[str, ...] = ()
    placement: PuzzleRigidTransform = field(default_factory=PuzzleRigidTransform)
    sheet_number: int | None = None

    @property
    def assembly_bounds(self) -> tuple[float, float, float, float]:
        return _bounds(self.assembly_outline)

    @property
    def layout_outline(self) -> tuple[Point, ...]:
        return tuple(self.placement.apply(point) for point in self.assembly_outline)

    @property
    def layout_bounds(self) -> tuple[float, float, float, float]:
        return _bounds(self.layout_outline)

    def to_dict(self) -> dict:
        return {
            "piece_id": self.piece_id,
            "label": self.label,
            "assembly_outline": [_point_dict(point) for point in self.assembly_outline],
            "seam_ids": list(self.seam_ids),
            "placement": self.placement.to_dict(),
            "sheet_number": self.sheet_number,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PuzzlePieceRecord":
        return cls(
            piece_id=str(value["piece_id"]),
            label=str(value.get("label", value["piece_id"])),
            assembly_outline=_points(value["assembly_outline"]),
            seam_ids=tuple(str(item) for item in value.get("seam_ids", ())),
            placement=PuzzleRigidTransform.from_dict(value.get("placement")),
            sheet_number=(None if value.get("sheet_number") is None else int(value["sheet_number"])),
        )


@dataclass(frozen=True)
class PuzzleSeamRecord:
    seam_id: str
    piece_a_id: str
    piece_b_id: str
    assembly_centerline: tuple[Point, ...]
    joint_key: str
    tab_owner_piece_id: str
    joint_clearance: float | None = None
    cutter_diameter: float | None = None
    uniqueness_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "seam_id": self.seam_id,
            "piece_a_id": self.piece_a_id,
            "piece_b_id": self.piece_b_id,
            "assembly_centerline": [_point_dict(point) for point in self.assembly_centerline],
            "joint_key": self.joint_key,
            "tab_owner_piece_id": self.tab_owner_piece_id,
            "joint_clearance": self.joint_clearance,
            "cutter_diameter": self.cutter_diameter,
            "uniqueness_verified": self.uniqueness_verified,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PuzzleSeamRecord":
        return cls(
            seam_id=str(value["seam_id"]),
            piece_a_id=str(value["piece_a_id"]),
            piece_b_id=str(value["piece_b_id"]),
            assembly_centerline=_points(value["assembly_centerline"]),
            joint_key=str(value["joint_key"]),
            tab_owner_piece_id=str(value["tab_owner_piece_id"]),
            joint_clearance=(
                None if value.get("joint_clearance") is None else float(value["joint_clearance"])
            ),
            cutter_diameter=(
                None if value.get("cutter_diameter") is None else float(value["cutter_diameter"])
            ),
            uniqueness_verified=bool(value.get("uniqueness_verified", False)),
        )


@dataclass(frozen=True)
class PuzzleTubeOpening:
    feature_id: str
    assembly_center: Point
    nominal_width: float = 4.0
    nominal_height: float = 4.0
    total_allowance_x: float = 0.25
    total_allowance_y: float = 0.25
    rotation_degrees: float = 0.0
    mode: str = "through_cut"
    owner_piece_id: str = ""
    cutter_diameter: float | None = None
    corner_treatment: str | None = None

    @property
    def finished_width(self) -> float:
        return self.nominal_width + self.total_allowance_x

    @property
    def finished_height(self) -> float:
        return self.nominal_height + self.total_allowance_y

    @property
    def per_side_allowance_x(self) -> float:
        return self.total_allowance_x / 2.0

    @property
    def per_side_allowance_y(self) -> float:
        return self.total_allowance_y / 2.0

    def to_dict(self) -> dict:
        return {
            "feature_id": self.feature_id,
            "assembly_center": _point_dict(self.assembly_center),
            "nominal_width": self.nominal_width,
            "nominal_height": self.nominal_height,
            "total_allowance_x": self.total_allowance_x,
            "total_allowance_y": self.total_allowance_y,
            "rotation_degrees": self.rotation_degrees,
            "mode": self.mode,
            "owner_piece_id": self.owner_piece_id,
            "cutter_diameter": self.cutter_diameter,
            "corner_treatment": self.corner_treatment,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PuzzleTubeOpening":
        return cls(
            feature_id=str(value["feature_id"]),
            assembly_center=_point(value["assembly_center"]),
            nominal_width=float(value.get("nominal_width", 4.0)),
            nominal_height=float(value.get("nominal_height", 4.0)),
            total_allowance_x=float(value.get("total_allowance_x", 0.25)),
            total_allowance_y=float(value.get("total_allowance_y", 0.25)),
            rotation_degrees=float(value.get("rotation_degrees", 0.0)),
            mode=str(value.get("mode", "through_cut")),
            owner_piece_id=str(value.get("owner_piece_id", "")),
            cutter_diameter=(
                None if value.get("cutter_diameter") is None else float(value["cutter_diameter"])
            ),
            corner_treatment=(
                None if value.get("corner_treatment") in (None, "")
                else str(value["corner_treatment"])
            ),
        )


@dataclass(frozen=True)
class PuzzleAssemblySettings:
    stock_width: float
    stock_height: float
    stock_thickness: float
    stock_thickness_verified: bool = False
    edge_margin: float | None = None
    joint_clearance: float | None = None
    cutter_diameter: float | None = None
    retention_mode: str = "unresolved"
    allow_part_rotation: bool = True
    joint_fit_verified: bool = False
    tube_layout_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "stock_width": self.stock_width,
            "stock_height": self.stock_height,
            "stock_thickness": self.stock_thickness,
            "stock_thickness_verified": self.stock_thickness_verified,
            "edge_margin": self.edge_margin,
            "joint_clearance": self.joint_clearance,
            "cutter_diameter": self.cutter_diameter,
            "retention_mode": self.retention_mode,
            "allow_part_rotation": self.allow_part_rotation,
            "joint_fit_verified": self.joint_fit_verified,
            "tube_layout_verified": self.tube_layout_verified,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PuzzleAssemblySettings":
        def optional(name: str) -> float | None:
            return None if value.get(name) is None else float(value[name])
        return cls(
            stock_width=float(value["stock_width"]),
            stock_height=float(value["stock_height"]),
            stock_thickness=float(value["stock_thickness"]),
            stock_thickness_verified=bool(value.get("stock_thickness_verified", False)),
            edge_margin=optional("edge_margin"),
            joint_clearance=optional("joint_clearance"),
            cutter_diameter=optional("cutter_diameter"),
            retention_mode=str(value.get("retention_mode", "unresolved")),
            allow_part_rotation=bool(value.get("allow_part_rotation", True)),
            joint_fit_verified=bool(value.get("joint_fit_verified", False)),
            tube_layout_verified=bool(value.get("tube_layout_verified", False)),
        )


@dataclass(frozen=True)
class PuzzleAssemblyRecord:
    assembly_id: str
    label: str
    units: str
    source_entity_ids: tuple[str, ...]
    source_fingerprint: str
    source_bounds: tuple[float, float, float, float]
    settings: PuzzleAssemblySettings
    pieces: tuple[PuzzlePieceRecord, ...] = ()
    seams: tuple[PuzzleSeamRecord, ...] = ()
    tube_openings: tuple[PuzzleTubeOpening, ...] = ()
    generation_revision: int = 0

    def to_dict(self) -> dict:
        return {
            "schema": PUZZLE_SCHEMA,
            "assembly_id": self.assembly_id,
            "label": self.label,
            "units": self.units,
            "source_entity_ids": list(self.source_entity_ids),
            "source_fingerprint": self.source_fingerprint,
            "source_bounds": list(self.source_bounds),
            "settings": self.settings.to_dict(),
            "pieces": [piece.to_dict() for piece in self.pieces],
            "seams": [seam.to_dict() for seam in self.seams],
            "tube_openings": [feature.to_dict() for feature in self.tube_openings],
            "generation_revision": self.generation_revision,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PuzzleAssemblyRecord":
        if value.get("schema") != PUZZLE_SCHEMA:
            raise ValueError("Unsupported puzzle-mounting assembly schema.")
        bounds = tuple(float(item) for item in value["source_bounds"])
        if len(bounds) != 4:
            raise ValueError("Puzzle assembly source bounds require four coordinates.")
        result = cls(
            assembly_id=str(value["assembly_id"]),
            label=str(value.get("label", "Puzzle assembly")),
            units=str(value.get("units", "in")),
            source_entity_ids=tuple(str(item) for item in value.get("source_entity_ids", ())),
            source_fingerprint=str(value["source_fingerprint"]),
            source_bounds=bounds,
            settings=PuzzleAssemblySettings.from_dict(value["settings"]),
            pieces=tuple(PuzzlePieceRecord.from_dict(item) for item in value.get("pieces", ())),
            seams=tuple(PuzzleSeamRecord.from_dict(item) for item in value.get("seams", ())),
            tube_openings=tuple(
                PuzzleTubeOpening.from_dict(item) for item in value.get("tube_openings", ())
            ),
            generation_revision=int(value.get("generation_revision", 0)),
        )
        validate_puzzle_assembly(result)
        return result


@dataclass(frozen=True)
class TubeOpeningPreview:
    nominal_outline: tuple[Point, ...]
    routed_outline: tuple[Point, ...]
    nominal_fits: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PuzzleCouponSpec:
    units: str
    width: float
    height: float
    seam_x: float
    tab_center_y: float
    tab_span: float
    tab_depth: float
    joint_clearance: float
    cutter_diameter: float
    stock_width: float
    stock_height: float
    stock_thickness: float
    stock_thickness_verified: bool
    retention_mode: str
    samples: int = 24


@dataclass(frozen=True)
class PuzzlePartitionSpec:
    units: str
    stock_width: float
    stock_height: float
    stock_thickness: float
    stock_thickness_verified: bool
    edge_margin: float
    joint_clearance: float
    cutter_diameter: float
    retention_mode: str
    joint_fit_verified: bool
    tab_span: float
    tab_depth: float
    tube_openings: tuple[PuzzleTubeOpening, ...] = ()
    allow_part_rotation: bool = True
    samples: int = 16
    seam_offsets: tuple[tuple[str, float], ...] = ()


def provisional_pad_settings() -> PuzzleAssemblySettings:
    """Confirmed 4x8 stock with the still-provisional 0.75-inch thickness."""
    return PuzzleAssemblySettings(
        stock_width=48.0,
        stock_height=96.0,
        stock_thickness=0.75,
        stock_thickness_verified=False,
        joint_clearance=None,
        cutter_diameter=None,
        retention_mode="unresolved",
    )


def provisional_four_inch_tube(
        center: Point, *, rotation_degrees: float = 0.0,
        feature_id: str | None = None) -> PuzzleTubeOpening:
    """Confirmed 4-inch tube with 0.25 total opening allowance, not per side."""
    return PuzzleTubeOpening(
        feature_id=feature_id or f"puzzle_tube_{uuid4().hex}",
        assembly_center=center,
        nominal_width=4.0,
        nominal_height=4.0,
        total_allowance_x=0.25,
        total_allowance_y=0.25,
        rotation_degrees=rotation_degrees,
        mode="through_cut",
    )


def puzzle_source_fingerprint(entities: Iterable[Entity]) -> str:
    payload = [entity.to_dict() for entity in sorted(entities, key=lambda item: item.id)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def puzzle_source_is_current(
        assembly: PuzzleAssemblyRecord, entities: Iterable[Entity]) -> bool:
    """Return False when an authoritative source was edited, removed, or replaced."""
    if not assembly.source_entity_ids:
        return True  # Synthetic coupons intentionally have no drawing source.
    by_id = {entity.id: entity for entity in entities}
    if any(entity_id not in by_id for entity_id in assembly.source_entity_ids):
        return False
    current = [by_id[entity_id] for entity_id in assembly.source_entity_ids]
    return puzzle_source_fingerprint(current) == assembly.source_fingerprint


def _rotated_rectangle(center: Point, width: float, height: float, rotation: float) -> tuple[Point, ...]:
    transform = PuzzleRigidTransform(center.x, center.y, rotation)
    return tuple(transform.apply(Point(x, y)) for x, y in (
        (-width / 2, -height / 2), (width / 2, -height / 2),
        (width / 2, height / 2), (-width / 2, height / 2),
    ))


def _rounded_rectangle(
        center: Point, width: float, height: float, radius: float,
        rotation: float, segments_per_corner: int = 8) -> tuple[Point, ...]:
    if radius <= 0 or radius * 2 > min(width, height):
        raise ValueError("Rounded tube-opening radius does not fit inside the requested opening.")
    local: list[Point] = []
    for corner_x, corner_y, start_angle in (
        (width / 2 - radius, height / 2 - radius, 0),
        (-width / 2 + radius, height / 2 - radius, 90),
        (-width / 2 + radius, -height / 2 + radius, 180),
        (width / 2 - radius, -height / 2 + radius, 270),
    ):
        for index in range(segments_per_corner + 1):
            angle = math.radians(start_angle + 90 * index / segments_per_corner)
            local.append(Point(corner_x + radius * math.cos(angle), corner_y + radius * math.sin(angle)))
    transform = PuzzleRigidTransform(center.x, center.y, rotation)
    return tuple(transform.apply(point) for point in local)


def preview_tube_opening(feature: PuzzleTubeOpening) -> TubeOpeningPreview:
    if feature.mode != "through_cut":
        raise ValueError("The confirmed puzzle-panel tube feature must be a through-cut opening.")
    if feature.cutter_diameter is None or feature.cutter_diameter <= 0:
        raise ValueError("Choose the actual cutter diameter before previewing routed tube corners.")
    if feature.corner_treatment != "rounded":
        raise ValueError("Choose a supported tube corner treatment; rounded is currently available for preview.")
    nominal = _rotated_rectangle(
        feature.assembly_center, feature.nominal_width,
        feature.nominal_height, feature.rotation_degrees,
    )
    routed = _rounded_rectangle(
        feature.assembly_center, feature.finished_width, feature.finished_height,
        feature.cutter_diameter / 2.0, feature.rotation_degrees,
    )
    nominal_fits = all(point_in_puzzle_polygon(point, routed) for point in nominal)
    warnings = () if nominal_fits else (
        "The cutter-rounded opening clips the nominal tube envelope at a corner; choose a smaller cutter, more allowance, or a reviewed relief treatment.",
    )
    return TubeOpeningPreview(nominal, routed, nominal_fits, warnings)


def assign_tube_openings_to_pieces(assembly: PuzzleAssemblyRecord) -> PuzzleAssemblyRecord:
    assigned: list[PuzzleTubeOpening] = []
    for feature in assembly.tube_openings:
        envelope = _rotated_rectangle(
            feature.assembly_center, feature.finished_width,
            feature.finished_height, feature.rotation_degrees,
        )
        candidates = [
            piece for piece in assembly.pieces
            if all(point_in_puzzle_polygon(point, piece.assembly_outline) for point in envelope)
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"Tube opening {feature.feature_id} crosses a seam, leaves the assembly, or has ambiguous ownership."
            )
        assigned.append(replace(feature, owner_piece_id=candidates[0].piece_id))
    return replace(assembly, tube_openings=tuple(assigned))


def validate_puzzle_assembly(assembly: PuzzleAssemblyRecord) -> None:
    if assembly.units not in {"in", "mm", "cm", "m", "ft", "yd"}:
        raise ValueError(f"Unsupported puzzle assembly units: {assembly.units}")
    if len(assembly.source_bounds) != 4:
        raise ValueError("Puzzle assembly source bounds require four coordinates.")
    piece_ids = [piece.piece_id for piece in assembly.pieces]
    if len(set(piece_ids)) != len(piece_ids):
        raise ValueError("Puzzle piece IDs must be unique inside an assembly.")
    seam_ids = [seam.seam_id for seam in assembly.seams]
    if len(set(seam_ids)) != len(seam_ids):
        raise ValueError("Puzzle seam IDs must be unique inside an assembly.")
    pieces = set(piece_ids)
    seams = set(seam_ids)
    for piece in assembly.pieces:
        if len(piece.assembly_outline) < 3 or abs(_polygon_area(piece.assembly_outline)) <= 1e-9:
            raise ValueError(f"Puzzle piece {piece.piece_id} has no usable closed area.")
        if not set(piece.seam_ids).issubset(seams):
            raise ValueError(f"Puzzle piece {piece.piece_id} references an unknown seam.")
    for seam in assembly.seams:
        if seam.piece_a_id not in pieces or seam.piece_b_id not in pieces:
            raise ValueError(f"Puzzle seam {seam.seam_id} references an unknown piece.")
        if seam.piece_a_id == seam.piece_b_id:
            raise ValueError(f"Puzzle seam {seam.seam_id} must join two different pieces.")
        if seam.tab_owner_piece_id not in {seam.piece_a_id, seam.piece_b_id}:
            raise ValueError(f"Puzzle seam {seam.seam_id} has an invalid tab owner.")
        if len(seam.assembly_centerline) < 2:
            raise ValueError(f"Puzzle seam {seam.seam_id} needs an ordered centerline.")
    for feature in assembly.tube_openings:
        if feature.mode != "through_cut":
            raise ValueError(f"Puzzle tube feature {feature.feature_id} is not a through-cut opening.")
        if min(feature.nominal_width, feature.nominal_height) <= 0:
            raise ValueError(f"Puzzle tube feature {feature.feature_id} has an invalid nominal size.")
        if min(feature.total_allowance_x, feature.total_allowance_y) < 0:
            raise ValueError(f"Puzzle tube feature {feature.feature_id} has negative total allowance.")
        if feature.owner_piece_id and feature.owner_piece_id not in pieces:
            raise ValueError(f"Puzzle tube feature {feature.feature_id} references an unknown piece.")


def puzzle_machining_blockers(assembly: PuzzleAssemblyRecord) -> tuple[str, ...]:
    validate_puzzle_assembly(assembly)
    blockers: list[str] = []
    settings = assembly.settings
    if not settings.stock_thickness_verified:
        blockers.append("Verify the actual plywood thickness; 0.75 in is provisional.")
    if settings.joint_clearance is None:
        blockers.append("Enter and coupon-test puzzle-joint fit clearance.")
    if settings.cutter_diameter is None:
        blockers.append("Choose the actual cutter diameter.")
    if settings.retention_mode == "unresolved":
        blockers.append("Choose flat-only alignment or a reviewed lifted-retention method.")
    elif settings.retention_mode != "flat_alignment":
        blockers.append("Lifted retention is not proven by the current in-plane puzzle joint.")
    if assembly.source_entity_ids and not settings.joint_fit_verified:
        blockers.append("Physically verify the selected joint clearance with the coupon before machining the full assembly.")
    if assembly.source_entity_ids and not settings.tube_layout_verified:
        blockers.append("Enter and review the real tube-location list before machining the full assembly.")
    if not assembly.pieces:
        blockers.append("Generate and review editable puzzle pieces before machining.")
    for feature in assembly.tube_openings:
        if not feature.owner_piece_id:
            blockers.append(f"Assign tube opening {feature.feature_id} to one unambiguous piece.")
        if feature.cutter_diameter is None or feature.corner_treatment is None:
            blockers.append(f"Preview cutter corners for tube opening {feature.feature_id}.")
        else:
            preview = preview_tube_opening(feature)
            blockers.extend(preview.warnings)
    return tuple(dict.fromkeys(blockers))


def seam_mating_edges(seam: PuzzleSeamRecord) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    if seam.joint_clearance is None or seam.joint_clearance < 0:
        raise ValueError("A nonnegative explicit joint clearance is required.")
    half = seam.joint_clearance / 2.0
    left = tuple(Point(point.x - half, point.y) for point in seam.assembly_centerline)
    right = tuple(Point(point.x + half, point.y) for point in seam.assembly_centerline)
    return left, right


def generate_two_piece_coupon(
        spec: PuzzleCouponSpec, *, assembly_id: str | None = None) -> PuzzleAssemblyRecord:
    values = (
        spec.width, spec.height, spec.seam_x, spec.tab_center_y, spec.tab_span,
        spec.tab_depth, spec.cutter_diameter, spec.stock_width, spec.stock_height,
        spec.stock_thickness,
    )
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Coupon dimensions, cutter, stock, and thickness must be finite and positive.")
    if spec.joint_clearance < 0 or not math.isfinite(spec.joint_clearance):
        raise ValueError("Coupon joint clearance must be finite and nonnegative.")
    if spec.retention_mode != "flat_alignment":
        raise ValueError(
            "The current 2D coupon proves flat alignment only; lifted retention needs a separate reviewed design."
        )
    if not 0 < spec.seam_x < spec.width:
        raise ValueError("Coupon seam X must be inside the coupon.")
    start_y = spec.tab_center_y - spec.tab_span / 2.0
    end_y = spec.tab_center_y + spec.tab_span / 2.0
    if start_y <= 0 or end_y >= spec.height:
        raise ValueError("Coupon tab span requires straight material above and below it.")
    if spec.seam_x + spec.tab_depth + spec.joint_clearance / 2.0 >= spec.width:
        raise ValueError("Coupon tab leaves no usable material on the mating piece.")
    curvature_radius = spec.tab_span * spec.tab_span / (2.0 * spec.tab_depth * math.pi * math.pi)
    if curvature_radius + 1e-9 < spec.cutter_diameter / 2.0:
        raise ValueError("Coupon tab curvature is tighter than the selected cutter radius.")
    samples = max(8, int(spec.samples))
    centerline = [Point(spec.seam_x, 0.0), Point(spec.seam_x, start_y)]
    centerline.extend(
        Point(
            spec.seam_x + spec.tab_depth * math.sin(math.pi * index / samples) ** 2,
            start_y + spec.tab_span * index / samples,
        )
        for index in range(1, samples)
    )
    centerline.extend((Point(spec.seam_x, end_y), Point(spec.seam_x, spec.height)))
    seam_points = tuple(centerline)
    digest = hashlib.sha256(json.dumps(
        [[round(point.x, 7), round(point.y, 7)] for point in seam_points],
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:16]
    assembly_id = assembly_id or f"puzzle_assembly_{uuid4().hex}"
    left_id = f"{assembly_id}:A"
    right_id = f"{assembly_id}:B"
    seam_id = f"{assembly_id}:S1"
    seam = PuzzleSeamRecord(
        seam_id=seam_id,
        piece_a_id=left_id,
        piece_b_id=right_id,
        assembly_centerline=seam_points,
        joint_key=f"joint-{digest}",
        tab_owner_piece_id=left_id,
        joint_clearance=spec.joint_clearance,
        cutter_diameter=spec.cutter_diameter,
        uniqueness_verified=False,
    )
    left_edge, right_edge = seam_mating_edges(seam)
    left_outline = (Point(0.0, 0.0), *left_edge, Point(0.0, spec.height))
    right_outline = (
        right_edge[0], Point(spec.width, 0.0), Point(spec.width, spec.height),
        right_edge[-1], *reversed(right_edge[1:-1]),
    )
    settings = PuzzleAssemblySettings(
        stock_width=spec.stock_width,
        stock_height=spec.stock_height,
        stock_thickness=spec.stock_thickness,
        stock_thickness_verified=spec.stock_thickness_verified,
        joint_clearance=spec.joint_clearance,
        cutter_diameter=spec.cutter_diameter,
        retention_mode=spec.retention_mode,
    )
    fingerprint = hashlib.sha256(json.dumps(spec.__dict__, sort_keys=True).encode("utf-8")).hexdigest()
    result = PuzzleAssemblyRecord(
        assembly_id=assembly_id,
        label="Two-piece puzzle fit coupon",
        units=spec.units,
        source_entity_ids=(),
        source_fingerprint=fingerprint,
        source_bounds=(0.0, 0.0, spec.width, spec.height),
        settings=settings,
        pieces=(
            PuzzlePieceRecord(left_id, "Coupon A", tuple(left_outline), (seam_id,)),
            PuzzlePieceRecord(right_id, "Coupon B", tuple(right_outline), (seam_id,)),
        ),
        seams=(seam,),
    )
    validate_puzzle_assembly(result)
    return result


def _partition_seam_points(
        start: Point, end: Point, *, tab_span: float, tab_depth: float,
        bulge_sign: float, samples: int) -> tuple[Point, ...]:
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError("Puzzle partition seam has zero length.")
    if tab_span <= 0 or tab_span >= length:
        raise ValueError(
            f"Requested {tab_span:g}-unit tab span does not leave straight material on a {length:g}-unit seam."
        )
    ux, uy = dx / length, dy / length
    nx, ny = -uy * bulge_sign, ux * bulge_sign
    straight = (length - tab_span) / 2.0
    points = [start, Point(start.x + ux * straight, start.y + uy * straight)]
    count = max(8, int(samples))
    points.extend(
        Point(
            start.x + ux * (straight + tab_span * index / count)
            + nx * tab_depth * math.sin(math.pi * index / count) ** 2,
            start.y + uy * (straight + tab_span * index / count)
            + ny * tab_depth * math.sin(math.pi * index / count) ** 2,
        )
        for index in range(1, count)
    )
    points.extend((
        Point(start.x + ux * (straight + tab_span), start.y + uy * (straight + tab_span)),
        end,
    ))
    return tuple(points)


def _partition_axis_offsets(
        seam: PuzzleSeamRecord) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    if seam.joint_clearance is None or seam.joint_clearance < 0:
        raise ValueError("A nonnegative explicit joint clearance is required.")
    start, end = seam.assembly_centerline[0], seam.assembly_centerline[-1]
    half = seam.joint_clearance / 2.0
    if abs(end.y - start.y) >= abs(end.x - start.x):
        return (
            tuple(Point(point.x - half, point.y) for point in seam.assembly_centerline),
            tuple(Point(point.x + half, point.y) for point in seam.assembly_centerline),
        )
    return (
        tuple(Point(point.x, point.y - half) for point in seam.assembly_centerline),
        tuple(Point(point.x, point.y + half) for point in seam.assembly_centerline),
    )


def puzzle_partition_conflicts(assembly: PuzzleAssemblyRecord) -> tuple[str, ...]:
    """Explain source features that cannot belong to exactly one finished piece."""
    conflicts: list[str] = []
    for feature in assembly.tube_openings:
        envelope = _rotated_rectangle(
            feature.assembly_center, feature.finished_width,
            feature.finished_height, feature.rotation_degrees,
        )
        candidates = [
            piece for piece in assembly.pieces
            if all(point_in_puzzle_polygon(point, piece.assembly_outline) for point in envelope)
        ]
        if len(candidates) != 1:
            location = f"X {feature.assembly_center.x:g}, Y {feature.assembly_center.y:g}"
            if not candidates:
                conflicts.append(
                    f"Tube {feature.feature_id} at {location} crosses a proposed seam or leaves the source boundary; move the seam or tube."
                )
            else:
                conflicts.append(
                    f"Tube {feature.feature_id} at {location} has ambiguous ownership across {len(candidates)} pieces."
                )
    return tuple(conflicts)


def generate_rectangular_partition(
        source_entities: Iterable[Entity], spec: PuzzlePartitionSpec, *,
        assembly_id: str | None = None, label: str = "Puzzle panel assembly") -> PuzzleAssemblyRecord:
    """Build a deterministic, editable rectangular-envelope partition preview.

    This bounded generator deliberately accepts one axis-aligned rectangular
    source.  Irregular boundaries stay unsupported rather than silently using
    their bounding box as production geometry.
    """
    sources = tuple(source_entities)
    if len(sources) != 1:
        raise ValueError("Select exactly one closed rectangular source for this partition increment.")
    source = sources[0]
    bounds = entity_bounds(source)
    min_x, min_y, max_x, max_y = bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        raise ValueError("The selected source has no usable rectangular area.")
    if isinstance(source, Polyline):
        if not source.closed or len(source.points) != 4:
            raise ValueError("Irregular closed sources are not yet production-supported; use one four-corner rectangle.")
        corners = {
            (round(point.x, 8), round(point.y, 8)) for point in source.points
        }
        expected = {
            (round(x, 8), round(y, 8))
            for x, y in ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
        }
        if corners != expected:
            raise ValueError("The first partition increment requires an axis-aligned rectangular source.")
    elif source.__class__.__name__ != "Rectangle":
        raise ValueError("The first partition increment requires an axis-aligned Rectangle or four-corner closed polyline.")

    numeric = (
        spec.stock_width, spec.stock_height, spec.stock_thickness,
        spec.tab_span, spec.tab_depth, spec.cutter_diameter,
    )
    if any(not math.isfinite(value) or value <= 0 for value in numeric):
        raise ValueError("Stock, thickness, tab, and cutter values must be finite and positive.")
    if spec.edge_margin < 0 or spec.joint_clearance < 0:
        raise ValueError("Edge margin and joint clearance cannot be negative.")
    if spec.retention_mode != "flat_alignment":
        raise ValueError("The current partition proves flat alignment only; lifted retention needs a reviewed method.")
    curvature_radius = spec.tab_span * spec.tab_span / (2.0 * spec.tab_depth * math.pi * math.pi)
    if curvature_radius + 1e-9 < spec.cutter_diameter / 2.0:
        raise ValueError("Partition tab curvature is tighter than the selected cutter radius.")
    usable_w = spec.stock_width - 2.0 * spec.edge_margin
    usable_h = spec.stock_height - 2.0 * spec.edge_margin
    if min(usable_w, usable_h) <= 2.0 * spec.tab_depth:
        raise ValueError("Stock and edge margin leave no room for the requested puzzle-tab depth.")

    def grid_for(stock_w: float, stock_h: float) -> tuple[int, int]:
        cell_w = stock_w - 2.0 * spec.tab_depth
        cell_h = stock_h - 2.0 * spec.tab_depth
        return max(1, math.ceil(width / cell_w)), max(1, math.ceil(height / cell_h))

    candidates = [(grid_for(usable_w, usable_h), False)]
    if spec.allow_part_rotation:
        candidates.append((grid_for(usable_h, usable_w), True))
    (columns, rows), _rotated = min(
        candidates, key=lambda item: (item[0][0] * item[0][1], item[0][1], item[0][0], item[1]),
    )
    xs = tuple(min_x + width * index / columns for index in range(columns + 1))
    ys = tuple(min_y + height * index / rows for index in range(rows + 1))
    offsets = dict(spec.seam_offsets)
    xs = tuple(
        value + (offsets.get(f"V{index:02d}", 0.0) if 0 < index < columns else 0.0)
        for index, value in enumerate(xs)
    )
    ys = tuple(
        value + (offsets.get(f"H{index:02d}", 0.0) if 0 < index < rows else 0.0)
        for index, value in enumerate(ys)
    )
    if any(end - start <= spec.tab_depth * 2.0 + spec.joint_clearance for start, end in zip(xs, xs[1:])):
        raise ValueError("A moved vertical seam leaves a piece too narrow for the requested tabs and clearance.")
    if any(end - start <= spec.tab_depth * 2.0 + spec.joint_clearance for start, end in zip(ys, ys[1:])):
        raise ValueError("A moved horizontal seam leaves a piece too short for the requested tabs and clearance.")
    assembly_id = assembly_id or f"puzzle_assembly_{uuid4().hex}"
    piece_ids = {
        (row, column): f"{assembly_id}:R{row + 1:02d}C{column + 1:02d}"
        for row in range(rows) for column in range(columns)
    }
    seams: list[PuzzleSeamRecord] = []
    vertical: dict[tuple[int, int], tuple[tuple[Point, ...], tuple[Point, ...], PuzzleSeamRecord]] = {}
    horizontal: dict[tuple[int, int], tuple[tuple[Point, ...], tuple[Point, ...], PuzzleSeamRecord]] = {}

    def make_seam(
            key: str, piece_a: str, piece_b: str, start: Point, end: Point,
            *, owner_a: bool, bulge_sign: float) -> tuple[tuple[Point, ...], tuple[Point, ...], PuzzleSeamRecord]:
        centerline = _partition_seam_points(
            start, end, tab_span=spec.tab_span, tab_depth=spec.tab_depth,
            bulge_sign=bulge_sign, samples=spec.samples,
        )
        digest = hashlib.sha256(json.dumps(
            [[round(point.x, 7), round(point.y, 7)] for point in centerline],
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:16]
        seam = PuzzleSeamRecord(
            seam_id=f"{assembly_id}:{key}", piece_a_id=piece_a, piece_b_id=piece_b,
            assembly_centerline=centerline, joint_key=f"joint-{digest}",
            tab_owner_piece_id=piece_a if owner_a else piece_b,
            joint_clearance=spec.joint_clearance, cutter_diameter=spec.cutter_diameter,
            uniqueness_verified=False,
        )
        seams.append(seam)
        side_a, side_b = _partition_axis_offsets(seam)
        return side_a, side_b, seam

    for row in range(rows):
        for boundary in range(1, columns):
            owner_a = (row + boundary) % 2 == 1
            vertical[(row, boundary)] = make_seam(
                f"V{row + 1:02d}-{boundary:02d}",
                piece_ids[(row, boundary - 1)], piece_ids[(row, boundary)],
                Point(xs[boundary], ys[row]), Point(xs[boundary], ys[row + 1]),
                owner_a=owner_a, bulge_sign=1.0 if owner_a else -1.0,
            )
    for boundary in range(1, rows):
        for column in range(columns):
            owner_a = (boundary + column) % 2 == 1
            horizontal[(boundary, column)] = make_seam(
                f"H{boundary:02d}-{column + 1:02d}",
                piece_ids[(boundary - 1, column)], piece_ids[(boundary, column)],
                Point(xs[column], ys[boundary]), Point(xs[column + 1], ys[boundary]),
                owner_a=owner_a, bulge_sign=1.0 if owner_a else -1.0,
            )

    def append_edge(output: list[Point], values: Iterable[Point]) -> None:
        for point in values:
            if not output or output[-1].distance_to(point) > 1e-9:
                output.append(point)

    pieces: list[PuzzlePieceRecord] = []
    for row in range(rows):
        for column in range(columns):
            outline: list[Point] = []
            bottom = (
                (Point(xs[column], ys[row]), Point(xs[column + 1], ys[row]))
                if row == 0 else horizontal[(row, column)][1]
            )
            right = (
                (Point(xs[column + 1], ys[row]), Point(xs[column + 1], ys[row + 1]))
                if column == columns - 1 else vertical[(row, column + 1)][0]
            )
            top = (
                (Point(xs[column + 1], ys[row + 1]), Point(xs[column], ys[row + 1]))
                if row == rows - 1 else tuple(reversed(horizontal[(row + 1, column)][0]))
            )
            left = (
                (Point(xs[column], ys[row + 1]), Point(xs[column], ys[row]))
                if column == 0 else tuple(reversed(vertical[(row, column)][1]))
            )
            append_edge(outline, bottom)
            append_edge(outline, right)
            append_edge(outline, top)
            append_edge(outline, left)
            seam_ids = tuple(
                seam.seam_id for seam in seams
                if piece_ids[(row, column)] in {seam.piece_a_id, seam.piece_b_id}
            )
            pieces.append(PuzzlePieceRecord(
                piece_ids[(row, column)], f"R{row + 1:02d}-C{column + 1:02d}",
                tuple(outline), seam_ids,
            ))

    settings = PuzzleAssemblySettings(
        stock_width=spec.stock_width, stock_height=spec.stock_height,
        stock_thickness=spec.stock_thickness,
        stock_thickness_verified=spec.stock_thickness_verified,
        edge_margin=spec.edge_margin, joint_clearance=spec.joint_clearance,
        cutter_diameter=spec.cutter_diameter, retention_mode=spec.retention_mode,
        allow_part_rotation=spec.allow_part_rotation,
        joint_fit_verified=spec.joint_fit_verified,
        tube_layout_verified=bool(spec.tube_openings),
    )
    assembly = PuzzleAssemblyRecord(
        assembly_id=assembly_id, label=label, units=spec.units,
        source_entity_ids=tuple(entity.id for entity in sources),
        source_fingerprint=puzzle_source_fingerprint(sources),
        source_bounds=bounds, settings=settings, pieces=tuple(pieces),
        seams=tuple(seams), tube_openings=spec.tube_openings,
    )
    assigned: list[PuzzleTubeOpening] = []
    for feature in assembly.tube_openings:
        envelope = _rotated_rectangle(
            feature.assembly_center, feature.finished_width,
            feature.finished_height, feature.rotation_degrees,
        )
        owners = [
            piece for piece in assembly.pieces
            if all(point_in_puzzle_polygon(point, piece.assembly_outline) for point in envelope)
        ]
        assigned.append(replace(feature, owner_piece_id=owners[0].piece_id if len(owners) == 1 else ""))
    assembly = replace(assembly, tube_openings=tuple(assigned))
    validate_puzzle_assembly(assembly)
    return assembly


def puzzle_piece_stock_issues(assembly: PuzzleAssemblyRecord) -> tuple[str, ...]:
    settings = assembly.settings
    margin = max(0.0, settings.edge_margin or 0.0)
    usable_width = settings.stock_width - margin * 2.0
    usable_height = settings.stock_height - margin * 2.0
    issues: list[str] = []
    for piece in assembly.pieces:
        bounds = piece.layout_bounds
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        fits = width <= usable_width + 1e-9 and height <= usable_height + 1e-9
        rotated_fits = (
            settings.allow_part_rotation
            and height <= usable_width + 1e-9
            and width <= usable_height + 1e-9
        )
        if not (fits or rotated_fits):
            issues.append(
                f"{piece.label} finished bounds {width:g} × {height:g} {assembly.units} "
                f"do not fit usable stock {usable_width:g} × {usable_height:g}; tabs are included."
            )
    return tuple(issues)


def _pose_metadata(
        assembly: PuzzleAssemblyRecord, piece: PuzzlePieceRecord,
        project_units: str) -> dict:
    factor = unit_scale(assembly.units, project_units)
    origin = piece.placement.apply(Point(0.0, 0.0))
    x_axis = piece.placement.apply(Point(1.0, 0.0))
    y_axis = piece.placement.apply(Point(0.0, 1.0))
    return {
        PUZZLE_POSE_ORIGIN_KEY: [origin.x * factor, origin.y * factor],
        PUZZLE_POSE_X_KEY: [x_axis.x * factor, x_axis.y * factor],
        PUZZLE_POSE_Y_KEY: [y_axis.x * factor, y_axis.y * factor],
        PUZZLE_POSE_PROJECT_UNITS_KEY: project_units,
    }


def _entity_metadata(
        assembly: PuzzleAssemblyRecord, piece: PuzzlePieceRecord,
        project_units: str, role: str, *, machinable: bool) -> dict:
    return {
        "puzzle_assembly_id": assembly.assembly_id,
        "puzzle_piece_id": piece.piece_id,
        "puzzle_source_fingerprint": assembly.source_fingerprint,
        "mounting_part_id": f"puzzle:{assembly.assembly_id}:{piece.piece_id}",
        "puzzle_role": role,
        "mounting_generated": True,
        "machinable": machinable,
        "reference_only": not machinable,
        **_pose_metadata(assembly, piece, project_units),
    }


def puzzle_assembly_entities(
        assembly: PuzzleAssemblyRecord, project_units: str, *,
        include_references: bool = True, require_machinable: bool = False) -> list[Entity]:
    validate_puzzle_assembly(assembly)
    blockers = puzzle_machining_blockers(assembly)
    if require_machinable and blockers:
        raise ValueError("Puzzle assembly is not machining-ready: " + " ".join(blockers))
    factor = unit_scale(assembly.units, project_units)
    entities: list[Entity] = []
    pieces = {piece.piece_id: piece for piece in assembly.pieces}
    for piece in assembly.pieces:
        outline = [piece.placement.apply(point) for point in piece.assembly_outline]
        entities.append(Polyline(
            id=f"puzzle_piece_{hashlib.sha1(piece.piece_id.encode()).hexdigest()[:20]}",
            layer=LAYER_PUZZLE_PIECES,
            points=[Point(point.x * factor, point.y * factor) for point in outline],
            closed=True,
            metadata=_entity_metadata(
                assembly, piece, project_units, "finished_piece_outline", machinable=True,
            ),
        ))
        if include_references:
            bounds = piece.layout_bounds
            entities.append(TextEntity(
                id=f"puzzle_label_{hashlib.sha1(piece.piece_id.encode()).hexdigest()[:20]}",
                layer=LAYER_PUZZLE_IDS,
                insertion=Point(
                    ((bounds[0] + bounds[2]) / 2.0) * factor,
                    ((bounds[1] + bounds[3]) / 2.0) * factor,
                ),
                text=piece.label,
                height=0.3 * unit_scale("in", project_units),
                alignment="middle_center",
                metadata=_entity_metadata(
                    assembly, piece, project_units, "piece_id_reference", machinable=False,
                ),
            ))
    for feature in assembly.tube_openings:
        piece = pieces.get(feature.owner_piece_id)
        nominal = _rotated_rectangle(
            feature.assembly_center, feature.nominal_width,
            feature.nominal_height, feature.rotation_degrees,
        )
        if include_references:
            points = [piece.placement.apply(point) for point in nominal] if piece is not None else list(nominal)
            if piece is None:
                metadata = {
                    "puzzle_assembly_id": assembly.assembly_id,
                    "puzzle_feature_id": feature.feature_id,
                    "puzzle_role": "unassigned_tube_reference",
                    "machinable": False,
                    "reference_only": True,
                    "puzzle_conflict": True,
                }
            else:
                metadata = _entity_metadata(
                    assembly, piece, project_units, "nominal_tube_envelope", machinable=False,
                )
                metadata["puzzle_feature_id"] = feature.feature_id
            entities.append(Polyline(
                id=f"puzzle_nominal_{hashlib.sha1(feature.feature_id.encode()).hexdigest()[:20]}",
                layer=LAYER_PUZZLE_TUBE_NOMINAL,
                points=[Point(point.x * factor, point.y * factor) for point in points],
                closed=True,
                metadata=metadata,
            ))
        if piece is not None and feature.cutter_diameter is not None and feature.corner_treatment is not None:
            routed = preview_tube_opening(feature).routed_outline
            points = [piece.placement.apply(point) for point in routed]
            metadata = _entity_metadata(
                assembly, piece, project_units, "tube_through_opening", machinable=True,
            )
            metadata["puzzle_feature_id"] = feature.feature_id
            metadata["puzzle_total_allowance"] = [
                feature.total_allowance_x, feature.total_allowance_y,
            ]
            entities.append(Polyline(
                id=f"puzzle_tube_{hashlib.sha1(feature.feature_id.encode()).hexdigest()[:20]}",
                layer=LAYER_PUZZLE_TUBE_OPENINGS,
                points=[Point(point.x * factor, point.y * factor) for point in points],
                closed=True,
                metadata=metadata,
            ))
    if include_references:
        for seam in assembly.seams:
            entities.append(Polyline(
                id=f"puzzle_seam_{hashlib.sha1(seam.seam_id.encode()).hexdigest()[:20]}",
                layer=LAYER_PUZZLE_SEAMS,
                points=[Point(point.x * factor, point.y * factor) for point in seam.assembly_centerline],
                closed=False,
                metadata={
                    "puzzle_assembly_id": assembly.assembly_id,
                    "puzzle_seam_id": seam.seam_id,
                    "puzzle_joint_key": seam.joint_key,
                    "puzzle_role": "shared_seam_reference",
                    "machinable": False,
                    "reference_only": True,
                },
            ))
    return entities


def current_puzzle_assembly_point(
        entity: Entity, point: Point, *, assembly_units: str, project_units: str) -> Point:
    metadata = entity.metadata
    if metadata.get(PUZZLE_POSE_PROJECT_UNITS_KEY) != project_units:
        raise ValueError("Puzzle piece pose units do not match the current project.")
    origin = _point(metadata.get(PUZZLE_POSE_ORIGIN_KEY))
    x_axis = _point(metadata.get(PUZZLE_POSE_X_KEY))
    y_axis = _point(metadata.get(PUZZLE_POSE_Y_KEY))
    vector_x = Point(x_axis.x - origin.x, x_axis.y - origin.y)
    vector_y = Point(y_axis.x - origin.x, y_axis.y - origin.y)
    expected = unit_scale(assembly_units, project_units)
    if (
        not math.isclose(math.hypot(vector_x.x, vector_x.y), expected, rel_tol=1e-7, abs_tol=1e-7)
        or not math.isclose(math.hypot(vector_y.x, vector_y.y), expected, rel_tol=1e-7, abs_tol=1e-7)
        or not math.isclose(vector_x.x * vector_y.x + vector_x.y * vector_y.y, 0.0, abs_tol=max(1e-7, expected * expected * 1e-7))
    ):
        raise ValueError("Puzzle piece pose has the wrong unit scale or is distorted.")
    return Point(
        origin.x + point.x * vector_x.x + point.y * vector_y.x,
        origin.y + point.x * vector_x.y + point.y * vector_y.y,
    )


def store_puzzle_assembly(project: CamProject, assembly: PuzzleAssemblyRecord) -> None:
    validate_puzzle_assembly(assembly)
    values = [
        item for item in project.metadata.get(PUZZLE_ASSEMBLIES_KEY, [])
        if isinstance(item, dict) and str(item.get("assembly_id", "")) != assembly.assembly_id
    ]
    values.append(assembly.to_dict())
    project.metadata[PUZZLE_ASSEMBLIES_KEY] = values


def load_puzzle_assemblies(project: CamProject) -> tuple[PuzzleAssemblyRecord, ...]:
    output: list[PuzzleAssemblyRecord] = []
    for value in project.metadata.get(PUZZLE_ASSEMBLIES_KEY, []):
        if isinstance(value, dict):
            output.append(PuzzleAssemblyRecord.from_dict(value))
    return tuple(output)


def puzzle_piece_entity(
        entities: Iterable[Entity], assembly_id: str, piece_id: str) -> Entity | None:
    return next((
        entity for entity in entities
        if entity.metadata.get("puzzle_assembly_id") == assembly_id
        and entity.metadata.get("puzzle_piece_id") == piece_id
        and entity.metadata.get("puzzle_role") == "finished_piece_outline"
    ), None)


def puzzle_piece_geometry_matches_record(
        assembly: PuzzleAssemblyRecord, piece_id: str, entity: Entity,
        project_units: str, *, tolerance: float | None = None) -> bool:
    """Return whether an applied outline is still only a rigidly placed generated piece.

    Regeneration may safely retain a translated/rotated/nested piece, but it must
    not silently overwrite point edits made to the production contour.
    """
    if not isinstance(entity, Polyline) or not entity.closed:
        return False
    piece = next((item for item in assembly.pieces if item.piece_id == piece_id), None)
    if piece is None:
        return False
    factor = unit_scale(assembly.units, project_units)
    expected = tuple(
        Point(point.x * factor, point.y * factor)
        for point in (piece.placement.apply(value) for value in piece.assembly_outline)
    )
    if len(entity.points) != len(expected):
        return False
    epsilon = tolerance
    if epsilon is None:
        epsilon = max(1e-7, unit_scale("in", project_units) * 1e-6)
    return all(current.distance_to(reference) <= epsilon for current, reference in zip(entity.points, expected))


def update_piece_placement_from_entity(
        assembly: PuzzleAssemblyRecord, piece_id: str, entity: Entity,
        project_units: str, *, sheet_number: int | None = None) -> PuzzleAssemblyRecord:
    origin = current_puzzle_assembly_point(
        entity, Point(0.0, 0.0), assembly_units=assembly.units, project_units=project_units,
    )
    x_axis = current_puzzle_assembly_point(
        entity, Point(1.0, 0.0), assembly_units=assembly.units, project_units=project_units,
    )
    scale = unit_scale(project_units, assembly.units)
    vector_x = Point(x_axis.x - origin.x, x_axis.y - origin.y)
    rotation = math.degrees(math.atan2(vector_x.y, vector_x.x))
    placement = PuzzleRigidTransform(origin.x * scale, origin.y * scale, rotation)
    pieces = tuple(
        replace(piece, placement=placement, sheet_number=sheet_number)
        if piece.piece_id == piece_id else piece
        for piece in assembly.pieces
    )
    if pieces == assembly.pieces:
        raise ValueError(f"Unknown puzzle piece: {piece_id}")
    return replace(assembly, pieces=pieces, generation_revision=assembly.generation_revision + 1)
