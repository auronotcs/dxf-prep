"""Explicit, unqualified single-bend geometry. No material forming recipe."""
from dataclasses import asdict,dataclass
import math

POLY_MATERIAL = 'Clear polycarbonate — prototype'
LEGACY_MATERIAL = 'Aluminum / existing flat layout'
FORMING_METHODS = ('Unspecified — prototype', 'Heat forming — unqualified', 'Cold forming — unqualified')
EXPLICIT_ALLOWANCE = 'operator-entered; coupon calibration required'
TEST_ALLOWANCE = 'test estimate: inside radius 3 x thickness, mid-thickness neutral axis; coupon calibration required'
SHOP_ALLOWANCE = '4.5 mm structural-back shop test: prior flat length minus 0.5 inch; repeat-bend fit verification required'
NEUTRAL_DATUM = 'initial neutral-axis plane to free tab tip'
OVERALL_DATUM = 'overall projection from back surface to free tab tip'

def validate_existing_holes(existing, expected, tolerance):
    """Regeneration must not erase a manually moved/resized/restyled managed hole."""
    by_id={hole.id:hole for hole in expected}
    for hole in existing:
        prior=by_id.get(hole.id)
        if (prior is None or hole.center.distance_to(prior.center)>tolerance
                or abs(hole.radius-prior.radius)>tolerance or hole.layer!=prior.layer
                or hole.appearance!=prior.appearance):
            raise ValueError('A generated tab hole was manually changed. Undo that edit or retain it separately before regenerating this back; nothing was applied.')

@dataclass(frozen=True)
class BentTabForming:
    thickness: float
    inside_radius: float
    angle_degrees: float
    bend_allowance: float
    finished_standoff: float
    hole_diameter: float = 0.0
    hole_tip_offset: float = 0.0
    method: str = FORMING_METHODS[0]
    allowance_source: str = EXPLICIT_ALLOWANCE
    flat_length_adjustment: float = 0.0
    standoff_datum: str = NEUTRAL_DATUM

    def flat_projection(self):
        values=(self.thickness,self.inside_radius,self.angle_degrees,self.bend_allowance,
                self.finished_standoff,self.hole_diameter,self.hole_tip_offset,self.flat_length_adjustment)
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Polycarbonate dimensions must be finite.')
        if self.thickness<=0 or self.inside_radius<0 or not 0<self.angle_degrees<180:
            raise ValueError('Enter positive thickness, nonnegative inside radius and bend angle between 0 and 180 degrees.')
        if self.bend_allowance<=0 or self.finished_standoff<=0 or min(self.hole_diameter,self.hole_tip_offset)<0:
            raise ValueError('Enter positive explicit bend allowance/standoff and nonnegative hole dimensions.')
        if self.method not in FORMING_METHODS:
            raise ValueError('Choose an explicit prototype forming method.')
        if self.standoff_datum not in {NEUTRAL_DATUM,OVERALL_DATUM}:
            raise ValueError('Unknown finished-standoff measurement datum.')
        theta=math.radians(self.angle_degrees)
        radius=self.bend_allowance/theta
        if not self.inside_radius-1e-8 <= radius <= self.inside_radius+self.thickness+1e-8:
            raise ValueError('Allowance implies a neutral bend radius outside the entered sheet thickness; review radius, angle and allowance.')
        # Preserve the original layout estimate as a baseline. Shop correction
        # is empirical, not a new neutral-axis/temperature model: the overall
        # datum labels its measured target, not a guaranteed forming outcome.
        straight=(self.finished_standoff-radius*(1-math.cos(theta)))/math.sin(theta)
        straight+=self.flat_length_adjustment
        if straight<=0:
            raise ValueError('Standoff and flat-length correction must leave a positive straight flange after the bend zone.')
        if self.hole_diameter and (self.hole_tip_offset<=self.hole_diameter/2
                or self.hole_tip_offset+self.hole_diameter/2>=straight):
            raise ValueError('Hole must fit in the straight flange, clear of the tip and bend zone.')
        return self.bend_allowance+straight

    def to_dict(self):
        self.flat_projection()
        return {**asdict(self),'material':'clear_polycarbonate','qualified':False,
                'allowance_source':self.allowance_source}

    @classmethod
    def from_dict(cls,data):
        return cls(**{key:data[key] for key in cls.__dataclass_fields__ if key in data})


def coupon_project(settings,units):
    """An isolated review coupon; never inserts geometry into the open job."""
    from .model import CamProject,Point,Polyline
    from .bent_tab_mounting import BentTabAnchor,generate_bent_tab_back
    from copy import deepcopy
    if settings.forming is None:raise ValueError('Choose clear polycarbonate and complete its prototype inputs first.')
    width=max(settings.width*4,settings.width+4*settings.root_relief_radius+2*settings.edge_end_clearance)
    depth=settings.width*2
    source=Polyline(id='poly-bend-coupon',layer='POLY_TAB_CUT',closed=True,
                    points=[Point(0,0),Point(width,0),Point(width,depth),Point(0,depth)])
    result=generate_bent_tab_back(source,[BentTabAnchor(0,.5)],settings)
    outline=deepcopy(result.modified_cut_outline);outline.id=source.id
    outline.metadata.update(bent_tab_cut_outline=True,mounting_owner_entity_id=source.id,
                            mounting_part_id=result.group_id,mounting_workflow='bent_tabs')
    source.id=outline.id+':bent-tab-source';source.layer='_BENT_TAB_SOURCE'
    source.metadata.update(bent_tab_generated=True,bent_tab_source_reference=True,
        mounting_owner_entity_id=outline.id,mounting_part_id=result.group_id,group_id=result.group_id,
        reference_only=True,machinable=False)
    children=deepcopy([*result.bend_guides,*result.keyholes])
    for entity in children:
        entity.metadata.update(mounting_generated=True,mounting_workflow='bent_tabs',
            mounting_owner_entity_id=outline.id,mounting_part_id=result.group_id)
    return CamProject('UNQUALIFIED clear-poly bend coupon',units=units,
        entities=[outline,source,*children],metadata={'last_mounting_workflow':'bent_tabs',
        'job_setup':{'material':'Polycarbonate','material_thickness':settings.forming.thickness},
        'prototype_notice':'Coupon only: no qualified thickness, forming process, strength or optical result.',
        'layer_settings':{'_BENT_TAB_SOURCE':{'visible':False,'locked':True},
                          'BEND_GUIDES':{'visible':True,'locked':False}}})
