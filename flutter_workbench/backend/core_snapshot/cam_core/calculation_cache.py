"""Bounded, session-only exact-input reuse. Never a substitute for preflight.

Values are private pickles created in this process tree, never loaded from jobs
or disk. Serialization gives every caller its own copy of mutable motion data.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import pickle
import threading
from pathlib import Path

SCHEMA = 3
MAX_BYTES = 192 * 1024 * 1024
MAX_ENTRIES = 1024
_active = ContextVar('cam_calculation_cache', default=None)


class CalculationCache:
    def __init__(self, entries=()):
        from .build_info import load_build_info
        self.build_identity = load_build_info(Path(__file__).resolve().parents[1]).build_id
        self.entries = OrderedDict(entries)
        self.size = sum(len(value) for value in self.entries.values())
        self.hits = self.misses = 0
        self.trim()

    def trim(self):
        while self.entries and (self.size > MAX_BYTES or len(self.entries) > MAX_ENTRIES):
            _, value = self.entries.popitem(last=False)
            self.size -= len(value)

    def compute(self, key, calculate):
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return pickle.loads(self.entries[key])
        self.misses += 1
        result = calculate()  # Failed/cancelled calculations are never cached.
        value = pickle.dumps(result, protocol=5)
        if len(value) <= MAX_BYTES:
            self.entries[key] = value
            self.size += len(value)
            self.trim()
        return result


@contextmanager
def calculation_session(cache):
    token = _active.set(cache)
    try:
        yield cache
    finally:
        _active.reset(token)


def reuse(kind, inputs, calculate, *, owner=None):
    cache = _active.get()
    if cache is None:
        return calculate()
    # Exact floats; never round geometry or infer translation equivalence.
    try:
        key = hashlib.sha256(json.dumps((SCHEMA, cache.build_identity, kind, inputs), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if owner is not None:
            # Namespaced ownership permits saving only this job's cleanout
            # calculations, never another open document's cache or audits.
            key = f'{kind}:{hashlib.sha256(owner.encode()).hexdigest()}:{key}'
    except (ValueError, TypeError):
        # Invalid inputs still go through the ordinary validator, not a cache
        # serialization error which could hide an actionable machining issue.
        return calculate()
    return cache.compute(key, calculate)


def reuse_fill_region(project, operation, tool, stepover, calculate):
    if _active.get() is None:
        return calculate()
    ids = set(operation.entity_ids) | set(operation.island_entity_ids)
    # Audited dependency boundary of _generate_single_island_fill: selected
    # contours, resolved operation/tool, stepover, stock thickness and cycle
    # estimate settings used by closed-wall depth routing. No neighboring geometry or other operations
    # are read here. Nesting/classification is always rerun before this call.
    inputs = {
        'units': project.units, 'operation': operation.to_dict(),
        'tool': tool.to_dict(), 'stepover': stepover,
        'stock_thickness': project.metadata.get('job_setup', {}).get('material_thickness', 0),
        'cycle_optimization': project.metadata.get('cycle_optimization', {}),
        'entities': [entity.to_dict() for entity in project.entities if entity.id in ids],
    }
    return reuse('raw-fill-region', inputs, calculate, owner=operation.id)


def reuse_cleanout_path(project, operation, optimize, calculate):
    """Reuse complete fill/rest motion, never neighboring-contour checks.

    Audited for the two exact types below: their planners only read selected
    boundaries/islands and source-fill dependencies. Optimization also reads
    keepouts and operation/release settings. Preserve ALL non-entity project
    data in the key, including tools, units, metadata, keepouts and operations;
    exclude only geometry outside this operation's dependency closure.
    Profiles are intentionally not cached here: entry placement reads neighbors.
    """
    if _active.get() is None:
        return calculate()
    from .model import IslandFillOperation, RestMachiningOperation
    if type(operation) not in (IslandFillOperation, RestMachiningOperation):
        return calculate()
    by_id = {op.id: op for op in project.operations}
    by_id[operation.id] = operation  # Pending parameter-dialog edits take precedence.
    pending, seen, ids = [operation], set(), set()
    while pending:
        dependency = pending.pop()
        if dependency.id in seen:
            continue
        seen.add(dependency.id)
        ids.update(dependency.entity_ids)
        ids.update(getattr(dependency, 'island_entity_ids', ()))
        references = [*getattr(dependency, 'depends_on_ids', ())]
        source = getattr(dependency, 'source_operation_id', '')
        if source:
            references.append(source)
        pending.extend(by_id[i] for i in references if i in by_id and i not in seen)
    inputs = project.to_dict()
    inputs['entities'] = [e.to_dict() for e in project.entities if e.id in ids]
    return reuse('complete-cleanout-path', {
        'project': inputs, 'operation': operation.to_dict(), 'optimize': optimize,
    }, calculate, owner=operation.id)


def reuse_rest_region_loops(outer, islands, previous_radius, new_radius, cutter_radius,
                            stepover, ignore_stock, calculate, *, owner=None, source_context=None):
    """Only the exact geometric rest-loop calculation; feeds/depths stay outside."""
    if _active.get() is None:
        return calculate()
    return reuse('rest-region-loops', {
        'outer': [(p.x, p.y) for p in outer],
        'islands': [[(p.x, p.y) for p in island] for island in islands],
        'previous_radius': previous_radius, 'new_radius': new_radius,
        'cutter_radius': cutter_radius, 'stepover': stepover, 'ignore_stock': ignore_stock,
        'source_context': source_context,
    }, calculate, owner=owner)


def reuse_motion_audit(project, operation, generated, calculate):
    if _active.get() is None:
        return calculate()
    from .depth_intent import resolve_fill_depth
    try:
        operation = resolve_fill_depth(project, operation)
    except (TypeError, ValueError):
        return calculate()
    ids = set(operation.entity_ids) | set(getattr(operation, 'island_entity_ids', []))
    tool = project.tool_library.by_station(operation.tool_station)
    # Independent audit inputs, not a planner's success flag. The complete
    # finished motion stream and all block metadata are hashed. Cross-object
    # collision/fixture checks are separate and are NEVER cached here.
    inputs = {'units': project.units, 'operation': operation.to_dict(),
              'tool': tool.to_dict() if tool is not None else None,
              'stock_thickness': project.metadata.get('job_setup', {}).get('material_thickness', 0),
              'entities': [e.to_dict() for e in project.entities if e.id in ids],
              'motion': hashlib.sha256(pickle.dumps(generated, protocol=5)).hexdigest()}
    return reuse('independent-motion-audit', inputs, calculate)


def cached_worker(worker, args, entries, result_queue):
    """Run an existing worker protocol, handing back cache only on completion."""
    cache = CalculationCache(entries)
    class QueueProxy:
        def put(self, item):
            if item[0] == 'done':
                result_queue.put(('calculation_cache', list(cache.entries.items())))
            result_queue.put(item)
    with calculation_session(cache):
        worker(*args, QueueProxy())


def queued_calculation_worker(request_queue, result_queue):
    # Keep large project/cache payloads out of Process.start's synchronous
    # Windows spawn pipe. Queue's feeder transfers them without freezing Tk.
    worker, args, entries = request_queue.get()
    cached_worker(worker, args, entries, result_queue)


def close_request_queue(request_queue):
    """Cancel an in-flight Windows pipe write without racing its handle close.

Queue.close only posts a sentinel after the pending payload. The pinned Python
PipeConnection exposes its pending overlapped write; cancel that I/O, then let
the feeder unwind before closing handles. Never block Tk waiting for a sender.
"""
    request_queue.cancel_join_thread()
    request_queue.close()
    feeder = request_queue._thread
    def finish():
        if feeder is not None:
            while feeder.is_alive():
                pending = getattr(request_queue._writer, '_send_ov', None)
                if pending is not None:
                    try:
                        pending.cancel()
                    except OSError:
                        pass  # The write may have completed concurrently.
                feeder.join(.02)
        request_queue._writer.close()
        request_queue._reader.close()
    threading.Thread(target=finish, name='cam-request-cleanup', daemon=True).start()
