import copy
import json
from pathlib import Path
import tempfile
import unittest
from design_bridge import Bridge
from cam_core.appearance import ObjectAppearance


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bridge = Bridge(self.root/'settings.json')
        self.source = self.root/'source.json'
        self.palette = dict(palette_schema='cam-workbench-color-palette',palette_schema_version=1,
            id='synthetic',name='Test colors',manufacturer='Test maker',collection='Test collection',
            source='Synthetic fixture',version='v1',swatches=[dict(id='blue',name='Exact blue',product_code='T-123',
            color=dict(model='cmyk',channels=[88.123456789,22,0,1],preview_rgb=[30,190,250]))])
        self.source.write_text(json.dumps(self.palette))

    def run_command(self, command, **params):
        result = self.bridge.handle(dict(command=command,params=params))
        self.assertTrue(result['ok'],result.get('error'))
        return result

    def import_palette(self):
        review=self.run_command('palette.import_preview',path=str(self.source))
        self.run_command('palette.import_apply',token=review['token'])
        return review

    def test_import_review_cancel_restart_search_data_and_invalid_input(self):
        before=self.bridge.snapshot()
        review=self.run_command('palette.import_preview',path=str(self.source))
        self.assertEqual(review['count'],1)
        self.assertEqual(self.run_command('palette.list')['palettes'],[])
        source_bytes=self.source.read_bytes()
        self.run_command('palette.import_apply',token=review['token'])
        self.assertEqual(self.source.read_bytes(),source_bytes)
        self.assertEqual(self.bridge.snapshot(),before)
        self.bridge=Bridge(self.root/'settings.json')
        palette=self.run_command('palette.list')['palettes'][0]
        self.assertEqual(palette['swatches'][0]['product_code'],'T-123')
        self.assertEqual(palette['swatches'][0]['color']['channels'][0],88.123456789)
        stored=self.bridge.palette_library.path.read_bytes()
        self.source.write_text('{broken')
        self.assertFalse(self.bridge.handle(dict(command='palette.import_preview',params={'path':str(self.source)}))['ok'])
        self.assertEqual(self.bridge.palette_library.path.read_bytes(),stored)

    def test_snapshot_survives_replacement_removal_apply_undo_and_save(self):
        self.import_palette()
        chosen=self.run_command('palette.choose',palette_id='synthetic',swatch_id='blue')
        self.palette['version']='v2';self.palette['swatches'][0]['color']['channels'][0]=50
        self.source.write_text(json.dumps(self.palette))
        self.assertEqual(self.import_palette()['replaces'],'Test colors')
        listing=self.run_command('palette.list')
        self.run_command('palette.remove',palette_id='synthetic',digest=listing['digest'])
        self.run_command('geometry.add_rectangle',x=0,y=0,width=10,height=5)
        identity=self.bridge.snapshot()['selected'][0]
        appearance=ObjectAppearance(fill_enabled=True).to_dict();appearance['fill']=chosen['color']
        history=self.bridge.snapshot()['history']['index']
        result=self.run_command('design.appearance',entity_ids=[identity],expected_revision=self.bridge.revision,
            appearance=appearance,palette_tokens={'fill':chosen['token']})['state']
        self.assertEqual(result['entities'][0]['appearance']['fill'],chosen['color'])
        self.assertEqual(result['history']['index'],history+1)
        self.run_command('history.undo')
        self.assertIsNone(self.bridge.snapshot()['entities'][0]['appearance'])
        self.run_command('history.redo')
        path=self.root/'drawing.camjob.json';self.run_command('project.save',path=str(path))
        self.run_command('project.new');result=self.run_command('project.open',path=str(path))['state']
        self.assertEqual(result['entities'][0]['appearance']['fill'],chosen['color'])
        self.assertEqual(self.run_command('palette.list')['palettes'],[])

    def test_library_drift_and_forged_swatch_fail_without_changes(self):
        self.import_palette()
        review=self.run_command('palette.import_preview',path=str(self.source))
        listing=self.run_command('palette.list')
        self.run_command('palette.remove',palette_id='synthetic',digest=listing['digest'])
        self.assertFalse(self.bridge.handle(dict(command='palette.import_apply',params={'token':review['token']}))['ok'])
        self.import_palette()
        chosen=self.run_command('palette.choose',palette_id='synthetic',swatch_id='blue')
        self.run_command('geometry.add_rectangle',x=0,y=0,width=10,height=5)
        appearance=ObjectAppearance(fill_enabled=True).to_dict();appearance['fill']=copy.deepcopy(chosen['color'])
        appearance['fill']['channels']=(0,0,0,0)
        before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.appearance',params=dict(entity_ids=before['selected'],
            expected_revision=self.bridge.revision,appearance=appearance,palette_tokens={'fill':chosen['token']})))
        self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)


if __name__ == '__main__': unittest.main()
