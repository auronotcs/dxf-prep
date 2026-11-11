"""Choose one existing operation to edit; never edit a batch implicitly."""
import tkinter as tk
from tkinter import ttk


def operation_choice_rows(project, operations):
    labels = {'router_offset': 'Profile cut', 'drill_circles': 'Drill',
              'island_fill': 'Cleanout', 'engrave': 'Engrave',
              'thread_mill': 'Thread mill', 'tab_release': 'Final tab release',
              'rest_machining': 'Rest cleanout', 'profile_rest_machining': 'Profile rest cut'}
    rows = []
    for operation in operations:
        kind = labels.get(operation.kind, operation.kind.replace('_', ' ').title())
        if operation.kind == 'router_offset':
            sides = set(operation.entity_sides.values()) or {operation.side.value}
            if len(sides) > 1:
                kind = 'Profile cut — mixed inside/outside'
            else:
                kind = {'outside': 'Outside cut', 'inside': 'Inside cut', 'on': 'Cut line'}.get(next(iter(sides)), kind)
        tool = project.tool_library.by_station(operation.tool_station)
        tool_label = f'T{operation.tool_station}' + (f' — {tool.name}' if tool is not None else ' — missing tool')
        targets = set(operation.entity_ids) | set(getattr(operation, 'island_entity_ids', ()))
        rows.append((operation.id, operation.name, kind, tool_label, len(targets), 'Enabled' if operation.enabled else 'Disabled'))
    return rows


class OperationChoiceDialog(tk.Toplevel):
    def __init__(self, parent, rows):
        super().__init__(parent)
        self.result = None
        self.rows = {row[0]: row for row in rows}
        self.title('Choose operation to edit')
        self.transient(parent)
        self.geometry('820x360')
        self.minsize(520, 280)
        self.columnconfigure(0, weight=1);self.rowconfigure(1, weight=1)
        ttk.Label(self, text=f'{len(rows)} operations selected. Which one would you like to edit?',
                  font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', padx=12, pady=12)
        self.tree = ttk.Treeview(self, columns=('name','kind','tool','count','state'), show='headings', selectmode='browse')
        for key,title,width in [('name','Operation name',290),('kind','Type',210),('tool','Tool',180),('count','Objects',65),('state','State',80)]:
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=55)
        self.tree.grid(row=1,column=0,sticky='nsew',padx=(12,0))
        vertical=ttk.Scrollbar(self,orient='vertical',command=self.tree.yview)
        vertical.grid(row=1,column=1,sticky='ns',padx=(0,12))
        horizontal=ttk.Scrollbar(self,orient='horizontal',command=self.tree.xview)
        horizontal.grid(row=2,column=0,sticky='ew',padx=(12,0))
        self.tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        for row in rows:self.tree.insert('', 'end', iid=row[0], values=row[1:])
        self.detail=ttk.Label(self,text='',wraplength=780)
        self.detail.grid(row=3,column=0,columnspan=2,sticky='ew',padx=12,pady=8)
        buttons=ttk.Frame(self);buttons.grid(row=4,column=0,columnspan=2,sticky='e',padx=12,pady=(0,12))
        ttk.Button(buttons,text='Cancel',command=self.destroy).pack(side='right')
        self.edit=ttk.Button(buttons,text='Edit selected operation',command=self._accept)
        self.edit.pack(side='right',padx=8)
        self.tree.bind('<<TreeviewSelect>>',self._selected)
        self.tree.bind('<Double-Button-1>',self._double_click)
        self.bind('<Return>',lambda event:self._accept())
        self.bind('<Escape>',lambda event:self.destroy())
        self.bind('<Configure>',self._resize)
        if rows:self.tree.selection_set(rows[0][0]);self.tree.focus(rows[0][0])
        self._selected();self.tree.focus_set();self.grab_set()

    def _resize(self,event):
        if event.widget is self:self.detail.configure(wraplength=max(200,event.width-30))

    def _selected(self,event=None):
        selected=self.tree.selection()
        row=self.rows.get(selected[0]) if len(selected)==1 else None
        self.edit.configure(state='normal' if row else 'disabled')
        self.detail.configure(text=f'{row[1]}\n{row[2]} • {row[3]} • {row[4]} object(s) • {row[5]}' if row else 'Choose one operation.')

    def _double_click(self,event):
        if self.tree.identify_row(event.y):self._accept()

    def _accept(self):
        selected=self.tree.selection()
        if len(selected)==1 and selected[0] in self.rows:
            self.result=selected[0]
            self.destroy()
