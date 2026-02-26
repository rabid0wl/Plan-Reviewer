#!/usr/bin/env python3
"""Fail when meaningful changes do not include PROGRESS docs updates."""

from __future__ import annotations

import argparse
import subprocess
import sys


PROGRESS_FILES = {"PROGRESS.md", "PROGRESS_SUMMARY.md"}

IGNORE_PREFIXES = (
    ".git/",
    "output/",
    "logs/",
    "References/",
    "test-extractions/",
    "__pycache__/",
    ".pytest_cache/",
)

ENFORCEMENT_FILES = {
    "AGENTS.md",
    "docs/PROGRESS_LOGGING_PROTOCOL.md",
    "scripts/check_progress_docs.py",
    "scripts/setup_progress_hook.ps1",
    ".githooks/pre-commit",
    ".github/workflows/progress-docs-check.yml",
    ".cursor/rules/progress-logging.mdc",
    ".cursor/skills/progress-log/SKILL.md",
    ".claude/skills/progress-log/SKILL.md",
    ".codex/skills/progress-log/SKILL.md",
    "skills/progress-log/SKILL.md",
}


def run_git(args: list[str]) -> list[str]:
    try:
        out = subprocess.check_output(["git", *args], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        print("[progress-check] WARN: git command failed:", " ".join(exc.cmd))
        print(exc.output.strip())
        return []
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def get_changed_files(staged: bool, against: str | None) -> list[str]:
    if staged:
        return run_git(["diff", "--name-only", "--cached"])
    if against:
        return run_git(["diff", "--name-only", f"{against}...HEAD"])
    return run_git(["diff", "--name-only"])


def is_ignored(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in IGNORE_PREFIXES)


def is_relevant(path: str) -> bool:
    if is_ignored(path):
        return False
    if path in PROGRESS_FILES:
        return False
    if path in ENFORCEMENT_FILES:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate progress docs are updated.")
    parser.add_argument("--staged", action="store_true", help="Check staged changes")
    parser.add_argument("--against", help="Compare against revision, e.g. origin/main")
    args = parser.parse_args()

    changed = get_changed_files(staged=args.staged, against=args.against)
    if not changed:
        print("[progress-check] No changed files detected; skipping.")
        return 0

    relevant = [path for path in changed if is_relevant(path)]
    if not relevant:
        print("[progress-check] Only progress/enforcement files changed; passing.")
        return 0

    changed_set = set(changed)
    missing = sorted(path for path in PROGRESS_FILES if path not in changed_set)
    if missing:
        print("[progress-check] FAIL: progress docs are missing updates.")
        print("[progress-check] Relevant changed files:")
        for path in relevant:
            print(f"  - {path}")
        print("[progress-check] Missing required updates:")
        for path in missing:
            print(f"  - {path}")
        print(
            "[progress-check] Update both PROGRESS.md and PROGRESS_SUMMARY.md "
            "for meaningful changes."
        )
        return 1

    print("[progress-check] PASS: progress docs updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

