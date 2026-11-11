import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from design_bridge import Bridge
from cam_core.model import CamProject, Rectangle, Point, Tool, ToolLibrary, RouterOffsetOperation
from cam_core.svg_import import import_svg
from cam_core.svg_import_ui import prepare_svg_append
from cam_core.layout import entity_bounds
from cam_core.units import unit_scale
import svg_import_review as review

ART = '''<svg xmlns="http://www.w3.org/2000/svg" width="4in" height="3in" viewBox="0 0 400 300">
<style>.outline {fill:none;stroke:#123456}</style><defs><path id="mark" d="M0 0L20 0" class="outline"/></defs>
<g id="Cut" transform="translate(20,30)"><path id="frame" fill-rule="evenodd" d="M0 0H200V100H0Z M20 20H50V50H20Z"/>
<path id="curve" class="outline" d="M0 130C40 90 100 160 150 130"/><use href="#mark" x="220" y="100"/></g>
<text x="0" y="20">omitted lettering</text><image href="https://invalid.example/image.png" width="10" height="10"/>
<rect width="30" height="20" display="none"/></svg>'''


class SvgImportReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.b = Bridge(self.root/'prefs.json'); self.addCleanup(self.b.manufacturing.close)
        self.path = self.root/'art.svg'; self.path.write_text(ART, encoding='utf-8')
        self.ok('project.new')
        self.b.session.project.metadata['job_setup'] = dict(material='Plywood', material_thickness=.5, safe_z=.25)
        self.b.session.project.tool_library = ToolLibrary([Tool(1, 'Existing tool', .25)])
        self.ok('geometry.add_rectangle', x=8, y=8, width=2, height=1)
    def call(self, command, **params):
        return self.b.handle(dict(command=command, params=params, revision=self.b.revision))
    def ok(self, command, **params):
        r = self.call(command, **params); self.assertTrue(r['ok'], r); json.dumps(r, allow_nan=False); return r
    def reject(self, command, **params):
        before = copy.deepcopy(self.b.session.project.to_dict()); history = copy.deepcopy(self.b.session._history)
        result = self.call(command, **params); self.assertFalse(result['ok'], result)
        self.assertEqual(before, self.b.session.project.to_dict()); self.assertEqual(history, self.b.session._history)
        return result['error']
    def begin(self): return self.ok('design.svg_begin', path=str(self.path))['context']
    def preview(self, c, **patches):
        p = dict(context_token=c['context_token'], placement='original', x=0, y=0, tolerance=c['tolerance']); p.update(patches)
        return self.ok('design.svg_preview', **p)['preview']
    def apply(self, d): return self.ok('design.svg_apply', token=d['token'], reviewed=True)

    def test_core_parity_physical_units_and_private_preview(self):
        for units in ('in', 'mm', 'cm'):
            self.b.session.project.units = units
            before = copy.deepcopy(self.b.session.project.to_dict()); h = self.b.session._history_index; selected = set(self.b.session.selected_ids)
            c = self.begin(); d = self.preview(c)
            imported = import_svg(self.path)
            expected = prepare_svg_append(self.b.session.project, imported.project)
            self.assertEqual([entity_bounds(e) for e in expected.entities], [tuple(e['bounds']) for e in d['entities']])
            self.assertEqual(d['page_size'], [4*unit_scale('in', units), 3*unit_scale('in', units)])
            self.assertEqual((d['closed'], d['open']), (2, 2))
            self.assertTrue(any('text: 1' in s and 'image: 1' in s for s in d['warnings']))
            self.assertTrue(any('centerline' in s for s in d['warnings']))
            self.assertEqual(d['entities'][0]['metadata']['svg_fill_rule'], 'evenodd')
            self.assertEqual(d['entities'][0]['metadata']['compound_id'], d['entities'][1]['metadata']['compound_id'])
            self.assertEqual(before, self.b.session.project.to_dict()); self.assertEqual(h, self.b.session._history_index); self.assertEqual(selected, self.b.session.selected_ids)
    def test_one_step_apply_undo_redo_native_save_and_source_preserved(self):
        original = copy.deepcopy(self.b.session.project.to_dict()); source = self.path.read_bytes(); h = self.b.session._history_index
        c = self.begin(); d = self.preview(c)
        self.reject('design.svg_apply', token=d['token'], reviewed=False)
        self.apply(d); after = copy.deepcopy(self.b.session.project.to_dict())
        self.assertEqual(self.b.session._history_index, h+1); self.assertEqual(len(self.b.session.selected_ids), 4)
        self.assertEqual(after['entities'][0], original['entities'][0]); self.assertEqual(after['tool_library'], original['tool_library'])
        self.assertEqual(after['metadata']['job_setup'], original['metadata']['job_setup'])
        notes = next(iter(after['metadata']['svg_import_batches'].values()))
        self.assertEqual(notes['source'], str(self.path)); self.assertEqual(notes['warnings'], d['warnings'])
        self.ok('history.undo'); self.assertEqual(self.b.session.project.to_dict(), original)
        self.ok('history.redo'); self.assertEqual(self.b.session.project.to_dict(), after)
        destination = self.root/'copy.camjob.json'; self.ok('project.save', path=str(destination))
        self.assertEqual(CamProject.load(destination).to_dict(), after)
        self.assertEqual(self.path.read_bytes(), source)
        self.reject('design.svg_apply', token=d['token'], reviewed=True)
    def test_plate_custom_placement_and_protected_layer_remapping(self):
        j = self.b.session.project
        j.metadata['plates'] = [dict(number=1, x=100, y=50, width=96, height=48)]
        j.metadata['layer_settings'] = {'Cut':dict(visible=False, locked=True)}
        c = self.begin(); d = self.preview(c, placement='plate')
        self.assertEqual(d['bounds'][:2], [100., 50.]); self.assertEqual(d['renamed_layers'], {'Cut':'Cut (import)'})
        self.assertTrue(all(e['layer']=='Cut (import)' for e in d['entities']))
        custom = self.preview(c, placement='custom', x=-2, y=7)
        self.assertEqual(custom['bounds'][:2], [-2., 7.])
        self.reject('design.svg_apply', token=d['token'], reviewed=True)
        self.apply(custom); self.assertEqual(self.b.session.project.metadata['layer_settings']['Cut'], dict(visible=False, locked=True))
    def test_stale_source_document_and_layer_change(self):
        c = self.begin(); d = self.preview(c); self.path.write_text(ART+'\n', encoding='utf-8')
        self.assertIn('source SVG changed', self.reject('design.svg_apply', token=d['token'], reviewed=True))
        c = self.begin(); d = self.preview(c); self.b.session.project.name = 'Changed without revision'
        self.reject('design.svg_apply', token=d['token'], reviewed=True)
        c = self.begin(); d = self.preview(c); self.b.session.project.metadata.setdefault('layer_settings', {})['Cut'] = dict(locked=True)
        self.reject('design.svg_apply', token=d['token'], reviewed=True)
        c = self.begin(); d = self.preview(c); self.ok('geometry.add_circle', x=5, y=5, diameter=1)
        self.reject('design.svg_preview', context_token=c['context_token'], placement='original', x=0, y=0, tolerance=.001)
    def test_invalid_inputs_and_failed_preview_invalidate_prior_offer(self):
        c = self.begin(); d = self.preview(c)
        p = dict(context_token=c['context_token'], placement='custom', x=0, y=0, tolerance=.001)
        for change in [dict(x=float('nan')), dict(y=True), dict(tolerance=0), dict(tolerance=.1), dict(placement='arbitrary'), dict(x=1e20), dict(extra='ignored')]:
            self.reject('design.svg_preview', **dict(p, **change))
            self.reject('design.svg_apply', token=d['token'], reviewed=True)
    def test_external_resources_scripts_and_ambiguous_rendering_are_refused(self):
        cases = ['<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]><svg/>',
            '<svg width="10" height="10"><script>bad()</script></svg>',
            '<svg width="10" height="10"><use href="https://invalid.example/art.svg#x"/></svg>',
            '<svg width="10" height="10"><rect width="5" height="5" clip-path="url(#clip)"/></svg>',
            '<svg width="10" height="10"><style>@import url(https://invalid.example);</style></svg>']
        for text in cases:
            self.path.write_text(text, encoding='utf-8'); c = self.begin()
            with patch('socket.socket', side_effect=AssertionError('No network access permitted')):
                self.reject('design.svg_preview', context_token=c['context_token'], placement='original', x=0, y=0, tolerance=.001)
    def test_bounds_file_size_and_parser_point_budget(self):
        with patch.object(review, 'LIMIT', 8): self.reject('design.svg_begin', path=str(self.path))
        self.reject('design.svg_begin', path=str(self.root/'wrong.json'))
        # Exercise the actual core point budget without allocating 200,000 test points.
        c = self.begin()
        from cam_core.svg_path import Budget
        with patch('cam_core.svg_import.Budget', lambda: Budget(3)):
            self.reject('design.svg_preview', context_token=c['context_token'], placement='original', x=0, y=0, tolerance=.001)
    def test_machining_and_registered_assemblies_stay_protected(self):
        self.b.session.project.operations.append(RouterOffsetOperation(name='Existing', tool_station=1, entity_ids=[], target_depth=.1, safe_z=.5))
        self.reject('design.svg_begin', path=str(self.path))
        self.b.session.project.operations.clear()
        self.b.session.project.metadata['cabinet_tab_plans'] = {'registered':{}}
        self.reject('design.svg_begin', path=str(self.path))
        del self.b.session.project.metadata['cabinet_tab_plans']
        self.b.session.project.metadata['cabinet_fabrication_snapshot'] = {}
        self.reject('design.svg_begin', path=str(self.path))
