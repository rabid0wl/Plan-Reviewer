# Progress Logging Protocol

This repository maintains two mandatory progress artifacts:

- `PROGRESS.md` for detailed engineering history.
- `PROGRESS_SUMMARY.md` for concise rollups.

## Update Contract

For any meaningful implementation change in the same session:

1. Add a detailed entry to `PROGRESS.md`:
   - date (Pacific time: `America/Los_Angeles`, PST/PDT seasonally)
   - decision/failure/fix
   - files touched
   - validation run and result
2. Add a concise entry to `PROGRESS_SUMMARY.md`:
   - summary
   - milestones
   - validation outcome
3. Keep both files aligned to the same work scope and Pacific date.

## Timezone Standard

- Use Pacific time for all progress dates/timestamps: `America/Los_Angeles`.
- If recording date-only headings (for example `## YYYY-MM-DD`), use the Pacific calendar date.

## What Counts as Meaningful

- changes under `src/` or `tests/`
- architecture/workflow behavior changes
- dependency/runtime/config changes affecting behavior
- major refactors, bug fixes, or feature additions

## Do Not Skip

- Do not close a task without progress updates.
- Do not leave summary stale while detail advances.
- Do not record validation claims without command evidence.

## Enforcement

- local: `.githooks/pre-commit` runs `python scripts/check_progress_docs.py --staged`
- CI: `.github/workflows/progress-docs-check.yml`
