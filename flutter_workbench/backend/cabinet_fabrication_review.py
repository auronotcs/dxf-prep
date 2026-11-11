"""Private Cabinet fabrication inspection. Never creates jobs or grants output permission."""
import copy
import math
from dataclasses import asdict
from cam_core.cabinet_tab_production import FabricationSettings, SETTINGS_KEY, inspect_fabrication
from cam_core.selection import is_closed_entity
from cam_core.model import ToolKind
from cabinet_authoring import guard


def current(draft,project,p,revision,digest):
    guard(project)
    d=draft.cached
    if not d or p.get('token')!=d['token'] or revision!=d['revision'] or digest!=d['digest']:
        raise ValueError('Tab layout or document changed. Preview Cabinet Tabs again before inspecting fabrication.')
    return d['project'],d['plan']


def choices(project,plan):
    members={i for plane in plan.planes for i in plane.member_ids}
    return [e for e in project.entities if is_closed_entity(e) and e.id not in members
        and e.metadata.get('mounting_workflow')!='cabinet_tabs'
        and not e.metadata.get('reference_only') and e.metadata.get('machinable') is not False]


def parse_settings(s):
    if not isinstance(s,dict) or set(s)!=set(FabricationSettings('',(),-1,1,0,0).to_dict()):raise ValueError('Provide the complete fabrication inputs.')
    if type(s['top_id']) is not str or not isinstance(s['keepout_ids'],list) or any(type(i) is not str for i in s['keepout_ids']):raise ValueError('Choose current top and opening identities.')
    if type(s['tool_station']) is not int:raise ValueError('Choose a loaded cutter station.')
    for k in ('top_thickness','edge_clearance','minimum_web'):
        if type(s[k]) not in (float,int) or not math.isfinite(s[k]):raise ValueError('Enter finite physical dimensions in drawing units.')
    if any(type(s[k]) is not bool for k in ('registration_reviewed','fit_reviewed')):raise ValueError('Review the registration and fit choices explicitly.')
    return FabricationSettings(**dict(s,keepout_ids=tuple(s['keepout_ids'])))


def begin(draft,project,p,revision,digest):
    if set(p)!={'token'}:raise ValueError('Choose a current Cabinet preview.')
    candidate,plan=current(draft,project,p,revision,digest)
    f=25.4 if candidate.units=='mm' else 1.
    default=FabricationSettings('',(),-1,.063*f,.125*f,.125*f)
    warning=None
    saved_all=candidate.metadata.get(SETTINGS_KEY,{})
    saved=saved_all.get(plan.plan_id) if isinstance(saved_all,dict) else None
    if not isinstance(saved_all,dict):warning='Saved fabrication inputs are invalid. Choose current geometry, cutter and dimensions.'
    if saved:
        try:default=parse_settings(FabricationSettings.from_dict(saved).to_dict())
        except (ValueError,TypeError,KeyError):warning='Saved fabrication inputs are invalid. Choose current geometry, cutter and dimensions.'
    return dict(token=p['token'],units=candidate.units,plan_id=plan.plan_id,settings=default.to_dict(),warning=warning,
        candidates=[dict(id=e.id,label=e.metadata.get('name') or e.id,layer=e.layer,kind=e.to_dict()['kind']) for e in choices(candidate,plan)],
        tools=[dict(station=t.station,name=t.name,diameter=t.diameter,cutting_edge_length=t.cutting_edge_length) for t in candidate.tool_library.tools if t.kind==ToolKind.END_MILL and math.isfinite(t.diameter) and t.diameter>0],
        skin=plan.front_skin_thickness,width_play=plan.width_clearance_total,thickness_play=plan.thickness_clearance_total,
        slot_thickness=plan.back_thickness+plan.thickness_clearance_total)


def inspect(draft,project,p,revision,digest,*,candidate_override=None):
    if set(p)!={'token','settings'}:raise ValueError('Unknown Cabinet fabrication review input.')
    candidate,plan=current(draft,project,p,revision,digest)
    if candidate_override is not None:candidate=candidate_override
    options=parse_settings(p['settings'])
    probe=copy.deepcopy(candidate)
    report=inspect_fabrication(probe,plan,options)
    issues=[asdict(i) for i in report.issues]
    allowed={e.id for e in choices(candidate,plan)}
    for key in (options.top_id,*options.keepout_ids):
        if key and key not in allowed:
            issues.append(dict(code='source_choice',message='Use an original closed top/opening, not a back, reference or nonmachinable object.',entity_ids=[key],point=None,pair_id='',view='plan',severity='error'))
    def row(e,role):return dict(e.to_dict(),review_role=role)
    planview=[row(e,'top') for e in (report.top,) if e]+[row(e,'reserve') for e in report.safe_regions]+[row(e,'opening') for e in report.keepouts]+[row(e,'slot') for e in report.slots]
    backview=[row(e,'back') for e in report.backs]
    if not backview:backview=[row(e,'source') for e in candidate.entities if e.id in {plane.owner_entity_id for plane in plan.planes}]
    # Include diagnostic sources that are intentionally absent from the proposed output.
    for name,view in [('plan',planview),('back',backview)]:
        present={e['id'] for e in view};ids={i for issue in issues if issue['view']==name for i in issue['entity_ids']}
        if name=='plan':ids-={i for plane in plan.planes for i in plane.member_ids}
        view.extend(row(e,'source') for e in candidate.entities if e.id in ids-present)
    return dict(token=p['token'],units=candidate.units,settings=options.to_dict(),issues=issues,
        geometry_checks_passed=not any(i['severity']=='error' for i in issues),can_export=False,
        views=dict(plan=planview,back=backview),focus_span=max(plan.back_thickness*8,max(t.width for t in plan.tabs)*2),
        slot_end_extension=(plan.back_thickness+plan.thickness_clearance_total)/2,
        message='Inspection only. No machining copies, document changes, stored approval or output permission are created.')
