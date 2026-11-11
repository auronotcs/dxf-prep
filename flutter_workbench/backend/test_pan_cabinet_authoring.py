import copy
import math
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from design_bridge import Bridge
from cam_core.model import Point, Rectangle, Polyline, Circle, RouterOffsetOperation
from cam_core.dr_pans import DrPanSettings,DrPanHoleSettings,generate_dr_pan,dr_pan_bend_marks
from cam_core.cabinet_tab_mounting import CabinetTabPlan,with_applied_review,source_fingerprint
from cam_core.units import convert_project_units

class PanCabinetAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.b=Bridge(self.root/'layout.json')
    def call(self,cmd,**p):
        return self.b.handle(dict(command=cmd,params=p,revision=self.b.revision))
    def ok(self,cmd,**p):
        r=self.call(cmd,**p);self.assertTrue(r['ok'],r);return r
    def reject(self,cmd,**p):
        before=copy.deepcopy(self.b.session.project.to_dict());h=self.b.session._history_index
        r=self.call(cmd,**p);self.assertFalse(r['ok'],r)
        self.assertEqual(before,self.b.session.project.to_dict());self.assertEqual(h,self.b.session._history_index)
        return r['error']
    def source(self,poly=False,f=1.):
        self.ok('project.new');j=self.b.session.project;j.units='mm' if f==25.4 else 'in'
        j.entities=[Polyline(id='face',points=[Point(0.,0.),Point(12*f,0.),Point(12*f,8*f),Point(0.,8*f)],closed=True) if poly else Rectangle(id='face',corner_a=Point(0.,0.),corner_b=Point(12*f,8*f))]
        self.b.session.selected_ids={'face'};self.b.snapshot();self.b.session._record_history();return j
    def settings(self,f=1.,**patch):
        s=asdict(DrPanSettings());s={k:v*(f if k!='k_factor' else 1) for k,v in s.items()}
        h=asdict(DrPanHoleSettings());h.update(enabled=True,diameter=.125*f,max_spacing=4*f,end_offset=1*f)
        return dict(dict(mode='create',settings=s,holes=h,marks_enabled=True,mark_offset=.02*f),**patch)
    def pan(self,**p):return self.ok('design.pan_preview',**(p or self.settings()))['preview']
    def apply_pan(self,d):self.ok('design.pan_apply',token=d['token'],reviewed=True)
    def cabinet(self):
        c=self.ok('design.cabinet_begin')['context'];f=25.4 if c['units']=='mm' else 1.
        self.assertEqual(len(c['candidates']),1,c)
        p=dict(schema='cabinet-tab-plan-v1',plan_id=c['new_id'],units=c['units'],**c['defaults'],planes=copy.deepcopy(c['candidates']),tabs=[dict(pair_id='pair1',owner_entity_id='face',center_u=4*f,width=2*f,projection=.5*f)])
        return c,p
    def cab_preview(self,c,p):return self.ok('design.cabinet_preview',context_token=c['token'],plan=p)['preview']
    def apply_cab(self,d):self.ok('design.cabinet_apply',token=d['token'],reviewed=True)
    def roundtrip(self):
        path=self.root/'author.camjob.json';self.ok('project.save',path=str(path));before=copy.deepcopy(self.b.session.project.to_dict());self.ok('project.open',path=str(path));self.assertEqual(before,self.b.session.project.to_dict())

    def test_pan_core_parity_physical_units_holes_marks_and_roundtrip(self):
        for f in (1.,25.4):
            j=self.source(f=f);original=copy.deepcopy(j.to_dict());p=self.settings(f);p['holes'].update(top=False,left=False)
            expected=generate_dr_pan(j.entities[0],DrPanSettings(**p['settings']),DrPanHoleSettings(**p['holes']))
            d=self.pan(**p);self.assertEqual(original,j.to_dict());h=self.b.session._history_index
            self.apply_pan(d);j=self.b.session.project;self.assertEqual(h+1,self.b.session._history_index)
            self.assertEqual(j.entities[0].to_dict(),expected.outline.to_dict())
            guides=[e for e in j.entities if e.metadata.get('reference_only')];holes=[e for e in j.entities if e.metadata.get('dr_pan_hole')];marks=[e for e in j.entities if e.metadata.get('dr_pan_bend_mark')]
            self.assertEqual(len(guides),4);self.assertEqual(len(marks),4);self.assertEqual(len(holes),len(expected.holes))
            self.assertTrue(all(e.metadata['machinable'] is False for e in guides))
            self.assertEqual([(e.center,e.radius) for e in holes],[(e.center,e.radius) for e in expected.holes])
            after=copy.deepcopy(j.to_dict());self.ok('history.undo');self.assertEqual(original,self.b.session.project.to_dict());self.ok('history.redo');self.assertEqual(after,self.b.session.project.to_dict());self.roundtrip()
    def test_pan_rotated_reversed_collinear_multiple_faces(self):
        j=self.source(poly=True);e=j.entities[0];a=.37
        pts=[Point(0.,0.),Point(6.,0.),Point(12.,0.),Point(12.,8.),Point(0.,8.)]
        e.points=[Point(p.x*math.cos(a)-p.y*math.sin(a),p.x*math.sin(a)+p.y*math.cos(a)) for p in reversed(pts)]
        other=Rectangle(id='other',corner_a=Point(30.,0.),corner_b=Point(42.,8.));j.entities.append(other);self.b.session.selected_ids.add('other')
        d=self.pan();self.assertEqual(len(d['rows']),2);self.apply_pan(d);self.assertEqual({e.id for e in self.b.session.project.entities if e.metadata.get('dr_pan')},{'face','other'})
    def test_pan_validation_atomic_guards_and_private_tokens(self):
        j=self.source();p=self.settings();d=self.pan();self.reject('design.pan_apply',token=d['token'],reviewed=False)
        for key,value in [('thickness',0),('inside_lip',.01),('inside_radius',-1),('k_factor',.8),('corner_gap',-1),('cutter_diameter',float('nan'))]:
            q=copy.deepcopy(p);q['settings'][key]=value;self.reject('design.pan_preview',**q)
        self.reject('design.pan_apply',token=d['token'],reviewed=True)
        d=self.pan();j=self.b.session.project;j.entities[0].corner_b=Point(13.,8.);self.reject('design.pan_apply',token=d['token'],reviewed=True)
        j=self.b.session.project;j.entities[0].metadata['mounting_generated']=True;self.reject('design.pan_preview',**p)
        j=self.source();self.ok('layer.set',name='0',locked=True);self.reject('design.pan_preview',**p)
        j=self.source();j.operations=[RouterOffsetOperation(name='Cut',tool_station=1,entity_ids=['face'],target_depth=.2,safe_z=.5)];self.reject('design.pan_preview',**p)
    def test_pan_nonrectangle_partial_group_and_reserved_layer(self):
        j=self.source(poly=True);j.entities[0].points[2]=Point(10.,7.);self.reject('design.pan_preview',**self.settings())
        j=self.source();j.entities[0].metadata['group_id']='g';j.entities.append(Circle(id='hole',center=Point(2.,2.),radius=.3,metadata={'group_id':'g'}));self.reject('design.pan_preview',**self.settings())
        j=self.source();self.ok('layer.set',name='DR_PAN_BEND_REFERENCE',locked=True);self.reject('design.pan_preview',**self.settings())
    def test_existing_marks_identity_reorder_missing_signed_offsets(self):
        self.source();self.apply_pan(self.pan());j=self.b.session.project
        immutable={e.id:e.to_dict() for e in j.entities if not e.metadata.get('dr_pan_bend_mark')};old={e.id for e in j.entities if e.metadata.get('dr_pan_bend_mark')}
        j.entities.reverse();self.b.session.selected_ids={'face'}
        d=self.pan(mode='marks',marks_enabled=True,mark_offset=-.04);self.assertTrue(any(e['id']=='face' for e in d['geometry']));self.apply_pan(d);j=self.b.session.project
        self.assertEqual(old,{e.id for e in j.entities if e.metadata.get('dr_pan_bend_mark')});self.assertEqual(immutable,{e.id:e.to_dict() for e in j.entities if not e.metadata.get('dr_pan_bend_mark')})
        missing=next(e.id for e in j.entities if e.metadata.get('dr_pan_bend_mark'));j.entities=[e for e in j.entities if e.id!=missing];self.b.session.selected_ids={'face'}
        d=self.pan(mode='marks',marks_enabled=True,mark_offset=.03);self.assertEqual(d['rows'][0]['created'],1);self.apply_pan(d)
        self.assertTrue(old-{missing}<={e.id for e in self.b.session.project.entities});self.roundtrip()
    def test_existing_marks_reject_alteration_and_locked_assembly(self):
        self.source();self.apply_pan(self.pan());j=self.b.session.project;mark=next(e for e in j.entities if e.metadata.get('dr_pan_bend_mark'));mark.start=Point(99.,99.)
        self.reject('design.pan_preview',mode='marks',marks_enabled=True,mark_offset=0.)
        self.source();self.apply_pan(self.pan());self.ok('layer.set',name='DR_PAN_BEND_REFERENCE',locked=True)
        self.reject('design.pan_preview',mode='marks',marks_enabled=True,mark_offset=0.)
    def test_cabinet_private_review_core_parity_save_undo_and_no_machining(self):
        for f in (1.,25.4):
            j=self.source(poly=True,f=f);c,p=self.cabinet();p['planes'][0].update(plan_origin=dict(x=30*f,y=40*f),angle_degrees=35.,inward_setback=.2*f);p['width_clearance_total']=.01*f;p['thickness_clearance_total']=.02*f
            original=copy.deepcopy(j.to_dict());expected=with_applied_review(j,CabinetTabPlan.from_dict(p));d=self.cab_preview(c,p);self.assertEqual(original,j.to_dict())
            self.apply_cab(d);j=self.b.session.project;self.assertEqual(j.entities[0].to_dict(),original['entities'][0]);self.assertFalse(j.operations)
            self.assertEqual([e.to_dict() for e in j.entities],[e.to_dict() for e in expected.entities]);self.assertTrue(all(e.metadata.get('machinable') is False for e in j.entities[1:]))
            after=copy.deepcopy(j.to_dict());self.ok('history.undo');self.assertEqual(original,self.b.session.project.to_dict());self.ok('history.redo');self.assertEqual(after,self.b.session.project.to_dict());self.roundtrip()
            self.reject('geometry.add_rectangle',x=30,y=30,width=1,height=1)
    def test_cabinet_saved_edit_stable_pairs_and_remove_pair(self):
        self.source(poly=True);c,p=self.cabinet();p['tabs'].append(dict(pair_id='pair2',owner_entity_id='face',center_u=8.,width=2.,projection=.5));self.apply_cab(self.cab_preview(c,p));j=self.b.session.project
        oldids={e.id for e in j.entities if e.metadata.get('tab_slot_pair_id')=='pair1'};c=self.ok('design.cabinet_begin')['context'];p=copy.deepcopy(c['saved'][p['plan_id']]);p['tabs']=p['tabs'][:1];p['tabs'][0]['width']=2.5
        self.apply_cab(self.cab_preview(c,p));j=self.b.session.project
        self.assertEqual(oldids,{e.id for e in j.entities if e.metadata.get('tab_slot_pair_id')=='pair1'});self.assertFalse(any(e.metadata.get('tab_slot_pair_id')=='pair2' for e in j.entities));self.roundtrip()
    def test_cabinet_reregister_changed_sources_explicit_and_unit_conversion(self):
        self.source(poly=True);c,p=self.cabinet();self.apply_cab(self.cab_preview(c,p));j=self.b.session.project
        j.entities[0].points[2]=Point(13.,8.);c=self.ok('design.cabinet_begin')['context'];p=copy.deepcopy(c['saved'][p['plan_id']]);self.reject('design.cabinet_preview',context_token=c['token'],plan=p)
        self.reject('design.cabinet_reregister',context_token=c['token'],plan=p,confirmed=False)
        before=copy.deepcopy(j.to_dict());p=self.ok('design.cabinet_reregister',context_token=c['token'],plan=p,confirmed=True)['plan'];self.assertEqual(before,j.to_dict());self.apply_cab(self.cab_preview(c,p))
        self.b.session.project=convert_project_units(self.b.session.project,'mm');j=self.b.session.project;c=self.ok('design.cabinet_begin')['context'];p=copy.deepcopy(c['saved'][p['plan_id']]);self.reject('design.cabinet_preview',context_token=c['token'],plan=p)
        p=self.ok('design.cabinet_reregister',context_token=c['token'],plan=p,confirmed=True)['plan'];self.assertEqual(p['units'],'mm');self.assertAlmostEqual(p['tabs'][0]['width'],50.8);self.apply_cab(self.cab_preview(c,p))
    def test_cabinet_reject_ownership_overlap_fabrication_lock_stale(self):
        j=self.source(poly=True);c,p=self.cabinet();d=self.cab_preview(c,p)
        self.reject('design.cabinet_apply',token=d['token'],reviewed=False)
        for patch in ('sha','overlap','missing','thickness'):
            q=copy.deepcopy(p)
            if patch=='sha':q['planes'][0]['source_sha256']='0'*64
            if patch=='missing':q['planes'][0]['member_ids']=(*q['planes'][0]['member_ids'],'missing')
            if patch=='overlap':q['tabs'].append(dict(q['tabs'][0],pair_id='pair2'))
            if patch=='thickness':q['back_thickness']=0
            self.reject('design.cabinet_preview',context_token=c['token'],plan=q)
        self.reject('design.cabinet_apply',token=d['token'],reviewed=True)
        d=self.cab_preview(c,p);j=self.b.session.project;j.entities[0].points[2]=Point(13.,8.);self.reject('design.cabinet_apply',token=d['token'],reviewed=True)
        self.b.session.project.metadata['cabinet_fabrication_snapshot']={'test':True};self.reject('design.cabinet_begin')
        j=self.source(poly=True);self.ok('layer.set',name='0',locked=True);self.b.session.selected_ids={'face'};c=self.ok('design.cabinet_begin')['context'];self.assertFalse(c['candidates']);self.assertIn('unlocked',c['warning'])
    def test_cabinet_holes_preserved_owned_and_not_duplicated(self):
        j=self.source(poly=True);j.entities.append(Circle(id='hole',center=Point(5.,4.),radius=.5));self.b.session.selected_ids.add('hole');self.b.snapshot();c,p=self.cabinet();self.assertEqual(set(p['planes'][0]['member_ids']),{'face','hole'})
        original=copy.deepcopy(j.entities[1].to_dict());self.apply_cab(self.cab_preview(c,p));self.assertEqual(next(e for e in self.b.session.project.entities if e.id=='hole').to_dict(),original)

    def test_reference_faces_and_empty_fabrication_marker_stay_protected(self):
        for key,value in [('reference_only',True),('machinable',False)]:
            j=self.source();j.entities[0].metadata[key]=value;self.reject('design.pan_preview',**self.settings())
        j=self.source(poly=True);j.metadata['cabinet_fabrication_snapshot']={};self.reject('design.cabinet_begin')

    def test_duplicate_marks_and_stale_request_revision_refused(self):
        self.source();self.apply_pan(self.pan());j=self.b.session.project
        mark=copy.deepcopy(next(e for e in j.entities if e.metadata.get('dr_pan_bend_mark')));mark.id='duplicate';j.entities.append(mark)
        self.reject('design.pan_preview',mode='marks',marks_enabled=True,mark_offset=0.)
        self.source();d=self.pan();before=copy.deepcopy(self.b.session.project.to_dict())
        r=self.b.handle(dict(command='design.pan_apply',revision=-1,params=dict(token=d['token'],reviewed=True)))
        self.assertFalse(r['ok']);self.assertEqual(before,self.b.session.project.to_dict())

if __name__=='__main__':unittest.main()
