"""Guide-driven proposals using the same split/apply engine as Tkinter."""
import copy
import math
from cam_core.model import Point
from cam_core.layout import entity_bounds
from cam_core.automation import _automation_layer_settings
from cam_core.shape_construction import cleave_shapes, apply_cleave_result


def propose(project, selected_ids, guide_id=None, line=None):
    guide = None
    if line is None:
        guide = next((g for g in project.guides if g.id == guide_id), None)
        if guide is None or not guide.visible:
            raise ValueError('Choose a visible construction guide.')
        guide.validate()
        start, end = ((Point(0, guide.position), Point(1, guide.position)) if guide.orientation == 'horizontal'
                      else (Point(guide.position, 0), Point(guide.position, 1)))
    else:
        if guide_id is not None: raise ValueError('Choose a guide or a finite line, not both.')
        if not isinstance(line, list) or len(line) != 2:
            raise ValueError('Pick two endpoints for the finite splitting line.')
        start, end = (Point(float(p['x']), float(p['y'])) for p in line)
        if not all(math.isfinite(v) for v in (start.x,start.y,end.x,end.y)):
            raise ValueError('Splitting line coordinates must be finite.')
    ids = set(selected_ids)
    compounds = {e.metadata['compound_id'] for e in project.entities
                 if e.id in ids and e.metadata.get('compound_id')}
    ids.update(e.id for e in project.entities if e.metadata.get('compound_id') in compounds)
    selected = [e for e in project.entities if e.id in ids]
    settings = _automation_layer_settings(project)
    for e in selected:
        layer = settings.get(e.layer, {})
        if layer.get('locked') or layer.get('visible') is False or e.metadata.get('annotation_hidden'):
            raise ValueError('Every contour in the selected compound must be visible and unlocked.')
        if e.metadata.get('reference_only'):
            raise ValueError('Reference-only geometry cannot be split.')
        if e.metadata.get('mounting_generated'):
            raise ValueError('Generated mounting features must be regenerated from their owner; select source outlines.')
    result = cleave_shapes(selected, start, end, infinite_line=line is None)
    candidate = copy.deepcopy(project)
    applied = apply_cleave_result(candidate, copy.deepcopy(result))
    removed = set(applied.removed_dependent_ids)
    replaced = set(result.source_entity_ids) | removed
    if any(settings.get(e.layer, {}).get('locked') for e in project.entities if e.id in removed):
        raise ValueError('A generated dependent is on a locked layer. Unlock it before splitting its owner.')
    details = dict(
        guide=guide.to_dict() if guide else None, line=line, areaBefore=result.area_before, areaAfter=result.area_after,
        unaffectedCount=len(result.unaffected_entity_ids), expandedCount=len(ids-set(selected_ids)),
        leftCount=sum(e.metadata.get('cleave_side') == 'left' for e in result.entities),
        rightCount=sum(e.metadata.get('cleave_side') == 'right' for e in result.entities),
        holeCount=sum(bool(e.metadata.get('cleave_hole')) for e in result.entities),
        circleCount=sum(e.kind == 'circle' for e in selected),
        detachedDimensionCount=sum(any(ref is not None and ref.entity_id in replaced
            for ref in (d.start, d.end, d.vertex)) for d in project.dimensions),
        removedDependentIds=sorted(removed), reviewedLedPlanIds=list(applied.reviewed_led_plan_ids),
        sources=[dict(e.to_dict(), bounds=entity_bounds(e)) for e in selected],
    )
    return candidate, list(result.entities), set(result.source_entity_ids), removed, details
