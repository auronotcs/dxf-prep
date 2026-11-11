import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from design_bridge import Bridge
from cam_core.box_generator import MailerOptions, plan_mailer, mailer_entities
from cam_core.layout import entity_bounds
from cam_core.model import CamProject, Tool, ToolKind, RouterOffsetOperation
from cam_core.units import unit_scale
from cam_core.toolpath_background import generate_toolpath_preview_bundle

class MailerAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.b=Bridge(self.root/'prefs.json'); self.addCleanup(self.b.manufacturing.close)
        self.ok('project.new')
    def call(self,verb,**params): return self.b.handle(dict(command=verb,params=params,revision=self.b.revision))
    def ok(self,verb,**params):
        r=self.call(verb,**params); self.assertTrue(r['ok'],r); json.dumps(r,allow_nan=False); return r
    def reject(self,command,**params):
        self.b.snapshot()
        before=copy.deepcopy(self.b.session.project.to_dict()); h=copy.deepcopy(self.b.session._history)
        r=self.call(command,**params); self.assertFalse(r['ok'],r)
        self.assertEqual(before,self.b.session.project.to_dict()); self.assertEqual(h,self.b.session._history)
        return r['error']
    def begin(self): return self.ok('design.mailer_begin')['context']
    def preview(self,c,**changes): return self.ok('design.mailer_preview',context_token=c['context_token'],options=dict(c['defaults'],**changes))['preview']
    def apply(self,d,placement=True): return self.ok('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=placement)
    def test_private_core_parity_units_rotation(self):
        for units in ('in','mm','cm'):
            self.ok('project.new',units=units)
            before=copy.deepcopy(self.b.session.project.to_dict()); h=self.b.session._history_index; selected=set(self.b.session.selected_ids)
            c=self.begin()
            self.assertAlmostEqual(c['defaults']['length'],10*unit_scale('in',units))
            for rotate in (False,True):
                d=self.preview(c,rotate=rotate); plan=plan_mailer(MailerOptions(**d['options']),units)
                self.assertEqual(d['bounds'],list(plan.bounds)); self.assertEqual(d['cut_count'],1); self.assertEqual(d['score_count'],12)
                actual=CamProject.from_dict(dict(before,entities=d['entities']))
                self.assertEqual([entity_bounds(e) for e in actual.entities],[entity_bounds(e) for e in mailer_entities(plan)])
                self.assertEqual(len(d['labels']),len(plan.labels))
            self.assertEqual(before,self.b.session.project.to_dict()); self.assertEqual(h,self.b.session._history_index); self.assertEqual(selected,self.b.session.selected_ids)
    def test_apply_one_history_step_preserves_drawing_and_roundtrips(self):
        self.ok('geometry.add_rectangle',x=60,y=35,width=2,height=1)
        before=copy.deepcopy(self.b.session.project.to_dict()); h=self.b.session._history_index
        d=self.preview(self.begin()); self.reject('design.mailer_apply',token=d['token'],reviewed=False,placement_reviewed=True)
        self.apply(d); after=copy.deepcopy(self.b.session.project.to_dict()); entities=self.b.session.project.entities[1:]
        self.assertEqual(self.b.session._history_index,h+1); self.assertEqual(len(self.b.session.selected_ids),13)
        self.assertEqual(after['entities'][0],before['entities'][0])
        for key,value in before['metadata'].items():
            if key=='layer_settings':
                for layer,settings in value.items(): self.assertEqual(after['metadata'][key][layer],settings)
            else: self.assertEqual(after['metadata'][key],value)
        self.assertEqual(after['tool_library'],before['tool_library']); self.assertFalse(after['operations'])
        self.assertEqual(len({e.metadata['box_owner'] for e in entities}),1)
        self.assertTrue(all(e.metadata['qualification']=='prototype' for e in entities))
        self.ok('history.undo'); self.assertEqual(before,self.b.session.project.to_dict())
        self.ok('history.redo'); self.assertEqual(after,self.b.session.project.to_dict())
        dest=self.root/'mailer.camjob.json'; self.ok('project.save',path=str(dest)); self.assertEqual(CamProject.load(dest).to_dict(),after)
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
    def test_placement_overlap_requires_extra_review(self):
        self.b.session.project.metadata['plates']=[dict(number=1,x=100,y=50,width=96,height=48)]
        c=self.begin(); self.assertEqual((c['defaults']['x'],c['defaults']['y']),(101,51))
        d=self.preview(c,x=-2,y=7,rotate=True); self.assertEqual(d['bounds'][:2],[-2,7]); self.assertTrue(d['warnings'])
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=False); self.apply(d)
        d=self.preview(self.begin(),x=-2,y=7,rotate=True); self.assertTrue(any('overlap' in w.lower() for w in d['warnings']))
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=False)
    def test_protected_layers_and_machining_or_registered_sources(self):
        for layer in ('BOX_CUT','BOX_SCORE'):
            for setting in (dict(locked=True),dict(visible=False)):
                self.b.session.project.metadata={'layer_settings':{layer:setting}}
                self.reject('design.mailer_begin')
        self.b.session.project.metadata={}; self.b.session.project.operations=[RouterOffsetOperation('Cut',1,[],.1,.25)]
        self.reject('design.mailer_begin'); self.b.session.project.operations=[]
        for metadata in ({'cabinet_tab_plans':{'one':{}}},{'cabinet_fabrication_snapshot':{}}):
            self.b.session.project.metadata=metadata; self.reject('design.mailer_begin')
    def test_revision_digest_layer_context_and_token_staleness(self):
        c=self.begin(); d=self.preview(c); self.b.session.project.name='External change'
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
        c=self.begin(); d=self.preview(c); self.ok('geometry.add_circle',x=60,y=35,diameter=1)
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
        c=self.begin(); d=self.preview(c); newer=self.preview(c,rotate=True)
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
        self.begin(); self.reject('design.mailer_apply',token=newer['token'],reviewed=True,placement_reviewed=True)
        c=self.begin(); d=self.preview(c); self.b.session.project.metadata['layer_settings']={'BOX_SCORE':{'locked':True}}
        self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
    def test_bad_preview_revokes_previous_offer(self):
        c=self.begin()
        for change in (dict(length=float('nan')),dict(width=True),dict(thickness=0),dict(clearance=-1),dict(height=1e20),dict(rotate=1),dict(extra=2),dict(score_inset=100)):
            d=self.preview(c)
            self.reject('design.mailer_preview',context_token=c['context_token'],options=dict(c['defaults'],**change))
            self.reject('design.mailer_apply',token=d['token'],reviewed=True,placement_reviewed=True)
        self.reject('design.mailer_preview',context_token=c['context_token'],options={})
    def test_normal_manufacturing_score_then_cut_units_and_native_save(self):
        for units in ('in','mm'):
            f=unit_scale('in',units); self.ok('project.new',units=units)
            self.ok('manufacturing.setup',material='Plywood',material_thickness=.125*f,safe_z=.25*f)
            self.ok('manufacturing.tool_save',station=1,name='Outline cutter',diameter=.125*f,spindle_rpm=18000,feed_rate=2*f,plunge_rate=.5*f,max_stepdown=.05*f,cutting_edge_length=.75*f)
            self.b.session.project.tool_library.tools.append(Tool(8,'Score tool',.1*f,kind=ToolKind.ENGRAVER,spindle_mode='disabled',feed_rate=1*f,plunge_rate=.25*f,metadata={'spring_travel':.375*f}))
            self.apply(self.preview(self.begin())); j=self.b.session.project; self.assertFalse(j.operations)
            scores=[e.id for e in j.entities if e.layer=='BOX_SCORE']; cut=[e.id for e in j.entities if e.layer=='BOX_CUT']
            d=self.ok('manufacturing.operation_prepare',command='special_save',params=dict(kind='engrave',name='Score FIRST',tool_station=8,entity_ids=scores,target_depth=.02*f,safe_z=.25*f,approach_height=.05*f))['manufacturing']
            deadline=time.monotonic()+40
            while d['status']=='running' and time.monotonic()<deadline:
                time.sleep(.03); d=self.ok('manufacturing.poll')['manufacturing']
            self.assertEqual(d['status'],'operation_ready',d)
            self.ok('manufacturing.operation_apply',token=d['token'],reviewed=True)
            self.ok('manufacturing.profile_save',name='Outline after scoring',tool_station=1,entity_ids=cut,target_depth=.125*f,safe_z=.25*f,stepdown=.05*f,side='outside',cut_direction='climb')
            j=self.b.session.project; self.assertEqual([o.kind for o in j.operations],['engrave','router_offset'])
            bundle,errors=generate_toolpath_preview_bundle(j); self.assertFalse(errors,errors); self.assertEqual(len(bundle),2); self.assertTrue(all(v.motions for v in bundle.values()))
            after=copy.deepcopy(j.to_dict()); self.ok('history.undo'); self.assertEqual(len(self.b.session.project.operations),1); self.ok('history.redo'); self.assertEqual(after,self.b.session.project.to_dict())
            dest=self.root/(units+'.camjob.json'); self.ok('project.save',path=str(dest)); self.assertEqual(CamProject.load(dest).to_dict(),after)
            self.reject('manufacturing.export',token='not-reviewed',reviewed=True,name='no-output')

if __name__=='__main__': unittest.main()
