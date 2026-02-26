# Codex Task 005: HTML Plan Review Report Generator

## Goal

Create a single-file HTML report generator that reads existing graph + findings JSON and produces a human-readable review summary. The audience is a licensed civil engineer (PE) who needs to see extracted data and flagged issues at a glance — not raw JSON.

No new dependencies. No server. Just a Python script that writes a self-contained `.html` file you open in a browser.

---

## Input Files

All files already exist in the project. The report reads them, doesn't modify them.

**Graph JSONs** (one per utility):
- `output/graphs/{prefix}-sd.json`
- `output/graphs/{prefix}-ss.json`
- `output/graphs/{prefix}-w.json`

Each contains: `utility_type`, `quality_summary`, `nodes[]`, `edges[]`

**Findings JSONs** (one per utility):
- `output/graphs/findings/{prefix}-sd-findings.json`
- `output/graphs/findings/{prefix}-ss-findings.json`
- `output/graphs/findings/{prefix}-w-findings.json`

Each contains: `utility_type`, `graph` (node/edge counts, quality_summary), `counts` (by_severity, by_type), `findings[]`

**Batch summary** (optional):
- `output/extractions/{extraction-dir}/batch_summary.json`

Contains: `started_at`, `completed_at`, `model`, `counts`, and per-tile `results[]` with `meta.usage.cost`, `meta.sanitized`, `meta.coherence_score`, etc.

---

## CLI

```
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix calibration-clean \
  --batch-summary output/extractions/calibration-clean/batch_summary.json \
  --out output/reports/calibration-clean-report.html
```

**Parameters:**
- `--graphs-dir` (required): directory containing `{prefix}-{sd,ss,w}.json`
- `--findings-dir` (required): directory containing `{prefix}-{sd,ss,w}-findings.json`
- `--prefix` (required): filename prefix (e.g., `calibration-clean`)
- `--batch-summary` (optional): path to `batch_summary.json` for extraction stats
- `--out` (required): output HTML file path

---

## Report Layout

Single self-contained HTML file with inline CSS. No external stylesheets, no JavaScript dependencies. Should look clean and professional in Chrome/Edge.

### Section 1: Header Banner

```
Plan Review Report
Generated: 2026-02-22 18:07 UTC
Pages analyzed: 14, 19, 34, 36
Extraction model: google/gemini-3-flash-preview
Total extraction cost: $0.18
```

Pull from `batch_summary.json`:
- Date from `completed_at`
- Pages from unique page numbers across `results[].meta.tile_id` (parse `p{page}_r{row}_c{col}`)
- Model from `model`
- Cost: sum of all `results[].meta.usage.cost`

### Section 2: Findings Summary (top of page, most important)

A color-coded summary bar:
- Red badge: `{N} errors`
- Yellow badge: `{N} warnings`
- Blue badge: `{N} info`

Then a table of all findings across all utilities, sorted by severity (errors first), with columns:

| Severity | Utility | Type | Description | Sheet(s) |
|---|---|---|---|---|
| ERROR | SS | flow_direction_error | Backfall on edge SS:e14... | 36 |
| WARNING | SD | slope_mismatch | Slope mismatch on edge SD:e1... | 14 |
| INFO | SD | unanchored_pipe | Pipe SD:e4 has no endpoint... | 14 |

Severity column should be color-coded:
- Error: red background
- Warning: yellow/amber background
- Info: light blue background

### Section 3: Structure Schedule (per utility)

For each utility (SD, SS, W) that has structure nodes, a table:

**Storm Drain (SD) — 4 structures**

| Station | Offset | Type | Size | RIM | Inverts | Notes | Source Sheets |
|---|---|---|---|---|---|---|---|
| 13+40.73 | 28.00' RT | SDMH | 48" | 305.44 | E:300.80, S:300.90 | INSTALL TYPE I (48") SDMH, DI | 14 |
| 13+40.73 | 45.00' RT | INLET | — | — | N:301.00, E:300.80, S:300.90 | DI | 14 |

Pull from graph JSON `nodes[]` where `kind == "structure"`. Format inverts as `{direction}:{elevation}` comma-separated. Skip orphan_anchor nodes.

### Section 4: Pipe Schedule (per utility)

For each utility that has pipe edges, a table:

**Storm Drain (SD) — 7 pipes**

| From | To | Size | Length | Slope | Material | Notes | Confidence | Sheet(s) |
|---|---|---|---|---|---|---|---|---|
| SDMH 13+40.73 | SDMH 16+82.45 | 12" | 342 LF | 0.0030 | — | INSTALL 342 LF... | medium | 14 |

Pull from graph JSON `edges[]`. For `from`/`to`, show structure type + station (parsed from node_id or node data). Skip orphan-to-orphan edges (both endpoints are orphan_anchor). If `oriented_by_gravity` is true, show a small "↻" indicator.

### Section 5: Extraction Quality

A compact summary:

```
Extraction Quality: Grade B
  24 tiles processed, 5 sanitized (20.8%), 0 skipped
  Sanitized tiles: p14_r1_c0, p14_r1_c1, p19_r1_c0, p19_r1_c1, p19_r1_c2
```

Pull from findings JSON `graph.quality_summary` (any utility — they share the same extraction data).

If `batch_summary.json` is provided, also show a per-tile mini-table:

| Tile | Coherence | Structures | Pipes | Callouts | Cost | Sanitized |
|---|---|---|---|---|---|---|
| p14_r0_c0 | 0.897 | 2 | 1 | 10 | $0.012 | — |
| p14_r1_c0 | 1.000 | 2 | 1 | 2 | $0.005 | ⚠ (1 struct dropped) |

---

## Styling Guidelines

- Use a clean sans-serif font (system font stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`)
- Tables: alternating row colors, sticky header, borders
- Finding severity colors: error = `#fee2e2` (red-50), warning = `#fef3c7` (amber-100), info = `#dbeafe` (blue-100)
- Max width ~1200px, centered
- Print-friendly (no background images, reasonable margins)
- All CSS inline in `<style>` tag in `<head>` — no external files

---

## File Structure

```
src/report/__init__.py          # empty
src/report/html_report.py       # main module
```

No tests needed for this task — it's a pure rendering module. Validation is visual (Dylan opens the HTML and says if it makes sense).

---

## Implementation Notes

- Use Python's `html` module for escaping user-derived text (structure notes, pipe notes)
- Use `pathlib.Path` for all file I/O
- Gracefully handle missing files (if SS graph doesn't exist, skip that utility section)
- Round all floats to 2 decimal places in display
- Sort structures by station within each utility
- Sort pipes by from-station within each utility
- The finding `description` field already contains human-readable text — display it as-is
- For cost display, format as `$X.XX` (2 decimal USD)
- For the findings table, include ALL findings from ALL utilities in one combined table

---

## Validation

```bash
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix calibration-clean \
  --batch-summary output/extractions/calibration-clean/batch_summary.json \
  --out output/reports/calibration-clean-report.html
```

Then open `output/reports/calibration-clean-report.html` in a browser. Should render cleanly with all sections populated.

---

## Files Created

| File | Purpose |
|---|---|
| `src/report/__init__.py` | Package init (empty) |
| `src/report/html_report.py` | Report generator module with CLI |

No schema changes. No extraction changes. No graph changes. No new dependencies.
