"""Typed, unit-aware cleanout presets; full motion remains a separate review."""
import copy
from cam_core.model import FillStrategy
from cam_core.units import unit_scale
from cam_core.depth_intent import resolve_fill_depth
from cam_core.second_tool_recipe import validate_record
from router_recipe_presets import number

LENGTHS={'target_depth','breakthrough','safe_z','bottom_finish_depth','ramp_length','helix_diameter','helix_min_diameter','helix_pitch','helix_clearance','sliver_max_path_length','sliver_link_distance','speed_max_full_depth_engagement','speed_link_distance','stay_down_max_distance','stay_down_max_engagement'}
RATES={'bottom_finish_feed_ipm':'bottom_finish_feed_override','helix_feed_ipm':'helix_feed_override'}
BOOLEAN={'bottom_finish_pass','corner_tags','sliver_cleanup','keep_tool_down'}
ENUMS={'cut_intent':{'custom','partial','through'},'pass_mode':{'maximum_stepdown','equal_count','custom_increments'},'cut_direction':{'climb','conventional','bidirectional'},'entry_mode':{'plunge','ramp','helical'},'helix_fallback':{'ramp','plunge','error'},'strategy':{'concentric','speed_cleanout'},'speed_fallback':{'depth_step','error'},'spacing_mode':{'overlap','stepover'}}
SCALARS={'helix_ramp_angle','dwell_seconds','stay_down_feed_percent','speed_center_feed_percent','overlap_percent'}
FIELDS=LENGTHS|set(RATES)|BOOLEAN|set(ENUMS)|SCALARS|{'pass_count','helix_finish_revolutions','pass_depths','second_tool'}
MODEL_FIELDS=(FIELDS-{'spacing_mode','overlap_percent','second_tool'}-set(RATES))|set(RATES.values())|{'stepover'}
HOLDING={'target_depth','cut_intent','breakthrough','bottom_finish_depth','bottom_finish_pass'}

def apply_preset(project,operation,recipe):
    if recipe.get('cut_parameters'):raise ValueError('Profile cut settings cannot be applied to a cleanout.')
    p=recipe.get('fill_parameters',{})
    if not isinstance(p,dict):raise ValueError('fill_parameters must be a named settings record.')
    unknown=set(p)-FIELDS
    if unknown:raise ValueError('Unsupported fill recipe settings: '+', '.join(sorted(unknown)))
    result=copy.deepcopy(operation);f=unit_scale('in',project.units)
    for key,value in p.items():
        if key in LENGTHS:
            if number(value,key)<0:raise ValueError(f'Recipe {key} cannot be negative.')
            setattr(result,key,value*f)
        elif key in RATES:
            if value is not None and number(value,key)<=0:raise ValueError(f'Recipe {key} must be positive or unset.')
            setattr(result,RATES[key],None if value is None else value*f/60)
        elif key in BOOLEAN:
            if type(value) is not bool:raise ValueError(f'Recipe {key} must be on or off.')
            setattr(result,key,value)
        elif key in ENUMS:
            if not isinstance(value,str) or value not in ENUMS[key]:raise ValueError(f'Unsupported recipe {key}: {value}.')
            if key!='spacing_mode':setattr(result,key,FillStrategy(value) if key=='strategy' else value)
        elif key in SCALARS:
            number(value,key)
            if key=='overlap_percent':
                if not 0<=value<100:raise ValueError('Overlap must be from zero to less than 100 percent.')
            else:setattr(result,key,value)
        elif key in {'pass_count','helix_finish_revolutions'}:
            if value is None and key=='pass_count':result.pass_count=None;continue
            if type(value) is not int or not 0<=value<=100000:raise ValueError(f'Recipe {key} must be a bounded whole number.')
            setattr(result,key,value)
        elif key=='pass_depths':
            if not isinstance(value,list) or len(value)>1000 or any(number(v,key)<=0 for v in value):raise ValueError('Pass increments must be a bounded list of positive numbers.')
            result.pass_depths=[v*f for v in value]
        elif key=='second_tool':
            validate_record(value,'fill')
            if value['enabled']:raise ValueError('Enabled second-tool recipes require complete-group review.')
    # Tkinter stores the resolved physical spacing at recipe level. The saved
    # overlap control is presentation metadata, never a second conflicting input.
    if recipe.get('stepover') is not None:result.stepover=number(recipe['stepover'],'stepover')*f
    elif p.get('spacing_mode')=='overlap':raise ValueError('An overlap recipe needs its saved physical stepover.')
    result=resolve_fill_depth(project,result)
    return result

def validate(project,op,tool):
    """Cheap input/depth checks; swept-area/entry proofs run in the worker."""
    from cam_core.toolpath import island_fill_depth_schedule
    from profile_helix import validate as validate_helix
    op=resolve_fill_depth(project,op)
    for key,values in ENUMS.items():
        if key=='spacing_mode':continue
        value=getattr(op,key)
        if key=='strategy':value=value.value if isinstance(value,FillStrategy) else value
        if value not in values:raise ValueError(f'Unsupported cleanout {key}.')
    for key in BOOLEAN:
        if type(getattr(op,key)) is not bool:raise ValueError(f'Cleanout {key} must be on or off.')
    for key in MODEL_FIELDS-{'pass_depths','pass_count'}-BOOLEAN-set(ENUMS):
        value=getattr(op,key)
        if value is not None:number(value,key)
    if any(getattr(op,key)<0 for key in LENGTHS):raise ValueError('Cleanout dimensions cannot be negative.')
    for key in RATES.values():
        if getattr(op,key) is not None and getattr(op,key)<=0:raise ValueError('Cleanout feed overrides must be positive.')
    if op.pass_count is not None and (type(op.pass_count) is not int or not 1<=op.pass_count<=100000):raise ValueError('Cleanout pass count must be a positive bounded whole number.')
    if not isinstance(op.pass_depths,list) or len(op.pass_depths)>1000 or any(number(v,'pass increment')<=0 for v in op.pass_depths):raise ValueError('Invalid cleanout pass increments.')
    if op.dwell_seconds<0:raise ValueError('Cleanout dwell cannot be negative.')
    if not 0<op.stay_down_feed_percent<=100 or not 0<op.speed_center_feed_percent<=100:raise ValueError('Cleanout feed percentages must be greater than zero and no more than 100.')
    if op.target_depth<=0 or op.safe_z<=0:raise ValueError('Cleanout depth and Safe Z must be positive.')
    if op.cut_intent=='partial' and op.target_depth>=project.metadata.get('job_setup',{}).get('material_thickness',0):raise ValueError('Partial cleanout depth must be less than stock thickness.')
    if op.stepover is not None and not 0<op.stepover<=tool.diameter:raise ValueError('Stepover must be positive and no greater than cutter diameter.')
    if op.stepdown is None or number(op.stepdown,'stepdown')<=0:raise ValueError('Cleanout requires a positive stepdown.')
    validate_helix(op)
    island_fill_depth_schedule(op)
