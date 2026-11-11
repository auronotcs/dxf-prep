"""Isolated, read-only output preparation. Never publishes controller files."""
from time import perf_counter

from .preflight import run_preflight, has_blocking_errors
from .plates import make_plate_project
from .stock import generate_operation_toolpath
from .optimization import estimate_cycle_metrics
from .workflow import ordered_operations


def prepare_output_bundle(project, plates, options, report=lambda stage: None, issue_callback=None):
    plates = tuple(plates)
    generated = {}
    started = perf_counter()
    # Non-mutating settings pass catches missing tools, depth/stock and source
    # reference problems before the first expensive region is calculated.
    if issue_callback is not None:
        issue_callback(run_preflight(project, plates, settings_only=True, **options))
    issues = run_preflight(project, plates, generated_paths_out=generated,
                           progress_callback=report, issue_callback=issue_callback, **options)
    preflight_seconds = perf_counter() - started
    artifacts, plate_errors = {}, {}
    if has_blocking_errors(issues):
        report("Checks complete — review blocking problems before plate preparation")
        return issues, generated, preflight_seconds, artifacts, plate_errors
    for index, plate in enumerate(plates, 1):
        report(f"Preparing plate {index}/{len(plates)}: local motion and cycle estimate")
        try:
            result = make_plate_project(project, plate)
            paths = {}
            operation_paths = []
            started = perf_counter()
            operations = ordered_operations([op for op in result.project.operations if op.enabled])
            for op_index, operation in enumerate(operations, 1):
                report(f"Plate {index}/{len(plates)}, operation {op_index}/{len(operations)}: {operation.name}")
                path = generate_operation_toolpath(result.project, operation)
                paths[operation.id] = path
                operation_paths.append((operation, path))
            seconds = estimate_cycle_metrics(result.project, operation_paths).estimated_seconds
            artifacts[plate.number] = (result, paths, seconds, perf_counter() - started)
        except (KeyError, TypeError, ValueError) as exc:
            plate_errors[plate.number] = str(exc)
    report("Preparation complete — returning verified results")
    return issues, generated, preflight_seconds, artifacts, plate_errors


def output_preparation_worker(project, plates, options, result_queue):
    try:
        result = prepare_output_bundle(project, plates, options,
                                       lambda stage: result_queue.put(('progress', stage)),
                                       lambda issues: result_queue.put(('issues', issues)))
        result_queue.put(('done', result))
    except Exception as exc:
        result_queue.put(('failed', str(exc)))
