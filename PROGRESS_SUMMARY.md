# Plan Reviewer - Progress Summary

## Daily Entry Template

Use this structure for each day. Append newest days at the top.

```md
## YYYY-MM-DD

### Summary
- 2-5 bullets: what moved forward today.

### Milestones
- Major completed items (module-level or workflow-level).

### Validation
- Tests/checks run and pass/fail status.
- Key numeric results (accuracy, coverage, finding counts).

### Cost and Performance
- Material cost/tokens/runtime changes.

### Deliverables
- New or updated files/CLIs worth tracking.

### Risks / Follow-Ups
- Open issues, caveats, and next-step actions.
```

## 2026-02-22

### Summary
- Implemented Modules 5-6 (graph merge, assembly, deterministic checks) and wired provenance through nodes/edges.
- Ran graph checks on `calibration-opt`, then reduced false positives in connectivity findings.
- Added quality-aware connectivity behavior so low-quality extraction runs are flagged without flooding warnings.
- Completed Task 003 fixes: pipe deduplication, GB filtering in SD graph, directional invert matching, and fresh no-cache validation run.
- Completed Task 004 fixes: same-station offset fallback, gravity edge reorientation (SD+SS), and proximity structure merge.
- Completed Task 005 report UI pass: standalone HTML report generator with quality banner, fallback page detection, and provenance columns.
- Updated extraction model routing: default `google/gemini-2.5-flash-lite` with automatic escalation to `google/gemini-3-flash-preview` on low confidence/issues.
- Reverted extraction default model back to `google/gemini-3-flash-preview` after quality/cost comparison.
- Completed Task 006 crown/invert heuristic in graph/check layers and validated on corridor + FNC outputs.
- Added and standardized human-readable documentation entry points (`README.md` + doc conventions cleanup).

### Milestones
- Added graph modules:
  - `src/graph/merge.py`
  - `src/graph/assembly.py`
  - `src/graph/checks.py`
- Expanded parsing for signed offsets:
  - `src/utils/parsing.py`
- Added graph test coverage:
  - `tests/test_graph_merge.py`
  - `tests/test_graph_assembly.py`
  - `tests/test_graph_checks.py`
- Added report module:
  - `src/report/html_report.py`
- Added report smoke tests:
  - `tests/test_html_report.py`
- Added extraction escalation tests:
  - `tests/test_run_hybrid_escalation.py`
- Added crown heuristic tests:
  - `tests/test_graph_checks.py` (5 new cases)

### Validation
- Unit tests passed after implementation and tuning (`11/11`).
- Graph assembly smoke run completed for all utilities (`SD`, `SS`, `W`) on `output/extractions/calibration-opt`.
- Findings outputs generated:
  - `output/graphs/findings/calibration-opt-sd-findings.json`
  - `output/graphs/findings/calibration-opt-ss-findings.json`
  - `output/graphs/findings/calibration-opt-w-findings.json`
- False-positive reduction pass:
  - SD findings reduced from `46` to `10`.
  - W findings reduced from `13` to `1` (`connectivity_unverifiable`).
- Task 003 validation:
  - Unit tests `16/16` passing.
  - Fresh extraction batch (`24/24` OK) on pages 14/19/34/36 with `--no-cache`.
  - Calibration score remained `9/10`.
  - SD flow-direction false positives from reversed duplicates reduced to `0`.
- Task 004 validation:
  - Unit tests `21/21` passing.
  - SD findings reduced to `4` with `0` errors.
  - SS `flow_direction_error` reduced from `4` to `0`; SS findings reduced from `13` to `10`.
  - No uphill SD/SS edges remain after orientation pass.
- Task 005 validation:
  - Report smoke tests `2/2` passing.
  - Full suite now `23/23` passing.
  - Real report generated from `calibration-clean` artifacts:
    - `output/reports/calibration-clean-report.html`
- Model routing validation:
  - Escalation tests `2/2` passing.
  - Full suite updated to `25/25` passing.
  - `run_hybrid` and `run_hybrid_batch` CLIs expose escalation flags.
- Model default reversion validation:
  - Default model confirmed as `google/gemini-3-flash-preview` in single and batch runners.
  - Escalation tests remain passing with explicit lite-primary fixtures.
- Task 006 validation:
  - Graph checks tests `11/11` passing.
  - Full suite `30/30` passing.
  - Corridor SD slope mismatches reduced `4 -> 0` and reclassified to `crown_contamination` infos.
  - FNC warning/info counts unchanged vs current post-fix baseline (`16` warnings, `12` info).

### Cost and Performance
- No new model-inference spend required for graph/check work (ran on existing extraction outputs).
- Post-processing checks are deterministic Python and run locally.

### Deliverables
- Utility graph JSON outputs for calibration run:
  - `output/graphs/calibration-opt-sd.json`
  - `output/graphs/calibration-opt-ss.json`
  - `output/graphs/calibration-opt-w.json`
- `README.md` (project overview + quickstart)
- `src/report/html_report.py` (single-file HTML report CLI)
- `src/extraction/run_hybrid.py` (automatic model escalation routing)
- `docs/CODEX-TASK-006-CROWN-INVERT-HEURISTIC.md` (implemented task spec)
- Updated summary and milestone tracking format in this file.

### Risks / Follow-Ups
- Connectivity quality is still bounded by extraction completeness in the input tiles.
- Next high-value step: add a single checks CLI entry point for orchestrator use.

## 2026-02-21

### Summary
- Finalized architecture and core technical decisions (D1-D12), including hybrid extraction, provenance, coherence gating, and graph-first consistency checks.
- Completed intake foundation (Modules 1-2 + 7) with standalone CLI entry points.
- Completed extraction interface (Modules 3-4), including single-tile and batch extraction runners.
- Ran live calibration and added deterministic scorer.
- Implemented optimization pass (Task 002) to lower cost and improve reliability.

### Milestones
- Intake pipeline implemented:
  - `src/intake/tiler.py`
  - `src/intake/text_layer.py`
  - `src/intake/manifest.py`
- Extraction pipeline implemented:
  - `src/extraction/schemas.py`
  - `src/extraction/prompts.py`
  - `src/extraction/run_hybrid.py`
  - `src/extraction/run_hybrid_batch.py`
  - `src/extraction/score_calibration.py`
- Parsing baseline completed:
  - `src/utils/parsing.py`
  - `tests/test_parsing.py`

### Validation
- Page 14 tiling acceptance checks passed (3x2 tiles, overlap, no gaps).
- Tile text-layer JSONs validated with bounding boxes.
- FNC Farms coherence sweep completed (57 pages above threshold).
- Parsing tests passed for station/offset.
- Calibration accuracy reached 9/10 on core checks.

### Cost and Performance
- 20-tile cost reduced from about `$0.4047` to `$0.1515` after optimization.
- Prompt token load reduced significantly with compact schema + slim text-item payload.
- Cache validated; reruns on unchanged tiles avoided additional API spend.

### Deliverables
- Optimized prompting, retry/backoff, and caching in extraction runner.
- Batch orchestration for repeated tile subsets.
- Calibration scoring artifact generation.

### Risks / Follow-Ups
- Detailed command history, reasoning, and edge-case notes remain in `PROGRESS.md`.
- Task 007 (reference-sheet suppression) implemented with page-level fallback for mixed tile classification:
  - `is_reference_only` now set for edges from inferred signing/striping pages even when some tiles are labeled `plan_view`.
  - Connectivity checks skip `is_reference_only` edges.
- Validation:
  - Full suite `33/33` passing via unittest.
  - Corridor-expanded findings reduced after regeneration:
    - SD `6 -> 3` (removed SD `dead_end_pipe` and `unanchored_pipe` noise from reference sheets)
    - SS `18 -> 11`
    - W `24 -> 14`
  - Connectivity findings from pages `93/103` are now suppressed (`0` remaining).
- FNC regression (calibration-clean):
  - No `is_reference_only=True` edges in SD/SS/W.
  - Severity totals remain `warning=8, info=7, error=0` (no regression).
- Task 008 (null top-level metadata recovery) implemented:
  - Added pre-validation patch in `run_hybrid` to recover null/missing `tile_id` and `page_number` from text-layer metadata.
  - Added fallback page derivation from `tile_id` pattern (`pNN_rX_cY`) when text-layer page is absent.
- Validation:
  - Full suite now `35/35` passing.
  - Corridor-expanded extraction improved from `29/30 OK, 1 validation_error` to `30/30 OK, 0 validation_error`.
  - Previously failed tile `p26_r1_c0` now writes valid output (`page_number=26`) and is included downstream.
- Refreshed `corridor-expanded` graphs/findings/report after recovery.
