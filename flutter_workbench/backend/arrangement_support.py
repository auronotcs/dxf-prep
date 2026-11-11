"""Isolated arrangement ownership/registration adapted from Tkinter.

Source build src-20261026-fb3f1a2ac353; GUI SHA256: 21f200b2d0a94e200c06ae7a480171d59d992af017279fb9aac696ff83e95d6b.
No GUI imports or production writes. Geometry remains in the captured core.
"""
import copy
import math
from cam_core.model import Point, Polyline
from cam_core.layout import entity_bounds, drawing_bounds
from cam_core.transform import transform_entity
from cam_core.tab_slot_mounting import (
    TabSlotPlacement, TabSlotRailSettings, generate_tab_slot_part, trim_tab_slot_letter_base,
)
from cam_core.led_layout import layout_from_plan, transform_part


class ArrangementSupport:
    def __init__(self, project):
        self.project = project

    def _sheet_unit_scale(self):
        return 25.4 if self.project.units == 'mm' else 1.0

    @staticmethod
    def registered_source(entity):
        return bool(entity.metadata.get('reference_only') and (
            entity.metadata.get('bent_tab_source_reference') or
            entity.metadata.get('tab_slot_source_reference')) and
            entity.metadata.get('mounting_owner_entity_id'))

    def groups(self):
        """Transitive physical assemblies; hidden source outlines follow owners separately."""
        entities = [e for e in self.project.entities if not self.registered_source(e)]
        parent = {e.id: e.id for e in entities}
        def root(identity):
            while parent[identity] != identity:
                parent[identity] = parent[parent[identity]]
                identity = parent[identity]
            return identity
        owners = {}
        for e in entities:
            for key in ('group_id', 'mounting_part_id', 'compound_id'):
                value = str(e.metadata.get(key) or '').strip()
                if value:
                    earlier = owners.setdefault((key, value), e.id)
                    parent[root(e.id)] = root(earlier)
        components = {}
        for e in entities:
            components.setdefault(root(e.id), []).append(e)
        # Preserve existing ordinary group reference IDs for current clients.
        return {(group[0].metadata.get('group_id') if group[0].metadata.get('group_id')
                 and all(e.metadata.get('group_id') == group[0].metadata['group_id'] for e in group)
                 else group[0].id): group for group in components.values()}

    def describe(self, key, group, *, body=False):
        visible = [e for e in group if not e.metadata.get('reference_only')] or group
        result = dict(id=key, entityIds=[e.id for e in group],
            label=f"{visible[0].metadata.get('name') or (str(len(visible)) + ' associated objects' if len(visible)>1 else visible[0].kind)} · {visible[0].layer} · {key[-8:]}",
            bounds=drawing_bounds(visible, include_annotations=True), bodyBounds=None)
        # The Tkinter body option refers to the original production outline of
        # an applied tab/slot letter, registered to its current rigid placement.
        displayed = next((e for e in visible if isinstance(e, Polyline)
                          and e.metadata.get('tab_slot_letter_outline')), None)
        if body and displayed is not None:
            source = next((e for e in self.project.entities if isinstance(e, Polyline)
                and e.metadata.get('tab_slot_source_reference')
                and e.metadata.get('mounting_owner_entity_id') == displayed.id), None)
            if source is not None:
                result['bodyBounds'] = entity_bounds(self._registered_tab_slot_source(displayed, source))
        return result

    def translate(self, moves):
        """Move physical members and their registered data once; rails stay independent."""
        movement = {}
        for move in moves:
            if move.stationary:
                continue
            owners = set(move.entity_ids)
            for identity in self._registered_transform_ids(owners):
                delta = (move.delta_x, move.delta_y)
                if identity in movement and movement[identity] != delta:
                    raise ValueError('An associated object belongs to conflicting arrangement items.')
                movement[identity] = delta
            self._transform_tab_slot_plan_owners(owners, delta_x=move.delta_x, delta_y=move.delta_y)
            factor = 1 / self._sheet_unit_scale()
            for plan in self.project.metadata.get('led_plans', []):
                if not isinstance(plan, dict):
                    continue
                layout = layout_from_plan(plan)
                if layout is not None:
                    for owner in owners:
                        layout = transform_part(layout, owner, dx_in=move.delta_x*factor, dy_in=move.delta_y*factor)
                    plan['layout'] = layout.to_dict()
        self.project.entities = [transform_entity(e, *movement[e.id]) if e.id in movement else e
                                 for e in self.project.entities]

    def _registered_tab_slot_source(self, displayed: Polyline, source_reference: Polyline) -> Polyline:
        """Recover a nested letter's source frame without moving its assembly rail slots."""
        plan = next((
            value for value in self.project.metadata.get("tab_slot_rail_plans", ())
            if isinstance(value, dict) and str(value.get("owner_entity_id", "")) == displayed.id
        ), None)
        source = copy.deepcopy(source_reference)
        untrimmed_source = copy.deepcopy(source_reference)
        if plan is None:
            return source

        def with_untrimmed_hint(value: Polyline, original: Polyline) -> Polyline:
            value.metadata["_tab_slot_untrimmed_points"] = [
                [point.x, point.y] for point in original.points
            ]
            return value

        try:
            base_trim = plan.get("base_trim")
            if (
                    not source_reference.metadata.get("tab_slot_source_is_production_body")
                    and isinstance(base_trim, dict) and displayed.id in {
                    str(value) for value in base_trim.get("trim_owner_ids", ())}
            ):
                source = trim_tab_slot_letter_base(
                    source, float(base_trim["baseline_y"]),
                    maximum_trim=float(base_trim["maximum_trim"]),
                    tolerance=1e-7 * self._sheet_unit_scale(),
                )
            placements = tuple(TabSlotPlacement.from_dict(value) for value in plan.get("placements", ()))
            settings = TabSlotRailSettings.from_dict(plan["settings"])
            # Applied source references receive a new persistence ID; saved
            # placements retain the original source ID. Rebind only this
            # temporary regeneration input, preserving the stored entity ID.
            regeneration_source = copy.deepcopy(source)
            if placements:
                regeneration_source.id = placements[0].owner_entity_id
            regenerated = generate_tab_slot_part(regeneration_source, placements, settings).bent_result.modified_cut_outline
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Cannot register the original body. Turn off original body bounds or repair its tab/slot plan.') from exc
        original_points = regenerated.points
        current_points = displayed.points
        if len(original_points) != len(current_points) or len(original_points) < 2:
            raise ValueError('The tabbed outline changed. Turn off original body bounds or regenerate its tabs.')
        source_x = sum(point.x for point in original_points) / len(original_points)
        source_y = sum(point.y for point in original_points) / len(original_points)
        target_x = sum(point.x for point in current_points) / len(current_points)
        target_y = sum(point.y for point in current_points) / len(current_points)
        dot = cross = 0.0
        for original, current in zip(original_points, current_points):
            ox, oy = original.x - source_x, original.y - source_y
            tx, ty = current.x - target_x, current.y - target_y
            dot += ox * tx + oy * ty
            cross += ox * ty - oy * tx
        if math.hypot(dot, cross) <= settings.tolerance:
            raise ValueError('Cannot resolve the original body placement. Turn off original body bounds.')
        angle = math.atan2(cross, dot)
        cosine, sine = math.cos(angle), math.sin(angle)
        delta_x = target_x - (source_x * cosine - source_y * sine)
        delta_y = target_y - (source_x * sine + source_y * cosine)
        maximum_error = max(
            math.hypot(
                original.x * cosine - original.y * sine + delta_x - current.x,
                original.x * sine + original.y * cosine + delta_y - current.y,
            )
            for original, current in zip(original_points, current_points)
        )
        bounds = entity_bounds(displayed)
        diagonal = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) if bounds is not None else 1.0
        if maximum_error > max(settings.tolerance * 100.0, diagonal * 1e-6):
            raise ValueError('The tabbed outline no longer matches its source. Turn off original body bounds or regenerate its tabs.')
        registered = transform_entity(
            source, delta_x=delta_x, delta_y=delta_y,
            angle_degrees=math.degrees(angle), pivot=Point(0.0, 0.0),
        )
        assert isinstance(registered, Polyline)
        registered_original = transform_entity(
            untrimmed_source, delta_x=delta_x, delta_y=delta_y,
            angle_degrees=math.degrees(angle), pivot=Point(0.0, 0.0),
        )
        assert isinstance(registered_original, Polyline)
        registered.metadata["tab_slot_registration_recovered"] = True
        return with_untrimmed_hint(registered, registered_original)

    def _registered_transform_ids(self, entity_ids: set[str]) -> set[str]:
        """Include hidden associative geometry when its visible owner moves.

        Locked reference layers remain protected from direct editing.  A
        source reference linked to an unlocked selected owner is different: it
        must receive the same rigid/scale transform or later regeneration will
        compare current holes to an obsolete outline. Applied rail slots are
        physical members of their rail piece, not hidden letter dependencies;
        their stable pair IDs remain available for editing but never pull them
        into a letter transform.
        """
        result = set(entity_ids)
        owner_ids = {
            entity.id for entity in self.project.entities
            if entity.id in entity_ids and not entity.metadata.get("reference_only")
        }
        result.update(
            entity.id for entity in self.project.entities
            if (
                entity.metadata.get("reference_only")
                and (
                    entity.metadata.get("bent_tab_source_reference")
                    or entity.metadata.get("tab_slot_source_reference")
                )
                and str(entity.metadata.get("mounting_owner_entity_id", "")) in owner_ids
            )
        )
        return result

    def _transform_tab_slot_plan_owners(
            self, owner_ids: set[str], *, delta_x: float = 0.0, delta_y: float = 0.0,
            angle: float = 0.0, pivot: Point | None = None) -> None:
        """Update only the physical side of a moved applied tab/slot pair.

        A letter move updates its tab/assembly center and base-trim datum. A
        rail move updates only slot centers whose generated rail geometry is
        selected. The pair identity remains stable without making the two
        separately manufacturable parts transform as one object.
        """
        if not owner_ids:
            return
        selected_entities = [
            entity for entity in self.project.entities if entity.id in owner_ids
        ]
        selected_letter_ids = {
            entity.id for entity in selected_entities
            if not entity.metadata.get("reference_only")
        }
        selected_rail_pair_ids = {
            str(entity.metadata.get("tab_slot_pair_id", ""))
            for entity in selected_entities
            if entity.metadata.get("tab_slot_role") in {
                "rail_through_slot", "nominal_slot_reference",
            }
            and str(entity.metadata.get("tab_slot_pair_id", ""))
        }
        rail_origin_by_pair = {}
        for entity in sorted(selected_entities, key=lambda item: item.metadata.get("tab_slot_role") != "rail_through_slot"):
            pair_id = str(entity.metadata.get("tab_slot_pair_id", ""))
            if pair_id in selected_rail_pair_ids:
                rail_origin_by_pair.setdefault(pair_id, float(entity.metadata.get("rail_origin_y", 0.0)))
        radians = math.radians(angle)
        cosine, sine = math.cos(radians), math.sin(radians)
        center = pivot or Point(0.0, 0.0)

        def moved(values) -> list[float]:
            point = Point(float(values[0]), float(values[1]))
            offset_x, offset_y = point.x - center.x, point.y - center.y
            return [
                center.x + offset_x * cosine - offset_y * sine + delta_x,
                center.y + offset_x * sine + offset_y * cosine + delta_y,
            ]

        for plan in self.project.metadata.get("tab_slot_rail_plans", ()):
            if not isinstance(plan, dict):
                continue
            letter_selected = str(plan.get("owner_entity_id", "")) in selected_letter_ids
            for placement in plan.get("placements", ()):
                if not isinstance(placement, dict):
                    continue
                if letter_selected:
                    values = placement.get("assembly_center")
                    if isinstance(values, (list, tuple)) and len(values) == 2:
                        placement["assembly_center"] = moved(values)
                if str(placement.get("pair_id", "")) in selected_rail_pair_ids:
                    values = placement.get("rail_slot_center")
                    if isinstance(values, (list, tuple)) and len(values) == 2:
                        # Saved slots use the rail's pre-display Y frame. Turn
                        # that point into drawing coordinates before rotation,
                        # then return to its unchanged stored origin frame.
                        origin_y = rail_origin_by_pair[str(placement["pair_id"])]
                        physical = moved((float(values[0]), float(values[1]) + origin_y))
                        placement["rail_slot_center"] = [physical[0], physical[1] - origin_y]
            base_trim = plan.get("base_trim")
            if (
                    letter_selected and isinstance(base_trim, dict)
                    and math.isclose(angle, 0.0, abs_tol=1e-12)
                    and "baseline_y" in base_trim
            ):
                base_trim["baseline_y"] = float(base_trim["baseline_y"]) + delta_y
