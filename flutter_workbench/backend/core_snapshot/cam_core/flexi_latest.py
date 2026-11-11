"""Manual, read-only selection of the latest completed captured Flexi DXF.

This is deliberately not a watcher or a delivery service. A caller must invoke
it explicitly, and only a capture with a matching completion sidecar is
accepted. If the newest candidate is incomplete or damaged, selection fails
instead of silently falling back to an older drawing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re

from .flexi_handoff import validate_complete_ascii_dxf


class LatestFlexiExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class LatestFlexiCapture:
    path: Path
    metadata_path: Path
    source_path: str
    captured_at: str
    size: int
    sha256: str
    mtime_ns: int

    def to_metadata(self) -> dict[str, object]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload["metadata_path"] = str(self.metadata_path)
        payload["import_mode"] = "manual-read-only"
        return payload


def _metadata_values(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise LatestFlexiExportError(f"Could not read capture completion metadata {path.name}: {exc}") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key.strip().casefold()] = value.strip()
    return result


def _candidate_time_ns(path: Path) -> int:
    sidecar = path.with_suffix(".txt")
    if sidecar.is_file():
        try:
            captured = _metadata_values(sidecar).get("captured", "")
            parsed = datetime.fromisoformat(captured)
            if parsed.tzinfo is not None:
                return int(parsed.timestamp() * 1_000_000_000)
        except (LatestFlexiExportError, ValueError, OverflowError):
            pass
    match = re.match(r"(\d{8})-(\d{6})-", path.name)
    if match is not None:
        try:
            parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            return int(parsed.timestamp() * 1_000_000_000)
        except (ValueError, OverflowError):
            pass
    try:
        return path.stat().st_mtime_ns
    except OSError as exc:
        raise LatestFlexiExportError(f"Could not inspect captured DXF {path.name}: {exc}") from exc


def _stable_capture_bytes(path: Path) -> tuple[bytes, int, int]:
    try:
        before = path.stat()
        first = path.read_bytes()
        middle = path.stat()
        second = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise LatestFlexiExportError(f"Could not read captured DXF {path.name}: {exc}") from exc
    before_signature = (before.st_size, before.st_mtime_ns)
    if (
        before_signature != (middle.st_size, middle.st_mtime_ns)
        or before_signature != (after.st_size, after.st_mtime_ns)
        or first != second
        or len(second) != after.st_size
    ):
        raise LatestFlexiExportError(
            f"The newest captured DXF is still changing: {path.name}. Try again after the export completes."
        )
    try:
        validate_complete_ascii_dxf(second)
    except RuntimeError as exc:
        raise LatestFlexiExportError(f"The newest captured DXF is incomplete: {path.name}. {exc}") from exc
    return second, after.st_size, after.st_mtime_ns


def verify_flexi_capture(capture: LatestFlexiCapture) -> None:
    """Reverify the selected immutable capture before adopting imported data."""
    data, size, mtime_ns = _stable_capture_bytes(capture.path)
    digest = hashlib.sha256(data).hexdigest().upper()
    if size != capture.size or mtime_ns != capture.mtime_ns or digest != capture.sha256:
        raise LatestFlexiExportError(
            f"The captured DXF changed after it was selected: {capture.path.name}. Nothing was opened."
        )


def latest_completed_flexi_capture(folder: Path) -> LatestFlexiCapture:
    root = Path(folder)
    if not root.is_dir():
        raise LatestFlexiExportError(
            "The captured Flexi export folder is unavailable:\n\n" + str(root)
        )
    try:
        candidates = [path for path in root.iterdir() if path.is_file() and path.suffix.casefold() == ".dxf"]
    except OSError as exc:
        raise LatestFlexiExportError(f"Could not inspect the captured Flexi export folder: {exc}") from exc
    if not candidates:
        raise LatestFlexiExportError(
            "No captured Flexi DXF is available yet. Export a DXF from Flexi, wait for the capture to finish, then try again."
        )

    # Rank every DXF first. Validation is intentionally applied only to the
    # winner so a damaged newest export cannot make an older file look current.
    selected = max(candidates, key=lambda path: (_candidate_time_ns(path), path.name.casefold()))
    metadata_path = selected.with_suffix(".txt")
    if not metadata_path.is_file():
        raise LatestFlexiExportError(
            f"The newest captured DXF has no completion record yet: {selected.name}. "
            "Wait for capture to finish and try again."
        )
    metadata = _metadata_values(metadata_path)
    required = ("source", "captured", "length", "sha256")
    missing = [name for name in required if not metadata.get(name)]
    if missing:
        raise LatestFlexiExportError(
            f"The newest capture completion record is incomplete ({', '.join(missing)}): {metadata_path.name}."
        )
    try:
        expected_size = int(metadata["length"])
        parsed_time = datetime.fromisoformat(metadata["captured"])
    except (ValueError, OverflowError) as exc:
        raise LatestFlexiExportError(
            f"The newest capture completion record is invalid: {metadata_path.name}."
        ) from exc
    if parsed_time.tzinfo is None:
        raise LatestFlexiExportError(
            f"The newest capture completion time has no time zone: {metadata_path.name}."
        )
    expected_hash = metadata["sha256"].upper()
    if not re.fullmatch(r"[0-9A-F]{64}", expected_hash):
        raise LatestFlexiExportError(
            f"The newest capture completion hash is invalid: {metadata_path.name}."
        )
    data, actual_size, mtime_ns = _stable_capture_bytes(selected)
    actual_hash = hashlib.sha256(data).hexdigest().upper()
    if actual_size != expected_size or actual_hash != expected_hash:
        raise LatestFlexiExportError(
            f"The newest captured DXF does not match its completion record: {selected.name}. Nothing was opened."
        )
    filename_hash = re.search(r"-([0-9A-Fa-f]{12})\.dxf$", selected.name)
    if filename_hash is not None and not expected_hash.startswith(filename_hash.group(1).upper()):
        raise LatestFlexiExportError(
            f"The newest captured DXF filename does not match its completion hash: {selected.name}."
        )
    return LatestFlexiCapture(
        path=selected.resolve(),
        metadata_path=metadata_path.resolve(),
        source_path=metadata["source"],
        captured_at=parsed_time.isoformat(),
        size=actual_size,
        sha256=actual_hash,
        mtime_ns=mtime_ns,
    )
