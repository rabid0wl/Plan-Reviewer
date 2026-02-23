"""Generate self-contained HTML plan review reports from graph/findings artifacts."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils.parsing import parse_station

UTILITIES = ("SD", "SS", "W")
UTILITY_LABELS = {
    "SD": "Storm Drain",
    "SS": "Sanitary Sewer",
    "W": "Water",
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
TILE_ID_PATTERN = re.compile(r"p(?P<page>\d+)_r\d+_c\d+", re.IGNORECASE)


@dataclass(frozen=True)
class ReportArtifacts:
    graphs: dict[str, dict[str, Any]]
    findings: dict[str, dict[str, Any]]
    warnings: list[str]


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _to_int_list(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    parsed: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            parsed.add(value)
            continue
        if isinstance(value, float):
            parsed.add(int(value))
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                parsed.add(int(stripped))
    return sorted(parsed)


def _format_float(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def _format_money(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "$0.00" if digits == 2 else "$0.0000"
    return f"${number:.{digits}f}"


def _read_json(path: Path, *, warnings: list[str], label: str) -> dict[str, Any] | None:
    if not path.exists():
        warnings.append(f"Missing {label}: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(f"Invalid JSON in {label} ({path}): {exc}")
        return None
    if not isinstance(payload, dict):
        warnings.append(f"Unexpected JSON root for {label} ({path}); expected object.")
        return None
    return payload


def load_report_artifacts(*, graphs_dir: Path, findings_dir: Path, prefix: str) -> ReportArtifacts:
    """Load utility graph/findings files with graceful missing-file handling."""
    warnings: list[str] = []
    graphs: dict[str, dict[str, Any]] = {}
    findings: dict[str, dict[str, Any]] = {}

    for utility in UTILITIES:
        utility_lower = utility.lower()
        graph_path = graphs_dir / f"{prefix}-{utility_lower}.json"
        finding_path = findings_dir / f"{prefix}-{utility_lower}-findings.json"

        graph_payload = _read_json(graph_path, warnings=warnings, label=f"graph JSON for {utility}")
        finding_payload = _read_json(
            finding_path,
            warnings=warnings,
            label=f"findings JSON for {utility}",
        )
        if graph_payload is not None:
            graphs[utility] = graph_payload
        if finding_payload is not None:
            findings[utility] = finding_payload

    if not graphs and not findings:
        warnings.append("No graph/findings artifacts were loaded.")

    return ReportArtifacts(graphs=graphs, findings=findings, warnings=warnings)


def _get_quality_summary(artifacts: ReportArtifacts) -> dict[str, Any]:
    for payload in artifacts.findings.values():
        graph_block = payload.get("graph")
        if isinstance(graph_block, dict):
            quality = graph_block.get("quality_summary")
            if isinstance(quality, dict):
                return quality
    for payload in artifacts.graphs.values():
        quality = payload.get("quality_summary")
        if isinstance(quality, dict):
            return quality
    return {}


def _collect_pages_from_batch(batch_summary: dict[str, Any] | None) -> list[int]:
    if not isinstance(batch_summary, dict):
        return []
    pages: set[int] = set()
    results = batch_summary.get("results")
    if not isinstance(results, list):
        return []
    for result in results:
        if not isinstance(result, dict):
            continue
        meta = result.get("meta")
        if not isinstance(meta, dict):
            continue
        tile_id = meta.get("tile_id")
        if not isinstance(tile_id, str):
            continue
        match = TILE_ID_PATTERN.search(tile_id)
        if not match:
            continue
        pages.add(int(match.group("page")))
    return sorted(pages)


def _collect_pages_from_artifacts(artifacts: ReportArtifacts) -> list[int]:
    pages: set[int] = set()

    for payload in artifacts.graphs.values():
        nodes = payload.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    pages.update(_to_int_list(node.get("source_page_numbers")))
        edges = payload.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, dict):
                    pages.update(_to_int_list(edge.get("source_page_numbers")))

    for payload in artifacts.findings.values():
        rows = payload.get("findings")
        if not isinstance(rows, list):
            continue
        for finding in rows:
            if isinstance(finding, dict):
                pages.update(_to_int_list(finding.get("source_sheets")))

    return sorted(pages)


def _format_pages(pages: list[int]) -> str:
    if not pages:
        return "-"
    return ", ".join(str(page) for page in pages)


def _batch_model(batch_summary: dict[str, Any] | None) -> str:
    if not isinstance(batch_summary, dict):
        return "-"
    model = batch_summary.get("model")
    if not isinstance(model, str) or not model.strip():
        return "-"
    return model.strip()


def _batch_total_cost(batch_summary: dict[str, Any] | None) -> float:
    if not isinstance(batch_summary, dict):
        return 0.0
    total = 0.0
    results = batch_summary.get("results")
    if not isinstance(results, list):
        return total
    for result in results:
        if not isinstance(result, dict):
            continue
        meta = result.get("meta")
        if not isinstance(meta, dict):
            continue
        usage = meta.get("usage")
        if not isinstance(usage, dict):
            continue
        cost = _to_float(usage.get("cost"))
        if cost is not None:
            total += cost
    return total


def _quality_ratio(quality_summary: dict[str, Any]) -> float:
    total = int(_to_float(quality_summary.get("total_tiles")) or 0)
    if total <= 0:
        return 0.0
    sanitized = int(_to_float(quality_summary.get("sanitized_tiles")) or 0)
    skipped = int(_to_float(quality_summary.get("skipped_tiles")) or 0)
    return (sanitized + skipped) / total


def _severity_rank(value: str) -> int:
    return SEVERITY_ORDER.get(value.lower(), 99)


def _collect_findings(artifacts: ReportArtifacts) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for utility, payload in artifacts.findings.items():
        utility_label = str(payload.get("utility_type") or utility).upper()
        findings = payload.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "info").lower()
            row = {
                "severity": severity,
                "utility": utility_label,
                "finding_type": str(finding.get("finding_type") or "-"),
                "description": str(finding.get("description") or "-"),
                "source_sheets": _to_int_list(finding.get("source_sheets")),
            }
            rows.append(row)

    rows.sort(
        key=lambda row: (
            _severity_rank(str(row.get("severity", "info"))),
            str(row.get("utility", "")),
            str(row.get("finding_type", "")),
            str(row.get("description", "")),
        )
    )
    return rows


def _count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for row in findings:
        severity = str(row.get("severity") or "info").lower()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _station_sort_key(station: Any, station_ft: Any) -> tuple[int, float, str]:
    station_num = _to_float(station_ft)
    if station_num is None and isinstance(station, str):
        station_num = parse_station(station)
    if station_num is None:
        return (1, float("inf"), str(station or ""))
    return (0, station_num, str(station or ""))


def _format_inverts(inverts: Any) -> str:
    if not isinstance(inverts, list) or not inverts:
        return "-"
    parts: list[str] = []
    for invert in inverts:
        if not isinstance(invert, dict):
            continue
        direction = str(invert.get("direction") or "?").upper()
        elevation = _to_float(invert.get("elevation"))
        if elevation is None:
            parts.append(direction)
            continue
        parts.append(f"{direction}:{elevation:.2f}")
    return ", ".join(parts) if parts else "-"


def _format_pages_for_row(values: Any) -> str:
    pages = _to_int_list(values)
    if not pages:
        return "-"
    return ", ".join(str(page) for page in pages)


def _format_provenance(pages_value: Any, tile_ids_value: Any) -> str:
    pages = _to_int_list(pages_value)
    tiles = tile_ids_value if isinstance(tile_ids_value, list) else []
    tile_count = len({str(tile_id) for tile_id in tiles if str(tile_id).strip()})
    page_text = ",".join(str(page) for page in pages) if pages else "-"
    if tile_count == 0:
        return f"pg {page_text}"
    return f"pg {page_text}, {tile_count} tile(s)"


def _node_label(node_by_id: dict[str, dict[str, Any]], node_id: str) -> str:
    node = node_by_id.get(node_id, {})
    if node.get("kind") == "orphan_anchor":
        return "ORPHAN"
    structure_type = str(node.get("structure_type") or "").strip().upper()
    station = str(node.get("station") or "").strip()
    if structure_type and station:
        return f"{structure_type} {station}"
    if structure_type:
        return structure_type
    if station:
        return station
    return node_id


def _collect_structure_rows(graph_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    nodes = graph_payload.get("nodes")
    if not isinstance(nodes, list):
        return rows
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("kind") != "structure":
            continue
        row = {
            "station": str(node.get("station") or "-"),
            "offset": str(node.get("offset") or "-"),
            "structure_type": str(node.get("structure_type") or "-"),
            "size": str(node.get("size") or "-"),
            "rim": _format_float(node.get("rim_elevation"), 2),
            "inverts": _format_inverts(node.get("inverts")),
            "notes": str(node.get("notes") or "-"),
            "source_sheets": _format_pages_for_row(node.get("source_page_numbers")),
            "provenance": _format_provenance(node.get("source_page_numbers"), node.get("source_tile_ids")),
            "_station_sort": _station_sort_key(node.get("station"), node.get("station_ft")),
        }
        rows.append(row)
    rows.sort(key=lambda row: row["_station_sort"])
    for row in rows:
        row.pop("_station_sort", None)
    return rows


def _collect_pipe_rows(graph_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    nodes = graph_payload.get("nodes")
    edges = graph_payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return rows

    node_by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("node_id"), str)
    }

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_node = str(edge.get("from_node") or "")
        to_node = str(edge.get("to_node") or "")
        from_kind = node_by_id.get(from_node, {}).get("kind")
        to_kind = node_by_id.get(to_node, {}).get("kind")
        if from_kind == "orphan_anchor" and to_kind == "orphan_anchor":
            continue

        confidence = str(edge.get("matched_confidence") or "-")
        if bool(edge.get("oriented_by_gravity")):
            confidence = f"{confidence}; gravity-oriented"

        row = {
            "from": _node_label(node_by_id, from_node),
            "to": _node_label(node_by_id, to_node),
            "size": str(edge.get("size") or "-"),
            "length": _format_float(edge.get("length_lf"), 2),
            "slope": _format_float(edge.get("slope"), 4),
            "material": str(edge.get("material") or "-"),
            "notes": str(edge.get("notes") or "-"),
            "confidence": confidence,
            "source_sheets": _format_pages_for_row(edge.get("source_page_numbers")),
            "provenance": _format_provenance(edge.get("source_page_numbers"), edge.get("source_tile_ids")),
            "_station_sort": _station_sort_key(
                edge.get("from_station"),
                node_by_id.get(from_node, {}).get("station_ft"),
            ),
        }
        rows.append(row)

    rows.sort(key=lambda row: row["_station_sort"])
    for row in rows:
        row.pop("_station_sort", None)
    return rows


def _render_table(headers: list[str], rows: list[list[str]], *, class_name: str = "") -> str:
    if not rows:
        return '<p class="empty">No rows.</p>'

    table_class = "data-table"
    if class_name:
        table_class = f"{table_class} {class_name}"

    header_html = "".join(f"<th>{_escape(header)}</th>" for header in headers)
    row_html_parts: list[str] = []
    for row in rows:
        cells = "".join(f"<td>{_escape(cell)}</td>" for cell in row)
        row_html_parts.append(f"<tr>{cells}</tr>")
    body_html = "".join(row_html_parts)
    return f'<table class="{_escape(table_class)}"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>'


def _render_findings_table(findings_rows: list[dict[str, Any]]) -> str:
    if not findings_rows:
        return '<p class="empty">No findings.</p>'

    header_html = "".join(
        [
            "<th>Severity</th>",
            "<th>Utility</th>",
            "<th>Type</th>",
            "<th>Description</th>",
            "<th>Sheet(s)</th>",
        ]
    )
    body_parts: list[str] = []
    for row in findings_rows:
        severity = str(row.get("severity") or "info").lower()
        severity_label = severity.upper()
        sheets = _format_pages(list(row.get("source_sheets") or []))
        body_parts.append(
            "".join(
                [
                    f'<tr class="sev-{_escape(severity)}">',
                    f"<td>{_escape(severity_label)}</td>",
                    f"<td>{_escape(row.get('utility', '-'))}</td>",
                    f"<td>{_escape(row.get('finding_type', '-'))}</td>",
                    f"<td>{_escape(row.get('description', '-'))}</td>",
                    f"<td>{_escape(sheets)}</td>",
                    "</tr>",
                ]
            )
        )

    body_html = "".join(body_parts)
    return (
        '<table class="data-table findings-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
    )


def _render_batch_results_table(batch_summary: dict[str, Any] | None) -> str:
    if not isinstance(batch_summary, dict):
        return '<p class="empty">Batch summary not provided.</p>'

    results = batch_summary.get("results")
    if not isinstance(results, list) or not results:
        return '<p class="empty">No per-tile batch results found.</p>'

    rows: list[list[str]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        meta = result.get("meta")
        if not isinstance(meta, dict):
            continue

        tile_id = str(meta.get("tile_id") or "-")
        coherence = _format_float(meta.get("coherence_score"), 3)
        structures = str(int(_to_float(meta.get("structures_count")) or 0))
        pipes = str(int(_to_float(meta.get("pipes_count")) or 0))
        callouts = str(int(_to_float(meta.get("callouts_count")) or 0))
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        cost_text = _format_money(usage.get("cost"), 4)

        sanitized = bool(meta.get("sanitized", False))
        sanitized_text = "no"
        if sanitized:
            dropped = meta.get("dropped_invalid_counts")
            dropped_parts: list[str] = []
            if isinstance(dropped, dict):
                for key in ("structures", "pipes", "inverts", "callouts"):
                    count = int(_to_float(dropped.get(key)) or 0)
                    if count > 0:
                        dropped_parts.append(f"{key}={count}")
            sanitized_text = "yes"
            if dropped_parts:
                sanitized_text += f" ({', '.join(dropped_parts)})"

        rows.append([tile_id, coherence, structures, pipes, callouts, cost_text, sanitized_text])

    return _render_table(
        ["Tile", "Coherence", "Structures", "Pipes", "Callouts", "Cost", "Sanitized"],
        rows,
    )


def render_html_report(
    *,
    graphs_dir: Path,
    findings_dir: Path,
    prefix: str,
    batch_summary_path: Path | None = None,
    title: str = "Plan Review Report",
) -> str:
    """Build report HTML content from graph/findings artifacts."""
    artifacts = load_report_artifacts(graphs_dir=graphs_dir, findings_dir=findings_dir, prefix=prefix)
    warnings = list(artifacts.warnings)

    batch_summary: dict[str, Any] | None = None
    if batch_summary_path is not None:
        batch_summary = _read_json(
            batch_summary_path,
            warnings=warnings,
            label="batch summary JSON",
        )

    findings_rows = _collect_findings(artifacts)
    counts = _count_by_severity(findings_rows)

    quality_summary = _get_quality_summary(artifacts)
    quality_grade = str(quality_summary.get("quality_grade") or "-")
    total_tiles = int(_to_float(quality_summary.get("total_tiles")) or 0)
    ok_tiles = int(_to_float(quality_summary.get("ok_tiles")) or 0)
    sanitized_tiles = int(_to_float(quality_summary.get("sanitized_tiles")) or 0)
    skipped_tiles = int(_to_float(quality_summary.get("skipped_tiles")) or 0)
    bad_ratio = _quality_ratio(quality_summary)

    pages = _collect_pages_from_batch(batch_summary)
    if not pages:
        pages = _collect_pages_from_artifacts(artifacts)

    completed_at = "-"
    if isinstance(batch_summary, dict):
        raw_completed = batch_summary.get("completed_at")
        if isinstance(raw_completed, str) and raw_completed.strip():
            completed_at = raw_completed.strip()

    if completed_at == "-":
        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    model = _batch_model(batch_summary)
    total_cost = _batch_total_cost(batch_summary)

    summary_banners = "".join(
        [
            f'<span class="badge badge-error">{counts.get("error", 0)} errors</span>',
            f'<span class="badge badge-warning">{counts.get("warning", 0)} warnings</span>',
            f'<span class="badge badge-info">{counts.get("info", 0)} info</span>',
        ]
    )

    structure_sections: list[str] = []
    pipe_sections: list[str] = []
    for utility in UTILITIES:
        graph_payload = artifacts.graphs.get(utility)
        if not isinstance(graph_payload, dict):
            continue

        utility_name = UTILITY_LABELS.get(utility, utility)

        structure_rows = _collect_structure_rows(graph_payload)
        structure_table_rows = [
            [
                row["station"],
                row["offset"],
                row["structure_type"],
                row["size"],
                row["rim"],
                row["inverts"],
                row["notes"],
                row["source_sheets"],
                row["provenance"],
            ]
            for row in structure_rows
        ]
        structure_sections.append(
            "".join(
                [
                    f"<h3>{_escape(utility_name)} ({_escape(utility)}) - {len(structure_rows)} structures</h3>",
                    _render_table(
                        [
                            "Station",
                            "Offset",
                            "Type",
                            "Size",
                            "RIM",
                            "Inverts",
                            "Notes",
                            "Source Sheets",
                            "Provenance",
                        ],
                        structure_table_rows,
                    ),
                ]
            )
        )

        pipe_rows = _collect_pipe_rows(graph_payload)
        pipe_table_rows = [
            [
                row["from"],
                row["to"],
                row["size"],
                row["length"],
                row["slope"],
                row["material"],
                row["notes"],
                row["confidence"],
                row["source_sheets"],
                row["provenance"],
            ]
            for row in pipe_rows
        ]
        pipe_sections.append(
            "".join(
                [
                    f"<h3>{_escape(utility_name)} ({_escape(utility)}) - {len(pipe_rows)} pipes</h3>",
                    _render_table(
                        [
                            "From",
                            "To",
                            "Size",
                            "Length (LF)",
                            "Slope",
                            "Material",
                            "Notes",
                            "Confidence",
                            "Source Sheets",
                            "Provenance",
                        ],
                        pipe_table_rows,
                    ),
                ]
            )
        )

    quality_warnings = quality_summary.get("warnings")
    quality_warning_list = ""
    if isinstance(quality_warnings, list) and quality_warnings:
        warning_rows = "".join(f"<li>{_escape(item)}</li>" for item in quality_warnings)
        quality_warning_list = f"<ul>{warning_rows}</ul>"

    data_warning_html = ""
    if warnings:
        warning_rows = "".join(f"<li>{_escape(item)}</li>" for item in warnings)
        data_warning_html = (
            '<section class="panel warning-panel"><h2>Data Warnings</h2>'
            f"<ul>{warning_rows}</ul>"
            "</section>"
        )

    risk_banner_html = ""
    if bad_ratio > 0.30:
        risk_banner_html = (
            '<div class="risk-banner">'
            f"Warning: extraction quality below threshold ({bad_ratio * 100:.1f}% degraded tiles). "
            "Findings may be incomplete."
            "</div>"
        )

    html_content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #4b5563;
      --line: #d1d5db;
      --error-bg: #fee2e2;
      --warning-bg: #fef3c7;
      --info-bg: #dbeafe;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", "Tahoma", "Geneva", sans-serif;
      line-height: 1.4;
    }}
    .page {{
      max-width: 1200px;
      margin: 20px auto 28px auto;
      padding: 0 12px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 14px;
      margin-bottom: 12px;
    }}
    .title-block h1 {{ margin: 0 0 8px 0; font-size: 1.7rem; }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px 12px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .risk-banner {{
      background: #fff5cc;
      color: #7a4e00;
      border: 1px solid #f5d98f;
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 12px;
      font-weight: 600;
    }}
    .badge-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.9rem;
    }}
    .badge-error {{ background: var(--error-bg); }}
    .badge-warning {{ background: var(--warning-bg); }}
    .badge-info {{ background: var(--info-bg); }}
    h2 {{ margin: 0 0 10px 0; font-size: 1.15rem; }}
    h3 {{ margin: 16px 0 8px 0; font-size: 1.0rem; }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 12px;
      font-size: 0.92rem;
    }}
    .data-table th,
    .data-table td {{
      border: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      padding: 6px 8px;
    }}
    .data-table thead th {{
      background: #eef2f7;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    .data-table tbody tr:nth-child(even) {{ background: #fafafa; }}
    .findings-table .sev-error td {{ background: var(--error-bg); }}
    .findings-table .sev-warning td {{ background: var(--warning-bg); }}
    .findings-table .sev-info td {{ background: var(--info-bg); }}
    .empty {{
      color: var(--muted);
      font-style: italic;
      margin: 0 0 12px 0;
    }}
    .warning-panel {{
      border-color: #f5d98f;
      background: #fffbeb;
    }}
    ul {{
      margin: 8px 0 0 18px;
      padding: 0;
    }}
    @media print {{
      body {{ background: #ffffff; }}
      .page {{ max-width: none; margin: 0; padding: 0; }}
      .panel {{ border-radius: 0; }}
      .data-table thead th {{ position: static; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    {risk_banner_html}
    <section class="panel title-block">
      <h1>{_escape(title)}</h1>
      <div class="meta-grid">
        <div><strong>Generated:</strong> {_escape(completed_at)}</div>
        <div><strong>Pages analyzed:</strong> {_escape(_format_pages(pages))}</div>
        <div><strong>Extraction model:</strong> {_escape(model)}</div>
        <div><strong>Total extraction cost:</strong> {_escape(_format_money(total_cost, 2))}</div>
      </div>
    </section>

    {data_warning_html}

    <section class="panel">
      <h2>Findings Summary</h2>
      <div class="badge-row">{summary_banners}</div>
      {_render_findings_table(findings_rows)}
    </section>

    <section class="panel">
      <h2>Structure Schedule</h2>
      {"".join(structure_sections) if structure_sections else '<p class="empty">No structure graph data loaded.</p>'}
    </section>

    <section class="panel">
      <h2>Pipe Schedule</h2>
      {"".join(pipe_sections) if pipe_sections else '<p class="empty">No pipe graph data loaded.</p>'}
    </section>

    <section class="panel">
      <h2>Extraction Quality</h2>
      <p>
        <strong>Grade { _escape(quality_grade) }</strong><br>
        {_escape(str(total_tiles))} tiles processed, {_escape(str(ok_tiles))} ok, {_escape(str(sanitized_tiles))} sanitized, {_escape(str(skipped_tiles))} skipped
      </p>
      {quality_warning_list}
      {_render_batch_results_table(batch_summary)}
    </section>
  </div>
</body>
</html>
"""
    return html_content


def write_html_report(
    *,
    graphs_dir: Path,
    findings_dir: Path,
    prefix: str,
    out_path: Path,
    batch_summary_path: Path | None = None,
    title: str = "Plan Review Report",
) -> Path:
    """Render and write report HTML to disk."""
    html_content = render_html_report(
        graphs_dir=graphs_dir,
        findings_dir=findings_dir,
        prefix=prefix,
        batch_summary_path=batch_summary_path,
        title=title,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_content, encoding="utf-8")
    return out_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate HTML review report from graph/findings JSON artifacts.")
    parser.add_argument("--graphs-dir", type=Path, required=True, help="Directory with graph JSON files.")
    parser.add_argument("--findings-dir", type=Path, required=True, help="Directory with findings JSON files.")
    parser.add_argument("--prefix", type=str, required=True, help="Artifact file prefix, e.g. calibration-clean.")
    parser.add_argument("--batch-summary", type=Path, default=None, help="Optional path to batch_summary.json.")
    parser.add_argument("--title", type=str, default="Plan Review Report", help="Optional report title.")
    parser.add_argument("--out", type=Path, required=True, help="Output HTML path.")
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    output_path = write_html_report(
        graphs_dir=args.graphs_dir,
        findings_dir=args.findings_dir,
        prefix=args.prefix,
        out_path=args.out,
        batch_summary_path=args.batch_summary,
        title=args.title,
    )
    print(f"Wrote report: {output_path}")


if __name__ == "__main__":
    main()

