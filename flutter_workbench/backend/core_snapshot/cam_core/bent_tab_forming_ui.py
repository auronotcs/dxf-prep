"""Bent Tabs prototype controls; host's existing placement/apply services own geometry."""
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from pathlib import Path
from .bent_tab_forming import (BentTabForming,LEGACY_MATERIAL,POLY_MATERIAL,FORMING_METHODS,
                              EXPLICIT_ALLOWANCE,TEST_ALLOWANCE,SHOP_ALLOWANCE,NEUTRAL_DATUM,OVERALL_DATUM,coupon_project)
from .bent_tab_test_defaults import initial_values,estimated_bend,shop_correction,thickness_label,THICKNESS_CHOICES,CUSTOM_THICKNESS

FIELDS=(('thickness','Sheet thickness (in)','Actual coupon stock thickness; unverified until measured.'),
 ('inside_radius','Inside bend radius (in)','Proposed inside radius, not a qualified material minimum.'),
 ('angle_degrees','Bend angle (deg)','Rotation from the flat back; 90 degrees is a right-angle tab.'),
 ('bend_allowance','Bend allowance (in)','Explicit developed length of the bend zone. No metal K-factor is assumed.'),
 ('finished_standoff','Finished standoff (in)','Normal distance from the original neutral-axis plane at the bend-start tangent to the free tab tip; not outside-face cabinet depth.'),
 ('hole_diameter','Tab hole diameter (in)','0 omits holes. A positive value adds one round mounting hole to each tab.'),
 ('hole_tip_offset','Tip to hole center (in)','Distance inward from the free tab end to the hole center. Ignored when hole diameter is 0; the hole must clear the bend zone and cut edges.'),
 ('flat_length_adjustment','Flat-length correction (in)','Signed change to the previous flat layout: negative shortens the tab. The 4.5 mm / 90 degree heat-forming shop test uses -0.500 in. Other estimates use 0. Not a certified bend model.'))

def initialize(app):
    saved=app.settings.get('mounting',{}).get('bent_poly_prototype',{})
    values,automatic=initial_values(saved)
    app.bent_material_var=tk.StringVar(value=LEGACY_MATERIAL)
    app.bent_forming_vars={key:tk.StringVar(value=values[key]) for key,_label,_tip in FIELDS}
    app.bent_forming_vars['method']=tk.StringVar(value=values['method'])
    app.bent_forming_vars['standoff_datum']=tk.StringVar(value=values['standoff_datum'])
    app.bent_test_estimate_var=tk.BooleanVar(value=automatic)
    app.bent_thickness_choice_var=tk.StringVar(value=thickness_label(values['thickness']))
    app.bent_forming_entries={}
    app.bent_loaded_forming=None
    app._bent_last_material=LEGACY_MATERIAL
    app.bent_forming_status_var=tk.StringVar()

def fill_placement_blanks(app):
    # Existing nonblank shop layout choices remain authoritative. Fresh setups
    # also need the ordinary tab fields before manual placement can run.
    for name,value in (('bent_tab_width_var','1'),('bent_projection_var','1'),
                       ('bent_root_radius_var','.125'),('bent_tip_radius_var','.0625'),
                       ('bent_corner_clearance_var','.25'),('bent_short_edge_center_var','4')):
        variable=getattr(app,name,None)
        if variable is not None and not variable.get().strip():variable.set(value)

def load_test_defaults(app):
    # Explicit reset action only; hydration never invokes this.
    values,_=initial_values()
    app._bent_forming_updating=True
    try:
        app.bent_loaded_forming=None
        fill_placement_blanks(app)
        for key,value in values.items():app.bent_forming_vars[key].set(value)
        app.bent_test_estimate_var.set(True)
        app.bent_material_var.set(POLY_MATERIAL)
    finally:app._bent_forming_updating=False
    refresh(app)

def choose_thickness(app):
    value=THICKNESS_CHOICES.get(app.bent_thickness_choice_var.get())
    if value is not None:app.bent_forming_vars['thickness'].set(f'{value:.12g}')

def number(app,key):
    try:return float(app.bent_forming_vars[key].get())
    except ValueError as exc:
        label=next(label for name,label,_ in FIELDS if name==key)
        raise ValueError(f'Enter a number for {label}.') from exc

def read(app):
    if app.bent_material_var.get()!=POLY_MATERIAL:return None
    factor=app._sheet_unit_scale()
    automatic=app.bent_test_estimate_var.get()
    values={key:number(app,key) for key in ('thickness','angle_degrees','finished_standoff','hole_diameter')}
    values['hole_tip_offset']=number(app,'hole_tip_offset') if values['hole_diameter'] else 0.
    if automatic:
        values['inside_radius'],values['bend_allowance']=estimated_bend(values['thickness'],values['angle_degrees'])
        values['flat_length_adjustment']=shop_correction(values['thickness'],values['angle_degrees'],app.bent_forming_vars['method'].get())
    else:
        for key in ('inside_radius','bend_allowance','flat_length_adjustment'):values[key]=number(app,key)
    corrected=automatic and values['flat_length_adjustment']!=0
    datum=(OVERALL_DATUM if corrected else NEUTRAL_DATUM) if automatic else app.bent_forming_vars['standoff_datum'].get()
    values={key:value*(1 if key=='angle_degrees' else factor) for key,value in values.items()}
    source=(SHOP_ALLOWANCE if corrected else TEST_ALLOWANCE) if automatic else EXPLICIT_ALLOWANCE
    loaded=app.bent_loaded_forming
    if not automatic and loaded and all(values[key]==loaded.get(key) for key in
                                       ('thickness','inside_radius','angle_degrees','bend_allowance','flat_length_adjustment')):
        source=loaded.get('allowance_source',EXPLICIT_ALLOWANCE)
    plan=BentTabForming(**values,method=app.bent_forming_vars['method'].get(),allowance_source=source,standoff_datum=datum)
    plan.flat_projection()
    return plan

def refresh(app):
    if getattr(app,'_bent_forming_updating',False):return
    poly=app.bent_material_var.get()==POLY_MATERIAL
    app.bent_flat_projection_entry.configure(state='disabled' if poly else 'normal')
    if poly:app.bent_forming_frame.grid()
    else:app.bent_forming_frame.grid_remove()
    try:
        app._bent_forming_updating=True
        if poly and app._bent_last_material!=POLY_MATERIAL:fill_placement_blanks(app)
        app._bent_last_material=app.bent_material_var.get()
        app.bent_thickness_choice_var.set(thickness_label(app.bent_forming_vars['thickness'].get()))
        automatic=app.bent_test_estimate_var.get()
        for key in ('inside_radius','bend_allowance','flat_length_adjustment'):
            app.bent_forming_entries[key].configure(state='disabled' if automatic else 'normal')
        try:has_holes=number(app,'hole_diameter')!=0
        except ValueError:has_holes=True
        app.bent_forming_entries['hole_tip_offset'].configure(state='normal' if has_holes else 'disabled')
        if automatic and poly:
            radius,allowance=estimated_bend(number(app,'thickness'),number(app,'angle_degrees'))
            app.bent_forming_vars['inside_radius'].set(f'{radius:.12g}')
            app.bent_forming_vars['bend_allowance'].set(f'{allowance:.12g}')
            correction=shop_correction(number(app,'thickness'),number(app,'angle_degrees'),app.bent_forming_vars['method'].get())
            app.bent_forming_vars['flat_length_adjustment'].set(f'{correction:g}')
            app.bent_forming_vars['standoff_datum'].set(OVERALL_DATUM if correction else NEUTRAL_DATUM)
        plan=read(app)
        app.bent_standoff_label.configure(text=('Overall standoff (in)' if plan and plan.standoff_datum==OVERALL_DATUM else 'Neutral-axis standoff (in)'))
        text=(f'TEST ONLY • flat tab projection {plan.flat_projection()/app._sheet_unit_scale():.4f} in. '
              + ('4.5 mm structural-back shop test: 0.500 in shorter than the prior layout. Target is overall projection from the back surface to the tab tip; not the inside-face measurement. '
                 if plan.allowance_source==SHOP_ALLOWANCE else
                 'Uncalibrated estimate: radius = 3 × thickness; neutral-axis standoff. ' if automatic else 'Custom / preserved bend values. ')
              + 'Confirm fit on a sample bend; not a qualified forming recipe.'
              if plan else 'Existing flat layout unchanged; no forming allowance is assumed.')
    except ValueError as exc:text=str(exc)
    finally:app._bent_forming_updating=False
    app.bent_forming_status_var.set(text)
    app._refresh_bent_tab_guidance()

def build(app,parent):
    header=ttk.Frame(parent);header.grid(row=18,column=0,columnspan=4,sticky='ew',pady=(6,0))
    ttk.Label(header,text='Material / forming').pack(side='left')
    combo=ttk.Combobox(header,textvariable=app.bent_material_var,
        values=(LEGACY_MATERIAL,POLY_MATERIAL),state='readonly',width=34)
    combo.pack(side='left',padx=5)
    form=ttk.LabelFrame(parent,text='Clear-poly prototype — no qualified forming recipe',padding=5)
    app.bent_forming_frame=form
    form.grid(row=19,column=0,columnspan=4,sticky='ew',pady=3)
    preset=ttk.Frame(form);preset.grid(row=0,column=0,columnspan=4,sticky='ew',pady=(0,5))
    ttk.Label(preset,text='Thickness preset').pack(side='left')
    picker=ttk.Combobox(preset,textvariable=app.bent_thickness_choice_var,
        values=(*THICKNESS_CHOICES,CUSTOM_THICKNESS),state='readonly',width=23)
    picker.pack(side='left',padx=4);picker.bind('<<ComboboxSelected>>',lambda _:choose_thickness(app))
    ttk.Button(preset,text='Load 4.5 mm Shop Test',command=lambda:load_test_defaults(app)).pack(side='right')
    for index,(key,label,tip) in enumerate(FIELDS):
        row,column=divmod(index,2);row+=1
        name=ttk.Label(form,text=label);name.grid(row=row,column=column*2,sticky='w',padx=3,pady=2)
        if key=='finished_standoff':
            app.bent_standoff_label=name
            tip='Shop test: overall projection from the back surface to the free tab tip, not the interior face. Uncalibrated/legacy layouts retain the original neutral-axis datum; see setup status below.'
        entry=ttk.Entry(form,textvariable=app.bent_forming_vars[key],width=10)
        entry.grid(row=row,column=column*2+1,sticky='ew')
        app.bent_forming_entries[key]=entry
        app._attach_tooltip(name,tip);app._attach_tooltip(entry,tip)
    ttk.Checkbutton(form,text='Automatic test setup (uncheck for custom / preserved values)',
        variable=app.bent_test_estimate_var).grid(row=5,column=0,columnspan=4,sticky='w',pady=3)
    ttk.Label(form,text='Forming method').grid(row=6,column=0,sticky='w')
    ttk.Combobox(form,textvariable=app.bent_forming_vars['method'],values=FORMING_METHODS,
        state='readonly',width=25).grid(row=6,column=1,columnspan=3,sticky='ew')
    ttk.Label(form,text='Hole diameter 0 = no tab hole. Tip offset measures to the hole center.\n'
              'These controls do not change your current job stock; confirm thickness in Manufacturing.',
        wraplength=420).grid(row=7,column=0,columnspan=4,sticky='w')
    ttk.Label(form,textvariable=app.bent_forming_status_var,wraplength=420,
        foreground='#a04b00').grid(row=8,column=0,columnspan=4,sticky='w',pady=3)
    ttk.Button(form,text='Preview / Save Isolated Coupon',command=lambda:preview_coupon(app)).grid(
        row=9,column=0,columnspan=4,sticky='w')
    for variable in (app.bent_material_var,app.bent_test_estimate_var,*app.bent_forming_vars.values()):
        variable.trace_add('write',lambda *_:refresh(app))
    refresh(app)

def signature(app):
    return (app.bent_material_var.get(),app.bent_test_estimate_var.get(),*(var.get() for var in app.bent_forming_vars.values()))

def hydrate(app,owner):
    data=owner.metadata.get('bent_tab_forming')
    app._bent_forming_updating=True
    try:
        app.bent_material_var.set(POLY_MATERIAL if data else LEGACY_MATERIAL)
        if data:
            data={**data,'flat_length_adjustment':data.get('flat_length_adjustment',0.0),
                  'standoff_datum':data.get('standoff_datum',NEUTRAL_DATUM)}
            app.bent_loaded_forming=dict(data)
            # Existing applied parts keep their exact numbers, even if a new
            # estimate formula is introduced later. Re-estimation is explicit.
            app.bent_test_estimate_var.set(False)
            factor=app._sheet_unit_scale()
            for key,var in app.bent_forming_vars.items():
                value=data.get(key,FORMING_METHODS[0] if key=='method' else '')
                if key not in ('method','angle_degrees','standoff_datum') and value!='':value=float(value)/factor
                var.set(str(value))
    finally:app._bent_forming_updating=False
    refresh(app)

def save_preferences(app):
    app.settings.setdefault('mounting',{})['bent_poly_prototype']={key:var.get() for key,var in app.bent_forming_vars.items()}
    app.settings['mounting']['bent_poly_prototype']['use_test_estimate']=app.bent_test_estimate_var.get()

def preview_coupon(app):
    try:project=coupon_project(app._bent_tab_geometry_settings(),app.project.units)
    except ValueError as exc:
        messagebox.showerror('Prototype Coupon',str(exc),parent=app);return
    window=tk.Toplevel(app);window.title('UNQUALIFIED clear-poly bend coupon')
    ttk.Label(window,text='Isolated coupon: review, save a separate job, then set stock/tools in Manufacturing.\n'
        'No forming temperature, thickness, strength or halo result is qualified.',padding=10).pack()
    canvas=tk.Canvas(window,width=560,height=350,background='white');canvas.pack()
    from .layout import drawing_bounds
    from .model import Circle,Polyline
    entities=[e for e in project.entities if not e.metadata.get('bent_tab_source_reference')]
    bounds=drawing_bounds(entities);scale=min(500/(bounds[2]-bounds[0]),290/(bounds[3]-bounds[1]))
    def point(p):return (30+(p.x-bounds[0])*scale,320-(p.y-bounds[1])*scale)
    for entity in entities:
        if isinstance(entity,Polyline):
            canvas.create_line(*[v for p in [*entity.points,entity.points[0]] for v in point(p)])
        elif isinstance(entity,Circle):
            x,y=point(entity.center);r=entity.radius*scale
            canvas.create_oval(x-r,y-r,x+r,y+r,outline='#e8590c')
        else:canvas.create_line(*point(entity.start),*point(entity.end),fill='#9c36b5',dash=(4,3))
    def save():
        name=filedialog.asksaveasfilename(parent=window,title='Save separate prototype coupon',
            defaultextension='.camjob.json',filetypes=(('CAM job','*.camjob.json'),))
        if name:
            try:project.save(Path(name))
            except OSError as exc:messagebox.showerror('Save Coupon',str(exc),parent=window);return
            window.destroy()
    ttk.Button(window,text='Save Separate Coupon Job…',command=save).pack(pady=8)
