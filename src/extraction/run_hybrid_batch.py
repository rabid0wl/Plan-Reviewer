"""Batch runner for hybrid extraction across multiple tile/text-layer pairs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..utils.io_json import write_json_atomic
from .package_contract import CONTRACT_VERSION, build_analysis_package_from_summary
from .run_hybrid import (
    DEFAULT_ESCALATION_COHERENCE_THRESHOLD,
    DEFAULT_ESCALATION_MODEL,
    DEFAULT_MODEL,
    run_hybrid_extraction,
)

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


def run_batch(
    *,
    tiles_dir: Path,
    text_layers_dir: Path,
    out_dir: Path,
    tile_globs: list[str],
    max_tiles: int | None,
    model: str,
    api_key: str,
    referer: str,
    title: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: int,
    allow_low_coherence: bool,
    dry_run: bool,
    no_cache: bool,
    use_json_schema: bool,
    prompt_dir: Path | None,
    fail_fast: bool,
    summary_out: Path,
    escalation_model: str | None = DEFAULT_ESCALATION_MODEL,
    escalation_coherence_threshold: float = DEFAULT_ESCALATION_COHERENCE_THRESHOLD,
    escalation_enabled: bool = True,
    max_concurrency: int = 1,
) -> int:
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

        logger.info("(%s/%s) Processing %s", idx, total_pairs, stem)

        local_counts = {
            "ok": 0,
            "dry_run": 0,
            "skipped_low_coherence": 0,
            "validation_error": 0,
            "runtime_error": 0,
        }

        try:
            exit_code = run_hybrid_extraction(
                tile_path=tile_path,
                text_layer_path=text_layer_path,
                output_path=out_path,
                raw_output_path=raw_out_path,
                meta_output_path=meta_out_path,
                model=model,
                api_key=api_key,
                referer=referer,
                title=title,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
                allow_low_coherence=allow_low_coherence,
                dry_run=dry_run,
                no_cache=no_cache,
                use_json_schema=use_json_schema,
                prompt_output_path=prompt_out_path,
                escalation_model=escalation_model,
                escalation_coherence_threshold=escalation_coherence_threshold,
                escalation_enabled=escalation_enabled,
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
    summary = {
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
        "escalation_model": escalation_model,
        "escalation_coherence_threshold": escalation_coherence_threshold,
        "escalation_enabled": escalation_enabled,
        "max_concurrency": max(1, int(max_concurrency or 1)),
        "dry_run": dry_run,
        "no_cache": no_cache,
        "use_json_schema": use_json_schema,
        "allow_low_coherence": allow_low_coherence,
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
        "--api-key-env",
        type=str,
        default="OPENROUTER_API_KEY",
        help="Environment variable name holding the OpenRouter API key.",
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
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv()
    parser = _build_arg_parser()
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, "")
    if not args.dry_run and not api_key:
        raise SystemExit(
            f"Missing API key in env var '{args.api_key_env}'. "
            "Set it in environment or .env before running."
        )

    summary_out = args.summary_out or (args.out_dir / "batch_summary.json")
    tile_globs = args.tile_glob or ["*.png"]
    exit_code = run_batch(
        tiles_dir=args.tiles_dir,
        text_layers_dir=args.text_layers_dir,
        out_dir=args.out_dir,
        tile_globs=tile_globs,
        max_tiles=args.max_tiles,
        model=args.model,
        api_key=api_key,
        referer=args.referer,
        title=args.title,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_sec=args.timeout_sec,
        allow_low_coherence=args.allow_low_coherence,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        use_json_schema=args.use_json_schema,
        prompt_dir=args.prompt_dir,
        fail_fast=args.fail_fast,
        summary_out=summary_out,
        escalation_model=args.escalation_model,
        escalation_coherence_threshold=args.escalation_coherence_threshold,
        escalation_enabled=args.escalation,
        max_concurrency=args.max_concurrency,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
