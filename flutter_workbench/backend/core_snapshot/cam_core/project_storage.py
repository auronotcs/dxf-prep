from __future__ import annotations

"""Small persistence boundary for native project documents.

The model owns the schema.  This module owns filesystem I/O so successful
saves remain byte-compatible while interrupted writes cannot leave a partly
written project in place.
"""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping


def serialize_project_payload(payload: Mapping[str, Any]) -> str:
    """Return the canonical, human-readable native document representation."""
    return json.dumps(payload, indent=2) + "\n"


def write_project_payload(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace *path* with a serialized project payload.

    The temporary file is created beside the destination so ``os.replace`` is
    atomic on the same filesystem.  A failed save leaves the prior document
    untouched and removes its temporary file when possible.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialize_project_payload(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def read_project_payload(path: Path) -> dict[str, Any]:
    """Load and type-check the top-level native project payload."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CAM project document must contain a JSON object.")
    return payload
