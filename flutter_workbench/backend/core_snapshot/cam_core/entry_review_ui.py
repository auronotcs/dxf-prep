"""Scrollable, complete entry notices with an explicit preview-finalizing stage."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


class EntryPlacementReviewDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Preparing cut preview")
        self.transient(parent)
        self.ready = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.minsize(480, 320)
        self.geometry(f"{min(800, self.winfo_screenwidth()-80)}x{min(580, self.winfo_screenheight()-120)}")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.heading = ttk.Label(self, text="Preparing preview and entry review…", font=("Segoe UI", 11, "bold"))
        self.heading.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        self.status = ttk.Label(self, text="Path calculation is finished. Updating the display; please wait.")
        self.status.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
        self.text = ScrolledText(self, wrap="word", width=78, height=20, padx=8, pady=8,
                                 font=("Segoe UI", 10), state="disabled")
        self.text.grid(row=2, column=0, sticky="nsew", padx=12)
        self.footer = ttk.Label(self, text="", wraplength=740)
        self.footer.grid(row=3, column=0, sticky="ew", padx=12, pady=8)
        self.ok = ttk.Button(self, text="OK", command=self.close, state="disabled")
        self.ok.grid(row=4, column=0, sticky="e", padx=12, pady=(0, 12))
        self.bind("<Escape>", lambda event: self.close())
        self.bind("<Configure>", lambda event: self.footer.configure(wraplength=max(200, self.winfo_width()-30)))
        self.grab_set()
        self.update_idletasks()

    def show_review(self, lines):
        self.title("Entry placement review")
        self.heading.configure(text=f"{len(lines)} adjusted entry position(s) — complete list")
        self.status.configure(text="Preview ready. Scroll to review every entry; text can be selected and copied.")
        self.text.configure(state="normal")
        self.text.insert("1.0", "\n\n".join(lines))
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        self.footer.configure(text="The programmed contour and final cut depth were not changed. Preflight retains a warning for review.")
        self.ready = True
        self.ok.configure(state="normal")
        self.ok.focus_set()

    def close(self):
        if self.ready:
            self.destroy()
