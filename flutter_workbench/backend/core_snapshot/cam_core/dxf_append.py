"""Prepare an additive DXF import without changing the destination document."""
from copy import deepcopy
from dataclasses import dataclass
import math
from uuid import uuid4

from .layout import drawing_bounds, entity_bounds
from .model import Entity
from .transform import transform_entity
from .units import UNIT_TO_INCHES, convert_project_units


@dataclass
class PreparedDxfAppend:
    entities: list[Entity]
    bounds: tuple[float, float, float, float]
    renamed_layers: dict[str, str]


def prepare_dxf_append(destination, imported, *, source_units=None, lower_left=None):
    """Convert only incoming geometry; never replace job setup/tools/operations.

    Hidden/locked destination layers are not unlocked. Incoming geometry on
    those names gets a new layer so it remains visible and movable.
    """
    units = source_units or imported.units
    if units not in UNIT_TO_INCHES or destination.units not in UNIT_TO_INCHES:
        raise ValueError('Choose supported source and job units.')
    if not imported.entities:
        raise ValueError('The DXF contains no supported geometry to add.')
    source = deepcopy(imported)
    source.units = units  # An explicit override interprets raw DXF coordinates.
    converted = convert_project_units(source, destination.units)
    def finite(value):
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, dict):
            return all(finite(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(item) for item in value)
        return True
    for entity in converted.entities:
        box = entity_bounds(entity)
        if box is None or not finite(entity.to_dict()) or not all(math.isfinite(v) for v in box):
            raise ValueError('The DXF contains geometry with invalid coordinates.')
    bounds = drawing_bounds(converted.entities, include_annotations=True)
    dx = dy = 0.0
    if lower_left is not None:
        if len(lower_left) != 2 or not all(math.isfinite(v) for v in lower_left):
            raise ValueError('Import placement must be finite X/Y coordinates.')
        dx, dy = lower_left[0] - bounds[0], lower_left[1] - bounds[1]
    settings = destination.metadata.get('layer_settings', {})
    reserved = {e.layer for e in destination.entities} | set(settings) | {e.layer for e in converted.entities}
    renamed = {}
    for layer in sorted({e.layer for e in converted.entities}):
        state = settings.get(layer, {})
        if state.get('locked') or state.get('visible') is False:
            base = f'{layer} (import)'
            name = base
            suffix = 2
            while name in reserved:
                name = f'{base} {suffix}'
                suffix += 1
            renamed[layer] = name
            reserved.add(name)
    batch = uuid4().hex
    entities = []
    for entity in converted.entities:
        added = transform_entity(entity, delta_x=dx, delta_y=dy, preserve_id=False)
        for key in ('object_number', 'object_created_order'):
            added.metadata.pop(key, None)
        added.metadata['dxf_append_batch'] = batch
        added.metadata['dxf_append_source'] = {
            'file': str(imported.source.get('file', '')),
            'units': units, 'layer': entity.layer,
        }
        added.layer = renamed.get(entity.layer, entity.layer)
        entities.append(added)
    return PreparedDxfAppend(entities, drawing_bounds(entities, include_annotations=True), renamed)
