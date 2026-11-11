"""Explicit source/copy relationships, verified against current geometry."""
from .model import Circle, Line, Polyline, Rectangle


def _geometry_key(entity):
    if isinstance(entity, Polyline):
        return (Polyline, entity.closed, tuple((p.x, p.y) for p in entity.points))
    if isinstance(entity, Rectangle):
        return (Rectangle, entity.corner_a, entity.corner_b)
    if isinstance(entity, Circle):
        return (Circle, entity.center, entity.radius)
    if isinstance(entity, Line):
        return (Line, entity.start, entity.end)
    return None


def coincident_process_copies(project):
    """Copies of the same outline are not unrelated entry obstacles.

    Require both provenance and exact current geometry. Moved/edited copies,
    coincident independent parts, and mere layer names never grant exclusion.
    This does not exempt a different partial-depth boundary from interference.
    """
    entities = {entity.id: entity for entity in project.entities}
    result = {}
    for copy in project.entities:
        metadata = copy.metadata
        if not metadata.get('layer_prep_generated') or metadata.get('layer_prep_role') != 'cleanout_outline':
            continue
        source = entities.get(metadata.get('layer_prep_source_entity_id'))
        if source is None or source.id == copy.id:
            continue
        shape = _geometry_key(source)
        if shape is not None and shape == _geometry_key(copy):
            result.setdefault(source.id, set()).add(copy.id)
            result.setdefault(copy.id, set()).add(source.id)
    return result


def noncutting_bent_source_ids(project):
    """Identify preserved bent-tab originals, never real collision obstacles.

    Layer names/visibility alone grant no exemption. Require application
    provenance, explicit non-machining status and a surviving generated owner.
    A reference explicitly targeted by an enabled operation remains checked.
    The original need not equal the owner: the owner now includes its tabs.
    """
    entities = {entity.id: entity for entity in project.entities}
    targeted = {
        entity_id for operation in project.operations if operation.enabled
        for entity_id in operation.entity_ids
    }
    result = set()
    for entity in project.entities:
        metadata = entity.metadata
        if (not metadata.get('bent_tab_source_reference')
                or not metadata.get('bent_tab_generated')
                or metadata.get('reference_only') is not True
                or metadata.get('machinable') is not False
                or entity.id in targeted):
            continue
        owner = entities.get(metadata.get('mounting_owner_entity_id'))
        if (owner is not None and owner.id != entity.id
                and owner.metadata.get('bent_tab_generated')
                and owner.metadata.get('bent_tab_cut_outline')
                and not owner.metadata.get('reference_only')
                and owner.metadata.get('machinable') is not False):
            result.add(entity.id)
    return result
