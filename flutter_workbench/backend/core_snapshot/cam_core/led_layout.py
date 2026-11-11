"""Pure LED placement proposals; no canvas objects, cutting geometry, or photometry.

All coordinates, footprint dimensions, spacing and clearances are INCHES.
The caller converts drawing units and tessellates curved boundaries. Regions
are one simple outer ring plus polygonal holes/keep-outs. Clipping resolution
is 0.000001 inch; caller tessellation must account for curve error. Boundary contact is
allowed; clearance uses a conservatively enlarged rectangular footprint.
No product dimensions or illumination rules are inferred from the catalog.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Iterable, Mapping

from ._vendor import pyclipper
from .led import LEDProductPreset, SupplyGroupAllocation, calculate_led_plan

_SCALE = 1_000_000
_LIMIT = 1_000_000.0
_MAX_PLACEMENTS = 10_000
_MAX_CANDIDATES = 100_000


def _number(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    if not math.isfinite(value) or abs(value) > _LIMIT or (positive and value <= 0):
        raise ValueError(f"{name} is out of range.")
    return float(value)


def _identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string.")


def _path(points):
    return [(round(x * _SCALE), round(y * _SCALE)) for x, y in points]


def _ring(points):
    result = tuple((_number(x, "x"), _number(y, "y")) for x, y in points)
    if len(result) > 1 and result[0] == result[-1]:
        result = result[:-1]
    if not 3 <= len(result) <= 10_000:
        raise ValueError("A closed region needs 3 to 10000 vertices.")
    path = _path(result)
    if len(set(path)) != len(path) or abs(pyclipper.Area(path)) < 1:
        raise ValueError("Degenerate region polygon.")
    simple = pyclipper.SimplifyPolygon(path, pyclipper.PFT_EVENODD)
    if len(simple) != 1 or abs(abs(pyclipper.Area(simple[0])) - abs(pyclipper.Area(path))) > 1:
        raise ValueError("Region polygon must be simple.")
    return result


@dataclass(frozen=True)
class LEDPlacement:
    placement_id: str
    owner_part_id: str
    product_id: str
    x_in: float
    y_in: float
    rotation_deg: float
    width_in: float
    height_in: float
    manual: bool = False

    def __post_init__(self):
        for name in ("placement_id", "owner_part_id", "product_id"):
            _identity(getattr(self, name), name)
        for name in ("x_in", "y_in", "rotation_deg"):
            _number(getattr(self, name), name)
        for name in ("width_in", "height_in"):
            value = _number(getattr(self, name), name, positive=True)
            if value < 2 / _SCALE:
                raise ValueError("Footprint is below geometry resolution.")
        if not isinstance(self.manual, bool):
            raise ValueError("manual must be boolean.")

    def corners(self, clearance_in: float = 0):
        clearance = _number(clearance_in, "clearance")
        if clearance < 0:
            raise ValueError("Clearance cannot be negative.")
        angle = math.radians(self.rotation_deg)
        c, s = math.cos(angle), math.sin(angle)
        w, h = self.width_in / 2 + clearance, self.height_in / 2 + clearance
        return tuple((self.x_in + x*c - y*s, self.y_in + x*s + y*c)
                     for x, y in ((-w, -h), (w, -h), (w, h), (-w, h)))


@dataclass(frozen=True)
class LEDRegion:
    owner_part_id: str
    outer: tuple
    holes: tuple = ()
    keepouts: tuple = ()

    def __post_init__(self):
        _identity(self.owner_part_id, "owner_part_id")
        if len(self.holes) + len(self.keepouts) > 1000:
            raise ValueError("Too many exclusion polygons.")
        object.__setattr__(self, "outer", _ring(self.outer))
        object.__setattr__(self, "holes", tuple(_ring(ring) for ring in self.holes))
        object.__setattr__(self, "keepouts", tuple(_ring(ring) for ring in self.keepouts))


def placement_fits(placement: LEDPlacement, region: LEDRegion, *, clearance_in: float) -> bool:
    """Entire expanded footprint must fit; excludes holes even inside the module.

    Corner expansion is conservative: corners can require more than the given
    radial clearance. Polygon approximation tolerance belongs to the caller.
    Overlap between separate LED modules is not checked by this function.
    """
    footprint = _path(placement.corners(clearance_in))
    if placement.owner_part_id != region.owner_part_id:
        return False
    clip = pyclipper.Pyclipper()
    clip.AddPath(footprint, pyclipper.PT_SUBJECT, True)
    clip.AddPath(_path(region.outer), pyclipper.PT_CLIP, True)
    outside = clip.Execute(pyclipper.CT_DIFFERENCE)
    if outside:
        return False
    for ring in region.holes + region.keepouts:
        clip = pyclipper.Pyclipper()
        clip.AddPath(footprint, pyclipper.PT_SUBJECT, True)
        clip.AddPath(_path(ring), pyclipper.PT_CLIP, True)
        if clip.Execute(pyclipper.CT_INTERSECTION):
            return False
    return True


@dataclass(frozen=True)
class LEDLayout:
    placements: tuple[LEDPlacement, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "placements", tuple(self.placements))
        if len(self.placements) > _MAX_PLACEMENTS:
            raise ValueError("Too many LED placements.")
        if not all(isinstance(item, LEDPlacement) for item in self.placements):
            raise ValueError("Invalid placement record.")
        if len({item.placement_id for item in self.placements}) != len(self.placements):
            raise ValueError("Duplicate LED placement ID.")

    def to_dict(self):
        return {"schema_version": 1, "units": "in",
                "placements": [asdict(item) for item in self.placements]}

    @classmethod
    def from_dict(cls, data: Mapping):
        if not isinstance(data, Mapping):
            raise ValueError("LED layout must be an object.")
        if type(data.get("schema_version")) is not int or data.get("schema_version") != 1 or data.get("units") != "in":
            raise ValueError("Unsupported LED layout version or units.")
        if not isinstance(data.get("placements"), list):
            raise ValueError("Explicit placement list required.")
        if len(data["placements"]) > _MAX_PLACEMENTS:
            raise ValueError("Too many LED placements.")
        try:
            return cls(tuple(LEDPlacement(**item) for item in data["placements"]))
        except (TypeError, KeyError) as exc:
            raise ValueError("Invalid LED placement data.") from exc


def layout_from_plan(plan: Mapping) -> LEDLayout | None:
    """Legacy count/load-only plans deliberately return None, never invented XY."""
    data = plan.get("layout")
    return None if data is None else LEDLayout.from_dict(data)


def put_placement(layout: LEDLayout, placement: LEDPlacement, region: LEDRegion,
                  *, clearance_in: float) -> LEDLayout:
    """Validated immutable add/update by stable ID; explicit edits are manual."""
    if not placement_fits(placement, region, clearance_in=clearance_in):
        raise ValueError("LED footprint violates the region or edge clearance.")
    placement = replace(placement, manual=True)
    items = list(layout.placements)
    for index, existing in enumerate(items):
        if existing.placement_id == placement.placement_id:
            items[index] = placement
            break
    else:
        items.append(placement)
    return LEDLayout(tuple(items))


def remove_placement(layout: LEDLayout, placement_id: str) -> LEDLayout:
    if not any(item.placement_id == placement_id for item in layout.placements):
        raise KeyError(placement_id)
    return LEDLayout(tuple(item for item in layout.placements if item.placement_id != placement_id))


def transform_part(layout: LEDLayout, owner_part_id: str, *, dx_in: float = 0,
                   dy_in: float = 0, rotation_deg: float = 0,
                   pivot_in: tuple[float, float] = (0, 0)) -> LEDLayout:
    """Rigid rotate about pivot, then translate. Caller transforms region too.

    Identities and manual-edit flags survive. Copies need fresh IDs assigned
    by document integration; no implicit linking to nested sheet transforms.
    """
    dx = _number(dx_in, "dx")
    dy = _number(dy_in, "dy")
    a = math.radians(_number(rotation_deg, "rotation"))
    px, py = (_number(v, "pivot") for v in pivot_in)
    c, s = math.cos(a), math.sin(a)
    items = []
    for item in layout.placements:
        if item.owner_part_id == owner_part_id:
            x, y = item.x_in-px, item.y_in-py
            item = replace(item, x_in=px + c*x-s*y+dx, y_in=py+s*x+c*y+dy,
                           rotation_deg=(item.rotation_deg+rotation_deg) % 360)
        items.append(item)
    return LEDLayout(tuple(items))


def scale_part_positions(
    layout: LEDLayout,
    owner_part_id: str,
    *,
    scale_x: float,
    scale_y: float,
    pivot_in: tuple[float, float] = (0, 0),
) -> LEDLayout:
    """Scale an owner's placement centers while preserving physical modules.

    This keeps registration during artwork resizing without pretending the
    module footprint itself can stretch. The document integration separately
    marks the plan for spacing/fit review after any non-rigid transform.
    """
    sx = _number(scale_x, "scale x", positive=True)
    sy = _number(scale_y, "scale y", positive=True)
    px, py = (_number(value, "pivot") for value in pivot_in)
    items = []
    for item in layout.placements:
        if item.owner_part_id == owner_part_id:
            item = replace(
                item,
                x_in=px + (item.x_in - px) * sx,
                y_in=py + (item.y_in - py) * sy,
            )
        items.append(item)
    return LEDLayout(tuple(items))


def mirror_part(
    layout: LEDLayout,
    owner_part_id: str,
    *,
    axis_start_in: tuple[float, float],
    axis_end_in: tuple[float, float],
    new_owner_part_id: str | None = None,
) -> LEDLayout:
    """Reflect one owner's placements across an explicit infinite axis."""
    ax, ay = (_number(value, "axis start") for value in axis_start_in)
    bx, by = (_number(value, "axis end") for value in axis_end_in)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 1 / _SCALE:
        raise ValueError("Mirror axis has zero length.")
    ux, uy = dx / length, dy / length
    if new_owner_part_id is not None:
        _identity(new_owner_part_id, "new_owner_part_id")
    items = []
    for item in layout.placements:
        if item.owner_part_id == owner_part_id:
            rx, ry = item.x_in - ax, item.y_in - ay
            projection = rx * ux + ry * uy
            x = ax + 2 * projection * ux - rx
            y = ay + 2 * projection * uy - ry
            angle = math.radians(item.rotation_deg)
            vx, vy = math.cos(angle), math.sin(angle)
            component = vx * ux + vy * uy
            reflected_vx = 2 * component * ux - vx
            reflected_vy = 2 * component * uy - vy
            item = replace(
                item,
                owner_part_id=new_owner_part_id or item.owner_part_id,
                x_in=x,
                y_in=y,
                rotation_deg=math.degrees(math.atan2(reflected_vy, reflected_vx)) % 360,
            )
        items.append(item)
    return LEDLayout(tuple(items))


def generate_grid(region: LEDRegion, *, product_id: str, width_in: float,
                  height_in: float, spacing_x_in: float, spacing_y_in: float,
                  clearance_in: float, rotation_deg: float = 0,
                  id_prefix: str = "led", max_candidates: int = 20_000,
                  max_modules: int = 5000) -> LEDLayout:
    """Deterministic row-major grid in region bounds; no illumination prediction.

    Spacing is axis-aligned center pitch, not edge gap. Rejects pitches that
    would overlap rotated footprint bounding boxes. Limits raise rather than
    silently returning a partial layout. Regeneration is a fresh proposal;
    callers must explicitly reconcile manual edits before replacing a layout.
    """
    _identity(id_prefix, "id_prefix")
    for value, cap in ((max_candidates, _MAX_CANDIDATES), (max_modules, _MAX_PLACEMENTS)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
            raise ValueError("Invalid generation limit.")
    sx = _number(spacing_x_in, "spacing_x", positive=True)
    sy = _number(spacing_y_in, "spacing_y", positive=True)
    seed = LEDPlacement(id_prefix, region.owner_part_id, product_id, 0, 0,
                        rotation_deg, width_in, height_in)
    corners = seed.corners(clearance_in)
    half_x = max(abs(x) for x, y in corners)
    half_y = max(abs(y) for x, y in corners)
    raw = seed.corners()
    if sx + 1e-12 < 2*max(abs(x) for x,y in raw) or sy + 1e-12 < 2*max(abs(y) for x,y in raw):
        raise ValueError("Grid pitch would overlap module footprints.")
    xs, ys = zip(*region.outer)
    start_x, start_y = min(xs)+half_x, min(ys)+half_y
    nx = max(0, math.floor((max(xs)-half_x-start_x)/sx + 1e-12)+1)
    ny = max(0, math.floor((max(ys)-half_y-start_y)/sy + 1e-12)+1)
    if nx*ny > max_candidates:
        raise ValueError("Grid candidate limit exceeded; increase spacing or narrow selection.")
    items = []
    for row in range(ny):
        for col in range(nx):
            item = replace(seed, placement_id=f"{id_prefix}:{region.owner_part_id}:{row}:{col}",
                           x_in=start_x+col*sx, y_in=start_y+row*sy)
            if placement_fits(item, region, clearance_in=clearance_in):
                items.append(item)
                if len(items) > max_modules:
                    raise ValueError("Module limit exceeded; no partial layout returned.")
    return LEDLayout(tuple(items))


def calculate_layout_load(layout: LEDLayout, product: LEDProductPreset, *,
                          owner_part_id: str | None = None,
                          supplies: Iterable[SupplyGroupAllocation] = ()):
    """Count actual placements of this product, optionally one owner, then delegate.

    Other product IDs are excluded explicitly. Integration must calculate each
    product and aggregate job totals without counting PSU capacity repeatedly.
    Rope/strip products require length planning and are not module placements.
    """
    if product.product_type != "module":
        raise ValueError("Placement load calculation supports modules only.")
    count = sum(item.product_id == product.preset_id and
                (owner_part_id is None or item.owner_part_id == owner_part_id)
                for item in layout.placements)
    return calculate_led_plan(product, manual_module_count=count, supplies=supplies)

