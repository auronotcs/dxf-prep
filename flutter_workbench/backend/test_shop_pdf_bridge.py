import unittest
from unittest.mock import patch
import pdfplumber
from pypdf import PdfReader
from test_design_bridge import DesignBridgeTests
from cam_core.model import Point, RouterOffsetOperation
from cam_core.dimensions import DimensionAnnotation, DimensionReference


class ShopPdfBridgeTests(unittest.TestCase):
    setUp = DesignBridgeTests.setUp
    run_command = DesignBridgeTests.run_command

    def fixture(self, units='in'):
        self.run_command('project.new',units=units)
        factor=25.4 if units=='mm' else 1
        self.run_command('plate.define',plates=[dict(number=1,x=10*factor,y=5*factor,width=6*factor,height=3*factor,name='First'),
            dict(number=2,x=20*factor,y=5*factor,width=4*factor,height=3*factor,name='Second')])
        self.run_command('geometry.add_rectangle',x=11*factor,y=6*factor,width=3*factor,height=1*factor)
        self.run_command('geometry.add_circle',x=21*factor,y=6*factor,diameter=factor)
        self.addCleanup(self.bridge.shop_pdf.clear)

    def prepare(self, **extra):
        p=dict(plate_numbers=[1,2],page_size='Tabloid',orientation='landscape',scale_mode='actual',scale_denominator=1)
        p.update(extra)
        r=self.bridge.handle(dict(command='design.shop_pdf_prepare',params=p))
        self.assertTrue(r['ok'],r.get('error'))
        return r['preview']

    def export(self, preview, path=None):
        path=path or self.folder/'proof.pdf'
        r=self.bridge.handle(dict(command='design.shop_pdf_export',params=dict(token=preview['token'],path=str(path))))
        self.assertTrue(r['ok'],r.get('error'))
        return path

    def test_plate_selection_translation_actual_mm_and_no_mutation(self):
        for units in ['in','mm']:
            self.fixture(units)
            before=self.bridge.snapshot()
            p=self.prepare()
            self.assertEqual(self.bridge.snapshot(),before)
            path=self.export(p)
            self.assertEqual(self.bridge.snapshot(),before)
            reader=PdfReader(path)
            self.assertEqual(len(reader.pages),2)
            self.assertIn('Plate 1',reader.pages[0].extract_text())
            self.assertIn('Plate 2',reader.pages[1].extract_text())
            self.assertAlmostEqual(float(reader.pages[0].mediabox.width),17*72)
            with pdfplumber.open(path) as pdf:
                red=[r for r in pdf.pages[0].rects if tuple(r.get('stroking_color') or ())==(.78,.16,.16)]
                self.assertTrue(any(abs(r['width']-6*72)<.1 for r in red))
            self.assertEqual([page['scale_denominator'] for page in p['pages']],[1,1])
            p=self.prepare(plate_numbers=[2],scale_mode='custom',scale_denominator=2)
            self.assertEqual(len(p['pages']),1)
            self.assertEqual(p['pages'][0]['scale_denominator'],2)

    def test_invalid_settings_clipping_stale_and_atomic_destination(self):
        self.fixture()
        path=self.folder/'existing.pdf'; path.write_bytes(b'previous')
        for params in [dict(plate_numbers=[]),dict(plate_numbers=[99]),dict(plate_numbers=[True]),
                       dict(page_size='invalid'),dict(scale_denominator=0),dict(scale_denominator=float('inf')),
                       dict(page_size='Letter',orientation='portrait',scale_mode='custom',scale_denominator=.1)]:
            before=self.bridge.snapshot()
            r=self.bridge.handle(dict(command='design.shop_pdf_prepare',params={'plate_numbers':[1],**params}))
            self.assertFalse(r['ok'],params)
            self.assertEqual(self.bridge.snapshot(),before)
            self.assertEqual(path.read_bytes(),b'previous')
        p=self.prepare()
        with patch('shop_pdf.os.replace',side_effect=OSError('Simulated destination failure')):
            r=self.bridge.handle(dict(command='design.shop_pdf_export',params=dict(token=p['token'],path=str(path))))
        self.assertFalse(r['ok'])
        self.assertEqual(path.read_bytes(),b'previous')
        self.assertFalse(list(self.folder.glob('.shop-drawing-*')))
        self.run_command('geometry.add_rectangle',x=0,y=0,width=1,height=1)
        r=self.bridge.handle(dict(command='design.shop_pdf_export',params=dict(token=p['token'],path=str(path))))
        self.assertFalse(r['ok'])
        self.assertEqual(path.read_bytes(),b'previous')

    def test_omission_counts_and_annotations(self):
        self.fixture()
        self.run_command('geometry.add_rectangle',x=15,y=6,width=2,height=1)
        project=self.bridge.session.project
        project.dimensions.append(DimensionAnnotation('horizontal',DimensionReference(Point(11.,6.)),DimensionReference(Point(14.,6.)),offset=.4,precision=3))
        before=self.bridge.snapshot()
        p=self.prepare(plate_numbers=[1])
        self.assertEqual(p['omittedCount'],2)
        self.assertEqual(p['crossingCount'],1)
        path=self.export(p)
        self.assertIn('3.000 in',PdfReader(path).pages[0].extract_text())
        self.assertEqual(self.bridge.snapshot(),before)

    def test_production_extension_and_machining_guards(self):
        self.fixture(); p=self.prepare()
        for path in [self.folder/'wrong.camjob.json',self.bridge.shop_pdf.production_root/'never-written.pdf']:
            r=self.bridge.handle(dict(command='design.shop_pdf_export',params=dict(token=p['token'],path=str(path))))
            self.assertFalse(r['ok'])
            self.assertFalse(path.exists())
        self.bridge.session.project.operations.append(RouterOffsetOperation(name='test',tool_station=1,entity_ids=[],target_depth=.1,safe_z=.25))
        before=self.bridge.snapshot()
        self.assertFalse(self.bridge.handle(dict(command='design.shop_pdf_prepare',params=dict(plate_numbers=[1])))['ok'])
        self.assertFalse(self.bridge.handle(dict(command='design.shop_pdf_export',params=dict(token=p['token'],path=str(self.folder/'blocked.pdf'))))['ok'])
        self.assertEqual(self.bridge.snapshot(),before)


if __name__=='__main__': unittest.main()
