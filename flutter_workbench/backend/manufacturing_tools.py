"""Isolated reusable catalog and reviewed document turret, never the live rack."""
import copy,json,math
from pathlib import Path
from uuid import uuid4
from dataclasses import fields
from cam_core.model import Tool,ToolKind,ToolLibrary
from cam_core.units import unit_scale
from manufacturing_recipes import ManufacturingRecipes

GEOMETRY=('diameter','cutting_edge_length','shank_diameter','overall_length','holder_diameter','stickout')

def validate_turret(tools,minimum=8000,maximum=24000):
    if type(minimum) is not int or type(maximum) is not int or not 0<minimum<maximum<=100000:
        raise ValueError('Machine RPM limits must be positive whole numbers with minimum below maximum.')
    errors=[i.message for i in ToolLibrary(tools).validate() if i.severity=='error']
    for t in tools:
        if type(t.station) is not int: errors.append('Station must be a whole number.')
        if not isinstance(t.metadata,dict): raise ValueError('Tool metadata must be an object.')
        for key in ('spring_travel','tip_angle'):
            v=t.metadata.get(key)
            if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<=0): errors.append(f'T{t.station}: {key} must be positive and finite.')
        if not t.name.strip(): errors.append('Enter a tool name.')
        for key in (*GEOMETRY,'feed_rate','plunge_rate','max_stepdown'):
            v=getattr(t,key)
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0): errors.append(f'T{t.station}: {key} must be positive and finite.')
        if t.station==8 and t.spindle_mode!='disabled': errors.append('T8 is spindle-disabled on this router.')
        if t.spindle_mode!='disabled':
            lo=max(minimum,t.min_rpm or minimum);hi=min(maximum,t.max_rpm or maximum,12000 if t.station==6 else maximum)
            if type(t.spindle_rpm) is not int or not lo<=t.spindle_rpm<=hi: errors.append(f'T{t.station}: RPM must be within {lo}–{hi}.')
        for key in ('min_rpm','max_rpm'):
            v=getattr(t,key)
            if v is not None and (type(v) is not int or v<=0): errors.append(f'T{t.station}: RPM limits must be positive integers.')
    if errors: raise ValueError(' '.join(errors))

def prepare_turret(project,p):
    if set(p)!={'tools','minimum_rpm','maximum_rpm'} or not isinstance(p['tools'],list) or len(p['tools'])>12: raise ValueError('Review up to twelve loaded stations and machine RPM limits.')
    tools=[];allowed={f.name for f in fields(Tool)}
    for row in p['tools']:
        if not isinstance(row,dict) or set(row)-allowed: raise ValueError('Unsupported turret field.')
        row=copy.deepcopy(row);row['kind']=ToolKind(row['kind']);tools.append(Tool(**row))
    validate_turret(tools,p['minimum_rpm'],p['maximum_rpm'])
    stations={t.station for t in tools}
    if any(o.tool_station not in stations for o in project.operations): raise ValueError('A station used by an operation cannot be cleared.')
    from cam_core.turret_updates import changed_stations,affected_operations
    changed=changed_stations(project.tool_library.tools,tools)
    affected=affected_operations(project.operations,changed)
    project.tool_library.tools=tools
    project.metadata.setdefault('job_setup',{}).update(machine_min_rpm=p['minimum_rpm'],machine_max_rpm=p['maximum_rpm'])
    return dict(changed_stations=sorted(changed),affected_operations=[o.name for o in project.operations if o.id in affected],machine_rpm=[p['minimum_rpm'],p['maximum_rpm']])

class ManufacturingTools(ManufacturingRecipes):
    def __init__(self,path,seed): super().__init__(path);self.seed=Path(seed)
    def read(self):
        data={'schema':'flutter-tool-catalog-v1','tools':[]}
        if self.path.exists():
            if self.path.stat().st_size>2_000_000: raise ValueError('Tool catalog is too large.')
            data=json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema')!='flutter-tool-catalog-v1' or not isinstance(data.get('tools'),list): raise ValueError('Invalid tool catalog.')
        return data
    def command(self,action,p,project):
        from catalog_lifecycle import ACTIONS,change
        selection={}
        downgraded=[]
        if action in {'catalog_save','catalog_delete','catalog_rack_save','catalog_update'}|ACTIONS:
            with self.locked():
                data=self.read()
                if p.get('version')!=self.version(data): raise ValueError('Tool library changed. Reopen it before saving.')
                if action in ACTIONS:
                    selection=change(data,action,p)
                elif action=='catalog_update':
                    from catalog_edit import revise
                    index=next((i for i,t in enumerate(data['tools']) if t['id']==p.get('id')),None)
                    if index is None: raise ValueError('Only custom library entries can be edited.')
                    row,downgraded=revise(data['tools'][index],p)
                    data['tools'][index]=row
                elif action=='catalog_rack_save':
                    setup=project.metadata.get('job_setup',{})
                    minimum=setup.get('machine_min_rpm',8000);maximum=setup.get('machine_max_rpm',24000)
                    validate_turret(project.tool_library.tools,minimum,maximum)
                    data['rack']=dict(units=project.units,tools=[t.to_dict() for t in project.tool_library.tools],minimum_rpm=minimum,maximum_rpm=maximum)
                elif action=='catalog_delete':
                    if not any(t['id']==p.get('id') for t in data['tools']): raise ValueError('Only custom library entries can be deleted.')
                    data['tools']=[t for t in data['tools'] if t['id']!=p['id']]
                else:
                    tool=project.tool_library.by_station(p.get('station'))
                    if tool is None: raise ValueError('Choose a loaded station to save to the library.')
                    f=unit_scale(project.units,'in');row=dict(id='custom-'+uuid4().hex,name=tool.name,kind=tool.kind.value,spindle_mode=tool.spindle_mode,min_rpm=tool.min_rpm,max_rpm=tool.max_rpm,custom=True)
                    row['specialized_metadata']={k:copy.deepcopy(tool.metadata[k]) for k in ('spring_travel','tip_angle','thread_tpi','thread_form') if k in tool.metadata}
                    if 'spring_travel' in row['specialized_metadata']: row['specialized_metadata']['spring_travel']*=f
                    for k in GEOMETRY:
                        if getattr(tool,k) is not None: row[k+'_in']=getattr(tool,k)*f
                    row['recipes']=[dict(material='Saved station settings',rpm=tool.spindle_rpm,feed=(tool.feed_rate or 0)*60*f,plunge=(tool.plunge_rate or 0)*60*f,stepdown=(tool.max_stepdown or 0)*f)]
                    data['tools'].append(row)
                self.write(data)
        data=self.read()
        entries=list({row['id']:row for row in [*json.loads(self.seed.read_text(encoding='utf-8')),*data['tools']]}.values())
        if action in {'catalog_matches','catalog_match_resolve'}:
            from catalog_matching import list_matches,resolve
            return (list_matches if action=='catalog_matches' else resolve)(project,entries,self.version(data),p)
        if action=='catalog_rack_resolve':
            if p.get('version')!=self.version(data): raise ValueError('Library changed; reopen it.')
            rack=data.get('rack')
            if not rack: raise ValueError('No saved Flutter turret preset. Review and apply a rack, then save it.')
            from cam_core.model import CamProject
            from cam_core.units import convert_project_units
            payload=CamProject('Turret preset',units=rack['units']).to_dict()
            payload['tool_library']={'capacity':12,'tools':copy.deepcopy(rack['tools'])}
            converted=convert_project_units(CamProject.from_dict(payload),project.units)
            return dict(rack=dict(tools=[t.to_dict() for t in converted.tool_library.tools],minimum_rpm=rack['minimum_rpm'],maximum_rpm=rack['maximum_rpm']))
        if action=='catalog_resolve':
            if p.get('version')!=self.version(data): raise ValueError('Library changed; reopen it.')
            row=next((t for t in entries if t['id']==p.get('id')),None)
            if row is None: raise ValueError('Tool no longer exists.')
            index=p.get('recipe',0);recipes=row.get('recipes',[])
            if type(index) is not int or not 0<=index<len(recipes): raise ValueError('Choose a tool recipe.')
            r=recipes[index];f=unit_scale('in',project.units)
            if r.get('qualification')=='retired':raise ValueError('This recipe is retired. Reactivate it as Prototype before loading.')
            t=Tool(p['station'],row['name'],float(row['diameter_in'])*f,kind=ToolKind(row['kind']),spindle_mode=row.get('spindle_mode','required'),spindle_rpm=r.get('rpm'),min_rpm=row.get('min_rpm'),max_rpm=row.get('max_rpm'),feed_rate=float(r.get('feed') or 0)*f/60,plunge_rate=float(r.get('plunge') or 0)*f/60,max_stepdown=float(r.get('stepdown') or 0)*f,
                metadata=dict(catalog_tool_id=row['id'],material_recipe=r.get('material'),loaded_recipe=copy.deepcopy(r),compatible_materials=row.get('compatible_materials',[]),part_number=row.get('part_number','')))
            for k in GEOMETRY:
                if row.get(k+'_in') is not None:setattr(t,k,float(row[k+'_in'])*f)
            for k in ('spring_travel','tip_angle','thread_tpi','thread_form'):
                v=row.get('specialized_metadata',{}).get(k,row.get(k))
                if v is not None:t.metadata[k]=v*f if k=='spring_travel' else v
            return dict(tool=t.to_dict())
        if action not in {'catalog_list','catalog_save','catalog_delete','catalog_rack_save','catalog_update'}|ACTIONS: raise ValueError('Unknown library action.')
        return dict(catalog=entries,version=self.version(data),hasRack=bool(data.get('rack')),editCustom=True,editScope=True,matchRecipes=True,manageRecipes=True,downgradedRecipes=downgraded,**selection)
