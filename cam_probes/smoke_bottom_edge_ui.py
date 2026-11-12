from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(r"C:\Users\auron\Documents\CAM Workbench")
sys.path.insert(0, str(REPO))

from cam_core import (
    Point,
    Rectangle,
    RouterOffsetOperation,
    calculate_rail_countersink,
    load_bottom_edge_mounting_plans,
)
import cam_editor_app as app_module
from cam_editor_app import CamWorkbench


def main() -> None:
    app_module.AUTOSAVE_PATH = Path(r"C:\Users\auron\Documents\Boat stuff\bottom-edge-ui-smoke-recovery.json")
    app_module.messagebox.askyesnocancel = lambda *_args, **_kwargs: False
    app_module.messagebox.askyesno = lambda *_args, **_kwargs: False
    app_module.messagebox.showinfo = lambda title, message, **_kwargs: (_ for _ in ()).throw(AssertionError(f"{title}: {message}"))
    app_module.messagebox.showerror = lambda title, message, **_kwargs: (_ for _ in ()).throw(AssertionError(f"{title}: {message}"))
    app_module.messagebox.showwarning = lambda title, message, **_kwargs: (_ for _ in ()).throw(AssertionError(f"{title}: {message}"))
    app = CamWorkbench()
    app.withdraw()
    app._save_settings = lambda: None
    try:
        outer = Rectangle(
            corner_a=Point(10, 4), corner_b=Point(20, 14),
            layer="LETTERS", metadata={"mounting_label": "A"},
        )
        counter = Rectangle(corner_a=Point(13, 7), corner_b=Point(17, 11), layer="LETTERS")
        app.project.entities = [outer, counter]
        app.selected_ids = {outer.id, counter.id}
        app._switch_workspace("mounting")
        app.select_mounting_workflow("Bottom-Edge Rail")
        app.edge_rail_length_var.set("24")
        app.edge_rail_width_var.set("2")
        app.edge_rail_clearance_var.set("0.281")
        app.edge_rail_row_offset_var.set("0.4")
        app.edge_rail_centered_var.set(True)
        app._toggle_edge_rail_centering()
        centered_settings = app._bottom_edge_jig_settings()
        if centered_settings.rail_length != 24 or centered_settings.rail_width != 2:
            raise AssertionError("Editable rail dimensions did not reach the jig settings")
        if centered_settings.rail_hole_row_offset != 0 or str(app.edge_rail_row_offset_entry.cget("state")) != "disabled":
            raise AssertionError("Centered rail row did not override and disable the offset")
        app.edge_rail_centered_var.set(False)
        app.edge_rail_origin_x_var.set("-2")
        app.edge_rail_row_offset_var.set("0.25")
        app._toggle_edge_rail_centering()
        offset_settings = app._bottom_edge_jig_settings()
        if offset_settings.rail_origin_x != -2 or offset_settings.rail_hole_row_offset != 0.25:
            raise AssertionError("Manual rail origin/row offset did not reach the jig settings")
        if str(app.edge_rail_row_offset_entry.cget("state")) != "normal":
            raise AssertionError("Manual rail row offset did not enable")
        app.generate_mounting_preview()
        if app.edge_mounting_plan is None or len(app.edge_mounting_plan.parts) != 1:
            raise AssertionError("Bottom-edge review did not discover the selected letter")
        values = tuple(app.edge_part_combo.cget("values"))
        if len(values) != 1:
            raise AssertionError("Letter picker did not expose one repeat-safe part")
        app.edge_part_var.set(values[0])
        app.edge_along_var.set("2")
        app.add_mounting_feature()
        if len(app.edge_mounting_plan.holes) != 1:
            raise AssertionError("Bottom-edge Add Hole did not update the review")
        app.apply_mounting_preview()
        axes = [entity for entity in app.project.entities if entity.metadata.get("bottom_edge_mounting_generated")]
        if len(axes) != 1 or axes[0].metadata.get("machinable") is not False:
            raise AssertionError("Applied axis is missing or machinable")
        if app.project.metadata["layer_settings"]["BOTTOM_EDGE_DRILL_AXES"]["locked"]:
            raise AssertionError("Registered axis layer is locked and would not move with its letter")
        plans = load_bottom_edge_mounting_plans(app.project)
        if len(plans) != 1 or plans[0].holes[0].hole_id != app.edge_mounting_plan.holes[0].hole_id:
            raise AssertionError("Applied plan did not retain its durable hole ID")
        app.open_bottom_edge_rail_routing_job()
        if not app.project.metadata.get("bottom_edge_rail_routing_job"):
            raise AssertionError("Prepare Machining did not create a separate rail routing job")
        if app.workspace_var.get() != "manufacturing":
            raise AssertionError("Rail routing job did not open in Manufacturing")
        if len(app.project.metadata.get("plates", ())) != 1:
            raise AssertionError("Rail routing job did not define its physical rail plate")
        selected = [entity for entity in app.project.entities if entity.id in app.selected_ids]
        if not selected or any(entity.layer != "RAIL_CLEARANCE_HOLES" for entity in selected):
            raise AssertionError("Rail routing job did not select linked clearance holes first")
        profile = [entity for entity in app.project.entities if entity.layer == "RAIL_CUT_PROFILE"]
        if len(profile) != 1 or not profile[0].metadata.get("machinable"):
            raise AssertionError("Rail routing job did not expose one machinable outside profile")
        cutter = next(
            tool for tool in app.project.tool_library.tools
            if abs(tool.diameter - 0.25) < 1e-8
            and tool.feed_rate is not None and tool.feed_rate > 0
            and tool.plunge_rate is not None and tool.plunge_rate > 0
            and tool.spindle_rpm is not None
            and tool.spindle_mode != "disabled"
        )
        countersink_plan = calculate_rail_countersink(0.281, 0.49, 82.0, 0.010, 0.25, 0.25)
        actual_dialog = app_module.RailCountersinkDialog(
            app, cutter.station, 0.281, 0.25, 0.5,
        )
        actual_dialog.update_idletasks()
        if "Calculated depth" not in actual_dialog.preview_var.get():
            raise AssertionError("Rail countersink dialog did not present its calculated path preview")
        if actual_dialog.variables["included_angle_degrees"].get() != "82":
            raise AssertionError("Rail countersink dialog did not expose the editable 82-degree shop assumption")
        actual_dialog.destroy()

        class FakeCountersinkDialog:
            def __init__(self, *_args, **_kwargs):
                self.result = {
                    "station": cutter.station,
                    "plan": countersink_plan,
                    "safe_z": 0.5,
                    "feed_rate": cutter.feed_rate,
                    "plunge_rate": cutter.plunge_rate,
                    "spindle_rpm": cutter.spindle_rpm,
                    "cutter_geometry_verified": True,
                }

        app_module.RailCountersinkDialog = FakeCountersinkDialog
        app.wait_window = lambda _dialog: None
        app.add_rail_countersink()
        countersinks = [
            operation for operation in app.project.operations
            if isinstance(operation, RouterOffsetOperation)
            and operation.recipe_metadata.get("rail_countersink")
        ]
        if len(countersinks) != 1:
            raise AssertionError("Rail countersink did not create exactly one operation for the active rail")
        if abs(countersinks[0].target_depth - countersink_plan.target_depth) > 1e-8:
            raise AssertionError("Rail countersink did not retain the calculated partial depth")
        paths = [entity for entity in app.project.entities if entity.metadata.get("rail_countersink_path")]
        if len(paths) != len(selected):
            raise AssertionError("Rail countersink did not create one cutter-center orbit per clearance hole")
        app.add_rail_countersink()
        countersinks = [
            operation for operation in app.project.operations
            if isinstance(operation, RouterOffsetOperation)
            and operation.recipe_metadata.get("rail_countersink")
        ]
        paths = [entity for entity in app.project.entities if entity.metadata.get("rail_countersink_path")]
        if len(countersinks) != 1 or len(paths) != len(selected):
            raise AssertionError("Updating the rail countersink duplicated its operation or path geometry")
        print("bottom-edge-ui-smoke=PASS")
    finally:
        app.dirty = False
        app.destroy()


if __name__ == "__main__":
    main()
