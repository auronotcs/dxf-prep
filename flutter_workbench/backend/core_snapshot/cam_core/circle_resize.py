"""Batch circle diameter edits, independent of selection bounding boxes."""
import copy
import math

from .feature_recognition import circular_polyline_to_circle
from .model import Circle
from .units import unit_scale


def circle_resize_candidates(entities, selected_ids, *, units="in"):
    """Recognize only explicitly selected circles; never mutate source geometry."""
    ids = set(selected_ids)
    candidates = []
    for entity in entities:
        if entity.id not in ids or entity.metadata.get("reference_only"):
            continue
        candidate = entity if isinstance(entity, Circle) else circular_polyline_to_circle(
            entity, absolute_tolerance=.001 * unit_scale("in", units),
            relative_tolerance=.005, minimum_vertices=8,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def resize_selected_circles(entities, selected_ids, diameter, *, units="in", locked_layers=()):
    """Return a new entity list and changed IDs, preserving circle centers and identity."""
    diameter = float(diameter)
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError("New diameter must be a finite number greater than zero.")
    entities = list(entities)
    candidates = circle_resize_candidates(entities, selected_ids, units=units)
    if not candidates:
        raise ValueError("Select circles first. Use Circles Ø to find them by diameter.")
    locked = set(locked_layers)
    if any((circle.layer or "0") in locked for circle in candidates):
        raise ValueError("A selected circle is on a locked layer. Unlock it or remove it from the selection first.")
    originals = {entity.id: entity for entity in entities}
    replacements = {}
    for circle in candidates:
        if isinstance(originals[circle.id], Circle) and circle.radius == diameter / 2:
            continue
        resized = copy.deepcopy(circle)
        resized.radius = diameter / 2
        replacements[circle.id] = resized
    return [replacements.get(entity.id, entity) for entity in entities], set(replacements)
