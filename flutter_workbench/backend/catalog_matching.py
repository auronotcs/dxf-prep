"""Explain recipe applicability; resolve only a current, explicitly reviewed choice.

This first Guided Production slice loads an applied cutter's settings into the
existing turret draft. It does not author operations or confer qualification.
"""
import copy
import hashlib
import json
import math
from cam_core.model import ToolKind, inferred_sequence_role
from cam_core.units import unit_scale
from cam_core.workflow import normalize_material, recipe_matches, RECIPE_PROCESS_ROLES, RECIPE_QUALIFICATION_STATES
from catalog_edit import numeric


def token(project, entries, version, station, role):
    return hashlib.sha256(json.dumps([project.to_dict(), entries, version, station, role],
        sort_keys=True, allow_nan=False).encode()).hexdigest()


def list_matches(project, entries, version, params, *, operation=None):
    station, role = params.get('station'), params.get('process_role')
    if type(station) is not int or not 1 <= station <= 12:
        raise ValueError('Choose an applied turret station from 1 to 12.')
    if role not in RECIPE_PROCESS_ROLES or role == 'any':
        raise ValueError('Choose the intended process role.')
    tool = project.tool_library.by_station(station)
    if tool is None: raise ValueError('Apply a cutter in Turret Setup before matching recipes.')
    if tool.kind != ToolKind.END_MILL or tool.spindle_mode == 'disabled' or station == 8:
        raise ValueError('Recipe matching currently supports loaded router end mills. Use specialized editors for other tools.')
    f = unit_scale('in', project.units)
    setup = project.metadata.get('job_setup', {})
    material = str(setup.get('material', '')).strip()
    thickness = setup.get('material_thickness')
    numeric(thickness, 'Stock thickness')
    if normalize_material(material) in ('', 'anything'): raise ValueError('Choose the actual stock material before matching recipes.')
    thickness_in = thickness / f
    affected = [operation] if operation is not None else [o for o in project.operations if o.tool_station == station and o.enabled]
    results = []
    for entry in entries:
        for index, raw in enumerate(entry.get('recipes', [])):
            reasons, warnings = [], []
            recipe = copy.deepcopy(raw)
            if not isinstance(recipe, dict): continue
            qualification = str(recipe.get('qualification', 'prototype'))
            try:
                planned = None
                if operation is not None:
                    from router_recipe_presets import apply_preset
                    planned = apply_preset(project,operation,recipe)
                identity = tool.metadata.get('catalog_tool_id')
                if identity:
                    if entry.get('id') != identity: reasons.append('Catalog identity differs from the applied cutter.')
                elif str(entry.get('name', '')).casefold() != tool.name.casefold():
                    reasons.append('Tool name differs; no catalog identity is recorded on this station.')
                if entry.get('kind') != tool.kind.value: reasons.append('Cutter type differs.')
                numeric(entry.get('diameter_in'), 'Catalog cutter diameter')
                for key in ('diameter','cutting_edge_length','shank_diameter','overall_length','holder_diameter','stickout'):
                    expected = entry.get(key + '_in')
                    current = getattr(tool, key)
                    if expected is not None:
                        numeric(expected, key)
                        if current is None or not math.isclose(current / f, expected, rel_tol=1e-12, abs_tol=1e-9):
                            reasons.append(f'Applied cutter {key.replace("_", " ")} differs.')
                if entry.get('spindle_mode', 'required') != tool.spindle_mode:
                    reasons.append('Spindle mode differs.')
                if qualification not in RECIPE_QUALIFICATION_STATES:
                    reasons.append('Unknown qualification status.')
                elif qualification == 'retired': reasons.append('Recipe is retired.')
                elif qualification != 'production_approved': warnings.append('Not production approved; a recorded test does not qualify this job.')
                if not recipe.get('qualification_evidence'):
                    warnings.append('No separate physical-test evidence is recorded.')
                if normalize_material(str(recipe.get('material_family') or recipe.get('material',''))) in ('','anything'):
                    warnings.append('No material-specific qualification scope is recorded.')
                minimum, maximum = recipe.get('thickness_min_in'), recipe.get('thickness_max_in')
                for k, v in [('Minimum thickness', minimum), ('Maximum thickness', maximum)]:
                    numeric(v, k, optional=True)
                if minimum is not None and maximum is not None and minimum > maximum:
                    reasons.append('Recipe thickness range is invalid.')
                if minimum is None or maximum is None: warnings.append('Thickness qualification range is not fully recorded.')
                if not recipe_matches(recipe, material, thickness_in, role):
                    # Reuse the core's material normalization/matching semantics.
                    neutral = dict(recipe, qualification='prototype', thickness_min_in=None, thickness_max_in=None, process_roles=['any'])
                    if not recipe_matches(neutral, material, thickness_in, role): reasons.append('Stock material does not match.')
                    roles = recipe.get('process_roles', ['any'])
                    roles = [s.strip() for s in roles.split(',')] if isinstance(roles,str) else roles
                    if 'any' not in roles and role not in roles: reasons.append('Process role does not match.')
                    if minimum is not None and thickness_in < minimum or maximum is not None and thickness_in > maximum:
                        reasons.append('Stock thickness is outside the recorded range.')
                if operation is None and (recipe.get('cut_parameters') or recipe.get('fill_parameters')):
                    reasons.append('Contains operation-specific settings; use the operation editor.')
                rpm = numeric(recipe.get('rpm'), 'Recipe RPM', integer=True)
                for k in ('feed', 'plunge', 'stepdown'): numeric(recipe.get(k), k)
                numeric(recipe.get('stepover'), 'Stepover', optional=True)
                lo = max(setup.get('machine_min_rpm',8000), tool.min_rpm or 0, entry.get('min_rpm') or 0)
                hi = min(setup.get('machine_max_rpm',24000), tool.max_rpm or 100000,
                         entry.get('max_rpm') or 100000, 12000 if station == 6 else 100000)
                if not lo <= rpm <= hi: reasons.append(f'RPM is outside applied machine/cutter limits ({lo}–{hi}).')
                if tool.cutting_edge_length is not None and recipe['stepdown'] * f > tool.cutting_edge_length:
                    reasons.append('Stepdown exceeds applied cutting edge length.')
                if recipe.get('stepover') is not None and recipe['stepover'] * f > tool.diameter:
                    reasons.append('Stepover exceeds applied cutter diameter.')
                for op in affected:
                    if not recipe_matches(recipe, material, thickness_in, inferred_sequence_role(op)):
                        reasons.append(f'Also used by incompatible operation: {op.name}.')
                    entries_allowed = recipe.get('entry_strategies')
                    if entries_allowed and (not isinstance(entries_allowed,list) or not all(isinstance(v,str) for v in entries_allowed)):
                        raise ValueError('Entry strategies must be a list of named strategies.')
                    if entries_allowed and getattr(planned if planned is not None else op,'entry_mode','plunge') not in entries_allowed:
                        reasons.append(f'Entry strategy is outside recipe scope: {op.name}.')
                if any(o.feed_rate_override is not None or o.plunge_rate_override is not None or o.spindle_rpm_override is not None or o.recipe_metadata for o in affected):
                    warnings.append('Operation overrides or recipe records remain unchanged and can override station settings.')
            except (ValueError, TypeError, KeyError, OverflowError) as e:
                reasons.append(f'Invalid or incomplete recipe: {e}')
            results.append(dict(id=entry.get('id'),recipe=index,tool_name=entry.get('name'),
                name=recipe.get('material','Unnamed recipe'),qualification=qualification,
                evidence=recipe.get('qualification_evidence',''),notes=recipe.get('notes',''),
                scope={k:recipe.get(k) for k in ('material_family','thickness_min_in','thickness_max_in','process_roles','entry_strategies')},
                values={k:recipe.get(k) for k in ('rpm','feed','plunge','stepdown','stepover')},
                eligible=not reasons,reasons=list(dict.fromkeys(reasons)),warnings=list(dict.fromkeys(warnings))))
    rank={'production_approved':0,'shop_tested':1,'prototype':2,'archive_observed':3,'retired':9}
    results.sort(key=lambda r:(not r['eligible'],rank.get(r['qualification'],8),str(r['name']).casefold(),str(r['id']),r['recipe']))
    return dict(version=version,token=token(project,entries,version,station,role),station=station,
        process_role=role,material=material,thickness=thickness,units=project.units,
        affected_operations=[dict(id=o.id,name=o.name,role=inferred_sequence_role(o)) for o in affected],matches=results)


def resolve(project, entries, version, params):
    current=list_matches(project,entries,version,params)
    if params.get('version')!=version or params.get('token')!=current['token']:
        raise ValueError('Job, cutter or library changed. Refresh recipe matches before loading.')
    if params.get('reviewed') is not True: raise ValueError('Review recipe scope, qualification and affected operations before loading a draft.')
    selected=next((r for r in current['matches'] if r['id']==params.get('id') and type(params.get('recipe')) is int and r['recipe']==params['recipe']),None)
    if selected is None or not selected['eligible']: raise ValueError('Recipe is not an applicable match for the current job and cutter.')
    row=next(t for t in entries if t['id']==selected['id']);recipe=copy.deepcopy(row['recipes'][selected['recipe']])
    tool=copy.deepcopy(project.tool_library.by_station(current['station']))
    f=unit_scale('in',project.units)
    tool.spindle_rpm=recipe['rpm'];tool.feed_rate=recipe['feed']*f/60;tool.plunge_rate=recipe['plunge']*f/60;tool.max_stepdown=recipe['stepdown']*f
    # Keep physical cutter identity, calibration, limits, and custom metadata.
    # Freeze the recipe copy; later library edits never change an applied job.
    for key in ('thickness_min','thickness_max'):
        if recipe.get(key+'_in') is not None:recipe[key]=recipe[key+'_in']*f
        else:recipe.pop(key,None)
    tool.metadata.update(catalog_tool_id=row['id'],material_recipe=recipe['material'],loaded_recipe=recipe)
    return dict(tool=tool.to_dict(),match=selected,reviewed=False,
        note='Station feeds/RPM and stepdown recommendation only. Operation depths, entries, pass schedules, stepover and holding remain unchanged; review actual regenerated motion before Apply.')
