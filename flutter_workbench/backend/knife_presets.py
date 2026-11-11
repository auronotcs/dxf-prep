"""Private knife presets. Resolving settings never mutates or approves a job."""
import copy
import json
import math
from pathlib import Path
from cam_core.model import DragKnifeOperation, ToolKind
from cam_core.knife_recipe_store import SCHEMA, tool_identity
from cam_core.drag_knife_ui import FIELDS, knife_recipe_values
from cam_core.units import unit_scale
from manufacturing_recipes import ManufacturingRecipes
from manufacturing_picking import fingerprint


class KnifePresets(ManufacturingRecipes):
    @staticmethod
    def validate(data):
        if not isinstance(data,dict) or data.get('schema')!=SCHEMA or not isinstance(data.get('recipes'),list):
            raise ValueError('Choose a CAM Workbench knife recipe JSON file.')
        seen=set()
        for record in data['recipes']:
            if not isinstance(record,dict): raise ValueError('Invalid knife preset.')
            name=record.get('name');identity=record.get('catalog_tool_id')
            if not isinstance(name,str) or not name.strip() or len(name)>100 or not isinstance(identity,str) or not identity:
                raise ValueError('Knife preset needs a name and tool identity.')
            if not isinstance(record.get('material'),str): raise ValueError('Knife preset needs a material.')
            key=(identity,name.casefold())
            if key in seen: raise ValueError('Duplicate knife preset name for this tool; choose a new name.')
            seen.add(key)
            knife_recipe_values(record,'in')
        if len(json.dumps(data,allow_nan=False).encode())>1024*1024: raise ValueError('Knife preset library exceeds 1 MB.')
        return data

    def read_file(self,path):
        path=Path(path)
        if path.stat().st_size>1024*1024: raise ValueError('Knife preset file exceeds 1 MB.')
        return self.validate(json.loads(path.read_text(encoding='utf-8-sig')))

    def read(self):
        return self.read_file(self.path) if self.path.exists() else dict(schema=SCHEMA,recipes=[])

    @staticmethod
    def matching(record,project):
        f=unit_scale('in',project.units);setup=project.metadata.get('job_setup',{})
        if str(setup.get('material','')).strip().casefold()!=record['material'].strip().casefold():
            return [],'Material differs from the saved preset.'
        if not math.isclose(float(setup.get('material_thickness',0))/f,float(record['stock_thickness_in']),abs_tol=1e-6):
            return [],'Stock thickness differs from the saved preset.'
        tools=[t for t in project.tool_library.tools if t.kind==ToolKind.DRAG_KNIFE and tool_identity(t)==record['catalog_tool_id']]
        return tools,'' if tools else 'Load the matching knife in Turret Setup.'

    def command(self,action,p,project):
        if action in {'knife_save','knife_import'}:
            with self.locked():
                data=self.read()
                if p.get('version')!=self.version(data): raise ValueError('Knife presets changed; reopen the browser.')
                if p.get('fingerprint')!=fingerprint(project): raise ValueError('Job changed; reopen knife presets.')
                if action=='knife_import':
                    imported=self.read_file(p['path'])
                    for record in imported['recipes']:
                        record=copy.deepcopy(record);record['qualification']='prototype'
                        data['recipes'].append(record)
                else:
                    op=next((o for o in project.operations if o.id==p.get('operation_id')),None)
                    if type(op) is not DragKnifeOperation or not op.enabled: raise ValueError('Choose an applied enabled knife operation.')
                    tool=project.tool_library.by_station(op.tool_station)
                    if tool is None or tool.kind!=ToolKind.DRAG_KNIFE: raise ValueError('The knife is no longer loaded.')
                    f=unit_scale(project.units,'in');values={}
                    for _,key in FIELDS:
                        value=getattr(op,key)
                        if key=='feed_rate_override': value=tool.feed_rate if value is None else value
                        if key=='plunge_rate_override': value=tool.plunge_rate if value is None else value
                        values[key]=value*(60 if key.endswith('rate_override') else 1)*(1 if key in {'passes','swivel_angle'} else f)
                    values['passes']=op.passes
                    values.update(outside_corner_loops=op.outside_corner_loops,edge_aware_corners=op.edge_aware_corners,
                        reverse_paths=op.reverse_paths,loop_radius=op.loop_radius*f,loop_feed_percent=op.loop_feed_percent)
                    # Omit legacy null feeds: the core recipe reader derives their equivalent explicit rates.
                    for key in ('corner_feed_ipm','lead_in_feed_ipm'):
                        if getattr(op,key) is not None: values[key]=getattr(op,key)
                    setup=project.metadata.get('job_setup',{})
                    data['recipes'].append(dict(name=str(p.get('name','')).strip(),units='in',catalog_tool_id=tool_identity(tool),
                        material=str(setup.get('material','')),stock_thickness_in=float(setup.get('material_thickness',0))*f,
                        qualification='prototype',values=values))
                self.validate(data);self.write(data)
            return self.command('knife_list',{},project)
        data=self.read()
        if action=='knife_list':
            rows=[]
            for i,r in enumerate(data['recipes']):
                tools,reason=self.matching(r,project)
                rows.append(dict(r,index=i,compatible=bool(tools),reason=reason,stations=[t.station for t in tools]))
            return dict(version=self.version(data),fingerprint=fingerprint(project),recipes=rows)
        if action!='knife_resolve': raise ValueError('Unknown knife preset action.')
        if p.get('version')!=self.version(data) or p.get('fingerprint')!=fingerprint(project):
            raise ValueError('Job or presets changed; reopen the browser.')
        index=p.get('index')
        if type(index) is not int or not 0<=index<len(data['recipes']): raise ValueError('Choose a knife preset.')
        record=data['recipes'][index];tools,reason=self.matching(record,project)
        tool=next((t for t in tools if t.station==p.get('station')),None)
        if tool is None: raise ValueError(reason or 'Choose the matching loaded knife station.')
        values=knife_recipe_values(record,project.units)
        for key in ('feed_rate_override','plunge_rate_override'): values[key]/=60
        values.update(name=record['name'],tool_station=tool.station,setup_verified=False)
        return dict(values=values)
