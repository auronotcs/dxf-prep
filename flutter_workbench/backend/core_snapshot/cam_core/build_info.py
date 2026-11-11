from __future__ import annotations

"""Small, dependency-free build identity boundary for source checkpoints."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


BUILD_INFO_SCHEMA = "cam-workbench-build-v1"
DEVELOPMENT_VERSION = "0.1.0-dev"


@dataclass(frozen=True)
class BuildInfo:
    version: str
    build_id: str
    source_revision: str = ""
    manifest_sha256: str = ""
    checkpointed: bool = False

    @property
    def window_label(self) -> str:
        return self.build_id if self.checkpointed else "uncheckpointed"

    @property
    def diagnostic_label(self) -> str:
        revision = f"; base {self.source_revision[:12]}" if self.source_revision else ""
        return f"{self.version} / {self.window_label}{revision}"


def build_info_path(root: Path | None = None) -> Path:
    application_root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return application_root / "cam_build.json"


def load_build_info(root: Path | None = None) -> BuildInfo:
    """Load a generated checkpoint identity, failing closed to uncheckpointed."""
    path = build_info_path(root)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != BUILD_INFO_SCHEMA:
            raise ValueError("unsupported build-info schema")
        version = str(payload["version"]).strip()
        build_id = str(payload["build_id"]).strip()
        manifest_sha256 = str(payload["manifest_sha256"]).strip().casefold()
        source_revision = str(payload.get("source_revision", "")).strip().casefold()
        if not version or not build_id or len(manifest_sha256) != 64:
            raise ValueError("incomplete build identity")
        return BuildInfo(
            version=version,
            build_id=build_id,
            source_revision=source_revision,
            manifest_sha256=manifest_sha256,
            checkpointed=True,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return BuildInfo(DEVELOPMENT_VERSION, "uncheckpointed")
