# Phase 1: Vision Validation Findings

**Date:** 2026-02-21
**Test Data:** FNC Farms Ph. 1 Civils (57 pages, 36"x24" ARCH D sheets)
**PDF:** `References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf`
**Extraction tool:** PyMuPDF (fitz) v1.27.1

---

## Executive Summary

**Vision-based structured extraction works on civil engineering plans**, but ONLY with a cropping/tiling strategy. Full-page images fail because the API downsamples large images, destroying the fine annotation text that carries all the critical data.

The recommended approach is a **two-pass pipeline**:
1. Full page at low res → triage, classify sheet type, identify regions of interest
2. Targeted crops at 300 DPI → detailed structured extraction per region

---

## Resolution Study Results

### Test Matrix

| Image Type | Pixel Size | Readability (1-5) | Fine Text | Invert Elevations | Pipe Sizes |
|---|---|---|---|---|---|
| Full page, 200 DPI | 7200x4800 | 1.5 | Illegible | Illegible | Illegible |
| Half-page crop, 300 DPI | ~5000x3200 | 3 | Partial | Some readable | Most readable |
| Quarter-page crop, 300 DPI | ~3200x2900 | 4.5 | Nearly all readable | Precise to 0.01' | All readable |
| Title block crop, 300 DPI | 1944x7200 | 5 | Everything readable | N/A | N/A |

### Why Full Pages Fail

Claude's image processing downsamples to max ~1568px on the longest side. A 7200x4800 full page becomes ~1568x1045 internally — each displayed pixel represents ~4.6 original pixels. Civil engineering annotation text at 6-8pt font becomes sub-pixel and unreadable.

### Why Crops Work

A 3200x2900 crop downsamples to ~1568x1420 — each pixel represents ~2.0 original pixels. The text remains above the readability threshold. More importantly, the *information density per final pixel* is much higher because we're not wasting pixels on blank margins and areas without annotations.

---

## Structured Extraction Test Results

### Test 1: Storm Drain Plan View (Page 14, Prosperity Avenue)

**Crop:** ~3200x2900px quarter-page at 300 DPI

**Successfully extracted:**
- 3 structures with station (to 0.01'), offset, direction, type (SDMH/GB), size (48"), rim/TC/FL elevations, and directional inverts
- 2 pipe runs with size (12" SD), length (342 LF), and slope (S=0.0020, S=0.0030)
- 15 edge-of-pavement callouts with station, offset, elevation, and MATCH EXIST. flags
- 9 lot numbers (41-49)
- Detail bubble references (bubble 11)
- Cross-reference: SEE SHEET 16

**Sample extracted structure:**
```json
{
  "station": "16+82.45",
  "offset": "28.00'",
  "offset_direction": "RT",
  "type": "SDMH",
  "size": "48\"",
  "rim_elevation": 305.95,
  "inverts": [
    {"direction": "E", "size": "12\"", "elevation": 299.77},
    {"direction": "W", "size": "12\"", "elevation": 299.77}
  ]
}
```

**Accuracy:** High. Station numbers precise to hundredths, elevations precise to hundredths, pipe sizes and slopes exact.

### Test 2: Utility Profile View (Page 36, Bishop Street)

**Crop:** ~4600x3100px half-page at 300 DPI

**Successfully extracted:**
- Station range: 9+00 to 14+00
- Elevation range: 288 to 306
- 6 pipe runs: SS (8" @ S=0.005, two segments: 300 LF and 201 LF) and W (8" DI, three segments: 23/9/146 LF)
- 7 structures: 3 SSMHs with rim elevations (301.79, 302.78, 302.90), 2 water bends (45°), 2 gate valves
- Invert elevations at each structure: INV 8" S 294.86, INV 8" E 294.49, etc.
- Cover depth annotation: "4.0' MIN COVER"
- Grade lines: "EXISTING GRADE @ CENTERLINE" and "PROPOSED TC @ EAST SIDE OF STREET"

**Accuracy:** High for structures and pipe runs. Some minor confusion between utility_type labels in JSON (SD vs SS) — extraction prompt needs tighter schema guidance.

### Test 3: Basin/Grading Plan (Page 9)

**Crop:** ~3200x1800px at 300 DPI

**Successfully extracted:**
- Spot elevations (284.74, 297.74, 298.82, 286.24, 292.24)
- Setback annotations ("20' CLEAR")
- Lot numbers (83)
- Detail reference bubbles (A, B)
- Street name (BANDON TRAILS)

### Test 4: Title Block (Page 36)

**Crop:** 1944x7200px at 300 DPI

**Successfully extracted:**
- Project: "FNC FARMS SUBDIVISION - PHASE 1"
- Owner: "SAN JOAQUIN VALLEY HOMES"
- Engineer: 4CREEKS
- Job No: 240704
- Sheet No: 36 OF 57
- Plot Date: Dec 09, 2025
- PE stamp visible

---

## What Vision CAN Do (Confirmed)

1. **Read pipe callouts:** Size (8", 12"), material type (SD, SS, W), and line symbology
2. **Read structure data blocks:** Station to 0.01', offset to 0.01', rim/TC/FL elevations to 0.01'
3. **Read invert elevations:** Direction (N/S/E/W), pipe size, elevation to 0.01'
4. **Read pipe installation notes:** Length (LF), slope (S=0.XXXX), material
5. **Read station numbers:** Both in plan and profile views
6. **Read elevation grids:** Profile view vertical axis
7. **Read cross-references:** SEE SHEET XX, detail bubble numbers
8. **Read edge-of-pavement callouts:** Station, offset, elevation, MATCH EXIST. flags
9. **Read title blocks:** All standard fields
10. **Read lot numbers and street names**
11. **Distinguish line types:** Existing vs proposed grade in profiles
12. **Read cover depth annotations**

## What Vision CANNOT Do (or struggles with)

1. **Read full pages at once** — API downsampling destroys fine text
2. **Distinguish line types precisely** — Dashed vs dash-dot for SS vs W is marginal at any resolution
3. **Parse overlapping annotations** — Where callout leaders cross, extraction gets confused
4. **Identify contour elevations** — Contour lines on grading plans blend together
5. **Match detail bubbles to target sheets** — Can read the bubble number but target sheet reference is often too small or partially obscured

---

## Recommended Tiling Strategy for Production

### Option A: Systematic 3x2 Grid (Simple, Predictable)

Divide each 36"x24" sheet into 6 tiles:
```
┌──────────┬──────────┬──────────┐
│  Tile 1  │  Tile 2  │  Tile 3  │
│  (12x12) │  (12x12) │  (12x12) │
├──────────┼──────────┼──────────┤
│  Tile 4  │  Tile 5  │  Tile 6  │
│  (12x12) │  (12x12) │  (12x12) │
└──────────┴──────────┴──────────┘
```

At 300 DPI: each tile = 3600x3600px → well within the sweet spot.

- **Pro:** Simple, no intelligence needed, every annotation gets captured somewhere
- **Con:** Wastes tokens on empty tiles, annotations at tile boundaries get split
- **Mitigation:** 10% overlap between tiles catches boundary annotations

### Option B: Two-Pass Adaptive (Smarter, More Efficient)

1. **Pass 1 (cheap, Sonnet):** Full page at 150 DPI → classify sheet type, identify annotation-dense regions
2. **Pass 2 (targeted, Opus):** Extract 2-4 crops per sheet based on where the data actually is

For a typical plan/profile sheet:
- Crop 1: Title block (right strip)
- Crop 2: Plan view annotation zone (where structures and pipes are)
- Crop 3: Profile view left half
- Crop 4: Profile view right half

- **Pro:** More cost-efficient, fewer wasted tokens, better context per crop
- **Con:** Requires Pass 1 intelligence to identify crop regions, more complex pipeline
- **Risk:** Pass 1 might miss annotation regions → incomplete extraction

### Recommendation: Option A for MVP, Option B for Production

Start with systematic tiling (simple, reliable, easy to validate), then optimize to adaptive cropping once the pipeline is proven.

---

## Cost Implications

### Per-Sheet Extraction Cost (estimated with tiling)

| Step | Model | Tiles/Calls | Est. Cost |
|---|---|---|---|
| Title block extraction | Haiku | 1 | $0.01 |
| Triage classification | Sonnet | 1 (full page, low res) | $0.03 |
| Detailed extraction | Opus | 6 tiles | $0.50-0.80 |
| **Per sheet total** | | | **~$0.55-0.85** |

### Per Plan Set (57 sheets)

| Step | Est. Cost |
|---|---|
| All sheets extracted | $31-48 |
| Cross-reference analysis | $2-3 |
| Output generation | $0.50 |
| **Total** | **$34-52** |

This is higher than the ARCHITECTURE.md estimate of $10-15 per plan set. Optimizations:
- Skip sheets that don't need detailed extraction (cover, general notes, signing/striping)
- Use Sonnet instead of Opus for simpler sheet types (grading, signing)
- Adaptive cropping (Option B) reduces tiles from 6 to 2-4 per sheet
- With these optimizations, ~$15-25 per plan set is achievable

---

## Key Constraints Discovered

1. **Image dimension limit:** Claude API downsamples images. Effective max for readable civil annotations is ~3500px on the longest side at 300 DPI source.

2. **One page per subagent (confirmed):** ARCHITECTURE.md's recommendation stands. Each tile extraction should be its own agent call for context isolation.

3. **Schema matters:** The extraction prompt schema significantly affects output quality. Tight schema with explicit field names (station, offset, rim_elevation, inverts[].direction) produces much better JSON than open-ended "extract everything" prompts.

4. **Utility type confusion:** Vision occasionally confuses SD/SS/W labels, especially in profiles where all three are shown. The extraction schema should require explicit utility_type fields and the prompt should emphasize distinguishing between them.

5. **300 DPI is the sweet spot:** 200 DPI crops are marginal. 300 DPI is clearly readable. 400 DPI adds file size without significant readability gain (the API will downscale anyway).

---

## Next Steps

- [ ] Build extraction schemas (JSON) for each sheet type: plan view, profile, detail, grading, title block
- [ ] Test tiling overlap strategy (10% overlap vs 15% vs none)
- [ ] Test on second plan set (Corridor) to validate generalization
- [ ] Build the PyMuPDF tiling extraction script as a reusable module
- [ ] Begin Phase 0 skills development in parallel
