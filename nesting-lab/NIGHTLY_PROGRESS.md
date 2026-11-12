# Nightly nesting development

## September 22, 2026 — refreshed baseline and orientation-order experiment

Lead-owned isolated session. Builder idle at revision40 after completing depth
intent and generation progress. No production files, customer jobs, app settings
or default engine changed. Existing prototype remains experimental.

Commands (bundled Python, `-B`):
- `nesting-lab/verify_nesting.py`: all14 tests passed, including seeded cases.
- `nesting-lab/nightly_baseline.py`:18 fresh-process samples, three per case/engine.
- `nesting-lab/angle_trial.py`:18 independently clearance-checked layouts.

Raw records: `nightly-results-2026-09-22.json` records source SHA256 hashes,
per-sample wall/CPU and Windows peak working set. Startup/imports excluded from
solver wall time; CPU includes post-solver validation; process peak memory includes
Python/imports (about61–62MB), not algorithm-only allocations. Production and
prototype retain their own search policies. Prototype layouts are checked using
the existing segment-clearance checker; this is not independent analytic-curve
qualification or production-engine integration acceptance.

| Synthetic job | Current shape-aware median s | Contact median s | Sheets both | Current/contact envelope |
|---|---:|---:|---:|---:|
|12 L|0.8321|0.03313|1|408.25 /355.9512|
|12 E|1.3876|0.01730|2|533.5625 /543.7556|
|36 E|8.7083|0.12883|4|1600.6875 /1631.2694|

All three repeated runs give identical sheet counts/envelopes per configuration.
The E envelope penalty remains1.91%; speed alone still does not justify replacing
the engine. No new algorithmic speed gain is claimed from these refreshed timings.

Bounded hypothesis: varying the deterministic orientation tie order might improve
packing cheaply. Tested all six permutations of the same allowed0/90/180-degree
set. No envelope or sheet-count improvement on these cases; do not add this
multi-trial overhead to production. Raw results: `angle-trial-2026-09-22.json`.
Combined solver time for all six36-E trials was0.772s, excluding validation.

Next bounded experiment: introduce isolated selectable candidate scoring (area
versus height/width priorities) while always retaining the baseline best result.
Use mixed unique shapes as well as repeated E cases, because sorting identical
parts cannot repair this repeated-part quality gap. Compare complete layouts by
sheet count first, then envelope; retain strict budget and independent clearance
checks. Add deterministic/no-worse-than-baseline acceptance before any adapter
or production integration. Existing curve, obstacle, scope and worker limitations
in README remain unresolved.

## September 23, 2026 — bounded candidate-scoring experiment

Builder snapshot :49 remained notLoaded with last turn completed; no active production work or dispatch. Lead modified only nesting-lab. Introduced an experimental scoring option (area default unchanged, height-first, width-first) without changing feasible-region generation, collision checks, source geometry or output transforms. No production integration or default-engine replacement.

Commands with bundled Python and -B:
- nesting-lab/score_trial.py: 30 fresh-process trials, two repeats of five cases and three policies, independent segment-clearance validation after each solve.
- nesting-lab/verify_nesting.py: 14 existing tests pass.
- nesting-lab/verify_scores.py: five additional tests pass: default parity, invalid policy rejection, all-policy holes/compound/inch-mm/determinism, cancellation/timeout/impossible-fit refusal, and retaining best validated baseline-or-better quality.

Source identity and raw measurements: score-trial-2026-09-23.json records hashes, wall/CPU, peak process memory, validation time, material area, sheet/envelope utilization, candidate/cache counts and repeat layout hashes. contact_nesting.py SHA256 552f03ef3dcda0ece1e864b68eeb42edf3d45526caea18ac61c15a08c284c212. Solver wall/CPU exclude imports and validation; peak process memory includes imports. Cold independent caches per policy, no shared-cache speed claim. All repeats deterministic; all layouts passed clearance, ID and immutable-input checks.

| Case | Baseline / retained envelope | Sheets both | Baseline mean wall s | Three-trial mean solver wall s |
|---|---:|---:|---:|---:|
| L12 | 355.9512 / 355.9512 | 1 | .03485 | .09667 |
| E12 | 543.7556 / 543.7556 | 2 | .01749 | .07088 |
| E36 | 1631.2694 / 1631.2694 | 4 | .12603 | .51441 |
| Mixed 9 plus ring/insert/rigid/curve | 397.7967 / 397.7967 | 1 | .11476 | .34869 |
| Mixed 24 unique concave parts | 734.9727 / 725.9730 | 2 | .39938 | 1.16954 |

Mixed24 width-first reduced envelope 1.2245%; sheet count and material/sheet utilization were unchanged (~32%). Area/height/width mean CPU ~.41/.39/.38 seconds; process peaks 60.98/61.30/61.81 MiB. Other case process peaks 57.7–58.8 MiB. Width-first alone badly regresses E36 envelope to1781.03 and doubles its solve cost; height-first regresses L12 and Mixed24. Therefore no global policy switch is justified. The retained-best three-policy trial costs about2.93x baseline on Mixed24 and ~4.08x on E36, with no E36 quality improvement. Prior production E36 envelope1600.6875 remains better than this prototype's1631.2694; old quality gap is not solved. No new production speed gain or material savings claim.

Budget/failure limits: each solver has an8s cooperative budget and each fresh process a25s timeout. This script is a finite offline experiment, not an integrated portfolio service or UI cancellation solution. A failed/invalid trial is excluded; a failed baseline cannot establish baseline-preservation acceptance. Existing native clipping interruption, analytic curve/quantization, obstacles, scope, plate-origin/margins and adapter/Undo/reopen/stale-document limitations remain. Polygonized curve tests do not certify analytic curve clearance. No customer art, live settings/recovery, machine state or production source changed.

Next bounded experiment: deterministic part-order trials on mixed unique cases, keeping area-order baseline and independent validation. Expose an isolated order hook with strict finite trial count; compare input order, material-area and bounded dimension-based ordering, retaining sheet count then envelope best. Repeated identical E parts will not benefit from order-only trials, so do not claim that addresses their gap. Do not add expensive scoring portfolio to production on this evidence. Shared-cache budgeting and production adapter remain later work. Session ended after completing the finite hypothesis and checks, without idle padding.


## September 24, 23:00 Central — deterministic part-order experiment

Builder :69 idle/completed; no production dispatch. Added optional area/input/longest/box_area ordering to lead-owned ContactNester only; default material-area order and all geometry/collision/scoring behavior preserved. Longest and box-area use the first permitted orientation, with area and stable ID tie breaks. Finite experiment, not production integration.

Commands: bundled Python -B nesting-lab/order_trial.py; bundled Python -B -m unittest discover -s nesting-lab -p "verify_*.py". 24 tests pass (14 existing geometry + 5 scoring + 5 order tests), including source immutability, explicit input order, default parity, deterministic holes/rigid/curve and inch/mm layouts, cancellation/timeout/impossible fits and retained-baseline quality.

Four orderings × two fresh-process repeats × four cases = 32 final measured/independently validated layouts. An initial 24-sample run was expanded to include Mixed48; final JSON replaces that preliminary run. Inputs seed7441, 24×24 sheet, .25 spacing, rotations0/90/180. Solver timeout8s per call; process timeout25s per trial. Cold per-trial caches. Wall/CPU exclude imports and validation; independent validation wall and process peak memory recorded separately. No live UI integration.

| Case | Baseline → best envelope | Sheets | Winning order | Baseline / four-trial wall s | Baseline / four-trial CPU s | Peak process MiB |
|---|---:|---:|---|---:|---:|---:|
| E36 | 1631.2694 → 1631.2694 | 4 | area | 0.1287 / 0.5106 | 0.1328 / 0.5156 | 59.19 |
| mixed9 | 397.7967 → 364.6234 | 1 | box_area | 0.1189 / 0.4235 | 0.1172 / 0.4297 | 59.23 |
| mixed24 | 734.9727 → 734.9727 | 2 | area | 0.3969 / 1.6466 | 0.3906 / 1.6406 | 61.65 |
| mixed48 | 1588.9829 → 1562.6924 | 3 | longest | 1.4605 / 6.3144 | 1.4453 / 6.2891 | 62.91 |

Mixed9 actually includes 13 parts (nine concave plus ring, insert, rigid compound and polygonized curve): box-area order improves envelope8.34%. Mixed48 longest-first improves envelope1.65%. Sheet counts remain1 and3 respectively, so these are envelope improvements, NOT sheet/material savings. E36 remains4sheets/envelope1631.2694 and the prior ~1.9% gap to production is unresolved. Mixed24 alternatives all worsen envelope; baseline retention is essential. All four trials cost roughly3.6–4.3 times baseline solver time. No universal order switch or speed claim justified.

Source/commands/raw timings/CPU/memory/utilization/candidate counts/layout hashes: order-trial-2026-09-24.json. ContactNester SHA256 213439aef799437862734c223e50e6b117b2a35f558faa28d60b4e45aa20fb20. Post-run manifest-only source adjustment is disclosed in JSON; measured solver unchanged. Default remains area. Input geometry/IDs/allowed rotations unchanged; no customer fixtures or production source edits.

Limits unchanged: synthetic polygon clearance, unqualified analytic curve/chord/quantization margins, unsupported fixed obstacles/grain/mixed sheets/scope/plate-origin adapter, no UI undo/reopen/cancellation/stale-document integration. Per-call cooperative budget does not bound native clip interruption; fresh process timeout bounds experiments. No integration readiness or machining claim.

Next bounded experiment: reuse a bounded forbidden-region cache across only area and box-area/longest trials to measure redundant-work reduction while retaining independently validated baseline and winner. First compare cold versus shared-cache identical layouts and peak memory, cancellation/time limits; avoid an unbounded scoring×ordering search. Use Mixed13/24/48, keep E36 as no-quality-gain control. Production adapter remains later. Session ends after finite experiment and documentation; no idle padding.
