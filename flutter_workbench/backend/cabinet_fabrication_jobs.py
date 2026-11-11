"""Private, freshly checked Cabinet snapshots; atomic local publication only."""
import copy
import tempfile
from pathlib import Path
from uuid import uuid4
from cam_core.model import CamProject, Tool, ToolKind
from cam_core.layout import entity_bounds
from cam_core.cabinet_tab_production import machining_jobs, save_machining_jobs, SETTINGS_KEY
from manufacturing_tools import validate_turret
import cabinet_fabrication_review as review

OUTPUT_ROOT = Path(__file__).resolve().parents[1] / 'build' / 'cabinet-output'
NAMES = ('Tabbed backs.camjob.json', 'Cabinet top and slots.camjob.json', 'Assembly review.camjob.json')


def prepare(draft, project, p, revision, digest, catalog):
    if set(p) != {'token', 'settings', 'private_tool'}:
        raise ValueError('Provide the current layout, complete fabrication inputs and optional private cutter.')
    candidate, plan = review.current(draft, project, p, revision, digest)
    candidate = copy.deepcopy(candidate)
    settings = review.parse_settings(p['settings'])
    ref = p['private_tool']
    if ref is not None:
        if not isinstance(ref, dict) or set(ref) != {'id', 'recipe', 'station', 'version'} or type(ref['station']) is not int or not 1 <= ref['station'] <= 12:
            raise ValueError('Choose a current private library recipe and station.')
        if ref['station'] != settings.tool_station:
            raise ValueError('The private cutter must match the reviewed station.')
        payload = catalog.command('catalog_resolve', ref, candidate)['tool']
        tool = Tool(**dict(payload, kind=ToolKind(payload['kind'])))
        if tool.kind != ToolKind.END_MILL or tool.spindle_mode == 'disabled':
            raise ValueError('Choose a spindle-enabled flat end mill for the slots.')
        validate_turret([tool])
        candidate.tool_library.tools = [t for t in candidate.tool_library.tools if t.station != tool.station] + [tool]
    result = review.inspect(draft, project, {'token': p['token'], 'settings': p['settings']}, revision, digest, candidate_override=candidate)
    return candidate, plan, settings, result


def preview(draft, project, p, revision, digest, catalog):
    draft.fabrication = None  # A failed/new check invalidates any earlier export offer.
    candidate, plan, settings, result = prepare(draft, project, p, revision, digest, catalog)
    if not result['geometry_checks_passed']:
        return result
    jobs = machining_jobs(candidate, plan, settings)
    token = uuid4().hex
    draft.fabrication = dict(token=token, request=copy.deepcopy(p), candidate=candidate.to_dict())
    summaries = []
    for i, job in enumerate(jobs):
        bounds = [entity_bounds(e) for e in job.entities]
        summary = dict(view=f'job{i}', name=job.name, objects=len(job.entities),
            bounds=[min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)],
            translation=job.metadata['cabinet_fabrication_snapshot']['assembly_to_job_translation'],
            thickness=job.metadata['job_setup']['material_thickness'])
        summaries.append(summary)
        result['views'][f'job{i}'] = [dict(e.to_dict(), review_role='slot' if e.metadata.get('cabinet_pair_id') else 'back') for e in job.entities]
    result.update(can_export=True, review_token=token, jobs=summaries,
        message='Ready to save two independent machining jobs and an assembly review copy. Stock and clamp zones retain their stock coordinates; review placement, tooling and hold-down in each job. No operations or output approval are created.')
    return result


def publish(jobs, assembly):
    root = OUTPUT_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.cabinet-export-', dir=root)).resolve()
    published = None
    try:
        paths = save_machining_jobs(jobs, staging, assembly.name)
        bundle = paths[0].parent
        assembly_path = bundle / NAMES[2]
        assembly.save(assembly_path)
        if CamProject.load(assembly_path).to_dict() != assembly.to_dict():
            raise ValueError('Assembly review copy did not round-trip correctly.')
        destination = root / bundle.name
        if destination.exists():
            raise ValueError('Export destination already exists; preview again.')
        bundle.rename(destination)  # All three verified native files become visible together.
        published = destination
    finally:
        # Only new, contained folders and our three known files. Never delete a tree.
        if staging.parent != root or not staging.name.startswith('.cabinet-export-'):
            raise ValueError('Invalid private export staging location.')
        try:
            for child in staging.iterdir():
                if child.is_dir() and not child.is_symlink() and child.resolve().parent == staging:
                    for name in NAMES:
                        path = child / name
                        if path.is_file() and not path.is_symlink():
                            path.unlink()
                    if not any(child.iterdir()): child.rmdir()
            if not any(staging.iterdir()): staging.rmdir()
        except OSError:
            if published is None: raise
            # Publication succeeded. An empty staging-folder cleanup failure
            # must not report the save as failed or cause a duplicate on retry.
    return dict(paths=[str(published / name) for name in NAMES[:2]], assembly_path=str(published / NAMES[2]), folder=str(published))


def export(draft, project, p, revision, digest, catalog):
    if set(p) != {'token', 'review_token'}:
        raise ValueError('Preview the paired jobs before saving them.')
    saved = getattr(draft, 'fabrication', None)
    if not saved or p['review_token'] != saved['token'] or p['token'] != saved['request']['token']:
        raise ValueError('Fabrication preview expired. Check and preview again.')
    candidate, plan, settings, result = prepare(draft, project, saved['request'], revision, digest, catalog)
    if not result['geometry_checks_passed'] or candidate.to_dict() != saved['candidate']:
        draft.fabrication = None
        raise ValueError('Fabrication inputs or cutter changed. Check and preview again.')
    if saved.get('published'):
        return saved['published']  # Retrying a successful request cannot duplicate the bundle.
    jobs = machining_jobs(candidate, plan, settings)
    assembly = copy.deepcopy(candidate)
    if not isinstance(assembly.metadata.get(SETTINGS_KEY), dict): assembly.metadata[SETTINGS_KEY] = {}
    assembly.metadata[SETTINGS_KEY][plan.plan_id] = dict(settings.to_dict(), registration_reviewed=False, fit_reviewed=False)
    result = publish(jobs, assembly)
    saved['published'] = result
    return result
