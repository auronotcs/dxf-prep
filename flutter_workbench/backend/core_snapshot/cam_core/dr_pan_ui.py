"""Small controller for the DR Pans mounting workflow."""
import copy
import math
import tkinter as tk
from tkinter import messagebox, ttk

from .dr_pans import DrPanHoleSettings, DrPanSettings, dr_pan_bend_marks, generate_dr_pan
from .object_identity import object_identity_label
from .units import unit_scale


class DrPanWorkflow:
    def __init__(self, app):
        self.app = app
        mounting = app.settings.get("mounting", {})
        saved = mounting.get("dr_pan", {}) if isinstance(mounting,dict) else {}
        saved = saved if isinstance(saved,dict) else {}
        defaults = {"inside_lip":1.0625,"thickness":.063,"cutter_diameter":.25,
                    "inside_radius":"", "k_factor":.4,"corner_gap":.063}
        self.vars = {k:tk.StringVar(app, value=str(saved.get(k,v))) for k,v in defaults.items()}
        self.hole_vars = {k:tk.StringVar(app,value=str(saved.get(k,v))) for k,v in
                          {"hole_diameter":.125,"hole_max_spacing":24.,"hole_end_offset":2.}.items()}
        self.hole_flags = {k:tk.BooleanVar(app,value=saved.get(k,True)) for k in
                           ("holes_enabled","top","bottom","left","right")}
        self.marks_enabled = tk.BooleanVar(app,value=saved.get("marks_enabled",True))
        self.mark_offset = tk.StringVar(app,value=str(saved.get("mark_offset",0)))
        self.results = []
        self.marks = []
        self.project = None
        self.source_snapshots = {}
        self.settings_snapshot = None
        self.holes_snapshot = None
        self.marks_snapshot = None
        self.message = "Select face rectangle(s), Generate Pan, then Apply Pan."
        for var in (*self.vars.values(),*self.hole_vars.values(),*self.hole_flags.values(),self.marks_enabled,self.mark_offset):
            var.trace_add("write", self._changed)

    def build_form(self, parent):
        self.form = ttk.LabelFrame(parent, text="DR Pans — four bent sides", padding=8)
        self.form.columnconfigure(1, weight=1)
        ttk.Label(self.form, text="Rectangle = finished outside face. Lip = finished inside depth.\n"
                  "Bend compensation is an editable shop estimate.", wraplength=390).grid(
                      row=0,column=0,columnspan=2,sticky="w",pady=(0,8))
        for row,(key,label) in enumerate((("inside_lip","Finished inside lip (in)"),
                                          ("thickness","Material thickness (in)"),
                                          ("cutter_diameter","Actual router cutter Ø (in)")),1):
            ttk.Label(self.form,text=label).grid(row=row,column=0,sticky="w",pady=3)
            if key == "thickness":
                entry = ttk.Combobox(self.form,textvariable=self.vars[key],values=("0.063","0.080"),width=12)
            else:
                entry = ttk.Entry(self.form,textvariable=self.vars[key],width=14)
            entry.grid(row=row,column=1,sticky="ew",padx=(10,0))
        self.advanced = ttk.Frame(self.form)
        self.advanced.columnconfigure(1,weight=1)
        self.advanced_visible = tk.BooleanVar(self.app,False)
        ttk.Checkbutton(self.form,text="Advanced bend / corner settings",variable=self.advanced_visible,
                        command=self._toggle_advanced).grid(row=4,column=0,columnspan=2,sticky="w",pady=6)
        for row,(key,label) in enumerate((("inside_radius","Estimated inside radius (in; blank = thickness)"),
                                          ("k_factor","K-factor"),("corner_gap","Corner relief gap (in)"))):
            ttk.Label(self.advanced,text=label,wraplength=280).grid(row=row,column=0,sticky="w",pady=3)
            ttk.Entry(self.advanced,textvariable=self.vars[key],width=12).grid(row=row,column=1,sticky="ew",padx=(10,0))
        ttk.Label(self.form,text="Reliefs include cutter clearance; use the actual cutter size above.\n"
                  "Bend guides are reference-only, not cut lines.\n"
                  "Check the first bent sample; adjust Advanced settings if needed.",wraplength=390).grid(
                      row=6,column=0,columnspan=2,sticky="w",pady=(6,0))
        holes = ttk.LabelFrame(self.form,text="Mounting screw holes — folded lips",padding=6)
        holes.grid(row=7,column=0,columnspan=2,sticky="ew",pady=(8,0))
        holes.columnconfigure(1,weight=1)
        ttk.Checkbutton(holes,text="Add mounting holes",variable=self.hole_flags["holes_enabled"]).grid(
            row=0,column=0,columnspan=2,sticky="w")
        sides = ttk.Frame(holes)
        sides.grid(row=1,column=0,columnspan=2,sticky="w",pady=3)
        for side in ("top","bottom","left","right"):
            ttk.Checkbutton(sides,text=side.title(),variable=self.hole_flags[side]).pack(side="left",padx=(0,6))
        for row,(key,label) in enumerate((("hole_diameter","Drill-hole diameter (in)"),
                ("hole_max_spacing","Maximum spacing (in)"),("hole_end_offset","End offset (in)")),2):
            ttk.Label(holes,text=label).grid(row=row,column=0,sticky="w",pady=3)
            ttk.Entry(holes,textvariable=self.hole_vars[key],width=12).grid(row=row,column=1,sticky="ew",padx=(8,0))
        ttk.Label(holes,text="Evenly spaced; centered on the straight lip. Short lips get one centered hole.\n"
                  "Sides follow the rectangle axes closest to drawing X/Y.",wraplength=390).grid(
                      row=5,column=0,columnspan=2,sticky="w",pady=(5,0))
        ttk.Button(holes,text="Select Applied Holes for Drilling",command=self.prepare_holes).grid(
            row=6,column=0,columnspan=2,sticky="w",pady=(5,0))
        marks = ttk.LabelFrame(self.form,text="Brake alignment marks",padding=6)
        marks.grid(row=8,column=0,columnspan=2,sticky="ew",pady=(8,0))
        marks.columnconfigure(1,weight=1)
        ttk.Checkbutton(marks,text="Include engravable bend marks",variable=self.marks_enabled).grid(
            row=0,column=0,columnspan=2,sticky="w")
        ttk.Label(marks,text="Bend-mark offset (in)").grid(row=1,column=0,sticky="w",pady=3)
        ttk.Entry(marks,textvariable=self.mark_offset,width=12).grid(row=1,column=1,sticky="ew",padx=(8,0))
        ttk.Label(marks,text="0 = bend-zone center; + toward lip, − toward face.\n"
                  "Shifts marks only, not cuts or lip size. Choose engraving depth in Manufacturing.",wraplength=390).grid(
                      row=2,column=0,columnspan=2,sticky="w",pady=(4,0))
        ttk.Button(marks,text="Apply / Select Bend Marks for Engraving",command=self.prepare_marks).grid(
            row=3,column=0,columnspan=2,sticky="w",pady=(5,0))

    def _toggle_advanced(self):
        if self.advanced_visible.get():
            self.advanced.grid(row=5,column=0,columnspan=2,sticky="ew")
        else:
            self.advanced.grid_remove()

    def configure_workflow(self):
        a = self.app
        self.form.pack_forget()
        if a.mounting_workflow_var.get() != "DR Pans":
            if getattr(self,"tree_configured",False):
                a.mounting_tree.configure(displaycolumns="#all")
                a.mounting_tree.column("size",width=48)
                self.tree_configured = False
            return
        a.mounting_tree.configure(displaycolumns=("owner","kind","size"))
        a.mounting_tree.column("size",width=185)
        self.tree_configured = True
        self.form.pack(fill="x",pady=(8,6),before=a.mounting_summary_label)
        a.mounting_common_form.pack_forget()
        a.mounting_feature_editor.pack_forget()
        a.mounting_edit_actions.pack_forget()
        a.mounting_generate_button.configure(text="Generate Pan",state="normal")
        a.mounting_apply_button.configure(text="Apply Pan",state="normal")
        a.mounting_prepare_button.configure(text="Prepare Machining",state="normal")
        a.mounting_apply_tooltip.text = "Apply the preview as one undoable change; bend guides are reference-only."
        self.refresh_tree()

    def settings(self):
        try:
            values = {k:float(v.get()) for k,v in self.vars.items() if k != "inside_radius"}
            values["inside_radius"] = float(self.vars["inside_radius"].get() or values["thickness"])
        except ValueError:
            raise ValueError("Enter numeric pan dimensions (inches).") from None
        factor = unit_scale("in", self.app.project.units)
        settings = DrPanSettings(**{k:v if k == "k_factor" else v*factor for k,v in values.items()})
        settings.validate()
        return settings

    def hole_settings(self):
        enabled = bool(self.hole_flags["holes_enabled"].get())
        sides = {k:bool(self.hole_flags[k].get()) for k in ("top","bottom","left","right")}
        if not enabled:
            return DrPanHoleSettings(enabled=False,**sides)
        try:
            factor = unit_scale("in",self.app.project.units)
            values = {k.removeprefix("hole_"):float(v.get())*factor for k,v in self.hole_vars.items()}
        except ValueError:
            raise ValueError("Enter numeric mounting-hole dimensions (inches).") from None
        settings = DrPanHoleSettings(**values,**sides)
        settings.validate()
        return settings

    def mark_settings(self):
        if not self.marks_enabled.get():
            return False,0.
        try:
            value=float(self.mark_offset.get())*unit_scale("in",self.app.project.units)
        except ValueError:
            raise ValueError("Enter a numeric bend-mark offset (inches).") from None
        if not math.isfinite(value):
            raise ValueError("Bend-mark offset must be a finite number.")
        return True,value

    def _changed(self, *_):
        if self.results:
            self.clear()
            self.message = "Settings changed. Generate Pan again before applying."
            if hasattr(self.app,"canvas"):
                self.app.canvas.delete("dr_pan_preview")
                self.refresh_tree()

    def clear(self):
        self.results = []
        self.marks = []
        self.project = None
        self.source_snapshots = {}
        self.settings_snapshot = None
        self.holes_snapshot = None
        self.marks_snapshot = None
        self.message = "Select face rectangle(s), Generate Pan, then Apply Pan."

    def current(self):
        a = self.app
        if not self.results or self.project is not a.project:
            return False
        sources = {e.id:e.to_dict() for e in a.project.entities if e.id in self.source_snapshots}
        return (sources == self.source_snapshots and self.settings() == self.settings_snapshot
                and self.hole_settings() == self.holes_snapshot and self.mark_settings() == self.marks_snapshot)

    def preview(self):
        a = self.app
        self.clear()
        try:
            settings = self.settings()
            hole_settings = self.hole_settings()
            mark_settings = self.mark_settings()
            selected = [e for e in a.project.entities if e.id in a.selected_ids]
            if not selected:
                raise ValueError("Select one or more finished face rectangles first.")
            if a._unlocked_ids({e.id for e in selected}) != {e.id for e in selected}:
                raise ValueError("Unlock the selected face rectangles before making pans.")
            results = [generate_dr_pan(e, settings, hole_settings) for e in selected]
            marks = [line for r in results for line in dr_pan_bend_marks(r.outline,r.references,mark_settings[1])] if mark_settings[0] else []
        except ValueError as exc:
            a.canvas.delete("dr_pan_preview")
            self.refresh_tree()
            messagebox.showerror("DR Pans",str(exc),parent=a)
            return
        self.project = a.project
        self.source_snapshots = {e.id:copy.deepcopy(e.to_dict()) for e in selected}
        self.settings_snapshot = settings
        self.holes_snapshot = hole_settings
        self.marks_snapshot = mark_settings
        self.marks = marks
        self.results = results
        self.message = f"Generated {len(results)} pan(s), {sum(len(r.holes) for r in results)} drill hole(s), {len(marks)} bend mark(s). Review, then Apply Pan to change the job."
        self.refresh_tree()
        a._redraw()

    def refresh_tree(self):
        a = self.app
        tree = a.mounting_tree
        tree.delete(*tree.get_children())
        a.mounting_summary_var.set(self.message)
        factor = unit_scale(a.project.units,"in")
        if self.results and self.project is a.project:
            for r in self.results:
                tree.insert("","end",values=(object_identity_label(a.project,r.outline,include_number_with_name=True),
                    "Pan blank","—","—",f"{r.blank_width*factor:.3f} × {r.blank_height*factor:.3f} in",r.outline.layer))
                for side in ("top","bottom","left","right"):
                    holes = [h for h in r.holes if h.metadata["dr_pan_side"] == side]
                    if holes:
                        tree.insert("","end",values=(side.title(),f"{len(holes)} drill holes","—","—",
                            f"Ø {holes[0].radius*2*factor:g} in",holes[0].layer))
        a.mounting_detail_var.set("Dashed outline: flat blank. Bend-zone centers: reference only. Compensation is uncalibrated.")

    def draw(self):
        a = self.app
        if a.workspace_var.get() != "mounting" or not self.current():
            return
        for result in self.results:
            a._draw_entity(result.outline,color_override="#087f8c",width_override=2,dash=(6,3),
                           tags_override=("mounting_preview","dr_pan_preview"))
            for hole in result.holes:
                a._draw_entity(hole,color_override="#1971c2",width_override=2,
                               tags_override=("mounting_preview","dr_pan_preview"))
            for guide in result.references:
                a._draw_entity(guide,color_override="#9c36b5",width_override=1,dash=(4,3),
                               tags_override=("mounting_preview","dr_pan_preview"))
        for mark in self.marks:
            a._draw_entity(mark,color_override="#e67700",width_override=2,
                           tags_override=("mounting_preview","dr_pan_preview"))

    def apply(self):
        a = self.app
        try:
            if not self.current():
                raise ValueError("Generate Pan again: no current preview for this document and geometry.")
            ids = set(self.source_snapshots)
            if a._unlocked_ids(ids) != ids:
                raise ValueError("Unlock the previewed rectangles before applying.")
        except ValueError as exc:
            messagebox.showinfo("DR Pans",str(exc),parent=a)
            return
        replacements = {r.outline.id:r.outline for r in self.results}
        refs = [g for r in self.results for g in r.references]
        holes = [h for r in self.results for h in r.holes]
        a.project.entities = [replacements.get(e.id,e) for e in a.project.entities] + holes + refs + self.marks
        a.selected_ids = set(replacements)
        a.settings.setdefault("mounting",{})["dr_pan"] = {
            k:v.get() for k,v in {**self.vars,**self.hole_vars,**self.hole_flags,
                                 "marks_enabled":self.marks_enabled,"mark_offset":self.mark_offset}.items()}
        a.settings["mounting"]["workflow"] = "DR Pans"
        a._save_settings()
        count = len(replacements)
        self.clear()
        self.message = f"Applied {count} pan(s), {len(holes)} drill hole(s). Use the separate outline, drilling and bend-mark machining actions."
        self.refresh_tree()
        a._refresh_layer_choices()
        a._finish_geometry_transform("Apply DR pan flat layouts","MT")

    def prepare(self):
        a = self.app
        pan_entities = [e for e in a.project.entities if e.metadata.get("dr_pan") and not e.metadata.get("reference_only")]
        pans = {e.id for e in pan_entities}
        if not pans:
            messagebox.showinfo("DR Pans","Apply a pan before preparing machining.",parent=a)
            return
        groups = {e.metadata.get("group_id") for e in a.project.entities if e.id in a.selected_ids}-{None}
        selected = {e.id for e in pan_entities if e.id in a.selected_ids or e.metadata.get("group_id") in groups}
        a.selected_ids = selected or pans
        a._switch_workspace("manufacturing")
        a._redraw()
        a.status.set("Pan outlines selected for Outside cuts. Bend guides are reference-only; choose your material/tool recipe.")

    def prepare_holes(self):
        a = self.app
        pans = [e for e in a.project.entities if e.metadata.get("dr_pan") and not e.metadata.get("reference_only")]
        selected_groups = {e.metadata.get("group_id") for e in a.project.entities if e.id in a.selected_ids}
        selected = [e for e in pans if e.id in a.selected_ids or e.metadata.get("group_id") in selected_groups-{None}]
        targets = selected or pans
        groups = {e.metadata.get("group_id") for e in targets}-{None}
        holes = {e.id for e in a.project.entities if e.metadata.get("dr_pan_hole")
                 and e.metadata.get("group_id") in groups and not e.metadata.get("reference_only")}
        if not holes:
            messagebox.showinfo("DR Pans","No applied mounting holes on the selected pan(s). Generate and apply with mounting holes enabled first.",parent=a)
            return
        a.selected_ids = holes
        a._switch_workspace("manufacturing")
        a._redraw()
        a.status.set(f"Selected {len(holes)} pan mounting holes for Drill. Choose a suitable drill tool and depth; bend guides and cut outlines are excluded.")

    def prepare_marks(self):
        """Create/update only mark lines on applied pans, including legacy pans.

        Stable line identities keep existing engraving operations attached when
        the operator tunes the offset. Never changes the blank or bend guides.
        """
        a=self.app
        if self.results:
            messagebox.showinfo("DR Pans","Apply the generated pan first, then select its bend marks.",parent=a)
            return
        try:
            enabled,offset=self.mark_settings()
            if not enabled:
                raise ValueError("Enable Include engravable bend marks first. Existing marks are not deleted by this checkbox.")
            pans=[e for e in a.project.entities if e.metadata.get("dr_pan") and not e.metadata.get("reference_only")]
            selected_groups={e.metadata.get("group_id") for e in a.project.entities if e.id in a.selected_ids}-{None}
            selected=[e for e in pans if e.id in a.selected_ids or e.metadata.get("group_id") in selected_groups]
            targets=selected or pans
            if not targets:
                raise ValueError("Apply a pan before preparing bend-mark engraving.")
            replacements={};created=[];selected_ids=set()
            for pan in targets:
                group=pan.metadata.get("group_id")
                members=[e for e in a.project.entities if
                         (bool(group) and e.metadata.get("group_id")==group)
                         or (not group and e.metadata.get("dr_pan_owner")==pan.id)]
                owner_ids={pan.id,pan.metadata.get("dr_pan",{}).get("source",{}).get("id")}-{None}
                guides=[e for e in members if e.metadata.get("reference_only") and e.metadata.get("dr_pan_owner") in owner_ids]
                existing={int(e.metadata["dr_pan_bend_index"]):e for e in members
                          if e.metadata.get("dr_pan_bend_mark") and e.metadata.get("dr_pan_owner") in owner_ids}
                proposed=dr_pan_bend_marks(pan,guides,offset)
                involved={pan.id,*(e.id for e in guides),*(e.id for e in existing.values())}
                if a._unlocked_ids(involved)!=involved:
                    raise ValueError("Unlock the selected pan, bend guides and marks before adjusting marks.")
                for index,line in enumerate(proposed):
                    old=existing.get(index)
                    if old:
                        line.id=old.id
                        line.metadata={**old.metadata,**line.metadata}
                        line.appearance=copy.deepcopy(old.appearance)
                        if line.to_dict()!=old.to_dict():replacements[old.id]=line
                    else:created.append(line)
                    selected_ids.add(line.id)
        except (ValueError,KeyError) as exc:
            messagebox.showinfo("DR Pan Bend Marks",str(exc),parent=a)
            return
        if replacements or created:
            a.project.entities=[replacements.get(e.id,e) for e in a.project.entities]+created
            a.selected_ids=selected_ids
            a._refresh_layer_choices()
            a._finish_geometry_transform("Update DR pan bend marks","MT")
        a.settings.setdefault("mounting",{}).setdefault("dr_pan",{}).update(
            marks_enabled=True,mark_offset=self.mark_offset.get())
        a._save_settings()
        a.selected_ids=selected_ids
        a._switch_workspace("manufacturing")
        a._redraw()
        a.status.set(f"Selected {len(selected_ids)} bend-mark lines for Engrave. Set your marking tool and shallow depth; pan cuts, holes and reference guides are unchanged.")
