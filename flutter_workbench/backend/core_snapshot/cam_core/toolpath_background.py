"""Shared toolpath-preview generation for synchronous and isolated workers."""

from __future__ import annotations

from collections.abc import Callable

from .model import CamProject
from .stock import generate_operation_toolpath


def generate_toolpath_preview_bundle(
        project: CamProject, *,
        operation_ids: set[str] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None):
    """Generate enabled operations (all by default), retaining dependency context."""
    previews = {}
    errors: dict[str, str] = {}
    operations = [operation for operation in project.operations if operation.enabled
                  and (operation_ids is None or operation.id in operation_ids)]
    total = len(operations)
    for index, operation in enumerate(operations, start=1):
        if cancel_check is not None and cancel_check():
            raise InterruptedError("Toolpath preview generation canceled.")
        if progress_callback is not None:
            progress_callback(index - 1, total, operation.name)
        try:
            previews[operation.id] = generate_operation_toolpath(project, operation)
        except ValueError as exc:
            errors[operation.id] = str(exc)
        if progress_callback is not None:
            progress_callback(index, total, operation.name)
    return previews, errors


def toolpath_preview_process_worker(
        project: CamProject, request_id: str, result_queue, cancel_event) -> None:
    """Process target: compute an all-or-nothing preview bundle in isolation."""
    def report(completed: int, total: int, operation_name: str) -> None:
        result_queue.put((
            "progress", request_id, completed, total, operation_name,
        ))

    try:
        previews, errors = generate_toolpath_preview_bundle(
            project,
            cancel_check=cancel_event.is_set,
            progress_callback=report,
        )
        if cancel_event.is_set():
            raise InterruptedError("Toolpath preview generation canceled.")
        result_queue.put(("done", request_id, previews, errors))
    except InterruptedError:
        result_queue.put(("canceled", request_id))
    except Exception as exc:
        result_queue.put(("failed", request_id, str(exc)))
