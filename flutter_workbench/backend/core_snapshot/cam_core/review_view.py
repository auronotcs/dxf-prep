"""Pure view framing helpers; never modify geometry or saved layer settings."""
import math


def uncovered_canvas_rect(width, height, obstruction, gap=24):
    """Largest rectangular canvas area outside a dialog, in canvas pixels.

    Prefer the right side on equal-area ties. The gap also covers window chrome
    and shadows; root-window coordinates are converted by the caller.
    """
    full=(0.0,0.0,float(width),float(height))
    x0,y0,x1,y1=obstruction
    x0=max(0.,x0-gap);y0=max(0.,y0-gap)
    x1=min(float(width),x1+gap);y1=min(float(height),y1+gap)
    if x1<=x0 or y1<=y0:return full
    candidates=((x1,0.,float(width),float(height)),
                (0.,0.,x0,float(height)),
                (0.,y1,float(width),float(height)),
                (0.,0.,float(width),y0))
    usable=[r for r in candidates if r[2]-r[0]>=160 and r[3]-r[1]>=140]
    return max(usable,key=lambda r:(r[2]-r[0])*(r[3]-r[1])) if usable else None


def review_transform(bounds, viewport, units_per_inch=1., padding=50.):
    """True-center framing with physical context and a bounded inspection zoom."""
    if not all(math.isfinite(v) for v in (*bounds,*viewport,units_per_inch)) or units_per_inch<=0:
        raise ValueError('Review bounds and unit scale must be finite.')
    x0,y0,x1,y1=bounds;left,top,right,bottom=viewport
    if x1<x0 or y1<y0 or right<=left or bottom<=top:
        raise ValueError('Invalid review bounds or viewport.')
    width=max(x1-x0,.1*units_per_inch)
    height=max(y1-y0,.1*units_per_inch)
    scale=min(max(1.,right-left-2*padding)/width,
              max(1.,bottom-top-2*padding)/height,5000./units_per_inch)
    # Do not anchor a substituted minimum span at the original minimum corner.
    return (scale,(left+right)/2-(x0+x1)/2*scale,
            (top+bottom)/2+(y0+y1)/2*scale)
