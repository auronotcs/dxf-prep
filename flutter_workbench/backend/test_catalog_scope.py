import copy
from test_catalog_matching import CatalogMatchingTests as Base


class CatalogScopeTests(Base):
    def test_units_scope_private_until_reviewed_turret_history_save_output(self):
        for units in ('in','mm'):
            self.matching(units);f=25.4 if units=='mm' else 1
            before=copy.deepcopy(self.p.to_dict());revision=self.b.revision;identity=self.row['id']
            self.update(scope=dict(material_family='Plywood',thickness_min=.2*f,thickness_max=.8*f,
                                   process_roles=['outside_profile'],entry_strategies=['plunge']))
            r=self.row['recipes'][0]
            self.assertEqual(self.row['id'],identity);self.assertAlmostEqual(r['thickness_min_in'],.2)
            self.assertAlmostEqual(r['thickness_max_in'],.8);self.assertEqual(r['qualification'],'prototype')
            self.assertEqual(before,self.p.to_dict());self.assertEqual(revision,self.b.revision)
            matches=self.run_command('manufacturing.catalog_matches',**self.args)
            tool=self.load(matches)['tool'];self.assertAlmostEqual(tool['metadata']['loaded_recipe']['thickness_max'],.8*f)
            self.apply_workflow(self.workflow('turret',dict(tools=[tool],minimum_rpm=8000,maximum_rpm=24000)))
            self.parity('scoped-'+units)

    def test_roles_entries_material_and_bounds_change_actual_matching(self):
        self.matching()
        for scope,eligible in [(dict(process_roles=['pocket_fill']),False),
            (dict(process_roles=['outside_profile'],entry_strategies=['helical']),False),
            (dict(entry_strategies=['plunge']),True),(dict(material_family='Aluminium'),False),
            (dict(material_family='plywood',thickness_max=.4),False),(dict(thickness_max=.6),True)]:
            self.update(scope=scope)
            match=next(r for r in self.run_command('manufacturing.catalog_matches',**self.args)['matches'] if r['id']==self.row['id'])
            self.assertEqual(match['eligible'],eligible,scope)

    def test_invalid_scope_is_atomic(self):
        self.matching();lib=self.b.manufacturing_tools;before=lib.path.read_bytes()
        cases=[dict(thickness_min=v) for v in (0,-1,True,float('inf'),float('nan'),'1')]
        cases += [dict(thickness_min=2,thickness_max=1),dict(process_roles=[]),dict(process_roles=['any','outside_profile']),
            dict(process_roles=['made_up']),dict(process_roles='outside_profile'),dict(entry_strategies=['helix']),
            dict(entry_strategies=[True]),dict(entry_strategies=None),dict(material_family=12),dict(unknown='value')]
        for scope in cases:
            with self.subTest(scope=scope),self.assertRaises((ValueError,TypeError)):
                lib.command('catalog_update',dict(id=self.row['id'],version=self.library['version'],units='in',recipe_index=0,scope=scope),self.p)
            self.assertEqual(before,lib.path.read_bytes())

    def test_scope_changes_reset_evidence_but_semantic_and_metric_roundtrips_do_not(self):
        self.matching('mm')
        self.update(scope=dict(material_family='plywood',thickness_min=.25*25.4,thickness_max=.75*25.4,process_roles=['outside_profile'],entry_strategies=[]))
        self.assertEqual(self.row['recipes'][0]['qualification'],'shop_tested');self.assertEqual(self.library['downgradedRecipes'],[])
        self.update(scope=dict(process_roles=['outside_profile','inside_profile']),qualification='shop_tested',evidence='Old test')
        self.assertEqual(self.row['recipes'][0]['qualification'],'prototype')
        self.assertIn('previous_qualification',self.row['recipes'][0])
        self.update(qualification='shop_tested',evidence='Fresh test for both roles')
        self.update(scope=dict(process_roles=['inside_profile','outside_profile','inside_profile']))
        self.assertEqual(self.library['downgradedRecipes'],[])
        self.update(scope=dict(material_family='Aluminium'))
        self.update(qualification='shop_tested',evidence='New aluminum coupon')
        self.update(scope=dict(material_family='aluminum'))
        self.assertEqual(self.row['recipes'][0]['qualification'],'shop_tested')

    def test_stale_matches_and_peer_edits_refused_after_scope_change(self):
        matches=self.matching();old_version=self.library['version']
        self.update(scope=dict(thickness_max=.8));before=self.b.manufacturing_tools.path.read_bytes()
        with self.assertRaisesRegex(AssertionError,'changed'):self.load(matches)
        with self.assertRaisesRegex(ValueError,'changed'):
            self.b.manufacturing_tools.command('catalog_update',dict(id=self.row['id'],version=old_version,units='in',recipe_index=0,scope=dict(thickness_max=.9)),self.p)
        self.assertEqual(before,self.b.manufacturing_tools.path.read_bytes())

    def test_cleared_bounds_drop_derived_unit_values_and_leave_other_recipe_metadata(self):
        self.matching();lib=self.b.manufacturing_tools;data=lib.read();r=data['tools'][0]['recipes'][0]
        r.update(thickness_min=9,thickness_max=99,shop_note='Keep this');lib.write(data)
        self.library=self.run_command('manufacturing.catalog_list')
        self.update(scope=dict(thickness_min=None,thickness_max=None,entry_strategies=[]))
        matches=self.run_command('manufacturing.catalog_matches',**self.args)
        loaded=self.load(matches)['tool']['metadata']['loaded_recipe']
        self.assertNotIn('thickness_min',loaded);self.assertNotIn('thickness_max',loaded)
        self.assertEqual(loaded['shop_note'],'Keep this')


for name in dir(Base):
    if name.startswith('test_'):setattr(CatalogScopeTests,name,None)
