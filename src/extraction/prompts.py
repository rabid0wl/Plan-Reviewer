"""Prompt templates for hybrid image + text-layer extraction."""

from __future__ import annotations

import json
from typing import Any

COMPACT_SCHEMA = """{
  "tile_id":"string e.g. p14_r0_c2",
  "page_number":"int",
  "sheet_type":"plan_view|profile_view|detail_sheet|grading|signing_striping|cover|notes|other",
  "utility_types_present":["SD","SS","W"],
  "structures":[{
    "id":"string|null",
    "structure_type":"SDMH|SSMH|CB|GB|inlet|cleanout|junction|other",
    "size":"string|null e.g. 48\\"",
    "station":"string e.g. 16+82.45",
    "offset":"string e.g. 28.00' RT",
    "rim_elevation":"float|null",
    "tc_elevation":"float|null",
    "fl_elevation":"float|null",
    "inverts":[{
      "direction":"N|S|E|W|NE|NW|SE|SW",
      "pipe_size":"string e.g. 12\\"",
      "pipe_type":"SD|SS|W|null",
      "elevation":"float",
      "source_text_ids":[0]
    }],
    "notes":"string|null",
    "source_text_ids":[0]
  }],
  "pipes":[{
    "pipe_type":"SD|SS|W",
    "size":"string e.g. 12\\"",
    "material":"RCP|PVC|DIP|HDPE|string|null",
    "length_lf":"float|null",
    "slope":"float|null",
    "from_station":"string|null",
    "to_station":"string|null",
    "from_structure_hint":"string|null",
    "to_structure_hint":"string|null",
    "notes":"string|null",
    "source_text_ids":[0]
  }],
  "callouts":[{
    "callout_type":"edge_of_pavement|detail_reference|cross_reference|grading_note|installation_note|match_existing|cover_depth|other",
    "text":"string",
    "station":"string|null",
    "offset":"string|null",
    "elevation":"float|null",
    "reference_sheet":"string|null",
    "reference_detail":"string|null",
    "source_text_ids":[0]
  }],
  "street_names":["string"],
  "lot_numbers":[0],
  "extraction_notes":"string|null"
}"""

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
- Do NOT output null for required fields. If a structure is missing station or offset, omit it.
  If a pipe is missing pipe_type or size, omit it.

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
Each item has:
- "id" = text_id used in source_text_ids
- "t" = text content
- "b" = bounding box [x0,y0,x1,y1] in tile-local pixels
```json
{text_layer_json}
```

Now examine the image and extract all structures, pipes, and callouts visible in this tile.
Return ONLY the JSON output, no other text.
"""


def build_hybrid_prompt(text_layer: dict[str, Any]) -> str:
    """
    Build the complete extraction prompt with schema and tile text items.
    """
    raw_items = text_layer.get("items", [])
    slim_items = [
        {
            "id": int(item["text_id"]),
            "t": str(item["text"]),
            "b": [round(float(coord)) for coord in item["bbox_local"]],
        }
        for item in raw_items
        if "text_id" in item and "text" in item and "bbox_local" in item
    ]

    return HYBRID_EXTRACTION_PROMPT.format(
        schema=COMPACT_SCHEMA,
        text_layer_json=json.dumps(slim_items, separators=(",", ":"), ensure_ascii=False),
    )
