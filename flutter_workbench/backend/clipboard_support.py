"""Geometry clipboard parity with captured Tkinter build src-20261027-8bc5a2e4449c.

Core entities/transforms remain authoritative. Clipboard data is an isolated copy,
not document history; paste/cut are committed by the bridge as one transaction.
"""
import copy
from dataclasses import replace
from cam_core.model import new_id, Point, CamProject
from cam_core.dimensions import DimensionReference, refresh_dimension_fallbacks
from cam_core.automation import _automation_layer_settings
import json
from cam_core.transform import transform_entity
from cam_core.led_layout import LEDLayout, layout_from_plan
from arrangement_support import ArrangementSupport


def expanded_entities(project, selected_ids):
    ids = set(selected_ids)
    for group in ArrangementSupport(project).groups().values():
        if ids.intersection(e.id for e in group):
            ids.update(e.id for e in group)
    # Keep hidden production outlines registered to the copied owner.
    ids = ArrangementSupport(project)._registered_transform_ids(ids)
    return [e for e in project.entities if e.id in ids]


def capture_led(project, ids):
    result = []
    for plan in project.metadata.get('led_plans', []):
        if not isinstance(plan, dict):
            continue
        layout = layout_from_plan(plan)
        matches = [p for p in layout.placements if p.owner_part_id in ids] if layout else []
        if matches:
            result.append((str(plan.get('id', '')), copy.deepcopy(matches),
                set(plan.get('linked_entity_ids', [])).intersection(ids)))
    return result


def clone_entities(entities, dx=0, dy=0, layer=None):
    copies = [transform_entity(e, dx, dy, preserve_id=False) for e in entities]
    ids = {e.id: c.id for e, c in zip(entities, copies)}
    maps = {key: {} for key in ('group_id', 'compound_id', 'mounting_part_id')}
    for e in copies:
        # Display identities are document-local too; never retain an original's
        # assigned number on a new UUID. The core registry can assign new ones.
        e.metadata.pop('object_number', None)
        e.metadata.pop('object_created_order', None)
        if layer is not None:
            e.layer = layer
        for key, mapping in maps.items():
            source = e.metadata.get(key)
            if source:
                e.metadata[key] = mapping.setdefault(source, new_id(key))
    for e in copies:
        for key in ('mounting_owner_entity_id', 'owner_entity_id'):
            source = e.metadata.get(key)
            if source in ids:
                e.metadata[key] = ids[source]
        for key in ('bent_tab_inner_cutout_ids', 'pattern_source_entity_ids'):
            source = e.metadata.get(key)
            if isinstance(source, list):
                e.metadata[key] = [ids.get(str(i), str(i)) for i in source]
        for key in ('mounting_feature_key', 'mounting_scope_key', 'mounting_pair_id'):
            source = e.metadata.get(key)
            if isinstance(source, str):
                for old, new in maps['mounting_part_id'].items():
                    source = source.replace(str(old), new)
                e.metadata[key] = source
    return copies, ids


def paste_led(project, captured, ids, dx, dy):
    factor = 1 / 25.4 if project.units == 'mm' else 1
    for plan_id, placements, links in captured:
        plan = next((p for p in project.metadata.get('led_plans', [])
            if isinstance(p, dict) and str(p.get('id', '')) == plan_id), None)
        if plan is None:
            raise ValueError('This clipboard includes LED placements. Paste into its original LED plan drawing.')
        layout = layout_from_plan(plan) or LEDLayout()
        additions = [replace(p, placement_id=new_id('led_copy'), owner_part_id=ids[p.owner_part_id],
            x_in=p.x_in+dx*factor, y_in=p.y_in+dy*factor) for p in placements]
        plan['layout'] = LEDLayout((*layout.placements, *additions)).to_dict()
        plan['linked_entity_ids'] = sorted(set(plan.get('linked_entity_ids', [])) | {ids[i] for i in links})


def cut_led(project, ids):
    for plan in project.metadata.get('led_plans', []):
        if not isinstance(plan, dict):
            continue
        layout = layout_from_plan(plan)
        if layout:
            plan['layout'] = LEDLayout(tuple(p for p in layout.placements if p.owner_part_id not in ids)).to_dict()
        plan['linked_entity_ids'] = [i for i in plan.get('linked_entity_ids', []) if i not in ids]


def capture_annotations(project, dimension_ids, guide_ids, *, cutting=False):
    dims = [d for d in project.dimensions if d.id in dimension_ids]
    guides = [g for g in project.guides if g.id in guide_ids]
    if {d.id for d in dims} != set(dimension_ids) or {g.id for g in guides} != set(guide_ids):
        raise ValueError('An annotation or guide no longer exists; select it again.')
    settings = _automation_layer_settings(project)
    for d in dims:
        layer = settings.get(d.layer, {})
        if layer.get('locked') or layer.get('visible') is False or d.metadata.get('annotation_hidden'):
            raise ValueError('Show and unlock annotation layers before copying or cutting.')
    if any(not g.visible or (cutting and g.locked) for g in guides):
        raise ValueError('Show guides before copying; unlock them before cutting.')
    return copy.deepcopy(refresh_dimension_fallbacks(dims, project.entities)), copy.deepcopy(guides)


def clone_annotations(dims, guides, ids, dx, dy, layer=None):
    dims, guides = copy.deepcopy(dims), copy.deepcopy(guides)
    for d in dims:
        d.id = new_id('dimension')
        if layer is not None: d.layer = layer
        for key in ('start', 'end', 'vertex'):
            ref = getattr(d, key)
            if ref is None: continue
            # Only copied owners remain associative. All other references become
            # explicit free points, so IDs in another drawing cannot rebind them.
            target = ids.get(ref.entity_id, '')
            setattr(d, key, DimensionReference(Point(ref.fallback.x+dx, ref.fallback.y+dy),
                target, ref.feature if target else 'free'))
        d.validate()
    for g in guides:
        g.id = new_id('guide')
        g.position += dy if g.orientation == 'horizontal' else dx
        g.validate()
    return dims, guides


def envelope(entities, dims, guides, units, has_led):
    return dict(version=1, units=units, entities=[e.to_dict() for e in entities],
        dimensions=[d.to_dict() for d in dims], guides=[g.to_dict() for g in guides], hasLED=bool(has_led))


def read_envelope(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported drawing clipboard format.')
    if len(json.dumps(data, allow_nan=False)) > 16*1024*1024:
        raise ValueError('Drawing clipboard exceeds 16 MiB.')
    if data.get('hasLED'):
        raise ValueError('LED placements must be pasted within their original drawing tab.')
    if data.get('units') not in {'in','mm'}:
        raise ValueError('Invalid drawing clipboard units.')
    payload = CamProject(name='Clipboard').to_dict()
    payload.update({k: data[k] for k in ('units','entities','dimensions','guides')})
    project = CamProject.from_dict(payload)
    json.dumps(project.to_dict(), allow_nan=False)
    ids = [item.id for item in [*project.entities,*project.dimensions,*project.guides]]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate drawing clipboard identities.')
    return project
