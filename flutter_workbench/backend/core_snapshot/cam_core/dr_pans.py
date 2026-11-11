"""Four-sided, 90-degree pan blanks. Bend estimates are deliberately editable.

Source rectangle = finished OUTSIDE face dimensions. Lip = finished INSIDE
depth from the back of that face. References mark developed bend-zone centers,
not a promise about where any particular brake should be clamped.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field

from ._vendor import pyclipper as pc
from .model import Circle, Line, Point, Polyline, Rectangle

DR_PAN_HOLE_LAYER = "DR_PAN_MOUNT_HOLES"
DR_PAN_MARK_LAYER = "DR_PAN_BEND_MARKS"


@dataclass(frozen=True)
class DrPanHoleSettings:
    enabled: bool = True
    diameter: float = .125
    max_spacing: float = 24.
    end_offset: float = 2.
    top: bool = True
    bottom: bool = True
    left: bool = True
    right: bool = True

    def validate(self):
        if not self.enabled:
            return
        if not all(math.isfinite(v) for v in (self.diameter,self.max_spacing,self.end_offset)):
            raise ValueError("Mounting-hole dimensions must be finite numbers.")
        if self.diameter <= 0 or self.max_spacing <= self.diameter or self.end_offset < 0:
            raise ValueError("Hole diameter must be positive, spacing greater than diameter, and end offset nonnegative.")


@dataclass(frozen=True)
class DrPanSettings:
    thickness: float = .063
    inside_lip: float = 1.0625
    inside_radius: float = .063
    k_factor: float = .4
    cutter_diameter: float = .25
    corner_gap: float = .063

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Pan dimensions must be finite numbers.")
        if min(self.thickness, self.inside_lip, self.cutter_diameter) <= 0:
            raise ValueError("Thickness, inside lip and cutter diameter must be positive.")
        if self.inside_radius < 0 or self.corner_gap < 0 or not 0 <= self.k_factor <= .5:
            raise ValueError("Radius/gap must be nonnegative; K-factor must be between 0 and 0.5.")
        if self.inside_lip <= self.inside_radius:
            raise ValueError("The inside lip must be deeper than the inside bend radius.")

    @property
    def allowance(self):
        return math.pi / 2 * (self.inside_radius + self.k_factor * self.thickness)

    @property
    def deduction(self):
        return 2 * (self.inside_radius + self.thickness) - self.allowance


def rectangle_frame(entity):
    """Recognize a true rectangle, including rotated/reversed DXF polylines."""
    if entity.metadata.get("dr_pan") or entity.metadata.get("reference_only"):
        raise ValueError("Select an original face rectangle, not an applied pan or reference line.")
    if isinstance(entity, Rectangle):
        a, b = entity.corner_a, entity.corner_b
        points = [a, Point(b.x, a.y), b, Point(a.x, b.y)]
    elif isinstance(entity, Polyline) and entity.closed:
        points = list(entity.points)
    else:
        raise ValueError("DR Pans needs closed rectangular geometry (not its bounding box).")
    if not points or not all(math.isfinite(v) for p in points for v in (p.x, p.y)):
        raise ValueError("The rectangle contains invalid coordinates.")
    span = max(max(p.x for p in points)-min(p.x for p in points),
               max(p.y for p in points)-min(p.y for p in points))
    tol = max(span * 1e-8, 1e-9)
    clean = []
    for p in points:
        if not clean or p.distance_to(clean[-1]) > tol:
            clean.append(p)
    if len(clean) > 1 and clean[0].distance_to(clean[-1]) <= tol:
        clean.pop()
    # Remove redundant straight DXF vertices, without rounding odd shapes into rectangles.
    changed = True
    while changed and len(clean) > 4:
        changed = False
        for i, p in enumerate(clean):
            a, b = clean[i-1], clean[(i+1) % len(clean)]
            cross = abs((p.x-a.x)*(b.y-a.y)-(p.y-a.y)*(b.x-a.x))
            dot = (p.x-a.x)*(b.x-p.x)+(p.y-a.y)*(b.y-p.y)
            if cross <= tol * a.distance_to(b) and dot >= 0:
                clean.pop(i)
                changed = True
                break
    if len(clean) != 4:
        raise ValueError("Select a four-sided rectangle; convert/repair imported geometry first.")
    a, b, c, d = clean
    width, height = a.distance_to(b), b.distance_to(c)
    if min(width, height) <= tol:
        raise ValueError("The face rectangle has zero width or height.")
    u = Point((b.x-a.x)/width, (b.y-a.y)/width)
    v = Point((c.x-b.x)/height, (c.y-b.y)/height)
    if (abs(u.x*v.x+u.y*v.y) > 1e-8
            or Point(a.x+c.x-b.x, a.y+c.y-b.y).distance_to(d) > tol):
        raise ValueError("The selected outline is not a rectangle.")
    return Point((a.x+c.x)/2, (a.y+c.y)/2), u, v, width, height


@dataclass
class DrPanResult:
    outline: Polyline
    references: list[Line]
    face_width: float
    face_height: float
    blank_width: float
    blank_height: float
    holes: list[Circle] = field(default_factory=list)


def dr_pan_bend_marks(outline: Polyline, references: list[Line], offset: float = 0.) -> list[Line]:
    """Opt-in, machinable marking lines shifted from the original bend guides.

    Positive offset is toward each lip. Clip line ends to the blank so the
    marks cannot extend into the corner relief. Original guides never change.
    """
    if not math.isfinite(offset):
        raise ValueError("Bend-mark offset must be a finite number.")
    if len(references) != 4 or not all(isinstance(g,Line) for g in references):
        raise ValueError("Four original bend reference lines are required to position the marks.")
    midpoints = [Point((g.start.x+g.end.x)/2,(g.start.y+g.end.y)/2) for g in references]
    center = Point(sum(p.x for p in midpoints)/4,sum(p.y for p in midpoints)/4)
    scale = 1e6
    polygon = [(round(p.x*scale),round(p.y*scale)) for p in outline.points]
    marks=[]
    for i,(guide,mid) in enumerate(zip(references,midpoints)):
        length = center.distance_to(mid)
        if length <= 1e-9:
            raise ValueError("The pan bend references do not define a valid center.")
        dx,dy=(mid.x-center.x)/length*offset,(mid.y-center.y)/length*offset
        segment=[(round((p.x+dx)*scale),round((p.y+dy)*scale)) for p in (guide.start,guide.end)]
        engine=pc.Pyclipper()
        engine.AddPath(segment,pc.PT_SUBJECT,False)
        engine.AddPath(polygon,pc.PT_CLIP,True)
        clipped=pc.OpenPathsFromPolyTree(engine.Execute2(pc.CT_INTERSECTION,pc.PFT_NONZERO,pc.PFT_NONZERO))
        if len(clipped)!=1 or len(clipped[0])!=2:
            raise ValueError("Bend-mark offset puts a line outside the pan or across a relief; reduce the offset.")
        pts=[Point(x/scale,y/scale) for x,y in clipped[0]]
        metadata={"dr_pan_owner":outline.id,"dr_pan_bend_mark":True,"dr_pan_bend_index":i,
                  "dr_pan_mark_offset":offset,"machinable":True,"mounting_workflow":"dr_pan",
                  "name":f"DR pan bend mark {i+1}"}
        if outline.metadata.get("group_id"):
            metadata["group_id"]=outline.metadata["group_id"]
        marks.append(Line(start=pts[0],end=pts[1],layer=DR_PAN_MARK_LAYER,metadata=metadata))
    return marks


def generate_dr_pan(source, settings: DrPanSettings, hole_settings: DrPanHoleSettings | None = None) -> DrPanResult:
    settings.validate()
    center, u, v, width, height = rectangle_frame(source)
    s = settings
    qx, qy = width/2-s.inside_radius-s.thickness, height/2-s.inside_radius-s.thickness
    ba = s.allowance
    ax, ay = qx+ba+s.inside_lip-s.inside_radius, qy+ba+s.inside_lip-s.inside_radius
    # Round corner-root relief reaches the flat-face tangent corner, but never
    # crosses into the flat visible face. Extra clearance is on the bent lips.
    inset = max(0., (ba-s.corner_gap)/2)
    # An exactly cutter-sized pocket collapses to a zero-width connection in
    # the outside offset and is skipped. Give the cutter 2% diametral clearance
    # (0.005 in for a 0.250 in tool), including polygon/offset rounding room.
    # Moving the circle center with its radius keeps the flat-face tangent fixed.
    radius = max(s.cutter_diameter*.51, inset/math.sqrt(2)+s.thickness*1e-4)
    if min(qx, qy) <= radius or min(ax-qx, ay-qy) <= 2*radius:
        raise ValueError("Face/lip is too small for these bend and cutter-relief dimensions.")
    scale = 1e7 / max(1., width, height, ax, ay)
    def path(points):
        result = [(round(x*scale), round(y*scale)) for x,y in points]
        return result if pc.Orientation(result) else list(reversed(result))
    clips = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            tx, ty = qx+inset, qy+inset
            clips.append(path([(sx*tx,sy*ty),(sx*(ax+radius),sy*ty),
                               (sx*(ax+radius),sy*(ay+radius)),(sx*tx,sy*(ay+radius))]))
            cx, cy = sx*(qx+radius/math.sqrt(2)), sy*(qy+radius/math.sqrt(2))
            clips.append(path([(cx+radius*math.cos(i*math.tau/128),
                                cy+radius*math.sin(i*math.tau/128)) for i in range(128)]))
            # Open a cutter-width approach along the outward diagonal. A round
            # pocket joined only by the quadrant rectangle has a narrow mouth;
            # its offset becomes an isolated loop even with diameter clearance.
            # The corridor starts at the circle center, so its inward extent
            # cannot cross the flat-face tangent touched by the circular root.
            nx, ny = -sy/math.sqrt(2), sx/math.sqrt(2)
            ex, ey = sx*(max(abs(cx),tx)+radius), sy*(max(abs(cy),ty)+radius)
            clips.append(path([(cx+nx*radius,cy+ny*radius),(ex+nx*radius,ey+ny*radius),
                               (ex-nx*radius,ey-ny*radius),(cx-nx*radius,cy-ny*radius)]))
    engine = pc.Pyclipper()
    engine.AddPath(path([(-ax,-ay),(ax,-ay),(ax,ay),(-ax,ay)]), pc.PT_SUBJECT, True)
    engine.AddPaths(clips, pc.PT_CLIP, True)
    paths = engine.Execute(pc.CT_DIFFERENCE, pc.PFT_NONZERO, pc.PFT_NONZERO)
    if len(paths) != 1 or len(paths[0]) < 12:
        raise ValueError("Corner relief did not produce one connected pan blank; revise dimensions.")
    def world(x, y):
        return Point(center.x+u.x*x+v.x*y, center.y+u.y*x+v.y*y)
    metadata = copy.deepcopy(source.metadata)
    metadata.update(dr_pan={"version":1, "settings":asdict(s),
                            "relief_revision":2, "relief_diameter":2*radius,
                            "face_width":width, "face_height":height,
                            "bend_estimate":"uncalibrated", "source":source.to_dict()},
                    mounting_workflow="dr_pan", mounting_generated=True)
    outline = Polyline(id=source.id, layer=source.layer, metadata=metadata,
                       appearance=copy.deepcopy(source.appearance), closed=True,
                       points=[world(x/scale,y/scale) for x,y in paths[0]])
    reference_meta = {"dr_pan_owner":source.id, "reference_only":True,
                      "machinable":False, "role":"reference_only"}
    # Centerline is a bend-zone reference only; reliefs shorten its ends.
    bx, by = qx+ba/2, qy+ba/2
    ends = [((-qx,-by),(qx,-by)),((-qx,by),(qx,by)),
            ((-bx,-qy),(-bx,qy)),((bx,-qy),(bx,qy))]
    refs = [Line(layer="DR_PAN_BEND_REFERENCE", start=world(*a), end=world(*b),
                 metadata={**reference_meta,"name":"DR pan bend-zone center (reference)"}) for a,b in ends]
    holes = []
    if hole_settings is not None:
        hole_settings.validate()
        outline.metadata["dr_pan"]["mounting_holes"] = asdict(hole_settings)
        if hole_settings.enabled and any(getattr(hole_settings,k) for k in ("top","bottom","left","right")):
            # Label sides in a stable drawing frame, independent of the first
            # vertex or winding of imported rectangles. The rightward axis
            # closest to world X is local X; its CCW normal is local Y.
            axes = [Point(-p.x,-p.y) if p.x < 0 or (p.x == 0 and p.y < 0) else p for p in (u,v)]
            hx = max(axes,key=lambda p:(round(abs(p.x),12),p.y))
            hy = Point(-hx.y,hx.x)
            # Each physical lip is labeled by its outward normal, not by the
            # order in which the source rectangle happened to be drawn.
            side_for = lambda nx,ny: ("right" if nx*hx.x+ny*hx.y > 0 else "left") if abs(nx*hx.x+ny*hx.y) > .5 else ("top" if nx*hy.x+ny*hy.y > 0 else "bottom")
            hr = hole_settings.diameter/2
            straight_lip = s.inside_lip-s.inside_radius
            if hr*2 >= straight_lip:
                raise ValueError("Mounting holes do not fit across the straight portion of the lip.")
            def positions(half_span):
                # End offset is measured from the straight end of each lip.
                span = 2*half_span-2*hole_settings.end_offset
                if span <= 0:
                    return [0.]  # Short lips get a single centered hole.
                count = max(2,math.ceil(span/hole_settings.max_spacing)+1)
                if count > 1000:
                    raise ValueError("Mounting-hole spacing would create over 1,000 holes on one lip.")
                return [-span/2+i*span/(count-1) for i in range(count)]
            lips = [(v.x,v.y,qx+inset,lambda t:(t,(qy+ba+ay)/2)),
                    (-v.x,-v.y,qx+inset,lambda t:(t,-(qy+ba+ay)/2)),
                    (u.x,u.y,qy+inset,lambda t:((qx+ba+ax)/2,t)),
                    (-u.x,-u.y,qy+inset,lambda t:(-(qx+ba+ax)/2,t))]
            for nx,ny,half_span,location in lips:
                side = side_for(nx,ny)
                if not getattr(hole_settings,side):
                    continue
                for index,t in enumerate(positions(half_span),1):
                    x,y = location(t)
                    # Check the entire hole and its clearance to every actual
                    # blank edge, including the rounded root reliefs.
                    ip = (round(x*scale),round(y*scale))
                    if pc.PointInPolygon(ip,paths[0]) != 1:
                        raise ValueError(f"{side.title()} mounting hole is outside the pan blank; increase its end offset.")
                    for i,(px,py) in enumerate(paths[0]):
                        ex,ey=paths[0][(i+1)%len(paths[0])]
                        dx,dy=(ex-px)/scale,(ey-py)/scale
                        vx,vy=x-px/scale,y-py/scale
                        length2=dx*dx+dy*dy
                        f=max(0.,min(1.,(vx*dx+vy*dy)/length2)) if length2 else 0.
                        if math.hypot(vx-f*dx,vy-f*dy) <= hr+2/scale:
                            raise ValueError(f"{side.title()} mounting hole touches an edge or corner relief; increase its end offset or reduce its diameter.")
                    holes.append(Circle(layer=DR_PAN_HOLE_LAYER,center=world(x,y),radius=hr,
                        metadata={"dr_pan_owner":source.id,"dr_pan_hole":True,
                                  "dr_pan_side":side,"mounting_workflow":"dr_pan",
                                  "name":f"DR pan {side} mounting hole {index}","machinable":True}))
        # Use ordinary application groups so copies and rigid transforms carry
        # the cut outline, drill holes and reference guides together.
        group_id = str(source.metadata.get("group_id") or f"dr_pan_{source.id}")
        for item in (outline,*refs,*holes):
            item.metadata["group_id"] = group_id
    return DrPanResult(outline, refs, width, height, 2*ax, 2*ay, holes)
