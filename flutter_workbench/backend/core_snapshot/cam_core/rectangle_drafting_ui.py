"""Canvas-owned rectangle preview and numeric editing; no modal dialog."""
import tkinter as tk
from tkinter import ttk

from .model import Rectangle
from .rectangle_drafting import RectangleDraft


class RectangleDrawing:
    def __init__(self, app):
        self.app = app
        self.draft = None
        self.phase = "ready"
        self.last_dimension = "width"
        self.context = None
        self.layer = None
        self.last_valid_endpoint = None
        self.updating = False
        self.frames = {}
        self.entries = {}
        self.variables = {}
        self.unit_labels = {}

    def _context(self):
        return id(self.app.project), self.app.document_id, self.app.document_revision

    def _current(self):
        if self.draft is not None and self.context != self._context():
            self.cancel()
            self.app.status.set("Rectangle preview canceled because the document changed.")
        return self.draft is not None

    def sync_mode(self):
        if self.app.mode.get() != "rectangle":
            self.cancel()
        elif self.draft is None:
            self.app.canvas.focus_set()
            self.app.status.set("Rectangle: drag opposite corners, then type W/H. Tab switches dimensions; Enter creates; Esc cancels.")

    def _ensure_controls(self):
        if self.frames:
            return
        for key, label in (("width", "W"), ("height", "H")):
            frame = self.frames[key] = ttk.Frame(self.app.canvas, padding=3)
            ttk.Label(frame, text=label).pack(side="left", padx=(0, 3))
            variable = self.variables[key] = tk.StringVar(master=self.app)
            entry = self.entries[key] = ttk.Entry(frame, textvariable=variable, width=11)
            entry.pack(side="left")
            self.unit_labels[key] = ttk.Label(frame)
            self.unit_labels[key].pack(side="left", padx=(3, 0))
            entry.bind("<FocusIn>", lambda _event, dimension=key: self._remember(dimension))
            entry.bind("<KeyPress>", lambda _event, dimension=key: self._remember(dimension))
            entry.bind("<Tab>", self.switch_dimension)
            entry.bind("<Shift-Tab>", self.switch_dimension)
            entry.bind("<Return>", lambda _event: self.commit())
            entry.bind("<KP_Enter>", lambda _event: self.commit())
            entry.bind("<Escape>", lambda _event: self._escape())
            variable.trace_add("write", lambda *_args, dimension=key: self._edited(dimension))

    def _remember(self, key):
        self.last_dimension = key

    def _focus(self, key):
        self._ensure_controls()
        self.last_dimension = key
        self.entries[key].focus_set()
        self.entries[key].selection_range(0, "end")
        self.entries[key].icursor("end")

    def switch_dimension(self, _event=None):
        if self._current():
            self._focus("height" if self.last_dimension == "width" else "width")
        return "break"

    def press(self, point):
        if self._current() and self.phase == "adjust":
            self._focus(self.last_dimension)
            self.app.status.set("Rectangle: Enter creates this preview; Esc cancels it.")
            return
        self.cancel()
        self._ensure_controls()
        point = self.app._snap_point(point)
        self.draft = RectangleDraft(point, point)
        self.context = self._context()
        self.layer = self.app._active_geometry_layer()
        self.phase = "drag"
        self.app.start_world = None
        self.app.canvas.focus_set()
        self._set_dimensions()
        self.draw()

    def _set_dimensions(self):
        self.updating = True
        try:
            for key, value in zip(("width", "height"), self.draft.dimensions):
                self.variables[key].set(f"{value:.12g}")
        finally:
            self.updating = False

    def motion(self, point):
        if self._current() and self.phase == "drag":
            self.draft.pointer = self.app._snap_point(point)
            self._set_dimensions()
            self.draw()

    def release(self, point):
        if not self._current() or self.phase != "drag":
            return
        self.motion(point)
        if self.draft.start.distance_to(self.draft.pointer) * self.app.scale < 1:
            self.cancel()
            self.sync_mode()
            return
        self.phase = "adjust"
        self._focus(self.last_dimension)
        self.app.status.set("Rectangle preview: type the highlighted dimension; Tab switches W/H; Enter creates; Esc cancels.")

    def _endpoint(self):
        return self.draft.endpoint(self.variables["width"].get(), self.variables["height"].get())

    def _edited(self, key):
        if self.updating or not self._current():
            return
        self.last_dimension = key
        self.phase = "adjust"
        self.draw()

    def draw(self):
        if not self._current():
            return
        try:
            end = self._endpoint()
            self.last_valid_endpoint = end
        except ValueError as exc:
            end = self.last_valid_endpoint or self.draft.pointer
            if self.phase == "adjust":
                self.app.status.set(str(exc) + " Rectangle remains a preview; nothing added.")
        start_xy = self.app.world_to_canvas(self.draft.start)
        end_xy = self.app.world_to_canvas(end)
        self.app.canvas.delete("rectangle_draft")
        self.app.canvas.create_rectangle(*start_xy, *end_xy,
            outline=self.app._appearance_color("selection"), dash=(4, 3), width=2, tags="rectangle_draft")
        self._ensure_controls()
        positions = {
            "width": ((start_xy[0] + end_xy[0]) / 2, max(start_xy[1], end_xy[1]) + 8),
            "height": (min(start_xy[0], end_xy[0]) - 8, (start_xy[1] + end_xy[1]) / 2),
        }
        placed = {}
        for key, (x, y) in positions.items():
            frame = self.frames[key]
            self.unit_labels[key].configure(text=self.app.project.units)
            width, height = frame.winfo_reqwidth(), frame.winfo_reqheight()
            x -= width / 2 if key == "width" else width
            if key == "height":
                y -= height / 2
            x = max(4, min(x, max(4, self.app.canvas.winfo_width() - width - 4)))
            y = max(4, min(y, max(4, self.app.canvas.winfo_height() - height - 4)))
            if key == "height":
                wx, wy, ww, wh = placed["width"]
                if x < wx + ww and x + width > wx and y < wy + wh and y + height > wy:
                    y = wy - height - 4 if wy >= height + 8 else wy + wh + 4
            placed[key] = (x, y, width, height)
            frame.place(x=x, y=y)
            frame.lift()

    def commit(self):
        if not self._current():
            return "break"
        try:
            end = self._endpoint()
        except ValueError as exc:
            self.app.status.set(str(exc) + " Nothing added.")
            self._focus("width" if str(exc).startswith("Width") else "height")
            return "break"
        if self.app._is_layer_locked(self.layer):
            self.app.status.set(f"Layer {self.layer} is locked; rectangle was not added.")
            return "break"
        entity = Rectangle(corner_a=self.draft.start, corner_b=end, layer=self.layer)
        self.cancel()
        self.app.project.entities.append(entity)
        self.app.selected_ids = {entity.id}
        self.app._set_dirty(True)
        self.app._update_sheet_after_geometry_change()
        self.app._redraw()
        self.app._record_history("Add rectangle", "□")
        self.app.canvas.focus_set()
        self.app.status.set("Added rectangle. Drag the next rectangle; Esc returns to Select.")
        return "break"

    def cancel(self):
        self.draft = None
        self.context = None
        self.last_valid_endpoint = None
        self.phase = "ready"
        self.app.start_world = None
        self.app.canvas.delete("rectangle_draft")
        for frame in self.frames.values():
            frame.place_forget()

    def _escape(self):
        self.app.cancel_interactive_mode()
        return "break"
