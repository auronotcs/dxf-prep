LETTER MOUNTING AUTOMATION

1. Open a DXF. The Open dialog remembers the last successfully used source
   folder between runs.
2. Choose the Stud Holes, Backer + Pins, Keyholes, or Pattern tab. Backer-panel
   drawings automatically open Backer + Pins; ordinary drawings open Stud
   Holes.
3. For Stud Holes or Keyholes, enter only the actual lettering. Turn on Select
   logo/shape in preview for a logo; do not type a fake character for it.
4. Configure the selected workflow. Keyhole calibration, load, LED, and file
   paths are under Show advanced keyhole settings.
5. Click the context-aware Generate ... Review button. Generation stays in the
   temporary review area and does not save a production DXF.
6. Switch between Full Sheet and Tile Review. Click any tile for a larger
   inspection view.
7. When the review is approved, open Output. Save all approved DXFs, save the
   currently displayed review, or choose a specific master, letters-only,
   backers-only, or mounting-pattern DXF.

Clear Generation returns to the source artwork. Ctrl+Z restores a cleared
generation, reverses logo selections, and undoes text edits.

During hole and keyhole generation, the preview marks the current shape in
yellow and completed shapes in green. The bottom bar shows the piece count and
elapsed time. FINISHED or FAILED is shown explicitly when work stops.

Procedural keyhole jobs estimate finished channel-letter mass from:
- face area x 4.5 mm polycarbonate;
- face area x 0.125-inch aluminum;
- twice the complete perimeter x 0.063 x 2-inch aluminum coil; and
- LED centerline equal to half the perimeter, using the editable LED lb/ft.

Estimated mass is shown for the job and on each review tile. Planned load per
mount defaults to 5 lb and is editable. Geometry rules remain the minimum;
mass can raise the mount count. Preferred terminal reach is a limited safeguard
for genuinely long strokes, not a requirement to cover every outline point.

Keyhole tile reviews show an amber dotted LED guide along the estimated medial
module path. LED path clearance defaults to 0.750 inch and biases mounting
points away from that guide when the back has room; it is not a hard failure on
narrow strokes. The guide is review-only and is not written to the approved DXF.
Detached dots, apostrophes/commas, and other compact punctuation use two
anti-rotation mounting points, with a 1.000-inch tool-access spacing exception
when required.
The configured coil/edge clearance is a hard minimum for both keyholes and
round fallbacks. A complete all-keyhole layout is preferred whenever it can
also satisfy the required mount spacing; round holes are used only when the
letter cannot support that compliant keyhole layout.

For day-to-day testing, launch Run Letter Mounting Automation.bat. The EXE only
needs to be rebuilt for a stable production release.

BACKER / ROLL-PIN JOBS

When a DXF contains large panels with nested copy, the program detects the
backers automatically and selects all of them. Lettering text is not required.
Turn on Select Backers and click a panel to include or remove it from the job.

Generate Backer Pins uses one center map for three registered review files:
- master artwork with both concentric hole diameters;
- letters-only artwork with the letter roll-pin holes; and
- backers-only artwork with the larger clearance holes.

The default letter hole is 0.125 inch, backer clearance hole is 0.140 inch,
material beyond the letter-hole edge is 0.125 inch, and pin spacing is 0.500
inch. All four values are editable. Compact pieces receive two pins whenever
the configured material and spacing rules allow it. Identical copies reuse the
same local pin layout. Use Full Sheet, Tile Review, Letters, and Backers to
inspect the result. Output > Save all approved DXFs opens three consecutive
Windows Save As dialogs. Save letters-only DXF and Save backers-only DXF each
open one dialog when only that production file is needed.

Typical measured time on the 27-shape Arkansas Federal test:
- Stud holes: about 1 second after the job starts.
- Procedural keyholes with electrical holes: about 17 seconds.

Complex artwork may take longer, but the elapsed timer should keep moving and
each completed shape should be visibly marked.
