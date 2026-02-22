"""Deterministic consistency checks for assembled utility graphs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import networkx as nx

from ..extraction.schemas import TileExtraction
from ..utils.parsing import parse_station


@dataclass(frozen=True)
class Finding:
    """A single graph consistency finding."""

    finding_type: str
    severity: str
    description: str
    source_sheets: list[int]
    source_text_ids: list[int]
    node_ids: list[str]
    edge_ids: list[str]
    expected_value: str | None = None
    actual_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _edge_id(u: str, v: str, data: dict[str, Any]) -> str:
    return str(data.get("edge_id") or f"{u}->{v}")


def _unique_ints(values: list[int]) -> list[int]:
    return sorted({int(v) for v in values})


def _normalize_pipe_size(value: str | None) -> str:
    return str(value or "").upper().replace(" ", "")


def _get_directional_invert(
    node_data: dict[str, Any],
    other_node_data: dict[str, Any],
    pipe_size: str | None = None,
) -> float | None:
    """Find invert facing the other node, optionally matching pipe size first."""
    representative = node_data.get("representative_invert")
    representative_val = float(representative) if isinstance(representative, (int, float)) else None

    inverts = node_data.get("inverts", [])
    if not isinstance(inverts, list) or not inverts:
        return representative_val

    my_station = node_data.get("station_ft")
    other_station = other_node_data.get("station_ft")
    preferred_dirs: set[str] | None = None
    if isinstance(my_station, (int, float)) and isinstance(other_station, (int, float)):
        station_delta = float(other_station) - float(my_station)
        if station_delta > 0.5:
            preferred_dirs = {"E", "NE", "SE"}
        elif station_delta < -0.5:
            preferred_dirs = {"W", "NW", "SW"}

    if preferred_dirs is None:
        my_offset = node_data.get("signed_offset_ft")
        other_offset = other_node_data.get("signed_offset_ft")
        if isinstance(my_offset, (int, float)) and isinstance(other_offset, (int, float)):
            my_dist = abs(float(my_offset))
            other_dist = abs(float(other_offset))
            if other_dist > my_dist:
                preferred_dirs = {"N", "NE", "NW"}
            elif other_dist < my_dist:
                preferred_dirs = {"S", "SE", "SW"}

    norm_size = _normalize_pipe_size(pipe_size) if pipe_size else ""

    if preferred_dirs and norm_size:
        for invert in inverts:
            try:
                direction = str(invert.get("direction", "")).upper()
                inv_size = _normalize_pipe_size(invert.get("pipe_size"))
                if direction in preferred_dirs and inv_size == norm_size:
                    return float(invert["elevation"])
            except Exception:
                continue

    if preferred_dirs:
        for invert in inverts:
            try:
                direction = str(invert.get("direction", "")).upper()
                if direction in preferred_dirs:
                    return float(invert["elevation"])
            except Exception:
                continue

    return representative_val


def check_slope_consistency(graph: nx.DiGraph, tolerance: float = 0.0002) -> list[Finding]:
    """Compare labeled slope against slope derived from endpoint representative inverts."""
    findings: list[Finding] = []
    for u, v, data in graph.edges(data=True):
        length = data.get("length_lf")
        labeled_slope = data.get("slope")
        if not isinstance(length, (int, float)) or length <= 0:
            continue
        if not isinstance(labeled_slope, (int, float)):
            continue

        pipe_size = data.get("size")
        upstream = _get_directional_invert(graph.nodes[u], graph.nodes[v], pipe_size)
        downstream = _get_directional_invert(graph.nodes[v], graph.nodes[u], pipe_size)
        if not isinstance(upstream, (int, float)) or not isinstance(downstream, (int, float)):
            continue

        calculated = abs(float(upstream) - float(downstream)) / float(length)
        if abs(calculated - float(labeled_slope)) <= tolerance:
            continue

        source_text_ids = _unique_ints(
            list(data.get("source_text_ids", []))
            + list(graph.nodes[u].get("source_text_ids", []))
            + list(graph.nodes[v].get("source_text_ids", []))
        )
        source_sheets = _unique_ints(
            list(data.get("source_page_numbers", []))
            + list(graph.nodes[u].get("source_page_numbers", []))
            + list(graph.nodes[v].get("source_page_numbers", []))
        )
        findings.append(
            Finding(
                finding_type="slope_mismatch",
                severity="warning",
                description=(
                    f"Slope mismatch on edge {_edge_id(u, v, data)}: labeled {float(labeled_slope):.4f}, "
                    f"calculated {calculated:.4f}."
                ),
                source_sheets=source_sheets,
                source_text_ids=source_text_ids,
                node_ids=[u, v],
                edge_ids=[_edge_id(u, v, data)],
                expected_value=f"{float(labeled_slope):.4f}",
                actual_value=f"{calculated:.4f}",
            )
        )
    return findings


def check_pipe_size_consistency(
    sd_plan_extractions: list[TileExtraction],
    sd_profile_extractions: list[TileExtraction],
) -> list[Finding]:
    """Compare pipe sizes across plan/profile extraction sets using station or length/slope signatures."""
    findings: list[Finding] = []
    buckets: dict[tuple[Any, ...], list[tuple[str, int]]] = {}

    def add_pipes(extractions: list[TileExtraction], source: str) -> None:
        for extraction in extractions:
            for pipe in extraction.pipes:
                from_station = parse_station(pipe.from_station or "") if pipe.from_station else None
                to_station = parse_station(pipe.to_station or "") if pipe.to_station else None
                if from_station is not None and to_station is not None:
                    key = (
                        pipe.pipe_type.upper(),
                        round(min(from_station, to_station), 2),
                        round(max(from_station, to_station), 2),
                    )
                else:
                    key = (
                        pipe.pipe_type.upper(),
                        round(float(pipe.length_lf or 0.0), 1),
                        round(float(pipe.slope or 0.0), 4),
                    )
                buckets.setdefault(key, []).append((pipe.size, extraction.page_number))

    add_pipes(sd_plan_extractions, "plan")
    add_pipes(sd_profile_extractions, "profile")

    for key, rows in buckets.items():
        sizes = {size for size, _ in rows if size}
        if len(sizes) <= 1:
            continue
        findings.append(
            Finding(
                finding_type="size_inconsistency",
                severity="warning",
                description=f"Pipe size mismatch for segment key {key}: {sorted(sizes)}.",
                source_sheets=_unique_ints([page for _, page in rows]),
                source_text_ids=[],
                node_ids=[],
                edge_ids=[],
                expected_value=None,
                actual_value=", ".join(sorted(sizes)),
            )
        )
    return findings


def check_elevation_consistency(
    graph: nx.DiGraph,
    all_extractions: list[TileExtraction],  # noqa: ARG001 - reserved for future cross-sheet checks
    rim_tolerance: float = 0.10,
) -> list[Finding]:
    """Flag merged structures that carry conflicting rim elevations across duplicates."""
    findings: list[Finding] = []
    for node_id, data in graph.nodes(data=True):
        if data.get("kind") != "structure":
            continue
        rims = [float(v) for v in data.get("rim_elevation_values", []) if isinstance(v, (int, float))]
        if len(rims) <= 1:
            continue
        if (max(rims) - min(rims)) <= rim_tolerance:
            continue

        findings.append(
            Finding(
                finding_type="elevation_mismatch",
                severity="warning",
                description=(
                    f"Rim elevation mismatch at node {node_id}: min={min(rims):.2f}, max={max(rims):.2f}."
                ),
                source_sheets=_unique_ints(list(data.get("source_page_numbers", []))),
                source_text_ids=_unique_ints(list(data.get("source_text_ids", []))),
                node_ids=[node_id],
                edge_ids=[],
                expected_value=f"{min(rims):.2f}-{max(rims):.2f}",
                actual_value=f"{rims}",
            )
        )
    return findings


def check_connectivity(graph: nx.DiGraph) -> list[Finding]:
    """Flag isolated structures and unresolved/orphan pipe connections."""
    findings: list[Finding] = []
    quality = graph.graph.get("quality_summary", {}) or {}
    total_tiles = int(quality.get("total_tiles") or 0)
    bad_tiles = int(quality.get("sanitized_tiles") or 0) + int(quality.get("skipped_tiles") or 0)
    degraded_quality = total_tiles > 0 and (bad_tiles / total_tiles) > 0.30

    structure_nodes = [
        (node_id, data)
        for node_id, data in graph.nodes(data=True)
        if data.get("kind") == "structure"
    ]

    if not structure_nodes and graph.number_of_edges() > 0:
        findings.append(
            Finding(
                finding_type="connectivity_unverifiable",
                severity="info",
                description=(
                    "Connectivity checks are limited: graph has pipe edges but no resolved structure nodes."
                ),
                source_sheets=[],
                source_text_ids=[],
                node_ids=[],
                edge_ids=[],
            )
        )
        return findings

    suppressed_orphans = 0
    for node_id, data in structure_nodes:
        if graph.degree(node_id) != 0:
            continue
        if degraded_quality:
            suppressed_orphans += 1
            continue
        findings.append(
            Finding(
                finding_type="orphan_node",
                severity="warning",
                description=f"Structure node {node_id} is not connected to any pipe.",
                source_sheets=_unique_ints(list(data.get("source_page_numbers", []))),
                source_text_ids=_unique_ints(list(data.get("source_text_ids", []))),
                node_ids=[node_id],
                edge_ids=[],
            )
        )

    if suppressed_orphans:
        findings.append(
            Finding(
                finding_type="orphan_node_check_suppressed",
                severity="info",
                description=(
                    f"Suppressed {suppressed_orphans} orphan-node warnings due to low extraction quality."
                ),
                source_sheets=[],
                source_text_ids=[],
                node_ids=[],
                edge_ids=[],
            )
        )

    for u, v, data in graph.edges(data=True):
        unresolved = (
            data.get("matched_confidence") == "none"
            or graph.nodes[u].get("kind") == "orphan_anchor"
            or graph.nodes[v].get("kind") == "orphan_anchor"
        )
        if not unresolved:
            continue
        has_anchor_data = any(
            data.get(field)
            for field in ("from_station", "to_station", "from_structure_hint", "to_structure_hint")
        )
        if not has_anchor_data:
            findings.append(
                Finding(
                    finding_type="unanchored_pipe",
                    severity="info",
                    description=(
                        f"Pipe {_edge_id(u, v, data)} has no station/hint endpoint metadata; "
                        "connection could not be verified."
                    ),
                    source_sheets=_unique_ints(list(data.get("source_page_numbers", []))),
                    source_text_ids=_unique_ints(list(data.get("source_text_ids", []))),
                    node_ids=[u, v],
                    edge_ids=[_edge_id(u, v, data)],
                )
            )
            continue
        findings.append(
            Finding(
                finding_type="dead_end_pipe",
                severity="warning",
                description=f"Pipe {_edge_id(u, v, data)} has unresolved endpoint matching.",
                source_sheets=_unique_ints(list(data.get("source_page_numbers", []))),
                source_text_ids=_unique_ints(list(data.get("source_text_ids", []))),
                node_ids=[u, v],
                edge_ids=[_edge_id(u, v, data)],
            )
        )
    return findings


def check_flow_direction(graph: nx.DiGraph, invert_tolerance: float = 0.01) -> list[Finding]:
    """For SD/SS gravity systems, flag edges where downstream invert is above upstream invert."""
    utility = str(graph.graph.get("utility_type", "")).upper()
    if utility not in {"SD", "SS"}:
        return []

    findings: list[Finding] = []
    for u, v, data in graph.edges(data=True):
        if graph.nodes[u].get("kind") == "orphan_anchor" or graph.nodes[v].get("kind") == "orphan_anchor":
            continue
        pipe_size = data.get("size")
        upstream = _get_directional_invert(graph.nodes[u], graph.nodes[v], pipe_size)
        downstream = _get_directional_invert(graph.nodes[v], graph.nodes[u], pipe_size)
        if not isinstance(upstream, (int, float)) or not isinstance(downstream, (int, float)):
            continue
        if float(downstream) <= float(upstream) + invert_tolerance:
            continue
        findings.append(
            Finding(
                finding_type="flow_direction_error",
                severity="error",
                description=(
                    f"Backfall on edge {_edge_id(u, v, data)}: upstream={float(upstream):.2f}, "
                    f"downstream={float(downstream):.2f}."
                ),
                source_sheets=_unique_ints(list(data.get("source_page_numbers", []))),
                source_text_ids=_unique_ints(
                    list(data.get("source_text_ids", []))
                    + list(graph.nodes[u].get("source_text_ids", []))
                    + list(graph.nodes[v].get("source_text_ids", []))
                ),
                node_ids=[u, v],
                edge_ids=[_edge_id(u, v, data)],
                expected_value=f"downstream <= {float(upstream):.2f}",
                actual_value=f"{float(downstream):.2f}",
            )
        )
    return findings


def run_all_checks(graph: nx.DiGraph) -> list[Finding]:
    """Run all graph-level checks with deterministic ordering."""
    findings: list[Finding] = []
    findings.extend(check_connectivity(graph))
    findings.extend(check_flow_direction(graph))
    findings.extend(check_slope_consistency(graph))
    findings.extend(check_elevation_consistency(graph, all_extractions=[]))
    return findings
