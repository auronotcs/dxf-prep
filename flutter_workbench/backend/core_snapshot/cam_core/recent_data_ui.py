"""Toggleable left data pane with paged, asynchronously previewed local history."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from .recent_data import access_date
from .recent_data_preview import load_recent_preview


class RecentDataPanel:
    PAGE_SIZE = 50

    def __init__(self, app, store, cache_root):
        self.app, self.store, self.cache_root = app, store, cache_root
        self.view, self.folder, self.page = 'folders', None, 0
        self.closed = False
        self.pending = {}
        self.poll_job = None
        self.search_job = None
        self.rows = []
        self.images = []
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='recent-data')
        self.frame = ttk.Frame(app.body, width=350, padding=(10, 8))
        self.frame.bind('<Destroy>', self._destroyed, add='+')
        header = ttk.Frame(self.frame)
        header.pack(fill='x')
        ttk.Button(header, text='Home', width=6, command=lambda: self.show('folders')).pack(side='left')
        self.title = ttk.Label(header, text='Recent Folders', font=('Segoe UI', 10, 'bold'))
        self.title.pack(side='left', padx=6)
        ttk.Button(header, text='↻', width=3, command=self.refresh).pack(side='right')
        ttk.Button(header, text='×', width=3, command=self.hide).pack(side='right')
        self.breadcrumb = ttk.Label(self.frame, wraplength=310)
        self.breadcrumb.pack(fill='x', pady=(6, 5))
        self.query = tk.StringVar(master=app)
        self.search = ttk.Entry(self.frame, textvariable=self.query)
        self.search.pack(fill='x', pady=(0, 8))
        app._attach_tooltip(self.search, 'Search remembered file or folder names and paths')
        self.query.trace_add('write', self._search_changed)
        area = ttk.Frame(self.frame)
        area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(area, highlightthickness=0, background=app._theme_color('panel'))
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._resized)
        self.canvas.bind('<MouseWheel>', self._wheel)
        footer = ttk.Frame(self.frame)
        footer.pack(fill='x', pady=(6, 0))
        self.previous = ttk.Button(footer, text='‹', width=3, command=lambda: self._page(-1))
        self.previous.pack(side='left')
        self.count = ttk.Label(footer)
        self.count.pack(side='left', expand=True)
        self.next = ttk.Button(footer, text='›', width=3, command=lambda: self._page(1))
        self.next.pack(side='right')
        self.folder_icon = app._render_workspace_icon('open_o1', size=44)

    def show(self, view='files', folder=None):
        changed = self.view != view or self.folder != folder
        self.view, self.folder = view, folder
        if changed:
            self.page = 0
            self.query.set('')
        if str(self.frame) not in self.app.body.panes():
            self.app.body.insert(0, self.frame, weight=0)
            self.app.after_idle(self._set_width)
        self.refresh()
        self.app._refresh_workspace_command_states(('file.recent_data',))

    def _set_width(self):
        if not self.closed and str(self.frame) in self.app.body.panes():
            self.app.body.sashpos(0, min(350, max(220, self.app.body.winfo_width() // 3)))

    def hide(self):
        if str(self.frame) in self.app.body.panes():
            self.app.body.forget(self.frame)
        for future in self.pending:
            future.cancel()
        self.pending.clear()
        self.app.canvas.focus_set()
        self.app._refresh_workspace_command_states(('file.recent_data',))

    def _search_changed(self, *_args):
        self.page = 0
        if self.search_job:
            self.app.after_cancel(self.search_job)
        self.search_job = self.app.after(150, self._search_now)

    def _search_now(self):
        self.search_job = None
        self.refresh()

    def _page(self, delta):
        self.page = max(0, self.page + delta)
        self.refresh()

    def _resized(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        for row in self.rows:
            for key in ('title', 'date', 'detail'):
                row[key].configure(wraplength=max(80, event.width - 145))

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _bind_card(self, widget, callback):
        widget.bind('<MouseWheel>', self._wheel)
        widget.bind('<Double-Button-1>', lambda _e: callback())

    def refresh(self):
        if self.closed or str(self.frame) not in self.app.body.panes():
            return
        if self.search_job:
            self.app.after_cancel(self.search_job)
            self.search_job = None
        for future in self.pending:
            future.cancel()
        self.pending.clear()
        self.rows, self.images = [], []
        for child in self.content.winfo_children():
            child.destroy()
        home = self.view == 'folders'
        self.title.configure(text='Recent Folders' if home else 'My Recent Data')
        self.breadcrumb.configure(text='Recently accessed local folders' if home else (str(self.folder) if self.folder else 'Local files · newest access first'))
        if home:
            ttk.Button(self.content, text='My Recent Data\nFiles you recently opened or saved', command=lambda: self.show('files')).pack(fill='x', pady=(0, 10), ipady=10)
        records = self.store.records('folders' if home else 'files', self.query.get(), self.folder)
        self.page = min(self.page, max(0, (len(records)-1)//self.PAGE_SIZE))
        start = self.page * self.PAGE_SIZE
        visible = records[start:start+self.PAGE_SIZE]
        self.count.configure(text=f'{start+1}–{start+len(visible)} of {len(records)}' if visible else '0 records')
        self.previous.configure(state='normal' if self.page else 'disabled')
        self.next.configure(state='normal' if start+self.PAGE_SIZE < len(records) else 'disabled')
        if self.store.warning:
            ttk.Label(self.content, text=self.store.warning, wraplength=280).pack(fill='x', pady=8)
        if not visible:
            ttk.Label(self.content, text='No matching history.' if self.query.get() else 'Files and folders appear after you open or save a job.', wraplength=280).pack(fill='x', pady=12)
        for record in visible:
            path = Path(record['path'])
            card = ttk.Frame(self.content, padding=6, relief='solid', borderwidth=1)
            card.pack(fill='x', pady=(0, 8))
            image_host = ttk.Frame(card, width=96, height=72)
            image_host.pack(side='left', padx=(0, 8))
            image_host.pack_propagate(False)
            thumbnail = ttk.Label(image_host, image=self.folder_icon if home else '', text='' if home else 'Loading\npreview…', anchor='center')
            thumbnail.pack(fill='both', expand=True)
            text = ttk.Frame(card)
            text.pack(side='left', fill='both', expand=True)
            title = ttk.Label(text, text=path.name or str(path), font=('Segoe UI', 9, 'bold'), wraplength=190, cursor='hand2')
            title.pack(fill='x', anchor='w')
            date = ttk.Label(text, text=access_date(record.get('accessed_at')), font=('Segoe UI', 8), wraplength=200)
            date.pack(fill='x', pady=(2, 2))
            detail = ttk.Label(text, text=str(path) if home else 'Reading file details…', font=('Segoe UI', 8), wraplength=190)
            detail.pack(fill='x')
            row = {'record': record, 'path': path, 'title': title, 'date': date, 'detail': detail, 'thumbnail': thumbnail, 'preview': None}
            self.rows.append(row)
            activate = lambda selected=path: self.show('files', selected) if home else self.app.open_recent_job(selected)
            title.bind('<Button-1>', lambda _e, action=activate: action())
            for widget in (card, thumbnail, text, title, date, detail):
                self._bind_card(widget, activate)
            menu = tk.Menu(card, tearoff=False)
            if home:
                menu.add_command(label='Show recent files in this folder', command=activate)
                menu.add_command(label='Open file from folder…', command=lambda p=path: self.app.open_recent_folder(p))
            else:
                menu.add_command(label='Open', command=activate)
                menu.add_command(label='Open file from folder…', command=lambda p=path.parent: self.app.open_recent_folder(p))
                menu.add_command(label='File information', command=lambda r=row: self._information(r))
            ttk.Menubutton(card, text='▾', menu=menu, width=2).pack(side='right', anchor='s')
            self.pending[self.executor.submit(load_recent_preview, path, self.cache_root, home)] = row
            # Reserve the row menu before allocating the expanding text area.
            # Reapply wrapping on every page, not only on canvas resize events.
            text.pack_forget()
            text.pack(side='left', fill='both', expand=True)
            wrap = max(80, self.canvas.winfo_width() - 145)
            for widget in (title, date, detail):
                widget.configure(wraplength=wrap)
        self.canvas.yview_moveto(0)
        if self.pending and self.poll_job is None:
            self.poll_job = self.app.after(50, self._poll)

    def _poll(self):
        self.poll_job = None
        if self.closed:
            return
        from PIL import ImageTk
        for future, row in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[future]
            try:
                preview = future.result()
            except Exception as exc:
                preview = {'available': False, 'image': None, 'details': 'Preview unavailable: ' + str(exc)}
            row['preview'] = preview
            row['detail'].configure(text=(str(row['path']) if self.view == 'folders' and preview['available'] else preview['details']))
            if preview.get('image') is not None:
                image = ImageTk.PhotoImage(preview['image'], master=self.app)
                self.images.append(image)
                row['thumbnail'].configure(image=image, text='', width=0)
            elif self.view != 'folders':
                row['thumbnail'].configure(text='No preview' if preview['available'] else 'Unavailable')
        if self.pending:
            self.poll_job = self.app.after(50, self._poll)

    def _information(self, row):
        preview = row.get('preview') or {}
        lines = [str(row['path']), access_date(row['record'].get('accessed_at')), preview.get('details', 'Details are loading.')]
        if 'bytes' in preview:
            lines.append(f"Size: {preview['bytes']:,} bytes")
        if preview.get('modified_at'):
            lines.append(access_date(preview['modified_at']).replace('Accessed ', 'File modified ', 1))
        messagebox.showinfo('File information', '\n\n'.join(lines), parent=self.app)

    def _destroyed(self, event):
        if event.widget is not self.frame:
            return
        self.closed = True
        for job in (self.poll_job, self.search_job):
            if job:
                self.app.after_cancel(job)
        self.executor.shutdown(wait=False, cancel_futures=True)
