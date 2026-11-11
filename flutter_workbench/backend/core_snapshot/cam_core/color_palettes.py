"""User-owned named/manufacturer color palette import.

Palette files are reusable application resources.  Applying a swatch returns a
fully self-contained :class:`ColorValue` snapshot, so a later palette update or
removal cannot change artwork already stored in a project.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile

from .appearance import ColorValue
from .colors import cmyk_to_rgb


PALETTE_SCHEMA_ID = "cam-workbench-color-palette"
PALETTE_SCHEMA_VERSION = 1
SHERWIN_WILLIAMS_SOURCE_URL = (
    "https://www.sherwin-williams.com/home-builders/tools/downloadable-color-palettes"
)
SHERWIN_WILLIAMS_PALETTE_ID = "sherwin-williams-colorsnap"


class PaletteFormatError(ValueError):
    """A user-supplied palette does not satisfy the documented schema."""


def _text(value: Any, field_name: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PaletteFormatError(f"Palette field {field_name!r} is required.")
    return text


def _metadata(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PaletteFormatError(f"Palette field {field_name!r} must be an object.")
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise PaletteFormatError(f"Palette field {field_name!r} must contain JSON values.") from exc


def _preview_from_color(model: str, channels: tuple[float, ...], raw_preview: Any) -> tuple[int, int, int]:
    if raw_preview is not None:
        if not isinstance(raw_preview, (list, tuple)) or len(raw_preview) != 3:
            raise PaletteFormatError("Swatch preview_rgb must contain three integers from 0-255.")
        try:
            preview = tuple(int(value) for value in raw_preview)
        except (TypeError, ValueError) as exc:
            raise PaletteFormatError("Swatch preview_rgb must contain three integers from 0-255.") from exc
        if any(value < 0 or value > 255 for value in preview):
            raise PaletteFormatError("Swatch preview_rgb must contain three integers from 0-255.")
        return preview
    if model == "rgb" and len(channels) == 3:
        return tuple(round(value) for value in channels)
    if model == "cmyk" and len(channels) == 4:
        return tuple(int(value) for value in cmyk_to_rgb(*channels))
    raise PaletteFormatError("A named swatch requires preview_rgb for its on-screen preview.")


@dataclass(frozen=True)
class PaletteSwatch:
    id: str
    name: str
    color: ColorValue
    product_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.id.strip():
            raise PaletteFormatError("Every palette swatch requires an id.")
        if not self.name.strip():
            raise PaletteFormatError("Every palette swatch requires a name.")
        self.color.validate()
        _metadata(self.metadata, f"swatches[{self.id}].metadata")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "product_code": self.product_code,
            "color": self.color.to_dict(),
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class ColorPalette:
    id: str
    name: str
    manufacturer: str
    collection: str
    source: str
    version: str
    swatches: tuple[PaletteSwatch, ...]
    profile: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise PaletteFormatError("Palette id and name are required.")
        if not self.swatches:
            raise PaletteFormatError("A palette must contain at least one swatch.")
        seen: set[str] = set()
        for swatch in self.swatches:
            swatch.validate()
            key = swatch.id.casefold()
            if key in seen:
                raise PaletteFormatError(f"Duplicate swatch id {swatch.id!r} in palette {self.name!r}.")
            seen.add(key)
        _metadata(self.metadata, "metadata")

    def swatch_snapshot(self, swatch: PaletteSwatch | str) -> ColorValue:
        """Return a document-owned snapshot with full palette provenance."""
        if isinstance(swatch, str):
            match = next((item for item in self.swatches if item.id == swatch), None)
            if match is None:
                raise KeyError(swatch)
            swatch = match
        return replace(
            swatch.color,
            name=swatch.name,
            palette_id=self.id,
            swatch_id=swatch.id,
            manufacturer=self.manufacturer,
            collection=self.collection,
            product_code=swatch.product_code,
            source=self.source,
            source_version=self.version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "palette_schema": PALETTE_SCHEMA_ID,
            "palette_schema_version": PALETTE_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "collection": self.collection,
            "source": self.source,
            "version": self.version,
            "profile": self.profile,
            "swatches": [swatch.to_dict() for swatch in self.swatches],
            "metadata": copy.deepcopy(self.metadata),
        }


def palette_from_dict(payload: Any) -> ColorPalette:
    if not isinstance(payload, dict):
        raise PaletteFormatError("A palette file must contain one JSON object.")
    schema = payload.get("palette_schema")
    if schema != PALETTE_SCHEMA_ID:
        raise PaletteFormatError(f"Unsupported palette schema: {schema!r}.")
    version_value = payload.get("palette_schema_version")
    if isinstance(version_value, bool) or not isinstance(version_value, int) or version_value != PALETTE_SCHEMA_VERSION:
        raise PaletteFormatError(f"Unsupported palette schema version: {version_value!r}.")

    palette_id = _text(payload.get("id"), "id", required=True)
    name = _text(payload.get("name"), "name", required=True)
    manufacturer = _text(payload.get("manufacturer"), "manufacturer")
    collection = _text(payload.get("collection"), "collection")
    source = _text(payload.get("source"), "source")
    source_version = _text(payload.get("version"), "version")
    palette_profile = _text(payload.get("profile"), "profile")
    raw_swatches = payload.get("swatches")
    if not isinstance(raw_swatches, list):
        raise PaletteFormatError("Palette field 'swatches' must be a list.")

    swatches: list[PaletteSwatch] = []
    for index, raw in enumerate(raw_swatches):
        if not isinstance(raw, dict):
            raise PaletteFormatError(f"Palette swatch {index + 1} must be an object.")
        swatch_id = _text(raw.get("id"), f"swatches[{index}].id", required=True)
        swatch_name = _text(raw.get("name"), f"swatches[{index}].name", required=True)
        product_code = _text(raw.get("product_code"), f"swatches[{index}].product_code")
        raw_color = raw.get("color")
        if not isinstance(raw_color, dict):
            raise PaletteFormatError(f"Swatch {swatch_name!r} requires a color object.")
        model = _text(raw_color.get("model", "rgb"), "color.model", required=True).casefold()
        raw_channels = raw_color.get("channels", [])
        if not isinstance(raw_channels, (list, tuple)):
            raise PaletteFormatError(f"Swatch {swatch_name!r} channels must be a list.")
        try:
            channels = tuple(float(value) for value in raw_channels)
        except (TypeError, ValueError) as exc:
            raise PaletteFormatError(f"Swatch {swatch_name!r} channels must be numbers.") from exc
        if any(not math.isfinite(value) for value in channels):
            raise PaletteFormatError(f"Swatch {swatch_name!r} channels must be finite numbers.")
        preview = _preview_from_color(model, channels, raw_color.get("preview_rgb"))
        color_payload = {
            "model": model,
            "channels": channels,
            "preview_rgb": preview,
            "profile": _text(raw_color.get("profile", palette_profile), "color.profile"),
            "name": swatch_name,
        }
        try:
            color = ColorValue.from_dict(color_payload)
        except (TypeError, ValueError) as exc:
            raise PaletteFormatError(f"Invalid color for swatch {swatch_name!r}: {exc}") from exc
        swatches.append(PaletteSwatch(
            swatch_id, swatch_name, color, product_code,
            _metadata(raw.get("metadata"), f"swatches[{index}].metadata"),
        ))

    palette = ColorPalette(
        palette_id, name, manufacturer, collection, source, source_version,
        tuple(swatches), palette_profile, _metadata(payload.get("metadata"), "metadata"),
    )
    palette.validate()
    return palette


def _read_json_palette(path: Path) -> ColorPalette:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaletteFormatError(f"Could not read palette file: {exc}") from exc
    return palette_from_dict(payload)


def _xlsx_column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference.upper())
    if match is None:
        raise PaletteFormatError(f"Invalid Excel cell reference: {reference!r}.")
    index = 0
    for character in match.group(1):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _xlsx_text(cell: ET.Element, shared_strings: list[str], namespace: str) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{{{namespace}}}t"))
    value_node = cell.find(f"{{{namespace}}}v")
    raw = "" if value_node is None or value_node.text is None else value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError) as exc:
            raise PaletteFormatError("The Excel palette contains a damaged shared-string reference.") from exc
    return raw


def _xlsx_table(workbook_bytes: bytes) -> list[list[str]]:
    """Read the first populated XLSX worksheet with only the Python standard library."""
    try:
        package = zipfile.ZipFile(io.BytesIO(workbook_bytes))
    except (OSError, zipfile.BadZipFile) as exc:
        raise PaletteFormatError(f"Could not open the Excel palette: {exc}") from exc
    with package:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in package.namelist():
            try:
                shared_root = ET.fromstring(package.read("xl/sharedStrings.xml"))
            except ET.ParseError as exc:
                raise PaletteFormatError("The Excel palette has damaged shared strings.") from exc
            shared_strings = [
                "".join(node.text or "" for node in item.findall(f".//{{{namespace}}}t"))
                for item in shared_root.findall(f"{{{namespace}}}si")
            ]
        worksheet_names = sorted(
            name for name in package.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        if not worksheet_names:
            raise PaletteFormatError("The Excel palette does not contain a worksheet.")
        for worksheet_name in worksheet_names:
            try:
                root = ET.fromstring(package.read(worksheet_name))
            except ET.ParseError as exc:
                raise PaletteFormatError("The Excel palette has a damaged worksheet.") from exc
            result: list[list[str]] = []
            for row in root.findall(f".//{{{namespace}}}sheetData/{{{namespace}}}row"):
                cells: dict[int, str] = {}
                for cell in row.findall(f"{{{namespace}}}c"):
                    reference = cell.attrib.get("r", "")
                    if reference:
                        cells[_xlsx_column_index(reference)] = _xlsx_text(
                            cell, shared_strings, namespace,
                        )
                if cells:
                    result.append([cells.get(index, "") for index in range(max(cells) + 1)])
            if result:
                return result
    raise PaletteFormatError("The Excel palette does not contain any populated rows.")


def _sherwin_source_version(filename: str) -> str:
    match = re.search(r"(?:^|[-_])(\d{2})(\d{2})(\d{2})(?=\.xlsx$)", filename, re.IGNORECASE)
    if match is None:
        return "Not supplied"
    month, day, year = (int(value) for value in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return "Not supplied"
    return f"{2000 + year:04d}-{month:02d}-{day:02d}"


def read_sherwin_williams_palette(path: Path) -> ColorPalette:
    """Import the official ColorSnap Excel download as a local palette.

    The source workbook remains user-owned and is never added to application
    assets. Exact RGB values are retained as unprofiled screen previews; they
    are not represented as physical paint measurements.
    """
    source_path = Path(path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise PaletteFormatError(f"Could not read Sherwin-Williams palette file: {exc}") from exc
    workbook_name = source_path.name
    workbook_bytes = source_bytes
    if source_path.suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(source_bytes)) as outer:
                candidates = [
                    name for name in outer.namelist()
                    if name.casefold().endswith(".xlsx") and "__macosx" not in name.casefold()
                ]
                if len(candidates) != 1:
                    raise PaletteFormatError(
                        "The Sherwin-Williams package must contain exactly one Excel workbook."
                    )
                workbook_name = Path(candidates[0]).name
                workbook_bytes = outer.read(candidates[0])
        except zipfile.BadZipFile as exc:
            raise PaletteFormatError(f"Could not open Sherwin-Williams palette package: {exc}") from exc

    rows = _xlsx_table(workbook_bytes)
    required = ("COLOR #", "COLOR NAME", "LOCATOR #", "RED", "GREEN", "BLUE", "HEX")
    header_index = -1
    columns: dict[str, int] = {}
    for index, row in enumerate(rows):
        candidate = {
            re.sub(r"\s+", " ", value.strip().upper()): column
            for column, value in enumerate(row)
            if value.strip()
        }
        if all(name in candidate for name in required):
            header_index = index
            columns = candidate
            break
    if header_index < 0:
        raise PaletteFormatError(
            "This Excel file is not the expected Sherwin-Williams ColorSnap color workbook."
        )

    swatches: list[PaletteSwatch] = []
    seen: set[str] = set()
    for excel_row, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        def value(column_name: str) -> str:
            column = columns[column_name]
            return row[column].strip() if column < len(row) else ""

        code = value("COLOR #")
        name = value("COLOR NAME")
        if not code and not name:
            continue
        if not code or not name:
            raise PaletteFormatError(f"Sherwin-Williams row {excel_row} is missing a color code or name.")
        key = code.casefold()
        if key in seen:
            raise PaletteFormatError(f"Sherwin-Williams row {excel_row} duplicates color code {code!r}.")
        seen.add(key)
        try:
            rgb = tuple(int(value(channel)) for channel in ("RED", "GREEN", "BLUE"))
        except ValueError as exc:
            raise PaletteFormatError(f"Sherwin-Williams row {excel_row} has invalid RGB values.") from exc
        if any(channel < 0 or channel > 255 for channel in rgb):
            raise PaletteFormatError(f"Sherwin-Williams row {excel_row} has RGB values outside 0-255.")
        source_hex = value("HEX").removeprefix("#").upper()
        calculated_hex = "".join(f"{channel:02X}" for channel in rgb)
        if not re.fullmatch(r"[0-9A-F]{6}", source_hex) or source_hex != calculated_hex:
            raise PaletteFormatError(
                f"Sherwin-Williams row {excel_row} has RGB/hex values that do not agree."
            )
        swatches.append(PaletteSwatch(
            id=code,
            name=name,
            product_code=code,
            color=ColorValue.rgb(*rgb, name=name),
            metadata={"locator_number": value("LOCATOR #"), "source_hex": source_hex},
        ))
    if not swatches:
        raise PaletteFormatError("The Sherwin-Williams workbook did not contain any colors.")

    palette = ColorPalette(
        id=SHERWIN_WILLIAMS_PALETTE_ID,
        name="Sherwin-Williams ColorSnap",
        manufacturer="Sherwin-Williams",
        collection="ColorSnap",
        source=SHERWIN_WILLIAMS_SOURCE_URL,
        version=_sherwin_source_version(workbook_name),
        swatches=tuple(swatches),
        metadata={
            "usage": "private-local-import",
            "redistribution": "not bundled with CAM Workbench",
            "source_file": workbook_name,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest().upper(),
        },
    )
    palette.validate()
    return palette


def read_palette(path: Path) -> ColorPalette:
    """Read a native JSON palette or a supported manufacturer source file."""
    source_path = Path(path)
    if source_path.suffix.casefold() in {".xlsx", ".zip"}:
        return read_sherwin_williams_palette(source_path)
    return _read_json_palette(source_path)


def normalized_palette_records(records: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize settings records without allowing one damaged import to block startup."""
    if not isinstance(records, list):
        return [], (["Stored color palettes were not a list and were ignored."] if records not in (None, []) else [])
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            palette = palette_from_dict(record)
        except PaletteFormatError as exc:
            warnings.append(f"Stored palette {index + 1} was ignored: {exc}")
            continue
        key = palette.id.casefold()
        if key in seen:
            warnings.append(f"Duplicate stored palette id {palette.id!r} was ignored.")
            continue
        seen.add(key)
        normalized.append(palette.to_dict())
    return normalized, warnings


def palettes_from_records(records: Any) -> list[ColorPalette]:
    normalized, _warnings = normalized_palette_records(records)
    return [palette_from_dict(record) for record in normalized]


def merge_palette_records(records: Any, palette: ColorPalette) -> tuple[list[dict[str, Any]], bool]:
    """Insert or replace by stable palette id; return records and replacement flag."""
    palette.validate()
    normalized, _warnings = normalized_palette_records(records)
    key = palette.id.casefold()
    replacement = False
    result: list[dict[str, Any]] = []
    for record in normalized:
        if str(record.get("id", "")).casefold() == key:
            if not replacement:
                result.append(palette.to_dict())
                replacement = True
            continue
        result.append(record)
    if not replacement:
        result.append(palette.to_dict())
    return result, replacement


def iter_palette_swatches(palettes: Iterable[ColorPalette]):
    for palette in palettes:
        for swatch in palette.swatches:
            yield palette, swatch
