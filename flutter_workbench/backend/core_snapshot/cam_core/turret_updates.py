"""Select preview work after a physical tool-rack edit."""
from dataclasses import replace


def changed_stations(before, after):
    old = {tool.station: tool.to_dict() for tool in before}
    new = {tool.station: tool.to_dict() for tool in after}
    return {station for station in old.keys() | new.keys() if old.get(station) != new.get(station)}


def affected_operations(operations, stations):
    affected = {op.id for op in operations if op.tool_station in stations}
    # Source-linked rest machining and reviewed release stages must follow their
    # changed prerequisites, even if the dependent uses another tool station.
    while True:
        added = {op.id for op in operations if (
            getattr(op, 'source_operation_id', '') in affected
            or set(op.depends_on_ids) & affected)} - affected
        if not added:
            return affected
        affected.update(added)


def refresh_unaffected_automatic_stamps(before, after, previews, affected):
    """Rebase a derived whole-job stamp only after a proven tool-only change.

No reviewed choice/fingerprint is re-signed. Automatic retention depends on its
own cutter and geometry; a different station cannot change those tab choices.
Invalid/older stamps are not blessed as current.
"""
    from .automatic_tab_cleanup import signature
    by_id = {op.id: op for op in before.operations}
    for op in after.operations:
        if op.id in affected or getattr(op, 'tab_release_plan', {}).get('mode') != 'automatic_opposed':
            continue
        path = previews.get(op.id)
        if not path or not path.blocks or op.id not in by_id:
            continue
        stamp = path.blocks[0].metadata.get('automatic_tab_cleanup')
        if not stamp or stamp.get('signature') != signature(before, by_id[op.id]):
            continue
        block = path.blocks[0]
        path.blocks[0] = replace(block, metadata=dict(block.metadata,
            automatic_tab_cleanup=dict(stamp, signature=signature(after, op))))
