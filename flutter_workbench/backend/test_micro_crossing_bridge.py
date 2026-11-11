import copy,json,tempfile,unittest
from pathlib import Path
from design_bridge import Bridge
from cam_core.model import CamProject,Point,Polyline,Circle,RouterOffsetOperation
from cam_core.units import unit_scale
from cam_core.micro_crossings import micro_crossings,remove_micro_crossings
from cam_core.preflight import _self_intersects,_first_self_intersection
from cam_core.fragment_repair import propose_output_contour_repair

def loop(scale=1,error=.0002):
    return [Point(x*scale,y*scale) for x,y in [(0,0),(4+error,0),(4,-error),(4,3),(0,3)]]

class MicroCrossingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.b=Bridge(self.root/'prefs.json',recovery_directory=self.root/'recovery')
        self.addCleanup(self.b.recovery.close);self.addCleanup(self.b.manufacturing.close)
    def call(self,command,**p):return self.b.handle(dict(command=command,params=p,revision=self.b.revision))
    def ok(self,command,**p):
        r=self.call(command,**p);self.assertTrue(r['ok'],r);json.dumps(r,allow_nan=False);return r
    def seed(self,units='in',error=.0002):
        self.ok('project.new',units=units);f=unit_scale('in',units)
        e=Polyline(id='outline',layer='CUT',closed=True,points=loop(f,error),metadata={'source_note':'keep'})
        self.b.session.project.entities=[e];self.b.session.selected_ids={e.id};self.b.snapshot();self.b.session._record_history();self.b.sync_recovery(force=True)
        return e
    def frozen(self):
        self.b.snapshot();return copy.deepcopy((self.b.session.project.to_dict(),self.b.session.selected_ids,self.b.session._history,self.b.revision))
    def preview(self):return self.ok('design.suite_preview',scope='all',include_fragments=False)['preview']
    def test_units_real_repair_history_recovery_and_native(self):
        for units in ('in','mm','cm'):
            e=self.seed(units);f=unit_scale('in',units);self.assertTrue(_self_intersects(e.points));before=self.frozen();index=self.b.session._history_index
            r=self.preview();self.assertEqual(r['count'],1);self.assertEqual(self.frozen(),before)
            self.assertAlmostEqual(r['rows'][0]['point']['x'],4*f);self.assertFalse(any(x.get('severity')=='error' for x in r['rows']))
            self.ok('design.suite_apply',token=r['token']);out=self.b.session.project.entities[0]
            self.assertEqual(out.id,e.id);self.assertEqual(out.layer,'CUT');self.assertEqual(out.metadata['source_note'],'keep');self.assertEqual(out.points,[Point(0,0),Point(4*f,0),Point(4*f,3*f),Point(0,3*f)])
            self.assertFalse(_self_intersects(out.points));self.assertEqual(self.b.session._history_index,index+1);self.assertEqual(self.b.session.selected_ids,{'outline'})
            saved=copy.deepcopy(self.b.session.project.to_dict());self.assertEqual(self.b.recovery.read(self.b.recovery.identity)[1].to_dict(),saved)
            self.ok('history.undo');self.assertEqual(self.b.session.project.to_dict(),before[0]);self.ok('history.redo');self.assertEqual(self.b.session.project.to_dict(),saved)
            path=self.root/f'crossing-{units}.camjob.json';self.ok('project.save',path=str(path));self.ok('project.open',path=str(path));self.assertEqual(CamProject.load(path).to_dict(),saved)
    def test_seam_reverse_and_open_paths_preserve_intended_edges(self):
        original=loop()
        for points,closed in [(original,True),(original[2:]+original[:2],True),(list(reversed(original)),True),(original[:4],False)]:
            repaired,count,locations,error=remove_micro_crossings(points,closed=closed)
            self.assertEqual(count,1);self.assertEqual(len(repaired),len(points)-1);self.assertEqual(len(locations),1);self.assertLessEqual(error,.0005)
            self.assertIn(Point(4,0),repaired)
            if closed:self.assertFalse(_self_intersects(repaired))
    def test_large_crossing_and_ordinary_corner_are_never_simplified(self):
        for points in (loop(error=.02),[Point(0,0),Point(4,0),Point(4,.0002),Point(4,3),Point(0,3)]):
            out,count,_,_=remove_micro_crossings(points,closed=True);self.assertEqual(count,0);self.assertEqual(out,points)
        e=self.seed(error=.02);before=self.frozen();r=self.preview();self.assertIsNone(r['token']);self.assertEqual(r['count'],0)
        row=next(x for x in r['rows'] if x.get('code')=='geometry.self_intersection');self.assertEqual(row['severity'],'error');self.assertEqual(row['ids'],[e.id]);self.assertAlmostEqual(row['point']['x'],4);self.assertEqual(self.frozen(),before)
        self.assertFalse(self.call('design.suite_apply',token='invented')['ok']);self.assertEqual(self.frozen(),before)
    def test_shared_diagnostics_find_unrepairable_crossing_and_zero_radius(self):
        self.seed();self.b.session.project.entities=[Polyline(id='bow',closed=True,points=[Point(0,0),Point(4,3),Point(0,3),Point(4,0)]),Circle(id='zero',center=Point(7,8),radius=0)]
        before=self.frozen();r=self.preview();errors={x['code']:x for x in r['rows'] if x.get('severity')=='error'}
        self.assertIsNone(r['token']);self.assertEqual(errors['geometry.self_intersection']['point'],{'x':2.0,'y':1.5});self.assertEqual(errors['geometry.zero_radius']['ids'],['zero']);self.assertEqual(errors['geometry.zero_radius']['point'],{'x':7,'y':8});self.assertEqual(self.frozen(),before)
    def test_mixed_scope_repairs_only_safe_outline_reports_remaining_error(self):
        self.seed();other=Polyline(id='manual',closed=True,points=[Point(10,0),Point(14,3),Point(10,3),Point(14,0)]);self.b.session.project.entities.append(other)
        r=self.preview();self.assertEqual(r['count'],1);self.assertTrue(any(x.get('code')=='geometry.self_intersection' and x['ids']==['manual'] for x in r['rows']))
        self.ok('design.suite_apply',token=r['token']);self.assertEqual(next(e for e in self.b.session.project.entities if e.id=='manual').to_dict(),other.to_dict())
    def test_locked_hidden_generated_and_machining_sources_are_protected(self):
        for mode in ('locked','hidden','generated','machining'):
            e=self.seed();j=self.b.session.project
            if mode=='locked':j.metadata['layer_settings']={'CUT':{'locked':True}}
            elif mode=='hidden':j.metadata['layer_settings']={'CUT':{'visible':False}}
            elif mode=='generated':e.metadata['mounting_generated']=True
            else:j.operations=[RouterOffsetOperation('Cut',1,[e.id],.1,.2)]
            before=self.frozen();r=self.call('design.suite_preview',scope='all',include_fragments=False)
            if mode=='machining':self.assertFalse(r['ok'])
            else:self.assertTrue(r['ok'],r);self.assertIsNone(r['preview']['token'])
            self.assertEqual(self.frozen(),before)
    def test_stale_review_and_changed_scope_do_not_apply(self):
        self.seed();r=self.preview();self.ok('geometry.add_circle',x=10,y=10,diameter=1);before=self.frozen();self.assertFalse(self.call('design.suite_apply',token=r['token'])['ok']);self.assertEqual(self.frozen(),before)
    def test_output_repair_cap_never_turns_large_tolerance_into_large_repair(self):
        for units in ('in','mm','cm'):
            e=self.seed(units);f=unit_scale('in',units);r=propose_output_contour_repair(self.b.session.project,{e.id},tolerance=10*f)
            self.assertEqual(len(r),1);self.assertLessEqual(r[0].maximum_error,.0005*f);self.assertFalse(r[0].remaining_issues)
            e.points=loop(f,error=.02);self.assertEqual(propose_output_contour_repair(self.b.session.project,{e.id},tolerance=10*f),[])
