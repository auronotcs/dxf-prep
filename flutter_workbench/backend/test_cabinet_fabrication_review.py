import copy
from dataclasses import asdict
from test_pan_cabinet_authoring import PanCabinetAuthoringTests as Base
from cam_core.model import Point,Rectangle,Circle,Tool,ToolLibrary,ToolKind
from cam_core.cabinet_tab_mounting import CabinetTabPlan
from cam_core.cabinet_tab_production import FabricationSettings,inspect_fabrication,SETTINGS_KEY

class CabinetFabricationReviewTests(Base):
    def fixture(self,f=1.,saved=None):
        j=self.source(poly=True,f=f)
        j.entities.extend([Rectangle(id='top',corner_a=Point(20*f,0.),corner_b=Point(40*f,10*f)),Circle(id='opening',center=Point(30*f,8*f),radius=.25*f)])
        j.tool_library=ToolLibrary([Tool(1,'Slot cutter',.125*f,cutting_edge_length=.75*f)])
        self.b.snapshot();c,p=self.cabinet();p['planes'][0]['plan_origin']=dict(x=22*f,y=5*f);p['width_clearance_total']=.02*f;p['thickness_clearance_total']=.02*f
        if saved is not None:
            j.metadata[SETTINGS_KEY]={p['plan_id']:saved};c=self.ok('design.cabinet_begin')['context'];p['plan_id']=c['new_id'];j.metadata[SETTINGS_KEY]={p['plan_id']:saved}
            # Context must be issued only after the final metadata edit.
            self.b.cabinet_draft.context['digest']=self.b.digest()
        d=self.cab_preview(c,p)
        settings=dict(top_id='top',keepout_ids=['opening'],tool_station=1,top_thickness=.063*f,edge_clearance=.125*f,minimum_web=.125*f,registration_reviewed=True,fit_reviewed=True)
        return d,p,settings
    def inspect(self,d,s):return self.ok('design.cabinet_fabrication_inspect',token=d['token'],settings=s)['review']
    def test_readonly_direct_core_parity_units_and_preview_groups(self):
        for f in (1.,25.4):
            d,p,s=self.fixture(f);before=copy.deepcopy(self.b.session.project.to_dict());h=self.b.session._history_index;rev=self.b.revision;selected=set(self.b.session.selected_ids)
            info=self.ok('design.cabinet_fabrication_begin',token=d['token'])['review'];self.assertEqual({x['id'] for x in info['candidates']},{'top','opening'});self.assertFalse(info['settings']['registration_reviewed']);self.assertFalse(info['settings']['fit_reviewed'])
            r=self.inspect(d,s);self.assertTrue(r['geometry_checks_passed'],r['issues']);self.assertFalse(r['can_export'])
            expected=inspect_fabrication(self.b.cabinet_draft.cached['project'],CabinetTabPlan.from_dict(p),FabricationSettings(**dict(s,keepout_ids=tuple(s['keepout_ids']))))
            self.assertEqual(r['issues'],[asdict(i) for i in expected.issues]);slots=[{k:v for k,v in e.items() if k!='review_role'} for e in r['views']['plan'] if e['review_role']=='slot'];self.assertEqual(slots,[e.to_dict() for e in expected.slots]);self.assertTrue(r['views']['back']);self.assertAlmostEqual(r['slot_end_extension'],.135*f)
            self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(h,self.b.session._history_index);self.assertEqual(rev,self.b.revision);self.assertEqual(selected,self.b.session.selected_ids)
            self.apply_cab(d);self.roundtrip()
    def test_unassigned_opening_location_and_keepout_collision(self):
        d,p,s=self.fixture();s['keepout_ids']=[];r=self.inspect(d,s);issue=next(i for i in r['issues'] if i['code']=='unassigned');self.assertEqual(issue['entity_ids'],('opening',));self.assertEqual(issue['point'],dict(x=30.,y=8.));self.assertTrue(any(e['id']=='opening' for e in r['views']['plan']));self.assertFalse(r['geometry_checks_passed'])
        j=self.b.session.project;j.entities[-1].center=Point(26.,5.);c,p=self.cabinet();p['planes'][0]['plan_origin']=dict(x=22.,y=5.);p['width_clearance_total']=p['thickness_clearance_total']=.02;d=self.cab_preview(c,p);s['keepout_ids']=['opening'];r=self.inspect(d,s)
        hit=next(i for i in r['issues'] if i['code']=='keepout_hit');self.assertEqual(hit['pair_id'],'pair1');self.assertIn('opening',hit['entity_ids']);self.assertIsNotNone(hit['point']);self.assertFalse(r['geometry_checks_passed'])
    def test_fit_tool_edge_and_acknowledgment_findings(self):
        d,p,s=self.fixture();s.update(registration_reviewed=False,fit_reviewed=False,edge_clearance=6);r=self.inspect(d,s);self.assertTrue({'registration','fit','edge','skin'}<={i['code'] for i in r['issues']})
        s.update(tool_station=-1,top_id='');r=self.inspect(d,s);self.assertTrue({'tool','top'}<={i['code'] for i in r['issues']})
        self.b.session.project.tool_library.tools[0].diameter=.5;c,p=self.cabinet();p['planes'][0]['plan_origin']=dict(x=22.,y=5.);d=self.cab_preview(c,p);s.update(tool_station=1,top_id='top');r=self.inspect(d,s);self.assertIn('tool_fit',{i['code'] for i in r['issues']})
    def test_stale_replaced_applied_and_invalid_requests_atomic(self):
        d,p,s=self.fixture()
        for patch in [dict(top_thickness=float('nan')),dict(tool_station=True),dict(registration_reviewed=1),dict(keepout_ids=['opening',3]),dict(extra=1)]:self.reject('design.cabinet_fabrication_inspect',token=d['token'],settings=dict(s,**patch))
        self.reject('design.cabinet_fabrication_inspect',token='old',settings=s)
        self.b.session.project.entities[0].points[2]=Point(13.,8.);self.reject('design.cabinet_fabrication_inspect',token=d['token'],settings=s)
        d,p,s=self.fixture();self.apply_cab(d);self.reject('design.cabinet_fabrication_begin',token=d['token'])
        d,p,s=self.fixture();self.ok('design.cabinet_begin');self.reject('design.cabinet_fabrication_inspect',token=d['token'],settings=s)
    def test_saved_approvals_reset_and_invalid_source_choice(self):
        saved=dict(top_id='top',keepout_ids=['opening'],tool_station=1,top_thickness=.1,edge_clearance=.2,minimum_web=.3,registration_reviewed=True,fit_reviewed=True)
        d,p,s=self.fixture(saved=saved);info=self.ok('design.cabinet_fabrication_begin',token=d['token'])['review'];self.assertFalse(info['settings']['registration_reviewed']);self.assertFalse(info['settings']['fit_reviewed']);self.assertEqual(info['settings']['top_thickness'],.1)
        s['top_id']='face';r=self.inspect(d,s);self.assertIn('source_choice',{i['code'] for i in r['issues']});self.assertFalse(r['geometry_checks_passed'])
    def test_no_export_or_apply_fabrication_command(self):
        d,p,s=self.fixture();self.assertTrue(self.inspect(d,s)['geometry_checks_passed'])
        self.reject('design.cabinet_fabrication_export',token=d['token'],settings=s)
        self.reject('design.cabinet_fabrication_apply',token=d['token'],settings=s)

    def test_malformed_saved_settings_do_not_escape_as_nonfinite_json(self):
        import json
        saved=dict(top_id='top',keepout_ids=['opening'],tool_station=1,top_thickness=float('nan'),edge_clearance=.2,minimum_web=.3,registration_reviewed=True,fit_reviewed=True)
        # Inject only into the private proposal; live document is still a valid JSON job.
        d,p,options=self.fixture();self.b.cabinet_draft.cached['project'].metadata[SETTINGS_KEY]={p['plan_id']:saved}
        info=self.ok('design.cabinet_fabrication_begin',token=d['token'])['review'];self.assertTrue(info['warning']);self.assertEqual(info['settings']['top_thickness'],.063);json.dumps(info,allow_nan=False)

for name in dir(Base):
    if name.startswith('test_'):setattr(CabinetFabricationReviewTests,name,None)
