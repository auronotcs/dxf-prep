"""Batch review before CAM; repairs drawing and pending snapshots consistently."""
import copy
import tkinter as tk
from tkinter import ttk,messagebox
from .quick_offset_check import quick_offset_check,check_key
from .fragment_repair import apply_fragment_repair
from .fragment_repair_ui import FragmentRepairDialog
from .contour_repair_ui import ContourRepairDialog
from .review_view import uncovered_canvas_rect,review_transform
from .units import unit_scale
from .quick_offset_resolution import repairable_fragment_ids, exclusion_plan, commit_profile_exclusion


class QuickRepairDialog(FragmentRepairDialog):
    def __init__(self,app,project,ids):
        self.target=project
        self.live_project=app.project;self.live_payload=app.project.to_dict()
        super().__init__(app)
        self.project=copy.deepcopy(project);self.source_payload=project.to_dict();self.selected=set(ids)
        self.title('Quick Check — Preview Batch Repair')
        self.apply_button.configure(text='Apply Repair & Recheck Offsets Only')

    def current(self):
        return (self.parent.project is self.live_project and self.parent.project.to_dict()==self.live_payload
                and self.parent.document_id==self.document_id and self.parent.document_revision==self.revision
                and self.target.to_dict()==self.source_payload)

    def preview(self):
        ContourRepairDialog.preview(self)
        if self.proposal:
            try:apply_fragment_repair(self.project,self.proposal,self.selected)
            except (ValueError,TypeError) as exc:
                self.proposal=[];self.apply_button.configure(state='disabled');self.summary.set(str(exc));return
        self.summary.set(self.summary.get()+'\nQuick workflow: Apply rechecks offsets only. No full generation until Continue. Accepted drawing repairs remain undoable if later generation is canceled.')

    def accept(self):
        if not self.current():
            self.summary.set('Job changed. Cancel and restart the quick check.');return
        if self.proposal:
            try:apply_fragment_repair(self.target,self.proposal,self.selected)
            except (ValueError,TypeError) as exc:
                self.summary.set(str(exc));self.apply_button.configure(state='disabled');return
            self.result=self.proposal;self.destroy()


def commit_quick_repair(app,target,proposals,ids):
    """Keep pending operation settings intact; never publish staged-only geometry."""
    candidate,_=apply_fragment_repair(target,proposals,ids)
    if target is app.project:
        live_candidate=candidate
    else:
        original={e.id:e for e in target.entities};live={e.id:e for e in app.project.entities}
        changed={e.id for e in candidate.entities if e.to_dict()!=original[e.id].to_dict()}
        if any(i not in live or live[i].to_dict()!=original[i].to_dict() for i in changed):
            raise ValueError('Pending geometry differs from the drawing. Cancel generation and repair the source through Repair first; no changes applied.')
        live_candidate,_=apply_fragment_repair(app.project,proposals,ids)
        changed_live={e.id for e in live_candidate.entities if e.to_dict()!=live[e.id].to_dict()}
        if changed_live!=changed:raise ValueError('Pending and live dependencies differ. No changes applied; repair the source first.')
    # Preserve project/operation identity held by parameter-dialog callers.
    app.project.entities=live_candidate.entities;app.project.metadata=live_candidate.metadata
    if target is not app.project:
        target.entities=candidate.entities;target.metadata=candidate.metadata
    app._invalidate_layer_preparation();app._defer_toolpath_regeneration()
    app._output_generation_cache.clear();app.clear_geometry_diagnostics()
    app._set_dirty(True);app._record_history('Quick-check batch contour repair','JR');app._redraw()


class QuickOffsetDialog(tk.Toplevel):
    def __init__(self,app,project,operations,findings,checked):
        super().__init__(app);self.app=app;self.project=project;self.operations=operations;self.result=False
        self.live=app.project;self.token=(app.document_id,app.document_revision)
        self.title('Quick Profile Check — before generation');self.geometry('1000x600');self.minsize(800,480)
        self.transient(app);self.grab_set();self.protocol('WM_DELETE_WINDOW',self.destroy)
        ttk.Label(self,text='Review the whole batch before generating toolpaths',font=('',12,'bold'),padding=12).pack(anchor='w')
        ttk.Label(self,text='Offsets only: no depth passes, entries or collision sweeps. Full output collision checks still run afterward.',wraplength=950,padding=(12,0)).pack(anchor='w')
        self.summary=tk.StringVar();ttk.Label(self,textvariable=self.summary,padding=12,wraplength=950).pack(anchor='w')
        frame=ttk.Frame(self,padding=12);frame.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(frame,columns=('status','object','operation','issue'),show='headings',selectmode='extended')
        for col,title,width in (('status','Status',75),('object','Geometry',90),('operation','Operation / cutter',240),('issue','Offset finding',495)):
            self.tree.heading(col,text=title);self.tree.column(col,width=width,minwidth=70)
        self.tree.grid(row=0,column=0,sticky='nsew');frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);scroll.grid(row=0,column=1,sticky='ns');self.tree.configure(yscrollcommand=scroll.set)
        self.details=tk.StringVar();ttk.Label(self,textvariable=self.details,wraplength=950,padding=12).pack(fill='x')
        actions=ttk.Frame(self,padding=(12,0));actions.pack(fill='x')
        self.exclude_button=ttk.Button(actions,text='Exclude Selected from Profile…',command=self.exclude);self.exclude_button.pack(side='left')
        ttk.Label(actions,text='Keeps the drawing geometry. Only use for contours you do not want this profile to cut.',wraplength=500).pack(side='left',padx=8)
        buttons=ttk.Frame(self,padding=12);buttons.pack(fill='x')
        self.show_button=ttk.Button(buttons,text='Show Me',command=self.show);self.show_button.pack(side='left')
        self.repair_button=ttk.Button(buttons,text='Repair Point Defects…',command=self.repair);self.repair_button.pack(side='left',padx=8)
        ttk.Button(buttons,text='Recheck Offsets',command=self.rescan).pack(side='left')
        ttk.Button(buttons,text='Cancel Generation',command=self.destroy).pack(side='right')
        self.continue_button=ttk.Button(buttons,text='Continue to Generation',command=self.accept);self.continue_button.pack(side='right',padx=8)
        self.tree.bind('<<TreeviewSelect>>',self.selection_changed);self.tree.bind('<Double-1>',lambda _:self.show())
        self.showing=False;self.populate(findings,checked)

    def current(self):
        return (self.app.project is self.live and self.token==(self.app.document_id,self.app.document_revision)
                and self.live.to_dict()==self.live_payload and check_key(self.project,self.operations)==self.key)

    def populate(self,findings,checked):
        self.findings=sorted(findings,key=lambda f:f['severity']!='error');findings=self.findings
        self.repair_ids=repairable_fragment_ids(self.project,findings)
        self.key=check_key(self.project,self.operations);self.live_payload=self.live.to_dict()
        self.token=(self.app.document_id,self.app.document_revision)
        self.tree.delete(*self.tree.get_children())
        for index,f in enumerate(findings):self.tree.insert('','end',iid=str(index),values=('BLOCKED' if f['severity']=='error' else 'REVIEW',f['geometry'],f['operation'],f['summary']))
        blockers=sum(f['severity']=='error' for f in findings)
        self.summary.set(f'{checked} distinct profile offset(s) checked; {blockers} blocking error(s), {len(findings)-blockers} review warning(s). '
            +('Resolve the BLOCKED rows to continue. ' if blockers else 'Continue is available; full generation and collision checks follow. ')
            + (f'{len(self.repair_ids)} contour(s) have point cleanup available.' if self.repair_ids else 'No safe near-duplicate point repair found at the default tolerance.'))
        self.repair_button.configure(state='normal' if self.repair_ids else 'disabled')
        self.continue_button.configure(state='disabled' if any(f['severity']=='error' for f in findings) else 'normal')
        if findings:self.tree.selection_set('0')
        self.selection_changed()

    def selection_changed(self,event=None):
        rows=self.tree.selection();f=self.findings[int(rows[0])] if rows else None
        details=f['details'] if f else ''
        if f and f['code']=='toolpath.split_geometry' and not set(f['entity_ids']) & self.repair_ids:
            details+='\nPoint cleanup will not resolve this finding at the default tolerance. A narrow feature can split a valid offset. Continue to review the generated paths, or cancel and choose a smaller cutter if that detail must be reached.'
        elif f and f['severity']=='error' and 'collapses' in f['details'].lower():
            details+='\nThe cutter does not fit this contour at the selected allowance. Use a smaller cutter, or explicitly exclude this contour if it should not be cut. Point cleanup is not a cutter-size change.'
        self.details.set(details)
        self.show_button.configure(state='normal' if f and f['point'] else 'disabled')
        selected=[self.findings[int(row)] for row in rows]
        try:exclusion_plan(self.project,self.operations,selected)
        except (ValueError,TypeError):can_exclude=False
        else:can_exclude=True
        self.exclude_button.configure(state='normal' if can_exclude else 'disabled')
        if self.showing:self.show()

    def show(self):
        rows=self.tree.selection()
        if not rows or not self.current():return
        f=self.findings[int(rows[0])];point=f['point']
        if point is None:return
        self.showing=True
        self.app.focus_output_problem(f['entity_ids'],point,f['summary'],f['code'],f['severity'])
        self.update_idletasks();c=self.app.canvas
        dx=self.winfo_rootx()-c.winfo_rootx();dy=self.winfo_rooty()-c.winfo_rooty()
        viewport=uncovered_canvas_rect(c.winfo_width(),c.winfo_height(),(dx,dy,dx+self.winfo_width(),dy+self.winfo_height()))
        if viewport:
            factor=unit_scale('in',self.project.units);span=.5*factor
            self.app.scale,self.app.origin_x,self.app.origin_y=review_transform((point.x-span,point.y-span,point.x+span,point.y+span),viewport,factor)
            self.app._redraw_canvas()

    def rescan(self):
        if not self.current():
            self.summary.set('Job changed outside this review. Cancel and restart generation.');self.continue_button.configure(state='disabled');return
        self.populate(*quick_offset_check(self.project,self.operations))

    def repair(self):
        if not self.current():self.rescan();return
        ids=self.repair_ids
        if not ids:return
        dialog=QuickRepairDialog(self.app,self.project,ids);self.wait_window(dialog);self.grab_set()
        if dialog.result is None:return
        if not self.current() or not dialog.current():self.rescan();return
        try:commit_quick_repair(self.app,self.project,dialog.result,ids)
        except (ValueError,TypeError) as exc:messagebox.showerror('Quick repair',str(exc),parent=self);return
        self.populate(*quick_offset_check(self.project,self.operations))

    def exclude(self):
        if not self.current():self.rescan();return
        selected=[self.findings[int(row)] for row in self.tree.selection()]
        try:exclusion_plan(self.project,self.operations,selected)
        except (ValueError,TypeError) as exc:messagebox.showerror('Profile selection',str(exc),parent=self);return
        labels=list(dict.fromkeys(f"{f['geometry']} — {f['operation']}" for f in selected))
        if not messagebox.askyesno('Exclude selected contours from profile?',
            '\n'.join(labels)+'\n\nThese contours will NOT be cut by the listed profiles. Their drawing geometry stays intact. '
            'Use this only for intentionally unwanted cuts. Existing profile changes are undoable; a new profile remains pending. '
            'This rechecks offsets without generating or writing CNC files.',parent=self):return
        if not self.current():self.rescan();return
        try:commit_profile_exclusion(self.app,self.project,self.operations,selected)
        except (ValueError,TypeError) as exc:messagebox.showerror('Profile selection',str(exc),parent=self);return
        self.populate(*quick_offset_check(self.project,self.operations))

    def accept(self):
        if not self.current():self.rescan();return
        if any(f['severity']=='error' for f in self.findings):return
        if self.findings and not messagebox.askyesno('Continue with offset findings?',
            'Unresolved offset findings remain. Continue to full generation and collision checks? This does not approve collisions or write CNC files.',parent=self):return
        if not self.current():self.rescan();return
        self.result=True;self.destroy()


def quick_offset_gate(app,project,operations):
    if app.__dict__.get('_quick_offset_check_active',False):
        raise ValueError('A quick profile review is already open. Finish or cancel it first.')
    app._quick_offset_check_active=True
    try:return _quick_offset_gate(app,project,operations)
    finally:app._quick_offset_check_active=False


def _quick_offset_gate(app,project,operations):
    # Review all already-configured profiles even when the requested expensive
    # generation is a cleanout. Pending edits override their stored operation.
    pending={op.id:op for op in operations}
    operations=[pending.pop(op.id,op) for op in project.operations]+list(pending.values())
    key=check_key(project,operations)
    accepted=getattr(app,'_quick_offset_review_keys',[])
    if key in accepted:return True
    findings,checked=quick_offset_check(project,operations)
    if findings:
        dialog=QuickOffsetDialog(app,project,operations,findings,checked)
        app.wait_window(dialog)
        if not dialog.result:return False
    key=check_key(project,operations)
    app._quick_offset_review_keys=(accepted+[key])[-8:]
    return True
