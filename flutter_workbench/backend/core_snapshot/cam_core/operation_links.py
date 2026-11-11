"""Versioned dependency snapshots for source-linked machining operations.

The snapshot is deliberately independent of generated motion. A dependent
operation records the source inputs that defined its remaining-stock problem;
later validation recomputes those inputs and refuses to post a stale child.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .model import CamProject, ProfileRestMachiningOperation, RouterOffsetOperation


PROFILE_REST_SNAPSHOT_VERSION = 2
# Bump this when the interpretation of parent stock changes even if the saved
# operation fields do not. Existing children will then require regeneration.
PROFILE_REST_STOCK_MODEL_REVISION = 1


@dataclass(frozen=True)
class ProfileRestDependencyStatus:
    state: str
    message: str
    changed_components: tuple[str, ...] = ()
    current_snapshot: dict[str, Any] | None = None

    @property
    def current(self) -> bool:
        return self.state == "current"


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _geometry_payload(project: CamProject, source: RouterOffsetOperation) -> list[dict[str, Any]]:
    def coordinates(value):
        # JSON reload normalizes Point coordinates to float. Hash equivalent
        # integer/float geometry identically so a save/reopen is not an edit.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, dict):return {k:coordinates(v) for k,v in value.items()}
        if isinstance(value, list):return [coordinates(v) for v in value]
        return value
    by_id = {entity.id: entity for entity in project.entities}
    payload = []
    for entity_id in sorted(source.entity_ids):
        entity = by_id.get(entity_id)
        if entity is None:
            payload.append({"id": entity_id, "missing": True})
            continue
        values = entity.to_dict()
        # Selection/group/display metadata does not alter the machined contour.
        values.pop("metadata", None)
        payload.append(coordinates(_plain(values)))
    return payload


def _strategy_payload(source: RouterOffsetOperation) -> dict[str, Any]:
    fields = (
        "entity_ids", "entity_sides", "side", "contour_order", "manual_contour_order", "target_depth", "allowance",
        "stepdown", "pass_depths", "finishing_pass", "finish_axial_stock",
        "finish_feed_override", "finish_spindle_rpm_override", "onion_skin",
        "tab_count", "tab_width", "tab_height", "tab_mode",
        "corner_tab_offset", "tab_spacing", "female_tab_min_perimeter",
        "tab_cleanup_min_perimeter", "remove_tabs_after",
        "tab_cleanup_order", "manual_tab_positions", "cut_intent",
        "breakthrough",
    )
    payload = {field: _plain(getattr(source, field)) for field in fields}
    if source.tab_release_plan:
        payload["tab_release_plan"] = _plain(source.tab_release_plan)
    if source.profile_retention:
        payload["profile_retention"] = _plain(source.profile_retention)
    return payload


def profile_rest_source_snapshot(
    project: CamProject,
    source: RouterOffsetOperation,
) -> dict[str, Any]:
    if type(source) is not RouterOffsetOperation:
        raise ValueError("Corner rest needs an ordinary profile source, not a final tab-release stage.")
    tool = project.tool_library.by_station(source.tool_station)
    geometry = _geometry_payload(project, source)
    strategy = _strategy_payload(source)
    tool_payload = {
        "station": source.tool_station,
        "missing": tool is None,
        "name": "" if tool is None else tool.name,
        "kind": "" if tool is None else _plain(tool.kind),
        "diameter": None if tool is None else tool.diameter,
        "cutting_edge_length": None if tool is None else tool.cutting_edge_length,
    }
    components = {
        "geometry": _digest(geometry),
        "strategy": _digest(strategy),
        "tool": _digest(tool_payload),
        "stock_model": _digest({"revision": PROFILE_REST_STOCK_MODEL_REVISION}),
    }
    return {
        "version": PROFILE_REST_SNAPSHOT_VERSION,
        "source_operation_id": source.id,
        "source_name": source.name,
        "components": components,
        "fingerprint": _digest(components),
    }


def capture_profile_rest_dependency(
    project: CamProject,
    operation: ProfileRestMachiningOperation,
) -> dict[str, Any]:
    source = next(
        (item for item in project.operations if item.id == operation.source_operation_id),
        None,
    )
    if type(source) is not RouterOffsetOperation:
        raise ValueError("Profile rest requires an existing router-offset source before its dependency can be captured.")
    snapshot = profile_rest_source_snapshot(project, source)
    operation.source_snapshot = snapshot
    if source.id not in operation.depends_on_ids:
        operation.depends_on_ids.append(source.id)
    return snapshot


def profile_rest_dependency_status(
    project: CamProject,
    operation: ProfileRestMachiningOperation,
) -> ProfileRestDependencyStatus:
    source = next(
        (item for item in project.operations if item.id == operation.source_operation_id),
        None,
    )
    if type(source) is not RouterOffsetOperation:
        return ProfileRestDependencyStatus(
            "missing",
            f"{operation.name} has no valid parent profile. Choose a source profile and regenerate corner rest.",
        )
    current = profile_rest_source_snapshot(project, source)
    saved = operation.source_snapshot or {}
    if not saved:
        return ProfileRestDependencyStatus(
            "uncaptured",
            f"{operation.name} was created before verified parent links were recorded. Open it and regenerate corner rest before output.",
            ("dependency snapshot",), current,
        )
    if saved.get("version") != PROFILE_REST_SNAPSHOT_VERSION:
        return ProfileRestDependencyStatus(
            "stale",
            f"{operation.name} uses an older parent-link format. Regenerate corner rest before output.",
            ("snapshot format",), current,
        )
    if saved.get("source_operation_id") != source.id:
        return ProfileRestDependencyStatus(
            "stale",
            f"{operation.name} is linked to a different parent profile than the one currently selected. Regenerate corner rest.",
            ("parent operation",), current,
        )
    labels = {
        "geometry": "parent geometry",
        "strategy": "profile depth, cut side, allowance, pass, or tab strategy",
        "tool": "parent tool assignment or cutter geometry",
        "stock_model": "remaining-stock calculation revision",
    }
    saved_components = saved.get("components", {})
    current_components = current["components"]
    changed = tuple(labels[key] for key in labels if saved_components.get(key) != current_components[key])
    if changed:
        detail = "; ".join(changed)
        return ProfileRestDependencyStatus(
            "stale",
            f"{operation.name} is stale because its {detail} changed. Regenerate corner rest; stale motion will not be output.",
            changed, current,
        )
    return ProfileRestDependencyStatus(
        "current",
        f"Verified link to {source.name}: parent geometry, profile strategy, source cutter, and stock-model revision match.",
        current_snapshot=current,
    )
