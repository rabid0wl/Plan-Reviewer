"""Validate pre-analysis extraction package contracts."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid

from pydantic import ValidationError

from ..utils.io_json import sha256_file, write_json_atomic
from .package_contract import (
    CONTRACT_VERSION,
    AnalysisPackage,
    AnalysisValidationReport,
    CompatMode,
    PackageArtifact,
    ValidationQuality,
    ValidationResult,
    build_analysis_package_from_summary,
)
from .schemas import TileExtraction

logger = logging.getLogger(__name__)

DEFAULT_WARN_THRESHOLD = 0.15
DEFAULT_FAIL_THRESHOLD = 0.30


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return payload


def _existing_path(path_value: str | None) -> Path | None:
    if not isinstance(path_value, str) or not path_value.strip():
        return None
    return Path(path_value)


def _verify_hash(
    *,
    path: Path | None,
    expected_sha256: str | None,
    label: str,
    critical_errors: list[str],
) -> None:
    if not expected_sha256:
        return
    if path is None or not path.exists():
        critical_errors.append(f"{label} hash provided but file is missing: {path}")
        return
    actual = sha256_file(path)
    if actual != expected_sha256:
        critical_errors.append(
            f"{label} hash mismatch for {path}: expected={expected_sha256} actual={actual}"
        )


def _validate_path_consistency(
    artifact: PackageArtifact,
    *,
    verify_hashes: bool,
    critical_errors: list[str],
    warnings: list[str],
) -> None:
    tile_path = _existing_path(artifact.paths.tile_path)
    text_layer_path = _existing_path(artifact.paths.text_layer_path)
    extraction_path = _existing_path(artifact.paths.extraction_path)
    meta_path = _existing_path(artifact.paths.meta_path)
    raw_path = _existing_path(artifact.paths.raw_path)

    status = artifact.status.value

    if tile_path is None or not tile_path.exists():
        critical_errors.append(f"{artifact.tile_id}: missing tile_path for status={status}")

    if text_layer_path is None:
        if status != "missing_text_layer":
            critical_errors.append(f"{artifact.tile_id}: missing text_layer_path for status={status}")
    elif status == "missing_text_layer":
        if text_layer_path.exists():
            warnings.append(f"{artifact.tile_id}: status=missing_text_layer but text layer exists")
    elif not text_layer_path.exists():
        critical_errors.append(f"{artifact.tile_id}: text layer not found at {text_layer_path}")

    if meta_path is None:
        critical_errors.append(f"{artifact.tile_id}: missing meta_path for status={status}")
    elif not meta_path.exists():
        critical_errors.append(f"{artifact.tile_id}: meta file not found at {meta_path}")

    if status == "ok":
        if extraction_path is None or not extraction_path.exists():
            critical_errors.append(f"{artifact.tile_id}: status=ok requires extraction JSON")
    elif status in {"dry_run", "skipped_low_coherence", "missing_text_layer"}:
        if extraction_path is not None and extraction_path.exists():
            warnings.append(
                f"{artifact.tile_id}: status={status} has extraction file present ({extraction_path})"
            )
    else:
        # validation_error/runtime_error may or may not have extraction payloads.
        pass

    if raw_path is None and status == "ok":
        warnings.append(f"{artifact.tile_id}: raw_path missing for status=ok")

    if verify_hashes:
        _verify_hash(
            path=extraction_path,
            expected_sha256=artifact.hashes.extraction_sha256,
            label=f"{artifact.tile_id}.extraction",
            critical_errors=critical_errors,
        )
        _verify_hash(
            path=meta_path,
            expected_sha256=artifact.hashes.meta_sha256,
            label=f"{artifact.tile_id}.meta",
            critical_errors=critical_errors,
        )
        _verify_hash(
            path=text_layer_path,
            expected_sha256=artifact.hashes.text_layer_sha256,
            label=f"{artifact.tile_id}.text_layer",
            critical_errors=critical_errors,
        )


def _validate_ok_extraction_payload(
    artifact: PackageArtifact,
    *,
    critical_errors: list[str],
) -> None:
    if artifact.status.value != "ok":
        return
    extraction_path = _existing_path(artifact.paths.extraction_path)
    if extraction_path is None or not extraction_path.exists():
        return
    try:
        payload = _read_json(extraction_path)
        extraction = TileExtraction.model_validate(payload)
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        critical_errors.append(f"{artifact.tile_id}: extraction payload invalid for contract loader ({exc})")
        return

    if extraction.tile_id != artifact.tile_id:
        critical_errors.append(
            f"{artifact.tile_id}: tile_id mismatch (manifest={artifact.tile_id}, extraction={extraction.tile_id})"
        )
    if extraction.page_number != artifact.page_number:
        critical_errors.append(
            f"{artifact.tile_id}: page_number mismatch "
            f"(manifest={artifact.page_number}, extraction={extraction.page_number})"
        )


def _reconcile_counts(
    package: AnalysisPackage,
    *,
    critical_errors: list[str],
    warnings: list[str],
) -> None:
    artifacts = package.artifacts
    status_counts = {
        "ok": 0,
        "dry_run": 0,
        "skipped_low_coherence": 0,
        "validation_error": 0,
        "runtime_error": 0,
        "missing_text_layer": 0,
    }
    for artifact in artifacts:
        status_counts[artifact.status.value] += 1

    if package.counts.total_candidates != len(artifacts):
        critical_errors.append(
            "counts.total_candidates does not match artifact count "
            f"({package.counts.total_candidates} vs {len(artifacts)})"
        )

    expected_paired = len(artifacts) - status_counts["missing_text_layer"]
    if package.counts.paired_tiles != expected_paired:
        critical_errors.append(
            "counts.paired_tiles mismatch "
            f"({package.counts.paired_tiles} vs {expected_paired})"
        )

    if package.counts.missing_text_layers != status_counts["missing_text_layer"]:
        critical_errors.append(
            "counts.missing_text_layers mismatch "
            f"({package.counts.missing_text_layers} vs {status_counts['missing_text_layer']})"
        )

    if package.counts.ok != status_counts["ok"]:
        warnings.append(f"counts.ok mismatch ({package.counts.ok} vs {status_counts['ok']})")
    if package.counts.dry_run != status_counts["dry_run"]:
        warnings.append(f"counts.dry_run mismatch ({package.counts.dry_run} vs {status_counts['dry_run']})")
    if package.counts.skipped_low_coherence != status_counts["skipped_low_coherence"]:
        warnings.append(
            "counts.skipped_low_coherence mismatch "
            f"({package.counts.skipped_low_coherence} vs {status_counts['skipped_low_coherence']})"
        )
    if package.counts.validation_error != status_counts["validation_error"]:
        warnings.append(
            "counts.validation_error mismatch "
            f"({package.counts.validation_error} vs {status_counts['validation_error']})"
        )
    if package.counts.runtime_error != status_counts["runtime_error"]:
        warnings.append(
            f"counts.runtime_error mismatch ({package.counts.runtime_error} vs {status_counts['runtime_error']})"
        )


def _compute_quality(
    package: AnalysisPackage,
    *,
    warn_threshold: float,
    fail_threshold: float,
) -> ValidationQuality:
    paired_tiles = package.counts.paired_tiles
    if paired_tiles <= 0:
        paired_tiles = max(0, len(package.artifacts) - package.counts.missing_text_layers)

    sanitized_tiles = sum(1 for artifact in package.artifacts if artifact.meta_summary.sanitized)
    skipped_low = sum(
        1 for artifact in package.artifacts if artifact.status.value == "skipped_low_coherence"
    )
    bad_ratio = (
        (sanitized_tiles + skipped_low) / paired_tiles
        if paired_tiles > 0
        else 0.0
    )
    return ValidationQuality(
        bad_ratio=bad_ratio,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        paired_tiles=paired_tiles,
        sanitized_tiles=sanitized_tiles,
        skipped_low_coherence=skipped_low,
    )


def validate_analysis_package(
    package: AnalysisPackage,
    *,
    compat_mode: CompatMode = CompatMode.NATIVE,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
    verify_hashes: bool = True,
    package_manifest_path: Path | None = None,
    migrated_manifest_path: Path | None = None,
) -> AnalysisValidationReport:
    """Validate a parsed package and return a gate report."""
    critical_errors: list[str] = []
    warnings: list[str] = []

    seen_tile_ids: set[str] = set()
    for artifact in package.artifacts:
        if artifact.tile_id in seen_tile_ids:
            critical_errors.append(f"duplicate tile_id in package: {artifact.tile_id}")
            continue
        seen_tile_ids.add(artifact.tile_id)

        _validate_path_consistency(
            artifact,
            verify_hashes=verify_hashes,
            critical_errors=critical_errors,
            warnings=warnings,
        )
        _validate_ok_extraction_payload(artifact, critical_errors=critical_errors)

    _reconcile_counts(package, critical_errors=critical_errors, warnings=warnings)
    quality = _compute_quality(
        package,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )

    if quality.bad_ratio > fail_threshold:
        critical_errors.append(
            "quality gate exceeded fail threshold: "
            f"bad_ratio={quality.bad_ratio:.3f} > {fail_threshold:.3f}"
        )
    elif quality.bad_ratio > warn_threshold:
        warnings.append(
            "quality gate exceeded warn threshold: "
            f"bad_ratio={quality.bad_ratio:.3f} > {warn_threshold:.3f}"
        )

    if critical_errors:
        result = ValidationResult.FAIL
    elif warnings:
        result = ValidationResult.WARN
    else:
        result = ValidationResult.PASS

    return AnalysisValidationReport(
        contract_version=package.contract_version,
        run_id=package.run_id,
        validated_at=datetime.now(UTC).isoformat(),
        result=result,
        compat_mode=compat_mode,
        critical_errors=critical_errors,
        warnings=warnings,
        quality=quality,
        package_manifest_path=str(package_manifest_path) if package_manifest_path else None,
        migrated_manifest_path=str(migrated_manifest_path) if migrated_manifest_path else None,
    )


def _failed_bootstrap_report(
    *,
    run_id: str,
    compat_mode: CompatMode,
    critical_errors: list[str],
    warn_threshold: float,
    fail_threshold: float,
    package_manifest_path: Path | None,
    migrated_manifest_path: Path | None,
) -> AnalysisValidationReport:
    return AnalysisValidationReport(
        contract_version=CONTRACT_VERSION,
        run_id=run_id,
        validated_at=datetime.now(UTC).isoformat(),
        result=ValidationResult.FAIL,
        compat_mode=compat_mode,
        critical_errors=critical_errors,
        warnings=[],
        quality=ValidationQuality(
            bad_ratio=0.0,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
            paired_tiles=0,
            sanitized_tiles=0,
            skipped_low_coherence=0,
        ),
        package_manifest_path=str(package_manifest_path) if package_manifest_path else None,
        migrated_manifest_path=str(migrated_manifest_path) if migrated_manifest_path else None,
    )


def validate_extraction_package(
    *,
    extractions_dir: Path,
    package_manifest_path: Path | None = None,
    validation_report_path: Path | None = None,
    quality_warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    quality_fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
    verify_hashes: bool = True,
    emit_migrated_manifest: bool = True,
    migrated_manifest_path: Path | None = None,
) -> AnalysisValidationReport:
    """
    Validate an extraction directory and write `analysis_validation.json`.

    Uses native `analysis_package.json` when available; otherwise falls back to
    legacy `batch_summary.json` migration mode.
    """
    extractions_dir = extractions_dir.resolve()
    package_path = (package_manifest_path or (extractions_dir / "analysis_package.json")).resolve()
    validation_path = (validation_report_path or (extractions_dir / "analysis_validation.json")).resolve()
    migrated_path = (
        migrated_manifest_path
        or (extractions_dir / "analysis_package.migrated.json")
    ).resolve()

    package: AnalysisPackage | None = None
    compat_mode = CompatMode.NATIVE
    run_id = str(uuid.uuid4())

    if package_path.exists():
        try:
            package = AnalysisPackage.model_validate(_read_json(package_path))
            run_id = package.run_id
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            report = _failed_bootstrap_report(
                run_id=run_id,
                compat_mode=compat_mode,
                critical_errors=[f"malformed/invalid manifest schema: {exc}"],
                warn_threshold=quality_warn_threshold,
                fail_threshold=quality_fail_threshold,
                package_manifest_path=package_path,
                migrated_manifest_path=None,
            )
            write_json_atomic(validation_path, report.model_dump(mode="json"), indent=2, sort_keys=True)
            return report
    else:
        compat_mode = CompatMode.LEGACY
        summary_path = extractions_dir / "batch_summary.json"
        if not summary_path.exists():
            report = _failed_bootstrap_report(
                run_id=run_id,
                compat_mode=compat_mode,
                critical_errors=[
                    f"missing package manifest ({package_path}) and legacy batch summary ({summary_path})"
                ],
                warn_threshold=quality_warn_threshold,
                fail_threshold=quality_fail_threshold,
                package_manifest_path=package_path,
                migrated_manifest_path=None,
            )
            write_json_atomic(validation_path, report.model_dump(mode="json"), indent=2, sort_keys=True)
            return report
        try:
            summary = _read_json(summary_path)
            run_id = str(summary.get("run_id") or run_id)
            package = build_analysis_package_from_summary(summary, run_id=run_id)
            if emit_migrated_manifest:
                write_json_atomic(
                    migrated_path,
                    package.model_dump(mode="json"),
                    indent=2,
                    sort_keys=True,
                )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            report = _failed_bootstrap_report(
                run_id=run_id,
                compat_mode=compat_mode,
                critical_errors=[f"legacy migration failed: {exc}"],
                warn_threshold=quality_warn_threshold,
                fail_threshold=quality_fail_threshold,
                package_manifest_path=package_path,
                migrated_manifest_path=migrated_path if emit_migrated_manifest else None,
            )
            write_json_atomic(validation_path, report.model_dump(mode="json"), indent=2, sort_keys=True)
            return report

    assert package is not None
    report = validate_analysis_package(
        package,
        compat_mode=compat_mode,
        warn_threshold=quality_warn_threshold,
        fail_threshold=quality_fail_threshold,
        verify_hashes=verify_hashes,
        package_manifest_path=package_path if package_path.exists() else None,
        migrated_manifest_path=migrated_path if compat_mode == CompatMode.LEGACY and emit_migrated_manifest else None,
    )
    write_json_atomic(validation_path, report.model_dump(mode="json"), indent=2, sort_keys=True)
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate extraction package contract before analysis.")
    parser.add_argument(
        "--extractions-dir",
        type=Path,
        required=True,
        help="Directory containing extraction outputs.",
    )
    parser.add_argument(
        "--package-manifest",
        type=Path,
        default=None,
        help="Optional path to analysis_package.json.",
    )
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=None,
        help="Output path for analysis_validation.json. Defaults to <extractions-dir>/analysis_validation.json",
    )
    parser.add_argument(
        "--quality-warn-threshold",
        type=float,
        default=DEFAULT_WARN_THRESHOLD,
        help="Warn when (sanitized + skipped_low_coherence) / paired_tiles exceeds this ratio.",
    )
    parser.add_argument(
        "--quality-fail-threshold",
        type=float,
        default=DEFAULT_FAIL_THRESHOLD,
        help="Fail when (sanitized + skipped_low_coherence) / paired_tiles exceeds this ratio.",
    )
    parser.add_argument(
        "--verify-hashes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify SHA-256 hashes declared in package manifest.",
    )
    parser.add_argument(
        "--emit-migrated-manifest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When validating legacy summary-only runs, emit analysis_package.migrated.json.",
    )
    parser.add_argument(
        "--migrated-manifest",
        type=Path,
        default=None,
        help="Optional output path for migrated legacy package manifest.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_arg_parser().parse_args()

    report = validate_extraction_package(
        extractions_dir=args.extractions_dir,
        package_manifest_path=args.package_manifest,
        validation_report_path=args.validation_report,
        quality_warn_threshold=args.quality_warn_threshold,
        quality_fail_threshold=args.quality_fail_threshold,
        verify_hashes=args.verify_hashes,
        emit_migrated_manifest=args.emit_migrated_manifest,
        migrated_manifest_path=args.migrated_manifest,
    )
    logger.info(
        "Validation result=%s critical=%s warnings=%s bad_ratio=%.3f",
        report.result.value,
        len(report.critical_errors),
        len(report.warnings),
        report.quality.bad_ratio,
    )
    raise SystemExit(2 if report.result.value == "fail" else 0)


if __name__ == "__main__":
    main()
