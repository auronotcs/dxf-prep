from __future__ import annotations

from pathlib import Path
import sys

REPO = Path(r"C:\Users\auron\Documents\CAM Workbench")
sys.path.insert(0, str(REPO))

from cam_core import CamProject, PatchworkArea, Plate, Point, Rectangle, entity_bounds
import cam_editor_app as app_module
from cam_editor_app import CamWorkbench, NestOptionsDialog, PatchworkDialog


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main() -> None:
    app_module.AUTOSAVE_PATH = Path(r"C:\Users\auron\Documents\Boat stuff\patchwork-y-smoke-recovery.json")
    errors: list[tuple[str, str]] = []
    app_module.messagebox.askyesnocancel = lambda *_args, **_kwargs: False
    app_module.messagebox.askyesno = lambda *_args, **_kwargs: False
    app_module.messagebox.showinfo = lambda *_args, **_kwargs: None
    app_module.messagebox.showwarning = lambda *_args, **_kwargs: None
    app_module.messagebox.showerror = lambda title, message, **_kwargs: errors.append((str(title), str(message)))
    app = CamWorkbench()
    app.withdraw()
    app._save_settings = lambda: None
    try:
        plate = Plate(1, 0, 0, 120, 60, "Router table")
        app.project = CamProject("Patchwork Y smoke", metadata={
            "plates": [plate.to_dict()],
            "active_plate_number": 1,
            "job_setup": {"material": "Aluminum", "material_thickness": 0.5, "safe_z": 0.75},
        })
        app._restore_sheet_metadata()
        app._reset_history("Patchwork smoke start", "PW")

        original_scaling = float(app.tk.call("tk", "scaling"))
        y_bars = None
        for multiplier in (1.0, 1.25, 1.5):
            app.tk.call("tk", "scaling", original_scaling * multiplier)
            dialog = PatchworkDialog(app, plate, [], "in")
            dialog.repeat_values["name"].set("0.5 in aluminum flat bar")
            dialog.repeat_values["width"].set("54.5")
            dialog.repeat_values["height"].set("12")
            dialog.repeat_values["count"].set("3")
            dialog.repeat_values["x"].set("2")
            dialog.repeat_values["y"].set("1")
            dialog.repeat_values["gap"].set("1")
            dialog.repeat_values["direction"].set("Along Y")
            dialog._add_repeated()
            dialog.update_idletasks()
            if [(item.x, item.y) for item in dialog.areas] != [(2, 1), (2, 14), (2, 27)]:
                raise AssertionError("Three-bar Along Y preview has incorrect coordinates")
            if len({item.id for item in dialog.areas}) != 3:
                raise AssertionError("Repeated stock did not receive stable distinct IDs")
            if len(dialog.preview_canvas.find_all()) < 4:
                raise AssertionError("Physical stock preview did not draw plate plus three stock pieces")
            apply_button = next(
                child for child in descendants(dialog)
                if isinstance(child, app_module.ttk.Button) and str(child.cget("text")) == "Apply Patchwork"
            )
            if apply_button.winfo_rooty() + apply_button.winfo_height() > dialog.winfo_rooty() + dialog.winfo_height():
                raise AssertionError(f"Patchwork footer is clipped at {multiplier:g}x Tk scaling")
            y_bars = list(dialog.areas)
            dialog.destroy()
        app.tk.call("tk", "scaling", original_scaling)
        assert y_bars is not None

        x_dialog = PatchworkDialog(app, plate, [], "in")
        x_dialog.repeat_values["width"].set("20")
        x_dialog.repeat_values["height"].set("12")
        x_dialog.repeat_values["count"].set("3")
        x_dialog.repeat_values["x"].set("1")
        x_dialog.repeat_values["y"].set("4")
        x_dialog.repeat_values["gap"].set("2")
        x_dialog.repeat_values["direction"].set("Across X")
        x_dialog._add_repeated()
        if [(item.x, item.y) for item in x_dialog.areas] != [(1, 4), (23, 4), (45, 4)]:
            raise AssertionError("Across X regression changed the original straight-run behavior")
        x_dialog.destroy()

        overflow_dialog = PatchworkDialog(app, plate, [], "in")
        overflow_dialog.repeat_values["width"].set("54.5")
        overflow_dialog.repeat_values["height"].set("12")
        overflow_dialog.repeat_values["count"].set("5")
        overflow_dialog.repeat_values["x"].set("2")
        overflow_dialog.repeat_values["y"].set("1")
        overflow_dialog.repeat_values["gap"].set("1")
        overflow_dialog.repeat_values["direction"].set("Along Y")
        overflow_dialog._add_repeated()
        if overflow_dialog.areas or not errors or "does not wrap or rotate" not in errors[-1][1]:
            raise AssertionError("Plate overflow did not reject the complete repeated-stock run")
        overflow_dialog.destroy()

        table_dialog = PatchworkDialog(app, plate, [], "in")
        table_dialog._use_router_table_envelope()
        if (table_dialog.result_plate.width, table_dialog.result_plate.height) != (120, 62):
            raise AssertionError("Explicit router-table preset did not produce X120 × Y62")
        if app.project.metadata["plates"][0]["height"] != 60:
            raise AssertionError("Choosing the dialog preset silently resized the live job before Apply")
        table_dialog.repeat_values["width"].set("54.5")
        table_dialog.repeat_values["height"].set("12")
        table_dialog.repeat_values["count"].set("5")
        table_dialog.repeat_values["x"].set("0")
        table_dialog.repeat_values["y"].set("0")
        table_dialog.repeat_values["gap"].set("0.5")
        table_dialog.repeat_values["direction"].set("Along Y")
        table_dialog._add_repeated()
        if len(table_dialog.areas) != 5 or table_dialog.areas[-1].y + table_dialog.areas[-1].height != 62:
            raise AssertionError("Five 12-inch bars with 0.5-inch gaps did not fit the 62-inch table")
        table_plate = table_dialog.result_plate
        table_bars = list(table_dialog.areas)
        table_dialog.destroy()

        class SavedPatchworkDialog:
            def __init__(self, *_args, **_kwargs):
                self.result = list(table_bars)
                self.result_plate = table_plate

        app_module.PatchworkDialog = SavedPatchworkDialog
        app.wait_window = lambda _dialog: None
        app.open_patchwork_editor()
        saved_ids = [item["id"] for item in app.project.metadata["patchwork_areas"]]
        if app.project.metadata["plates"][0]["height"] != 62:
            raise AssertionError("Applying the explicit table preset did not commit the 62-inch envelope")
        app._undo_history()
        if app.project.metadata.get("patchwork_areas"):
            raise AssertionError("Undo did not restore the pre-Patchwork job")
        app._redo_history()
        if [item["id"] for item in app.project.metadata["patchwork_areas"]] != saved_ids:
            raise AssertionError("Redo did not restore the same persistent Patchwork IDs")
        reopened = CamProject.from_dict(app.project.to_dict())
        if [item["id"] for item in reopened.metadata["patchwork_areas"]] != saved_ids:
            raise AssertionError("Save/reopen round-trip changed Patchwork IDs")

        options_dialog = NestOptionsDialog(
            app, units="in", initial_spacing=0.25, initial_resolution=0.125,
            has_selection=False, plate_count=1, active_plate_number=1,
            prefer_active_plate=False, has_patchwork=True,
        )
        options_dialog.update_idletasks()
        if options_dialog.overflow_var.get() or str(options_dialog.overflow_check.cget("state")) != "disabled":
            raise AssertionError("Patchwork nesting did not visibly disable automatic overflow plates")
        options_dialog.destroy()

        app.project.entities = [
            Rectangle(id=f"part-{index}", corner_a=Point(80 + index, 40), corner_b=Point(120 + index, 50))
            for index in range(3)
        ]

        class SavedNestOptionsDialog:
            def __init__(self, *_args, **_kwargs):
                self.result = {
                    "spacing": 0.5,
                    "selected_only": False,
                    "active_plate_only": False,
                    "allow_rotation": False,
                    "rotation_step_degrees": None,
                    "maximum_rotation_degrees": 0.0,
                    "placement_strategy": "rectangular",
                    "use_holes": False,
                    "search_resolution": 0.125,
                    "automatic_compounds": False,
                    "allow_new_plates": True,
                    "common_line_station": None,
                }

        app_module.NestOptionsDialog = SavedNestOptionsDialog
        app.auto_nest_to_plates()
        physical_bounds = [item.bounds for item in table_bars]
        for entity in app.project.entities:
            bounds = entity_bounds(entity)
            if bounds is None:
                continue
            if not any(
                area[0] <= bounds[0] <= bounds[2] <= area[2]
                and area[1] <= bounds[1] <= bounds[3] <= area[3]
                for area in physical_bounds
            ):
                raise AssertionError(f"Auto Nest placed {entity.id} in a Patchwork gap")
        if len(app.project.metadata.get("plates", [])) != 1:
            raise AssertionError("Patchwork nesting replaced the production plate with virtual regions")
        print("patchwork-y-axis-smoke=PASS")
    finally:
        app.dirty = False
        app.destroy()


if __name__ == "__main__":
    main()
