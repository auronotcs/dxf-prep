from pathlib import Path
import time

import cam_editor_app as desktop
from cam_core import CamProject


snapshot = Path(r"C:\Users\auron\Documents\Boat stuff\WHHS-boundary-inspection-snapshot.camjob.json")
desktop.CamWorkbench._offer_recovery = lambda self: None
app = desktop.CamWorkbench()
app.withdraw()
app.project = CamProject.load(snapshot)
app.project_path = None
app.document_id = "output-ui-proof"
app.document_revision = 1
app._restore_sheet_metadata()
app._sync_job_setup_controls()

measurements = {}


def children_of_kind(parent, kind):
    return [child for child in parent.winfo_children() if isinstance(child, kind)]


def poll_first_dialog():
    dialogs = children_of_kind(app, desktop.PlateOutputDialog)
    if not dialogs:
        app.after(10, poll_first_dialog)
        return
    dialog = dialogs[-1]
    measurements["first_open"] = time.perf_counter() - measurements["first_started"]
    dialog._open_problems()
    dialog.after(10, lambda: inspect_problem(dialog))


def inspect_problem(dialog):
    problems = children_of_kind(dialog, desktop.OutputProblemsDialog)
    if not problems:
        dialog.after(10, lambda: inspect_problem(dialog))
        return
    problem_dialog = problems[-1]
    target = next(
        (index for index, problem in enumerate(problem_dialog.problems) if problem.get("entity_ids")),
        0,
    )
    item = str(target)
    problem_dialog.tree.selection_set(item)
    problem_dialog.tree.focus(item)
    problem_dialog._update_details()
    measurements["inspected_index"] = target
    if problem_dialog.problems[target].get("entity_ids"):
        problem_dialog._show_selected()
    else:
        problem_dialog.destroy()
        dialog.destroy()
    app.after(100, begin_second)


def begin_first():
    measurements["first_started"] = time.perf_counter()
    app.after(10, poll_first_dialog)
    app.output_plates()


def begin_second():
    measurements["second_started"] = time.perf_counter()
    app.after(10, poll_second_dialog)
    app.output_plates()


def poll_second_dialog():
    dialogs = children_of_kind(app, desktop.PlateOutputDialog)
    if not dialogs:
        app.after(10, poll_second_dialog)
        return
    dialog = dialogs[-1]
    measurements["second_open"] = time.perf_counter() - measurements["second_started"]
    prepared_rows = [
        dialog.summary.item(item, "values")
        for item in dialog.summary.get_children()
        if dialog.summary.item(item, "values")[0] == "Prepared output"
    ]
    measurements["prepared_row"] = prepared_rows[0][1] if prepared_rows else "missing"
    measurements["selected_problem_index"] = dialog.output_cache.selected_problem_index
    measurements["plate_artifacts"] = len(dialog.output_cache.plate_artifacts)
    dialog.destroy()
    app.after(50, finish)


def finish():
    if getattr(app, "autosave_job", None) is not None:
        app.after_cancel(app.autosave_job)
    print(f"first_dialog_seconds={measurements['first_open']:.6f}")
    print(f"reopen_dialog_seconds={measurements['second_open']:.6f}")
    print(f"inspected_index={measurements['inspected_index']}")
    print(f"selected_problem_restored={measurements['selected_problem_index'] == measurements['inspected_index']}")
    print(f"prepared_row={measurements['prepared_row']}")
    print(f"plate_artifacts={measurements['plate_artifacts']}")
    app.destroy()


app.after(50, begin_first)
app.mainloop()

