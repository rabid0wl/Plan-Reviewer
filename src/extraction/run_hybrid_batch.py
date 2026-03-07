"""Batch runner for hybrid extraction across multiple tile/text-layer pairs."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from ..utils.io_json import write_json_atomic
from .package_contract import CONTRACT_VERSION, build_analysis_package_from_summary, page_number_from_tile_id
from .prompts import build_hybrid_prompt_split
from .config_models import (
    EscalationConfig,
    ExtractionConfig,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
)
from .run_hybrid import (
    _coerce_int,
    _extract_json_candidate,
    _pre_correct_tile_metadata,
    _sanitize_extraction_payload,
    _STRUCTURED_NONE,
    DEFAULT_MODEL,
    run_hybrid_extraction,
)
from ..config import (
    DEFAULT_ESCALATION_MODEL,
    ESCALATION_COHERENCE_THRESHOLD as DEFAULT_ESCALATION_COHERENCE_THRESHOLD,
)
from .schemas import TileExtraction

try:
    import anthropic as _anthropic_module
except ImportError:  # pragma: no cover
    _anthropic_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _find_pairs(
    *,
    tiles_dir: Path,
    text_layers_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
) -> tuple[list[tuple[Path, Path]], list[dict[str, Any]]]:
    seen: set[Path] = set()
    tiles: list[Path] = []
    for pattern in tile_globs:
        for tile in sorted(tiles_dir.glob(pattern)):
            if tile in seen:
                continue
            seen.add(tile)
            tiles.append(tile)
    tiles.sort()

    if max_tiles is not None:
        tiles = tiles[:max_tiles]

    pairs: list[tuple[Path, Path]] = []
    missing_text_layers: list[dict[str, Any]] = []
    for tile_path in tiles:
        text_layer_path = text_layers_dir / f"{tile_path.stem}.json"
        if not text_layer_path.exists():
            missing_text_layers.append(
                {
                    "tile_stem": tile_path.stem,
                    "tile_path": str(tile_path),
                    "expected_text_layer_path": str(text_layer_path),
                    "status": "missing_text_layer",
                }
            )
            continue
        pairs.append((tile_path, text_layer_path))

    return pairs, missing_text_layers


def _build_page_to_model_tier(manifest_path: Path) -> dict[int, str]:
    """Load manifest JSON and return a mapping of page_number -> model_tier."""
    with manifest_path.open("r", encoding="utf-8") as f:
        entries = json.load(f)
    mapping: dict[int, str] = {}
    for entry in entries:
        page_number = entry.get("page_number")
        model_tier = entry.get("model_tier", "standard")
        if page_number is not None:
            mapping[int(page_number)] = str(model_tier)
    return mapping


def run_batch(
    *,
    tiles_dir: Path,
    text_layers_dir: Path,
    out_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
    config: ExtractionConfig,
    escalation: EscalationConfig,
    allow_low_coherence: bool,
    dry_run: bool,
    no_cache: bool,
    prompt_dir: Path | None,
    fail_fast: bool,
    summary_out: Path,
    max_concurrency: int = 1,
    manifest_path: Path | None = None,
    model_fast: str | None = None,
    model_standard: str | None = None,
    model_premium: str | None = None,
) -> int:
    # Unpack config for local access.
    model = config.model
    api_key = config.api_key
    provider = config.provider
    referer = config.referer
    title = config.title
    temperature = config.temperature
    max_tokens = config.max_tokens
    timeout_sec = config.timeout_sec
    use_json_schema = config.use_json_schema
    escalation_model = escalation.model
    escalation_coherence_threshold = escalation.coherence_threshold
    escalation_enabled = escalation.enabled

    # Build page -> model_tier lookup when a manifest is provided.
    page_to_model_tier: dict[int, str] = {}
    if manifest_path is not None:
        page_to_model_tier = _build_page_to_model_tier(manifest_path)
        logger.info(
            "Loaded manifest model tiers for %s pages from %s",
            len(page_to_model_tier),
            manifest_path,
        )

    # Resolve tier -> model mapping.  Falls back to the base model for any
    # tier whose override was not explicitly supplied.
    tier_to_model: dict[str, str] = {
        "fast": model_fast or model,
        "standard": model_standard or model,
        "premium": model_premium or model,
    }

    pairs, missing_items = _find_pairs(
        tiles_dir=tiles_dir,
        text_layers_dir=text_layers_dir,
        tile_globs=tile_globs,
        max_tiles=max_tiles,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_out.parent.mkdir(parents=True, exist_ok=True)

    if not pairs:
        logger.warning("No tile/text-layer pairs found.")

    started_at = datetime.now(UTC).isoformat()
    run_id = str(uuid.uuid4())
    results: list[dict[str, Any]] = []
    results.extend(missing_items)

    ok_count = 0
    dry_run_count = 0
    skipped_count = 0
    validation_error_count = 0
    runtime_error_count = 0

    total_pairs = len(pairs)
    if total_pairs:
        logger.info(
            "Starting batch: %s tile/text-layer pairs (max_concurrency=%s, fail_fast=%s)",
            total_pairs,
            max(1, int(max_concurrency or 1)),
            fail_fast,
        )

    def _resolve_tile_model(tile_stem: str) -> tuple[str, str]:
        """Return (resolved_model, model_tier) for the given tile stem."""
        if not page_to_model_tier:
            return model, "standard"
        page_num = page_number_from_tile_id(tile_stem)
        if page_num is None:
            return model, "standard"
        tier = page_to_model_tier.get(page_num, "standard")
        return tier_to_model.get(tier, model), tier

    def _process_one(
        idx: int,
        tile_path: Path,
        text_layer_path: Path,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        stem = tile_path.stem
        out_path = out_dir / f"{stem}.json"
        raw_out_path = out_dir / f"{stem}.json.raw.txt"
        meta_out_path = out_dir / f"{stem}.json.meta.json"
        prompt_out_path = (prompt_dir / f"{stem}.prompt.txt") if prompt_dir else None

        tile_model, model_tier = _resolve_tile_model(stem)
        logger.info(
            "(%s/%s) Processing %s  [tier=%s model=%s]",
            idx,
            total_pairs,
            stem,
            model_tier,
            tile_model,
        )

        local_counts = {
            "ok": 0,
            "dry_run": 0,
            "skipped_low_coherence": 0,
            "validation_error": 0,
            "runtime_error": 0,
        }

        try:
            from dataclasses import replace as _dc_replace
            exit_code = run_hybrid_extraction(
                tile_path=tile_path,
                text_layer_path=text_layer_path,
                output_path=out_path,
                raw_output_path=raw_out_path,
                meta_output_path=meta_out_path,
                config=_dc_replace(config, model=tile_model),
                escalation=escalation,
                allow_low_coherence=allow_low_coherence,
                dry_run=dry_run,
                no_cache=no_cache,
                prompt_output_path=prompt_out_path,
            )

            meta_payload: dict[str, Any] = {}
            if meta_out_path.exists():
                try:
                    with meta_out_path.open("r", encoding="utf-8") as f:
                        meta_payload = json.load(f)
                except Exception:
                    meta_payload = {}

            status = str(meta_payload.get("status", "unknown"))
            if status == "ok":
                local_counts["ok"] += 1
            elif status == "dry_run":
                local_counts["dry_run"] += 1
            elif status == "skipped_low_coherence":
                local_counts["skipped_low_coherence"] += 1
            elif status == "validation_error" or exit_code == 2:
                local_counts["validation_error"] += 1
            else:
                if exit_code != 0:
                    local_counts["runtime_error"] += 1

            result_row: dict[str, Any] = {
                "tile_stem": stem,
                "tile_path": str(tile_path),
                "text_layer_path": str(text_layer_path),
                "out_path": str(out_path),
                "meta_path": str(meta_out_path),
                "raw_out_path": str(raw_out_path),
                "status": status,
                "exit_code": exit_code,
                "model_tier": model_tier,
                "model_used": tile_model,
            }
            if meta_payload:
                result_row["meta"] = meta_payload
            return result_row, local_counts
        except Exception as exc:
            logger.exception("Runtime error while processing %s", stem)
            result_row = {
                "tile_stem": stem,
                "tile_path": str(tile_path),
                "text_layer_path": str(text_layer_path),
                "status": "runtime_error",
                "error": str(exc),
                "model_tier": model_tier,
                "model_used": tile_model,
            }
            local_counts["runtime_error"] += 1
            return result_row, local_counts

    if max_concurrency and max_concurrency > 1 and not dry_run:
        # Concurrency is best-effort; fail_fast is honored once a failing
        # result is observed, but in-flight tasks may still complete.
        workers = max(1, int(max_concurrency))
        logger.info("Running batch with ThreadPoolExecutor (workers=%s)", workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_process_one, idx, tile_path, text_layer_path): (idx, tile_path.stem)
                for idx, (tile_path, text_layer_path) in enumerate(pairs, start=1)
            }
            for future in as_completed(future_to_idx):
                idx, tile_stem = future_to_idx[future]
                try:
                    result_row, local_counts = future.result()
                except Exception as exc:
                    logger.exception("Unhandled error in worker for index %s: %s", idx, exc)
                    runtime_error_count += 1
                    results.append(
                        {
                            "tile_stem": tile_stem,
                            "status": "runtime_error",
                            "error": str(exc),
                        }
                    )
                    if fail_fast:
                        logger.error("Stopping early due to fail-fast after worker exception.")
                        break
                    continue

                ok_count += local_counts["ok"]
                dry_run_count += local_counts["dry_run"]
                skipped_count += local_counts["skipped_low_coherence"]
                validation_error_count += local_counts["validation_error"]
                runtime_error_count += local_counts["runtime_error"]
                results.append(result_row)

                if fail_fast and (
                    local_counts["validation_error"] > 0 or local_counts["runtime_error"] > 0
                ):
                    logger.error("Stopping early due to fail-fast after tile %s.", result_row.get("tile_stem"))
                    break
    else:
        for idx, (tile_path, text_layer_path) in enumerate(pairs, start=1):
            result_row, local_counts = _process_one(idx, tile_path, text_layer_path)
            ok_count += local_counts["ok"]
            dry_run_count += local_counts["dry_run"]
            skipped_count += local_counts["skipped_low_coherence"]
            validation_error_count += local_counts["validation_error"]
            runtime_error_count += local_counts["runtime_error"]
            results.append(result_row)

            if fail_fast and (local_counts["validation_error"] > 0 or local_counts["runtime_error"] > 0):
                logger.error("Stopping early due to fail-fast on %s.", result_row.get("tile_stem"))
                break

    completed_at = datetime.now(UTC).isoformat()
    return _build_batch_summary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        tiles_dir=tiles_dir,
        text_layers_dir=text_layers_dir,
        out_dir=out_dir,
        tile_globs=tile_globs,
        max_tiles=max_tiles,
        model=model,
        manifest_path=manifest_path,
        tier_to_model=tier_to_model if page_to_model_tier else None,
        escalation_model=escalation_model,
        escalation_coherence_threshold=escalation_coherence_threshold,
        escalation_enabled=escalation_enabled,
        max_concurrency=max_concurrency,
        dry_run=dry_run,
        no_cache=no_cache,
        use_json_schema=use_json_schema,
        allow_low_coherence=allow_low_coherence,
        provider=provider,
        pairs=pairs,
        missing_items=missing_items,
        ok_count=ok_count,
        dry_run_count=dry_run_count,
        skipped_count=skipped_count,
        validation_error_count=validation_error_count,
        runtime_error_count=runtime_error_count,
        results=results,
        summary_out=summary_out,
    )


def _build_batch_summary(
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    tiles_dir: Path,
    text_layers_dir: Path,
    out_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
    model: str,
    manifest_path: Path | None,
    tier_to_model: dict[str, str] | None,
    escalation_model: str | None,
    escalation_coherence_threshold: float,
    escalation_enabled: bool,
    max_concurrency: int,
    dry_run: bool,
    no_cache: bool,
    use_json_schema: bool,
    allow_low_coherence: bool,
    provider: str,
    pairs: list[tuple[Path, Path]],
    missing_items: list[dict[str, Any]],
    ok_count: int,
    dry_run_count: int,
    skipped_count: int,
    validation_error_count: int,
    runtime_error_count: int,
    results: list[dict[str, Any]],
    summary_out: Path,
) -> int:
    """Build batch summary and analysis package, write them to disk.

    Returns ``0`` on full success, ``2`` when any tile produced a validation
    or runtime error.
    """
    summary: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "tiles_dir": str(tiles_dir),
        "text_layers_dir": str(text_layers_dir),
        "out_dir": str(out_dir),
        "tile_globs": tile_globs,
        "max_tiles": max_tiles,
        "model": model,
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "model_tier_routing": {
            "fast": tier_to_model["fast"],
            "standard": tier_to_model["standard"],
            "premium": tier_to_model["premium"],
        } if tier_to_model else None,
        "escalation_model": escalation_model,
        "escalation_coherence_threshold": escalation_coherence_threshold,
        "escalation_enabled": escalation_enabled,
        "max_concurrency": max(1, int(max_concurrency or 1)),
        "dry_run": dry_run,
        "no_cache": no_cache,
        "use_json_schema": use_json_schema,
        "allow_low_coherence": allow_low_coherence,
        "provider": provider,
        "counts": {
            "total_candidates": len(pairs) + len(missing_items),
            "paired_tiles": len(pairs),
            "missing_text_layers": len(missing_items),
            "ok": ok_count,
            "dry_run": dry_run_count,
            "skipped_low_coherence": skipped_count,
            "validation_error": validation_error_count,
            "runtime_error": runtime_error_count,
        },
        "results": results,
    }
    summary["analysis_package_path"] = str(out_dir / "analysis_package.json")

    with summary_out.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    package_model = build_analysis_package_from_summary(summary, run_id=run_id, created_at=completed_at)
    package_path = out_dir / "analysis_package.json"
    write_json_atomic(package_path, package_model.model_dump(mode="json"), indent=2, sort_keys=True)

    logger.info("Batch summary written: %s", summary_out)
    logger.info("Analysis package written: %s", package_path)
    logger.info(
        "Counts: ok=%s dry_run=%s skipped=%s validation_error=%s runtime_error=%s missing_text_layers=%s",
        ok_count,
        dry_run_count,
        skipped_count,
        validation_error_count,
        runtime_error_count,
        len(missing_items),
    )

    has_failures = (validation_error_count + runtime_error_count) > 0
    return 2 if has_failures else 0


# ---------------------------------------------------------------------------
# Anthropic Batch API path
# ---------------------------------------------------------------------------


def _image_bytes_to_b64(image_bytes: bytes) -> str:
    """Return the raw base64 string for PNG image bytes (no data-URL prefix)."""
    return base64.b64encode(image_bytes).decode("ascii")


def _process_batch_result(
    *,
    tile_id: str,
    tile_path: Path,
    text_layer_path: Path,
    out_dir: Path,
    raw_text: str,
    response_dict: dict[str, Any],
    text_layer: dict[str, Any],
    model: str,
    coherence_score: float,
    is_hybrid_viable: bool,
) -> tuple[dict[str, Any], str]:
    """Parse, validate, and write extraction outputs for one successful batch result.

    Returns a 2-tuple of ``(result_row, status_key)`` where ``status_key`` is one of
    ``"ok"``, ``"validation_error"``, or ``"runtime_error"``.
    """
    out_path = out_dir / f"{tile_id}.json"
    raw_out_path = out_dir / f"{tile_id}.json.raw.txt"
    meta_out_path = out_dir / f"{tile_id}.json.meta.json"

    meta_common: dict[str, Any] = {
        "tile_id": tile_id,
        "coherence_score": coherence_score,
        "is_hybrid_viable": is_hybrid_viable,
        "model": model,
        "attempted_models": [model],
        "escalated": False,
        "escalation_reason": None,
        "_provider": PROVIDER_ANTHROPIC,
    }

    raw_out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_out_path.write_text(raw_text, encoding="utf-8")

    # JSON parse
    payload_obj: Any
    try:
        payload_obj = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            json_candidate = _extract_json_candidate(raw_text)
            payload_obj = json.loads(json_candidate)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error("Batch result JSON parse failed for %s: %s", tile_id, exc)
            meta_out_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_out_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "validation_error",
                        **meta_common,
                        "error": f"JSON parse error: {exc}",
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            return (
                {
                    "tile_stem": tile_id,
                    "tile_path": str(tile_path),
                    "text_layer_path": str(text_layer_path),
                    "out_path": str(out_path),
                    "meta_path": str(meta_out_path),
                    "raw_out_path": str(raw_out_path),
                    "status": "validation_error",
                    "exit_code": 2,
                },
                "validation_error",
            )

    if not isinstance(payload_obj, dict):
        logger.error("Batch result for %s is not a JSON object.", tile_id)
        meta_out_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_out_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "validation_error",
                    **meta_common,
                    "error": "Top-level JSON output is not an object.",
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return (
            {
                "tile_stem": tile_id,
                "tile_path": str(tile_path),
                "text_layer_path": str(text_layer_path),
                "out_path": str(out_path),
                "meta_path": str(meta_out_path),
                "raw_out_path": str(raw_out_path),
                "status": "validation_error",
                "exit_code": 2,
            },
            "validation_error",
        )

    # Pre-correct tile metadata then validate with Pydantic
    sanitized = False
    dropped_invalid_counts: dict[str, int] = {
        "structures": 0,
        "inverts": 0,
        "pipes": 0,
        "callouts": 0,
    }
    _pre_correct_tile_metadata(payload_obj, text_layer)
    try:
        extraction = TileExtraction.model_validate(payload_obj)
    except ValidationError as exc:
        sanitized_payload, dropped_invalid_counts = _sanitize_extraction_payload(payload_obj)
        try:
            extraction = TileExtraction.model_validate(sanitized_payload)
            sanitized = True
            logger.warning(
                "Validation recovered for %s by dropping invalid items: %s",
                tile_id,
                dropped_invalid_counts,
            )
        except ValidationError:
            logger.error("Schema validation failed for %s", tile_id)
            meta_out_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_out_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "validation_error",
                        **meta_common,
                        "error": str(exc),
                        "dropped_invalid_counts": dropped_invalid_counts,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            return (
                {
                    "tile_stem": tile_id,
                    "tile_path": str(tile_path),
                    "text_layer_path": str(text_layer_path),
                    "out_path": str(out_path),
                    "meta_path": str(meta_out_path),
                    "raw_out_path": str(raw_out_path),
                    "status": "validation_error",
                    "exit_code": 2,
                },
                "validation_error",
            )

    # Correct mismatched tile_id / page_number
    expected_tile_id = str(text_layer.get("tile_id", extraction.tile_id))
    expected_page_number = _coerce_int(text_layer.get("page_number"))
    if expected_page_number is None:
        expected_page_number = page_number_from_tile_id(expected_tile_id)
    if expected_page_number is None:
        expected_page_number = extraction.page_number
    corrected_fields: dict[str, Any] = {}
    if extraction.tile_id != expected_tile_id:
        corrected_fields["tile_id"] = expected_tile_id
    if extraction.page_number != expected_page_number:
        corrected_fields["page_number"] = expected_page_number
    if corrected_fields:
        logger.warning(
            "Batch result had mismatched tile metadata; corrected fields: %s",
            ", ".join(sorted(corrected_fields.keys())),
        )
        extraction = extraction.model_copy(update=corrected_fields)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(extraction.model_dump(), f, indent=2, ensure_ascii=False)

    usage = response_dict.get("usage", {})
    meta_out_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_out_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "status": "ok",
                **meta_common,
                "structures_count": len(extraction.structures),
                "pipes_count": len(extraction.pipes),
                "callouts_count": len(extraction.callouts),
                "usage": usage,
                "response_format_type": _STRUCTURED_NONE,
                "corrected_fields": sorted(corrected_fields.keys()),
                "sanitized": sanitized,
                "dropped_invalid_counts": dropped_invalid_counts,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        "Batch result processed for %s: structures=%s pipes=%s callouts=%s",
        extraction.tile_id,
        len(extraction.structures),
        len(extraction.pipes),
        len(extraction.callouts),
    )
    result_row: dict[str, Any] = {
        "tile_stem": tile_id,
        "tile_path": str(tile_path),
        "text_layer_path": str(text_layer_path),
        "out_path": str(out_path),
        "meta_path": str(meta_out_path),
        "raw_out_path": str(raw_out_path),
        "status": "ok",
        "exit_code": 0,
        "meta": {
            "status": "ok",
            "tile_id": extraction.tile_id,
            "page_number": extraction.page_number,
            "sanitized": sanitized,
            "coherence_score": coherence_score,
            "corrected_fields": sorted(corrected_fields.keys()),
        },
    }
    return result_row, "ok"


def _write_batch_api_summary(
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    tiles_dir: Path,
    text_layers_dir: Path,
    out_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
    model: str,
    pairs: list[tuple[Path, Path]],
    missing_items: list[dict[str, Any]],
    ok_count: int,
    skipped_count: int,
    validation_error_count: int,
    runtime_error_count: int,
    results: list[dict[str, Any]],
    allow_low_coherence: bool,
    summary_out: Path,
    batch_id: str | None = None,
) -> None:
    """Write batch_summary.json and analysis_package.json for the batch-API path."""
    summary: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "tiles_dir": str(tiles_dir),
        "text_layers_dir": str(text_layers_dir),
        "out_dir": str(out_dir),
        "tile_globs": tile_globs,
        "max_tiles": max_tiles,
        "model": model,
        "escalation_model": None,
        "escalation_coherence_threshold": DEFAULT_ESCALATION_COHERENCE_THRESHOLD,
        "escalation_enabled": False,
        "max_concurrency": 1,
        "dry_run": False,
        "no_cache": False,
        "use_json_schema": False,
        "allow_low_coherence": allow_low_coherence,
        "provider": PROVIDER_ANTHROPIC,
        "batch_api": True,
        "counts": {
            "total_candidates": len(pairs) + len(missing_items),
            "paired_tiles": len(pairs),
            "missing_text_layers": len(missing_items),
            "ok": ok_count,
            "dry_run": 0,
            "skipped_low_coherence": skipped_count,
            "validation_error": validation_error_count,
            "runtime_error": runtime_error_count,
        },
        "results": results,
    }
    if batch_id is not None:
        summary["batch_id"] = batch_id
    summary["analysis_package_path"] = str(out_dir / "analysis_package.json")

    with summary_out.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    package_model = build_analysis_package_from_summary(
        summary, run_id=run_id, created_at=completed_at
    )
    package_path = out_dir / "analysis_package.json"
    write_json_atomic(
        package_path, package_model.model_dump(mode="json"), indent=2, sort_keys=True
    )

    logger.info("Batch summary written: %s", summary_out)
    logger.info("Analysis package written: %s", package_path)


def run_batch_api(
    *,
    tiles_dir: Path,
    text_layers_dir: Path,
    out_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
    model: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    allow_low_coherence: bool,
    summary_out: Path,
    poll_interval: int = 30,
) -> int:
    """Submit all tile extraction requests as a single Anthropic Message Batch.

    Enabled with ``--batch-api`` (requires ``--provider anthropic``).  Requests
    are collected from all valid tile/text-layer pairs, submitted in one batch
    API call, polled every ``poll_interval`` seconds until
    ``processing_status == "ended"``, then post-processed with the same JSON
    parsing, sanitization, and validation logic used by the synchronous path.

    Returns:
        ``0`` on full success, ``2`` when any tile produced a validation or
        runtime error.
    """
    if _anthropic_module is None:
        raise ImportError(
            "The 'anthropic' package is required for --batch-api. "
            "Install it with: pip install 'anthropic>=0.40.0'"
        )

    pairs, missing_items = _find_pairs(
        tiles_dir=tiles_dir,
        text_layers_dir=text_layers_dir,
        tile_globs=tile_globs,
        max_tiles=max_tiles,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    summary_out.parent.mkdir(parents=True, exist_ok=True)

    if not pairs:
        logger.warning("No tile/text-layer pairs found.")

    started_at = datetime.now(UTC).isoformat()
    run_id = str(uuid.uuid4())
    results: list[dict[str, Any]] = []
    results.extend(missing_items)

    ok_count = 0
    skipped_count = 0
    validation_error_count = 0
    runtime_error_count = 0

    # ------------------------------------------------------------------ #
    # Collection phase: build batch request objects                        #
    # ------------------------------------------------------------------ #
    batch_requests: list[dict[str, Any]] = []
    # Keyed by tile_id so results can be quickly looked up during collect phase.
    tile_meta: dict[str, dict[str, Any]] = {}

    logger.info(
        "Collecting batch requests for %s tile/text-layer pairs.", len(pairs)
    )

    for tile_path, text_layer_path in pairs:
        stem = tile_path.stem
        try:
            with text_layer_path.open("r", encoding="utf-8") as f:
                text_layer: dict[str, Any] = json.load(f)
        except Exception as exc:
            logger.error("Failed to load text layer for %s: %s", stem, exc)
            results.append(
                {
                    "tile_stem": stem,
                    "tile_path": str(tile_path),
                    "text_layer_path": str(text_layer_path),
                    "status": "runtime_error",
                    "error": f"text layer load error: {exc}",
                }
            )
            runtime_error_count += 1
            continue

        coherence_score = float(text_layer.get("coherence_score", 0.0))
        is_hybrid_viable = bool(text_layer.get("is_hybrid_viable", True))
        tile_id = str(text_layer.get("tile_id", stem))

        if not is_hybrid_viable and not allow_low_coherence:
            logger.warning(
                "Skipping tile %s: coherence %.3f below viability threshold.",
                tile_id,
                coherence_score,
            )
            meta_out_path = out_dir / f"{stem}.json.meta.json"
            meta_out_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_out_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "skipped_low_coherence",
                        "tile_id": tile_id,
                        "coherence_score": coherence_score,
                        "is_hybrid_viable": is_hybrid_viable,
                        "model": model,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            results.append(
                {
                    "tile_stem": stem,
                    "tile_path": str(tile_path),
                    "text_layer_path": str(text_layer_path),
                    "out_path": str(out_dir / f"{stem}.json"),
                    "meta_path": str(meta_out_path),
                    "raw_out_path": str(out_dir / f"{stem}.json.raw.txt"),
                    "status": "skipped_low_coherence",
                    "exit_code": 0,
                }
            )
            skipped_count += 1
            continue

        try:
            image_bytes = tile_path.read_bytes()
        except Exception as exc:
            logger.error("Failed to read image for %s: %s", stem, exc)
            results.append(
                {
                    "tile_stem": stem,
                    "tile_path": str(tile_path),
                    "text_layer_path": str(text_layer_path),
                    "status": "runtime_error",
                    "error": f"image read error: {exc}",
                }
            )
            runtime_error_count += 1
            continue

        system_prompt, user_prompt = build_hybrid_prompt_split(text_layer)
        b64_data = _image_bytes_to_b64(image_bytes)

        batch_requests.append(
            {
                "custom_id": tile_id,
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64_data,
                                    },
                                },
                                {"type": "text", "text": user_prompt},
                            ],
                        }
                    ],
                },
            }
        )

        tile_meta[tile_id] = {
            "tile_path": tile_path,
            "text_layer_path": text_layer_path,
            "text_layer": text_layer,
            "stem": stem,
            "coherence_score": coherence_score,
            "is_hybrid_viable": is_hybrid_viable,
        }

    logger.info(
        "Collected %s batch requests (%s skipped, %s pre-submit errors).",
        len(batch_requests),
        skipped_count,
        runtime_error_count,
    )

    if not batch_requests:
        logger.warning("No requests to submit. Writing empty summary.")
        completed_at = datetime.now(UTC).isoformat()
        _write_batch_api_summary(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            tiles_dir=tiles_dir,
            text_layers_dir=text_layers_dir,
            out_dir=out_dir,
            tile_globs=tile_globs,
            max_tiles=max_tiles,
            model=model,
            pairs=pairs,
            missing_items=missing_items,
            ok_count=ok_count,
            skipped_count=skipped_count,
            validation_error_count=validation_error_count,
            runtime_error_count=runtime_error_count,
            results=results,
            allow_low_coherence=allow_low_coherence,
            summary_out=summary_out,
        )
        return 2 if (validation_error_count + runtime_error_count) > 0 else 0

    # ------------------------------------------------------------------ #
    # Submit phase                                                         #
    # ------------------------------------------------------------------ #
    client = _anthropic_module.Anthropic(api_key=api_key)
    logger.info(
        "Submitting batch of %s requests to Anthropic Batch API.", len(batch_requests)
    )
    batch = client.messages.batches.create(requests=batch_requests)  # type: ignore[arg-type]
    batch_id = batch.id
    logger.info("Batch submitted. batch_id=%s", batch_id)

    # ------------------------------------------------------------------ #
    # Poll phase                                                           #
    # ------------------------------------------------------------------ #
    while True:
        status_obj = client.messages.batches.retrieve(batch_id)
        processing_status = status_obj.processing_status
        req_counts = status_obj.request_counts
        logger.info(
            "Batch %s status=%s | processing=%s succeeded=%s errored=%s "
            "canceled=%s expired=%s",
            batch_id,
            processing_status,
            getattr(req_counts, "processing", "?"),
            getattr(req_counts, "succeeded", "?"),
            getattr(req_counts, "errored", "?"),
            getattr(req_counts, "canceled", "?"),
            getattr(req_counts, "expired", "?"),
        )
        if processing_status == "ended":
            break
        time.sleep(poll_interval)

    # ------------------------------------------------------------------ #
    # Collect phase                                                        #
    # ------------------------------------------------------------------ #
    logger.info("Batch %s ended. Collecting results.", batch_id)
    processed_tile_ids: set[str] = set()

    for result in client.messages.batches.results(batch_id):
        custom_id: str = result.custom_id
        processed_tile_ids.add(custom_id)

        meta_info = tile_meta.get(custom_id)
        if meta_info is None:
            logger.warning(
                "Received result for unknown custom_id %s; skipping.", custom_id
            )
            runtime_error_count += 1
            results.append(
                {
                    "tile_stem": custom_id,
                    "status": "runtime_error",
                    "error": "custom_id not found in tile_meta",
                }
            )
            continue

        tile_path_r: Path = meta_info["tile_path"]
        text_layer_path_r: Path = meta_info["text_layer_path"]
        text_layer_r: dict[str, Any] = meta_info["text_layer"]
        coherence_score_r: float = meta_info["coherence_score"]
        is_hybrid_viable_r: bool = meta_info["is_hybrid_viable"]

        result_type: str = result.result.type
        if result_type != "succeeded":
            err_detail = str(getattr(result.result, "error", result_type))
            logger.error(
                "Batch result for %s: type=%s error=%s",
                custom_id,
                result_type,
                err_detail,
            )
            meta_out_path = out_dir / f"{custom_id}.json.meta.json"
            meta_out_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_out_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "runtime_error",
                        "tile_id": custom_id,
                        "model": model,
                        "batch_result_type": result_type,
                        "error": err_detail,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            results.append(
                {
                    "tile_stem": custom_id,
                    "tile_path": str(tile_path_r),
                    "text_layer_path": str(text_layer_path_r),
                    "meta_path": str(meta_out_path),
                    "status": "runtime_error",
                    "exit_code": 1,
                    "error": err_detail,
                }
            )
            runtime_error_count += 1
            continue

        # Extract raw text from the message content blocks
        message = result.result.message
        raw_text_parts: list[str] = []
        for block in message.content:
            if hasattr(block, "text"):
                raw_text_parts.append(block.text)
        raw_text = "\n".join(raw_text_parts).strip()

        # Build a response_dict for _process_batch_result
        usage_obj = message.usage
        response_dict: dict[str, Any] = {
            "id": message.id,
            "model": message.model,
            "stop_reason": message.stop_reason,
            "usage": {
                "prompt_tokens": usage_obj.input_tokens,
                "completion_tokens": usage_obj.output_tokens,
                "total_tokens": usage_obj.input_tokens + usage_obj.output_tokens,
            },
            "_response_format_type": _STRUCTURED_NONE,
            "_provider": PROVIDER_ANTHROPIC,
        }
        if hasattr(usage_obj, "cache_creation_input_tokens"):
            response_dict["usage"]["cache_creation_input_tokens"] = (
                usage_obj.cache_creation_input_tokens
            )
        if hasattr(usage_obj, "cache_read_input_tokens"):
            response_dict["usage"]["cache_read_input_tokens"] = (
                usage_obj.cache_read_input_tokens
            )

        try:
            result_row, status_key = _process_batch_result(
                tile_id=custom_id,
                tile_path=tile_path_r,
                text_layer_path=text_layer_path_r,
                out_dir=out_dir,
                raw_text=raw_text,
                response_dict=response_dict,
                text_layer=text_layer_r,
                model=model,
                coherence_score=coherence_score_r,
                is_hybrid_viable=is_hybrid_viable_r,
            )
        except Exception as exc:
            logger.exception(
                "Unexpected error post-processing batch result for %s", custom_id
            )
            results.append(
                {
                    "tile_stem": custom_id,
                    "tile_path": str(tile_path_r),
                    "text_layer_path": str(text_layer_path_r),
                    "status": "runtime_error",
                    "error": str(exc),
                }
            )
            runtime_error_count += 1
            continue

        if status_key == "ok":
            ok_count += 1
        elif status_key == "validation_error":
            validation_error_count += 1
        else:
            runtime_error_count += 1
        results.append(result_row)

    # Detect any submitted tiles absent from results
    for tile_id, meta_info in tile_meta.items():
        if tile_id not in processed_tile_ids:
            logger.error(
                "Tile %s was submitted but not returned in batch results.", tile_id
            )
            results.append(
                {
                    "tile_stem": meta_info["stem"],
                    "tile_path": str(meta_info["tile_path"]),
                    "text_layer_path": str(meta_info["text_layer_path"]),
                    "status": "runtime_error",
                    "error": "tile not returned in batch results",
                }
            )
            runtime_error_count += 1

    # ------------------------------------------------------------------ #
    # Write phase                                                          #
    # ------------------------------------------------------------------ #
    completed_at = datetime.now(UTC).isoformat()
    _write_batch_api_summary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        tiles_dir=tiles_dir,
        text_layers_dir=text_layers_dir,
        out_dir=out_dir,
        tile_globs=tile_globs,
        max_tiles=max_tiles,
        model=model,
        pairs=pairs,
        missing_items=missing_items,
        ok_count=ok_count,
        skipped_count=skipped_count,
        validation_error_count=validation_error_count,
        runtime_error_count=runtime_error_count,
        results=results,
        allow_low_coherence=allow_low_coherence,
        summary_out=summary_out,
        batch_id=batch_id,
    )

    logger.info(
        "Batch API run complete: ok=%s skipped=%s validation_error=%s "
        "runtime_error=%s missing_text_layers=%s",
        ok_count,
        skipped_count,
        validation_error_count,
        runtime_error_count,
        len(missing_items),
    )
    return 2 if (validation_error_count + runtime_error_count) > 0 else 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run hybrid extraction in batch for matching tile/text-layer files."
    )
    parser.add_argument("--tiles-dir", type=Path, required=True, help="Directory containing tile PNGs.")
    parser.add_argument(
        "--text-layers-dir",
        type=Path,
        required=True,
        help="Directory containing tile text layer JSON files.",
    )
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for extraction outputs.")
    parser.add_argument(
        "--tile-glob",
        type=str,
        action="append",
        default=None,
        help="Glob pattern for selecting tiles in --tiles-dir. Repeat flag for multiple patterns.",
    )
    parser.add_argument(
        "--max-tiles",
        type=int,
        default=None,
        help="Optional max number of tiles to process (sorted by filename).",
    )
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="OpenRouter model id.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Optional path to manifest JSON produced by the intake pipeline. "
            "When provided, each tile's page number is looked up in the manifest "
            "to select the appropriate model tier (fast/standard/premium)."
        ),
    )
    parser.add_argument(
        "--model-fast",
        type=str,
        default="google/gemini-2.5-flash-lite",
        help=(
            "Model id to use for 'fast' tier sheets "
            "(cover, notes, demolition, erosion, other, signing_striping). "
            "Only applies when --manifest is provided."
        ),
    )
    parser.add_argument(
        "--model-standard",
        type=str,
        default=None,
        help=(
            "Model id to use for 'standard' tier sheets "
            "(plan_view, profile, grading). "
            "Defaults to --model when not specified. "
            "Only applies when --manifest is provided."
        ),
    )
    parser.add_argument(
        "--model-premium",
        type=str,
        default=None,
        help=(
            "Model id to use for 'premium' tier sheets "
            "(detail, or any sheet with coherence < 0.50). "
            "Defaults to --model when not specified. "
            "Only applies when --manifest is provided."
        ),
    )
    parser.add_argument(
        "--escalation-model",
        type=str,
        default=DEFAULT_ESCALATION_MODEL,
        help="Fallback model id used when low confidence or extraction issues are detected.",
    )
    parser.add_argument(
        "--escalation-coherence-threshold",
        type=float,
        default=DEFAULT_ESCALATION_COHERENCE_THRESHOLD,
        help="Escalate to fallback model when text-layer coherence is below this threshold.",
    )
    parser.add_argument(
        "--escalation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable automatic model escalation.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=[PROVIDER_OPENROUTER, PROVIDER_ANTHROPIC],
        default=PROVIDER_OPENROUTER,
        help=(
            "API provider to use for vision extraction. "
            "'openrouter' uses the OpenRouter HTTP API (default, backward compatible). "
            "'anthropic' calls the Anthropic SDK directly with prompt caching on the system message."
        ),
    )
    parser.add_argument(
        "--api-key-env",
        type=str,
        default=None,
        help=(
            "Environment variable name holding the API key. "
            "Defaults to ANTHROPIC_API_KEY when --provider=anthropic, "
            "OPENROUTER_API_KEY otherwise."
        ),
    )
    parser.add_argument("--temperature", type=float, default=0.0, help="Model temperature.")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Model max tokens.")
    parser.add_argument("--timeout-sec", type=int, default=120, help="HTTP timeout seconds.")
    parser.add_argument(
        "--allow-low-coherence",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow extraction on low-coherence tiles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompts/metadata only; do not call the model API.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable hash-based cache and force API calls for every tile.",
    )
    parser.add_argument(
        "--use-json-schema",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use response_format=json_schema with strict mode before json_object fallback.",
    )
    parser.add_argument(
        "--referer",
        type=str,
        default="https://planreviewer.local",
        help="HTTP-Referer header for OpenRouter request.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Plan Reviewer Hybrid Extraction Batch",
        help="X-Title header for OpenRouter request.",
    )
    parser.add_argument(
        "--prompt-dir",
        type=Path,
        default=None,
        help="Optional directory to save generated prompts per tile.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop batch on first validation/runtime failure.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="Path to batch summary JSON. Defaults to <out-dir>/batch_summary.json",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Optional max number of tiles to process in parallel (>=1). Defaults to serial execution.",
    )
    parser.add_argument(
        "--batch-api",
        action="store_true",
        default=False,
        help=(
            "Submit all tile requests as a single Anthropic Message Batch for a 50%% cost discount. "
            "Requires --provider anthropic. Incompatible with --dry-run."
        ),
    )
    parser.add_argument(
        "--batch-poll-interval",
        type=int,
        default=30,
        help="Seconds between Anthropic Batch API status polls. Only used with --batch-api.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv()
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Validate --batch-api constraints before touching the API key
    if args.batch_api:
        if args.provider != PROVIDER_ANTHROPIC:
            raise SystemExit(
                "--batch-api requires --provider anthropic. "
                f"Got: --provider {args.provider}"
            )
        if args.dry_run:
            raise SystemExit("--batch-api is incompatible with --dry-run.")

    api_key_env = args.api_key_env or (
        "ANTHROPIC_API_KEY" if args.provider == PROVIDER_ANTHROPIC else "OPENROUTER_API_KEY"
    )
    api_key = os.getenv(api_key_env, "")
    if not args.dry_run and not api_key:
        raise SystemExit(
            f"Missing API key in env var '{api_key_env}'. "
            "Set it in environment or .env before running."
        )

    summary_out = args.summary_out or (args.out_dir / "batch_summary.json")
    tile_globs = args.tile_glob or ["*.png"]

    if args.batch_api:
        exit_code = run_batch_api(
            tiles_dir=args.tiles_dir,
            text_layers_dir=args.text_layers_dir,
            out_dir=args.out_dir,
            tile_globs=tile_globs,
            max_tiles=args.max_tiles,
            model=args.model,
            api_key=api_key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            allow_low_coherence=args.allow_low_coherence,
            summary_out=summary_out,
            poll_interval=args.batch_poll_interval,
        )
    else:
        _ext_config = ExtractionConfig(
            model=args.model,
            api_key=api_key,
            provider=args.provider,
            referer=args.referer,
            title=args.title,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_sec=args.timeout_sec,
            use_json_schema=args.use_json_schema,
        )
        _esc_config = EscalationConfig(
            enabled=args.escalation,
            model=args.escalation_model,
            coherence_threshold=args.escalation_coherence_threshold,
        )
        exit_code = run_batch(
            tiles_dir=args.tiles_dir,
            text_layers_dir=args.text_layers_dir,
            out_dir=args.out_dir,
            tile_globs=tile_globs,
            max_tiles=args.max_tiles,
            config=_ext_config,
            escalation=_esc_config,
            allow_low_coherence=args.allow_low_coherence,
            dry_run=args.dry_run,
            no_cache=args.no_cache,
            prompt_dir=args.prompt_dir,
            fail_fast=args.fail_fast,
            summary_out=summary_out,
            max_concurrency=args.max_concurrency,
            manifest_path=args.manifest,
            model_fast=args.model_fast,
            model_standard=args.model_standard,
            model_premium=args.model_premium,
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
