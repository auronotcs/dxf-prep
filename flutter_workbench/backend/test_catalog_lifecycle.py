import copy,json
from test_catalog_matching import CatalogMatchingTests as Base
from manufacturing_tools import ManufacturingTools

class CatalogLifecycleTests(Base):
    def action(self,kind,**params):
        self.library=self.run_command('manufacturing.'+kind,id=self.row['id'],version=self.library['version'],**params)
        self.row=next(r for r in self.library['catalog'] if r['id']==self.library['selectedId'])
        return self.library

    def test_add_recipes_canonical_units_identity_and_private_job(self):
        for units in ('in','mm'):
            self.custom(units);f=25.4 if units=='mm' else 1
            original=copy.deepcopy(self.row);job=copy.deepcopy(self.b.session.project.to_dict());revision=self.b.revision
            self.action('catalog_recipe_add',units=units,recipe=dict(material='Plywood finish '+units,rpm=16000,feed=90*f,plunge=24*f,stepdown=.1*f,stepover=.05*f),
                scope=dict(material_family='plywood',thickness_min=.25*f,thickness_max=.75*f,process_roles=['finish_cutoff'],entry_strategies=['plunge']))
            added=self.row['recipes'][-1]
            self.assertAlmostEqual(added['feed'],90);self.assertAlmostEqual(added['thickness_max_in'],.75)
            self.assertEqual(added['qualification'],'prototype');self.assertTrue(added['id'].startswith('recipe-'))
            self.assertEqual(self.row['id'],original['id']);self.assertEqual(self.row['recipes'][:-1],original['recipes'])
            self.assertEqual(self.b.session.project.to_dict(),job);self.assertEqual(self.b.revision,revision)

    def test_recipe_duplicate_copies_scope_and_specialized_data_but_not_test_approval(self):
        self.matching();data=self.b.manufacturing_tools.read();data['tools'][0]['recipes'][0]['knife_preset']={'keep':'all'}
        self.b.manufacturing_tools.write(data);self.library=self.run_command('manufacturing.catalog_list')
        self.row=next(r for r in self.library['catalog'] if r.get('custom'));old=copy.deepcopy(self.row['recipes'][0])
        self.action('catalog_recipe_duplicate',recipe_index=0,name='Plywood alternate')
        new=self.row['recipes'][-1];self.assertEqual(self.row['recipes'][0],old)
        for k in ('feed','thickness_min_in','process_roles','knife_preset'):self.assertEqual(new[k],old[k])
        self.assertEqual(new['qualification'],'prototype');self.assertNotIn('qualification_evidence',new)
        self.assertNotIn('previous_qualification',new);self.assertNotEqual(new.get('id'),old.get('id'))
        data=self.b.manufacturing_tools.read();data['tools'][0]['recipes'][0].pop('material_family')
        self.b.manufacturing_tools.write(data);self.library=self.run_command('manufacturing.catalog_list')
        self.action('catalog_recipe_duplicate',recipe_index=0,name='Plywood fallback')
        self.assertEqual(self.row['recipes'][-1]['material_family'],'plywood')

    def test_remove_confirmed_recipe_preserves_other_records_and_blocks_last(self):
        self.custom();self.action('catalog_recipe_duplicate',recipe_index=0,name='Variant')
        keeper=copy.deepcopy(self.row['recipes'][1])
        before=self.b.manufacturing_tools.path.read_bytes()
        for confirm in (False,1,None):
            r=self.b.handle(dict(command='manufacturing.catalog_recipe_remove',revision=self.b.revision,params=dict(id=self.row['id'],version=self.library['version'],recipe_index=0,confirmed=confirm)))
            self.assertFalse(r['ok']);self.assertEqual(before,self.b.manufacturing_tools.path.read_bytes())
        self.action('catalog_recipe_remove',recipe_index=0,confirmed=True)
        self.assertEqual(self.row['recipes'],[keeper]);self.assertEqual(self.library['selectedRecipe'],0)
        with self.assertRaisesRegex(AssertionError,'at least one'):self.action('catalog_recipe_remove',recipe_index=0,confirmed=True)

    def test_retirement_blocks_matching_loading_and_editing_reactivation_is_prototype(self):
        self.matching();self.action('catalog_recipe_status',recipe_index=0,retired=True,confirmed=True)
        self.assertEqual(self.row['recipes'][0]['qualification'],'retired')
        matches=self.run_command('manufacturing.catalog_matches',**self.args)
        self.assertFalse(matches['matches'][0]['eligible'])
        with self.assertRaisesRegex(AssertionError,'retired'):self.run_command('manufacturing.catalog_resolve',id=self.row['id'],version=self.library['version'],recipe=0,station=1)
        with self.assertRaisesRegex(AssertionError,'Reactivate'):self.update(recipe=dict(feed=88))
        self.action('catalog_recipe_status',recipe_index=0,retired=False,confirmed=True)
        r=self.row['recipes'][0];self.assertEqual(r['qualification'],'prototype');self.assertNotIn('qualification_evidence',r)
        self.assertTrue(self.run_command('manufacturing.catalog_matches',**self.args)['matches'][0]['eligible'])

    def test_other_retired_recipe_stays_retired_on_cutter_edits(self):
        self.custom();self.action('catalog_recipe_duplicate',recipe_index=0,name='Retired variant')
        self.action('catalog_recipe_status',recipe_index=1,retired=True,confirmed=True)
        self.update(tool=dict(diameter=.3))
        self.assertEqual(self.row['recipes'][1]['qualification'],'retired')

    def test_tool_duplicate_new_identity_preserves_cutter_and_resets_all_qualification(self):
        self.matching();old=copy.deepcopy(next(t for t in self.library['catalog'] if t['id']==self.row['id']))
        self.action('catalog_tool_duplicate',name='New cutter copy')
        self.assertNotEqual(self.row['id'],old['id']);self.assertEqual(self.library['selectedRecipe'],0)
        for k,v in old.items():
            if k not in {'id','name','recipes'}:self.assertEqual(self.row[k],v)
        self.assertEqual(self.row['recipes'][0]['qualification'],'prototype')
        original=next(t for t in self.library['catalog'] if t['id']==old['id']);self.assertEqual(original,old)

    def test_invalid_and_stale_actions_leave_library_bytes_and_job_unchanged(self):
        self.custom();lib=self.b.manufacturing_tools;before=lib.path.read_bytes();job=copy.deepcopy(self.b.session.project.to_dict())
        cases=[('catalog_recipe_add',dict(units='mm',recipe=dict(material='Bad',rpm=18000,feed=float('nan'),plunge=30,stepdown=1))),
            ('catalog_recipe_duplicate',dict(recipe_index=True,name='Variant')),('catalog_recipe_duplicate',dict(recipe_index=0,name='Saved station settings')),
            ('catalog_recipe_status',dict(recipe_index=0,retired='true',confirmed=True)),('catalog_tool_duplicate',dict(name=' ')),
            ('catalog_tool_duplicate',dict(name='Copy',tool={'diameter_in':999})),('catalog_recipe_add',dict(units='in',recipe={},qualification='shop_tested'))]
        for action,params in cases:
            r=self.b.handle(dict(command='manufacturing.'+action,revision=self.b.revision,params=dict(id=self.row['id'],version=self.library['version'],**params)))
            self.assertFalse(r['ok'],r);self.assertEqual(lib.path.read_bytes(),before);self.assertEqual(self.b.session.project.to_dict(),job)
        version=self.library['version'];self.action('catalog_tool_duplicate',name='Peer copy');after=lib.path.read_bytes()
        peer=ManufacturingTools(lib.path,lib.seed)
        with self.assertRaisesRegex(ValueError,'changed'):peer.command('catalog_recipe_remove',dict(id=self.row['id'],version=version,recipe_index=0,confirmed=True),self.b.session.project)
        self.assertEqual(after,lib.path.read_bytes())

    def test_loaded_recipe_survives_retire_duplicate_remove_save_reopen_and_output(self):
        for units in ('in','mm'):
            matches=self.matching(units);tool=self.load(matches)['tool']
            self.apply_workflow(self.workflow('turret',dict(tools=[tool],minimum_rpm=8000,maximum_rpm=24000)))
            before=copy.deepcopy(self.b.session.project.to_dict());self.library=self.run_command('manufacturing.catalog_list')
            self.action('catalog_recipe_duplicate',recipe_index=0,name='Future variant')
            self.action('catalog_recipe_status',recipe_index=0,retired=True,confirmed=True)
            self.action('catalog_recipe_remove',recipe_index=0,confirmed=True)
            self.assertEqual(before,self.b.session.project.to_dict())
            self.assertEqual(self.b.session.project.tool_library.tools[0].metadata['loaded_recipe']['qualification'],'shop_tested')
            self.parity('recipe-lifecycle-'+units)

for name in dir(Base):
    if name.startswith('test_'):setattr(CatalogLifecycleTests,name,None)
