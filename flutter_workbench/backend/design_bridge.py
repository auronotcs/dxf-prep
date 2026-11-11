"""Local persistent Design adapter. stdout is JSONL; no machine-output commands."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = Path(os.environ.get('WORKBENCH_BACKEND_MANIFEST', ROOT / 'source_manifest.json')).resolve()
if not MANIFEST_PATH.is_relative_to(ROOT):
    raise ValueError('Backend manifest must be inside the isolated Flutter backend.')
SOURCE_MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
CORE_ROOT = (ROOT / SOURCE_MANIFEST.get('snapshot_directory', 'core_snapshot')).resolve()
if not CORE_ROOT.is_relative_to(ROOT) or not (CORE_ROOT / 'cam_core').is_dir():
    raise ValueError('Invalid isolated backend snapshot directory.')
sys.path.insert(0, str(CORE_ROOT))
from cam_core.automation import AutomationSession, _automation_layer_settings, _project_plates
from cam_core.model import CamProject, Point, Line, Circle, Rectangle, Polyline, TextEntity, new_id
from cam_core.layout import entity_bounds, drawing_bounds, entity_perimeter
from cam_core.transform import transform_entity, scale_entity, merge_lines, explode_contour
from cam_core.arrangement import ArrangementItem, plan_arrangement, ALIGN_ACTIONS
from cam_core.shape_construction import boolean_shapes
from cam_core.shape_macros import ShapeMacroSpec, make_shape_macro
from cam_core.shape_conversion import propose_shape_conversion, apply_shape_conversion
from cam_core.toolpath import offset_closed_geometry
from cam_core.contour_repair import propose_contour_repair, propose_near_point_repair, apply_contour_repair
from cam_core.appearance import ObjectAppearance, ColorValue, appearance_from_layer_color
from palette_library import PaletteLibrary
from shop_pdf import ShopPDF
from arrangement_support import ArrangementSupport
import dimension_support as dimensions
import guide_support as guides
from cutoff_support import CutoffDraft
from repeat_support import RepeatDraft
from pan_authoring import PanDraft
from cabinet_authoring import CabinetDraft, editable as cabinet_editable
from repair_suite_support import RepairSuiteDraft
import clipboard_support as clipboard
import cleave_support
import manufacturing_support as manufacturing
from manufacturing_recipes import ManufacturingRecipes
from knife_presets import KnifePresets
from inspection_support import selection_inspection
from recovery_support import RecoveryStore
from cam_core.small_geometry import DEFAULT_SPECK_IN, find_small_geometry, affected_operations, delete_small_geometry
from cam_core.endpoint_repair import opening_repair_diagnostics
from cam_core.plates import (Plate, PatchworkArea, validate_plate_definitions, validate_patchwork_areas,
    create_repeated_patchwork_areas, patchwork_nesting_plates, place_patchwork_area, arrange_patchwork_areas)
from cam_core.nesting import nest_entities_to_plates
from cam_core.push_through_fit import (PushThroughFitSettings, propose_push_through_fit,
    corrected_metal_entities, push_through_candidate_entities, apply_push_through_fit)

ALLOWED = {
    'project.new', 'project.open', 'project.save', 'project.export_dxf',
    'selection.set', 'selection.clear', 'selection.circles', 'selection.filter',
    'layer.set', 'layer.move_selection', 'plate.define', 'plate.add', 'plate.update',
    'plate.set_active', 'job.setup', 'history.undo', 'history.redo', 'history.goto',
    'geometry.add_line', 'geometry.add_rectangle', 'geometry.add_circle',
    'geometry.add_polyline', 'geometry.add_text', 'geometry.update_text',
    'geometry.convert_text', 'geometry.transform', 'geometry.mirror', 'geometry.delete',
    'geometry.add_cutoff_line', 'geometry.auto_nest',
}
CUSTOM = {'design.repeat_preview','design.repeat_apply','design.scale', 'design.arrange', 'design.boolean', 'design.duplicate',
          'design.paste', 'design.cut', 'design.group', 'design.ungroup', 'design.compound',
          'design.uncompound', 'design.merge', 'design.explode', 'design.shape',
          'design.resize_circles', 'design.points', 'design.import', 'design.delete_layer',
          'design.preview_shapes', 'design.apply_shapes', 'design.point_begin', 'design.point_edit', 'design.appearance',
          'design.repair_scan', 'design.review_repair', 'design.push_fit_analyze',
          'design.push_fit_review', 'design.push_fit_apply', 'design.patchwork_preview', 'design.patchwork_apply',
          'design.shop_pdf_prepare', 'design.shop_pdf_export', 'design.small_scan', 'design.small_keep', 'design.small_delete', 'design.small_unlock', 'design.suite_preview', 'design.suite_apply',
          'design.dimension_context', 'design.dimension_pick', 'design.dimension_preview', 'design.dimension_apply', 'design.dimension_delete',
          'design.guide_add', 'design.guides_preview', 'design.guides_apply'}

def finite(value):
    value = float(value)
    if not math.isfinite(value): raise ValueError('Enter a finite number.')
    return value

def validate_numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Enter finite numbers only.')
    if isinstance(value, dict):
        for item in value.values(): validate_numbers(item)
    elif isinstance(value, list):
        for item in value: validate_numbers(item)

class Bridge:
    def __init__(self, preferences_path=None, recovery_directory=None):
        self.session = AutomationSession()
        self.revision = 0
        self.clipboard = []
        self.clipboard_led = []
        self.clipboard_dimensions = []
        self.clipboard_guides = []
        self.clipboard_units = None
        self.opened_path = None
        self.saved_path = None
        self.saved_digest = None
        self.preferences_path = preferences_path or ROOT / 'user_state' / 'design-layout.json'
        self.palette_library = PaletteLibrary(self.preferences_path.with_name('color-palettes.json'))
        self.shop_pdf = ShopPDF(SOURCE_MANIFEST['source'])
        self.source_id = SOURCE_MANIFEST['snapshot_id']
        self.manufacturing = manufacturing.Manufacturing(self.source_id)
        self.reviewed_import_history = False
        from manufacturing_tools import ManufacturingTools
        self.manufacturing_tools = ManufacturingTools(self.preferences_path.with_name("tool-catalog.json"), CORE_ROOT / "default_tool_catalog.json")
        self.manufacturing_recipes = ManufacturingRecipes(self.preferences_path.with_name('second-tool-recipes.json'))
        self.knife_presets = KnifePresets(self.preferences_path.with_name('knife-presets.json'))
        from svg_import_review import SvgDraft
        self.svg_draft = SvgDraft()
        from mailer_authoring import MailerDraft
        self.mailer_draft = MailerDraft()
        self.shape_preview = None
        self.repair_scan = None
        self.push_fit = None
        self.patchwork_preview = None
        self.small_review = None
        self.dimension_draft = dimensions.DimensionDraft()
        self.guide_draft = guides.GuideDraft()
        self.cutoff_draft = CutoffDraft()
        self.repeat_draft = RepeatDraft()
        self.pan_draft = PanDraft()
        self.cabinet_draft = CabinetDraft()
        self.suite_draft = RepairSuiteDraft()
        self.new()
        self.recovery_directory = recovery_directory
        self.recovery = None
        self.recovery_warning = None
        if recovery_directory is not None:
            try: self.recovery = RecoveryStore(recovery_directory)
            except (OSError, ValueError) as exc: self.recovery_warning = str(exc)

    def new(self, params=None):
        result = self.session.execute('project.new', params or {})
        if not result['ok']: raise ValueError(result['error']['message'])
        factor = 1 if self.session.project.units == 'in' else 25.4
        self.session.execute('plate.define', {'plates':[{'number':1,'x':0,'y':0,'width':96*factor,'height':48*factor,'name':'Plate 1'}]})
        self.session._reset_history()
        self.history_labels = ['New drawing']
        self.opened_path = self.saved_path = None
        self.saved_digest = self.digest()

    def digest(self):
        _automation_layer_settings(self.session.project)
        return hashlib.sha256(json.dumps(self.session.project.to_dict(),sort_keys=True).encode()).hexdigest()

    def eligible(self):
        settings = _automation_layer_settings(self.session.project)
        return {e.id for e in self.session.project.entities
                if settings.get(e.layer,{}).get('visible',True) and not settings.get(e.layer,{}).get('locked',False)
                and not e.metadata.get('annotation_hidden',False)}

    def selected(self):
        selected = [e for e in self.session.project.entities if e.id in self.session.selected_ids]
        if not selected: raise ValueError('Select geometry first.')
        if not self.session.selected_ids <= self.eligible(): raise ValueError('Hidden or locked geometry cannot be edited.')
        return selected

    def locked_reference_groups(self):
        """Visible complete groups may be read as anchors, never selected for edits."""
        settings = _automation_layer_settings(self.session.project)
        groups = ArrangementSupport(self.session.project).groups()
        return {key:group for key,group in groups.items()
                if any(settings.get(e.layer, {}).get('locked', False) for e in group)
                and all(settings.get(e.layer, {}).get('visible', True)
                        and not e.metadata.get('annotation_hidden', False) for e in group)}

    def snapshot(self):
        s = self.session
        assemblies = ArrangementSupport(s.project)
        layers = s.execute('layer.list')['data']['layers']
        for layer in layers:
            layer['annotations']=sum(item.layer==layer['name'] for item in s.project.dimensions)
            layer['objects']+=layer['annotations']
        selected = [e for e in s.project.entities if e.id in s.selected_ids]
        caption = 'Perimeter'
        if len(selected) > 1: caption = f'Total length ({len(selected)})'
        elif len(selected) == 1:
            e = selected[0]
            caption = ('Editable annotation' if isinstance(e, TextEntity) else
                       'Perimeter' if isinstance(e, (Circle, Rectangle)) or isinstance(e, Polyline) and e.closed else 'Length')
        return {'name':s.project.name,'units':s.project.units,'revision':self.revision,
                'entities':[dict(e.to_dict(), bounds=entity_bounds(e)) for e in s.project.entities],
                'dimensions':dimensions.inventory(s.project), 'dimensionAuthoring':True,
                'guides':[g.to_dict() for g in s.project.guides], 'guideAuthoring':True, 'guideCleave':True,
                'layers':layers, 'plates':[p.to_dict() for p in _project_plates(s.project)],
                'operationRecipes':True, 'guidedReadiness':True, 'arrangementCorners':'align_top_left' in ALIGN_ACTIONS, 'panAuthoring':True, 'mailerAuthoring':True, 'svgImport':True, 'cabinetFabricationJobs':True, 'cabinetFabricationInspection':True, 'cabinetAuthoring':cabinet_editable(s.project), 'assemblyReview':True, 'stepRepeat':True, 'sheetCutoffs':True, 'patchworkAreas':self.patchwork_targets(), 'patchworkPacking':True, 'repairSuite':True,'smallGeometryActions':True,'smallGeometryReview':True,
                'activePlate':s.project.metadata.get('active_plate_number',1),
                'setup':s.project.metadata.get('job_setup',{}),
                'selected':sorted(s.selected_ids),'selectionBounds':drawing_bounds(selected,include_annotations=True),
                'selectionMeasurement': {'caption':caption,'value':sum(entity_perimeter(e) for e in selected)} if selected else None,
                'selectionInspection': selection_inspection(selected),
                'recovery': self.recovery_status(),
                'history':{'index':s._history_index,'entries':list(self.history_labels)},
                'bounds':drawing_bounds(s.project.entities,include_annotations=True),
                'canUndo':s.can_undo,'canRedo':s.can_redo,'dirty':self.digest()!=self.saved_digest,
                'path':str(self.saved_path or self.opened_path or ''),
                'savedPath':str(self.saved_path) if self.saved_path else None,
                'manufacturing':manufacturing.inventory(s.project),
                'readOnly':bool(s.project.operations) or manufacturing.has_cabinet_review(s.project),'operationCount':len(s.project.operations),
                'readOnlyReason':'Cabinet-tab sources are protected. Use Cabinet Tabs authoring for registration and paired review; fabrication remains separate.' if manufacturing.has_cabinet_review(s.project) else None,
                'annotationClipboard':True, 'clipboardCount':len(self.clipboard)+len(self.clipboard_dimensions)+len(self.clipboard_guides), 'backend':self.source_id,
                'arrangementItems':[assemblies.describe(key, group)
                    for key,group in assemblies.groups().items() if any(e.id in s.selected_ids for e in group)],
                'lockedArrangementReferences':[
                    assemblies.describe(key, group)
                    for key,group in self.locked_reference_groups().items()],
                'capabilities':['delete_layer','history_navigation','selection_measurements','shape_tools','arrange_repair','graphical_points','object_appearance','locked_arrangement_reference','color_palettes','opening_repair_review','push_through_fit','patchwork','shop_drawing_pdf']}

    def recovery_status(self):
        store = self.recovery
        return dict(enabled=self.recovery_directory is not None, warning=self.recovery_warning,
                    updated=store.updated if store else None, candidates=store.candidates if store else [])

    def sync_recovery(self, *, force=False):
        if self.recovery_directory is None: return
        try:
            if self.recovery is None: self.recovery = RecoveryStore(self.recovery_directory)
            if self.digest() == self.saved_digest or not manufacturing.supported(self.session.project):
                self.recovery.clear()
            else:
                self.recovery.checkpoint(self.session.project, self.saved_path or self.opened_path,
                    self.session.selected_ids, self.source_id, self.digest(), force=force)
            self.recovery_warning = None
        except (OSError, ValueError, TypeError) as exc:
            # A failed recovery write must not roll back a successful drawing edit.
            self.recovery_warning = str(exc)

    def recovery_command(self, command, p):
        if self.recovery is None: raise ValueError('Recovery is unavailable. Save As, then retry recovery.')
        store = self.recovery
        if command == 'recovery.list': store.scan()
        elif command == 'recovery.clear': store.clear()
        elif command in {'recovery.restore', 'recovery.discard'}:
            with store.candidate(p.get('id'), p.get('token')) as path:
                if command == 'recovery.restore':
                    s = self.session
                    if (self.digest() != self.saved_digest or self.opened_path or self.saved_path or
                        s.project.entities or s.project.dimensions or s.project.guides or s.project.operations or
                        s.can_undo or s.can_redo):
                        raise ValueError('Recovery requires a new, empty drawing. Save your current work first.')
                    data, project, _ = store.read(p['id'])
                    source = Path(data['source']).resolve() if data.get('source') else None
                    s.project = project
                    s.selected_ids = set(data.get('selected', [])) & self.eligible()
                    s._reset_history()
                    self.history_labels = ['Recovered drawing']
                    self.opened_path = source
                    self.saved_path = None
                    self.saved_digest = None
                    self.revision += 1
                    # Validate the complete display/state before consuming the old copy.
                    # The outer transaction restores the old drawing on any failure here.
                    self.snapshot()
                    store.checkpoint(s.project, source, s.selected_ids, self.source_id,
                                     self.digest(), force=True)
                try: path.unlink()
                except OSError as exc:
                    if command == 'recovery.discard': raise
                    self.recovery_warning = 'Recovered; old copy could not be removed: ' + str(exc)
            try: store.scan()
            except (OSError, ValueError) as exc:
                self.recovery_warning = 'Recovery list could not refresh: ' + str(exc)
        else: raise ValueError('Unknown recovery command.')
        return {'ok': True, 'state': self.snapshot()}

    def scan_small_geometry(self, p):
        self.small_review = None
        project = self.session.project
        factor = 25.4 if project.units == 'mm' else 1.0
        threshold = finite(p.get('maximum_size',DEFAULT_SPECK_IN*factor))
        for key in ('include_kept','isolated_only'):
            if key in p and type(p[key]) is not bool: raise ValueError('Choose valid review filters.')
        scope = p.get('entity_ids')
        known = {e.id:e for e in project.entities}
        if scope is not None and (not isinstance(scope,list) or any(not isinstance(i,str) or i not in known for i in scope)):
            raise ValueError('The review selection changed. Scan the drawing again.')
        # Scan the complete project for shared endpoints; restrict imported IDs
        # only after classification so attached details are not called isolated.
        findings = find_small_geometry(project,threshold/factor,include_kept=p.get('include_kept',False))
        if scope is not None: findings = [f for f in findings if f.entity_id in set(scope)]
        if p.get('isolated_only'): findings = [f for f in findings if f.deletable]
        settings = _automation_layer_settings(project)
        rows = []
        for f in findings:
            entity = known[f.entity_id]
            affected = affected_operations(project,{f.entity_id})
            layer = settings.get(entity.layer or '0',{})
            kept = project.metadata.get('small_geometry_kept',{}).get(f.entity_id)==f.signature
            recommendation = ('Inspect the whole object and its dimensions. If intentional, keep it.' if f.deletable
                else 'Inspect the flagged detail and its protection reason. Use the owning workflow or local contour repair when appropriate.')
            rows.append(dict(id=f.entity_id,label=entity.metadata.get('name') or f"Object {entity.metadata.get('object_number',f.entity_id[-8:])}",
                layer=entity.layer or '0',width=f.width,height=f.height,x=f.point.x,y=f.point.y,bounds=entity_bounds(entity),
                reason=f.reason,recommendation=recommendation,isolated=f.deletable,kept=kept,
                hidden=not layer.get('visible',True) or bool(entity.metadata.get('annotation_hidden')),
                locked=bool(layer.get('locked',False)),
                affectedOperations=[op.name for op in project.operations if op.id in affected]))
        token = new_id('small-review')
        self.small_review = dict(token=token,revision=self.revision,digest=self.digest(),findings={f.entity_id:f for f in findings},maximum_size_in=threshold/factor)
        return dict(token=token,revision=self.revision,maximumSize=threshold,findings=rows)

    def patchwork_areas(self):
        return [PatchworkArea.from_dict(a) for a in self.session.project.metadata.get('patchwork_areas',[])]

    def patchwork_targets(self):
        plates = {p.number:p for p in _project_plates(self.session.project)}
        return [dict(a.to_dict(),bounds=[plates[a.plate_number].x+a.x,plates[a.plate_number].y+a.y,
            plates[a.plate_number].x+a.x+a.width,plates[a.plate_number].y+a.y+a.height])
            for a in self.patchwork_areas() if a.plate_number in plates]

    def preview_patchwork(self, p):
        self.patchwork_preview = None
        plates = _project_plates(self.session.project)
        number = p.get('plate_number')
        plate = next((plate for plate in plates if plate.number == number),None)
        if type(number) is not int or plate is None: raise ValueError('Choose a defined plate.')
        original_plate = plate
        if 'envelope' in p:
            envelope = p['envelope']
            plate = Plate(number,plate.x,plate.y,finite(envelope['width']),finite(envelope['height']),plate.name)
            plates = validate_plate_definitions([plate if item.number==number else item for item in plates])
        areas = [PatchworkArea.from_dict(dict(a,plate_number=number)) for a in p['areas']]
        if len(areas)>500: raise ValueError('Keep this plate to at most 500 cut areas.')
        if sum(key in p for key in ('repeat','auto_place','pack_direction'))>1:
            raise ValueError('Choose one Patchwork placement action at a time.')
        if 'repeat' in p:
            repeat = p['repeat']
            count = repeat.get('count')
            if type(count) is not int or not 1<=count<=200: raise ValueError('Use a whole repeat count from 1 to 200.')
            areas.extend(create_repeated_patchwork_areas(areas,plate,width=finite(repeat['width']),
                height=finite(repeat['height']),count=count,start_x=finite(repeat['x']),start_y=finite(repeat['y']),
                gap=finite(repeat['gap']),direction=repeat['direction'],name_prefix=str(repeat.get('name','Piece'))))
        elif 'auto_place' in p:
            # Preserve existing material positions; only the new piece is placed.
            validate_patchwork_areas(areas,[plate])
            values = p['auto_place']
            seed = PatchworkArea(new_id('patch'),number,0,0,finite(values['width']),finite(values['height']),
                str(values.get('name') or f'Piece {len(areas)+1}'))
            areas.append(place_patchwork_area(areas,plate,seed,direction=values.get('direction','x')))
        elif 'pack_direction' in p:
            if p['pack_direction'] not in ('x','y'): raise ValueError('Choose X or Y packing.')
            if not areas: raise ValueError('Add material pieces before packing.')
            # Reject duplicate identities before repacking, but allow a new
            # envelope to relocate pieces that currently sit beyond its edge.
            if len({a.id for a in areas})!=len(areas): raise ValueError('Patchwork area IDs must be unique.')
            areas = arrange_patchwork_areas(areas,plate,direction=p['pack_direction'])
        if len(areas)>500: raise ValueError('Keep this plate to at most 500 cut areas.')
        retained = [a for a in self.patchwork_areas() if a.plate_number != number]
        validate_patchwork_areas(retained+areas,plates)
        candidate = copy.deepcopy(self.session.project)
        if plate != original_plate:
            candidate.metadata['plates'] = [item.to_dict() for item in plates]
            candidate.metadata.pop('sheet_layout',None)
        if retained+areas: candidate.metadata['patchwork_areas'] = [a.to_dict() for a in retained+areas]
        else: candidate.metadata.pop('patchwork_areas',None)
        token = new_id('patchwork-review')
        self.patchwork_preview = dict(token=token,revision=self.revision,digest=self.digest(),project=candidate)
        return dict(token=token,areas=[a.to_dict() for a in areas],plate=plate.to_dict(),
            envelopeChanged=plate != original_plate)

    def nest_patchwork(self, p):
        s = self.session
        plates = _project_plates(s.project)
        regions, owners = patchwork_nesting_plates(plates,self.patchwork_areas())
        spacing = finite(p.get('spacing',.25))
        if spacing < 0: raise ValueError('Nest spacing cannot be negative.')
        result = nest_entities_to_plates(s.project.entities,regions,spacing,
            entity_ids=set(p['entity_ids']),allow_new_plates=False)
        if not set(result.assignments) <= self.eligible(): raise ValueError('Unlock/show all members of the nested parts first.')
        s.project.entities = result.entities
        for e in s.project.entities:
            if e.id in result.assignments: e.metadata['plate_number'] = owners[result.assignments[e.id]]
        s.selected_ids = set(result.assignments)
        s._record_history()

    def push_fit_sources(self, ids):
        if not isinstance(ids, list) or not ids or any(not isinstance(i,str) for i in ids):
            raise ValueError('Assign contours to both metal and acrylic roles.')
        if len(set(ids)) != len(ids) or not set(ids) <= self.eligible():
            raise ValueError('Sources must be present, visible and unlocked.')
        sources = [e for e in self.session.project.entities if e.id in ids]
        if any(e.metadata.get('reference_only') or e.metadata.get('mounting_generated') for e in sources):
            raise ValueError('Reference-only and generated mounting geometry cannot be fit sources.')
        return sources

    def checked_push_fit(self, p):
        cached = self.push_fit
        if (not cached or p.get('token') != cached['token'] or
            self.revision != cached['revision'] or self.digest() != cached['digest']):
            raise ValueError('The drawing changed. Analyze the fit again.')
        proposal = cached['proposal']
        self.push_fit_sources(list(proposal.metal_entity_ids))
        self.push_fit_sources(list(proposal.acrylic_entity_ids))
        return cached

    def preview_push_fit(self, p, analyze=False):
        def drawing(entities):
            return [dict(e.to_dict(),bounds=entity_bounds(e)) for e in entities]
        if analyze:
            self.push_fit = None
            metal = self.push_fit_sources(p.get('metal_ids'))
            acrylic = self.push_fit_sources(p.get('acrylic_ids'))
            proposal = propose_push_through_fit(metal,acrylic,PushThroughFitSettings(
                cutter_diameter=finite(p['cutter_diameter']),fit_clearance=finite(p['fit_clearance'])))
            included = [c.candidate_id for c in proposal.candidates if c.recommended]
            self.push_fit = dict(token=new_id('fit-review'), revision=self.revision,
                digest=self.digest(),proposal=proposal, sources=drawing(metal+acrylic))
        else:
            self.checked_push_fit(p)
            self.push_fit['review_token'] = None
            included = p.get('included_ids')
        if not isinstance(included,list) or any(not isinstance(i,str) for i in included):
            raise ValueError('Choose interference regions to include.')
        cached = self.push_fit
        proposal = cached['proposal']
        outputs = corrected_metal_entities(proposal,included)
        # Candidate storage makes Apply commit exactly the reviewed result.
        candidate = copy.deepcopy(self.session.project)
        result = apply_push_through_fit(candidate,proposal,included) if included else None
        if result:
            removed = set(result.removed_dependent_ids)
            if not removed <= self.eligible() or removed.intersection(proposal.acrylic_entity_ids):
                raise ValueError('Relief would remove protected or acrylic dependent geometry. Resolve its associations first.')
            outputs = [e for e in candidate.entities if e.id in result.replacement_entity_ids]
        cached.update(review_token=new_id('fit-apply'), project=candidate,
                      selected=set(result.replacement_entity_ids) if result else set(), included=list(included))
        return dict(token=cached['token'],reviewToken=cached['review_token'],
            sources=cached['sources'],entities=drawing(outputs),
            acrylic=drawing(proposal.actual_acrylic_entities),
            candidates=[dict(id=c.candidate_id,bounds=c.bounds,area=c.area,recommended=c.recommended,
                note=c.note,entities=drawing(push_through_candidate_entities(proposal,[c.candidate_id]))) for c in proposal.candidates],
            included=included,warnings=list(proposal.warnings),
            removedCount=len(result.removed_dependent_ids) if result else 0)

    def scan_repair(self, p):
        self.repair_scan = None
        ids = set(p['entity_ids']) if 'entity_ids' in p else None
        candidates = [e for e in self.session.project.entities if ids is None or e.id in ids]
        issues = opening_repair_diagnostics(candidates, self.session.project.units)
        eligible = self.eligible() if not self.session.project.operations else set()
        editable = {identity for issue in issues if set(issue.entity_ids) <= eligible for identity in issue.entity_ids}
        affected = {identity for issue in issues for identity in issue.entity_ids}
        token = new_id('repair-review')
        self.repair_scan = dict(token=token, revision=self.revision, digest=self.digest(), editable=editable)
        return dict(token=token,gaps=sum(x.code=='geometry.repairable_gap' for x in issues),
            backtracks=sum(x.code=='geometry.micro_backtrack' for x in issues),
            affectedCount=len(affected),editableCount=len(editable),blockedCount=len(affected-editable),
            entities=[dict(e.to_dict(),bounds=entity_bounds(e)) for e in candidates if e.id in affected],
            markers={'joins':[dict(x=i.point.x,y=i.point.y) for i in issues if i.code=='geometry.repairable_gap'][:500],
                     'backtracks':[dict(x=i.point.x,y=i.point.y) for i in issues if i.code=='geometry.micro_backtrack'][:500]})

    def arrangement(self, selected, p):
        s = self.session
        assemblies = ArrangementSupport(s.project)
        ids = {e.id for e in selected}
        groups = {key:group for key,group in assemblies.groups().items() if any(e.id in ids for e in group)}
        expanded_ids = {e.id for group in groups.values() for e in group}
        if not ids <= expanded_ids:
            raise ValueError('Arrange the visible owner of a registered source outline.')
        if not expanded_ids <= self.eligible():
            raise ValueError('All associated objects must be visible and unlocked to move together.')
        selected = [e for e in s.project.entities if e.id in expanded_ids]
        ids = expanded_ids
        use_body = p.get('use_body_bounds', True)
        if type(use_body) is not bool: raise ValueError('Choose whether to use original body bounds.')
        def item(key, group, locked=False):
            info = assemblies.describe(key, group, body=use_body)
            return ArrangementItem(key, tuple(info['entityIds']), info['bodyBounds'] or info['bounds'], locked=locked)
        items = [item(key, group) for key,group in groups.items()]
        plate = next((x for x in _project_plates(s.project) if x.number == s.project.metadata.get('active_plate_number',1)), None)
        plate_bounds = (plate.x,plate.y,plate.x+plate.width,plate.y+plate.height) if plate else None
        reference = p.get('reference', 'selection')
        if reference not in {'selection','object','plate','locked','patchwork'}:
            raise ValueError('Choose selection bounds, a stationary object/group, or the active plate.')
        if reference == 'locked':
            key = p.get('reference_item_id')
            group = self.locked_reference_groups().get(key)
            if not group or any(e.id in ids for e in group):
                raise ValueError('Choose a visible locked object/group outside the moving selection.')
            items.append(item(key, group, locked=True))
        patchwork_bounds = None
        if reference == 'patchwork':
            validate_patchwork_areas(self.patchwork_areas(),_project_plates(s.project))
            area = next((a for a in self.patchwork_targets() if a['id']==p.get('patchwork_id')),None)
            if not area: raise ValueError('Choose an existing Patchwork cut area.')
            patchwork_bounds = tuple(area['bounds'])
        plan = plan_arrangement(items, p['action'], reference_mode='object' if reference == 'locked' else reference,
            reference_item_id=p.get('reference_item_id'), plate_bounds=plate_bounds,
            patchwork_bounds=patchwork_bounds,
            gap=finite(p['gap']) if 'gap' in p else None)
        candidate = copy.deepcopy(s.project)
        ArrangementSupport(candidate).translate(plan.moves)
        outputs = [e for e in candidate.entities if e.id in ids]
        return plan, candidate, outputs

    def preview_shapes(self, p):
        self.shape_preview = None
        selected = self.selected()
        s = self.session
        kind = p.get('kind', 'convert')
        removed = set()
        error = 0.0
        details = {}
        if kind == 'cleave':
            candidate, outputs, changed, removed, details = cleave_support.propose(s.project, s.selected_ids, p.get('guide_id'), p.get('line'))
            label = 'Cleave / split along drawn line' if p.get('line') is not None else 'Cleave / split along guide'
        elif kind == 'offset':
            distance = finite(p['distance'])
            if abs(distance) < 1e-12: raise ValueError('Enter a nonzero offset distance.')
            outputs = [offset_closed_geometry(e, distance) for e in selected]
            candidate = copy.deepcopy(s.project)
            candidate.entities.extend(outputs)
            changed = set()
            label = 'Offset outline' if distance > 0 else 'Offset inline'
        elif kind == 'arrange':
            plan, candidate, outputs = self.arrangement(selected, p)
            if not plan.changed_entity_ids: raise ValueError('The selection already matches this arrangement.')
            changed = set(s.selected_ids)
            label = p['action'].replace('_',' ').capitalize()
            details = dict(referenceBounds=plan.reference_bounds, gap=plan.gap,
                           movedCount=len(plan.changed_entity_ids), fixedItem=plan.reference_item_id)
        elif kind == 'repair':
            mode = p.get('mode', 'join')
            if mode not in {'join','backtracks','points'}: raise ValueError('Unknown contour repair mode.')
            tolerance = finite(p.get('tolerance', (.0001 if mode=='points' else .001) * (25.4 if s.project.units=='mm' else 1)))
            if mode == 'points':
                proposal = propose_near_point_repair(s.project.entities, s.selected_ids, tolerance=tolerance)
            else:
                for key in ('cleanup','backtracks'):
                    if key in p and not isinstance(p[key], bool): raise ValueError('Repair switches must be true or false.')
                proposal = propose_contour_repair(s.project.entities, s.selected_ids, tolerance=tolerance,
                    units=s.project.units, cleanup=p.get('cleanup',True) if mode=='join' else False,
                    backtracks=p.get('backtracks',True), join_endpoints=mode=='join',
                    endpoint_context=s.project.entities if p.get('opening_review') is True else None)
            if not proposal: raise ValueError('Nothing to repair at this tolerance. Select matching contour pieces or inspect their points.')
            candidate, _, removed = apply_contour_repair(s.project, proposal)
            outputs = [item.entity for item in proposal]
            changed = {identity for item in proposal for identity in item.source_ids}
            error = max(item.maximum_error for item in proposal)
            label = {'join':'Join / repair contours','backtracks':'Remove micro backtracks','points':'Remove near-duplicate points'}[mode]
            details = dict(closedCount=sum(e.closed for e in outputs),
                joinCount=sum(len(item.joins) for item in proposal),
                removedPoints=sum(item.removed_points for item in proposal),
                backtrackCount=sum(item.backtracks_removed for item in proposal),
                remainingIssues=sum(item.remaining_issues for item in proposal),
                markers={key:[dict(x=q.x,y=q.y) for item in proposal for q in getattr(item,attr)]
                         for key,attr in [('joins','joins'),('backtracks','backtrack_locations'),('points','near_point_locations')]})
        elif kind == 'convert':
            proposal = propose_shape_conversion(s.project.entities, s.selected_ids,
                units=s.project.units, **{k:p[k] for k in
                    ('mode','scope','tolerance','diameter','width','height') if k in p})
            if not proposal: raise ValueError('Nothing to convert; selected objects already have this representation.')
            candidate, _, removed = apply_shape_conversion(s.project, proposal)
            outputs = [item.entity for item in proposal]
            changed = {identity for item in proposal for identity in item.source_ids}
            error = max(item.maximum_error for item in proposal)
            label = 'Editable outlines' if p.get('mode') == 'outline' else 'Convert to shape'
        else: raise ValueError('Unknown shape tool.')
        token = new_id('preview')
        selection_base = {e['id'] for e in details['sources']} if kind == 'cleave' else s.selected_ids
        self.shape_preview = dict(token=token, revision=self.revision, digest=self.digest(),
            selection=set(s.selected_ids), project=candidate,
            selected=(selection_base-changed-removed)|{e.id for e in outputs} if kind in {'convert','repair','cleave'} else {e.id for e in outputs},
            label=label)
        return dict(token=token, entities=[dict(e.to_dict(), bounds=entity_bounds(e)) for e in outputs],
                    sourceCount=len(changed) if kind in {'convert','repair','cleave'} else len(selected),
                    removedCount=len(removed), maximumError=error, label=label, **details)

    def command(self, command, p):
        s = self.session
        if command == 'design.guide_add':
            if len(s.project.guides)>=1000: raise ValueError('Keep this drawing to at most 1000 guides.')
            guides.add(s.project,p)
            s._record_history()
            return
        if command == 'design.guides_apply':
            before=[g.to_dict() for g in s.project.guides]
            self.guide_draft.apply(s.project,p,self.revision,self.digest())
            if before!=[g.to_dict() for g in s.project.guides]: s._record_history()
            return
        if command == 'design.dimension_apply':
            self.dimension_draft.apply(s.project,p,self.revision,self.digest())
            for item in s.project.dimensions:
                _automation_layer_settings(s.project).setdefault(item.layer,dict(visible=True,locked=False,color='#536d9e'))
            s._record_history()
            return
        if command == 'design.dimension_delete':
            self.dimension_draft.delete(s.project,p['id'])
            s._record_history()
            return
        if command == 'design.repeat_apply':
            self.repeat_draft.apply(s,p,self.revision,self.digest())
            return
        if command == 'design.suite_apply':
            self.suite_draft.apply(s,p,self.revision,self.digest())
            return
        if command in {'design.small_keep','design.small_delete','design.small_unlock'}:
            cached = self.small_review
            if (not cached or p.get('token')!=cached['token'] or self.revision!=cached['revision'] or self.digest()!=cached['digest']):
                raise ValueError('The drawing or review changed. Scan small geometry again.')
            ids = p.get('entity_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in cached['findings'] for i in ids):
                raise ValueError('Choose findings from the current review.')
            if command in {'design.small_delete','design.small_unlock'}:
                if set(p) != {'token','entity_ids','confirmed'} or p.get('confirmed') is not True or len(ids)!=len(set(ids)):
                    raise ValueError('Explicitly confirm the selected review action.')
                if command == 'design.small_delete':
                    candidate, affected = delete_small_geometry(s.project,[cached['findings'][i] for i in ids],cached['maximum_size_in'])
                    if affected: raise ValueError('Use the owning machining workflow before deleting source geometry.')
                    s.project = candidate
                    s.selected_ids -= set(ids)
                else:
                    layers={e.layer or '0' for e in s.project.entities if e.id in ids}
                    settings=_automation_layer_settings(s.project)
                    if not any(settings.get(layer,{}).get('locked') for layer in layers):
                        raise ValueError('The selected findings have no locked layers. Scan again.')
                    for layer in layers:
                        if settings.get(layer,{}).get('locked'): settings[layer]['locked']=False
                s._record_history()
                self.small_review=None
                return
            kept = s.project.metadata.setdefault('small_geometry_kept',{})
            changed = any(kept.get(i)!=cached['findings'][i].signature for i in ids)
            if changed:
                kept.update({i:cached['findings'][i].signature for i in ids})
                s._record_history()
            return
        if command == 'design.patchwork_apply':
            cached = self.patchwork_preview
            if not cached or p.get('token')!=cached['token'] or self.revision!=cached['revision'] or self.digest()!=cached['digest']:
                raise ValueError('The drawing changed. Preview Patchwork again.')
            if s.project.to_dict() != cached['project'].to_dict():
                s.project = copy.deepcopy(cached['project'])
                s._record_history()
            return
        if command == 'design.review_repair':
            review = self.repair_scan
            if (not review or p.get('token') != review['token'] or self.revision != review['revision']
                or self.digest() != review['digest']):
                raise ValueError('The drawing changed. Check contours again before reviewing repair.')
            if not review['editable'] or not review['editable'] <= self.eligible() or s.project.operations:
                raise ValueError('No complete editable contour chains are available for repair.')
            s.selected_ids = set(review['editable'])
            return
        if command == 'design.appearance':
            selected = self.selected()
            if (type(p.get('expected_revision')) is not int or p['expected_revision'] != self.revision or
                set(p.get('entity_ids',[])) != {e.id for e in selected}):
                raise ValueError('The drawing or selection changed. Reopen Object Fill / Stroke.')
            if p.get('inherit_layer') is True:
                appearance = None
            else:
                first = selected[0]
                layer_color = _automation_layer_settings(s.project).get(first.layer,{}).get('color','#202830')
                original = first.appearance or appearance_from_layer_color(layer_color)
                payload = p['appearance']
                def color(key):
                    candidate = ColorValue.from_dict(payload[key])
                    token = p.get('palette_tokens', {}).get(key)
                    if token: return self.palette_library.selected_color(token, candidate)
                    previous = getattr(original,key)
                    if candidate == previous: return copy.deepcopy(previous)
                    kwargs = dict(profile=candidate.profile,name=candidate.name)
                    if candidate.model == 'rgb': return ColorValue.rgb(*candidate.channels,**kwargs)
                    if candidate.model == 'cmyk': return ColorValue.cmyk(*candidate.channels,**kwargs)
                    return ColorValue.named(candidate.name,candidate.preview_rgb,profile=candidate.profile)
                if any(type(payload.get(k)) is not bool for k in ('fill_enabled','stroke_enabled')):
                    raise ValueError('Choose valid fill and stroke settings.')
                appearance = ObjectAppearance(fill_enabled=payload['fill_enabled'],fill=color('fill'),
                    stroke_enabled=payload['stroke_enabled'],stroke=color('stroke'),stroke_width=finite(payload['stroke_width']))
                appearance.validate()
            for entity in selected: entity.appearance = copy.deepcopy(appearance)
            s._record_history()
            return
        if command in {'design.point_begin', 'design.point_edit'}:
            selected = self.selected()
            if (type(p.get('expected_revision')) is not int or p['expected_revision'] != self.revision or len(selected) != 1 or
                selected[0].id != p.get('entity_id')):
                raise ValueError('The drawing or selection changed. Select the contour again.')
            entity = selected[0]
            if not isinstance(entity, (Line, Polyline, Rectangle)):
                raise ValueError('Choose a line, rectangle or polyline. Convert curves to an editable outline first.')
            if command == 'design.point_begin':
                if not isinstance(entity, Rectangle): return
                x1,y1,x2,y2 = entity_bounds(entity)
                replacement = Polyline(id=entity.id, layer=entity.layer,
                    metadata=copy.deepcopy(entity.metadata), appearance=copy.deepcopy(entity.appearance),
                    points=[Point(x1,y1),Point(x2,y1),Point(x2,y2),Point(x1,y2)], closed=True)
            else:
                if isinstance(entity, Rectangle): raise ValueError('Activate the rectangle for point editing first.')
                points = list(entity.points) if isinstance(entity, Polyline) else [entity.start,entity.end]
                operation = p.get('operation')
                indices = p.get('indices', [])
                if (not isinstance(indices,list) or any(type(i) is not int or not 0<=i<len(points) for i in indices)):
                    raise ValueError('Select valid contour points.')
                indices = set(indices)
                if operation in {'move','delete'} and not indices:
                    raise ValueError('Select contour points first. No objects were deleted.')
                if operation == 'move':
                    dx,dy = finite(p['dx']),finite(p['dy'])
                    if dx == 0 and dy == 0: return
                    points = [Point(finite(q.x+dx),finite(q.y+dy)) if i in indices else q for i,q in enumerate(points)]
                elif operation == 'delete':
                    minimum = 3 if isinstance(entity,Polyline) and entity.closed else 2
                    if len(points)-len(indices)<minimum:
                        raise ValueError(f'Keep at least {minimum} points in this contour. No objects were deleted.')
                    points = [q for i,q in enumerate(points) if i not in indices]
                elif operation == 'insert':
                    q = Point(finite(p['x']),finite(p['y']))
                    count = len(points) if isinstance(entity,Polyline) and entity.closed else len(points)-1
                    def distance(i):
                        a,b = points[i],points[(i+1)%len(points)]
                        dx,dy = b.x-a.x,b.y-a.y
                        length = dx*dx+dy*dy
                        t = max(0,min(1,((q.x-a.x)*dx+(q.y-a.y)*dy)/length)) if length else 0
                        return (q.x-a.x-t*dx)**2+(q.y-a.y-t*dy)**2
                    after = min(range(count),key=distance)
                    points.insert(after+1,q)
                else: raise ValueError('Unknown point edit.')
                replacement = copy.deepcopy(entity)
                if isinstance(entity,Line) and operation == 'insert':
                    replacement = Polyline(id=entity.id,layer=entity.layer,
                        metadata=copy.deepcopy(entity.metadata),appearance=copy.deepcopy(entity.appearance),
                        points=points,closed=False)
                elif isinstance(replacement,Line): replacement.start,replacement.end = points
                else: replacement.points = points
            s.project.entities = [replacement if e.id==entity.id else e for e in s.project.entities]
            s._record_history()
            return
        if command == 'design.apply_shapes':
            preview = self.shape_preview
            if (not preview or p.get('token') != preview['token'] or
                self.revision != preview['revision'] or self.digest() != preview['digest'] or
                s.selected_ids != preview['selection']):
                raise ValueError('The drawing or selection changed. Preview again before applying.')
            self.selected()
            s.project = copy.deepcopy(preview['project'])
            s.selected_ids = set(preview['selected'])
            s._record_history()
            return
        if command == 'design.push_fit_apply':
            cached = self.checked_push_fit(p)
            if not cached.get('review_token') or p.get('review_token') != cached['review_token'] or not cached.get('included'):
                raise ValueError('Preview at least one included interference region before applying.')
            s.project = copy.deepcopy(cached['project'])
            s.selected_ids = set(cached['selected'])
            s._record_history()
            return
        if command == 'design.delete_layer':
            name = str(p.get('name', '')).strip()
            settings = _automation_layer_settings(s.project)
            if name == '0': raise ValueError('The default layer 0 cannot be deleted.')
            if name not in settings: raise ValueError('That layer no longer exists.')
            if settings[name].get('locked'): raise ValueError('Unlock the layer before deleting it.')
            objects = [e for e in [*s.project.entities,*s.project.dimensions] if e.layer == name]
            if objects and settings.get('0', {}).get('locked'):
                raise ValueError('Unlock layer 0 before moving objects to it.')
            for entity in objects: entity.layer = '0'
            del settings[name]
            if objects:
                settings.setdefault('0', {'locked':False,'color':'#263238','order':0})['visible'] = True
            s._record_history()
            return
        if command in {'design.copy', 'design.cut'}:
            dim_ids, guide_ids = p.get('dimension_ids', []), p.get('guide_ids', [])
            selected = clipboard.expanded_entities(s.project, s.selected_ids) if p.get('include_geometry', True) else []
            copied_dims, copied_guides = clipboard.capture_annotations(s.project, dim_ids, guide_ids, cutting=command=='design.cut')
            if not selected and not copied_dims and not copied_guides: raise ValueError('Select geometry, dimensions or guides to copy.')
            if any(e.id not in self.eligible() and not ArrangementSupport.registered_source(e) for e in selected):
                raise ValueError('Unlock and show all associated objects before copying or cutting this group.')
            ids = {e.id for e in selected}
            led = clipboard.capture_led(s.project, ids)
            self.clipboard = copy.deepcopy(selected)
            for entity in self.clipboard:
                if entity.id not in self.eligible() and ArrangementSupport.registered_source(entity):
                    entity.metadata['annotation_hidden'] = True
            self.clipboard_dimensions, self.clipboard_guides = copied_dims, copied_guides
            self.clipboard_led = led
            self.clipboard_units = s.project.units
            if command == 'design.cut':
                s.project.entities = [e for e in s.project.entities if e.id not in ids]
                clipboard.cut_led(s.project, ids)
                s.project.dimensions = [d for d in s.project.dimensions if d.id not in dim_ids]
                s.project.guides = [g for g in s.project.guides if g.id not in guide_ids]
                s.selected_ids.clear()
                s._record_history()
            return
        if command == 'design.import':
            other = AutomationSession()
            result = other.execute('project.open', {'path':p['path']})
            if not result['ok']: raise ValueError(result['error']['message'])
            if other.project.operations: raise ValueError('Import source geometry from a DXF or a drawing without machining operations.')
            if other.project.units != s.project.units: raise ValueError('Import units differ. Open separately or convert the source units first.')
            selected = copy.deepcopy(other.project.entities)
            imported, _ = clipboard.clone_entities(selected)
            settings = _automation_layer_settings(s.project)
            if any(settings.get(e.layer, {}).get('locked') or settings.get(e.layer, {}).get('visible') is False for e in imported):
                raise ValueError('Choose visible, unlocked destination layers before importing.')
            s.project.entities.extend(imported)
            s.selected_ids = {e.id for e in imported}
            s._record_history()
            return
        paste_dims, paste_guides = [], []
        external = p.get('clipboard')
        if command == 'design.paste':
            if external is not None:
                imported = clipboard.read_envelope(external)
                selected, paste_dims, paste_guides, units = imported.entities, imported.dimensions, imported.guides, imported.units
            else:
                selected = copy.deepcopy(self.clipboard)
                paste_dims, paste_guides, units = self.clipboard_dimensions, self.clipboard_guides, self.clipboard_units
            if not selected and not paste_dims and not paste_guides: raise ValueError('The drawing clipboard is empty.')
            if units != s.project.units:
                raise ValueError('Clipboard units differ from this drawing. Paste into a drawing with matching units.')
        elif command == 'design.shape': selected = []
        else: selected = self.selected()
        ids = {e.id for e in selected}
        pivot_bounds = drawing_bounds(selected,include_annotations=True)
        pivot = Point((pivot_bounds[0]+pivot_bounds[2])/2,(pivot_bounds[1]+pivot_bounds[3])/2) if pivot_bounds else Point(0,0)
        if command in {'design.duplicate','design.paste'}:
            default_offset = 0 if command == 'design.paste' else 1
            dx, dy = finite(p.get('dx',default_offset)), finite(p.get('dy',default_offset))
            target = p.get('target_layer')
            settings = _automation_layer_settings(s.project)
            known_layers = {'0', *settings, *(e.layer for e in s.project.entities)}
            if target is not None and (not isinstance(target,str) or target not in known_layers):
                raise ValueError('Choose an existing drawing layer before pasting.')
            for e in [*selected, *paste_dims]:
                if target is None and ArrangementSupport.registered_source(e): continue
                destination = target if target is not None else e.layer
                layer_settings = settings.get(destination, {})
                if layer_settings.get('locked') or layer_settings.get('visible') is False:
                    raise ValueError(f'Layer {destination} is hidden or locked. Choose a visible, unlocked destination.')
            copies, source_ids = clipboard.clone_entities(selected, dx, dy, target)
            if command == 'design.paste':
                if external is None: clipboard.paste_led(s.project, self.clipboard_led, source_ids, dx, dy)
                new_dims, new_guides = clipboard.clone_annotations(paste_dims, paste_guides, source_ids, dx, dy, target)
                s.project.dimensions.extend(new_dims)
                s.project.guides.extend(new_guides)
            s.project.entities.extend(copies); s.selected_ids={e.id for e in copies}
        elif command == 'design.scale':
            s.project.entities=[scale_entity(e,finite(p['sx']),finite(p['sy']),pivot) if e.id in ids else e for e in s.project.entities]
        elif command == 'design.arrange':
            _, s.project, outputs = self.arrangement(selected, p)
            s.selected_ids = {e.id for e in outputs}
        elif command == 'design.boolean':
            op=p['operation']; target=selected[0].id
            if op=='subtract_front': op='subtract'
            elif op=='subtract_back': op='subtract'; target=selected[-1].id
            result=boolean_shapes(selected,op,target_entity_id=target)
            s.project.entities=[e for e in s.project.entities if e.id not in ids]+list(result.entities)
            s.selected_ids={e.id for e in result.entities}
        elif command in {'design.group','design.ungroup','design.compound','design.uncompound'}:
            key='compound_id' if 'compound' in command else 'group_id'; identity=new_id(key)
            for e in selected:
                if '.un' in command: e.metadata.pop(key,None)
                else: e.metadata[key]=identity
        elif command in {'design.merge','design.explode'}:
            if command=='design.merge':
                if not all(isinstance(e,Line) for e in selected): raise ValueError('Join currently accepts connected line segments.')
                result=[merge_lines(selected)]
            else: result=[part for e in selected for part in explode_contour(e)]
            s.project.entities=[e for e in s.project.entities if e.id not in ids]+result
            s.selected_ids={e.id for e in result}
        elif command=='design.resize_circles':
            diameter=finite(p['diameter'])
            if diameter<=0 or not all(isinstance(e,Circle) for e in selected): raise ValueError('Select true circles and enter a positive diameter.')
            for e in selected: e.radius=diameter/2
        elif command=='design.points':
            if len(selected)!=1 or not isinstance(selected[0],Polyline): raise ValueError('Select one polyline to edit its points.')
            points=[Point(finite(q['x']),finite(q['y'])) for q in p['points']]
            if len(points)<(3 if selected[0].closed else 2): raise ValueError('Not enough points.')
            selected[0].points=points
        elif command=='design.shape':
            kind=p['kind']; w=finite(p['width']); h=finite(p['height']); x=finite(p.get('x',0)); y=finite(p.get('y',0))
            if w<=0 or h<=0: raise ValueError('Width and height must be positive.')
            layer=str(p.get('layer','0'))
            if kind in ('arrow','double_arrow','chevron','star'):
                e=make_shape_macro(ShapeMacroSpec(kind,w,h,points=int(p.get('sides',5))),Point(x+w/2,y+h/2),layer=layer)
            else:
                count=int(p.get('sides',6)) if kind=='polygon' else 128
                if not 3<=count<=256: raise ValueError('Polygon sides must be between 3 and 256.')
                if kind=='slot':
                    if w<h: raise ValueError('Horizontal slot width must be at least its height.')
                    r=h/2
                    points=[Point(x+w-r+r*math.cos(a),y+r+r*math.sin(a)) for a in [-math.pi/2+i*math.pi/64 for i in range(65)]]
                    points += [Point(x+r+r*math.cos(a),y+r+r*math.sin(a)) for a in [math.pi/2+i*math.pi/64 for i in range(65)]]
                else: points=[Point(x+w/2+w/2*math.cos(i*2*math.pi/count),y+h/2+h/2*math.sin(i*2*math.pi/count)) for i in range(count)]
                e=Polyline(points=points,closed=True,layer=layer)
            s.project.entities.append(e); s.selected_ids={e.id}
        else: raise ValueError('Design command unavailable.')
        s._record_history()

    def handle(self, request):
        command=request['command']; p=request.get('params') or {}
        before_project=copy.deepcopy(self.session.project)
        before=before_project.to_dict()
        selection=set(self.session.selected_ids)
        history=copy.deepcopy(self.session._history); index=self.session._history_index
        labels=list(self.history_labels)
        old_clipboard = (self.clipboard, self.clipboard_led, self.clipboard_units, self.clipboard_dimensions, self.clipboard_guides)
        old_document = (self.opened_path, self.saved_path, self.saved_digest, self.revision)
        try:
            if command in {'design.svg_begin','design.svg_preview'}: self.svg_draft.cached = None
            if command == 'design.svg_begin': self.svg_draft.loaded = None
            if command in {'design.mailer_begin','design.mailer_preview'}: self.mailer_draft.cached = None
            if command == 'design.mailer_begin': self.mailer_draft.context = None
            validate_numbers(p)
            if command=='hello':
                prefs={}
                try: prefs=json.loads(self.preferences_path.read_text(encoding='utf-8'))
                except (OSError,ValueError): pass
                return {'ok':True,'protocol':1,'state':self.snapshot(),'preferences':prefs}
            if command=='ui.save':
                self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
                tmp=self.preferences_path.with_suffix('.tmp'); tmp.write_text(json.dumps(p),encoding='utf-8'); tmp.replace(self.preferences_path)
                return {'ok':True}
            if command=='design.clipboard_export':
                if request.get('revision') != self.revision: raise ValueError('Drawing changed; copy again.')
                return {'ok':True, 'clipboard':clipboard.envelope(self.clipboard, self.clipboard_dimensions, self.clipboard_guides, self.clipboard_units, self.clipboard_led)}
            if command=='state': return {'ok':True,'state':self.snapshot()}
            if command.startswith('recovery.'):
                if request.get('revision') != self.revision: raise ValueError('Drawing changed; retry recovery.')
                if command == 'recovery.checkpoint':
                    self.sync_recovery(force=True)
                    return {'ok': self.recovery_warning is None, 'error': self.recovery_warning, 'state': self.snapshot()}
                return self.recovery_command(command, p)
            if command.startswith('palette.'):
                return {'ok':True, **self.palette_library.command(command,p)}
            if command in {'design.mailer_begin','design.mailer_preview','design.mailer_apply'}:
                if request.get('revision') != self.revision:raise ValueError('Drawing changed. Reopen the mailer review.')
                if command=='design.mailer_begin':return {'ok':True,'context':self.mailer_draft.begin(self.session.project,p,self.revision,self.digest())}
                if command=='design.mailer_preview':return {'ok':True,'preview':self.mailer_draft.preview(self.session.project,p,self.revision,self.digest())}
                self.mailer_draft.apply(self.session,p,self.revision,self.digest())
                self.history_labels=labels[:index+1]+['Add hinged-mailer blank and scores']
                self.manufacturing.invalidate();self.revision+=1;self.sync_recovery()
                return {'ok':True,'state':self.snapshot()}
            if command in {'design.svg_begin','design.svg_preview','design.svg_apply'}:
                if request.get('revision') != self.revision:raise ValueError('Drawing changed. Reopen SVG import.')
                if command=='design.svg_begin':return {'ok':True,'context':self.svg_draft.begin(self.session.project,p,self.revision,self.digest())}
                if command=='design.svg_preview':return {'ok':True,'preview':self.svg_draft.preview(self.session.project,p,self.revision,self.digest())}
                self.svg_draft.apply(self.session,p,self.revision,self.digest())
                self.history_labels=labels[:index+1]+['Import SVG geometry']
                self.manufacturing.invalidate();self.revision+=1;self.sync_recovery()
                return {'ok':True,'state':self.snapshot()}
            if command in {'design.cabinet_fabrication_jobs_preview','design.cabinet_fabrication_export'}:
                if request.get('revision') != self.revision:raise ValueError('Document changed. Preview Cabinet Tabs again.')
                import cabinet_fabrication_jobs as fabrication_jobs
                action=fabrication_jobs.preview if command.endswith('_preview') else fabrication_jobs.export
                return {'ok':True,'review':action(self.cabinet_draft,self.session.project,p,self.revision,self.digest(),self.manufacturing_tools)}
            if command in {'design.cabinet_fabrication_begin','design.cabinet_fabrication_inspect'}:
                if request.get('revision') != self.revision:raise ValueError('Document changed. Preview Cabinet Tabs again.')
                import cabinet_fabrication_review as fabrication
                action=fabrication.begin if command.endswith('_begin') else fabrication.inspect
                return {'ok':True,'review':action(self.cabinet_draft,self.session.project,p,self.revision,self.digest())}
            if command in {'design.pan_preview','design.pan_apply','design.cabinet_begin','design.cabinet_reregister','design.cabinet_preview','design.cabinet_apply'}:
                if request.get('revision') != self.revision: raise ValueError('Document changed. Reopen the authoring review.')
                if command=='design.pan_preview':return {'ok':True,'preview':self.pan_draft.preview(self.session.project,p,self.revision,self.digest(),self.session.selected_ids)}
                if command=='design.cabinet_begin':return {'ok':True,'context':self.cabinet_draft.begin(self.session.project,self.revision,self.digest(),self.session.selected_ids)}
                if command=='design.cabinet_reregister':return {'ok':True,'plan':self.cabinet_draft.reregister(self.session.project,p,self.revision,self.digest())}
                if command=='design.cabinet_preview':return {'ok':True,'preview':self.cabinet_draft.preview(self.session.project,p,self.revision,self.digest())}
                draft=self.pan_draft if command=='design.pan_apply' else self.cabinet_draft
                draft.apply(self.session,p,self.revision,self.digest())
                project=self.session.project
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,CamProject.from_dict(before).entities)
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,project.entities)
                self.session._history[self.session._history_index]=copy.deepcopy(project.to_dict())
                self.history_labels=labels[:index+1]+['DR pan geometry' if command=='design.pan_apply' else 'Cabinet tab review']
                self.manufacturing.invalidate();self.revision+=1;self.sync_recovery()
                return {'ok':True,'state':self.snapshot()}
            if command in {'design.cabinet_review','design.pan_review','design.pan_select'}:
                if request.get('revision') != self.revision: raise ValueError('Drawing changed. Refresh assembly review.')
                import assembly_review
                if command=='design.cabinet_review': return {'ok':True,'review':assembly_review.cabinet(self.session.project)}
                if command=='design.pan_review': return {'ok':True,'review':assembly_review.dr_pans(self.session.project)}
                if manufacturing.has_cabinet_review(self.session.project): raise ValueError('Cabinet jobs remain read-only.')
                self.session.selected_ids=assembly_review.pan_selection(self.session.project,p)
                self.revision+=1
                return {'ok':True,'state':self.snapshot()}
            if command in {'manufacturing.attachment_list','manufacturing.attachment_review','manufacturing.attachment_prepare'}:
                if request.get('revision') != self.revision: raise ValueError('Document changed; refresh recipe choices.')
                import operation_recipe
                if command.endswith('_list'):
                    return {'ok':True,'review':operation_recipe.listing(self.session.project,self.manufacturing_tools,self.revision,p)}
                if command.endswith('_prepare'):
                    self.manufacturing.invalidate()
                    if not {'choice','reviewed'}<=set(p) or set(p)-{'choice','reviewed','holding_reviewed'} or p['reviewed'] is not True:raise ValueError('Review the exact recipe settings before generating motion.')
                    choice=p['choice']
                else: choice=p
                candidate,review=operation_recipe.resolve(self.session.project,self.manufacturing_tools,self.revision,choice)
                if command.endswith('_review'):
                    self.manufacturing.invalidate()
                    return {'ok':True,'review':review}
                if review['requiresHoldingReview'] and p.get('holding_reviewed') is not True:raise ValueError('Explicitly review changes to depth, tabs and hold-down before generating motion.')
                result=self.manufacturing.command('attachment_prepare',{'candidate':candidate,'review':review},self.session.project,self.revision)
                return {'ok':True,'manufacturing':result}
            if command in {'manufacturing.guided_readiness','manufacturing.guided_action'}:
                if request.get('revision') != self.revision: raise ValueError('Document changed; refresh readiness.')
                import guided_readiness
                fn=guided_readiness.review if command.endswith('guided_readiness') else guided_readiness.action
                return {'ok':True,'review':fn(self.session.project,self.manufacturing_tools,self.revision,p)}
            if manufacturing.has_cabinet_review(self.session.project) and not (
                command in {'project.open','project.new','design.copy','manufacturing.poll','manufacturing.cancel'}
                or (cabinet_editable(self.session.project) and (command=='project.save' or command.startswith('history.')))
                or command.startswith(('selection.','manufacturing.catalog_'))):
                raise ValueError('Cabinet-tab sources are protected. Use Cabinet Tabs authoring for registration and paired review; fabrication remains separate.')
            if command.startswith('manufacturing.'):
                if request.get('revision') != self.revision: raise ValueError('Document changed; retry against the current job.')
                action = command.split('.',1)[1]
                if action.startswith('catalog_'):
                    return {'ok':True,**self.manufacturing_tools.command(action,p,self.session.project)}
                if action.startswith('recipe_'):
                    return {'ok':True,**self.manufacturing_recipes.command(action,p,self.session.project)}
                if action.startswith('knife_'):
                    return {'ok':True,**self.knife_presets.command(action,p,self.session.project)}
                if action in {'small_centers','open_paths','closed_overruns','linked_info','picking_info','generate','poll','cancel','export','group_prepare','operation_prepare','optimization_prepare','management_prepare'}:
                    return {'ok':True,'manufacturing':self.manufacturing.command(action,p,self.session.project,self.revision)}
                if action in {'group_apply','operation_apply','optimization_apply','management_apply'}:
                    imported = action=='management_apply' and self.manufacturing.result.get('action')=='import_groups'
                    attachment=self.manufacturing.result.get('attachment')
                    if attachment:
                        import operation_recipe
                        candidate,_=operation_recipe.resolve(self.session.project,self.manufacturing_tools,self.revision,attachment['choice'])
                        if self.manufacturing.group_candidate is None or candidate.to_dict()!=self.manufacturing.group_candidate.to_dict():
                            raise ValueError('Recipe draft changed. Review and regenerate motion.')
                        # Allows one-step Undo back to a previously protected imported record.
                    self.manufacturing.apply_group(self.session,p,self.revision)
                    if attachment: self.reviewed_import_history = True
                    if imported: self.reviewed_import_history = True
                else: manufacturing.mutate(self.session,action,p)
                if self.session._history_index == index+1:
                    self.history_labels = labels[:index+1]+['Manufacturing: '+action.replace('_',' ')]
                self.manufacturing.invalidate()
                self.revision += 1
                self.sync_recovery()
                return {'ok':True,'state':self.snapshot()}
            if command=='design.cutoff_preview':
                if request.get('revision') != self.revision: raise ValueError('Drawing changed; preview again.')
                return {'ok':True,'preview':self.cutoff_draft.preview(self.session.project,p,self.revision,self.digest())}
            if command=='design.cutoff_apply':
                if request.get('revision') != self.revision: raise ValueError('Drawing changed; preview again.')
                self.cutoff_draft.apply(self.session,p,self.revision,self.digest())
                project=self.session.project
                from cam_core.patchwork_holes import refresh_linked_holes
                refresh_linked_holes(project, _project_plates(project))
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,CamProject.from_dict(before).entities)
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,project.entities)
                self.session._history[self.session._history_index]=copy.deepcopy(project.to_dict())
                self.history_labels=labels[:index+1]+['Sheet cutoff lines']
                self.manufacturing.invalidate()
                self.revision+=1
                self.sync_recovery()
                return {'ok':True,'state':self.snapshot()}
            if command not in ALLOWED|CUSTOM|{'design.copy'}: raise ValueError('Command is not exposed by the Design adapter.')
            is_view=command.startswith('selection.') or command in {'design.copy','plate.set_active','design.repair_scan','design.review_repair','design.small_scan','design.dimension_context','design.dimension_pick'}
            if self.session.project.operations and not is_view and command not in {'project.open','project.new'} and not ((manufacturing.supported(self.session.project) and (command=='project.save' or command.startswith('history.'))) or (self.reviewed_import_history and command.startswith('history.'))):
                raise ValueError('Jobs with machining operations are view-only in this Design build. Use a source DXF or a new drawing; operation association editing is not ported yet.')
            if command not in {'project.new','project.open'} and 'revision' in request and request['revision']!=self.revision:
                raise ValueError('The document changed. Retry this action against the current drawing.')
            if command=='design.repeat_preview':
                return {'ok':True,'preview':self.repeat_draft.preview(self.session.project,p,self.revision,self.digest(),self.session.selected_ids)}
            if command=='design.suite_preview':
                return {'ok':True,'preview':self.suite_draft.preview(self.session.project,p,self.revision,self.digest(),self.session.selected_ids)}
            if command=='design.preview_shapes':
                return {'ok':True, 'preview':self.preview_shapes(p)}
            if command=='design.dimension_context':
                return {'ok':True,'references':dimensions.context(self.session.project,self.session.selected_ids)}
            if command=='design.dimension_pick':
                return {'ok':True,'reference':dimensions.pick(self.session.project,p)}
            if command=='design.dimension_preview':
                return {'ok':True,'preview':self.dimension_draft.preview(self.session.project,p,self.revision,self.digest())}
            if command=='design.guides_preview':
                return {'ok':True,'preview':self.guide_draft.preview(self.session.project,p,self.revision,self.digest())}
            if command in {'design.push_fit_analyze','design.push_fit_review'}:
                return {'ok':True, 'preview':self.preview_push_fit(p,analyze=command=='design.push_fit_analyze')}
            if command=='design.patchwork_preview':
                return {'ok':True,'preview':self.preview_patchwork(p)}
            if command=='design.shop_pdf_prepare':
                return {'ok':True,'preview':self.shop_pdf.prepare(self.session.project,_project_plates(self.session.project),p,
                    revision=self.revision,digest=self.digest())}
            if command=='design.shop_pdf_export':
                return {'ok':True,'output':self.shop_pdf.export(p,revision=self.revision,digest=self.digest())}
            if command=='design.repair_scan':
                return {'ok':True, 'review':self.scan_repair(p)}
            if command=='design.small_scan':
                return {'ok':True, 'review':self.scan_small_geometry(p)}
            if command in {'project.save','project.export_dxf'}:
                path=Path(p['path']).resolve()
                if self.opened_path and path==self.opened_path: raise ValueError('Keep the opened source unchanged. Choose a different file name with Save As.')
                if command=='project.save' and path.suffix.lower()!='.json': raise ValueError('Save a .camjob.json file.')
                if command=='project.export_dxf' and path.suffix.lower()!='.dxf': raise ValueError('Export a .dxf file.')
            if command.startswith('geometry.add_') or command=='design.shape':
                settings=_automation_layer_settings(self.session.project).get(p.get('layer','0'),{})
                if settings.get('locked') or settings.get('visible') is False:
                    raise ValueError('Choose a visible, unlocked drawing layer.')
            if command=='geometry.auto_nest':
                targets=self.session.selected_ids or self.eligible()
                if not targets: raise ValueError('Select or draw geometry to nest.')
                p=dict(p,entity_ids=sorted(targets))
            if command in {'plate.define','plate.add','plate.update','design.patchwork_apply','job.setup'}:
                from cam_core.patchwork_holes import refresh_linked_holes
                refresh_linked_holes(self.session.project, _project_plates(self.session.project))
            if command=='history.goto':
                target=p.get('index')
                if isinstance(target,bool) or not isinstance(target,int) or not 0<=target<len(history):
                    raise ValueError('That history state is no longer available.')
                self.session.project=CamProject.from_dict(copy.deepcopy(history[target]))
                self.session._history_index=target
                self.session.selected_ids.clear()
            elif command=='geometry.auto_nest' and self.patchwork_areas(): self.nest_patchwork(p)
            elif command in CUSTOM or command=='design.copy':
                self.command(command,p)
            elif command=='project.new': self.new(p)
            else:
                if command.startswith('geometry.') and not command.startswith('geometry.add_') and command!='geometry.auto_nest':
                    self.selected()
                if command=='selection.set': p['entity_ids']=list(set(p.get('entity_ids',[])) & self.eligible())
                result=self.session.execute(command,p)
                if not result['ok']: raise ValueError(result['error']['message'])
                if command=='project.open':
                    self.opened_path=Path(p['path']).resolve(); self.saved_path=None; self.saved_digest=self.digest()
                    self.history_labels=['Opened drawing']
                elif command=='project.save':
                    self.saved_path=Path(p['path']).resolve(); self.saved_digest=self.digest()
            if command in {'project.new','project.open'}: self.reviewed_import_history = False
            if command.startswith('plate.') and self.patchwork_areas():
                validate_patchwork_areas(self.patchwork_areas(),_project_plates(self.session.project))
            if not command.startswith('history.') and command not in {'project.new','project.open'} and self.session._history_index==index+1:
                # Keep the last resolved points even when a source was removed or
                # changed representation. Store this in the same undo transaction.
                project=self.session.project
                from cam_core.patchwork_holes import refresh_linked_holes
                refresh_linked_holes(project, _project_plates(project))
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,CamProject.from_dict(before).entities)
                project.dimensions=dimensions.refresh_dimension_fallbacks(project.dimensions,project.entities)
                self.session._history[self.session._history_index]=copy.deepcopy(project.to_dict())
                label=command.split('.')[-1].replace('_',' ').capitalize()
                if command=='design.shape': label='Add '+str(p.get('kind','shape'))
                elif command=='design.arrange': label=str(p.get('action','Arrange')).replace('_',' ').capitalize()
                elif command=='design.boolean': label=str(p.get('operation','Boolean')).replace('_',' ').capitalize()
                elif command=='layer.set': label='Layer: '+str(p.get('name',''))
                elif command=='design.apply_shapes': label=self.shape_preview['label']
                elif command=='design.push_fit_apply': label='Push-through fit'
                elif command=='design.patchwork_apply': label='Patchwork cut areas'
                elif command=='design.small_keep': label='Keep reviewed small geometry'
                elif command=='design.small_delete': label='Delete reviewed small geometry'
                elif command=='design.small_unlock': label='Unlock reviewed layers'
                elif command=='design.suite_apply': label='Repair suite batch'
                elif command=='design.dimension_apply': label='Add / edit dimension'
                elif command=='design.dimension_delete': label='Delete dimension'
                elif command=='design.guide_add': label='Add '+str(p['orientation'])+' guide'
                elif command=='design.guides_apply': label='Edit ruler guides'
                elif command=='design.paste': label='Paste in place' if p.get('target_layer') is None else 'Paste to layer '+p['target_layer']
                elif command=='design.cut': label='Cut geometry'
                elif command=='design.point_begin': label='Rectangle to editable contour'
                elif command=='design.point_edit': label=str(p.get('operation','Edit')).capitalize()+' points'
                elif command=='design.appearance': label='Use layer appearance' if p.get('inherit_layer') else 'Object Fill / Stroke'
                self.history_labels=labels[:index+1]+[label]
            self.session.selected_ids &= self.eligible()
            self.manufacturing.invalidate()
            self.revision+=1
            self.sync_recovery()
            return {'ok':True,'state':self.snapshot()}
        except Exception as exc:
            self.session.project=before_project; self.session.selected_ids=selection
            self.session._history=history; self.session._history_index=index
            self.history_labels=labels
            self.clipboard, self.clipboard_led, self.clipboard_units, self.clipboard_dimensions, self.clipboard_guides = old_clipboard
            self.opened_path, self.saved_path, self.saved_digest, self.revision = old_document
            return {'ok':False,'error':str(exc),'state':self.snapshot()}

def main():
    recovery = None
    if os.environ.get('WORKBENCH_RECOVERY_ENABLED') == '1':
        override = os.environ.get('WORKBENCH_RECOVERY_DIRECTORY')
        recovery = Path(override) if override else ROOT / 'user_state' / 'recovery'
        if override and not recovery.resolve().is_relative_to((ROOT.parent / 'build').resolve()):
            raise ValueError('Test recovery directories must stay inside the Flutter build directory.')
    bridge=Bridge(recovery_directory=recovery)
    try:
        for line in sys.stdin:
            request={}
            try:
                request=json.loads(line)
                with __import__('contextlib').redirect_stdout(sys.stderr): result=bridge.handle(request)
                result['id']=request.get('id')
            except Exception as exc: result={'id':request.get('id'),'ok':False,'error':str(exc)}
            print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)
    finally:
        bridge.manufacturing.close()
        if bridge.recovery:
            try: bridge.recovery.close()
            except OSError: pass

if __name__=='__main__': main()
