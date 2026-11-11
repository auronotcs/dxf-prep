"""Inactive, isolated Flexi DXF capture/inbox prototype.

This module deliberately contains no filesystem watcher, process launcher,
association change, startup hook, or production-folder default.  Callers must
explicitly opt into ``synthetic_mode`` and provide an inbox root.  Capture and
delivery have separate durable states so a failed import can be replayed
without recapturing or silently losing the file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from .dxf import import_dxf


HANDOFF_SCHEMA = "cam-workbench.isolated-flexi-inbox"
HANDOFF_VERSION = 1


class HandoffCaptureError(RuntimeError):
    pass


@dataclass
class CaptureRecord:
    id: str
    event_key: str
    source_path: str
    captured_path: str = ""
    source_size: int = 0
    source_mtime_ns: int = 0
    sha256: str = ""
    captured_at_utc: str = ""
    capture_status: str = "waiting"
    capture_error: str = ""
    delivery_status: str = "pending"
    delivery_attempts: int = 0
    delivery_error: str = ""
    last_delivery_at_utc: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CaptureRecord":
        return cls(**payload)


@dataclass(frozen=True)
class DeliveryAttempt:
    record_id: str
    accepted: bool
    replayed: bool
    result: Any = None
    error: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_complete_ascii_dxf(data: bytes) -> None:
    """Reject an empty, partial, binary, or structurally incomplete DXF."""
    if data.startswith(b"AutoCAD Binary DXF"):
        raise HandoffCaptureError("Binary DXF is not supported by the isolated handoff.")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HandoffCaptureError("Captured DXF is not ASCII text.") from exc
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) < 4 or len(lines) % 2:
        raise HandoffCaptureError("DXF is incomplete: group-code/value pairs are truncated.")
    pairs = [(code.strip(), value.strip()) for code, value in zip(lines[0::2], lines[1::2])]
    if not any(code == "0" and value.casefold() == "section" for code, value in pairs):
        raise HandoffCaptureError("DXF is incomplete: SECTION header was not found.")
    if not any(code == "0" and value.casefold() == "endsec" for code, value in pairs):
        raise HandoffCaptureError("DXF is incomplete: ENDSEC record was not found.")
    if pairs[-1] != ("0", "EOF"):
        raise HandoffCaptureError("DXF is incomplete: final EOF record was not found.")


class IsolatedFlexiInbox:
    """Durable capture queue that is impossible to activate implicitly."""

    def __init__(self, root: Path, *, synthetic_mode: bool = False) -> None:
        if not synthetic_mode:
            raise HandoffCaptureError(
                "The Flexi handoff is inactive. Explicit synthetic_mode=True is required for isolated testing."
            )
        self.root = Path(root).resolve()
        self.capture_root = self.root / "captures"
        self.stage_root = self.root / "staging"
        self.state_path = self.root / "inbox-state.json"
        self.capture_root.mkdir(parents=True, exist_ok=True)
        self.stage_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.records: list[CaptureRecord] = []
        self._load()

    @property
    def automatic_workbench_delivery(self) -> bool:
        return False

    def _load(self) -> None:
        if not self.state_path.is_file():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("schema") != HANDOFF_SCHEMA or payload.get("version") != HANDOFF_VERSION:
            raise HandoffCaptureError("Unsupported isolated Flexi inbox state.")
        if payload.get("automatic_workbench_delivery") is not False:
            raise HandoffCaptureError("Automatic Workbench delivery must remain disabled in this prototype.")
        self.records = [CaptureRecord.from_dict(item) for item in payload.get("records", [])]

    def _save(self) -> None:
        payload = {
            "schema": HANDOFF_SCHEMA,
            "version": HANDOFF_VERSION,
            "automatic_workbench_delivery": False,
            "records": [asdict(record) for record in self.records],
        }
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                prefix=".inbox-state.", suffix=".tmp", dir=self.root,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _record_for_event(self, event_key: str, source: Path) -> CaptureRecord:
        existing = next((item for item in self.records if item.event_key == event_key), None)
        if existing is not None:
            return existing
        record = CaptureRecord(
            id=f"capture_{uuid4().hex}", event_key=event_key,
            source_path=str(source), capture_status="waiting",
        )
        self.records.append(record)
        return record

    def get(self, record_id: str) -> CaptureRecord:
        record = next((item for item in self.records if item.id == record_id), None)
        if record is None:
            raise HandoffCaptureError(f"Capture record {record_id!r} was not found.")
        return record

    def capture(
        self,
        source_path: Path,
        *,
        event_key: str | None = None,
        stable_checks: int = 3,
        poll_interval: float = 0.02,
        timeout: float = 2.0,
        read_bytes: Callable[[Path], bytes] | None = None,
    ) -> CaptureRecord:
        """Wait for stable complete bytes, verify a staged copy, then publish.

        Reusing one event key coalesces duplicate filesystem notifications.
        A new event key intentionally creates a new capture even for identical
        content. A failed event retains retryable state and may be called again.
        """
        if stable_checks < 2 or poll_interval < 0 or timeout < 0:
            raise ValueError("Capture stability settings are invalid.")
        source = Path(source_path).resolve()
        key = event_key or f"intentional_{uuid4().hex}"
        reader = read_bytes or (lambda path: path.read_bytes())
        with self._lock:
            record = self._record_for_event(key, source)
            if record.capture_status == "captured":
                return record
            record.capture_status = "waiting"
            record.capture_error = ""
            self._save()
            started = time.monotonic()
            previous: tuple[int, int] | None = None
            matches = 0
            last_error = "Source file did not become stable."
            while True:
                try:
                    before = source.stat()
                    signature = (before.st_size, before.st_mtime_ns)
                    matches = matches + 1 if signature == previous else 1
                    previous = signature
                    if matches >= stable_checks:
                        data = reader(source)
                        after = source.stat()
                        if signature != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
                            matches = 0
                            last_error = "Source changed while it was being read."
                        else:
                            # A second read closes the same-size/coarse-mtime
                            # race that metadata-only settling cannot detect.
                            verification = reader(source)
                            final = source.stat()
                            if data != verification or signature != (final.st_size, final.st_mtime_ns):
                                matches = 0
                                last_error = "Source bytes changed during final verification."
                            else:
                                validate_complete_ascii_dxf(verification)
                                return self._publish(record, source, verification, final.st_size, final.st_mtime_ns)
                except (OSError, HandoffCaptureError) as exc:
                    matches = 0
                    last_error = str(exc)
                if time.monotonic() - started >= timeout:
                    record.capture_status = "failed_retryable"
                    record.capture_error = last_error
                    self._save()
                    raise HandoffCaptureError(last_error)
                time.sleep(poll_interval)

    def _publish(self, record: CaptureRecord, source: Path, data: bytes, size: int, mtime_ns: int) -> CaptureRecord:
        expected = _sha256(data)
        suffix = source.suffix if source.suffix.casefold() == ".dxf" else ".dxf"
        safe_stem = "".join(character if character.isalnum() or character in "-_ " else "_" for character in source.stem).strip() or "capture"
        destination = self.capture_root / f"{record.id}_{safe_stem}{suffix}"
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(mode="wb", delete=False, prefix=f".{record.id}.", suffix=".tmp", dir=self.stage_root) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if _sha256(temporary.read_bytes()) != expected:
                raise HandoffCaptureError("Staged capture hash did not match the settled source bytes.")
            os.replace(temporary, destination)
            temporary = None
            if _sha256(destination.read_bytes()) != expected:
                destination.unlink(missing_ok=True)
                raise HandoffCaptureError("Published capture hash verification failed.")
        except Exception:
            record.capture_status = "failed_retryable"
            record.capture_error = "Capture copy or hash verification failed."
            self._save()
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        record.captured_path = str(destination)
        record.source_size = size
        record.source_mtime_ns = mtime_ns
        record.sha256 = expected
        record.captured_at_utc = _utc_now()
        record.capture_status = "captured"
        record.capture_error = ""
        record.delivery_status = "pending"
        self._save()
        return record

    def deliver(
        self,
        record_id: str,
        importer: Callable[[Path], Any],
        *,
        replay: bool = False,
    ) -> DeliveryAttempt:
        """Deliberately deliver one immutable copy; never runs automatically."""
        with self._lock:
            record = self.get(record_id)
            if record.capture_status != "captured" or not record.captured_path:
                raise HandoffCaptureError("Only a verified captured file can be delivered.")
            if record.delivery_status == "accepted" and not replay:
                return DeliveryAttempt(record.id, True, False)
            record.delivery_attempts += 1
            record.last_delivery_at_utc = _utc_now()
            try:
                result = importer(Path(record.captured_path))
            except Exception as exc:
                record.delivery_status = "failed_retryable"
                record.delivery_error = str(exc)
                self._save()
                return DeliveryAttempt(record.id, False, replay, error=str(exc))
            record.delivery_status = "accepted"
            record.delivery_error = ""
            self._save()
            return DeliveryAttempt(record.id, True, replay, result=result)

    def import_captured_dxf(self, record_id: str, *, replay: bool = False) -> DeliveryAttempt:
        """Manual import through the existing DXF owner; creates no watcher."""
        return self.deliver(record_id, import_dxf, replay=replay)
