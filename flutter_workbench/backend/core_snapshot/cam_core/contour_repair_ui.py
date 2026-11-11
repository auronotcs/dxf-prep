"""Preview and explicit transaction acceptance for endpoint contour repairs."""
import copy
import tkinter as tk
from tkinter import ttk

from .contour_repair import propose_contour_repair,propose_near_point_repair,apply_contour_repair
from .shape_conversion_ui import ShapeConversionDialog
from .object_identity import object_identity_label
from .units import unit_scale
from .layout import entity_bounds


class ContourRepairDialog(ShapeConversionDialog):
    # Reuse the existing isolated source/result renderer and document guards.
    def __init__(self,parent,*,backtracks_only=False,opening_review=False,points_only=False):
        tk.Toplevel.__init__(self,parent)
        title = 'Remove Near-Duplicate Points' if points_only else 'Micro Backtrack Repair' if backtracks_only else 'Join / Repair Contours'
        self.title(title);self.geometry('860x620');self.minsize(650,470)
        self.parent=parent;self.source_project=parent.project
        self.backtracks_only=backtracks_only
        self.points_only=points_only
        self.opening_review=opening_review
        self.document_id=parent.document_id;self.revision=parent.document_revision
        self.project=copy.deepcopy(parent.project);self.selected=set(parent.selected_ids)
        self.source_payload=self.project.to_dict();self.result=None;self.proposal=[]
        self.tolerance=tk.StringVar(self,value=f'{(.0001 if points_only else .001)*unit_scale("in",self.project.units):g}')
        self.cleanup=tk.BooleanVar(self,value=not backtracks_only)
        self.backtracks=tk.BooleanVar(self,value=True)
        form=ttk.Frame(self,padding=12);form.pack(fill='x')
        ttk.Label(form,text=title,font=('',12,'bold')).pack(anchor='w')
        ttk.Label(form,text='Collapse tightly clustered redundant vertices within the chosen tolerance.\n'
                  'Contours are not joined; open endpoints and native circles/rectangles stay unchanged.' if points_only else
                  'Connect line/polyline endpoints on the same layer and in the same group.\n'
                  '1. Join unambiguous gaps.  2. Remove microscopic backtracks from the joined result.'
                  if not backtracks_only else
                  'Remove microscopic backtracks only. Separate contours will not be joined.',wraplength=790).pack(anchor='w',pady=6)
        row=ttk.Frame(form);row.pack(fill='x')
        ttk.Label(row,text=f'{"Maximum point displacement" if points_only else "Repair tolerance" if backtracks_only else "Maximum endpoint gap"} ({self.project.units})').pack(side='left')
        ttk.Entry(row,textvariable=self.tolerance,width=14).pack(side='left',padx=8)
        ttk.Button(row,text='Preview Repair',command=self.preview).pack(side='right')
        if not points_only:
            ttk.Checkbutton(form,text='Remove repeated / negligible points (up to 0.00001 in; never more than join tolerance)',
                            variable=self.cleanup,state='disabled' if backtracks_only else 'normal').pack(anchor='w',pady=6)
            ttk.Checkbutton(form,text='Remove microscopic backtracks (up to 0.0005 in; limited by tolerance; not ordinary short edges)',
                            variable=self.backtracks).pack(anchor='w')
        ttk.Label(form,text='Gray = originals; blue = result; purple = joins; orange = backtracks; teal = redundant points. Nothing changes until Apply.\n'
                  'Same-contour cleanup preserves machining settings and refreshes paths in Manufacturing. '
                  'Joins/ambiguous dependents may require reassociation; see preview below.',wraplength=790).pack(anchor='w')
        footer=ttk.Frame(self,padding=10);footer.pack(side='bottom',fill='x')
        ttk.Button(footer,text='Cancel',command=self.destroy).pack(side='right')
        self.apply_button=ttk.Button(footer,text='Apply Repair',command=self.accept,state='disabled')
        self.apply_button.pack(side='right',padx=8)
        self.summary=tk.StringVar(self,value='Preview the selected contour pieces.')
        ttk.Label(self,textvariable=self.summary,wraplength=790,padding=(12,4)).pack(side='bottom',fill='x')
        self.canvas=tk.Canvas(self,background='white',height=220,highlightthickness=1)
        self.canvas.pack(fill='both',expand=True,padx=12,pady=4)
        self.canvas.bind('<Configure>',lambda event:self.draw())
        for var in (self.tolerance,self.cleanup,self.backtracks):var.trace_add('write',lambda *args:self.invalidate())
        self.protocol('WM_DELETE_WINDOW',self.destroy);self.bind('<Escape>',lambda event:self.destroy())
        self.transient(parent);self.grab_set();self.after_idle(self.preview)

    def invalidate(self):
        self.proposal=[];self.apply_button.configure(state='disabled')
        self.summary.set('Settings changed. Preview again before applying.');self.draw()

    def draw(self):
        super().draw()
        if not self.proposal:
            return
        sources=[e for e in self.project.entities if e.id in self.selected]
        boxes=[entity_bounds(e) for e in sources+[p.entity for p in self.proposal]]
        boxes=[b for b in boxes if b is not None]
        if not boxes:return
        x1=min(b[0] for b in boxes);y1=min(b[1] for b in boxes)
        x2=max(b[2] for b in boxes);y2=max(b[3] for b in boxes)
        width=max(100,self.canvas.winfo_width());height=max(80,self.canvas.winfo_height())
        scale=min((width-32)/max(x2-x1,1e-9),(height-32)/max(y2-y1,1e-9))
        for proposal in self.proposal:
            for points,color,tag in ((proposal.joins,'#8446b1','join_marker'),
                                     (proposal.backtrack_locations,'#d46a00','backtrack_marker'),
                                     (proposal.near_point_locations,'#008577','near_point_marker')):
                for p in points:
                    x=width/2+(p.x-(x1+x2)/2)*scale;y=height/2-(p.y-(y1+y2)/2)*scale
                    self.canvas.create_oval(x-6,y-6,x+6,y+6,outline=color,width=2,tags=tag)

    def preview(self):
        self.proposal=[];self.apply_button.configure(state='disabled')
        try:
            if not self.current():raise ValueError('Document changed. Close this dialog and start again.')
            if self.points_only:
                proposal=propose_near_point_repair(self.project.entities,self.selected,tolerance=float(self.tolerance.get()))
            else:
                proposal=propose_contour_repair(self.project.entities,self.selected,
                    tolerance=float(self.tolerance.get()),units=self.project.units,cleanup=self.cleanup.get(),
                    backtracks=self.backtracks.get(),join_endpoints=not self.backtracks_only,
                    endpoint_context=self.project.entities if self.opening_review else None)
            _,disabled,removed=apply_contour_repair(self.project,proposal)
            self.proposal=proposal
            if not proposal:
                self.summary.set('No near-duplicate vertices found at this tolerance.' if self.points_only else
                                 'Nothing to repair at this tolerance. Select matching open pieces, or inspect the endpoints.')
            else:
                closed=sum(p.entity.closed for p in proposal)
                names=[' + '.join(object_identity_label(self.project,i) for i in p.source_ids) for p in proposal[:4]]
                self.summary.set(f'{sum(len(p.source_ids) for p in proposal)} source object(s) → {len(proposal)} contour(s): '
                    f'{closed} closed, {len(proposal)-closed} still open.\n'
                    f'{sum(len(p.joins) for p in proposal)} endpoint join(s); {sum(p.removed_points for p in proposal)} point(s) removed '
                    f'({sum(p.backtracks_removed for p in proposal)} microscopic backtrack(s)). '
                    f'{"Maximum point displacement" if self.points_only else "Largest gap"}: {max(p.maximum_error for p in proposal):.6g} {self.project.units}.\n'
                    + '; '.join(names) + (' …' if len(proposal)>4 else '') + '\n'
                    + (f'WARNING: {sum(p.remaining_issues for p in proposal)} crossing/overlap issue(s) remain; use Inspect Geometry.\n'
                       if any(p.remaining_issues for p in proposal) else '')
                    +f'{len(disabled)} ambiguous toolpath association(s) disabled; {len(removed)} generated dependent(s) removed. '
                    'Other machining settings/associations are preserved; paths must regenerate. '
                    'Each result keeps its first source ID/layer/appearance. Undo restores the originals.')
                self.apply_button.configure(state='normal')
        except (ValueError,TypeError,OverflowError) as exc:self.summary.set(str(exc))
        self.draw()
