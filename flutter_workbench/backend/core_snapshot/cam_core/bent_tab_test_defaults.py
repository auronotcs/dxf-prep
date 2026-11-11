"""Unqualified shop-test layout defaults, not a validated forming recipe.

The 3t radius and mid-thickness neutral axis are provisional geometry estimates.
They do not prescribe heating temperatures or establish structural performance.
All UI values in this module are inches, except angles in degrees.
"""
import math
from .bent_tab_forming import FORMING_METHODS,NEUTRAL_DATUM

THICKNESS_CHOICES = {
    '3 mm (.1181 in)': 3/25.4,
    '.150 in (3.81 mm)': .15,
    '4.5 mm (.1772 in)': 4.5/25.4,
}
CUSTOM_THICKNESS = 'Custom / measured'


def shop_correction(thickness,angle,method):
    """One bounded next-test correction, not a recipe for other stock/bends."""
    return (-.5 if abs(thickness-4.5/25.4)<1e-7 and abs(angle-90)<1e-7
            and method==FORMING_METHODS[1] else 0.0)


def estimated_bend(thickness, angle):
    if not math.isfinite(thickness) or thickness<=0:
        raise ValueError('Sheet thickness must be a positive finite number.')
    if not math.isfinite(angle) or not 0<angle<180:
        raise ValueError('Bend angle must be between 0 and 180 degrees.')
    radius=3*thickness
    return radius,math.radians(angle)*(radius+thickness/2)


def initial_values(saved=None):
    saved=saved if isinstance(saved,dict) else {}
    defaults={'thickness':f'{4.5/25.4:.12g}','angle_degrees':'90','finished_standoff':'1.5',
              'hole_diameter':'0','hole_tip_offset':'.375','method':FORMING_METHODS[1],
              'flat_length_adjustment':'0','standoff_datum':NEUTRAL_DATUM}
    values={key:str(saved.get(key,'')) if str(saved.get(key,'')).strip() else value
            for key,value in defaults.items()}
    # Existing explicit (including invalid-but-entered) values are not replaced.
    custom=any(str(saved.get(key,'')).strip() for key in ('inside_radius','bend_allowance'))
    if not custom and values['method']==FORMING_METHODS[0]:values['method']=FORMING_METHODS[1]
    automatic=saved.get('use_test_estimate',not custom) is True
    try:radius,allowance=estimated_bend(float(values['thickness']),float(values['angle_degrees']))
    except ValueError:radius=allowance=None
    for key,value in (('inside_radius',radius),('bend_allowance',allowance)):
        prior=str(saved.get(key,''))
        values[key]=prior if prior.strip() else (f'{value:.12g}' if value is not None else '')
    return values,automatic


def thickness_label(value):
    try:number=float(value)
    except (ValueError,TypeError):return CUSTOM_THICKNESS
    return next((label for label,size in THICKNESS_CHOICES.items() if abs(number-size)<1e-8),CUSTOM_THICKNESS)
