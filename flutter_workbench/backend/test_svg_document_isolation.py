import copy
import tempfile
import unittest
from pathlib import Path
from design_bridge import Bridge
from test_svg_import_review import ART
from cam_core.model import CamProject, RouterOffsetOperation
from cam_core.units import unit_scale

class SvgDocumentIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.original=self.bridge('original'); self.target=self.bridge('target')
        self.source=self.root/'Artwork.svg'; self.source.write_text(ART,encoding='utf-8')
        self.ok(self.original,'project.new',name='Unsaved production drawing')
        self.ok(self.original,'geometry.add_rectangle',x=50,y=25,width=2,height=3)
        self.original.session.project.operations.append(RouterOffsetOperation('Existing cut',1,list(self.original.session.selected_ids),.125,.25))
        self.original.session.project.metadata['job_setup']={'material':'Aluminum','material_thickness':.125}
        self.original.session._record_history(); self.original.sync_recovery(force=True)
    def bridge(self,name):
        b=Bridge(self.root/(name+'.prefs.json'),recovery_directory=self.root/'recovery')
        self.addCleanup(b.recovery.close);self.addCleanup(b.manufacturing.close);return b
    def call(self,b,verb,**p):return b.handle(dict(command=verb,params=p,revision=b.revision))
    def ok(self,b,verb,**p):
        r=self.call(b,verb,**p); self.assertTrue(r['ok'],r); return r
    def frozen_original(self):
        b=self.original
        return copy.deepcopy((b.session.project.to_dict(),b.session._history,b.session._history_index,b.session.selected_ids,b.revision,b.saved_digest,b.recovery.read(b.recovery.identity)[1].to_dict()))
    def preview(self,b):
        c=self.ok(b,'design.svg_begin',path=str(self.source))['context']
        return self.ok(b,'design.svg_preview',context_token=c['context_token'],placement='original',x=0,y=0,tolerance=c['tolerance'])['preview']
    def test_separate_dirty_native_job_units_history_and_original_recovery(self):
        before=self.frozen_original(); source=self.source.read_bytes()
        for units in ('in','mm','cm'):
            self.ok(self.target,'project.new',name='Artwork',units=units)
            blank=copy.deepcopy(self.target.session.project.to_dict()); d=self.preview(self.target)
            self.assertEqual(d['page_size'],[4*unit_scale('in',units),3*unit_scale('in',units)])
            self.assertEqual(blank,self.target.session.project.to_dict())
            r=self.ok(self.target,'design.svg_apply',token=d['token'],reviewed=True)['state']
            self.assertTrue(r['dirty']); self.assertFalse(r['path']);self.assertIsNone(r['savedPath'])
            self.assertEqual(r['name'],'Artwork');self.assertEqual(len(self.target.session.project.entities),4)
            self.assertFalse(self.target.session.project.operations); self.assertNotIn('job_setup',self.target.session.project.metadata)
            after=copy.deepcopy(self.target.session.project.to_dict())
            self.ok(self.target,'history.undo');self.assertEqual(self.target.session.project.to_dict(),blank)
            self.ok(self.target,'history.redo');self.assertEqual(self.target.session.project.to_dict(),after)
            dest=self.root/(units+'.camjob.json');self.ok(self.target,'project.save',path=str(dest))
            self.assertEqual(CamProject.load(dest).to_dict(),after)
            self.assertEqual(before,self.frozen_original());self.assertEqual(source,self.source.read_bytes())
    def test_cancel_clears_only_private_recovery_and_rejects_cross_document_token(self):
        before=self.frozen_original();self.ok(self.target,'project.new',name='Artwork')
        d=self.preview(self.target); spare=self.bridge('spare'); self.ok(spare,'project.new',name='Other')
        self.assertFalse(self.call(spare,'design.svg_apply',token=d['token'],reviewed=True)['ok'])
        self.ok(self.target,'recovery.clear')
        self.assertEqual(before,self.frozen_original());self.assertNotEqual(self.target.recovery.identity,self.original.recovery.identity)
        self.assertFalse(self.target.session.project.entities)
    def test_changed_source_or_destination_refuses_apply_without_affecting_original(self):
        before=self.frozen_original();self.ok(self.target,'project.new',name='Artwork');d=self.preview(self.target)
        self.source.write_text(ART+'\n',encoding='utf-8')
        self.assertFalse(self.call(self.target,'design.svg_apply',token=d['token'],reviewed=True)['ok'])
        self.assertFalse(self.target.session.project.entities)
        d=self.preview(self.target);self.ok(self.target,'geometry.add_circle',x=5,y=5,diameter=1)
        changed=copy.deepcopy(self.target.session.project.to_dict())
        self.assertFalse(self.call(self.target,'design.svg_apply',token=d['token'],reviewed=True)['ok'])
        self.assertEqual(changed,self.target.session.project.to_dict());self.assertEqual(before,self.frozen_original())

if __name__=='__main__':unittest.main()
