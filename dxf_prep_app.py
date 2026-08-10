from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

import classify_dxf as dxf


RUNTIME_TCL = Path(r"C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl")
if RUNTIME_TCL.exists():
    os.environ.setdefault("TCL_LIBRARY", str(RUNTIME_TCL / "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", str(RUNTIME_TCL / "tk8.6"))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


LAYER_ORDER = [
    dxf.LAYER_DRILL,
    dxf.LAYER_PROFILE_HOLES,
    dxf.LAYER_INNER,
    dxf.LAYER_OUTER,
    dxf.LAYER_ID_MARKING,
    dxf.LAYER_REVIEW,
]

DISPLAY_COLORS = {
    dxf.LAYER_DRILL: "#d9480f",
    dxf.LAYER_PROFILE_HOLES: "#1971c2",
    dxf.LAYER_INNER: "#2b8a3e",
    dxf.LAYER_OUTER: "#343a40",
    dxf.LAYER_ID_MARKING: "#868e96",
    dxf.LAYER_REVIEW: "#f08c00",
}

LABEL_FONT = ("Segoe UI", -12)

DXF_COLOR_NAMES = {
    "0": "black/default",
    "1": "red",
    "2": "yellow",
    "3": "green",
    "5": "blue",
    "7": "white/black",
    "8": "gray",
}

MATERIAL_SIZES = {
    "Auto": "auto",
    "Drawing": None,
    "48 x 96": (96.0, 48.0),
    "48 x 120": (120.0, 48.0),
    "60 x 120": (120.0, 60.0),
}


def app_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def bundled_resource_path(name: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / name
    return Path(__file__).with_name(name)


SETTINGS_PATH = app_directory() / "dxf_prep_settings.json"
DEFAULT_SETTINGS = {
    "default_directory": r"C:\Users\auron\Documents\Classification root",
    "last_directory": "",
    "invert_shift_scroll": False,
    "selection_tolerance_px": 12,
}


def estimate_material_layout(
    drawing_bounds: tuple[float, float, float, float] | None,
    selected_label: str,
) -> dict | None:
    if not drawing_bounds:
        return None

    min_x, min_y, max_x, max_y = drawing_bounds
    drawing_width = max(max_x - min_x, 1.0)
    drawing_height = max(max_y - min_y, 1.0)
    selected = MATERIAL_SIZES[selected_label]

    if selected == "auto":
        candidates = []
        for label, size in MATERIAL_SIZES.items():
            if not isinstance(size, tuple):
                continue
            sheet_width, sheet_height = size
            count = max(1, math.ceil(drawing_width / sheet_width))
            used_width = count * sheet_width
            height_over = max(0.0, drawing_height - sheet_height)
            width_over = max(0.0, drawing_width - used_width)
            waste_width = max(0.0, used_width - drawing_width)
            score = (
                height_over * 1000
                + width_over * 1000
                + waste_width
                + abs(sheet_height - drawing_height) * 0.2
            )
            candidates.append((score, label, sheet_width, sheet_height, count))
        _score, label, sheet_width, sheet_height, count = min(candidates)
    elif isinstance(selected, tuple):
        label = selected_label
        sheet_width, sheet_height = selected
        count = max(1, math.ceil(drawing_width / sheet_width))
    else:
        return None

    origin_x = min_x
    origin_y = min_y
    return {
        "label": label,
        "sheet_width": sheet_width,
        "sheet_height": sheet_height,
        "count": count,
        "origin_x": origin_x,
        "origin_y": origin_y,
    }


def sheet_content_boxes(
    component_boxes: list[tuple[float, float, float, float]],
    layout: dict | None,
) -> list[tuple[float, float, float, float]]:
    if not component_boxes or not layout:
        return []

    groups: list[list[tuple[float, float, float, float]]] = [[] for _ in range(layout["count"])]
    sheet_width = layout["sheet_width"]
    origin_x = layout["origin_x"]

    for box in component_boxes:
        min_x, min_y, max_x, max_y = box
        center_x = (min_x + max_x) / 2
        sheet_index = int((center_x - origin_x) // sheet_width)
        sheet_index = max(0, min(layout["count"] - 1, sheet_index))
        groups[sheet_index].append(box)

    merged = []
    for group in groups:
        if not group:
            continue
        merged.append(
            (
                min(box[0] for box in group),
                min(box[1] for box in group),
                max(box[2] for box in group),
                max(box[3] for box in group),
            )
        )
    return merged


def merge_boxes(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def boxes_are_close(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    x_gap_limit: float,
    y_gap_limit: float,
) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x_gap = max(bx1 - ax2, ax1 - bx2, 0.0)
    y_gap = max(by1 - ay2, ay1 - by2, 0.0)
    return x_gap <= x_gap_limit and y_gap <= y_gap_limit


def box_center_inside(
    box: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
) -> bool:
    min_x, min_y, max_x, max_y = box
    bound_min_x, bound_min_y, bound_max_x, bound_max_y = bounds
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    return bound_min_x <= center_x <= bound_max_x and bound_min_y <= center_y <= bound_max_y


def round_up_to_increment(value: float, increment: float) -> float:
    return max(increment, math.ceil(value / increment) * increment)


def sheet_size_for_bounds(bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = bounds
    content_width = max_x - min_x
    content_height = max_y - min_y
    sheet_height = 48.0 if content_height <= 48.0 else 60.0
    sheet_width = round_up_to_increment(content_width, 12.0)
    return sheet_height, sheet_width


def make_group_record(
    index: int,
    content_bounds: tuple[float, float, float, float],
) -> dict:
    min_x, min_y, _max_x, _max_y = content_bounds
    sheet_height, sheet_width = sheet_size_for_bounds(content_bounds)
    return {
        "index": index,
        "content_bounds": content_bounds,
        "sheet_height": sheet_height,
        "sheet_width": sheet_width,
        "sheet_bounds": (min_x, min_y, min_x + sheet_width, min_y + sheet_height),
    }


def sequential_content_groups(
    component_boxes: list[tuple[float, float, float, float]],
    layout: dict | None = None,
) -> list[dict]:
    if not component_boxes:
        return []

    sheet_width = layout["sheet_width"] if layout else 120.0
    x_gap_limit = max(12.0, sheet_width * 0.06)
    max_group_width = 120.0
    clusters: list[list[tuple[float, float, float, float]]] = [[]]

    for box in sorted(component_boxes, key=lambda item: (item[0], item[1])):
        if not clusters[-1]:
            clusters[-1].append(box)
            continue

        current_bounds = merge_boxes(clusters[-1])
        gap_from_current = box[0] - current_bounds[2]
        combined_width = max(current_bounds[2], box[2]) - min(current_bounds[0], box[0])
        if gap_from_current > x_gap_limit or combined_width > max_group_width:
            clusters.append([box])
            continue

        clusters[-1].append(box)

    merged = [merge_boxes(cluster) for cluster in clusters]
    return [make_group_record(index, bounds) for index, bounds in enumerate(merged, start=1)]


class DxfPrepApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DXF Prep")
        self.geometry("1240x820")
        self.minsize(980, 640)
        self.set_app_icon()

        self.input_path: Path | None = None
        self.analysis: dict | None = None
        self.component_layers: list[str] = []
        self.selected_component: int | None = None
        self.selected_components: set[int] = set()
        self.undo_stack: list[list[tuple[int, str, str]]] = []
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.annotation_widgets: list[tk.Widget] = []
        self.settings = self.load_settings()
        self.layer_visibility: dict[str, tk.BooleanVar] = {
            layer: tk.BooleanVar(value=True) for layer in LAYER_ORDER
        }
        self.reassign_var = tk.StringVar(value=dxf.LAYER_REVIEW)
        self.selected_layer_var = tk.StringVar(value="")
        self.material_var = tk.StringVar(value="Auto")
        self.status_var = tk.StringVar(value="Choose a DXF to begin.")
        self.file_var = tk.StringVar(value="No file loaded")

        self._build_layout()
        self._bind_canvas_events()

    def load_settings(self) -> dict:
        settings = DEFAULT_SETTINGS.copy()
        if SETTINGS_PATH.exists():
            try:
                loaded = json.loads(SETTINGS_PATH.read_text())
                if isinstance(loaded, dict):
                    settings.update({key: value for key, value in loaded.items() if key in DEFAULT_SETTINGS})
            except (OSError, json.JSONDecodeError):
                pass
        return settings

    def save_settings(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2))

    def preferred_directory(self) -> Path:
        for key in ("last_directory", "default_directory"):
            value = self.settings.get(key, "")
            if value and Path(value).exists():
                return Path(value)
        return Path.cwd()

    def remember_directory(self, path: Path) -> None:
        self.settings["last_directory"] = str(path)
        self.save_settings()

    def setting_bool(self, key: str) -> bool:
        value = self.settings.get(key, DEFAULT_SETTINGS[key])
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def selection_tolerance_px(self) -> float:
        try:
            return max(1.0, float(self.settings.get("selection_tolerance_px", DEFAULT_SETTINGS["selection_tolerance_px"])))
        except (TypeError, ValueError):
            return float(DEFAULT_SETTINGS["selection_tolerance_px"])

    def set_app_icon(self) -> None:
        icon_path = bundled_resource_path("dxf_prep_icon.ico")
        if not icon_path.exists():
            return
        try:
            self.iconbitmap(str(icon_path))
        except tk.TclError:
            pass

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(8, weight=1)

        ttk.Button(toolbar, text="Open DXF", command=self.open_file).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="Open Latest DXF", command=self.open_latest_file).grid(row=0, column=1, padx=(0, 12))
        ttk.Button(toolbar, text="Export Aluminum DXF", command=self.export_file).grid(row=0, column=2, padx=(0, 12))
        ttk.Button(toolbar, text="Export Acrylic DXF", command=self.export_acrylic_file).grid(row=0, column=3, padx=(0, 12))
        ttk.Button(toolbar, text="Settings", command=self.open_settings).grid(row=0, column=4, padx=(0, 12))
        ttk.Label(toolbar, text="View").grid(row=0, column=5, padx=(0, 6), sticky="e")
        material_combo = ttk.Combobox(
            toolbar,
            textvariable=self.material_var,
            values=list(MATERIAL_SIZES),
            state="readonly",
            width=10,
        )
        material_combo.grid(row=0, column=6, padx=(0, 8))
        material_combo.bind("<<ComboboxSelected>>", self.change_material_view)
        ttk.Button(toolbar, text="Fit", command=self.zoom_to_fit).grid(row=0, column=7, padx=(0, 12))
        ttk.Label(toolbar, textvariable=self.file_var).grid(row=0, column=8, sticky="w")

        body = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=(0, 0, 12, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        body.add(left, weight=0)

        ttk.Label(left, text="Layers").grid(row=0, column=0, sticky="w")
        self.layer_frame = ttk.Frame(left)
        self.layer_frame.grid(row=1, column=0, sticky="ew", pady=(6, 14))

        for row, layer in enumerate(LAYER_ORDER):
            check = ttk.Checkbutton(
                self.layer_frame,
                text=layer,
                variable=self.layer_visibility[layer],
                command=self.redraw,
            )
            check.grid(row=row, column=0, sticky="w", pady=2)
            swatch = tk.Canvas(self.layer_frame, width=18, height=14, highlightthickness=0)
            swatch.create_rectangle(1, 1, 17, 13, fill=DISPLAY_COLORS[layer], outline="")
            swatch.grid(row=row, column=1, padx=(8, 0))

        ttk.Label(left, text="Selected Component").grid(row=2, column=0, sticky="w")
        selected_panel = ttk.Frame(left)
        selected_panel.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        selected_panel.columnconfigure(0, weight=1)

        self.selected_text = tk.Text(selected_panel, width=36, height=12, wrap=tk.WORD)
        self.selected_text.grid(row=0, column=0, sticky="ew")
        self.selected_text.configure(state=tk.DISABLED)

        ttk.Label(selected_panel, text="Selected Layer").grid(row=1, column=0, sticky="w", pady=(12, 4))
        ttk.Combobox(
            selected_panel,
            textvariable=self.selected_layer_var,
            values=LAYER_ORDER,
            state="readonly",
        ).grid(row=2, column=0, sticky="ew")

        ttk.Label(selected_panel, text="Move To Layer").grid(row=3, column=0, sticky="w", pady=(12, 4))
        ttk.Combobox(
            selected_panel,
            textvariable=self.reassign_var,
            values=LAYER_ORDER,
            state="readonly",
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(selected_panel, text="Move Selected To Layer", command=self.reassign_selected).grid(
            row=5, column=0, sticky="ew"
        )
        ttk.Button(selected_panel, text="Zoom To Fit", command=self.zoom_to_fit).grid(row=6, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(selected_panel, text="Zoom To Sheet").grid(row=7, column=0, sticky="w", pady=(12, 4))
        self.sheet_button_frame = ttk.Frame(selected_panel)
        self.sheet_button_frame.grid(row=8, column=0, sticky="ew")
        self.sheet_button_frame.columnconfigure(0, weight=1)
        self.sheet_button_frame.columnconfigure(1, weight=1)

        right = ttk.Frame(body)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        body.add(right, weight=1)

        self.canvas = tk.Canvas(
            right,
            background="#f8f9fa",
            highlightthickness=1,
            highlightbackground="#ced4da",
            xscrollincrement=20,
            yscrollincrement=20,
        )
        x_scroll = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        status = ttk.Label(root, textvariable=self.status_var, anchor="w")
        status.grid(row=2, column=0, sticky="ew", pady=(8, 0))

    def _bind_canvas_events(self) -> None:
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self.select_at)
        self.canvas.bind("<Control-ButtonPress-1>", self.start_pan)
        self.canvas.bind("<Control-B1-Motion>", self.pan)
        self.canvas.bind("<Button-3>", self.deselect)
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.pan)
        self.canvas.bind("<MouseWheel>", self.zoom)
        self.bind_all("<Control-z>", self.undo_last_layer_change)
        self.bind_all("<Control-Z>", self.undo_last_layer_change)

    def build_sheet_buttons(self) -> None:
        for child in self.sheet_button_frame.winfo_children():
            child.destroy()
        if not self.analysis:
            return
        for index, group in enumerate(self.content_groups(), start=1):
            if self.group_has_review_unknown(group):
                button = tk.Button(
                    self.sheet_button_frame,
                    text=f"Sheet {index}",
                    command=lambda group_index=index - 1: self.zoom_to_sheet(group_index),
                    background=DISPLAY_COLORS[dxf.LAYER_REVIEW],
                    activebackground="#fab005",
                    foreground="#212529",
                    relief=tk.RAISED,
                    bd=1,
                    padx=6,
                    pady=2,
                )
            else:
                button = ttk.Button(
                    self.sheet_button_frame,
                    text=f"Sheet {index}",
                    command=lambda group_index=index - 1: self.zoom_to_sheet(group_index),
                )
            button.grid(row=(index - 1) // 2, column=(index - 1) % 2, sticky="ew", padx=2, pady=2)
        self.sheet_button_frame.columnconfigure(0, weight=1)
        self.sheet_button_frame.columnconfigure(1, weight=1)

    def group_has_review_unknown(self, group: dict) -> bool:
        if not self.analysis:
            return False
        sheet_bounds = group["sheet_bounds"]
        for index, component in enumerate(self.analysis["components"]):
            if self.component_layers[index] == dxf.LAYER_REVIEW and box_center_inside(component["bbox"], sheet_bounds):
                return True
        return False

    def open_settings(self) -> None:
        window = tk.Toplevel(self)
        window.title("Settings")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()

        default_var = tk.StringVar(value=self.settings.get("default_directory", ""))
        invert_var = tk.BooleanVar(value=self.setting_bool("invert_shift_scroll"))
        tolerance_var = tk.StringVar(value=str(int(self.selection_tolerance_px())))
        frame = ttk.Frame(window, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Default DXF Folder").grid(row=0, column=0, sticky="w", pady=(0, 6))
        entry = ttk.Entry(frame, textvariable=default_var, width=60)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        def browse() -> None:
            folder = filedialog.askdirectory(
                title="Choose Default DXF Folder",
                initialdir=str(self.preferred_directory()),
            )
            if folder:
                default_var.set(folder)

        ttk.Checkbutton(
            frame,
            text="Invert Shift + scroll horizontal direction",
            variable=invert_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 8))

        ttk.Label(frame, text="Selection Tolerance (pixels)").grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Spinbox(
            frame,
            from_=1,
            to=40,
            textvariable=tolerance_var,
            width=8,
        ).grid(row=3, column=1, sticky="e", pady=(0, 4))

        def save() -> None:
            folder = default_var.get().strip()
            if folder and not Path(folder).exists():
                messagebox.showerror("Folder not found", folder)
                return
            try:
                tolerance = int(float(tolerance_var.get()))
            except ValueError:
                messagebox.showerror("Invalid tolerance", "Selection tolerance must be a number.")
                return
            if not 1 <= tolerance <= 40:
                messagebox.showerror("Invalid tolerance", "Selection tolerance must be between 1 and 40 pixels.")
                return
            self.settings["default_directory"] = folder
            self.settings["invert_shift_scroll"] = bool(invert_var.get())
            self.settings["selection_tolerance_px"] = tolerance
            if not self.settings.get("last_directory"):
                self.settings["last_directory"] = folder
            self.save_settings()
            window.destroy()

        ttk.Button(frame, text="Browse", command=browse).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frame, text="Save", command=save).grid(row=4, column=1, sticky="e", pady=(8, 0))

    def open_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="Open DXF",
            filetypes=[("DXF files", "*.dxf"), ("All files", "*.*")],
            initialdir=str(self.preferred_directory()),
        )
        if not filename:
            return
        self.load_dxf(Path(filename))

    def open_latest_file(self) -> None:
        folder = self.preferred_directory()
        latest = self.find_latest_dxf(folder)
        if not latest:
            messagebox.showinfo("No DXF found", f"No source DXF files were found in:\n{folder}")
            return
        self.load_dxf(latest)

    def find_latest_dxf(self, folder: Path) -> Path | None:
        excluded_suffixes = (
            "_classified.dxf",
            "_acrylic_cutout.dxf",
            "_acrylic_cutout_test.dxf",
            "_app_test.dxf",
        )
        candidates = []
        try:
            files = folder.rglob("*.dxf")
            for path in files:
                name = path.name.lower()
                if any(name.endswith(suffix) for suffix in excluded_suffixes):
                    continue
                try:
                    candidates.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        except OSError as exc:
            messagebox.showerror("Could not search for DXF", str(exc))
            return None
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def load_dxf(self, path: Path) -> None:
        try:
            self.input_path = path
            self.remember_directory(self.input_path.parent)
            self.analysis = dxf.analyze_dxf(self.input_path)
            self.component_layers = [component["layer"] for component in self.analysis["components"]]
            self.selected_component = None
            self.selected_components.clear()
            self.undo_stack.clear()
            self.file_var.set(self.input_path.name)
            self.zoom_to_fit()
            self.build_sheet_buttons()
            self.update_status()
            self.update_selected_panel()
        except Exception as exc:
            messagebox.showerror("Could not read DXF", str(exc))

    def change_material_view(self, _event: tk.Event | None = None) -> None:
        self.zoom_to_fit()
        self.build_sheet_buttons()
        self.update_status()

    def export_file(self) -> None:
        if not self.analysis or not self.input_path:
            messagebox.showinfo("No DXF loaded", "Open a DXF before exporting.")
            return
        default_name = self.input_path.stem + "_classified.dxf"
        filename = filedialog.asksaveasfilename(
            title="Save Classified DXF",
            filetypes=[("DXF files", "*.dxf")],
            defaultextension=".dxf",
            initialdir=str(self.preferred_directory()),
            initialfile=default_name,
        )
        if not filename:
            return
        self.remember_directory(Path(filename).parent)
        self.sync_component_layers()
        try:
            dxf.export_classified_dxf(self.analysis, Path(filename))
            messagebox.showinfo("Export complete", f"Saved:\n{filename}")
        except Exception as exc:
            messagebox.showerror("Could not export DXF", str(exc))

    def export_acrylic_file(self) -> None:
        if not self.analysis or not self.input_path:
            messagebox.showinfo("No DXF loaded", "Open a DXF before exporting.")
            return
        default_name = self.input_path.stem + "_acrylic_cutout.dxf"
        filename = filedialog.asksaveasfilename(
            title="Save Acrylic Cutout DXF",
            filetypes=[("DXF files", "*.dxf")],
            defaultextension=".dxf",
            initialdir=str(self.preferred_directory()),
            initialfile=default_name,
        )
        if not filename:
            return
        self.remember_directory(Path(filename).parent)
        self.sync_component_layers()
        try:
            count = dxf.export_acrylic_cutout_dxf(self.analysis, Path(filename))
            messagebox.showinfo("Export complete", f"Saved {count} outside contours:\n{filename}")
        except Exception as exc:
            messagebox.showerror("Could not export acrylic DXF", str(exc))

    def sync_component_layers(self) -> None:
        if not self.analysis:
            return
        for index, layer in enumerate(self.component_layers):
            self.analysis["components"][index]["layer"] = layer

    def world_bounds(self) -> tuple[float, float, float, float] | None:
        if not self.analysis:
            return None
        boxes = [component["bbox"] for component in self.analysis["components"]]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def zoom_to_fit(self) -> None:
        bounds = self.fit_bounds()
        if not bounds:
            return
        self.zoom_to_bounds(bounds)

    def zoom_to_bounds(self, bounds: tuple[float, float, float, float]) -> None:
        min_x, min_y, max_x, max_y = bounds
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        canvas_width = max(self.canvas.winfo_width(), 100)
        canvas_height = max(self.canvas.winfo_height(), 100)
        self.scale = min((canvas_width - 60) / width, (canvas_height - 60) / height)
        self.offset_x = 30 - min_x * self.scale
        self.offset_y = 30 + max_y * self.scale
        self.redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def zoom_to_sheet(self, group_index: int) -> None:
        groups = self.content_groups()
        if group_index < 0 or group_index >= len(groups):
            return
        min_x, min_y, max_x, max_y = groups[group_index]["sheet_bounds"]
        self.zoom_to_sheet_bounds((min_x, min_y, max_x, max_y + 3.0))

    def zoom_to_sheet_bounds(self, bounds: tuple[float, float, float, float]) -> None:
        min_x, min_y, max_x, max_y = bounds
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        canvas_width = max(self.canvas.winfo_width(), 100)
        canvas_height = max(self.canvas.winfo_height(), 100)
        self.scale = min((canvas_width - 60) / width, (canvas_height - 60) / height)
        self.offset_x = 30 - min_x * self.scale
        self.offset_y = 30 + max_y * self.scale
        self.redraw()
        self.scroll_to_canvas_point(0, 0)

    def scroll_to_canvas_point(self, x: float, y: float) -> None:
        region = self.canvas.cget("scrollregion")
        if not region:
            return
        left, top, right, bottom = (float(value) for value in region.split())
        region_width = max(right - left, 1.0)
        region_height = max(bottom - top, 1.0)
        self.canvas.xview_moveto(max(0.0, min(1.0, (x - left) / region_width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (y - top) / region_height)))

    def estimate_sheet_layout(self) -> dict | None:
        return estimate_material_layout(self.world_bounds(), self.material_var.get())

    def fit_bounds(self) -> tuple[float, float, float, float] | None:
        drawing_bounds = self.world_bounds()
        layout = self.estimate_sheet_layout()
        if not drawing_bounds or not layout:
            return drawing_bounds
        min_x, min_y, max_x, max_y = drawing_bounds
        sheet_min_x = layout["origin_x"]
        sheet_min_y = layout["origin_y"]
        sheet_max_x = sheet_min_x + layout["sheet_width"] * layout["count"]
        sheet_max_y = sheet_min_y + layout["sheet_height"]
        return (
            min(min_x, sheet_min_x),
            min(min_y, sheet_min_y),
            max(max_x, sheet_max_x),
            max(max_y, sheet_max_y) + 3.0,
        )

    def world_to_screen(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return (x * self.scale + self.offset_x, -y * self.scale + self.offset_y)

    def screen_to_world(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return ((x - self.offset_x) / self.scale, -(y - self.offset_y) / self.scale)

    def redraw(self) -> None:
        for widget in self.annotation_widgets:
            widget.destroy()
        self.annotation_widgets.clear()
        self.canvas.delete("all")
        if not self.analysis:
            self.canvas.create_text(24, 24, anchor="nw", text="Open a DXF to preview classified geometry.", fill="#495057")
            self.canvas.configure(scrollregion=(0, 0, max(self.canvas.winfo_width(), 100), max(self.canvas.winfo_height(), 100)))
            return

        segments = self.analysis["segments"]
        for index, component in enumerate(self.analysis["components"]):
            layer = self.component_layers[index]
            if not self.layer_visibility[layer].get():
                continue
            color = DISPLAY_COLORS.get(layer, "#212529")
            width = 3 if index in self.selected_components else 1
            for segment_index in component["segment_indexes"]:
                self.draw_segment(segments[segment_index], color, width)

        for selected_index in self.selected_components:
            self.draw_selection_box(self.analysis["components"][selected_index]["bbox"])
        self.draw_group_boxes()
        self.update_scroll_region()

    def draw_segment(self, segment: dict, color: str, width: int) -> None:
        if segment["type"] == "ARC":
            points = self.arc_points(segment)
        else:
            points = [segment["p1"], segment["p2"]]
        screen_points = []
        for point in points:
            screen_points.extend(self.world_to_screen(point))
        if len(screen_points) >= 4:
            self.canvas.create_line(*screen_points, fill=color, width=width, smooth=False)

    @staticmethod
    def point_to_segment_distance(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        px, py = point
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_squared))
        nearest_x = x1 + t * dx
        nearest_y = y1 + t * dy
        return math.hypot(px - nearest_x, py - nearest_y)

    def distance_to_segment(self, world: tuple[float, float], segment: dict) -> float:
        if segment["type"] == "ARC":
            points = self.arc_points(segment)
        else:
            points = [segment["p1"], segment["p2"]]
        return min(
            self.point_to_segment_distance(world, start, end)
            for start, end in zip(points, points[1:])
        )

    def distance_to_component(self, world: tuple[float, float], component: dict) -> float:
        segments = self.analysis["segments"]
        return min(
            self.distance_to_segment(world, segments[segment_index])
            for segment_index in component["segment_indexes"]
        )

    def arc_points(self, segment: dict) -> list[tuple[float, float]]:
        cx, cy = segment["center"]
        radius = segment["radius"]
        start = segment["start_angle"]
        end = segment["end_angle"]
        sweep = end - start
        if sweep < 0:
            sweep += 360
        steps = max(4, min(48, int(abs(sweep) / 8) + 1))
        points = []
        for i in range(steps + 1):
            angle = math.radians(start + sweep * i / steps)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        return points

    def draw_selection_box(self, bbox: tuple[float, float, float, float]) -> None:
        min_x, min_y, max_x, max_y = bbox
        x1, y1 = self.world_to_screen((min_x, max_y))
        x2, y2 = self.world_to_screen((max_x, min_y))
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#c92a2a", width=2, dash=(6, 4))

    def draw_box_label(self, x_left: float, x_right: float, y: float, text: str, color: str) -> None:
        header_height = 18
        label = tk.Label(
            self.canvas,
            text=text,
            fg=color,
            bg="#f8f9fa",
            font=LABEL_FONT,
            anchor="w",
            padx=4,
            pady=0,
            bd=1,
            relief=tk.SOLID,
        )
        self.annotation_widgets.append(label)
        self.canvas.create_window(
            x_left,
            y - header_height,
            anchor="nw",
            window=label,
            width=max(90, int(x_right - x_left)),
            height=header_height,
        )

    def content_groups(self) -> list[dict]:
        if not self.analysis:
            return []
        boxes = [
            component["bbox"]
            for index, component in enumerate(self.analysis["components"])
            if self.component_layers[index] != dxf.LAYER_ID_MARKING
        ]
        return sequential_content_groups(boxes, self.estimate_sheet_layout())

    def draw_group_boxes(self) -> None:
        for group in self.content_groups():
            min_x, min_y, max_x, max_y = group["sheet_bounds"]
            pad = 0.25
            x1, y1 = self.world_to_screen((min_x - pad, max_y + pad))
            x2, y2 = self.world_to_screen((max_x + pad, min_y - pad))
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#868e96", width=2, dash=(6, 4))
            sheet_height = int(group["sheet_height"])
            sheet_width = int(group["sheet_width"])
            self.draw_box_label(
                x1,
                x2,
                y1 - 5,
                f"group {group['index']} sheet size {sheet_height}\" x {sheet_width}\"",
                "#868e96",
            )

    def select_at(self, event: tk.Event) -> None:
        if not self.analysis:
            return
        if event.state & 0x0004:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        world = self.screen_to_world((canvas_x, canvas_y))
        candidates = []
        pad = max(0.08, self.selection_tolerance_px() / max(self.scale, 1))
        for index, component in enumerate(self.analysis["components"]):
            layer = self.component_layers[index]
            if not self.layer_visibility[layer].get():
                continue
            min_x, min_y, max_x, max_y = component["bbox"]
            if min_x - pad <= world[0] <= max_x + pad and min_y - pad <= world[1] <= max_y + pad:
                distance = self.distance_to_component(world, component)
                if distance > pad:
                    continue
                area = max((max_x - min_x) * (max_y - min_y), 0.0001)
                review_priority = 0 if layer == dxf.LAYER_REVIEW else 1
                candidates.append((review_priority, distance, area, index))
        if candidates:
            selected = min(candidates)[3]
            if event.state & 0x0001:
                if selected in self.selected_components:
                    self.selected_components.remove(selected)
                else:
                    self.selected_components.add(selected)
                self.selected_component = selected if selected in self.selected_components else (
                    min(self.selected_components) if self.selected_components else None
                )
            else:
                self.selected_components = {selected}
                self.selected_component = selected
            self.update_selected_panel()
            self.redraw()

    def deselect(self, _event: tk.Event | None = None) -> None:
        self.selected_component = None
        self.selected_components.clear()
        self.update_selected_panel()
        self.redraw()

    def start_pan(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def pan(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def zoom(self, event: tk.Event) -> None:
        if not self.analysis:
            return
        if event.state & 0x0001:
            self.shift_scroll_horizontal(event)
            return
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        before = self.screen_to_world((canvas_x, canvas_y))
        self.scale *= factor
        after_screen = self.world_to_screen(before)
        self.offset_x += canvas_x - after_screen[0]
        self.offset_y += canvas_y - after_screen[1]
        self.redraw()

    def shift_scroll_horizontal(self, event: tk.Event) -> None:
        direction = -1 if event.delta > 0 else 1
        if self.setting_bool("invert_shift_scroll"):
            direction *= -1
        self.canvas.xview_scroll(direction * 5, "units")

    def update_scroll_region(self) -> None:
        bounds = self.canvas.bbox("all")
        if not bounds:
            return
        pad = 80
        left = min(bounds[0] - pad, 0)
        top = min(bounds[1] - pad, 0)
        right = max(bounds[2] + pad, self.canvas.winfo_width())
        bottom = max(bounds[3] + pad, self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(left, top, right, bottom))

    def reassign_selected(self) -> None:
        if not self.selected_components:
            return
        target_layer = self.reassign_var.get()
        changes = []
        for index in sorted(self.selected_components):
            old_layer = self.component_layers[index]
            if old_layer == target_layer:
                continue
            changes.append((index, old_layer, target_layer))
        if not changes:
            return
        self.undo_stack.append(changes)
        self.undo_stack = self.undo_stack[-100:]
        for index, _old_layer, _new_layer in changes:
            self.component_layers[index] = target_layer
        if self.selected_component is not None:
            self.selected_layer_var.set(self.component_layers[self.selected_component])
        self.build_sheet_buttons()
        self.update_status()
        self.update_selected_panel()
        self.redraw()

    def undo_last_layer_change(self, _event: tk.Event | None = None) -> str:
        if not self.undo_stack:
            return "break"
        changes = self.undo_stack.pop()
        restored_indexes = []
        for index, old_layer, _new_layer in changes:
            if 0 <= index < len(self.component_layers):
                self.component_layers[index] = old_layer
                restored_indexes.append(index)
        if restored_indexes:
            self.selected_components = set(restored_indexes)
            self.selected_component = restored_indexes[0]
        else:
            self.selected_component = None
            self.selected_components.clear()
        if self.selected_component is not None:
            self.selected_layer_var.set(self.component_layers[self.selected_component])
        self.build_sheet_buttons()
        self.update_status()
        self.update_selected_panel()
        self.redraw()
        return "break"

    def update_status(self) -> None:
        if not self.analysis:
            self.status_var.set("Choose a DXF to begin.")
            return
        counts = Counter(self.component_layers)
        parts = [f"{layer}: {counts[layer]}" for layer in LAYER_ORDER]
        layout = self.estimate_sheet_layout()
        if layout:
            parts.append(f"Nest: {layout['count']} sheet(s) {layout['label']}")
            parts.append(f"Groups: {len(self.content_groups())}")
        cleanup = self.analysis.get("cleanup_summary", Counter())
        cleanup_parts = []
        if cleanup.get("duplicate_entities"):
            cleanup_parts.append(f"{cleanup['duplicate_entities']} duplicates")
        if cleanup.get("degenerate_lines"):
            cleanup_parts.append(f"{cleanup['degenerate_lines']} zero-length lines")
        if cleanup_parts:
            parts.append("Cleaned: " + ", ".join(cleanup_parts))
        self.status_var.set("  |  ".join(parts))

    def update_selected_panel(self) -> None:
        self.selected_text.configure(state=tk.NORMAL)
        self.selected_text.delete("1.0", tk.END)
        if self.selected_component is None or not self.analysis:
            self.selected_layer_var.set("")
            self.selected_text.insert(tk.END, "Click a contour or marking in the preview.")
        else:
            component = self.analysis["components"][self.selected_component]
            self.selected_layer_var.set(self.component_layers[self.selected_component])
            min_x, min_y, max_x, max_y = component["bbox"]
            width = max_x - min_x
            height = max_y - min_y
            color_name = DXF_COLOR_NAMES.get(component["color"], component["color"])
            text = (
                f"Selected: {len(self.selected_components)}\n"
                f"Layer: {self.component_layers[self.selected_component]}\n"
                f"Closed: {component['closed']}\n"
                f"DXF color: {component['color']} ({color_name})\n"
                f"Width: {width:.4f} in\n"
                f"Height: {height:.4f} in\n"
                f"Entities: {len(component['entity_indexes'])}\n"
                f"Open nodes: {component['open_nodes']}"
            )
            self.selected_text.insert(tk.END, text)
        self.selected_text.configure(state=tk.DISABLED)


if __name__ == "__main__":
    DxfPrepApp().mainloop()
