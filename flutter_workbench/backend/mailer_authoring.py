"""Private physical mailer layout; the pinned engine owns cut/score geometry."""
import copy
from dataclasses import asdict
from uuid import uuid4
from cam_core.automation import _project_plates, _automation_layer_settings
from cam_core.box_generator import MailerOptions, default_options, plan_mailer, plan_warnings, mailer_entities, CUT_LAYER, SCORE_LAYER
from cam_core.layout import entity_bounds


def guard(project):
    if project.operations or project.metadata.get('cabinet_tab_plans') or 'cabinet_fabrication_snapshot' in project.metadata:
        raise ValueError('Create mailer geometry in a source drawing; machining and registered assemblies require their owning revision workflows.')
    settings = _automation_layer_settings(copy.deepcopy(project))
    for layer in (CUT_LAYER, SCORE_LAYER):
        if settings.get(layer, {}).get('locked') or settings.get(layer, {}).get('visible') is False:
            raise ValueError(f'Show and unlock {layer} before creating a mailer; its settings will not be changed automatically.')


def active_plate(project):
    return next((p for p in _project_plates(project) if p.number == project.metadata.get('active_plate_number', 1)), None)


class MailerDraft:
    def __init__(self): self.context = self.cached = None

    def begin(self, project, p, revision, digest):
        self.context = self.cached = None
        guard(project)
        if p: raise ValueError('Open a new mailer review without extra inputs.')
        defaults = asdict(default_options(project.units)); plate = active_plate(project)
        if plate:
            defaults['x'] += plate.bounds[0]; defaults['y'] += plate.bounds[1]
        token = uuid4().hex
        self.context = dict(token=token, revision=revision, digest=digest)
        return dict(context_token=token, units=project.units, defaults=defaults, plate=list(plate.bounds) if plate else None)

    def current(self, project, token, revision, digest):
        guard(project)
        c = self.context
        if not c or c['token'] != token or c['revision'] != revision or c['digest'] != digest:
            raise ValueError('Drawing or placement context changed. Reopen the mailer review.')

    def preview(self, project, p, revision, digest):
        self.cached = None
        if set(p) != {'context_token', 'options'} or not isinstance(p['options'], dict) or set(p['options']) != set(asdict(MailerOptions())):
            raise ValueError('Provide all physical mailer dimensions, placement and rotation.')
        self.current(project, p['context_token'], revision, digest)
        plan = plan_mailer(MailerOptions(**p['options']), project.units)
        plate = active_plate(project)
        warnings = plan_warnings(plan, plate.bounds if plate else None, [entity_bounds(e) for e in project.entities])
        entities = mailer_entities(plan)
        token = uuid4().hex
        self.cached = dict(token=token, context_token=p['context_token'], plan=plan, warnings=warnings)
        return dict(token=token, units=project.units, options=asdict(plan.options), bounds=list(plan.bounds),
            labels=[dict(text=name, x=x, y=y) for name,x,y in plan.labels], warnings=list(warnings),
            entities=[e.to_dict() for e in entities], cut_count=1, score_count=len(plan.scores))

    def apply(self, session, p, revision, digest):
        d = self.cached
        if set(p) != {'token', 'reviewed', 'placement_reviewed'} or p['reviewed'] is not True or type(p['placement_reviewed']) is not bool or not d or d['token'] != p['token']:
            raise ValueError('Preview the current blank and review fold fit and machining intent before Apply.')
        self.current(session.project, d['context_token'], revision, digest)
        if d['warnings'] and p['placement_reviewed'] is not True:
            raise ValueError('Review the sheet-fit/overlap findings before adding geometry for layout review.')
        entities = mailer_entities(d['plan'])  # Revalidates the immutable plan against the core.
        candidate = copy.deepcopy(session.project)
        candidate.entities.extend(entities)
        _automation_layer_settings(candidate)
        session.project = candidate
        session.selected_ids = {e.id for e in entities}
        session._record_history()
        self.context = self.cached = None
