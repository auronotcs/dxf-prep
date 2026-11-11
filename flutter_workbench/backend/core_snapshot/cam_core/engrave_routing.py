"""Travel-only drag-engraving routing; never join marks or alter their depth.

Two deterministic seeds (source and nearest-endpoint order) receive bounded
oriented 2-opt and closed-start refinement. The source route is a non-regression
fallback. This is a heuristic, not a claim of globally minimal travel. The
objective is XY travel from operation-local zero, without an assumed return
home; operation order and each mark's complete retract remain authoritative.
"""
from dataclasses import dataclass, replace
import math

from .toolpath import MotionKind, ToolPoint, ToolpathBlock

_ZERO = ToolPoint(0, 0, 0)
_EPS = 1e-8
_MAX_STARTS = 64
_MAX_CHECKS = 100_000


def _distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


@dataclass(frozen=True)
class _Visit:
    index: int
    entry: ToolPoint
    exit: ToolPoint
    start: int = 0
    reverse: bool = False
    reversible: bool = False

    def flipped(self):
        if not self.reversible:
            return self
        return replace(self, entry=self.exit, exit=self.entry, reverse=not self.reverse)


def _variants(index, block):
    """Only recognize the generator's vertical-entry/exit drag-block contract."""
    motions = block.motions
    original = _Visit(index, motions[0].end, motions[-1].end)
    if (len(motions) < 6 or block.metadata.get("side") != "engrave"
            or block.metadata.get("spindle") != "off"
            or [m.kind for m in motions[:3]] != [MotionKind.RAPID, MotionKind.RAPID, MotionKind.LINEAR]
            or [m.kind for m in motions[-2:]] != [MotionKind.LINEAR, MotionKind.RAPID]):
        return [original]
    trace = motions[3:-2]
    start = motions[2].end
    if (any(_distance(m.end, start) > _EPS for m in motions[:2])
            or any(_distance(m.end, trace[-1].end) > _EPS for m in motions[-2:])
            or any(abs(m.end.z - start.z) > _EPS or m.dwell_seconds is not None
                   or m.kind not in {MotionKind.LINEAR, MotionKind.ARC_CW, MotionKind.ARC_CCW}
                   for m in trace)):
        return [original]
    if block.metadata.get("closed") is False and all(m.kind is MotionKind.LINEAR for m in trace):
        original = replace(original, reversible=True)
        return [original, original.flipped()]
    if block.metadata.get("closed") is True and _distance(start, trace[-1].end) <= _EPS:
        # Rotate intact edges, including analytic arcs. No splitting, refitting,
        # tessellation, winding reversal, or new cutting connector is permitted.
        vertices = [start, *(m.end for m in trace[:-1])]
        count = min(len(vertices), _MAX_STARTS)
        return [_Visit(index, vertices[k], vertices[k], start=k)
                for k in (j * len(vertices) // count for j in range(count))]
    return [original]


def _cost(route):
    return sum(_distance(_ZERO if i == 0 else route[i-1].exit, v.entry)
               for i, v in enumerate(route))


def _refine(route, variants):
    route = list(route)
    checks = 0
    for _pass in range(6):
        changed = False
        # Choose orientation/start using BOTH neighboring links. Includes final
        # open strokes, for which only arrival cost matters.
        for i, visit in enumerate(route):
            before = _ZERO if i == 0 else route[i-1].exit
            after = route[i+1].entry if i+1 < len(route) else None
            def cost(v):
                return _distance(before, v.entry) + (0 if after is None else _distance(v.exit, after))
            best = min(variants[visit.index], key=cost)
            if cost(best) + _EPS < cost(visit):
                route[i] = best
                changed = True
        # Reversing a sub-route also reverses its OPEN strokes, preserving all
        # internal travel lengths. Closed strokes keep their cutting direction.
        for left in range(len(route)-1):
            before = _ZERO if left == 0 else route[left-1].exit
            for right in range(left+1, len(route)):
                if checks >= _MAX_CHECKS:
                    return route
                checks += 1
                # Unsupported directional blocks cannot participate in 2-opt.
                # Such a block stops this interval and every longer interval.
                end = route[right]
                if ((not end.reversible and _distance(end.entry, end.exit) > _EPS)
                        or (right == left+1 and not route[left].reversible
                            and _distance(route[left].entry, route[left].exit) > _EPS)):
                    break
                after = route[right+1].entry if right+1 < len(route) else None
                old = _distance(before, route[left].entry)
                new = _distance(before, end.exit)
                if after is not None:
                    old += _distance(end.exit, after)
                    new += _distance(route[left].entry, after)
                if new + _EPS < old:
                    route[left:right+1] = [v.flipped() for v in reversed(route[left:right+1])]
                    changed = True
                    break
        if not changed:
            break
    return route


def _materialize(block, visit, position):
    metadata = dict(block.metadata)
    motions = list(block.motions)
    if visit.reverse or visit.start:
        trace = motions[3:-2]
        if visit.reverse:
            starts = [motions[2].end, *(m.end for m in trace[:-1])]
            # Each edge retains its own feed, even for a heterogeneous trace.
            trace = [replace(m, end=p) for m, p in reversed(list(zip(trace, starts)))]
        else:
            trace = trace[visit.start:] + trace[:visit.start]
        motions = [
            *(replace(m, end=ToolPoint(visit.entry.x, visit.entry.y, m.end.z)) for m in motions[:3]),
            *trace,
            *(replace(m, end=ToolPoint(visit.exit.x, visit.exit.y, m.end.z)) for m in motions[-2:]),
        ]
    if visit.reverse:
        metadata["path_reversed_for_travel"] = True
    if visit.start:
        metadata["engrave_start_optimized"] = True
    if visit.index != position or visit.reverse or visit.start:
        metadata["travel_reordered"] = True
    return replace(block, motions=motions, metadata=metadata)


def order_engraving_blocks(blocks: list[ToolpathBlock]) -> list[ToolpathBlock]:
    if not blocks or any(not b.motions for b in blocks):
        return blocks
    variants = [_variants(i, b) for i, b in enumerate(blocks)]
    original = [options[0] for options in variants]
    remaining = set(range(len(blocks)))
    greedy = []
    current = _ZERO
    while remaining:
        selected = min((v for i in sorted(remaining) for v in variants[i]),
                       key=lambda v: (_distance(current, v.entry), v.index, v.reverse, v.start))
        greedy.append(selected)
        remaining.remove(selected.index)
        current = selected.exit
    candidates = [original, greedy, _refine(original, variants), _refine(greedy, variants)]
    best = min(candidates, key=_cost)
    # Keep source byte-for-byte motions on ties or sub-tolerance differences.
    if _cost(best) + _EPS >= _cost(original):
        return blocks
    return [_materialize(blocks[v.index], v, i) for i, v in enumerate(best)]
