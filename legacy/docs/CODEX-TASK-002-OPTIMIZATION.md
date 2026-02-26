# Codex Task 002: Extraction Pipeline Optimization

## Context

The 20-tile calibration run scored 90% (9/10 ground truth checks) at $0.40 total. Token analysis shows prompt tokens are 92-97% of cost, and the text-layer JSON is ~85% of prompt tokens. The biggest cost levers are (a) trimming what we send per text item, (b) retry/caching for dev workflow, and (c) fixing known data issues from the calibration run.

**Do NOT change the tiling grid dimensions or manifest gating logic — those are separate tasks. Focus only on the 7 changes below.**

---

## Change 1: Trim text-layer items in prompt (HIGH PRIORITY)

**File:** `src/extraction/prompts.py` — `build_hybrid_prompt()`

**Problem:** Each text item sent to the LLM contains 6 fields (~220 tokens/item). The LLM only uses 3 of them. On a 284-item tile, this wastes ~34,000 tokens.

**Change:** Before inserting text items into the prompt, strip each item down to only `text_id`, `text`, and `bbox_local`. Round `bbox_local` coordinates to integers.

```python
def build_hybrid_prompt(text_layer: dict[str, Any]) -> str:
    schema_json = TileExtraction.model_json_schema()
    raw_items = text_layer.get("items", [])

    # Trim to only fields the LLM needs; round bbox to save tokens
    slim_items = [
        {
            "id": item["text_id"],
            "t": item["text"],
            "b": [round(c) for c in item["bbox_local"]],
        }
        for item in raw_items
    ]

    return HYBRID_EXTRACTION_PROMPT.format(
        schema=json.dumps(schema_json, indent=2),
        text_layer_json=json.dumps(slim_items, separators=(",", ":")),
    )
```

**Also update the prompt text** to tell the LLM about the new field names:

In the `## TEXT LAYER DATA` section of `HYBRID_EXTRACTION_PROMPT`, add this line before the JSON block:
```
Each item has: "id" (text_id for source_text_ids), "t" (text content), "b" (bounding box [x0,y0,x1,y1] in tile-local pixels).
```

**Do NOT change** `text_layer.py` or `models.py` — the full data (font, font_size, bbox_global) must still be saved to disk for provenance. Only trim when building the prompt.

**Expected result:** ~35-45% reduction in prompt tokens on dense tiles. A 284-item tile should drop from ~62,000 text-layer tokens to ~35,000.

---

## Change 2: Compact JSON schema in prompt (MEDIUM PRIORITY)

**File:** `src/extraction/prompts.py`

**Problem:** `TileExtraction.model_json_schema()` dumps 11,826 characters (~3,000 tokens) because Pydantic v2 expands every nullable field to `"anyOf": [{"type": "string"}, {"type": "null"}]` and includes verbose `$defs`.

**Change:** Create a hand-written compact schema string. Add it as a module-level constant `COMPACT_SCHEMA` in `prompts.py`. Use it instead of the auto-generated schema.

The compact schema must list every field the LLM should output. Use this exact format:

```python
COMPACT_SCHEMA = """{
  "tile_id": "string - tile identifier e.g. p14_r0_c2",
  "page_number": "int",
  "sheet_type": "string - plan_view|profile|detail|grading|signing_striping|cover|notes|other",
  "utility_types_present": ["SD","SS","W"],
  "structures": [{
    "id": "string|null - structure ID e.g. MH-1",
    "structure_type": "string - SDMH|SSMH|CB|GB|inlet|cleanout|junction",
    "size": "string|null - e.g. 48\"",
    "station": "string - e.g. 16+82.45",
    "offset": "string - e.g. 28.00' RT",
    "rim_elevation": "float|null",
    "tc_elevation": "float|null - top of curb",
    "fl_elevation": "float|null - flowline",
    "inverts": [{
      "direction": "string - N|S|E|W|NE|NW|SE|SW",
      "pipe_size": "string - e.g. 12\"",
      "pipe_type": "string|null - SD|SS|W",
      "elevation": "float - to 0.01 ft",
      "source_text_ids": [int]
    }],
    "notes": "string|null",
    "source_text_ids": [int]
  }],
  "pipes": [{
    "pipe_type": "string - SD|SS|W",
    "size": "string - e.g. 12\"",
    "material": "string|null - RCP|PVC|DIP|HDPE",
    "length_lf": "float|null - linear feet",
    "slope": "float|null - decimal e.g. 0.0020",
    "from_station": "string|null",
    "to_station": "string|null",
    "from_structure_hint": "string|null - nearby structure description",
    "to_structure_hint": "string|null - nearby structure description",
    "notes": "string|null",
    "source_text_ids": [int]
  }],
  "callouts": [{
    "callout_type": "string - edge_of_pavement|detail_reference|cross_reference|grading_note|installation_note|match_existing|cover_depth|other",
    "text": "string - full callout text",
    "station": "string|null",
    "offset": "string|null",
    "elevation": "float|null",
    "reference_sheet": "string|null",
    "reference_detail": "string|null",
    "source_text_ids": [int]
  }],
  "street_names": ["string"],
  "lot_numbers": [int],
  "extraction_notes": "string|null"
}"""
```

Then in `build_hybrid_prompt`, replace `json.dumps(schema_json, indent=2)` with `COMPACT_SCHEMA`.

**Keep the Pydantic models in `schemas.py` unchanged** — they are still used for output validation via `TileExtraction.model_validate_json()`.

**Expected result:** Schema portion of prompt drops from ~3,000 tokens to ~500-700 tokens.

---

## Change 3: Lower default max-tokens

**Files:** `src/extraction/run_hybrid.py` line 306, `src/extraction/run_hybrid_batch.py` line 249

**Change:** Change `default=8192` to `default=4096` in both argparse definitions.

Largest observed completion was 2,665 tokens. 4096 gives plenty of headroom while preventing runaway generations.

---

## Change 4: Retry with exponential backoff (HIGH PRIORITY)

**File:** `src/extraction/run_hybrid.py` — `call_openrouter_vision()`

**Problem:** Transient 502s from OpenRouter require manual re-runs. The p34_r0_c1 tile needed this during calibration.

**Change:** Add retry logic around the `requests.post` call. Do NOT add `tenacity` as a dependency — use a simple manual loop. 3 attempts max, backoff 1s/3s/9s, only retry on 429 or 5xx status codes.

```python
import time

def call_openrouter_vision(...) -> tuple[str, dict[str, Any]]:
    headers = { ... }  # same as now
    payload = { ... }  # same as now

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_sec)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                wait = 3 ** attempt  # 1s, 3s, 9s
                logger.warning(
                    "Retryable status %s on attempt %s/%s. Waiting %ss.",
                    response.status_code, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 3 ** attempt
                logger.warning("Timeout on attempt %s/%s. Waiting %ss.", attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            raise

    response_json = response.json()
    # ... rest unchanged
```

---

## Change 5: Hash-based extraction caching for dev workflow

**File:** `src/extraction/run_hybrid.py` — `run_hybrid_extraction()`

**Problem:** During prompt iteration, re-running unchanged tiles wastes money. Calibration-20 was run multiple times at $0.40/run.

**Change:** After building the prompt and before calling the API, compute a cache key. If the output file already exists and was produced with the same cache key, skip the API call.

Add a `--no-cache` flag (default: caching enabled).

Cache key computation:
```python
import hashlib

def _compute_cache_key(prompt: str, image_path: Path) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(image_path.read_bytes())
    return h.hexdigest()[:16]
```

In `run_hybrid_extraction()`, after building the prompt:
1. Compute cache key
2. If `meta_output_path` exists, read it and check if `meta["cache_key"]` matches
3. If match and `output_path` exists and `--no-cache` is not set, log "cache hit" and return 0
4. Otherwise proceed with API call
5. Write cache_key into the meta JSON alongside existing fields

Add `--no-cache` flag to both `run_hybrid.py` and `run_hybrid_batch.py` argparse and plumb through to `run_hybrid_extraction()`.

---

## Change 6: Fix ground truth values in calibration scorer

**File:** `src/extraction/score_calibration.py`

### 6a: Fix Page 36 ground truth RIM elevation

The calibration run found SSMH at STA 12+07.59 with RIM 302.19. The original ground truth of 302.78 was wrong (it was a hand-reading error from Phase 1 — the text layer has the correct value).

**Line 223**, change:
```python
("12+07.18", 302.78),
```
to:
```python
("12+07.18", 302.19),
```

### 6b: Tighten tolerances

Page 36 uses very loose tolerances (station_tol_ft=3.0, rim tol=0.8) that were set to accommodate the hand-reading errors. Now that ground truth is corrected, tighten them.

**Lines 229-232** — change `station_tol_ft=3.0` to `station_tol_ft=2.0`

**Line 241** — change `_float_close(found.get("rim_elevation"), rim, 0.8)` to `_float_close(found.get("rim_elevation"), rim, 0.6)`

---

## Change 7: Fix tile boundary text truncation

**File:** `src/intake/text_layer.py` — `extract_text_layer()`

**Problem:** PyMuPDF's `get_text("dict", clip=rect)` only returns spans whose origin point is inside the clip rect. Spans that START just outside the boundary but overlap into the tile are dropped. This caused "INSTALL 342 LF..." to become "TALL 342 LF..." because the span origin was 3 points left of the tile edge.

**Change:** After calling `page.get_text("dict", clip=clip)`, also call `page.get_text("dict")` on the FULL page (without clip), then include any span whose bbox intersects the clip rect but wasn't already captured. Use a small padding (5 points) on the clip rect for the intersection test.

```python
def extract_text_layer(
    page: fitz.Page,
    clip: fitz.Rect | None = None,
    clip_origin: tuple[float, float] = (0.0, 0.0),
    *,
    tile_id: str | None = None,
    page_number: int | None = None,
) -> TextLayer:
    text_dict = page.get_text("dict", clip=clip)
    coherence_score, total_spans, multi_char_spans, numeric_spans, primary_font = (
        calculate_coherence(text_dict)
    )

    if page_number is None:
        page_number = page.number + 1
    if tile_id is None:
        tile_id = f"p{page_number}_full"

    origin_x, origin_y = clip_origin

    # Collect spans from clipped extraction
    clipped_spans = _iter_spans(text_dict)

    # If we have a clip, also find boundary spans that were missed
    if clip is not None:
        padded = fitz.Rect(clip.x0 - 5, clip.y0 - 5, clip.x1 + 5, clip.y1 + 5)
        full_dict = page.get_text("dict")
        clipped_origins = set()
        for span in clipped_spans:
            bbox = span.get("bbox", (0, 0, 0, 0))
            clipped_origins.add((round(bbox[0], 2), round(bbox[1], 2)))

        for span in _iter_spans(full_dict):
            bbox = span.get("bbox", (0, 0, 0, 0))
            span_rect = fitz.Rect(bbox)
            origin_key = (round(bbox[0], 2), round(bbox[1], 2))
            if origin_key not in clipped_origins and span_rect.intersects(padded):
                clipped_spans.append(span)

    # Build items from combined spans
    items: list[TextItem] = []
    text_id = 0
    for span in clipped_spans:
        text = clean_unicode(str(span.get("text", ""))).strip()
        if not text:
            continue
        raw_bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
        x0, y0, x1, y1 = (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))
        bbox_global = (x0, y0, x1, y1)
        bbox_local = (x0 - origin_x, y0 - origin_y, x1 - origin_x, y1 - origin_y)

        items.append(
            TextItem(
                text_id=text_id,
                text=text,
                bbox_local=bbox_local,
                bbox_global=bbox_global,
                font=str(span.get("font", "")),
                font_size=float(span.get("size", 0.0)),
            )
        )
        text_id += 1

    return TextLayer(
        tile_id=tile_id,
        page_number=page_number,
        items=items,
        coherence_score=coherence_score,
        total_spans=total_spans,
        multi_char_spans=multi_char_spans,
        numeric_spans=numeric_spans,
        primary_font=primary_font,
        is_hybrid_viable=coherence_score >= COHERENCE_THRESHOLD,
    )
```

**Performance note:** Calling `get_text("dict")` on the full page is redundant work. To mitigate, only do the boundary recovery when `clip` is not None. The extra cost is negligible (milliseconds) compared to the API call.

---

## Validation Plan

After making all changes, re-run the 20-tile calibration and verify:

1. **Accuracy preserved:** Calibration score must still be >= 9/10 (90%). Run:
   ```
   python -m src.extraction.run_hybrid_batch \
     --tiles-dir output/intake-pass1/tiles \
     --text-layers-dir output/intake-pass1/text_layers \
     --out-dir output/extractions/calibration-opt \
     --tile-glob "p14_*.png" --tile-glob "p19_*.png" --tile-glob "p34_*.png" --tile-glob "p36_*.png" \
     --max-tiles 20

   python -m src.extraction.score_calibration \
     --extractions-dir output/extractions/calibration-opt
   ```

2. **Token reduction:** Compare `prompt_tokens` in meta files between `calibration-20` and `calibration-opt`. Dense tiles (p14_r0_c0 had 72,685) should drop to ~40,000-45,000.

3. **Cost reduction:** Total batch cost should drop from $0.40 to ~$0.22-0.28.

4. **Cache works:** Re-run the same batch command. Second run should complete instantly with "cache hit" logs and $0.00 API cost.

5. **Retry works:** No way to unit test transient errors easily, but verify the retry loop compiles and the backoff math is correct by reading the code.

6. **Ground truth fix:** p36 SSMH at STA 12+07.18 check should now pass with RIM 302.19 (it already passed before due to loose tolerance, but now confirm with tighter tolerance).

7. **Boundary fix:** After re-running tiling with the text_layer change, check that p14_r0_c0's text layer contains the full text "INSTALL 342 LF OF 12" (not truncated "TALL 342 LF OF 12"). This requires re-running the tiler:
   ```
   python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --output output/intake-pass2 --pages 14
   ```
   Then grep the text layer JSON for "INSTALL".

---

## Files Modified (summary)

| File | Changes |
|------|---------|
| `src/extraction/prompts.py` | Trim text items to 3 fields, add `COMPACT_SCHEMA`, update prompt wording |
| `src/extraction/run_hybrid.py` | Retry loop in API call, cache logic, lower max-tokens default, plumb --no-cache |
| `src/extraction/run_hybrid_batch.py` | Lower max-tokens default, plumb --no-cache |
| `src/extraction/score_calibration.py` | Fix RIM 302.78→302.19, tighten tolerances |
| `src/intake/text_layer.py` | Boundary span recovery for clipped tiles |

## Files NOT modified

| File | Reason |
|------|--------|
| `src/extraction/schemas.py` | Pydantic models unchanged — still used for validation |
| `src/intake/models.py` | TextItem/TextLayer unchanged — full data still saved to disk |
| `src/intake/manifest.py` | No changes this task |
| `src/intake/tiler.py` | No changes this task |
| `tests/test_parsing.py` | No changes needed |
