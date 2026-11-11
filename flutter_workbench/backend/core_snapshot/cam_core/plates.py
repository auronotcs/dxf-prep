from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import shutil
from uuid import uuid4

from .layout import entity_bounds
from .model import CamProject, Entity, Line, Point, TextEntity
from .transform import transform_entity
from .dimensions import translate_dimension


@dataclass(frozen=True)
class Plate:
    number: int
    x: float
    y: float
    width: float
    height: float
    name: str = ""

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Plate origins and sizes must be finite numbers.")
        if self.number < 1:
            raise ValueError("Plate number must be at least 1.")
        if self.x < 0 or self.y < 0:
            raise ValueError("Plate X and Y must be in the positive quadrant.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Plate width and height must be positive.")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.width, self.y + self.height

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Plate":
        return cls(
            number=int(payload["number"]),
            name=str(payload.get("name", "")),
            x=float(payload["x"]),
            y=float(payload["y"]),
            width=float(payload["width"]),
            height=float(payload["height"]),
        )


@dataclass(frozen=True)
class PatchworkArea:
    """One physical piece of usable stock positioned inside a plate envelope.

    X and Y are intentionally local to the containing plate.  A patchwork area
    is a planning guide only: it is not drawing geometry or a machining
    boundary and therefore cannot accidentally reach controller output.
    """

    id: str
    plate_number: int
    x: float
    y: float
    width: float
    height: float
    name: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Patchwork area ID cannot be blank.")
        if self.plate_number < 1:
            raise ValueError("Patchwork plate number must be at least 1.")
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Patchwork origins and sizes must be finite numbers.")
        if self.x < 0 or self.y < 0:
            raise ValueError("Patchwork X and Y origins cannot be negative.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Patchwork width and height must be positive.")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.width, self.y + self.height

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plate_number": self.plate_number,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PatchworkArea":
        return cls(
            id=str(payload["id"]),
            plate_number=int(payload["plate_number"]),
            name=str(payload.get("name", "")),
            x=float(payload["x"]),
            y=float(payload["y"]),
            width=float(payload["width"]),
            height=float(payload["height"]),
        )


@dataclass(frozen=True)
class PlateProjectResult:
    project: CamProject
    crossing_entity_ids: tuple[str, ...]
    skipped_operation_ids: tuple[str, ...]


def validate_plate_definitions(plates: list[Plate], tolerance: float = 1e-9) -> list[Plate]:
    """Return numbered plates in order after rejecting duplicates and physical overlap."""
    ordered = sorted(plates, key=lambda plate: plate.number)
    if not ordered:
        raise ValueError("Define at least one plate.")
    if len({plate.number for plate in ordered}) != len(ordered):
        raise ValueError("Plate numbers must be unique.")
    for index, first in enumerate(ordered):
        first_min_x, first_min_y, first_max_x, first_max_y = first.bounds
        for second in ordered[index + 1:]:
            second_min_x, second_min_y, second_max_x, second_max_y = second.bounds
            overlaps = (
                min(first_max_x, second_max_x) - max(first_min_x, second_min_x) > tolerance
                and min(first_max_y, second_max_y) - max(first_min_y, second_min_y) > tolerance
            )
            if overlaps:
                raise ValueError(f"Plate {first.number} overlaps Plate {second.number}.")
    return ordered


def validate_patchwork_areas(
    areas: list[PatchworkArea],
    plates: list[Plate],
    tolerance: float = 1e-9,
) -> list[PatchworkArea]:
    """Validate local usable-stock rectangles against their plate envelopes."""
    plate_by_number = {plate.number: plate for plate in plates}
    if len(plate_by_number) != len(plates):
        raise ValueError("Plate numbers must be unique before defining patchwork areas.")
    if len({area.id for area in areas}) != len(areas):
        raise ValueError("Patchwork area IDs must be unique.")
    for area in areas:
        plate = plate_by_number.get(area.plate_number)
        if plate is None:
            raise ValueError(f"{area.name or area.id} refers to missing Plate {area.plate_number}.")
        if area.x + area.width > plate.width + tolerance or area.y + area.height > plate.height + tolerance:
            raise ValueError(
                f"{area.name or area.id} extends outside Plate {area.plate_number}. "
                f"Its far corner is X{area.x + area.width:g} Y{area.y + area.height:g}, "
                f"but the plate is X{plate.width:g} Y{plate.height:g}."
            )
    for index, first in enumerate(areas):
        for second in areas[index + 1:]:
            if first.plate_number != second.plate_number:
                continue
            overlaps = (
                min(first.x + first.width, second.x + second.width) - max(first.x, second.x) > tolerance
                and min(first.y + first.height, second.y + second.height) - max(first.y, second.y) > tolerance
            )
            if overlaps:
                raise ValueError(
                    f"{first.name or first.id} overlaps {second.name or second.id} on Plate {first.plate_number}."
                )
    return list(areas)


def place_patchwork_area(
    existing: list[PatchworkArea],
    plate: Plate,
    area: PatchworkArea,
    tolerance: float = 1e-9,
    *,
    direction: str = "x",
) -> PatchworkArea:
    """Place one oriented piece into the first opening along the chosen axis.

    ``x`` retains the original left-to-right, then upward search. ``y`` searches
    upward first, then to the right. Neither mode rotates the piece.
    """
    direction = str(direction).strip().casefold()
    if direction not in {"x", "y"}:
        raise ValueError("Patchwork stacking direction must be X or Y.")
    if area.plate_number != plate.number:
        area = PatchworkArea(area.id, plate.number, area.x, area.y, area.width, area.height, area.name)
    peers = [candidate for candidate in existing if candidate.plate_number == plate.number]
    x_candidates = {0.0}
    y_candidates = {0.0}
    for candidate in peers:
        x_candidates.update((candidate.x, candidate.x + candidate.width, candidate.x - area.width))
        y_candidates.update((candidate.y, candidate.y + candidate.height, candidate.y - area.height))
    positions = [
        (x, y)
        for y in y_candidates for x in x_candidates
        if x >= -tolerance and y >= -tolerance
        and x + area.width <= plate.width + tolerance
        and y + area.height <= plate.height + tolerance
    ]
    positions.sort(key=(lambda value: (value[1], value[0])) if direction == "x" else (lambda value: (value[0], value[1])))
    for x, y in positions:
        positioned = PatchworkArea(
            area.id, plate.number, max(0.0, x), max(0.0, y), area.width, area.height, area.name,
        )
        try:
            validate_patchwork_areas([*peers, positioned], [plate], tolerance)
        except ValueError:
            continue
        return positioned
    raise ValueError(
        f"{area.name or 'This piece'} ({area.width:g} × {area.height:g}) does not fit in an open rectangular area on Plate {plate.number}. "
        "Swap its X/Y dimensions, rearrange the existing pieces, or use a larger plate."
    )


def arrange_patchwork_areas(
    areas: list[PatchworkArea],
    plate: Plate,
    tolerance: float = 1e-9,
    *,
    direction: str = "x",
) -> list[PatchworkArea]:
    """Repack oriented pieces from the plate's lower-left in their listed order."""
    arranged: list[PatchworkArea] = []
    for area in areas:
        seed = PatchworkArea(area.id, plate.number, 0.0, 0.0, area.width, area.height, area.name)
        arranged.append(place_patchwork_area(arranged, plate, seed, tolerance, direction=direction))
    return arranged


def create_repeated_patchwork_areas(
    existing: list[PatchworkArea],
    plate: Plate,
    *,
    width: float,
    height: float,
    count: int,
    start_x: float,
    start_y: float,
    gap: float,
    direction: str = "y",
    name_prefix: str = "Flat bar",
    id_prefix: str | None = None,
    tolerance: float = 1e-9,
) -> list[PatchworkArea]:
    """Create one straight, oriented run of identical physical stock regions.

    This deliberately does not use the general open-rectangle packer: repeated
    stock never wraps to another row/column and is never rotated. The entire
    proposed run is validated before any caller installs it.
    """
    direction = str(direction).strip().casefold()
    if direction not in {"x", "y"}:
        raise ValueError("Repeated-stock stacking direction must be X or Y.")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("Repeated-stock count must be a whole number of at least 1.")
    values = (width, height, start_x, start_y, gap)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Repeated-stock dimensions, origin, and gap must be finite numbers.")
    if width <= 0 or height <= 0:
        raise ValueError("Repeated-stock X and Y sizes must both be greater than zero.")
    if start_x < 0 or start_y < 0:
        raise ValueError("Repeated-stock X and Y origins cannot be negative.")
    if gap < 0:
        raise ValueError("Repeated-stock gap cannot be negative.")

    run_width = width * count + gap * (count - 1) if direction == "x" else width
    run_height = height * count + gap * (count - 1) if direction == "y" else height
    if start_x + run_width > plate.width + tolerance or start_y + run_height > plate.height + tolerance:
        axis = "X" if direction == "x" else "Y"
        available = plate.width - start_x if direction == "x" else plate.height - start_y
        required = run_width if direction == "x" else run_height
        raise ValueError(
            f"This {count}-piece run needs {required:g} along {axis}, but only {available:g} is available from its start origin. "
            "Patchwork does not wrap or rotate repeated stock; reduce the count, size, or gap."
        )

    prefix = str(name_prefix).strip() or "Flat bar"
    stable_prefix = str(id_prefix).strip() if id_prefix is not None else f"patch_repeat_{uuid4().hex}"
    if not stable_prefix:
        stable_prefix = f"patch_repeat_{uuid4().hex}"
    generated = []
    for index in range(count):
        x = start_x + (width + gap) * index if direction == "x" else start_x
        y = start_y + (height + gap) * index if direction == "y" else start_y
        generated.append(PatchworkArea(
            f"{stable_prefix}_{index + 1}", plate.number, x, y, width, height, f"{prefix} {index + 1}",
        ))
    validate_patchwork_areas([*existing, *generated], [plate], tolerance)
    return generated


def patchwork_nesting_plates(
    plates: list[Plate],
    areas: list[PatchworkArea],
    *,
    plate_numbers: set[int] | None = None,
) -> tuple[list[Plate], dict[int, int]]:
    """Expose physical patchwork pieces as isolated nesting envelopes.

    Plates without patchwork retain their full rectangular envelope. Once a
    plate has patchwork areas, only those material rectangles are returned;
    gaps can therefore never become nesting space. Virtual plate numbers are
    deterministic for a given saved area order and map back to the owning
    production plate after nesting.
    """
    ordered_plates = validate_plate_definitions(list(plates))
    validate_patchwork_areas(list(areas), ordered_plates)
    selected_numbers = set(plate_numbers or (plate.number for plate in ordered_plates))
    by_plate: dict[int, list[PatchworkArea]] = {}
    for area in areas:
        by_plate.setdefault(area.plate_number, []).append(area)
    regions: list[Plate] = []
    owner_by_virtual: dict[int, int] = {}
    next_number = 1
    for plate in ordered_plates:
        if plate.number not in selected_numbers:
            continue
        physical_areas = by_plate.get(plate.number, [])
        if physical_areas:
            for area in physical_areas:
                regions.append(Plate(
                    next_number,
                    plate.x + area.x,
                    plate.y + area.y,
                    area.width,
                    area.height,
                    f"Plate {plate.number} / {area.name or area.id}",
                ))
                owner_by_virtual[next_number] = plate.number
                next_number += 1
        else:
            regions.append(Plate(
                next_number, plate.x, plate.y, plate.width, plate.height,
                plate.name or f"Plate {plate.number}",
            ))
            owner_by_virtual[next_number] = plate.number
            next_number += 1
    if not regions:
        raise ValueError("Choose at least one plate for nesting.")
    return regions, owner_by_virtual


def append_plate_definition(
    plates: list[Plate],
    width: float,
    height: float,
    *,
    name: str = "",
    x: float | None = None,
    y: float = 0.0,
) -> list[Plate]:
    """Append a mixed-size plate, defaulting to the right edge of the current layout."""
    next_number = max((plate.number for plate in plates), default=0) + 1
    if x is None:
        x = max((plate.x + plate.width for plate in plates), default=0.0)
    added = Plate(next_number, float(x), float(y), float(width), float(height), name.strip())
    return validate_plate_definitions([*plates, added])


def replace_plate_definition(
    plates: list[Plate],
    updated: Plate,
    *,
    reflow_contiguous: bool = True,
    tolerance: float = 1e-9,
) -> list[Plate]:
    """Replace one plate and optionally carry its touching positive-X row along."""
    ordered = validate_plate_definitions(list(plates), tolerance)
    original_index = next((index for index, plate in enumerate(ordered) if plate.number == updated.number), None)
    if original_index is None:
        raise ValueError(f"Plate {updated.number} does not exist.")
    original = ordered[original_index]
    result = list(ordered)
    result[original_index] = updated
    same_origin = math.isclose(updated.x, original.x, abs_tol=tolerance) and math.isclose(
        updated.y, original.y, abs_tol=tolerance
    )
    width_delta = updated.width - original.width
    if reflow_contiguous and same_origin and not math.isclose(width_delta, 0.0, abs_tol=tolerance):
        previous_original = original
        for index in range(original_index + 1, len(ordered)):
            candidate = ordered[index]
            touches_row = (
                math.isclose(candidate.y, previous_original.y, abs_tol=tolerance)
                and math.isclose(candidate.x, previous_original.x + previous_original.width, abs_tol=tolerance)
            )
            if not touches_row:
                break
            result[index] = Plate(
                candidate.number,
                candidate.x + width_delta,
                candidate.y,
                candidate.width,
                candidate.height,
                candidate.name,
            )
            previous_original = candidate
    return validate_plate_definitions(result, tolerance)


def make_automatic_cutoff_line(
    entities: list[Entity],
    plate: Plate,
    clearance: float,
) -> Line:
    """Create a full-height cutoff line to the right of geometry on one plate."""
    if not math.isfinite(clearance) or clearance < 0:
        raise ValueError("Cutoff clearance must be a finite nonnegative number.")
    plate_min_x, plate_min_y, plate_max_x, plate_max_y = plate.bounds
    relevant_bounds: list[tuple[float, float, float, float]] = []
    for entity in entities:
        # Editable text is a drafting annotation until it is explicitly
        # converted to curves.  Labels must not move an automatic cutoff or
        # otherwise change the usable stock calculation.
        if isinstance(entity, TextEntity):
            continue
        if entity.metadata.get("primitive") == "automatic_sheet_cutoff":
            continue
        bounds = entity_bounds(entity)
        if bounds is None:
            continue
        min_x, min_y, max_x, max_y = bounds
        intersects = not (
            max_x < plate_min_x or min_x > plate_max_x
            or max_y < plate_min_y or min_y > plate_max_y
        )
        if intersects:
            relevant_bounds.append(bounds)
    if not relevant_bounds:
        raise ValueError("No applicable geometry was found on the active plate.")
    rightmost = max(bounds[2] for bounds in relevant_bounds)
    cutoff_x = rightmost + clearance
    if cutoff_x > plate_max_x + 1e-9:
        raise ValueError("There is not enough stock to the right of the geometry for the requested cutoff clearance.")
    return Line(
        start=Point(cutoff_x, plate.y),
        end=Point(cutoff_x, plate.y + plate.height),
        layer="Cutoff",
        metadata={
            "primitive": "automatic_sheet_cutoff",
            "clearance": clearance,
            "plate_number": plate.number,
        },
    )


def install_automatic_cutoff_line(
    project: CamProject,
    plate: Plate,
    cutoff: Line,
    tolerance: float = 0.001,
) -> tuple[Line, int]:
    """Install one active-plate cutoff, replacing prior automatic duplicates.

    The oldest matching cutoff keeps its entity ID so an open-contour
    operation already assigned to it remains attached after recalculation.
    References to accidental duplicates are consolidated onto that same ID.
    """
    plate_min_x, plate_min_y, plate_max_x, plate_max_y = plate.bounds

    def matches(entity: Entity) -> bool:
        if entity.metadata.get("primitive") != "automatic_sheet_cutoff":
            return False
        stored_number = entity.metadata.get("plate_number")
        if stored_number not in (None, ""):
            try:
                return int(stored_number) == plate.number
            except (TypeError, ValueError):
                return False
        bounds = entity_bounds(entity)
        if bounds is None:
            return False
        min_x, min_y, max_x, max_y = bounds
        return (
            min_x >= plate_min_x - tolerance
            and max_x <= plate_max_x + tolerance
            and min_y <= plate_min_y + tolerance
            and max_y >= plate_max_y - tolerance
        )

    previous = [entity for entity in project.entities if matches(entity)]
    cutoff.metadata["plate_number"] = plate.number
    if not previous:
        project.entities.append(cutoff)
        return cutoff, 0

    retained_id = previous[0].id
    replaced_ids = {entity.id for entity in previous}
    cutoff.id = retained_id
    project.entities = [entity for entity in project.entities if entity.id not in replaced_ids]
    project.entities.append(cutoff)

    def remap(values) -> list[str]:
        result: list[str] = []
        for entity_id in values:
            mapped = retained_id if entity_id in replaced_ids else entity_id
            if mapped not in result:
                result.append(mapped)
        return result

    for operation in project.operations:
        operation.entity_ids = remap(operation.entity_ids)
        if hasattr(operation, "island_entity_ids"):
            operation.island_entity_ids = remap(operation.island_entity_ids)
        if hasattr(operation, "entity_sides"):
            sides = {}
            for entity_id, side in operation.entity_sides.items():
                sides[retained_id if entity_id in replaced_ids else entity_id] = side
            operation.entity_sides = sides
    return cutoff, len(previous)


def make_plate_project(project: CamProject, plate: Plate, tolerance: float = 0.001) -> PlateProjectResult:
    """Create a local-X0/Y0 project containing geometry fully inside one plate."""
    plate_min_x, plate_min_y, plate_max_x, plate_max_y = plate.bounds
    contained: list[Entity] = []
    crossing_ids: list[str] = []
    for entity in project.entities:
        bounds = entity_bounds(entity)
        if bounds is None:
            continue
        min_x, min_y, max_x, max_y = bounds
        intersects = not (
            max_x < plate_min_x - tolerance
            or min_x > plate_max_x + tolerance
            or max_y < plate_min_y - tolerance
            or min_y > plate_max_y + tolerance
        )
        inside = (
            min_x >= plate_min_x - tolerance
            and min_y >= plate_min_y - tolerance
            and max_x <= plate_max_x + tolerance
            and max_y <= plate_max_y + tolerance
        )
        if inside:
            contained.append(entity)
        elif intersects and not isinstance(entity, TextEntity):
            # A title or note straddling a plate edge is harmless to the CNC
            # file.  Preserve text which belongs wholly to this plate, but do
            # not turn annotation placement into a blocking output error.
            crossing_ids.append(entity.id)

    output = CamProject.from_dict(project.to_dict())
    contained_ids = {entity.id for entity in contained}
    output.entities = [
        transform_entity(entity, delta_x=-plate.x, delta_y=-plate.y)
        for entity in contained
    ]
    def reference_belongs(reference) -> bool:
        if reference.entity_id:
            return reference.entity_id in contained_ids
        point = reference.fallback
        return (
            plate_min_x - tolerance <= point.x <= plate_max_x + tolerance
            and plate_min_y - tolerance <= point.y <= plate_max_y + tolerance
        )

    output.dimensions = [
        translate_dimension(item, -plate.x, -plate.y)
        for item in output.dimensions
        if (
            reference_belongs(item.start)
            and reference_belongs(item.end)
            and (item.vertex is None or reference_belongs(item.vertex))
        )
    ]
    output.guides = [
        guide.localized(delta_x=-plate.x, delta_y=-plate.y)
        for guide in output.guides
        if (
            plate_min_y - tolerance <= guide.position <= plate_max_y + tolerance
            if guide.orientation == "horizontal"
            else plate_min_x - tolerance <= guide.position <= plate_max_x + tolerance
        )
    ]
    output.keepouts = [
        type(zone)(
            name=zone.name,
            points=[Point(point.x - plate.x, point.y - plate.y) for point in zone.points],
            clearance_z=zone.clearance_z,
            enabled=zone.enabled,
            id=zone.id,
        )
        for zone in output.keepouts
        if zone.points and not (
            max(point.x for point in zone.points) < plate_min_x - tolerance
            or min(point.x for point in zone.points) > plate_max_x + tolerance
            or max(point.y for point in zone.points) < plate_min_y - tolerance
            or min(point.y for point in zone.points) > plate_max_y + tolerance
        )
    ]
    kept_operations = []
    skipped_operations = []
    for operation in output.operations:
        local_entity_ids = [entity_id for entity_id in operation.entity_ids if entity_id in contained_ids]
        if not local_entity_ids:
            skipped_operations.append(operation.id)
            continue
        operation.entity_ids = local_entity_ids
        if hasattr(operation, "island_entity_ids"):
            operation.island_entity_ids = [
                entity_id for entity_id in operation.island_entity_ids if entity_id in contained_ids
            ]
        if hasattr(operation, "entity_sides"):
            operation.entity_sides = {
                entity_id: side for entity_id, side in operation.entity_sides.items()
                if entity_id in contained_ids
            }
        kept_operations.append(operation)
    output.operations = kept_operations
    # Plate extraction intentionally translates/prunes geometry. Rebase only a
    # parent link that was current in the full source job; never bless stale
    # user edits while preparing a local-coordinate output copy.
    from .model import ProfileRestMachiningOperation
    from .operation_links import profile_rest_dependency_status, capture_profile_rest_dependency
    originals = {operation.id: operation for operation in project.operations}
    for operation in output.operations:
        if isinstance(operation, ProfileRestMachiningOperation) and operation.enabled:
            status = profile_rest_dependency_status(project, originals[operation.id])
            if not status.current:
                raise ValueError(status.message)
            capture_profile_rest_dependency(output, operation)
    kept_operation_ids = {operation.id for operation in kept_operations}
    for operation in output.operations:
        operation.depends_on_ids = [value for value in operation.depends_on_ids if value in kept_operation_ids]
    from .hybrid_profile import rebase_plate_groups
    rebase_plate_groups(project,output)
    output.name = f"{project.name} {plate.number:02d}"
    output.metadata["source_plate"] = plate.to_dict()
    output.metadata["plates"] = [Plate(1, 0.0, 0.0, plate.width, plate.height, plate.name).to_dict()]
    output.metadata["sheet_layout"] = {
        "label": "Individual plates",
        "width": plate.width,
        "height": plate.height,
        "columns": 1,
        "rows": 1,
        "origin_x": 0.0,
        "origin_y": 0.0,
        "auto": False,
    }
    patchwork = output.metadata.get("patchwork_areas")
    if isinstance(patchwork, list):
        local_patchwork = []
        for payload in patchwork:
            try:
                area = PatchworkArea.from_dict(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if area.plate_number != plate.number:
                continue
            local_patchwork.append(
                PatchworkArea(area.id, 1, area.x, area.y, area.width, area.height, area.name).to_dict()
            )
        if local_patchwork:
            output.metadata["patchwork_areas"] = local_patchwork
        else:
            output.metadata.pop("patchwork_areas", None)
    return PlateProjectResult(output, tuple(crossing_ids), tuple(skipped_operations))


def plate_output_filename(base_name: str, plate_number: int) -> str:
    from .project_paths import project_basename
    clean = project_basename(base_name.strip()).rstrip()
    if not clean:
        raise ValueError("Output file name cannot be empty.")
    if any(character in clean for character in '<>:"/\\|?*') or clean.endswith((".", " ")):
        raise ValueError("Output file name contains characters Windows cannot use.")
    suffix = f" {plate_number:02d}"
    return f"{clean if clean.endswith(suffix) else clean + suffix}.camjob.json"


def output_base_name(project_name: str, plate_numbers) -> str:
    """Remove a trailing plate suffix when it already identifies this job's plate."""
    clean = project_name.strip()
    for number in plate_numbers:
        suffix = f" {int(number):02d}"
        if clean.endswith(suffix):
            return clean[:-len(suffix)].rstrip()
    return clean


def save_plate_project(result: PlateProjectResult, directory: Path, base_name: str, plate_number: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / plate_output_filename(base_name, plate_number)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    result.project.save(path)
    return path
