import copy
from pathlib import Path
from unittest.mock import patch
from test_cabinet_fabrication_review import CabinetFabricationReviewTests as Base
from cam_core.model import CamProject, Point, KeepoutZone
from cam_core.cabinet_tab_production import SETTINGS_KEY, machining_jobs
from cam_core.layout import entity_bounds
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from manufacturing_support import supported
from design_bridge import Bridge
import cabinet_fabrication_jobs as jobs


class CabinetFabricationJobsTests(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.b.manufacturing.close)
        self.output = self.root / 'exports'
        self.patch = patch.object(jobs, 'OUTPUT_ROOT', self.output)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def proposal(self, f=1., private=True):
        d, p, s = self.fixture(f)
        ref = None
        if private:
            catalog = self.ok('manufacturing.catalog_list')
            ref = dict(id='vortex-5609a', recipe=0, station=2, version=catalog['version'])
            s['tool_station'] = 2
        params = dict(token=d['token'], settings=s, private_tool=ref)
        r = self.ok('design.cabinet_fabrication_jobs_preview', **params)['review']
        self.assertTrue(r['can_export'], r)
        return params, r

    def export_pair(self, p, r):
        return self.ok('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])['review']

    def test_private_recipe_units_pair_preview_and_source_unchanged(self):
        for f in (1., 25.4):
            p, r = self.proposal(f)
            before = copy.deepcopy(self.b.session.project.to_dict()); history = copy.deepcopy(self.b.session._history)
            revision = self.b.revision; selected = set(self.b.session.selected_ids)
            candidate, plan, settings, _ = jobs.prepare(self.b.cabinet_draft, self.b.session.project, p, revision, self.b.digest(), self.b.manufacturing_tools)
            cutter = candidate.tool_library.by_station(2)
            self.assertAlmostEqual(cutter.diameter, .125*f); self.assertAlmostEqual(cutter.feed_rate, 72/60*f)
            direct = machining_jobs(candidate, plan, settings)
            for i, job in enumerate(direct):
                actual = [{k: v for k, v in e.items() if k != 'review_role'} for e in r['views'][f'job{i}']]
                # Core generated slot IDs differ between independent evaluations.
                self.assertEqual([entity_bounds(e) for e in job.entities], [entity_bounds(e) for e in CamProject.from_dict(dict(job.to_dict(), entities=actual)).entities])
                self.assertEqual(r['jobs'][i]['translation'], job.metadata['cabinet_fabrication_snapshot']['assembly_to_job_translation'])
                self.assertAlmostEqual(r['jobs'][i]['bounds'][0], .5*f)
                self.assertAlmostEqual(r['jobs'][i]['bounds'][1], .5*f)
            exported = self.export_pair(p, r)
            assembly = CamProject.load(exported['assembly_path'])
            self.assertEqual(assembly.tool_library.by_station(2).to_dict(), cutter.to_dict())
            saved = assembly.metadata[SETTINGS_KEY][plan.plan_id]
            self.assertFalse(saved['registration_reviewed']); self.assertFalse(saved['fit_reviewed'])
            self.assertTrue(assembly.metadata['cabinet_tab_plans']); self.assertFalse(supported(assembly))
            self.assertEqual(before, self.b.session.project.to_dict()); self.assertEqual(history, self.b.session._history)
            self.assertEqual(selected, self.b.session.selected_ids); self.assertEqual(revision, self.b.revision)
            self.assertEqual(exported, self.export_pair(p, r))
            self.assertEqual(len(list(Path(exported['folder']).iterdir())), 3)

    def test_current_plan_catalog_and_failed_new_check_invalidate_export(self):
        p, r = self.proposal(); self.b.session.project.name = 'Changed'
        self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])
        p, r = self.proposal(); self.ok('design.cabinet_begin')
        self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])
        p, r = self.proposal(); catalog = self.b.manufacturing_tools
        data = catalog.read(); data['tools'] = [dict(catalog.command('catalog_list', {}, self.b.session.project)['catalog'][0], name='Updated')]; catalog.path.parent.mkdir(exist_ok=True, parents=True); catalog.write(data)
        self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])
        p, r = self.proposal(); bad = copy.deepcopy(p); bad['settings']['edge_clearance'] = 99
        report = self.ok('design.cabinet_fabrication_jobs_preview', **bad)['review']; self.assertFalse(report['can_export'])
        self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])
        self.assertFalse(self.output.exists())

    def test_private_selection_forgery_and_destination_rejected(self):
        p, r = self.proposal()
        for ref in [dict(p['private_tool'], version='stale'), dict(p['private_tool'], station=True), dict(p['private_tool'], station=8), dict(p['private_tool'], diameter=.001), dict(p['private_tool'], recipe=999)]:
            self.reject('design.cabinet_fabrication_jobs_preview', **dict(p, private_tool=ref))
        self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'], path=str(self.root/'unrequested'))
        self.assertFalse(self.output.exists())

    def test_atomic_bundle_failure_never_publishes_half_pair(self):
        p, r = self.proposal()
        real_save = CamProject.save
        def fail_assembly(project, path):
            if Path(path).name == jobs.NAMES[2]:
                Path(path).write_text('partial', encoding='utf-8')
                raise OSError('Simulated assembly save failure')
            return real_save(project, path)
        with patch.object(CamProject, 'save', fail_assembly):
            error = self.reject('design.cabinet_fabrication_export', token=p['token'], review_token=r['review_token'])
            self.assertIn('Simulated', error)
        self.assertEqual(list(self.output.iterdir()), [])
        exported = self.export_pair(p, r)
        self.assertTrue(all(Path(path).is_file() for path in exported['paths']))

    def test_post_publish_cleanup_failure_is_not_a_failed_or_duplicate_save(self):
        p, r = self.proposal()
        original_rmdir = Path.rmdir
        def busy_staging(path):
            if path.name.startswith('.cabinet-export-'): raise PermissionError('Staging folder busy')
            return original_rmdir(path)
        with patch.object(Path, 'rmdir', busy_staging): exported = self.export_pair(p, r)
        self.assertEqual(exported, self.export_pair(p, r))
        bundles = [path for path in self.output.iterdir() if not path.name.startswith('.')]
        self.assertEqual(len(bundles), 1)
        self.assertEqual({p.name for p in bundles[0].iterdir()}, set(jobs.NAMES))

    def test_malformed_saved_settings_can_be_replaced_in_private_assembly_copy(self):
        d, _, s = self.fixture()
        self.b.cabinet_draft.cached['project'].metadata[SETTINGS_KEY] = ['bad old setting']
        p = dict(token=d['token'], settings=s, private_tool=None)
        r = self.ok('design.cabinet_fabrication_jobs_preview', **p)['review']
        result = self.export_pair(p, r)
        self.assertIsInstance(CamProject.load(result['assembly_path']).metadata[SETTINGS_KEY], dict)
        self.assertEqual(self.b.cabinet_draft.cached['project'].metadata[SETTINGS_KEY], ['bad old setting'])

    def test_loaded_tool_export_no_operations_approval_and_independent_open(self):
        p, r = self.proposal(private=False); exported = self.export_pair(p, r)
        original = copy.deepcopy(self.b.session.project.to_dict())
        for path in exported['paths']:
            independent = Bridge(self.root / (Path(path).stem + '-prefs.json'))
            self.addCleanup(independent.manufacturing.close)
            result = independent.handle(dict(command='project.open', params=dict(path=path), revision=independent.revision))
            self.assertTrue(result['ok'], result)
            job = independent.session.project
            self.assertTrue(supported(job)); self.assertEqual(job.operations, [])
            self.assertTrue(job.metadata['cabinet_fabrication_snapshot']['operations_review_required'])
            self.assertNotIn('stock_verification', job.metadata)
            self.assertNotIn('cabinet_tab_plans', job.metadata)
            self.assertTrue(all(e.metadata.get('machinable') for e in job.entities))
            blocked = independent.handle(dict(command='manufacturing.export', params={}, revision=independent.revision))
            self.assertFalse(blocked['ok'])
        self.assertEqual(original, self.b.session.project.to_dict())

    def test_machine_space_keepouts_and_stock_not_translated_with_parts(self):
        d, _, s = self.fixture()
        # Physical fixtures are machine-space zones, not drawing openings.
        candidate = self.b.cabinet_draft.cached['project']
        zone = KeepoutZone('Clamp', [Point(60, 0), Point(61, 0), Point(61, 1), Point(60, 1)])
        candidate.keepouts = [zone]
        candidate.metadata['sheet_layout'] = dict(width=96., height=48.)
        p = dict(token=d['token'], settings=s, private_tool=None)
        r = self.ok('design.cabinet_fabrication_jobs_preview', **p)['review']; exported = self.export_pair(p, r)
        for path in exported['paths']:
            job = CamProject.load(path)
            self.assertEqual(job.keepouts[0].to_dict(), zone.to_dict())
            self.assertEqual(job.metadata['sheet_layout'], candidate.metadata['sheet_layout'])
            self.assertNotEqual(job.metadata['cabinet_fabrication_snapshot']['assembly_to_job_translation'], [0, 0])

    def test_generated_pair_accepts_normal_manufacturing_operations_and_native_roundtrip(self):
        for f in (1., 25.4):
            p, r = self.proposal(f); exported = self.export_pair(p, r)
            for path in exported['paths']:
                independent = Bridge(self.root / 'manufacturing-prefs.json')
                self.addCleanup(independent.manufacturing.close)
                def command(command_name, **params):
                    result = independent.handle(dict(command=command_name, params=params, revision=independent.revision))
                    self.assertTrue(result['ok'], result)
                    return result
                command('project.open', path=path)
                job = independent.session.project
                before = copy.deepcopy(job.to_dict()); history = independent.session._history_index
                # Ordinary operation creation uses the exported real contours, not references.
                contour = next(e for e in job.entities if e.layer.endswith('_CUT'))
                command('manufacturing.profile_save', name='Reviewed outside', tool_station=2,
                    entity_ids=[contour.id], target_depth=.05*f, safe_z=.5*f, stepdown=.05*f, side='outside', cut_direction='climb')
                self.assertEqual(independent.session._history_index, history+1)
                after = copy.deepcopy(independent.session.project.to_dict())
                command('history.undo'); self.assertEqual(independent.session.project.to_dict(), before)
                command('history.redo'); self.assertEqual(independent.session.project.to_dict(), after)
                slots = [e.id for e in independent.session.project.entities if e.layer == 'CABINET_SLOTS']
                if slots:
                    command('manufacturing.fill_save', name='Slot cleanout', entity_ids=slots, island_entity_ids=[], tool_station=2, target_depth=.05*f, safe_z=.5*f, stepdown=.05*f, stepover=.04*f, cut_direction='climb')
                bundle, errors = generate_toolpath_preview_bundle(independent.session.project)
                self.assertFalse(errors, errors); self.assertTrue(bundle)
                self.assertTrue(all(toolpath.motions for toolpath in bundle.values()))
                command('project.save', path=str(self.root / 'independent-edited.camjob.json'))
                self.assertEqual(CamProject.load(self.root / 'independent-edited.camjob.json').to_dict(), independent.session.project.to_dict())
                # Native part creation never supplies a reviewed motion/export token.
                blocked = independent.handle(dict(command='manufacturing.export', params=dict(token=r['review_token'], reviewed=True, name='no-motion-review'), revision=independent.revision))
                self.assertFalse(blocked['ok'])

for name in dir(Base):
    if name.startswith('test_'): setattr(CabinetFabricationJobsTests, name, None)
