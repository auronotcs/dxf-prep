"""Reviewed partial tab cleanup. Geometry connectivity is not fixture qualification.

This deliberately bounded first planner does not release parts. It keeps at least
X original bridges PER PART, preserves every profile motion, and only filters and
orders the existing end-of-operation cleanup spans. Ambiguous/unsupported scope
fails closed rather than treating loose waste as anchored stock.
"""
from dataclasses import replace
import hashlib
import json
import math

from ._vendor import pyclipper as pc
from .model import RouterOffsetOperation, effective_tool
from .plates import Plate, make_plate_project

VERSION = 1
SCALE = 1000000


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _canonical(value):
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def local_scope(project, operation, plate_data):
    """Same canonical frame in editor and intentional plate-local output."""
    plate = Plate.from_dict(plate_data)
    source = project.metadata.get("source_plate")
    if source:
        if _canonical(source) != _canonical(plate_data):
            raise ValueError("Tab cleanup plan belongs to a different source plate; re-plan it.")
        plate = Plate(plate.number, 0, 0, plate.width, plate.height, plate.name)
        declared = project.metadata.get("plates", [])
        if declared and (len(declared) != 1 or Plate.from_dict(declared[0]).bounds != plate.bounds):
            raise ValueError("Local plate size/origin changed; re-plan against the correct source plate.")
    else:
        declared = project.metadata.get("plates", [])
        layout = project.metadata.get("sheet_layout")
        if declared:
            current = next((Plate.from_dict(p) for p in declared if p.get("number") == plate.number), None)
        elif layout:
            from .layout import SheetLayout, sheet_rectangles
            boxes = sheet_rectangles(SheetLayout(layout.get("label", ""), layout["width"], layout["height"],
                layout["columns"], layout["rows"], layout.get("origin_x",0), layout.get("origin_y",0)))
            current = (Plate(plate.number, *boxes[plate.number-1][:2],
                       boxes[plate.number-1][2]-boxes[plate.number-1][0],
                       boxes[plate.number-1][3]-boxes[plate.number-1][1]) if plate.number <= len(boxes) else None)
        else:
            current = plate
        if current is None or current.bounds != plate.bounds:
            raise ValueError("Plate size/origin changed; re-plan tab cleanup for the current plate.")
    scoped = make_plate_project(project, plate)
    local, crossing = scoped.project, scoped.crossing_entity_ids
    found = next((o for o in local.operations if o.id == operation.id), None)
    if found is None or found.entity_ids != operation.entity_ids:
        raise ValueError("Tab cleanup planning requires the entire operation on one plate.")
    enabled = [o for o in local.operations if o.enabled]
    if len(enabled) != 1 or enabled[0].id != operation.id:
        raise ValueError("This first tab-cleanup planner requires one enabled operation on the plate; other machining and release dependencies need manual review.")
    if crossing:
        raise ValueError("Geometry crosses this plate boundary; resolve plate ownership before planning tab cleanup.")
    if any(k.enabled for k in local.keepouts):
        raise ValueError("Tab-cleanup planning with fixture keepouts is not yet supported; use reviewed manual cleanup.")
    if project.metadata.get("patchwork") or project.metadata.get("patchwork_areas"):
        raise ValueError("Tab-cleanup support checking requires a full rectangular sheet, not patchwork stock.")
    return local, found, plate


def fingerprint(project, operation, plate_data):
    local, op, plate = local_scope(project, operation, plate_data)
    payload = local.to_dict()
    for item in payload["operations"]:
        if item["id"] == op.id:
            dependencies=item["depends_on_ids"]
            item.update(operation.to_dict())
            item["depends_on_ids"]=dependencies
            item["recipe_metadata"].pop("_computed_aligned_tab_bands",None)
        item.pop("tab_release_plan", None)
        if item["id"] == op.id:
            item["remove_tabs_after"] = True
            item["tab_cleanup_order"] = "end_of_operation"
    # Document name/view/selection are not machining inputs. Include ALL local
    # stock/material metadata except the deliberately normalized plate frame.
    metadata = dict(payload.get("metadata", {}))
    for key in ("source_plate", "plates", "sheet_layout", "active_plate_number", "active_layer", "cycle_optimization"):
        metadata.pop(key, None)
    if isinstance(metadata.get("job_setup"), dict):
        from .output_workflow import _OUTPUT_ONLY_JOB_SETUP_KEYS
        metadata["job_setup"] = {k:v for k,v in metadata["job_setup"].items() if k not in _OUTPUT_ONLY_JOB_SETUP_KEYS}
    return _hash(_canonical({"v": VERSION, "units": local.units,
        **({"tab_height_reference": "stock_bottom_v1"} if op.cut_intent == "through" and op.breakthrough else {}),
        "entities": payload["entities"], "operations": payload["operations"],
        "tools": payload["tool_library"], "metadata": metadata,
        "keepouts": payload.get("keepouts", []),
        "plate": [plate.width, plate.height, plate_data]}))


def cleanup_spans(blocks, origin=(0, 0)):
    """Keep exact generated entry/direction/arc motions, not re-derived intervals."""
    from .toolpath import MotionKind
    result = []
    for block in blocks:
        if block.metadata.get("role") != "tab_cleanup":
            continue
        active = []
        for motion in block.motions:
            active.append(motion)
            if len(active) > 1 and motion.kind == MotionKind.RAPID:
                owner = block.metadata["entity_id"]
                shape = [[m.kind.value, m.end.x-origin[0], m.end.y-origin[1], m.end.z,
                          None if m.center is None else [m.center.x-origin[0], m.center.y-origin[1]]]
                         for m in active]
                key = owner + ":" + _hash(_canonical(shape))[:20]
                result.append({"id": key, "owner": owner, "block": block,
                               "motions": active, "index": len(result)})
                active = []
        if active:
            raise ValueError("Incomplete tab cleanup span; regenerate the operation.")
    if len({s["id"] for s in result}) != len(result):
        raise ValueError("Duplicate tab span ownership is ambiguous; use manual cleanup.")
    return result


def _clip(subject, other=(), kind=pc.CT_DIFFERENCE, tree=False):
    if not subject:
        return []
    c = pc.Pyclipper()
    c.AddPaths(subject, pc.PT_SUBJECT, True)
    if other:
        c.AddPaths(other, pc.PT_CLIP, True)
    return (c.Execute2 if tree else c.Execute)(kind, pc.PFT_NONZERO, pc.PFT_NONZERO)


def _area(paths):
    return abs(sum(pc.Area(p) for p in paths)) / SCALE**2


def _sweep(blocks, radius, plane, tolerance):
    """Conservative cutter projection below retained-web top, arcs included.

    Chords inflate by tolerance; vertical plunges contribute their disk. Project
    the entire crossing segment, intentionally conservative about ramp entries.
    """
    from .toolpath import MotionKind
    offset = pc.PyclipperOffset(2, tolerance*SCALE/4)
    paths = 0
    for motions in blocks:
        for a, motion in zip(motions, motions[1:]):
            a, b = a.end, motion.end
            if motion.kind == MotionKind.RAPID:
                if min(a.z, b.z) < plane-tolerance*1e-6 and math.hypot(b.x-a.x,b.y-a.y) > tolerance:
                    raise ValueError("A below-web rapid needs manual support review.")
                continue
            if min(a.z, b.z) >= plane-tolerance*1e-6 or motion.kind == MotionKind.DWELL:
                continue
            points = [(a.x,a.y), (b.x,b.y)]
            if motion.center is not None:
                cx,cy = motion.center.x,motion.center.y
                r = math.hypot(a.x-cx,a.y-cy)
                start,end = math.atan2(a.y-cy,a.x-cx),math.atan2(b.y-cy,b.x-cx)
                sweep = (end-start) % (2*math.pi)
                if motion.kind == MotionKind.ARC_CW:
                    sweep = -((start-end) % (2*math.pi))
                if abs(sweep) < 1e-10:
                    sweep = -2*math.pi if motion.kind == MotionKind.ARC_CW else 2*math.pi
                step = 2*math.acos(max(-1,min(1,1-tolerance/max(r,tolerance))))
                count = max(1,math.ceil(abs(sweep)/max(step,1e-5)))
                points = [(cx+r*math.cos(start+sweep*i/count),cy+r*math.sin(start+sweep*i/count)) for i in range(count+1)]
            path = list(dict.fromkeys((round(x*SCALE),round(y*SCALE)) for x,y in points))
            if len(path) == 1:
                # Clipper open paths need two points; sub-tolerance extension.
                path.append((path[0][0]+1,path[0][1]))
            offset.AddPath(path, pc.JT_ROUND, pc.ET_OPENROUND)
            paths += 1
    return offset.Execute((radius+tolerance)*SCALE) if paths else []


class CleanupReview:
    """An isolated, source-fingerprinted proposal; does not modify the job."""
    def __init__(self, project, operation, plate):
        from .toolpath import generate_closed_router_offset, _closed_entity_points, _region_clipper_path
        from .units import unit_scale
        if not isinstance(operation, RouterOffsetOperation) or not operation.enabled:
            raise ValueError("Choose an enabled closed outside-profile operation.")
        self.plate_data = plate.to_dict()
        self.fingerprint = fingerprint(project, operation, self.plate_data)
        self.project, self.operation, self.plate = local_scope(project, operation, self.plate_data)
        op = replace(self.operation, tab_release_plan={}, remove_tabs_after=True,
                     tab_cleanup_order="end_of_operation")
        self.locked = op.sequence_locked or bool(op.manual_contour_order)
        self.tolerance = .001 * unit_scale("in", project.units)
        tool = effective_tool(self.project, op)
        if tool is None or tool.diameter <= 0:
            raise ValueError("Tab cleanup needs a valid cutter diameter.")
        self.radius = tool.diameter/2
        # Use the source's stored thickness, never its filename or recipe label.
        stock = self.project.metadata.get("job_setup", {})
        thickness = stock.get("material_thickness")
        if not isinstance(thickness, (float, int)) or thickness <= 0:
            raise ValueError("Set a positive stock thickness before reviewing retained tabs.")
        if not (0 < op.tab_floor_depth < thickness <= op.target_depth+self.tolerance):
            raise ValueError("Tab cleanup needs a through cut with positive remaining tab material; review stored stock thickness, depth and tab height.")
        generated = generate_closed_router_offset(self.project, op)
        self.spans = cleanup_spans(generated.blocks)
        if not self.spans:
            raise ValueError("This operation has no eligible tab-cleanup spans.")
        if len(self.spans) > 500:
            raise ValueError("Review at most 500 cleanup spans in one operation.")
        if any(s["block"].metadata.get("side") != "outside" for s in self.spans):
            raise ValueError("Retained-X planning currently supports outside parts, not tabbed inside slugs.")
        self.by_id = {s["id"]: s for s in self.spans}
        self.owners = list(dict.fromkeys(s["owner"] for s in self.spans))
        outside_owners = {b.metadata.get("entity_id") for b in generated.blocks
                          if b.metadata.get("side") == "outside"}
        if outside_owners - set(self.owners):
            raise ValueError("An outside part has no cleanup-eligible tabs (including perimeter-threshold exclusions). Review it separately; this planner cannot assume an already released part remains still.")
        plane = -(op.tab_floor_depth)
        self.stock = [[(0,0),(round(plate.width*SCALE),0),
                       (round(plate.width*SCALE),round(plate.height*SCALE)),(0,round(plate.height*SCALE))]]
        profiles = [b.motions for b in generated.blocks if b.metadata.get("role") != "tab_cleanup"]
        self.profile_sweep = _sweep(profiles, self.radius, plane, self.tolerance)
        self.base = _clip(self.stock, self.profile_sweep)
        self.parts = {}
        for owner in self.owners:
            entity = next(e for e in self.project.entities if e.id == owner)
            self.parts[owner] = [_region_clipper_path(_closed_entity_points(entity))]
        inside_ids = {b.metadata.get("entity_id") for b in generated.blocks
                      if b.metadata.get("side") == "inside"}
        holes = [_region_clipper_path(_closed_entity_points(e)) for e in self.project.entities if e.id in inside_ids]
        for owner in self.owners:
            self.parts[owner] = _clip(self.parts[owner],holes)
            if _area(_clip(self.base,self.parts[owner],pc.CT_INTERSECTION)) <= self.tolerance**2*10:
                raise ValueError(f"Part {owner} has no surviving body material; inspect the original profiles.")
        self.unavailable = []
        for span in self.spans:
            span["sweep"] = _sweep([span["motions"]], self.radius, plane, self.tolerance)
            span["bridge"] = _clip(span["sweep"], self.profile_sweep)
            span["bridge"] = _clip(span["bridge"], self.stock, pc.CT_INTERSECTION)
            if _area(span["bridge"]) <= self.tolerance**2 * 10:
                self.unavailable.append(span["id"])
            p,q = span["motions"][1].end, span["motions"][-2].end
            span["mid"] = ((p.x+q.x)/2,(p.y+q.y)/2)
        if self.unavailable:
            raise ValueError(f"{len(self.unavailable)} original tab span(s) are already cut away by neighboring profiles. Align/review original tabs before staged removal.")
        self._assert_connected(_clip(self.base, kind=pc.CT_UNION, tree=True))
        # Removing one physical bridge may cut another contour's tab too.
        self.coupled = {s["id"]:{s["id"]} for s in self.spans}
        for i,a in enumerate(self.spans):
            for b in self.spans[i+1:]:
                if (_area(_clip(a["sweep"],b["bridge"],pc.CT_INTERSECTION)) > self.tolerance**2*10
                    or _area(_clip(b["sweep"],a["bridge"],pc.CT_INTERSECTION)) > self.tolerance**2*10):
                    self.coupled[a["id"]].add(b["id"])
                    self.coupled[b["id"]].add(a["id"])
        self.groups = []
        seen = set()
        for span in self.spans:
            if span["id"] not in seen:
                group=self.group(span["id"])
                self.groups.append(group);seen.update(group)

    def group(self, key):
        result, todo = set(), [key]
        while todo:
            item = todo.pop()
            if item not in result:
                result.add(item)
                todo.extend(self.coupled[item]-result)
        return result

    def _assert_connected(self, tree):
        w,h = round(self.plate.width*SCALE),round(self.plate.height*SCALE)
        components = []
        def visit(node):
            if node.Contour and not node.IsHole:
                rings = [node.Contour]+[c.Contour for c in node.Childs if c.IsHole]
                anchored = any(x <= 1 or y <= 1 or x >= w-1 or y >= h-1 for x,y in node.Contour)
                components.append((rings,anchored))
            for child in node.Childs:
                visit(child)
        visit(tree)
        if not components:
            raise ValueError("No connected stock remains after profiling.")
        # Only detached components can violate connectivity. Intersecting the
        # giant anchored stock polygon once PER PART dominated complex jobs.
        for rings,anchor in components:
            if anchor:
                continue
            for owner, polygon in self.parts.items():
                if _area(_clip(rings,polygon,pc.CT_INTERSECTION)) > self.tolerance**2*10:
                    raise ValueError(f"Part {owner}: remaining material is disconnected from the sheet perimeter; isolated waste is not support.")

    @staticmethod
    def count_value(value):
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError("Retained tabs PER PART must be a nonnegative integer.")
        value = int(value)
        if value == 0:
            raise ValueError("0 means full release. This planner retains support; cancel and open Toolpath > Manage Operations > Final Tab Release Review for the separate reviewed option. Loose-part motion and hold-down require explicit review; existing manual cleanup remains available.")
        return value

    def validate(self, remove, count):
        count = self.count_value(count)
        if len(remove) != len(set(remove)) or not set(remove) <= self.by_id.keys():
            raise ValueError("Unknown or duplicate cleanup span; re-plan.")
        removed = set(remove)
        for key in removed:
            if not self.group(key) <= removed:
                raise ValueError("Shared physical bridge must be retained/removed together on all owning parts.")
        # A seam can split one bridge into multiple cleanup spans. Count physical
        # coupled groups once per owner, never promise two supports from one tab.
        totals = {o:sum(any(self.by_id[k]["owner"] == o for k in g) for g in self.groups) for o in self.owners}
        retained = {o:sum(not g & removed and any(self.by_id[k]["owner"] == o for k in g)
                         for g in self.groups) for o in self.owners}
        for owner in self.owners:
            if retained[owner] < min(count,totals[owner]):
                raise ValueError(f"Part {owner} would retain fewer than {min(count,totals[owner])} tabs.")
        if self.locked:
            indexes = [self.by_id[k]["index"] for k in remove]
            if indexes != sorted(indexes):
                raise ValueError("The existing manual/locked contour order must be preserved.")
        sweeps = [p for key in remove for p in self.by_id[key]["sweep"]]
        self._assert_connected(_clip(self.base,sweeps,tree=True))
        # Final connectivity implies connectivity at every earlier stage: this
        # process only subtracts material and never changes original profiles.
        return totals,retained

    def propose(self, count):
        count = self.count_value(count)
        remove = []
        # Prefer keeping opposed left/right support in regular arrays. This is
        # a tie-break, NEVER a substitute for actual connectivity validation.
        def rank(s):
            p,q=s["motions"][1].end,s["motions"][-2].end
            return (abs(q.y-p.y)>abs(q.x-p.x),s["mid"][1],s["mid"][0],s["id"])
        for span in sorted(self.spans,key=rank):
            candidate = set(remove)|self.group(span["id"])
            ordered = [s["id"] for s in self.spans if s["id"] in candidate]
            try:
                self.validate(ordered,count)
            except ValueError:
                continue
            remove = ordered
        # Deterministic greedy travel order only while all parts remain held;
        # locked/manual ordering is literal. No free-part traversal assumption.
        if not self.locked:
            pending=set(remove); remove=[]; previous=(0.,0.)
            while pending:
                key=min(pending,key=lambda k:(math.dist(previous,self.by_id[k]["mid"]),k))
                remove.append(key); pending.remove(key); previous=self.by_id[key]["mid"]
        self.validate(remove,count)
        return remove

    def plan(self, remove, count):
        totals,retained=self.validate(remove,count)
        return {"version":VERSION,"operation_id":self.operation.id,
                "plate":self.plate_data,"fingerprint":self.fingerprint,
                "retain_per_part":self.count_value(count),"remove":list(remove),
                "totals":totals,"retained":retained}


def apply_tab_release_plan(project, operation, blocks):
    """Posting guard: source, ownership, exact spans AND geometry are rechecked."""
    plan = operation.tab_release_plan
    if not plan:
        return blocks
    if plan.get("mode") == "automatic_opposed":
        from .automatic_tab_cleanup import apply_automatic_cleanup
        return apply_automatic_cleanup(project, operation, blocks)
    if (not isinstance(plan,dict) or plan.get("version") != VERSION or plan.get("operation_id") != operation.id
        or not isinstance(plan.get("plate"),dict) or not isinstance(plan.get("remove"),list)):
        raise ValueError("Invalid tab-cleanup plan; open Plan Tab Cleanup and re-plan.")
    if not operation.remove_tabs_after or operation.tab_cleanup_order != "end_of_operation":
        raise ValueError("A reviewed tab-cleanup plan requires End of operation removal; re-plan or clear the plan.")
    if fingerprint(project,operation,plan["plate"]) != plan.get("fingerprint"):
        raise ValueError("Tab-cleanup plan is stale after a geometry, stock, tool or operation change. Re-plan before output.")
    review=CleanupReview(project,replace(operation,tab_release_plan={}),Plate.from_dict(plan["plate"]))
    review.validate(plan.get("remove",[]),plan.get("retain_per_part"))
    origin=(0,0) if project.metadata.get("source_plate") else (review.plate_data["x"],review.plate_data["y"])
    actual={s["id"]:s for s in cleanup_spans(blocks,origin)}
    if set(actual) != set(review.by_id):
        raise ValueError("Generated cleanup spans changed; re-plan before output.")
    result=[b for b in blocks if b.metadata.get("role") != "tab_cleanup"]
    chosen=[actual[key] for key in plan["remove"]]
    if chosen:
        # One cleanup stage with the original Safe-Z retract between EVERY span.
        # Do not insert an unnecessary M22/M12 router disengage/engage per tab.
        metadata=dict(chosen[0]["block"].metadata)
        for key in ("entity_id","path_index","contour_order_index","containment_depth"):
            metadata.pop(key,None)
        metadata.update(entity_ids=list(dict.fromkeys(s["owner"] for s in chosen)),
                        tab_release_span_ids=list(plan["remove"]),
                        tab_release_owner_ids=[s["owner"] for s in chosen],
                        retain_per_part=plan["retain_per_part"])
        result.append(replace(chosen[0]["block"],motions=[m for s in chosen for m in s["motions"]],
                              label="Reviewed staged tab cleanup (parts remain attached)",metadata=metadata))
    return result


def check_prepared_tab_cleanup(project, operation, generated):
    """Prepared-output cache may not bypass source or reviewed-choice guards."""
    plan=operation.tab_release_plan
    if isinstance(plan, dict) and plan.get("mode") == "automatic_opposed":
        from .automatic_tab_cleanup import check_prepared
        return check_prepared(project, operation, generated)
    if not isinstance(plan,dict) or plan.get("version") != VERSION or plan.get("operation_id") != operation.id:
        raise ValueError("Invalid prepared tab-cleanup plan; regenerate Output.")
    CleanupReview.count_value(plan.get("retain_per_part"))
    if (not operation.remove_tabs_after or operation.tab_cleanup_order != "end_of_operation"
        or fingerprint(project,operation,plan["plate"]) != plan.get("fingerprint")):
        raise ValueError("Prepared tab cleanup is stale; re-plan and regenerate Output.")
    cleanup=[b for b in generated.blocks if b.metadata.get("role") == "tab_cleanup"]
    if cleanup and generated.blocks[-len(cleanup):] != cleanup:
        raise ValueError("Prepared tab cleanup is not after every profile; regenerate Output.")
    if ([key for b in cleanup for key in b.metadata.get("tab_release_span_ids", [])] != plan.get("remove")
        or any(b.metadata.get("retain_per_part") != plan["retain_per_part"] for b in cleanup)):
        raise ValueError("Prepared cleanup does not match the reviewed tab choices/order; regenerate Output.")
