import copy
from test_manufacturing_tools import ManufacturingToolTests as Base
from manufacturing_tools import ManufacturingTools


class CatalogEditTests(Base):
    def custom(self, units='in'):
        self.fixture(units)
        self.library = self.run_command('manufacturing.catalog_list')
        self.library = self.run_command('manufacturing.catalog_save', station=1, version=self.library['version'])
        self.row = next(t for t in self.library['catalog'] if t.get('custom'))

    def update(self, **kwargs):
        self.library = self.run_command('manufacturing.catalog_update', id=self.row['id'],
            version=self.library['version'], units=self.b.session.project.units,
            recipe_index=0, **kwargs)
        self.row = next(t for t in self.library['catalog'] if t['id'] == self.row['id'])
        return self.row

    def test_unit_roundtrip_identity_private_until_reviewed_turret_output(self):
        for units in ('in', 'mm'):
            self.custom(units);f=25.4 if units=='mm' else 1
            before=copy.deepcopy(self.b.session.project.to_dict());revision=self.b.revision
            identity=self.row['id']
            self.update(tool=dict(name='Edited reusable cutter',diameter=.25*f),
                        recipe=dict(feed=90*f,plunge=24*f,stepdown=.05*f,stepover=.1*f))
            self.assertEqual(self.row['id'],identity)
            self.assertAlmostEqual(self.row['recipes'][0]['feed'],90)
            self.assertAlmostEqual(self.row['recipes'][0]['stepdown'],.05)
            self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(revision,self.b.revision)
            resolved=self.run_command('manufacturing.catalog_resolve',id=identity,version=self.library['version'],station=1,recipe=0)['tool']
            self.assertAlmostEqual(resolved['feed_rate'],1.5*f)
            self.assertEqual(before,self.b.session.project.to_dict())
            self.apply_workflow(self.workflow('turret',dict(tools=[resolved],minimum_rpm=8000,maximum_rpm=24000)))
            self.parity('catalog-edit-'+units)
            self.assertEqual(self.b.session.project.tool_library.tools[0].name,'Edited reusable cutter')

    def test_qualification_needs_evidence_then_resets_and_requires_separate_save(self):
        self.custom()
        before=self.b.manufacturing_tools.path.read_bytes()
        with self.assertRaisesRegex(AssertionError,'evidence'):
            self.update(qualification='shop_tested',evidence='')
        self.assertEqual(before,self.b.manufacturing_tools.path.read_bytes())
        self.update(qualification='shop_tested',evidence='2026-10-29; plywood .5 in; test router; vacuum; clean coupon.')
        self.assertEqual(self.row['recipes'][0]['qualification'],'shop_tested')
        self.update(recipe=dict(feed=111),qualification='shop_tested',evidence='Old evidence cannot approve a changed input')
        self.assertEqual(self.row['recipes'][0]['qualification'],'prototype')
        self.assertNotIn('qualification_evidence',self.row['recipes'][0])
        self.assertEqual(self.library['downgradedRecipes'],[0])
        self.update(qualification='shop_tested',evidence='New coupon tested at 111 in/min; same stock/setup; clean cut.')
        self.update(tool=dict(name='Renamed without cutting change'))
        self.assertEqual(self.row['recipes'][0]['qualification'],'shop_tested')

    def test_geometry_resets_all_recipes_and_preserves_unedited_metadata(self):
        self.custom();lib=self.b.manufacturing_tools;data=lib.read()
        extra=copy.deepcopy(data['tools'][0]['recipes'][0]);extra.update(material='Other material',qualification='shop_tested',qualification_evidence='Coupon B')
        data['tools'][0]['recipes'].append(extra);data['tools'][0]['notes']='Keep notes'
        lib.write(data);self.library=self.run_command('manufacturing.catalog_list')
        self.update(qualification='shop_tested',evidence='Coupon A')
        self.update(tool=dict(diameter=.24))
        self.assertEqual(self.library['downgradedRecipes'],[0,1])
        self.assertTrue(all(r['qualification']=='prototype' for r in self.row['recipes']))
        self.assertEqual(self.row['notes'],'Keep notes')
        reopened=ManufacturingTools(lib.path,lib.seed)
        self.assertEqual(reopened.read(),lib.read())

    def test_stale_cross_process_version_and_seed_protection_leave_bytes_unchanged(self):
        self.custom();old_version=self.library['version'];lib=self.b.manufacturing_tools
        self.update(tool=dict(name='Other tab edit'))
        before=lib.path.read_bytes()
        peer=ManufacturingTools(lib.path,lib.seed)
        with self.assertRaisesRegex(ValueError,'changed'):
            peer.command('catalog_update',dict(id=self.row['id'],version=old_version,units='in',recipe_index=0,tool=dict(name='Lost update')),self.b.session.project)
        seed=next(t for t in self.library['catalog'] if not t.get('custom'))
        with self.assertRaisesRegex(ValueError,'custom'):
            peer.command('catalog_update',dict(id=seed['id'],version=self.library['version'],units='in',recipe_index=0),self.b.session.project)
        self.assertEqual(before,lib.path.read_bytes())

    def test_invalid_numbers_limits_and_fields_are_atomic(self):
        self.custom();lib=self.b.manufacturing_tools;before=lib.path.read_bytes()
        cases=[dict(tool=dict(diameter=x)) for x in (0,-1,True,float('nan'),float('inf'))]
        cases += [dict(recipe=dict(rpm=18000.5)),dict(recipe=dict(feed=0)),dict(recipe=dict(stepover=4)),
                  dict(recipe=dict(stepdown=20)),dict(tool=dict(min_rpm=20000,max_rpm=10000)),
                  dict(tool=dict(kind='drill')),dict(qualification='machine_approved'),dict(units='cm'),
                  dict(recipe_index=True),dict(recipe=dict(rpm=999999)),dict(tool=dict(name=''))]
        for change in cases:
            params=dict(id=self.row['id'],version=self.library['version'],units='in',recipe_index=0,**{})
            params.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):lib.command('catalog_update',params,self.b.session.project)
            self.assertEqual(before,lib.path.read_bytes())

    def test_metric_unchanged_roundtrip_keeps_test_record(self):
        self.custom('mm')
        self.update(qualification='shop_tested',evidence='Metric physical coupon recorded.')
        self.update(tool=dict(diameter=self.row['diameter_in']*25.4),
            recipe={k:self.row['recipes'][0][k]*25.4 for k in ('feed','plunge','stepdown')})
        self.assertEqual(self.row['recipes'][0]['qualification'],'shop_tested')
        self.assertEqual(self.library['downgradedRecipes'],[])


for name in dir(Base):
    if name.startswith('test_'):setattr(CatalogEditTests,name,None)
