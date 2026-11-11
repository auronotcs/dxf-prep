from __future__ import annotations

"""Vendor-neutral artwork and print-job interchange primitives.

This is deliberately not a RIP or printer driver.  It preserves source bytes,
physical intent, and the two placement spaces needed by sign production while
keeping artwork outside the machinable geometry collection.
"""

from dataclasses import asdict, dataclass, field
from io import BytesIO
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4
import zipfile

if TYPE_CHECKING:
    from .model import CamProject


PRINT_JOB_SCHEMA_ID = "sign-production.print-job"
PRINT_JOB_SCHEMA_VERSION = 1
LOCAL_PACKAGE_ADAPTER_ID = "local.portable-print-package"
SUPPORTED_PRINT_UNITS = {"in", "mm", "cm", "m", "ft", "yd"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _encoded(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode("ascii") if data is not None else None


def _decoded(value: str | None, label: str) -> bytes | None:
    if value is None:
        return None
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{label} is not valid base64 data.") from exc


def _embedded_color_profile(data: bytes) -> tuple[bytes | None, str | None]:
    """Best-effort ICC extraction; failure never alters the original asset."""
    try:
        from PIL import Image, ImageCms
        with Image.open(BytesIO(data)) as image:
            profile = image.info.get("icc_profile")
        if not isinstance(profile, bytes) or not profile:
            return None, None
        name = None
        try:
            cms_profile = ImageCms.ImageCmsProfile(BytesIO(profile))
            name = ImageCms.getProfileDescription(cms_profile).strip() or None
        except Exception:
            pass
        return profile, name
    except Exception:
        return None, None


@dataclass(frozen=True)
class PrintJobIssue:
    severity: str
    code: str
    message: str


@dataclass
class ArtworkAsset:
    original_name: str
    media_type: str
    content_base64: str
    sha256: str
    byte_length: int
    asset_id: str = field(default_factory=lambda: _new_id("artwork"))
    color_profile_name: str | None = None
    color_profile_base64: str | None = None
    color_profile_sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_bytes(
        cls,
        original_name: str,
        data: bytes,
        media_type: str | None = None,
        *,
        color_profile: bytes | None = None,
        color_profile_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ArtworkAsset":
        if not data:
            raise ValueError("Artwork file is empty.")
        if color_profile is None:
            color_profile, discovered_name = _embedded_color_profile(data)
            color_profile_name = color_profile_name or discovered_name
        resolved_media_type = media_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        return cls(
            original_name=Path(original_name).name,
            media_type=resolved_media_type,
            content_base64=_encoded(data) or "",
            sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
            color_profile_name=color_profile_name,
            color_profile_base64=_encoded(color_profile),
            color_profile_sha256=hashlib.sha256(color_profile).hexdigest() if color_profile else None,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_file(cls, path: Path) -> "ArtworkAsset":
        source = Path(path)
        return cls.from_bytes(source.name, source.read_bytes())

    def original_bytes(self) -> bytes:
        data = _decoded(self.content_base64, f"Artwork asset {self.asset_id}") or b""
        if len(data) != self.byte_length:
            raise ValueError(
                f"Artwork asset {self.original_name!r} has {len(data)} bytes; expected {self.byte_length}."
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.sha256:
            raise ValueError(f"Artwork asset {self.original_name!r} failed its SHA-256 integrity check.")
        return data

    def color_profile_bytes(self) -> bytes | None:
        data = _decoded(self.color_profile_base64, f"Color profile for {self.asset_id}")
        if data is not None and self.color_profile_sha256:
            digest = hashlib.sha256(data).hexdigest()
            if digest != self.color_profile_sha256:
                raise ValueError(f"Color profile for {self.original_name!r} failed its SHA-256 integrity check.")
        return data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArtworkAsset":
        return cls(**payload)


@dataclass
class PrintPlacement:
    x: float = 0.0
    y: float = 0.0
    rotation_degrees: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    units: str = "in"
    plate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PrintPlacement":
        return cls(**dict(payload or {}))


@dataclass
class PrintPart:
    name: str
    asset_id: str
    width: float
    height: float
    units: str = "in"
    quantity: int = 1
    material_intent: str = ""
    installation_placement: PrintPlacement = field(default_factory=PrintPlacement)
    machining_sheet_placement: PrintPlacement = field(default_factory=PrintPlacement)
    machining_entity_ids: list[str] = field(default_factory=list)
    bleed: float = 0.0
    panelization: dict[str, Any] = field(default_factory=dict)
    part_id: str = field(default_factory=lambda: _new_id("print_part"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["installation_placement"] = self.installation_placement.to_dict()
        payload["machining_sheet_placement"] = self.machining_sheet_placement.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PrintPart":
        values = dict(payload)
        values["installation_placement"] = PrintPlacement.from_dict(values.get("installation_placement"))
        values["machining_sheet_placement"] = PrintPlacement.from_dict(values.get("machining_sheet_placement"))
        return cls(**values)


@dataclass
class PrintJobData:
    assets: list[ArtworkAsset] = field(default_factory=list)
    parts: list[PrintPart] = field(default_factory=list)
    job_id: str = field(default_factory=lambda: _new_id("print_job"))
    schema_id: str = PRINT_JOB_SCHEMA_ID
    schema_version: int = PRINT_JOB_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "assets": [asset.to_dict() for asset in self.assets],
            "parts": [part.to_dict() for part in self.parts],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PrintJobData":
        schema_id = payload.get("schema_id", PRINT_JOB_SCHEMA_ID)
        version = payload.get("schema_version", PRINT_JOB_SCHEMA_VERSION)
        if schema_id != PRINT_JOB_SCHEMA_ID or version != PRINT_JOB_SCHEMA_VERSION:
            raise ValueError(f"Unsupported print-job schema: {schema_id!r} version {version!r}.")
        return cls(
            assets=[ArtworkAsset.from_dict(item) for item in payload.get("assets", [])],
            parts=[PrintPart.from_dict(item) for item in payload.get("parts", [])],
            job_id=str(payload.get("job_id") or _new_id("print_job")),
            schema_id=schema_id,
            schema_version=version,
            metadata=dict(payload.get("metadata", {})),
        )


def validate_print_job(job: PrintJobData, project: "CamProject | None" = None) -> list[PrintJobIssue]:
    issues: list[PrintJobIssue] = []
    if job.schema_id != PRINT_JOB_SCHEMA_ID or job.schema_version != PRINT_JOB_SCHEMA_VERSION:
        issues.append(PrintJobIssue("error", "print.schema", "Print-job schema is not supported."))

    asset_ids = [asset.asset_id for asset in job.assets]
    if len(asset_ids) != len(set(asset_ids)):
        issues.append(PrintJobIssue("error", "print.asset_duplicate", "Artwork asset IDs must be unique."))
    asset_id_set = set(asset_ids)
    for asset in job.assets:
        try:
            asset.original_bytes()
            asset.color_profile_bytes()
        except ValueError as exc:
            issues.append(PrintJobIssue("error", "print.asset_integrity", str(exc)))

    part_ids = [part.part_id for part in job.parts]
    if len(part_ids) != len(set(part_ids)):
        issues.append(PrintJobIssue("error", "print.part_duplicate", "Print part IDs must be unique."))
    project_entity_ids = {entity.id for entity in project.entities} if project is not None else set()
    for part in job.parts:
        if part.asset_id not in asset_id_set:
            issues.append(PrintJobIssue(
                "error", "print.asset_missing",
                f"Print part {part.name!r} references missing artwork asset {part.asset_id!r}.",
            ))
        if part.width <= 0 or part.height <= 0 or part.quantity < 1:
            issues.append(PrintJobIssue(
                "error", "print.part_dimensions",
                f"Print part {part.name!r} requires positive dimensions and a quantity of at least one.",
            ))
        if part.units not in SUPPORTED_PRINT_UNITS:
            issues.append(PrintJobIssue("error", "print.part_units", f"Print part {part.name!r} uses unsupported units {part.units!r}."))
        for label, placement in (
            ("installation", part.installation_placement),
            ("machining", part.machining_sheet_placement),
        ):
            if placement.units not in SUPPORTED_PRINT_UNITS or placement.scale_x <= 0 or placement.scale_y <= 0:
                issues.append(PrintJobIssue(
                    "error", "print.placement",
                    f"Print part {part.name!r} has an invalid {label} placement.",
                ))
        if project is not None:
            missing = sorted(set(part.machining_entity_ids) - project_entity_ids)
            if missing:
                issues.append(PrintJobIssue(
                    "warning", "print.machining_reference_missing",
                    f"Print part {part.name!r} references {len(missing)} missing machining object(s). Relink or clear those references.",
                ))
    return issues


@dataclass(frozen=True)
class PrintOutputCapabilities:
    portable_package: bool = True
    rasterization: bool = False
    color_management: bool = False
    printer_ready_output: bool = False
    live_device_submission: bool = False
    verified_vendor_import: bool = False


class PrintOutputAdapter(Protocol):
    adapter_id: str
    capabilities: PrintOutputCapabilities

    def export(self, job: PrintJobData, destination: Path, project: "CamProject | None" = None) -> Path:
        ...


def _safe_member_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip(" .")
    return cleaned or "artwork.bin"


class LocalPrintPackageAdapter:
    """Export an inspectable ZIP package; no vendor behavior is implied."""

    adapter_id = LOCAL_PACKAGE_ADAPTER_ID
    capabilities = PrintOutputCapabilities()

    def export(self, job: PrintJobData, destination: Path, project: "CamProject | None" = None) -> Path:
        errors = [issue for issue in validate_print_job(job, project) if issue.severity == "error"]
        if errors:
            raise ValueError("Cannot export print job: " + " ".join(issue.message for issue in errors))

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest_job = job.to_dict()
        asset_members: dict[str, dict[str, str | None]] = {}
        for asset_payload in manifest_job["assets"]:
            asset_payload.pop("content_base64", None)
            asset_payload.pop("color_profile_base64", None)
        for asset in job.assets:
            stem = f"assets/{asset.asset_id}/{_safe_member_name(asset.original_name)}"
            profile_member = f"profiles/{asset.asset_id}.icc" if asset.color_profile_base64 else None
            asset_members[asset.asset_id] = {"artwork": stem, "color_profile": profile_member}
        manifest = {
            "format_id": PRINT_JOB_SCHEMA_ID,
            "format_version": PRINT_JOB_SCHEMA_VERSION,
            "adapter_id": self.adapter_id,
            "capabilities": asdict(self.capabilities),
            "limitations": [
                "Package preserves artwork and job intent; it is not rasterized or color managed.",
                "Package is not printer-ready and is not submitted to a device.",
                "No vendor import compatibility is claimed until a separate adapter is qualified.",
            ],
            "project": {
                "name": project.name if project is not None else "",
                "units": project.units if project is not None else "",
            },
            "asset_members": asset_members,
            "print_job": manifest_job,
        }

        temporary: Path | None = None
        try:
            with NamedTemporaryFile(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
                for asset in job.assets:
                    members = asset_members[asset.asset_id]
                    archive.writestr(str(members["artwork"]), asset.original_bytes())
                    profile = asset.color_profile_bytes()
                    if profile is not None and members["color_profile"]:
                        archive.writestr(str(members["color_profile"]), profile)
            temporary.replace(target)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return target
