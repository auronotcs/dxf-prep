"""Conversion-owned second-tool recipes; saved settings never approve motion."""
import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from cam_core.second_tool_recipe import SCHEMA, FIELDS, validate_record, tool_identity, matches_tool
from cam_core.profile_group_edit import resolve_profile_group_edit
from cam_core.units import unit_scale
from cam_core.model import ToolKind, RouterOffsetOperation
from manufacturing_support import supported

STRATEGIES={'Contour rest / retain skin':'retained_skin',
    'Tabbed profile + rest / leave X tabs':'Tabbed profile + rest / leave X tabs',
    'Clear waste + tabbed finish':'Clear waste + tabbed finish'}

class ManufacturingRecipes:
    def __init__(self,path): self.path=Path(path)
    def read(self):
        if not self.path.exists(): return {'schema':'flutter-second-tool-library-v1','recipes':[]}
        if self.path.stat().st_size>2_000_000: raise ValueError('Recipe library exceeds the supported size.')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema')!='flutter-second-tool-library-v1' or not isinstance(data.get('recipes'),list): raise ValueError('Unsupported recipe library.')
        for item in data['recipes']:
            if not isinstance(item.get('id'),str) or not isinstance(item.get('name'),str): raise ValueError('Invalid recipe entry.')
            validate_record(item['recipe'],'profile')
        return data
    @staticmethod
    def version(data): return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.with_suffix('.lock').open('a+b') as handle:
            handle.seek(0,2)
            if handle.tell()==0: handle.write(b'0');handle.flush()
            handle.seek(0)
            import msvcrt
            try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise ValueError('Another drawing is updating recipes; retry.')
            try: yield
            finally: handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    def write(self,data):
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
        temporary.replace(self.path)
    def command(self,action,p,project):
        if action in {'recipe_save','recipe_delete'}:
            with self.locked():
                data=self.read()
                if p.get('version')!=self.version(data): raise ValueError('Recipe library changed. Reopen it before saving or deleting.')
                if action=='recipe_save':
                    name=str(p.get('name','')).strip()
                    if not name or len(name)>100: raise ValueError('Use a recipe name of 1–100 characters.')
                    if any(item['name'].casefold()==name.casefold() for item in data['recipes']): raise ValueError('That recipe name already exists. Choose another name.')
                    if not supported(project): raise ValueError('Unsupported machining is protected.')
                    op=next((o for o in project.operations if o.id==p.get('operation_id')),None)
                    if op is None or not op.profile_retention: raise ValueError('Save a recipe from an applied second-tool group.')
                    settings=resolve_profile_group_edit(project,op).settings
                    f=unit_scale('in',project.units)
                    tool=project.tool_library.by_station(settings['station'])
                    if tool is None: raise ValueError('Recipe cutter is missing.')
                    strategy=next(k for k,v in STRATEGIES.items() if v==settings.get('strategy','retained_skin'))
                    values={k:float(settings.get(k,0)) for k in FIELDS['profile']}
                    for key in values:
                        if key in {'feed_rate','plunge_rate'}: values[key]*=60/f
                        elif key!='spindle_rpm': values[key]/=f
                    record=dict(schema=SCHEMA,kind='profile',enabled=True,tool=tool_identity(tool,f),values=values,strategy=strategy,retain_tabs=settings.get('retain_tabs',2))
                    validate_record(record,'profile')
                    data['recipes'].append(dict(id=uuid4().hex,name=name,recipe=record))
                else:
                    old=len(data['recipes']);data['recipes']=[item for item in data['recipes'] if item['id']!=p.get('id')]
                    if old==len(data['recipes']): raise ValueError('Recipe no longer exists.')
                self.write(data)
            return self.command('recipe_list',{},project)
        data=self.read();f=unit_scale('in',project.units)
        if action=='recipe_list':
            return dict(version=self.version(data),recipes=[dict(item,compatible=any(matches_tool(t,item['recipe']['tool'],f) for t in project.tool_library.tools)) for item in data['recipes']])
        if action!='recipe_resolve': raise ValueError('Unknown recipe action.')
        item=next((r for r in data['recipes'] if r['id']==p.get('id')),None)
        if item is None: raise ValueError('Recipe no longer exists.')
        if p.get('version')!=self.version(data): raise ValueError('Recipe library changed; reopen it.')
        record=validate_record(copy.deepcopy(item['recipe']),'profile')
        if not record['enabled']: raise ValueError('This saved recipe has its second tool disabled.')
        op=next((o for o in project.operations if o.id==p.get('operation_id')),None)
        if op is None: raise ValueError('Choose a primary profile.')
        source=resolve_profile_group_edit(project,op).source if op.profile_retention else op
        if type(source) is not RouterOffsetOperation: raise ValueError('Choose a primary profile.')
        primary=project.tool_library.by_station(source.tool_station)
        replacement=p.get('replacement_station')
        if replacement is not None and type(replacement) is not int: raise ValueError('Choose an explicit cutter station.')
        tool=project.tool_library.by_station(replacement if replacement is not None else record['tool']['station'])
        if tool is None or tool.kind!=ToolKind.END_MILL or primary is None or tool.diameter>=primary.diameter:
            raise ValueError('Choose a loaded end mill smaller than the primary cutter.')
        if replacement is None and not matches_tool(tool,record['tool'],f): raise ValueError('Saved cutter is unavailable or changed. Select a replacement explicitly.')
        values=record['values'];settings={k:v*(f/60 if k in {'feed_rate','plunge_rate'} else 1 if k=='spindle_rpm' else f) for k,v in values.items()}
        strategy=STRATEGIES[record['strategy']]
        if strategy!='Clear waste + tabbed finish': settings.pop('stepover');settings.pop('main_stepover')
        if strategy.startswith('Tabbed'): settings['retain_tabs']=record['retain_tabs']
        settings.update(strategy=strategy,station=tool.station,spindle_rpm=int(settings['spindle_rpm']))
        return dict(settings=settings,recipe_tool=tool_identity(tool,f),name=item['name'],replaced=replacement is not None,reviewed=False)
