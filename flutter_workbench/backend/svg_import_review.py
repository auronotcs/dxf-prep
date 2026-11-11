"""Bounded, offline SVG capture and additive, revision-bound geometry review."""
import copy
import hashlib
import math
from pathlib import Path
import tempfile
from uuid import uuid4
from cam_core.automation import _project_plates
from cam_core.svg_import import import_svg
from cam_core.svg_import_ui import prepare_svg_append
from cam_core.units import unit_scale
from cam_core.layout import entity_bounds

LIMIT = 16 * 1024 * 1024


def guard(project):
    if project.operations or project.metadata.get('cabinet_tab_plans') or 'cabinet_fabrication_snapshot' in project.metadata:
        raise ValueError('Import SVG into a source drawing; registered assemblies and machining jobs require their owning revision workflows.')


def capture(path):
    with path.open('rb') as stream: data = stream.read(LIMIT + 1)
    if len(data) > LIMIT: raise ValueError('SVG exceeds the 16 MB import limit.')
    return data


class SvgDraft:
    def __init__(self): self.loaded = self.cached = None

    def begin(self, project, p, revision, digest):
        self.loaded = self.cached = None
        guard(project)
        if set(p) != {'path'} or not isinstance(p['path'], str): raise ValueError('Choose one SVG file.')
        path = Path(p['path']).resolve()
        if path.suffix.lower() != '.svg' or not path.is_file(): raise ValueError('Choose an existing .svg file.')
        data = capture(path)
        if data != capture(path): raise ValueError('SVG changed while reading. Reopen the import.')
        token = uuid4().hex
        plate = next((p for p in _project_plates(project) if p.number == project.metadata.get('active_plate_number', 1)), None)
        self.loaded = dict(token=token, path=path, data=data, sha256=hashlib.sha256(data).hexdigest(), revision=revision, digest=digest)
        return dict(context_token=token, filename=path.name, units=project.units, tolerance=.001*unit_scale('in', project.units),
            tolerance_range=[.00001*unit_scale('in', project.units), .01*unit_scale('in', project.units)],
            plate=list(plate.bounds) if plate else None)

    def current(self, project, token, revision, digest):
        guard(project)
        d = self.loaded
        if not d or d['token'] != token or d['revision'] != revision or d['digest'] != digest:
            raise ValueError('Drawing changed. Reopen SVG import before applying geometry.')
        if hashlib.sha256(capture(d['path'])).hexdigest() != d['sha256']:
            raise ValueError('The source SVG changed. Reopen the import to review its new contents.')
        return d

    def preview(self, project, p, revision, digest):
        self.cached = None
        if set(p) != {'context_token', 'placement', 'x', 'y', 'tolerance'}: raise ValueError('Provide complete SVG placement and tolerance inputs.')
        d = self.current(project, p['context_token'], revision, digest)
        for key in ('x', 'y', 'tolerance'):
            if type(p[key]) not in (int, float) or not math.isfinite(p[key]): raise ValueError('Enter finite SVG placement and tolerance dimensions.')
        lower_left = None
        if p['placement'] == 'plate':
            plate = next((v for v in _project_plates(project) if v.number == project.metadata.get('active_plate_number', 1)), None)
            if plate is None: raise ValueError('Choose an active plate before placing SVG geometry on it.')
            lower_left = plate.bounds[:2]
        elif p['placement'] == 'custom': lower_left = (p['x'], p['y'])
        elif p['placement'] != 'original': raise ValueError('Choose original page coordinates, active plate or a lower-left position.')
        tolerance = p['tolerance'] * unit_scale(project.units, 'in')
        # Parse exactly the captured bytes. No external resource or source-file writes.
        with tempfile.TemporaryDirectory(prefix='flutter-svg-') as folder:
            local = Path(folder) / 'source.svg'; local.write_bytes(d['data'])
            imported = import_svg(local, tolerance=tolerance)
        imported.project.source['file'] = str(d['path'])
        imported.project.metadata['svg_import']['source'] = str(d['path'])
        prepared = prepare_svg_append(project, imported.project, lower_left=lower_left)
        if any(abs(v) > 1e7 for v in prepared.bounds): raise ValueError('SVG placement exceeds the supported drawing range.')
        candidate = copy.deepcopy(project)
        candidate.entities.extend(prepared.entities)
        batch = prepared.entities[0].metadata['svg_append_batch']
        notes = list(imported.warnings) + ['Original artwork colors and stroke widths are not reproduced; imported geometry retains layer, compound and fill-rule intent.']
        batches = candidate.metadata.setdefault('svg_import_batches', {})
        if not isinstance(batches, dict): raise ValueError('Saved SVG provenance is malformed; use a new source drawing.')
        batches[batch] = dict(source=str(d['path']), sha256=d['sha256'], warnings=notes,
            page_inches=list(imported.page_size), tolerance_inches=tolerance, placement=p['placement'], bounds=list(prepared.bounds))
        token = uuid4().hex
        self.cached = dict(token=token, context_token=d['token'], project=candidate, ids={e.id for e in prepared.entities})
        f = unit_scale('in', project.units)
        closed = sum(bool(getattr(e, 'closed', False)) for e in prepared.entities)
        return dict(token=token, units=project.units, filename=d['path'].name, bounds=list(prepared.bounds),
            page_size=[v*f for v in imported.page_size], tolerance=p['tolerance'], closed=closed, open=len(prepared.entities)-closed,
            counts=dict(imported.entity_counts), warnings=notes, renamed_layers=prepared.renamed_layers,
            entities=[dict(e.to_dict(), bounds=list(entity_bounds(e))) for e in prepared.entities])

    def apply(self, session, p, revision, digest):
        d = self.cached
        if set(p) != {'token', 'reviewed'} or p['reviewed'] is not True or not d or p['token'] != d['token']:
            raise ValueError('Preview and review SVG dimensions and notices before importing.')
        self.current(session.project, d['context_token'], revision, digest)
        session.project = copy.deepcopy(d['project'])
        session.selected_ids = set(d['ids'])
        session._record_history()
        self.loaded = self.cached = None
