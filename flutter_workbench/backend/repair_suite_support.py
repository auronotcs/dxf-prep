"""Revision-bound, nonmutating repair-suite review for source drawings."""
import copy
import math
from cam_core.repair_suite import plan_repair_suite, SuiteFinding
from cam_core.contour_repair import apply_contour_repair
from cam_core.layout import entity_bounds
from cam_core.model import new_id


class RepairSuiteDraft:
    def __init__(self): self.cached = None

    def preview(self, project, params, revision, digest, selection):
        self.cached = None
        if set(params) - {'scope', 'include_fragments'} or params.get('scope') not in {'selected', 'all'}:
            raise ValueError('Choose selected objects or the whole drawing.')
        if type(params.get('include_fragments', False)) is not bool:
            raise ValueError('Choose whether to include reviewed fragments.')
        if project.operations:
            raise ValueError('Repair source drawings before creating machining operations.')
        scope = set(selection) if params['scope'] == 'selected' else {e.id for e in project.entities}
        if not scope: raise ValueError('Select or draw objects to inspect.')
        private = copy.deepcopy(project)
        # Hidden geometry remains ambiguity context, but cannot be changed silently.
        hidden = {e.layer for e in project.entities if project.metadata.get('layer_settings', {}).get(e.layer, {}).get('visible') is False}
        excluded = {e.id for e in project.entities if e.id in scope and
                    (e.metadata.get('annotation_hidden') or e.metadata.get('tab_slot_generated'))}
        plan = plan_repair_suite(private, scope-excluded, locked_layers=hidden, include_fragments=params.get('include_fragments', False))
        plan.findings += [SuiteFinding((i,), 'Hidden/generated geometry; unchanged.') for i in sorted(excluded)]
        def serial(e): return dict(e.to_dict(), bounds=entity_bounds(e))
        rows = []
        for p in plan.proposal:
            markers = p.joins + p.backtrack_locations + p.near_point_locations
            rows.append(dict(ids=list(p.source_ids), kind='repair', message=f'{len(p.joins)} joins; {p.removed_points} redundant points; {p.absorbed_fragments} seam fragments. Maximum change {p.maximum_error:.6g} {project.units}.',
                outputs=[serial(p.entity)], point=vars(markers[0]) if markers else None))
        for ids, tolerance, needs in plan.tolerances:
            if needs:
                e = next((e for e in private.entities if e.id in ids and len(getattr(e, 'points', [])) > 2), None)
                rows.append(dict(ids=list(ids), kind='review', message=f'Endpoint tolerance {tolerance:.6g} {project.units}; ' + ('reviewed fragments included.' if params.get('include_fragments') else 'requires explicit fragment approval.'),
                    outputs=[], point=vars(e.points[0]) if e else None))
        for finding in plan.findings:
            point=getattr(finding,'point',None)
            marker=vars(point) if point is not None and all(math.isfinite(v) for v in (point.x,point.y)) else None
            rows.append(dict(ids=list(finding.ids),kind='finding',message=finding.message,outputs=[],point=marker,
                code=getattr(finding,'code',''),severity=getattr(finding,'severity','warning')))
        token = None
        if plan.proposal:
            candidate, disabled, removed = apply_contour_repair(private, plan.proposal)
            if disabled: raise ValueError('Machining reassociation must be performed in its owning workflow.')
            proposed = {i for p in plan.proposal for i in p.source_ids}
            originals = {e.id:e.to_dict() for e in project.entities}
            propagated = {e.id for e in candidate.entities if e.id not in proposed and
                          e.id in originals and e.to_dict()!=originals[e.id]}
            if removed or propagated:
                raise ValueError('This repair changes generated dependent geometry. Review it in the owning Tkinter workflow before repairing the source.')
            token = new_id('repair-suite')
            self.cached = dict(token=token, revision=revision, digest=digest, project=candidate,
                selected=(set(selection)-{i for p in plan.proposal for i in p.source_ids}-set(removed)) | {p.entity.id for p in plan.proposal})
        return dict(token=token, revision=revision, rows=rows, count=len(plan.proposal),
            sources=[serial(e) for e in private.entities if e.id in scope],
            outputs=[serial(p.entity) for p in plan.proposal], units=project.units)

    def apply(self, session, params, revision, digest):
        c = self.cached
        if set(params) != {'token'} or not c or params.get('token') != c['token'] or revision != c['revision'] or digest != c['digest']:
            raise ValueError('Drawing or repair options changed. Preview the batch again.')
        session.project = copy.deepcopy(c['project'])
        session.selected_ids = set(c['selected'])
        session._record_history()
        self.cached = None
