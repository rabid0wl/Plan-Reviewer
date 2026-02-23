# Plan Reviewer — Development Progress

**Project:** Civil Engineering Plan Review Tool
**Started:** 2026-02-21
**Author:** Dylan (PE #98682)

> Working journal grouped by component. High verbosity during active work, pruned at session end.
> For architecture overview see `ARCHITECTURE.md`. For polished test results see `docs/findings/`.

---

## Decisions Log

Quick-reference for every architectural choice. Newest first.

| # | Date | Decision | Reasoning | Status |
|---|------|----------|-----------|--------|
| D14 | 2026-02-22 | Pre-validate null tile metadata before Pydantic schema check | Model occasionally returns `page_number: null`. This fails Pydantic before post-validation corrector can run, losing the tile permanently. Fix: `_pre_correct_tile_metadata()` injects correct values from text_layer before first `model_validate()`. Also added `_page_number_from_tile_id()` as a second fallback when text_layer is also missing the field. | Validated |
| D13 | 2026-02-22 | Tag signing_striping pipe edges as `is_reference_only`; suppress connectivity findings | SSM sheets show existing utilities for reference only — no station/slope/length. Pipes survive sanitization but generate false unanchored/dead_end findings and cross-corridor false matches. Tag edges at assembly, skip in `check_connectivity()`. Added page-level fallback via `_reference_only_pages()` for tiles misclassified as plan_view on an SSM page. | Validated |
| D12 | 2026-02-21 | Auto-correct tile metadata from text layer payload | LLM frequently hallucinates tile_id and page_number. Codex implemented auto-correction from source payload — fired 12/20 times in calibration. | Validated |
| D11 | 2026-02-21 | Gemini Flash for calibration, Claude/Opus for production | $0.02/tile with Flash is ideal for iteration (~$0.40 for 20 tiles). Switch to higher-accuracy model once pipeline is stable. | Active |
| D10 | 2026-02-21 | Flat LLM output schema → Python assembles graph | LLMs lose track in deeply nested JSON. Extract flat lists (structures[], pipes[], callouts[]), Python stitches graph in Phase 3.5. | Adopted |
| D9 | 2026-02-21 | Data provenance: every value traces to text_id + bbox | Enables exact crop generation for findings UI. PE can click finding → see exact spot on sheet. Critical for trust. | Adopted |
| D8 | 2026-02-21 | Text coherence gate for SHX font detection | Score text layer quality before hybrid extraction. High coherence → hybrid. Low (SHX/raster) → OCR fallback (Tesseract/GCP Vision). | Adopted |
| D7 | 2026-02-21 | Graph data structure for utility networks | Nodes=structures, edges=pipes. Enables deterministic math checks (slope verify, connectivity) without LLM. Replaces flat "inventory.json" concept. | Adopted |
| D6 | 2026-02-21 | Hybrid extraction: PDF text layer + vision | Vision for spatial understanding, text layer for exact numbers. Eliminates hallucinated digits. | **Validated** — FNC Farms has full ArialNarrow text layer, 0.65-0.89 coherence, all critical values with bboxes. |
| D5 | 2026-02-21 | Sheet index bootstrapping from cover sheet | Parse cover sheet index table first, use to seed/validate manifest before scanning all title blocks. | Adopted |
| D4 | 2026-02-21 | 3x2 tile grid at 300 DPI for extraction | Quarter-page crops readable, full pages not. 3x2 gives ~3600x2400 tiles — sweet spot for API. | Validated |
| D3 | 2026-02-21 | Schema-driven extraction prompts, not open-ended | Tight JSON schemas with explicit fields (station, offset, rim_elevation, inverts[].direction) produce far better structured output than "extract everything." | Validated |
| D2 | 2026-02-21 | PyMuPDF for PDF→PNG (not pdftoppm) | pdftoppm/ImageMagick not installed on Windows. PyMuPDF handles rendering natively, produces clean PNGs, supports clip regions for tiling. | Locked in |
| D1 | 2026-02-21 | Two tools: Consistency Checker first, Standards Compliance second | 80% of RFI-prevention value is in cross-reference consistency. Standards compliance requires agency-specific skills. Build the high-ROI tool first. | From architecture |

---

## Open Questions

Tracked here until resolved, then moved to Decisions Log.

| # | Date Opened | Question | Context |
|---|-------------|----------|---------|
| Q1 | 2026-02-21 | What overlap % for tiles? | 10% assumed. Need to test whether annotations at tile boundaries get split. Haven't validated yet. |
| Q2 | 2026-02-21 | Can adaptive cropping (two-pass) reliably identify annotation regions? | Full-page triage pass at low res can see layout but may miss sparse annotations. Need to test. |
| ~~Q3~~ | ~~2026-02-21~~ | ~~Best data structure for cross-reference inventory?~~ | **RESOLVED → D7.** Graph: nodes=structures, edges=pipes. |
| ~~Q4~~ | ~~2026-02-21~~ | ~~Profile extraction: how to handle SS/W label confusion?~~ | **RESOLVED → D6.** Text layer provides exact labels; vision no longer sole source. |
| ~~Q5~~ | ~~2026-02-21~~ | ~~Can we skip ~30% of sheets (cover, gen notes, signing/striping) in extraction?~~ | **RESOLVED → D13.** Signing/striping sheets: still extract (structures correctly dropped by sanitizer, pipes kept), but tag edges `is_reference_only=True` and suppress connectivity findings. Net effect: SSM sheets cost ~$0.05/page but produce zero false findings. Cover/gen-notes sheets are already low-cost (few text items). Skip optimization is low-priority. |

---

## Component: PDF Intake & Tiling

Everything related to getting from a PDF file to renderable tiles.

### 2026-02-21 — Initial extraction pipeline built

**Setup:** PyMuPDF (fitz) v1.27.1. Windows machine, no pdftoppm/ImageMagick.

**Test PDF:** FNC Farms Ph. 1 Civils — 57 pages, 36"x24" ARCH D, 75MB. Each page = 2592x1728 pts = 10800x7200px at 300 DPI.

**Extraction script:** Simple Python using `page.get_pixmap(matrix=..., clip=...)`. Works perfectly for both full pages and arbitrary crop regions. No external dependencies needed.

**DPI testing:**
- 150 DPI: 5400x3600px — too aggressive, some text loss
- 200 DPI: 7200x4800px — good for full-page triage, marginal for annotation reading
- 300 DPI: 10800x7200px full page (huge), but 3200x2900 crops are perfect
- 400 DPI: No improvement over 300 DPI because the API downsamples anyway

**Key finding:** PyMuPDF's `clip` parameter is perfect for tiling. Pass a `fitz.Rect()` and it renders only that region. No need to render the full page and then crop — saves memory and time.

**Files created:**
- `test-extractions/p01_cover.png` — cover/vicinity map
- `test-extractions/p03_demo_notes.png` — demolition key notes
- `test-extractions/p09_sd_plan.png` — storm drain basin plan
- `test-extractions/p19_profile.png` — SD/SS/W profile view
- `test-extractions/p34_utility.png` — utility plan/profile
- `test-extractions/p36_street_section.png` — typical street section + utility profiles
- Plus ~8 tight crops for resolution testing

**Next:** Build a reusable tiling module that takes a page and returns N tiles with overlap.

---

## Component: Vision Extraction

Everything related to getting structured data from tile images via Claude vision.

### 2026-02-21 — Resolution study (the critical finding)

**The problem:** Claude's image API downsamples large images to ~1568px on the longest side. A full 36x24 civil sheet at any DPI becomes ~1568x1045 internal pixels. At that resolution, 6-8pt annotation text (pipe sizes, elevations, station numbers) is sub-pixel and unreadable.

**Test results:**

| Image Type | Source Size | After API Downscale | Text Readable? |
|---|---|---|---|
| Full page, 200 DPI | 7200x4800 | ~1568x1045 | ❌ Layout only |
| Half-page crop, 300 DPI | ~4600x3100 | ~1568x1056 | ⚠️ Large text yes, fine annotations marginal |
| Quarter-page crop, 300 DPI | ~3200x2900 | ~1568x1420 | ✅ Nearly everything |
| Title block strip, 300 DPI | 1944x7200 | ~424x1568 | ✅ Everything crystal clear |

**Why quarter-page is the sweet spot:** The downscale ratio matters less than information density. A quarter-page crop has 1/4 the content competing for ~the same number of pixels after downscaling. The effective resolution per annotation roughly doubles.

**The takeaway:** At production time, every sheet gets tiled into 6 pieces (3x2 grid). There's no way around this — it's a hard API constraint.

### 2026-02-21 — Structured extraction test: Plan view (storm drain)

**Input:** Quarter-page crop of Page 14 (Prosperity Avenue, SD plan), ~3200x2900px at 300 DPI.

**Prompt approach:** Schema-driven JSON extraction with explicit field names. NOT "extract everything from this image" but "fill this exact JSON schema."

**What it extracted (highlights):**

Structures (example):
```json
{
  "station": "16+82.45",
  "offset": "28.00' RT",
  "type": "SDMH", "size": "48\"",
  "rim_elevation": 305.95,
  "inverts": [
    {"direction": "E", "size": "12\"", "elevation": 299.77},
    {"direction": "W", "size": "12\"", "elevation": 299.77}
  ]
}
```

Pipe runs:
- `INSTALL 342 LF OF 12" SD PIPE @ S=0.0030` ✅
- `INSTALL 342 LF OF 12" SD PIPE @ S=0.0020` ✅ (second run, different slope)

Also got: 15 edge-of-pavement callouts, 9 lot numbers, detail bubble refs, SEE SHEET cross-refs.

**Accuracy assessment:** Station numbers precise to 0.01'. Elevations precise to 0.01'. Pipe sizes exact. Slopes exact. No hallucinated values detected. Some edge-of-pavement elevations flagged as "(parenthetical)" which is actually correct — parenthetical elevations on plans indicate proposed vs existing.

### 2026-02-21 — Structured extraction test: Profile view

**Input:** Half-page crop of Page 36 (Bishop Street profile), ~4600x3100px at 300 DPI.

**What it extracted:**
- 6 pipe runs (SS and W) with sizes, lengths, slopes, station ranges
- 7 structures: 3 SSMHs (rim 301.79, 302.78, 302.90), 2 water bends (45°), 2 gate valves
- Invert elevations at each structure (INV 8" S 294.86, INV 8" E 294.49, etc.)
- Cover depth: "4.0' MIN COVER"
- Grade lines: "EXISTING GRADE @ CENTERLINE", "PROPOSED TC @ EAST SIDE OF STREET"

**Issue spotted:** One pipe_run entry had `utility_type: "SD"` but the note said "SS PIPE" — the schema field and the annotation text didn't match. This is a prompt engineering problem, not a vision problem. The model read "SS" correctly from the image but misclassified it in the JSON field. Fix: add explicit guidance in the extraction prompt about SD vs SS vs W classification.

**Profile-specific challenges:**
- Profiles show multiple utilities overlapping vertically (SD lowest, SS middle, W highest). Vision can distinguish them but sometimes confuses which annotations belong to which pipe.
- Dashed vs solid line type is recognized but not consistently labeled — "dashed line" vs "dash-dot" vs "hidden" aren't reliably distinguished.
- Station annotations on the profile grid (bottom axis) are always readable.
- Elevation annotations on the profile grid (left axis) are always readable.

### 2026-02-21 — Hybrid extraction validated (text layer test)

**The gating question:** Does FNC Farms PDF have a usable vector text layer, or is it rasterized/SHX garbage?

**Answer: Full, high-quality vector text layer.** ArialNarrow (TrueType) throughout. No SHX issues.

**Text layer coherence scores by page:**

| Page | Text Blocks | Spans | Multi-char | Coherence | Font |
|---|---|---|---|---|---|
| 1 (Cover) | 210 | 392 | 347 | 0.89 | ArialNarrow |
| 9 (SD Plan) | 199 | 310 | 267 | 0.86 | ArialNarrow |
| 14 (SD Plan) | 446 | 906 | 590 | 0.65 | ArialNarrow |
| 19 (Profile) | 219 | 388 | 296 | 0.76 | ArialNarrow |
| 34 (Utility) | 179 | 335 | 289 | 0.86 | ArialNarrow |
| 36 (Street) | 216 | 426 | 354 | 0.83 | ArialNarrow |

Page 14 has lower coherence (0.65) because it's annotation-dense with many short pipe callouts (`12'' SD` repeated). Still well above the SHX threshold (which would be <0.3).

**Tile text layer test (Page 14, right quadrant):**

Extracted 280 text items with bounding boxes for a single tile. 44KB JSON payload. All critical engineering data present:
- 2 structures with RIM elevations (305.44, 305.95)
- 4 inverts with direction, size, and elevation (INV. 12" E 300.80, etc.)
- 2 pipe runs (INSTALL 342 LF OF 12")
- 17 station callouts with exact offsets
- 13 elevation callouts (TC, FL, EP)

**Cross-check against vision extraction of same region:**

| Value | Vision (from earlier test) | Text Layer | Match? |
|---|---|---|---|
| STA 16+82.45, 28.00' RT | ✅ | ✅ | Exact |
| RIM: 305.95 | ✅ | ✅ | Exact |
| INV. 12" E 299.77 | ✅ | ✅ | Exact |
| INV. 12" W 299.77 | ✅ | ✅ | Exact |
| INSTALL 342 LF | ✅ | ✅ | Exact |

Vision happened to get everything right this time, but the text layer provides certainty. No more trusting vision for digit accuracy.

**Minor issue:** The diameter symbol ⌀ (U+2205) appears in some pipe installation notes and causes Windows cp1252 encoding errors. Trivially fixed with `.replace('\u2205', 'DIA')` in the text extraction pipeline.

**PyMuPDF clip feature works for text too:** `page.get_text("dict", clip=rect)` returns only text within the clip region. This means we can extract the text layer per tile, not per page — matching exactly to the tile image. Perfect for the hybrid prompt approach.

**Conclusion:** D6 (hybrid extraction) is fully validated. The extraction pipeline payload per tile will be: tile PNG image + 280-item text layer JSON (~44KB). Vision handles spatial understanding, text layer provides ground-truth numbers.

---

## Component: Sheet Classification

Understanding what type of content each sheet contains.

### 2026-02-21 — Initial sheet type observations

From text extraction + visual inspection of FNC Farms:

| Page(s) | Sheet Type | Content | Needs Deep Extraction? |
|---|---|---|---|
| 1 | Cover | Vicinity map, sheet index, owner/developer | Title block + sheet index only |
| 2 | Title/Notes | Revision block, general notes | Probably skip |
| 3 | Demolition | Demo key notes, removals | Light extraction |
| 4-13 | Civil Plan | Lot grading, pipe plans, structures | ✅ Full tiled extraction |
| 14-18 | SD Plan | Storm drain plan views with structures | ✅ Full tiled extraction |
| 19-31 | Profiles | SD/SS/W profile views | ✅ Full tiled extraction |
| 32-35 | Utility Plan | Combined SD/SS/W plan views | ✅ Full tiled extraction |
| 36 | Street Section | Typical section + utility profiles | ✅ Full tiled extraction |
| 37-47 | SS/W Plans | Sanitary sewer and water plans | ✅ Full tiled extraction |
| 48-49 | Lot Plan | Lot numbering / layout | Light extraction |
| 50-52 | Signing/Striping | Traffic signs, pavement markings | Probably skip for consistency checker |
| 53-55 | Details/Notes | Standard details, general notes | ⚠️ May need extraction for detail refs |
| 56-57 | Utility Plan | Additional utility plans | ✅ Full tiled extraction |

**Rough count:** ~35 sheets need full extraction, ~10 can be skipped or lightly extracted, ~12 are profile views (need half-page crops not full tiling).

---

## Component: Skills / Standards

Agency standards, design criteria, extraction schemas.

*(Not yet started — Phase 0 in ARCHITECTURE.md)*

---

## Component: Cross-Reference Analysis

Diffing extracted data against itself for consistency.

*(Not yet started — Phase 2 pipeline component)*

---

## Cost Tracking

| Date | Activity | Model | API Cost | Notes |
|---|---|---|---|---|
| 2026-02-21 | Phase 1 vision validation | Claude Code (Pro) | $0 | All testing done through Pro subscription |

---

## Session Log

### 2026-02-21 — Phase 1 Vision Validation

**Goal:** Can vision extract structured data from civil engineering plan sheets?

**Answer:** YES, with tiling. Full pages fail. Quarter-page crops at 300 DPI work excellently.

**Time spent:** ~2 hours

**Key outcomes:**
1. Built PyMuPDF extraction pipeline (full page + arbitrary crop support)
2. Validated resolution sweet spot: 300 DPI crops of ~3200x2900px
3. Confirmed structured extraction accuracy: stations to 0.01', elevations to 0.01', pipe sizes exact
4. Identified extraction prompt best practice: schema-driven, not open-ended
5. Identified SD/SS/W confusion issue in profiles — needs prompt engineering fix
6. Wrote `PHASE1-VISION-FINDINGS.md` (polished reference) and this progress doc

**What surprised us:**
- How dramatically crop size affects readability — it's not gradual, it's a cliff. Full page is useless, quarter-page is excellent.
- How well schema-driven prompts work — the JSON output from a tight schema is production-quality, not "needs cleanup."
- PyMuPDF's clip parameter eliminates the need for render-then-crop. Direct region rendering is clean and fast.

**Next session priorities:**
1. Validate hybrid extraction — test PyMuPDF text layer on FNC Farms pages
2. Build reusable tiling module (take page → return 6 tiles with overlap)
3. Build extraction schemas for each sheet type (plan, profile, detail, title block)
4. Test on second plan set (Corridor) to confirm generalization
5. Start Phase 0 skills (agency standards decomposition)

### 2026-02-21 — Architecture review (Gemini 2.5 Pro feedback)

Fed ARCHITECTURE.md to Gemini for independent review. 6 suggestions received, 3 adopted:

**Adopted — Hybrid Extraction (D6):** Instead of pure vision, extract the PDF text layer (PyMuPDF `get_text("dict")`) which gives exact text strings with `(x,y)` bounding boxes. Feed text data alongside tile images to the extraction agent. Vision handles spatial context (what connects to what), text layer provides ground-truth numbers (no hallucinated digits). This is the single highest-impact improvement — directly addresses the SS/W label confusion and digit accuracy concerns from Phase 1 testing. **Needs validation:** must confirm FNC Farms PDF has a usable vector text layer (not rasterized).

**Adopted — Graph Data Structure (D7):** Replace the flat "inventory.json" with a topological graph. Nodes = structures (MH, CB, inlet), Edges = pipes. This unlocks deterministic Python checks that don't need an LLM: slope = (invertA - invertB) / length, connectivity analysis, orphan detection. Cheaper, faster, and more trustworthy than asking an LLM to do arithmetic.

**Adopted — Sheet Index Bootstrapping (D5):** Parse the cover sheet's index table first, use it to seed the manifest before scanning all 57 title blocks individually. Minor efficiency win.

**Noted for later:** HITL triage dashboard ("Tinder for RFIs"), delta/revision review (diff Rev1 graph vs Rev2 graph), scaling limitation caveat (tool can only verify explicitly labeled data, cannot scale distances off drawings).

**Rejected/already covered:** Tiling strategy (we already validated this empirically before the review).

### 2026-02-21 — Architecture review round 2 (Gemini, accuracy/robustness focus)

Second round of Gemini feedback, focused on failure modes at pipeline seams. 5 suggestions, 3 adopted:

**Adopted — SHX Font Coherence Gate (D8):** AutoCAD SHX fonts often produce garbage text layers (fragmented single characters, vector linework instead of text). Before using hybrid extraction, calculate a coherence score (multi-char spans / total spans). High → hybrid. Low → fall back to OCR (Tesseract or GCP Vision API) to generate synthetic text layer. FNC Farms scored 0.65-0.89 (all ArialNarrow TTF), so no SHX issue here — but other firms' PDFs will vary.

**Adopted — Data Provenance (D9):** Every extracted value must trace back to `text_id` → `bbox_global` → exact PDF coordinates. Enables: (1) generating exact crop screenshots for findings, (2) PE audit trail (click finding → see sheet location), (3) deduplication by spatial matching in overlap zones.

**Adopted — Flat LLM Output Schema (D10):** Don't ask LLM to output nested graph JSON. Instead: flat lists of `structures[]`, `pipes[]`, `callouts[]`, each with `text_id` references. Python (Phase 3.5) stitches flat lists into NetworkX graph. Reduces JSON hallucination, easier to validate.

**Noted for later:** Cloud Run state machine / distributed job queue with exponential backoff (production resilience). Overlap deduplication algorithm: coordinate translation → spatial clustering → semantic match → center-of-tile tiebreaker.

### 2026-02-21 — Text layer validation (hybrid extraction gate)

**Tested:** PyMuPDF `get_text("dict")` on 6 representative FNC Farms pages.

**Result:** Full vector text layer confirmed. ArialNarrow TTF throughout. Coherence 0.65-0.89. All critical engineering data (stations, elevations, inverts, pipe sizes, installation notes) present with exact bounding boxes.

**Tile-level test:** Extracted 280 text items from one tile region (44KB JSON). Cross-checked against vision extraction of same region — all values match exactly. Text layer provides ground-truth certainty for every number vision reads.

**D6 status changed from "Adopted, needs validation" → "Validated."**

**Updated next session priorities:**
1. ~~Validate hybrid extraction~~ ✅ Done
2. Build reusable tiling module (page → 6 tiles with overlap + text layer JSON per tile)
3. Build flat extraction schemas for each sheet type (structures[], pipes[], callouts[])
4. Test hybrid extraction end-to-end (tile image + text JSON → structured output)
5. Test on second plan set (Corridor) — especially check text layer quality (different firm may use SHX)
6. Start Phase 0 skills (agency standards decomposition)

### 2026-02-21 — Coding spec written for handoff

Created `docs/CODING-SPEC-INTAKE-PIPELINE.md` — a self-contained spec covering all 7 modules of the intake/extraction/graph pipeline. Includes function signatures, data structures, Pydantic models, prompt templates, test plan with ground-truth values, known edge cases, and file paths. Designed to be handed off to any coding agent (Gemini, Codex, or Claude) without needing conversation context.

### 2026-02-21 - Intake pipeline implementation pass (Modules 1-4 + 7)

Implemented the first coding pass as requested:

- `src/intake/tiler.py` with CLI: `python -m src.intake.tiler`
- `src/intake/text_layer.py` with CLI: `python -m src.intake.text_layer`
- `src/intake/manifest.py` with CLI: `python -m src.intake.manifest`
- `src/intake/models.py` dataclasses for tile/text/manifest payloads
- `src/extraction/schemas.py` (Pydantic extraction models)
- `src/extraction/prompts.py` (hybrid prompt builder)
- `src/utils/unicode.py` (unicode cleanup map)

Implementation notes:

- Coherence gate is implemented (`COHERENCE_THRESHOLD = 0.40`) with `is_hybrid_viable`.
- If `--skip-low-coherence` is enabled (default in tiler), low-coherence pages are logged and skipped.
- OCR fallback is intentionally not implemented in this pass (deferred by scope decision).

Accepted deviation from original module split:

- `parse_station` and `parse_offset` were implemented in `src/utils/parsing.py` (not `src/graph/assembly.py`) because Modules 5-6 are deferred to next pass.
- Rationale: parser functions can be tested now without forcing premature graph scaffolding.

CrossBeam reference repo naming cleanup (to avoid `PROGRESS.md` ambiguity):

- `References/cc-crossbeam-main/progress.md` -> `References/cc-crossbeam-main/CC-PROGRESS.md`
- `References/cc-crossbeam-main/README.md` -> `References/cc-crossbeam-main/CC-README.md`
- `References/cc-crossbeam-main/DEMO.md` -> `References/cc-crossbeam-main/CC-DEMO.md`
- `References/cc-crossbeam-main/plan-supabase-realtime-fix.md` -> `References/cc-crossbeam-main/CC-PLAN-SUPABASE-REALTIME-FIX.md`

Acceptance checks run:

1. Tiler on FNC Farms Page 14:
   `python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --pages 14 --output "output/intake-pass1"`
   - Result: 6 tiles generated (`p14_r{0-1}_c{0-2}`) with matching 6 text-layer JSON files.
   - Coverage check: no geometry gaps across full page bounds (0,0)-(2592,1728).
   - Tile sizes at 300 DPI with 10% overlap:
     - Edge tiles: 3960 x 3960 px
     - Middle column tiles: 4320 x 3960 px
   - Note: this differs from the "~3600x2400" wording in the spec; with a 3x2 split on a 36x24 sheet, base tile geometry is square and overlap increases size.

2. Text layer JSON validity for Page 14 tiles:
   - All 6 JSON files have `items` with both `bbox_local` and `bbox_global` arrays of length 4.
   - All 6 tiles flagged `is_hybrid_viable = true`.

3. Full 57-page coherence batch (FNC Farms, scores only):
   `python -m src.intake.text_layer --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --output "output/fnc-coherence" --scores-only`
   - Result: 57/57 pages hybrid-viable.
   - Lowest coherence: page 50 = 0.802
   - Highest coherence: page 53 = 1.000
   - Primary font observed: ArialNarrow

4. Parser unit tests:
   `python -m unittest tests/test_parsing.py -v`
   - Result: all tests passed for station and offset variants in the coding spec.

### 2026-02-21 - Hybrid extraction runner implemented (Module 4 execution)

Implemented `src/extraction/run_hybrid.py` as a standalone CLI to execute one tile-level hybrid extraction call:

- Inputs: tile PNG + tile text-layer JSON
- Prompt build: `build_hybrid_prompt(...)` from `src/extraction/prompts.py`
- Model call: OpenRouter vision endpoint
- Output parsing: extracts JSON even when wrapped in markdown fences
- Validation: strict `TileExtraction` Pydantic validation
- Outputs written:
  - validated JSON: `--out`
  - raw model text: default `<out>.raw.txt`
  - run metadata: default `<out>.meta.json`

CLI usage:

`python -m src.extraction.run_hybrid --tile <tile.png> --text-layer <tile.json> --out <extraction.json>`

Key behavior added for reliability:

- Enforces coherence gate from text layer (`is_hybrid_viable`) and skips by default when not viable.
- Supports `--dry-run` (build prompt + metadata only, no API call).
- Corrects model hallucinations on `tile_id` and `page_number` by forcing values from the text-layer payload.
  - This was needed in testing: one profile run returned `tile_id: p1` and `page_number: 1` for a page 36 tile.

Calibration runs completed:

1. Plan-view tile (Page 14):
   `python -m src.extraction.run_hybrid --tile "output/intake-pass1/tiles/p14_r0_c2.png" --text-layer "output/intake-pass1/text_layers/p14_r0_c2.json" --out "output/extractions/p14_r0_c2.json" --model "google/gemini-3-flash-preview"`
   - Status: OK
   - Output: 1 structure, 2 pipes, 12 callouts
   - Metadata file includes token/cost usage and corrected_fields list (empty for this run)

2. Profile tile (Page 36, left half from 1x2 tiling):
   `python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --pages 36 --output "output/intake-pass1-profile" --grid-rows 1 --grid-cols 2 --overlap-pct 0.10`
   `python -m src.extraction.run_hybrid --tile "output/intake-pass1-profile/tiles/p36_r0_c0.png" --text-layer "output/intake-pass1-profile/text_layers/p36_r0_c0.json" --out "output/extractions/p36_r0_c0.json" --model "google/gemini-3-flash-preview"`
   - Status: OK
   - Output: 4 structures, 5 pipes, 8 callouts
   - `corrected_fields`: `page_number`, `tile_id` (model metadata mismatch auto-corrected)

Generated artifacts:

- `output/extractions/p14_r0_c2.json`
- `output/extractions/p14_r0_c2.json.raw.txt`
- `output/extractions/p14_r0_c2.json.meta.json`
- `output/extractions/p36_r0_c0.json`
- `output/extractions/p36_r0_c0.json.raw.txt`
- `output/extractions/p36_r0_c0.json.meta.json`

### 2026-02-21 - Batch hybrid extraction CLI added

Implemented `src/extraction/run_hybrid_batch.py` to run many tile/text-layer pairs in one command.

Primary command shape:

`python -m src.extraction.run_hybrid_batch --tiles-dir <tiles_dir> --text-layers-dir <text_layers_dir> --out-dir <out_dir>`

Key features:

- Auto-matches files by stem:
  - tile: `<tiles-dir>/<stem>.png`
  - text layer: `<text-layers-dir>/<stem>.json`
- Supports `--max-tiles` for cheap calibration runs (e.g., first 10-20 tiles).
- Supports `--dry-run` to build prompts + metadata without API calls.
- Writes per-tile outputs exactly like single-run CLI:
  - `<out-dir>/<stem>.json`
  - `<out-dir>/<stem>.json.raw.txt`
  - `<out-dir>/<stem>.json.meta.json`
- Writes batch summary JSON with counts and per-tile status:
  - default: `<out-dir>/batch_summary.json`
- Handles missing text-layer files explicitly in summary.
- Supports `--fail-fast` to stop on first validation/runtime failure.

Validation runs:

1. Dry-run smoke test (3 tiles):
`python -m src.extraction.run_hybrid_batch --tiles-dir "output/intake-pass1/tiles" --text-layers-dir "output/intake-pass1/text_layers" --out-dir "output/extractions/batch-smoke" --max-tiles 3 --dry-run --prompt-dir "output/extractions/batch-smoke/prompts"`
- Result: 3/3 dry-run success, 0 missing, 0 validation/runtime errors.
- Summary: `output/extractions/batch-smoke/batch_summary.json`

2. Live test (1 tile):
`python -m src.extraction.run_hybrid_batch --tiles-dir "output/intake-pass1/tiles" --text-layers-dir "output/intake-pass1/text_layers" --out-dir "output/extractions/batch-live" --max-tiles 1 --model "google/gemini-3-flash-preview" --timeout-sec 180`
- Result: 1/1 OK (schema-valid extraction generated).
- Summary: `output/extractions/batch-live/batch_summary.json`
- Note: tile metadata correction safeguard fired (`tile_id`) as expected and was recorded.

### 2026-02-21 - 10-tile live calibration + automated scoring

Completed a live 10-tile calibration batch and added an automated scoring CLI for known ground-truth checks.

New module:

- `src/extraction/score_calibration.py`
  - Loads validated extraction JSON files from a directory
  - Runs deterministic checks against known Page 14 and Page 36 values from coding spec
  - Outputs `calibration_score.json` with per-check pass/fail and summary pass rate

Batch run executed:

`python -m src.extraction.run_hybrid_batch --tiles-dir "output/intake-pass1/tiles" --text-layers-dir "output/intake-pass1/text_layers" --out-dir "output/extractions/calibration-10" --max-tiles 10 --model "google/gemini-3-flash-preview" --timeout-sec 180`

Results:

- 10/10 tiles completed successfully
- 0 validation errors
- 0 runtime errors
- Outputs: validated JSON + raw model text + meta JSON per tile
- Batch summary: `output/extractions/calibration-10/batch_summary.json`

Scoring command:

`python -m src.extraction.score_calibration --extractions-dir "output/extractions/calibration-10"`

Scoring results:

- Overall: 9/10 checks passed (90.0%)
- Page 14 checks: 5/5 passed
  - Captured SDMH at 16+82.45, 28.00' RT, RIM 305.95
  - Captured INV 12" E 299.77 and INV 12" W 299.77
  - Captured 12" SD pipe ~342 LF @ 0.003 and 12" SD pipe @ 0.002
- Page 36 checks: 4/5 passed
  - Captured all three SSMH targets near expected stations/rims within tolerance
  - Captured 8" SS pipe near 201 LF @ 0.005
  - Missed explicit 300 LF @ 0.005 pipe check in this 10-tile subset

Calibration batch usage summary (from meta files):

- Total cost: ~$0.256341
- Total tokens: 424,822
- Tile metadata correction events (tile_id/page_number auto-fix): 12

Cleanup performed:

- Removed temporary test output folders:
  - `output/extractions/batch-smoke`
  - `output/extractions/batch-live`
  - `output/extractions/_batch-check`
  - `output/intake-pass1-profile`
- Removed one-off extraction artifacts superseded by calibration batch:
  - `output/extractions/p14_r0_c2.*`
  - `output/extractions/p36_r0_c0.*`
- Removed generated `__pycache__` folders from `src/` and `tests/`.

### 2026-02-21 - 20-tile live calibration run

Ran a larger mixed-page calibration batch (Pages 14, 19, 34, partial 36 coverage) using the batch CLI.

Dataset prep:

- Added tiles for pages 19 and 34 into `output/intake-pass1`.
- Intake inventory after prep: 24 tile/text-layer pairs available.

Batch command:

`python -m src.extraction.run_hybrid_batch --tiles-dir "output/intake-pass1/tiles" --text-layers-dir "output/intake-pass1/text_layers" --out-dir "output/extractions/calibration-20" --max-tiles 20 --model "google/gemini-3-flash-preview" --timeout-sec 180`

Run outcome:

- Initial batch pass: 19 OK, 1 runtime error (transient OpenRouter 502 on `p34_r0_c1`).
- Retried failed tile with single-run CLI:
  - `python -m src.extraction.run_hybrid --tile "output/intake-pass1/tiles/p34_r0_c1.png" --text-layer "output/intake-pass1/text_layers/p34_r0_c1.json" --out "output/extractions/calibration-20/p34_r0_c1.json" --model "google/gemini-3-flash-preview" --timeout-sec 180`
  - Retry succeeded.
- Final post-retry status: 20/20 extraction metas report `status=ok`.
- Post-retry summary: `output/extractions/calibration-20/batch_summary_final.json`

Scoring:

`python -m src.extraction.score_calibration --extractions-dir "output/extractions/calibration-20"`

- Score: 9/10 (90.0%), same as 10-tile run.
- Page 14 checks: 5/5 pass.
- Page 36 checks: 4/5 pass.
- Missed check remains `p36_ss_pipe_300_005` because this 20-tile selection included only the first 2 Page 36 tiles (not full Page 36 coverage).

Cost/tokens comparison vs previous 10-tile run:

- 10-tile: cost ~$0.256341, tokens 424,822, score 9/10
- 20-tile: cost ~$0.404731, tokens 666,188, score 9/10
- Delta (20 - 10): +$0.148390, +241,366 tokens

Notes:

- `corrected_fields` safeguard (tile_id/page_number enforcement from text layer) continued to catch metadata drift in model output.
- No schema validation errors observed across the 20-tile final output set.

### 2026-02-21 — Code review of Codex build (Claude session 2)

Reviewed all code and extraction outputs produced by Codex.

**Code assessment: A-**
- 2,126 lines across 12 Python source files
- All 7 modules implemented per spec (Modules 1-4, 7 + batch runner + scoring)
- Clean modular structure, proper CLI entry points, Pydantic validation, error handling
- `parse_station`/`parse_offset` placed in `src/utils/parsing.py` (sensible deviation from spec which had them in graph/)
- Metadata correction safeguard (auto-fix tile_id/page_number) is a smart addition not in the spec

**Extraction accuracy: B+**
- 9/10 ground truth checks pass (90%)
- Page 14: 5/5 — all structures, inverts, pipes match exactly
- Page 36: 4/5 — 3 SSMHs found, 1 pipe found, 1 pipe outside tile subset (not an extraction failure)

**Key finding: Hybrid extraction is more accurate than Phase 1 vision-only.**

The Phase 1 hand-readings (which we used to SET the ground truth) were actually wrong in some cases:

| Value | Phase 1 Hand-Reading | Text Layer (True Value) | Delta |
|---|---|---|---|
| SSMH #1 station | 10+08.08 | 10+06.00 | 2.08' off |
| SSMH #1 RIM | 301.79 | 301.76 | 0.03' off |
| SSMH #2 station | 12+07.18 | 12+07.59 | 0.41' off |
| SSMH #2 RIM | 302.78 | **302.19** | **0.59' off** |
| SSMH #3 station | 14+08.18 | 14+09.19 | 1.01' off |

Our Phase 1 vision reading of RIM 302.78 was actually 302.19 — a 0.59' error that would have been a real consistency check false positive. The text layer caught it. **This is D6 (hybrid extraction) working exactly as designed.**

**Tile boundary text truncation issue found:**

In `p14_r0_c2.json`, pipe notes read `"TALL 342 LF OF 12\" DIA PIPE @ S=0.0030"` — should be `"INSTALL..."`. The text span starts at `bbox_local=(-3, 612)`, meaning 3 points outside the tile's left boundary. PyMuPDF's clip parameter returns partial spans at boundaries, truncating `"INSTALL"` to `"TALL"`.

**Impact:** Low — numeric values (342, 12", 0.0030) are all correct. LLM still correctly identified this as a pipe run. But tile boundary text truncation is a known artifact.

**Possible fix for Q1 (overlap):** Instead of relying on spatial overlap for text, also include any text span whose bbox *intersects* the tile's clip rect (even if its origin is outside). This is a text_layer.py change — include spans where `bbox[2] > clip.x0` (right edge inside clip) even if `bbox[0] < clip.x0` (left edge outside).

**Other observations:**
- Gemini 3 Flash via OpenRouter was used (not Claude) — cost-effective at $0.02/tile
- Metadata correction safeguard fired 12 times in 20 tiles — LLM frequently returns wrong tile_id/page_number
- 57/57 FNC Farms pages scored above coherence threshold (0.80-1.00)
- Calibration scoring has wide tolerances (±5' station, ±1' elevation) — should tighten for production

**Updated ground truth in score_calibration.py should use text layer values, not Phase 1 hand-readings.**

**Decisions to add:**

| # | Decision | Reasoning |
|---|---|---|
| D11 | Gemini Flash for calibration, Claude/Opus for production | $0.02/tile with Flash is ideal for iteration. Switch to higher-accuracy model once pipeline is stable. |
| D12 | Auto-correct tile metadata from text layer, not LLM output | LLM frequently hallucinates tile_id and page_number. Force from the source payload. |

### 2026-02-21 - Task 002 optimization implemented (prompt, retry, cache, scorer, boundary recovery)

Implemented optimization set from `docs/CODEX-TASK-002-OPTIMIZATION.md` with the agreed edits.

Code changes:

- `src/extraction/prompts.py`
  - Added compact hand-written output schema (`COMPACT_SCHEMA`) and switched prompt generation to use it.
  - Trimmed prompt text-layer payload to compact fields only: `id`, `t`, `b` (rounded bbox local coords).
  - Added explicit instruction to omit incomplete entities instead of emitting null for required fields.
- `src/extraction/run_hybrid.py`
  - Reduced default `--max-tokens` from 8192 to 4096.
  - Added retry/backoff for transient API failures (429/5xx, plus timeout/connection errors), 3 attempts with 1s/3s/9s backoff.
  - Added hash-based caching (enabled by default) with `--no-cache` override.
  - Cache key includes prompt + image bytes + model + temperature + max_tokens + cache schema version.
  - Added validation recovery path: if strict validation fails, drop invalid entities/inverts and re-validate.
- `src/extraction/run_hybrid_batch.py`
  - Reduced default `--max-tokens` from 8192 to 4096.
  - Added `--no-cache` plumbing.
  - Updated `--tile-glob` to support multiple values (repeatable flag) and deduped union of matches.
- `src/extraction/score_calibration.py`
  - Updated Page 36 RIM ground truth for STA 12+07.18 from 302.78 to 302.19.
  - Tightened Page 36 tolerances: station tol 3.0 -> 2.0, rim tol 0.8 -> 0.6.
  - Excluded summary/score artifacts (`batch_summary_final.json`, `calibration_score.json`) from extraction loading.
- `src/intake/text_layer.py`
  - Added boundary span recovery for clipped tiles by intersecting full-page spans with a padded clip region.
  - Recomputed coherence metrics from merged span set.
  - Dedupe key uses `(cleaned_text, rounded_bbox)`.

Validation highlights:

1. Prompt token reduction (same style tile, p14_r0_c0):
- Before optimization (`calibration-20`): prompt_tokens ~= 72,686 (prior run), avg prompt tokens across 20 tiles = 31,876.7
- After optimization (`calibration-opt`): prompt_tokens = 11,588 for p14_r0_c0, avg prompt tokens across 20 tiles = 5,883.8

2. Cost reduction (20-tile run):
- Before optimization (`calibration-20`): $0.404731
- After optimization (`calibration-opt`): $0.151463
- Delta: -$0.253268 (-62.6%)

3. Retry behavior verified in live run:
- Observed `ConnectionError` on `p19_r0_c1`, auto-retried and recovered without manual intervention.

4. Cache behavior verified:
- Re-ran identical 20-tile `calibration-opt` batch; all 20 tiles logged `Cache hit ... Skipping API call`.

5. Boundary truncation check:
- Re-tiled Page 14 with updated text-layer recovery and confirmed no standalone truncated `TALL ...` boundary artifact remained in sampled tile JSONs; full `INSTALL 342 LF OF 12"` span present.

6. Calibration scoring:
- `calibration-opt` score: 8/10.
- `calibration-20` with tightened scorer also evaluates to 8/10.
- Accuracy is unchanged relative to the updated scorer baseline; remaining misses are in Page 36 checks (`10+08.08` structure mismatch and 300 LF SS pipe check).

Notes on deviations from original task text:

- Added sanitizer recovery in `run_hybrid.py` to prevent full-tile failure when model emits partial invalid entities. This was necessary after compact prompt/schema changes increased nulls in required fields.
- Multi-glob batch validation command now works directly because `--tile-glob` is repeatable.

### 2026-02-22 - Generalization check on unseen sheet mix (anti-overfit validation)

Ran an out-of-sample robustness test on pages not used in prior calibration subsets:

- Pages: 4, 9, 25, 53
- Mix includes plan/profile/detail-like content with very different annotation density patterns.

Commands run:

1) Build fresh intake tiles/text layers
`python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --pages 4,9,25,53 --output "output/intake-generalization" --grid-rows 2 --grid-cols 3 --overlap-pct 0.10`

2) Run optimized extraction with no cache (real API calls)
`python -m src.extraction.run_hybrid_batch --tiles-dir "output/intake-generalization/tiles" --text-layers-dir "output/intake-generalization/text_layers" --out-dir "output/extractions/generalization-20" --tile-glob "p4_*.png" --tile-glob "p9_*.png" --tile-glob "p25_*.png" --tile-glob "p53_*.png" --max-tiles 20 --model "google/gemini-3-flash-preview" --timeout-sec 180 --no-cache`

Results:

- Candidates processed: 20
- OK: 18
- Skipped (low coherence): 2
- Validation errors: 0
- Runtime errors: 0
- Sanitizer recoveries: 2 tiles

Low-coherence skips (expected behavior):

- `p53_r0_c2` -> coherence 0.0, items 0
- `p53_r1_c2` -> coherence 0.0, items 0

These were automatically skipped by the coherence gate, confirming fallback gating still works on sparse/non-text regions.

Cost/tokens for this generalization run:

- Total cost: ~$0.124678
- Total tokens: 132,506
- Prompt tokens: 109,136
- Avg cost per OK tile: ~$0.006927

Extraction richness sanity check (18 validated JSONs):

- Total structures extracted: 30
- Total pipes extracted: 28
- Total callouts extracted: 88
- Non-empty extraction tiles: 18/18

Interpretation:

- The optimized pipeline is not brittle to only the prior calibration pages.
- It generalizes to a new sheet mix without schema failures.
- Coherence gate correctly avoids garbage extraction on blank/low-text detail regions.
- Cost profile remains low on unseen pages.

### 2026-02-22 - Ground truth correction for Page 36 + builder utility

Implemented requested ground-truth fix and added a dev utility to generate tuple-ready values from live extraction.

Changes:

- `src/extraction/score_calibration.py`
  - Updated Page 36 expected structure tuples to text-layer-aligned values:
    - (`10+06.00`, 301.76)
    - (`12+07.59`, 302.19)
    - (`14+09.19`, 302.90)

- `src/extraction/build_ground_truth.py` (new)
  - CLI utility that:
    1. Tiles a requested page
    2. Runs hybrid extraction via existing batch runner
    3. Prints Python-ready tuples for structures (`station`, `rim`) and optional pipes (`type`, `size`, `slope`, `length`)
  - Example:
    - `python -m src.extraction.build_ground_truth --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --page 36 --structure-type SSMH --pipe-type SS`

Validation:

- Re-ran scorer on existing optimized calibration outputs:
  - `python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-opt`
  - Result: **9/10 (90.0%)**
- This matches the expected improvement from stale hand-read values to text-layer-grounded values.

Minor robustness tweak in builder:

- Deduping of structure rows now keys on `(station, rim, structure_type)` to avoid duplicate tuple lines from small offset-string variations (e.g., `RT` vs `R`).

Cleanup:

- Removed temporary `output/ground-truth/` run artifacts after validation.

### 2026-02-22 - Modules 5-6 implemented (graph merge/assembly/checks)

Implemented the next pass of the pipeline (post-extraction graph stage) with deterministic tests and a real-data smoke run.

Code added/updated:

- `src/graph/merge.py` (new)
  - Exact duplicate merge by parsed location key:
    - `(page_number, structure_type, parse_station(station), parse_signed_offset(offset))`
  - Keeps sanitized-tile entities in merge output (does not drop them).
  - Merge preference rule for overlap duplicates:
    - choose record with most inverts
    - tie-break on longest notes
  - Aggregates provenance on merged nodes:
    - `source_tile_ids`, `source_page_numbers`, `source_text_ids`
  - Carries quality context:
    - `sanitized`, `variants_count`, `rim_elevation_values`

- `src/graph/assembly.py` (new)
  - Builds per-utility directed graph (`SD`, `SS`, `W`) from flat tile extractions.
  - Creates structure nodes from merged records.
  - Adds pipe edges with endpoint matching by:
    - structure hints (`from_structure_hint` / `to_structure_hint`)
    - station proximity (`from_station` / `to_station`)
  - Keeps unmatched pipes by attaching orphan anchor nodes (no hard-fail path).
  - Emits graph-level `quality_summary`:
    - `total_tiles`, `ok_tiles`, `sanitized_tiles`, `skipped_tiles`, `quality_grade`, `warnings`
  - Includes extraction quality threshold warning when `(sanitized + skipped) / total > 0.30`.
  - Added CLI:
    - `python -m src.graph.assembly --extractions-dir <dir> --utility-type SD --out <graph.json>`

- `src/graph/checks.py` (new)
  - Deterministic checks implemented:
    - `check_slope_consistency`
    - `check_connectivity`
    - `check_flow_direction`
    - `check_elevation_consistency`
    - `check_pipe_size_consistency` (cross-list utility helper)
    - `run_all_checks`
  - Findings include provenance fields (`source_sheets`, `source_text_ids`) and node/edge IDs.

- `src/utils/parsing.py`
  - Offset parser expanded to accept `RT/LT` and `R/L`.
  - Added `parse_signed_offset()`:
    - `RT`/`R` => positive
    - `LT`/`L` => negative

- `src/graph/__init__.py`
  - Export surface finalized with lazy loading to avoid `python -m src.graph.assembly` runtime warning.

Tests added:

- `tests/test_graph_merge.py`
  - `test_merge_exact_duplicates`
  - `test_merge_degraded_copy_prefers_complete_record`
  - `test_merge_three_way_overlap`
- `tests/test_graph_assembly.py`
  - `test_build_graph_keeps_orphan_pipe_and_quality_summary`
- `tests/test_graph_checks.py`
  - `test_slope_check`
  - `test_connectivity`
  - `test_flow_direction_backfall`
- `tests/test_parsing.py` updated
  - signed offsets + `R/L` parsing coverage

Validation run:

- Unit tests:
  - `python -m unittest discover -s tests -v`
  - Result: **10/10 passing**
- Real-data graph assembly smoke run (`output/extractions/calibration-opt`):
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-opt --utility-type SD --out output/graphs/calibration-opt-sd.json`
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-opt --utility-type SS --out output/graphs/calibration-opt-ss.json`
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-opt --utility-type W --out output/graphs/calibration-opt-w.json`
  - All completed without runtime errors.

Notes:

- This implementation follows the agreed rules:
  - sanitized tiles included (flagged, not excluded),
  - exact parsed dedupe key,
  - signed offsets,
  - orphan pipes retained with low/none match confidence,
  - warnings emitted instead of hard-failing when quality is degraded.

### 2026-02-22 - Connectivity false-positive reduction pass (orphan/dead-end tuning)

Ran a focused tuning pass to reduce noisy orphan/dead-end findings while keeping deterministic checks.

Changes made:

- `src/graph/merge.py`
  - Tightened utility-type structure filter:
    - removed permissive fallback that accepted any structure type when `utility_types_present` matched.
    - Effect: dropped spurious `structure_type="other"` nodes that were inflating orphan counts.

- `src/graph/assembly.py`
  - Added station extraction from structure hints when explicit `from_station`/`to_station` is missing.
  - Added one-endpoint inference heuristic:
    - if one endpoint is matched and `length_lf` exists, infer the missing endpoint by nearest station to `anchor_station ± length`.
    - confidence assigned by station delta (`high/medium/low`).

- `src/graph/checks.py`
  - Connectivity now handles low-confidence contexts more explicitly:
    - If graph has pipe edges but no structure nodes, emit one `connectivity_unverifiable` info finding and skip dead-end spam.
    - If extraction quality is degraded (>30% sanitized+skipped), suppress per-node orphan warnings and emit summary info:
      - `orphan_node_check_suppressed`
    - Reclassified unresolved pipes with no station/hint metadata as:
      - `unanchored_pipe` (info), not `dead_end_pipe` warning.
    - Keep `dead_end_pipe` warnings only when endpoint anchor data exists but matching remains unresolved.

- Tests updated:
  - `tests/test_graph_checks.py`
    - `test_connectivity` now expects `unanchored_pipe` for no-anchor unresolved edges.
    - Added `test_connectivity_unverifiable_when_no_structure_nodes`.

Validation:

- Unit tests:
  - `python -m unittest discover -s tests -v`
  - Result: **11/11 passing**

- Rebuilt graphs + findings on same dataset (`output/extractions/calibration-opt`) and compared counts:

Before (pre-tuning):
- SD: 46 findings (`orphan_node=38`, `dead_end_pipe=6`)
- SS: 10 findings (`dead_end_pipe=7`)
- W: 13 findings (`dead_end_pipe=13`)

After (post-tuning):
- SD: 10 findings (`dead_end_pipe=1`, `unanchored_pipe=3`, `orphan_node_check_suppressed=1`)
- SS: 11 findings (`dead_end_pipe=3`, `unanchored_pipe=3`)
- W: 1 finding (`connectivity_unverifiable=1`)

Updated outputs:

- Graph JSON:
  - `output/graphs/calibration-opt-sd.json`
  - `output/graphs/calibration-opt-ss.json`
  - `output/graphs/calibration-opt-w.json`
- Findings JSON:
  - `output/graphs/findings/calibration-opt-sd-findings.json`
  - `output/graphs/findings/calibration-opt-ss-findings.json`
  - `output/graphs/findings/calibration-opt-w-findings.json`

### 2026-02-22 - Task 003 graph fixes implemented (pipe dedup, GB filter, directional inverts)

Implemented `docs/CODEX-TASK-003-GRAPH-FIXES.md` end-to-end and validated against a fresh no-cache extraction run.

Code changes:

- `src/graph/assembly.py`
  - Added pipe-edge dedup pass `_deduplicate_pipe_edges(...)` after edge creation.
  - Dedup groups by unordered endpoint pair, then merges edges with similar signature (size + length/slope tolerance).
  - Keeps best edge by confidence/metadata ranking and merges provenance from dropped duplicates:
    - `source_tile_ids`, `source_page_numbers`, `source_text_ids`.
  - Added endpoint metadata backfill from dropped edges when kept edge is sparse.
  - Downgraded inferred endpoint confidence from station+length heuristic to avoid outranking explicit endpoint data.

- `src/graph/merge.py`
  - Removed `GB` from SD utility type set.
  - Added fallback to include `GB` only when inverts are present (`has_inverts=True`).

- `src/graph/checks.py`
  - Added directional invert helper `_get_directional_invert(...)`.
  - Updated slope and flow-direction checks to use directional invert selection first, then representative fallback.

Tests added/updated:

- `tests/test_graph_assembly.py`
  - `test_pipe_dedup_keeps_highest_confidence`
  - `test_pipe_dedup_reversed_direction`
- `tests/test_graph_merge.py`
  - `test_gb_without_inverts_excluded_from_sd`
  - `test_gb_with_inverts_included_in_sd`
- `tests/test_graph_checks.py`
  - `test_slope_uses_directional_invert`

Validation:

- Unit tests:
  - `python -m unittest discover -s tests -v`
  - Result: **16/16 passing**

- Fresh extraction run (no cache) on pages 14,19,34,36:
  - `python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --pages 14,19,34,36 --output output/intake-pass2`
  - `python -m src.extraction.run_hybrid_batch --tiles-dir output/intake-pass2/tiles --text-layers-dir output/intake-pass2/text_layers --out-dir output/extractions/calibration-clean --max-tiles 24 --model "google/gemini-3-flash-preview" --timeout-sec 180 --no-cache`
  - Batch result: 24/24 `ok`, 0 validation errors, 0 runtime errors.

- Calibration score:
  - `python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-clean`
  - Result: **9/10 (90.0%)**

- Graph outputs (`calibration-clean`):
  - `output/graphs/calibration-clean-sd.json`
  - `output/graphs/calibration-clean-ss.json`
  - `output/graphs/calibration-clean-w.json`
  - Findings:
    - `output/graphs/findings/calibration-clean-sd-findings.json`
    - `output/graphs/findings/calibration-clean-ss-findings.json`
    - `output/graphs/findings/calibration-clean-w-findings.json`

Checklist outcomes:

- SD graph node makeup corrected:
  - Structure nodes: 4 (`SDMH`/`inlet`), GBs removed unless invert-bearing.
- SD duplicate reversed-edge false positives resolved:
  - `flow_direction_error` in SD: **0**
  - Duplicate edge groups in SD: **0**
- Boundary truncation check:
  - No true `"TALL 342 LF..."` artifacts found in pass2 text layers or calibration-clean extractions.
  - `INSTALL 342 LF OF 12"` present in all relevant tiles.
- Sanitizer rate re-check:
  - 5/24 tiles sanitized (20.8%, below 30% threshold).
  - Concentrated on `p14_r1_c0`, `p14_r1_c1`, and `p19_r1_*`.
  - Dropped invalid totals: `structures=16`, `pipes=2`, `inverts=0`, `callouts=0`.

### 2026-02-22 - Task 004 implemented (orientation + offset fallback + proximity merge)

Implemented `docs/CODEX-TASK-004-GRAPH-ORIENTATION-AND-PROXIMITY-MERGE.md` with user-approved adjustments:

- Accepted tweaks applied:
  - offset fallback uses `abs(signed_offset_ft)` for same-station direction selection.
  - proximity merge regenerates `node_id` from merged primary attributes.
  - gravity reorientation preserves provenance (`oriented_by_gravity`, original endpoints).
- Proximity merge tolerance set to `station_tol=3.0` (no rim guard).
- Gravity orientation applied to both `SD` and `SS`.

Code changes:

- `src/graph/merge.py`
  - Added second-pass `_proximity_merge(...)` for near-duplicate same-page/same-type structures.
  - Added `_collapse_merged_group(...)` to combine provenance and keep best primary data.
  - Proximity-merged node IDs now regenerated deterministically using `_make_node_id(...)`.

- `src/graph/assembly.py`
  - Added `_orient_gravity_edges(...)` and called it after pipe dedup.
  - Edges with uphill orientation (from invert < to invert) are flipped for SD/SS.
  - Flipped edges retain orientation audit fields:
    - `oriented_by_gravity: true`
    - `original_from_node`
    - `original_to_node`

- `src/graph/checks.py`
  - Updated `_get_directional_invert(...)`:
    - station-based E/W heuristic now only when station delta exceeds ±0.5 ft.
    - same-station fallback uses absolute signed offsets to pick N/S direction hints.

Tests added:

- `tests/test_graph_checks.py`
  - `test_directional_invert_offset_fallback`
- `tests/test_graph_assembly.py`
  - `test_gravity_orientation_flips_uphill_edge`
  - `test_gravity_orientation_preserves_correct_direction`
- `tests/test_graph_merge.py`
  - `test_proximity_merge_collapses_nearby_structures`
  - `test_proximity_merge_preserves_distinct_structures`

Validation:

- Unit tests:
  - `python -m unittest discover -s tests -v`
  - Result: **21/21 passing**

- Rebuilt graphs from existing clean extractions (`output/extractions/calibration-clean`):
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SD --out output/graphs/calibration-clean-sd.json`
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SS --out output/graphs/calibration-clean-ss.json`
  - `python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type W --out output/graphs/calibration-clean-w.json`

- Re-ran checks and wrote updated findings:
  - `output/graphs/findings/calibration-clean-sd-findings.json`
  - `output/graphs/findings/calibration-clean-ss-findings.json`
  - `output/graphs/findings/calibration-clean-w-findings.json`

Outcome vs Task 004 checklist:

- SD findings: **4** (from 6 pre-Task-004)
  - types: `dead_end_pipe=1`, `unanchored_pipe=3`
  - `slope_mismatch` on same-station edge removed.
- SD errors: **0**
- SS structure nodes reduced to **5** (from 7 pre-Task-004 in calibration-clean run).
- SS `flow_direction_error`: **0** (from 4 pre-Task-004).
- SS `slope_mismatch`: **3** (from 4 pre-Task-004).
- SS total findings: **10** (down from 13 pre-Task-004).
- W findings: unchanged, `connectivity_unverifiable=1` (info).
- No uphill edges remain in SD/SS when checked by representative invert ordering.

Additional validation:

- Calibration scorer unchanged:
  - `python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-clean`
  - Result: **9/10 (90.0%)**

### 2026-02-22 - Documentation sweep + README added

Completed a documentation tidy pass and added a user-friendly entry point for GitHub readers.

Changes:

- Added `README.md` with:
  - project purpose and current status
  - prerequisites and setup
  - command cookbook (intake, extraction, scoring, graph build, tests)
  - documentation map and repository notes

- Updated `ARCHITECTURE.md` doc conventions section:
  - now includes `README.md` and `PROGRESS_SUMMARY.md`
  - adjusted repository layout to reflect current active `src/` pipeline
  - removed outdated wording that prohibited new top-level docs

- Tidied `PROGRESS_SUMMARY.md` validation bullets for readability/scanability.

Result:

- Docs now follow a clear hierarchy:
  - `README.md` = fast onboarding
  - `ARCHITECTURE.md` = system design
  - `PROGRESS_SUMMARY.md` = day-level milestones
  - `PROGRESS.md` = detailed engineering log

### 2026-02-22 - Task 005 implemented (HTML report generator)

Implemented report UI planning output from `docs/CODEX-TASK-005-HTML-REPORT.md` as a standalone CLI module.

What was delivered:

- Added report package:
  - `src/report/__init__.py`
  - `src/report/html_report.py`
- Added report smoke tests:
  - `tests/test_html_report.py`
- Updated command docs:
  - `README.md` (new HTML report command)

Implementation details:

- Built dependency-free single-file HTML renderer with inline CSS and no server/framework requirements.
- Added schema-tolerant loading for utility artifacts:
  - gracefully handles missing `SD`/`SS`/`W` graph or findings files
  - emits a `Data Warnings` section in the report instead of failing
- Added quality risk banner:
  - banner appears when `(sanitized_tiles + skipped_tiles) / total_tiles > 0.30`
- Added page detection fallback:
  - primary source: `batch_summary.json` tile IDs
  - fallback: union of `source_page_numbers` / `source_sheets` from graph/findings payloads
- Preserved station string display while sorting numerically under the hood.
- Added provenance columns to structure and pipe schedules:
  - `pg <pages>, <tile_count> tile(s)`
- Included gravity-orientation visibility in pipe confidence text:
  - adds `gravity-oriented` when `oriented_by_gravity=true`
- Added optional `--title` CLI override for report headers.

Validation:

- New smoke tests:
  - `python -m unittest tests.test_html_report -v`
  - Result: **2/2 passing**
- Full suite:
  - `python -m unittest discover -s tests -v`
  - Result: **23/23 passing**
- Real artifact generation:
  - `python -m src.report.html_report --graphs-dir output/graphs --findings-dir output/graphs/findings --prefix calibration-clean --batch-summary output/extractions/calibration-clean/batch_summary.json --out output/reports/calibration-clean-report.html`
  - Result: report written successfully to `output/reports/calibration-clean-report.html`

### 2026-02-22 - Model routing update (lite default with automatic escalation)

Updated extraction model routing so `google/gemini-2.5-flash-lite` remains the default, with automatic escalation to `google/gemini-3-flash-preview` when quality risk is detected.

Code changes:

- `src/extraction/run_hybrid.py`
  - Added escalation constants:
    - `DEFAULT_ESCALATION_MODEL = "google/gemini-3-flash-preview"`
    - `DEFAULT_ESCALATION_COHERENCE_THRESHOLD = 0.70`
  - Added automatic escalation triggers:
    - low text-layer coherence (`coherence_score < threshold`)
    - API call failure
    - JSON parse / shape failure
    - unrecoverable schema validation failure
    - sanitizer recovery usage (`sanitized=True`)
  - Added escalation metadata fields in `.meta.json`:
    - `attempted_models`
    - `escalated`
    - `escalation_reason`
    - `escalation_model`
    - `escalation_enabled`
    - `escalation_coherence_threshold`
  - Added CLI flags:
    - `--escalation-model`
    - `--escalation-coherence-threshold`
    - `--escalation / --no-escalation`

- `src/extraction/run_hybrid_batch.py`
  - Propagates escalation settings to each tile extraction call.
  - Added matching batch CLI flags.
  - Stores escalation settings in `batch_summary.json`.

- `README.md`
  - Documented automatic escalation behavior and `--no-escalation`.

Tests added:

- `tests/test_run_hybrid_escalation.py`
  - `test_low_coherence_escalates_to_fallback_model`
  - `test_sanitized_primary_output_escalates_to_fallback`

Validation:

- `python -m unittest tests.test_run_hybrid_escalation -v`
  - Result: **2/2 passing**
- `python -m unittest discover -s tests -v`
  - Result: **25/25 passing**
- CLI sanity:
  - `python -m src.extraction.run_hybrid --help` (new escalation flags present)
  - `python -m src.extraction.run_hybrid_batch --help` (new escalation flags present)

### 2026-02-22 - Model default reverted to preview

After comparative cost/quality checks, reverted extraction default model back to `google/gemini-3-flash-preview`.

Changes:

- `src/extraction/run_hybrid.py`
  - `DEFAULT_MODEL` reset to `google/gemini-3-flash-preview`
- `README.md`
  - batch example and default-model note updated to preview default
- `tests/test_run_hybrid_escalation.py`
  - tests now use an explicit lite primary (`google/gemini-2.5-flash-lite`) to keep fallback behavior covered regardless of runtime default

Validation:

- `python -m unittest tests.test_run_hybrid_escalation -v` -> **2/2 passing**
- `python -m unittest discover -s tests -v` -> **25/25 passing**

### 2026-02-22 - Task 006 implemented (crown/invert heuristic)

Implemented `docs/CODEX-TASK-006-CROWN-INVERT-HEURISTIC.md` with edge-aware crown contamination handling at graph/check stage.

Code changes:

- `src/graph/assembly.py`
  - Added `_parse_pipe_diameter_ft(...)`
  - Added `_filter_suspect_crowns(graph)` with two passes:
    - Pass 1: multi-invert spread filtering into `crown_suspects`
    - Pass 2: cross-edge anomaly flagging via `crown_contamination_candidate` and `suspect_crown`
  - Integrated crown filter before edge dedup/orientation in `build_utility_graph(...)`
  - Preserved edge-level crown flag in `_merge_edge_provenance(...)`
  - Crown filtering scoped to gravity systems only (`SD`, `SS`), skipped for `W`

- `src/graph/checks.py`
  - Updated `check_slope_consistency(...)` to reclassify likely crown-driven mismatches to:
    - `finding_type="crown_contamination"`
    - `severity="info"`
  - Uses absolute labeled slope (`abs(labeled_slope)`) for robust ratio checks

- `tests/test_graph_checks.py`
  - Added crown heuristic tests:
    - multi-invert crown removal to `crown_suspects`
    - single-invert edge contamination candidate
    - `slope_mismatch` -> `crown_contamination` reclassification
    - non-crown mismatch remains warning
    - water utility skip behavior

Validation:

- Targeted tests:
  - `python -m unittest tests.test_graph_checks -v` -> **11/11 passing**
- Full suite:
  - `python -m unittest discover -s tests -v` -> **30/30 passing**

Corridor validation (using existing extractions in `output/extractions/corridor-u1u2-postfix`):

- Generated:
  - `output/graphs/corridor-u1u2-crownfix-sd.json`
  - `output/graphs/corridor-u1u2-crownfix-ss.json`
  - `output/graphs/corridor-u1u2-crownfix-w.json`
  - `output/graphs/findings/corridor-u1u2-crownfix-sd-findings.json`
  - `output/graphs/findings/corridor-u1u2-crownfix-ss-findings.json`
  - `output/graphs/findings/corridor-u1u2-crownfix-w-findings.json`
  - `output/reports/corridor-u1u2-crownfix-report.html`

Key result:

- Corridor SD `slope_mismatch`: **4 -> 0**
- Corridor SD `crown_contamination`: **4**
- Corridor overall severity: `warning=9, info=17, error=0`

FNC regression validation (using available extraction dir `output/extractions/calibration-clean-regression`):

- Generated:
  - `output/graphs/calibration-crownfix-sd.json`
  - `output/graphs/calibration-crownfix-ss.json`
  - `output/graphs/calibration-crownfix-w.json`
  - `output/graphs/findings/calibration-crownfix-sd-findings.json`
  - `output/graphs/findings/calibration-crownfix-ss-findings.json`
  - `output/graphs/findings/calibration-crownfix-w-findings.json`
  - `output/reports/calibration-crownfix-report.html`

Key result:

- FNC severity totals remained at `warning=16, info=12, error=0` (no regression vs current post-fix baseline)
- Verified no crown flags on water graph nodes in corridor crownfix output (`0/19`)

### 2026-02-23 - Task 007 reference-sheet suppression (+ page fallback)

Implemented and validated `docs/CODEX-TASK-007-REFERENCE-SHEET-SUPPRESSION.md` with an additional fallback for mixed tile classification on reference sheets.

Code changes:

- `src/graph/assembly.py`
  - Added `_reference_only_pages(extractions)` to infer reference pages when:
    - at least one tile is `signing_striping`
    - no tile is `profile_view`
  - Added page-level fallback in `build_utility_graph(...)`:
    - `is_reference_only=True` for edges on inferred reference pages, even when a tile is misclassified as `plan_view`
  - Preserved `is_reference_only` during edge dedup merge (`_merge_edge_provenance(...)`)

- `src/graph/checks.py`
  - `check_connectivity(...)` now skips edges with `is_reference_only=True`

- `tests/test_graph_checks.py`
  - Added:
    - `test_reference_only_edge_suppresses_unanchored`
    - `test_non_reference_edge_still_flags_unanchored`

- `tests/test_graph_assembly.py`
  - Added:
    - `test_reference_only_page_fallback_flags_misclassified_tile`

Validation:

- `python -m unittest discover -s tests -v` -> **33/33 passing**

Corridor-expanded refresh (`output/extractions/corridor-expanded` reused):

- Regenerated findings:
  - `output/graphs/findings/corridor-expanded-sd-findings.json`
  - `output/graphs/findings/corridor-expanded-ss-findings.json`
  - `output/graphs/findings/corridor-expanded-w-findings.json`
- Regenerated report:
  - `output/reports/corridor-expanded-report.html`

Observed impact:

- SD findings: **6 -> 3**
  - `dead_end_pipe`: **1 -> 0**
  - `unanchored_pipe`: **2 -> 0**
- SS findings: **18 -> 11** (from prior post-Task007 state)
  - All connectivity findings from pages **93/103** suppressed (`0` remaining on those pages)
- W findings: **24 -> 14** (reference connectivity noise reduced)

FNC regression check (`output/extractions/calibration-clean`):

- `is_reference_only=True` edges: **0** for SD/SS/W
- Severity totals unchanged at **warning=8, info=7, error=0** (no regression)

### 2026-02-23 - Task 008 null page-number recovery (pre-validation)

Reviewed `docs/CODEX-TASK-008-NULL-PAGE-NUMBER-RECOVERY.md` and implemented the core idea, plus one hardening improvement.

Code changes:

- `src/extraction/run_hybrid.py`
  - Added `_pre_correct_tile_metadata(payload, text_layer)` and invoked it before first `TileExtraction.model_validate(...)`.
  - Added `_coerce_int(...)` and `_page_number_from_tile_id(...)` helpers.
  - Added `_TILE_ID_PAGE_RE` for deriving page number from `tile_id` pattern (`p26_r1_c0 -> 26`).
  - Hardened post-validation correction logic:
    - `expected_page_number` now safely resolves from text layer or tile_id fallback instead of direct `int(...)` cast.

Notes:
- This preserves strict schema requirements (`page_number` still required in `TileExtraction`).
- It only patches null/missing top-level metadata before validation; wrong-but-valid integers are still corrected in existing post-validation logic.

Tests:

- `tests/test_run_hybrid_escalation.py`
  - Added `test_null_page_number_recovered_from_text_layer`
  - Added `test_null_metadata_recovered_from_tile_id_when_text_layer_page_missing`

Validation:

- `python -m unittest tests.test_run_hybrid_escalation -v` -> **4/4 passing**
- `python -m unittest discover -s tests -v` -> **35/35 passing**

Corridor-expanded re-run (cache-aware):

- Re-ran batch extraction:
  - `python -m src.extraction.run_hybrid_batch --tiles-dir output/intake-corridor-expanded/tiles --text-layers-dir output/intake-corridor-expanded/text_layers --out-dir output/extractions/corridor-expanded --model google/gemini-3-flash-preview --timeout-sec 180`
- Result: **30/30 OK** (`validation_error: 0`)
- Recovered tile:
  - `output/extractions/corridor-expanded/p26_r1_c0.json.meta.json` status now `ok`
  - `output/extractions/corridor-expanded/p26_r1_c0.json` exists with `tile_id: p26_r1_c0`, `page_number: 26`

Downstream refresh:

- Regenerated graphs/findings/report for `corridor-expanded`.
- Findings remained stable for SD/SS after prior Task 007 suppression; W shifted slightly with recovered tile data:
  - SD findings: `3`
  - SS findings: `11`
  - W findings: `14` (type split now `dead_end_pipe=7`, `unanchored_pipe=6`)
