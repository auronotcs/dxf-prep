"""Second-tool panel embedded in the ordinary closed offset dialog."""
import tkinter as tk
from tkinter import ttk
from .model import RouterOffsetOperation
from .cleanout_recovery import recovery_tools
from .profile_second_tool import validate_settings


class ProfileSecondToolPanel(ttk.LabelFrame):
    def __init__(self,parent,dialog):
        super().__init__(parent,text='Second tool / Profile rest machining',padding=7)
        self.dialog=dialog
        self.enabled=tk.BooleanVar(value=False)
        self.reviewed=tk.BooleanVar(value=False)
        self.tools={}
        self.strategy=tk.StringVar(value='Contour rest / retain skin')
        self.variables={k:tk.StringVar() for k in ('station','feed_rate','plunge_rate',
            'spindle_rpm','safe_z','stepdown','skin','ignore_stock_less_than','main_stepover','stepover')}
        self.toggle=ttk.Checkbutton(self,text='Use a smaller second tool — retain bottom skin',
            variable=self.enabled,command=self.toggle_body)
        self.toggle.pack(anchor='w')
        self.body=ttk.Frame(self);self.body.columnconfigure(1,weight=1)
        ttk.Label(self.body,text='Workflow').grid(row=0,column=0,sticky='w')
        self.strategy_widget=ttk.Combobox(self.body,textvariable=self.strategy,state='readonly',
            values=('Contour rest / retain skin','Clear waste + tabbed finish'),width=32)
        self.strategy_widget.grid(row=0,column=1,sticky='ew',padx=(7,0))
        self.strategy.trace_add('write',self.invalidate)
        units=dialog.parent_app.project.units
        self.widgets={}
        fields=[('Second tool station','station'),(f'Cut feed ({units}/min)','feed_rate'),
            (f'Plunge feed ({units}/min)','plunge_rate'),('Spindle RPM','spindle_rpm'),
            (f'Safe Z ({units})','safe_z'),(f'Maximum stepdown ({units})','stepdown'),
            (f'Retained bottom skin ({units})','skin'),(f'Ignore residual below ({units})','ignore_stock_less_than'),
            (f'Main clearing stepover ({units})','main_stepover'),(f'Small clearing stepover ({units})','stepover')]
        for row,(label,key) in enumerate(fields,start=1):
            ttk.Label(self.body,text=label).grid(row=row,column=0,sticky='w',pady=2)
            widget=(ttk.Combobox(self.body,textvariable=self.variables[key],state='readonly',width=26)
                    if key=='station' else ttk.Entry(self.body,textvariable=self.variables[key],width=15))
            widget.grid(row=row,column=1,sticky='ew',padx=(7,0),pady=2);self.widgets[key]=widget
            self.variables[key].trace_add('write',self.invalidate)
        self.widgets['station'].bind('<<ComboboxSelected>>',self.tool_changed)
        self.info=tk.StringVar()
        ttk.Label(self.body,textvariable=self.info,wraplength=420,foreground='#9c4f00').grid(row=11,column=0,columnspan=2,sticky='w',pady=5)
        ttk.Label(self.body,wraplength=420,text='Larger tool, then small-feature / corner rest. BOTH stop above the skin. '
            'No automatic release. Existing tabs are not used above the continuous skin. '
            'Small tool uses its own stepdown and plunge entries; review full-slot capability. '
            'Clear waste + tabbed finish instead pockets bounded INSIDE waste, then profiles ALL selected contours with the small tool. '
            'Outside outlines finish last; exterior sheet waste is not pocketed. '
            'Set Through stock and at least two bridge tabs above. Tab height must not exceed skin. '
            'Clearing stepovers apply only to this mode (maximum 50% diameter). Walls finish to size; no wall allowance. '
            'Automatic tab removal is disabled, including on small female contours. Split offsets may still leave unreachable detail.').grid(row=12,column=0,columnspan=2,sticky='w')
        ttk.Checkbutton(self.body,text='Reviewed both workflows, feeds, plunge, skin and retained tabs',
            variable=self.reviewed).grid(row=13,column=0,columnspan=2,sticky='w',pady=5)
        for key in ('station','diameter','target_depth','cut_intent','breakthrough',
                    'tab_count','tab_width','tab_height','tab_mode','stepdown','allowance'):
            if key in dialog.variables:dialog.variables[key].trace_add('write',self.invalidate)

    def invalidate(self,*_args):self.reviewed.set(False)

    def show(self):
        if getattr(self,'is_editing',False):return
        if not self.enabled.get():
            self.enabled.set(True);self.toggle_body()
        self.dialog.update_idletasks()
        self.dialog._cut_canvas.yview_moveto(1.0)

    def editing(self,operation):
        self.is_editing=True;self.enabled.set(False);self.body.pack_forget()
        self.toggle.configure(state='disabled')
        text='Editing one saved stage. Create a new offset to build a two-tool group; edit its separate corner-rest operation for second-tool feeds.'
        if operation.profile_retention:
            if operation.profile_retention.get('workflow') == 'waste_then_tabbed_finish':
                text='Hybrid group: recreate the group to change geometry, tools or machining settings together. Individual edits invalidate its tab-support contract. Final finish cuts through between retained tabs; release tabs separately.'
            else:
                text+=f" Retained skin: {operation.profile_retention['skin']:g} {self.dialog.parent_app.project.units}. Release separately."
        ttk.Label(self,text=text,wraplength=800).pack(anchor='w')

    def refresh(self):
        station=int(self.dialog.variables['station'].get().split('—')[0].strip().lstrip('T'))
        choices=recovery_tools(self.dialog.parent_app.project,RouterOffsetOperation('choices',station,[],1,1))
        self.tools={f'T{t.station} — {t.name}':t for t in choices}
        self.widgets['station'].configure(values=tuple(self.tools))
        if self.variables['station'].get() not in self.tools:
            self.variables['station'].set(next(iter(self.tools),''));self.tool_changed()

    def toggle_body(self):
        self.invalidate()
        if self.enabled.get():
            self.body.pack(fill='x',pady=4)
            try:self.refresh()
            except ValueError as exc:self.info.set(str(exc))
        else:self.body.pack_forget()

    def tool_changed(self,*_args):
        tool=self.tools.get(self.variables['station'].get())
        if tool is None:
            self.info.set('Load a smaller flat end mill first.');return
        values=dict(feed_rate=None if tool.feed_rate is None else tool.feed_rate*60,
            plunge_rate=None if tool.plunge_rate is None else tool.plunge_rate*60,
            spindle_rpm=tool.spindle_rpm,stepdown=tool.max_stepdown,
            safe_z=self.dialog.variables['safe_z'].get(),skin=.025*self.dialog.parent_app._sheet_unit_scale(),
            ignore_stock_less_than=.001*self.dialog.parent_app._sheet_unit_scale(),
            main_stepover=float(self.dialog.variables['diameter'].get())/2,stepover=tool.diameter/2)
        for k,v in values.items():self.variables[k].set('' if v is None else str(v))
        self.info.set(f'Library starting values, not an approved recipe. Diameter {tool.diameter:g}; '
            f'cutting-edge length {tool.cutting_edge_length if tool.cutting_edge_length is not None else "unknown"} '
            f'{self.dialog.parent_app.project.units}. Stock flatness/Z error can reduce physical skin.')

    def collect(self,values):
        if not self.enabled.get():return None
        self.refresh()
        tool=self.tools.get(self.variables['station'].get())
        if tool is None:raise ValueError('Choose a loaded smaller second cutter.')
        settings={k:float(v.get()) for k,v in self.variables.items() if k!='station'}
        settings.update(station=tool.station,reviewed=self.reviewed.get(),strategy=self.strategy.get())
        settings['feed_rate']/=60;settings['plunge_rate']/=60
        source=RouterOffsetOperation('validate',values['station'],[],values['target_depth'],values['safe_z'],
            side=self.dialog.side,common_line_mode=values['common_line_mode'])
        validate_settings(self.dialog.parent_app.project,source,settings)
        app=self.dialog.parent_app
        if not app.settings.get('machine_spindle_min_rpm',8000)<=settings['spindle_rpm']<=app.settings.get('machine_spindle_max_rpm',24000):
            raise ValueError('Second-tool RPM is outside the configured machine limits.')
        if tool.station==6 and settings['spindle_rpm']>12000:
            raise ValueError('T6 is limited to 12000 RPM.')
        return settings
