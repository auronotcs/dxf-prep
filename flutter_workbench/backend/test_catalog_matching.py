import copy
from test_catalog_edit import CatalogEditTests as Base
from catalog_matching import list_matches, resolve


class CatalogMatchingTests(Base):
    def matching(self, units='in'):
        self.custom(units)
        self.update(recipe=dict(material='Plywood', feed=90*(25.4 if units=='mm' else 1)))
        lib=self.b.manufacturing_tools;data=lib.read()
        recipe=data['tools'][0]['recipes'][0]
        recipe.update(material_family='plywood',thickness_min_in=.25,thickness_max_in=.75,
                      process_roles=['outside_profile'],qualification='shop_tested',qualification_evidence='Isolated coupon test')
        lib.write(data)
        self.library=self.run_command('manufacturing.catalog_list')
        self.p=self.b.session.project
        self.args=dict(station=1,process_role='outside_profile')
        return self.run_command('manufacturing.catalog_matches',**self.args)

    def load(self, matches, **changes):
        args=dict(self.args,version=matches['version'],token=matches['token'],id=self.row['id'],recipe=0,reviewed=True)
        args.update(changes)
        return self.run_command('manufacturing.catalog_match_resolve',**args)

    def test_match_resolve_private_physical_identity_history_output_both_units(self):
        for units in ('in','mm'):
            matches=self.matching(units);f=25.4 if units=='mm' else 1
            self.assertTrue(matches['matches'][0]['eligible'])
            self.assertEqual(matches['matches'][0]['qualification'],'shop_tested')
            self.assertTrue(matches['matches'][0]['warnings'])
            self.p.tool_library.tools[0].metadata['calibration']='preserve calibration'
            path=str(self.root/('calibrated-'+units+'.camjob.json'))
            self.run_command('project.save',path=path);self.run_command('project.open',path=path)
            self.p=self.b.session.project
            matches=self.run_command('manufacturing.catalog_matches',**self.args)
            before=copy.deepcopy(self.p.to_dict());rev=self.b.revision
            tool=self.load(matches)['tool']
            self.assertEqual(before,self.p.to_dict());self.assertEqual(rev,self.b.revision)
            self.assertAlmostEqual(tool['feed_rate'],1.5*f)
            self.assertEqual(tool['metadata']['calibration'],'preserve calibration')
            for key in ('diameter','cutting_edge_length','shank_diameter','overall_length','holder_diameter','stickout','min_rpm','max_rpm'):
                self.assertEqual(tool[key],before['tool_library']['tools'][0][key])
            self.assertEqual(tool['metadata']['loaded_recipe']['qualification'],'shop_tested')
            self.assertAlmostEqual(tool['metadata']['loaded_recipe']['thickness_max'],.75*f)
            self.apply_workflow(self.workflow('turret',dict(tools=[tool],minimum_rpm=8000,maximum_rpm=24000)))
            self.assertEqual(before['operations'],self.b.session.project.to_dict()['operations'])
            self.parity('matched-'+units)

    def test_stale_job_library_cutter_and_explicit_review_refused(self):
        for change in ('stock','cutter','library','review'):
            matches=self.matching()
            if change=='stock':self.p.metadata['job_setup']['material_thickness']=.4
            if change=='cutter':self.p.tool_library.tools[0].diameter=.2
            if change=='library':self.update(tool=dict(name='Changed by another tab'))
            before=copy.deepcopy(self.p.to_dict())
            with self.assertRaisesRegex(AssertionError,'changed|Review recipe'):
                self.load(matches,reviewed=change!='review')
            self.assertEqual(before,self.p.to_dict())

    def test_exclusions_scope_geometry_roles_retired_and_operation_specific(self):
        self.matching();row=copy.deepcopy(self.row)
        # Use the enriched record, not the earlier editor response.
        row=self.b.manufacturing_tools.read()['tools'][0]
        cases=[('material_family','aluminum','material'),('thickness_max_in',.3,'thickness'),
               ('process_roles',['pocket_fill'],'role'),('qualification','retired','retired'),
               ('cut_parameters',{'target_depth':.25},'operation-specific'),
               ('entry_strategies',['helix'],'Entry strategy'),('feed',0,'Invalid')]
        for key,value,reason in cases:
            entry=copy.deepcopy(row);entry['recipes'][0][key]=value
            result=list_matches(self.p,[entry],'v',self.args)['matches'][0]
            self.assertFalse(result['eligible'],key);self.assertIn(reason,' '.join(result['reasons']))
        entry=copy.deepcopy(row);entry['diameter_in']=.125
        self.assertFalse(list_matches(self.p,[entry],'v',self.args)['matches'][0]['eligible'])
        entry=copy.deepcopy(row);entry.pop('diameter_in')
        self.assertIn('Catalog cutter diameter',' '.join(list_matches(self.p,[entry],'v',self.args)['matches'][0]['reasons']))
        self.p.operations[0].side='inside'
        self.assertIn('incompatible operation',' '.join(list_matches(self.p,[row],'v',self.args)['matches'][0]['reasons']))

    def test_role_material_alias_range_boundaries_and_machine_limits(self):
        self.matching('mm');row=self.b.manufacturing_tools.read()['tools'][0]
        row['recipes'][0]['material_family']='aluminum';self.p.metadata['job_setup']['material']='Aluminium'
        for thickness in (.25,.75):
            self.p.metadata['job_setup']['material_thickness']=thickness*25.4
            self.assertTrue(list_matches(self.p,[row],'v',self.args)['matches'][0]['eligible'])
        self.p.metadata['job_setup']['machine_max_rpm']=12000
        self.assertIn('RPM',' '.join(list_matches(self.p,[row],'v',self.args)['matches'][0]['reasons']))

    def test_missing_stock_station_unsupported_tool_and_wrong_choice(self):
        matches=self.matching()
        with self.assertRaisesRegex(AssertionError,'applicable'):self.load(matches,id='missing')
        with self.assertRaisesRegex(AssertionError,'applicable'):self.load(matches,recipe=True)
        for args in (dict(station=2,process_role='outside_profile'),dict(station=True,process_role='outside_profile'),dict(station=1,process_role='any')):
            with self.assertRaises(ValueError):list_matches(self.p,[],'v',args)
        self.p.metadata['job_setup']['material']='Anything'
        with self.assertRaisesRegex(ValueError,'actual stock'):list_matches(self.p,[],'v',self.args)

    def test_loaded_recipe_is_frozen_and_override_is_visible(self):
        matches=self.matching();self.p.operations[0].feed_rate_override=1.2
        matches=self.run_command('manufacturing.catalog_matches',**self.args)
        item=next(r for r in matches['matches'] if r['id']==self.row['id'])
        self.assertIn('overrides',' '.join(item['warnings']))
        tool=self.load(matches)['tool'];saved=copy.deepcopy(tool)
        self.update(recipe=dict(feed=100))
        self.assertEqual(tool,saved)


for name in dir(Base):
    if name.startswith('test_'):setattr(CatalogMatchingTests,name,None)
