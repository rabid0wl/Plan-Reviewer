---
name: progress-log
description: Use when implementing, fixing, or refactoring meaningful project behavior to keep PROGRESS.md and PROGRESS_SUMMARY.md updated in the same session.
---

# Progress Log

## Purpose

Keep engineering history auditable across sessions by updating:

- `PROGRESS.md` (detailed)
- `PROGRESS_SUMMARY.md` (concise)

## When To Use

Use for any meaningful change:

- source/test changes
- architecture/process changes
- bug fixes/refactors/features
- dependency/runtime behavior changes

## Required Steps

1. Update `PROGRESS.md` with:
   - date (Pacific time: `America/Los_Angeles`, PST/PDT seasonally)
   - decision/failure/fix details
   - files touched
   - validation commands/results
2. Update `PROGRESS_SUMMARY.md` with the same scope in concise form.
3. Ensure both files reflect the same Pacific date and work item.

## Quality Bar

- No completion claim without both updates.
- No validation claim without evidence.
- Prefer newest-first entries.
