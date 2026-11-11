import copy
import unittest
from test_design_bridge import DesignBridgeTests
from cam_core.model import RouterOffsetOperation
from cam_core.dxf import import_dxf


class GuidesBridgeTests(unittest.TestCase):
    setUp=DesignBridgeTests.setUp
    run_command=DesignBridgeTests.run_command
    def request(self,command,**params):
        result=self.bridge.handle(dict(command=command,params=params,revision=self.bridge.revision))
        self.assertTrue(result['ok'],result.get('error'))
        return result
    def preview(self,actions):
        before=self.bridge.snapshot()
        result=self.request('design.guides_preview',actions=actions)['preview']
        self.assertEqual(self.bridge.snapshot(),before)
        return result

    def test_add_units_history_save_reopen_and_stable_identity(self):
        for units,factor in [('in',1),('mm',25.4)]:
            self.run_command('project.new',units=units)
            self.run_command('geometry.add_line',x1=0,y1=0,x2=10,y2=0)
            entities=copy.deepcopy(self.bridge.session.project.to_dict()['entities'])
            selected=set(self.bridge.session.selected_ids)
            self.run_command('design.guide_add',orientation='horizontal',position=2.25*factor)
            self.run_command('design.guide_add',orientation='vertical',position=-3*factor,name='left datum')
            expected=copy.deepcopy(self.bridge.session.project.to_dict())
            self.assertEqual(expected['entities'],entities)
            self.assertEqual(self.bridge.session.selected_ids,selected)
            self.run_command('history.undo');self.assertEqual(len(self.bridge.session.project.guides),1)
            self.run_command('history.redo');self.assertEqual(self.bridge.session.project.to_dict(),expected)
            path=self.folder/f'{units}.camjob.json'
            self.run_command('project.save',path=str(path));self.run_command('project.open',path=str(path))
            self.assertEqual(self.bridge.session.project.to_dict(),expected)

    def test_draft_edit_visibility_lock_order_and_single_undo(self):
        self.run_command('design.guide_add',orientation='horizontal',position=5)
        identity=self.bridge.session.project.guides[0].id
        original=copy.deepcopy(self.bridge.session.project.to_dict())
        actions=[dict(operation='locked',id=identity,value=True)]
        before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.guides_preview',params=dict(actions=actions+[dict(operation='edit',id=identity,position=8)])))
        self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)
        actions += [dict(operation='locked',id=identity,value=False),dict(operation='edit',id=identity,position=8,name='datum'),
                    dict(operation='visible',id=identity,value=False),dict(operation='add',id='new-guide',orientation='vertical',position=2),
                    dict(operation='lock_all',value=True)]
        preview=self.preview(actions)
        self.assertEqual(preview['guides'][0]['position'],8)
        history=self.bridge.session._history_index
        self.run_command('design.guides_apply',token=preview['token'])
        self.assertEqual(self.bridge.session._history_index,history+1)
        self.assertFalse(self.bridge.session.project.guides[0].visible)
        self.assertTrue(all(g.locked for g in self.bridge.session.project.guides))
        self.run_command('history.undo');self.assertEqual(self.bridge.session.project.to_dict(),original)
        self.run_command('history.redo');self.assertEqual(len(self.bridge.session.project.guides),2)

    def test_delete_requires_unlock_in_draft_and_cancel_is_nonmutating(self):
        self.run_command('design.guide_add',orientation='vertical',position=3)
        identity=self.bridge.session.project.guides[0].id
        preview=self.preview([dict(operation='lock_all',value=True)])
        self.run_command('design.guides_apply',token=preview['token'])
        before=self.bridge.snapshot()
        result=self.bridge.handle(dict(command='design.guides_preview',params=dict(actions=[dict(operation='delete',id=identity)])))
        self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)
        preview=self.preview([dict(operation='locked',id=identity,value=False),dict(operation='delete',id=identity)])
        self.assertEqual(self.bridge.snapshot(),before)
        self.run_command('design.guides_apply',token=preview['token'])
        self.assertEqual(self.bridge.session.project.guides,[])
        self.run_command('history.undo');self.assertTrue(self.bridge.session.project.guides[0].locked)

    def test_invalid_stale_and_unchanged_drafts_are_atomic(self):
        for data in [dict(orientation='diagonal',position=0),dict(orientation='vertical',position=float('nan')),
                     dict(orientation='horizontal',position=True),dict(orientation='horizontal',position='inf')]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle(dict(command='design.guide_add',params=data))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        for actions in [[dict(operation='add',id='same',orientation='vertical',position=1)]*2,
                        [dict(operation='show_all',value='false')],[dict(operation='edit',id='missing',position=1)]]:
            before=self.bridge.snapshot()
            self.assertFalse(self.bridge.handle(dict(command='design.guides_preview',params=dict(actions=actions)))['ok'])
            self.assertEqual(self.bridge.snapshot(),before)
        preview=self.preview([]);history=self.bridge.session._history_index
        self.run_command('design.guides_apply',token=preview['token'])
        self.assertEqual(self.bridge.session._history_index,history)
        preview=self.preview([dict(operation='add',id='draft',orientation='vertical',position=1)])
        self.run_command('design.guide_add',orientation='horizontal',position=3)
        self.assertFalse(self.bridge.handle(dict(command='design.guides_apply',params=dict(token=preview['token'])))['ok'])
        preview=self.preview([]);self.bridge.session.project.metadata['outside_change']=True
        self.assertFalse(self.bridge.handle(dict(command='design.guides_apply',params=dict(token=preview['token'])))['ok'])

    def test_guides_never_enter_cutting_export_or_geometry_transforms(self):
        self.run_command('geometry.add_rectangle',x=0,y=0,width=4,height=3)
        self.run_command('design.guide_add',orientation='vertical',position=1)
        guides=copy.deepcopy(self.bridge.snapshot()['guides'])
        self.run_command('geometry.transform',dx=5,dy=5)
        self.assertEqual(self.bridge.snapshot()['guides'],guides)
        path=self.folder/'cutting.dxf';self.run_command('project.export_dxf',path=str(path))
        output=import_dxf(path).project
        self.assertEqual(len(output.entities),1);self.assertEqual(output.guides,[])
        self.run_command('geometry.delete')
        self.assertEqual(self.bridge.snapshot()['guides'],guides)

    def test_machining_jobs_reject_guide_mutations(self):
        self.bridge.session.project.operations.append(RouterOffsetOperation('Cut',1,[],.1,.2))
        for command,params in [('design.guide_add',dict(orientation='vertical',position=1)),
                               ('design.guides_preview',dict(actions=[])),('design.guides_apply',dict(token='no'))]:
            before=self.bridge.snapshot()
            result=self.bridge.handle(dict(command=command,params=params))
            self.assertFalse(result['ok']);self.assertEqual(self.bridge.snapshot(),before)


if __name__=='__main__': unittest.main()
