"""Private custom catalog edits. Units are explicit; qualification is evidence, not approval."""
import copy
import math
from cam_core.workflow import normalize_material, RECIPE_PROCESS_ROLES, ENTRY_STRATEGIES

GEOMETRY = ('diameter', 'cutting_edge_length', 'shank_diameter', 'overall_length',
            'holder_diameter', 'stickout')
TOOL_FIELDS = {'name', 'spindle_mode', 'min_rpm', 'max_rpm', *GEOMETRY}
RECIPE_FIELDS = {'material', 'rpm', 'feed', 'plunge', 'stepdown', 'stepover'}


def numeric(value, name, *, optional=False, integer=False):
    if optional and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be positive and finite.')
    if integer and (type(value) is not int or value > 100000):
        raise ValueError(f'{name} must be a whole number up to 100000.')
    return value


def label(value, name, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f'{name} must contain 1–{maximum} characters.')
    return value.strip()


def revise(old, params):
    if set(params) - {'id', 'version', 'units', 'tool', 'recipe_index', 'recipe', 'scope', 'qualification', 'evidence'}:
        raise ValueError('Unsupported library edit field.')
    if old.get('custom') is not True:
        raise ValueError('Only custom library entries can be edited. Save an applied station as a custom entry first.')
    units = params.get('units')
    if units not in ('in', 'mm'):
        raise ValueError('Choose inch or millimeter display units.')
    factor = 1 if units == 'in' else 1 / 25.4
    patch, recipe_patch = params.get('tool', {}), params.get('recipe', {})
    if not isinstance(patch, dict) or set(patch) - TOOL_FIELDS:
        raise ValueError('Unsupported cutter field.')
    if not isinstance(recipe_patch, dict) or set(recipe_patch) - RECIPE_FIELDS:
        raise ValueError('Unsupported cutting recipe field.')
    row = copy.deepcopy(old)
    recipes = row.get('recipes', [])
    index = params.get('recipe_index')
    if type(index) is not int or not 0 <= index < len(recipes):
        raise ValueError('Choose an existing material recipe.')
    for key, value in patch.items():
        if key == 'name': row[key] = label(value, 'Tool name')
        elif key == 'spindle_mode':
            if value not in ('required', 'optional', 'disabled'): raise ValueError('Unknown spindle mode.')
            row[key] = value
        elif key in ('min_rpm', 'max_rpm'):
            row[key] = numeric(value, key, optional=True, integer=True)
        else:
            value = numeric(value, key, optional=key != 'diameter')
            row[key + '_in'] = None if value is None else value * factor
    recipe = recipes[index]
    if recipe.get('qualification')=='retired':raise ValueError('Reactivate the retired recipe as Prototype before editing it.')
    for key, value in recipe_patch.items():
        if key == 'material': recipe[key] = label(value, 'Material / recipe name')
        elif key == 'rpm': recipe[key] = numeric(value, 'RPM', optional=row.get('spindle_mode') == 'disabled', integer=True)
        else:
            value = numeric(value, key, optional=key == 'stepover')
            recipe[key] = None if value is None else value * factor
    scope = params.get('scope')
    if scope is not None:
        allowed = {'material_family','thickness_min','thickness_max','process_roles','entry_strategies'}
        if not isinstance(scope,dict) or set(scope)-allowed:
            raise ValueError('Unsupported recipe applicability field.')
        for key,value in scope.items():
            if key=='material_family':
                if value is None or value=='': recipe.pop(key,None)
                else: recipe[key]=normalize_material(label(value,'Material family'))
            elif key in ('thickness_min','thickness_max'):
                value=numeric(value,key,optional=True)
                recipe[key+'_in']=None if value is None else value*factor
                recipe.pop(key,None)  # Catalog ranges are always canonical inches.
            else:
                choices=RECIPE_PROCESS_ROLES if key=='process_roles' else ENTRY_STRATEGIES
                if not isinstance(value,list) or any(not isinstance(v,str) or v not in choices for v in value):
                    raise ValueError(f'Choose known {key.replace("_"," ")}.')
                if key=='process_roles' and (not value or 'any' in value and len(set(value))>1):
                    raise ValueError('Choose Any role alone or one or more specific process roles.')
                recipe[key]=sorted(set(value))
        low,high=recipe.get('thickness_min_in'),recipe.get('thickness_max_in')
        numeric(low,'Minimum thickness',optional=True);numeric(high,'Maximum thickness',optional=True)
        if low is not None and high is not None and low>high:
            raise ValueError('Minimum thickness exceeds maximum thickness.')
    # Validate every recipe against the edited cutter, without station-specific
    # assumptions. The separate turret draft applies T6/T8/machine restrictions.
    for key in GEOMETRY:
        numeric(row.get(key + '_in'), key, optional=key != 'diameter')
    lo = numeric(row.get('min_rpm'), 'Minimum RPM', optional=True, integer=True)
    hi = numeric(row.get('max_rpm'), 'Maximum RPM', optional=True, integer=True)
    if lo is not None and hi is not None and lo > hi: raise ValueError('Minimum RPM exceeds maximum RPM.')
    for r in recipes:
        label(r.get('material'), 'Material / recipe name')
        if row.get('spindle_mode') == 'disabled':
            if r.get('rpm') is not None: raise ValueError('Spindle-disabled recipes must leave RPM blank.')
        else:
            rpm = numeric(r.get('rpm'), 'RPM', integer=True)
            if (lo is not None and rpm < lo) or (hi is not None and rpm > hi): raise ValueError('Recipe RPM is outside cutter limits.')
        for key in ('feed', 'plunge', 'stepdown'): numeric(r.get(key), key)
        numeric(r.get('stepover'), 'stepover', optional=True)
        if r.get('stepover') is not None and r['stepover'] > row['diameter_in']:
            raise ValueError('Stepover exceeds cutter diameter.')
        if row.get('cutting_edge_length_in') is not None and r['stepdown'] > row['cutting_edge_length_in']:
            raise ValueError('Stepdown exceeds cutting edge length.')
    # Treat conversion-only roundoff as unchanged, but never hide an intentional
    # numeric edit. IEEE roundtrips can differ by one ULP.
    def same(a, b):
        if type(a) in (float, int) and type(b) in (float, int):
            return abs(a - b) <= max(math.ulp(float(a)), math.ulp(float(b))) * 2
        return a == b
    cutter_changed = any(not same(old.get(k), row.get(k)) for k in
        ('spindle_mode', 'min_rpm', 'max_rpm', *(k + '_in' for k in GEOMETRY)))
    changed = [i for i, r in enumerate(recipes) if cutter_changed or
               any(not same(old['recipes'][i].get(k), r.get(k)) for k in RECIPE_FIELDS)]
    def scope_values(r):
        roles=r.get('process_roles',['any'])
        if isinstance(roles,str):roles=[v.strip() for v in roles.split(',') if v.strip()]
        return (normalize_material(str(r.get('material_family') or r.get('material','Anything'))),
                r.get('thickness_min_in'),r.get('thickness_max_in'),
                sorted(set(roles)),sorted(set(r.get('entry_strategies') or [])))
    if scope is not None and any(not same(a,b) for a,b in zip(scope_values(old['recipes'][index]),scope_values(recipe))):
        changed=sorted(set([*changed,index]))
    for i in changed:
        r = recipes[i]
        if r.get('qualification') or r.get('qualification_evidence'):
            r['previous_qualification'] = {k: r[k] for k in ('qualification', 'qualification_evidence') if k in r}
        if r.get('qualification')!='retired':r['qualification'] = 'prototype'
        r.pop('qualification_evidence', None)
    qualification = params.get('qualification', recipe.get('qualification', 'prototype'))
    if qualification not in ('prototype', 'shop_tested'):
        raise ValueError('Choose Prototype or Shop tested with evidence.')
    evidence = params.get('evidence', recipe.get('qualification_evidence', ''))
    if not isinstance(evidence, str) or len(evidence) > 4000:
        raise ValueError('Evidence must be text up to 4000 characters.')
    if index in changed:
        # A changed setup must first be saved as a prototype; a later explicit
        # evidence edit can record a real test of those exact saved inputs.
        recipe['qualification'] = 'prototype'
    else:
        if qualification == 'shop_tested' and not evidence.strip():
            raise ValueError('Record material/thickness, machine/setup, test date and observed result as evidence before marking Shop tested.')
        recipe['qualification'] = qualification
        recipe['qualification_evidence'] = evidence.strip()
    return row, changed
