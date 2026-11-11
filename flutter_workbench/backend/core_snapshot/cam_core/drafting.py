"""Small geometric helpers for click-chain drafting; no persistent constraints."""
import math
from .model import Circle, Line, Point, Polyline, Rectangle


def segment_intersection(a, b, c, d, tolerance=1e-9):
    dx,dy=b.x-a.x,b.y-a.y
    ex,ey=d.x-c.x,d.y-c.y
    cross=dx*ey-dy*ex
    if abs(cross)<=tolerance*max(math.hypot(dx,dy),math.hypot(ex,ey),tolerance):
        return None
    u=((c.x-a.x)*ey-(c.y-a.y)*ex)/cross
    v=((c.x-a.x)*dy-(c.y-a.y)*dx)/cross
    eu=tolerance/max(math.hypot(dx,dy),tolerance)
    ev=tolerance/max(math.hypot(ex,ey),tolerance)
    if -eu<=u<=1+eu and -ev<=v<=1+ev:
        return Point(a.x+u*dx,a.y+u*dy)
    return None


def entity_segments(entity):
    if isinstance(entity,Line):return [(entity.start,entity.end)]
    if isinstance(entity,Rectangle):
        a,b=entity.corner_a,entity.corner_b
        points=[a,Point(b.x,a.y),b,Point(a.x,b.y),a]
    elif isinstance(entity,Polyline):
        points=list(entity.points)
        if entity.closed and points:points.append(points[0])
    else:return []
    return list(zip(points,points[1:]))


def _line_circle(a,b,circle):
    dx,dy=b.x-a.x,b.y-a.y
    fx,fy=a.x-circle.center.x,a.y-circle.center.y
    aa=dx*dx+dy*dy
    if aa<=1e-24:return []
    bb=2*(fx*dx+fy*dy)
    cc=fx*fx+fy*fy-circle.radius*circle.radius
    disc=bb*bb-4*aa*cc
    if disc<0:return []
    return [Point(a.x+t*dx,a.y+t*dy) for t in
            ((-bb-math.sqrt(disc))/(2*aa),(-bb+math.sqrt(disc))/(2*aa)) if 0<=t<=1]


def _circle_circle(first,second):
    a,b=first.center,second.center
    distance=a.distance_to(b)
    if distance<=1e-12 or distance>first.radius+second.radius or distance<abs(first.radius-second.radius):return []
    along=(first.radius**2-second.radius**2+distance**2)/(2*distance)
    height=math.sqrt(max(0,first.radius**2-along**2))
    x,y=a.x+along*(b.x-a.x)/distance,a.y+along*(b.y-a.y)/distance
    return [Point(x+sign*height*(b.y-a.y)/distance,y-sign*height*(b.x-a.x)/distance) for sign in (-1,1)]


def drafting_snap(point,entities,radius,anchor=None):
    """Return nearest local geometry snap, then horizontal/vertical inference."""
    candidates=[];segments=[];circles=[]
    def offer(candidate,label):
        distance=point.distance_to(candidate)
        if distance<=radius:candidates.append((distance,len(candidates),candidate,label))
    for entity in entities:
        if isinstance(entity,Circle):
            if abs(point.distance_to(entity.center)-entity.radius)<=radius:circles.append(entity)
            offer(entity.center,'Center')
            for angle in (0,math.pi/2,math.pi,3*math.pi/2):
                offer(Point(entity.center.x+entity.radius*math.cos(angle),entity.center.y+entity.radius*math.sin(angle)),'Quadrant')
        for a,b in entity_segments(entity):
            if (max(a.x,b.x)<point.x-radius or min(a.x,b.x)>point.x+radius or
                max(a.y,b.y)<point.y-radius or min(a.y,b.y)>point.y+radius):continue
            segments.append((a,b))
            offer(a,'Endpoint');offer(b,'Endpoint')
            offer(Point((a.x+b.x)/2,(a.y+b.y)/2),'Midpoint')
    for index,(a,b) in enumerate(segments):
        for c,d in segments[index+1:]:
            hit=segment_intersection(a,b,c,d)
            if hit is not None:offer(hit,'Intersection')
        for circle in circles:
            for hit in _line_circle(a,b,circle):offer(hit,'Intersection')
    for index,circle in enumerate(circles):
        for other in circles[index+1:]:
            for hit in _circle_circle(circle,other):offer(hit,'Intersection')
    if candidates:
        _,_,candidate,label=min(candidates)
        return candidate,label
    if anchor is not None:
        dx,dy=abs(point.x-anchor.x),abs(point.y-anchor.y)
        if min(dx,dy)<=radius*.5:
            return (Point(anchor.x,point.y),'Vertical') if dx<dy else (Point(point.x,anchor.y),'Horizontal')
    return point,''


def polar_endpoint(start,length,angle):
    length,angle=float(length),float(angle)
    if not all(math.isfinite(value) for value in (length,angle)) or length<=0:
        raise ValueError('Length must be positive and Length/Angle must be finite numbers.')
    radians=math.radians(angle)
    end=Point(start.x+length*math.cos(radians),start.y+length*math.sin(radians))
    if not math.isfinite(end.x) or not math.isfinite(end.y):
        raise ValueError('The typed segment endpoint is outside the finite coordinate range.')
    return end
