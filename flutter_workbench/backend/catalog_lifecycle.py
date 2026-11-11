"""Version-locked custom library actions. Loaded job records are never rewritten."""
import copy,json
from uuid import uuid4
from catalog_edit import label,revise
from cam_core.workflow import normalize_material

ACTIONS={'catalog_recipe_add','catalog_recipe_duplicate','catalog_recipe_remove','catalog_recipe_status','catalog_tool_duplicate'}

def prototype(recipe):
    out=copy.deepcopy(recipe)
    for key in list(out):
        if key.startswith('qualification') or key in {'previous_qualification','retired_qualification'}:out.pop(key)
    out['qualification']='prototype'
    return out

def unique_name(value,items,key):
    name=label(value,'Name')
    if any(str(item.get(key,'')).strip().casefold()==name.casefold() for item in items):
        raise ValueError('That name already exists. Choose a distinct name.')
    return name

def change(data,action,p):
    common={'id','version'}
    extra={'catalog_recipe_add':{'units','recipe','scope'},
        'catalog_recipe_duplicate':{'recipe_index','name'},
        'catalog_recipe_remove':{'recipe_index','confirmed'},
        'catalog_recipe_status':{'recipe_index','retired','confirmed'},
        'catalog_tool_duplicate':{'name'}}[action]
    if set(p)-(common|extra):raise ValueError('Unsupported library action field.')
    row=next((t for t in data['tools'] if t['id']==p.get('id') and t.get('custom') is True),None)
    if row is None:raise ValueError('Choose a custom tool library entry.')
    recipes=row.setdefault('recipes',[])
    if action=='catalog_tool_duplicate':
        clone=copy.deepcopy(row);clone.update(id='custom-'+uuid4().hex,name=unique_name(p.get('name'),data['tools'],'name'))
        clone['recipes']=[dict(prototype(r),id='recipe-'+uuid4().hex) for r in recipes]
        data['tools'].append(clone)
        result=dict(selectedId=clone['id'],selectedRecipe=0)
    elif action=='catalog_recipe_add':
        recipe=p.get('recipe')
        if not isinstance(recipe,dict):raise ValueError('Enter a material recipe.')
        name=unique_name(recipe.get('material'),recipes,'material')
        draft=copy.deepcopy(row);draft['recipes'].append(dict(material=name,qualification='prototype'))
        candidate,_=revise(draft,dict(id=row['id'],version=p.get('version'),units=p.get('units'),
            recipe_index=len(recipes),recipe=recipe,scope=p.get('scope')))
        added=prototype(candidate['recipes'][-1]);added['id']='recipe-'+uuid4().hex
        recipes.append(added);result=dict(selectedId=row['id'],selectedRecipe=len(recipes)-1)
    else:
        index=p.get('recipe_index')
        if type(index) is not int or not 0<=index<len(recipes):raise ValueError('Choose an existing material recipe.')
        current=recipes[index]
        if action=='catalog_recipe_duplicate':
            clone=prototype(current)
            clone['material_family']=normalize_material(current.get('material_family') or current.get('material','Anything'))
            clone.update(id='recipe-'+uuid4().hex,material=unique_name(p.get('name'),recipes,'material'))
            recipes.append(clone);index=len(recipes)-1
        elif action=='catalog_recipe_remove':
            if p.get('confirmed') is not True:raise ValueError('Confirm removal of this library recipe.')
            if len(recipes)<=1:raise ValueError('Keep at least one recipe for this tool. Retire it instead, or remove the entire custom tool.')
            recipes.pop(index);index=min(index,len(recipes)-1)
        elif action=='catalog_recipe_status':
            if type(p.get('retired')) is not bool or p.get('confirmed') is not True:raise ValueError('Confirm retirement or reactivation.')
            retired=current.get('qualification')=='retired'
            if p['retired'] and not retired:
                current['retired_qualification']={k:copy.deepcopy(v) for k,v in current.items() if k.startswith('qualification')}
                current['qualification']='retired'
            elif not p['retired'] and retired:
                recipes[index]=prototype(current)
        result=dict(selectedId=row['id'],selectedRecipe=index)
    if len(json.dumps(data,indent=2,allow_nan=False).encode())>2_000_000:raise ValueError('Tool catalog is too large.')
    return result
