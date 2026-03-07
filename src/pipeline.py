"""
Single-command pipeline runner for the Plan Reviewer tool.

Orchestrates all phases end-to-end:
  1. Tiling       — tile PDF pages into PNG + text-layer pairs
  2. Manifest     — classify sheet types, assign model tiers
  3. Extraction   — run hybrid vision extraction on all tiles (API calls)
  4. Validation   — validate the extraction package contract
  5. Graph        — build utility graphs (SD, SS, W) from extractions
  6. Checks       — run deterministic consistency checks on each graph
  7. Report       — generate self-contained HTML report

Supports --resume to skip already-completed phases when re-running a
partially-finished run directory.

Usage:
    python -m src.pipeline --pdf path/to/plans.pdf --output-dir ./runs
    python -m src.pipeline --pdf path/to/plans.pdf --output-dir ./runs --resume ./runs/run_20260306_143022
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default model (mirrors run_hybrid.py so the pipeline stays in sync)
# ---------------------------------------------------------------------------

def _default_model() -> str:
    try:
        from .extraction.run_hybrid import DEFAULT_MODEL
        return DEFAULT_MODEL
    except Exception:
        return "google/gemini-3.1-flash-lite-preview"


# ---------------------------------------------------------------------------
# Run directory layout helpers
# ---------------------------------------------------------------------------

def _make_run_id() -> str:
    """Generate a timestamped run identifier."""
    return "run_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _run_dirs(run_dir: Path) -> dict[str, Path]:
    """Return the canonical sub-directory paths for a run directory."""
    return {
        "intake": run_dir / "intake",
        "tiles": run_dir / "intake" / "tiles",
        "text_layers": run_dir / "intake" / "text_layers",
        "extractions": run_dir / "extractions",
        "graphs": run_dir / "graphs",
        "report": run_dir / "report",
    }


def _ensure_dirs(dirs: dict[str, Path]) -> None:
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Phase completion detection (for --resume)
# ---------------------------------------------------------------------------

def _tiling_complete(run_dir: Path) -> bool:
    return (run_dir / "intake" / "tiles_index.json").exists()


def _manifest_complete(run_dir: Path) -> bool:
    return (run_dir / "intake" / "manifest.json").exists()


def _extraction_complete(run_dir: Path) -> bool:
    pkg = run_dir / "extractions" / "analysis_package.json"
    summary = run_dir / "extractions" / "batch_summary.json"
    return pkg.exists() or summary.exists()


def _validation_complete(run_dir: Path) -> bool:
    return (run_dir / "extractions" / "analysis_validation.json").exists()


def _graphs_complete(run_dir: Path, utilities: list[str], prefix: str) -> bool:
    """Return True when every utility graph file exists."""
    for utility in utilities:
        graph_path = run_dir / "graphs" / f"{prefix}-{utility.lower()}.json"
        if not graph_path.exists():
            return False
    return bool(utilities)


def _checks_complete(run_dir: Path, utilities: list[str], prefix: str) -> bool:
    """Return True when every utility findings file exists."""
    for utility in utilities:
        findings_path = run_dir / "graphs" / f"{prefix}-{utility.lower()}-findings.json"
        if not findings_path.exists():
            return False
    return bool(utilities)


def _report_complete(run_dir: Path, prefix: str) -> bool:
    return (run_dir / "report" / f"{prefix}_report.html").exists()


# ---------------------------------------------------------------------------
# Utility auto-detection from manifest
# ---------------------------------------------------------------------------

def _detect_utilities_from_manifest(manifest_path: Path) -> list[str]:
    """Collect unique utility_type values across all SheetInfo entries."""
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read manifest for utility detection: %s", exc)
        return []

    seen: set[str] = set()
    for entry in entries:
        for ut in entry.get("utility_types", []):
            upper = str(ut).strip().upper()
            if upper in {"SD", "SS", "W"}:
                seen.add(upper)

    # Return in a stable order.
    result: list[str] = []
    for ut in ("SD", "SS", "W"):
        if ut in seen:
            result.append(ut)
    return result


# ---------------------------------------------------------------------------
# Phase 1: Tiling
# ---------------------------------------------------------------------------

def run_phase_tiling(
    *,
    pdf_path: Path,
    intake_dir: Path,
    dpi: int,
) -> int:
    """Tile PDF pages.  Returns total tile count."""
    from .intake.tiler import tile_pdf, _write_tiles_index

    logger.info("Phase 1/7: Tiling PDF at %s DPI ...", dpi)
    results = tile_pdf(
        pdf_path,
        intake_dir,
        dpi=dpi,
        grid_rows=2,
        grid_cols=3,
        overlap_pct=0.10,
        skip_low_coherence=True,
    )
    _write_tiles_index(results, intake_dir)
    total_tiles = sum(len(tiles) for tiles in results.values())
    logger.info("  Tiling done — %s page(s), %s tiles", len(results), total_tiles)
    return total_tiles


# ---------------------------------------------------------------------------
# Phase 2: Manifest
# ---------------------------------------------------------------------------

def run_phase_manifest(
    *,
    pdf_path: Path,
    intake_dir: Path,
) -> Path:
    """Build sheet manifest.  Returns path to manifest.json."""
    from .intake.manifest import build_manifest, save_manifest

    logger.info("Phase 2/7: Building sheet manifest ...")
    manifest = build_manifest(pdf_path)
    manifest_path = intake_dir / "manifest.json"
    save_manifest(manifest, manifest_path)
    deep_count = sum(1 for s in manifest if s.needs_deep_extraction)
    logger.info(
        "  Manifest done — %s sheets, %s need deep extraction",
        len(manifest),
        deep_count,
    )
    return manifest_path


# ---------------------------------------------------------------------------
# Phase 3: Extraction
# ---------------------------------------------------------------------------

def run_phase_extraction(
    *,
    intake_dir: Path,
    extractions_dir: Path,
    manifest_path: Path,
    model: str,
    provider: str,
    workers: int,
    dry_run: bool,
) -> int:
    """Run hybrid batch extraction.  Returns exit code from run_batch (0 or 2)."""
    from .extraction.run_hybrid_batch import run_batch
    from .extraction.config_models import EscalationConfig, ExtractionConfig, PROVIDER_ANTHROPIC

    tiles_dir = intake_dir / "tiles"
    text_layers_dir = intake_dir / "text_layers"
    summary_out = extractions_dir / "batch_summary.json"

    # Resolve API key from environment.
    if provider == PROVIDER_ANTHROPIC:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

    if not api_key and not dry_run:
        logger.warning(
            "No API key found for provider '%s'. "
            "Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in environment.",
            provider,
        )

    logger.info(
        "Phase 3/7: Extraction — model=%s provider=%s workers=%s dry_run=%s ...",
        model,
        provider,
        workers,
        dry_run,
    )

    ext_config = ExtractionConfig(
        model=model,
        api_key=api_key,
        provider=provider,
        referer="https://github.com/4creeks/plan-reviewer",
        title="Plan Reviewer",
        temperature=0.0,
        max_tokens=4096,
        timeout_sec=120,
        use_json_schema=False,
    )

    exit_code = run_batch(
        tiles_dir=tiles_dir,
        text_layers_dir=text_layers_dir,
        out_dir=extractions_dir,
        tile_globs=["*.png"],
        max_tiles=None,
        config=ext_config,
        escalation=EscalationConfig(),
        allow_low_coherence=False,
        dry_run=dry_run,
        no_cache=False,
        prompt_dir=None,
        fail_fast=False,
        summary_out=summary_out,
        max_concurrency=workers,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )

    status_label = "dry-run complete" if dry_run else ("done" if exit_code == 0 else "done with errors")
    logger.info("  Extraction %s (exit_code=%s)", status_label, exit_code)
    return exit_code


# ---------------------------------------------------------------------------
# Phase 4: Validation
# ---------------------------------------------------------------------------

def run_phase_validation(
    *,
    extractions_dir: Path,
) -> str:
    """Validate the extraction package.  Returns result value: 'pass', 'warn', or 'fail'."""
    from .extraction.validate_package import validate_extraction_package

    logger.info("Phase 4/7: Validating extraction package ...")
    report = validate_extraction_package(
        extractions_dir=extractions_dir,
        verify_hashes=True,
    )
    result = report.result.value
    logger.info(
        "  Validation %s — %s critical errors, %s warnings, bad_ratio=%.3f",
        result.upper(),
        len(report.critical_errors),
        len(report.warnings),
        report.quality.bad_ratio,
    )
    if report.critical_errors:
        for msg in report.critical_errors[:5]:
            logger.warning("    [critical] %s", msg)
        if len(report.critical_errors) > 5:
            logger.warning("    ... and %s more", len(report.critical_errors) - 5)
    return result


# ---------------------------------------------------------------------------
# Phase 5: Graph assembly
# ---------------------------------------------------------------------------

def run_phase_graphs(
    *,
    extractions_dir: Path,
    graphs_dir: Path,
    utilities: list[str],
    prefix: str,
) -> dict[str, Path]:
    """Build one utility graph per utility type.  Returns {utility: graph_path}."""
    from .graph.assembly import build_utility_graph, graph_to_dict, load_extractions_with_meta

    logger.info("Phase 5/7: Graph assembly for utilities: %s ...", utilities)
    extractions, tile_meta = load_extractions_with_meta(extractions_dir)
    logger.info("  Loaded %s extraction records", len(extractions))

    graph_paths: dict[str, Path] = {}
    for utility in utilities:
        graph = build_utility_graph(
            extractions=extractions,
            utility_type=utility,
            tile_meta_by_id=tile_meta,
        )
        payload = graph_to_dict(graph)
        # html_report.py expects: {prefix}-{utility_lower}.json
        graph_path = graphs_dir / f"{prefix}-{utility.lower()}.json"
        graph_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "  %s graph: %s nodes, %s edges -> %s",
            utility,
            graph.number_of_nodes(),
            graph.number_of_edges(),
            graph_path.name,
        )
        graph_paths[utility] = graph_path

    logger.info("  Graph assembly done")
    return graph_paths


# ---------------------------------------------------------------------------
# Phase 6: Checks
# ---------------------------------------------------------------------------

def run_phase_checks(
    *,
    graphs_dir: Path,
    utilities: list[str],
    prefix: str,
) -> dict[str, Path]:
    """Run all checks on each graph.  Returns {utility: findings_path}."""
    from .graph.checks import run_all_checks

    logger.info("Phase 6/7: Running consistency checks ...")
    findings_paths: dict[str, Path] = {}

    for utility in utilities:
        graph_path = graphs_dir / f"{prefix}-{utility.lower()}.json"
        if not graph_path.exists():
            logger.warning("  %s graph not found, skipping checks: %s", utility, graph_path)
            continue

        payload = json.loads(graph_path.read_text(encoding="utf-8"))
        graph = _graph_from_dict(payload)

        findings = run_all_checks(graph)

        findings_payload = {
            "utility_type": utility,
            "prefix": prefix,
            "graph": payload,
            "findings": [f.to_dict() for f in findings],
        }
        # html_report.py expects: {prefix}-{utility_lower}-findings.json
        findings_path = graphs_dir / f"{prefix}-{utility.lower()}-findings.json"
        findings_path.write_text(
            json.dumps(findings_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        error_count = sum(1 for f in findings if f.severity == "error")
        warn_count = sum(1 for f in findings if f.severity == "warning")
        info_count = sum(1 for f in findings if f.severity == "info")
        logger.info(
            "  %s: %s findings (errors=%s warnings=%s info=%s) -> %s",
            utility,
            len(findings),
            error_count,
            warn_count,
            info_count,
            findings_path.name,
        )
        findings_paths[utility] = findings_path

    logger.info("  Checks done")
    return findings_paths


def _graph_from_dict(payload: dict[str, Any]) -> Any:
    """Reconstruct a networkx DiGraph from a graph_to_dict payload.

    Works on shallow copies of each node/edge dict so the original payload
    is not mutated (callers may reuse it for findings output).
    """
    import networkx as nx

    utility_type = payload.get("utility_type", "")
    quality_summary = payload.get("quality_summary", {})
    graph: nx.DiGraph = nx.DiGraph(
        utility_type=utility_type,
        quality_summary=quality_summary,
    )

    for raw_node in payload.get("nodes", []):
        node = dict(raw_node)  # shallow copy — do not mutate caller's dict
        node_id = node.pop("node_id", None)
        if node_id is None:
            continue
        graph.add_node(node_id, **node)

    for raw_edge in payload.get("edges", []):
        edge = dict(raw_edge)  # shallow copy
        from_node = edge.pop("from_node", None)
        to_node = edge.pop("to_node", None)
        if from_node is None or to_node is None:
            continue
        graph.add_edge(from_node, to_node, **edge)

    return graph


# ---------------------------------------------------------------------------
# Phase 7: Report
# ---------------------------------------------------------------------------

def run_phase_report(
    *,
    graphs_dir: Path,
    report_dir: Path,
    prefix: str,
    batch_summary_path: Path | None,
    title: str,
) -> Path:
    """Generate HTML report.  Returns path to the written HTML file."""
    from .report.html_report import write_html_report

    logger.info("Phase 7/7: Generating HTML report ...")
    out_path = report_dir / f"{prefix}_report.html"
    write_html_report(
        graphs_dir=graphs_dir,
        findings_dir=graphs_dir,  # findings live alongside graphs
        prefix=prefix,
        out_path=out_path,
        batch_summary_path=batch_summary_path if (batch_summary_path and batch_summary_path.exists()) else None,
        title=title,
    )
    logger.info("  Report written: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _write_run_metadata(
    run_dir: Path,
    *,
    run_id: str,
    pdf_path: Path,
    start_time: str,
    end_time: str | None,
    phases_completed: list[str],
    utilities: list[str],
    model: str,
    provider: str,
) -> None:
    meta = {
        "run_id": run_id,
        "pdf_path": str(pdf_path.resolve()),
        "start_time": start_time,
        "end_time": end_time,
        "phases_completed": phases_completed,
        "utilities": utilities,
        "model": model,
        "provider": provider,
    }
    meta_path = run_dir / "run_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plan-reviewer-pipeline",
        description="Run the full plan review pipeline from PDF to HTML report.",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="Path to the input PDF plan set.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Parent directory for run outputs. A timestamped sub-directory is created per run.",
    )
    parser.add_argument(
        "--utilities",
        type=str,
        default=None,
        help=(
            "Comma-separated utility types to process, e.g. 'SD,SS,W'. "
            "Defaults to auto-detect from manifest."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Extraction model override (e.g. 'claude-sonnet-4-6' or an OpenRouter model ID). "
             "Defaults to the project default model.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="openrouter",
        choices=["openrouter", "anthropic"],
        help="API provider to use for extraction. Default: openrouter.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Render DPI for tiling. Default: 300.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Skip actual API calls during extraction (for testing the pipeline plumbing).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent extraction workers. Default: 1.",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help=(
            "Prefix for graph/findings/report artifact file names. "
            "Defaults to the PDF file name stem with spaces replaced by hyphens."
        ),
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Path to an existing run directory. Completed phases are detected and skipped.",
    )
    return parser


# ---------------------------------------------------------------------------
# Phase-skipping logic (used for resume)
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert a string to a safe hyphenated slug for file name use."""
    slug = re.sub(r"[^\w]+", "-", text).strip("-")
    return slug or "run"


def _resolve_prefix(pdf_path: Path, override: str | None) -> str:
    if override:
        return override
    return _slugify(pdf_path.stem)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = _build_arg_parser()
    args = parser.parse_args()

    pdf_path: Path = args.pdf.resolve()
    if not pdf_path.exists():
        logger.error("PDF not found: %s", pdf_path)
        sys.exit(1)

    model = args.model or _default_model()
    provider: str = args.provider
    dpi: int = args.dpi
    dry_run: bool = args.dry_run
    workers: int = max(1, args.workers)

    # Resolve run directory.
    if args.resume is not None:
        run_dir: Path = args.resume.resolve()
        if not run_dir.is_dir():
            logger.error("Resume directory not found: %s", run_dir)
            sys.exit(1)
        run_id = run_dir.name
        logger.info("Resuming run: %s", run_dir)
    else:
        run_id = _make_run_id()
        run_dir = args.output_dir.resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Starting new run: %s", run_dir)

    dirs = _run_dirs(run_dir)
    _ensure_dirs(dirs)

    prefix = _resolve_prefix(pdf_path, args.prefix)
    start_time = datetime.now(UTC).isoformat()
    phases_completed: list[str] = []

    # ------------------------------------------------------------------
    # Phase 1: Tiling
    # ------------------------------------------------------------------
    if _tiling_complete(run_dir):
        logger.info("Phase 1/7: Tiling — SKIPPED (tiles_index.json already exists)")
        phases_completed.append("tiling:skipped")
    else:
        try:
            run_phase_tiling(
                pdf_path=pdf_path,
                intake_dir=dirs["intake"],
                dpi=dpi,
            )
            phases_completed.append("tiling:done")
        except Exception:
            logger.exception("Phase 1 (tiling) failed")
            _write_run_metadata(
                run_dir,
                run_id=run_id,
                pdf_path=pdf_path,
                start_time=start_time,
                end_time=datetime.now(UTC).isoformat(),
                phases_completed=phases_completed + ["tiling:error"],
                utilities=[],
                model=model,
                provider=provider,
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Phase 2: Manifest
    # ------------------------------------------------------------------
    manifest_path = dirs["intake"] / "manifest.json"
    if _manifest_complete(run_dir):
        logger.info("Phase 2/7: Manifest — SKIPPED (manifest.json already exists)")
        phases_completed.append("manifest:skipped")
    else:
        try:
            manifest_path = run_phase_manifest(
                pdf_path=pdf_path,
                intake_dir=dirs["intake"],
            )
            phases_completed.append("manifest:done")
        except Exception:
            logger.exception("Phase 2 (manifest) failed")
            _write_run_metadata(
                run_dir,
                run_id=run_id,
                pdf_path=pdf_path,
                start_time=start_time,
                end_time=datetime.now(UTC).isoformat(),
                phases_completed=phases_completed + ["manifest:error"],
                utilities=[],
                model=model,
                provider=provider,
            )
            sys.exit(1)

    # Resolve utility types now that we have the manifest.
    if args.utilities:
        utilities: list[str] = [u.strip().upper() for u in args.utilities.split(",") if u.strip()]
    else:
        utilities = _detect_utilities_from_manifest(manifest_path)
        if utilities:
            logger.info("Auto-detected utilities from manifest: %s", utilities)
        else:
            logger.warning(
                "No utilities detected from manifest; defaulting to SD, SS, W. "
                "Use --utilities to specify explicitly."
            )
            utilities = ["SD", "SS", "W"]

    # Persist metadata with known utilities before the expensive phase.
    _write_run_metadata(
        run_dir,
        run_id=run_id,
        pdf_path=pdf_path,
        start_time=start_time,
        end_time=None,
        phases_completed=phases_completed,
        utilities=utilities,
        model=model,
        provider=provider,
    )

    # ------------------------------------------------------------------
    # Phase 3: Extraction
    # ------------------------------------------------------------------
    if _extraction_complete(run_dir):
        logger.info("Phase 3/7: Extraction — SKIPPED (analysis_package.json / batch_summary.json exists)")
        phases_completed.append("extraction:skipped")
        extraction_exit_code = 0
    else:
        try:
            extraction_exit_code = run_phase_extraction(
                intake_dir=dirs["intake"],
                extractions_dir=dirs["extractions"],
                manifest_path=manifest_path,
                model=model,
                provider=provider,
                workers=workers,
                dry_run=dry_run,
            )
            phases_completed.append(
                "extraction:done" if extraction_exit_code == 0 else "extraction:done_with_errors"
            )
        except Exception:
            logger.exception("Phase 3 (extraction) failed with an unexpected exception")
            _write_run_metadata(
                run_dir,
                run_id=run_id,
                pdf_path=pdf_path,
                start_time=start_time,
                end_time=datetime.now(UTC).isoformat(),
                phases_completed=phases_completed + ["extraction:error"],
                utilities=utilities,
                model=model,
                provider=provider,
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Phase 4: Validation
    # ------------------------------------------------------------------
    if _validation_complete(run_dir):
        logger.info("Phase 4/7: Validation — SKIPPED (analysis_validation.json already exists)")
        phases_completed.append("validation:skipped")
        validation_result = "pass"
    else:
        try:
            validation_result = run_phase_validation(extractions_dir=dirs["extractions"])
            phases_completed.append(f"validation:{validation_result}")
        except Exception:
            logger.exception("Phase 4 (validation) failed")
            # Warn but do not abort — graph assembly can still proceed on best-effort.
            validation_result = "error"
            logger.warning("Continuing to graph assembly despite validation failure.")
            phases_completed.append("validation:error")

    if validation_result == "fail":
        logger.warning(
            "Extraction validation FAILED. Graph assembly may produce incomplete results. "
            "Continuing anyway."
        )

    # ------------------------------------------------------------------
    # Phase 5: Graph assembly
    # ------------------------------------------------------------------
    if _graphs_complete(run_dir, utilities, prefix):
        logger.info(
            "Phase 5/7: Graph assembly — SKIPPED (graph files already exist for %s)",
            utilities,
        )
        phases_completed.append("graphs:skipped")
    else:
        try:
            run_phase_graphs(
                extractions_dir=dirs["extractions"],
                graphs_dir=dirs["graphs"],
                utilities=utilities,
                prefix=prefix,
            )
            phases_completed.append("graphs:done")
        except Exception:
            logger.exception("Phase 5 (graph assembly) failed")
            _write_run_metadata(
                run_dir,
                run_id=run_id,
                pdf_path=pdf_path,
                start_time=start_time,
                end_time=datetime.now(UTC).isoformat(),
                phases_completed=phases_completed + ["graphs:error"],
                utilities=utilities,
                model=model,
                provider=provider,
            )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Phase 6: Checks
    # ------------------------------------------------------------------
    if _checks_complete(run_dir, utilities, prefix):
        logger.info(
            "Phase 6/7: Checks — SKIPPED (findings files already exist for %s)",
            utilities,
        )
        phases_completed.append("checks:skipped")
    else:
        try:
            run_phase_checks(
                graphs_dir=dirs["graphs"],
                utilities=utilities,
                prefix=prefix,
            )
            phases_completed.append("checks:done")
        except Exception:
            logger.exception("Phase 6 (checks) failed")
            # Non-fatal: a partial findings set is still usable for the report.
            logger.warning("Continuing to report generation despite check failure.")
            phases_completed.append("checks:error")

    # ------------------------------------------------------------------
    # Phase 7: Report
    # ------------------------------------------------------------------
    batch_summary_path = dirs["extractions"] / "batch_summary.json"

    if _report_complete(run_dir, prefix):
        logger.info("Phase 7/7: Report — SKIPPED (report HTML already exists)")
        phases_completed.append("report:skipped")
    else:
        try:
            report_title = f"Plan Review — {pdf_path.name}"
            run_phase_report(
                graphs_dir=dirs["graphs"],
                report_dir=dirs["report"],
                prefix=prefix,
                batch_summary_path=batch_summary_path,
                title=report_title,
            )
            phases_completed.append("report:done")
        except Exception:
            logger.exception("Phase 7 (report) failed")
            phases_completed.append("report:error")

    # ------------------------------------------------------------------
    # Wrap up
    # ------------------------------------------------------------------
    end_time = datetime.now(UTC).isoformat()
    _write_run_metadata(
        run_dir,
        run_id=run_id,
        pdf_path=pdf_path,
        start_time=start_time,
        end_time=end_time,
        phases_completed=phases_completed,
        utilities=utilities,
        model=model,
        provider=provider,
    )

    logger.info("Pipeline complete.")
    logger.info("  Run directory: %s", run_dir)
    logger.info("  Phases: %s", phases_completed)
    report_path = dirs["report"] / f"{prefix}_report.html"
    if report_path.exists():
        logger.info("  Report: %s", report_path)
    else:
        logger.warning("  Report not generated (see errors above).")


if __name__ == "__main__":
    main()
