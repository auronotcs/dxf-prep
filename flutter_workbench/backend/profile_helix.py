"""Validate profile settings assigned directly by the Tkinter model editor."""
import math

FIELDS={'helix_min_diameter','helix_ramp_angle','helix_clearance','helix_finish_revolutions'}

def validate(operation):
    for key in FIELDS:
        value=getattr(operation,key)
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError(f'{key} must be a finite nonnegative number.')
    turns=operation.helix_finish_revolutions
    if type(turns) is not int or turns>100000:
        raise ValueError('Helix finish revolutions must be a whole number from 0 to 100000.')
    if operation.helix_min_diameter>operation.helix_diameter:
        raise ValueError('Minimum helix diameter cannot exceed the preferred helix diameter.')
    if operation.helix_ramp_angle>=90:
        raise ValueError('Helix ramp angle must be below 90 degrees; 0 uses pitch only.')
