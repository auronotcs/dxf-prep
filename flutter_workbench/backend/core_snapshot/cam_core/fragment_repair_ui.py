"""Warning -> bounded preview -> undoable repair -> fresh output preflight."""
from tkinter import messagebox
from .contour_repair_ui import ContourRepairDialog
from .fragment_repair import apply_fragment_repair,context_is_current
from .object_identity import object_identity_label


class FragmentRepairDialog(ContourRepairDialog):
    def __init__(self,app):
        super().__init__(app,points_only=True)
        self.title('Repair Fragment Contours — preserve machining settings')
        self.apply_button.configure(text='Apply, Regenerate & Recheck Output')

    def preview(self):
        super().preview()
        if not self.proposal:
            self.summary.set(self.summary.get()+'\nA fragment is not necessarily junk. No automatic deletion; inspect the source if point cleanup cannot repair it.')
            return
        try:
            _,linked=apply_fragment_repair(self.project,self.proposal,self.selected)
        except (ValueError,TypeError) as exc:
            self.proposal=[];self.apply_button.configure(state='disabled')
            self.summary.set(str(exc));return
        names=', '.join(object_identity_label(self.project,i) for i in sorted(linked))
        self.summary.set(self.summary.get()+'\n'
            +(f'Exact linked cleanout copies also updated: {names}. ' if linked else '')
            +'All machining operation settings remain identical. Apply regenerates and reruns full output checks; it does NOT write CNC files. Unresolved warnings remain for review.')

    def accept(self):
        if self.current() and self.proposal:
            try:apply_fragment_repair(self.parent.project,self.proposal,self.selected)
            except (ValueError,TypeError) as exc:
                self.apply_button.configure(state='disabled');self.summary.set(str(exc));return
        super().accept()


def run_fragment_repair(app, ids, context):
    if not context_is_current(app,context):
        app.status.set('Job changed since the fragment warnings. Reopen Output before repairing.');return
    app.selected_ids=set(ids)
    dialog=FragmentRepairDialog(app)
    app.wait_window(dialog)
    if dialog.result is None:return
    if not dialog.current():
        app.status.set('Job changed; fragment repair was not applied.');return
    try:candidate,linked=apply_fragment_repair(app.project,dialog.result,ids)
    except (ValueError,TypeError) as exc:
        messagebox.showerror('Repair Fragment Contours',str(exc),parent=app);return
    app.project=candidate
    app._invalidate_layer_preparation()
    app._defer_toolpath_regeneration()
    app._output_generation_cache.clear()
    app.clear_geometry_diagnostics()
    app._update_sheet_after_geometry_change()
    app._set_dirty(True)
    app._record_history('Repair fragment contours (machining settings preserved)','JR')
    app._redraw()
    app.status.set(f'Repaired {len(dialog.result)} contour(s) and {len(linked)} linked copy/copies. Machining settings preserved. Regenerating and rechecking Output; Undo restores the repair.')
    # The old Output modal must unwind before starting a fresh output worker.
    # Normal Output handles cancellation/failure and never publishes until the
    # user explicitly confirms the newly checked controller-file dialog.
    context=(app.project,app.document_id,app.document_revision,app.project.to_dict())
    app.after_idle(lambda: recheck_fragment_output(app,context))


def recheck_fragment_output(app,context):
    if not context_is_current(app,context):
        app.status.set('Job changed after repair. Reopen Output to generate and check the current job.');return
    app._fragment_repair_preview_context=context
    try:app.output_plates()
    finally:app.__dict__.pop('_fragment_repair_preview_context',None)


def adopt_rechecked_preview(app,cache):
    """Reuse the completed output generation for display; no second CAM pass."""
    context=getattr(app,'_fragment_repair_preview_context',None)
    if context is None or not context_is_current(app,context):return
    enabled={o.id for o in app.project.operations if o.enabled}
    if not enabled.issubset(cache.generated_paths):return
    app.toolpath_previews=dict(cache.generated_paths)
    app.toolpath_preview_errors={}
    app._mark_toolpath_previews_current()
    app._redraw_canvas()
