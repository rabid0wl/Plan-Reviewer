"""Deterministic JSON helpers and atomic file writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def canonical_json_dumps(payload: Any) -> str:
    """Serialize JSON deterministically for hashing/canonical comparisons."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_sha256(payload: Any) -> str:
    """Return SHA-256 of deterministic JSON serialization."""
    raw = canonical_json_dumps(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    """Compute SHA-256 for a file using chunked reads."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    """
    Atomically write JSON by writing a temp file then replacing the target path.

    Uses `os.replace` to guarantee atomic replacement semantics on the same
    filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, ensure_ascii=False, sort_keys=sort_keys)
        os.replace(str(tmp_path), str(path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
