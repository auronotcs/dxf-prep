from __future__ import annotations

"""Validated, recoverable persistence for application settings.

Settings contain the shop tool/recipe catalog, so they receive the same
atomic-write discipline as native project files.  This module owns only the
storage contract; versioned semantic migrations remain explicit at the app
boundary where catalog defaults and preference schemas are known.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping


SETTINGS_SCHEMA_ID = "cam-workbench-settings"
SETTINGS_SCHEMA_VERSION = 1


class SettingsStorageError(ValueError):
    pass


@dataclass(frozen=True)
class SettingsLoadResult:
    payload: dict[str, Any]
    source: str
    warning: str = ""
    recovery_path: Path | None = None


def settings_backup_path(path: Path) -> Path:
    destination = Path(path)
    return destination.with_name(destination.name + ".bak")


def validate_settings_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsStorageError("Settings must contain one JSON object.")
    schema = payload.get("settings_schema")
    if schema not in (None, SETTINGS_SCHEMA_ID):
        raise SettingsStorageError(f"Unsupported settings schema: {schema!r}.")
    version = payload.get("settings_schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise SettingsStorageError("Settings schema version must be a nonnegative integer.")
    if version > SETTINGS_SCHEMA_VERSION:
        raise SettingsStorageError(
            f"Settings version {version} is newer than this CAM Workbench supports "
            f"({SETTINGS_SCHEMA_VERSION})."
        )
    expected_types = {
        "tool_catalog": list,
        "recipe_context_preferences": dict,
        "quick_cut_recipes": dict,
        "workspace_layouts": dict,
        "color_palettes": list,
    }
    for key, expected in expected_types.items():
        if key in payload and not isinstance(payload[key], expected):
            raise SettingsStorageError(
                f"Settings field {key!r} must be a {expected.__name__}."
            )
    # Round-trip here catches non-JSON application objects before a write can
    # replace the last valid shop configuration.
    try:
        return json.loads(json.dumps(payload))
    except (TypeError, ValueError) as exc:
        raise SettingsStorageError(f"Settings contain a value JSON cannot preserve: {exc}") from exc


def read_settings_payload(path: Path) -> SettingsLoadResult:
    destination = Path(path)
    if not destination.exists():
        return SettingsLoadResult({}, "defaults")
    try:
        return SettingsLoadResult(
            validate_settings_payload(json.loads(destination.read_text(encoding="utf-8"))),
            "primary",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, SettingsStorageError) as primary_error:
        backup = settings_backup_path(destination)
        try:
            recovered = validate_settings_payload(json.loads(backup.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, SettingsStorageError):
            return SettingsLoadResult(
                {}, "defaults",
                warning=(
                    f"CAM Workbench could not read {destination.name}: {primary_error} "
                    "The damaged file was left untouched. Defaults are active; restore or inspect "
                    f"{destination} before replacing shop catalog settings."
                ),
                recovery_path=destination,
            )
        return SettingsLoadResult(
            recovered, "backup",
            warning=(
                f"CAM Workbench could not read {destination.name}: {primary_error} "
                f"The damaged file was left untouched and the last valid backup was loaded from {backup}. "
                "Review the recovered settings, then save to repair the primary file."
            ),
            recovery_path=backup,
        )


def write_settings_payload(path: Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    validated = validate_settings_payload(dict(payload))
    text = json.dumps(validated, indent=2) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        prior = destination.read_bytes()
        try:
            validate_settings_payload(json.loads(prior.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, SettingsStorageError):
            _write_recovery_copy(destination, prior)
        else:
            _atomic_write_bytes(settings_backup_path(destination), prior)
    _atomic_write_text(destination, text)


def _write_recovery_copy(destination: Path, content: bytes) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = destination.with_name(f"{destination.name}.corrupt-{stamp}")
    suffix = 2
    while candidate.exists():
        candidate = destination.with_name(
            f"{destination.name}.corrupt-{stamp}-{suffix}",
        )
        suffix += 1
    _atomic_write_bytes(candidate, content)
    return candidate


def _atomic_write_text(destination: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="",
            prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
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


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
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
