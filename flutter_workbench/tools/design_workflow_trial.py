"""Repeatable isolated file/output trial; never writes source examples or CNC.

Run from the Flutter project using its bundled Python runtime. Outputs go to a
new build/design-trials folder and include a machine-readable evidence report.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from design_bridge import Bridge
from cam_core.automation import AutomationSession
from cam_core.model import Tool
from cam_core.layout import drawing_bounds
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from cam_core.output_workflow import output_generation_fingerprint
from cam_core.automation import _project_plates
from pypdf import PdfReader


def run(bridge, command, **params):
    response = bridge.handle(dict(command=command, params=params, revision=bridge.revision))
    assert response['ok'], (command, response.get('error'))
    return response


def manufacturing_probe():
    """Measure core readiness only; does not expose manufacturing in Flutter."""
    results = []
    for units, factor in [('in', 1), ('mm', 25.4)]:
        session = AutomationSession()
        def execute(command, **params):
            r = session.execute(command, params)
            assert r['ok'], r
            return r
        execute('project.new', units=units)
        session.project.tool_library.tools = [Tool(1, 'Trial cutter', .25*factor,
            spindle_rpm=18000, feed_rate=2*factor, plunge_rate=.5*factor,
            max_stepdown=.125*factor, cutting_edge_length=.75*factor)]
        execute('job.setup', material='Plywood', material_thickness=.5*factor, safe_z=.25*factor)
        execute('geometry.add_rectangle', x=2*factor, y=2*factor, width=6*factor, height=4*factor)
        execute('operation.add_router_offset', tool_station=1, target_depth=.5*factor,
            safe_z=.25*factor, stepdown=.125*factor, side='outside')
        before = copy.deepcopy(session.project.to_dict())
        progress = []
        started = time.perf_counter()
        bundle, errors = generate_toolpath_preview_bundle(copy.deepcopy(session.project),
            progress_callback=lambda done,total,name: progress.append((done,total,name)))
        elapsed = time.perf_counter()-started
        assert not errors and bundle and progress[-1][0] == progress[-1][1]
        assert session.project.to_dict() == before
        paths = list(bundle.values())
        assert all(block.motions for path in paths for block in path.blocks)
        canceled = False
        try: generate_toolpath_preview_bundle(session.project, cancel_check=lambda: True)
        except InterruptedError: canceled = True
        assert canceled
        fingerprint = output_generation_fingerprint(session.project, _project_plates(session.project))
        session.project.operations[0].target_depth *= .9
        assert output_generation_fingerprint(session.project, _project_plates(session.project)) != fingerprint
        session.project.operations[0].target_depth /= .9
        validation = execute('project.validate')
        results.append(dict(units=units, seconds=round(elapsed,4),
            blocks=sum(len(p.blocks) for p in paths), motions=sum(len(b.motions) for p in paths for b in p.blocks),
            progress=True, cancel=True, inputInvalidation=True, validation=validation))
    return results


def main():
    base = ROOT / 'build/design-trials'
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='five-task-', dir=base))
    source = ROOT / 'examples/design-evaluation.camjob.json'
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    first = Bridge(folder/'first-settings.json', recovery_directory=folder/'recovery')
    second = Bridge(folder/'second-settings.json', recovery_directory=folder/'recovery')
    try:
        run(first, 'project.open', path=str(source))
        original = copy.deepcopy(first.session.project.to_dict())
        ids = [e.id for e in first.session.project.entities]
        assert len(ids) == 16
        run(first, 'selection.set', entity_ids=ids)
        run(first, 'geometry.transform', dx=1, dy=1)
        run(first, 'history.undo')
        assert first.session.project.to_dict() == original
        run(first, 'history.redo')
        panel = next(e for e in first.snapshot()['entities'] if e['layer']=='Panel')
        x0,y0,x1,y1 = panel['bounds']
        run(first, 'selection.set', entity_ids=[panel['id']])
        preview = run(first, 'design.preview_shapes', kind='cleave',
            line=[dict(x=(x0+x1)/2,y=y0-1),dict(x=(x0+x1)/2,y=y1+1)])['preview']
        assert abs(preview['areaBefore']-preview['areaAfter']) < 1e-7
        run(first, 'design.apply_shapes', token=preview['token'])
        annotation = dict(kind='horizontal', start=dict(fallback=dict(x=x0,y=y0)),
            end=dict(fallback=dict(x=x1,y=y0)), offset=-3, text_height=.6,
            extension_offset=.2, precision=2, layer='DIMENSIONS', metadata={})
        preview = run(first, 'design.dimension_preview', annotation=annotation)['preview']
        run(first, 'design.dimension_apply', token=preview['token'])
        run(first, 'design.guide_add', orientation='vertical', position=(x0+x1)/2)
        p = first.session.project
        run(first, 'selection.set', entity_ids=[e.id for e in p.entities])
        run(first, 'design.copy', dimension_ids=[d.id for d in p.dimensions], guide_ids=[g.id for g in p.guides])
        clipboard = run(first, 'design.clipboard_export')['clipboard']
        run(second, 'project.new', name='Design workflow trial', units=p.units)
        run(second, 'design.paste', clipboard=clipboard)
        assert len(second.session.project.entities) == len(first.session.selected_ids), (len(second.session.project.entities),len(first.session.selected_ids))
        assert len(second.session.project.dimensions) == len(second.session.project.guides) == 1
        expected = copy.deepcopy(second.session.project.to_dict())
        run(second, 'history.undo')
        assert not second.session.project.entities and not second.session.project.dimensions
        run(second, 'history.redo')
        assert second.session.project.to_dict() == expected
        job = folder/'drawing.camjob.json'
        run(second, 'project.save', path=str(job))
        assert second.snapshot()['recovery']['updated'] is None
        run(second, 'project.open', path=str(job))
        assert second.session.project.to_dict() == expected
        dxf = folder/'cutting-geometry.dxf'
        run(second, 'project.export_dxf', path=str(dxf))
        preview = run(second, 'design.shop_pdf_prepare', plate_numbers=[1],
            page_size='Tabloid', orientation='landscape', scale_mode='fit')['preview']
        assert preview['omittedCount'] == preview['unresolvedCount'] == 0
        pdf = folder/'shop-drawing.pdf'
        run(second, 'design.shop_pdf_export', token=preview['token'], path=str(pdf))
        reader = PdfReader(pdf)
        assert len(reader.pages) == 1 and 'Plate 1' in reader.pages[0].extract_text()
        expected_bounds = drawing_bounds(second.session.project.entities, include_annotations=True)
        run(second, 'project.open', path=str(dxf))
        imported = second.session.project
        assert not imported.dimensions and not imported.guides and not imported.operations
        assert len(imported.entities) == len(expected['entities'])
        assert all(abs(a-b) < 1e-6 for a,b in zip(expected_bounds,drawing_bounds(imported.entities,include_annotations=True)))
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        report = dict(source=str(source), sourceSHA256=original_hash, sourceUnchanged=True,
            snapshot=first.source_id, inputObjects=16, outputObjects=len(expected['entities']),
            hiddenSourceObjectsExcluded=len(first.session.project.entities)-len(expected['entities']),
            workflow=['open','move','undo/redo','finite split','dimension','guide','cross-document clipboard',
                      'independent undo/redo','CAM save/reopen','DXF export/reopen','PDF prepare/export'],
            preservedAnnotations=1, preservedGuides=1, annotationsExcludedFromDXF=True,
            boundsMatch=True, outputs=[str(job),str(dxf),str(pdf)], manufacturingProbe=manufacturing_probe())
        (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(dict(report=str(folder/'report.json'),pdf=str(pdf),status='passed')))
    finally:
        for bridge in (first,second):
            bridge.shop_pdf.clear()
            if bridge.recovery:
                bridge.recovery.clear()
                bridge.recovery.close()


if __name__ == '__main__': main()
