# Plan Reviewer

AI-assisted civil engineering plan set review pipeline focused on one goal:
catch cross-sheet consistency issues before they become RFIs.

This project is being developed by a licensed PE and supports both:
- deterministic engineering checks (graph-based math/logic), and
- LLM-assisted extraction from tiled PDF plans.

## Current State

Implemented and validated:
- Intake pipeline (tiling, text-layer extraction, manifest generation)
- Hybrid extraction runners (single tile + batch)
- Calibration scorer (ground-truth checks)
- Graph pipeline (merge, assembly, consistency checks)
- Cost optimization + graph false-positive reduction passes

Latest calibration status:
- `9/10` calibration score on `calibration-clean`
- Graph checks operational for `SD`, `SS`, `W`

## Quick Start

## 1) Prerequisites

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

## 2) Core Commands

Intake commands (independent building blocks):

```bash
python -m src.intake.tiler --pdf "path/to/file.pdf" --pages 14 --output ./output/
python -m src.intake.text_layer --pdf "path/to/file.pdf" --pages 14 --output ./output/
python -m src.intake.manifest --pdf "path/to/file.pdf" --output ./output/
```

Run hybrid extraction in batch:

```bash
python -m src.extraction.run_hybrid_batch \
  --tiles-dir output/intake-pass2/tiles \
  --text-layers-dir output/intake-pass2/text_layers \
  --out-dir output/extractions/calibration-clean \
  --max-tiles 24 \
  --model "google/gemini-3-flash-preview" \
  --timeout-sec 180
```

Score calibration:

```bash
python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-clean
```

Build utility graphs:

```bash
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SD --out output/graphs/calibration-clean-sd.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SS --out output/graphs/calibration-clean-ss.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type W  --out output/graphs/calibration-clean-w.json
```

Run unit tests:

```bash
python -m unittest discover -s tests -v
```

## Documentation Map

- `ARCHITECTURE.md`  
  System blueprint, pipeline phases, design decisions.

- `PROGRESS.md`  
  Detailed engineering journal and implementation logs.

- `PROGRESS_SUMMARY.md`  
  Day-level milestone summary (human-readable checkpoint view).

- `docs/CODING-SPEC-INTAKE-PIPELINE.md`  
  Implementation spec for intake/extraction phases.

- `docs/CODEX-TASK-00x-*.md`  
  Focused task briefs used for iterative development passes.

- `docs/findings/PHASE1-VISION-FINDINGS.md`  
  Vision feasibility and extraction findings from Phase 1.

## Repository Notes

Large source references and generated outputs are intentionally gitignored:
- `References/`
- `output/`
- `test-extractions/`
- `.env`

This keeps the repository lightweight and safe to share while preserving local working assets.

