"""Explicit prototype setup; never silently replaces an occupied turret tool."""
import copy
import tkinter as tk
from tkinter import ttk,messagebox
from .model import DragKnifeOperation
from .units import unit_scale

FIELDS=(('Blade trailing offset','blade_offset'),('Final penetration below surface','target_depth'),
        ('Swivel penetration below surface','swivel_depth'),('Alignment lead in waste','alignment_length'),
        ('Rapid approach above surface','approach_height'),('Safe Z','safe_z'),
        ('Curve tolerance','curve_tolerance'),('Cut feed / min','feed_rate_override'),
        ('Z feed / min','plunge_rate_override'),('Lift at turns ≥ degrees','swivel_angle'),('Depth passes','passes'))


class DragKnifeDialog(tk.Toplevel):
    def __init__(self,parent,operation=None):
        super().__init__(parent);self.parent_app=parent;self.operation=operation;self.result=None
        self.title('Drag Knife — passive swivel prototype');self.transient(parent);self.grab_set()
        self.resizable(False,False)
        factor=unit_scale('mm',parent.project.units)
        self.vars={}
        frame=ttk.Frame(self,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Drag Knife — separate from diamond engraving',font=('Segoe UI',11,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Label(frame,text='Configure a Drag Knife tool in T8 with spindle disabled and RPM blank.\nThis dialog does not replace your engraver or change turret assignments.',wraplength=580).grid(row=1,column=0,columnspan=2,sticky='w',pady=8)
        defaults={'blade_offset':4*factor,'target_depth':'','swivel_depth':'',
                  'alignment_length':12*factor,'approach_height':1*factor,
                  'safe_z':max(6*factor,float(parent._job_setup()['safe_z'])),
                  'curve_tolerance':.025*factor,'feed_rate_override':'','plunge_rate_override':'',
                  'swivel_angle':20,'passes':1}
        for row,(label,key) in enumerate(FIELDS,2):
            value=getattr(operation,key) if operation else defaults[key]
            if operation and key.endswith('rate_override') and value is not None:value*=60
            self.vars[key]=tk.StringVar(value='' if value is None else str(value))
            suffix='' if key in ('swivel_angle','passes') else f' ({parent.project.units})'
            ttk.Label(frame,text=label+suffix).grid(row=row,column=0,sticky='w',pady=3,padx=(0,12))
            ttk.Entry(frame,textvariable=self.vars[key],width=20).grid(row=row,column=1,sticky='ew',pady=3)
        self.reverse=tk.BooleanVar(value=operation.reverse_paths if operation else False)
        self.review=tk.BooleanVar(value=False)
        row=len(FIELDS)+2
        ttk.Checkbutton(frame,text='Reverse contour direction',variable=self.reverse).grid(row=row,column=0,columnspan=2,sticky='w')
        ttk.Label(frame,text='DK1 prototype: 4 mm nominal offset; 6 mm tip-to-holder clearance; 95.71 mm rotating envelope.\nDepths and feeds are deliberately blank until chosen for your material. Positive swivel depth keeps the tip engaged.\nAlignment leads extend backward from each contour start and must lie in waste. The preview shows the holder pivot path, not the blade-tip outline.',wraplength=580,foreground='#9c4f00').grid(row=row+1,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Checkbutton(frame,text='I reviewed the waste leads, holder/clamp clearance and spindle-disabled T8 setup',variable=self.review).grid(row=row+2,column=0,columnspan=2,sticky='w')
        buttons=ttk.Frame(frame);buttons.grid(row=row+3,column=0,columnspan=2,sticky='e')
        ttk.Button(buttons,text='Cancel',command=self.destroy).pack(side='right')
        ttk.Button(buttons,text='Save / Generate',command=self.accept).pack(side='right',padx=6)

    def accept(self):
        from .drag_knife import validate_knife_setup
        try:
            values={key:(int(var.get()) if key=='passes' else float(var.get())) for key,var in self.vars.items()}
            for key in ('feed_rate_override','plunge_rate_override'):values[key]/=60
            op=copy.deepcopy(self.operation) if self.operation else DragKnifeOperation('Drag Knife — prototype',8,[],1,1)
            for key,value in values.items():setattr(op,key,value)
            op.reverse_paths=self.reverse.get();op.setup_verified=self.review.get()
            validate_knife_setup(self.parent_app.project,op)
        except (ValueError,TypeError) as exc:
            messagebox.showerror('Drag Knife',str(exc),parent=self);return
        self.result=op;self.destroy()


def configure_drag_knife(app,operation=None):
    original_project=app.project
    document_token=(app.document_id,app.document_revision)
    selected=[e.id for e in app.project.entities if e.id in app.selected_ids]
    if operation is None and not selected:
        messagebox.showinfo('Drag Knife','Select the contours to knife cut first.',parent=app);return
    tool=app.project.tool_library.by_station(operation.tool_station if operation else 8)
    if tool is None or tool.kind.value!='drag_knife':
        messagebox.showinfo('Drag Knife','Set up a Drag Knife tool in Tool Library / Turret T8 first: spindle disabled, RPM blank. Your current tool assignments have not been changed.',parent=app);return
    dialog=DragKnifeDialog(app,operation);app.wait_window(dialog)
    if dialog.result is None:return
    if app.project is not original_project or (app.document_id,app.document_revision)!=document_token:
        app.status.set('Document changed; reopen Drag Knife to edit the current selection.');return
    op=dialog.result
    if operation is None:op.entity_ids=selected
    candidate=copy.deepcopy(app.project)
    if operation is None:candidate.operations.append(op)
    else:candidate.operations=[op if item.id==op.id else item for item in candidate.operations]
    try:path=app._compute_cut_generation(candidate,[op])[op.id]
    except ValueError as exc:
        messagebox.showerror('Drag Knife',str(exc),parent=app);return
    if app.project is not original_project or (app.document_id,app.document_revision)!=document_token:
        app.status.set('Document changed; knife generation was not applied.');return
    app.project.operations=candidate.operations
    app.toolpath_previews[op.id]=path;app._mark_toolpath_previews_current()
    app.selected_operation_ids={op.id};app._set_dirty(True);app._redraw()
    app._record_history('Edit drag knife' if operation else 'Create drag knife','KN')
    app.status.set('Knife pivot paths generated. Review alignment leads and test on scrap before production.')
