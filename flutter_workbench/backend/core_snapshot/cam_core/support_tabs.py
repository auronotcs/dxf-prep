"""Same-budget support coverage on closed cutter contours.

Sample a bounded set of outline targets, then relocate existing centers in two
coordinate sweeps to reduce their longest unsupported XY reach. No extra tabs,
meshing, toolpath trials, subset search, or claimed mechanical strength model.
The existing spacing budget and full-width corner avoidance remain constraints.
"""
from bisect import bisect_right
from functools import lru_cache
import math

try:
    import numpy as np
except ImportError:  # Portable core fallback; shipped runtime includes numpy.
    np = None

from .smooth_tabs import intervals, legal_spans, nearest_legal, planned_intervals as smooth_intervals

MODE = 'support_aware'
MAX_TARGETS = 128
MAX_CANDIDATES = 192


def centers_from_intervals(spans, total):
    spans = list(spans)
    centers = []
    if len(spans) > 1 and spans[0][0] == 0 and abs(spans[-1][1]-total) < total*1e-10:
        centers.append(((spans[-1][0]+spans[0][1]+total)/2) % total)
        spans = spans[1:-1]
    centers.extend((a+b)/2 for a,b in spans)
    return sorted(centers)


def _ring(coordinates):
    points = []
    for point in coordinates:
        if not points or math.dist(point, points[-1]) > 1e-10:
            points.append(point)
    if len(points)>1 and math.dist(points[0],points[-1]) < 1e-10:
        points.pop()
    return points


def _lengths(points):
    cumulative = [0.]
    for a,b in zip(points, points[1:]):
        cumulative.append(cumulative[-1]+math.dist(a,b))
    return cumulative


def _at(points, cumulative, distance):
    distance %= cumulative[-1]
    i = min(bisect_right(cumulative, distance)-1, len(points)-2)
    t = (distance-cumulative[i])/(cumulative[i+1]-cumulative[i])
    a,b = points[i:i+2]
    return (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)


@lru_cache(maxsize=128)
def _canonical_centers(points, width, requested_count, max_spacing):
    closed = (*points, points[0])
    cumulative = _lengths(closed)
    total, legal = legal_spans(closed, width)
    baseline = smooth_intervals(closed, width, requested_count, max_spacing)
    centers = centers_from_intervals(baseline, total)
    count = len(centers)
    if not centers or not legal:
        return tuple(centers)
    # Bounded work independent of imported vertex density. Keep all centers so
    # the baseline is always available; dense, already well-supported rings do
    # not need an expensive additional search.
    if count > MAX_TARGETS // 2:
        return tuple(centers)
    target_count = min(MAX_TARGETS, max(64, count*12))
    distances = [i*total/target_count for i in range(target_count)]
    # Include extremal vertices so a short projecting tip isn't missed between
    # equal-length samples. Support is based on shape extent, not curve density.
    for axis in (0,1):
        for direction in (-1,1):
            i = max(range(len(points)), key=lambda i: direction*points[i][axis])
            distances.append(cumulative[i])
    targets = [_at(closed,cumulative,d) for d in distances]
    starts = [a for a,b in legal]; ends = [b for a,b in legal]
    limit = max(max_spacing, total/count)
    expanded = [(a+shift,b+shift) for shift in (-total,0,total) for a,b in legal]
    expanded_starts = [a for a,b in expanded]; expanded_ends = [b for a,b in expanded]
    # Very small contours can fit only one/two tabs. Local movement alone gets
    # trapped by their spacing constraint, so compare eight whole-ring phases
    # first (not combinations of tab subsets). This costs only O(8*n*targets).
    def seed_score(seed):
        sites = [_at(closed,cumulative,c) for c in seed]
        coverage = [min((p[0]-q[0])**2+(p[1]-q[1])**2 for q in sites) for p in targets]
        unsmooth = sum(nearest_legal(c,c-1e-9,c+1e-9,legal,starts,ends) is None for c in seed)
        return unsmooth, max(coverage), sum(coverage)
    seeds = [centers]
    for phase in range(1,8):
        seed = sorted((c+phase*total/count/8)%total for c in centers)
        for i in range(count):
            previous = seed[i-1] if i else seed[-1]-total
            following = seed[i+1] if i+1<count else seed[0]+total
            low = max(previous+2*width,following-limit) if count>1 else 0
            high = min(following-2*width,previous+limit) if count>1 else total
            moved = nearest_legal(seed[i],low,high,expanded,expanded_starts,expanded_ends)
            if moved is not None:seed[i]=moved
        seeds.append(sorted(c%total for c in seed))
    centers = min(seeds,key=seed_score)
    candidates = set(centers)
    for distance in distances:
        moved = nearest_legal(distance, 0, total, legal, starts, ends)
        if moved is not None:
            candidates.add(moved)
    # Midpoints and ends of long legal runs let supports sit near a stem tip
    # without wrapping the corner at its actual endpoint.
    for a,b in sorted(legal, key=lambda span: span[1]-span[0], reverse=True)[:24]:
        candidates.update((a,(a+b)/2,b))
    candidates = sorted(candidates)
    if len(candidates)>MAX_CANDIDATES:
        candidates = sorted(set(centers) | {candidates[round(i*(len(candidates)-1)/(MAX_CANDIDATES-count-1))]
                                            for i in range(MAX_CANDIDATES-count)})
    scale = max(max(p[0] for p in points)-min(p[0] for p in points),
                max(p[1] for p in points)-min(p[1] for p in points), width)
    def distances_to(distance):
        point = _at(closed,cumulative,distance)
        return [((point[0]-p[0])/scale)**2+((point[1]-p[1])/scale)**2 for p in targets]
    scores = {distance:distances_to(distance) for distance in candidates}
    starts = [a for a,b in expanded]; ends = [b for a,b in expanded]
    # Candidate evaluation is just a small distance table, never a regenerated
    # contour. The two fixed sweeps bound work and preserve cyclic ordering.
    for indices in (range(count), range(count-1,-1,-1)):
        for i in indices:
            previous = centers[i-1] if i else centers[-1]-total
            following = centers[i+1] if i+1<count else centers[0]+total
            low = max(previous+2*width, following-limit) if count>1 else 0
            high = min(following-2*width, previous+limit) if count>1 else total
            current = centers[i]
            options = [c+shift for shift in (-total,0,total) for c in candidates if low-1e-10<=c+shift<=high+1e-10]
            closest = nearest_legal(current,low,high,expanded,starts,ends)
            if closest is not None:
                options.append(closest)
            options.append(current)
            # Prefer a full-width smooth span if any is feasible, but never
            # block generation or reduce the original count when it isn't.
            smooth = [c for c in options if nearest_legal(c,c-1e-9,c+1e-9,expanded,starts,ends) is not None]
            options = smooth or [current]
            other = [distances_to(c) for j,c in enumerate(centers) if j!=i]
            nearest = [min((row[t] for row in other),default=float('inf')) for t in range(len(targets))]
            rows = []
            for c in options:
                key = c%total
                if key not in scores:
                    scores[key] = distances_to(c)
                rows.append(scores[key])
            if np is not None:
                coverage = np.minimum(np.asarray(rows),np.asarray(nearest))
                worst, means = coverage.max(axis=1), coverage.mean(axis=1)
                index = min(range(len(options)), key=lambda k:(round(float(worst[k]),12),
                    round(float(means[k]),12), round(abs(options[k]-current)/total,12), options[k]))
            else:
                def score(k):
                    coverage = [min(a,b) for a,b in zip(rows[k],nearest)]
                    return (round(max(coverage),12),round(sum(coverage)/len(coverage),12),
                            round(abs(options[k]-current)/total,12),options[k])
                index = min(range(len(options)),key=score)
            centers[i] = options[index]
    return tuple(c%total for c in centers)


def planned_intervals(coordinates, width, count, max_spacing):
    points = _ring(coordinates)
    if len(points)<3 or width<=0 or max_spacing<=0:
        return ()
    closed = (*points,points[0])
    cumulative = _lengths(closed)
    total = cumulative[-1]
    if total<=width:
        return ()
    # Canonical start/winding keeps physical supports identical after entry
    # relocation, reversal and successive depth passes, and makes caching useful.
    start = min(range(len(points)),key=lambda i: points[i])
    origin = cumulative[start]
    area = sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(closed,closed[1:]))
    direction = 1 if area>=0 else -1
    canonical = tuple(points[(start+direction*i)%len(points)] for i in range(len(points)))
    centers = _canonical_centers(canonical,width,count,max_spacing)
    return intervals(sorted((origin+direction*c)%total for c in centers),width,total)
