"""Recovery inventory and guarded, explicitly selected batch deletion.

No originals are opened for writing. The only legacy exception to the managed
directory is the exact configured historical single-snapshot file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .recovery_store import (
    closed_unsaved_timestamp, expire_closed_unsaved_copy, read_recovery_session,
    recovery_candidate_paths, recovery_document_path, recovery_session_path,
    recovery_store_directory, write_recovery_session,
)

CONTEXT_KEY = "_cam_workbench_recovery_context"
CONTEXT_SCHEMA = "cam-workbench-recovery-context-v1"


def snapshot_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def recovery_saved_label(context: dict, path: Path) -> str:
    value = context.get("written_at_ns")
    recorded = type(value) is int and value > 0
    if not recorded:
        try:
            value = path.stat().st_mtime_ns
        except OSError:
            return "Unknown — timestamp unavailable"
    try:
        instant = datetime.fromtimestamp(value / 1_000_000_000, timezone.utc)
        try:
            local = instant.astimezone(ZoneInfo("America/Chicago"))
        except ZoneInfoNotFoundError:
            # Windows installations need not ship the optional tzdata package.
            # US Central rules since 2007; older dates are explicitly UTC.
            if instant.year < 2007:
                return instant.strftime("%Y-%m-%d %I:%M:%S %p UTC") + " (time-zone data unavailable)"
            march = datetime(instant.year, 3, 1, 8, tzinfo=timezone.utc)
            november = datetime(instant.year, 11, 1, 7, tzinfo=timezone.utc)
            start = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
            end = november + timedelta(days=(6 - november.weekday()) % 7)
            daylight = start <= instant < end
            local = instant.astimezone(timezone(timedelta(hours=-5 if daylight else -6),
                                               "CDT" if daylight else "CST"))
        label = local.strftime("%Y-%m-%d %I:%M:%S %p %Z")
        return label + ("" if recorded else " (file modified; legacy fallback)")
    except (OverflowError, OSError, ValueError, TypeError):
        return "Unknown — invalid timestamp"


@dataclass
class RecoveryEntry:
    path: Path
    project: object | None = None
    context: dict = field(default_factory=dict)
    error: str = ""
    stamp: tuple | None = None
    active: bool = False

    @property
    def document_id(self):
        return str(self.context.get("document_id", "")).strip()

    @property
    def job_name(self):
        name = str(getattr(self.project, "name", "") or "").strip()
        if not name or name.casefold() == "untitled":
            return "Unnamed job" if self.project is not None else "Unreadable / missing job"
        return name

    @property
    def original_path(self):
        return str(self.context.get("original_project_path", "") or "").strip()

    @property
    def status(self):
        if self.active:
            return "Already open — protected"
        if self.error:
            return self.error
        if closed_unsaved_timestamp(self.context) is not None:
            return "Closed unsaved — 14-day retention"
        return "Available"

    @property
    def can_open(self):
        return self.project is not None and not self.error and not self.active


def collect_recoveries(legacy_path: Path, layout: dict, *, autosave_enabled: bool,
                       now_ns: int, active_ids=()) -> list[RecoveryEntry]:
    from . import CamProject
    entries = []
    expired_paths = set()
    paths = recovery_candidate_paths(legacy_path)
    for identity in layout.get("document_ids", []):
        path = recovery_document_path(legacy_path, identity)
        if path not in paths:
            paths.append(path)
    for path in paths:
        entry = RecoveryEntry(path)
        try:
            entry.stamp = snapshot_stamp(path)
            entry.project = CamProject.load(path)
            raw = entry.project.metadata.get(CONTEXT_KEY)
            if isinstance(raw, dict) and raw.get("schema") == CONTEXT_SCHEMA:
                entry.context = dict(raw)
                entry.context["document_revision"] = int(raw.get("document_revision", 0) or 0)
                entry.context["document_id"] = str(raw.get("document_id", "")).strip()
                entry.project.metadata[CONTEXT_KEY] = dict(entry.context)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            entry.error = "Missing recovery copy" if not path.exists() else f"Unreadable: {exc}"
        # The filename still protects an active document if its payload is bad.
        entry.active = entry.document_id in active_ids or any(
            path == recovery_document_path(legacy_path, identity) for identity in active_ids)
        if not entry.active and expire_closed_unsaved_copy(legacy_path, path, entry.context, now_ns):
            if not path.exists():
                expired_paths.add(path.resolve())
            continue
        if not autosave_enabled and closed_unsaved_timestamp(entry.context) is None:
            continue
        entries.append(entry)
    if expired_paths:
        # Expiry must not reappear on the next launch as a missing-file row.
        # Only successfully expired copies lose their advisory session reference.
        try:
            _prune_recovery_references(legacy_path, expired_paths, active_ids)
        except (OSError, ValueError):
            pass  # A later missing-file row can explicitly retry reference cleanup.
    rank = {identity: index for index, identity in enumerate(layout.get("document_ids", []))}
    entries.sort(key=lambda e: rank.get(e.document_id, len(rank)))
    return entries


def _not_linked(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Linked recovery paths cannot be deleted")


def guard_recovery_path(legacy_path: Path, path: Path) -> None:
    """Require a direct managed snapshot, or the exact historical snapshot."""
    _not_linked(path)
    _not_linked(legacy_path.parent)
    resolved = path.resolve()
    directory = recovery_store_directory(legacy_path).resolve()
    legacy = legacy_path.resolve()
    if resolved != legacy and (resolved.parent != directory or not path.name.endswith(".camjob.json")):
        raise ValueError("Not a managed recovery copy")


@dataclass
class DeleteRecoveryResult:
    removed: list[RecoveryEntry] = field(default_factory=list)
    failed: list[tuple[RecoveryEntry, str]] = field(default_factory=list)
    reference_error: str = ""


def _prune_recovery_references(legacy_path: Path, removed_paths: set[Path], active_ids) -> None:
    _not_linked(recovery_session_path(legacy_path))
    layout = read_recovery_session(legacy_path)
    ids = [i for i in layout.get("document_ids", [])
           if recovery_document_path(legacy_path, i).resolve() not in removed_paths or i in active_ids]
    if ids != layout.get("document_ids", []):
        write_recovery_session(legacy_path, ids, layout.get("active_document_id", ""))


def delete_recoveries(legacy_path: Path, entries: list[RecoveryEntry], *, active_ids=(),
                      original_paths=()) -> DeleteRecoveryResult:
    """Delete exactly the selected, unchanged snapshots; failures are per-copy.

    Called synchronously on the UI thread, with current open document identities.
    A snapshot refreshed since inventory is refused, not silently deleted.
    """
    result = DeleteRecoveryResult()
    protected = {Path(p).resolve() for p in original_paths if p}
    # Original paths in any selected context remain protected even if corrupt
    # metadata accidentally points at another selected snapshot.
    invalid_originals = {}
    for entry in entries:
        if entry.original_path:
            try:
                protected.add(Path(entry.original_path).resolve())
            except (OSError, ValueError) as exc:
                invalid_originals[id(entry)] = f"Invalid original-job reference; copy preserved: {exc}"
    selected_paths = set()
    for entry in entries:
        if entry.path in selected_paths:
            continue
        selected_paths.add(entry.path)
        try:
            if id(entry) in invalid_originals:
                raise ValueError(invalid_originals[id(entry)])
            guard_recovery_path(legacy_path, entry.path)
            if entry.active or entry.document_id in active_ids or any(
                    entry.path.resolve() == recovery_document_path(legacy_path, i).resolve() for i in active_ids):
                raise ValueError("This recovery belongs to an open document")
            if entry.path.resolve() in protected:
                raise ValueError("This path is an original job, not a disposable recovery")
            current = snapshot_stamp(entry.path)
            if current is not None and current != entry.stamp:
                raise ValueError("Recovery changed since this list opened; close and reopen the list")
            entry.path.unlink(missing_ok=True)
            result.removed.append(entry)
        except (OSError, ValueError) as exc:
            result.failed.append((entry, str(exc)))
    if result.removed:
        # Re-read layout, and prune only references whose OWNED file was removed.
        # Deleting a duplicate legacy copy must not forget its managed sibling.
        try:
            removed_paths = {e.path.resolve() for e in result.removed}
            _prune_recovery_references(legacy_path, removed_paths, active_ids)
        except (OSError, ValueError) as exc:
            result.reference_error = f"Copies were removed, but recovery-list references could not be updated: {exc}"
    return result


def deletion_confirmation(entries: list[RecoveryEntry]) -> str:
    names = "\n".join(f"• {e.job_name} — {e.original_path or e.path.name}" for e in entries)
    return (f"Permanently delete {len(entries)} selected recovery cop{'y' if len(entries) == 1 else 'ies'}?\n\n"
            + names + "\n\nOnly these temporary copies will be deleted. Original saved jobs and unselected copies are unchanged. "
            "Deleted recovery copies cannot be reopened.")
