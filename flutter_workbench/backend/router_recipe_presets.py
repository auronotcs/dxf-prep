"""Typed Tkinter cut_parameters conversion for ordinary router recipe drafts."""
import copy,math
from cam_core.units import unit_scale
from cam_core.depth_intent import through_depth
from cam_core.second_tool_recipe import validate_record

LENGTHS={'target_depth','safe_z','allowance','finish_axial_stock','lead_in','lead_out','onion_skin','tab_width','tab_height','corner_tab_offset','tab_spacing','female_tab_min_perimeter','tab_cleanup_min_perimeter','helix_diameter','helix_min_diameter','helix_clearance','helix_pitch','breakthrough','curve_feed_transition_distance','common_line_tolerance'}
RATES={'finish_feed':'finish_feed_override','helix_feed':'helix_feed_override'}
INTEGER={'finish_spindle_rpm':'finish_spindle_rpm_override','start_index':'start_index','tab_count':'tab_count','helix_finish_revolutions':'helix_finish_revolutions'}
BOOLEAN={'tabs_enabled','finishing_pass','remove_tabs_after'}
ENUMS={'cut_direction':{'climb','conventional'},'cut_intent':{'through','partial'},'entry_mode':{'plunge','ramp','helical'},'entry_side':{'automatic_waste','inside','outside'},'helix_fallback':{'waste_plunge','strict_error'},'tab_mode':{'even','minimum_and_spacing','minimum_avoid_corners','support_aware','corners_and_spacing','manual','aligned'},'tab_cleanup_order':{'after_each_part','end_of_operation'},'curve_feed_mode':{'off','wall_compensation'},'common_line_mode':{'off','paired_rectangles'},'contour_order':{'inside_first','part_by_part','source_order'}}
HOLDING={'cut_intent','target_depth','breakthrough','onion_skin','tabs_enabled','tab_mode','tab_count','tab_width','tab_height','corner_tab_offset','tab_spacing','female_tab_min_perimeter','tab_cleanup_min_perimeter','manual_tab_positions','remove_tabs_after','tab_cleanup_order','contour_order','common_line_mode','common_line_tolerance','finish_axial_stock'}
FIELDS=LENGTHS|set(RATES)|set(INTEGER)|BOOLEAN|set(ENUMS)|{'helix_ramp_angle','pass_depths','manual_tab_positions','curve_feed_min_percent','tab_cleanup_retain','second_tool'}

def number(value,key):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(f'Recipe {key} must be a finite number.')
    return value

def apply_preset(project,operation,recipe):
    """Return an independent model copy; never modify source job/recipe."""
    from cam_core.model import IslandFillOperation
    if type(operation) is IslandFillOperation:
        from fill_recipe_presets import apply_preset as fill_preset
        return fill_preset(project,operation,recipe)
    if recipe.get('fill_parameters'):raise ValueError('Cleanout settings cannot be applied to a profile.')
    p=recipe.get('cut_parameters',{})
    if not isinstance(p,dict):raise ValueError('cut_parameters must be a named settings record.')
    unknown=set(p)-FIELDS
    if unknown:raise ValueError('Unsupported cut recipe settings: '+', '.join(sorted(unknown)))
    result=copy.deepcopy(operation);f=unit_scale('in',project.units)
    for key,value in p.items():
        if key in LENGTHS:setattr(result,key,number(value,key)*f)
        elif key in RATES:
            if number(value,key)<=0:raise ValueError(f'Recipe {key} must be positive.')
            setattr(result,RATES[key],value*f/60)
        elif key in INTEGER:
            if type(value) is not int or not 0<=value<=100000:raise ValueError(f'Recipe {key} must be a nonnegative whole number up to 100000.')
            setattr(result,INTEGER[key],value)
        elif key in BOOLEAN:
            if type(value) is not bool:raise ValueError(f'Recipe {key} must be on or off.')
            if key!='tabs_enabled':setattr(result,key,value)
        elif key in ENUMS:
            if not isinstance(value,str) or value not in ENUMS[key]:raise ValueError(f'Unsupported recipe {key}: {value}.')
            setattr(result,key,value)
        elif key in {'pass_depths','manual_tab_positions'}:
            if not isinstance(value,list) or len(value)>1000:raise ValueError(f'Recipe {key} must be a bounded list.')
            values=[number(v,key) for v in value]
            if key=='pass_depths':
                if any(v<=0 for v in values):raise ValueError('Pass increments must be positive.')
                values=[v*f for v in values]
            elif any(not 0<=v<1 for v in values) or len(set(values))!=len(values):raise ValueError('Manual tab centers must be unique fractions from 0 to less than 1.')
            setattr(result,key,values)
        elif key in {'curve_feed_min_percent','helix_ramp_angle'}:setattr(result,key,number(value,key))
        elif key=='tab_cleanup_retain':
            if value is not None:raise ValueError('Retained-tab release recipes require a staged release review.')
        elif key=='second_tool':
            validate_record(value,'profile')
            if value['enabled']:raise ValueError('Enabled second-tool recipes require complete-group review.')
    if p.get('tabs_enabled') is False:
        if p.get('remove_tabs_after') is True:raise ValueError('A recipe cannot remove tabs with tabs disabled.')
        result.tab_mode='even';result.tab_count=0;result.manual_tab_positions=[];result.remove_tabs_after=False
    elif p.get('tabs_enabled') is True and not (result.tab_count>0 or result.tab_mode!='even'):
        raise ValueError('Enabled tabs need a positive count or an automatic placement mode.')
    if p and result.cut_intent=='through':
        result.target_depth=through_depth(project.metadata.get('job_setup',{}).get('material_thickness',0),result.breakthrough)
    from profile_helix import validate
    validate(result)
    return result

def differences(project,operation,recipe):
    proposed=apply_preset(project,operation,recipe)
    return [key for key in vars(operation) if getattr(operation,key)!=getattr(proposed,key)]
