"""Optional second-tool controls hosted by the existing Island Fill dialog."""
import tkinter as tk
from tkinter import ttk
from .cleanout_recovery import recovery_tools, validate_rest_settings, analyze_access
from .model import IslandFillOperation
from .object_identity import object_identity_label
from .toolpath import classify_island_fill_regions
from .layout import entity_bounds


class RestToolPanel(ttk.LabelFrame):
    def __init__(self, parent, dialog):
        super().__init__(parent, text='Rest Machining (optional)', padding=6)
        self.dialog = dialog
        self.enabled = tk.BooleanVar(value=False)
        self.reviewed = tk.BooleanVar(value=False)
        self.variables = {key: tk.StringVar(value='') for key in (
            'station', 'feed_rate', 'plunge_rate', 'spindle_rpm', 'safe_z',
            'stepdown', 'spacing', 'overlap', 'stepover', 'ignore_stock_less_than')}
        self.toggle = ttk.Checkbutton(self, text='Use a smaller second tool', variable=self.enabled,
                                     command=self._toggle)
        self.toggle.pack(anchor='w')
        self.body = ttk.Frame(self)
        self.body.columnconfigure(1, weight=1)
        self.widgets = {}
        units = dialog.parent_app.project.units
        fields = [('Second tool station', 'station'), (f'Cut feed ({units}/min)', 'feed_rate'),
                  (f'Plunge feed ({units}/min)', 'plunge_rate'), ('Spindle RPM', 'spindle_rpm'),
                  (f'Safe Z ({units})', 'safe_z'), (f'Maximum stepdown ({units})', 'stepdown'),
                  ('Pass spacing input', 'spacing'), ('Overlap (%)', 'overlap'),
                  (f'Stepover ({units})', 'stepover'),
                  (f'Ignore residual thinner than ({units})', 'ignore_stock_less_than')]
        for row, (label, key) in enumerate(fields):
            ttk.Label(self.body, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
            if key in {'station', 'spacing'}:
                widget = ttk.Combobox(self.body, textvariable=self.variables[key], state='readonly', width=28)
                if key == 'spacing':
                    widget.configure(values=('Overlap percentage', 'Direct stepover'))
                widget.bind('<<ComboboxSelected>>', self._tool_changed if key == 'station' else self._sync_spacing)
            else:
                widget = ttk.Entry(self.body, textvariable=self.variables[key], width=20)
            widget.grid(row=row, column=1, sticky='ew', pady=2)
            self.widgets[key] = widget
            if key in {'overlap', 'stepover'}:
                widget.bind('<FocusOut>', self._sync_spacing)
            self.variables[key].trace_add('write', self._invalidate_review)
        self.info = tk.StringVar(value='')
        ttk.Label(self.body, textvariable=self.info, foreground='#9c4f00', wraplength=390).grid(
            row=10, column=0, columnspan=2, sticky='w', pady=4)
        ttk.Label(self.body, text='Main fill depth at creation. Plunge entries; normal depth passes.\n'
                  'Creates separate editable small-pocket / rest toolpaths.\n'
                  'Thin-residual filter affects rest only, not full small pockets.\n'
                  'Save Recipe applies to the main tool only.', wraplength=390).grid(
            row=11, column=0, columnspan=2, sticky='w', pady=4)
        ttk.Checkbutton(self.body, text='I reviewed these settings for this material and cutter',
                        variable=self.reviewed).grid(row=12, column=0, columnspan=2, sticky='w')
        self.tools = {}
        # A change in main tool/depth invalidates the operator's earlier review.
        for key in ('station', 'target_depth', 'cut_intent', 'breakthrough'):
            dialog.variables[key].trace_add('write', self._invalidate_review)

    def _invalidate_review(self, *_args):
        self.reviewed.set(False)

    def _toggle(self):
        self.reviewed.set(False)
        if self.enabled.get():
            self.body.pack(fill='x', pady=4)
            try:
                self.refresh_tools()
            except ValueError as exc:
                self.tools = {}
                self.widgets['station'].configure(values=())
                self.variables['station'].set('')
                self.info.set(str(exc))
        else:
            self.body.pack_forget()

    def refresh_tools(self):
        # Match the station label without importing the application module.
        station = int(self.dialog.variables['station'].get().split('—')[0].strip().lstrip('T'))
        source = IslandFillOperation('Tool choices', station, [], 1, 1)
        choices = recovery_tools(self.dialog.parent_app.project, source)
        self.tools = {f'T{t.station} — {t.name}': t for t in choices}
        self.widgets['station'].configure(values=tuple(self.tools))
        if self.variables['station'].get() not in self.tools:
            preferred = next((label for label, t in self.tools.items() if t.station == 9),
                             next(iter(self.tools), ''))
            self.variables['station'].set(preferred)
            self._tool_changed()

    def _tool_changed(self, *_args):
        tool = self.tools.get(self.variables['station'].get())
        if tool is None:
            self.info.set('Load a smaller flat end mill in the turret first.')
            return
        defaults = {'feed_rate': None if tool.feed_rate is None else tool.feed_rate * 60,
                    'plunge_rate': None if tool.plunge_rate is None else tool.plunge_rate * 60,
                    'spindle_rpm': tool.spindle_rpm, 'stepdown': tool.max_stepdown,
                    'safe_z': self.dialog.variables['safe_z'].get(), 'overlap': 50,
                    'stepover': tool.diameter / 2, 'spacing': 'Overlap percentage',
                    'ignore_stock_less_than': .001 * self.dialog.parent_app._sheet_unit_scale()}
        for key, value in defaults.items():
            self.variables[key].set('' if value is None else str(value))
        reach = 'unknown' if tool.cutting_edge_length is None else f'{tool.cutting_edge_length:g}'
        self.info.set(f'Library starting values—not an approved material recipe. '
                      f'Diameter {tool.diameter:g}; cutting-edge length {reach} '
                      f'{self.dialog.parent_app.project.units}. Review feeds, reach and plunge suitability.')
        self._sync_spacing()

    def _sync_spacing(self, *_args):
        tool = self.tools.get(self.variables['station'].get())
        direct = self.variables['spacing'].get() == 'Direct stepover'
        self.widgets['overlap'].configure(state='readonly' if direct else 'normal')
        self.widgets['stepover'].configure(state='normal' if direct else 'readonly')
        if tool is None:
            return
        try:
            key = 'overlap' if direct else 'stepover'
            value = (100 * (1 - float(self.variables['stepover'].get()) / tool.diameter)
                     if direct else tool.diameter * (1 - float(self.variables['overlap'].get()) / 100))
            text = f'{value:.12g}'
            if self.variables[key].get() != text:
                self.variables[key].set(text)
        except (ValueError, ZeroDivisionError):
            pass

    def collect(self, source):
        if not self.enabled.get():
            return None
        self.refresh_tools()
        self._sync_spacing()
        tool = self.tools.get(self.variables['station'].get())
        if tool is None:
            raise ValueError('Choose a smaller rest tool station.')
        settings = {key: float(self.variables[key].get()) for key in (
            'feed_rate', 'plunge_rate', 'spindle_rpm', 'safe_z', 'stepdown', 'stepover', 'ignore_stock_less_than')}
        settings.update(station=tool.station, reviewed=self.reviewed.get())
        settings['feed_rate'] /= 60
        settings['plunge_rate'] /= 60
        validate_rest_settings(self.dialog.parent_app.project, source, settings)
        return settings

    def editing(self):
        self.enabled.set(False)
        self.body.pack_forget()
        self.toggle.configure(state='disabled')
        ttk.Label(self, text='Editing one saved toolpath. Edit its separate rest toolpath\n'
                  'for second-tool settings; use a new fill to create a new pair.',
                  wraplength=390).pack(anchor='w')


def review_access(dialog, result):
    """Keep settings open, with a locatable list instead of a dead-end error."""
    boundaries = getattr(dialog, 'fill_boundaries', None)
    if boundaries is None:
        return True
    regions = classify_island_fill_regions(boundaries)
    source = IslandFillOperation('Access check', result['station'],
        [outer.id for outer, _ in regions], result['target_depth'], result['safe_z'],
        island_entity_ids=[e.id for _, islands in regions for e in islands])
    tools = recovery_tools(dialog.parent_app.project, source)
    access = analyze_access(dialog.parent_app.project, source, [t.station for t in tools])
    rest = result.get('rest_settings')
    blocked = [r for r in access if source.tool_station not in r.reachable_stations
               and (not rest or rest['station'] not in r.reachable_stations)]
    if not blocked:
        return True
    if not hasattr(dialog, 'reach_page'):
        dialog.reach_page = ttk.Frame(dialog.settings_notebook, padding=8)
        dialog.settings_notebook.add(dialog.reach_page, text='Tool Reach')
        ttk.Label(dialog.reach_page, text='These pockets cannot be entered by the selected tool(s).\n'
                  'Choose a smaller rest tool in Cut Setup, or revise the artwork. Nothing is omitted.',
                  wraplength=700).pack(anchor='w', pady=6)
        dialog.reach_tree = ttk.Treeview(dialog.reach_page, columns=('object', 'tools'), show='headings', height=10)
        dialog.reach_tree.heading('object', text='Pocket / boundary')
        dialog.reach_tree.heading('tools', text='Loaded tools that can enter')
        dialog.reach_tree.pack(fill='both', expand=True)
        ttk.Button(dialog.reach_page, text='Show Selected Pocket', command=lambda: show_reach(dialog)).pack(anchor='w', pady=6)
    tree = dialog.reach_tree
    tree.delete(*tree.get_children())
    for r in blocked:
        tree.insert('', 'end', iid=r.outer_id, values=(
            object_identity_label(dialog.parent_app.project, r.outer_id),
            ', '.join(f'T{s}' for s in sorted(r.reachable_stations)) or 'None — smaller cutter / artwork change needed'))
    tree.selection_set(blocked[0].outer_id)
    dialog.settings_notebook.select(dialog.reach_page)
    return False


def show_reach(dialog):
    ids = set(dialog.reach_tree.selection())
    app = dialog.parent_app
    bounds = [entity_bounds(e) for e in app.project.entities if e.id in ids]
    if not bounds:
        return
    app.selected_ids = ids
    app._zoom_to_bounds((min(b[0] for b in bounds), min(b[1] for b in bounds),
                         max(b[2] for b in bounds), max(b[3] for b in bounds)))
    app._redraw()
    # Keep the settings available without covering the centered problem on
    # the canvas. Coordinates follow this app window, including test windows.
    if app.winfo_width() >= 1000:
        width = 620
        height = min(dialog.winfo_height(), max(380, app.winfo_height()-90))
        x = app.winfo_rootx()+app.winfo_width()-width-12
        y = app.winfo_rooty()+55
        dialog.geometry(f'{width}x{height}{x:+d}{y:+d}')
    app.status.set('Tool reach: highlighted the selected inaccessible pocket; fill settings remain open.')
