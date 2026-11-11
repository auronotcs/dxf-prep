"""Read-only selection measurements, resolved by the captured geometry core."""
from cam_core.layout import entity_perimeter
from cam_core.workflow import entity_area
from cam_core.model import Circle, Rectangle, Polyline, TextEntity


def selection_inspection(entities):
    items = []
    for entity in entities:
        closed = isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and entity.closed
        text = isinstance(entity, TextEntity)
        items.append(dict(id=entity.id, closed=closed, approximateBounds=text,
                          length=None if text else entity_perimeter(entity),
                          area=entity_area(entity) if closed else None))
    lengths = [item['length'] for item in items if item['length'] is not None]
    areas = [item['area'] for item in items if item['area'] is not None]
    return dict(items=items, totalLength=sum(lengths) if lengths else None,
                contourAreaSum=sum(areas) if areas else None,
                lengthCount=len(lengths), areaCount=len(areas),
                textCount=sum(item['approximateBounds'] for item in items))
