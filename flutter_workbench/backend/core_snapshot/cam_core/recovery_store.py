from __future__ import annotations

"""Filesystem layout helpers for document-owned crash-recovery snapshots."""

import hashlib
import json
from pathlib import Path
import re


_NATIVE_SUFFIX = ".camjob.json"
_SESSION_SCHEMA = "cam-workbench-recovery-session-v1"
CLOSED_UNSAVED_RETENTION_NS = 14 * 24 * 60 * 60 * 1_000_000_000


def closed_unsaved_timestamp(context: dict) -> int | None:
    """Only explicit, valid close records qualify for automatic expiry."""
    value = context.get("closed_unsaved_at_ns")
    return value if type(value) is int and value > 0 else None


def expire_closed_unsaved_copy(legacy_path: Path, path: Path, context: dict, now_ns: int) -> bool:
    """Expire only an owned, marked temporary copy; never a user job/legacy file.

    Return True for an expired candidate even if deletion fails, so it is not
    offered indefinitely. A later launch can retry the individual file removal.
    Unknown/corrupt timestamps and linked files are preserved conservatively.
    """
    closed_at = closed_unsaved_timestamp(context)
    identity = context.get("document_id")
    if (closed_at is None or now_ns - closed_at < CLOSED_UNSAVED_RETENTION_NS
            or context.get("schema") != "cam-workbench-recovery-context-v1"
            or not isinstance(identity, str) or not identity
            or path != recovery_document_path(legacy_path, identity)
            or path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction()):
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def recovery_session_path(legacy_path: Path) -> Path:
    return recovery_store_directory(legacy_path) / "session.json"


def read_recovery_session(legacy_path: Path) -> dict:
    """Layout is advisory: missing/corrupt metadata must never hide documents."""
    try:
        value = json.loads(recovery_session_path(legacy_path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != _SESSION_SCHEMA:
            return {}
        ids = value.get("document_ids")
        active = value.get("active_document_id")
        if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
            return {}
        if not isinstance(active, str):
            return {}
        return {"document_ids": list(dict.fromkeys(ids)), "active_document_id": active}
    except (OSError, ValueError):
        return {}


def write_recovery_session(legacy_path: Path, document_ids: list[str], active_document_id: str) -> None:
    """Use the same atomic boundary as snapshots; no document content is changed."""
    from .project_storage import write_project_payload
    ids = list(dict.fromkeys(document_ids))
    write_project_payload(recovery_session_path(legacy_path), {
        "schema": _SESSION_SCHEMA,
        "document_ids": ids,
        "active_document_id": active_document_id if active_document_id in ids else "",
    })


def recovery_store_directory(legacy_path: Path) -> Path:
    """Return the per-document store beside the historical single snapshot."""
    name = legacy_path.name
    if name.casefold().endswith(_NATIVE_SUFFIX):
        name = name[:-len(_NATIVE_SUFFIX)]
    else:
        name = f"{legacy_path.stem}_documents"
    return legacy_path.with_name(name)


def recovery_document_path(legacy_path: Path, document_id: str) -> Path:
    """Return a traversal-safe snapshot path owned by one document identity."""
    value = str(document_id).strip()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not safe or safe != value or len(safe) > 96:
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:32]
        safe = f"document_{digest}"
    return recovery_store_directory(legacy_path) / f"{safe}{_NATIVE_SUFFIX}"


def recovery_candidate_paths(legacy_path: Path) -> list[Path]:
    """Return newest per-document snapshots plus the legacy file, if present."""
    directory = recovery_store_directory(legacy_path)
    paths = list(directory.glob(f"*{_NATIVE_SUFFIX}")) if directory.is_dir() else []
    if legacy_path.is_file():
        paths.append(legacy_path)

    def modified(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    # Newest still wins duplicate/legacy ownership. A separate explicit session
    # layout determines tab order; ties here have a deterministic fallback.
    return sorted(paths, key=lambda path: (-modified(path), str(path).casefold(), str(path)))
