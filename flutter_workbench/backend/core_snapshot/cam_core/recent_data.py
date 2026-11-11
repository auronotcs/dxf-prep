"""Persistent local access history, independent of the eight-item quick menu.

Reading history never probes or removes missing files and never invents access
dates for legacy entries. No folders are scanned to populate this index.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import json

from .project_storage import write_project_payload


def path_key(path):
    return os.path.abspath(os.path.expanduser(str(path))).casefold()


def access_date(value):
    if not value:
        return "Access date unavailable"
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            return "Access date unavailable"
        return "Accessed " + date.astimezone().strftime("%b %d, %Y %I:%M %p %Z")
    except (ValueError, TypeError):
        return "Access date unavailable"


def newest_first(rows):
    def stamp(row):
        try:
            value = datetime.fromisoformat(row.get('accessed_at'))
            return value.timestamp() if value.tzinfo is not None else float('-inf')
        except (TypeError, ValueError, OverflowError, OSError):
            return float('-inf')
    return sorted(rows, key=stamp, reverse=True)


class RecentDataStore:
    def __init__(self, path, legacy_paths=()):
        self.path = Path(path)
        self.warning = ""
        self.files = []
        self.folders = []
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("schema") != 1:
                    raise ValueError("unsupported history format")
                self.files = self._rows(payload.get("files"))
                self.folders = self._rows(payload.get("folders"))
            except (OSError, ValueError, TypeError) as exc:
                self.warning = f"Recent Data history could not be read: {exc}. Existing history will not be overwritten."
        else:
            self.files = self._rows([{"path": str(p), "accessed_at": None} for p in legacy_paths])
            self.folders = self._rows([{"path": str(Path(r['path']).parent), "accessed_at": None} for r in self.files])

    @staticmethod
    def _rows(values):
        if not isinstance(values, list):
            raise ValueError("history entries must be a list")
        rows, seen = [], set()
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("path"), str) or not value["path"].strip():
                continue
            key = path_key(value["path"])
            if key not in seen:
                seen.add(key)
                rows.append({"path": value["path"], "accessed_at": value.get("accessed_at")})
        return newest_first(rows)

    def record_file(self, path, accessed_at=None):
        if self.warning:
            raise OSError(self.warning)
        path = Path(os.path.abspath(os.path.expanduser(str(path))))
        stamp = accessed_at or datetime.now(timezone.utc).isoformat()
        # Files and folders are distinct histories; successful access updates
        # both in one atomic write, with no fixed eight-file retention limit.
        files = [{"path": str(path), "accessed_at": stamp},
                 *(row for row in self.files if path_key(row["path"]) != path_key(path))]
        folders = [{"path": str(path.parent), "accessed_at": stamp},
                   *(row for row in self.folders if path_key(row["path"]) != path_key(path.parent))]
        files, folders = newest_first(files), newest_first(folders)
        write_project_payload(self.path, {"schema": 1, "files": files, "folders": folders})
        self.files, self.folders = files, folders

    def records(self, kind="files", query="", folder=None):
        rows = self.folders if kind == "folders" else self.files
        query = query.strip().casefold()
        return [dict(row) for row in rows
                if (not query or query in row["path"].casefold())
                and (folder is None or path_key(Path(row["path"]).parent) == path_key(folder))]
