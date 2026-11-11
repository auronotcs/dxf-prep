from __future__ import annotations

"""Deterministic, anonymized performance fixtures for engineering decisions."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import platform
from pathlib import Path
import statistics
import tempfile
import time
import tracemalloc
from typing import Callable

from .layout import drawing_bounds, entity_bounds
from .model import CamProject, Circle, Point, Polyline, Rectangle, RouterOffsetOperation, OffsetSide, Tool, ToolLibrary
from .nesting import nest_entities_to_plates
from .plates import Plate
from .postprocess import post_enroute_cnc
from .print_job import ArtworkAsset, PrintJobData, PrintPart
from .toolpath import generate_closed_router_offset


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    median_ms: float
    p95_ms: float
    peak_mib: float
    iterations: int
    memory_iterations: int


def _measure(
    name: str, action: Callable[[], object], iterations: int, *, memory_iterations: int = 3,
) -> BenchmarkResult:
    """Measure elapsed time without tracemalloc, then memory in separate runs.

    Python's allocation tracing materially changes geometry-heavy timings. The
    two measurements are deliberately not combined or presented as though they
    came from the same execution.
    """
    if iterations < 3 or memory_iterations < 1:
        raise ValueError("Elapsed timing needs at least 3 iterations and memory needs at least 1.")
    action()
    durations: list[float] = []
    for _ in range(iterations):
        gc.collect()
        started = time.perf_counter()
        action()
        durations.append((time.perf_counter() - started) * 1000.0)
    peaks: list[int] = []
    for _ in range(memory_iterations):
        gc.collect()
        tracemalloc.start()
        action()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
    ordered = sorted(durations)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return BenchmarkResult(
        name=name,
        median_ms=statistics.median(durations),
        p95_ms=ordered[p95_index],
        peak_mib=max(peaks) / (1024.0 * 1024.0),
        iterations=iterations,
        memory_iterations=memory_iterations,
    )


def representative_entities(count: int = 12_000) -> list:
    entities = []
    for index in range(count):
        column = index % 200
        row = index // 200
        x = column * 0.62
        y = row * 0.72
        choice = index % 3
        if choice == 0:
            entities.append(Rectangle(id=f"rect-{index}", corner_a=Point(x, y), corner_b=Point(x + 0.5, y + 0.6)))
        elif choice == 1:
            entities.append(Circle(id=f"circle-{index}", center=Point(x + 0.25, y + 0.3), radius=0.24))
        else:
            entities.append(Polyline(
                id=f"poly-{index}", closed=True,
                points=[Point(x, y), Point(x + 0.5, y), Point(x + 0.44, y + 0.6), Point(x + 0.08, y + 0.55)],
            ))
    return entities


def _legacy_drawing_bounds(entities: list) -> tuple[float, float, float, float] | None:
    boxes = [box for entity in entities if (box := entity_bounds(entity)) is not None]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def representative_toolpath_project(count: int = 80) -> tuple[CamProject, RouterOffsetOperation]:
    entities = []
    for index in range(count):
        column = index % 20
        row = index // 20
        x = column * 3.0
        y = row * 3.0
        entities.append(Rectangle(
            id=f"profile-{index}", layer="CUT",
            corner_a=Point(x, y), corner_b=Point(x + 2.0, y + 2.0),
        ))
    operation = RouterOffsetOperation(
        "Representative outside profiles", 1, [entity.id for entity in entities],
        0.13, 0.30, side=OffsetSide.OUTSIDE, stepdown=0.07,
        entry_mode="plunge", lead_in=0.0, lead_out=0.0,
    )
    project = CamProject(
        name="anonymized-signage-toolpath",
        entities=entities,
        operations=[operation],
        tool_library=ToolLibrary([Tool(1, "Representative quarter-inch cutter", 0.25, feed_rate=205 / 60, plunge_rate=75 / 60, spindle_rpm=18_000)]),
    )
    return project, operation


def representative_nesting_entities(count: int = 72) -> list[Rectangle]:
    return [
        Rectangle(
            id=f"nest-{index}",
            corner_a=Point(0.0, 0.0),
            corner_b=Point(1.5 + (index % 7) * 0.2, 2.0 + (index % 5) * 0.25),
        )
        for index in range(count)
    ]


def representative_persistence_project(count: int = 1_500) -> CamProject:
    project = CamProject(name="anonymized-portable-job", entities=representative_entities(count))
    artwork = ArtworkAsset.from_bytes("source-artwork.bin", bytes(range(256)) * 1024)
    project.print_job = PrintJobData(
        assets=[artwork],
        parts=[PrintPart("Representative printed face", artwork.asset_id, 48.0, 96.0, quantity=2)],
    )
    return project


def performance_equivalence_fingerprints() -> dict[str, str]:
    """Stable geometry/controller signatures used around performance edits."""
    entities = representative_entities()
    project, operation = representative_toolpath_project()
    toolpath = generate_closed_router_offset(project, operation)
    toolpath_payload = [
        {
            "kind": motion.kind.value,
            "end": [motion.end.x, motion.end.y, motion.end.z],
            "feed": motion.feed,
            "center": [motion.center.x, motion.center.y] if motion.center is not None else None,
            "dwell": motion.dwell_seconds,
        }
        for motion in toolpath.motions
    ]
    nesting = nest_entities_to_plates(
        representative_nesting_entities(), [Plate(1, 0.0, 0.0, 120.0, 60.0)],
        spacing=0.25, allow_rotation=True, allow_new_plates=True,
    )
    nesting_payload = {
        "entities": [entity.to_dict() for entity in nesting.entities],
        "assignments": nesting.assignments,
        "rotations": nesting.rotations,
    }
    with tempfile.TemporaryDirectory() as folder:
        controller_path = Path(folder) / "representative.cnc"
        posted = post_enroute_cnc(project, controller_path, require_positive_xy=False)
        controller_bytes = posted.path.read_bytes()

    def digest(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    return {
        "drawing_bounds_sha256": digest(drawing_bounds(entities)),
        "toolpath_sha256": digest(toolpath_payload),
        "nesting_sha256": digest(nesting_payload),
        "controller_sha256": hashlib.sha256(controller_bytes).hexdigest(),
    }


def run_performance_baseline(iterations: int = 7) -> dict:
    entities = representative_entities()
    toolpath_project, operation = representative_toolpath_project()
    nesting_entities = representative_nesting_entities()
    persistence_project = representative_persistence_project()
    with tempfile.TemporaryDirectory() as folder:
        project_path = Path(folder) / "representative.camjob.json"

        def save_open() -> object:
            persistence_project.save(project_path)
            return CamProject.load(project_path)

        results = [
            _measure("drawing bounds — retained legacy algorithm", lambda: _legacy_drawing_bounds(entities), iterations),
            _measure("drawing bounds — current algorithm", lambda: drawing_bounds(entities), iterations),
            _measure("80-profile toolpath generation", lambda: generate_closed_router_offset(toolpath_project, operation), iterations),
            _measure(
                "72-part rectangular nesting",
                lambda: nest_entities_to_plates(
                    nesting_entities, [Plate(1, 0.0, 0.0, 120.0, 60.0)],
                    spacing=0.25, allow_rotation=True, allow_new_plates=True,
                ),
                iterations,
            ),
            _measure("1,500-object project save + reopen (256 KiB artwork)", save_open, iterations),
        ]
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "measurement": {
            "warmup_iterations": 1,
            "elapsed_instrumentation": "time.perf_counter; tracemalloc disabled",
            "memory_instrumentation": "three separate tracemalloc runs; maximum peak reported",
            "percentile": "nearest-rank p95",
        },
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unreported",
            "python": platform.python_version(),
        },
        "fixtures": {
            "bounds_objects": len(entities),
            "profile_contours": len(operation.entity_ids),
            "nest_parts": len(nesting_entities),
            "persistence_objects": len(persistence_project.entities),
            "embedded_artwork_bytes": persistence_project.print_job.assets[0].byte_length,
        },
        "equivalence_fingerprints": performance_equivalence_fingerprints(),
        "results": [asdict(result) for result in results],
    }
