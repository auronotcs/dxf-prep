import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
from test_manufacturing_bridge import ManufacturingTests as Base
from cam_core import Line,Polyline,Point,Plate,Rectangle
from cam_core.plates import make_plate_project,open_path_plate
from cam_core.automation import _project_plates
from cam_core.model import OffsetSide,KeepoutZone
from cam_core.toolpath_background import generate_toolpath_preview_bundle
from cam_core.output_workflow import prepare_plate_outputs,execute_plate_outputs,repository_output_paths

class OwnershipTests(Base):
    def paths(self,units='in',multi=False,poly=False):
        self.fixture(units);f=25.4 if units=='mm' else 1
        p=self.b.session.project;eid=p.entities[0].id
        points=[Point(90*f,10*f),Point(100*f,10*f)]
        p.entities=[Polyline(id=eid,points=points,closed=False) if poly else Line(id=eid,start=points[0],end=points[1])]
        p.entities[0].metadata['custom']='preserve'
        p.operations[0].side=OffsetSide.ON
        p.metadata['plates']=[Plate(1,0,0,96*f,48*f).to_dict()]
        if multi:p.metadata['plates'].append(Plate(2,96*f,0,96*f,48*f).to_dict())
        self.b.session._record_history()
        return p,eid,f

    def info(self):return self.run_command('manufacturing.open_paths')['manufacturing']
    def assign(self,eid,number,token=None):
        return self.run_command('manufacturing.open_path_assign',id=eid,plate_number=number,token=token or self.info()['token'])

    def test_assignment_is_undoable_preserves_direction_selection_and_roundtrip(self):
        for units in ('in','mm'):
            for poly in (False,True):
                p,eid,f=self.paths(units,True,poly);before=copy.deepcopy(p.to_dict());selected=set(self.b.session.selected_ids)
                row=self.info()['paths'][0];self.assertIsNone(row['owner']);self.assertEqual(row['choices'],[1,2])
                self.assign(eid,2);assigned=copy.deepcopy(self.b.session.project.to_dict())
                self.assertEqual(self.b.session.selected_ids,selected)
                self.assertEqual(assigned['entities'][0],dict(before['entities'][0],metadata={'custom':'preserve','output_plate_number':2}))
                self.assertEqual(assigned['operations'],before['operations'])
                self.run_command('history.undo');self.assertEqual(self.b.session.project.to_dict(),before)
                self.run_command('history.redo');self.assertEqual(self.b.session.project.to_dict(),assigned)
                path=self.root/f'{units}-{poly}.camjob.json';self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
                self.assertEqual(self.b.session.project.to_dict(),assigned)
                self.assign(eid,None);self.assertIsNone(self.info()['paths'][0]['owner'])

    def test_ambiguous_or_invalid_assignment_is_atomic_and_selected_output_cannot_hide_it(self):
        p,eid,f=self.paths(multi=True);before=copy.deepcopy(p.to_dict());token=self.info()['token']
        for number in (True,'1',0,3,1.5):
            r=self.b.handle(dict(command='manufacturing.open_path_assign',revision=self.b.revision,params=dict(id=eid,plate_number=number,token=token)))
            self.assertFalse(r['ok']);self.assertEqual(self.b.session.project.to_dict(),before)
        self.assertFalse(self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision,params={'plates':[1]}))['ok'])
        self.assign(eid,1)
        self.assertFalse(self.b.handle(dict(command='manufacturing.open_path_assign',revision=self.b.revision,params=dict(id=eid,plate_number=2,token=token)))['ok'])
        self.b.session.project.entities[0].metadata['reference_only']=True
        self.assertFalse(self.b.handle(dict(command='manufacturing.open_path_assign',revision=self.b.revision,params=dict(id=eid,plate_number=2,token=self.info()['token'])))['ok'])

    def test_complete_paths_are_localized_once_without_clipping_and_keepouts_survive(self):
        for units in ('in','mm'):
            p,eid,f=self.paths(units,True,True)
            p.keepouts=[KeepoutZone('Outside fixture',[Point(99*f,9*f),Point(101*f,9*f),Point(101*f,11*f),Point(99*f,11*f)],.75*f)]
            self.assign(eid,2);p=self.b.session.project;before=copy.deepcopy(p.to_dict());plates=_project_plates(p)
            first=make_plate_project(p,plates[0]);second=make_plate_project(p,plates[1])
            self.assertEqual(first.project.entities,[]);self.assertEqual(len(second.project.entities),1)
            points=second.project.entities[0].points
            self.assertAlmostEqual(points[0].x,-6*f);self.assertAlmostEqual(points[1].x,4*f)
            self.assertEqual(second.project.entities[0].metadata['output_plate_number'],1)
            self.assertEqual(len(second.project.keepouts),1);self.assertAlmostEqual(second.project.keepouts[0].points[0].x,3*f)
            self.assertEqual(p.to_dict(),before)
            # A path with an overlapping bounding box but no intersecting segment has no owner.
            diagonal=Line(start=Point(-5*f,1*f),end=Point(1*f,-5*f))
            self.assertIsNone(open_path_plate(diagonal,[plates[0]]))

    def test_overtravel_requires_explicit_review_real_export_parity_and_stale_refusal(self):
        for units in ('in','mm'):
            p,eid,f=self.paths(units);original=copy.deepcopy(p.to_dict())
            preview=self.generate();self.assertTrue(preview['openPathOvertravel'])
            self.assertTrue(any(i['code']=='plate.open_overtravel' for i in preview['validation']['issues']))
            export=dict(token=preview['token'],reviewed=True,name='open-'+units)
            with patch('manufacturing_support.__file__',str(self.root/'backend/manufacturing_support.py')):
                existing=set(self.root.rglob('*'))
                r=self.b.handle(dict(command='manufacturing.export',revision=self.b.revision,params=export));self.assertFalse(r['ok']);self.assertIn('beyond stock',r['error'])
                self.assertEqual(set(self.root.rglob('*')),existing)
                out=self.run_command('manufacturing.export',**export,overtravel_reviewed=True)['manufacturing']
                prepared=self.b.manufacturing.prepared
                direct,errors=generate_toolpath_preview_bundle(prepared[0].result.project);self.assertFalse(errors)
                self.assertEqual(direct,self.b.manufacturing.bundle)
                ref=self.root/('reference-'+units);ref.mkdir()
                expected=prepare_plate_outputs(p,_project_plates(p),repository_output_paths(ref,'reference',[1]))
                emitted=execute_plate_outputs(expected,'CNC + CAM project',generated_paths_by_plate={1:direct})
                actual=next(Path(v) for v in out['paths'] if v.endswith('.cnc'))
                lines=lambda path:[line for line in path.read_text().splitlines() if line and not line.startswith('(')]
                self.assertEqual(lines(actual),lines(emitted.controller_paths[0]))
            self.assertEqual(self.b.session.project.to_dict(),original)
            self.assign(eid,1)
            self.assertFalse(self.b.handle(dict(command='manufacturing.export',revision=self.b.revision,params=dict(export,overtravel_reviewed=True)))['ok'])

    def test_external_fixture_collision_blocks_open_cut_even_with_overtravel_review(self):
        p,eid,f=self.paths()
        p.keepouts=[KeepoutZone('Clamp beyond stock',[Point(98,9),Point(101,9),Point(101,11),Point(98,11)],.75)]
        preview=self.generate('blocked');self.assertTrue(preview['validation']['blocking'])
        self.assertFalse(self.b.handle(dict(command='manufacturing.export',revision=self.b.revision,params=dict(token=preview.get('token'),reviewed=True,overtravel_reviewed=True,name='blocked')))['ok'])

    def test_closed_crossings_and_invalid_imported_ownership_still_refuse_output(self):
        p,eid,f=self.paths()
        p.entities[0]=Rectangle(id=eid,corner_a=Point(90,5),corner_b=Point(100,10))
        self.assertFalse(self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision))['ok'])
        for assigned in ('1',True,1.5,3):
            p,eid,f=self.paths();p.entities[0].metadata['output_plate_number']=assigned
            row=self.info()['paths'][0];self.assertFalse(row['assignmentValid']);self.assertIsNone(row['owner'])
            r=self.b.handle(dict(command='manufacturing.generate',revision=self.b.revision));self.assertFalse(r['ok']);self.assertIn('assignment',r['error'])
            self.b.session._record_history();index=self.b.session._history_index
            self.assign(eid,1);self.assertEqual(self.info()['paths'][0]['owner'],1)
            self.assertEqual(self.b.session._history_index,index+1)
            self.run_command('history.undo');self.assertFalse(self.info()['paths'][0]['assignmentValid'])
            self.run_command('history.redo');self.assertIs(type(self.b.session.project.entities[0].metadata['output_plate_number']),int)

for name in dir(Base):
    if name.startswith('test_'):setattr(OwnershipTests,name,None)

if __name__=='__main__':unittest.main()
