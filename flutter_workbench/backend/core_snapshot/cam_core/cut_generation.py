"""Read-only cut generation used by the modal worker; no publication or Tk."""
from .stock import generate_operation_toolpath
from .toolpath_background import generate_toolpath_preview_bundle


def generate_cut_bundle(project, operations, *, preview=False, report=lambda text: None):
    if preview:
        return generate_toolpath_preview_bundle(project, operation_ids={op.id for op in operations},
            progress_callback=lambda done,total,name:
            report(f"Generating preview {min(done+1,total)}/{total}: {name} ({done} complete)"))
    paths = {}
    for index,operation in enumerate(operations,1):
        report(f"Generating operation {index}/{len(operations)}: {operation.name}")
        # Create/edit historically stores raw paths. Optimization still belongs
        # to explicit preview/output; moving work off Tk must not change motion.
        paths[operation.id] = generate_operation_toolpath(project,operation,optimize=False)
    return paths


def cut_generation_worker(project,operations,preview,result_queue):
    try:
        result=generate_cut_bundle(project,operations,preview=preview,
                                   report=lambda text:result_queue.put(('progress',text)))
        result_queue.put(('done',result))
    except Exception as exc:
        result_queue.put(('failed',str(exc)))
