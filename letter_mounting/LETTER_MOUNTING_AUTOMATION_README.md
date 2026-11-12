# Letter Mounting Automation

Use `Run Letter Mounting Automation.bat` while production rules are being refined. Rebuild the EXE only for a stable shop release. The program never overwrites the opened source DXF.

## Basic operation

1. Click **Open DXF**. The drawing appears immediately in the main preview.
2. Choose **Stud Holes**, **Backer + Pins**, **Keyholes**, or **Pattern**. Detected backer-panel files automatically open the Backer + Pins tab; ordinary files open Stud Holes.
3. Configure only the selected workflow, then click the context-aware **Generate ... Review** button.
4. Inspect the available review views, then open **Output**. Save all deliverables together, save the current review, or explicitly save the master, letters-only, backers-only, or pattern DXF.

Windows Save As controls every output location. Use the mouse wheel to zoom the preview, middle-drag or Ctrl+left-drag to pan, and **Fit** to return to the full drawing.

## Workflows

- **Stud Holes:** applies the established stud-placement rules and writes a grouped R2000 DXF.
- **Backer + Pins:** detects all nested backer panels, selects all of them by default, and creates one registered roll-pin center map. Use **Select backer panels in preview** to include/exclude panels. The defaults are a 0.125-inch letter hole, 0.140-inch backer clearance hole, 0.125 inch of material beyond the letter hole, and 0.500-inch minimum pin spacing. Review **Full Sheet**, **Tile Review**, **Letters**, and **Backers** before output. **Output > Save all approved DXFs** opens three Save As dialogs; the same menu can save only the letters or only the backers.
- **Keyholes:** uses the bundled keyhole part, coil-clearance, round-hole fallback, LED/hardware spacing, mass estimation, and unsupported-reach rules. Electrical openings have a separate on/off control and configurable diameter. Calibration, LED, load, and template settings are under **Show advanced keyhole settings**.
- **Pattern:** adds the bounding box, horizontal/vertical centers, and right-side Arial label to the opened DXF.

For either hole workflow, enable **Also make pattern after holes/keyholes** to receive both the production hole file and a separately labeled pattern file.

## Pattern compatibility

Compact continuous guides are the default and add only six LINE entities. Enable **Physical dash segments (Flexi-safe)** when Flexi must show guaranteed gaps; dash and gap lengths are configurable. Physical dashes do not depend on Flexi honoring a DXF linetype.

## Colors and layers

- Green: keyholes (`KEYHOLES`)
- Purple: 0.250-inch fallback mounting holes (`MOUNTING_HOLES`)
- Blue: electrical openings (`ELECTRICAL_HOLES`)
- Purple: letter roll-pin holes (`LETTER_PIN_HOLES`)
- Blue: backer clearance holes (`BACKER_CLEARANCE_HOLES`)
- Gray: physical dashed pattern guides (`PATTERN_GUIDES`)
- Pattern label: editable Arial text (`PATTERN_LABEL`)

All production dimensions are editable in the program before each run.
