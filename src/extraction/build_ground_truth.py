"""Dev utility: build page-level ground truth tuples from live hybrid extraction."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..intake.tiler import tile_pdf
from ..utils.parsing import parse_station
from .run_hybrid_batch import DEFAULT_MODEL, run_batch

logger = logging.getLogger(__name__)


def _load_extractions(folder: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(folder.glob("*.json")):
        if p.name.endswith(".meta.json") or p.name == "batch_summary.json":
            continue
        rows.append(json.loads(p.read_text(encoding="utf-8")))
    return rows


def _station_sort_key(value: str | None) -> float:
    parsed = parse_station(value or "")
    return parsed if parsed is not None else 1e12


def _print_structures(extractions: list[dict[str, Any]], structure_type: str | None) -> None:
    seen: set[tuple[str, str, float | None, str]] = set()
    rows: list[tuple[str, float | None, str, str]] = []
    for ext in extractions:
        for s in ext.get("structures", []):
            stype = str(s.get("structure_type", "")).upper()
            if structure_type and structure_type.upper() not in stype:
                continue
            station = str(s.get("station") or "")
            offset = str(s.get("offset") or "")
            rim = s.get("rim_elevation")
            key = (station, rim, stype)
            if not station or key in seen:
                continue
            seen.add(key)
            rows.append((station, rim, offset, stype))

    rows.sort(key=lambda r: _station_sort_key(r[0]))
    print("# Structure ground truth (from text-layer-guided extraction)")
    print("expected_structures = [")
    for station, rim, offset, stype in rows:
        rim_str = "None" if rim is None else f"{float(rim):.2f}"
        print(f"    ({station!r}, {rim_str}),   # {stype}, {offset}")
    print("]")


def _print_pipes(extractions: list[dict[str, Any]], pipe_type: str | None) -> None:
    seen: set[tuple[str, str, float | None, float | None]] = set()
    rows: list[tuple[str, str, float | None, float | None]] = []
    for ext in extractions:
        for p in ext.get("pipes", []):
            ptype = str(p.get("pipe_type", "")).upper()
            if pipe_type and pipe_type.upper() != ptype:
                continue
            size = str(p.get("size") or "")
            slope = p.get("slope")
            length = p.get("length_lf")
            key = (ptype, size, slope, length)
            if not ptype or not size or key in seen:
                continue
            seen.add(key)
            rows.append(key)

    rows.sort(key=lambda r: (r[0], r[1], r[2] if r[2] is not None else 1e9))
    print("# Pipe ground truth (from text-layer-guided extraction)")
    print("expected_pipes = [")
    for ptype, size, slope, length in rows:
        slope_str = "None" if slope is None else f"{float(slope):.4f}"
        length_str = "None" if length is None else f"{float(length):.1f}"
        print(f"    ({ptype!r}, {size!r}, {slope_str}, {length_str}),")
    print("]")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Build ground truth tuples from one page extraction.")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to PDF.")
    parser.add_argument("--page", type=int, required=True, help="1-indexed page number.")
    parser.add_argument("--structure-type", type=str, default=None, help="Filter structure type, e.g., SSMH.")
    parser.add_argument("--pipe-type", type=str, default=None, help="Filter pipe type, e.g., SS.")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="OpenRouter model id.")
    parser.add_argument("--timeout-sec", type=int, default=180, help="API timeout seconds.")
    parser.add_argument("--output-root", type=Path, default=Path("output/ground-truth"), help="Working output root.")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise SystemExit("Missing OPENROUTER_API_KEY in environment/.env")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work_dir = args.output_root / f"p{args.page}_{run_id}"
    tile_pdf(args.pdf, work_dir, page_numbers=[args.page], skip_low_coherence=False)
    out_dir = work_dir / "extractions"
    code = run_batch(
        tiles_dir=work_dir / "tiles",
        text_layers_dir=work_dir / "text_layers",
        out_dir=out_dir,
        tile_globs=[f"p{args.page}_*.png"],
        max_tiles=None,
        model=args.model,
        api_key=api_key,
        referer="https://planreviewer.local",
        title="Plan Reviewer Ground Truth Builder",
        temperature=0.0,
        max_tokens=4096,
        timeout_sec=args.timeout_sec,
        allow_low_coherence=False,
        dry_run=False,
        no_cache=True,
        prompt_dir=None,
        fail_fast=False,
        summary_out=out_dir / "batch_summary.json",
    )
    if code != 0:
        raise SystemExit(f"Batch extraction completed with non-zero status: {code}")

    rows = _load_extractions(out_dir)
    print(f"# Source page: {args.page} | extracted tiles: {len(rows)} | output: {out_dir}")
    _print_structures(rows, args.structure_type)
    if args.pipe_type:
        print()
        _print_pipes(rows, args.pipe_type)


if __name__ == "__main__":
    main()
