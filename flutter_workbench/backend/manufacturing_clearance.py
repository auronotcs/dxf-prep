"""Read-only knife diagnostics from the exact core generator and its motion blocks."""
import copy,json
from cam_core.knife_corner_loops import KnifeClearanceError
from cam_core.drag_knife import generate_drag_knife

def findings(project, paths, progress=lambda _:None):
    rows=[];seen=set()
    for op in project.operations:
        if not op.enabled or op.kind!='drag_knife':continue
        progress('Collecting knife clearance locations')
        generated=paths.get(op.id)
        if generated is None:
            try:generate_drag_knife(project,op)
            except KnifeClearanceError as exc:
                if exc.finding:rows.append(dict(copy.deepcopy(exc.finding),accepted=False,operation_id=op.id,operation_name=op.name,units=project.units))
            except ValueError:pass # The authoritative preflight already reports non-geometric failures.
        else:
            for block in generated.blocks:
                for finding in block.metadata.get('edge_swivels',[]):
                    key=(op.id,json.dumps(finding,sort_keys=True))
                    if key in seen:continue
                    seen.add(key)
                    rows.append(dict(copy.deepcopy(finding),accepted=True,operation_id=op.id,operation_name=op.name,units=project.units))
    return rows
