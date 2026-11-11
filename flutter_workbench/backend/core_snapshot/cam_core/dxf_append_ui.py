"""Add DXF geometry to the current document, with explicit units and placement."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .dxf import import_dxf
from .dxf_append import prepare_dxf_append
from .units import UNIT_TO_INCHES


class DxfAppendDialog(tk.Toplevel):
    def __init__(self, app, imported, filename, plate):
        super().__init__(app)
        self.result = None
        self.app = app
        self.imported = imported
        self.plate = plate
        self.title('Import DXF into Current Job')
        self.transient(app)
        self.resizable(False, False)
        self.grab_set()
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'{filename.name}: {len(imported.project.entities)} object(s)\n'
                  f'Add to {app.project.name} ({app.project.units}). Existing work stays in place.',
                  wraplength=580).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,10))
        self.units = tk.StringVar(value=imported.project.units)
        ttk.Label(frame, text='DXF source units').grid(row=1, column=0, sticky='w')
        ttk.Combobox(frame, textvariable=self.units, state='readonly',
                     values=tuple(UNIT_TO_INCHES), width=18).grid(row=1, column=1, sticky='w')
        self.placement = tk.StringVar(value='original')
        ttk.Radiobutton(frame, text='Keep original DXF coordinates (converted to job units)',
                        variable=self.placement, value='original').grid(row=2, column=0, columnspan=2, sticky='w', pady=(10,0))
        label = f'Place drawing lower-left at active Plate {plate.number} origin' if plate else 'Place at active plate origin (no plate available)'
        ttk.Radiobutton(frame, text=label, variable=self.placement, value='plate',
                        state='normal' if plate else 'disabled').grid(row=3, column=0, columnspan=2, sticky='w')
        self.size = tk.StringVar()
        ttk.Label(frame, textvariable=self.size).grid(row=4, column=0, columnspan=2, sticky='w', pady=8)
        notes = 'No scaling to fit, nesting, or new plates. Imported objects stay selected for moving.\n'
        notes += 'DXF layer names are retained; hidden/locked name conflicts get a separate import layer.'
        ttk.Label(frame, text=notes, wraplength=580).grid(row=5, column=0, columnspan=2, sticky='w')
        if imported.warnings:
            box = tk.Text(frame, height=6, width=74, wrap='word')
            box.grid(row=6, column=0, columnspan=2, pady=8)
            box.insert('1.0', '\n\n'.join(imported.warnings))
            box.configure(state='disabled')
            scroll = ttk.Scrollbar(frame, orient='vertical', command=box.yview)
            scroll.grid(row=6, column=2, sticky='ns')
            box.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky='e', pady=(10,0))
        ttk.Button(buttons, text='Cancel', command=self.destroy).pack(side='right')
        ttk.Button(buttons, text='Import into Current Job', command=self.accept).pack(side='right', padx=8)
        self.units.trace_add('write', self.update_size)
        self.update_size()
        self.bind('<Escape>', lambda event: self.destroy())

    def prepare(self):
        origin = self.plate.bounds[:2] if self.placement.get() == 'plate' and self.plate else None
        return prepare_dxf_append(self.app.project, self.imported.project,
                                  source_units=self.units.get(), lower_left=origin)

    def update_size(self, *_args):
        try:
            box = self.prepare().bounds
            self.size.set(f'Imported size: {box[2]-box[0]:.4f} × {box[3]-box[1]:.4f} {self.app.project.units}')
        except ValueError as exc:
            self.size.set(str(exc))

    def accept(self):
        try:
            self.result = self.prepare()
        except ValueError as exc:
            messagebox.showerror('DXF import', str(exc), parent=self)
            return
        self.destroy()


def import_into_current(app):
    project = app.project
    token = (app.document_id, app.document_revision)
    def current():
        return app.project is project and token == (app.document_id, app.document_revision)
    filename = filedialog.askopenfilename(parent=app, title='Import DXF into Current Job',
        initialdir=app.settings.get('last_open_directory') or None,
        filetypes=(('DXF drawings', ('*.dxf','*.DXF')),))
    if not filename or not current():
        return
    try:
        imported = import_dxf(Path(filename))
        if not imported.project.entities:
            raise ValueError('No supported 2D geometry was found. The current job was not changed.\n\n'
                             + '\n'.join(imported.warnings))
        dialog = DxfAppendDialog(app, imported, Path(filename), app._selected_plate())
        app.wait_window(dialog)
    except (OSError, ValueError, KeyError) as exc:
        messagebox.showerror('DXF import failed', str(exc), parent=app)
        return
    if dialog.result is None:
        return
    if not current():
        app.status.set('Document changed while importing; nothing was added. Please retry in the intended job.')
        return
    prepared = dialog.result
    app.cancel_interactive_mode()
    app.mode.set('select')
    project.entities.extend(prepared.entities)
    app.selected_ids = {e.id for e in prepared.entities}
    app.selected_operation_ids.clear()
    app.selected_dimension_id = None
    app.selected_guide_id = None
    app.layer_filter_var.set('All layers')
    app.layer_isolation_active = False
    app.active_plate_only_var.set(False)
    app._refresh_layer_choices()
    app._defer_toolpath_regeneration()
    app._set_dirty(True)
    app._zoom_to_bounds(prepared.bounds)
    app._redraw()
    app._record_history(f'Import DXF: {Path(filename).name}', 'DXF')
    committed_token = (app.document_id, app.document_revision)
    # Added geometry can change entry clearance; do not bless old CAM caches.
    app._regenerate_toolpaths()
    if app.project is not project or committed_token != (app.document_id, app.document_revision):
        return
    suffix = ' Toolpaths will refresh in Manufacturing or Output.' if app.__dict__.get('_toolpaths_deferred') else ''
    app.status.set(f'Added {len(prepared.entities)} object(s) from {Path(filename).name}; '
                   f'selected for moving. Undo removes this import.{suffix}')
    app._recommend_contour_repair({e.id for e in prepared.entities})
