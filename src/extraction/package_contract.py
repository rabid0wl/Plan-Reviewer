"""Pre-analysis package contract models and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
import re
import uuid
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.io_json import sha256_file

CONTRACT_VERSION = "preanalysis.v1"
TILE_ID_PATTERN = re.compile(r"^p(?P<page>\d+)_r\d+_c\d+$", re.IGNORECASE)


class ArtifactStatus(str, Enum):
    OK = "ok"
    DRY_RUN = "dry_run"
    SKIPPED_LOW_COHERENCE = "skipped_low_coherence"
    VALIDATION_ERROR = "validation_error"
    RUNTIME_ERROR = "runtime_error"
    MISSING_TEXT_LAYER = "missing_text_layer"


class ValidationResult(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class CompatMode(str, Enum):
    NATIVE = "native"
    LEGACY = "legacy"


def page_number_from_tile_id(tile_id: str) -> int | None:
    """Extract page number from tile id shaped like pNN_rX_cY."""
    match = TILE_ID_PATTERN.match(tile_id.strip())
    if not match:
        return None
    try:
        return int(match.group("page"))
    except ValueError:
        return None


def normalize_status(raw_status: Any) -> ArtifactStatus:
    """Normalize loose status strings into strict contract enum values."""
    status = str(raw_status or "").strip().lower()
    if status == ArtifactStatus.OK.value:
        return ArtifactStatus.OK
    if status == ArtifactStatus.DRY_RUN.value:
        return ArtifactStatus.DRY_RUN
    if status == ArtifactStatus.SKIPPED_LOW_COHERENCE.value:
        return ArtifactStatus.SKIPPED_LOW_COHERENCE
    if status == ArtifactStatus.VALIDATION_ERROR.value:
        return ArtifactStatus.VALIDATION_ERROR
    if status == ArtifactStatus.RUNTIME_ERROR.value:
        return ArtifactStatus.RUNTIME_ERROR
    if status == ArtifactStatus.MISSING_TEXT_LAYER.value:
        return ArtifactStatus.MISSING_TEXT_LAYER
    return ArtifactStatus.RUNTIME_ERROR


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class ContractBaseModel(BaseModel):
    """Base model with strict unknown-field handling."""

    model_config = ConfigDict(extra="forbid")


class PackagePaths(ContractBaseModel):
    tiles_dir: str
    text_layers_dir: str
    out_dir: str


class PackageSettings(ContractBaseModel):
    model: str
    escalation_model: str | None
    allow_low_coherence: bool
    escalation_enabled: bool
    escalation_coherence_threshold: float
    max_concurrency: int


class PackageCounts(ContractBaseModel):
    total_candidates: int = Field(ge=0)
    paired_tiles: int = Field(ge=0)
    missing_text_layers: int = Field(ge=0)
    ok: int = Field(ge=0)
    dry_run: int = Field(ge=0)
    skipped_low_coherence: int = Field(ge=0)
    validation_error: int = Field(ge=0)
    runtime_error: int = Field(ge=0)


class ArtifactPaths(ContractBaseModel):
    tile_path: str | None
    text_layer_path: str | None
    extraction_path: str | None
    meta_path: str | None
    raw_path: str | None


class ArtifactHashes(ContractBaseModel):
    extraction_sha256: str | None
    meta_sha256: str | None
    text_layer_sha256: str | None


class ArtifactMetaSummary(ContractBaseModel):
    sanitized: bool
    coherence_score: float | None
    corrected_fields: list[str]


class PackageArtifact(ContractBaseModel):
    tile_id: str
    page_number: int = Field(ge=0)
    status: ArtifactStatus
    paths: ArtifactPaths
    hashes: ArtifactHashes
    meta_summary: ArtifactMetaSummary

    @field_validator("tile_id")
    @classmethod
    def _validate_tile_id(cls, value: str) -> str:
        token = value.strip()
        if not TILE_ID_PATTERN.match(token):
            raise ValueError("tile_id must match pNN_rX_cY")
        return token


class AnalysisPackage(ContractBaseModel):
    contract_version: str
    run_id: str
    created_at: str
    paths: PackagePaths
    settings: PackageSettings
    counts: PackageCounts
    artifacts: list[PackageArtifact]


class ValidationQuality(ContractBaseModel):
    bad_ratio: float = Field(ge=0.0)
    warn_threshold: float = Field(ge=0.0)
    fail_threshold: float = Field(ge=0.0)
    paired_tiles: int = Field(ge=0)
    sanitized_tiles: int = Field(ge=0)
    skipped_low_coherence: int = Field(ge=0)


class AnalysisValidationReport(ContractBaseModel):
    contract_version: str
    run_id: str
    validated_at: str
    result: ValidationResult
    compat_mode: CompatMode
    critical_errors: list[str]
    warnings: list[str]
    quality: ValidationQuality
    package_manifest_path: str | None
    migrated_manifest_path: str | None


def _safe_hash(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    return sha256_file(path)


def _normalize_tile_id(value: Any, *, fallback_index: int) -> str:
    if isinstance(value, str):
        token = value.strip()
        if TILE_ID_PATTERN.match(token):
            return token
    # Deterministic fallback for malformed rows; validator will still flag mismatch.
    return f"p0_r0_c{fallback_index}"


def _path_from_row(row: Mapping[str, Any], key: str) -> Path | None:
    value = row.get(key)
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def build_analysis_package_from_summary(
    summary: Mapping[str, Any],
    *,
    run_id: str | None = None,
    created_at: str | None = None,
) -> AnalysisPackage:
    """Build a strict manifest model from a batch summary payload."""
    out_dir = Path(str(summary.get("out_dir", "")))
    run_id = run_id or str(summary.get("run_id") or uuid.uuid4())
    created_at = created_at or str(summary.get("completed_at") or datetime.now(UTC).isoformat())

    counts_raw = summary.get("counts", {})
    counts = PackageCounts(
        total_candidates=_to_int(getattr(counts_raw, "get", lambda *_: 0)("total_candidates")),
        paired_tiles=_to_int(getattr(counts_raw, "get", lambda *_: 0)("paired_tiles")),
        missing_text_layers=_to_int(getattr(counts_raw, "get", lambda *_: 0)("missing_text_layers")),
        ok=_to_int(getattr(counts_raw, "get", lambda *_: 0)("ok")),
        dry_run=_to_int(getattr(counts_raw, "get", lambda *_: 0)("dry_run")),
        skipped_low_coherence=_to_int(
            getattr(counts_raw, "get", lambda *_: 0)("skipped_low_coherence")
        ),
        validation_error=_to_int(getattr(counts_raw, "get", lambda *_: 0)("validation_error")),
        runtime_error=_to_int(getattr(counts_raw, "get", lambda *_: 0)("runtime_error")),
    )

    artifacts: list[PackageArtifact] = []
    results = summary.get("results", [])
    if isinstance(results, list):
        for idx, row in enumerate(results, start=1):
            if not isinstance(row, Mapping):
                continue
            meta = row.get("meta", {})
            meta_dict = meta if isinstance(meta, Mapping) else {}

            tile_id = _normalize_tile_id(
                meta_dict.get("tile_id") or row.get("tile_stem"),
                fallback_index=idx,
            )
            page_number = _to_int(meta_dict.get("page_number"), default=-1)
            if page_number < 0:
                page_number = page_number_from_tile_id(tile_id) or 0

            tile_path = _path_from_row(row, "tile_path")
            text_layer_path = (
                _path_from_row(row, "text_layer_path")
                or _path_from_row(row, "expected_text_layer_path")
                or (Path(str(summary.get("text_layers_dir", ""))) / f"{tile_id}.json")
            )
            extraction_path = _path_from_row(row, "out_path") or (out_dir / f"{tile_id}.json")
            meta_path = _path_from_row(row, "meta_path") or (out_dir / f"{tile_id}.json.meta.json")
            raw_path = _path_from_row(row, "raw_out_path") or (out_dir / f"{tile_id}.json.raw.txt")

            artifacts.append(
                PackageArtifact(
                    tile_id=tile_id,
                    page_number=page_number,
                    status=normalize_status(row.get("status")),
                    paths=ArtifactPaths(
                        tile_path=str(tile_path) if tile_path is not None else None,
                        text_layer_path=str(text_layer_path) if text_layer_path is not None else None,
                        extraction_path=str(extraction_path) if extraction_path is not None else None,
                        meta_path=str(meta_path) if meta_path is not None else None,
                        raw_path=str(raw_path) if raw_path is not None else None,
                    ),
                    hashes=ArtifactHashes(
                        extraction_sha256=_safe_hash(extraction_path),
                        meta_sha256=_safe_hash(meta_path),
                        text_layer_sha256=_safe_hash(text_layer_path),
                    ),
                    meta_summary=ArtifactMetaSummary(
                        sanitized=bool(meta_dict.get("sanitized", False)),
                        coherence_score=_to_float(meta_dict.get("coherence_score")),
                        corrected_fields=[
                            str(v)
                            for v in (meta_dict.get("corrected_fields") or [])
                            if isinstance(v, (str, int, float))
                        ],
                    ),
                )
            )

    artifacts.sort(key=lambda artifact: artifact.tile_id)

    return AnalysisPackage(
        contract_version=CONTRACT_VERSION,
        run_id=run_id,
        created_at=created_at,
        paths=PackagePaths(
            tiles_dir=str(summary.get("tiles_dir", "")),
            text_layers_dir=str(summary.get("text_layers_dir", "")),
            out_dir=str(out_dir),
        ),
        settings=PackageSettings(
            model=str(summary.get("model", "")),
            escalation_model=(
                str(summary.get("escalation_model"))
                if summary.get("escalation_model") is not None
                else None
            ),
            allow_low_coherence=bool(summary.get("allow_low_coherence", False)),
            escalation_enabled=bool(summary.get("escalation_enabled", True)),
            escalation_coherence_threshold=float(summary.get("escalation_coherence_threshold", 0.70)),
            max_concurrency=_to_int(summary.get("max_concurrency"), default=1),
        ),
        counts=counts,
        artifacts=artifacts,
    )
