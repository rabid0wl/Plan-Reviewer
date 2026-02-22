"""Run one hybrid extraction call for a tile image + text layer JSON."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from .prompts import build_hybrid_prompt
from .schemas import TileExtraction

logger = logging.getLogger(__name__)

DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-flash-preview"
CACHE_SCHEMA_VERSION = "hybrid-cache-v2"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _image_bytes_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _compute_cache_key(
    *,
    prompt: str,
    image_bytes: bytes,
    model: str,
    temperature: float,
    max_tokens: int,
    cache_schema_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload = {
        "cache_schema_version": cache_schema_version,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "image_sha256": image_hash,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _extract_json_candidate(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        return stripped[first : last + 1]

    raise ValueError("No JSON object found in model output.")


def _flatten_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks).strip()
    return str(content)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sanitize_source_text_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    cleaned: list[int] = []
    for item in value:
        try:
            cleaned.append(int(item))
        except Exception:
            continue
    return cleaned


def _sanitize_extraction_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    dropped = {
        "structures": 0,
        "inverts": 0,
        "pipes": 0,
        "callouts": 0,
    }
    sanitized = dict(payload)

    raw_structures = payload.get("structures", [])
    good_structures: list[dict[str, Any]] = []
    if isinstance(raw_structures, list):
        for structure in raw_structures:
            if not isinstance(structure, dict):
                dropped["structures"] += 1
                continue
            if not (
                _non_empty_str(structure.get("structure_type"))
                and _non_empty_str(structure.get("station"))
                and _non_empty_str(structure.get("offset"))
            ):
                dropped["structures"] += 1
                continue

            inverts_clean: list[dict[str, Any]] = []
            raw_inverts = structure.get("inverts", [])
            if isinstance(raw_inverts, list):
                for invert in raw_inverts:
                    if not isinstance(invert, dict):
                        dropped["inverts"] += 1
                        continue
                    has_required = (
                        _non_empty_str(invert.get("direction"))
                        and _non_empty_str(invert.get("pipe_size"))
                        and invert.get("elevation") is not None
                    )
                    if not has_required:
                        dropped["inverts"] += 1
                        continue
                    invert_clean = dict(invert)
                    invert_clean["source_text_ids"] = _sanitize_source_text_ids(
                        invert.get("source_text_ids")
                    )
                    inverts_clean.append(invert_clean)

            structure_clean = dict(structure)
            structure_clean["inverts"] = inverts_clean
            structure_clean["source_text_ids"] = _sanitize_source_text_ids(
                structure.get("source_text_ids")
            )
            good_structures.append(structure_clean)

    raw_pipes = payload.get("pipes", [])
    good_pipes: list[dict[str, Any]] = []
    if isinstance(raw_pipes, list):
        for pipe in raw_pipes:
            if not isinstance(pipe, dict):
                dropped["pipes"] += 1
                continue
            if not (_non_empty_str(pipe.get("pipe_type")) and _non_empty_str(pipe.get("size"))):
                dropped["pipes"] += 1
                continue
            pipe_clean = dict(pipe)
            pipe_clean["source_text_ids"] = _sanitize_source_text_ids(pipe.get("source_text_ids"))
            good_pipes.append(pipe_clean)

    raw_callouts = payload.get("callouts", [])
    good_callouts: list[dict[str, Any]] = []
    if isinstance(raw_callouts, list):
        for callout in raw_callouts:
            if not isinstance(callout, dict):
                dropped["callouts"] += 1
                continue
            if not (_non_empty_str(callout.get("callout_type")) and _non_empty_str(callout.get("text"))):
                dropped["callouts"] += 1
                continue
            callout_clean = dict(callout)
            callout_clean["source_text_ids"] = _sanitize_source_text_ids(
                callout.get("source_text_ids")
            )
            good_callouts.append(callout_clean)

    sanitized["structures"] = good_structures
    sanitized["pipes"] = good_pipes
    sanitized["callouts"] = good_callouts
    return sanitized, dropped


def call_openrouter_vision(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_data_url: str,
    referer: str,
    title: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: int,
    endpoint: str = DEFAULT_OPENROUTER_URL,
) -> tuple[str, dict[str, Any]]:
    """Call OpenRouter vision model and return raw text + response JSON."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": title,
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    retryable_statuses = {429, 500, 502, 503, 504}
    max_retries = 3
    response: requests.Response | None = None

    for attempt in range(max_retries):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_sec)
            if response.status_code in retryable_statuses and attempt < max_retries - 1:
                wait = 3**attempt
                logger.warning(
                    "Retryable status %s on attempt %s/%s. Waiting %ss.",
                    response.status_code,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt < max_retries - 1:
                wait = 3**attempt
                logger.warning(
                    "%s on attempt %s/%s. Waiting %ss.",
                    exc.__class__.__name__,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
                continue
            raise

    if response is None:
        raise RuntimeError("OpenRouter request did not produce a response.")

    response_json = response.json()

    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected OpenRouter response structure.") from exc

    return _flatten_message_content(content), response_json


def run_hybrid_extraction(
    *,
    tile_path: Path,
    text_layer_path: Path,
    output_path: Path,
    raw_output_path: Path,
    meta_output_path: Path,
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
    prompt_output_path: Path | None,
) -> int:
    """Execute one hybrid extraction call and persist outputs."""
    text_layer = _load_json(text_layer_path)
    coherence_score = float(text_layer.get("coherence_score", 0.0))
    is_hybrid_viable = bool(text_layer.get("is_hybrid_viable", True))
    tile_id = str(text_layer.get("tile_id", "unknown"))

    if not is_hybrid_viable and not allow_low_coherence:
        logger.warning(
            "Skipping tile %s: coherence %.3f below viability threshold.",
            tile_id,
            coherence_score,
        )
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        meta_payload = {
            "status": "skipped_low_coherence",
            "tile_id": tile_id,
            "coherence_score": coherence_score,
            "is_hybrid_viable": is_hybrid_viable,
        }
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2, ensure_ascii=False)
        return 0

    prompt = build_hybrid_prompt(text_layer)
    if prompt_output_path:
        prompt_output_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_output_path.write_text(prompt, encoding="utf-8")

    image_bytes = tile_path.read_bytes()
    cache_key = _compute_cache_key(
        prompt=prompt,
        image_bytes=image_bytes,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if (
        not no_cache
        and output_path.exists()
        and meta_output_path.exists()
    ):
        try:
            existing_meta = _load_json(meta_output_path)
        except Exception:
            existing_meta = {}
        if (
            existing_meta.get("cache_key") == cache_key
            and existing_meta.get("status") in {"ok", "dry_run"}
        ):
            logger.info("Cache hit for tile %s (cache_key=%s). Skipping API call.", tile_id, cache_key)
            return 0

    if dry_run:
        logger.info("Dry run complete. Prompt built for tile %s.", tile_id)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "dry_run",
                    "tile_id": tile_id,
                    "coherence_score": coherence_score,
                    "is_hybrid_viable": is_hybrid_viable,
                    "model": model,
                    "prompt_chars": len(prompt),
                    "text_items_count": len(text_layer.get("items", [])),
                    "cache_key": cache_key,
                    "cache_hit": False,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return 0

    image_data_url = _image_bytes_to_data_url(image_bytes)
    raw_text, response_json = call_openrouter_vision(
        api_key=api_key,
        model=model,
        prompt=prompt,
        image_data_url=image_data_url,
        referer=referer,
        title=title,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_sec=timeout_sec,
    )

    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path.write_text(raw_text, encoding="utf-8")

    json_candidate = _extract_json_candidate(raw_text)
    try:
        payload_obj = json.loads(json_candidate)
    except json.JSONDecodeError as exc:
        logger.error("Model JSON parse failed for %s", tile_id)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "validation_error",
                    "tile_id": tile_id,
                    "error": f"JSON parse error: {exc}",
                    "coherence_score": coherence_score,
                    "is_hybrid_viable": is_hybrid_viable,
                    "model": model,
                    "cache_key": cache_key,
                    "cache_hit": False,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return 2

    if not isinstance(payload_obj, dict):
        logger.error("Model output for %s is not a JSON object.", tile_id)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "validation_error",
                    "tile_id": tile_id,
                    "error": "Top-level JSON output is not an object.",
                    "coherence_score": coherence_score,
                    "is_hybrid_viable": is_hybrid_viable,
                    "model": model,
                    "cache_key": cache_key,
                    "cache_hit": False,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return 2

    sanitized = False
    dropped_invalid_counts = {"structures": 0, "inverts": 0, "pipes": 0, "callouts": 0}
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
            meta_output_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_output_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "validation_error",
                        "tile_id": tile_id,
                        "error": str(exc),
                        "coherence_score": coherence_score,
                        "is_hybrid_viable": is_hybrid_viable,
                        "model": model,
                        "cache_key": cache_key,
                        "cache_hit": False,
                        "dropped_invalid_counts": dropped_invalid_counts,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            return 2

    expected_tile_id = str(text_layer.get("tile_id", extraction.tile_id))
    expected_page_number = int(text_layer.get("page_number", extraction.page_number))
    corrected_fields: dict[str, Any] = {}
    if extraction.tile_id != expected_tile_id:
        corrected_fields["tile_id"] = expected_tile_id
    if extraction.page_number != expected_page_number:
        corrected_fields["page_number"] = expected_page_number
    if corrected_fields:
        logger.warning(
            "Model returned mismatched tile metadata; corrected fields: %s",
            ", ".join(sorted(corrected_fields.keys())),
        )
        extraction = extraction.model_copy(update=corrected_fields)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(extraction.model_dump(), f, indent=2, ensure_ascii=False)

    usage = response_json.get("usage", {})
    meta_output_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_output_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "status": "ok",
                "tile_id": tile_id,
                "coherence_score": coherence_score,
                "is_hybrid_viable": is_hybrid_viable,
                "model": model,
                "prompt_chars": len(prompt),
                "text_items_count": len(text_layer.get("items", [])),
                "structures_count": len(extraction.structures),
                "pipes_count": len(extraction.pipes),
                "callouts_count": len(extraction.callouts),
                "usage": usage,
                "corrected_fields": sorted(corrected_fields.keys()),
                "sanitized": sanitized,
                "dropped_invalid_counts": dropped_invalid_counts,
                "cache_key": cache_key,
                "cache_hit": False,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        "Extraction complete for %s: structures=%s pipes=%s callouts=%s",
        extraction.tile_id,
        len(extraction.structures),
        len(extraction.pipes),
        len(extraction.callouts),
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run hybrid extraction for one tile image and one tile text-layer JSON."
    )
    parser.add_argument("--tile", type=Path, required=True, help="Path to tile PNG.")
    parser.add_argument("--text-layer", type=Path, required=True, help="Path to tile text layer JSON.")
    parser.add_argument("--out", type=Path, required=True, help="Path for validated extraction JSON.")
    parser.add_argument(
        "--raw-out",
        type=Path,
        default=None,
        help="Path for raw model text output. Defaults to <out>.raw.txt",
    )
    parser.add_argument(
        "--meta-out",
        type=Path,
        default=None,
        help="Path for run metadata JSON. Defaults to <out>.meta.json",
    )
    parser.add_argument(
        "--prompt-out",
        type=Path,
        default=None,
        help="Optional path to save the full generated prompt.",
    )
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="OpenRouter model id.")
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
        help="Allow extraction even if tile is marked non-viable for hybrid extraction.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompt and metadata only; do not call the model.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable hash-based cache and force API calls.",
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
        default="Plan Reviewer Hybrid Extraction",
        help="X-Title header for OpenRouter request.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    load_dotenv()
    parser = _build_arg_parser()
    args = parser.parse_args()

    out_path = args.out
    raw_out = args.raw_out or out_path.with_suffix(out_path.suffix + ".raw.txt")
    meta_out = args.meta_out or out_path.with_suffix(out_path.suffix + ".meta.json")

    api_key = os.getenv(args.api_key_env, "")
    if not args.dry_run and not api_key:
        raise SystemExit(
            f"Missing API key in env var '{args.api_key_env}'. "
            "Set it in environment or .env before running."
        )

    exit_code = run_hybrid_extraction(
        tile_path=args.tile,
        text_layer_path=args.text_layer,
        output_path=out_path,
        raw_output_path=raw_out,
        meta_output_path=meta_out,
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
        prompt_output_path=args.prompt_out,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
