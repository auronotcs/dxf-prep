"""Isolated, atomic crash recovery. No production paths or original documents are written."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4
from manufacturing_support import supported
from cam_core.model import CamProject, SUPPORTED_UNITS
from cam_core.layout import drawing_bounds, entity_perimeter
from cam_core.project_storage import write_project_payload, serialize_project_payload

SCHEMA = 'flutter-design-recovery-v1'
MAX_BYTES = 64 * 1024 * 1024
IDENTITY = re.compile(r'^[0-9a-f]{32}$')


def file_token(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def validate_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Recovery contains nonfinite coordinates.')
    if isinstance(value, dict):
        for item in value.values(): validate_finite(item)
    elif isinstance(value, list):
        for item in value: validate_finite(item)


def lock_file(path):
    stream = path.open('a+b')
    try:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise BlockingIOError('Recovery belongs to an active session.')


class RecoveryStore:
    def __init__(self, directory):
        self.root = Path(directory).absolute()
        for path in [self.root, *self.root.parents]:
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Recovery directory must not be a linked location.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity = uuid4().hex
        self.lock = lock_file(self.path(self.identity, '.lock'))
        self.last_digest = None
        self.updated = None
        self.candidates = []
        try: self.scan()
        except Exception:
            self.lock.close()
            raise

    def path(self, identity, suffix='.json'):
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise ValueError('Invalid recovery identity.')
        path = self.root / (identity + suffix)
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('Recovery file must remain in its owned directory.')
        return path

    def read(self, identity):
        path = self.path(identity)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Recovery exceeds the 64 MiB review limit; file retained.')
        raw = path.read_bytes()
        token = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') != SCHEMA:
            raise ValueError('Unsupported recovery format; file retained.')
        validate_finite(data)
        project = CamProject.from_dict(data['project'])
        validate_finite(project.to_dict())
        if project.units not in SUPPORTED_UNITS:
            raise ValueError('Unsupported recovery units; file retained.')
        drawing_bounds(project.entities, include_annotations=True)
        for entity in project.entities: entity_perimeter(entity)
        if not isinstance(data.get('selected', []), list) or not all(isinstance(i, str) for i in data.get('selected', [])):
            raise ValueError('Invalid recovery selection; file retained.')
        if not isinstance(data.get('source', ''), str):
            raise ValueError('Invalid recovery source; file retained.')
        if not supported(project):
            raise ValueError('Recovery with machining operations is not supported; file retained.')
        return data, project, token

    @contextmanager
    def candidate(self, identity, token):
        if identity == self.identity:
            raise ValueError('Cannot replace the active recovery copy.')
        lock = lock_file(self.path(identity, '.lock'))
        try:
            path = self.path(identity)
            if file_token(path) != token:
                raise ValueError('Recovery changed. Refresh the list and try again.')
            yield path
        finally:
            lock.close()

    def scan(self):
        entries = []
        for path in self.root.glob('*.json'):
            identity = path.stem
            if identity == self.identity or not IDENTITY.fullmatch(identity):
                continue
            try:
                self.path(identity)
                lock = lock_file(self.path(identity, '.lock'))
            except (OSError, ValueError):
                continue
            try:
                try:
                    data, project, token = self.read(identity)
                    entries.append(dict(id=identity, token=token, name=project.name,
                                        units=project.units, objects=len(project.entities),
                                        dimensions=len(project.dimensions), guides=len(project.guides),
                                        source=str(data.get('source') or ''), updated=data.get('updated'),
                                        error=None))
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    # Invalid copies remain visible for deliberate discard, never silently deleted.
                    try: token = file_token(path)
                    except OSError: token = None
                    entries.append(dict(id=identity, token=token,
                                        name='Unreadable recovery', source='', updated=None, error=str(exc)))
            finally:
                lock.close()
            entries.sort(key=lambda item: str(item.get('updated') or ''), reverse=True)
        self.candidates = entries
        return entries

    def checkpoint(self, project, source, selection, snapshot, digest, *, force=False):
        if not force and digest == self.last_digest:
            return
        updated = datetime.now(timezone.utc).isoformat()
        payload = dict(schema=SCHEMA, updated=updated, source=str(source or ''),
                       snapshot=snapshot, selected=sorted(selection), project=project.to_dict())
        validate_finite(payload)
        if len(serialize_project_payload(payload).encode('utf-8')) > MAX_BYTES:
            raise ValueError('Recovery exceeds 64 MiB. Use Save As; the previous copy is retained.')
        write_project_payload(self.path(self.identity), payload)
        self.last_digest = digest
        self.updated = updated

    def clear(self):
        self.path(self.identity).unlink(missing_ok=True)
        self.last_digest = None
        self.updated = None

    def close(self):
        self.lock.close()
        # Keep a lock inode beside an existing copy; OS release handles crashes too.
        if not self.path(self.identity).exists():
            self.path(self.identity, '.lock').unlink(missing_ok=True)

