"""Conservative final rest-loop filtering; never changes source geometry.

Only feed-controlled LINEAR motion fully at depth proves source clearance.
Arcs/ramps/rapids are deliberately not credited. Upper candidate footprints
and lower cleared footprints prevent round-offset approximation from proving
clearance that has not actually been cut. Threshold zero removes only proven
redundancy, not small details. Skipped loops never contribute clearance.
"""
from ._vendor import pyclipper


def stroke(points, radius, *, upper=False):
    from .toolpath import _REGION_SCALE
    if len(points)<2 or radius<=0:return []
    # Clipper's round-join chord error plus integer rounding are bounded here.
    tolerance=max(1.0, radius*_REGION_SCALE/2048)
    adjusted=radius*_REGION_SCALE+(2*tolerance+4 if upper else -4)
    if adjusted<=0:return []
    path=[(round(p.x*_REGION_SCALE),round(p.y*_REGION_SCALE)) for p in points]
    if len(set(path))<2:return []
    offset=pyclipper.PyclipperOffset(8.0,tolerance)
    offset.AddPath(path,pyclipper.JT_ROUND,pyclipper.ET_OPENROUND)
    return offset.Execute(adjusted)


def source_clearance(generated, depth, radius):
    from .toolpath import MotionKind,_clipper_union
    paths=[]
    for block in generated.blocks:
        previous=None;run=[]
        for motion in block.motions:
            if (previous is not None and motion.kind==MotionKind.LINEAR
                    and previous.z<=-depth and motion.end.z<=-depth):
                if not run:run.append(previous)
                run.append(motion.end)
            else:
                if run:paths.extend(stroke(run,radius));run=[]
            previous=motion.end
        if run:paths.extend(stroke(run,radius))
    return _clipper_union(paths)


def significant(paths, threshold):
    if not paths:return False
    if threshold<=0:return True
    from .toolpath import _REGION_SCALE
    # Shrink by slightly LESS than half the chosen width, retaining borderline
    # stock despite integer rounding. No area or path-size deletion criterion.
    distance=threshold*_REGION_SCALE/2-4
    if distance<=0:return True
    offset=pyclipper.PyclipperOffset(8.0,max(1.0,distance/1024))
    offset.AddPaths(paths,pyclipper.JT_ROUND,pyclipper.ET_CLOSEDPOLYGON)
    return bool(offset.Execute(-distance))


def filter_loops(loops, design, radius, threshold, cleared_factory):
    from .toolpath import _clipper_intersection,_clipper_difference
    retained=[];remaining=None
    for loop in loops:
        upper=stroke([*loop,loop[0]],radius,upper=True)
        if not upper:
            retained.append(loop);continue
        # Nominal tool reachability is NOT clearance evidence: its offset
        # approximation can invent stock at locations already cleared by the
        # actual wall/center paths. Test every final candidate against motion.
        if remaining is None:
            cleared=cleared_factory()
            if not cleared:
                return loops  # No source-motion proof: never guess.
            remaining=_clipper_difference(design,cleared)
        contribution=_clipper_intersection(upper,remaining)
        if significant(contribution,threshold):
            retained.append(loop)
        # Do not credit skipped/retained rest loops as source clearance. This
        # avoids accumulated omissions and dependence on rest ordering/depth.
    return retained


def confirmed_no_work_ids(project, operation, generated):
    """Do not accept a claimed omission from stale/foreign block metadata."""
    from .model import RestMachiningOperation
    from .toolpath import generate_rest_machining
    if type(operation) is not RestMachiningOperation:return set()
    claimed={i for b in generated.blocks for i in b.metadata.get('rest_no_work_entity_ids',[]) if isinstance(i,str)}
    if not claimed:return set()
    try:
        current=generate_rest_machining(project,operation)
    except ValueError:
        return set()  # Stale/invalid setup cannot authorize missing geometry.
    confirmed={i for b in current.blocks for i in b.metadata.get('rest_no_work_entity_ids',[])}
    return claimed & confirmed & set(operation.entity_ids)
