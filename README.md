# Plan Reviewer

AI-assisted civil engineering plan set review pipeline focused on one goal:
catch cross-sheet consistency issues before they become RFIs.

This project is being developed by a licensed PE and supports both:
- deterministic engineering checks (graph-based math/logic), and
- LLM-assisted extraction from tiled PDF plans.

## Current State

Implemented and validated:
- **End-to-end pipeline runner** with 7 phases and `--resume` crash recovery
- Intake pipeline (tiling, text-layer extraction, title block crops, manifest generation)
- Adaptive tiling (content-aware region detection via occupancy grid + flood fill)
- Model routing (fast / standard / premium tiers based on sheet complexity and coherence)
- Hybrid extraction runners (single tile + batch, with Anthropic Batch API support)
- Calibration scorer (ground-truth checks)
- Graph pipeline (merge, assembly, consistency checks with dual confidence)
- Cost optimization + graph false-positive reduction passes
- 91 unit tests

Latest calibration status:
- `9/10` calibration score on `calibration-clean`
- Graph checks operational for `SD`, `SS`, `W`

Archived: the old Streamlit prototype is in `legacy/iteration-1-streamlit/` and is not part of current development.

## Quick Start

### 1) Prerequisites

- Python 3.13
- Git
- OpenRouter API key (for live extraction runs)

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `.env` in repo root:

```env
OPENROUTER_API_KEY=your_key_here
```

The CLI tools read this repo-root `.env` via `python-dotenv`.

### 2) Run the Pipeline

The recommended way to run a full review is the single-command pipeline runner:

```bash
python -m src.pipeline \
  --pdf "path/to/plans.pdf" \
  --output-dir ./runs/my-project
```

This executes all 7 phases in sequence:
1. **Tiling** — renders PDF pages into overlapping tile crops at 300 DPI
2. **Manifest** — classifies sheets by type and assigns model tiers
3. **Extraction** — sends tiles through hybrid (text + vision) extraction
4. **Validation** — checks extraction quality and package contract
5. **Graphs** — assembles utility networks (SD, SS, W)
6. **Checks** — runs deterministic consistency checks
7. **Report** — generates HTML review report

Use `--resume` to restart from the last incomplete phase after a crash or interruption:

```bash
python -m src.pipeline \
  --pdf "path/to/plans.pdf" \
  --output-dir ./runs/my-project \
  --resume
```

### 3) Individual Commands (Advanced)

Each pipeline phase is also available as a standalone CLI module.

Intake:

```bash
python -m src.intake.tiler --pdf "path/to/file.pdf" --pages 14 --output ./output/
python -m src.intake.text_layer --pdf "path/to/file.pdf" --pages 14 --output ./output/
python -m src.intake.manifest --pdf "path/to/file.pdf" --output ./output/
```

Hybrid extraction (batch mode):

```bash
python -m src.extraction.run_hybrid_batch \
  --tiles-dir output/intake/tiles \
  --text-layers-dir output/intake/text_layers \
  --out-dir output/extractions \
  --max-tiles 24 \
  --model "google/gemini-2.5-flash-lite" \
  --timeout-sec 180
```

Score calibration:

```bash
python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-clean
```

Validate pre-analysis package contract:

```bash
python -m src.extraction.validate_package \
  --extractions-dir output/extractions/calibration-clean
```

Build utility graphs:

```bash
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SD --out output/graphs/sd.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SS --out output/graphs/ss.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type W  --out output/graphs/w.json
```

Generate HTML review report:

```bash
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix calibration-clean \
  --batch-summary output/extractions/calibration-clean/batch_summary.json \
  --out output/reports/report.html
```

### 4) Run Tests

```bash
python -m pytest tests/ -q
```

## Key Design Decisions

- **Tiling over full-page vision**: Claude/Gemini APIs downsample images beyond ~1568px. Full-page renders (7200x4800 at 200 DPI) become illegible. Quarter-page crops at 300 DPI preserve text readability.
- **Model routing**: Sheets are classified into tiers — fast (cover/notes), standard (plan/profile), premium (details, low-coherence sheets) — to balance cost and accuracy.
- **Default model**: `google/gemini-2.5-flash-lite` with escalation to `google/gemini-3.1-flash-lite-preview` for low-coherence tiles.
- **Centralized thresholds**: All coherence thresholds and heuristic constants live in `src/config.py`.
- **Dual confidence**: Findings carry both `extraction_confidence` (was the data read correctly?) and `check_confidence` (is the finding actually a problem?).

## Documentation Map

- `ARCHITECTURE.md`
  System blueprint, pipeline phases, design decisions.

- `PROGRESS.md`
  Detailed engineering journal and implementation logs.

- `PROGRESS_SUMMARY.md`
  Day-level milestone summary (human-readable checkpoint view).

- `docs/CODING-SPEC-INTAKE-PIPELINE.md`
  Implementation spec for intake/extraction phases.

- `docs/findings/PHASE1-VISION-FINDINGS.md`
  Vision feasibility and extraction findings from Phase 1.

## Repository Notes

- `References/` and `output/` are tracked in git for reproducibility.
- `test-extractions/` and `.env` are gitignored.
- The `legacy/` directory contains the archived first iteration (Streamlit prototype).
