from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

import ezdxf
from ezdxf.path import make_path
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union


source = ROOT / "St. mark life center export.dxf"
doc = ezdxf.readfile(source)
msp = doc.modelspace()

lines = []
for entity in msp:
    points = [(p.x, p.y) for p in make_path(entity).flattening(distance=0.002)]
    if len(points) >= 2:
        lines.append(LineString(points))

noded = unary_union(lines)
polygons = list(polygonize(noded))

print("entities:", Counter(e.dxftype() for e in msp))
print("line strings:", len(lines))
print("polygons:", len(polygons))
for index, poly in enumerate(sorted(polygons, key=lambda p: p.bounds[0]), 1):
    x0, y0, x1, y1 = poly.bounds
    print(
        f"{index:02d} bounds=({x0:.3f},{y0:.3f})-({x1:.3f},{y1:.3f}) "
        f"size={x1-x0:.3f}x{y1-y0:.3f} area={poly.area:.3f}"
    )
