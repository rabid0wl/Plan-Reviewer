# Coding Spec: Intake & Extraction Pipeline

**Date:** 2026-02-21
**For:** Any coding agent (Gemini, Codex, Claude, etc.)
**Context:** Civil engineering plan review tool. This spec covers the PDF intake pipeline — going from a raw PDF plan set to tile images + text layer JSONs ready for LLM extraction.

> **IMPORTANT:** Read `ARCHITECTURE.md` and `PROGRESS.md` in the project root for full context. This spec implements Pipeline Phases 1, 3, and 3.5 from the architecture.

---

## Project Setup

**Language:** Python 3.13 (already installed at `C:\Python313\`)
**Key dependency:** PyMuPDF (fitz) v1.27.1 (already installed)
**OS:** Windows 11
**Project root:** `C:\Users\dylan\Documents\AI\Projects\Plan Reviewer\`

### Directory Structure to Create

```
src/
├── __init__.py
├── intake/
│   ├── __init__.py
│   ├── tiler.py              # PDF → tile PNGs + text layer JSONs
│   ├── text_layer.py         # Text extraction with bounding boxes + coherence scoring
│   ├── manifest.py           # Sheet manifest generation (cover index + title blocks)
│   └── models.py             # Pydantic models for all data structures
├── extraction/
│   ├── __init__.py
│   ├── schemas.py            # Flat extraction schemas (structures[], pipes[], callouts[])
│   └── prompts.py            # Prompt templates for hybrid extraction
├── graph/
│   ├── __init__.py
│   ├── assembly.py           # Flat extractions → NetworkX graph
│   ├── merge.py              # Cross-sheet node merging + overlap deduplication
│   └── checks.py             # Deterministic consistency checks on graph
└── utils/
    ├── __init__.py
    └── unicode.py             # Handle ⌀ symbol and other civil engineering unicode
```

### Dependencies to Install

```
pip install pydantic networkx
```

PyMuPDF is already installed. Do NOT install pdftoppm, ImageMagick, pdf2image, or pdfplumber — they are not available on this machine and not needed.

---

## Module 1: `src/intake/tiler.py`

### Purpose
Takes a PDF page and produces a 3x2 grid of tile PNGs with configurable overlap, plus a matching text layer JSON for each tile.

### Key Architectural Decisions (do not deviate)
- **D4:** 3x2 tile grid at 300 DPI. Each tile ≈ 3600x2400px for a 36"x24" ARCH D sheet.
- **D2:** Use PyMuPDF (fitz) exclusively. The `clip` parameter on `page.get_pixmap()` and `page.get_text()` renders/extracts only within the clip region — no need to render full page then crop.
- **10% overlap** between adjacent tiles (Q1 — assumed, may be adjusted after testing).

### Function Signatures

```python
from pathlib import Path
from dataclasses import dataclass
import fitz


@dataclass
class TileInfo:
    """Metadata for a single tile."""
    tile_id: str              # e.g., "p14_r0_c1" (page 14, row 0, col 1)
    page_number: int          # 1-indexed PDF page number
    row: int                  # 0 or 1
    col: int                  # 0, 1, or 2
    clip_rect: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points
    image_path: Path          # Path to saved PNG
    text_layer_path: Path     # Path to saved text layer JSON
    image_width_px: int
    image_height_px: int


def tile_page(
    doc: fitz.Document,
    page_index: int,          # 0-indexed
    output_dir: Path,
    dpi: int = 300,
    grid_rows: int = 2,
    grid_cols: int = 3,
    overlap_pct: float = 0.10,
) -> list[TileInfo]:
    """
    Extract tiles from a single PDF page.

    Returns list of TileInfo with paths to saved PNGs and text layer JSONs.

    Tile naming: p{page_number}_r{row}_c{col}.png / .json
    Example: p14_r0_c1.png, p14_r0_c1.json
    """
    ...


def tile_pdf(
    pdf_path: Path,
    output_dir: Path,
    page_numbers: list[int] | None = None,  # 1-indexed, None = all pages
    dpi: int = 300,
    grid_rows: int = 2,
    grid_cols: int = 3,
    overlap_pct: float = 0.10,
) -> dict[int, list[TileInfo]]:
    """
    Tile an entire PDF (or specific pages).

    Returns dict mapping page_number → list of TileInfo.
    Creates subdirectories: output_dir/tiles/, output_dir/text_layers/
    """
    ...
```

### Tiling Geometry Calculation

For a page of width `W` and height `H` in PDF points (FNC Farms: W=2592, H=1728):

```python
# Base tile dimensions (without overlap)
base_w = W / grid_cols   # 2592 / 3 = 864 pts
base_h = H / grid_rows   # 1728 / 2 = 864 pts

# Overlap in points
overlap_w = base_w * overlap_pct  # 86.4 pts
overlap_h = base_h * overlap_pct  # 86.4 pts

# Tile clip rectangles (with overlap extending into adjacent tiles)
for row in range(grid_rows):
    for col in range(grid_cols):
        x0 = col * base_w - (overlap_w if col > 0 else 0)
        y0 = row * base_h - (overlap_h if row > 0 else 0)
        x1 = (col + 1) * base_w + (overlap_w if col < grid_cols - 1 else 0)
        y1 = (row + 1) * base_h + (overlap_h if row < grid_rows - 1 else 0)

        # Clamp to page bounds
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(W, x1)
        y1 = min(H, y1)

        clip = fitz.Rect(x0, y0, x1, y1)
```

### PNG Rendering

```python
zoom = dpi / 72  # 300/72 = 4.167
mat = fitz.Matrix(zoom, zoom)
pix = page.get_pixmap(matrix=mat, clip=clip)
pix.save(str(image_path))
```

### Text Layer Extraction Per Tile

Call `page.get_text("dict", clip=clip)` for each tile's clip rect. See Module 2 for the text layer format.

---

## Module 2: `src/intake/text_layer.py`

### Purpose
Extract text with bounding boxes from a PDF page/region. Calculate coherence score to detect SHX font issues.

### Key Architectural Decisions
- **D6:** Hybrid extraction — text layer provides ground-truth numbers, vision provides spatial understanding.
- **D8:** Coherence gate — score text quality before deciding hybrid vs. OCR fallback.
- **D9:** Data provenance — every text item gets a sequential `text_id` and both local (tile) and global (page) bounding boxes.

### Data Structure

```python
@dataclass
class TextItem:
    """A single text span from the PDF with provenance."""
    text_id: int                    # Sequential ID within this tile
    text: str                       # The actual text string (unicode-cleaned)
    bbox_local: tuple[float, float, float, float]   # (x0,y0,x1,y1) relative to tile origin
    bbox_global: tuple[float, float, float, float]  # (x0,y0,x1,y1) in full-page PDF points
    font: str                       # Font name (e.g., "ArialNarrow")
    font_size: float                # Font size in points


@dataclass
class TextLayer:
    """All text items for a single tile, with quality metrics."""
    tile_id: str
    page_number: int
    items: list[TextItem]
    coherence_score: float          # 0.0-1.0, multi-char spans / total spans
    total_spans: int
    multi_char_spans: int
    numeric_spans: int              # Spans containing digits and len > 1
    primary_font: str               # Most common font name
    is_hybrid_viable: bool          # coherence_score > threshold (0.40)
```

### Function Signatures

```python
def extract_text_layer(
    page: fitz.Page,
    clip: fitz.Rect | None = None,
    clip_origin: tuple[float, float] = (0.0, 0.0),  # For local bbox calculation
) -> TextLayer:
    """
    Extract all text spans from a page (or clipped region) with bounding boxes.

    Args:
        page: PyMuPDF page object
        clip: Optional clip rectangle (PDF points). None = full page.
        clip_origin: (x0, y0) of clip rect for calculating local coordinates.

    Returns:
        TextLayer with all text items and coherence metrics.
    """
    ...


def calculate_coherence(text_dict: dict) -> tuple[float, int, int, int, str]:
    """
    Calculate text coherence score from PyMuPDF text dict.

    Returns: (coherence_score, total_spans, multi_char_spans, numeric_spans, primary_font)

    Coherence = multi_char_spans / total_spans

    Thresholds (from empirical testing on FNC Farms):
    - > 0.60: High coherence, hybrid extraction viable (FNC Farms range: 0.65-0.89)
    - 0.30-0.60: Medium, may be partial SHX — inspect before proceeding
    - < 0.30: Low, likely SHX or rasterized — need OCR fallback
    """
    ...


COHERENCE_THRESHOLD = 0.40  # Below this, text layer is unreliable
```

### Unicode Cleaning

Civil engineering PDFs commonly contain these problematic characters:

```python
UNICODE_REPLACEMENTS = {
    '\u2205': 'DIA',    # ⌀ (empty set used as diameter symbol)
    '\u00d8': 'DIA',    # Ø (Latin O with stroke, also used for diameter)
    '\u2300': 'DIA',    # ⌀ (actual diameter sign)
    '\u00b0': 'deg',    # ° (degree symbol)
    '\u2032': "'",       # ′ (prime, used for feet)
    '\u2033': '"',       # ″ (double prime, used for inches)
    '\u00b1': '+/-',     # ± (plus-minus)
}

def clean_unicode(text: str) -> str:
    """Replace problematic unicode characters with ASCII equivalents."""
    for char, replacement in UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text
```

### Text Layer JSON Output Format

Each tile's text layer is saved as JSON. This is the exact payload that gets sent to the LLM alongside the tile image.

```json
{
  "tile_id": "p14_r0_c2",
  "page_number": 14,
  "coherence_score": 0.72,
  "total_spans": 280,
  "multi_char_spans": 201,
  "primary_font": "ArialNarrow",
  "is_hybrid_viable": true,
  "items": [
    {
      "text_id": 0,
      "text": "12'' SD",
      "bbox_local": [55.0, 349.0, 75.0, 357.0],
      "bbox_global": [1091.0, 608.0, 1111.0, 616.0],
      "font": "ArialNarrow",
      "font_size": 7.7
    },
    {
      "text_id": 8,
      "text": "INV. 12\" E 300.80",
      "bbox_local": [0.0, 529.0, 68.0, 539.0],
      "bbox_global": [1036.0, 788.0, 1104.0, 798.0],
      "font": "ArialNarrow",
      "font_size": 10.3
    }
  ]
}
```

---

## Module 3: `src/extraction/schemas.py`

### Purpose
Define the flat JSON schemas the LLM outputs during Phase 3 extraction. These are Pydantic models that validate LLM responses.

### Key Architectural Decisions
- **D10:** Flat lists, NOT nested graph. The LLM outputs `structures[]`, `pipes[]`, `callouts[]` as separate flat arrays. Python assembles the graph later.
- **D9:** Every extracted value references `source_text_ids` — the `text_id` values from the text layer that provided the value.
- **D3:** Schema-driven extraction produces far better output than open-ended prompts.

### Pydantic Models

```python
from pydantic import BaseModel, Field


class InvertElevation(BaseModel):
    """A single invert at a structure."""
    direction: str = Field(description="Compass direction: N, S, E, W, NE, NW, SE, SW")
    pipe_size: str = Field(description="Pipe diameter, e.g., '12\"' or '8\"'")
    pipe_type: str | None = Field(default=None, description="SD, SS, or W if identifiable")
    elevation: float = Field(description="Invert elevation to nearest 0.01'")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer that provided this value")


class Structure(BaseModel):
    """A utility structure (manhole, catch basin, inlet, cleanout, etc.)."""
    id: str | None = Field(default=None, description="Structure ID if labeled (e.g., 'MH-1', 'CB-3')")
    structure_type: str = Field(description="SDMH, SSMH, CB, GB, inlet, cleanout, junction, etc.")
    size: str | None = Field(default=None, description="Structure size, e.g., '48\"'")
    station: str = Field(description="Station, e.g., '16+82.45'")
    offset: str = Field(description="Offset with direction, e.g., '28.00' RT'")
    rim_elevation: float | None = Field(default=None, description="Rim elevation")
    tc_elevation: float | None = Field(default=None, description="Top of curb elevation if applicable")
    fl_elevation: float | None = Field(default=None, description="Flowline elevation if applicable")
    inverts: list[InvertElevation] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Any additional annotation text")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer for station/offset/rim")


class Pipe(BaseModel):
    """A pipe run between two structures or points."""
    pipe_type: str = Field(description="SD (storm drain), SS (sanitary sewer), or W (water)")
    size: str = Field(description="Pipe diameter, e.g., '12\"'")
    material: str | None = Field(default=None, description="Pipe material: RCP, PVC, DIP, HDPE, etc.")
    length_lf: float | None = Field(default=None, description="Pipe length in linear feet")
    slope: float | None = Field(default=None, description="Pipe slope as decimal, e.g., 0.0020")
    from_station: str | None = Field(default=None, description="Starting station if identifiable")
    to_station: str | None = Field(default=None, description="Ending station if identifiable")
    from_structure_hint: str | None = Field(default=None, description="Nearby structure description for graph assembly")
    to_structure_hint: str | None = Field(default=None, description="Nearby structure description for graph assembly")
    notes: str | None = Field(default=None, description="Installation notes, e.g., 'INSTALL 342 LF OF 12\" DIA SD PIPE'")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer")


class Callout(BaseModel):
    """Any other annotation/callout that isn't a structure or pipe."""
    callout_type: str = Field(description="Category: edge_of_pavement, detail_reference, cross_reference, "
                                          "grading_note, installation_note, match_existing, cover_depth, other")
    text: str = Field(description="Full text of the callout")
    station: str | None = Field(default=None, description="Station if applicable")
    offset: str | None = Field(default=None, description="Offset if applicable")
    elevation: float | None = Field(default=None, description="Elevation if applicable")
    reference_sheet: str | None = Field(default=None, description="Referenced sheet number, e.g., 'SEE SHEET 16'")
    reference_detail: str | None = Field(default=None, description="Detail bubble reference, e.g., 'D7'")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer")


class TileExtraction(BaseModel):
    """Complete extraction from a single tile — FLAT structure, no nesting."""
    tile_id: str = Field(description="Tile identifier, e.g., 'p14_r0_c2'")
    page_number: int
    sheet_type: str = Field(description="plan_view, profile_view, detail_sheet, grading, signing_striping, cover, notes, other")
    utility_types_present: list[str] = Field(description="List of utility types visible: SD, SS, W, etc.")

    structures: list[Structure] = Field(default_factory=list)
    pipes: list[Pipe] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)

    street_names: list[str] = Field(default_factory=list, description="All street names visible in tile")
    lot_numbers: list[int] = Field(default_factory=list, description="All lot numbers visible in tile")

    extraction_notes: str | None = Field(default=None, description="Any issues or ambiguities encountered during extraction")
```

---

## Module 4: `src/extraction/prompts.py`

### Purpose
Build the hybrid extraction prompt: tile image + text layer JSON → TileExtraction JSON.

### Prompt Template

```python
HYBRID_EXTRACTION_PROMPT = """You are a civil engineering plan extraction agent. You will receive:
1. An IMAGE of a tile (cropped section) from a civil improvement plan sheet
2. A TEXT LAYER JSON containing all text strings found in this tile with exact positions

## CRITICAL RULES
- For ALL numeric values (elevations, stations, pipe sizes, slopes, lengths), you MUST use the
  exact values from the text layer JSON. Do NOT estimate or read numbers from the image.
- For EVERY value you extract, include the text_id(s) from the text layer that provided it
  in the source_text_ids field.
- Use the IMAGE for spatial understanding: which annotations belong together, what connects
  to what, which pipe a callout refers to, layout and flow direction.
- Use the TEXT LAYER for exact values: every number, every label, every string.

## UTILITY TYPE CLASSIFICATION
- SD = Storm Drain (carries stormwater/runoff)
- SS = Sanitary Sewer (carries wastewater)
- W = Water (potable water supply)
Do NOT confuse these. If a text item says "SS" it is sanitary sewer, not storm drain.
If a text item says "SD" it is storm drain, not sanitary sewer.

## STRUCTURE IDENTIFICATION
Common structure types on civil plans:
- SDMH = Storm Drain Manhole
- SSMH = Sanitary Sewer Manhole
- CB = Catch Basin
- GB = Grade Break (curb structure)
- Type I, Type II, etc. = Standard structure types (include the type designation)

## INVERT DIRECTIONS
Inverts are labeled with compass directions (N, S, E, W) indicating which direction the pipe
goes FROM the structure. Extract exactly as labeled.

## OUTPUT FORMAT
Return valid JSON matching this exact schema:
{schema}

## TEXT LAYER DATA
```json
{text_layer_json}
```

Now examine the image and extract all structures, pipes, and callouts visible in this tile.
Return ONLY the JSON output, no other text.
"""
```

### Function to Build Prompt

```python
import json
from .schemas import TileExtraction


def build_hybrid_prompt(text_layer: dict) -> str:
    """
    Build the complete extraction prompt with text layer data injected.

    Args:
        text_layer: The text layer JSON dict (loaded from tile's .json file)

    Returns:
        Complete prompt string ready to send to Claude/Gemini with the tile image.
    """
    schema_json = TileExtraction.model_json_schema()

    # Only include text items (not metadata) to save tokens
    text_items_only = text_layer.get("items", [])

    return HYBRID_EXTRACTION_PROMPT.format(
        schema=json.dumps(schema_json, indent=2),
        text_layer_json=json.dumps(text_items_only, indent=1),
    )
```

---

## Module 5: `src/graph/assembly.py`

### Purpose
Take flat TileExtraction results from multiple tiles/sheets and assemble into a NetworkX graph.

### Key Architectural Decisions
- **D7:** Graph structure — nodes are structures, edges are pipes.
- **D10:** Input is flat lists from LLM, Python does the graph assembly.
- Separate graphs per utility type (SD, SS, W).

### Function Signatures

```python
import networkx as nx
from ..extraction.schemas import TileExtraction, Structure, Pipe


def build_utility_graph(
    extractions: list[TileExtraction],
    utility_type: str,  # "SD", "SS", or "W"
) -> nx.DiGraph:
    """
    Build a directed graph for a single utility type from tile extractions.

    Nodes: structures (keyed by station + offset, deduplicated across tiles)
    Edges: pipes (matched to structures by from/to hints and spatial proximity)

    Node attributes: structure_type, size, rim, inverts[], source_sheets[], source_text_ids[]
    Edge attributes: size, material, length, slope, source_sheets[], source_text_ids[]
    """
    ...


def merge_duplicate_nodes(
    graph: nx.DiGraph,
    station_tolerance: float = 1.0,   # feet — structures within 1' are candidates
    offset_tolerance: float = 1.0,    # feet
) -> nx.DiGraph:
    """
    Merge nodes that represent the same physical structure extracted from different tiles/sheets.

    Strategy:
    1. Parse station strings to float (e.g., "16+82.45" → 1682.45)
    2. Parse offset strings to float (e.g., "28.00' RT" → 28.00)
    3. Cluster nodes within tolerance
    4. For clusters: merge attributes, keep the extraction with bbox closest to tile center
    """
    ...


def parse_station(station_str: str) -> float | None:
    """
    Parse civil engineering station format to decimal feet.

    Examples:
        "16+82.45" → 1682.45
        "STA: 16+82.45" → 1682.45
        "STA. 16+82.45" → 1682.45
        "10+00" → 1000.0
        "9+94.00" → 994.0

    Returns None if unparseable.
    """
    ...


def parse_offset(offset_str: str) -> tuple[float, str] | None:
    """
    Parse offset string to (distance, direction).

    Examples:
        "28.00' RT" → (28.0, "RT")
        "6.00' LT" → (6.0, "LT")
        "45.00' RT" → (45.0, "RT")

    Returns None if unparseable.
    """
    ...
```

---

## Module 6: `src/graph/checks.py`

### Purpose
Deterministic consistency checks on the utility graph. No LLM needed — pure Python math.

### Checks to Implement

```python
from dataclasses import dataclass


@dataclass
class Finding:
    """A single consistency finding."""
    finding_type: str           # slope_mismatch, size_inconsistency, elevation_mismatch, etc.
    severity: str               # error, warning, info
    description: str            # Human-readable description
    source_sheets: list[int]    # Page numbers involved
    source_text_ids: list[int]  # Text IDs for provenance
    node_ids: list[str]         # Graph node IDs involved
    edge_ids: list[str]         # Graph edge IDs involved
    expected_value: str | None = None
    actual_value: str | None = None


def check_slope_consistency(graph: nx.DiGraph) -> list[Finding]:
    """
    For each pipe edge, verify:
    calculated_slope = abs(upstream_invert - downstream_invert) / length
    Does calculated_slope match the labeled slope (within tolerance)?

    Tolerance: 0.0002 (accounts for rounding in callouts)
    """
    ...


def check_pipe_size_consistency(
    sd_plan_extractions: list[TileExtraction],
    sd_profile_extractions: list[TileExtraction],
    # Optionally: pipe schedule if extracted
) -> list[Finding]:
    """
    Same pipe segment referenced on plan view vs profile view vs pipe schedule:
    do they all show the same size?

    Match by: station range overlap + utility type
    """
    ...


def check_elevation_consistency(
    graph: nx.DiGraph,
    all_extractions: list[TileExtraction],
) -> list[Finding]:
    """
    Same structure appearing on multiple sheets:
    do rim elevations and inverts match?

    Match by: station + offset (using parse_station/parse_offset with tolerance)
    """
    ...


def check_connectivity(graph: nx.DiGraph) -> list[Finding]:
    """
    Are all nodes reachable from at least one other node?
    Flag orphan nodes (structures with no connecting pipes).
    Flag dead-end pipes (pipes connected on only one end).
    """
    ...


def check_flow_direction(graph: nx.DiGraph) -> list[Finding]:
    """
    For gravity systems (SD, SS): verify inverts decrease in the downstream direction.
    Flag any pipe where the downstream invert is higher than upstream (backfall).
    """
    ...
```

---

## Module 7: `src/intake/manifest.py`

### Purpose
Generate a sheet manifest mapping page numbers to sheet types and metadata.

### Strategy (D5 — Sheet Index Bootstrapping)

1. Extract text from Page 1 (cover sheet) — look for "SHEET INDEX" or "CIVIL SHEET INDEX" table
2. Parse the index table to get sheet number → description mappings
3. For each page, extract the title block (right strip, ~18% of page width) to get sheet number
4. Cross-reference cover index against title block readings

```python
@dataclass
class SheetInfo:
    page_number: int            # 1-indexed
    sheet_label: str | None     # e.g., "C-3", "U-2", "SS-1", "D-1"
    sheet_type: str             # plan_view, profile, detail, grading, signing, cover, notes, other
    description: str | None     # From index: "STORM DRAIN PLAN - PROSPERITY AVE"
    utility_types: list[str]    # SD, SS, W if identifiable from description
    needs_deep_extraction: bool # Should this sheet go through Phase 3?


def build_manifest(pdf_path: Path) -> list[SheetInfo]:
    """
    Build sheet manifest from cover index + title blocks.

    Step 1: Try to parse cover sheet index table
    Step 2: Extract title block text from each page
    Step 3: Classify sheet type using text content heuristics
    Step 4: Determine which sheets need deep extraction
    """
    ...


# Sheet type classification heuristics
SHEET_TYPE_KEYWORDS = {
    "cover": ["CIVIL IMPROVEMENT PLANS", "SHEET INDEX", "VICINITY MAP"],
    "notes": ["GENERAL NOTES", "ABBREVIATIONS", "LEGEND"],
    "demolition": ["DEMOLITION", "DEMO KEY NOTES", "REMOVALS"],
    "plan_view": ["STORM DRAIN PLAN", "UTILITY PLAN", "SEWER PLAN", "WATER PLAN",
                   "GRADING PLAN", "IMPROVEMENT PLAN"],
    "profile": ["PROFILE", "STA:", "EXISTING GRADE"],
    "detail": ["TYPICAL", "DETAIL", "SECTION", "STANDARD"],
    "signing": ["SIGN", "STRIPING", "PAVEMENT MARKING", "TRAFFIC"],
    "erosion": ["EROSION", "SWPPP", "BMP"],
}

# Sheets that need deep tiled extraction
EXTRACT_SHEET_TYPES = {"plan_view", "profile", "detail"}
# Sheets that get light extraction (title block only)
LIGHT_EXTRACT_TYPES = {"cover", "notes", "demolition", "signing", "erosion"}
```

---

## Testing Plan

### Test 1: Tiler Output Validation
```python
# Run tiler on FNC Farms Page 14
# Verify: 6 tiles produced, each ~3600x2400px at 300 DPI
# Verify: 6 matching text layer JSONs produced
# Verify: overlap zones contain shared content
# Verify: no gaps between tiles
```

### Test 2: Text Layer Coherence
```python
# Run coherence scoring on all 57 FNC Farms pages
# Verify: all pages score > 0.40 (ArialNarrow TTF expected)
# Run on Corridor PDF (different firm) — may have SHX issues
# Report any pages below threshold
```

### Test 3: Station/Offset Parsing
```python
# Test parse_station with known values from FNC Farms:
assert parse_station("16+82.45") == 1682.45
assert parse_station("STA: 13+40.73,  28.00'  RT") == 1340.73  # station only
assert parse_station("10+00") == 1000.0
assert parse_station("9+94.00") == 994.0

# Test parse_offset:
assert parse_offset("28.00' RT") == (28.0, "RT")
assert parse_offset("6.00' LT") == (6.0, "LT")
assert parse_offset("45.00' RTGB") == (45.0, "RT")  # handle GB suffix
```

### Test 4: End-to-End Tile → Extraction
```python
# Take tile p14_r0_c2 (the one we already tested manually)
# Build hybrid prompt with text layer
# Send to Claude/Gemini with tile image
# Validate output parses as TileExtraction
# Cross-check extracted values against known ground truth:
#   - SDMH at STA 16+82.45, 28.00' RT, RIM 305.95
#   - INV 12" E 299.77, INV 12" W 299.77
#   - 342 LF of 12" SD pipe
```

### Test 5: Corridor PDF Text Layer (SHX Gate)
```python
# Run coherence check on References/240085_CORRIDOR_REV 3_PLAN SET.pdf
# This is from a different firm — may use SHX fonts
# If coherence < 0.40 on any pages, we need the OCR fallback path
# Report: which pages pass, which fail, what fonts are present
```

---

## Known Issues & Edge Cases

1. **Diameter symbol:** `⌀` (U+2205) and `Ø` (U+00D8) appear in pipe callout text. Replace with "DIA" in unicode cleaning.

2. **Station format variations:** Plans use inconsistent formats — `STA: 16+82.45`, `STA. 16+82.45`, `16+82.45`, and sometimes the offset is on the same line (`STA: 16+82.45, 28.00' RT`). The parser must handle all.

3. **Offset suffixes:** Some offsets have structure type suffixes like `45.00' RTGB` (RT to Grade Break). Strip the suffix when parsing distance.

4. **Parenthetical elevations:** Edge-of-pavement callouts use parentheses for proposed elevations: `EP (305.88)` vs `EP 305.88` for existing. Both formats must be captured.

5. **Profile views are landscape:** The profile area is typically the bottom 40-50% of a sheet. For profile sheets, a 1x2 grid (left half, right half) may work better than the standard 3x2 grid. The tiler should support configurable grid dimensions.

6. **Title block position:** Always on the right side of the sheet, approximately the rightmost 12-15% of page width. For manifest building, extract just this strip.

7. **Windows encoding:** When writing to console/logs on Windows, use `encoding='utf-8'` or `errors='replace'` to avoid cp1252 crashes on unicode characters.

---

## File Paths (Test Data)

```python
PROJECT_ROOT = Path("C:/Users/dylan/Documents/AI/Projects/Plan Reviewer")
FNC_FARMS_PDF = PROJECT_ROOT / "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf"
CORRIDOR_PDF = PROJECT_ROOT / "References/240085_CORRIDOR_REV 3_PLAN SET.pdf"
TEST_EXTRACTIONS = PROJECT_ROOT / "test-extractions"
SRC_DIR = PROJECT_ROOT / "src"
```

## Validated Ground Truth (for testing)

From Page 14 (SD Plan, Prosperity Avenue), right quadrant tile:

```json
{
  "structures": [
    {"station": "13+40.73", "offset": "28.00' RT", "type": "SDMH", "size": "48\"", "rim": 305.44,
     "inverts": [{"dir": "E", "size": "12\"", "elev": 300.80}, {"dir": "S", "size": "12\"", "elev": 300.90}]},
    {"station": "16+82.45", "offset": "28.00' RT", "type": "SDMH", "size": "48\"", "rim": 305.95,
     "inverts": [{"dir": "E", "size": "12\"", "elev": 299.77}, {"dir": "W", "size": "12\"", "elev": 299.77}]}
  ],
  "pipes": [
    {"type": "SD", "size": "12\"", "length": 342, "slope": 0.0030},
    {"type": "SD", "size": "12\"", "length": 342, "slope": 0.0020}
  ]
}
```

From Page 36 (Bishop Street Profile), left half:

```json
{
  "structures": [
    {"station": "10+08.08", "offset": "6.02' RT", "type": "SSMH", "size": "48\"", "rim": 301.79},
    {"station": "12+07.18", "offset": "6.02' RT", "type": "SSMH", "size": "48\"", "rim": 302.78},
    {"station": "14+08.18", "offset": "6.02' RT", "type": "SSMH", "rim": 302.90}
  ],
  "pipes": [
    {"type": "SS", "size": "8\"", "length": 300, "slope": 0.005},
    {"type": "SS", "size": "8\"", "length": 201, "slope": 0.005},
    {"type": "W", "size": "8\"", "material": "DI", "length": 23},
    {"type": "W", "size": "8\"", "material": "DI", "length": 9},
    {"type": "W", "size": "8\"", "material": "DI", "length": 146}
  ]
}
```
