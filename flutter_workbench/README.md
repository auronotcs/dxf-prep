# CAM Workbench — Flutter evaluation

Native Windows Design and Manufacturing evaluation workspaces, October 28, 2026. Flutter replaces the interface and calls a captured copy of the existing Python core for document and geometry operations. The live Tkinter application remains available for production work.

## Open

Double-click **Run Flutter Prototype Windows.bat** in the parent Boat stuff folder. This opens the functional Design evaluation. Keep this project folder and backend/ in place; this local build uses the existing Python runtime under the current user's .cache/codex-runtimes/codex-primary-runtime/dependencies/python/ directory. It is not yet a portable installer.

**Run Flutter Prototype.bat** still opens the earlier browser-only visual demo on localhost:58743. The Python-connected Design workspace is currently Windows-only.

## Live UI development

Double-click **Run Flutter Development.bat** in the parent Boat stuff folder. It builds and opens a debug version with **Reload UI** at the top right. Initial startup can take a few minutes. The helper runs in the background; no terminal needs to remain open. Launching it again while it is building or running does not create another development editor.

After saving Flutter/Dart UI edits, click **Reload UI**. Supported changes apply to the running screen, retaining the document, selection, view and Python backend. A short message confirms success or reports failure. The button is disabled during reload and while the document engine is busy. A rejected patch never triggers an automatic restart. The button appears only in debug sessions started by this launcher; opening the release executable does not enable hot reload.

Hot reload does not rerun startup code or apply Python/native C++ changes. Changes requiring restart must be handled by saving the drawing, closing the app normally, and launching again. Closing the development app also ends its Flutter runner and local reload helper. The existing release launcher is still available; save any drawing there before transferring it to development mode.

For startup or compiler errors, inspect `build/development.log`. `tools/dev_launcher.py` owns `flutter run --machine` and sends `app.restart` with `fullRestart: false` over Flutter's documented daemon protocol. A random loopback port and per-session token connect the button to that specific runner; no remote update/download service is involved. Generated logs and session credentials stay in ignored `build/` files.

## Stock and Manufacturing

Manufacturing now uses a compact ribbon, left-aligned stock/material/active-plate bar, left navigation strip and a larger canvas. **Inspect** opens or hides the operations/review panel. Preparing a draft opens that panel with review controls at the top. **Outside**, **Inside**, **Cut Line**, **Cleanout** and **Drill** act on the current compatible drawing selection. **More** exposes the existing advanced operation controls. Use **Design** to return to the drawing/file workspace.

**Library** opens the searchable Tool & Recipe Library. Choose a tool, material recipe and station, then **Load into T… draft**. **Turret** shows all 12 stations and edits cutter geometry, spindle mode/RPM limits, feeds (displayed per minute), reach and stepdown. Station T6 retains its 12,000-RPM maximum; T8 remains spindle-disabled. **Review turret changes** prepares actual motion without changing the job; inspect and Apply, or discard. Used stations cannot be cleared, and Undo restores the previous rack and limits.

The existing Tkinter catalog (14 saved entries) and eight-station rack were copied read-only into `backend/user_state/tool-catalog.json`. The merged library includes additional bundled entries. In Turret Setup, **Load saved turret preset** loads that rack into a private draft, converting dimensions and feeds to the current drawing units. **Save applied turret as preset** saves the current applied rack for other Flutter jobs. **Save applied station to library** creates a reusable custom tool/recipe; custom entries can be deleted. These library/preset changes are separate from document Undo. Tkinter settings and the current drawing are not changed by browsing or loading a draft. Full recipe authoring/qualification and specialized operation authoring remain separate work.

Use **Run Flutter Development.bat**. The Design stock bar now offers **Material** and **Thickness**, with Tkinter's standard choices, physical inch/mm presets and custom values. Custom thickness uses the drawing's units. Changes are undoable and save with the drawing. **Verify stock** shows the current sheet/machine setup and requires three explicit physical confirmations; changing the relevant setup invalidates it.

Open **Manufacturing** in the header (**CAM** in compact windows):

1. Set stock thickness and safe Z; add document-owned end mills or drills. Feeds use document units per second.
2. Manufacturing opens on **Drawing**, showing the existing geometry and stock before any toolpaths are generated. Click a contour; Shift-click adds/removes, and dragging a box selects enclosed contours. Scroll to zoom, middle-drag or choose Pan, and use Fit drawing/Fit stock. This canvas selects geometry without moving it. Then use **Add profile**, **Add drilling**, or **Add cleanout**. New operations inherit compatible selected contours; cleanout also has a separate protected-island selection. Click an operation to edit it. Switch between **Drawing** and **Toolpaths** as needed; completed drafts/generation show the actual motion automatically. Changing selection invalidates an earlier motion review, as other document revisions do.
3. Use the tune icon beside a profile for **Profile entry / finishing** (ramp/helix, leads, finishing stock/feed/RPM). Use **Second-tool workflow** for retained skin, tabbed profile + rest, or clear waste + tabbed finish. Tabbed strategies use stock thickness + breakthrough and retain physical bridges; hybrid clearing requires bounded inside waste. **Add Speed Cleanout** offers engagement/link/feed limits and fallback. Prepare a draft, inspect actual motion/settings, confirm review and Apply once. Group editing/toggling/deleting acts on the whole group; Undo and Save As preserve it.
4. Choose output plates, then **Generate and check toolpaths**. Select a preflight finding to highlight its contours/location and access relevant editors. Inspect each plate's actual XY motion and numeric XYZ/feed; pan/zoom and use the motion slider. Changes require fresh generation/review.
5. Optionally enable **Require verified physical stock for export**, then complete **Verify stock** against the actual material and machine. This does not qualify a recipe for production. Confirm motion review and **Export reviewed package**. A fresh folder under `build/manufacturing-output/` receives selected-plate CNC/CAM files and a publication manifest. The plate list is shown before export. No machine delivery occurs.

Use **Add profile with holding** (or the profile tune icon) for stock-based depth, onion skin and automatic tabs. **Through** derives depth from stock thickness plus breakthrough; **Partial** must stop above the stock bottom. A thickness change blocks an outdated through/partial profile until its draft is reviewed again. Onion skin remains during roughing and is removed by the final full-depth pass. Tabs can remain raised through that pass: choose Even, minimum-and-spacing, corner-avoiding, support-aware or corner-and-spacing placement. Even with count zero disables tabs. Other modes use count and maximum spacing. Width is finished retained material; the core adds cutter diameter to the raised cutter span. Inside contours below the minimum perimeter receive no tabs. Inspect the generated block details and actual XYZ motion. Automatic tab removal remains protected.

**Add Cut Line** cuts visible, unlocked lines and open polylines with on/left/right compensation, plunge/ramp entry, leads and custom depth increments. Left/right follows the source geometry direction; reversing cut direction does not swap compensation. **Add on-contour cut** follows a closed contour without cutter-radius compensation. Both prepare a nonmutating motion draft with explicit review and Apply; their operations retain Undo and saved-job identity. Open paths cannot use closed-profile tabs/finishing or second-tool groups.

Supported scope: closed profiles with exposed entry/finishing/holding controls, open Cut Line, adapter-authored retained-skin/tabbed/hybrid groups, circle drilling and Concentric/Speed Cleanout. Custom profile/fill schedules, floor finishing, fill ramp/helical entry and proven Keep Tool Down links are supported through reviewed drafts. Advanced drilling cycles, short-loop cleanup, curve-feed compensation, contour-order choices and paired-rectangle common lines are supported through reviewed drafts. Project-wide cycle optimization and unverified imported groups remain protected. Design geometry remains protected while machining references it. Preview is XY with numeric depth inspection, not 3D stock simulation. The core currently rejects one millimeter circular-ring Speed Cleanout case for incomplete coverage; Flutter keeps that guard. Release/browser builds are older.

## Try Design

`examples/design-evaluation.camjob.json` is a synthetic starter drawing with a panel, holes, converted lettering, and editable shapes. Use Open to load it and Save As for your changes.

1. Open a DXF or a geometry-only CAM JSON drawing, or start a new drawing in inches or millimeters. An opened source cannot be overwritten: use Save As to create an evaluation copy. Subsequent Save updates that copy.

2. Choose Line, Rectangle, or Circle and click two points. More Create commands include ellipse, slot, polygon, text, arrows, chevron, star, and precise dimensions. Text can be edited in the Inspector or converted using Python's outline engine.

3. Select objects with a click or selection box; Shift extends selection. Drag selected objects to move them. Use Move/rotate, Scale, Mirror, Duplicate, clipboard, grouping, alignment, spacing, booleans, or Auto nest.

4. Pan with the middle mouse button or hand tool; scroll to zoom. Enable Snap for geometry features, visible guides and a 1/8-inch grid (3.175 mm), within 10 screen pixels. The amber marker names the active snap target. Fit drawing and Fit plate are separate controls.

5. Drag a toolbar group heading onto another group to reorder it, or drop it over the canvas to float it. The small outward-arrow button also floats a group. Move a palette by its header; drag it back to the toolbar or use its upward dock button to return it. Layout, theme, grid, and snapping are saved locally. The header's layout button resets the panels.

6. **Drawing layer** chooses the layer for newly created objects; it does not move existing geometry. **Layers…** opens add/delete controls, visibility, locking and object counts. Layer 0 is the default and cannot be deleted. Deleting another unlocked layer moves its objects to layer 0 and can be undone. Existing sessions need one save/restart to load the Python deletion command; the dialog explains when this is required. Inspect or change active plate and plate dimensions. Save/reopen a .camjob.json evaluation copy or export DXF. Undo/redo operate on the Python document history.

Shortcuts include Ctrl+O/N/S, Ctrl+P (Shop drawing PDF), Ctrl+Shift+S (Save As), Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z (Redo), Ctrl+A/C/X/V, Delete, Escape, and arrow-key nudging (one project unit). File/Edit menus show shortcut hints. Commands and closed dialogs return focus to the drawing; text fields retain normal local editing shortcuts while active.

The bottom HISTORY strip provides first state, undo, redo, latest state and clickable state markers (hover for action names). New edits after going back replace that forward branch; saving does not clear history. History is session-only and starts afresh when opening a document. Already-running backends need one save/restart to enable the timeline. Grouped repeat stacks and history pruning are not yet ported.

The status strip shows pointer XY, selected width/height, perimeter or open-path length (summed for multiple selections), action status, zoom percentage and the object beneath the pointer, including overlap count. Existing object names/numbers are shown when available, otherwise a stable ID suffix. Hover readouts include visible locked objects; hidden objects are excluded. Click the zoom percentage for 100% (96 logical pixels/inch with display scaling), which changes the view only. The strip uses two compact rows on smaller windows.

Physical Ctrl+O was confirmed by the user, with key receipt verified during diagnosis. Shortcut dispatch, dialog focus restoration and text-field isolation pass widget tests. Synthetic native keyboard injection remains unreliable; this does not establish a failure of physical shortcuts, but every native chord has not yet been individually checked.

## Current boundaries

- Manufacturing edits and isolated reviewed export are available for the supported families above. Associated Design geometry and unported machining settings remain protected. Machine/controller delivery is not connected.
- Common Design, recovery and independent document tabs are implemented; full specialized mounting/LED, advanced machining, large-job benchmarks and portable packaging remain.
- Text uses Flutter font rendering; Python's converted contours are authoritative.
- Local preferences remain in backend/user_state/design-layout.json. The selected immutable core is 7a34a3f40d41d0f2 (172 files, src-20261027-bb8214df9ca3). backend/source_manifest.json chooses the snapshot for each new backend process; running processes keep their own source identity. No runtime imports come from production.

Modify now includes Offset outline/inline, Convert to shape (circle recognition, circle from bounds, rectangle from bounds), and Editable outlines. Select geometry, choose settings, Preview, then Apply. Blue shows the result and gray the sources. Offset retains the originals; conversion replaces them, preserving source identity/layer/appearance according to Tkinter's geometry engine. Each Apply is one undo step. Preview is invalidated by drawing/selection changes; cancel leaves the drawing untouched. Text still uses the separate Text to curves command.

Arrange commands now open Align & Space. Choose selection bounds, a fixed selected object/group, a locked object/group, the active plate, or a named Patchwork cut area; equal spacing requires at least three items and exact edge gaps require two. Groups count as one item. The purple box marks the reference. Exact gaps start at the left/bottom edge for a plate reference and may extend beyond the plate. Preview and Apply use the current document units and preserve a stationary reference.

For a locked reference, select only the objects that should move, open an Arrange command, choose **Reference > Locked object / group**, then choose the stationary item. The reference stays locked and outside the editable selection; all group members must be visible. Preview shows its bounds in purple. It counts as one item when spacing: one moving item plus a locked reference is enough for exact gaps; equal spacing needs at least two moving items plus the reference. Apply is one undo step. If the list is empty, lock the reference layer in Layers and reopen Align & space.

Modify also includes Join / Repair Contours, Remove micro backtracks, and Remove near-duplicate points. Adjust tolerance, Preview, then Apply. Purple markers show endpoint joins, orange shows backtrack cleanup, and teal shows redundant points. The summary reports open/closed results, removals and remaining crossing/overlap issues. No edits occur on Cancel; each Apply is undoable. Machining-bearing jobs protect Design geometry; use Manufacturing for supported basic profiles.

Select objects and choose **Modify > Object Fill / Stroke** to edit RGB, CMYK or named fill/stroke colors, visibility and stroke width in document units (0 is a hairline). **Use layer appearance** clears object overrides. Apply is one undo step; Cancel leaves the drawing untouched. Unchanged authored colors retain their exact values and palette provenance; changed colors use the Python color model. **Palette…** beside Fill or Stroke opens searchable swatches with palette filtering, product codes and exact color/source details. Use **Import…** for CAM Workbench palette JSON or supported Sherwin-Williams XLSX/ZIP files; review Import/Replace before committing. **Remove palette** removes only the library entry, preserving colors already stored in drawings. **Refresh palettes** rereads the library. No manufacturer palette data is bundled. The Flutter library starts separately from Tkinter and is saved at backend/user_state/color-palettes.json. Selecting a swatch changes only the appearance draft until Apply; Cancel preserves the drawing.

Open and Import now run a focused contour check for unambiguous small endpoint gaps (default 0.001 inch, scaled to document units) and microscopic backtracks. **Inspect > Check contours** runs it again. The review marks gaps/backtracks and offers **Keep drawing unchanged** or **Review repair**. Review selects complete editable chains; **Preview**, then **Apply**, commits one undoable repair. Hidden/locked chains and machining-bearing jobs cannot be repaired here. Increasing tolerance also checks nearby unselected geometry to avoid partial/ambiguous joins. This does not replace the full geometry inspector or small-feature recommendation workflow.

## Verification and development

**Modify > Edit points** enters graphical editing. Click a line, rectangle or polyline; drag its square handles to move vertices. Shift/Ctrl toggles multiple points; dragging one selected handle moves that set. **Select all points**, **Add point**, **Delete points**, and **Done** appear in a compact strip above the canvas. Add point inserts where you click into the nearest segment, including the closing edge. The numeric **Point coordinates** dialog remains available for polylines.

Point drags preview without changing the drawing until release, with one undo step per committed edit. Release outside the canvas or press Escape to cancel a drag. Escape cancels Add point first, then exits point mode; Done also exits. Delete removes only selected vertices, never the object, and keeps at least three points in closed contours or two in open paths. Arrows nudge selected points by one document unit. Rectangles become closed polylines when activated, preserving identity/layer/appearance; this conversion is separately undoable. Curves need Editable outlines first. Snap uses visible geometry/guide targets and the physical 1/8-inch grid; the edited contour is excluded from its own targets. Drawing/selection/history changes invalidate stale handles; click the contour again to continue. Native automated keyboard input remains unverified, so use visible controls where available.

Current milestone: 164 Flutter tests, 153 unique Python checks and clean analysis. This batch adds 23 focused upstream checks on the unchanged captured backend. Widget tests cover forms, cancellation, review/output gating and compact layout; native checks cover mounted Manufacturing, worker-generated motion, reviewed export, history/recovery and original-drawing preservation. Synthetic inch/mm CNC instructions match the pinned core. See SESSION_LOG.md for evidence and limitations.

From this directory:

```powershell

..\.flutter-sdk\bin\flutter.bat analyze

..\.flutter-sdk\bin\flutter.bat test

..\.flutter-sdk\bin\flutter.bat build windows --release --no-tree-shake-icons

```

Keep --no-tree-shake-icons for this evaluation: an incremental build reused the earlier demo's font subset and omitted newly used icons. The full font fixed the native rendering.

lib/design/ owns the native workspace, canvas, and process client. backend/design_bridge.py wraps the captured AutomationSession and existing geometry helpers. backend/test_design_bridge.py runs with the existing Python runtime. lib/main.dart, model.dart, and drawing.dart retain the earlier web demo.

The ongoing conversion follows CONVERSION_PLAN.md, current handoff CONVERSION_STATUS.md, FEATURE_COVERAGE.md, and SESSION_LOG.md. Daily sessions remain scheduled for 3 PM Central, approximately one hour.

## Syncing ongoing Tkinter development

Start every session with `python -B backend/sync_source.py stage` using the same Python runtime as Design. It stages the allowlisted backend in a new versioned directory, records source and GUI hashes, and verifies the source did not change during capture. It also discovers newly added Python core modules before the production release manifest is updated. Review the reported delta and new UI work; run bridge, affected upstream and Dart/Python integration tests against the candidate by setting `WORKBENCH_BACKEND_MANIFEST` to the returned manifest path. Tests must use the isolated candidate, never imports from production.

After verification, run `python -B backend/sync_source.py adopt --manifest <candidate-manifest>`. Adoption rechecks candidate hashes and the current production source, archives the prior manifest, and atomically changes the default manifest. It never replaces core files used by running sessions. Clear any candidate test environment override after testing. New sessions use the adopted snapshot; save and restart existing Flutter sessions when ready. `python -B backend/sync_source.py verify` checks the selected snapshot. Keep the old core folders and checkpoint for rollback. GUI-only changes are tracked as new source observations without replacing backend files.

## Push-through fit and Patchwork

**Macros > Push-through fit:** assign each closed contour as Metal, Acrylic or Unused in the source list. Clicking a row highlights its geometry; Selection → metal/acrylic assigns the drawing selection. Enter cutter diameter and clearance per side in the displayed document units, then **Analyze fit**. Review each interference region, include/skip it, or **Zoom to region**. Broad mismatches start unchecked. Gray is original artwork, blue the reviewed metal, green the diameter-based routed acrylic approximation and orange the focused region. **Apply metal corrections** changes only the metal opening in one undo step, preserving the acrylic and an original-metal record. Changing source roles or settings requires analysis again; Cancel leaves the document unchanged.

**Macros > Patchwork:** define physical drop-material rectangles on the active plate. Coordinates are local to that plate. Enter dimensions, origin, count, gap and X/Y repeat direction, then **Add pieces**. Select a listed piece to edit with **Update selected** or remove with **Remove selected**. These actions change only the draft; **Apply layout** commits the reviewed layout as one undo step. Overlaps and pieces outside the plate are rejected. Teal guides appear on the drawing and never become cutting geometry or DXF entities. Arrange > Reference > Patchwork cut area aligns to a named piece. Auto nest uses the physical areas, leaves gaps empty and refuses overflow plates when Patchwork is present. Automatic repacking and editing the table envelope inside this dialog remain pending; use the existing plate controls for envelope changes.

## Shop Drawing PDF

Use **File > Shop drawing PDF** or **Ctrl+P**. Choose the plates, paper size (Letter, Tabloid, ARCH C or ARCH D), orientation, and Fit / Actual size / Custom 1:N scale. **Prepare PDF** renders a staged copy and reports each page's physical size and scale, omitted/crossing objects and unresolved dimensions. **Save PDF…** opens the native Windows PDF destination picker; canceling writes nothing. Existing files are replaced atomically only after successful preparation. **Open PDF after saving** uses the default Windows PDF viewer; a viewer failure leaves the saved PDF intact. Paper/scale/viewer preferences are remembered separately from document content. Print at 100% to retain the reported scale.

Fit never enlarges beyond 1:1, while actual/custom scales reject clipping. Like Tkinter, each page contains geometry fully inside its plate, rebased to that plate's origin. Layer visibility does not filter the PDF; hidden annotations are excluded. Existing dimension annotations render without becoming geometry or toolpaths; dimension authoring and construction guides are available in the Create/Inspect menus. PDF generation does not alter geometry, selection, undo history, revision or dirty state. Machining-bearing jobs remain view-only in this evaluation, and the live CAM Workbench folder cannot be an output destination.


## Construction guides

Drag from the top ruler to create a horizontal guide, or from the left ruler for a vertical guide. In Select mode, hover an existing guide and drag it to move it; the orange preview commits on an inside release as one Undo step. Escape, outside release or a document change cancels the move. Snap can align it to geometry, the grid and other visible guides without snapping back to itself.

Right-click a guide for an exact coordinate/name edit, lock/unlock, hide or delete. Locked guides remain snap targets but must be unlocked before moving or deleting. Inspect > Construction guides manages hidden guides and all-guide visibility/locks. Guides remain non-cutting document annotations; changing them preserves selected geometry.


## Direct dimension editing

In Select mode, click a shop dimension's label or line to highlight it. Drag horizontal, vertical or aligned dimensions to adjust their offset; each release makes one Undo step. Escape or an outside release cancels. Right-click for the full Edit dimension dialog or confirmed Delete; the Delete key also targets the selected dimension. Measured geometry and feature references remain unchanged. Locked layers must be unlocked through Layers first. Radius dimensions have a diamond beside the label: drag along the radius line to change label offset without changing the measured radius. Angular dimensions have a round arc handle: drag the handle, arc or label toward/away from the vertex to change arc radius while retaining the measured angle. Minor/reflex settings are preserved. Free leader and note labels have a square handle: drag the label or handle in any direction to reposition the landing, keeping a leader anchor fixed. A leader cannot collapse onto its anchor. Geometry-linked landings stay attached; right-click Edit and choose **Free coordinates** for the landing reference to detach it explicitly before dragging. Diameter placement remains constrained by the core model.


## Split along a construction guide

Select closed shapes, then use **Modify > Cleave / Split along guide**, or right-click a visible guide and choose **Split selection along guide**. Choose the guide, **Preview**, review both sides and dependent effects, then **Apply**. Both sides are kept; the split removes no material and the guide stays unchanged. Locked guides work as references. Complete compounds/holes are included, while locked source/dependent layers prevent the split. Undo restores the original drawing in one step. Circular sources use polygon outlines; linked dimensions keep their last measured points and need new references after splitting. For a finite segment, choose **Modify > Cleave / Split: draw line**, then click two endpoints across the selected outlines. Extend the segment beyond both sides of each shape you want to split. Review **Preview** before **Apply**; outlines beyond the segment remain unchanged. Escape cancels an unfinished line.


## Geometry Inspector

Use **Inspect > Properties** (or the header Inspector button) to show the right-hand panel. Select an object to see its coordinates, bounds, dimensions and applicable length/perimeter/area in drawing units. Circle, polyline and text details appear for their respective types. Values are selectable for copying.

With multiple objects selected, the panel shows combined bounds and totals; expand an object for its details, or use **Show more** for larger selections. Contour areas are summed individually, without subtracting holes or overlap. Self-intersecting polylines use the core polygon-area calculation, which can cancel. Editable text has approximate visual bounds and no cutting length/area until converted to curves.

Buttons open the existing geometry editors. Hidden or locked selections and view-only jobs remain inspectable, with editing disabled. Unlock/show the whole selection before editing.


## Unsaved drawing recovery

The native Flutter app automatically updates a recovery copy after committed drawing changes in its own **backend/user_state/recovery** folder. It does not save over your drawing. Selection-only changes do not rewrite the copy.

After an interrupted session, choose **Recover copy**, **Discard…** (with confirmation), or **Later**. You can return through **File > Recover drawing…**. Copies from running sessions are excluded. To recover while already working, use **File > New** to create another tab, then **File > Recover drawing** there; recovery requires an empty, clean drawing and keeps your other tabs intact.

A recovered drawing preserves its geometry, dimensions, guides and units, starts a fresh Undo history and is marked unsaved. Use **Save As** to keep it under a new name; the original source is protected. Successful save/new/open or explicit close/discard removes only the current session copy. Cancelling a dialog or a failed save/open keeps the copy.

If a recovery write fails, the previous copy remains and a warning offers **Retry recovery**; use **Save As** to protect the latest edits. Recovery covers committed edits, not an unfinished drag/dialog or pre-crash Undo history. Copies are capped at 64 MiB; larger drawings show a warning and need manual saving. Machining jobs remain view-only and do not receive recovery checkpoints. Corrupt or unsupported copies are retained for review or explicit discard.


## Drawing tabs and shared clipboard

**File > New**, **File > Open**, Ctrl+N and Ctrl+O create independent drawing tabs. Switch using the compact tab strip; each tab keeps its canvas view, selection, Undo/Redo and recovery copy. Opening an already-open path selects its tab. Close with its X; unsaved drawings offer Save As, Discard or Cancel. Closing the window reviews every drawing before closing any tabs. A later Cancel leaves the tabs open (any earlier explicit saves remain saved). The last tab closes the window. This is a document strip, not a Design/Manufacturing workspace switcher.

Ctrl+C / Ctrl+X copy or cut selected geometry, or the currently selected dimension. Use **Edit > Copy dimensions / guides...** for an explicit mixture of selected geometry, dimensions and visible guides. Locked guides can be copied but must be unlocked to cut. **Paste** stays in place; **Paste to Current Layer** changes the pasted geometry/dimension layer. Guides have no drawing layer.

The clipboard is shared across tabs and survives closing its source tab for ordinary geometry and annotations. Paste creates fresh identities. Dimensions whose referenced geometry is also copied link to the new geometry; other references become fixed measurement points. Copy those owners too when you want associations preserved. Paste requires matching units. LED placements remain restricted to their original drawing tab; unit conversion and specialized saved rail-plan cloning remain pending. Clipboard data is internal to this running app, not the Windows text clipboard.

The native Debug development build contains these changes. Release and browser bundles remain older. Repeat the isolated file/output trial with the bundled Python runtime and **tools/design_workflow_trial.py**; it writes new test files only under build/design-trials.


## Recipes, passes and cleanout entry

In **Manufacturing > Second-tool workflow**, save a recipe from an applied group, or load one for a primary profile. The library lives only in `backend/user_state/second-tool-recipes.json`. Matching checks the cutter identity and station; if the cutter changed, choose a replacement explicitly. Settings convert with drawing units. Loading opens a new draft for review against the current drawing and stock. Save/delete do not change drawing Undo history. Reopen the dialog if another tab changes the library.

Use the profile tune icon for comma-separated **Pass increments**; blank uses maximum stepdown, and the core appends any remaining roughing depth. Use the cleanout tune icon for **Cleanout passes / entry / links** (also available in Speed Cleanout). Choose maximum stepdown, equal roughing count, or custom increments; custom fill increments must total target depth minus reserved floor stock. Enable floor finishing and optionally specify its feed. Review the generated depth schedule and motion before Apply.

Choose plunge, ramp, or helical fill entry. Helical entry requires positive orbit diameter, pitch and ramp length, including when fallback is set to error. Fit limits and fallback are checked by the core. Nonzero dwell currently fails EnRoute output qualification. **Keep tool down between proven clear loops** exposes maximum link distance, engagement and feed percentage; the core only links when its clearance checks permit it. All applied operation changes remain one-step undoable.

The millimeter circular-ring Speed Cleanout issue is still guarded. Sampling-only fixes were rejected after other geometry regressions; see `build/circle-sampling-investigation.md` for the reproducible case and next correction requirements.


## Motion controls and advanced drilling

Use the profile **tune** icon for **Curve feed**, **Contour order**, and **Shared rectangle edge**. Wall compensation reduces tight-curve feed without exceeding the normal cutting feed. Inside first and part by part follow containment; source order is a literal override. Review **Generated block order / details** before applying. Per-contour inside/outside overrides and manual job-specific ordering are not yet exposed.

Paired-rectangle common lines require compatible outside plunge cuts with no allowance, leads or tabs, and rectangles separated by one cutter diameter within the chosen tolerance. Matching pairs cut the shared edge once; unmatched parts remain separate. Review the core's hold-down and no-match messages.

In the cleanout tune dialog, **Clean short residual loops once at final depth** enables sliver cleanup for concentric clearing. Set maximum loop length and optional link distance; 0 retracts between loops, and links cannot exceed one cutter diameter. Speed Cleanout retains its separate center-cleanup strategy.

Use **Add drilling**, then its **Drilling cycle** tune icon (or Edit) for equal peck counts, feed-controlled chip-break retracts, fed return to the surface, and feed/RPM overrides. Feeds are drawing units per second. Draft review lists cumulative peck depths and actual motion. Unsupported dwell and below-stock rapid retracts are refused by preflight. Apply requires explicit review, and Undo restores the previous cycle in one step.


## Specialized Manufacturing tools

Use **Manufacturing > Engrave**, or **CREATE > More** for **Drag knife**, **Thread milling**, **Pocket rest machining**, **Profile rest machining**, and **Final tab release**. Configure matching cutters in **Turret Setup** first; spring travel/tip angle and thread TPI/form fields are available. Operation forms prepare private motion drafts; review and Apply in the inspector.

Final release starts with an audit. Review every physical body and loose-waste warning, confirm independent restraint, and describe the verified method before preparing the final release operation. Confirm the actual release motion at Apply and export. A drawing group is not proof of hold-down. Editable text must become strokes before drag engraving. Threading supports the core's qualified 10-24/1/4-20 UN Class 2B cycles and requires verified cutter and pilot depth. Rest machining inherits source geometry/depth and requires a smaller cutter.

These are evaluation workflows, with synthetic inch/mm and native output checks. Physical cutter/fixture/customer-job qualification remains necessary. The development launcher now uses windowless Python workers on Windows to keep its bridge pipe stable.


### Shared Design and Manufacturing workspaces

Use **Design** and **Manufacturing** beside the Workbench name. Document tabs, stock/material/layer controls, Grid/Snap and history stay in the same workspace. Source-drawing zoom, position and selection are shared; each drawing keeps its own view. Both toolbars support dragging, floating and docking groups, with separate arrangements for the two workspaces. Manufacturing's Setup palette contains Tool Library and Turret Setup; additional creation commands are in Create's dropdown/menu. Drawing/Toolpaths changes only the Manufacturing view; generated motion has its own plate-local camera. Leaving Manufacturing cancels private uncommitted reviews as before. UI hot reload preserves open unsaved drawings; this update requires no backend restart.


### Manufacturing fixtures and keepouts

Use **Manufacturing > Setup > Fixtures / keepouts**. Add a rectangle in drawing coordinates or copy one selected closed contour. Edit/move, enable/disable and delete all prepare a private review; inspect the red zone, bounds, required clearance Z and regenerated motion before Apply. Changes are undoable and saved with the job. Required clearance Z is above the stock top and should include your clearance margin. Copies do not follow later source edits.

Checks use conservative polygon bounding boxes, cutter/known-holder envelopes and full-circle arc bounds, so some clear paths may be rejected. Supply holder diameter/stickout in Turret Setup for holder checks. Zones do not automatically reroute motion or raise Safe Z. Physical machine/fixture qualification remains separate. Output preserves zones even outside sheet edges.

The current unsaved tab retains its running backend; new tabs load the updated adapter. If the keepout button reports an older backend, keep your work open or save and reopen it when convenient. Reload UI alone does not restart Python. A saved evaluation example is at build/native-keepout-trial/keepout-evaluation.camjob.json.


### Graphical Manufacturing tools

Open **Manufacturing > Inspect > Graphical Manufacturing tools**, or an existing operation's menu. Pick a preferred profile start, place manual tabs on the compensated contour, select cleanout outer boundaries/islands, or click contours in cutting order. Pan/zoom inside the picker is independent of the drawing view. Click **Review picked changes**, inspect actual toolpaths, then Apply; Cancel leaves the job untouched and Apply is undoable.

Starts/tabs currently require one closed contour. Graphical tabs require plunge entry without a separate finishing pass; width means the finished bridge. Preferred starts may be adjusted by core clearance/cycle optimization. Manual tab locations are preserved when changing a start; automatic tabs may shift. Linked/dependent workflows and staged cleanup remain protected. Use the holding editor to remove all tabs.

Open a saved evaluation copy from `build/native-picking-trial/profile-after.camjob.json`, `fill-after.camjob.json` or `order-after.camjob.json` to try the updated backend. Already-open tabs retain their Python process; save/reopen when convenient. Reload UI does not restart Python or discard work.


### Complete linked Manufacturing workflows

In **Manufacturing > Inspect**, use a linked stage's **workflow (three-dot) menu** to replace source contours, bind/refresh/remove a geometry rule, edit original-profile contour compensation/order, or set whole-group sequencing/prerequisites. **Review revised DXF** below the operations list now replans complete linked groups too. Targets can come from the drawing selection or the source list.

Every change regenerates the complete workflow and opens a private motion review. Inspect target and stage changes, then Apply; Cancel is inert, and Apply is undoable. New source membership may change generated stages; references to disappearing stages are refused. Independent rest/release contracts still need their own qualification. Explicit contour overrides/order must be reset when incompatible with a new selection. Derived rest/clearing traversal remains controlled by the planner.

The saved `build/native-linked-trial/linked-evaluation.camjob.json` demo is open in a new tab. Earlier tabs retain their Python processes and unsaved work; save/reopen when convenient to use this capability there. UI reload alone does not restart Python.


### Edit custom tools and material recipes

Open **Manufacturing > Library**, select a custom entry and material recipe, then **Edit custom tool / recipe**. To customize a seed/imported entry, load it into a turret draft, review/apply, then use **Save applied station to library**. Dimensions and feeds display in the drawing's units; storage remains inches and inches/minute. Saving library data does not change the job. Use **Load into Tn draft**, review motion, then Apply when you want the job to use it.

Record **Shop tested** only with evidence describing the material/thickness, machine/setup, date and result. Editing cutting inputs resets the affected recipe to Prototype; cutter edits reset all its recipes. Save changed settings first, then record a new physical test separately. A library test record does not qualify the current job or output. Older running backends need a deliberate save/reopen to enable editing; Reload UI alone does not restart Python.


### Match recipes to the applied job

Open **Manufacturing > Library** or **Turret Setup**, choose a station, and select **Match job recipes**. Apply the intended cutter and choose actual stock material/thickness first. Select the process role to see matches and their exclusions, recorded scope, physical-test evidence and affected operations. Review a recipe explicitly, then **Load Tn draft**. This replaces that station's private draft with the applied cutter plus the selected rates/recommendation; other unsaved station edits are not used for matching. Operation depths, pass schedules, entries, stepover and holding remain configured as before. **Review turret changes** regenerates motion before Apply. Nothing is automatically production-qualified. Library/job changes require refreshing and reviewing the choice again.


### Edit recipe applicability

In **Manufacturing > Library**, select a custom entry and recipe, then **Edit custom tool / recipe**. Under **Recipe applicability**, enter a material family and optional stock-thickness range in the displayed units. Blank family uses the recipe name; blank bounds are unbounded. Choose **Any role** or specific roles. Select allowed entry methods, or clear all to leave entries unrestricted. Changing scope resets that recipe to Prototype; record new physical-test evidence separately. Saving changes only the private library. Refresh **Match job recipes**, explicitly load a station draft, and review turret changes before Apply. Stale editors require reloading the current library entry.


### Assign open cuts to output plates

In the Manufacturing inspector, open **Open-path plate assignments** above **Generate and check toolpaths**. Choose a plate touched by the path, or **Automatic ownership**. Each line/open polyline outputs whole on one plate; it is not clipped at stock boundaries. Ambiguous cuts need an explicit owner. Changes apply immediately and support Undo. **Select path** highlights the source geometry; **Refresh** reloads a stale review.

After generation, cuts extending beyond stock appear under **Open cuts beyond stock**. Review the complete motion, machine travel and fixture clearance, then check the acknowledgment before exporting the evaluation package. Job edits, regeneration and output-scope changes require review again. Fixture collisions and other blocking preflight findings cannot be acknowledged away. Production/controller destinations remain unchanged.


### Manage custom tools and material recipes

Open **Manufacturing > Library**, select a custom tool, then use **Add recipe**, **Duplicate recipe**, **Remove recipe**, **Retire recipe / Reactivate recipe**, or **Duplicate custom tool**. Scroll the library panel on smaller windows. Add recipe opens a blank form in the current drawing's units. Copies need a distinct name and start as Prototype, without copied physical-test qualification. A renamed recipe copy retains its material-family scope.

Removing a recipe requires confirmation and cannot remove the tool's final recipe. Retirement keeps the record but blocks new loading/matching; reactivation starts as Prototype. Library actions do not rewrite recipes already loaded in jobs or existing turret drafts. Load into a draft and review machining changes separately. If another drawing updates the library, reopen it before retrying the action.


### Knife presets and corner controls

Use **Manufacturing > More > Knife presets** to browse or import a chosen Tkinter knife-recipe JSON file by path. Import copies validated records to Flutter's private library; it does not change the source. To save, choose an applied enabled knife operation and a new preset name. Duplicate names for the same tool are rejected.

**Review with station…** requires a matching loaded knife identity, material and thickness. It opens **Add Drag knife**, clears physical Setup verified, and fills editable values. Confirm the actual blade setup, select contours and review the generated draft before Apply. Saving/importing/loading a preset never approves motion or modifies a job by itself.

**Drag knife** now includes outside corner loops, loop radius/legacy percentage, edge-aware fallback and separate cut/plunge/corner/lead-in feeds. Rates display drawing units per minute; blank fields use the cutter or existing legacy behavior. Core clearance checks still block unsafe loops/leads and fixture conflicts. Generated block details list fallback findings; edge-aware motion requires an additional corner-separation/scrap acknowledgment for Apply and output. Use **Show knife clearance** to inspect blocked or accepted findings without moving the drawing.


### Clearance findings and small centers

After a knife draft or output generation, use **Show knife clearance** in Operations & review. Choose a finding, zoom/pan locally, or switch between **Fit finding** and **Whole contour / plate**. Blue is the attempted path, orange dashed bounds are reserved clearance rather than a cut, and green is the checked fallback envelope. Output findings show plate-local coordinates. Failed generation cannot Apply or export; **Return to knife settings** restores entered parameters and target contours for correction and a fresh review.

For an enabled independent profile, open its **Operation workflow > Small-center cleanout**. Eligible enclosed inside/female centers can be selected in the table or with **Pick in drawing**. Ineligible rows explain the core restriction. **Turn off all** clears the optional centers through the same reviewed draft. **Preview selected centers**, inspect the center-out ramps preceding the profile, then **Apply workflow draft**. The clearing stays part of the parent operation and follows its cutter, depth, stepdown and feeds. Larger centers, protected artwork, linked groups and unsupported combinations need their separate workflows.


### Graphical final-release order

Open **Manufacturing > More > Final tab release**, audit the tabbed profile and plate, then choose **Pick release order in drawing**. Click a numbered body to remove it and click again to append it, or clear and pick the complete order. **Re-audit release order** recalculates the core release events and hazards; shared bridges may release bodies together. Locked/manual orders remain fixed. After reviewing the new audit, explicitly confirm independent restraint for every body and any loose-waste warnings, inspect actual motion, and Apply the reviewed draft. The picker has its own fit/pan/zoom and does not edit the source drawing or its view settings.


### Sheet Cutoff Lines

Open **Sheet Cutoff Lines** from Design's **Macros** group or Manufacturing's **Create** group. Choose a sheet or all sheets, vertical/right or horizontal/above placement, and clearance in the current drawing units. **Preview placement** lists each proposed line or skip reason; click a sheet row to inspect its preview. Hidden parts still occupy stock. **Apply cutoff lines** adds/replaces the proposals on the Cutoff layer in one Undo step and selects the new lines. Existing independent Cut Line operations keep their attachments, but old toolpaths are invalidated: generate and review again before output. Dependent/protected associations require their separate requalification workflow. Editing options or changing the drawing requires a fresh preview.


## Repair batch and small-object actions

In **Design > Inspect > Run Repair Suite**, choose selected objects or the whole drawing, then **Preview repair batch**. Safe repair uses bounded physical-unit tolerances. **Include reviewed endpoint fragments** separately permits artwork changes up to 0.002 in / 0.0508 mm and requires a new preview. Click a row for a close-up, or **Show batch** for context. Apply always commits the complete reviewed batch in one Undo step. Locked/hidden/protected objects stay unchanged; jobs with machining or generated-dependent changes require their owning workflow.

In **Small geometry / Specks**, select findings to **Delete selected** or **Unlock selected layers**. Deletion requires a separate whole-object confirmation and only accepts independent, unlocked findings. Unlocking affects every object on each named layer, preserves visibility and requires a fresh scan before deletion. Both actions are undoable. These commands require the new Python adapter on the next development launch; Reload UI alone cannot replace an already-running backend.


Corner alignment: open an alignment tool in **Design > Arrange** and choose **Align top left**, **Align top right**, **Align bottom left**, or **Align bottom right** in **Operation**. Choose the reference, Preview, then Apply. Locked reference geometry stays fixed and Undo restores the complete move. The choices require the new backend on the next development launch.
