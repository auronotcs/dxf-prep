import copy
import unittest
from pypdf import PdfReader
from test_design_bridge import DesignBridgeTests
from cam_core.model import RouterOffsetOperation


class DimensionsBridgeTests(unittest.TestCase):
    setUp=DesignBridgeTests.setUp
    run_command=DesignBridgeTests.run_command

    def request(self,command,**params):
        result=self.bridge.handle(dict(command=command,params=params,revision=self.bridge.revision))
        self.assertTrue(result['ok'],result.get('error'))
        return result

    def data(self,kind='aligned',factor=1,**extra):
        def ref(x,y): return dict(entity_id='',feature='free',fallback=dict(x=x*factor,y=y*factor))
        return dict(kind=kind,start=ref(1,1),end=ref(4,3),vertex=ref(1,3),offset=.5*factor,
                    precision=3,text_height=.18*factor,extension_offset=.0625*factor,layer='DIMENSIONS',
                    metadata=dict(angle_mode='minor',text='Check fit'),**extra)

    def preview(self,data,identity=None):
        before=self.bridge.snapshot()
        result=self.request('design.dimension_preview',annotation=data,id=identity)['preview']
        self.assertEqual(self.bridge.snapshot(),before)
        return result

    def create(self,data):
        preview=self.preview(data)
        self.run_command('design.dimension_apply',token=preview['token'])
        return self.bridge.session.project.dimensions[-1].id

    def test_all_kinds_units_preview_apply_and_roundtrip_without_cutting_entities(self):
        for units,factor in [('in',1),('mm',25.4)]:
            self.run_command('project.new',units=units)
            for kind in ('horizontal','vertical','aligned','radius','diameter','angle','leader','note'):
                data=self.data(kind,factor)
                self.create(data)
            self.assertEqual(len(self.bridge.snapshot()['dimensions']),8)
            self.assertEqual(self.bridge.session.project.entities,[])
            labels=[d['label'] for d in self.bridge.snapshot()['dimensions']]
            self.assertIn(f'{3*factor:.3f} {units}',labels)
            self.assertTrue(any('90.000' in label for label in labels))
            before=self.bridge.session.project.to_dict()
            self.run_command('history.undo'); self.assertEqual(len(self.bridge.session.project.dimensions),7)
            self.run_command('history.redo'); self.assertEqual(self.bridge.session.project.to_dict(),before)
            path=self.folder/f'{units}.camjob.json'
            self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
            self.assertEqual(self.bridge.session.project.to_dict(),before)

    def test_associations_follow_transform_and_deleted_sources_keep_latest_fallback(self):
        state=self.run_command('geometry.add_line',x1=2,y1=2,x2=5,y2=2)
        refs=self.request('design.dimension_context')['references']
        data=self.data('horizontal')
        data['start']=refs[0]['reference'];data['end']=refs[1]['reference']
        identity=self.create(data)
        self.run_command('geometry.transform',dx=10,dy=5)
        item=self.bridge.snapshot()['dimensions'][0]
        self.assertEqual(item['dimension_start']['x'],12)
        self.assertEqual(item['annotation']['start']['fallback'],dict(x=12.,y=7.))
        self.run_command('geometry.delete')
        broken=self.bridge.snapshot()['dimensions'][0]
        self.assertFalse(broken['resolved']);self.assertIn('UNRESOLVED',broken['label'])
        self.assertEqual(broken['start'],dict(x=12.,y=7.))
        self.run_command('history.undo');self.assertTrue(self.bridge.snapshot()['dimensions'][0]['resolved'])
        self.run_command('history.redo');self.assertFalse(self.bridge.snapshot()['dimensions'][0]['resolved'])
        self.assertEqual(self.bridge.session.project.dimensions[0].id,identity)

    def test_circle_pick_edit_identity_metadata_and_atomic_validation(self):
        self.run_command('geometry.add_circle',x=5,y=5,diameter=4)
        center=self.request('design.dimension_pick',x=5.001,y=5,tolerance=.1)['reference']
        right=self.request('design.dimension_pick',x=7,y=5,tolerance=.1)['reference']
        self.assertEqual(center['feature'],'circle:center')
        data=self.data('radius');data.update(start=center,end=right);data['metadata']['custom']='retain'
        identity=self.create(data)
        self.assertEqual(self.bridge.snapshot()['dimensions'][0]['label'],'R 2.000 in')
        edited=copy.deepcopy(self.bridge.snapshot()['dimensions'][0]['annotation']);edited['precision']=1;edited['offset']=1
        preview=self.preview(edited,identity)
        self.run_command('design.dimension_apply',token=preview['token'])
        self.assertEqual(self.bridge.session.project.dimensions[0].id,identity)
        self.assertEqual(self.bridge.session.project.dimensions[0].metadata['custom'],'retain')
        self.assertEqual(self.bridge.snapshot()['dimensions'][0]['label'],'R 2.0 in')
        for changes in [dict(precision=1.2),dict(precision=7),dict(text_height=0),dict(extension_offset=-1),dict(offset=float('nan')),
                        dict(start={'entity_id':'bad','feature':'line:start','fallback':dict(x=0,y=0)}),dict(kind='angle',vertex=None)]:
            before=self.bridge.snapshot()
            result=self.bridge.handle(dict(command='design.dimension_preview',params=dict(annotation={**edited,**changes},id=identity)))
            self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)

    def test_stale_tokens_document_changes_and_machining_protection(self):
        preview=self.preview(self.data())
        self.run_command('geometry.add_line',x1=1,y1=1,x2=3,y2=1)
        self.assertFalse(self.bridge.handle(dict(command='design.dimension_apply',params=dict(token=preview['token'])))['ok'])
        preview=self.preview(self.data())
        self.bridge.session.project.metadata['external_edit']=True
        self.assertFalse(self.bridge.handle(dict(command='design.dimension_apply',params=dict(token=preview['token'])))['ok'])
        self.bridge.session.project.operations.append(RouterOffsetOperation(name='Cut',tool_station=1,entity_ids=[],target_depth=.1,safe_z=.25))
        self.request('design.dimension_context')
        for cmd,params in [('design.dimension_preview',dict(annotation=self.data())),('design.dimension_delete',dict(id='anything'))]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle(dict(command=cmd,params=params))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)

    def test_annotation_layer_visibility_lock_delete_and_undo(self):
        identity=self.create(self.data())
        self.run_command('layer.set',name='DIMENSIONS',locked=True)
        for command,params in [('design.dimension_delete',dict(id=identity)),('design.dimension_preview',dict(id=identity,annotation=self.data()))]:
            self.assertFalse(self.bridge.handle(dict(command=command,params=params))['ok'])
        self.run_command('layer.set',name='DIMENSIONS',locked=False,visible=False)
        self.assertFalse(self.bridge.handle(dict(command='design.dimension_delete',params=dict(id=identity)))['ok'])
        self.run_command('layer.set',name='DIMENSIONS',visible=True)
        self.run_command('design.delete_layer',name='DIMENSIONS')
        self.assertEqual(self.bridge.session.project.dimensions[0].layer,'0')
        self.run_command('history.undo');self.assertEqual(self.bridge.session.project.dimensions[0].layer,'DIMENSIONS')
        self.run_command('design.dimension_delete',id=identity)
        self.assertEqual(self.bridge.session.project.dimensions,[])
        self.run_command('history.undo');self.assertEqual(self.bridge.session.project.dimensions[0].id,identity)

    def test_authored_annotation_appears_in_pdf_and_not_dxf_cutting_entities(self):
        self.create(self.data('horizontal'))
        p=self.request('design.shop_pdf_prepare',plate_numbers=[1],page_size='Letter',orientation='landscape',scale_mode='fit')['preview']
        self.addCleanup(self.bridge.shop_pdf.clear)
        output=self.folder/'dimensions.pdf'
        self.request('design.shop_pdf_export',token=p['token'],path=str(output))
        self.assertIn('3.000 in',PdfReader(output).pages[0].extract_text())
        self.run_command('geometry.add_line',x1=0,y1=0,x2=4,y2=0)
        output=self.folder/'cutting.dxf'
        self.run_command('project.export_dxf',path=str(output))
        from cam_core.dxf import import_dxf
        self.assertEqual(len(import_dxf(output).project.entities),1)


if __name__=='__main__': unittest.main()
