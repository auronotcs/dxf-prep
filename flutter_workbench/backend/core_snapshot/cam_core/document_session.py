from __future__ import annotations

"""UI-independent ownership for open documents and asynchronous result tokens."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .model import CamProject


def new_document_id() -> str:
    return f"document_{uuid4().hex}"


@dataclass(frozen=True)
class DocumentRevisionToken:
    """Identity captured when work starts and checked before its result is used."""

    document_id: str
    revision: int
    purpose: str
    request_id: str = field(default_factory=lambda: f"request_{uuid4().hex}")

    def matches(self, document_id: str, revision: int) -> bool:
        return self.document_id == document_id and self.revision == revision


@dataclass
class DocumentSession:
    """All non-widget state that belongs to one open document tab."""

    project: CamProject
    document_id: str = field(default_factory=new_document_id)
    revision: int = 0
    project_path: Path | None = None
    project_file_signature: dict[str, Any] | None = None
    selected_ids: set[str] = field(default_factory=set)
    selected_operation_ids: set[str] = field(default_factory=set)
    toolpath_previews: dict[str, Any] = field(default_factory=dict)
    history_entries: list[dict[str, Any]] = field(default_factory=list)
    history_index: int = -1
    dirty: bool = False
    toolpath_session_memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    view_state: dict[str, Any] = field(default_factory=dict)

    def advance_revision(self) -> int:
        self.revision += 1
        return self.revision

    def issue_token(self, purpose: str) -> DocumentRevisionToken:
        return DocumentRevisionToken(self.document_id, self.revision, purpose)

    def accepts(self, token: DocumentRevisionToken) -> bool:
        return token.matches(self.document_id, self.revision)


class DocumentSessionController:
    """Registry of tab handles to document-owned state.

    Tk widget IDs stay outside the model; callers may use any stable string as
    a handle. Activating a session never mutates another session.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, DocumentSession] = {}
        self.active_handle: str | None = None

    def store(self, handle: str, session: DocumentSession) -> None:
        if not handle:
            raise ValueError("A document-session handle cannot be blank.")
        self._sessions[handle] = session

    def get(self, handle: str) -> DocumentSession | None:
        return self._sessions.get(handle)

    def activate(self, handle: str) -> DocumentSession | None:
        self.active_handle = handle
        return self.get(handle)

    def remove(self, handle: str) -> DocumentSession | None:
        session = self._sessions.pop(handle, None)
        if self.active_handle == handle:
            self.active_handle = None
        return session

    @property
    def active(self) -> DocumentSession | None:
        return self.get(self.active_handle) if self.active_handle is not None else None

    @property
    def sessions(self) -> tuple[DocumentSession, ...]:
        """Return a stable snapshot of every open tab's document state."""
        return tuple(self._sessions.values())

    def __len__(self) -> int:
        return len(self._sessions)
