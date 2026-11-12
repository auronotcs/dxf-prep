# Shop tools (dxf-prep repository)

| Folder | What it is | Start it with |
|---|---|---|
| `dxf_prep\` | DXF Prep app (described below) and its tests | `Run DXF Prep.bat` |
| `letter_mounting\` | Letter Mounting Automation app, the hole, keyhole, backer-pin and mounting-pattern generators it uses, its tests and test fixture | `Run Letter Mounting Automation.bat` |
| `cam_probes\` | One-off investigation and smoke scripts that load CAM Workbench | run individually |
| `nesting-lab\` | Nesting experiments and their results | see `nesting-lab\README.md` |

`! START CAM Workbench.bat` opens the CAM Workbench app in `..\CAM Workbench`.
The Flutter app moved to `CAM Workbench\flutter\`; the `Run Flutter *.bat` files
here still open the retired copy in `flutter_workbench\` until it is removed.

Not in git: `letter_mounting\holegen_libs\` (vendored ezdxf, numpy, scipy,
shapely; needed by Letter Mounting), `.build_tools\` (PyInstaller), and local
folders listed in `.gitignore`. Shop jobs and drawings live in
`Documents\Shop Jobs`; old folder copies are zipped in `Documents\Archive`.

Tests: run `python -m unittest discover -p "test_*.py"` inside `dxf_prep\` or
`letter_mounting\`, with the same Python the launchers use.

## DXF Prep

DXF Prep is a small Windows desktop utility for preparing CAD-exported DXF files for sign-fabrication and CNC workflows.

## Current Features

- Classifies DXF geometry into aluminum routing layers:
  - `DRILL_250`
  - `PROFILE_HOLES`
  - `INNER_SHELF_CUT_070`
  - `OUTER_PROFILE`
  - `ID_MARKING`
  - `REVIEW_UNKNOWN`
- Generates an **Aluminum Shelfed** DXF for review before saving.
- Generates a **Plain Aluminum** DXF that combines `PROFILE_HOLES` with `OUTER_PROFILE` while retaining drill and marking layers.
- Generates an **Acrylic Clean and Cut** DXF with ordered layers:
  - `DRILL_250`
  - `CLEANOUT`
  - `ACRYLIC CUTOUT`
  - `ID_MARKING`
- Exports a poly-back-ready DXF with:
  - `DRILL_250`
  - `POLY BACK CUTOUT`
  - `ID_MARKING`
- Exports a cleaned DXF that preserves drawing contents while removing duplicate/degenerate geometry and rebuilding 0.250 drill holes as true circles.
- Preserves polyline, lightweight polyline, line, arc, and circle geometry from source DXF exports.
- Shows a color-coded preview with detected sheet/group boxes.
- Provides a separate generated-output preview with named layer filtering, contour inspection, multi-selection, and layer reassignment before saving.
- Provides per-sheet zoom buttons.
- Supports undo, multi-selection, configurable hit targeting, and automatic alignment to `(0.5, 0.5)`.
- Remembers the default and last-used DXF folder through local settings.

## Running

Double-click:

```text
Run DXF Prep.bat
```

The launcher uses the bundled Codex Python runtime when it is available at:

```text
%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

If that runtime is not present, the launcher falls back to the Windows `py` launcher.

The application uses only the Python standard library and requires a Python installation with Tk support.

## Testing

Run the DXF Prep regression tests from the `dxf_prep` folder:

```text
py -m unittest -v test_classify_dxf
```

## Building

Install PyInstaller, then double-click:

```text
dxf_prep\Build DXF Prep EXE.bat
```

The executable is written to `dxf_prep\dist\DXF Prep.exe`. (Builds made before
the November 2026 reorganisation are in the top-level `dist\`.)

## Notes

This tool is intentionally tuned for a specific sign-fabrication workflow. It is useful as a starting point for similar CAD/CAM DXF preprocessing, but geometry classification rules may need adjustment for different export settings or shop conventions.

## Future Checks

- Acrylic export includes seam cleanup that collapses connected, collinear, very short line pairs. If aluminum exports show similar join/clean errors, consider applying the same cleanup to aluminum export as well.
