# Plan Reviewer - Progress Summary

## 2026-02-27

### Summary
- Unignored the top-level `References/` folder so it can be versioned and pushed to `main`.
- Added Git LFS tracking for the oversized reference PDF to keep the push compatible with GitHub limits.

### Milestones
- Updated `.gitignore`:
  - removed `References/`.
- Added `.gitattributes` LFS rule for:
  - `References/Dinuba Project/240085_CORRIDOR_REV 3_PLAN SET.pdf` (226.97 MB).
- Prepared `References/` for commit and push.

### Validation
- `git check-ignore -v References` (before edit) -> ignore match in `.gitignore`.
- Large-file scan identified one PDF over 95 MB and mapped it to LFS tracking.
- `git lfs track "References/Dinuba Project/240085_CORRIDOR_REV 3_PLAN SET.pdf"` -> rule created.
- `python scripts/check_progress_docs.py` -> `PASS`.

## 2026-02-25

### Summary
- Fixed Mermaid syntax compatibility issue in app architecture diagrams that caused Cursor preview parse errors.

### Milestones
- Updated node label syntax in:
  - `docs/diagrams/app-architecture.mmd`
  - `docs/diagrams/app-architecture.md`
  - `docs/diagrams/ARCHITECTURE_FLOWS.md`
- Changed `H[graph.checks.run_all_checks()]` to `H["graph.checks.run_all_checks()"]`.

### Validation
- `npx -y @mermaid-js/mermaid-cli -i docs/diagrams/app-architecture.mmd -o docs/diagrams/.app-architecture.validate.svg` -> parse success.
- `npx -y @mermaid-js/mermaid-cli -i docs/diagrams/extraction-decision-flow.mmd -o docs/diagrams/.extraction-decision-flow.validate.svg` -> parse success.
- `python scripts/check_progress_docs.py` -> `PASS`.

## 2026-02-25

### Summary
- Added markdown preview wrappers so each Mermaid diagram can be opened directly with Cursor markdown preview.

### Milestones
- Added:
  - `docs/diagrams/app-architecture.md`
  - `docs/diagrams/extraction-decision-flow.md`
- Each wrapper references its `.mmd` source and embeds the full Mermaid chart in a fenced block.

### Validation
- File check: `Get-ChildItem docs/diagrams -Name` -> wrapper files present.
- Progress gate: `python scripts/check_progress_docs.py` -> `PASS`.

## 2026-02-25

### Summary
- Added editor-viewable architecture diagrams for the full pipeline and the detailed extraction decision logic.
- Added a companion markdown guide with plain-language analogies that map directly to the implemented modules.

### Milestones
- Added:
  - `docs/diagrams/app-architecture.mmd`
  - `docs/diagrams/extraction-decision-flow.mmd`
  - `docs/diagrams/ARCHITECTURE_FLOWS.md`
- Captured both:
  - end-to-end phase/data handoffs,
  - tile-level extraction control flow (coherence, cache, escalation, validation, sanitizer recovery).

### Validation
- File creation check: `Get-ChildItem docs/diagrams -Name` -> all diagram files present.
- Progress gate: `python scripts/check_progress_docs.py` -> `PASS`.

## 2026-02-25

### Summary
- Standardized progress logging to Pacific time (`America/Los_Angeles`) so date headings match local working day.
- Updated all progress-log skill copies plus protocol/rule docs to enforce Pacific date usage.
- Corrected this session's `2026-02-26` progress headings to Pacific-local `2026-02-25`.

### Milestones
- Updated skills:
  - `.codex/skills/progress-log/SKILL.md`
  - `.claude/skills/progress-log/SKILL.md`
  - `.cursor/skills/progress-log/SKILL.md`
  - `skills/progress-log/SKILL.md`
- Updated supporting policy/rules:
  - `.cursor/rules/progress-logging.mdc`
  - `docs/PROGRESS_LOGGING_PROTOCOL.md`
  - `AGENTS.md`
- Updated progress headings:
  - `PROGRESS.md`
  - `PROGRESS_SUMMARY.md`

### Validation
- Pacific conversion check: `Get-Date` -> `2026-02-25 19:27:59 -08:00`.
- Progress gate: `python scripts/check_progress_docs.py` -> `PASS`.
## 2026-02-25

### Summary
- Archived historical `CODEX-TASK-*` design docs from `docs/` into `legacy/docs/`.
- Kept `docs/` focused on active protocol/spec/reference files.
- Updated task-doc links in README/progress history to the new archive path.

### Milestones
- Moved 8 task docs:
  - `legacy/docs/CODEX-TASK-002-OPTIMIZATION.md`
  - `legacy/docs/CODEX-TASK-003-GRAPH-FIXES.md`
  - `legacy/docs/CODEX-TASK-004-GRAPH-ORIENTATION-AND-PROXIMITY-MERGE.md`
  - `legacy/docs/CODEX-TASK-005-HTML-REPORT.md`
  - `legacy/docs/CODEX-TASK-006-CROWN-INVERT-HEURISTIC.md`
  - `legacy/docs/CODEX-TASK-007-REFERENCE-SHEET-SUPPRESSION.md`
  - `legacy/docs/CODEX-TASK-008-NULL-PAGE-NUMBER-RECOVERY.md`
  - `legacy/docs/CODEX-TASK-009-STRUCTURED-OUTPUT.md`
- Updated references:
  - `README.md`
  - `PROGRESS.md`
  - `PROGRESS_SUMMARY.md`

### Validation
- Progress gate: `python scripts/check_progress_docs.py` -> `PASS`.
## 2026-02-25

### Summary
- Archived the first Streamlit-based iteration under `legacy/iteration-1-streamlit/`.
- Cleaned active repo paths so current development reflects CLI-only pipeline work.
- Removed Streamlit from active dependencies and updated docs to match.

### Milestones
- Legacy archive created:
  - `legacy/iteration-1-streamlit/plan_reviewer.py`
  - `legacy/iteration-1-streamlit/.streamlit/config.toml`
  - `legacy/iteration-1-streamlit/pyproject.toml`
  - `legacy/iteration-1-streamlit/README.md`
- Active docs/config updated:
  - `README.md`
  - `ARCHITECTURE.md`
  - `requirements.txt`
  - `.gitignore`
- Cleanup pass executed:
  - cache/artifact clutter removed from active tree (`__pycache__`, `.pytest_cache`, `*.egg-info`, accidental scratch/log files).

### Validation
- Full test suite: `python -m unittest discover -s tests -v` -> `55/55` passing.
- Progress gate: `python scripts/check_progress_docs.py` -> `PASS`.

## 2026-02-25

### Summary
- Implemented model-agnostic progress logging enforcement so journal files stay current across agents/sessions.
- Added repository policy, shared `progress-log` skill wrappers, local pre-commit gate, and CI gate.

### Milestones
- Policy and protocol:
  - `AGENTS.md`
  - `docs/PROGRESS_LOGGING_PROTOCOL.md`
- Skill rollout:
  - `skills/progress-log/SKILL.md`
  - `.cursor/skills/progress-log/SKILL.md`
  - `.claude/skills/progress-log/SKILL.md`
  - `.codex/skills/progress-log/SKILL.md`
  - `.cursor/rules/progress-logging.mdc`
- Enforcement:
  - `scripts/check_progress_docs.py`
  - `.githooks/pre-commit`
  - `scripts/setup_progress_hook.ps1`
  - `.github/workflows/progress-docs-check.yml`

### Validation
- Hook path configured (`git config core.hooksPath .githooks` via setup script).
- Progress gate script passes in staged and comparison modes.

## 2026-02-22

### Summary
- Completed Task 007: reference-sheet suppression for signing/striping-derived connectivity noise.
- Completed Task 008: null top-level metadata recovery (`tile_id`, `page_number`) before schema validation.
- Completed Task 009: structured output (`response_format=json_object`) with safe provider fallback.
- Ran a housekeeping pass (dedupe helpers, named thresholds, docstring consistency).

### Milestones
- Graph connectivity quality hardening:
  - `is_reference_only` edge tagging and suppression in connectivity checks.
  - page-level reference fallback for mixed tile classification on signing/striping sheets.
- Extraction robustness hardening:
  - pre-validation metadata correction in `run_hybrid`.
  - fallback page derivation from `tile_id` (`pNN_rX_cY`).
  - direct JSON parse path with regex extractor retained as fallback.
- Maintainability cleanup:
  - structure-type normalization source-of-truth centralized in `src/extraction/schemas.py`.
  - graph/check magic thresholds moved to module constants.

### Validation
- Full test suite: `40/40` passing (`python -m unittest discover -s tests -v`).
- Corridor-expanded extraction recovered from `29/30` to `30/30` OK after null-metadata fix.
- Corridor connectivity findings from pages 93/103 are suppressed as intended after reference-sheet logic.
- Structured-output smoke test passed with `status: ok` and bare JSON raw response.

### Deliverables
- Key updated files:
  - `src/extraction/run_hybrid.py`
  - `src/extraction/schemas.py`
  - `src/graph/assembly.py`
  - `src/graph/checks.py`
  - `tests/test_run_hybrid_escalation.py`
- Task docs added:
  - `legacy/docs/CODEX-TASK-007-REFERENCE-SHEET-SUPPRESSION.md`
  - `legacy/docs/CODEX-TASK-008-NULL-PAGE-NUMBER-RECOVERY.md`
  - `legacy/docs/CODEX-TASK-009-STRUCTURED-OUTPUT.md`

### Repo Status
- Pushed to `main`:
  - `5dc44dc`
  - `d7a91ba`

## 2026-02-22

### Summary
- Implemented Modules 5-6 (graph merge/assembly/checks) and stabilized false-positive behavior.
- Added HTML report generator (`src/report/html_report.py`) and smoke tests.
- Implemented crown/invert contamination heuristic and validated no regression on FNC baseline.

### Validation
- Full suite reached `30/30` by end of day.
- Calibration scoring held at `9/10`.

## 2026-02-21

### Summary
- Finalized architecture and implemented intake + extraction foundations (Modules 1-4 + 7).
- Validated tiling, text-layer extraction, coherence gating, and calibration workflow.

### Validation
- Parsing tests passed (`parse_station`, `parse_offset`).
- FNC coherence sweep completed and calibration baseline established (`9/10`).





