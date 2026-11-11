"""Dimension presentation and guarded drafts; geometry stays in the captured core."""
import copy
import math
from dataclasses import asdict
from cam_core.dimensions import (DimensionAnnotation, DimensionReference, dimension_reference_candidates,
    dimension_reference, resolve_dimension, refresh_dimension_fallbacks, dimension_bounds)
from cam_core.model import Point, new_id
from cam_core.automation import _automation_layer_settings


def display(project, annotation):
    current = refresh_dimension_fallbacks([annotation], project.entities)[0]
    result = dict(annotation=current.to_dict(), id=annotation.id)
    try:
        resolved = resolve_dimension(current, project.entities, project.units)
        result.update(asdict(resolved), bounds=dimension_bounds(resolved, current.text_height))
    except ValueError as error:
        result.update(label=str(error), resolved=False, error=str(error))
    return result


def inventory(project):
    return [display(project, item) for item in project.dimensions]


def visible(project, entity):
    settings = _automation_layer_settings(project).get(entity.layer or '0', {})
    return settings.get('visible', True) and not entity.metadata.get('annotation_hidden', False)


def pick(project, params):
    target = Point(float(params['x']), float(params['y']))
    tolerance = float(params['tolerance'])
    if not all(math.isfinite(v) for v in (target.x,target.y,tolerance)) or tolerance <= 0:
        raise ValueError('Use a positive finite pick tolerance and finite coordinates.')
    result = DimensionReference(target)
    distance = tolerance
    for entity in project.entities:
        if not visible(project,entity): continue
        for feature, point in dimension_reference_candidates(entity):
            candidate = target.distance_to(point)
            if candidate < distance:
                result = dimension_reference(entity,feature)
                distance = candidate
    return result.to_dict()


def context(project, selected):
    refs = []
    for entity in project.entities:
        if entity.id not in selected or not visible(project,entity): continue
        for feature, point in dimension_reference_candidates(entity):
            refs.append(dict(label=f'{entity.kind} {entity.id[-6:]} / {feature}',
                             reference=DimensionReference(point,entity.id,feature).to_dict()))
    return refs


class DimensionDraft:
    def __init__(self):
        self.cached = None

    @staticmethod
    def editable(project, layer):
        settings = _automation_layer_settings(project).get(layer, {})
        if settings.get('locked') or settings.get('visible') is False:
            raise ValueError('Choose a visible, unlocked annotation layer.')

    def preview(self, project, params, revision, digest):
        self.cached = None
        identity = params.get('id')
        previous = next((d for d in project.dimensions if d.id == identity), None)
        if identity and previous is None: raise ValueError('The dimension no longer exists.')
        if previous: self.editable(project,previous.layer)
        raw = copy.deepcopy(params['annotation'])
        raw['id'] = previous.id if previous else new_id('dimension')
        raw['metadata'] = {**(previous.metadata if previous else {}), **raw.get('metadata',{})}
        if type(raw.get('precision')) is not int: raise ValueError('Decimal places must be a whole number from 0 to 6.')
        if not str(raw.get('layer','')).strip(): raise ValueError('Enter an annotation layer.')
        for key in ('start','end','vertex'):
            ref = raw.get(key)
            if ref is None: continue
            for value in ref['fallback'].values():
                if not math.isfinite(float(value)): raise ValueError('Reference coordinates must be finite.')
            if ref.get('entity_id'):
                entity = next((e for e in project.entities if e.id==ref['entity_id']),None)
                oldref = getattr(previous,key) if previous else None
                unchanged = oldref and oldref.entity_id==ref['entity_id'] and oldref.feature==ref['feature']
                if not unchanged:
                    if entity is None or not visible(project,entity): raise ValueError('Choose a visible reference or free coordinates.')
                    raw[key] = dimension_reference(entity,ref['feature']).to_dict()
        annotation = DimensionAnnotation.from_dict(raw)
        self.editable(project,annotation.layer)
        resolve_dimension(annotation,project.entities,project.units)
        token = new_id('dimension-preview')
        self.cached = dict(token=token,revision=revision,digest=digest,annotation=annotation,identity=identity)
        return dict(token=token,revision=revision,dimension=display(project,annotation))

    def apply(self, project, params, revision, digest):
        cached = self.cached
        if not cached or params.get('token')!=cached['token'] or revision!=cached['revision'] or digest!=cached['digest']:
            raise ValueError('The drawing or draft changed. Preview the dimension again.')
        item = copy.deepcopy(cached['annotation'])
        if cached['identity']:
            project.dimensions = [item if d.id==item.id else d for d in project.dimensions]
        else:
            project.dimensions.append(item)
        self.cached = None

    def delete(self, project, identity):
        item = next((d for d in project.dimensions if d.id==identity),None)
        if item is None: raise ValueError('Choose an existing dimension.')
        self.editable(project,item.layer)
        project.dimensions = [d for d in project.dimensions if d.id!=identity]
