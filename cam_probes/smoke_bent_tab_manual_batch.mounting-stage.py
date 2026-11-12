"""Live proof for multi-back/manual Bent Tab placement and cancellable generation."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cam_core import CamProject, Circle, Line, Point, Polyline
import cam_editor_app as app_module
from cam_editor_app import CamWorkbench


def wait_for_task(app: CamWorkbench, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while app.bent_tab_task_cancel is not None and time.monotonic() < deadline:
        app.update()
        time.sleep(0.005)
    if app.bent_tab_task_cancel is not None:
        raise AssertionError("Bent-tab task did not reach a terminal state")


def wait_for_manual_validation(app: CamWorkbench, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while app.bent_manual_validation_active is not None and time.monotonic() < deadline:
        app.update()
        time.sleep(0.005)
    app.update()
    if app.bent_manual_validation_active is not None:
        raise AssertionError("Manual Bent Tab validation did not reach a terminal state")


def capture(widget: CamWorkbench, path: Path) -> None:
    widget.update_idletasks()
    left, top = widget.winfo_rootx(), widget.winfo_rooty()
    ImageGrab.grab(
        bbox=(left, top, left + widget.winfo_width(), top + widget.winfo_height()),
        all_screens=True,
    ).save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = CamWorkbench()
    app._save_settings = lambda: None
    app.geometry("1450x860+30+30")

    def run() -> None:
        app.attributes("-topmost", True)
        app_module.messagebox.showerror = lambda title, message, **_kwargs: (_ for _ in ()).throw(AssertionError(f"{title}: {message}"))
        app_module.messagebox.showinfo = lambda *_args, **_kwargs: None
        app_module.messagebox.showwarning = lambda *_args, **_kwargs: None
        app_module.messagebox.askyesno = lambda *_args, **_kwargs: True
        app_module.messagebox.askyesnocancel = lambda *_args, **_kwargs: False
        short = Polyline(
            id="w-back",
            points=[Point(0,0),Point(2,0),Point(3,3),Point(7,3),Point(8,0),Point(10,0),Point(6,12),Point(4,12)],
            closed=True, layer="BACKS", metadata={"mounting_label":"W shortfall"},
        )
        roomy = Polyline(
            id="roomy-back", points=[Point(15,0),Point(29,0),Point(29,8),Point(15,8)],
            closed=True, layer="BACKS", metadata={"mounting_label":"Roomy back"},
        )
        hole = Polyline(
            id="roomy-hole", points=[Point(20,3),Point(22,3),Point(22,5),Point(20,5)],
            closed=True, layer="BACKS",
        )
        keyhole = Circle(
            id="roomy-keyhole", center=Point(25,4), radius=.2, layer="KEYHOLES",
            metadata={"mounting_generated":True, "mounting_owner_entity_id":roomy.id},
        )
        unrelated = Line(id="note-line", start=Point(40,0), end=Point(41,1), layer="NOTES")
        unrelated_before = unrelated.to_dict()
        app.project.entities = [short, roomy, hole, keyhole, unrelated]
        app.selected_ids = {entity.id for entity in app.project.entities}
        app._switch_workspace("mounting")
        app.select_mounting_workflow("Bent Tab Backs")
        app.update_idletasks()
        app.mounting_scroll_canvas.yview_moveto(1.0)
        app.update_idletasks()
        shell_top = app.mounting_workspace_panel.winfo_rooty()
        shell_bottom = shell_top + app.mounting_workspace_panel.winfo_height()
        for label, button in (
            ("Generate", app.mounting_generate_button),
            ("Apply", app.mounting_apply_button),
        ):
            if not button.winfo_viewable() or not shell_top <= button.winfo_rooty() < shell_bottom:
                raise AssertionError(f"Pinned {label} action disappeared when Mounting settings scrolled")
        app.mounting_scroll_canvas.yview_moveto(0.0)
        app.bent_minimum_count_var.set("5")
        app.bent_max_span_var.set("100")
        app.bent_corner_clearance_var.set("0.1")
        app.bent_minimum_separation_var.set("1")
        app.generate_mounting_preview()
        wait_for_task(app)
        if set(app.bent_tab_results) != {short.id, roomy.id}:
            raise AssertionError("Selected backs were not planned independently")
        if len(app.bent_tab_anchors_by_owner[short.id]) != 3 or short.id not in app.bent_tab_part_shortfalls:
            raise AssertionError("W 5-to-3 per-back shortfall was not explicit")
        if len(app.bent_tab_anchors_by_owner[roomy.id]) < 5:
            raise AssertionError("Roomy back did not search beyond one tab per logical edge")
        if unrelated.id not in app.bent_tab_unaccounted_selection:
            raise AssertionError("Unrelated selected geometry was not visibly accounted for")

        before_manual = copy.deepcopy(app.bent_tab_anchors_by_owner)
        app.start_bent_tab_manual_placement()
        if any(widget.winfo_manager() for widget in app.bent_auto_widgets):
            raise AssertionError("Manual mode did not collapse optional Auto proposal settings")
        hover_started = time.perf_counter()
        app._update_manual_bent_tab_hover(Point(2.5, 1.5))
        hover_callback_ms = (time.perf_counter() - hover_started) * 1000
        if hover_callback_ms > 50:
            raise AssertionError(f"Manual hover callback blocked for {hover_callback_ms:.1f} ms")
        wait_for_manual_validation(app)
        click_started = time.perf_counter()
        app._place_manual_bent_tab(Point(2.5, 1.5))
        click_callback_ms = (time.perf_counter() - click_started) * 1000
        if click_callback_ms > 100:
            raise AssertionError(f"Manual click callback blocked for {click_callback_ms:.1f} ms")
        wait_for_manual_validation(app)
        if len(app.bent_tab_anchors_by_owner[short.id]) != 4:
            raise AssertionError("Manual click did not add to the clicked owner")
        app.undo_bent_tab_preview_edit()
        if app.bent_tab_anchors_by_owner != before_manual:
            raise AssertionError("One-step manual placement undo did not restore the prior preview")
        app._update_manual_bent_tab_hover(Point(2.5, 1.5))
        wait_for_manual_validation(app)
        app._place_manual_bent_tab(Point(2.5, 1.5))
        last = len(app.bent_tab_anchors_by_owner[short.id]) - 1
        app.mounting_selected_feature_keys = {f"tab:{short.id}:{last}"}
        app.remove_mounting_features()
        if len(app.bent_tab_anchors_by_owner[short.id]) != 3:
            raise AssertionError("Preview tab deletion did not remain owner-scoped")
        reviewed_after_delete = copy.deepcopy(app.bent_tab_anchors_by_owner)
        app._generate_bent_tab_preview(preserve_existing=True)
        wait_for_task(app)
        if app.bent_tab_anchors_by_owner != reviewed_after_delete:
            raise AssertionError("Preserve regeneration silently resurrected a removed tab")

        # Slow only this proof so Cancel and Tk repaint/input responsiveness are observable.
        original_propose = app_module.propose_bent_tab_anchors
        def delayed(*values, **options):
            cancel = options.get("cancel_check")
            for _ in range(30):
                if cancel is not None and cancel():
                    raise InterruptedError("Bent-tab generation canceled.")
                time.sleep(0.01)
            return original_propose(*values, **options)
        app_module.propose_bent_tab_anchors = delayed
        preserved = copy.deepcopy(app.bent_tab_anchors_by_owner)
        repaint = {"ran": False}
        try:
            app._generate_bent_tab_preview(preserve_existing=False)
            app.after(10, lambda: repaint.__setitem__("ran", True))
            deadline = time.monotonic() + .12
            while time.monotonic() < deadline:
                app.update()
                time.sleep(.005)
            if (
                not app.bent_task_progress_frame.winfo_ismapped()
                or "Back" not in app.bent_task_progress_text_var.get()
                or "disabled" not in app.mounting_generate_button.state()
            ):
                raise AssertionError("Live progress did not expose phase/current back or disable duplicate Generate")
            capture(app, args.output / "bent-tab-generation-progress.png")
            app.cancel_bent_tab_generation()
            wait_for_task(app)
        finally:
            app_module.propose_bent_tab_anchors = original_propose
        if not repaint["ran"] or app.bent_tab_anchors_by_owner != preserved:
            raise AssertionError("Cancellation did not remain responsive or preserve the prior preview")

        # A document revision during a second task must discard that immutable result.
        app_module.propose_bent_tab_anchors = delayed
        try:
            app._generate_bent_tab_preview(preserve_existing=False)
            def change_document() -> None:
                app.project.metadata["bent_tab_stale_guard_proof"] = True
                app.document_revision += 1
            app.after(20, change_document)
            wait_for_task(app)
        finally:
            app_module.propose_bent_tab_anchors = original_propose
        if app.bent_tab_anchors_by_owner != preserved or not app.project.metadata.get("bent_tab_stale_guard_proof"):
            raise AssertionError("Stale worker result replaced the prior preview or the newer document")
        app.project.metadata.pop("bent_tab_stale_guard_proof", None)

        app.cancel_interactive_mode()
        app.apply_mounting_preview()
        outlines = [entity for entity in app.project.entities if entity.metadata.get("bent_tab_cut_outline")]
        if {entity.id for entity in outlines} != {short.id, roomy.id}:
            raise AssertionError("Multi-back Apply did not update each owner exactly once")
        current_unrelated = next(entity for entity in app.project.entities if entity.id == unrelated.id)
        if current_unrelated.to_dict() != unrelated_before:
            raise AssertionError("Unrelated selected geometry changed during multi-back Apply")
        current_keyhole = next(entity for entity in app.project.entities if entity.id == keyhole.id)
        if (
            current_keyhole.center != keyhole.center
            or current_keyhole.radius != keyhole.radius
            or current_keyhole.layer != keyhole.layer
            or not current_keyhole.metadata.get("mounting_generated")
            or current_keyhole.metadata.get("mounting_owner_entity_id") != roomy.id
        ):
            raise AssertionError("Existing registered keyhole geometry or ownership was not preserved")
        reopened = CamProject.from_dict(app.project.to_dict())
        guides = [entity for entity in reopened.entities if entity.metadata.get("bent_tab_generated") and entity.layer == "BEND_GUIDES"]
        if not guides or any("source_edge_index" not in guide.metadata for guide in guides):
            raise AssertionError("Source-relative manual anchors did not survive save/reopen")
        print(
            f"manual hover callback {hover_callback_ms:.2f} ms; "
            f"click callback {click_callback_ms:.2f} ms",
            flush=True,
        )
        app.dirty = False
        app.attributes("-topmost", False)
        app.destroy()

    app.after(250, run)
    app.mainloop()


if __name__ == "__main__":
    main()
