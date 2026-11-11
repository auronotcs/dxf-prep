"""One multi-selection recovery prompt; Close never means discard."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .recovery_manager import deletion_confirmation, recovery_saved_label


class RecoveryDialog(tk.Toplevel):
    def __init__(self, parent, entries, delete_selected):
        super().__init__(parent)
        self.title("Open recovery jobs")
        self.transient(parent)
        self.geometry("1100x520")
        self.minsize(780, 360)
        self.result = []
        self.entries = {str(i): entry for i, entry in enumerate(entries)}
        self.delete_selected = delete_selected
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Select recovery copies to open or delete. Close keeps them for later.").pack(anchor="w")
        ttk.Label(frame, text="Last Saved is the recovery copy's timestamp (America/Chicago), not the original job's save time.").pack(anchor="w", pady=(4, 8))
        table = ttk.Frame(frame)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("name", "saved", "source", "copy", "status"),
                                 show="headings", selectmode="extended")
        for key, title, width in (("name", "Job Name", 180), ("saved", "Last Saved (recovery copy)", 290),
                                  ("source", "Original filename / path", 260),
                                  ("copy", "Recovery copy", 220), ("status", "Status", 230)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=100)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        for key, entry in self.entries.items():
            self.tree.insert("", "end", iid=key, values=(entry.job_name,
                recovery_saved_label(entry.context, entry.path), entry.original_path or "No original saved file",
                entry.path.name, entry.status))
        self.status = tk.StringVar(self)
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=8)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.select_all_button = ttk.Button(buttons, text="Select All", command=self.select_all)
        self.select_all_button.pack(side="left")
        ttk.Label(buttons, text="  Ctrl-click / Shift-click to select multiple copies").pack(side="left")
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        self.delete_button = ttk.Button(buttons, text="Delete", command=self.delete)
        self.delete_button.pack(side="right", padx=8)
        self.open_button = ttk.Button(buttons, text="Open", command=self.open_selected)
        self.open_button.pack(side="right")
        self.tree.bind("<<TreeviewSelect>>", self.update_actions)
        self.tree.bind("<Control-a>", self.select_all)
        self.tree.bind("<Control-A>", self.select_all)
        self.tree.bind("<Return>", lambda _event: self.open_selected())
        self.update_actions()
        self.grab_set()
        self.tree.focus_set()

    def selected(self):
        ids = set(self.tree.selection())
        return [entry for key, entry in self.entries.items() if key in ids]

    def update_actions(self, _event=None):
        entries = self.selected()
        self.open_button.configure(state="normal" if entries and all(e.can_open for e in entries) else "disabled")
        self.delete_button.configure(state="normal" if entries and not any(e.active for e in entries) else "disabled")
        self.select_all_button.configure(state="normal" if self.entries else "disabled")
        self.status.set(f"{len(entries)} selected · {len(self.entries)} recovery copies. "
                        "Unreadable/missing copies can be deleted; open-document copies are protected.")

    def select_all(self, _event=None):
        self.tree.selection_set(tuple(self.entries))
        self.update_actions()
        return "break"

    def open_selected(self):
        selected = self.selected()
        if selected and all(e.can_open for e in selected):
            self.result = selected
            self.destroy()
        return "break"  # Do not run Treeview's Return binding after destruction.

    def delete(self):
        selected = self.selected()
        if not selected or any(e.active for e in selected):
            return
        # A scrollable confirmation keeps every name visible even for large batches.
        if not confirm_recovery_delete(self, selected):
            return
        result = self.delete_selected(selected)
        removed = {id(entry) for entry in result.removed}
        for key, entry in list(self.entries.items()):
            if id(entry) in removed:
                self.tree.delete(key)
                del self.entries[key]
        self.update_actions()
        self.status.set(f"Removed {len(result.removed)} selected recovery copies; {len(result.failed)} failed. "
                        "Original saved jobs were not deleted.")
        if result.failed or result.reference_error:
            details = "\n\n".join(f"{e.job_name} ({e.path.name}): {reason}" for e, reason in result.failed)
            messagebox.showerror("Recovery deletion incomplete", details + "\n\n" + result.reference_error, parent=self)

    def close(self):
        self.result = []
        self.destroy()


def confirm_recovery_delete(parent, entries):
    dialog = tk.Toplevel(parent)
    dialog.title(f"Delete {len(entries)} recovery copies?")
    dialog.transient(parent)
    dialog.geometry("680x400")
    dialog.minsize(500, 280)
    result = []
    frame = ttk.Frame(dialog, padding=12)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", height=12)
    scroll = ttk.Scrollbar(frame, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    text.insert("1.0", deletion_confirmation(entries))
    text.configure(state="disabled")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    buttons = ttk.Frame(frame)
    buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
    def accept():
        result.append(True)
        dialog.destroy()
    ttk.Button(buttons, text="Delete selected copies", command=accept).pack(side="right")
    cancel = ttk.Button(buttons, text="Cancel", command=dialog.destroy)
    cancel.pack(side="right", padx=8)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.grab_set()
    cancel.focus_set()
    parent.wait_window(dialog)
    if parent.winfo_exists():
        parent.grab_set()
    return bool(result)
