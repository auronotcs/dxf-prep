"""Explicit, undoable review of small objects and uncertain DXF joins."""
import tkinter as tk
from tkinter import ttk, messagebox
from .small_geometry import DEFAULT_SPECK_IN, find_small_geometry, delete_small_geometry, affected_operations
from .geometry_diagnostics import GeometryDiagnostic


def recommend_small_geometry(app, entity_ids=None):
    project=app.project;token=(app.document_id,app.document_revision)
    findings=find_small_geometry(project)
    if entity_ids is not None:findings=[f for f in findings if f.entity_id in entity_ids]
    if not findings:return
    accepted=messagebox.askyesno('Small geometry / DXF joins need review',
        f'Found {len(findings)} small object(s) or uncertain DXF join(s).\n\n'
        'Review dimensions and locations now? Small features may be intentional. '
        'Nothing is deleted or repaired automatically.',parent=app)
    if app.project is not project or token!=(app.document_id,app.document_revision):return
    if accepted:
        app.open_small_geometry()
        return True
    return False


class SmallGeometryDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.valid=False;self.rows={}
        self.title('Small Geometry / Specks');self.geometry('1000x580');self.minsize(850,450);self.transient(app)
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.size=tk.StringVar(value=str(DEFAULT_SPECK_IN));self.kept=tk.BooleanVar(value=False)
        self.only_specks=tk.BooleanVar(value=False);self.summary=tk.StringVar()
        ttk.Label(self,text='Review whole objects—not every short segment in a detailed contour.',padding=10).pack(anchor='w')
        options=ttk.Frame(self,padding=(10,0));options.pack(fill='x')
        ttk.Label(options,text='Maximum width AND height (in)').pack(side='left')
        ttk.Entry(options,textvariable=self.size,width=10).pack(side='left',padx=6)
        ttk.Button(options,text='Scan',command=self.scan).pack(side='left')
        ttk.Checkbutton(options,text='Include kept',variable=self.kept,command=self.scan).pack(side='left',padx=8)
        ttk.Checkbutton(options,text='Deletable specks only',variable=self.only_specks,command=self.scan).pack(side='left')
        frame=ttk.Frame(self,padding=10);frame.pack(fill='both',expand=True)
        columns=('object','layer','size','location','reason')
        self.tree=ttk.Treeview(frame,columns=columns,show='headings',selectmode='extended')
        for col,title,width in zip(columns,('Object','Layer','Width × Height','Location','Why flagged / protection'),(85,115,130,150,420)):
            self.tree.heading(col,text=title);self.tree.column(col,width=width,minwidth=65)
        self.tree.grid(row=0,column=0,sticky='nsew')
        y=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);y.grid(row=0,column=1,sticky='ns')
        x=ttk.Scrollbar(frame,orient='horizontal',command=self.tree.xview);x.grid(row=1,column=0,sticky='ew')
        self.tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        self.tree.bind('<<TreeviewSelect>>',self.selection_changed)
        self.tree.bind('<Control-a>',self.select_all);self.tree.bind('<Control-A>',self.select_all)
        self.tree.bind('<Double-Button-1>',lambda e:self.zoom())
        ttk.Label(self,textvariable=self.summary,padding=(10,3),wraplength=940,justify='left').pack(fill='x')
        protections=ttk.Frame(self,padding=(10,3));protections.pack(fill='x')
        self.unlock_button=ttk.Button(protections,text='Unlock Selected Layers',command=self.unlock_layers)
        self.unlock_button.pack(side='left')
        ttk.Label(protections,text='Unlocks the whole layer; deletion is a separate confirmed action.').pack(side='left',padx=8)
        buttons=ttk.Frame(self,padding=10);buttons.pack(fill='x')
        self.all_button=ttk.Button(buttons,text='Select All',command=self.select_all);self.all_button.pack(side='left')
        self.zoom_button=ttk.Button(buttons,text='Zoom to Selected',command=self.zoom);self.zoom_button.pack(side='left',padx=5)
        self.keep_button=ttk.Button(buttons,text='Keep Selected',command=self.keep);self.keep_button.pack(side='left')
        self.delete_button=ttk.Button(buttons,text='Delete Selected',command=self.delete);self.delete_button.pack(side='left',padx=5)
        ttk.Button(buttons,text='Inspect Geometry',command=app.open_geometry_diagnostics).pack(side='left')
        ttk.Button(buttons,text='Close',command=self.close).pack(side='right')
        self.size.trace_add('write',lambda *_:self.mark_stale())
        self.scan()

    def current(self):
        return (self.valid and self.app.project is self.project and
                self.token==(self.app.document_id,self.app.document_revision))

    def mark_stale(self):
        self.valid=False
        if self.app.geometry_diagnostic_results is getattr(self,'markers',None):
            self.app.clear_geometry_diagnostics()
        self.summary.set('Drawing or threshold changed—Scan again. Nothing has been deleted.')
        self.selection_changed()

    def scan(self):
        try:
            value=float(self.size.get());findings=find_small_geometry(self.app.project,value,include_kept=self.kept.get())
        except (ValueError,OverflowError) as exc:
            self.mark_stale();messagebox.showerror('Small Geometry',str(exc),parent=self);return
        if self.only_specks.get():findings=[f for f in findings if f.deletable]
        self.project=self.app.project;self.token=(self.app.document_id,self.app.document_revision)
        self.threshold=value;self.valid=True;self.rows={f.entity_id:f for f in findings}
        self.tree.delete(*self.tree.get_children());entities={e.id:e for e in self.project.entities}
        for f in findings:
            e=entities[f.entity_id];label=f"Object {e.metadata.get('object_number',e.id)}"
            self.tree.insert('', 'end', iid=e.id, values=(label,e.layer,
                f'{f.width:.6g} × {f.height:.6g} {self.project.units}',
                f'X{f.point.x:.6g} Y{f.point.y:.6g}',f.reason))
        self.markers=[GeometryDiagnostic('warning','geometry.speck_review',f.reason,f.point,(f.entity_id,)) for f in findings]
        self.app._speck_review_reveal_ids=set()
        self.app.geometry_diagnostic_results=self.markers
        self.app.active_geometry_diagnostic=None;self.app._redraw_canvas();self.selection_changed()

    def selected(self):return [self.rows[i] for i in self.tree.selection() if i in self.rows]

    def locked_selected_layers(self):
        ids={f.entity_id for f in self.selected()}
        settings=self.project.metadata.get('layer_settings',{})
        return sorted({e.layer or '0' for e in self.project.entities if e.id in ids
                       and settings.get(e.layer or '0',{}).get('locked',False)})

    def selection_changed(self,event=None):
        selected=self.selected();valid=self.current()
        locked=self.locked_selected_layers() if valid else []
        self.unlock_button.configure(state='normal' if locked else 'disabled')
        self.all_button.configure(state='normal' if valid and self.rows else 'disabled')
        for button in (self.keep_button,self.zoom_button):button.configure(state='normal' if valid and selected else 'disabled')
        self.delete_button.configure(state='normal' if valid and selected and all(f.deletable for f in selected) else 'disabled')
        if valid:
            ids={f.entity_id for f in selected};affected=affected_operations(self.project,ids)
            names=[op.name for op in self.project.operations if op.id in affected]
            self.summary.set(f'{len(self.rows)} candidate(s); {len(selected)} selected. '
                + ('Affected machining (will require reassociation/regeneration on deletion): '+', '.join(names)
                   if names else 'No directly dependent machining operations selected.')
                + (' Layer is locked: '+', '.join(locked)+'. Use Unlock Selected Layers, then Delete Selected. Other protections still apply.' if locked else
                   (' Selected review-only/attached objects cannot be deleted here; see Why flagged / protection.' if any(not f.deletable for f in selected) else '')))

    def unlock_layers(self):
        if not self.current():self.mark_stale();return
        selected=self.selected();layers=self.locked_selected_layers()
        if not layers:return
        ids={f.entity_id for f in selected}
        # Confirm the layer-wide effect, including objects outside this selection.
        counts={name:sum((e.layer or '0')==name for e in self.project.entities) for name in layers}
        message=('Unlock '+str(len(layers))+' selected layer(s)?\n\n'
                 +'\n'.join(f'{name} ({counts[name]} objects on layer)' for name in layers)
                 +'\n\nThis unlocks ALL objects on these layers, not just the selected specks. '
                 'Nothing will be deleted. Review the selection, then use Delete Selected. Undo restores the locks.')
        if not messagebox.askyesno('Unlock selected layers',message,parent=self):return
        if not self.current():self.mark_stale();return
        current={f.entity_id:f for f in find_small_geometry(self.project,self.threshold,include_kept=True)}
        if (ids!={f.entity_id for f in self.selected()} or layers!=self.locked_selected_layers()
                or any(f.entity_id not in current or current[f.entity_id].signature!=f.signature for f in selected)):
            self.mark_stale();return
        for name in layers:self.project.metadata['layer_settings'][name]['locked']=False
        self.app._set_dirty(True)
        self.app._record_history('Unlock selected speck layers','LY')
        self.app._redraw()
        self.scan()
        self.tree.selection_set(*(ids & self.rows.keys()));self.selection_changed()
        self.app.status.set('Unlocked layers: '+', '.join(layers)+'. No geometry deleted; review and Delete Selected when ready.')

    def select_all(self,event=None):
        if self.current():self.tree.selection_set(*self.tree.get_children());self.selection_changed()
        return 'break'

    def zoom(self):
        if not self.current():self.mark_stale();return
        selected=self.selected()
        if not selected:return
        from .layout import drawing_bounds
        from .review_view import uncovered_canvas_rect, review_transform
        from .units import unit_scale
        ids={f.entity_id for f in selected};bounds=drawing_bounds([e for e in self.project.entities if e.id in ids])
        if bounds is None:return
        self.update_idletasks()
        canvas=self.app.canvas
        def exposed_viewport():
            dx=self.winfo_rootx()-canvas.winfo_rootx();dy=self.winfo_rooty()-canvas.winfo_rooty()
            return uncovered_canvas_rect(canvas.winfo_width(),canvas.winfo_height(),
                (dx,dy,dx+self.winfo_width(),dy+self.winfo_height()))
        viewport=exposed_viewport()
        if viewport is None:
            self.summary.set('Not enough uncovered canvas. Move or resize the Specks window, then Zoom to Selected again.')
            return
        # View changes only: never change saved layer visibility or lock state.
        self.app.layer_filter_var.set('All layers');self.app.layer_isolation_active=False
        self.app.active_plate_only_var.set(False)
        self.app.selected_ids=ids;self.app.selected_operation_ids.clear()
        self.app._speck_review_reveal_ids=ids.copy()
        self.app.geometry_diagnostic_results=self.markers
        self.app.active_geometry_diagnostic=next((m for m in self.markers if m.entity_ids[0] in ids),None)
        self.app.scale,self.app.origin_x,self.app.origin_y=review_transform(
            bounds,viewport,unit_scale('in',self.project.units))
        self.app._redraw()
        # Selection/status controls can settle a new canvas size (especially at
        # changed DPI). Correct the framing once, without a recursive full refresh.
        self.update_idletasks()
        settled=exposed_viewport()
        if settled is not None and settled!=viewport:
            self.app.scale,self.app.origin_x,self.app.origin_y=review_transform(
                bounds,settled,unit_scale('in',self.project.units))
            self.app._redraw_canvas()

    def keep(self):
        if not self.current():self.mark_stale();return
        selected=self.selected()
        if not selected:return
        self.project.metadata.setdefault('small_geometry_kept',{}).update({f.entity_id:f.signature for f in selected})
        self.app._set_dirty(True);self.app._record_history('Keep reviewed small geometry','KEEP')
        self.scan()

    def delete(self):
        if not self.current():self.mark_stale();return
        selected=self.selected()
        if not selected:return
        try:candidate,affected=delete_small_geometry(self.project,selected,self.threshold)
        except ValueError as exc:
            messagebox.showerror('Small Geometry',str(exc),parent=self);return
        labels=[str(self.tree.item(f.entity_id,'values')[0]) for f in selected]
        names=[op.name for op in self.project.operations if op.id in affected]
        message=(f'Delete {len(selected)} selected object(s)?\n\n'+', '.join(labels)+'\n\n'
                 'Only these objects will be removed. Undo restores them. Existing toolpaths must regenerate.')
        if names:message+='\n\nThese dependent operations will be retained but disabled for reassociation: '+', '.join(names)
        if not messagebox.askyesno('Delete selected specks',message,parent=self):return
        if not self.current():self.mark_stale();return
        # Revalidate locks/dependencies even if changed inside a modal callback.
        try:candidate,affected=delete_small_geometry(self.project,selected,self.threshold)
        except ValueError as exc:messagebox.showerror('Small Geometry',str(exc),parent=self);return
        app=self.app;app.project=candidate;app.selected_ids.clear();app.selection_order.clear()
        app.selected_operation_ids.clear();app._defer_toolpath_regeneration()
        app._update_sheet_after_geometry_change();app._set_dirty(True);app._redraw()
        app._record_history('Delete selected small geometry','DEL')
        app.status.set(f'Deleted {len(selected)} reviewed speck(s); {len(affected)} dependent operation(s) require reassociation. '
                       'Unrelated operations preserved. Regenerate before output; Undo restores the batch.')
        self.scan()

    def close(self):
        if self.app.geometry_diagnostic_results is getattr(self,'markers',None):
            self.app.clear_geometry_diagnostics()
        self.destroy()
