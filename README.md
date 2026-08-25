# DXF Prep

DXF Prep is a small Windows desktop utility for preparing design-software-exported DXF files for CAM software sign fabrication workflows.

## Current Features

- Classifies DXF geometry into aluminum routing layers:
  - `DRILL_250`
  - `PROFILE_HOLES`
  - `INNER_SHELF_CUT_070`
  - `OUTER_PROFILE`
  - `ID_MARKING`
  - `REVIEW_UNKNOWN`
- Generates an **Aluminum Shelfed** DXF for review before saving.
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
- Provides a separate generated-output preview with named layer filtering and contour inspection.
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

Run the DXF Prep regression tests from the repository directory:

```text
py -m unittest -v test_classify_dxf
```

## Building

Install PyInstaller, then double-click:

```text
Build DXF Prep EXE.bat
```

The executable is written to `dist\DXF Prep.exe`.

## Notes

This tool is intentionally tuned for a specific sign fabrication workflow. It is useful as a starting point for similar CAM/design-software DXF preprocessing, but geometry classification rules may need adjustment for different export settings or shop conventions.

## Future Checks

- Commit `9d2cf3a` added acrylic-export seam cleanup by collapsing connected, collinear, very short line pairs. If aluminum exports show similar CAM software join/clean errors, consider applying the same cleanup to aluminum export as well.
