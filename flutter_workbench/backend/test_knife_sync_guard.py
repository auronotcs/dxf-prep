import copy
import json
from pathlib import Path
from test_manufacturing_special import SpecialManufacturingTests as Base
from cam_core.model import DragKnifeOperation, Tool, ToolKind
from manufacturing_support import supported


class KnifeSyncGuardTests(Base):
    def test_ported_imported_knife_settings_preserved_in_both_units(self):
        for units in ('in','mm'):
            self.empty_fixture(units);f=25.4 if units=='mm' else 1
            job=self.b.session.project
            job.tool_library.tools.append(Tool(8,'Knife',.1*f,kind=ToolKind.DRAG_KNIFE,
                spindle_mode='disabled',feed_rate=f,plunge_rate=.25*f))
            op=DragKnifeOperation('Knife',8,[job.entities[0].id],.1*f,.25*f,setup_verified=True)
            job.operations=[op]
            self.assertTrue(supported(job))
            for field,value in [('outside_corner_loops',True),('loop_radius',.3*f),('loop_feed_percent',60),
                                ('edge_aware_corners',True),('corner_feed_ipm',20),('lead_in_feed_ipm',25)]:
                candidate=copy.deepcopy(job);setattr(candidate.operations[0],field,value)
                before=copy.deepcopy(candidate.to_dict())
                self.assertTrue(supported(candidate),field)
                path=str(self.root/(field+'-'+units+'.camjob.json'))
                Path(path).write_text(json.dumps(before),encoding='utf-8')
                original=Path(path).read_bytes()
                self.run_command('project.open',path=path)
                self.assertEqual(before,self.b.session.project.to_dict())
                saved=str(self.root/('saved-'+field+'-'+units+'.camjob.json'))
                self.run_command('project.save',path=saved)
                self.assertEqual(Path(path).read_bytes(),original)
                self.run_command('project.open',path=saved)
                self.assertEqual(before,self.b.session.project.to_dict())


for name in dir(Base):
    if name.startswith('test_'):setattr(KnifeSyncGuardTests,name,None)
