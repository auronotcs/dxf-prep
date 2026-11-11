"""Non-mutating, locatable warnings for tab-free compensation fragments."""
from .geometry_diagnostics import GeometryDiagnostic
from .model import Point


def tab_free_fragment_diagnostics(path, units):
    issues = []
    seen = set()
    for block in path.blocks:
        metadata = block.metadata
        if metadata.get('tab_cleanup_skipped') != 'tab_free_tiny_fragment':
            continue
        entity_id = str(metadata.get('entity_id', ''))
        index = int(metadata.get('path_index', 0))
        key = (entity_id, index)
        if key in seen:
            continue
        seen.add(key)
        x, y = metadata['fragment_focus']
        length = float(metadata['fragment_perimeter'])
        issues.append(GeometryDiagnostic(
            'warning', 'toolpath.tab_free_fragment',
            f'Offset fragment {index + 1} has a {length:g} {units} perimeter and no raised tabs. '
            'Only its empty tab-cleanup pass was skipped; its profile cut remains. '
            'Main-contour tabs are unchanged. This may be a narrow feature or dirty DXF geometry. '
            'Review the marked source area before cutting; repair the contour or delete only '
            'separately identified junk after review. This generated loop is not a separate source '
            'object: deleting the selected source would delete the parent artwork.',
            Point(float(x), float(y)), (entity_id,),
        ))
    return issues
