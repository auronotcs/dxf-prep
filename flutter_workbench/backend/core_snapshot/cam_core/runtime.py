from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import struct
import sys


@dataclass(frozen=True)
class RuntimeCapabilities:
    bitness: int
    logical_cores: int
    python_version: str
    operating_system: str
    numpy_available: bool

    @property
    def is_64_bit(self) -> bool:
        return self.bitness == 64


def runtime_capabilities() -> RuntimeCapabilities:
    try:
        import numpy  # noqa: F401
        numpy_available = True
    except ImportError:
        numpy_available = False
    return RuntimeCapabilities(
        bitness=struct.calcsize("P") * 8,
        logical_cores=os.cpu_count() or 1,
        python_version=sys.version.split()[0],
        operating_system=platform.platform(),
        numpy_available=numpy_available,
    )


def recommended_stock_cell_size(width: float, height: float, max_cells: int, preferred: float) -> float:
    """Return the preferred cell size unless it exceeds the configured memory budget."""
    if width <= 0 or height <= 0 or max_cells <= 0 or preferred <= 0:
        raise ValueError("Positive stock dimensions, cell budget, and preferred cell size are required.")
    return max(preferred, (width * height / max_cells) ** 0.5)
