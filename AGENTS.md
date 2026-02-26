# AGENTS.md

## Project-Wide Progress Logging Policy

This repository uses two progress documents as mandatory operational artifacts:

- `PROGRESS.md`: detailed running log of decisions, failures, fixes, and implementation context.
- `PROGRESS_SUMMARY.md`: concise milestone summary for quick review.

### Required Behavior (all agents, all sessions)

For any meaningful implementation change, update both files in the same working session:

1. Update `PROGRESS.md` with concrete details:
   - date (Pacific time: `America/Los_Angeles`, PST/PDT seasonally)
   - what changed
   - why (decision rationale)
   - failures encountered
   - fix applied
   - validation commands/results
2. Update `PROGRESS_SUMMARY.md` with a concise entry for the same work.
3. Do not claim completion without these updates.

### Scope

Treat these as meaningful changes:

- source changes under `src/`
- test changes under `tests/`
- architecture/process changes in top-level docs (`ARCHITECTURE.md`, `README.md`, workflow docs)
- dependency/runtime/config changes that affect behavior

### Enforcement

- Local gate: `.githooks/pre-commit` runs `python scripts/check_progress_docs.py --staged`
- CI gate: `.github/workflows/progress-docs-check.yml`

If either progress file is missing from a qualifying change, the gate fails.
