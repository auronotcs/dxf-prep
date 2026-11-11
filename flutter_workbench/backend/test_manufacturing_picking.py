import copy,math
from test_manufacturing_management import ManufacturingManagementTests as Base
from manufacturing_picking import info,point_at,contour
from cam_core.model import Point,Rectangle,Circle,Polyline,OffsetSide

class PickingTests(Base):
    def pick(self,kind,**params):
        op=self.b.session.project.operations[0]
        data=self.run_command('manufacturing.picking_info',id=op.id,kind=kind)['manufacturing']
        return data,dict(id=op.id,kind=kind,fingerprint=data['fingerprint'],**params)

    def test_start_units_core_markers_history_and_manual_tabs_stay_put(self):
        for units in ('in','mm'):
            self.fixture(units);f=25.4 if units=='mm' else 1
            data,p=self.pick('start',index=1);self.assertEqual(len(data['shape']['starts']),4)
            self.apply_workflow(self.workflow('graphical',p));self.assertEqual(self.b.session.project.operations[0].start_index,1)
            self.parity('picked-start-'+units)
            data,p=self.pick('tabs',tab_width=.25*f,tab_height=.08*f)
            p['points']=[point_at(data['shape'],v) for v in (.17,.63)]
            self.apply_workflow(self.workflow('graphical',p))
            job=self.b.session.project;op=job.operations[0];old=[point_at(contour(job,op,True),v) for v in op.manual_tab_positions]
            _,p=self.pick('start',index=2);self.apply_workflow(self.workflow('graphical',p))
            job=self.b.session.project;op=job.operations[0];new=[point_at(contour(job,op,True),v) for v in op.manual_tab_positions]
            for a in old:self.assertTrue(any(math.hypot(a['x']-b['x'],a['y']-b['y'])<1e-7*f for b in new))
            self.parity('picked-tabs-'+units)

    def test_circle_quadrants_and_analytic_tab_projection(self):
        self.fixture();job=self.b.session.project;eid=job.entities[0].id
        job.entities=[Circle(id=eid,center=Point(5,5),radius=2)];self.freeze()
        data,p=self.pick('start',index=3);self.assertAlmostEqual(data['shape']['starts'][0]['y'],2.875)
        self.apply_workflow(self.workflow('graphical',p))
        data,p=self.pick('tabs',tab_width=.2,tab_height=.05)
        p['points']=[point_at(data['shape'],v) for v in (.2,.6)]
        self.apply_workflow(self.workflow('graphical',p));self.parity('picked-circle-tabs')
        values=self.b.session.project.operations[0].manual_tab_positions
        self.assertAlmostEqual(values[0],.2);self.assertAlmostEqual(values[1],.6)

    def fill_fixture(self,units='in'):
        self.fixture(units);f=25.4 if units=='mm' else 1
        op=self.b.session.project.operations[0];self.run_command('manufacturing.operation_delete',id=op.id)
        self.run_command('geometry.add_rectangle',x=3*f,y=3*f,width=f,height=f);island=self.b.session.project.entities[-1].id
        self.run_command('geometry.add_rectangle',x=12*f,y=2*f,width=4*f,height=4*f);outer=self.b.session.project.entities[-1].id
        source=self.b.session.project.entities[0].id
        self.run_command('manufacturing.fill_save',name='Pocket',tool_station=1,entity_ids=[source],island_entity_ids=[],target_depth=.1*f,safe_z=.25*f,stepdown=.1*f,stepover=.15*f,cut_direction='climb')
        return source,island,outer

    def test_boundary_and_island_picks_units_parity_and_empty_islands(self):
        for units in ('in','mm'):
            source,island,outer=self.fill_fixture(units)
            _,p=self.pick('islands',ids=[island]);self.apply_workflow(self.workflow('graphical',p));self.parity('picked-island-'+units)
            _,p=self.pick('boundaries',ids=[source,outer]);self.apply_workflow(self.workflow('graphical',p));self.parity('picked-boundaries-'+units)
            _,p=self.pick('boundaries',ids=[outer]);self.workflow('graphical',p,'failed')
            # Removing a protected drawn island makes the cutter enter another
            # contour. The existing core overlap guard must still refuse it.
            _,p=self.pick('islands',ids=[]);self.workflow('graphical',p,'failed')
            _,p=self.pick('boundaries',ids=[]);self.workflow('graphical',p,'failed')

    def test_graphical_order_core_containment_and_auto_restore(self):
        self.fixture();job=self.b.session.project;outer=job.entities[0].id
        job.entities.append(Rectangle(id='inner',corner_a=Point(3,3),corner_b=Point(4,4)));job.operations[0].entity_ids.append('inner');self.freeze()
        _,p=self.pick('order',ids=['inner',outer]);self.apply_workflow(self.workflow('graphical',p));paths=self.parity('picked-order')
        self.assertEqual(list(dict.fromkeys(b.metadata['entity_id'] for b in next(iter(paths.values())).blocks)),['inner',outer])
        _,p=self.pick('order',ids=[outer,'inner']);self.workflow('graphical',p,'failed')
        _,p=self.pick('order',ids=[],automatic=True);self.apply_workflow(self.workflow('graphical',p));self.assertEqual(self.b.session.project.operations[0].manual_contour_order,[])

    def test_invalid_stale_cancel_and_visibility_guards(self):
        self.fixture();data,p=self.pick('start',index=99);self.workflow('graphical',p,'failed')
        data,p=self.pick('tabs',points=[],tab_width=.2,tab_height=.05);self.workflow('graphical',p,'failed')
        data,p=self.pick('start',index=1)
        self.run_command('manufacturing.setup',material='Acrylic');self.workflow('graphical',p,'failed')
        _,p=self.pick('start',index=1);d=self.workflow('graphical',p);self.run_command('manufacturing.cancel')
        self.assertFalse(self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))['ok'])
        job=self.b.session.project;job.entities[0].metadata['annotation_hidden']=True
        with self.assertRaisesRegex(ValueError,'visible'):info(job,dict(id=job.operations[0].id,kind='start'))

    def test_polyline_compensation_direction_and_rejected_dependent_source(self):
        for side in (OffsetSide.OUTSIDE,OffsetSide.INSIDE):
            for direction in ('climb','conventional'):
                self.fixture();job=self.b.session.project;eid=job.entities[0].id
                job.entities=[Polyline(id=eid,points=[Point(2,2),Point(8,2),Point(7,6),Point(3,6)],closed=True)]
                op=job.operations[0];op.side=side;op.cut_direction=direction;self.freeze()
                data,p=self.pick('start',index=2)
                self.assertEqual(len(data['shape']['starts']),4)
                self.apply_workflow(self.workflow('graphical',p));self.parity('polyline-'+side.value+'-'+direction)
        job=self.b.session.project;source=job.operations[0];dependent=copy.deepcopy(source);dependent.id='dependent';dependent.depends_on_ids=[source.id];job.operations.append(dependent)
        with self.assertRaisesRegex(ValueError,'Dependent'):info(job,dict(id=source.id,kind='start'))
        job.operations.pop();source.geometry_selector={'layer':'0'}
        with self.assertRaisesRegex(ValueError,'associative'):info(job,dict(id=source.id,kind='start'))

    def test_review_cannot_apply_after_revision_and_invalid_tab_points(self):
        self.fixture();_,p=self.pick('start',index=1);d=self.workflow('graphical',p)
        self.run_command('manufacturing.setup',material='Acrylic')
        before=copy.deepcopy(self.b.session.project.to_dict())
        r=self.b.handle(dict(command='manufacturing.management_apply',params=dict(token=d['token'],reviewed=True),revision=self.b.revision))
        self.assertFalse(r['ok']);self.assertEqual(before,self.b.session.project.to_dict())
        _,p=self.pick('tabs',points=[dict(x=float('nan'),y=2)],tab_width=.2,tab_height=.05)
        r=self.b.handle(dict(command='manufacturing.management_prepare',params=dict(action='graphical',params=p),revision=self.b.revision));self.assertFalse(r['ok']);self.assertIn('finite',r['error'])
        _,p=self.pick('tabs',points=[dict(x=2,y=2)]*2,tab_width=.2,tab_height=.05);self.workflow('graphical',p,'failed')

for name in dir(Base):
    if name.startswith('test_') and name not in PickingTests.__dict__:setattr(PickingTests,name,None)
