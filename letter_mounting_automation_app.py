from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import math
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "holegen_libs"))

# The bundled Python runtime does not register Tcl/Tk in the usual Windows
# locations, so point tkinter at the packaged scripts before importing it.
if getattr(sys, "frozen", False):
    packaged_tcl = Path(getattr(sys, "_MEIPASS", ROOT)) / "tcl"
    if packaged_tcl.exists():
        os.environ["TCL_LIBRARY"] = str(packaged_tcl / "tcl8.6")
        os.environ["TK_LIBRARY"] = str(packaged_tcl / "tk8.6")
else:
    runtime_tcl = Path(
        r"C:\Users\auron\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\tcl"
    )
    if runtime_tcl.exists():
        os.environ["TCL_LIBRARY"] = str(runtime_tcl / "tcl8.6")
        os.environ["TK_LIBRARY"] = str(runtime_tcl / "tk8.6")

import ezdxf
import tkinter as tk
from PIL import Image, ImageTk
from shapely.geometry import Point, Polygon
from tkinter import filedialog, messagebox, ttk

from hole_generator import (
    generate_stud_holes,
    labels_for_text_components,
    order_polygons_for_text,
    recover_material_polygons,
)
from backer_pin_generator import discover_backer_panels, generate_backer_pin_job
from keyhole_generator import (
    approximate_led_centerline_points,
    estimate_channel_letter_mass,
    generate_from_reference,
    generate_procedural,
)
from mounting_pattern_generator import generate_pattern
from render_dxf_overview import render as render_dxf_overview, render_tile_review


APP_TITLE = "Letter Mounting Automation"
STUD_MODE = "stud holes"
KEYHOLE_MODE = "keyholes"
PATTERN_MODE = "mounting pattern"
BACKER_MODE = "backer pins"
SETTINGS_DIRECTORY_NAME = "Letter Mounting Automation"
SETTINGS_FILE_NAME = "settings.json"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    return base / name


def user_settings_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else ROOT
    return base / SETTINGS_DIRECTORY_NAME / SETTINGS_FILE_NAME


def load_last_open_directory(settings_path: Path | None = None) -> Path | None:
    path = settings_path or user_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_directory = data.get("last_open_directory")
        if not isinstance(raw_directory, str) or not raw_directory.strip():
            return None
        directory = Path(raw_directory)
        return directory if directory.is_dir() else None
    except (OSError, TypeError, ValueError):
        return None


def save_last_open_directory(directory: Path, settings_path: Path | None = None) -> None:
    path = settings_path or user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_open_directory": str(Path(directory).resolve())}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def next_version_path(directory: Path, base_name: str, extension: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for version in range(1, 1000):
        candidate = directory / f"{base_name} v{version}.{extension.lstrip('.')}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find an unused output version for {base_name!r}.")


def finite_positive(value: str, label: str, allow_zero: bool = False) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error
    lower_ok = parsed >= 0 if allow_zero else parsed > 0
    if not math.isfinite(parsed) or not lower_ok:
        comparison = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {comparison}.")
    return parsed


def audit_dxf(path: Path) -> None:
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    if auditor.errors:
        raise RuntimeError(f"DXF audit found {len(auditor.errors)} error(s) in {path.name}.")


def pattern_source_for_review(source: Path, review: dict | None) -> Path:
    """Use the current generated master when the Pattern tab follows a hole workflow."""
    source = Path(source)
    if not review or review.get("mode") not in {STUD_MODE, KEYHOLE_MODE, BACKER_MODE}:
        return source
    for artifact in review.get("artifacts") or []:
        if artifact.get("role") != "master" or not artifact.get("path"):
            continue
        candidate = Path(artifact["path"])
        if candidate.is_file():
            return candidate
    return source


class MountingAutomationApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1020, 680)
        self.option_add("*Font", ("Segoe UI", 9))
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("openai.lettermountingautomation")
        except Exception:
            pass
        icon = resource_path("assets/letter_mounting_icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self.events: queue.Queue = queue.Queue()
        self.source_path: Path | None = None
        self.settings_path = user_settings_path()
        self.last_open_directory = load_last_open_directory(self.settings_path)
        self.last_output_directory: Path | None = None
        self.preview_original: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.source_preview_path: Path | None = None
        self.source_preview_metadata: dict | None = None
        self.preview_is_source = False
        self.source_polygons = []
        self.selected_logo_indices: set[int] = set()
        self.backer_panels = []
        self.selected_backer_indices: set[int] = set()
        self.progress_markers: dict[tuple[float, float, float, float], str] = {}
        self.display_scale = 1.0
        self.display_left = 0.0
        self.display_top = 0.0
        self.run_started_at: float | None = None
        self.review: dict | None = None
        self.preview_mode = "source"
        self.action_undo_stack: list[dict] = []
        self.entry_undo_history: dict[str, list[str]] = {}
        self.preview_zoom = 1.0
        self.preview_pan_x = 0.0
        self.preview_pan_y = 0.0
        self.pan_origin: tuple[int, int, float, float] | None = None
        self.preview_directory = tempfile.TemporaryDirectory(prefix="letter-mounting-preview-")
        self.action_buttons: list[ttk.Button] = []
        self.logo_select_buttons: list[ttk.Checkbutton] = []
        self.backer_select_buttons: list[ttk.Checkbutton] = []
        self.busy = False
        self._build_variables()
        self._build_ui()
        self._install_undo_support()
        self._toggle_electrical()
        self._toggle_physical_dashes()
        self._configure_review_controls()
        self.after(150, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close_app)

    def _build_variables(self) -> None:
        self.file_name = tk.StringVar(value="No file loaded")
        self.preview_name = tk.StringVar(value="Open a DXF to begin")
        self.lettering = tk.StringVar()
        self.pattern_label = tk.StringVar()
        self.add_pattern = tk.BooleanVar(value=False)
        self.include_electrical = tk.BooleanVar(value=True)
        self.physical_dashes = tk.BooleanVar(value=False)
        self.keyhole_template = tk.StringVar(value=str(resource_path("Keyhole example.dxf")))
        self.handmade_reference = tk.StringVar()

        self.stud_diameter = tk.StringVar(value="0.230")
        self.stud_edge_clearance = tk.StringVar(value="0.600")
        self.logo_edge_clearance = tk.StringVar(value="0.600")
        self.stud_spacing = tk.StringVar(value="1.000")
        self.electrical_diameter = tk.StringVar(value="0.375")
        self.mount_spacing = tk.StringVar(value="2.700")
        self.round_mount_diameter = tk.StringVar(value="0.250")
        self.coil_clearance = tk.StringVar(value="0.625")
        self.maximum_reach = tk.StringVar(value="10.000")
        self.feature_gap = tk.StringVar(value="0.200")
        self.pounds_per_mount = tk.StringVar(value="5.000")
        self.led_weight_per_foot = tk.StringVar(value="0.030")
        self.led_path_clearance = tk.StringVar(value="0.750")
        self.mass_summary = tk.StringVar(value="Estimated assembly: open a DXF")
        self.dash_length = tk.StringVar(value="0.250")
        self.dash_gap = tk.StringVar(value="0.250")
        self.label_height = tk.StringVar(value="1.000")
        self.label_offset = tk.StringVar(value="0.750")
        self.status = tk.StringVar(value="Open a DXF to begin.")
        self.logo_select_mode = tk.BooleanVar(value=False)
        self.logo_summary = tk.StringVar(value="Logos selected: 0")
        self.backer_select_mode = tk.BooleanVar(value=False)
        self.backer_summary = tk.StringVar(value="Backers selected: 0")
        self.letter_pin_diameter = tk.StringVar(value="0.125")
        self.backer_pin_diameter = tk.StringVar(value="0.140")
        self.pin_edge_clearance = tk.StringVar(value="0.125")
        self.pin_spacing = tk.StringVar(value="0.500")
        self.workflow_summary = tk.StringVar(value="Workflow: Stud Holes")
        self.keyhole_advanced_visible = tk.BooleanVar(value=False)
        self.progress_text = tk.StringVar(value="Ready")
        self.progress_value = tk.IntVar(value=0)
        self.led_weight_per_foot.trace_add("write", lambda *_args: self._update_mass_summary())

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(6, weight=1)

        ttk.Button(toolbar, text="Open DXF", command=self._browse_source).grid(row=0, column=0, padx=(0, 8))
        self.generate_button = ttk.Button(
            toolbar,
            text="Generate Stud Review",
            command=self._generate_active_workflow,
            state="disabled",
        )
        self.generate_button.grid(row=0, column=1, padx=(0, 8))
        self.action_buttons = [self.generate_button]

        self.output_menu = tk.Menu(toolbar, tearoff=False)
        self.output_menu.add_command(label="Save all approved DXFs...", command=self._output_review)
        self.output_menu.add_separator()
        self.output_menu.add_command(
            label="Save current review DXF...",
            command=self._output_current_review,
        )
        self.output_menu.add_command(
            label="Save master DXF...",
            command=lambda: self._output_review_role("master"),
        )
        self.output_menu.add_command(
            label="Save letters-only DXF...",
            command=lambda: self._output_review_role("letters"),
        )
        self.output_menu.add_command(
            label="Save backers-only DXF...",
            command=lambda: self._output_review_role("backers"),
        )
        self.output_menu.add_command(
            label="Save mounting-pattern DXF...",
            command=lambda: self._output_review_role("pattern"),
        )
        self.output_button = ttk.Menubutton(
            toolbar,
            text="Output...",
            menu=self.output_menu,
            state="disabled",
        )
        self.output_button.grid(row=0, column=2, padx=(0, 8))
        self.clear_generation_button = ttk.Button(
            toolbar,
            text="Clear Generation",
            command=self._clear_generation,
            state="disabled",
        )
        self.clear_generation_button.grid(row=0, column=3, padx=(0, 8))
        ttk.Button(toolbar, text="Fit", command=self.zoom_to_fit).grid(row=0, column=4, padx=(0, 12))
        ttk.Label(toolbar, textvariable=self.workflow_summary, foreground="#245f83").grid(
            row=0, column=5, padx=(0, 12), sticky="w"
        )
        ttk.Label(toolbar, textvariable=self.file_name).grid(row=0, column=6, sticky="w")

        body = ttk.PanedWindow(root, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")

        settings_host = ttk.Frame(body, width=370)
        settings_host.grid_propagate(False)
        settings_host.rowconfigure(0, weight=1)
        settings_host.columnconfigure(0, weight=1)
        body.add(settings_host, weight=0)

        self.workflow_notebook = ttk.Notebook(settings_host)
        self.workflow_notebook.grid(row=0, column=0, sticky="nsew")
        self.settings_canvases = []
        self.workflow_modes = [STUD_MODE, BACKER_MODE, KEYHOLE_MODE, PATTERN_MODE]

        stud_tab, stud_settings = self._make_settings_tab(self.workflow_notebook, "Stud Holes")
        backer_tab, backer_settings = self._make_settings_tab(self.workflow_notebook, "Backer + Pins")
        keyhole_tab, keyhole_settings = self._make_settings_tab(self.workflow_notebook, "Keyholes")
        pattern_tab, pattern_settings = self._make_settings_tab(self.workflow_notebook, "Pattern")

        stud_job = self._build_letter_job_section(stud_settings, "Stud-mounted lettering")
        stud_job.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        stud = ttk.LabelFrame(stud_settings, text="Stud holes (inches)", padding=10)
        stud.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        stud.columnconfigure(1, weight=1)
        self._setting_row(stud, 0, "Hole diameter", self.stud_diameter)
        self._setting_row(stud, 1, "Edge clearance", self.stud_edge_clearance)
        self._setting_row(stud, 2, "Logo edge clearance", self.logo_edge_clearance)
        self._setting_row(stud, 3, "Hole spacing", self.stud_spacing)

        backer_job = ttk.LabelFrame(backer_settings, text="Backer-panel job", padding=10)
        backer_job.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        backer_job.columnconfigure(0, weight=1)
        ttk.Label(backer_job, textvariable=self.backer_summary, foreground="#245f83").grid(
            row=0, column=0, sticky="w"
        )
        backer_select = ttk.Checkbutton(
            backer_job,
            text="Select backer panels in preview",
            variable=self.backer_select_mode,
            command=self._backer_mode_changed,
            state="disabled",
        )
        backer_select.grid(row=1, column=0, sticky="w", pady=(7, 3))
        self.backer_select_buttons.append(backer_select)
        ttk.Label(
            backer_job,
            text="Detected panels are selected automatically. Click a panel to include or remove it.",
            foreground="#59636e",
            wraplength=310,
        ).grid(row=2, column=0, sticky="w")
        self._build_pattern_option(backer_job, start_row=3)

        backer_options = ttk.LabelFrame(backer_settings, text="Roll-pin registration (inches)", padding=10)
        backer_options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        backer_options.columnconfigure(1, weight=1)
        self._setting_row(backer_options, 0, "Letter hole diameter", self.letter_pin_diameter)
        self._setting_row(backer_options, 1, "Backer clearance diameter", self.backer_pin_diameter)
        self._setting_row(backer_options, 2, "Material beyond letter hole", self.pin_edge_clearance)
        self._setting_row(backer_options, 3, "Minimum pin spacing", self.pin_spacing)
        ttk.Label(
            backer_options,
            text="Master + letters-only + backers-only DXFs are created from one registered center map.",
            foreground="#59636e",
            wraplength=310,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        keyhole_job = self._build_letter_job_section(keyhole_settings, "Channel-letter backs")
        keyhole_job.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        keyhole_options = ttk.LabelFrame(keyhole_settings, text="Keyhole mounting (inches)", padding=10)
        keyhole_options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        keyhole_options.columnconfigure(1, weight=1)
        electrical_row = ttk.Frame(keyhole_options)
        electrical_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        electrical_row.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            electrical_row,
            text="Electrical holes",
            variable=self.include_electrical,
            command=self._toggle_electrical,
        ).grid(row=0, column=0, sticky="w")
        self.electrical_entry = ttk.Entry(electrical_row, textvariable=self.electrical_diameter, width=9)
        self.electrical_entry.grid(row=0, column=1, sticky="e")
        ttk.Label(electrical_row, text="diameter").grid(row=0, column=2, sticky="e", padx=(5, 0))
        self._setting_row(keyhole_options, 1, "Mount center spacing", self.mount_spacing)
        self._setting_row(keyhole_options, 2, "Round fallback diameter", self.round_mount_diameter)
        self._setting_row(keyhole_options, 3, "Coil clearance", self.coil_clearance)
        self._setting_row(keyhole_options, 4, "Preferred terminal reach", self.maximum_reach)
        ttk.Label(
            keyhole_options,
            textvariable=self.mass_summary,
            foreground="#244f73",
            wraplength=310,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(7, 0))

        ttk.Checkbutton(
            keyhole_settings,
            text="Show advanced keyhole settings",
            variable=self.keyhole_advanced_visible,
            command=self._toggle_keyhole_advanced,
        ).grid(row=2, column=0, sticky="w", pady=(1, 6))
        self.keyhole_advanced_frame = ttk.LabelFrame(
            keyhole_settings,
            text="Advanced keyhole settings",
            padding=10,
        )
        self.keyhole_advanced_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.keyhole_advanced_frame.columnconfigure(1, weight=1)
        self._setting_row(self.keyhole_advanced_frame, 0, "Mount/electrical web", self.feature_gap)
        self._setting_row(self.keyhole_advanced_frame, 1, "Planned load per mount (lb)", self.pounds_per_mount)
        self._setting_row(self.keyhole_advanced_frame, 2, "LED weight (lb/ft)", self.led_weight_per_foot)
        self._setting_row(self.keyhole_advanced_frame, 3, "LED path clearance", self.led_path_clearance)
        ttk.Label(
            self.keyhole_advanced_frame,
            text="4.5mm poly + .125 aluminum + 2x perimeter of .063 x 2in coil",
            foreground="#59636e",
            wraplength=310,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 8))
        ttk.Label(self.keyhole_advanced_frame, text="Keyhole part").grid(
            row=5, column=0, columnspan=2, sticky="w"
        )
        template_row = ttk.Frame(self.keyhole_advanced_frame)
        template_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(3, 7))
        template_row.columnconfigure(0, weight=1)
        ttk.Entry(template_row, textvariable=self.keyhole_template).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            template_row,
            text="Browse",
            command=lambda: self._browse_dxf_into(self.keyhole_template),
        ).grid(row=0, column=1, padx=(5, 0))
        ttk.Label(self.keyhole_advanced_frame, text="Handmade reference (optional)").grid(
            row=7, column=0, columnspan=2, sticky="w"
        )
        reference_row = ttk.Frame(self.keyhole_advanced_frame)
        reference_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        reference_row.columnconfigure(0, weight=1)
        ttk.Entry(reference_row, textvariable=self.handmade_reference).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            reference_row,
            text="Browse",
            command=lambda: self._browse_dxf_into(self.handmade_reference),
        ).grid(row=0, column=1, padx=(5, 0))

        pattern_job = ttk.LabelFrame(pattern_settings, text="Mounting-pattern job", padding=10)
        pattern_job.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        pattern_job.columnconfigure(0, weight=1)
        ttk.Label(pattern_job, text="Pattern label").grid(row=0, column=0, sticky="w")
        ttk.Entry(pattern_job, textvariable=self.pattern_label).grid(row=1, column=0, sticky="ew", pady=(3, 7))
        ttk.Label(
            pattern_job,
            text="Creates a bounding box plus horizontal and vertical centers around the opened DXF.",
            foreground="#59636e",
            wraplength=310,
        ).grid(row=2, column=0, sticky="w")

        pattern = ttk.LabelFrame(pattern_settings, text="Mounting pattern (inches)", padding=10)
        pattern.grid(row=1, column=0, sticky="ew")
        pattern.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            pattern,
            text="Physical dash segments (Flexi-safe)",
            variable=self.physical_dashes,
            command=self._toggle_physical_dashes,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        self.dash_entries = [
            self._setting_row(pattern, 1, "Dash length", self.dash_length),
            self._setting_row(pattern, 2, "Dash gap", self.dash_gap),
        ]
        self._setting_row(pattern, 3, "Label height", self.label_height)
        self._setting_row(pattern, 4, "Label offset", self.label_offset)
        ttk.Label(
            pattern,
            text="Off = six compact continuous guide lines.",
            foreground="#59636e",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.workflow_notebook.bind("<<NotebookTabChanged>>", self._workflow_tab_changed)
        self._toggle_keyhole_advanced()

        preview = ttk.Frame(body)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        body.add(preview, weight=1)

        preview_header = ttk.Frame(preview)
        preview_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        preview_header.columnconfigure(0, weight=1)
        ttk.Label(preview_header, textvariable=self.preview_name).grid(row=0, column=0, sticky="w")
        self.full_sheet_button = ttk.Button(
            preview_header,
            text="Full Sheet",
            command=lambda: self._show_review_mode("sheet"),
            state="disabled",
        )
        self.full_sheet_button.grid(row=0, column=1, padx=(8, 0))
        self.letters_review_button = ttk.Button(
            preview_header,
            text="Letters",
            command=lambda: self._show_review_mode("letters"),
            state="disabled",
        )
        self.letters_review_button.grid(row=0, column=2, padx=(5, 0))
        self.backers_review_button = ttk.Button(
            preview_header,
            text="Backers",
            command=lambda: self._show_review_mode("backers"),
            state="disabled",
        )
        self.backers_review_button.grid(row=0, column=3, padx=(5, 0))
        self.tile_review_button = ttk.Button(
            preview_header,
            text="Tile Review",
            command=lambda: self._show_review_mode("tiles"),
            state="disabled",
        )
        self.tile_review_button.grid(row=0, column=4, padx=(5, 0))
        self.pattern_review_button = ttk.Button(
            preview_header,
            text="Pattern",
            command=lambda: self._show_review_mode("pattern"),
            state="disabled",
        )
        self.pattern_review_button.grid(row=0, column=5, padx=(5, 0))
        self.legend_frames = {
            "artwork": self._legend_item(preview_header, 0, "Artwork", "#d7191c", row=1),
            "stud": self._legend_item(preview_header, 1, "Stud / round holes", "#7b1fa2", row=1),
            "letter_pin": self._legend_item(preview_header, 2, "Letter pin holes", "#7b1fa2", row=1),
            "electrical": self._legend_item(preview_header, 3, "Electrical holes", "#1769aa", row=1),
            "backer": self._legend_item(preview_header, 4, "Backer clearance", "#1769aa", row=1),
            "keyholes": self._legend_item(preview_header, 5, "Keyholes", "#159447", row=1),
            "led": self._legend_item(preview_header, 6, "LED guide", "#d69a00", row=1),
            "pattern": self._legend_item(preview_header, 7, "Pattern guides", "#6f7780", row=1),
        }
        self._update_legend()

        self.canvas = tk.Canvas(
            preview,
            background="#f8f9fa",
            highlightthickness=1,
            highlightbackground="#ced4da",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._redraw_preview())
        self.canvas.bind("<MouseWheel>", self._zoom_preview)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<B2-Motion>", self._pan_preview)
        self.canvas.bind("<Control-ButtonPress-1>", self._start_pan)
        self.canvas.bind("<Control-B1-Motion>", self._pan_preview)
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Button-3>", lambda _event: self.zoom_to_fit())

        progress_host = ttk.Frame(root)
        progress_host.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress_host.columnconfigure(0, weight=1)
        ttk.Label(progress_host, textvariable=self.status, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_host, textvariable=self.progress_text, anchor="e").grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.progress_bar = ttk.Progressbar(
            progress_host,
            variable=self.progress_value,
            maximum=1,
            mode="determinate",
        )
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def _make_settings_tab(self, notebook: ttk.Notebook, title: str):
        host = ttk.Frame(notebook)
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        notebook.add(host, text=title)

        canvas = tk.Canvas(host, width=350, highlightthickness=0, background="#f0f0f0")
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(canvas, padding=(8, 8, 10, 6))
        content.columnconfigure(0, weight=1)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda _event, current=canvas: current.configure(scrollregion=current.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event, current=canvas, item=window: current.itemconfigure(
                item,
                width=max(300, event.width - 2),
            ),
        )
        canvas.bind(
            "<Enter>",
            lambda _event, current=canvas: self._enable_settings_scroll(current),
        )
        canvas.bind("<Leave>", lambda _event: self._disable_settings_scroll())
        self.settings_canvases.append(canvas)
        if len(self.settings_canvases) == 1:
            self.settings_canvas = canvas
        return host, content

    def _build_letter_job_section(self, parent, title: str) -> ttk.LabelFrame:
        job = ttk.LabelFrame(parent, text=title, padding=10)
        job.columnconfigure(0, weight=1)
        ttk.Label(job, text="Lettering/shape order (do not type selected logos)").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Entry(job, textvariable=self.lettering).grid(row=1, column=0, sticky="ew", pady=(3, 7))
        logo_select = ttk.Checkbutton(
            job,
            text="Select logo/shape in preview",
            variable=self.logo_select_mode,
            command=self._logo_mode_changed,
            state="disabled",
        )
        logo_select.grid(row=2, column=0, sticky="w")
        self.logo_select_buttons.append(logo_select)
        ttk.Label(job, textvariable=self.logo_summary, foreground="#8a5200").grid(
            row=3,
            column=0,
            sticky="w",
            pady=(2, 0),
        )
        self._build_pattern_option(job, start_row=4)
        ttk.Label(
            job,
            text="Generate creates a temporary review. Output saves only after approval.",
            foreground="#59636e",
            wraplength=310,
        ).grid(row=7, column=0, sticky="w", pady=(7, 0))
        return job

    def _build_pattern_option(self, parent, start_row: int) -> None:
        ttk.Checkbutton(
            parent,
            text="Also create a mounting pattern",
            variable=self.add_pattern,
        ).grid(row=start_row, column=0, sticky="w", pady=(8, 0))
        ttk.Label(parent, text="Pattern label").grid(row=start_row + 1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(parent, textvariable=self.pattern_label).grid(
            row=start_row + 2,
            column=0,
            sticky="ew",
            pady=(3, 0),
        )

    def _enable_settings_scroll(self, canvas: tk.Canvas) -> None:
        self.settings_canvas = canvas
        canvas.bind_all("<MouseWheel>", self._scroll_settings)

    def _disable_settings_scroll(self) -> None:
        if hasattr(self, "settings_canvas"):
            self.settings_canvas.unbind_all("<MouseWheel>")

    def _toggle_keyhole_advanced(self) -> None:
        if not hasattr(self, "keyhole_advanced_frame"):
            return
        if self.keyhole_advanced_visible.get():
            self.keyhole_advanced_frame.grid()
        else:
            self.keyhole_advanced_frame.grid_remove()

    def _active_workflow_mode(self) -> str:
        if not hasattr(self, "workflow_notebook"):
            return STUD_MODE
        index = int(self.workflow_notebook.index("current"))
        return self.workflow_modes[index]

    def _generate_active_workflow(self) -> None:
        self._start_generation(self._active_workflow_mode())

    def _workflow_tab_changed(self, _event=None) -> None:
        mode = self._active_workflow_mode()
        if mode != BACKER_MODE:
            self.backer_select_mode.set(False)
        if mode not in {STUD_MODE, KEYHOLE_MODE}:
            self.logo_select_mode.set(False)
        if not self.logo_select_mode.get() and not self.backer_select_mode.get() and hasattr(self, "canvas"):
            self.canvas.configure(cursor="")
        self._refresh_workflow_controls()

    def _refresh_workflow_controls(self) -> None:
        mode = self._active_workflow_mode()
        labels = {
            STUD_MODE: ("Stud Holes", "Generate Stud Review"),
            BACKER_MODE: ("Backer + Pins", "Generate Backer Review"),
            KEYHOLE_MODE: ("Keyholes", "Generate Keyhole Review"),
            PATTERN_MODE: ("Mounting Pattern", "Generate Pattern Review"),
        }
        workflow_label, button_label = labels[mode]
        if mode == BACKER_MODE and self.source_path is not None and not self.backer_panels:
            workflow_label += " - no panels detected"
        self.workflow_summary.set(f"Workflow: {workflow_label}")
        can_generate = (
            not self.busy
            and self.source_path is not None
            and self.source_path.is_file()
            and (mode != BACKER_MODE or bool(self.backer_panels))
        )
        self.generate_button.configure(
            text=button_label,
            state="normal" if can_generate else "disabled",
        )
        logo_state = "normal" if not self.busy and self.source_path is not None else "disabled"
        for button in self.logo_select_buttons:
            button.configure(state=logo_state)
        backer_state = (
            "normal"
            if not self.busy and self.source_path is not None and self.backer_panels
            else "disabled"
        )
        for button in self.backer_select_buttons:
            button.configure(state=backer_state)
        self._update_legend()

    def _update_legend(self) -> None:
        if not hasattr(self, "legend_frames"):
            return
        mode = self.review.get("mode") if self.review is not None else self._active_workflow_mode()
        visible_by_mode = {
            STUD_MODE: ["artwork", "stud"],
            BACKER_MODE: ["artwork", "letter_pin", "backer"],
            KEYHOLE_MODE: ["artwork", "keyholes", "stud", "electrical", "led"],
            PATTERN_MODE: ["artwork", "pattern"],
        }
        for frame in self.legend_frames.values():
            frame.grid_remove()
        for column, key in enumerate(visible_by_mode.get(mode, ["artwork"])):
            self.legend_frames[key].grid(
                row=1,
                column=column,
                padx=(0 if column == 0 else 10, 0),
                pady=(4, 0),
            )

    @staticmethod
    def _setting_row(parent, row: int, label: str, variable: tk.StringVar) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=variable, width=10)
        entry.grid(row=row, column=1, sticky="e", pady=2)
        return entry

    @staticmethod
    def _legend_item(parent, column: int, label: str, color: str, row: int = 0) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, padx=(0 if column == 0 else 10, 0), pady=(4, 0))
        swatch = tk.Canvas(frame, width=12, height=12, highlightthickness=0)
        swatch.create_rectangle(1, 1, 11, 11, fill=color, outline="")
        swatch.pack(side="left", padx=(0, 4))
        ttk.Label(frame, text=label).pack(side="left")
        return frame

    def _scroll_settings(self, event: tk.Event) -> None:
        self.settings_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _install_undo_support(self) -> None:
        """Provide Ctrl+Z for text edits and reversible preview/logo actions."""
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Entry):
                    key = str(child)
                    self.entry_undo_history.setdefault(key, [])
                    child.bind("<KeyPress>", self._remember_entry_before_edit, add="+")
                visit(child)

        visit(self)
        self.bind_all("<Control-KeyPress-z>", self._undo, add="+")

    def _remember_entry_before_edit(self, event: tk.Event) -> None:
        if event.keysym.lower() == "z" and event.state & 0x4:
            return
        ignored = {
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Left", "Right", "Up", "Down", "Home", "End", "Tab", "Return", "Escape",
        }
        if event.keysym in ignored or (event.state & 0x4 and event.keysym.lower() in {"a", "c"}):
            return
        widget = event.widget
        key = str(widget)
        value = widget.get()
        history = self.entry_undo_history.setdefault(key, [])
        if not history or history[-1] != value:
            history.append(value)
            del history[:-100]

    def _undo(self, _event=None):
        focused = self.focus_get()
        if isinstance(focused, ttk.Entry):
            history = self.entry_undo_history.get(str(focused), [])
            if history:
                value = history.pop()
                focused.delete(0, "end")
                focused.insert(0, value)
                self.status.set("Undid the last text edit.")
                return "break"
        if not self.action_undo_stack:
            self.bell()
            self.status.set("Nothing to undo.")
            return "break"
        action = self.action_undo_stack.pop()
        if action["type"] == "logo":
            self.selected_logo_indices = set(action["indices"])
            self.logo_summary.set(f"Logos selected: {len(self.selected_logo_indices)}")
            self._redraw_preview()
            self.status.set("Undid logo selection.")
        elif action["type"] == "backer":
            self.selected_backer_indices = set(action["indices"])
            self.backer_summary.set(f"Backers selected: {len(self.selected_backer_indices)}")
            self._redraw_preview()
            self.status.set("Undid backer selection.")
        elif action["type"] == "clear_generation":
            self._clear_generation(push_undo=False)
            self.status.set("Undid generation; source preview restored.")
        elif action["type"] == "restore_review":
            self.review = action["review"]
            self._configure_review_controls()
            self._show_review_mode(action.get("mode", "sheet"))
            self.status.set("Restored the cleared generation.")
        return "break"

    def _toggle_electrical(self) -> None:
        self.electrical_entry.configure(state="normal" if self.include_electrical.get() else "disabled")

    def _toggle_physical_dashes(self) -> None:
        state = "normal" if self.physical_dashes.get() else "disabled"
        for entry in self.dash_entries:
            entry.configure(state=state)

    def _logo_mode_changed(self) -> None:
        if self.logo_select_mode.get():
            self.backer_select_mode.set(False)
            if self.review is not None:
                self._clear_generation()
            if self.source_preview_path and self.source_preview_path.exists():
                self._show_preview(
                    self.source_preview_path,
                    is_source=True,
                    metadata=self.source_preview_metadata,
                )
            self.status.set("Logo selection on - click a logo/shape in the preview; click again to remove it.")
            self.canvas.configure(cursor="hand2")
        else:
            self.status.set("Logo selection off.")
            self.canvas.configure(cursor="")

    def _backer_mode_changed(self) -> None:
        if self.backer_select_mode.get():
            self.logo_select_mode.set(False)
            if self.review is not None:
                self._clear_generation()
            if self.source_preview_path and self.source_preview_path.exists():
                self._show_preview(
                    self.source_preview_path,
                    is_source=True,
                    metadata=self.source_preview_metadata,
                )
            self.status.set("Backer selection on - click a panel to include or remove it.")
            self.canvas.configure(cursor="hand2")
        else:
            self.status.set("Backer selection off.")
            self.canvas.configure(cursor="")

    def _browse_source(self) -> None:
        initial = self.source_path.parent if self.source_path else self.last_open_directory
        if initial is None or not initial.is_dir():
            initial = Path.cwd()
        selected = filedialog.askopenfilename(
            title="Open DXF",
            filetypes=[("DXF files", "*.dxf"), ("All files", "*.*")],
            initialdir=str(initial),
        )
        if selected:
            self.load_dxf(Path(selected))

    def _browse_dxf_into(self, variable: tk.StringVar) -> None:
        initial = self.source_path.parent if self.source_path else Path.cwd()
        selected = filedialog.askopenfilename(
            title="Select DXF",
            filetypes=[("DXF files", "*.dxf")],
            initialdir=str(initial),
        )
        if selected:
            variable.set(selected)

    def load_dxf(self, path: Path) -> None:
        try:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() != ".dxf":
                raise ValueError("Select a valid DXF file.")
            self.status.set(f"Loading {path.name}...")
            self.update_idletasks()
            audit_dxf(path)
            preview_path = Path(self.preview_directory.name) / "opened-dxf.png"
            metadata = render_dxf_overview(path, preview_path, scale=8.0)
            document = ezdxf.readfile(path)
            backer_panels = discover_backer_panels(document.modelspace())
            if backer_panels:
                polygons = [letter for panel in backer_panels for letter in panel.letters]
            else:
                polygons, _ = order_polygons_for_text(recover_material_polygons(document.modelspace()))
            self.source_path = path
            self.source_preview_path = preview_path
            self.source_preview_metadata = metadata
            self.source_polygons = polygons
            self.backer_panels = backer_panels
            self.review = None
            self.selected_logo_indices.clear()
            self.selected_backer_indices = set(range(len(backer_panels)))
            self.progress_markers.clear()
            self.logo_summary.set("Logos selected: 0")
            self.backer_summary.set(f"Backers selected: {len(self.selected_backer_indices)}")
            self.logo_select_mode.set(False)
            self.backer_select_mode.set(False)
            self.last_open_directory = path.parent
            try:
                save_last_open_directory(path.parent, self.settings_path)
            except OSError:
                # The DXF is still usable if Windows blocks the preference file.
                pass
            self.last_output_directory = path.parent
            self.file_name.set(path.name)
            self.preview_name.set(f"Preview: {path.name}")
            self.pattern_label.set(path.stem.upper())
            self._show_preview(preview_path, is_source=True, metadata=metadata)
            self.workflow_notebook.select(1 if backer_panels else 0)
            self._refresh_workflow_controls()
            self._configure_review_controls()
            self.progress_value.set(0)
            self.progress_bar.configure(maximum=max(1, len(polygons)))
            self.progress_text.set(f"Ready - {len(polygons)} separated shape(s)")
            self._update_mass_summary()
            if backer_panels:
                self.status.set(
                    f"Ready - detected {len(backer_panels)} backer panel(s) and {len(polygons)} nested component(s)."
                )
            else:
                self.status.set("Ready - Generate creates a review; Output saves only after approval.")
        except Exception as error:
            self.status.set("Could not load DXF")
            messagebox.showerror(APP_TITLE, str(error), parent=self)

    def _update_mass_summary(self) -> None:
        if not hasattr(self, "mass_summary"):
            return
        if not self.source_polygons:
            self.mass_summary.set("Estimated assembly: open a DXF")
            return
        if self.backer_panels:
            self.mass_summary.set(
                f"Backer job: {len(self.backer_panels)} panel(s) | {len(self.source_polygons)} nested component(s)"
            )
            return
        try:
            led_weight = finite_positive(
                self.led_weight_per_foot.get(),
                "LED weight per foot",
                allow_zero=True,
            )
        except ValueError:
            self.mass_summary.set("Estimated assembly: enter a valid LED lb/ft")
            return
        estimates = [estimate_channel_letter_mass(polygon, led_weight) for polygon in self.source_polygons]
        total_mass = sum(estimate.total_lb for estimate in estimates)
        led_length = sum(estimate.led_length_ft for estimate in estimates)
        self.mass_summary.set(f"Estimated assembly: {total_mass:.1f} lb | LED centerline: {led_length:.1f} ft")

    def _show_preview(
        self,
        path: Path,
        is_source: bool = False,
        metadata: dict | None = None,
        mode: str | None = None,
    ) -> None:
        with Image.open(path) as image:
            self.preview_original = image.convert("RGB").copy()
        self.preview_is_source = is_source
        if mode is not None:
            self.preview_mode = mode
        elif is_source:
            self.preview_mode = "source"
        if is_source and metadata is not None:
            self.source_preview_metadata = metadata
        self.zoom_to_fit()

    def zoom_to_fit(self) -> None:
        self.preview_zoom = 1.0
        self.preview_pan_x = 0.0
        self.preview_pan_y = 0.0
        self._redraw_preview()

    def _redraw_preview(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 100)
        height = max(self.canvas.winfo_height(), 100)
        if self.preview_original is None:
            self.canvas.create_text(
                24,
                24,
                anchor="nw",
                text="Open a DXF to preview it here.",
                fill="#495057",
            )
            return
        base_scale = min((width - 40) / self.preview_original.width, (height - 40) / self.preview_original.height)
        scale = max(0.02, base_scale * self.preview_zoom)
        display_size = (
            max(1, round(self.preview_original.width * scale)),
            max(1, round(self.preview_original.height * scale)),
        )
        resized = self.preview_original.resize(display_size, Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(resized)
        self.display_scale = scale
        self.display_left = width / 2 + self.preview_pan_x - display_size[0] / 2
        self.display_top = height / 2 + self.preview_pan_y - display_size[1] / 2
        self.canvas.create_image(
            width / 2 + self.preview_pan_x,
            height / 2 + self.preview_pan_y,
            image=self.preview_photo,
            anchor="center",
        )
        if self.preview_is_source:
            self._draw_source_overlays()

    @staticmethod
    def _bounds_key(bounds) -> tuple[float, float, float, float]:
        return tuple(round(float(value), 3) for value in bounds)

    def _world_to_canvas(self, x: float, y: float) -> tuple[float, float] | None:
        metadata = self.source_preview_metadata
        if metadata is None:
            return None
        min_x, _min_y, _max_x, max_y = metadata["bounds"]
        render_scale = metadata["scale"]
        margin = metadata["margin"]
        image_x = margin + (x - min_x) * render_scale
        image_y = margin + (max_y - y) * render_scale
        return (
            self.display_left + image_x * self.display_scale,
            self.display_top + image_y * self.display_scale,
        )

    def _canvas_to_world(self, x: float, y: float) -> tuple[float, float] | None:
        metadata = self.source_preview_metadata
        if metadata is None or self.display_scale <= 0:
            return None
        image_x = (x - self.display_left) / self.display_scale
        image_y = (y - self.display_top) / self.display_scale
        min_x, _min_y, _max_x, max_y = metadata["bounds"]
        render_scale = metadata["scale"]
        margin = metadata["margin"]
        return (
            min_x + (image_x - margin) / render_scale,
            max_y - (image_y - margin) / render_scale,
        )

    def _draw_source_overlays(self) -> None:
        for index, panel in enumerate(self.backer_panels):
            if index not in self.selected_backer_indices:
                continue
            bounds = panel.polygon.bounds
            top_left = self._world_to_canvas(bounds[0], bounds[3])
            bottom_right = self._world_to_canvas(bounds[2], bounds[1])
            if top_left is None or bottom_right is None:
                continue
            x1, y1 = top_left
            x2, y2 = bottom_right
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#1769aa", width=3)
            self.canvas.create_text(
                x1 + 4,
                y1 + 3,
                text=f"BACKER {index + 1}",
                fill="#1769aa",
                anchor="nw",
                font=("Segoe UI", 10, "bold"),
            )
        for index, polygon in enumerate(self.source_polygons):
            bounds = polygon.bounds
            top_left = self._world_to_canvas(bounds[0], bounds[3])
            bottom_right = self._world_to_canvas(bounds[2], bounds[1])
            if top_left is None or bottom_right is None:
                continue
            key = self._bounds_key(bounds)
            marker = self.progress_markers.get(key)
            selected = index in self.selected_logo_indices
            if not marker and not selected:
                continue
            if marker == "complete":
                color, width, tag = "#159447", 3, "DONE"
            elif marker == "working":
                color, width, tag = "#e0a000", 3, "..."
            else:
                color, width, tag = "#f07c00", 3, f"LOGO {sorted(self.selected_logo_indices).index(index) + 1}"
            x1, y1 = top_left
            x2, y2 = bottom_right
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=width)
            self.canvas.create_text(
                x1 + 4,
                y1 + 3,
                text=tag,
                fill=color,
                anchor="nw",
                font=("Segoe UI", 10, "bold"),
            )

    def _canvas_click(self, event: tk.Event) -> None:
        if self.preview_mode == "tiles" and self.review and self.review.get("tile_manifest"):
            image_x = (event.x - self.display_left) / max(self.display_scale, 0.0001)
            image_y = (event.y - self.display_top) / max(self.display_scale, 0.0001)
            manifest = self.review["tile_manifest"]
            if image_x < 0 or image_y < 0:
                return
            column = int(image_x // manifest["cell_width"])
            row = int(image_y // manifest["cell_height"])
            index = row * manifest["columns"] + column
            details = manifest["details"]
            if 0 <= index < len(details):
                detail = details[index]
                self._show_preview(detail["path"], mode="detail")
                self.preview_name.set(
                    f"Inspecting tile {detail['index']:02d}: {detail['label']} - use Tile Review to go back"
                )
                self.canvas.configure(cursor="")
            return
        if self.backer_select_mode.get():
            self._select_backer_click(event)
        else:
            self._select_logo_click(event)

    def _select_backer_click(self, event: tk.Event) -> None:
        if not self.backer_select_mode.get() or not self.preview_is_source or not self.backer_panels:
            return
        world = self._canvas_to_world(event.x, event.y)
        if world is None:
            return
        probe = Point(*world)
        containing = [
            (index, panel.polygon.area)
            for index, panel in enumerate(self.backer_panels)
            if panel.polygon.buffer(0.03).covers(probe)
        ]
        if not containing:
            self.status.set("No detected backer panel under that point.")
            return
        index = min(containing, key=lambda item: item[1])[0]
        self.action_undo_stack.append(
            {"type": "backer", "indices": set(self.selected_backer_indices)}
        )
        if index in self.selected_backer_indices:
            self.selected_backer_indices.remove(index)
            action = "removed"
        else:
            self.selected_backer_indices.add(index)
            action = "selected"
        count = len(self.selected_backer_indices)
        self.backer_summary.set(f"Backers selected: {count}")
        self.status.set(f"Backer {index + 1} {action}. {count} panel(s) selected.")
        self._redraw_preview()

    def _select_logo_click(self, event: tk.Event) -> None:
        if not self.logo_select_mode.get() or not self.preview_is_source or not self.source_polygons:
            return
        world = self._canvas_to_world(event.x, event.y)
        if world is None:
            return
        probe = Point(*world)
        containing = [
            (index, polygon.area)
            for index, polygon in enumerate(self.source_polygons)
            if Polygon(polygon.exterior).buffer(0.03).contains(probe)
        ]
        if not containing:
            self.status.set("No closed shape under that point - click inside the logo outline.")
            return
        index = min(containing, key=lambda item: item[1])[0]
        self.action_undo_stack.append({"type": "logo", "indices": set(self.selected_logo_indices)})
        if index in self.selected_logo_indices:
            self.selected_logo_indices.remove(index)
            action = "removed"
        else:
            self.selected_logo_indices.add(index)
            action = "selected"
        count = len(self.selected_logo_indices)
        self.logo_summary.set(f"Logos selected: {count}")
        self.status.set(f"Logo {action}. {count} logo shape(s) selected.")
        self._redraw_preview()

    def _zoom_preview(self, event: tk.Event) -> None:
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self.preview_zoom = min(12.0, max(0.25, self.preview_zoom * factor))
        self._redraw_preview()

    def _start_pan(self, event: tk.Event) -> None:
        self.pan_origin = (event.x, event.y, self.preview_pan_x, self.preview_pan_y)

    def _pan_preview(self, event: tk.Event) -> None:
        if self.pan_origin is None:
            return
        start_x, start_y, original_x, original_y = self.pan_origin
        self.preview_pan_x = original_x + event.x - start_x
        self.preview_pan_y = original_y + event.y - start_y
        self._redraw_preview()

    def _collect_job(self, mode: str) -> dict:
        if self.source_path is None or not self.source_path.is_file():
            raise ValueError("Open a source DXF first.")
        pattern_source = (
            pattern_source_for_review(self.source_path, self.review)
            if mode == PATTERN_MODE
            else self.source_path
        )
        lettering = self.lettering.get()
        reference_text = self.handmade_reference.get().strip()
        if mode in {STUD_MODE, KEYHOLE_MODE} and not (mode == KEYHOLE_MODE and reference_text):
            text_polygons, _ = order_polygons_for_text(
                [
                    polygon
                    for index, polygon in enumerate(self.source_polygons)
                    if index not in self.selected_logo_indices
                ]
            )
            labels_for_text_components(lettering, text_polygons)
        if mode == BACKER_MODE:
            if not self.backer_panels:
                raise ValueError("This DXF does not contain a detected backer panel with nested lettering.")
            if not self.selected_backer_indices:
                raise ValueError("Select at least one backer panel in the preview.")

        wants_pattern = mode == PATTERN_MODE or self.add_pattern.get()
        label = self.pattern_label.get().strip()
        if wants_pattern and not label:
            raise ValueError("Enter the mounting-pattern label.")

        include_electrical = self.include_electrical.get()
        physical_dashes = self.physical_dashes.get()
        values = {
            "stud_diameter": finite_positive(self.stud_diameter.get(), "Stud-hole diameter"),
            "stud_edge_clearance": finite_positive(self.stud_edge_clearance.get(), "Stud edge clearance"),
            "logo_edge_clearance": finite_positive(self.logo_edge_clearance.get(), "Logo edge clearance"),
            "stud_spacing": finite_positive(self.stud_spacing.get(), "Stud spacing"),
            "letter_pin_diameter": finite_positive(
                self.letter_pin_diameter.get(), "Letter roll-pin hole diameter"
            ),
            "backer_pin_diameter": finite_positive(
                self.backer_pin_diameter.get(), "Backer clearance-hole diameter"
            ),
            "pin_edge_clearance": finite_positive(
                self.pin_edge_clearance.get(), "Material beyond letter hole", allow_zero=True
            ),
            "pin_spacing": finite_positive(self.pin_spacing.get(), "Minimum pin spacing"),
            "electrical_diameter": (
                finite_positive(self.electrical_diameter.get(), "Electrical diameter")
                if include_electrical
                else 0.375
            ),
            "mount_spacing": finite_positive(self.mount_spacing.get(), "Mount center spacing"),
            "round_mount_diameter": finite_positive(self.round_mount_diameter.get(), "Round fallback diameter"),
            "coil_clearance": finite_positive(self.coil_clearance.get(), "Coil clearance"),
            "maximum_reach": finite_positive(self.maximum_reach.get(), "Maximum unsupported reach"),
            "feature_gap": finite_positive(self.feature_gap.get(), "Mount/electrical web", allow_zero=True),
            "pounds_per_mount": finite_positive(self.pounds_per_mount.get(), "Planned load per mount"),
            "led_weight_per_foot": finite_positive(
                self.led_weight_per_foot.get(), "LED weight per foot", allow_zero=True
            ),
            "led_path_clearance": finite_positive(
                self.led_path_clearance.get(), "LED path clearance", allow_zero=True
            ),
            "dash_length": (
                finite_positive(self.dash_length.get(), "Pattern dash length") if physical_dashes else 0.25
            ),
            "dash_gap": (
                finite_positive(self.dash_gap.get(), "Pattern dash gap", allow_zero=True)
                if physical_dashes
                else 0.25
            ),
            "label_height": finite_positive(self.label_height.get(), "Pattern label height"),
            "label_offset": finite_positive(self.label_offset.get(), "Pattern label offset", allow_zero=True),
        }
        template = Path(self.keyhole_template.get().strip())
        if mode == KEYHOLE_MODE and not template.is_file():
            raise ValueError("Select a valid keyhole-part DXF.")
        reference = Path(reference_text) if reference_text else None
        if reference and not reference.is_file():
            raise ValueError("The handmade reference DXF does not exist.")
        return {
            "source": self.source_path,
            "pattern_source": pattern_source,
            "output_directory": self.source_path.parent,
            "mode": mode,
            "lettering": lettering,
            "label": label,
            "wants_pattern": wants_pattern,
            "include_electrical": include_electrical,
            "physical_dashes": physical_dashes,
            "template": template,
            "reference": reference,
            "logo_indices": set(self.selected_logo_indices),
            "logo_bounds": [self.source_polygons[index].bounds for index in sorted(self.selected_logo_indices)],
            "backer_bounds": [
                self.backer_panels[index].polygon.bounds for index in sorted(self.selected_backer_indices)
            ],
            **values,
        }

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._refresh_workflow_controls()
        if busy:
            self.output_button.configure(state="disabled")
            self.clear_generation_button.configure(state="disabled")
            for button in (
                self.full_sheet_button,
                self.tile_review_button,
                self.pattern_review_button,
                self.letters_review_button,
                self.backers_review_button,
            ):
                button.configure(state="disabled")
        else:
            self._configure_review_controls()

    def _configure_review_controls(self) -> None:
        available = self.review is not None
        self.output_button.configure(state="normal" if available else "disabled")
        self.clear_generation_button.configure(state="normal" if available else "disabled")
        has_tiles = bool(available and self.review.get("tile_manifest"))
        has_pattern = bool(available and self.review.get("pattern_preview"))
        has_letters = bool(available and self.review.get("letters_preview"))
        has_backers = bool(available and self.review.get("backers_preview"))

        def configure_review_button(button, visible: bool) -> None:
            if visible:
                button.grid()
                button.configure(state="normal")
            else:
                button.configure(state="disabled")
                button.grid_remove()

        configure_review_button(self.full_sheet_button, available)
        configure_review_button(self.tile_review_button, has_tiles)
        configure_review_button(self.pattern_review_button, has_pattern)
        configure_review_button(self.letters_review_button, has_letters)
        configure_review_button(self.backers_review_button, has_backers)
        self._update_legend()

    def _show_review_mode(self, mode: str) -> None:
        if self.review is None:
            return
        if mode == "tiles" and self.review.get("tile_manifest"):
            path = self.review["tile_manifest"]["sheet"]
            label = "Tile review - click any tile to inspect it"
            self.canvas.configure(cursor="hand2")
        elif mode == "letters" and self.review.get("letters_preview"):
            path = self.review["letters_preview"]
            label = "Letters-only review"
            self.canvas.configure(cursor="")
        elif mode == "backers" and self.review.get("backers_preview"):
            path = self.review["backers_preview"]
            label = "Backers-only review"
            self.canvas.configure(cursor="")
        elif mode == "pattern" and self.review.get("pattern_preview"):
            path = self.review["pattern_preview"]
            label = "Mounting-pattern review"
            self.canvas.configure(cursor="")
        else:
            mode = "sheet"
            path = self.review["sheet_preview"]
            label = "Full-sheet review"
            self.canvas.configure(cursor="")
        self._show_preview(path, is_source=False, mode=mode)
        mass_suffix = ""
        if self.review.get("total_mass_lb") is not None:
            mass_suffix = f" - estimated {self.review['total_mass_lb']:.1f} lb"
        self.preview_name.set(f"{label}{mass_suffix} - NOT YET OUTPUT")

    def _clear_generation(self, push_undo: bool = True) -> None:
        if self.review is None:
            return
        if push_undo:
            self.action_undo_stack.append(
                {"type": "restore_review", "review": self.review, "mode": self.preview_mode}
            )
        self.review = None
        self.progress_markers.clear()
        self.progress_value.set(0)
        self.progress_text.set(f"Ready - {len(self.source_polygons)} separated shape(s)")
        self._configure_review_controls()
        if self.source_preview_path and self.source_preview_path.exists():
            self._show_preview(
                self.source_preview_path,
                is_source=True,
                metadata=self.source_preview_metadata,
                mode="source",
            )
            self.preview_name.set(f"Preview: {self.source_path.name}")
        self.status.set("Generation cleared. No DXF was output. Ctrl+Z restores it.")

    def _output_artifacts(self, artifacts: list[dict]) -> None:
        if self.review is None:
            messagebox.showinfo(APP_TITLE, "Generate and review a job before outputting it.", parent=self)
            return
        destinations = []
        initial_directory = self.last_output_directory or (self.source_path.parent if self.source_path else Path.cwd())
        for artifact in artifacts:
            selected = filedialog.asksaveasfilename(
                title=artifact["title"],
                initialdir=str(initial_directory),
                initialfile=artifact["suggested"],
                defaultextension=".dxf",
                filetypes=[("DXF files", "*.dxf"), ("All files", "*.*")],
            )
            if not selected:
                self.status.set("Output cancelled. The generated review is still available.")
                return
            destination = Path(selected)
            destinations.append((artifact["path"], destination))
            initial_directory = destination.parent
        try:
            for source, destination in destinations:
                shutil.copy2(source, destination)
                audit_dxf(destination)
            self.last_output_directory = destinations[-1][1].parent
            self.status.set("OUTPUT SAVED: " + ", ".join(destination.name for _, destination in destinations))
            self.progress_text.set("Approved review written to disk")
        except Exception as error:
            messagebox.showerror(APP_TITLE, f"Could not output the approved DXF:\n{error}", parent=self)

    def _output_review(self) -> None:
        if self.review is None:
            messagebox.showinfo(APP_TITLE, "Generate and review a job before outputting it.", parent=self)
            return
        self._output_artifacts(self.review["artifacts"])

    def _output_review_role(self, role: str) -> None:
        if self.review is None:
            messagebox.showinfo(APP_TITLE, "Generate and review a job before outputting it.", parent=self)
            return
        artifact = next(
            (candidate for candidate in self.review["artifacts"] if candidate.get("role") == role),
            None,
        )
        if artifact is None:
            messagebox.showinfo(
                APP_TITLE,
                f"This generated review does not contain a {role.replace('_', ' ')} DXF.",
                parent=self,
            )
            return
        self._output_artifacts([artifact])

    def _output_current_review(self) -> None:
        if self.review is not None and self.review.get("mode") == PATTERN_MODE:
            role = "pattern"
        else:
            role = {
                "letters": "letters",
                "backers": "backers",
                "pattern": "pattern",
            }.get(self.preview_mode, "master")
        self._output_review_role(role)

    def _start_generation(self, mode: str) -> None:
        try:
            job = self._collect_job(mode)
        except Exception as error:
            messagebox.showerror(APP_TITLE, str(error), parent=self)
            return
        review_directory = Path(self.preview_directory.name) / f"review-{time.time_ns()}"
        review_directory.mkdir(parents=True, exist_ok=True)
        job["review_directory"] = review_directory
        job["output"] = review_directory / "generated-primary.dxf"
        job["pattern_output"] = review_directory / "generated-pattern.dxf"
        job["letters_output"] = review_directory / "generated-letters.dxf"
        job["backers_output"] = review_directory / "generated-backers.dxf"
        previous_review = self.review
        self.review = None
        if previous_review is not None:
            self.action_undo_stack.append(
                {"type": "restore_review", "review": previous_review, "mode": self.preview_mode}
            )
        self._configure_review_controls()
        if self.source_preview_path and self.source_preview_path.exists():
            self._show_preview(
                self.source_preview_path,
                is_source=True,
                metadata=self.source_preview_metadata,
            )
        self.logo_select_mode.set(False)
        self.backer_select_mode.set(False)
        self.canvas.configure(cursor="")
        self.progress_markers.clear()
        self.progress_value.set(0)
        self.progress_bar.configure(maximum=max(1, len(self.source_polygons)))
        self.run_started_at = time.perf_counter()
        self._set_busy(True)
        self.status.set(f"Generating {mode} - the preview will mark each completed shape.")
        self.progress_text.set("Starting...")
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()

    def _run_job(self, job: dict) -> None:
        try:
            source = job["source"]
            review_directory = job["review_directory"]
            artifacts: list[dict] = []
            sheet_preview: Path | None = None
            pattern_preview: Path | None = None
            letters_preview: Path | None = None
            backers_preview: Path | None = None
            tile_manifest: dict | None = None
            tile_items = []
            tile_led_guides = None
            working_source = source
            if job["mode"] == PATTERN_MODE:
                working_source = job.get("pattern_source", source)
            progress_callback = lambda payload: self.events.put(("progress", payload))

            if job["mode"] == STUD_MODE:
                output = job["output"]
                engine_preview = review_directory / "engine-stud-tiles.png"
                result = generate_stud_holes(
                    source,
                    job["lettering"],
                    output,
                    engine_preview,
                    job["stud_diameter"],
                    job["stud_edge_clearance"],
                    job["stud_spacing"],
                    job["logo_indices"],
                    job["logo_edge_clearance"],
                    progress_callback,
                )
                audit_dxf(output)
                artifacts.append(
                    {
                        "path": output,
                        "role": "master",
                        "title": "Output approved stud-hole DXF as",
                        "suggested": f"{source.stem} - AUTO STUD HOLES.dxf",
                    }
                )
                tile_items = [(letter.label, letter.polygon.bounds) for letter in result["letters"]]
                working_source = output

            elif job["mode"] == BACKER_MODE:
                output = job["output"]
                result = generate_backer_pin_job(
                    source,
                    output,
                    job["letters_output"],
                    job["backers_output"],
                    job["letter_pin_diameter"],
                    job["backer_pin_diameter"],
                    job["pin_edge_clearance"],
                    job["pin_spacing"],
                    job["backer_bounds"],
                    progress_callback,
                )
                for path in (output, job["letters_output"], job["backers_output"]):
                    audit_dxf(path)
                artifacts.extend(
                    [
                        {
                            "path": output,
                            "role": "master",
                            "title": "Output approved master pin-layout DXF as",
                            "suggested": f"{source.stem} - MASTER PIN LAYOUT.dxf",
                        },
                        {
                            "path": job["letters_output"],
                            "role": "letters",
                            "title": "Output approved letters-only pin DXF as",
                            "suggested": f"{source.stem} - LETTER PIN HOLES.dxf",
                        },
                        {
                            "path": job["backers_output"],
                            "role": "backers",
                            "title": "Output approved backers-only clearance DXF as",
                            "suggested": f"{source.stem} - BACKER CLEARANCE HOLES.dxf",
                        },
                    ]
                )
                tile_items = [
                    (
                        f"Panel {panel.index} | {len(panel.letters)} components | "
                        f"{sum(len(centers) for centers in layouts)} pins",
                        panel.polygon.bounds,
                    )
                    for panel, layouts in zip(result["panels"], result["layouts"])
                ]
                job["single_pin_components"] = result["single_pin_components"]
                job["total_pins"] = result["total_pins"]
                working_source = output

            elif job["mode"] == KEYHOLE_MODE:
                output = job["output"]
                if job["reference"]:
                    result = generate_from_reference(
                        source,
                        job["reference"],
                        job["template"],
                        output,
                        job["electrical_diameter"],
                        job["include_electrical"],
                        progress_callback,
                    )
                else:
                    result = generate_procedural(
                        source,
                        job["template"],
                        output,
                        job["lettering"],
                        job["electrical_diameter"],
                        job["mount_spacing"],
                        job["feature_gap"],
                        job["coil_clearance"],
                        job["round_mount_diameter"],
                        job["maximum_reach"],
                        job["include_electrical"],
                        job["logo_bounds"],
                        progress_callback,
                        job["logo_edge_clearance"],
                        job["pounds_per_mount"],
                        job["led_weight_per_foot"],
                        job["led_path_clearance"],
                    )
                audit_dxf(output)
                artifacts.append(
                    {
                        "path": output,
                        "role": "master",
                        "title": "Output approved keyhole DXF as",
                        "suggested": f"{source.stem} - AUTO KEYHOLES.dxf",
                    }
                )
                mass_estimates = result.get("mass_estimates") or [
                    estimate_channel_letter_mass(back, job["led_weight_per_foot"])
                    for back in result["backs"]
                ]
                mounts_by_back = result.get("mounts_by_back")
                tile_items = []
                for item_index, (label, back, estimate) in enumerate(
                    zip(result["labels"], result["backs"], mass_estimates)
                ):
                    mount_suffix = (
                        f" | {len(mounts_by_back[item_index])} mounts" if mounts_by_back else ""
                    )
                    tile_items.append(
                        (f"{label} | {estimate.total_lb:.1f} lb{mount_suffix}", back.bounds)
                    )
                job["total_mass_lb"] = sum(estimate.total_lb for estimate in mass_estimates)
                job["total_led_length_ft"] = sum(
                    estimate.led_length_ft for estimate in mass_estimates
                )
                tile_led_guides = result.get("led_guides") or [
                    approximate_led_centerline_points(back) for back in result["backs"]
                ]
                working_source = output

            if job["mode"] in {STUD_MODE, KEYHOLE_MODE, BACKER_MODE}:
                sheet_preview = review_directory / "full-sheet.png"
                render_dxf_overview(working_source, sheet_preview, scale=8.0)
                tile_manifest = render_tile_review(
                    working_source,
                    review_directory / "tile-review.png",
                    tile_items,
                    review_directory / "tile-details",
                    scale=8.0,
                    led_guides=tile_led_guides,
                )
                if job["mode"] == BACKER_MODE:
                    letters_preview = review_directory / "letters-only.png"
                    backers_preview = review_directory / "backers-only.png"
                    render_dxf_overview(job["letters_output"], letters_preview, scale=8.0)
                    render_dxf_overview(job["backers_output"], backers_preview, scale=8.0)

            if job["wants_pattern"]:
                self.events.put(("progress", {"stage": "pattern", "message": "Building mounting pattern..."}))
                pattern_output = job["pattern_output"]
                pattern_preview = review_directory / "pattern-review.png"
                generate_pattern(
                    working_source,
                    pattern_output,
                    pattern_preview,
                    job["label"],
                    job["label_height"],
                    job["label_offset"],
                    False,
                    job["dash_length"],
                    job["dash_gap"],
                    job["physical_dashes"],
                )
                audit_dxf(pattern_output)
                if job["mode"] == PATTERN_MODE:
                    artifacts = [
                        {
                            "path": pattern_output,
                            "role": "pattern",
                            "title": "Output approved mounting-pattern DXF as",
                            "suggested": f"{source.stem} - AUTO MOUNTING PATTERN.dxf",
                        }
                    ]
                    sheet_preview = pattern_preview
                    pattern_preview = None
                else:
                    artifacts.append(
                        {
                            "path": pattern_output,
                            "role": "pattern",
                            "title": "Output approved mounting-pattern DXF as",
                            "suggested": f"{source.stem} - AUTO MOUNTING PATTERN.dxf",
                        }
                    )

            self.events.put(
                (
                    "done",
                    {
                        "review": {
                            "artifacts": artifacts,
                            "sheet_preview": sheet_preview,
                            "pattern_preview": pattern_preview,
                            "letters_preview": letters_preview,
                            "backers_preview": backers_preview,
                            "tile_manifest": tile_manifest,
                            "mode": job["mode"],
                            "total_mass_lb": job.get("total_mass_lb"),
                            "total_led_length_ft": job.get("total_led_length_ft"),
                            "total_pins": job.get("total_pins"),
                            "single_pin_components": job.get("single_pin_components", []),
                        }
                    },
                )
            )
        except BaseException as error:
            self.events.put(("error", {"message": str(error), "traceback": traceback.format_exc()}))

    def _poll_events(self) -> None:
        needs_source_redraw = False
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    stage = payload.get("stage", "")
                    bounds = payload.get("bounds")
                    if bounds is not None and stage in {"piece_started", "piece_complete"}:
                        self.progress_markers[self._bounds_key(bounds)] = (
                            "complete" if stage == "piece_complete" else "working"
                        )
                        needs_source_redraw = True
                    if stage == "piece_complete":
                        self.progress_value.set(payload["index"])
                    total = payload.get("total")
                    index = payload.get("index")
                    label = payload.get("label")
                    elapsed = time.perf_counter() - self.run_started_at if self.run_started_at is not None else 0
                    if total and index:
                        verb = "Finished" if stage == "piece_complete" else "Working on"
                        self.progress_bar.configure(maximum=total)
                        self.progress_text.set(
                            f"{verb} {index}/{total}: {label}  |  {elapsed:.1f}s elapsed"
                        )
                    elif payload.get("message"):
                        self.progress_text.set(f"{payload['message']}  |  {elapsed:.1f}s elapsed")
                elif event == "done":
                    self.review = payload["review"]
                    elapsed = time.perf_counter() - self.run_started_at if self.run_started_at is not None else 0
                    total_mass = self.review.get("total_mass_lb")
                    mass_note = f" Estimated assembly: {total_mass:.1f} lb." if total_mass is not None else ""
                    total_pins = self.review.get("total_pins")
                    pin_note = f" {total_pins} registered pin center(s)." if total_pins is not None else ""
                    single_count = len(self.review.get("single_pin_components") or [])
                    exception_note = (
                        f" Review {single_count} compact single-pin exception(s)." if single_count else ""
                    )
                    self.status.set(
                        "REVIEW READY - inspect the available views, then open Output."
                        + mass_note
                        + pin_note
                        + exception_note
                    )
                    self.progress_text.set(f"Generated for review in {elapsed:.1f} seconds - not saved yet")
                    self.progress_value.set(int(float(self.progress_bar.cget("maximum"))))
                    self.run_started_at = None
                    self.action_undo_stack.append({"type": "clear_generation"})
                    self._set_busy(False)
                    self._show_review_mode("sheet")
                elif event == "error":
                    print(payload["traceback"], file=sys.stderr)
                    self.status.set("FAILED - no review or output was produced")
                    self.progress_text.set("Stopped with an error")
                    self.run_started_at = None
                    self._set_busy(False)
                    messagebox.showerror(APP_TITLE, payload["message"], parent=self)
        except queue.Empty:
            pass
        if needs_source_redraw and self.run_started_at is not None:
            # A large backer job can enqueue hundreds of component updates in
            # one UI poll. Paint the newest state once instead of resizing the
            # full preview once for every queued event.
            self._redraw_preview()
        if self.run_started_at is not None:
            elapsed = time.perf_counter() - self.run_started_at
            current = self.progress_text.get().split("  |  ")[0]
            self.progress_text.set(f"{current}  |  {elapsed:.1f}s elapsed")
        self.after(150, self._poll_events)

    def _close_app(self) -> None:
        self.preview_directory.cleanup()
        self.destroy()


def run_packaged_self_test(source: Path, output_directory: Path, text_value: str) -> Path:
    """Exercise numerical geometry, bundled resources, DXF writing, previewing, and auditing."""
    source = Path(source)
    output_directory = Path(output_directory)
    if not source.is_file():
        raise ValueError(f"Self-test source does not exist: {source}")
    output_directory.mkdir(parents=True, exist_ok=True)
    template = resource_path("Keyhole example.dxf")
    keyhole_output = next_version_path(output_directory, "PACKAGED SELF TEST - AUTO KEYHOLES", "dxf")
    generate_procedural(
        source,
        template,
        keyhole_output,
        text_value,
        0.375,
        2.70,
        0.20,
        0.625,
        0.250,
        10.0,
        True,
    )
    audit_dxf(keyhole_output)
    preview = keyhole_output.with_suffix(".png")
    render_dxf_overview(keyhole_output, preview, scale=8.0)
    pattern_output = next_version_path(output_directory, "PACKAGED SELF TEST - AUTO MOUNTING PATTERN", "dxf")
    pattern_preview = pattern_output.with_suffix(".png")
    generate_pattern(
        keyhole_output,
        pattern_output,
        pattern_preview,
        "PACKAGED SELF TEST",
        1.0,
        0.75,
        False,
        0.25,
        0.25,
        False,
    )
    audit_dxf(pattern_output)
    report = output_directory / "PACKAGED SELF TEST - PASS.txt"
    report.write_text(
        "Letter Mounting Automation packaged self-test passed.\n"
        f"Keyhole DXF: {keyhole_output.name}\n"
        f"Pattern DXF: {pattern_output.name}\n",
        encoding="utf-8",
    )
    return report


def run_ui_smoke_test(report_path: Path, source_path: Path | None = None) -> Path:
    """Construct, paint, and optionally load a DXF in Tk without interaction."""
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as report:
        faulthandler.enable(file=report)
        faulthandler.dump_traceback_later(15, file=report)
        try:
            app = MountingAutomationApp()
            app.withdraw()
            app.update_idletasks()
            if source_path is not None:
                source_path = Path(source_path)
                app.load_dxf(source_path)
                app.update_idletasks()
                if app.source_path != source_path:
                    raise RuntimeError(f"UI smoke test could not load {source_path}.")
                report.write(
                    f"Loaded source: {source_path.name}\n"
                    f"Detected backers: {len(app.backer_panels)}\n"
                    f"Selected backers: {len(app.selected_backer_indices)}\n"
                    f"Active workflow: {app._active_workflow_mode()}\n"
                    f"Generate action: {app.generate_button.cget('text')}\n"
                )
                if app.backer_panels:
                    collected = app._collect_job(BACKER_MODE)
                    report.write(f"Collected backers: {len(collected['backer_bounds'])}\n")
            report.write(
                "Letter Mounting Automation UI smoke test passed.\n"
                f"Requested layout: {app.winfo_reqwidth()} x {app.winfo_reqheight()}\n"
                f"Configured window: {app.winfo_width()} x {app.winfo_height()}\n"
            )
            def report_widgets(widget, depth=0):
                if depth > 4:
                    return
                for child in widget.winfo_children():
                    report.write(
                        f"{'  ' * depth}{child.winfo_class()} requested: "
                        f"{child.winfo_reqwidth()} x {child.winfo_reqheight()}\n"
                    )
                    report_widgets(child, depth + 1)

            report_widgets(app)
            report.flush()
            app._close_app()
        except Exception:
            traceback.print_exc(file=report)
            report.flush()
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
    return report_path


if __name__ == "__main__":
    if "--ui-smoke-test" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--ui-smoke-test", type=Path, required=True, metavar="REPORT_PATH")
        parser.add_argument("--ui-smoke-source", type=Path)
        arguments = parser.parse_args()
        run_ui_smoke_test(arguments.ui_smoke_test, arguments.ui_smoke_source)
    elif "--self-test" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--self-test", type=Path, required=True, metavar="SOURCE_DXF")
        parser.add_argument("--self-test-output", type=Path, required=True)
        parser.add_argument("--self-test-text", required=True)
        arguments = parser.parse_args()
        run_packaged_self_test(arguments.self_test, arguments.self_test_output, arguments.self_test_text)
    else:
        MountingAutomationApp().mainloop()
