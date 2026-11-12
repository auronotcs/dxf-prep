from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
import time

from cam_core import CamProject, Plate
from cam_editor_app import CamWorkbench, PlateOutputDialog


snapshot = Path(r"C:\Users\auron\Documents\Shop Jobs\Jobs\WHHS-boundary-inspection-snapshot.camjob.json")
project = CamProject.load(snapshot)
plates = [Plate.from_dict(payload) for payload in project.metadata.get("plates", [])]
setup = dict(project.metadata.get("job_setup", {}))

app = object.__new__(CamWorkbench)
app.project = project
app.settings = {"machine_spindle_min_rpm": 8000, "machine_spindle_max_rpm": 24000}
app.guided_production_var = SimpleNamespace(get=lambda: False)
app.document_id = "whhs-proof"
app.document_revision = 1
app._output_generation_cache = OrderedDict()
app._output_generation_cache_limit = 4
app._job_setup = lambda: setup
app._current_plates = lambda: plates

started = time.perf_counter()
entry, first_reused = CamWorkbench._prepared_output_generation(app, plates, setup)
first_preflight = time.perf_counter() - started
started = time.perf_counter()
reopened, second_reused = CamWorkbench._prepared_output_generation(app, plates, setup)
reopen_preflight = time.perf_counter() - started

dialog = object.__new__(PlateOutputDialog)
dialog.parent_app = app
dialog.output_cache = entry
dialog._plate_cycle_cache = {}
started = time.perf_counter()
first_cycle = PlateOutputDialog._estimate_plate_cycle(dialog, plates[0])
first_plate = time.perf_counter() - started

reopened_dialog = object.__new__(PlateOutputDialog)
reopened_dialog.parent_app = app
reopened_dialog.output_cache = reopened
reopened_dialog._plate_cycle_cache = {}
started = time.perf_counter()
second_cycle = PlateOutputDialog._estimate_plate_cycle(reopened_dialog, plates[0])
reopen_plate = time.perf_counter() - started

original_key = entry.key
app.document_revision += 1
invalidated_key = CamWorkbench._output_generation_cache_key(app, plates, setup)
app.document_id = "different-job"
other_job_key = CamWorkbench._output_generation_cache_key(app, plates, setup)

print(f"first_preflight_seconds={first_preflight:.6f}")
print(f"reopen_preflight_seconds={reopen_preflight:.6f}")
print(f"first_plate_seconds={first_plate:.6f}")
print(f"reopen_plate_seconds={reopen_plate:.6f}")
print(f"preflight_reused={second_reused and reopened is entry and not first_reused}")
print(f"cycle_identical={first_cycle == second_cycle}")
print(f"plate_artifact_identity={entry.plate_artifacts[plates[0].number] is reopened.plate_artifacts[plates[0].number]}")
print(f"revision_invalidates={invalidated_key != original_key}")
print(f"document_invalidates={other_job_key != invalidated_key}")

