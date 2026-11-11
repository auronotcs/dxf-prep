import copy,json
from test_manufacturing_bridge import ManufacturingTests as Base


class ScopeSyncGuardTests(Base):
    def test_imported_small_centers_and_cabinet_registration_remain_view_only(self):
        for units in ('in','mm'):
            for kind in ('small_centers','cabinet'):
                self.fixture(units);p=self.b.session.project
                if kind=='small_centers':p.operations[0].small_center_cleanout_ids=[p.entities[0].id]
                else:
                    p.operations=[];p.metadata['cabinet_tab_plans']={'imported-plan':{'saved':'registration data'}}
                payload=copy.deepcopy(p.to_dict());path=self.root/(kind+units+'.camjob.json')
                path.write_text(json.dumps(payload),encoding='utf-8');original=path.read_bytes()
                state=self.run_command('project.open',path=str(path))['state']
                self.assertTrue(state['readOnly']);self.assertFalse(state['manufacturing']['editable'])
                if kind=='cabinet':self.assertIn('Cabinet-tab',state['readOnlyReason'])
                for command,params in [('project.save',{'path':str(path)}),('manufacturing.setup',{'material':'Different'}),('geometry.delete',{})]:
                    self.assertFalse(self.b.handle(dict(command=command,params=params,revision=self.b.revision))['ok'])
                    self.assertEqual(payload,self.b.session.project.to_dict());self.assertEqual(original,path.read_bytes())
                self.run_command('selection.set',ids=[e.id for e in self.b.session.project.entities]);self.run_command('project.new')
                self.assertFalse(self.b.snapshot()['readOnly'])


for name in dir(Base):
    if name.startswith('test_'):setattr(ScopeSyncGuardTests,name,None)
