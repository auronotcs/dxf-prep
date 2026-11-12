"""Experimental contour nest. No grid search or application state mutation.

Polygon material is decomposed into convex scanline trapezoids (even/odd fill),
including holes. Pairwise convex Minkowski sums form forbidden translations.
Clip those from the stock's inner-fit rectangle; score its boundary vertices.
Not a globally optimal solver. Explicit complexity/cancel guards fail closed.
"""
from dataclasses import dataclass
from collections import OrderedDict
import math
import time


class SearchStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class Part:
    key: str
    paths: tuple


@dataclass(frozen=True)
class Placement:
    key: str
    sheet: int
    angle: float
    x: float
    y: float
    paths: tuple


def hull(points):
    points = sorted(set(points))
    def cross(a,b,c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lo=[]; hi=[]
    for p in points:
        while len(lo)>1 and cross(lo[-2],lo[-1],p)<=0: lo.pop()
        lo.append(p)
    for p in reversed(points):
        while len(hi)>1 and cross(hi[-2],hi[-1],p)<=0: hi.pop()
        hi.append(p)
    return lo[:-1]+hi[:-1]


class ContactNester:
    def __init__(self, clipper, *, scale=100000, timeout=15, cancelled=lambda:False,
                 max_pair_cells=20000, cache_limit=256, scoring='area', ordering='area'):
        if ordering not in ('area', 'input', 'longest', 'box_area'):
            raise ValueError('Unknown part ordering policy.')
        self.ordering=ordering
        if scoring not in ('area', 'height', 'width'):
            raise ValueError('Unknown candidate scoring policy.')
        self.scoring=scoring
        self.c=clipper; self.scale=scale; self.timeout=timeout
        self.cancelled=cancelled; self.max_pair_cells=max_pair_cells
        self.cache_limit=cache_limit; self.cache=OrderedDict()
        self.stats={}; self.deadline=0

    def check(self):
        if self.cancelled(): raise SearchStopped('Nesting cancelled; no layout applied.')
        if time.perf_counter()>self.deadline: raise SearchStopped('Nesting time budget exhausted; no layout applied.')

    def boolean(self, a, b=(), operation=None, fill=None):
        if not a: return []
        c=self.c; engine=c.Pyclipper()
        engine.AddPaths(a,c.PT_SUBJECT,True)
        if b: engine.AddPaths(b,c.PT_CLIP,True)
        return engine.Execute(c.CT_UNION if operation is None else operation,
                              c.PFT_NONZERO if fill is None else fill,c.PFT_NONZERO)

    def cells(self, paths):
        """Exact horizontal trapezoid decomposition, quantized at scale precision."""
        ys=sorted({y for p in paths for x,y in p}); cells=[]
        edges=[(p[j-1],v) for p in paths for j,v in enumerate(p) if p[j-1][1]!=v[1]]
        for low,high in zip(ys,ys[1:]):
            self.check(); mid=(low+high)/2
            active=[]
            for a,b in edges:
                if min(a[1],b[1])<mid<max(a[1],b[1]):
                    def at(y): return a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
                    active.append((at(mid),at(low),at(high)))
            active.sort()
            if len(active)%2: raise ValueError('Invalid material boundary.')
            for left,right in zip(active[::2],active[1::2]):
                poly=hull([(round(left[1]),low),(round(right[1]),low),
                           (round(right[2]),high),(round(left[2]),high)])
                if len(poly)>=3: cells.append(poly)
        return cells

    def oriented(self, part, angle):
        co=math.cos(math.radians(angle)); si=math.sin(math.radians(angle))
        paths=[[(round((x*co-y*si)*self.scale),round((x*si+y*co)*self.scale)) for x,y in p] for p in part.paths]
        paths=self.boolean(paths,fill=self.c.PFT_EVENODD)
        if not paths: raise ValueError('Empty part: '+part.key)
        x=min(x for p in paths for x,y in p); y=min(y for p in paths for x,y in p)
        paths=[[(a-x,b-y) for a,b in p] for p in paths]
        key=tuple(sorted(tuple(p) for p in paths))
        return {'paths':paths,'cells':self.cells(paths),'key':key,
                'w':max(x for p in paths for x,y in p),'h':max(y for p in paths for x,y in p)}

    def forbidden(self,a,b,gap):
        key=(a['key'],b['key'],gap)
        if key in self.cache:
            self.stats['cache_hits']+=1; self.cache.move_to_end(key); return self.cache[key]
        self.check()
        if len(a['cells'])*len(b['cells'])>self.max_pair_cells:
            raise SearchStopped('Contour complexity exceeds prototype budget; simplify with a controlled tolerance or use existing engine.')
        polys=[]
        for ca in a['cells']:
            self.check()
            for cb in b['cells']:
                poly=hull([(x-u,y-v) for x,y in ca for u,v in cb])
                if not self.c.Orientation(poly): poly.reverse()
                polys.append(poly)
        summed=self.boolean(polys)
        # Arc approximation is inflated by its tolerance plus quantization slack.
        # This is conservative; never treat raster samples as collision proof.
        offset=self.c.PyclipperOffset(2.0,2.0)
        offset.AddPaths(summed,self.c.JT_ROUND,self.c.ET_CLOSEDPOLYGON)
        summed=offset.Execute(gap+6)
        self.cache[key]=summed; self.stats['pair_builds']+=1
        while len(self.cache)>self.cache_limit: self.cache.popitem(last=False)
        return summed

    @staticmethod
    def shift(paths,x,y): return [[(a+x,b+y) for a,b in p] for p in paths]

    def nest(self,parts,width,height,*,spacing=.25,angles=(0,90),max_sheets=20):
        if not parts or not angles: raise ValueError('Parts and allowed angles are required.')
        if not all(math.isfinite(v) for v in (width,height,spacing,*angles)) or min(width,height)<=0 or spacing<0:
            raise ValueError('Invalid sheet/spacing/rotation values.')
        if len({p.key for p in parts})!=len(parts): raise ValueError('Part IDs must be unique.')
        for p in parts:
            if not p.paths or any(len(q)<3 or any(not math.isfinite(v) for xy in q for v in xy) for q in p.paths):
                raise ValueError('Each part requires finite closed polygon contours.')
            if sum(map(len,p.paths))>2000:
                raise SearchStopped('Part exceeds prototype vertex budget; no layout applied.')
            for contour in p.paths:
                raw=[(round(x*self.scale),round(y*self.scale)) for x,y in contour]
                clean=self.c.SimplifyPolygon(raw,self.c.PFT_EVENODD)
                if len(clean)!=1 or abs(abs(self.c.Area(raw))-abs(self.c.Area(clean[0])))>2:
                    raise ValueError('Self-intersecting or degenerate contour: '+p.key)
        self.deadline=time.perf_counter()+self.timeout
        self.stats={'pair_builds':0,'cache_hits':0,'candidate_checks':0}
        W=round(width*self.scale); H=round(height*self.scale); gap=math.ceil(spacing*self.scale)
        prepared=[]
        for part in parts:
            options=[(angle,self.oriented(part,angle)) for angle in dict.fromkeys(angles)]
            area=abs(sum(self.c.Area(p) for p in options[0][1]['paths']))
            prepared.append((part,options,area))
        if self.ordering == 'area':
            prepared.sort(key=lambda v:(-v[2],v[0].key))
        elif self.ordering == 'longest':
            prepared.sort(key=lambda v:(-max(v[1][0][1]['w'],v[1][0][1]['h']),-v[2],v[0].key))
        elif self.ordering == 'box_area':
            prepared.sort(key=lambda v:(-v[1][0][1]['w']*v[1][0][1]['h'],-v[2],v[0].key))
        sheets=[[]]; result=[]
        for part,options,area in prepared:
            self.check(); chosen=None
            for sheet_index in range(max_sheets):
                if sheet_index==len(sheets): sheets.append([])
                placed=sheets[sheet_index]; choices=[]
                for ai,(angle,shape) in enumerate(options):
                    self.check(); xmax=W-gap-shape['w']; ymax=H-gap-shape['h']
                    if xmax<gap or ymax<gap: continue
                    # Degenerate inner-fit regions use explicit corners below.
                    boundary=[(gap,gap),(xmax,gap),(xmax,ymax),(gap,ymax)]
                    blocked=[]
                    for other,x,y in placed:
                        blocked.extend(self.shift(self.forbidden(other,shape,gap),x,y))
                    free=self.boolean([boundary],blocked,self.c.CT_DIFFERENCE) if xmax>gap and ymax>gap else []
                    candidates=set(boundary)
                    for polygon in free: candidates.update(map(tuple,polygon))
                    ranked=[]
                    for x,y in candidates:
                        ex=max([x+shape['w'],*[a+o['w'] for o,a,b in placed]])
                        ey=max([y+shape['h'],*[b+o['h'] for o,a,b in placed]])
                        primary = {'area': (ex*ey,ey,ex),
                                   'height': (ey,ex,ex*ey),
                                   'width': (ex,ey,ex*ey)}[self.scoring]
                        ranked.append(((*primary,ai,y,x),x,y))
                    for score,x,y in sorted(ranked):
                        self.check(); self.stats['candidate_checks']+=1
                        if not (gap<=x<=xmax and gap<=y<=ymax): continue
                        # Independent of feasible-region clipping: reject any
                        # interior collision in the pair forbidden regions.
                        valid=True
                        for other,ox,oy in placed:
                            nfp=self.forbidden(other,shape,gap)
                            locations=[(p,self.c.PointInPolygon((x-ox,y-oy),p)) for p in nfp]
                            winding=0 if any(loc==-1 for p,loc in locations) else sum((1 if self.c.Orientation(p) else -1) for p,loc in locations if loc==1)
                            if winding: valid=False; break
                        if valid:
                            choices.append((score,angle,shape,x,y)); break
                if choices:
                    _,angle,shape,x,y=min(choices,key=lambda v:v[0]); chosen=(sheet_index,angle,shape,x,y); break
                if not placed: raise ValueError('Part does not fit on an empty sheet: '+part.key)
            if chosen is None: raise ValueError('Sheet limit exceeded.')
            index,angle,shape,x,y=chosen; sheets[index].append((shape,x,y))
            material=tuple(tuple((a/self.scale,b/self.scale) for a,b in p) for p in self.shift(shape['paths'],x,y))
            result.append(Placement(part.key,index,angle,x/self.scale,y/self.scale,material))
        return result

