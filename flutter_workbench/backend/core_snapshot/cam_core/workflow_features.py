"""Associative CAM, completeness, keepout, dependency, and template helpers.

This module deliberately has no Tk dependency so every workflow is available
to the GUI, automation layer, tests, and future DXF-cleaner integration.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Iterable, Sequence

from .layout import entity_bounds, entity_perimeter
from .model import (
    CamProject, Circle, Entity, KeepoutZone, Line, Operation, Point, Polyline,
    ProfileRestMachiningOperation, Rectangle, RouterOffsetOperation, TextEntity,
    _operation_from_dict, effective_tool, new_id,
)
from .operation_links import capture_profile_rest_dependency
from .stock import generate_operation_toolpath
from .toolpath import MotionKind, partial_entry_through_pairs
from .motion_verifier import verify_generated_motion
from .units import convert_project_units


@dataclass(frozen=True)
class SelectorResolution:
    selector: dict[str, Any]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class RevisionReport:
    preserved_entities: int
    replaced_entities: int
    added_entities: int
    removed_entities: int
    rebound_operations: tuple[str, ...] = ()
    unresolved_operations: tuple[str, ...] = ()
    changed_entities: tuple[str, ...] = ()
    invalidated_dependents: tuple[str, ...] = ()
    preserved_application_entities: int = 0
    revision_units_converted_from: str = ""


@dataclass(frozen=True)
class ToolpathCompletenessIssue:
    severity: str
    code: str
    message: str
    operation_id: str
    entity_ids: tuple[str, ...] = ()
    point: Point | None = None


@dataclass(frozen=True)
class ToolpathCompletenessReport:
    operation_count: int
    expected_entity_count: int
    covered_entity_count: int
    generated_block_count: int
    issues: tuple[ToolpathCompletenessIssue, ...] = ()

    @property
    def complete(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass
class ToolpathTemplate:
    name: str
    operations: list[dict[str, Any]]
    description: str = ""
    id: str = field(default_factory=lambda: new_id("template"))
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "version": self.version, "operations": deepcopy(self.operations),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolpathTemplate":
        return cls(
            id=str(payload.get("id") or new_id("template")),
            name=str(payload["name"]), description=str(payload.get("description", "")),
            version=int(payload.get("version", 1)), operations=deepcopy(payload.get("operations", [])),
        )


def entity_fingerprint(entity: Entity, tolerance: float = 1e-6) -> str:
    """Return a stable geometry/layer fingerprint independent of entity ID."""
    if tolerance <= 0:
        raise ValueError("Fingerprint tolerance must be positive.")

    def q(value: float) -> int:
        return round(value / tolerance)

    if isinstance(entity, Circle):
        geometry: Any = ("circle", q(entity.center.x), q(entity.center.y), q(entity.radius))
    elif isinstance(entity, Rectangle):
        geometry = ("rectangle", q(min(entity.corner_a.x, entity.corner_b.x)), q(min(entity.corner_a.y, entity.corner_b.y)), q(max(entity.corner_a.x, entity.corner_b.x)), q(max(entity.corner_a.y, entity.corner_b.y)))
    elif isinstance(entity, Line):
        ends = sorted(((q(entity.start.x), q(entity.start.y)), (q(entity.end.x), q(entity.end.y))))
        geometry = ("line", *ends)
    elif isinstance(entity, Polyline):
        points = [(q(point.x), q(point.y)) for point in entity.points]
        if entity.closed and points:
            rotations = [points[index:] + points[:index] for index in range(len(points))]
            reverse = list(reversed(points))
            rotations += [reverse[index:] + reverse[:index] for index in range(len(reverse))]
            points = min(rotations)
        geometry = ("polyline", bool(entity.closed), points)
    else:
        geometry = (entity.kind,)
    raw = json.dumps((entity.layer.casefold(), geometry), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def selector_from_entities(entities: Sequence[Entity]) -> dict[str, Any]:
    """Build the narrowest useful associative selector for a selection."""
    if not entities:
        raise ValueError("An associative selector requires geometry.")
    selector: dict[str, Any] = {}
    layers = sorted({entity.layer for entity in entities})
    kinds = sorted({entity.kind for entity in entities})
    if len(layers) == 1:
        selector["layers"] = layers
    if len(kinds) == 1:
        selector["kinds"] = kinds
    closed_values = {_entity_closed(entity) for entity in entities}
    if len(closed_values) == 1:
        selector["closed"] = closed_values.pop()
    if all(isinstance(entity, Circle) for entity in entities):
        diameters = [entity.radius * 2 for entity in entities if isinstance(entity, Circle)]
        center = sum(diameters) / len(diameters)
        spread = max(abs(value - center) for value in diameters)
        selector["diameter"] = center
        selector["diameter_tolerance"] = max(spread + 1e-6, center * 0.001)
    selector["scope"] = "all"
    return selector


def resolve_geometry_selector(project: CamProject, selector: dict[str, Any]) -> SelectorResolution:
    entities = list(project.entities)
    layers = {str(value).casefold() for value in selector.get("layers", [])}
    kinds = {str(value).casefold() for value in selector.get("kinds", [])}
    closed = selector.get("closed")
    diameter = _optional_float(selector.get("diameter"))
    diameter_tolerance = _optional_float(selector.get("diameter_tolerance")) or 0.0
    perimeter_min = _optional_float(selector.get("perimeter_min"))
    perimeter_max = _optional_float(selector.get("perimeter_max"))
    bounds = selector.get("bounds")
    matches = []
    for entity in entities:
        # Geometry selectors are persisted specifically to drive CAM. Editable
        # text remains an annotation until an explicit conversion command
        # creates ordinary Line/Polyline geometry.
        if isinstance(entity, TextEntity):
            continue
        if layers and entity.layer.casefold() not in layers:
            continue
        if kinds and entity.kind.casefold() not in kinds:
            continue
        if closed is not None and _entity_closed(entity) is not bool(closed):
            continue
        if diameter is not None:
            if not isinstance(entity, Circle) or abs(entity.radius * 2 - diameter) > diameter_tolerance:
                continue
        perimeter = entity_perimeter(entity)
        if perimeter_min is not None and perimeter < perimeter_min:
            continue
        if perimeter_max is not None and perimeter > perimeter_max:
            continue
        if bounds and len(bounds) == 4:
            left, bottom, right, top = map(float, bounds)
            entity_box = entity_bounds(entity)
            if entity_box[0] < left or entity_box[1] < bottom or entity_box[2] > right or entity_box[3] > top:
                continue
        matches.append(entity.id)
    return SelectorResolution(deepcopy(selector), tuple(matches))


def refresh_associative_operations(project: CamProject) -> tuple[str, ...]:
    changed = []
    for operation in project.operations:
        if not operation.geometry_selector:
            continue
        ids = list(resolve_geometry_selector(project, operation.geometry_selector).entity_ids)
        if ids != operation.entity_ids:
            operation.entity_ids = ids
            if isinstance(operation, RouterOffsetOperation):
                operation.entity_sides = {key: value for key, value in operation.entity_sides.items() if key in ids}
            changed.append(operation.id)
    return tuple(changed)


def _is_proven_dxf_source(entity: Entity) -> bool:
    """True only for imported source, never an application-generated copy."""
    metadata = entity.metadata
    # Additive imports belong to another source file, not the base drawing
    # replaced by Update from Revised DXF. Preserve them as independent work.
    if metadata.get("dxf_append_batch"):
        return False
    if metadata.get("bent_tab_source_reference") or metadata.get("tab_slot_source_reference"):
        return False
    if metadata.get("mounting_generated") and not metadata.get("bent_tab_cut_outline"):
        return False
    if metadata.get("bent_tab_generated") and not metadata.get("bent_tab_cut_outline"):
        return False
    if metadata.get("layer_prep_generated"):
        return False
    return (
        str(metadata.get("source_provenance", "")).casefold() == "dxf"
        or "dxf_type" in metadata
        or "dxf_types" in metadata
        or "source_dxf" in metadata
    )


def _dxf_identity(entity: Entity) -> tuple[str, tuple[str, ...]] | None:
    """Return a stable DXF identity when the producer supplied handles."""
    metadata = entity.metadata
    handles = metadata.get("dxf_handles")
    if not isinstance(handles, (list, tuple)):
        handle = str(metadata.get("dxf_handle", "")).strip()
        handles = [handle] if handle else []
    normalized = tuple(str(value).strip() for value in handles if str(value).strip())
    return (entity.kind, normalized) if normalized else None


def _merged_source_entity(
    incoming: Entity, prior: Entity, *, preserve_prior_geometry: bool = False,
) -> Entity:
    result = deepcopy(prior if preserve_prior_geometry else incoming)
    result.id = prior.id
    result.metadata = {**deepcopy(prior.metadata), **deepcopy(incoming.metadata)}
    result.metadata["source_provenance"] = "dxf"
    return result


def _clear_invalid_generated_state(entity: Entity) -> None:
    for key in (
        "bent_tab_generated", "bent_tab_cut_outline", "bent_tab_source_reference",
        "tab_slot_generated", "tab_slot_letter_outline", "tab_slot_source_reference", "tab_slot_pair_ids",
        "bent_tab_inner_cutout_ids", "mounting_generated", "mounting_pattern_generated",
        "mounting_workflow", "mounting_scope_key", "mounting_feature_key",
        "mounting_component", "mounting_manual_edit", "reference_only", "machinable",
        "converted_geometry_ids",
    ):
        entity.metadata.pop(key, None)


def _dependent_source_ids(entity: Entity) -> set[str]:
    metadata = entity.metadata
    result = {
        str(metadata.get(key, "")).strip()
        for key in ("mounting_owner_entity_id", "owner_entity_id", "layer_prep_source_entity_id")
        if str(metadata.get(key, "")).strip()
    }
    for key in ("pattern_source_entity_ids",):
        values = metadata.get(key)
        if isinstance(values, (list, tuple, set)):
            result.update(str(value) for value in values if str(value))
    return result


def _invalidate_led_revision_links(project: CamProject, source_ids: set[str]) -> tuple[str, ...]:
    invalidated = []
    for plan in project.metadata.get("led_plans", []):
        if not isinstance(plan, dict):
            continue
        linked = {str(value) for value in plan.get("linked_entity_ids", [])}
        layout = plan.get("layout")
        if isinstance(layout, dict):
            linked.update(
                str(item.get("owner_part_id", ""))
                for item in layout.get("placements", [])
                if isinstance(item, dict)
            )
        affected = sorted((linked & source_ids) - {""})
        if not affected:
            continue
        prior = {str(value) for value in plan.get("revision_invalidated_entity_ids", [])}
        plan["revision_invalidated_entity_ids"] = sorted(prior | set(affected))
        invalidated.append(f"led_plan:{plan.get('id') or plan.get('name') or 'unnamed'}")
    return tuple(invalidated)


def reconcile_dxf_revision(existing: CamProject, revised: CamProject) -> RevisionReport:
    """Reconcile proven DXF source while retaining independent application state."""
    revised_units = revised.units
    normalized = convert_project_units(revised, existing.units)
    for entity in normalized.entities:
        entity.metadata.setdefault("source_provenance", "dxf")

    existing_by_id = {entity.id: entity for entity in existing.entities}
    source_entities = [entity for entity in existing.entities if _is_proven_dxf_source(entity)]
    if existing.entities and not source_entities:
        raise ValueError(
            "This job has no proven DXF source ownership, so CAM Workbench cannot safely decide "
            "which legacy objects a revision may replace. Open the revised DXF as a new job, or "
            "resave the original DXF job once before using Update from Revised DXF."
        )
    source_entity_ids = {entity.id for entity in source_entities}
    application_entities = [
        entity for entity in existing.entities if entity.id not in source_entity_ids
    ]
    bent_references = {
        str(entity.metadata.get("mounting_owner_entity_id")): entity
        for entity in application_entities
        if entity.metadata.get("bent_tab_source_reference")
        and str(entity.metadata.get("mounting_owner_entity_id", ""))
    }
    baselines: dict[str, Entity] = {}
    for entity in source_entities:
        baseline = (
            bent_references.get(entity.id, entity)
            if entity.metadata.get("bent_tab_cut_outline") else entity
        )
        if baseline is not entity:
            baseline = deepcopy(baseline)
            # The hidden recovery reference intentionally lives on an internal
            # layer; source identity still belongs to the owner's DXF layer.
            baseline.layer = entity.layer
        baselines[entity.id] = baseline
    old_by_fingerprint: dict[str, list[Entity]] = {}
    old_by_identity: dict[tuple[str, tuple[str, ...]], list[Entity]] = {}
    for entity in source_entities:
        baseline = baselines[entity.id]
        old_by_fingerprint.setdefault(entity_fingerprint(baseline), []).append(entity)
        identity = _dxf_identity(baseline)
        if identity is not None:
            old_by_identity.setdefault(identity, []).append(entity)

    used_old_ids: set[str] = set()
    new_to_old: dict[str, str] = {}
    incoming_by_old_id: dict[str, Entity] = {}
    reconciled_sources: list[Entity] = []
    changed_source_ids: set[str] = set()
    preserved = 0
    replaced = 0
    added = 0
    for entity in normalized.entities:
        candidates = old_by_fingerprint.get(entity_fingerprint(entity), [])
        match = next((item for item in candidates if item.id not in used_old_ids), None)
        if match is not None:
            new_to_old[entity.id] = match.id
            used_old_ids.add(match.id)
            incoming_by_old_id[match.id] = entity
            reconciled_sources.append(_merged_source_entity(
                entity, match,
                preserve_prior_geometry=bool(match.metadata.get("bent_tab_cut_outline")),
            ))
            preserved += 1
            continue
        identity = _dxf_identity(entity)
        identity_candidates = old_by_identity.get(identity, []) if identity is not None else []
        match = next((item for item in identity_candidates if item.id not in used_old_ids), None)
        if match is not None:
            new_to_old[entity.id] = match.id
            used_old_ids.add(match.id)
            incoming_by_old_id[match.id] = entity
            replacement = _merged_source_entity(entity, match)
            _clear_invalid_generated_state(replacement)
            reconciled_sources.append(replacement)
            changed_source_ids.add(match.id)
            replaced += 1
            continue
        entity.metadata["source_provenance"] = "dxf"
        reconciled_sources.append(entity)
        added += 1

    removed_source_ids = {entity.id for entity in source_entities} - used_old_ids
    invalidated_source_ids = changed_source_ids | removed_source_ids
    # Bent-tab outlines also depend on their selected inner cutouts. If a
    # cutout changed, restore the owner's revised source outline and invalidate
    # its generated reference/guides rather than retaining a stale flat layout.
    for source in source_entities:
        cutouts = {
            str(value) for value in source.metadata.get("bent_tab_inner_cutout_ids", [])
        }
        if not cutouts.intersection(invalidated_source_ids) or source.id in invalidated_source_ids:
            continue
        invalidated_source_ids.add(source.id)
        changed_source_ids.add(source.id)
        incoming = incoming_by_old_id.get(source.id)
        if incoming is not None:
            replacement = _merged_source_entity(incoming, source)
            _clear_invalid_generated_state(replacement)
            reconciled_sources = [
                replacement if entity.id == source.id else entity
                for entity in reconciled_sources
            ]

    invalidated_application_ids = {
        str(value)
        for source in source_entities if source.id in invalidated_source_ids
        for value in source.metadata.get("converted_geometry_ids", [])
    }
    retained_application = []
    invalidated_dependents = []
    for entity in application_entities:
        if (
            entity.id in invalidated_application_ids
            or _dependent_source_ids(entity).intersection(invalidated_source_ids)
        ):
            invalidated_dependents.append(entity.id)
        else:
            retained_application.append(entity)

    existing.entities = reconciled_sources + retained_application
    existing.source = deepcopy(normalized.source)
    for key, value in normalized.metadata.items():
        if str(key).startswith("dxf_"):
            existing.metadata[key] = deepcopy(value)
    invalidated_dependents.extend(_invalidate_led_revision_links(existing, invalidated_source_ids))
    valid_ids = {entity.id for entity in existing.entities}
    unresolved = []
    for operation in existing.operations:
        operation.entity_ids = [new_to_old.get(entity_id, entity_id) for entity_id in operation.entity_ids if new_to_old.get(entity_id, entity_id) in valid_ids]
        if isinstance(operation, RouterOffsetOperation):
            operation.entity_sides = {
                new_to_old.get(entity_id, entity_id): side
                for entity_id, side in operation.entity_sides.items()
                if new_to_old.get(entity_id, entity_id) in valid_ids
            }
            operation.manual_contour_order = [
                new_to_old.get(entity_id, entity_id)
                for entity_id in operation.manual_contour_order
                if new_to_old.get(entity_id, entity_id) in valid_ids
            ]
        if hasattr(operation, "island_entity_ids"):
            operation.island_entity_ids = [new_to_old.get(entity_id, entity_id) for entity_id in operation.island_entity_ids if new_to_old.get(entity_id, entity_id) in valid_ids]
        if not operation.entity_ids and not operation.geometry_selector:
            unresolved.append(operation.id)
    rebound = refresh_associative_operations(existing)
    return RevisionReport(
        preserved_entities=preserved,
        replaced_entities=replaced,
        added_entities=added,
        removed_entities=len(removed_source_ids),
        rebound_operations=rebound, unresolved_operations=tuple(unresolved),
        changed_entities=tuple(sorted(changed_source_ids)),
        invalidated_dependents=tuple(sorted(set(invalidated_dependents))),
        preserved_application_entities=len(retained_application),
        revision_units_converted_from=revised_units if revised_units != existing.units else "",
    )


def analyze_toolpath_completeness(
    project: CamProject,
    operation_ids: set[str] | None = None,
    generated_paths: dict[str, Any] | None = None,
) -> ToolpathCompletenessReport:
    issues: list[ToolpathCompletenessIssue] = []
    expected_total = covered_total = block_total = operation_count = 0
    entities = {entity.id: entity for entity in project.entities}
    for operation in project.operations:
        if not operation.enabled or operation_ids is not None and operation.id not in operation_ids:
            continue
        operation_count += 1
        expected = set(operation.entity_ids)
        expected_total += len(expected)
        generated = (generated_paths or {}).get(operation.id)
        if generated is None:
            try:
                generated = generate_operation_toolpath(project, operation)
            except ValueError as exc:
                issues.append(ToolpathCompletenessIssue("error", "toolpath.generation_failed", f"{operation.name}: {exc}", operation.id, tuple(sorted(expected)), _entity_focus(entities, expected)))
                continue
        block_total += len(generated.blocks)
        if not generated.blocks or not generated.motions:
            issues.append(ToolpathCompletenessIssue("error", "toolpath.no_motion", f"{operation.name} generated no machine motion.", operation.id, tuple(sorted(expected)), _entity_focus(entities, expected)))
            continue
        for issue in verify_generated_motion(project, operation, generated):
            issues.append(ToolpathCompletenessIssue(
                issue.severity, issue.code, issue.message, operation.id,
                issue.entity_ids, issue.point,
            ))
        from .profile_fragment_review import tab_free_fragment_diagnostics
        for issue in tab_free_fragment_diagnostics(generated, project.units):
            issues.append(ToolpathCompletenessIssue(
                issue.severity, issue.code, f"{operation.name}: {issue.message}",
                operation.id, issue.entity_ids, issue.point,
            ))
        if isinstance(operation, RouterOffsetOperation) and operation.common_line_mode != "off":
            combined = [
                block for block in generated.blocks
                if int(block.metadata.get("common_line_lifts_saved", 0)) > 0
            ]
            if not combined:
                issues.append(ToolpathCompletenessIssue(
                    "warning", "toolpath.common_line_no_match",
                    f"{operation.name} requested shared-edge cutting, but no exact compatible rectangular pair was found; ordinary independent profiles will be output.",
                    operation.id, tuple(sorted(expected)), _entity_focus(entities, expected),
                ))
            else:
                saved = sum(int(block.metadata.get("common_line_lifts_saved", 0)) for block in combined)
                issues.append(ToolpathCompletenessIssue(
                    "warning", "toolpath.common_line_release",
                    f"{operation.name} contains {len(combined)} shared-edge pair(s) saving {saved} plunge/retract cycle(s). Verify vacuum hold-down because each pair is released by one continuous trail.",
                    operation.id,
                ))
        ignored = expected.intersection({
            str(value)
            for block in generated.blocks
            for value in block.metadata.get("ignored_entity_ids", ())
            if value
        })
        if ignored:
            # Open Cut Line generation deliberately discloses and excludes
            # zero-length remnants. They are not missing output geometry: they
            # have no machinable length and cannot produce controller motion.
            expected.difference_update(ignored)
            expected_total -= len(ignored)
        from .rest_path_filter import confirmed_no_work_ids
        no_rest_work = expected.intersection(confirmed_no_work_ids(project, operation, generated))
        if no_rest_work:
            expected.difference_update(no_rest_work)
            expected_total -= len(no_rest_work)
            issues.append(ToolpathCompletenessIssue(
                "warning", "toolpath.rest_no_work",
                f"{operation.name}: {len(no_rest_work)} source region(s) need no reachable rest cuts above the ignore-stock threshold; these regions were skipped.",
                operation.id, tuple(sorted(no_rest_work)), _entity_focus(entities, no_rest_work),
            ))
        attributed = {str(block.metadata.get("entity_id")) for block in generated.blocks if block.metadata.get("entity_id")}
        for block in generated.blocks:
            attributed.update(str(value) for value in block.metadata.get("entity_ids", []) if value)
            if block.metadata.get("outer_entity_id"):
                attributed.add(str(block.metadata["outer_entity_id"]))
        if not attributed and len(expected) == 1:
            attributed = set(expected)
        covered = expected.intersection(attributed)
        covered_total += len(covered)
        missing = expected - covered
        if missing:
            issues.append(ToolpathCompletenessIssue(
                "error", "toolpath.missing_geometry",
                f"{operation.name} selected {len(expected)} source object(s), but {len(missing)} produced no attributable toolpath blocks.",
                operation.id, tuple(sorted(missing)), _entity_focus(entities, missing),
            ))
        fragments: dict[str, set[int]] = {}
        for block in generated.blocks:
            entity_id = str(block.metadata.get("entity_id", ""))
            if entity_id:
                fragments.setdefault(entity_id, set()).add(int(block.metadata.get("path_index", 0)))
        for entity_id, indexes in fragments.items():
            if len(indexes) > 1:
                issues.append(ToolpathCompletenessIssue(
                    "warning", "toolpath.split_geometry",
                    f"{operation.name} produces {len(indexes)} separate path fragments for one source object; inspect the offset result.",
                    operation.id, (entity_id,), _entity_focus(entities, {entity_id}),
                ))
    return ToolpathCompletenessReport(operation_count, expected_total, covered_total, block_total, tuple(issues))


def keepout_violations(
    project: CamProject,
    operation_ids: set[str] | None = None,
    generated_paths: dict[str, Any] | None = None,
) -> tuple[ToolpathCompletenessIssue, ...]:
    issues = []
    for operation in project.operations:
        if not operation.enabled or operation_ids is not None and operation.id not in operation_ids:
            continue
        generated = (generated_paths or {}).get(operation.id)
        if generated is None:
            try:
                generated = generate_operation_toolpath(project, operation)
            except ValueError:
                continue
        for zone in project.keepouts:
            if not zone.enabled or len(zone.points) < 3:
                continue
            hit = _first_keepout_hit(generated.blocks, zone)
            if hit is not None:
                issues.append(ToolpathCompletenessIssue(
                    "error", "toolpath.keepout_collision",
                    f"{operation.name} enters keepout '{zone.name}' below its {zone.clearance_z:g} clearance height.",
                    operation.id, point=hit,
                ))
    return tuple(issues)


def entry_interference_violations(
    project: CamProject,
    operation_ids: set[str] | None = None,
    generated_paths: dict[str, Any] | None = None,
) -> tuple[ToolpathCompletenessIssue, ...]:
    """Find entry moves whose cutter sweep reaches unrelated geometry.

    An entry is proved against the contour it belongs to while it is generated,
    but that local proof cannot see a neighboring letter or part.  This second
    pass checks the actual generated helix or linear lead, expanded by the tool
    radius, against every other source contour in the drawing.
    """
    from .geometry_relationships import coincident_process_copies, noncutting_bent_source_ids
    process_copies = coincident_process_copies(project)
    bent_references = noncutting_bent_source_ids(project)
    entities = {entity.id: entity for entity in project.entities}
    issues: list[ToolpathCompletenessIssue] = []
    reported: set[tuple[str, str, str]] = set()
    checked_entries: set[tuple[str, str, int, float, str]] = set()
    for operation in project.operations:
        if (
            not operation.enabled
            or operation_ids is not None and operation.id not in operation_ids
            or not isinstance(operation, RouterOffsetOperation)
        ):
            continue
        tool = effective_tool(project, operation)
        if tool is None or tool.diameter <= 0:
            continue
        generated = (generated_paths or {}).get(operation.id)
        if generated is None:
            try:
                generated = generate_operation_toolpath(project, operation, optimize=False)
            except ValueError:
                continue
        cutter_radius = tool.diameter / 2.0
        later_through_pairs = partial_entry_through_pairs(
            project, operation, tool.diameter,
        )
        for block in generated.blocks:
            source_id = str(block.metadata.get("entity_id", ""))
            if not source_id or source_id not in entities:
                continue
            # Stem recovery is actual slot cutting, not the parent operation's
            # helix/lead. Check its complete sweep even with zero lead-in, and
            # at every depth (do not hide altered later passes by entry dedup).
            if block.metadata.get("role") == "narrow_slot":
                segments, own_hit = _narrow_slot_sweep_check(
                    block, entities[source_id], cutter_radius, project.units,
                )
                if own_hit is not None:
                    key = (operation.id, source_id, source_id)
                    if key not in reported:
                        reported.add(key)
                        issues.append(ToolpathCompletenessIssue(
                            "error", "toolpath.slot_interference",
                            f"{operation.name} has a keyhole stem cut whose cutter-width sweep exceeds its own outline. Check the slot width and cutter diameter.",
                            operation.id, (source_id,), own_hit,
                        ))
                for candidate_id, candidate in entities.items():
                    if (candidate_id == source_id
                            or candidate_id in bent_references
                            or candidate_id in process_copies.get(source_id, set())
                            or candidate_id in later_through_pairs.get(source_id, set())):
                        continue
                    candidate_bounds = entity_bounds(candidate)
                    if not segments or candidate_bounds is None or not _boxes_overlap(
                            _segments_bounds(segments, cutter_radius), candidate_bounds):
                        continue
                    hit = _first_swept_contour_hit(segments, candidate, cutter_radius)
                    key = (operation.id, source_id, candidate_id)
                    if hit is not None and key not in reported:
                        reported.add(key)
                        issues.append(ToolpathCompletenessIssue(
                            "error", "toolpath.slot_interference",
                            f"{operation.name} has a keyhole stem cut whose cutter-width sweep crosses unrelated geometry. Check part spacing and the slot path.",
                            operation.id, (source_id, candidate_id), hit,
                        ))
                continue
            if (operation.entry_mode not in {"helical", "ramp", "plunge"}
                    or operation.entry_mode != "helical" and operation.lead_in <= 1e-8):
                continue
            entry_key = (
                operation.id,
                source_id,
                int(block.metadata.get("path_index", 0)),
                round(float(block.metadata.get("offset_distance", 0.0)), 8),
                str(block.metadata.get("side", "")),
            )
            if entry_key in checked_entries:
                continue
            checked_entries.add(entry_key)
            if block.metadata.get("entry_adjustment_kind") == "checked_neighbor_plunge":
                from .toolpath import _profile_plunge_interference
                excluded = bent_references | process_copies.get(source_id, set()) | later_through_pairs.get(source_id, set())
                landing = block.motions[1].end
                hit = _profile_plunge_interference(Point(landing.x, landing.y), source_id,
                    [e for e in entities.values() if e.id not in excluded], cutter_radius,
                    protect_source=str(block.metadata.get("side", "")) in {"inside", "outside"})
                if hit is not None:
                    issues.append(ToolpathCompletenessIssue("error", "toolpath.entry_interference",
                        f"{operation.name}: the fallback plunge cutter footprint crosses a protected contour.",
                        operation.id, tuple(dict.fromkeys((source_id, hit[0]))), hit[1]))
            if block.metadata.get("entry_adjustment_kind") == "shelf_waste_shift":
                from .toolpath import (_helix_circle, _profile_entry_self_interference,
                                       _entry_segments_interference)
                helix = _helix_circle(block.motions)
                if helix is not None:
                    stop = 2
                    while (stop < len(block.motions) and block.motions[stop].center is not None
                           and block.motions[stop].center.distance_to(helix[0]) < 1e-7):
                        stop += 1
                    link = block.motions[stop-1:stop+1]
                    own_hit = _profile_entry_self_interference(link, "plunge", entities[source_id], cutter_radius)
                    if own_hit is not None:
                        issues.append(ToolpathCompletenessIssue("error", "toolpath.entry_interference",
                            f"{operation.name}: the shelf entry connection gouges its protected face.", operation.id, (source_id,), own_hit))
                    if len(link) == 2:
                        excluded = bent_references | process_copies.get(source_id,set()) | later_through_pairs.get(source_id,set())
                        a,b = (Point(m.end.x,m.end.y) for m in link)
                        hit = _entry_segments_interference([(a,b)],source_id,
                            [e for e in entities.values() if e.id not in excluded],cutter_radius)
                        if hit is not None:
                            issues.append(ToolpathCompletenessIssue("error", "toolpath.entry_interference",
                                f"{operation.name}: the shelf entry connection crosses unrelated geometry.", operation.id, (source_id,hit[0]), hit[1]))
                    for paired in later_through_pairs.get(source_id, ()):
                        hit = _profile_entry_self_interference(block.motions,"helical",entities[paired],cutter_radius)
                        if hit is not None:
                            issues.append(ToolpathCompletenessIssue("error", "toolpath.entry_interference",
                                f"{operation.name}: the relocated shelf helix crosses its paired finished boundary.", operation.id, (source_id,paired), hit))
            actual_mode = str(block.metadata.get("entry_mode_actual", operation.entry_mode))
            # Generation used to exclude its own source from every entry
            # collision check. Verify the actual prepared motions as well;
            # ordinary compensated contact is allowed, gouging is not.
            if str(block.metadata.get("side", "")) in {"inside", "outside"}:
                from .toolpath import _profile_entry_self_interference
                own_hit = _profile_entry_self_interference(block.motions, actual_mode, entities[source_id], cutter_radius)
                if own_hit is not None:
                    key = (operation.id, source_id, source_id)
                    if key not in reported:
                        reported.add(key)
                        issues.append(ToolpathCompletenessIssue(
                            "error", "toolpath.entry_interference",
                            f"{operation.name} has an entry whose cutter-width sweep gouges its own contour. Regenerate or move the entry onto a clear edge.",
                            operation.id, (source_id,), own_hit,
                        ))
            helix_circle = _complete_entry_circle(block.motions) if actual_mode == "helical" else None
            entry_segments = (
                [] if helix_circle is not None else
                _helical_entry_segments(block.motions)
                if actual_mode == "helical"
                else _direct_plunge_entry_segments(block.motions)
                if actual_mode == "plunge_on_contour_fallback"
                else _profile_constrained_entry_excess_segments(block.motions)
                if actual_mode == "contour_ramp_fallback"
                else _linear_entry_segments(block.motions)
            )
            if not entry_segments and helix_circle is None:
                continue
            if helix_circle is not None:
                center, radius = helix_circle
                extent = radius + cutter_radius
                entry_bounds = (center.x-extent, center.y-extent, center.x+extent, center.y+extent)
            else:
                entry_bounds = _segments_bounds(entry_segments, cutter_radius)
            for candidate_id, candidate in entities.items():
                if candidate_id == source_id:
                    continue
                if candidate_id in bent_references:
                    continue
                if candidate_id in process_copies.get(source_id, set()):
                    continue
                if candidate_id in later_through_pairs.get(source_id, set()):
                    # This is the matching later through-cut boundary. With an
                    # automatic waste-side entry and the same cutter/side, any
                    # shallow pre-cut is in material that operation removes.
                    continue
                candidate_bounds = entity_bounds(candidate)
                if candidate_bounds is None or not _boxes_overlap(entry_bounds, candidate_bounds):
                    continue
                hit = (_circle_swept_contour_hit(*helix_circle, candidate, cutter_radius)
                       if helix_circle is not None else
                       _first_swept_contour_hit(entry_segments, candidate, cutter_radius))
                if hit is None:
                    continue
                key = (operation.id, source_id, candidate_id)
                if key in reported:
                    continue
                reported.add(key)
                issues.append(ToolpathCompletenessIssue(
                    "error",
                    "toolpath.entry_interference",
                    f"{operation.name} has a {actual_mode} entry whose cutter-width sweep crosses unrelated geometry. "
                    + (
                        "This can gouge the neighboring part; move the entry, reduce the helix diameter, or increase part spacing."
                        if actual_mode == "helical"
                        else "The requested start plus up to four automatic downstream positions were unsafe. "
                        "Shorten the entry, change cut direction, or increase part spacing."
                    ),
                    operation.id,
                    (source_id, candidate_id),
                    hit,
                ))
    return tuple(issues)


def _narrow_slot_sweep_check(block, source, cutter_radius, units):
    """Prove a linear slot's swept disks stay inside its closed source.

    Imported six-decimal coordinates and the 1e-6 offset grid can disagree
    by a few millionths at a cutter-width neck. Allow only 0.000005 inch of
    numerical fit error, converted to job units; not a machining allowance.
    Endpoints inside plus clearance from every edge proves the entire capsule,
    including the plunge disk. No sampling or area threshold hides a gouge.
    """
    from .toolpath import _entry_segment_distance
    from .units import unit_scale
    tolerance = 0.000005 * unit_scale("in", units)
    segments = []
    invalid = None
    for previous, motion in zip(block.motions, block.motions[1:]):
        if min(previous.end.z, motion.end.z) >= 0:
            continue
        a = Point(previous.end.x, previous.end.y)
        b = Point(motion.end.x, motion.end.y)
        if motion.kind == MotionKind.RAPID:
            if a.distance_to(b) > tolerance:
                invalid = b
            continue  # A vertical retract does not add to the swept footprint.
        if motion.kind != MotionKind.LINEAR:
            invalid = b  # Recovery currently supports only plunge + straight cut.
        segments.append((a, b))
    if (not isinstance(source, Polyline) or not source.closed
            or len(source.points) < 3 or block.metadata.get("side") != "inside"
            or not segments or invalid is not None):
        return segments, invalid or (segments[0][0] if segments else Point(0, 0))
    edges = list(zip(source.points, [*source.points[1:], source.points[0]]))
    for a, b in segments:
        if not _point_in_polygon(a, source.points):
            return segments, a
        if not _point_in_polygon(b, source.points):
            return segments, b
        for c, d in edges:
            gap, tool_point, contour_point = _entry_segment_distance(a, b, c, d)
            if gap < cutter_radius - tolerance:
                return segments, contour_point
    return segments, None


def _complete_entry_circle(motions):
    """Recognize a complete XY revolution; depth/repeated turns add no XY area.

    Partial arcs and noncircular paths retain the existing segment check. This
    independent verifier does not call the planner's helix collision helper.
    """
    first = next((i for i in range(1, len(motions))
                  if motions[i].kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
                  and motions[i].center is not None
                  and min(motions[i-1].end.z, motions[i].end.z) < -1e-8), None)
    if first is None:
        return None
    center = motions[first].center
    previous = Point(motions[first-1].end.x, motions[first-1].end.y)
    radius = center.distance_to(previous)
    angle = 0.0
    direction = motions[first].kind
    for motion in motions[first:]:
        end = Point(motion.end.x, motion.end.y)
        if (motion.kind != direction or motion.center is None
                or motion.center.distance_to(center) > 1e-7
                or abs(center.distance_to(end)-radius) > 1e-7):
            break
        start_angle = math.atan2(previous.y-center.y, previous.x-center.x)
        end_angle = math.atan2(end.y-center.y, end.x-center.x)
        sweep = ((end_angle-start_angle) if direction == MotionKind.ARC_CCW else
                 (start_angle-end_angle)) % (2*math.pi)
        angle += sweep if sweep > 1e-10 else 2*math.pi
        if angle >= 2*math.pi-1e-8:
            return center, radius
        previous = end
    return None


def _circle_swept_contour_hit(center, radius, entity, cutter_radius):
    tolerance = max(1e-7, cutter_radius * 1e-5)
    for start, end in _entity_contour_segments(entity):
        dx, dy = end.x-start.x, end.y-start.y
        length2 = dx*dx+dy*dy
        t = max(0., min(1., ((center.x-start.x)*dx+(center.y-start.y)*dy)/length2)) if length2 else 0.
        closest = Point(start.x+t*dx, start.y+t*dy)
        minimum = center.distance_to(closest)
        maximum = max(center.distance_to(start), center.distance_to(end))
        if minimum > radius+cutter_radius+tolerance or maximum < radius-cutter_radius-tolerance:
            continue
        # A contour segment's radial distances form one continuous interval;
        # intersection with the swept annulus is an exact XY collision test.
        point = closest
        if maximum < radius:
            point = max((start,end), key=center.distance_to)
        elif minimum < radius and length2:
            b = 2*((start.x-center.x)*dx+(start.y-center.y)*dy)
            c = (start.x-center.x)**2+(start.y-center.y)**2-radius**2
            disc = max(0., b*b-4*length2*c)
            for root in ((-b-math.sqrt(disc))/(2*length2),(-b+math.sqrt(disc))/(2*length2)):
                if 0 <= root <= 1:
                    point = Point(start.x+root*dx,start.y+root*dy)
                    break
        return point
    return None


def _linear_entry_segments(motions) -> list[tuple[Point, Point]]:
    """Return the first non-zero XY feed move after the entry rapid.

    Linear lead-in and ramp entry blocks begin with a rapid to the entry point,
    followed by a vertical positioning move and one XY move to the contour.
    Only that first XY move is entry travel; subsequent moves are the profile.
    """
    current = None
    for motion in motions:
        end = Point(motion.end.x, motion.end.y)
        if (
            current is not None
            and motion.kind != MotionKind.RAPID
            and current.distance_to(end) > 1e-10
        ):
            return [(current, end)]
        current = end
    return []


def _direct_plunge_entry_segments(motions) -> list[tuple[Point, Point]]:
    """Return only unexpected XY travel before a direct on-profile plunge.

    A valid direct fallback consists of a rapid and vertical feed at identical
    XY coordinates. Its next XY move is the profile itself, not an entry lead.
    If that proof fails, retain the conservative linear-entry check.
    """
    if len(motions) >= 2:
        first = Point(motions[0].end.x, motions[0].end.y)
        second = Point(motions[1].end.x, motions[1].end.y)
        if (
            motions[0].kind == MotionKind.RAPID
            and motions[1].kind == MotionKind.LINEAR
            and first.distance_to(second) <= 1e-10
            and motions[1].end.z < motions[0].end.z - 1e-10
        ):
            return []
    return _linear_entry_segments(motions)


def _profile_constrained_entry_excess_segments(motions) -> list[tuple[Point, Point]]:
    """Return ramp-entry segments not proved by a later full-depth recut.

    The on-contour recovery deliberately recuts its descending span at target
    depth. Matching every ramp segment against that later pass proves the
    entry introduced no new XY cutter sweep; any unmatched segment remains a
    normal interference-check candidate.
    """
    if len(motions) < 4:
        return _linear_entry_segments(motions)
    target_z = min(motion.end.z for motion in motions)
    entry_segments: list[tuple[Point, Point]] = []
    previous = Point(motions[1].end.x, motions[1].end.y)
    entry_end_index = 1
    for index, motion in enumerate(motions[2:], start=2):
        end = Point(motion.end.x, motion.end.y)
        if motion.kind != MotionKind.RAPID and previous.distance_to(end) > 1e-10:
            entry_segments.append((previous, end))
        previous = end
        entry_end_index = index
        if motion.end.z <= target_z + 1e-10:
            break
    if not entry_segments:
        return []

    recut_segments: list[tuple[Point, Point]] = []
    previous = Point(motions[entry_end_index].end.x, motions[entry_end_index].end.y)
    for motion in motions[entry_end_index + 1:]:
        end = Point(motion.end.x, motion.end.y)
        if (
            motion.kind != MotionKind.RAPID
            and previous.distance_to(end) > 1e-10
            and abs(motion.end.z - target_z) <= 1e-8
        ):
            recut_segments.append((previous, end))
        previous = end
    return [
        segment for segment in entry_segments
        if not any(_same_xy_segment(segment, candidate) for candidate in recut_segments)
    ]


def _same_xy_segment(
    first: tuple[Point, Point],
    second: tuple[Point, Point],
    tolerance: float = 1e-7,
) -> bool:
    return (
        first[0].distance_to(second[0]) <= tolerance
        and first[1].distance_to(second[1]) <= tolerance
    ) or (
        first[0].distance_to(second[1]) <= tolerance
        and first[1].distance_to(second[0]) <= tolerance
    )


def create_toolpath_template(name: str, operations: Sequence[Operation], description: str = "") -> ToolpathTemplate:
    if not name.strip() or not operations:
        raise ValueError("A toolpath template requires a name and at least one operation.")
    from .model import TabReleaseOperation
    if any(isinstance(o, TabReleaseOperation) for o in operations):
        raise ValueError("Final tab-release stages are job-specific reviewed contracts, not reusable templates.")
    selected_ids = {operation.id for operation in operations}
    payloads = []
    for operation in operations:
        payload = operation.to_dict()
        # Manual cut order names concrete geometry in one job. A reusable
        # template must recalculate safe nesting for its destination geometry.
        payload.pop("manual_contour_order", None)
        payload.pop("tab_release_plan", None)
        payload["template_operation_id"] = operation.id
        payload.pop("id", None)
        payload["depends_on_ids"] = [dependency for dependency in operation.depends_on_ids if dependency in selected_ids]
        payloads.append(payload)
    return ToolpathTemplate(name.strip(), payloads, description.strip())


def instantiate_toolpath_template(project: CamProject, template: ToolpathTemplate) -> list[Operation]:
    created: list[Operation] = []
    old_to_new: dict[str, str] = {}
    # Templates made by earlier versions may retain IDs; use them only to map
    # dependencies and always assign fresh IDs in the destination project.
    for payload in template.operations:
        values = deepcopy(payload)
        values.pop("tab_release_plan", None)
        old_id = str(values.pop("template_operation_id", values.pop("id", "")))
        operation = _operation_from_dict(values)
        operation.id = new_id("operation")
        if old_id:
            old_to_new[old_id] = operation.id
        if operation.geometry_selector:
            operation.entity_ids = list(resolve_geometry_selector(project, operation.geometry_selector).entity_ids)
        created.append(operation)
    for operation in created:
        operation.depends_on_ids = [old_to_new.get(value, value) for value in operation.depends_on_ids]
        if isinstance(operation, ProfileRestMachiningOperation):
            operation.source_operation_id = old_to_new.get(operation.source_operation_id, operation.source_operation_id)
    project.operations.extend(created)
    for operation in created:
        if isinstance(operation, ProfileRestMachiningOperation):
            source = next((item for item in project.operations if item.id == operation.source_operation_id), None)
            if isinstance(source, RouterOffsetOperation):
                operation.entity_ids = list(source.entity_ids)
                capture_profile_rest_dependency(project, operation)
    return created


def _entity_closed(entity: Entity) -> bool:
    return isinstance(entity, (Circle, Rectangle)) or isinstance(entity, Polyline) and entity.closed


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _entity_focus(entities: dict[str, Entity], ids: Iterable[str]) -> Point | None:
    boxes = [entity_bounds(entities[entity_id]) for entity_id in ids if entity_id in entities]
    if not boxes:
        return None
    return Point(sum((box[0] + box[2]) / 2 for box in boxes) / len(boxes), sum((box[1] + box[3]) / 2 for box in boxes) / len(boxes))


def _helical_entry_segments(motions) -> list[tuple[Point, Point]]:
    """Flatten the first same-center arc run in a verified helical block."""
    first_arc = next(
        (
            index for index, motion in enumerate(motions)
            if index > 0
            and motion.kind in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
            and motion.center is not None
            and min(motions[index - 1].end.z, motion.end.z) < -1e-8
        ),
        None,
    )
    if first_arc is None:
        return []
    center = motions[first_arc].center
    if center is None:
        return []
    points = [Point(motions[first_arc - 1].end.x, motions[first_arc - 1].end.y)]
    for motion in motions[first_arc:]:
        if (
            motion.kind not in {MotionKind.ARC_CW, MotionKind.ARC_CCW}
            or motion.center is None
            or motion.center.distance_to(center) > 1e-7
        ):
            break
        points.extend(_flatten_arc(points[-1], motion, maximum_step=math.pi / 36)[1:])
    return [
        (first, second) for first, second in zip(points, points[1:])
        if first.distance_to(second) > 1e-10
    ]


def _flatten_arc(start: Point, motion, maximum_step: float) -> list[Point]:
    center = motion.center
    if center is None:
        return [start, Point(motion.end.x, motion.end.y)]
    end = Point(motion.end.x, motion.end.y)
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    if motion.kind == MotionKind.ARC_CCW:
        sweep = (end_angle - start_angle) % (2 * math.pi)
        if sweep <= 1e-10:
            sweep = 2 * math.pi
    else:
        sweep = -((start_angle - end_angle) % (2 * math.pi))
        if abs(sweep) <= 1e-10:
            sweep = -2 * math.pi
    radius = start.distance_to(center)
    count = max(2, math.ceil(abs(sweep) / maximum_step))
    return [
        Point(
            center.x + radius * math.cos(start_angle + sweep * index / count),
            center.y + radius * math.sin(start_angle + sweep * index / count),
        )
        for index in range(count + 1)
    ]


def _segments_bounds(
    segments: list[tuple[Point, Point]],
    padding: float,
) -> tuple[float, float, float, float]:
    points = [point for segment in segments for point in segment]
    return (
        min(point.x for point in points) - padding,
        min(point.y for point in points) - padding,
        max(point.x for point in points) + padding,
        max(point.y for point in points) + padding,
    )


def _boxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def _entity_contour_segments(entity: Entity) -> list[tuple[Point, Point]]:
    if isinstance(entity, Circle):
        points = [
            Point(
                entity.center.x + entity.radius * math.cos(2 * math.pi * index / 96),
                entity.center.y + entity.radius * math.sin(2 * math.pi * index / 96),
            )
            for index in range(96)
        ]
        return list(zip(points, [*points[1:], points[0]]))
    if isinstance(entity, Rectangle):
        min_x = min(entity.corner_a.x, entity.corner_b.x)
        min_y = min(entity.corner_a.y, entity.corner_b.y)
        max_x = max(entity.corner_a.x, entity.corner_b.x)
        max_y = max(entity.corner_a.y, entity.corner_b.y)
        points = [
            Point(min_x, min_y), Point(max_x, min_y),
            Point(max_x, max_y), Point(min_x, max_y),
        ]
        return list(zip(points, [*points[1:], points[0]]))
    if isinstance(entity, Line):
        return [(entity.start, entity.end)]
    if isinstance(entity, Polyline):
        if len(entity.points) < 2:
            return []
        pairs = list(zip(entity.points, entity.points[1:]))
        if entity.closed:
            pairs.append((entity.points[-1], entity.points[0]))
        return pairs
    return []


def _first_swept_contour_hit(
    tool_segments: list[tuple[Point, Point]],
    entity: Entity,
    cutter_radius: float,
) -> Point | None:
    tolerance = max(1e-7, cutter_radius * 1e-5)
    contour_segments = _entity_contour_segments(entity)
    for tool_start, tool_end in tool_segments:
        for contour_start, contour_end in contour_segments:
            distance, tool_point, contour_point = _segment_distance(
                tool_start, tool_end, contour_start, contour_end,
            )
            if distance <= cutter_radius + tolerance:
                return Point(
                    (tool_point.x + contour_point.x) / 2,
                    (tool_point.y + contour_point.y) / 2,
                )
    return None


def _segment_distance(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> tuple[float, Point, Point]:
    """Return the closest distance and points between two 2D segments."""
    ux, uy = first_end.x - first_start.x, first_end.y - first_start.y
    vx, vy = second_end.x - second_start.x, second_end.y - second_start.y
    wx, wy = first_start.x - second_start.x, first_start.y - second_start.y
    a = ux * ux + uy * uy
    b = ux * vx + uy * vy
    c = vx * vx + vy * vy
    d = ux * wx + uy * wy
    e = vx * wx + vy * wy
    denominator = a * c - b * b
    if a <= 1e-20 and c <= 1e-20:
        return first_start.distance_to(second_start), first_start, second_start
    if a <= 1e-20:
        first_fraction = 0.0
        second_fraction = max(0.0, min(1.0, e / c))
    elif c <= 1e-20:
        second_fraction = 0.0
        first_fraction = max(0.0, min(1.0, -d / a))
    else:
        first_fraction = max(0.0, min(1.0, (b * e - c * d) / denominator)) if abs(denominator) > 1e-20 else 0.0
        second_fraction = (b * first_fraction + e) / c
        if second_fraction < 0.0:
            second_fraction = 0.0
            first_fraction = max(0.0, min(1.0, -d / a))
        elif second_fraction > 1.0:
            second_fraction = 1.0
            first_fraction = max(0.0, min(1.0, (b - d) / a))
    first_point = Point(first_start.x + ux * first_fraction, first_start.y + uy * first_fraction)
    second_point = Point(second_start.x + vx * second_fraction, second_start.y + vy * second_fraction)
    return first_point.distance_to(second_point), first_point, second_point


def _first_keepout_hit(blocks, zone: KeepoutZone) -> Point | None:
    for block in blocks:
        previous = None
        for motion in block.motions:
            point = Point(motion.end.x, motion.end.y)
            if motion.end.z < zone.clearance_z and _point_in_polygon(point, zone.points):
                return point
            if previous is not None and min(previous[1], motion.end.z) < zone.clearance_z and _segment_intersects_polygon(previous[0], point, zone.points):
                return Point((previous[0].x + point.x) / 2, (previous[0].y + point.y) / 2)
            previous = (point, motion.end.z)
    return None


def _point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    inside = False
    for first, second in zip(polygon, (*polygon[1:], polygon[0])):
        if (first.y > point.y) != (second.y > point.y):
            crossing = first.x + (point.y - first.y) * (second.x - first.x) / (second.y - first.y)
            if crossing >= point.x:
                inside = not inside
    return inside


def _segment_intersects_polygon(start: Point, end: Point, polygon: Sequence[Point]) -> bool:
    if _point_in_polygon(start, polygon) or _point_in_polygon(end, polygon):
        return True
    return any(_segments_cross(start, end, first, second) for first, second in zip(polygon, (*polygon[1:], polygon[0])))


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
    return orient(a, b, c) * orient(a, b, d) <= 0 and orient(c, d, a) * orient(c, d, b) <= 0
