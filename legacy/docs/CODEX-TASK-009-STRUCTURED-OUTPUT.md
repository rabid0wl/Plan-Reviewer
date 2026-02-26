# Codex Task 009: Structured JSON Output (Reduce Regex JSON Parsing)

## Context

The current extraction flow parses the model's raw response through a regex scraper before JSON decoding:

```
model response → _extract_json_candidate() → json.loads() → Pydantic validate
```

`_extract_json_candidate()` (lines 77–91 in `run_hybrid.py`) handles three cases:
1. Clean bare JSON object — fast path
2. JSON wrapped in markdown fences (` ```json ... ``` `) — regex strip
3. JSON buried in preamble/postamble text — find-first-`{`-last-`}` fallback

When none of these work → `json_parse_error` escalation. When the model returns a non-object → `non_object_json` escalation.

**The fix:** Pass `response_format: {"type": "json_object"}` to the OpenRouter API. The model is contractually required to return a bare JSON object — no fences, no preamble. `_extract_json_candidate()` is demoted to a fallback for models/providers that don't honour the flag. Pydantic validation is unchanged.

**Why `json_object` and not `json_schema`:** The `json_schema` mode requires passing a full JSON Schema and support varies across models on OpenRouter. `json_object` mode is universally supported by Gemini Flash and Gemini Preview and is the right scope for now. Pydantic continues to enforce our schema — this is not a regression.

**Remaining gap:** `json_object` does not enforce field types, so `page_number: null` can still occur. Task 008's `_pre_correct_tile_metadata()` fix handles this and remains valid.

---

## Evidence: Escalation Paths Affected

| Escalation trigger | Current cause | After this task |
|---|---|---|
| `json_parse_error` | `json.loads()` fails on fenced/preamble output | ⬇️ Greatly reduced — model returns bare JSON; fallback still catches edge cases |
| `non_object_json` | Model returns `[...]` or `"..."` not `{...}` | ⬇️ Reduced — `json_object` mode makes this rare, but guard and tracking remain |
| `schema_validation_error` | Missing/wrong-type fields | ✅ Unchanged — Pydantic still validates |
| `sanitized_recovery` | Invalid structures/inverts | ✅ Unchanged — sanitizer still runs |

---

## Implementation

### Part 1: Add `response_format` to API call with 400 fallback — `src/extraction/run_hybrid.py`

Add `response_format` to the `call_openrouter_vision()` function. Add a `use_structured_output: bool = True` parameter so callers can disable it if needed. If the API returns a 400 status (rejection of `response_format`), retry once without it:

```python
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
) -> tuple[str, dict[str, Any]]:
    ...
    payload = {
        "model": model,
        "messages": [...],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if use_structured_output:
        payload["response_format"] = {"type": "json_object"}
    ...
```

In the retry loop, if a 400 response is received AND `use_structured_output` is True, retry once without `response_format`:

```python
if response.status_code == 400 and use_structured_output:
    logger.warning(
        "response_format rejected by provider for %s; retrying without structured output.",
        model,
    )
    payload.pop("response_format", None)
    response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_sec)
    response.raise_for_status()
    break
```

This makes rollout safe across provider and model quirks without requiring a separate flag.

### Part 2: Direct JSON parsing with fallback — `src/extraction/run_hybrid.py`

Current code at line ~550:
```python
json_candidate = _extract_json_candidate(raw_text)
try:
    payload_obj = json.loads(json_candidate)
except json.JSONDecodeError as exc:
    ...
    return 2
```

Replace with a try-direct-first, fallback-to-regex approach:

```python
try:
    payload_obj = json.loads(raw_text)
except json.JSONDecodeError:
    # Structured output should prevent this; fallback to regex extraction
    try:
        json_candidate = _extract_json_candidate(raw_text)
        payload_obj = json.loads(json_candidate)
    except (ValueError, json.JSONDecodeError) as exc:
        escalated_exit_code = _run_escalation("json_parse_error")
        if escalated_exit_code is not None:
            return escalated_exit_code
        logger.error("Model JSON parse failed for %s", tile_id)
        ...
        return 2
```

`_extract_json_candidate()` stays in the codebase as a named fallback. Do not delete it.

The `non_object_json` guard (`if not isinstance(payload_obj, dict)`) remains unchanged below this block — still escalates if somehow a non-object comes through.

### Part 3: Add `response_format_type` to cache key — `src/extraction/run_hybrid.py`

Instead of bumping `CACHE_SCHEMA_VERSION` (which forces full re-extraction of all existing tiles), add the structured output mode to the cache key payload in `_compute_cache_key()`:

```python
def _compute_cache_key(
    *,
    prompt: str,
    image_bytes: bytes,
    model: str,
    temperature: float,
    max_tokens: int,
    cache_schema_version: str = CACHE_SCHEMA_VERSION,
    use_structured_output: bool = True,        # NEW
) -> str:
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    payload = {
        "cache_schema_version": cache_schema_version,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "image_sha256": image_hash,
        "response_format_type": "json_object" if use_structured_output else "none",  # NEW
    }
    ...
```

Pass `use_structured_output` through from `run_hybrid_extraction()` to `_compute_cache_key()`. This means:
- Old cached tiles (missing `response_format_type`) get new cache keys → re-extracted on next run
- New tiles with `use_structured_output=True` get their own clean cache entries
- No forced global re-extraction cost — only tiles that actually run again pick up the new path

**Do NOT bump `CACHE_SCHEMA_VERSION`.**

---

## Validation

### Step 1: Run existing unit tests

```bash
python -m unittest discover -s tests -v
```

All 35 existing tests must pass.

### Step 2: Add 2 new unit tests — `tests/test_run_hybrid_escalation.py` (or a new `tests/test_structured_output.py`)

**Test A: Direct `json.loads(raw_text)` path succeeds on bare JSON**

Mock `call_openrouter_vision` to return a bare JSON string (no fences). Run `run_hybrid_extraction()`. Assert `status: ok`, assert `_extract_json_candidate` was NOT called (or verify by checking `.raw.txt` has no fence characters).

```python
def test_bare_json_parsed_directly_without_regex(self):
    bare_json = json.dumps({
        "tile_id": "p1_r0_c0",
        "page_number": 1,
        "sheet_type": "plan_view",
        "utility_types_present": ["SD"],
        "structures": [], "pipes": [], "callouts": [],
        "street_names": [], "lot_numbers": [],
        "extraction_notes": None,
    })
    # Mock call_openrouter_vision to return bare_json as raw_text
    # Assert status=ok, assert _extract_json_candidate not invoked
```

**Test B: Fallback path recovers fenced JSON**

Mock `call_openrouter_vision` to return fenced content:
```
```json
{"tile_id": "p1_r0_c0", ...}
```
```

Assert `json.loads(raw_text)` raises `JSONDecodeError`, fallback fires, `_extract_json_candidate` successfully strips fences, extraction succeeds with `status: ok`.

### Step 3: Smoke test one tile (live API call)

```bash
python -m src.extraction.run_hybrid \
  --tile output/tiles/corridor-expanded/p24_r0_c0.png \
  --text-layer output/text_layers/corridor-expanded/p24_r0_c0.json \
  --out /tmp/p24_r0_c0_test.json \
  --model google/gemini-3-flash-preview \
  --no-cache
```

Check:
- `status: ok` in the generated `.meta.json`
- The `.raw.txt` contains a **bare JSON object** with no markdown fences — visual confirmation that `response_format` is working

---

## Files to Modify

1. **`src/extraction/run_hybrid.py`** — Add `use_structured_output` param to `call_openrouter_vision()`; add `response_format` to API payload with 400 retry-without fallback; replace primary JSON parse path (direct `json.loads` first, regex fallback second); add `response_format_type` to `_compute_cache_key()`

## Files NOT to Modify

- `src/extraction/schemas.py` — No schema changes
- `src/extraction/prompts.py` — Prompt unchanged; `response_format` is an API-level parameter
- `src/extraction/run_hybrid_batch.py` — No changes needed; batch runner passes through to `run_hybrid_extraction()`
- `src/graph/` — No changes

---

## Success Criteria

1. All 35 existing unit tests pass; both new unit tests pass (37 total)
2. Single-tile smoke test: `status: ok` and `.raw.txt` contains bare JSON (no fences)
3. `_extract_json_candidate()` remains in codebase as a fallback — do not delete
4. `non_object_json` guard remains in place after the JSON parse block
5. `CACHE_SCHEMA_VERSION` is **not** changed — `response_format_type` in `_compute_cache_key()` handles cache differentiation
6. 400-response retry-without-`response_format` path is present and logged at WARNING level
