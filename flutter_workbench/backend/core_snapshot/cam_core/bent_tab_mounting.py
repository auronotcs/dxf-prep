"""Pure, review-only flat tab geometry; no project mutation or machining output.

Scope is already-reduced closed back contours with integral flat flanges.
Logical-edge recognition joins fragmented straights and smooth tessellated
curves; manual curved roots use the local contour chord/tangent and are fully
validated against the rebuilt outline and cutouts.
Round tips and semicircular root reliefs use explicit chord-error tolerance.
All lengths share the caller's project units. Projection is flat extension,
not a finished bent flange length; no bend allowance is calculated.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
import math
from typing import Callable, Iterable

from .model import Circle, Entity, Line, Point, Polyline
from .bent_tab_forming import BentTabForming


@dataclass(frozen=True)
class BentTabSettings:
    width: float
    flat_projection: float
    edge_end_clearance: float
    root_relief_radius: float
    tip_corner_radius: float
    bend_direction: str  # "front", "back", or explicitly "unspecified"
    tolerance: float  # project-unit geometric tolerance, supplied by caller
    curve_tolerance: float | None = None  # maximum arc chord error; required for nonzero radii
    forming: BentTabForming | None = None  # explicit polycarbonate prototype; legacy is unchanged


@dataclass(frozen=True)
class BentTabAnchor:
    edge_index: int
    fraction: float  # along original edge, not generated outline


@dataclass(frozen=True)
class BentTabLogicalEdge:
    """A visually continuous contour run used for manual tab placement.

    Straight runs may contain many collinear DXF/tessellation segments. Smooth
    curves may likewise contain many short chords. Sharp vertices delimit runs;
    the original segment index remains the durable source-relative anchor.
    """

    start_edge_index: int
    edge_indices: tuple[int, ...]
    start_distance: float
    length: float
    straight: bool
    closed_loop: bool = False


@dataclass(frozen=True)
class BentTabManualPlacement:
    anchor: BentTabAnchor
    point: Point
    logical_edge: BentTabLogicalEdge
    distance_from_click: float
    auto_centered: bool
    # Manual placement may deliberately use less than the automatic-layout
    # endpoint-clearance preference on a short straight.  This remains None
    # unless the tab/root footprint physically fits and the only relaxation
    # is that non-structural preference.
    endpoint_clearance_override: float | None = None


@dataclass(frozen=True)
class BentTabResult:
    source_back: Polyline
    source_references: tuple[Entity, ...]
    modified_cut_outline: Polyline
    bend_guides: tuple[Line, ...]
    keyholes: tuple[Entity, ...]
    owner_entity_id: str
    group_id: str
    warnings: tuple[str, ...]
    inner_cutouts: tuple[Polyline, ...] = ()


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x)


def _on_segment(a: Point, b: Point, p: Point, tol: float) -> bool:
    length=a.distance_to(b)
    return (abs(_cross(a,b,p)) <= tol*length and
            min(a.x,b.x)-tol <= p.x <= max(a.x,b.x)+tol and
            min(a.y,b.y)-tol <= p.y <= max(a.y,b.y)+tol)


def _intersects(a: Point, b: Point, c: Point, d: Point, tol: float) -> bool:
    # The exact test below accepts endpoint contact within tol on both axes.
    # Only reject boxes separated by more than that same tolerance; retain
    # every crossing, touching, collinear and near-contact candidate.
    if (max(a.x, b.x) + tol < min(c.x, d.x)
            or max(c.x, d.x) + tol < min(a.x, b.x)
            or max(a.y, b.y) + tol < min(c.y, d.y)
            or max(c.y, d.y) + tol < min(a.y, b.y)):
        return False
    ab=tol*a.distance_to(b); cd=tol*c.distance_to(d)
    c1,c2,c3,c4=_cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b)
    proper=((c1>ab and c2 < -ab) or (c1 < -ab and c2>ab)) and ((c3>cd and c4 < -cd) or (c3 < -cd and c4>cd))
    return proper or any((_on_segment(a,b,c,tol),_on_segment(a,b,d,tol),_on_segment(c,d,a,tol),_on_segment(c,d,b,tol)))


@lru_cache(maxsize=1024)
def _validate_polygon_cached(
        coordinates: tuple[tuple[float, float], ...], tol: float) -> float:
    points = [Point(x, y) for x, y in coordinates]
    n=len(points)
    if n < 3 or any(not math.isfinite(v) for p in points for v in (p.x,p.y)):
        raise ValueError("A finite polygon with at least three vertices is required.")
    for i,a in enumerate(points):
        b=points[(i+1)%n]
        if a.distance_to(b) <= tol:
            raise ValueError("Contour has a zero-length or sub-tolerance edge.")
        for j in range(i+1,n):
            if j==i+1 or (i==0 and j==n-1):
                continue
            if _intersects(a,b,points[j],points[(j+1)%n],tol):
                raise ValueError("Contour self-intersects or features collide within tolerance.")
        prev=points[(i-1)%n]
        if abs(_cross(prev,a,b)) <= tol*prev.distance_to(a):
            if (prev.x-a.x)*(b.x-a.x)+(prev.y-a.y)*(b.y-a.y)>0:
                raise ValueError("Contour doubles back along an adjacent edge.")
    origin=points[0]
    area2=sum(_cross(origin,points[i],points[(i+1)%n]) for i in range(n))
    if abs(area2) <= tol*sum(points[i].distance_to(points[(i+1)%n]) for i in range(n)):
        raise ValueError("Contour area is degenerate within tolerance.")
    return area2


def _normalized_point_key(points: list[Point]) -> tuple[tuple[float, float], ...]:
    """Translation-invariant exact key for immutable geometry calculations."""
    if not points:
        return ()
    origin = points[0]
    return tuple((point.x - origin.x, point.y - origin.y) for point in points)


def _validate_polygon(points: list[Point], tol: float) -> float:
    # DXF letters are repeatedly deep-copied during hover, click, preview and
    # Apply. Their coordinates remain immutable within each calculation, so a
    # bounded exact geometry key avoids millions of duplicate segment tests.
    return _validate_polygon_cached(_normalized_point_key(points), float(tol))


def _contour_geometry(points: list[Point]) -> tuple[list[float], list[float], float]:
    lengths = [point.distance_to(points[(index + 1) % len(points)]) for index, point in enumerate(points)]
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    return lengths, cumulative, cumulative[-1]


def _point_at_distance(
    points: list[Point], lengths: list[float], cumulative: list[float], distance: float,
) -> tuple[Point, int, float]:
    perimeter = cumulative[-1]
    value = distance % perimeter
    for index, length in enumerate(lengths):
        if value <= cumulative[index + 1] or index == len(lengths) - 1:
            fraction = max(0.0, min(1.0, (value - cumulative[index]) / length))
            start, end = points[index], points[(index + 1) % len(points)]
            return Point(
                start.x + (end.x - start.x) * fraction,
                start.y + (end.y - start.y) * fraction,
            ), index, fraction
    raise AssertionError("Closed contour distance could not be resolved.")


def _logical_bent_tab_edges_uncached(
    source_back: Polyline,
    tolerance: float,
    *,
    corner_angle_deg: float = 15.0,
    straight_angle_deg: float = 1.0,
) -> tuple[BentTabLogicalEdge, ...]:
    """Recognize visual contour runs without treating tessellation as ends.

    A sharp turn is a logical endpoint. Collinear fragments form one straight
    run; a chain of small turns forms one curved run. The thresholds are
    geometric recognition tolerances, not tab-placement or structural rules.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Positive finite tolerance required.")
    if not 0 < corner_angle_deg < 180 or not 0 <= straight_angle_deg < corner_angle_deg:
        raise ValueError("Logical-edge angle tolerances are invalid.")
    if not isinstance(source_back, Polyline) or not source_back.closed:
        raise ValueError("Bent-tab placement requires one closed Polyline back outline.")
    points = list(source_back.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= tolerance:
        points.pop()
    _validate_polygon(points, tolerance)
    lengths, cumulative, perimeter = _contour_geometry(points)
    directions = [
        ((points[(index + 1) % len(points)].x - point.x) / lengths[index],
         (points[(index + 1) % len(points)].y - point.y) / lengths[index])
        for index, point in enumerate(points)
    ]
    corner_limit = math.radians(corner_angle_deg)
    forced_breaks = {
        int(value) for value in source_back.metadata.get("logical_edge_break_vertices", ())
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(points)
    }
    breaks: list[int] = []
    for vertex in range(len(points)):
        incoming = directions[(vertex - 1) % len(points)]
        outgoing = directions[vertex]
        dot = max(-1.0, min(1.0, incoming[0] * outgoing[0] + incoming[1] * outgoing[1]))
        if vertex in forced_breaks or math.acos(dot) >= corner_limit:
            breaks.append(vertex)

    def straight_run(indices: list[int]) -> bool:
        first = directions[indices[0]]
        limit = math.sin(math.radians(straight_angle_deg)) + tolerance
        return all(
            first[0] * direction[0] + first[1] * direction[1] > 0
            and abs(first[0] * direction[1] - first[1] * direction[0]) <= limit
            for direction in (directions[index] for index in indices)
        )

    if not breaks:
        indices = list(range(len(points)))
        return (BentTabLogicalEdge(0, tuple(indices), 0.0, perimeter, straight_run(indices), True),)
    runs: list[BentTabLogicalEdge] = []
    for position, start_vertex in enumerate(breaks):
        end_vertex = breaks[(position + 1) % len(breaks)]
        indices: list[int] = []
        index = start_vertex
        while index != end_vertex or not indices:
            indices.append(index)
            index = (index + 1) % len(points)
        length = sum(lengths[index] for index in indices)
        runs.append(BentTabLogicalEdge(
            start_vertex,
            tuple(indices),
            cumulative[start_vertex],
            length,
            straight_run(indices),
            False,
        ))
    return tuple(runs)


@lru_cache(maxsize=1024)
def _logical_bent_tab_edges_cached(
        coordinates: tuple[tuple[float, float], ...],
        tolerance: float,
        corner_angle_deg: float,
        straight_angle_deg: float,
        forced_breaks: tuple[int, ...],
) -> tuple[BentTabLogicalEdge, ...]:
    source = Polyline(
        id="logical-edge-cache", layer="", closed=True,
        points=[Point(x, y) for x, y in coordinates],
        metadata={"logical_edge_break_vertices": forced_breaks},
    )
    return _logical_bent_tab_edges_uncached(
        source, tolerance,
        corner_angle_deg=corner_angle_deg,
        straight_angle_deg=straight_angle_deg,
    )


def logical_bent_tab_edges(
    source_back: Polyline,
    tolerance: float,
    *,
    corner_angle_deg: float = 15.0,
    straight_angle_deg: float = 1.0,
) -> tuple[BentTabLogicalEdge, ...]:
    """Recognize contour runs with a bounded exact-geometry cache."""
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Positive finite tolerance required.")
    if not 0 < corner_angle_deg < 180 or not 0 <= straight_angle_deg < corner_angle_deg:
        raise ValueError("Logical-edge angle tolerances are invalid.")
    if not isinstance(source_back, Polyline) or not source_back.closed:
        raise ValueError("Bent-tab placement requires one closed Polyline back outline.")
    points = list(source_back.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= tolerance:
        points.pop()
    forced_breaks = tuple(sorted({
        int(value) for value in source_back.metadata.get("logical_edge_break_vertices", ())
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(points)
    }))
    return _logical_bent_tab_edges_cached(
        _normalized_point_key(points), float(tolerance),
        float(corner_angle_deg), float(straight_angle_deg), forced_breaks,
    )


def _logical_edge_for_anchor(
    logical_edges: tuple[BentTabLogicalEdge, ...], anchor: BentTabAnchor,
) -> BentTabLogicalEdge:
    for logical_edge in logical_edges:
        if anchor.edge_index in logical_edge.edge_indices:
            return logical_edge
    raise ValueError("The tab anchor does not identify a recognized logical contour edge.")


def _logical_position(
    logical_edge: BentTabLogicalEdge,
    anchor: BentTabAnchor,
    lengths: list[float],
) -> float:
    distance = 0.0
    for edge_index in logical_edge.edge_indices:
        if edge_index == anchor.edge_index:
            return distance + lengths[edge_index] * anchor.fraction
        distance += lengths[edge_index]
    raise ValueError("The tab anchor is not on its resolved logical contour edge.")


def _anchor_on_logical_edge(
    logical_edge: BentTabLogicalEdge,
    distance: float,
    points: list[Point],
    lengths: list[float],
) -> tuple[BentTabAnchor, Point]:
    remaining = max(0.0, min(logical_edge.length, distance))
    for position, edge_index in enumerate(logical_edge.edge_indices):
        length = lengths[edge_index]
        if remaining <= length or position == len(logical_edge.edge_indices) - 1:
            fraction = max(0.0, min(1.0, remaining / length))
            start, end = points[edge_index], points[(edge_index + 1) % len(points)]
            point = Point(
                start.x + (end.x - start.x) * fraction,
                start.y + (end.y - start.y) * fraction,
            )
            return BentTabAnchor(edge_index, fraction), point
        remaining -= length
    raise AssertionError("Logical contour distance could not be resolved.")


def manual_bent_tab_effective_settings(
    source_back: Polyline,
    anchors: Iterable[BentTabAnchor],
    settings: BentTabSettings,
    *,
    short_straight_center_threshold: float = 4.0,
) -> tuple[BentTabSettings, float | None]:
    """Relax only the endpoint preference for a fitting short manual tab.

    Automatic proposals continue to use ``settings.edge_end_clearance`` as a
    hard layout rule.  A manual click on a short straight is different: the
    operator has selected the edge explicitly and the UI centers the tab.
    When its complete tab/root footprint fits but the preferred empty corner
    margin does not, use the largest symmetric margin that actually exists.

    Collision, self-intersection, cutout, overlap, and physical-footprint
    validation are intentionally unchanged and remain hard failures in
    ``generate_bent_tab_back``.
    """
    _validate_settings(settings)
    if not math.isfinite(short_straight_center_threshold) or short_straight_center_threshold < 0:
        raise ValueError("Short-straight auto-center threshold must be finite and nonnegative.")
    requested = tuple(anchors)
    if not requested or settings.edge_end_clearance <= 0:
        return settings, None
    points = list(source_back.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= settings.tolerance:
        points.pop()
    _validate_polygon(points, settings.tolerance)
    lengths, _cumulative, _perimeter = _contour_geometry(points)
    logical_edges = logical_bent_tab_edges(source_back, settings.tolerance)
    half_footprint = settings.width / 2.0 + 2.0 * settings.root_relief_radius
    effective_clearance = settings.edge_end_clearance
    relaxed = False
    for anchor in requested:
        logical_edge = _logical_edge_for_anchor(logical_edges, anchor)
        if (
            not logical_edge.straight
            or logical_edge.closed_loop
            or logical_edge.length > short_straight_center_threshold + settings.tolerance
        ):
            continue
        local_position = _logical_position(logical_edge, anchor, lengths)
        available = min(
            local_position - half_footprint,
            logical_edge.length - local_position - half_footprint,
        )
        # A negative margin means the footprint itself does not fit; leave the
        # setting alone so the normal geometric error remains authoritative.
        if available < -settings.tolerance:
            continue
        candidate = max(0.0, available - settings.tolerance * 2.0)
        if candidate + settings.tolerance < effective_clearance:
            effective_clearance = candidate
            relaxed = True
    if not relaxed:
        return settings, None
    return replace(settings, edge_end_clearance=effective_clearance), effective_clearance


def manual_bent_tab_placement(
    source_back: Polyline,
    click: Point,
    settings: BentTabSettings,
    *,
    existing_anchors: Iterable[BentTabAnchor] = (),
    inner_cutouts: Iterable[Polyline] = (),
    short_straight_center_threshold: float = 4.0,
) -> BentTabManualPlacement:
    """Resolve one manual click and prove the resulting tab geometry is valid."""
    _validate_settings(settings)
    if not math.isfinite(short_straight_center_threshold) or short_straight_center_threshold < 0:
        raise ValueError("Short-straight auto-center threshold must be finite and nonnegative.")
    points = list(source_back.points)
    if len(points) > 1 and points[0].distance_to(points[-1]) <= settings.tolerance:
        points.pop()
    _validate_polygon(points, settings.tolerance)
    lengths, _cumulative, _perimeter = _contour_geometry(points)
    nearest: tuple[float, BentTabAnchor, Point] | None = None
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        dx, dy = end.x - start.x, end.y - start.y
        length_squared = dx * dx + dy * dy
        fraction = max(0.0, min(1.0, ((click.x - start.x) * dx + (click.y - start.y) * dy) / length_squared))
        projected = Point(start.x + dx * fraction, start.y + dy * fraction)
        candidate = (click.distance_to(projected), BentTabAnchor(index, fraction), projected)
        if nearest is None or candidate[0] < nearest[0]:
            nearest = candidate
    assert nearest is not None
    distance, anchor, projected = nearest
    logical_edge = _logical_edge_for_anchor(logical_bent_tab_edges(source_back, settings.tolerance), anchor)
    auto_centered = bool(logical_edge.straight and logical_edge.length <= short_straight_center_threshold + settings.tolerance)
    if auto_centered:
        anchor, projected = _anchor_on_logical_edge(logical_edge, logical_edge.length / 2.0, points, lengths)
    prior_anchors = tuple(existing_anchors)
    all_anchors = (*prior_anchors, anchor)
    effective_settings, clearance_override = manual_bent_tab_effective_settings(
        source_back,
        all_anchors,
        settings,
        short_straight_center_threshold=short_straight_center_threshold,
    )
    generate_bent_tab_back(
        source_back,
        all_anchors,
        effective_settings,
        inner_cutouts=inner_cutouts,
    )
    return BentTabManualPlacement(
        anchor, projected, logical_edge, distance, auto_centered, clearance_override,
    )


def _root_relief_between(
    start: Point, end: Point, outward_x: float, outward_y: float,
    radius: float, error: float,
) -> list[Point]:
    """Join contour/root points with an exact-radius arc into the material."""
    length = start.distance_to(end)
    if length <= 1e-15:
        return [start, end]
    if length > 2 * radius + 1e-9:
        raise ValueError("The curved contour cannot join the requested root-relief radius at this location.")
    midpoint = Point((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    height = math.sqrt(max(0.0, radius * radius - (length / 2.0) ** 2))
    center = Point(midpoint.x + outward_x * height, midpoint.y + outward_y * height)
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    positive = (end_angle - start_angle) % (2 * math.pi)
    alternatives = (positive, positive - 2 * math.pi)
    # Choose the arc whose midpoint lies toward the material interior.
    delta = min(
        alternatives,
        key=lambda candidate: (
            (center.x + radius * math.cos(start_angle + candidate / 2.0) - midpoint.x) * outward_x
            + (center.y + radius * math.sin(start_angle + candidate / 2.0) - midpoint.y) * outward_y,
            abs(candidate),
        ),
    )
    if error <= 0 or error >= radius:
        count = 4
    else:
        step = 4 * math.asin(math.sqrt(error / (2 * radius)))
        count = max(4, math.ceil(abs(delta) / step))
    if count > 256:
        raise ValueError("Curve tolerance requires too many root-relief segments; choose a coarser tolerance.")
    return [
        Point(
            center.x + radius * math.cos(start_angle + delta * index / count),
            center.y + radius * math.sin(start_angle + delta * index / count),
        )
        for index in range(count + 1)
    ]


def generate_bent_tab_back(
    source_back: Polyline,
    anchors: Iterable[BentTabAnchor],
    settings: BentTabSettings,
    *,
    source_references: Iterable[Entity] = (),
    inner_cutouts: Iterable[Polyline] = (),
    widths: Iterable[float] | None = None,
) -> BentTabResult:
    """Return a new cut contour and separate reference-only bend guides.

    The supplied back must already have its intended shrink/clearance applied.
    Face and return references are copied unchanged. Input objects are never
    modified. No keyholes are generated or transferred implicitly. Integrators
    must replace the back's cutting role explicitly and must not send bend
    guides to machining merely because they are Line entities.
    """
    if not isinstance(source_back,Polyline) or not source_back.closed:
        raise ValueError("Bent-tab backs require one closed Polyline outline.")
    if settings.forming is not None:
        settings = replace(settings, flat_projection=settings.forming.flat_projection())
    _validate_settings(settings)
    tol=settings.tolerance
    points=list(source_back.points)
    if len(points)>1 and points[0].distance_to(points[-1])<=tol:
        points.pop()
    area2=_validate_polygon(points,tol)
    holes=tuple(inner_cutouts)
    _validate_holes(points,holes,tol)
    requested=list(anchors)
    if not requested:
        raise ValueError("Select at least one tab anchor.")
    requested_widths = [settings.width] * len(requested) if widths is None else list(widths)
    if len(requested_widths) != len(requested):
        raise ValueError("Each tab anchor requires exactly one tab width.")
    if any(not math.isfinite(width) or width <= 0 for width in requested_widths):
        raise ValueError("Every tab width must be finite and positive.")
    lengths, cumulative, perimeter = _contour_geometry(points)
    logical_edges = logical_bent_tab_edges(source_back, tol)
    resolved: list[dict[str, object]] = []
    for anchor, width in zip(requested, requested_widths):
        if isinstance(anchor.edge_index,bool) or not isinstance(anchor.edge_index,int) or not 0<=anchor.edge_index<len(points):
            raise ValueError("Tab edge index does not identify an original contour edge.")
        if not math.isfinite(anchor.fraction) or not 0<=anchor.fraction<=1:
            raise ValueError("Tab anchor fraction must be between zero and one.")
        logical_edge = _logical_edge_for_anchor(logical_edges, anchor)
        local_position = _logical_position(logical_edge, anchor, lengths)
        half_footprint = width / 2.0 + 2.0 * settings.root_relief_radius
        if 2.0 * half_footprint >= logical_edge.length - tol:
            raise ValueError(
                f"Logical {'straight' if logical_edge.straight else 'curved'} edge is {logical_edge.length:g} units long; "
                f"the tab plus root reliefs needs more than {2.0 * half_footprint:g}."
            )
        if not logical_edge.closed_loop and (
            local_position - half_footprint < settings.edge_end_clearance + tol
            or local_position + half_footprint > logical_edge.length - settings.edge_end_clearance - tol
        ):
            raise ValueError(
                "Tab footprint lacks required clearance from this logical edge's endpoint; "
                "reduce the tab/relief/clearance or click farther from the end."
            )
        center_distance = cumulative[anchor.edge_index] + lengths[anchor.edge_index] * anchor.fraction
        resolved.append({
            "anchor": anchor,
            "logical_edge": logical_edge,
            "center": center_distance,
            "start": center_distance - half_footprint,
            "end": center_distance + half_footprint,
            "width": width,
            "footprint_length": half_footprint * 2.0,
        })
    if any(float(item["footprint_length"]) >= perimeter - tol for item in resolved):
        raise ValueError("The tab footprint consumes the entire source perimeter.")
    ordered_features = sorted(resolved, key=lambda item: float(item["center"]) % perimeter)
    for index, feature in enumerate(ordered_features):
        following = ordered_features[(index + 1) % len(ordered_features)]
        gap = (float(following["center"]) - float(feature["center"])) % perimeter
        required = (
            float(feature["footprint_length"]) / 2.0
            + float(following["footprint_length"]) / 2.0
        )
        if len(ordered_features) > 1 and gap <= required + tol:
            raise ValueError("Tab footprints overlap or touch on the source contour.")
    owner=source_back.id
    group=str(source_back.metadata.get("group_id") or source_back.metadata.get("mounting_part_id") or f"bent_tab_{owner}")
    common={"bent_tab_generated":True,"owner_entity_id":owner,"group_id":group,
            "flat_projection":settings.flat_projection,"tab_width":settings.width,
            "tab_widths":requested_widths,
            "bend_direction":settings.bend_direction,"root_relief_radius":settings.root_relief_radius,
            "tip_corner_radius":settings.tip_corner_radius,"curve_tolerance":settings.curve_tolerance}
    if settings.forming is not None:
        common['bent_tab_forming'] = settings.forming.to_dict()
    sign=1 if area2>0 else -1
    guides=[]
    mounting_holes=[]
    for slot, feature in enumerate(resolved):
        anchor = feature["anchor"]
        logical_edge = feature["logical_edge"]
        width = float(feature["width"])
        assert isinstance(anchor, BentTabAnchor) and isinstance(logical_edge, BentTabLogicalEdge)
        root_start, _edge, _fraction = _point_at_distance(
            points, lengths, cumulative, float(feature["center"]) - width / 2.0,
        )
        root_end, _edge, _fraction = _point_at_distance(
            points, lengths, cumulative, float(feature["center"]) + width / 2.0,
        )
        foot_start, _edge, _fraction = _point_at_distance(points, lengths, cumulative, float(feature["start"]))
        foot_end, _edge, _fraction = _point_at_distance(points, lengths, cumulative, float(feature["end"]))
        chord = root_start.distance_to(root_end)
        if chord <= tol:
            raise ValueError("The curved contour turns too tightly to establish a tab root at this click.")
        ux, uy = (root_end.x - root_start.x) / chord, (root_end.y - root_start.y) / chord
        nx, ny = sign * uy, -sign * ux
        if settings.tip_corner_radius * 2 >= chord - tol:
            raise ValueError(
                "The curved contour shortens the root chord below the requested tip-radius clearance; "
                "use a narrower tab or a flatter location."
            )
        if logical_edge.straight:
            local = _tab_profile(replace(settings, width=width))
            profile = [
                Point(root_start.x + ux*x + nx*y, root_start.y + uy*x + ny*y)
                for x,y in local
            ]
        else:
            error = settings.curve_tolerance or max(tol * 10.0, chord / 1000.0)
            left_relief = (
                _root_relief_between(
                    foot_start, root_start, nx, ny, settings.root_relief_radius, error,
                )
                if settings.root_relief_radius else [root_start]
            )
            curved_settings = replace(
                settings, width=chord, root_relief_radius=0.0,
                tip_corner_radius=min(settings.tip_corner_radius, max(0.0, chord / 2.0 - tol)),
            )
            tab = [
                Point(root_start.x + ux*x + nx*y, root_start.y + uy*x + ny*y)
                for x,y in _tab_profile(curved_settings)
            ]
            right_relief = (
                _root_relief_between(
                    root_end, foot_end, nx, ny, settings.root_relief_radius, error,
                )
                if settings.root_relief_radius else [root_end]
            )
            profile = [*left_relief, *tab[1:], *right_relief[1:]]
        feature["profile"] = profile
        if settings.forming is not None and settings.forming.hole_diameter:
            distance=settings.flat_projection-settings.forming.hole_tip_offset
            mounting_holes.append(Circle(
                id=f'{owner}:bent_tab:hole:{anchor.edge_index}:{anchor.fraction:.12g}',
                layer='BENT_TAB_HOLES',center=Point((root_start.x+root_end.x)/2+nx*distance,
                    (root_start.y+root_end.y)/2+ny*distance),radius=settings.forming.hole_diameter/2,
                metadata={**common,'role':'mounting_hole','bent_tab_mount_hole':True,'machinable':True}))
        guides.append(Line(id=f"{owner}:bent_tab:guide:{anchor.edge_index}:{slot}",layer="BEND_GUIDES",
            metadata={**common,"role":"reference_only","machinable":False,
                      "source_edge_index":anchor.edge_index,"anchor_fraction":anchor.fraction,
                      "logical_edge_start":logical_edge.start_edge_index,
                      "logical_edge_kind":"straight" if logical_edge.straight else "curve",
                      "tab_width":width},
            start=root_start,end=root_end))

    # Put the cyclic seam in the largest untouched gap so a tab can safely sit
    # across the source polyline's first/last vertex without special casing.
    ordered_features = sorted(resolved, key=lambda item: float(item["center"]) % perimeter)
    gaps = []
    for index, feature in enumerate(ordered_features):
        following = ordered_features[(index + 1) % len(ordered_features)]
        end = float(feature["end"]) % perimeter
        following_start = float(following["start"]) % perimeter
        gap = (following_start - end) % perimeter
        gaps.append((gap, end))
    largest_gap, gap_start = max(gaps)
    seam = (gap_start + largest_gap / 2.0) % perimeter
    for feature in resolved:
        start_relative = (float(feature["start"]) - seam) % perimeter
        feature["start_relative"] = start_relative
        feature["end_relative"] = start_relative + float(feature["footprint_length"])
    resolved.sort(key=lambda item: float(item["start_relative"]))

    result: list[Point] = []
    def append_unique(point: Point) -> None:
        if not result or result[-1].distance_to(point) > tol:
            result.append(point)

    vertex_positions = [((distance - seam) % perimeter, point) for distance, point in zip(cumulative[:-1], points)]
    def append_source_segment(start_relative: float, end_relative: float) -> None:
        for _distance, point in sorted(
            (item for item in vertex_positions if start_relative + tol < item[0] < end_relative - tol),
            key=lambda item: item[0],
        ):
            append_unique(point)
        endpoint, _edge, _fraction = _point_at_distance(points, lengths, cumulative, seam + end_relative)
        append_unique(endpoint)

    seam_point, _edge, _fraction = _point_at_distance(points, lengths, cumulative, seam)
    append_unique(seam_point)
    cursor = 0.0
    for feature in resolved:
        start_relative = float(feature["start_relative"])
        end_relative = float(feature["end_relative"])
        append_source_segment(cursor, start_relative)
        for point in feature["profile"]:
            append_unique(point)
        cursor = end_relative
    append_source_segment(cursor, perimeter)
    if len(result) > 1 and result[0].distance_to(result[-1]) <= tol:
        result.pop()
    _validate_polygon(result,tol)
    _validate_holes(result,holes,tol)
    for hole in mounting_holes:
        boundaries=[result,*[list(inner.points) for inner in holes]]
        def distance_to_segment(p,a,b):
            dx,dy=b.x-a.x,b.y-a.y
            fraction=max(0.,min(1.,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy)))
            return p.distance_to(Point(a.x+fraction*dx,a.y+fraction*dy))
        if not _inside(hole.center,result,tol) or any(_inside(hole.center,inner,tol) for inner in boundaries[1:]) or any(
                distance_to_segment(hole.center,a,contour[(i+1)%len(contour)])<=hole.radius+tol
                for contour in boundaries for i,a in enumerate(contour)):
            raise ValueError('Polycarbonate tab mounting hole does not clear the flat cut outline/cutouts.')
    for i,hole in enumerate(mounting_holes):
        if any(hole.center.distance_to(other.center)<=hole.radius+other.radius+tol for other in mounting_holes[i+1:]):
            raise ValueError('Polycarbonate tab mounting holes overlap.')
    cut=Polyline(id=f"{owner}:bent_tab:cut",layer=source_back.layer,
                 metadata={**deepcopy(source_back.metadata),**common,"role":"cut_outline"},points=result,closed=True)
    if settings.forming is None:cut.metadata.pop('bent_tab_forming',None)
    warning = ('UNQUALIFIED clear-polycarbonate prototype: explicit allowance only; coupon bend/fit/strength/halo validation required.'
               if settings.forming is not None else
               'Flat layout only: no bend allowance, structural/spacing certification, forming validation, or keyhole generation.')
    return BentTabResult(deepcopy(source_back),tuple(deepcopy(tuple(source_references))),cut,tuple(guides),tuple(mounting_holes),owner,group,
        (warning,),tuple(deepcopy(holes)))


@dataclass(frozen=True)
class BentTabShopPreset:
    """Editable shop recipe; inch dimensions are not structural recommendations."""
    name: str
    face_return_inset_in: float
    back_total_inset_in: float
    tab_width_in: float
    flat_projection_in: float
    root_relief_radius_in: float
    tip_corner_radius_in: float

    def __post_init__(self):
        dimensions=(self.face_return_inset_in,self.back_total_inset_in,self.tab_width_in,
                    self.flat_projection_in,self.root_relief_radius_in,self.tip_corner_radius_in)
        if not self.name.strip() or any(not math.isfinite(value) or value<0 for value in dimensions):
            raise ValueError("Preset name and finite nonnegative dimensions are required.")
        if self.tab_width_in<=0 or self.flat_projection_in<=0:
            raise ValueError("Preset width and flat projection must be positive.")

    def settings(self, *, units_per_inch: float, edge_end_clearance: float,
                 tolerance: float, curve_tolerance: float, bend_direction: str) -> BentTabSettings:
        if not math.isfinite(units_per_inch) or units_per_inch<=0:
            raise ValueError("units_per_inch must be positive and finite.")
        return BentTabSettings(self.tab_width_in*units_per_inch,self.flat_projection_in*units_per_inch,
            edge_end_clearance,self.root_relief_radius_in*units_per_inch,self.tip_corner_radius_in*units_per_inch,
            bend_direction,tolerance,curve_tolerance)


COMMON_SHOP_PRESET = BentTabShopPreset("Common integral bent-tab back",.070,.320,1.0,1.0,.125,.0625)


def _arc(cx: float, cy: float, radius: float, start: float, end: float, error: float) -> list[tuple[float,float]]:
    step=4*math.asin(math.sqrt(error/(2*radius)))
    count=max(2,math.ceil(abs(end-start)/step))
    if count>256:
        raise ValueError("Curve tolerance requires too many arc segments; choose a coarser tolerance.")
    return [(cx+radius*math.cos(start+(end-start)*i/count),cy+radius*math.sin(start+(end-start)*i/count)) for i in range(count+1)]


def _tab_profile(settings: BentTabSettings) -> list[tuple[float,float]]:
    w,p,r,t=settings.width,settings.flat_projection,settings.root_relief_radius,settings.tip_corner_radius
    error=settings.curve_tolerance
    # Local +x follows original edge; local +y points out of material.
    # Root semicircles cut into material adjacent to either side of the flange.
    result=_arc(-r,0,r,math.pi,2*math.pi,error) if r else [(0,0)]
    if t:
        result.extend(_arc(t,p-t,t,math.pi,math.pi/2,error))
        result.extend(_arc(w-t,p-t,t,math.pi/2,0,error))
    else:
        result.extend(((0,p),(w,p)))
    result.extend(_arc(w+r,0,r,math.pi,2*math.pi,error) if r else [(w,0)])
    return result


def _inside(p: Point, contour: list[Point], tol: float) -> bool:
    inside=False
    for i,a in enumerate(contour):
        b=contour[(i+1)%len(contour)]
        if _on_segment(a,b,p,tol): return False
        if (a.y>p.y)!=(b.y>p.y) and p.x < a.x+(p.y-a.y)*(b.x-a.x)/(b.y-a.y):
            inside=not inside
    return inside


def _contours_intersect(a: list[Point], b: list[Point], tol: float) -> bool:
    return any(_intersects(p,a[(i+1)%len(a)],q,b[(j+1)%len(b)],tol) for i,p in enumerate(a) for j,q in enumerate(b))


def _polygon_points(contour: Polyline, tol: float) -> list[Point]:
    if not isinstance(contour,Polyline) or not contour.closed:
        raise ValueError("Cutouts must be closed straight-edged Polylines.")
    points=list(contour.points)
    if len(points)>1 and points[0].distance_to(points[-1])<=tol: points.pop()
    _validate_polygon(points,tol)
    return points


def _validate_holes(outer: list[Point], holes: tuple[Polyline,...], tol: float) -> None:
    validated=[]
    for hole in holes:
        pts=_polygon_points(hole,tol)
        if not all(_inside(p,outer,tol) for p in pts) or _contours_intersect(outer,pts,tol):
            raise ValueError("Inner cutout touches or crosses the back or tab relief contour.")
        for earlier in validated:
            if _contours_intersect(earlier,pts,tol) or _inside(pts[0],earlier,tol) or _inside(earlier[0],pts,tol):
                raise ValueError("Inner cutouts overlap, touch, or nest.")
        validated.append(pts)


@dataclass(frozen=True)
class BentTabPlacementRules:
    minimum_count: int
    max_unsupported_perimeter_span: float
    corner_clearance: float
    minimum_separation: float  # center spacing, both perimeter and straight-line distance
    candidate_budget: int = 128  # computational cap, not a production spacing default


@dataclass(frozen=True)
class BentTabAnchorProposal:
    anchors: tuple[BentTabAnchor,...]
    diagnostics: tuple[str,...]
    constraints_met: bool
    largest_perimeter_gap: float
    candidates_evaluated: int
    # No approval or mutation: caller presents anchors for user adjustment.


def _max_gap(positions: list[float], perimeter: float) -> float:
    if not positions: return perimeter
    ordered=sorted(positions)
    return max([b-a for a,b in zip(ordered,ordered[1:])]+[perimeter-ordered[-1]+ordered[0]])


def propose_bent_tab_anchors(source_back: Polyline, settings: BentTabSettings,
        rules: BentTabPlacementRules, *, inner_cutouts: Iterable[Polyline]=(),
        eligible_edge_indices: Iterable[int] | None=None,
        progress_callback: Callable[[str, float], None] | None = None,
        cancel_check: Callable[[], bool] | None = None) -> BentTabAnchorProposal:
    """Bounded geometric placement heuristic, never a sufficient-load tab count.

    Exterior contour only; holes are keep-outs. Interior-edge tabs need a future
    explicit separate API. Rules are caller-selected, with no structural default.
    Greedy discrete sampling may report unmet constraints even when an unsampled
    feasible placement exists. Returned anchors remain fully user-editable.
    """
    if isinstance(rules.minimum_count,bool) or not isinstance(rules.minimum_count,int) or rules.minimum_count<1:
        raise ValueError("Minimum count must be a positive integer.")
    if isinstance(rules.candidate_budget,bool) or not isinstance(rules.candidate_budget,int) or not 1<=rules.candidate_budget<=512:
        raise ValueError("Candidate budget must be an integer from 1 to 512.")
    if any(not math.isfinite(v) for v in (rules.max_unsupported_perimeter_span,rules.corner_clearance,rules.minimum_separation)) or rules.max_unsupported_perimeter_span<=0 or rules.corner_clearance<0 or rules.minimum_separation<=0:
        raise ValueError("Span/separation must be positive and corner clearance nonnegative.")
    tol=settings.tolerance
    if not math.isfinite(tol) or tol<=0: raise ValueError("Positive finite tolerance required.")
    points=_polygon_points(source_back,tol)
    holes=tuple(inner_cutouts)
    _validate_holes(points,holes,tol)
    effective=replace(settings,edge_end_clearance=max(settings.edge_end_clearance,rules.corner_clearance))
    # Validate settings before sampling, including failure cases with no eligible edges.
    _validate_settings(effective)
    lengths=[a.distance_to(points[(i+1)%len(points)]) for i,a in enumerate(points)]
    perimeter=sum(lengths)
    # Reliable contract: caller chooses terminal/flat edge indices. With no
    # explicit selection, only axis-aligned flats on the overall bounds are
    # suggested, not arbitrary perimeter edges or certified stroke terminals.
    heuristic=eligible_edge_indices is None
    eligible=set(suggest_extreme_flat_edges(source_back,tol)) if heuristic else set(eligible_edge_indices)
    if any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(points) for i in eligible):
        raise ValueError("Eligible edge indices must identify exterior contour edges.")
    raw=[]; offset=0.; short=0; by_edge_candidates={}
    margin=effective.width/2+2*effective.root_relief_radius+effective.edge_end_clearance+2*tol
    for i,length in enumerate(lengths):
        if i in eligible:
            usable=length-2*margin
            if usable<0: short+=1
            else:
                step=max(rules.minimum_separation,effective.width+4*effective.root_relief_radius)
                count=min(rules.candidate_budget, max(1,math.ceil(usable/step)+1))
                # Center first; then uniform samples including both eligible limits.
                distances=[length/2]+([margin+usable*j/(count-1) for j in range(count)] if count>1 else [])
                group=[]
                for d in distances:
                    if not any(abs(existing-d)<=tol for _,existing,_ in group): group.append((i,d,offset+d))
                by_edge_candidates[i]=group
        offset+=length
    # Round-robin preserves every selected end's center before adding samples.
    for j in range(max((len(g) for g in by_edge_candidates.values()),default=0)):
        for i in sorted(by_edge_candidates):
            group=by_edge_candidates[i]
            if j<len(group): raw.append(group[j])
    truncated=len(raw)>rules.candidate_budget
    raw=raw[:rules.candidate_budget]
    viable=[]; rejected=0
    stride = max(1, len(raw) // 20)
    for candidate_index, (edge,distance,position) in enumerate(raw, start=1):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Bent-tab generation canceled.")
        if progress_callback is not None and (candidate_index == 1 or candidate_index == len(raw) or candidate_index % stride == 0):
            progress_callback(
                f"checking tab position {candidate_index} of {len(raw)}",
                candidate_index / max(1, len(raw)),
            )
        anchor=BentTabAnchor(edge,distance/lengths[edge])
        try: generate_bent_tab_back(source_back,[anchor],effective,inner_cutouts=holes)
        except ValueError: rejected+=1; continue
        a,b=points[edge],points[(edge+1)%len(points)]
        center=Point(a.x+(b.x-a.x)*anchor.fraction,a.y+(b.y-a.y)*anchor.fraction)
        viable.append((anchor,position,center))
    selected=[]; blocked=set(); missed_edges=[]
    # Prefer one centered placement on every chosen flat/end. Never relocate
    # an unavailable chosen end onto a neighboring edge without user review.
    for edge in sorted(eligible):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Bent-tab generation canceled.")
        choices=sorted([(index,item) for index,item in enumerate(viable) if item[0].edge_index==edge],key=lambda pair:(abs(pair[1][0].fraction-.5),pair[1][0].fraction))
        found=False
        for index,item in choices:
            if any(min(abs(item[1]-x[1]),perimeter-abs(item[1]-x[1]))<rules.minimum_separation-tol or item[2].distance_to(x[2])<rules.minimum_separation-tol for x in selected): continue
            try: generate_bent_tab_back(source_back,[x[0] for x in selected]+[item[0]],effective,inner_cutouts=holes)
            except ValueError: blocked.add(index); continue
            selected.append(item); found=True; break
        if not found: missed_edges.append(edge)
    while viable:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Bent-tab generation canceled.")
        positions=[x[1] for x in selected]
        if len(selected)>=rules.minimum_count and _max_gap(positions,perimeter)<=rules.max_unsupported_perimeter_span+tol: break
        choices=[]
        for index,candidate in enumerate(viable):
            if index in blocked or candidate in selected: continue
            if any(min(abs(candidate[1]-item[1]),perimeter-abs(candidate[1]-item[1]))<rules.minimum_separation-tol or candidate[2].distance_to(item[2])<rules.minimum_separation-tol for item in selected): continue
            choices.append((_max_gap(positions+[candidate[1]],perimeter),index,candidate))
        if not choices: break
        _,index,candidate=min(choices,key=lambda x:(x[0],x[1]))
        try: generate_bent_tab_back(source_back,[x[0] for x in selected]+[candidate[0]],effective,inner_cutouts=holes)
        except ValueError: blocked.add(index); continue
        selected.append(candidate)
    gap=_max_gap([x[1] for x in selected],perimeter)
    met=not missed_edges and len(selected)>=rules.minimum_count and gap<=rules.max_unsupported_perimeter_span+tol
    diagnostics=["Geometric proposal only; tab count is not a mechanical sufficiency assessment."]
    if heuristic: diagnostics.append("Edges suggested only because they are axis-aligned flats on the exterior bounds; confirm actual letter ends manually.")
    if not eligible: diagnostics.append("No eligible flat ends found; select original exterior edge indices explicitly.")
    if missed_edges: diagnostics.append("No fit on requested/suggested end edges: "+", ".join(str(i) for i in missed_edges)+"; no neighboring edges were substituted.")
    if short: diagnostics.append(f"{short} eligible edges were too short for tab, reliefs and corner clearance.")
    if rejected: diagnostics.append(f"{rejected} candidate positions failed contour/cutout clearance checks.")
    if blocked: diagnostics.append(f"{len(blocked)} candidate combinations collided.")
    if truncated: diagnostics.append("Candidate budget reached; later contour positions were not searched.")
    if len(selected)<rules.minimum_count: diagnostics.append(f"Only {len(selected)} tabs fit the sampled positions; minimum requested is {rules.minimum_count}.")
    if gap>rules.max_unsupported_perimeter_span+tol: diagnostics.append(f"Largest perimeter gap {gap:g} exceeds requested {rules.max_unsupported_perimeter_span:g} project units.")
    if not met: diagnostics.append("Adjust rules, eligible edges or anchors; this bounded heuristic does not prove no feasible layout exists.")
    return BentTabAnchorProposal(tuple(x[0] for x in sorted(selected,key=lambda x:x[1])),tuple(diagnostics),met,gap,len(raw))




def _validate_settings(settings: BentTabSettings) -> None:
    values=(settings.width,settings.flat_projection,settings.edge_end_clearance,
            settings.root_relief_radius,settings.tip_corner_radius,settings.tolerance)
    if any(not math.isfinite(v) for v in values):
        raise ValueError("All dimensions and tolerance must be finite.")
    tol=settings.tolerance
    if tol<=0 or settings.width<=tol or settings.flat_projection<=tol or settings.edge_end_clearance<0:
        raise ValueError("Width/projection must exceed positive tolerance; clearance cannot be negative.")
    if settings.root_relief_radius<0 or settings.tip_corner_radius<0:
        raise ValueError("Radii cannot be negative.")
    if settings.tip_corner_radius*2 >= settings.width-tol or settings.tip_corner_radius >= settings.flat_projection-tol:
        raise ValueError("Tip radius must leave positive straight tip and side segments.")
    if settings.root_relief_radius or settings.tip_corner_radius:
        if settings.curve_tolerance is None or not math.isfinite(settings.curve_tolerance) or settings.curve_tolerance <= tol:
            raise ValueError("Rounded geometry requires explicit curve tolerance exceeding geometric tolerance.")
        if settings.curve_tolerance >= min(r for r in (settings.root_relief_radius,settings.tip_corner_radius) if r>0):
            raise ValueError("Curve tolerance must be smaller than every nonzero radius.")
    if settings.bend_direction not in {"front","back","unspecified"}:
        raise ValueError("Bend direction must be front, back, or explicitly unspecified.")


def suggest_extreme_flat_edges(source_back: Polyline, tolerance: float) -> tuple[int,...]:
    """Heuristic candidates only, not stroke-end recognition.

    Horizontal/vertical edges on the global contour bounds. For a typical
    upright A this can identify its top and both bottom flats. Rotated or
    non-axis-aligned ends require explicit selection by original edge index.
    """
    if not math.isfinite(tolerance) or tolerance<=0: raise ValueError("Positive tolerance required.")
    points=_polygon_points(source_back,tolerance)
    xmin,xmax=min(p.x for p in points),max(p.x for p in points)
    ymin,ymax=min(p.y for p in points),max(p.y for p in points)
    indices=[]
    for i,a in enumerate(points):
        b=points[(i+1)%len(points)]
        horizontal=abs(a.y-b.y)<=tolerance and any(abs(a.y-y)<=tolerance and abs(b.y-y)<=tolerance for y in (ymin,ymax))
        vertical=abs(a.x-b.x)<=tolerance and any(abs(a.x-x)<=tolerance and abs(b.x-x)<=tolerance for x in (xmin,xmax))
        if horizontal or vertical: indices.append(i)
    return tuple(indices)
