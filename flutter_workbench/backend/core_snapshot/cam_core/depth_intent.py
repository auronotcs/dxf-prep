"""Explicit fill depth intent, in project units and relative to the stock top.

Legacy/custom and partial depths stay numeric. Only an explicitly authored
through fill follows stock thickness; resolution never edits the saved job.
"""
from dataclasses import replace
import math


FILL_DEPTH_LABELS = {"custom": "Saved depth (legacy)", "partial": "Partial depth", "through": "Through cut"}


def through_depth(thickness, breakthrough):
    thickness, breakthrough = float(thickness), float(breakthrough)
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("Through cut requires a positive finite stock thickness in Job Setup.")
    if not math.isfinite(breakthrough) or breakthrough < 0:
        raise ValueError("Breakthrough must be finite and nonnegative.")
    return thickness + breakthrough


def resolve_fill_depth(project, operation):
    from .model import IslandFillOperation
    if not isinstance(operation, IslandFillOperation):
        return operation
    if operation.cut_intent not in FILL_DEPTH_LABELS:
        raise ValueError("Choose a valid fill depth intent.")
    if not math.isfinite(operation.breakthrough) or operation.breakthrough < 0:
        raise ValueError("Breakthrough must be finite and nonnegative.")
    if operation.cut_intent == "through":
        setup = project.metadata.get("job_setup", {})
        depth = through_depth(setup.get("material_thickness", 0), operation.breakthrough)
        return replace(operation, target_depth=depth)
    if not math.isfinite(operation.target_depth) or operation.target_depth <= 0:
        raise ValueError("Fill depth must be finite and positive.")
    return operation


def resolved_fill_project(project):
    """Non-mutating view used by validation, including dependent rest operations."""
    return replace(project, operations=[resolve_fill_depth(project, op) if op.enabled else op
                                        for op in project.operations])
