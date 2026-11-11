from __future__ import annotations

from dataclasses import dataclass

from .units import unit_scale


@dataclass(frozen=True)
class ThreadStandard:
    designation: str
    nominal_diameter_in: float
    tpi: int
    pitch_diameter_min_in: float
    pitch_diameter_max_in: float
    minor_diameter_min_in: float
    minor_diameter_max_in: float
    thread_class: str = "2B"
    included_angle_degrees: float = 60.0

    @property
    def pitch_in(self) -> float:
        return 1.0 / self.tpi

    @property
    def basic_pitch_diameter_in(self) -> float:
        return self.pitch_diameter_min_in

    def in_units(self, units: str) -> "ThreadDimensions":
        factor = unit_scale("in", units)
        return ThreadDimensions(
            standard=self,
            nominal_diameter=self.nominal_diameter_in * factor,
            pitch=self.pitch_in * factor,
            pitch_diameter_min=self.pitch_diameter_min_in * factor,
            pitch_diameter_max=self.pitch_diameter_max_in * factor,
            minor_diameter_min=self.minor_diameter_min_in * factor,
            minor_diameter_max=self.minor_diameter_max_in * factor,
        )


@dataclass(frozen=True)
class ThreadDimensions:
    standard: ThreadStandard
    nominal_diameter: float
    pitch: float
    pitch_diameter_min: float
    pitch_diameter_max: float
    minor_diameter_min: float
    minor_diameter_max: float


# ASME B1.1 Unified internal Class 2B limits. These two deliberately scoped
# presets are the initial qualified thread standards for CAM Workbench.
THREAD_STANDARDS: dict[str, ThreadStandard] = {
    "10-24 UNC-2B": ThreadStandard("10-24 UNC-2B", 0.1900, 24, 0.1629, 0.1672, 0.1450, 0.1560),
    "1/4-20 UNC-2B": ThreadStandard("1/4-20 UNC-2B", 0.2500, 20, 0.2175, 0.2224, 0.1960, 0.2070),
}


def thread_standard(designation: str) -> ThreadStandard:
    try:
        return THREAD_STANDARDS[designation]
    except KeyError as exc:
        raise ValueError(f"Unsupported qualified thread: {designation!r}.") from exc


def thread_standards_for_pilot_diameters(diameters: list[float], units: str) -> list[str]:
    """Return standards whose complete pilot range contains every diameter."""
    if not diameters:
        return []
    return [
        designation
        for designation, standard in THREAD_STANDARDS.items()
        if all(
            standard.in_units(units).minor_diameter_min - 1e-9
            <= diameter
            <= standard.in_units(units).minor_diameter_max + 1e-9
            for diameter in diameters
        )
    ]
