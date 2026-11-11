"""Native-project filename policy, independent of dialogs and JSON persistence."""
from pathlib import Path
import re

NATIVE_PROJECT_SUFFIX='.camjob.json'
_TERMINAL_SUFFIX=re.compile(r'(?:\.camjob(?:\.json)?)+$',re.IGNORECASE)


def project_basename(name: str) -> str:
    """Remove only native terminal suffixes; preserve dotted/version basenames."""
    return _TERMINAL_SUFFIX.sub('',name)


def is_native_project_path(path: Path | str) -> bool:
    return _TERMINAL_SUFFIX.search(Path(path).name) is not None


def native_project_path(path: Path | str) -> Path:
    destination=Path(path)
    base=project_basename(destination.name)
    if not base or base in {'.','..'}:
        raise ValueError('Enter a project name before the CAM project extension.')
    return destination.with_name(base+NATIVE_PROJECT_SUFFIX)
