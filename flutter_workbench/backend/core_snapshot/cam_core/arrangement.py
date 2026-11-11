from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


Bounds = tuple[float, float, float, float]

ALIGN_ACTIONS = frozenset({
    "align_left",
    "align_horizontal_center",
    "align_right",
    "align_bottom",
    "align_vertical_center",
    "align_top",
    "align_both_centers",
})
SPACING_ACTIONS = frozenset({
    "space_horizontal_equal",
    "space_vertical_equal",
    "space_horizontal_gap",
    "space_vertical_gap",
})
ARRANGEMENT_ACTIONS = ALIGN_ACTIONS | SPACING_ACTIONS
REFERENCE_MODES = frozenset({"selection", "object", "plate", "patchwork"})
TOLERANCE = 1e-9


@dataclass(frozen=True)
class ArrangementItem:
    """One independently movable selection item.

    ``entity_ids`` lets the UI represent a normal entity, a user group, or an
    associated sign/mounting assembly without making the arrangement engine
    depend on document metadata conventions.
    """

    item_id: str
    entity_ids: tuple[str, ...]
    bounds: Bounds
    label: str = ""
    locked: bool = False
    # Optional registration bounds for an original part body.  The UI may use
    # these in place of generated-tab extents without changing move ownership.
    body_bounds: Bounds | None = None
    # Compact document identity used inside the preview.  ``label`` remains the
    # full combobox description.
    short_label: str = ""

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]


@dataclass(frozen=True)
class ArrangementMove:
    item_id: str
    entity_ids: tuple[str, ...]
    delta_x: float
    delta_y: float
    stationary: bool = False


@dataclass(frozen=True)
class ArrangementPlan:
    action: str
    reference_mode: str
    reference_bounds: Bounds
    reference_item_id: str | None
    gap: float | None
    moves: tuple[ArrangementMove, ...]

    @property
    def changed_entity_ids(self) -> tuple[str, ...]:
        return tuple(
            entity_id
            for move in self.moves if not move.stationary
            for entity_id in move.entity_ids
        )


def combined_bounds(items: Iterable[ArrangementItem]) -> Bounds:
    values = list(items)
    if not values:
        raise ValueError("Arrangement requires at least one selection item.")
    return (
        min(item.bounds[0] for item in values),
        min(item.bounds[1] for item in values),
        max(item.bounds[2] for item in values),
        max(item.bounds[3] for item in values),
    )


def moved_bounds(bounds: Bounds, delta_x: float, delta_y: float) -> Bounds:
    return (
        bounds[0] + delta_x,
        bounds[1] + delta_y,
        bounds[2] + delta_x,
        bounds[3] + delta_y,
    )


def plan_arrangement(
    items: Iterable[ArrangementItem],
    action: str,
    *,
    reference_mode: str = "selection",
    reference_item_id: str | None = None,
    plate_bounds: Bounds | None = None,
    patchwork_bounds: Bounds | None = None,
    gap: float | None = None,
) -> ArrangementPlan:
    """Plan alignment or spacing without mutating document geometry."""
    values = list(items)
    if action not in ARRANGEMENT_ACTIONS:
        raise ValueError(f"Unsupported arrangement action: {action!r}.")
    if reference_mode not in REFERENCE_MODES:
        raise ValueError(f"Unsupported arrangement reference: {reference_mode!r}.")
    if not values:
        raise ValueError("Select at least one object to arrange.")
    item_ids = [item.item_id for item in values]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Arrangement item IDs must be unique.")
    entity_ids = [entity_id for item in values for entity_id in item.entity_ids]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("An entity cannot belong to more than one arrangement item.")
    if any(
        len(item.bounds) != 4
        or not all(math.isfinite(float(value)) for value in item.bounds)
        or item.width < -TOLERANCE
        or item.height < -TOLERANCE
        for item in values
    ):
        raise ValueError("Arrangement items require finite, ordered bounds.")

    selection_bounds = combined_bounds(values)
    reference_item = None
    if reference_mode == "object":
        reference_item = next(
            (item for item in values if item.item_id == reference_item_id), None,
        )
        if reference_item is None:
            raise ValueError("Choose one selected object as the fixed reference.")
        reference_bounds = reference_item.bounds
    elif reference_mode in {"plate", "patchwork"}:
        target_bounds = patchwork_bounds if reference_mode == "patchwork" else plate_bounds
        target_name = "patchwork plate" if reference_mode == "patchwork" else "plate"
        if target_bounds is None or len(target_bounds) != 4:
            raise ValueError(f"Define and select a {target_name} before arranging to it.")
        if not all(math.isfinite(float(value)) for value in target_bounds):
            raise ValueError(f"{target_name.title()} bounds must be finite.")
        if target_bounds[2] < target_bounds[0] or target_bounds[3] < target_bounds[1]:
            raise ValueError(f"{target_name.title()} bounds must be ordered.")
        reference_bounds = target_bounds
    else:
        reference_bounds = selection_bounds

    if action in ALIGN_ACTIONS:
        moves = _alignment_moves(values, action, reference_bounds, reference_item)
        normalized_gap = None
    else:
        if len(values) < 2:
            raise ValueError("Select at least two objects to space.")
        spacing_bounds = selection_bounds if reference_mode == "object" else reference_bounds
        normalized_gap = _spacing_gap(values, action, spacing_bounds, gap)
        moves = _spacing_moves(
            values, action, reference_mode, reference_bounds,
            reference_item, normalized_gap,
        )

    _validate_locked_moves(values, moves, reference_item)
    return ArrangementPlan(
        action=action,
        reference_mode=reference_mode,
        reference_bounds=reference_bounds,
        reference_item_id=reference_item.item_id if reference_item else None,
        gap=normalized_gap,
        moves=tuple(moves),
    )


def _alignment_moves(
    items: list[ArrangementItem],
    action: str,
    target: Bounds,
    reference_item: ArrangementItem | None,
) -> list[ArrangementMove]:
    target_x = {
        "align_left": target[0],
        "align_horizontal_center": (target[0] + target[2]) / 2.0,
        "align_right": target[2],
        "align_both_centers": (target[0] + target[2]) / 2.0,
    }.get(action)
    target_y = {
        "align_bottom": target[1],
        "align_vertical_center": (target[1] + target[3]) / 2.0,
        "align_top": target[3],
        "align_both_centers": (target[1] + target[3]) / 2.0,
    }.get(action)
    moves = []
    for item in items:
        if reference_item is not None and item.item_id == reference_item.item_id:
            delta_x = delta_y = 0.0
        else:
            delta_x = 0.0
            delta_y = 0.0
            if target_x is not None:
                source_x = (
                    item.bounds[0] if action == "align_left" else
                    item.bounds[2] if action == "align_right" else
                    (item.bounds[0] + item.bounds[2]) / 2.0
                )
                delta_x = target_x - source_x
            if target_y is not None:
                source_y = (
                    item.bounds[1] if action == "align_bottom" else
                    item.bounds[3] if action == "align_top" else
                    (item.bounds[1] + item.bounds[3]) / 2.0
                )
                delta_y = target_y - source_y
        moves.append(_move(item, delta_x, delta_y))
    return moves


def _spacing_gap(
    items: list[ArrangementItem],
    action: str,
    reference_bounds: Bounds,
    requested_gap: float | None,
) -> float:
    horizontal = action.startswith("space_horizontal")
    exact = action.endswith("_gap")
    if exact:
        if requested_gap is None or not math.isfinite(float(requested_gap)):
            raise ValueError("Enter a finite exact gap.")
        if requested_gap < 0:
            raise ValueError("Exact gap cannot be negative.")
        return float(requested_gap)
    if len(items) < 3:
        raise ValueError("Equal spacing requires at least three objects.")
    extent = (
        reference_bounds[2] - reference_bounds[0]
        if horizontal else reference_bounds[3] - reference_bounds[1]
    )
    occupied = sum(item.width if horizontal else item.height for item in items)
    calculated = (extent - occupied) / (len(items) - 1)
    if calculated < -TOLERANCE:
        raise ValueError(
            "The selected objects overlap along this axis; use an exact gap to spread them."
        )
    return max(0.0, calculated)


def _spacing_moves(
    items: list[ArrangementItem],
    action: str,
    reference_mode: str,
    reference_bounds: Bounds,
    reference_item: ArrangementItem | None,
    gap: float,
) -> list[ArrangementMove]:
    horizontal = action.startswith("space_horizontal")
    start_index = 0 if horizontal else 1
    end_index = 2 if horizontal else 3
    ordered = sorted(
        items,
        key=lambda item: (
            (item.bounds[start_index] + item.bounds[end_index]) / 2.0,
            item.item_id,
        ),
    )
    start = reference_bounds[start_index]
    target_starts: dict[str, float] = {}
    cursor = start
    for item in ordered:
        target_starts[item.item_id] = cursor
        cursor += (item.width if horizontal else item.height) + gap

    if reference_item is not None:
        correction = (
            reference_item.bounds[start_index]
            - target_starts[reference_item.item_id]
        )
        target_starts = {
            item_id: value + correction for item_id, value in target_starts.items()
        }

    moves = []
    for item in items:
        delta = target_starts[item.item_id] - item.bounds[start_index]
        moves.append(_move(item, delta if horizontal else 0.0, 0.0 if horizontal else delta))
    return moves


def _move(item: ArrangementItem, delta_x: float, delta_y: float) -> ArrangementMove:
    stationary = abs(delta_x) <= TOLERANCE and abs(delta_y) <= TOLERANCE
    return ArrangementMove(
        item_id=item.item_id,
        entity_ids=item.entity_ids,
        delta_x=0.0 if abs(delta_x) <= TOLERANCE else delta_x,
        delta_y=0.0 if abs(delta_y) <= TOLERANCE else delta_y,
        stationary=stationary,
    )


def _validate_locked_moves(
    items: list[ArrangementItem],
    moves: list[ArrangementMove],
    reference_item: ArrangementItem | None,
) -> None:
    item_by_id = {item.item_id: item for item in items}
    for move in moves:
        item = item_by_id[move.item_id]
        if item.locked and not move.stationary:
            qualifier = (
                " It may be used as the fixed reference."
                if reference_item is None or reference_item.item_id != item.item_id else ""
            )
            raise ValueError(f"{item.label or item.item_id} is on a locked layer and cannot move.{qualifier}")
