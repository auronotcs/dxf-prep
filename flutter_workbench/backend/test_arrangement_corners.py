import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.layout import entity_bounds
from cam_core.model import Rectangle, Point
from cam_core.dr_pans import DrPanSettings, generate_dr_pan, dr_pan_bend_marks, resolve_dr_pan_engraving
from cam_core.transform import transform_entity


class ArrangementCornerTests(unittest.TestCase):
    setUp=DesignBridgeTests.setUp
    run_command=DesignBridgeTests.run_command
    preview=DesignBridgeTests.preview

    def test_four_corners_locked_reference_both_units_history_and_save(self):
        for units,f in [('in',1),('mm',25.4)]:
            for action,x,y in [('align_top_left',10,23),('align_top_right',18,23),
                               ('align_bottom_left',10,20),('align_bottom_right',18,20)]:
                self.run_command('project.new',units=units)
                anchor=self.run_command('geometry.add_rectangle',x=10*f,y=20*f,width=10*f,height=5*f,layer='reference')['selected'][0]
                self.run_command('layer.set',name='reference',locked=True)
                moving=self.run_command('geometry.add_rectangle',x=40*f,y=3*f,width=2*f,height=2*f)['selected'][0]
                before=copy.deepcopy(self.bridge.session.project.to_dict());index=self.bridge.session._history_index
                draft=self.preview(kind='arrange',action=action,reference='locked',reference_item_id=anchor)
                self.assertEqual(self.bridge.session.project.to_dict(),before)
                self.run_command('design.apply_shapes',token=draft['token'])
                self.assertEqual(self.bridge.session._history_index,index+1)
                entities={e.id:e for e in self.bridge.session.project.entities}
                for actual,want in zip(entity_bounds(entities[moving]),(x*f,y*f,(x+2)*f,(y+2)*f)):
                    self.assertAlmostEqual(actual,want)
                self.assertEqual(entities[anchor].to_dict(),before['entities'][0])
                final=copy.deepcopy(self.bridge.session.project.to_dict())
                self.run_command('history.undo');self.assertEqual(self.bridge.session.project.to_dict(),before)
                self.run_command('history.redo');self.assertEqual(self.bridge.session.project.to_dict(),final)
                path=self.folder/f'{units}-{action}.camjob.json';self.run_command('project.save',path=str(path))
                self.run_command('project.new');self.run_command('project.open',path=str(path))
                self.assertEqual(self.bridge.session.project.to_dict(),final)

    def test_corner_plate_target_stale_preview_rejected(self):
        self.run_command('geometry.add_rectangle',x=3,y=4,width=2,height=5)
        draft=self.preview(kind='arrange',action='align_top_right',reference='plate')
        self.assertEqual(draft['entities'][0]['bounds'],(94,43,96,48))
        self.run_command('selection.clear');before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.apply_shapes',params=dict(token=draft['token']),revision=self.bridge.revision))
        self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)

    def test_bend_guides_resolve_existing_marks_after_reorder_and_transform(self):
        for offset in (0,.125,-.125):
            pan=generate_dr_pan(Rectangle(corner_a=Point(0,0),corner_b=Point(24,36)),DrPanSettings())
            marks=dr_pan_bend_marks(pan.outline,pan.references,offset)
            all_entities=[pan.outline,*pan.references,*marks]
            for angle in (0,37):
                entities=[transform_entity(e,delta_x=12,delta_y=-8,angle_degrees=angle) for e in all_entities]
                known={e.id:e for e in entities};entities.reverse()
                before=[e.to_dict() for e in entities]
                for guide,mark in zip(pan.references,marks):
                    resolved=resolve_dr_pan_engraving(entities,[known[guide.id]])
                    self.assertEqual([e.id for e in resolved],[mark.id])
                self.assertEqual([e.to_dict() for e in entities],before)

    def test_missing_and_ambiguous_bend_marks_refused(self):
        pan=generate_dr_pan(Rectangle(corner_a=Point(0,0),corner_b=Point(24,36)),DrPanSettings())
        marks=dr_pan_bend_marks(pan.outline,pan.references)
        with self.assertRaisesRegex(ValueError,'No matching'): resolve_dr_pan_engraving([pan.outline,*pan.references],[pan.references[0]])
        duplicate=copy.deepcopy(marks[0]);duplicate.id='duplicate-mark'
        with self.assertRaisesRegex(ValueError,'No matching'): resolve_dr_pan_engraving([pan.outline,*pan.references,*marks,duplicate],[pan.references[0]])


if __name__=='__main__':unittest.main()
