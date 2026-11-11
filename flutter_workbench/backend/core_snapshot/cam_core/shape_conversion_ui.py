"""Modal preview for explicit geometry replacement; no edits before Apply."""
import copy
import math
import tkinter as tk
from tkinter import ttk

from .layout import entity_bounds
from .model import Circle
from .object_identity import object_identity_label
from .shape_conversion import propose_shape_conversion,apply_shape_conversion,path_points
from .units import unit_scale


class ShapeConversionDialog(tk.Toplevel):
    MODES={'Repair / recognize circle':'repair','Circle from bounds':'circle',
           'Rectangle from bounds':'rectangle','Editable outlines':'outline'}
    SCOPES={'Connected pieces → separate shapes':'connected','Each object separately':'each',
            'Entire selection → one shape':'whole'}

    def __init__(self,parent,*,outlines=False):
        super().__init__(parent)
        self.title('Convert to Editable Outlines' if outlines else 'Convert to Shape')
        self.geometry('820x690');self.minsize(620,530)
        self.parent=parent;self.source_project=parent.project
        self.document_id=parent.document_id;self.revision=parent.document_revision
        self.project=copy.deepcopy(parent.project);self.selected=set(parent.selected_ids)
        self.source_payload=self.project.to_dict();self.result=None;self.proposal=[]
        self.mode=tk.StringVar(self,value='Editable outlines' if outlines else 'Repair / recognize circle')
        self.scope=tk.StringVar(self,value='Each object separately' if outlines else 'Connected pieces → separate shapes')
        self.tolerance=tk.StringVar(self,value=f'{.001*unit_scale("in",self.project.units):g}')
        self.diameter=tk.StringVar(self);self.width=tk.StringVar(self);self.height=tk.StringVar(self)
        form=ttk.Frame(self,padding=12);form.pack(fill='x')
        ttk.Label(form,text='Convert to Editable Outlines' if outlines else 'Convert to Shape',font=('',12,'bold')).grid(row=0,column=0,columnspan=4,sticky='w')
        ttk.Label(form,text='Preview first. Originals are replaced only on Apply; Undo restores them.').grid(row=1,column=0,columnspan=4,sticky='w',pady=(4,10))
        for row,(label,var,values) in enumerate((('Target',self.mode,list(self.MODES)[:-1] if not outlines else ['Editable outlines']),
                                               ('Scope',self.scope,list(self.SCOPES))),start=2):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w')
            ttk.Combobox(form,textvariable=var,values=values,state='disabled' if outlines else 'readonly',width=40).grid(row=row,column=1,columnspan=3,sticky='ew')
        units=self.project.units
        self.entries={}
        for row,(label,var) in enumerate(((f'Join / outline tolerance ({units})',self.tolerance),
                                           (f'Circle diameter ({units}, blank = auto)',self.diameter),
                                           (f'Rectangle width ({units}, blank = auto)',self.width),
                                           (f'Rectangle height ({units}, blank = auto)',self.height)),start=4):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w')
            entry=ttk.Entry(form,textvariable=var,width=16);entry.grid(row=row,column=1,sticky='w')
            self.entries[str(var)]=entry
            if outlines and var is not self.tolerance:entry.configure(state='disabled')
        self.help=ttk.Label(form,wraplength=750)
        self.help.grid(row=8,column=0,columnspan=4,sticky='w',pady=8)
        ttk.Button(form,text='Preview Conversion',command=self.preview).grid(row=9,column=0,sticky='w')
        form.columnconfigure(1,weight=1)
        footer=ttk.Frame(self,padding=10);footer.pack(side='bottom',fill='x')
        ttk.Button(footer,text='Cancel',command=self.destroy).pack(side='right')
        self.apply_button=ttk.Button(footer,text='Apply Conversion',command=self.accept,state='disabled')
        self.apply_button.pack(side='right',padx=8)
        self.summary=tk.StringVar(self)
        ttk.Label(self,textvariable=self.summary,wraplength=760,padding=(12,4)).pack(side='bottom',fill='x')
        self.canvas=tk.Canvas(self,background='white',height=280,highlightthickness=1)
        self.canvas.pack(fill='both',expand=True,padx=12,pady=4)
        self.canvas.bind('<Configure>',lambda event:self.draw())
        for var in (self.mode,self.scope,self.tolerance,self.diameter,self.width,self.height):
            var.trace_add('write',lambda *args:self.invalidate())
        self.protocol('WM_DELETE_WINDOW',self.destroy);self.bind('<Escape>',lambda event:self.destroy())
        self.transient(parent);self.grab_set();self.invalidate();self.after_idle(self.preview)

    def current(self):
        return (self.parent.project is self.source_project and self.parent.document_id==self.document_id
                and self.parent.document_revision==self.revision and self.parent.project.to_dict()==self.source_payload)

    def invalidate(self):
        self.proposal=[];self.apply_button.configure(state='disabled')
        mode=self.MODES[self.mode.get()]
        for var,enabled in ((self.diameter,mode=='circle'),(self.width,mode=='rectangle'),(self.height,mode=='rectangle')):
            self.entries[str(var)].configure(state='normal' if enabled else 'disabled')
        self.help.configure(text={
            'repair':'Joins endpoint-connected pieces and fits a complete circle. Radial/chord tolerance: the larger of join tolerance or 0.5% radius. Partial arcs, branches and noncircular paths are not repaired. Size overrides do not apply.',
            'circle':'Deliberate reshaping, not recognition. Center stays at bounds center. Automatic diameter uses the smaller bounds dimension; enter a diameter to override.',
            'rectangle':'Deliberate reshaping to an axis-aligned rectangle centered on the bounds. Blank sizes retain bounds width/height.',
            'outline':'Turns circles, rectangles and lines into editable polylines. Circle chord error stays within tolerance. Existing polylines stay unchanged.',
        }[mode])
        self.summary.set('Settings changed. Preview again before applying.');self.draw()

    def preview(self):
        self.proposal=[];self.apply_button.configure(state='disabled')
        try:
            if not self.current():raise ValueError('Document changed. Close this dialog and start again.')
            mode=self.MODES[self.mode.get()]
            optional=lambda var:float(var.get()) if var.get().strip() else None
            proposal=propose_shape_conversion(self.project.entities,self.selected,mode=mode,
                scope=self.SCOPES[self.scope.get()],units=self.project.units,tolerance=float(self.tolerance.get()),
                diameter=optional(self.diameter) if mode=='circle' else None,
                width=optional(self.width) if mode=='rectangle' else None,
                height=optional(self.height) if mode=='rectangle' else None)
            candidate,disabled,removed=apply_shape_conversion(self.project,proposal)
            self.proposal=proposal
            names=[object_identity_label(self.project,p.source_ids[0]) for p in proposal]
            details=[]
            for p in proposal[:4]:
                if isinstance(p.entity,Circle):
                    details.append(f'Ø {p.entity.radius*2:.6g}, center {p.entity.center.x:.6g}, {p.entity.center.y:.6g}')
                else:
                    box=entity_bounds(p.entity);details.append(f'{box[2]-box[0]:.6g} × {box[3]-box[1]:.6g}')
            self.summary.set(f'{sum(len(p.source_ids) for p in proposal)} source object(s) → {len(proposal)} shape(s). '
                + ', '.join(names[:5]) + (' …' if len(names)>5 else '') + '\n'
                + '; '.join(details) + f' {self.project.units}. Blue = result; gray = source. '
                + f'Max repair deviation: {max((p.maximum_error for p in proposal),default=0):.6g}.\n'
                + f'{len(disabled)} affected toolpath(s) disabled for reassociation; {len(removed)} generated dependent(s) removed. '
                + 'Merged shapes keep the first source ID, layer and appearance; Undo restores all originals.'
                if proposal else 'Nothing to convert; selected objects already have this representation.')
            self.apply_button.configure(state='normal' if proposal else 'disabled')
        except (ValueError,TypeError,OverflowError) as exc:
            self.summary.set(str(exc))
        self.draw()

    def draw(self):
        if not hasattr(self,'canvas'):return
        self.canvas.delete('all')
        sources=[e for e in self.project.entities if e.id in self.selected]
        outputs=[p.entity for p in self.proposal]
        boxes=[b for e in sources+outputs if (b:=entity_bounds(e)) is not None and all(math.isfinite(v) for v in b)]
        if not boxes:return
        x1=min(b[0] for b in boxes);y1=min(b[1] for b in boxes)
        x2=max(b[2] for b in boxes);y2=max(b[3] for b in boxes)
        width=max(100,self.canvas.winfo_width());height=max(80,self.canvas.winfo_height())
        scale=min((width-32)/max(x2-x1,1e-9),(height-32)/max(y2-y1,1e-9))
        def xy(x,y):return width/2+(x-(x1+x2)/2)*scale,height/2-(y-(y1+y2)/2)*scale
        for objects,color,thickness in ((sources,'#969696',1),(outputs,'#0075cf',2)):
            for entity in objects:
                if isinstance(entity,Circle):
                    c=entity.center;r=entity.radius
                    self.canvas.create_oval(*xy(c.x-r,c.y+r),*xy(c.x+r,c.y-r),outline=color,width=thickness)
                else:
                    try:points,closed=path_points(entity,.001*unit_scale('in',self.project.units))
                    except ValueError:continue
                    if closed and points:points=points+[points[0]]
                    if len(points)>1:self.canvas.create_line(*[v for p in points for v in xy(p.x,p.y)],fill=color,width=thickness)

    def accept(self):
        if not self.current():
            self.proposal=[];self.apply_button.configure(state='disabled')
            self.summary.set('Document changed. Close this dialog and start again.');return
        if self.proposal:
            self.result=self.proposal;self.destroy()
