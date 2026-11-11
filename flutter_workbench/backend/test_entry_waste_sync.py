import copy
import unittest
from unittest.mock import patch
import design_bridge  # Select only the versioned Flutter core.
from cam_core import CamProject, Circle, Point, Tool, ToolLibrary, RouterOffsetOperation, OffsetSide, generate_closed_router_offset
from cam_core.entry_waste import detached_entry_waste
from cam_core.workflow_features import entry_interference_violations


class EntryWasteSyncTests(unittest.TestCase):
    def test_hole_detection_filled_sweep_and_unit_translation_invariance(self):
        for scale in (1,25.4):
            for shift in (0,50):
                points=tuple(((x+shift)*scale,(y+shift)*scale) for x,y in [(0,0),(10,0),(10,10),(0,10)])
                center=((11+shift)*scale,(5+shift)*scale)
                hit=detached_entry_waste(points,.25*scale,center,.75*scale,.125*scale)
                self.assertIsNotNone(hit);self.assertGreater(hit[2]/scale**2,1)
                self.assertIsNone(detached_entry_waste(points,.25*scale,center,.1*scale,.125*scale))

    def test_actual_unsafe_helix_refused_or_explicit_fallback_and_independently_reported(self):
        for scale in (1,25.4):
            tool=Tool(1,'Cutter',.25*scale,spindle_rpm=18000,feed_rate=scale,plunge_rate=.5*scale)
            entity=Circle(id='part',center=Point(10*scale,10*scale),radius=3*scale)
            op=RouterOffsetOperation('Outside',1,[entity.id],.1*scale,.5*scale,side=OffsetSide.OUTSIDE,
                entry_mode='helical',helix_diameter=1*scale,helix_min_diameter=1*scale,helix_pitch=.05*scale,helix_fallback='strict_error')
            project=CamProject('Entry waste fixture',units='in' if scale==1 else 'mm',entities=[entity],tool_library=ToolLibrary([tool]),operations=[op])
            before=copy.deepcopy(project.to_dict())
            with self.assertRaisesRegex(ValueError,'loose waste chip'):generate_closed_router_offset(project,op)
            self.assertEqual(project.to_dict(),before)
            with patch('cam_core.toolpath._outside_entry_detached_waste',return_value=None):
                unsafe=generate_closed_router_offset(project,op)
            findings=entry_interference_violations(project,generated_paths={op.id:unsafe})
            self.assertTrue(any(i.code=='toolpath.entry_detached_waste' for i in findings))
            op.helix_fallback='waste_plunge';before=copy.deepcopy(project.to_dict())
            safe=generate_closed_router_offset(project,op)
            self.assertTrue(any(b.metadata.get('entry_fallback_reason_code')=='entry_detaches_waste' for b in safe.blocks))
            self.assertFalse(entry_interference_violations(project,generated_paths={op.id:safe}))
            self.assertEqual(project.to_dict(),before)
