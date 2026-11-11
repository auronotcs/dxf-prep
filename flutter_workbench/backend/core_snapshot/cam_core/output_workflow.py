from __future__ import annotations

"""Controller-file output orchestration independent of desktop dialogs."""

from dataclasses import dataclass, replace
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from .model import CamProject
from .plates import Plate, PlateProjectResult, make_plate_project, output_base_name, save_plate_project
from .postprocess import cnc_output_filename, post_enroute_cnc
from .toolpath import GeneratedToolpath


OUTPUT_PACKAGES = {"CNC only", "CNC + CAM project", "Full package"}
OUTPUT_GENERATION_CACHE_VERSION = 29
_OUTPUT_ONLY_JOB_SETUP_KEYS = {
    "copy_output_to_origin",
    "output_directory",
    "output_package",
    "output_plate_numbers",
    "output_scope",
    "allow_negative_xy",
}


@dataclass(frozen=True)
class PreparedPlateOutput:
    plate: Plate
    result: PlateProjectResult
    controller_path: Path


@dataclass(frozen=True)
class PlateOutputExecution:
    controller_paths: tuple[Path, ...]
    project_paths: tuple[Path, ...]
    setup_sheet_paths: tuple[Path, ...]
    shop_drawing_paths: tuple[Path, ...]
    origin_copy_paths: tuple[Path, ...]
    skipped_operation_count: int
    manifest_path: Path | None = None


SetupSheetWriter = Callable[
    [Path, str, list[tuple[Plate, PlateProjectResult]], list[Path], list[Path]],
    Path,
]
ShopDrawingWriter = Callable[
    [Path, str, Plate, PlateProjectResult],
    Path,
]
PublishReplace = Callable[[Path, Path], None]


class OutputPublicationError(OSError):
    """A publication failed and one or more prior targets could not be restored."""

    rollback_complete = False


@dataclass(frozen=True)
class _StagedOutput:
    source: Path
    destination: Path
    kind: str


def output_generation_fingerprint(
    project: CamProject,
    plates: Iterable[Plate],
    generation_context: Mapping[str, object] | None = None,
) -> str:
    """Return a stable identity for every input that can affect generated output.

    File destinations and checkpoint choices are deliberately omitted: they
    affect publication, not controller motion or preflight. Callers include
    document identity/revision separately so separate jobs never share a
    session artifact accidentally.
    """
    project_payload = project.to_dict()
    metadata = project_payload.get("metadata")
    if isinstance(metadata, dict):
        job_setup = metadata.get("job_setup")
        if isinstance(job_setup, dict):
            metadata["job_setup"] = {
                key: value for key, value in job_setup.items()
                if key not in _OUTPUT_ONLY_JOB_SETUP_KEYS
            }
    payload = {
        "cache_schema": OUTPUT_GENERATION_CACHE_VERSION,
        "postprocessor": "enroute-controller-neutral-v1",
        "project": project_payload,
        "plates": [plate.to_dict() for plate in plates],
        "generation_context": dict(generation_context or {}),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, payload: dict) -> None:
    """Durably replace a small output journal without exposing partial JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _package_manifest_path(items: tuple[PreparedPlateOutput, ...]) -> Path:
    numbers = [item.plate.number for item in items]
    base_name, directory = controller_output_choice(items[0].controller_path, numbers)
    return directory / f"{base_name} Output Manifest.json"


def _manifest_payload(
    package_id: str,
    output_package: str,
    items: tuple[PreparedPlateOutput, ...],
    state: str,
    *,
    outputs: Iterable[_StagedOutput] = (),
    started_utc: str,
    error: str = "",
    rollback_complete: bool | None = None,
) -> dict:
    output_records = []
    for output in outputs:
        if output.kind == "manifest":
            continue
        record = {"kind": output.kind, "path": str(output.destination)}
        if output.source.is_file():
            digest = hashlib.sha256()
            with output.source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            record.update(size_bytes=output.source.stat().st_size, sha256=digest.hexdigest())
        output_records.append(record)
    payload = {
        "schema": "cam_workbench.output_package",
        "version": 1,
        "package_id": package_id,
        "state": state,
        "output_package": output_package,
        "started_utc": started_utc,
        "updated_utc": _utc_now(),
        "plates": [item.plate.number for item in items],
        "outputs": output_records,
    }
    if error:
        payload["error"] = error
    if rollback_complete is not None:
        payload["rollback_complete"] = rollback_complete
    return payload


def _publish_staged_outputs(
    outputs: Iterable[_StagedOutput],
    package_id: str,
    publish_replace: PublishReplace,
) -> None:
    """Publish all staged files, restoring every prior target on failure."""
    published: list[tuple[Path, Path | None]] = []
    active_temporary: Path | None = None
    active_backup: Path | None = None
    active_destination: Path | None = None
    try:
        for output in outputs:
            destination = output.destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".publish", dir=destination.parent,
            )
            os.close(handle)
            active_temporary = Path(temporary_name)
            shutil.copy2(output.source, active_temporary)
            # Windows requires a writable handle for FlushFileBuffers, which
            # backs Python's fsync implementation.
            with active_temporary.open("r+b") as stream:
                os.fsync(stream.fileno())

            active_destination = destination
            active_backup = None
            if destination.exists():
                active_backup = destination.with_name(
                    f".{destination.name}.{package_id}.rollback",
                )
                active_backup.unlink(missing_ok=True)
                os.replace(destination, active_backup)
            try:
                publish_replace(active_temporary, destination)
            except Exception:
                if active_backup is not None and active_backup.exists():
                    os.replace(active_backup, destination)
                    active_backup = None
                elif destination.exists():
                    destination.unlink()
                raise
            published.append((destination, active_backup))
            active_temporary = None
            active_backup = None
            active_destination = None
    except Exception:
        if active_temporary is not None:
            active_temporary.unlink(missing_ok=True)
        if active_backup is not None and active_backup.exists() and active_destination is not None:
            active_destination.unlink(missing_ok=True)
            os.replace(active_backup, active_destination)
        rollback_errors: list[str] = []
        for destination, backup in reversed(published):
            try:
                destination.unlink(missing_ok=True)
                if backup is not None and backup.exists():
                    os.replace(backup, destination)
            except OSError as rollback_error:
                rollback_errors.append(f"{destination}: {rollback_error}")
        if rollback_errors:
            raise OutputPublicationError(
                "Output publication failed and rollback needs attention: "
                + "; ".join(rollback_errors)
            )
        raise
    else:
        for _destination, backup in published:
            if backup is not None:
                backup.unlink(missing_ok=True)


def controller_output_choice(path: Path, plate_numbers: Iterable[int]) -> tuple[str, Path]:
    """Convert one controller path into its normalized base and directory."""
    numbers = tuple(int(number) for number in plate_numbers)
    if not numbers:
        raise ValueError("At least one output plate is required.")
    candidate = Path(path)
    name = candidate.name
    stem = name[:-4] if name.casefold().endswith(".cnc") else candidate.stem
    base_name = output_base_name(stem, numbers)
    cnc_output_filename(base_name, numbers[0])
    return base_name, candidate.parent


def project_origin_directory(project: CamProject, project_path: Path | None = None) -> Path | None:
    """Return an existing source/project directory suitable for an optional copy."""
    source_file = str(project.source.get("file", "")).strip()
    candidates = [Path(source_file).expanduser() if source_file else None, project_path]
    for candidate in candidates:
        if candidate is not None and candidate.parent.is_dir():
            return candidate.parent
    return None


def repository_output_paths(
    repository: Path,
    base_name: str,
    plate_numbers: Iterable[int],
) -> dict[int, Path]:
    """Build deterministic numbered controller paths in one repository."""
    numbers = tuple(int(number) for number in plate_numbers)
    if not numbers:
        raise ValueError("At least one output plate is required.")
    normalized_base = output_base_name(base_name, numbers)
    return {
        number: Path(repository) / cnc_output_filename(normalized_base, number)
        for number in numbers
    }


def prepare_plate_outputs(
    project: CamProject,
    plates: Iterable[Plate],
    output_paths: dict[int, Path],
) -> tuple[PreparedPlateOutput, ...]:
    """Construct isolated plate projects without writing controller files."""
    prepared: list[PreparedPlateOutput] = []
    for plate in plates:
        if plate.number not in output_paths:
            raise ValueError(f"No controller output path was provided for Plate {plate.number}.")
        prepared.append(PreparedPlateOutput(
            plate=plate,
            result=make_plate_project(project, plate),
            controller_path=Path(output_paths[plate.number]),
        ))
    if not prepared:
        raise ValueError("Choose at least one plate for output.")
    return tuple(prepared)


def execute_plate_outputs(
    prepared: Iterable[PreparedPlateOutput],
    output_package: str,
    *,
    copy_to_origin: bool = False,
    origin_directory: Path | None = None,
    setup_sheet_writer: SetupSheetWriter | None = None,
    shop_drawing_writer: ShopDrawingWriter | None = None,
    publish_replace: PublishReplace = os.replace,
    generated_paths_by_plate: Mapping[int, Mapping[str, GeneratedToolpath]] | None = None,
    allow_negative_xy: bool | None = None,
) -> PlateOutputExecution:
    """Stage, validate, and transactionally publish a prepared output package.

    Controller, native-project, setup-sheet, shop-drawing, source-copy, and package-manifest
    files are generated away from their production names. Publication uses
    same-directory atomic replacements and rolls back every earlier target if
    any later target cannot be replaced.
    """
    items = tuple(prepared)
    if allow_negative_xy is not None:
        # Publication policy may change after cached motion preparation. Copy
        # metadata so the posted file and packaged native job agree, without
        # mutating the session cache or regenerating toolpaths.
        updated = []
        for item in items:
            metadata = copy.deepcopy(item.result.project.metadata)
            metadata.setdefault("job_setup", {})["allow_negative_xy"] = bool(allow_negative_xy)
            project = replace(item.result.project, metadata=metadata)
            updated.append(replace(item, result=replace(item.result, project=project)))
        items = tuple(updated)
    if not items:
        raise ValueError("Choose at least one prepared plate for output.")
    if output_package not in OUTPUT_PACKAGES:
        raise ValueError(f"Unsupported output package: {output_package!r}.")
    if copy_to_origin and origin_directory is None:
        raise ValueError("An origin directory is required when source-folder copying is enabled.")
    if output_package == "Full package" and setup_sheet_writer is None:
        raise ValueError("Full package output requires a setup-sheet writer.")

    package_id = uuid4().hex
    started_utc = _utc_now()
    manifest_path = _package_manifest_path(items)
    pending_manifest_path = manifest_path.with_name(
        f".{manifest_path.stem}.{package_id}.pending.json",
    )
    staged_outputs: list[_StagedOutput] = []
    controller_paths: list[Path] = []
    project_paths: list[Path] = []
    setup_sheet_paths: list[Path] = []
    shop_drawing_paths: list[Path] = []
    origin_copy_paths: list[Path] = []
    skipped_operation_count = 0
    try:
        with tempfile.TemporaryDirectory(prefix="cam-workbench-output-") as stage_name:
            stage_root = Path(stage_name)
            for index, item in enumerate(items):
                plate_stage = stage_root / f"plate-{index + 1:02d}"
                plate_stage.mkdir(parents=True, exist_ok=True)
                requested_controller = plate_stage / item.controller_path.name
                cached_paths = None
                if generated_paths_by_plate is not None:
                    cached_paths = generated_paths_by_plate.get(item.plate.number)
                    if cached_paths is None:
                        raise ValueError(
                            f"Prepared motion is missing for Plate {item.plate.number}; regenerate Output."
                        )
                staged_controller = post_enroute_cnc(
                    item.result.project, requested_controller,
                    generated_paths=cached_paths,
                ).path
                final_controller = item.controller_path.with_suffix(".cnc")
                controller_paths.append(final_controller)
                staged_outputs.append(_StagedOutput(staged_controller, final_controller, "controller"))

                if (
                    copy_to_origin and origin_directory is not None
                    and origin_directory.resolve() != final_controller.parent.resolve()
                ):
                    origin_path = origin_directory / final_controller.name
                    origin_copy_paths.append(origin_path)
                    staged_outputs.append(_StagedOutput(staged_controller, origin_path, "origin_copy"))

                plate_base, _plate_directory = controller_output_choice(
                    final_controller, [item.plate.number],
                )
                staged_native_paths: list[Path] = []
                if output_package in {"CNC + CAM project", "Full package"}:
                    staged_native = save_plate_project(
                        item.result, plate_stage, plate_base, item.plate.number,
                    )
                    final_native = final_controller.parent / staged_native.name
                    project_paths.append(final_native)
                    staged_native_paths.append(staged_native)
                    staged_outputs.append(_StagedOutput(staged_native, final_native, "cam_project"))
                if output_package == "Full package" and setup_sheet_writer is not None:
                    staged_setup = setup_sheet_writer(
                        plate_stage,
                        plate_base,
                        [(item.plate, item.result)],
                        staged_native_paths,
                        [staged_controller],
                    )
                    if not staged_setup.is_file():
                        raise OSError(f"Setup-sheet writer did not create {staged_setup}.")
                    final_setup = final_controller.parent / staged_setup.name
                    setup_sheet_paths.append(final_setup)
                    staged_outputs.append(_StagedOutput(staged_setup, final_setup, "setup_sheet"))
                if output_package == "Full package" and shop_drawing_writer is not None:
                    staged_drawing = shop_drawing_writer(
                        plate_stage, plate_base, item.plate, item.result,
                    )
                    if not staged_drawing.is_file():
                        raise OSError(f"Shop-drawing writer did not create {staged_drawing}.")
                    final_drawing = final_controller.parent / staged_drawing.name
                    shop_drawing_paths.append(final_drawing)
                    staged_outputs.append(_StagedOutput(staged_drawing, final_drawing, "shop_drawing_pdf"))
                skipped_operation_count += len(item.result.skipped_operation_ids)

            destinations = [output.destination.resolve() for output in staged_outputs]
            if len(destinations) != len(set(destinations)):
                raise ValueError("The output package contains duplicate destination paths.")
            missing_or_empty = [
                output.source for output in staged_outputs
                if not output.source.is_file() or output.source.stat().st_size <= 0
            ]
            if missing_or_empty:
                raise OSError(
                    "Output generation produced a missing or empty deliverable: "
                    + ", ".join(str(path) for path in missing_or_empty)
                )

            complete_manifest = _manifest_payload(
                package_id, output_package, items, "complete",
                outputs=staged_outputs, started_utc=started_utc,
            )
            staged_manifest = stage_root / "output-manifest.json"
            staged_manifest.write_text(
                json.dumps(complete_manifest, indent=2) + "\n", encoding="utf-8",
            )
            staged_outputs.append(_StagedOutput(staged_manifest, manifest_path, "manifest"))
            _atomic_json_write(
                pending_manifest_path,
                _manifest_payload(
                    package_id, output_package, items, "publication_pending",
                    outputs=staged_outputs, started_utc=started_utc,
                ),
            )
            try:
                _publish_staged_outputs(staged_outputs, package_id, publish_replace)
            except Exception as exc:
                _atomic_json_write(
                    pending_manifest_path,
                    _manifest_payload(
                        package_id, output_package, items, "publication_failed",
                        outputs=staged_outputs, started_utc=started_utc,
                        error=str(exc),
                        rollback_complete=not isinstance(exc, OutputPublicationError),
                    ),
                )
                raise
    except Exception as exc:
        if not pending_manifest_path.exists():
            try:
                _atomic_json_write(
                    pending_manifest_path,
                    _manifest_payload(
                        package_id, output_package, items, "generation_failed",
                        outputs=staged_outputs, started_utc=started_utc, error=str(exc),
                    ),
                )
            except OSError:
                pass
        raise
    pending_manifest_path.unlink(missing_ok=True)

    return PlateOutputExecution(
        controller_paths=tuple(controller_paths),
        project_paths=tuple(project_paths),
        setup_sheet_paths=tuple(setup_sheet_paths),
        shop_drawing_paths=tuple(shop_drawing_paths),
        origin_copy_paths=tuple(origin_copy_paths),
        skipped_operation_count=skipped_operation_count,
        manifest_path=manifest_path,
    )
