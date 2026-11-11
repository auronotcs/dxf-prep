"""Linear, conservative detection of microscopic imported contour reversals.

Not a general simplifier: ordinary short segments, corners and small features
are left alone. A candidate must reverse two much longer, nearly parallel
neighbours. Removing one endpoint changes the curve by at most the tiny edge
length. Candidates sharing a vertex are deliberately left for manual review.
"""
import math

from .model import Polyline
from .units import unit_scale


MICRO_BACKTRACK_INCHES = .0005


def backtrack_indices(points, *, closed=False, tolerance=.0005):
    """Return endpoints to remove, in one pass and without changing points."""
    n = len(points)
    if n < 4 or not math.isfinite(tolerance) or tolerance <= 0:
        return []
    candidates = []
    for b in (range(n) if closed else range(1, n - 2)):
        a, c, d = (b - 1) % n, (b + 1) % n, (b + 2) % n
        p, q, r, s = points[a], points[b], points[c], points[d]
        u = (q.x-p.x, q.y-p.y)
        v = (r.x-q.x, r.y-q.y)
        w = (s.x-r.x, s.y-r.y)
        lu, lv, lw = (math.hypot(*edge) for edge in (u, v, w))
        if not all(math.isfinite(length) for length in (lu, lv, lw)):
            continue
        if not (0 < lv <= tolerance and lv <= .01 * min(lu, lw)):
            continue
        dot = lambda x, y: x[0]*y[0] + x[1]*y[1]
        if (dot(u, v) <= -.95*lu*lv and dot(v, w) <= -.95*lv*lw
                and dot(u, w) >= math.cos(math.radians(15))*lu*lw):
            candidates.append(c)
    # Never combine adjacent edits: each accepted edit retains both of its
    # long neighbours, keeping the local deviation bound independently valid.
    candidate_set = set(candidates)
    return [i for i in candidates
            if not any((i+delta) % n in candidate_set for delta in (-2, -1, 1, 2))]


def remove_micro_backtracks(points, *, closed=False, tolerance=.0005):
    removed = set(backtrack_indices(points, closed=closed, tolerance=tolerance))
    return [p for i, p in enumerate(points) if i not in removed], len(removed)


def micro_backtrack_diagnostics(entities, units='in'):
    from .geometry_diagnostics import GeometryDiagnostic
    tolerance = MICRO_BACKTRACK_INCHES * unit_scale('in', units)
    issues = []
    for entity in entities:
        if not isinstance(entity, Polyline):
            continue
        if entity.metadata.get('reference_only') or entity.metadata.get('mounting_generated'):
            continue
        for i in backtrack_indices(entity.points, closed=entity.closed, tolerance=tolerance):
            length = entity.points[i].distance_to(entity.points[i-1])
            issues.append(GeometryDiagnostic(
                'warning', 'geometry.micro_backtrack',
                f'Microscopic contour backtrack ({length:.6g} {units}); '
                'repair recommended before cutter compensation.',
                entity.points[i], (entity.id,), ((i-1) % len(entity.points),)))
    return issues
