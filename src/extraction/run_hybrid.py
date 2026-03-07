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

from ..utils.io_json import read_json
from .config_models import (
    EscalationConfig,
    ExtractionConfig,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
)
from .package_contract import page_number_from_tile_id
from .prompts import build_hybrid_prompt, build_hybrid_prompt_split
from .schemas import TileExtraction, _WATER_STRUCTURE_TYPES, _normalize_structure_type

try:
    import anthropic as _anthropic_module
except ImportError:  # pragma: no cover
    _anthropic_module = None  # type: ignore[assignment]

try:
    import instructor as _instructor_module
except ImportError:  # pragma: no cover
    _instructor_module = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
from ..config import DEFAULT_EXTRACTION_MODEL as DEFAULT_MODEL
from ..config import DEFAULT_ESCALATION_MODEL
from ..config import ESCALATION_COHERENCE_THRESHOLD as DEFAULT_ESCALATION_COHERENCE_THRESHOLD
CACHE_SCHEMA_VERSION = "hybrid-cache-v2"
_STRUCTURED_NONE = "none"
_STRUCTURED_JSON_OBJECT = "json_object"
_STRUCTURED_JSON_SCHEMA = "json_schema"



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
    structured_output_mode: str = _STRUCTURED_JSON_OBJECT,
    cache_schema_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    """Build a stable cache key for one prompt-image-model extraction request."""
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload = {
        "cache_schema_version": cache_schema_version,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "image_sha256": image_hash,
        "response_format_type": str(structured_output_mode),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _extract_json_candidate(text: str) -> str:
    """Extract the best-effort JSON object substring from mixed model text."""
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


def _coerce_is_existing(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "y"}:
            return True
        if token in {"false", "0", "no", "n"}:
            return False
    return False


def _sanitize_source_text_ids(value: Any) -> list[int]:
    """Coerce a mixed list of ids into a clean list of integers."""
    if not isinstance(value, list):
        return []
    cleaned: list[int] = []
    for item in value:
        try:
            cleaned.append(int(item))
        except Exception:
            continue
    return cleaned


def _coerce_int(value: Any) -> int | None:
    """Return an integer when coercion succeeds, else None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pre_correct_tile_metadata(payload: dict[str, Any], text_layer: dict[str, Any]) -> None:
    """Patch null/missing tile metadata from authoritative text-layer values."""
    authoritative_tile_id = str(text_layer.get("tile_id", "")).strip()
    if not _non_empty_str(payload.get("tile_id")) and authoritative_tile_id:
        payload["tile_id"] = authoritative_tile_id

    authoritative_page_number = _coerce_int(text_layer.get("page_number"))
    if authoritative_page_number is None:
        authoritative_page_number = page_number_from_tile_id(authoritative_tile_id)
    if authoritative_page_number is None:
        authoritative_page_number = page_number_from_tile_id(str(payload.get("tile_id", "")))

    raw_page_number = payload.get("page_number")
    if (
        (raw_page_number is None or (isinstance(raw_page_number, str) and not raw_page_number.strip()))
        and authoritative_page_number is not None
    ):
        payload["page_number"] = authoritative_page_number


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
            structure_type = structure.get("structure_type")
            station = structure.get("station")
            if not (_non_empty_str(structure_type) and _non_empty_str(station)):
                dropped["structures"] += 1
                continue

            stype_norm = _normalize_structure_type(
                structure_type if isinstance(structure_type, str) else None
            )
            requires_offset = stype_norm not in _WATER_STRUCTURE_TYPES
            offset = structure.get("offset")
            if requires_offset and not _non_empty_str(offset):
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
            if not requires_offset and not _non_empty_str(offset):
                structure_clean["offset"] = "0' CL"
            structure_clean["inverts"] = inverts_clean
            structure_clean["is_existing"] = _coerce_is_existing(structure.get("is_existing", False))
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


def _response_format_payload(mode: str) -> dict[str, Any]:
    """Build response-format and provider hints for one structured-output mode."""
    if mode == _STRUCTURED_JSON_SCHEMA:
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "tile_extraction",
                    "strict": True,
                    "schema": TileExtraction.model_json_schema(),
                },
            },
            "provider": {"require_parameters": True},
        }
    if mode == _STRUCTURED_JSON_OBJECT:
        return {"response_format": {"type": "json_object"}}
    return {}


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
    use_structured_output: bool = True,
    use_json_schema: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Call OpenRouter vision model and return raw text + response JSON."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": title,
    }
    base_payload = {
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

    structured_modes: list[str]
    if use_structured_output:
        if use_json_schema:
            structured_modes = [_STRUCTURED_JSON_SCHEMA, _STRUCTURED_JSON_OBJECT, _STRUCTURED_NONE]
        else:
            structured_modes = [_STRUCTURED_JSON_OBJECT, _STRUCTURED_NONE]
    else:
        structured_modes = [_STRUCTURED_NONE]

    retryable_statuses = {429, 500, 502, 503, 504}
    max_retries = 3
    last_response: requests.Response | None = None

    for mode_idx, structured_mode in enumerate(structured_modes):
        payload = dict(base_payload)
        payload.update(_response_format_payload(structured_mode))

        for attempt in range(max_retries):
            try:
                last_response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_sec)
                if (
                    last_response.status_code == 400
                    and structured_mode != _STRUCTURED_NONE
                    and mode_idx < len(structured_modes) - 1
                ):
                    next_mode = structured_modes[mode_idx + 1]
                    logger.warning(
                        "Structured output mode '%s' rejected for %s; retrying with '%s'.",
                        structured_mode,
                        model,
                        next_mode,
                    )
                    break

                if last_response.status_code in retryable_statuses and attempt < max_retries - 1:
                    wait = 3**attempt
                    logger.warning(
                        "Retryable status %s on attempt %s/%s. Waiting %ss.",
                        last_response.status_code,
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                last_response.raise_for_status()
                response_json = last_response.json()
                response_json["_response_format_type"] = structured_mode
                try:
                    content = response_json["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise ValueError("Unexpected OpenRouter response structure.") from exc
                return _flatten_message_content(content), response_json
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

        if (
            last_response is not None
            and last_response.status_code == 400
            and structured_mode != _STRUCTURED_NONE
            and mode_idx < len(structured_modes) - 1
        ):
            continue

    if last_response is None:
        raise RuntimeError("OpenRouter request did not produce a response.")

    try:
        last_response.raise_for_status()
    except Exception:
        raise
    response_json = last_response.json()
    response_json.setdefault("_response_format_type", _STRUCTURED_NONE)
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected OpenRouter response structure.") from exc
    return _flatten_message_content(content), response_json


def call_anthropic_vision(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_data_url: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: int,
) -> tuple[str, dict[str, Any]]:
    """Call the Anthropic API directly with prompt caching on the system message.

    The system_prompt is sent with ``cache_control: {"type": "ephemeral"}`` so that
    the Claude API can cache it across tile calls that share the same schema and
    instructions, reducing both latency and cost.

    Args:
        api_key: Anthropic API key.
        model: Anthropic model identifier (e.g. ``claude-opus-4-6``).
        system_prompt: Cacheable system-level instructions and schema definition.
        user_prompt: Per-tile text layer data and extraction instruction.
        image_data_url: Base64-encoded data URL of the tile PNG image.
        temperature: Sampling temperature for the model.
        max_tokens: Maximum tokens in the response.
        timeout_sec: Request timeout in seconds.

    Returns:
        A 2-tuple of ``(raw_text, response_dict)`` matching the signature of
        ``call_openrouter_vision()``.

    Raises:
        ImportError: If the ``anthropic`` package is not installed.
        anthropic.APIError: On non-retryable API errors.
    """
    if _anthropic_module is None:
        raise ImportError(
            "The 'anthropic' package is required when using provider='anthropic'. "
            "Install it with: pip install 'anthropic>=0.40.0'"
        )

    # Parse media type and base64 payload from the data URL produced by
    # _image_bytes_to_data_url().  Format is "data:<media_type>;base64,<data>".
    if "," in image_data_url:
        header, b64_data = image_data_url.split(",", 1)
        media_type = header.split(";")[0].replace("data:", "") if ";" in header else "image/png"
    else:
        b64_data = image_data_url
        media_type = "image/png"

    client = _anthropic_module.Anthropic(api_key=api_key)

    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout_sec,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": user_prompt,
                    },
                ],
            }
        ],
    )

    # Flatten all text content blocks into a single string.
    raw_text_parts: list[str] = []
    for block in message.content:
        if hasattr(block, "text"):
            raw_text_parts.append(block.text)
    raw_text = "\n".join(raw_text_parts).strip()

    # Build a response dict compatible with the existing meta/usage reporting path.
    response_dict: dict[str, Any] = {
        "id": message.id,
        "model": message.model,
        "stop_reason": message.stop_reason,
        "usage": {
            "prompt_tokens": message.usage.input_tokens,
            "completion_tokens": message.usage.output_tokens,
            "total_tokens": message.usage.input_tokens + message.usage.output_tokens,
        },
        "_response_format_type": _STRUCTURED_NONE,
        "_provider": PROVIDER_ANTHROPIC,
    }

    # Capture cache usage fields when present (Anthropic SDK exposes them on usage).
    usage_obj = message.usage
    if hasattr(usage_obj, "cache_creation_input_tokens"):
        response_dict["usage"]["cache_creation_input_tokens"] = (
            usage_obj.cache_creation_input_tokens
        )
    if hasattr(usage_obj, "cache_read_input_tokens"):
        response_dict["usage"]["cache_read_input_tokens"] = (
            usage_obj.cache_read_input_tokens
        )

    return raw_text, response_dict


def call_anthropic_vision_structured(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_data_url: str,
    temperature: float,
    max_tokens: int,
    timeout_sec: int,
) -> tuple[TileExtraction, dict[str, Any]]:
    """Call the Anthropic API via instructor for automatic Pydantic validation.

    Uses instructor to patch the Anthropic client so that the response is
    automatically parsed and validated against ``TileExtraction``.  Up to 2
    automatic retries are performed by instructor on validation failure before
    raising an exception.

    The system_prompt is sent with ``cache_control: {"type": "ephemeral"}``
    matching the behaviour of ``call_anthropic_vision()``.

    Args:
        api_key: Anthropic API key.
        model: Anthropic model identifier (e.g. ``claude-opus-4-6``).
        system_prompt: Cacheable system-level instructions and schema definition.
        user_prompt: Per-tile text layer data and extraction instruction.
        image_data_url: Base64-encoded data URL of the tile PNG image.
        temperature: Sampling temperature for the model.
        max_tokens: Maximum tokens in the response.
        timeout_sec: Request timeout in seconds.

    Returns:
        A 2-tuple of ``(extraction, response_dict)`` where ``extraction`` is a
        validated ``TileExtraction`` instance and ``response_dict`` carries
        usage metadata compatible with the rest of the pipeline.

    Raises:
        ImportError: If either ``anthropic`` or ``instructor`` is not installed.
        instructor.exceptions.InstructorRetryException: If validation still
            fails after the configured number of retries.
        anthropic.APIError: On non-retryable API errors.
    """
    if _anthropic_module is None:
        raise ImportError(
            "The 'anthropic' package is required when using provider='anthropic'. "
            "Install it with: pip install 'anthropic>=0.40.0'"
        )
    if _instructor_module is None:
        raise ImportError(
            "The 'instructor' package is required for structured output. "
            "Install it with: pip install 'instructor>=1.7.0'"
        )

    # Parse media type and base64 payload from the data URL.
    if "," in image_data_url:
        header, b64_data = image_data_url.split(",", 1)
        media_type = header.split(";")[0].replace("data:", "") if ";" in header else "image/png"
    else:
        b64_data = image_data_url
        media_type = "image/png"

    client = _anthropic_module.Anthropic(api_key=api_key)
    instructor_client = _instructor_module.from_anthropic(client)

    extraction: TileExtraction = instructor_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout_sec,
        max_retries=2,
        response_model=TileExtraction,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": user_prompt,
                    },
                ],
            }
        ],
    )

    # instructor returns the validated model directly; usage is not surfaced the
    # same way as the raw SDK response.  Build a minimal compatible response dict.
    response_dict: dict[str, Any] = {
        "model": model,
        "usage": {},
        "_response_format_type": _STRUCTURED_NONE,
        "_provider": PROVIDER_ANTHROPIC,
        "_via_instructor": True,
    }

    return extraction, response_dict


def run_hybrid_extraction(
    *,
    tile_path: Path,
    text_layer_path: Path,
    output_path: Path,
    raw_output_path: Path,
    meta_output_path: Path,
    config: ExtractionConfig,
    escalation: EscalationConfig,
    allow_low_coherence: bool,
    dry_run: bool,
    no_cache: bool,
    prompt_output_path: Path | None,
    attempted_models: list[str] | None = None,
    escalation_reason: str | None = None,
    _escalated: bool = False,
) -> int:
    """Execute one hybrid extraction call and persist outputs."""
    # Unpack config for convenient local access (keeps the rest of the body unchanged).
    model = config.model
    api_key = config.api_key
    provider = config.provider
    referer = config.referer
    title = config.title
    temperature = config.temperature
    max_tokens = config.max_tokens
    timeout_sec = config.timeout_sec
    use_structured_output = config.use_structured_output
    use_json_schema = config.use_json_schema
    use_instructor = config.use_instructor
    escalation_model = escalation.model
    escalation_coherence_threshold = escalation.coherence_threshold
    escalation_enabled = escalation.enabled
    text_layer = read_json(text_layer_path)
    coherence_score = float(text_layer.get("coherence_score", 0.0))
    is_hybrid_viable = bool(text_layer.get("is_hybrid_viable", True))
    tile_id = str(text_layer.get("tile_id", "unknown"))
    attempt_chain = list(attempted_models or [])
    attempt_chain.append(model)

    def _can_escalate() -> bool:
        return (
            escalation_enabled
            and not _escalated
            and isinstance(escalation_model, str)
            and bool(escalation_model.strip())
            and escalation_model.strip() != model
        )

    def _meta_common() -> dict[str, Any]:
        return {
            "tile_id": tile_id,
            "coherence_score": coherence_score,
            "is_hybrid_viable": is_hybrid_viable,
            "model": model,
            "attempted_models": attempt_chain,
            "escalated": _escalated,
            "escalation_reason": escalation_reason,
            "escalation_model": escalation_model,
            "escalation_enabled": escalation_enabled,
            "escalation_coherence_threshold": escalation_coherence_threshold,
            "structured_output_preference": (
                _STRUCTURED_JSON_SCHEMA
                if (use_structured_output and use_json_schema)
                else (_STRUCTURED_JSON_OBJECT if use_structured_output else _STRUCTURED_NONE)
            ),
        }

    def _run_escalation(reason: str, *, force_allow_low_coherence: bool = False) -> int | None:
        if not _can_escalate():
            return None
        escalation_target = str(escalation_model).strip()
        logger.warning(
            "Escalating tile %s from %s to %s (%s).",
            tile_id,
            model,
            escalation_target,
            reason,
        )
        from dataclasses import replace as _dc_replace
        return run_hybrid_extraction(
            tile_path=tile_path,
            text_layer_path=text_layer_path,
            output_path=output_path,
            raw_output_path=raw_output_path,
            meta_output_path=meta_output_path,
            config=_dc_replace(config, model=escalation_target),
            escalation=escalation,
            allow_low_coherence=allow_low_coherence or force_allow_low_coherence,
            dry_run=dry_run,
            no_cache=no_cache,
            prompt_output_path=prompt_output_path,
            attempted_models=attempt_chain,
            escalation_reason=reason,
            _escalated=True,
        )

    low_confidence = coherence_score < float(escalation_coherence_threshold)
    if low_confidence:
        escalated_exit_code = _run_escalation(
            "low_coherence",
            force_allow_low_coherence=not is_hybrid_viable,
        )
        if escalated_exit_code is not None:
            return escalated_exit_code

    if not is_hybrid_viable and not allow_low_coherence:
        logger.warning(
            "Skipping tile %s: coherence %.3f below viability threshold.",
            tile_id,
            coherence_score,
        )
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        meta_payload = {"status": "skipped_low_coherence", **_meta_common()}
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2, ensure_ascii=False)
        return 0

    # Build prompts.  For the Anthropic provider we keep the split form so the
    # system message can be sent with cache_control.  For OpenRouter we use the
    # legacy combined prompt string.
    if provider == PROVIDER_ANTHROPIC:
        system_prompt, user_prompt = build_hybrid_prompt_split(text_layer)
        prompt = system_prompt + "\n\n" + user_prompt  # combined string for cache-key / dry-run
    else:
        prompt = build_hybrid_prompt(text_layer)
        system_prompt = ""
        user_prompt = ""

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
        structured_output_mode=(
            _STRUCTURED_JSON_SCHEMA
            if (use_structured_output and use_json_schema)
            else (_STRUCTURED_JSON_OBJECT if use_structured_output else _STRUCTURED_NONE)
        ),
    )

    if (
        not no_cache
        and output_path.exists()
        and meta_output_path.exists()
    ):
        try:
            existing_meta = read_json(meta_output_path)
        except Exception:
            existing_meta = {}
        if (
            existing_meta.get("cache_key") == cache_key
            and existing_meta.get("model") == model
            and existing_meta.get("status") in {"ok", "dry_run"}
        ):
            if bool(existing_meta.get("sanitized", False)):
                escalated_exit_code = _run_escalation("sanitized_recovery_cached")
                if escalated_exit_code is not None:
                    return escalated_exit_code
            logger.info("Cache hit for tile %s (cache_key=%s). Skipping API call.", tile_id, cache_key)
            return 0

    if dry_run:
        logger.info("Dry run complete. Prompt built for tile %s.", tile_id)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "dry_run",
                    **_meta_common(),
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

    # --- instructor-backed Anthropic path ---
    _use_instructor_path = (
        provider == PROVIDER_ANTHROPIC
        and use_instructor
        and _instructor_module is not None
    )
    if _use_instructor_path:
        try:
            extraction, response_json = call_anthropic_vision_structured(
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_data_url=image_data_url,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
            )
        except Exception as _instructor_exc:
            # Instructor failed (e.g. exhausted retries).  Log and fall through to
            # the manual parsing path below.
            logger.warning(
                "instructor path failed for tile %s (%s); falling back to manual parsing.",
                tile_id,
                _instructor_exc.__class__.__name__,
            )
            _use_instructor_path = False

        if _use_instructor_path:
            # instructor returned a validated TileExtraction; write raw JSON and meta.
            raw_output_path.parent.mkdir(parents=True, exist_ok=True)
            raw_output_path.write_text(extraction.model_dump_json(), encoding="utf-8")

            # Post-validate tile metadata correctness (same logic as manual path).
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
                        **_meta_common(),
                        "prompt_chars": len(prompt),
                        "text_items_count": len(text_layer.get("items", [])),
                        "structures_count": len(extraction.structures),
                        "pipes_count": len(extraction.pipes),
                        "callouts_count": len(extraction.callouts),
                        "usage": usage,
                        "response_format_type": response_json.get(
                            "_response_format_type", _STRUCTURED_NONE
                        ),
                        "corrected_fields": sorted(corrected_fields.keys()),
                        "sanitized": False,
                        "dropped_invalid_counts": {
                            "structures": 0,
                            "inverts": 0,
                            "pipes": 0,
                            "callouts": 0,
                        },
                        "cache_key": cache_key,
                        "cache_hit": False,
                        "via_instructor": True,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            logger.info(
                "Extraction complete for %s (instructor): structures=%s pipes=%s callouts=%s",
                extraction.tile_id,
                len(extraction.structures),
                len(extraction.pipes),
                len(extraction.callouts),
            )
            return 0

    # --- manual parsing path (OpenRouter, or Anthropic without instructor) ---
    try:
        if provider == PROVIDER_ANTHROPIC:
            raw_text, response_json = call_anthropic_vision(
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                image_data_url=image_data_url,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_sec=timeout_sec,
            )
        else:
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
                use_structured_output=use_structured_output,
                use_json_schema=use_json_schema,
            )
    except Exception:
        escalated_exit_code = _run_escalation("api_call_error")
        if escalated_exit_code is not None:
            return escalated_exit_code
        raise

    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path.write_text(raw_text, encoding="utf-8")

    try:
        payload_obj = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            json_candidate = _extract_json_candidate(raw_text)
            payload_obj = json.loads(json_candidate)
        except (ValueError, json.JSONDecodeError) as exc:
            escalated_exit_code = _run_escalation("json_parse_error")
            if escalated_exit_code is not None:
                return escalated_exit_code
            logger.error("Model JSON parse failed for %s", tile_id)
            meta_output_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_output_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "validation_error",
                        **_meta_common(),
                        "error": f"JSON parse error: {exc}",
                        "cache_key": cache_key,
                        "cache_hit": False,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            return 2

    if not isinstance(payload_obj, dict):
        escalated_exit_code = _run_escalation("non_object_json")
        if escalated_exit_code is not None:
            return escalated_exit_code
        logger.error("Model output for %s is not a JSON object.", tile_id)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        with meta_output_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "status": "validation_error",
                    **_meta_common(),
                    "error": "Top-level JSON output is not an object.",
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
            escalated_exit_code = _run_escalation("schema_validation_error")
            if escalated_exit_code is not None:
                return escalated_exit_code
            logger.error("Schema validation failed for %s", tile_id)
            meta_output_path.parent.mkdir(parents=True, exist_ok=True)
            with meta_output_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "status": "validation_error",
                        **_meta_common(),
                        "error": str(exc),
                        "cache_key": cache_key,
                        "cache_hit": False,
                        "dropped_invalid_counts": dropped_invalid_counts,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            return 2

    if sanitized:
        escalated_exit_code = _run_escalation("sanitized_recovery")
        if escalated_exit_code is not None:
            return escalated_exit_code

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
                **_meta_common(),
                "prompt_chars": len(prompt),
                "text_items_count": len(text_layer.get("items", [])),
                "structures_count": len(extraction.structures),
                "pipes_count": len(extraction.pipes),
                "callouts_count": len(extraction.callouts),
                "usage": usage,
                "response_format_type": response_json.get("_response_format_type", _STRUCTURED_NONE),
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
        "--use-json-schema",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use response_format=json_schema with strict mode before json_object fallback.",
    )
    parser.add_argument(
        "--no-instructor",
        action="store_true",
        default=False,
        help=(
            "Disable instructor-backed structured output for the Anthropic provider "
            "and fall back to manual JSON parsing. Has no effect when --provider=openrouter."
        ),
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

    api_key_env = args.api_key_env or (
        "ANTHROPIC_API_KEY" if args.provider == PROVIDER_ANTHROPIC else "OPENROUTER_API_KEY"
    )
    api_key = os.getenv(api_key_env, "")
    if not args.dry_run and not api_key:
        raise SystemExit(
            f"Missing API key in env var '{api_key_env}'. "
            "Set it in environment or .env before running."
        )

    _config = ExtractionConfig(
        model=args.model,
        api_key=api_key,
        provider=args.provider,
        referer=args.referer,
        title=args.title,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_sec=args.timeout_sec,
        use_json_schema=args.use_json_schema,
        use_instructor=not args.no_instructor,
    )
    _escalation = EscalationConfig(
        enabled=args.escalation,
        model=args.escalation_model,
        coherence_threshold=args.escalation_coherence_threshold,
    )
    exit_code = run_hybrid_extraction(
        tile_path=args.tile,
        text_layer_path=args.text_layer,
        output_path=out_path,
        raw_output_path=raw_out,
        meta_output_path=meta_out,
        config=_config,
        escalation=_escalation,
        allow_low_coherence=args.allow_low_coherence,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        prompt_output_path=args.prompt_out,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
