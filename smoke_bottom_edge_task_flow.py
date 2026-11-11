from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, r"C:\Users\auron\Documents\CAM Workbench")

from cam_core import create_bottom_edge_hole, discover_mounting_parts, import_dxf
import cam_editor_app as app_module
from cam_editor_app import CamWorkbench, DEFAULT_SETTINGS


def valid_offsets(plan, part_id: str) -> list[float]:
    part = next(value for value in plan.parts if value.part_id == part_id)
    width = part.bounds[2] - part.bounds[0]
    result = []
    for index in range(1, 20):
        value = width * index / 20.0
        try:
            create_bottom_edge_hole(plan, part_id, value)
        except ValueError:
            continue
        if not result or abs(value - result[-1]) > width * 0.12:
            result.append(value)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, required=True)
    args = parser.parse_args()
    def isolated_settings(self):
        self.settings_load_warning = ""
        return copy.deepcopy(DEFAULT_SETTINGS)

    CamWorkbench._load_settings = isolated_settings
    CamWorkbench._save_settings = lambda self: None
    CamWorkbench._offer_recovery = lambda self: None
    CamWorkbench._write_recovery_snapshot = lambda self, **kwargs: None
    app_module.messagebox.showerror = lambda title, message, **kwargs: (_ for _ in ()).throw(AssertionError(f"{title}: {message}"))
    app_module.messagebox.showwarning = lambda *args, **kwargs: None
    app_module.messagebox.showinfo = lambda *args, **kwargs: None
    app_module.messagebox.askyesno = lambda *args, **kwargs: True

    app = CamWorkbench()
    app.tk.call("tk", "scaling", args.scale)
    app.geometry("1280x760+10+10")
    app.update_idletasks()
    source = Path(r"C:\Users\auron\Documents\Boat stuff\AECI half inch rail mounted letters.reference.dxf")
    project = import_dxf(source).project
    project.name = "AECI bottom-edge UI proof"
    candidates = [
        part for part in discover_mounting_parts(project.entities, {entity.id for entity in project.entities})
        if 4.0 < part.bounds[2] - part.bounds[0] < 25.0 and 5.0 < part.bounds[3] - part.bounds[1] < 20.0
    ]
    if len(candidates) < 3:
        raise AssertionError("AECI copy did not expose three usable letter candidates")

    app.project = project
    app.project_path = None
    app._switch_workspace("mounting")
    app.select_mounting_workflow("Bottom-Edge Rail")
    app.selected_ids = set(candidates[0].member_ids + candidates[1].member_ids)
    app.generate_mounting_preview()
    if app.edge_mounting_plan is None or len(app.edge_mounting_plan.parts) != 2:
        raise AssertionError("Use Selected Letters did not create a two-letter review")
    first_part = app.edge_mounting_plan.parts[0]
    offsets = valid_offsets(app.edge_mounting_plan, first_part.part_id)
    if len(offsets) < 3:
        raise AssertionError("AECI letter did not expose three valid test locations")
    first_label = next(label for label, part_id in app.edge_part_ids.items() if part_id == first_part.part_id)
    app.edge_part_var.set(first_label)
    for value in offsets[:2]:
        app.edge_along_var.set(f"{value:g}")
        app.add_mounting_feature()
    if len(app.edge_mounting_plan.holes) != 2:
        raise AssertionError("Add Hole did not create two reviewed axes")

    first_row = app.mounting_tree.get_children()[0]
    edited_hole_id = app.mounting_entity_ids[first_row]
    app.mounting_tree.selection_set(first_row)
    app._mounting_tree_selected()
    app.mounting_edit_x_var.set(f"{offsets[2]:g}")
    app.update_mounting_feature()
    edited_hole = next(hole for hole in app.edge_mounting_plan.holes if hole.hole_id == edited_hole_id)
    if abs(edited_hole.along_from_left - offsets[2]) > 1e-5:
        raise AssertionError(
            f"Edit Selected Hole did not update the reviewed axis: expected {offsets[2]}, got {edited_hole.along_from_left}; "
            f"entry={app.mounting_edit_x_var.get()} status={app.status.get()}"
        )
    app.apply_mounting_preview()
    if app.edge_export_button.cget("state") == "disabled":
        raise AssertionError("Export Jigs did not enable after Save Hole Locations")
    app.update_idletasks()
    if not app.edge_export_button.winfo_ismapped():
        raise AssertionError("Persistent Export Jigs action is not visible")
    if app.edge_export_button.winfo_rooty() + app.edge_export_button.winfo_height() > app.winfo_rooty() + app.winfo_height():
        raise AssertionError("Persistent Export Jigs action fell below the 1280x760 window")

    export_dir = Path(tempfile.mkdtemp(prefix="cam-edge-jig-proof-"))
    app_module.filedialog.askdirectory = lambda **kwargs: str(export_dir)
    app.export_bottom_edge_jigs()
    if len(list(export_dir.glob("*.dxf"))) != 1 or not list(export_dir.glob("*MANIFEST.txt")):
        raise AssertionError("Fusion jig export did not write the expected DXF and manifest")

    retained_ids = {hole.hole_id for hole in app.edge_mounting_plan.holes}
    app.return_to_edge_selection()
    app.selected_ids = set(candidates[2].member_ids)
    app.add_selected_edge_letters()
    if len(app.edge_mounting_plan.parts) != 3:
        raise AssertionError("Add Selected Letters did not add the third AECI letter")
    if {hole.hole_id for hole in app.edge_mounting_plan.holes} != retained_ids:
        raise AssertionError("Adding another letter lost reviewed hole IDs")

    app.edge_rail_clearance_var.set("0.281")
    app.edge_rail_length_var.set("250")
    app.edge_rail_width_var.set("2")
    app.edge_rail_max_section_var.set("120")
    app.edge_rail_split_clearance_var.set("0.5")
    app.edge_rail_expanded_var.set(True)
    app._toggle_edge_rail_details()
    app.update_idletasks()
    if not app.edge_rail_frame.winfo_ismapped() or not app.edge_export_button.winfo_ismapped():
        raise AssertionError("Expanding rail details hid either the details or the persistent Export Jigs action")
    app.apply_mounting_preview()
    app.open_bottom_edge_rail_routing_job()
    plates = app.project.metadata.get("plates", [])
    if len(plates) != 3 or any(float(plate["width"]) > 120.0 for plate in plates):
        raise AssertionError("Long rail did not become three router-sized physical plates")

    app.update_idletasks()
    if app.winfo_width() < 1200 or app.winfo_height() < 700:
        raise AssertionError("Application did not retain the requested proof size")
    app.dirty = False
    app.destroy()
    print(f"PASS scale={args.scale:g} two holes/edit/apply/export/add-letter; rail plates={[plate['width'] for plate in plates]}")


if __name__ == "__main__":
    main()
