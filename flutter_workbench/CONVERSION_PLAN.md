# CAM Workbench Flutter conversion

Established October 26, 2026. Owner: this conversion chat. User authorized an incremental conversion alongside active development, approximately one hour per day, automatically at 3:00 PM America/Chicago, every day including weekends.

## Working agreement

October 26 update: the user directed a faster, direct conversion of the existing Design workspace. Treat the Tkinter screens, command groups, and behavior as the specification. Build and connect useful workspace functionality each session; do not spread already available backend integration across a fixed viewer-first queue. The first Design session combined the baseline, persistent bridge, real drawing, editing, history, save/reopen, and customizable toolbars. CONVERSION_STATUS.md is the authoritative next task.

Build useful workflows incrementally and give the user working native Windows builds to evaluate. Preserve the existing Python geometry/CAM engine and replace the Tkinter presentation and interaction layer with Flutter. The production Tkinter application remains available throughout the transition.

October 28 preceding direction: bring Manufacturing visually closer to the supplied current Tkinter layout and add Tool Library/Turret Setup now. This overrides the preceding functional-first ordering for this batch. Preserve the resulting layout while continuing the remaining operation audit in CONVERSION_STATUS.md.

Prioritize functional coverage over cosmetic perfection. Fix issues that block the current workflow, corrupt documents, lose undo/recovery state, or change machining correctness immediately. Record other rough edges for later use-driven improvements. Do not spend the migration budget polishing already usable screens while core workflows remain disconnected.

October 28 latest direction: Design and Manufacturing share the document shell and source viewport. This is implemented and verified; retain it while continuing functional conversion. Workspace switches preserve drawing zoom/position, selection and grid/snap settings.

## Daily effort and usage discipline

- One automatic session per local calendar day at 15:00 America/Chicago. Target 50–60 minutes total elapsed time, including planning, tool waits, verification, and reporting.
- Read current time at entry and record start time and intended stop time in CONVERSION_STATUS.md. Check time at meaningful checkpoints, at least every ten minutes. At about minute 45 stop expanding scope; reserve the last 10–15 minutes for checks and handoff. Do not start a long build/test late in the session. Stop at a safe checkpoint by approximately 60 minutes; record unavoidable overrun rather than silently extending work.
- Finish early if the bounded task is complete or a real blocker prevents useful independent work. Do not idle merely to consume an hour. Do not automatically make up missed days, double a later session, or create additional scheduled runs.
- Use one agent. No parallel agents, additional chats, or cross-thread messages without new authorization. Use focused reads, diffs, and tests; run broader checks only when integration changes justify them. Do not change models or account settings to manage usage.
- Time is an effort target, not a hard token/account-usage cap. Track elapsed time and available usage information without inventing per-run token counts. The user can shorten, skip, or reprioritize sessions.
- If another conversion run is active, do not overlap it. Use the durable status record and actual process/task evidence; investigate a stale running record rather than blindly starting a second writer.
- A blocked run should perform a brief check, record the specific missing dependency, and stop rather than repeatedly retrying for an hour.

Normal session: 5 minutes reading the handoff and checking upstream changes; 35–40 minutes implementing one bounded step; 10–15 minutes verifying, preserving a runnable state, and reporting.

## Locations and current evidence

- Conversion: `C:\Users\auron\Documents\Boat stuff\flutter_workbench`.
- Live reference source, read-only for this conversion: `C:\Users\auron\Documents\CAM Workbench`.
- Current live build observed during planning: `src-20261026-9cdb470fc643`; recorded source revision `9723c98a7ccc97baf364900a23b06e94ab424453`. These identifiers are observations, not a frozen migration baseline. Working-tree edits may differ from build metadata.
- Existing headless entry points: `cam_automation_cli.py`, `cam_core/automation.py`, `cam_core/document_session.py`, `cam_core/project_storage.py`, `cam_core/output_workflow.py`, and existing background/preview services.
- The CLI currently accumulates stdin JSONL before processing it. Do not assume it is already a responsive, persistent request server. Introduce a small process adapter around existing services and verify its lifecycle.
- Native Flutter Design now connects to backend snapshot `f9604dbf02bb8140`; tests and native interaction evidence are in the current status/log. The older browser build remains a visual demo.

## Coexistence with active development

1. Keep conversion-owned code, tests, fixtures, notes, and packaging in the conversion directory, with its existing parent launchers. Do not edit or install over the live Python application, its settings, recoveries, live jobs, or controller destinations. Preserve unrelated changes in the Boat stuff repository.
2. Establish a small allowlisted backend source snapshot with content hashes and source provenance, retaining needed license/third-party notices. Exclude customer files, settings, recoveries, caches, and output directories. Copy from a stable observed source state; compare hashes before/after and defer an inconsistent snapshot. Git HEAD alone is not sufficient because active development may be uncommitted.
3. Build the Flutter adapter against an explicitly identified snapshot, with isolated settings/recovery/output locations. Do not launch the production GUI to serve the prototype or dynamically import changing source into an already-running session.
4. Begin every daily session with upstream synchronization, within the existing one-hour allocation. Compare the live source build marker and content hashes against the last adopted snapshot, including uncommitted edits, added/deleted backend files and relevant Tkinter workflow changes. Review the differences and bring compatible backend changes into an isolated candidate snapshot in small tested batches before expanding affected workflows. Prioritize correctness fixes and previously deferred changes; record new UI features in FEATURE_COVERAGE.md. Recheck source hashes before/after capture so concurrent development cannot produce a mixed snapshot. Never replace a backend used by an open Flutter session or discard unsaved work to adopt an update. If the source is changing, tests fail, or reconciliation exceeds the session budget, retain the working snapshot, record the specific pending files/features and reason, and make that reconciliation the next session's first implementation task. Every session record last checked source identity/time, adopted snapshot, changes incorporated, remaining drift and exact next sync action (or explicitly no changes). Review the full source/feature delta every fifth completed session in addition to these daily checks.
5. Adopt upstream changes in small, verified batches. Never copy a stale conversion snapshot back into production. If shared-source extraction is needed, prepare a scoped proposal or isolated patch and continue independent conversion work; do not overwrite the active builder's files.
6. Record each preview's Flutter build and backend snapshot identity. Keep the previous working evaluation build available before replacing the current build. Use new copies of evaluation jobs until save/reopen compatibility has been verified.

## Architecture direction

Flutter owns windows, menus, panels, canvas rendering, input, selection display, and UI preferences. Python remains authoritative for project data, stable IDs, units, geometry mutations, operation state, persistence, undo/redo, validation, generation, and output.

Start with a local child process and structured messages over stdin/stdout to avoid adding a listening network service. The adapter needs protocol/version negotiation, request IDs, document/revision identity, structured errors, progress, cancellation, stale-result rejection, and clean shutdown/crash handling. Keep logs separate from protocol stdout. Use existing command handlers where they cover the workflow; audit GUI-only behavior before claiming parity.

Expose existing drawing and editing commands directly when their dependencies are understood. Keep exact geometry authoritative in Python, and retain unavailable controls visibly in the matching workspace. Audit machining associations before allowing edits to operation-bearing jobs. Optimize rendering against measured large-file fixtures when needed.

## Milestones and acceptance

| Stage | Scope | Evidence needed before marking it usable |
|---|---|---|
| 0. Baseline and bridge | Source snapshot, command inventory, isolated backend adapter and startup/shutdown | Correct version and request/error handling; no live-state access or unexpected writes |
| 1. Real-job viewer | Open copied CAM jobs/DXF, real geometry, units, plates, layers, pan/zoom, selection/inspection | Small and large fixtures match Python bounds/entities; source file unchanged; hidden/locked/selected behavior checked; native interaction measured |
| 2. Editable documents | Core selection/transform/drawing, clipboard, undo/redo, dirty state, Save As/reopen; then recovery and multiple documents | Roundtrip preserves IDs/operations; cancellation/failure leaves data intact; cross-document state isolation |
| 3. Main manufacturing workflow | Stock/tools, core profile/drill/cleanout/rest operations, background generation, progress/cancel, toolpath preview | Actual existing engine invoked; dependency changes invalidate stale previews; representative motion matches the backend baseline |
| 4. Complete evaluation workflow | Existing preflight, actionable diagnostics, output review and isolated file export | Open → edit → generate → review → save/export works; unchanged-job output comparison and existing validation gates pass; no automatic machine delivery |
| 5. Remaining workspaces | Layers/repair depth, mounting/keyholes/backers, tabs/rails/pans, LEDs, nesting, specialized operations, preferences and shortcuts | Feature inventory reconciled against current Python app; each migrated workflow tested on representative jobs and exposed for user feedback |
| 6. Daily-use refinement | User-reported bugs, remaining gaps, performance, packaging and documentation | User can complete agreed daily workflows; known limitations explicit; switch of production/default launcher requires user's decision |

Stages are functional milestones, not deadlines. Specialized workflows can move earlier when they are needed for the user's actual jobs. They should not delay the first complete common workflow.

## Original session queue — superseded by direct workspace conversion

The original viewer-first queue below is historical context, not the active work order. The user requested direct conversion; several of these items were completed together in the first Design implementation. Follow the concrete next task in CONVERSION_STATUS.md.

1. Confirm source/command inventory, create stable backend snapshot manifest, select synthetic fixtures, and write the minimal bridge contract.
2. Implement persistent backend adapter startup, version handshake, read-only project snapshot, and clean shutdown.
3. Wire the Flutter client to request IDs and errors; replace sample state with one real loaded project.
4. Render real line/circle/polyline geometry with units, bounds, plate framing, and basic layer visibility.
5. Verify real-job open/render in native Windows; check representative larger geometry; reassess remaining bridge/viewer work and publish first evidence-based forecast.
6. Add selection/inspection using stable IDs, including hidden/locked cases.
7. Complete practical plate/layer navigation and essential mouse/keyboard navigation.
8. Introduce the first backend mutation with one undo/redo transaction.
9. Implement isolated Save As/reopen and dirty-state handling; preserve unsupported data.
10. Publish a runnable milestone when ready, reconcile upstream changes, collect a short user trial, and replan the next ten sessions.

Do not infer a completion date from this superseded queue. Reforecast from working workspace coverage and actual user feedback; distinguish basic usability from full specialized-workflow parity.

## Definition of done for a slice

The behavior is real or explicitly marked as a demo/unsupported capability; relevant tests pass; the runnable native build is updated when the UI milestone is ready; a narrow manual check covers the changed interaction when needed; current status, feature coverage, and next step are recorded. Never present a successful compiler run as complete interaction or production verification.

Use focused tests for identity/unit conversions, transport errors, save/undo, worker lifecycle, stale results, geometry fidelity, and output correctness. Reuse the existing backend tests. Run selected native interaction checks and representative fixtures at milestone boundaries. User trials drive usability and edge-case improvements; they supplement basic data/output correctness checks.

## Handoff and reporting

- `CONVERSION_STATUS.md`: current stage, active session, source baseline, latest verified build, exact next task, blockers, and schedule identity.
- `FEATURE_COVERAGE.md`: initial coverage map expanded from the actual command/UI inventory; includes upstream changes and deferred bugs.
- `SESSION_LOG.md`: append one concise entry per session with local date, elapsed time, files/workflows changed, verification, what is ready to try, and next step.
- Report a short daily summary in this chat. Every fifth completed session include a compact milestone/forecast update, without an additional scheduled run.
- If the conversion is broadly functional, report the achieved scope and request the user's choice for the next bug-fix/prioritization phase. Do not change the production launcher or silently expand the daily allocation.


## Manufacturing entry review - October 27 five-task batch

The explicit five-task batch completed independent document tabs, annotation/guide clipboard, finite-line cleaving, a representative existing Design file/output trial, and this Manufacturing readiness review. This does not enable machining in the Flutter UI. The daily schedule and allocation remain unchanged.

Evidence: tools/design_workflow_trial.py opens the existing 16-object Design evaluation drawing, edits and splits it, adds annotations/guides, copies selectable content into an independent document, checks Undo/CAM roundtrip/DXF bounds and annotation exclusion, and prepares a visually checked PDF. One hidden source annotation is intentionally excluded from selectable clipboard content. It also exercises a separate synthetic outside-profile job through the pinned core in both inches and mm: four depth blocks / 60 motions, no preflight errors, progress reporting, cancellation-before-work and fingerprint invalidation after a parameter change. Generation took about 0.012s / 0.004s on this fixture; these are small synchronous core probes, not UI responsiveness or worker cancellation benchmarks. No CNC file or controller delivery is produced.

First functional Manufacturing workflow:

1. Expose stock/job setup, document-owned cutter stations and a basic closed-profile editor. Begin with one end mill and inside/outside profile, depth, clearance, stepdown, feed/RPM, and selection. Use core Tool/ToolLibrary and AutomationSession operation.add_router_offset; keep all changes transactional and undoable. Do not load or edit production tool/settings files. Gate unsupported operation families and grouped second-tool workflows explicitly.
2. Add snapshot-owned background generation and preview using toolpath_background.generate_toolpath_preview_bundle / queued_toolpath_preview_process_worker. Include document identity, revision and output_generation_fingerprint in every request/result; cancel or reject stale results after switching/editing/closing. The bridge currently serves requests serially, so a synchronous generate call would block edits and cancellation. Add a bounded worker poll/cancel protocol before connecting generation buttons. Expose progress, errors and actual generated motions in Flutter.
3. Connect core preflight/project.validate, workflow.analyze and output review. Reuse output_workflow for isolated, explicit file export and atomic publication. Verify equivalent inch/mm geometry, depths, tool identity, feed units, pass order, preview/output consistency and stale-output rejection; do not route to machine/controller destinations. Establish the first open/edit/generate/review/save/export acceptance fixture before forecasting a production switch.
4. Preserve the existing whole-profile-group model: resolve_profile_group_edit and rebuild_profile_group retain source inputs, second-tool/tabbed/hybrid dependencies and plate-local edits. Never enable generic operation edits that break a group. Bring this into a later bounded profile-expansion task, followed by drill/cleanout/rest workflows and saved cutter recipes.

Immediate next bounded implementation: source sync, then stock/cutter setup plus a basic profile-operation draft and operation list in a real Manufacturing workspace. Add an adapter capability for the supported operations, including safe save/reopen/Undo. The current blanket machining-job read-only rule must not simply be removed: allow only verified Manufacturing commands while keeping Design geometry edits on operation-bearing jobs guarded until invalidation/association parity is tested. Start from isolated drawings/copies and test missing cutter, invalid depth, canceled draft, failed apply and tab isolation.

Forecast: the Design evaluation workflow now includes document lifecycle and the queued clipboard/split gaps. Full conversion remains incomplete. The next review horizon is a usable simple-profile workflow through reviewed export, then grouped profiles and drilling/cleanout. A fixed full-switch date remains unsupported until worker integration, preflight/output and representative user jobs are measured. Diameter label placement still needs a compatible core-model decision; large-job benchmarks, specialized plan cloning and mounting/LED authoring remain tracked. This review does not enlarge the daily allocation.


## Manufacturing milestone - October 27, 2026

The explicit next-five request completed the initial stock, cutters, basic profile, background preview and reviewed output sequence. Scoped Manufacturing commands now replace the blanket view-only rule only for default basic closed RouterOffsetOperation settings; associated Design geometry remains guarded. Core preflight/generation is owned by a killable snapshot process. The reviewed output bundle is bound to document revision, complete generation fingerprint and backend snapshot; publication uses the existing atomic core publisher in an isolated folder. Synthetic inch/mm output parity and native mounted workflow checks pass.

Next implement a bounded grouped second-tool strategy with the existing group APIs, atomic preview/apply, retained IDs/dependencies/order and persistence tests, then extend worker/output support only after its complete preflight/motion validation. Other profile parameters, drilling/cleanout and broader output controls follow. Do not treat this synthetic single-plate milestone as full Manufacturing parity or a production-switch forecast. Latest coverage and exact next step live in CONVERSION_STATUS.md.


### October 27 material-bar and extended Manufacturing horizon

The material selection priority and next five tasks are implemented: stock-bar presets/customs; retained-skin second-tool group drafting/editing; circle drilling; concentric island cleanout; actionable preflight; selected/all-plate reviewed evaluation output. Core group identities/dependencies and operation units/history/persistence are retained. The next bounded task is single-tool ramp/helical entry and finishing controls, with narrow field gates and motion/output parity. Subsequent horizon: tabbed/hybrid groups, Speed Cleanout, Guided Production stock verification, remaining specialized workspaces and representative customer-job trials. Full conversion remains incomplete; daily effort, production isolation and switch-over decision rules are unchanged.
