"""Local edge lookup; never cached across geometry edits."""
import math
from collections import defaultdict


class ContourIndex:
    def __init__(self, points, reach):
        self.edges = list(zip(points, [*points[1:], points[0]]))
        bounds = (min(p.x for p in points), min(p.y for p in points),
                  max(p.x for p in points), max(p.y for p in points))
        self.cell = max(reach * 2, max(bounds[2]-bounds[0], bounds[3]-bounds[1]) / max(1, math.sqrt(len(points))), 1e-8)
        self.rows = defaultdict(list)
        for a, b in self.edges:
            for y in range(self.row(min(a.y,b.y)-1e-8), self.row(max(a.y,b.y)+1e-8)+1):
                self.rows[y].append((a,b))

    def row(self, y):
        return math.floor(y/self.cell)

    def contains(self, p):
        inside = False
        for a,b in self.rows.get(self.row(p.y), ()):
            dx,dy = b.x-a.x,b.y-a.y
            cross = (p.x-a.x)*dy-(p.y-a.y)*dx
            if abs(cross)<=1e-8 and min(a.x,b.x)-1e-8<=p.x<=max(a.x,b.x)+1e-8 and min(a.y,b.y)-1e-8<=p.y<=max(a.y,b.y)+1e-8:
                return True
            if (a.y>p.y)!=(b.y>p.y) and a.x+(p.y-a.y)*dx/dy>p.x:
                inside = not inside
        return inside

    def near_edges(self, bounds, reach):
        x0,y0,x1,y1 = bounds
        seen = set()
        for row in range(self.row(y0-reach),self.row(y1+reach)+1):
            for a,b in self.rows.get(row, ()):
                key=(a,b)
                if key in seen:
                    continue
                seen.add(key)
                if min(a.x,b.x)>x1+reach or max(a.x,b.x)<x0-reach:
                    continue
                if min(a.y,b.y)>y1+reach or max(a.y,b.y)<y0-reach:
                    continue
                yield a,b

    def closer_than(self, p, limit):
        for a,b in self.near_edges((p.x,p.y,p.x,p.y),limit):
            dx,dy=b.x-a.x,b.y-a.y
            length=dx*dx+dy*dy
            t=0 if length<=1e-9 else max(0,min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/length))
            if math.hypot(p.x-a.x-dx*t,p.y-a.y-dy*t)<limit:
                return True
        return False
