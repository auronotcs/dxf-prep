"""Unshifted plate-local cutter-center bounds, including arc extrema."""
import math
from .toolpath import MotionKind


def toolpath_xy_bounds(paths):
    xs, ys = [], []
    def add(x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Output motion contains non-finite X/Y coordinates.')
        xs.append(x);ys.append(y)
    for path in paths:
        for block in path.blocks:
            previous = None
            for motion in block.motions:
                end = motion.end
                add(end.x,end.y)
                if motion.kind in {MotionKind.ARC_CW,MotionKind.ARC_CCW}:
                    if previous is None or motion.center is None:
                        raise ValueError('Output arc has no start or center.')
                    cx,cy=motion.center.x,motion.center.y
                    if not math.isfinite(cx) or not math.isfinite(cy):
                        raise ValueError('Output arc contains a non-finite center.')
                    radius=math.hypot(previous.x-cx,previous.y-cy)
                    a=math.atan2(previous.y-cy,previous.x-cx)
                    b=math.atan2(end.y-cy,end.x-cx)
                    sign=-1 if motion.kind is MotionKind.ARC_CW else 1
                    sweep=(sign*(b-a))%(2*math.pi)
                    if sweep < 1e-10:sweep=2*math.pi
                    for angle in (0,math.pi/2,math.pi,3*math.pi/2):
                        if (sign*(angle-a))%(2*math.pi) <= sweep+1e-10:
                            add(cx+radius*math.cos(angle),cy+radius*math.sin(angle))
                previous=end
    return (min(xs),min(ys),max(xs),max(ys)) if xs else None
