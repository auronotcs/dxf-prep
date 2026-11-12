# Contour-contact nesting prototype

Developed September 10, 2026, separately from the production application. Production nesting files were not edited. The user approved the 19 Design icons for separate builder integration; canvas line changes remain discussion-only.

## Algorithm

`contact_nesting.py` implements a deterministic polygon placement engine, not a bounding-box packer or sheet-position raster scan. Decompose filled contours into convex horizontal trapezoids using even/odd fill, so holes and disconnected rigid islands remain represented. Compute the convex Minkowski sums of the stationary cells and reflected moving cells, union them into forbidden translations, and inflate for spacing. Subtract these regions from the sheet's inner-fit rectangle. Rank vertices of the remaining feasible regions by used envelope area, height, width and allowed orientation. Place larger material-area parts first. Cache pair regions by normalized geometry/orientation/spacing; cache is bounded.

The implementation uses the app's existing vendored pyclipper through dependency injection. It does not require Shapely, a GPU or a new installed library. Clipper's operation reference: https://angusj.com/clipper2/Docs/Units/Clipper/Functions/MinkowskiSum.htm . The prototype constructs convex sums directly; it does not assume a closed boundary sweep alone represents a solid polygon with holes.

## Validation and initial measurements

Run with bundled Python: `python -B nesting-lab/verify_nesting.py`. All 14 tests passed, including 12 seeded mixed-shape layouts inside one test, independently checked using the existing segment-intersection/clearance checker. Checks cover repeated concave shapes, an insert inside a ring, disconnected compound material, rotation, zero/positive spacing, unit conversion, reproducibility, polygonized curves, invalid input, impossible fits, cancellation and a complexity guard. These are synthetic tests, not approval of production machine output.

Single-run timings exclude Python/import startup. `run_benchmarks.py` runs each engine in a separate process with a 20-second ceiling; the prototype has its own cooperative budget. Same synthetic geometry, 24x24 sheets, .25 spacing, rotation allowance through 180 degrees. Existing engines retain their own orientation/search policy; this is an end-to-end comparison, not an identical-candidate microbenchmark.

| Case | Current shape-aware | Prototype | Sheets, both | Envelope area current / prototype |
|---|---:|---:|---:|---:|
| 12 L shapes | .842 s | .032 s | 1 | 408.25 / 355.95 |
| 12 E shapes | 1.394 s | .017 s | 2 | 533.56 / 543.76 |
| 36 E shapes | 9.221 s | .124 s | 4 | 1600.69 / 1631.27 |

Smaller envelope is better; these are summed used rectangular envelopes, not material area or a claim of globally optimal utilization. The E cases are about 1.9% worse in envelope area despite much lower runtime. `benchmark-results.json` also records the fast rectangular and smart-pairs engines. Repeated runs and real-job comparisons are required before setting a default. Timings were collected before adding input-validation guards; final test suite was rerun afterward, so rebenchmark final code for integration performance claims.

## Integration boundary and remaining work

- Input is immutable `Part(key, paths)` in project units; every path is a closed simple polygon. Group all outer/counter/member geometry belonging to one rigid part before calling. Intersecting contours within a part follow even/odd material semantics; do not pass unrelated overlapping parts as one part. Output contains sheet index, allowed angle, translated normalized bounds origin and material paths. Keep production source entities intact: transform originals by the returned rotation and normalized-origin translation, preserving IDs. Output polygon paths are collision geometry, not replacement machining artwork.
- Explicitly tessellate circles/arcs with a documented chord error and add its conservative clearance allowance. The prototype tests polygonized curves; it does not yet certify true analytic-curve clearance.
- Quantization uses 100,000 integer units per project unit. Round offset uses a conservative tolerance pad of six integer units. Physical tolerance policy, coordinate range validation and conservative decomposition rounding require production review. Very tight exact-contact arrangements can be rejected conservatively. No common-line nesting.
- Current API supports identical rectangular sheets and allowed rotations, holes and disconnected rigid groups. It does not yet support fixed obstacles, selected-only scopes, mixed sheet sizes, plate margins separate from interpart spacing, grain metadata, defect regions or partial-result recovery. Preserve existing behavior or explicitly disable unsupported controls in a future experimental adapter; never silently ignore them.
- It is a greedy contact-vertex heuristic, not an exact optimum or an exhaustive contact arrangement solver. It can miss better placements/orderings. Next quality increment should use a few deterministic order/score trials with shared caches and a strict budget, retaining the best validated result. Compare against current layout rather than blindly replacing it.
- Cancellation/time checks occur between computations. A native clipping call cannot be interrupted midway. Run in a separate process with an external timeout/cancel mechanism; no CPU-heavy work or polling loop on the UI thread. Return a document-revision token and validate before applying. Prototype guard exceptions return no partial layout.
- Before integration: add the scope/transform adapter, test retained identities/units/mounting/LED/CAM dependencies, validate physical polygon and analytic-curve clearance independently, benchmark realistic unique curved letters and varied rotation increments, and check worker cancellation/stale results. Do not replace the default engine based on these small synthetic results.

## Canvas line discussion

Current `_draw_entity` uses width 2, selected 3; `_draw_toolpaths` uses width 2, selected 4. `line-weight-study.png` illustrates ordinary/selected widths 1/2 versus current 2/3. This is an SVG-rendered illustration, not a screenshot of Tk's rasterization. No production line changes made. User should choose direction before implementation; later verify true Tk rendering at display scaling, selected toolpath visibility, coincident geometry and crisp thin strokes. Selection hit tolerance should remain independent of stroke width; plot/export widths must not change.

September24 nightly extension: optional deterministic input/longest/box-area part ordering remains lab-only; default area unchanged. See order_trial.py, verify_orders.py and NIGHTLY_PROGRESS.md. Synthetic Mixed13/Mixed48 envelope gains cost additional trials and do not reduce sheet count; no production integration or default change.
