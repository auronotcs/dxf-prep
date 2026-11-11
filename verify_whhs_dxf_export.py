from pathlib import Path
import tempfile

from cam_core import (
    CamProject,
    Plate,
    Polyline,
    dxf_export_entities,
    export_dxf,
    import_dxf,
    make_plate_project,
    entity_bounds,
)


snapshot = Path(r"C:\Users\auron\Documents\Boat stuff\WHHS-boundary-inspection-snapshot.camjob.json")
project = CamProject.load(snapshot)
plates = [Plate.from_dict(payload) for payload in project.metadata.get("plates", [])]
eligible_ids = {entity.id for entity in dxf_export_entities(project)}
source_ids = {
    entity.id for entity in project.entities
    if entity.metadata.get("bent_tab_source_reference")
}
outline_by_id = {
    entity.id: entity for entity in project.entities
    if isinstance(entity, Polyline) and entity.metadata.get("bent_tab_cut_outline")
}

raw_crossings = []
eligible_crossings = []
for plate in plates:
    result = make_plate_project(project, plate)
    raw_crossings.extend(result.crossing_entity_ids)
    eligible_crossings.extend(
        entity_id for entity_id in result.crossing_entity_ids
        if entity_id in eligible_ids
    )

with tempfile.TemporaryDirectory(dir=Path(r"C:\Users\auron\Documents\Boat stuff")) as folder:
    path = Path(folder) / "WHHS export proof.dxf"
    exported = export_dxf(project, path)
    restored = import_dxf(path).project
    restored_outlines = [
        entity for entity in restored.entities
        if entity.layer == "BENT_TAB_CUT" and isinstance(entity, Polyline)
    ]
    def signature(entity):
        return tuple((round(point.x, 7), round(point.y, 7)) for point in entity.points)
    restored_signatures = {signature(entity) for entity in restored.entities if isinstance(entity, Polyline)}
    exact_outline_matches = sum(signature(entity) in restored_signatures for entity in outline_by_id.values())
    restored_bounds = {
        tuple(round(value, 5) for value in entity_bounds(entity))
        for entity in restored.entities if isinstance(entity, Polyline)
    }
    outline_bounds_matches = sum(
        tuple(round(value, 5) for value in entity_bounds(entity)) in restored_bounds
        for entity in outline_by_id.values()
    )
    print(f"entities_source={len(project.entities)}")
    print(f"entities_exported={sum(exported.entity_counts.values())}")
    print(f"export_counts={dict(exported.entity_counts)}")
    print(f"private_sources={len(source_ids)}")
    print(f"raw_crossings={len(raw_crossings)}")
    print(f"raw_crossings_private={sum(entity_id in source_ids for entity_id in raw_crossings)}")
    print(f"eligible_crossings={len(eligible_crossings)}")
    print(f"source_layer_exported={'_BENT_TAB_SOURCE' in exported.layer_names}")
    print(f"final_outlines_source={len(outline_by_id)}")
    print(f"final_outlines_roundtrip={len(restored_outlines)}")
    print(f"final_outline_exact_matches={exact_outline_matches}")
    print(f"final_outline_bounds_matches={outline_bounds_matches}")
    print(f"final_outline_source_layers={sorted({entity.layer for entity in outline_by_id.values()})}")
    print(f"roundtrip_layers={sorted({entity.layer for entity in restored.entities})}")
    print(f"roundtrip_kinds={dict(__import__('collections').Counter(entity.kind for entity in restored.entities))}")
    print(f"bend_guides_exported={'BEND_GUIDES' in exported.layer_names}")
