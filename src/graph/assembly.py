"""Assemble utility graphs from tile-level extraction outputs."""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

import networkx as nx

from ..extraction.schemas import Pipe, TileExtraction
from ..utils.parsing import parse_signed_offset, parse_station
from .merge import MergedStructure, merge_structures

logger = logging.getLogger(__name__)

_TILE_JSON_RE = re.compile(r"^p\d+_r\d+_c\d+\.json$")
_TILE_META_RE = re.compile(r"^p\d+_r\d+_c\d+\.json\.meta\.json$")

_CONFIDENCE_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
_REFERENCE_SHEET_TYPES: frozenset[str] = frozenset({"signing_striping"})
_CROWN_SPREAD_BUFFER_FT = 0.5
_CROWN_RATIO_THRESHOLD = 10.0
_HINT_CLOSE_FT = 5.0
_HINT_MEDIUM_FT = 10.0
_HINT_FAR_FT = 25.0
_INFER_ENDPOINT_HIGH_FT = 2.0
_INFER_ENDPOINT_MEDIUM_FT = 10.0
_INFER_ENDPOINT_LOW_FT = 30.0
_QUALITY_GRADE_A_MAX_BAD_RATIO = 0.10
_QUALITY_GRADE_B_MAX_BAD_RATIO = 0.30
_QUALITY_GRADE_C_MAX_BAD_RATIO = 0.50
_QUALITY_WARNING_BAD_RATIO = 0.30


def load_extractions_with_meta(
    extractions_dir: Path,
) -> tuple[list[TileExtraction], dict[str, dict[str, Any]]]:
    """Load tile extraction JSON files and matching meta payloads from a directory."""
    extractions: list[TileExtraction] = []
    tile_meta_by_id: dict[str, dict[str, Any]] = {}

    for path in sorted(extractions_dir.iterdir()):
        if not path.is_file():
            continue
        if _TILE_JSON_RE.match(path.name):
            payload = json.loads(path.read_text(encoding="utf-8"))
            extractions.append(TileExtraction.model_validate(payload))
        elif _TILE_META_RE.match(path.name):
            payload = json.loads(path.read_text(encoding="utf-8"))
            tile_id = str(payload.get("tile_id") or path.name.replace(".json.meta.json", ""))
            tile_meta_by_id[tile_id] = payload

    return extractions, tile_meta_by_id


def _quality_grade(bad_ratio: float) -> str:
    """Map extraction bad-ratio to quality grade."""
    if bad_ratio <= _QUALITY_GRADE_A_MAX_BAD_RATIO:
        return "A"
    if bad_ratio <= _QUALITY_GRADE_B_MAX_BAD_RATIO:
        return "B"
    if bad_ratio <= _QUALITY_GRADE_C_MAX_BAD_RATIO:
        return "C"
    return "D"


def _reference_only_pages(extractions: list[TileExtraction]) -> set[int]:
    """Infer pages that should be treated as reference-only utility context."""
    page_sheet_types: dict[int, set[str]] = {}
    for extraction in extractions:
        sheet_type = str(extraction.sheet_type or "").strip().lower()
        if not sheet_type:
            continue
        page_sheet_types.setdefault(extraction.page_number, set()).add(sheet_type)

    reference_pages: set[int] = set()
    for page_number, types in page_sheet_types.items():
        if "signing_striping" in types and "profile_view" not in types:
            reference_pages.add(page_number)
    return reference_pages


def build_quality_summary(
    *,
    extractions: list[TileExtraction],
    tile_meta_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build extraction quality summary from meta files and extraction records."""
    tile_ids = {extraction.tile_id for extraction in extractions} | set(tile_meta_by_id.keys())
    total_tiles = len(tile_ids)
    if total_tiles == 0:
        return {
            "total_tiles": 0,
            "ok_tiles": 0,
            "sanitized_tiles": 0,
            "skipped_tiles": 0,
            "quality_grade": "D",
            "warnings": ["No extraction tiles were loaded."],
        }

    ok_tiles = 0
    sanitized_tiles = 0
    skipped_tiles = 0
    for tile_id in tile_ids:
        meta = tile_meta_by_id.get(tile_id, {})
        status = str(meta.get("status", "ok"))
        if status == "ok":
            ok_tiles += 1
            if bool(meta.get("sanitized", False)):
                sanitized_tiles += 1
        elif status == "skipped_low_coherence":
            skipped_tiles += 1

    bad_ratio = (sanitized_tiles + skipped_tiles) / total_tiles
    warnings: list[str] = []
    if sanitized_tiles:
        warnings.append(f"{sanitized_tiles} tiles had sanitizer recovery")
    if skipped_tiles:
        warnings.append(f"{skipped_tiles} tiles skipped (low coherence)")
    if bad_ratio > _QUALITY_WARNING_BAD_RATIO:
        warnings.append("Extraction quality below threshold — findings may be incomplete")

    return {
        "total_tiles": total_tiles,
        "ok_tiles": ok_tiles,
        "sanitized_tiles": sanitized_tiles,
        "skipped_tiles": skipped_tiles,
        "quality_grade": _quality_grade(bad_ratio),
        "warnings": warnings,
    }


def _node_representative_invert(inverts: list[dict[str, Any]]) -> float | None:
    """Return the minimum invert elevation used as representative node invert."""
    elevations: list[float] = []
    for invert in inverts:
        value = invert.get("elevation")
        if isinstance(value, (int, float)):
            elevations.append(float(value))
    if not elevations:
        return None
    return min(elevations)


def _parse_pipe_diameter_ft(size_str: str | None) -> float | None:
    """Parse pipe size string like '18"' or '36"' to diameter in feet."""
    if not size_str:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(size_str))
    if not match:
        return None
    inches = float(match.group(1))
    return inches / 12.0


def _filter_suspect_crowns(graph: nx.DiGraph) -> None:
    """Filter likely crown elevations from gravity-system node inverts."""
    utility = str(graph.graph.get("utility_type", "")).upper()
    if utility not in {"SD", "SS"}:
        return

    # Pass 1: multi-invert spread check on each structure node.
    for node_id, node_data in graph.nodes(data=True):
        if node_data.get("kind") != "structure":
            continue
        inverts = node_data.get("inverts")
        if not isinstance(inverts, list) or len(inverts) < 2:
            continue

        elevations: list[float] = []
        diameters: list[float] = []
        for invert in inverts:
            if not isinstance(invert, dict):
                continue
            elevation = invert.get("elevation")
            if isinstance(elevation, (int, float)):
                elevations.append(float(elevation))
            diameter_ft = _parse_pipe_diameter_ft(str(invert.get("pipe_size") or ""))
            if diameter_ft is not None:
                diameters.append(diameter_ft)

        if len(elevations) < 2 or not diameters:
            continue

        min_elev = min(elevations)
        max_elev = max(elevations)
        max_pipe_diameter_ft = max(diameters)
        spread_threshold = max_pipe_diameter_ft + _CROWN_SPREAD_BUFFER_FT
        if (max_elev - min_elev) <= spread_threshold:
            continue

        suspect_cutoff = min_elev + spread_threshold
        crown_suspects: list[dict[str, Any]] = []
        keep_inverts: list[dict[str, Any]] = []
        for invert in inverts:
            if not isinstance(invert, dict):
                continue
            elevation = invert.get("elevation")
            if isinstance(elevation, (int, float)) and float(elevation) > suspect_cutoff:
                crown_suspects.append(dict(invert))
            else:
                keep_inverts.append(dict(invert))

        if not crown_suspects:
            continue

        existing = node_data.get("crown_suspects")
        merged_suspects: list[dict[str, Any]] = []
        if isinstance(existing, list):
            for row in existing:
                if isinstance(row, dict):
                    merged_suspects.append(dict(row))
        merged_suspects.extend(crown_suspects)

        node_data["crown_suspects"] = merged_suspects
        node_data["inverts"] = keep_inverts
        node_data["representative_invert"] = _node_representative_invert(keep_inverts)
        logger.debug(
            "Filtered crown suspects at node %s: removed=%s kept=%s",
            node_id,
            len(crown_suspects),
            len(keep_inverts),
        )

    # Pass 2: cross-edge drop comparison to flag likely crown contamination.
    for u, v, edge_data in graph.edges(data=True):
        labeled_slope = edge_data.get("slope")
        length_lf = edge_data.get("length_lf")
        if not isinstance(labeled_slope, (int, float)):
            continue
        if not isinstance(length_lf, (int, float)) or float(length_lf) <= 0:
            continue
        if graph.nodes[u].get("kind") != "structure" or graph.nodes[v].get("kind") != "structure":
            continue

        expected_drop = abs(float(labeled_slope)) * float(length_lf)
        from_inv = graph.nodes[u].get("representative_invert")
        to_inv = graph.nodes[v].get("representative_invert")
        if not isinstance(from_inv, (int, float)) or not isinstance(to_inv, (int, float)):
            continue

        actual_drop = abs(float(from_inv) - float(to_inv))
        if actual_drop > (expected_drop * _CROWN_RATIO_THRESHOLD) and actual_drop > 2.0:
            edge_data["crown_contamination_candidate"] = True
            suspect_node_id = u if float(from_inv) >= float(to_inv) else v
            graph.nodes[suspect_node_id]["suspect_crown"] = True
            logger.debug(
                "Flagged crown contamination candidate on edge %s->%s (expected_drop=%.4f actual_drop=%.4f)",
                u,
                v,
                expected_drop,
                actual_drop,
            )


def _pipe_matches_utility(pipe: Pipe, utility_type: str) -> bool:
    """Return whether a pipe belongs to the utility being assembled."""
    return pipe.pipe_type.upper().strip() == utility_type.upper().strip()


def _hint_score(node_data: dict[str, Any], hint: str | None) -> int:
    """Return hint-text affinity score between a candidate node and endpoint hint."""
    if not hint:
        return 0
    hint_norm = re.sub(r"\s+", " ", hint.upper()).strip()
    if not hint_norm:
        return 0

    fields = [
        str(node_data.get("id") or ""),
        str(node_data.get("structure_type") or ""),
        str(node_data.get("notes") or ""),
    ]
    blob = " | ".join(field.upper() for field in fields)
    if hint_norm and hint_norm in blob:
        return 2

    hint_tokens = {tok for tok in re.split(r"[^A-Z0-9]+", hint_norm) if tok}
    if not hint_tokens:
        return 0
    if any(token in blob for token in hint_tokens):
        return 1
    return 0


def _pick_match_confidence(*, distance: float | None, hint_score: int) -> str:
    """Convert hint quality and station distance into a match confidence label."""
    if distance is None:
        return "medium" if hint_score >= 1 else "none"
    if hint_score >= 2 and distance <= _HINT_CLOSE_FT:
        return "high"
    if hint_score >= 1 and distance <= _HINT_MEDIUM_FT:
        return "medium"
    if distance <= _HINT_CLOSE_FT:
        return "medium"
    if distance <= _HINT_FAR_FT:
        return "low"
    return "none"


def _best_node_match(
    *,
    candidates: list[tuple[str, dict[str, Any]]],
    station_ft: float | None,
    hint: str | None,
    exclude_node_id: str | None = None,
) -> tuple[str | None, str]:
    """Pick the best structure candidate for one endpoint and confidence grade."""
    ranked: list[tuple[int, float, str]] = []
    for node_id, node_data in candidates:
        if exclude_node_id and node_id == exclude_node_id:
            continue
        hint_score = _hint_score(node_data, hint)
        node_station = node_data.get("station_ft")
        distance: float | None = None
        if isinstance(node_station, (float, int)) and station_ft is not None:
            distance = abs(float(node_station) - station_ft)
        dist_val = float("inf") if distance is None else float(distance)
        ranked.append((-hint_score, dist_val, node_id))

    if not ranked:
        return None, "none"
    ranked.sort()
    _, best_dist, best_node_id = ranked[0]
    best_node_data = dict(candidates)[best_node_id]
    best_hint_score = _hint_score(best_node_data, hint)
    confidence = _pick_match_confidence(
        distance=None if best_dist == float("inf") else best_dist,
        hint_score=best_hint_score,
    )
    return best_node_id, confidence


def _worse_confidence(a: str, b: str) -> str:
    """Return the lower of two confidence labels."""
    return a if _CONFIDENCE_ORDER[a] <= _CONFIDENCE_ORDER[b] else b


def _confidence_from_station_delta(delta: float) -> str:
    """Map station-distance residual to endpoint inference confidence."""
    if delta <= _INFER_ENDPOINT_HIGH_FT:
        return "high"
    if delta <= _INFER_ENDPOINT_MEDIUM_FT:
        return "medium"
    if delta <= _INFER_ENDPOINT_LOW_FT:
        return "low"
    return "none"


def _infer_other_endpoint_from_length(
    *,
    candidates: list[tuple[str, dict[str, Any]]],
    anchor_node_id: str,
    length_lf: float | None,
) -> tuple[str | None, str]:
    """Infer missing opposite endpoint by projecting pipe length from anchor station."""
    if not isinstance(length_lf, (int, float)) or length_lf <= 0:
        return None, "none"
    candidate_map = dict(candidates)
    anchor_data = candidate_map.get(anchor_node_id, {})
    anchor_station = anchor_data.get("station_ft")
    if not isinstance(anchor_station, (int, float)):
        return None, "none"

    best_node_id: str | None = None
    best_delta = float("inf")
    for node_id, node_data in candidates:
        if node_id == anchor_node_id:
            continue
        station_ft = node_data.get("station_ft")
        if not isinstance(station_ft, (int, float)):
            continue
        delta = min(
            abs(float(station_ft) - (float(anchor_station) + float(length_lf))),
            abs(float(station_ft) - (float(anchor_station) - float(length_lf))),
        )
        if delta < best_delta:
            best_delta = delta
            best_node_id = node_id

    if best_node_id is None:
        return None, "none"
    confidence = _confidence_from_station_delta(best_delta)
    if confidence in {"high", "medium"}:
        confidence = "low"
    if confidence == "none":
        return None, "none"
    return best_node_id, confidence


def _ensure_orphan_anchor(
    graph: nx.DiGraph,
    *,
    utility_type: str,
    pipe_edge_id: str,
    side: str,
    page_number: int,
) -> str:
    node_id = f"orphan:{utility_type}:{pipe_edge_id}:{side}"
    if not graph.has_node(node_id):
        graph.add_node(
            node_id,
            kind="orphan_anchor",
            utility_type=utility_type,
            page_number=page_number,
            structure_type="ORPHAN",
            source_tile_ids=[],
            source_page_numbers=[page_number],
            source_text_ids=[],
            sanitized=False,
            station_ft=None,
            signed_offset_ft=None,
            representative_invert=None,
        )
    return node_id


def _edge_signature(data: dict[str, Any]) -> tuple[str, float | None, float | None]:
    size = str(data.get("size") or "").upper().replace(" ", "")
    length = data.get("length_lf")
    slope = data.get("slope")
    length_val = float(length) if isinstance(length, (int, float)) else None
    slope_val = float(slope) if isinstance(slope, (int, float)) else None
    return size, length_val, slope_val


def _edges_are_similar(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    length_tol: float = 2.0,
    slope_tol: float = 0.0010,
) -> bool:
    size_a, length_a, slope_a = _edge_signature(a)
    size_b, length_b, slope_b = _edge_signature(b)
    if size_a != size_b:
        return False

    if length_a is not None and length_b is not None and abs(length_a - length_b) > length_tol:
        return False
    if slope_a is not None and slope_b is not None and abs(slope_a - slope_b) > slope_tol:
        return False
    return True


def _edge_rank(data: dict[str, Any]) -> tuple[int, int, int, int, int]:
    conf = _CONFIDENCE_ORDER.get(str(data.get("matched_confidence", "none")), 0)
    from_conf = _CONFIDENCE_ORDER.get(str(data.get("from_match_confidence", "none")), 0)
    to_conf = _CONFIDENCE_ORDER.get(str(data.get("to_match_confidence", "none")), 0)
    station_fields = int(bool(data.get("from_station"))) + int(bool(data.get("to_station")))
    notes_len = len(str(data.get("notes") or ""))
    return conf, from_conf + to_conf, station_fields, notes_len, len(data.get("source_text_ids", []))


def _merge_edge_provenance(kept: dict[str, Any], dropped: dict[str, Any]) -> None:
    kept["source_tile_ids"] = sorted(
        {str(v) for v in list(kept.get("source_tile_ids", [])) + list(dropped.get("source_tile_ids", []))}
    )
    kept["source_page_numbers"] = sorted(
        {int(v) for v in list(kept.get("source_page_numbers", [])) + list(dropped.get("source_page_numbers", []))}
    )
    kept["source_text_ids"] = sorted(
        {int(v) for v in list(kept.get("source_text_ids", [])) + list(dropped.get("source_text_ids", []))}
    )
    kept["sanitized"] = bool(kept.get("sanitized", False) or dropped.get("sanitized", False))
    kept["crown_contamination_candidate"] = bool(
        kept.get("crown_contamination_candidate", False)
        or dropped.get("crown_contamination_candidate", False)
    )
    kept["is_reference_only"] = bool(
        kept.get("is_reference_only", False)
        or dropped.get("is_reference_only", False)
    )
    for field in ("from_station", "to_station", "from_structure_hint", "to_structure_hint", "notes"):
        if not kept.get(field) and dropped.get(field):
            kept[field] = dropped[field]


def _deduplicate_pipe_edges(graph: nx.DiGraph) -> None:
    """Remove duplicate pipe edges between same node pair and merge provenance."""
    grouped_by_pair: dict[tuple[str, str], list[tuple[str, str, dict[str, Any]]]] = {}
    for u, v, data in graph.edges(data=True):
        pair = tuple(sorted([u, v]))
        grouped_by_pair.setdefault(pair, []).append((u, v, dict(data)))

    for pair_edges in grouped_by_pair.values():
        if len(pair_edges) <= 1:
            continue
        consumed = set()
        for i, (u_i, v_i, d_i) in enumerate(pair_edges):
            if i in consumed:
                continue
            dup_group: list[tuple[str, str, dict[str, Any], int]] = [(u_i, v_i, d_i, i)]
            for j in range(i + 1, len(pair_edges)):
                if j in consumed:
                    continue
                u_j, v_j, d_j = pair_edges[j]
                if _edges_are_similar(d_i, d_j):
                    dup_group.append((u_j, v_j, d_j, j))
                    consumed.add(j)

            if len(dup_group) <= 1:
                continue

            best = max(dup_group, key=lambda row: _edge_rank(row[2]))
            best_u, best_v, best_data, _ = best
            for u, v, data, idx in dup_group:
                if idx == best[3]:
                    continue
                if graph.has_edge(u, v):
                    current = graph.get_edge_data(u, v) or {}
                    if current.get("edge_id") == data.get("edge_id"):
                        _merge_edge_provenance(best_data, current)
                        graph.remove_edge(u, v)

            if graph.has_edge(best_u, best_v):
                graph[best_u][best_v].update(best_data)


def _orient_gravity_edges(graph: nx.DiGraph, invert_tolerance: float = 0.01) -> None:
    """For SD/SS, orient edges from higher invert to lower invert."""
    utility = str(graph.graph.get("utility_type", "")).upper()
    if utility not in {"SD", "SS"}:
        return

    edges_to_flip: list[tuple[str, str, dict[str, Any]]] = []
    for u, v, data in graph.edges(data=True):
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        if u_data.get("kind") != "structure" or v_data.get("kind") != "structure":
            continue
        u_inv = u_data.get("representative_invert")
        v_inv = v_data.get("representative_invert")
        if not isinstance(u_inv, (int, float)) or not isinstance(v_inv, (int, float)):
            continue
        if float(u_inv) < float(v_inv) - invert_tolerance:
            edges_to_flip.append((u, v, dict(data)))

    for u, v, data in edges_to_flip:
        if not graph.has_edge(u, v):
            continue
        graph.remove_edge(u, v)
        data["from_station"], data["to_station"] = data.get("to_station"), data.get("from_station")
        data["from_structure_hint"], data["to_structure_hint"] = (
            data.get("to_structure_hint"),
            data.get("from_structure_hint"),
        )
        data["from_match_confidence"], data["to_match_confidence"] = (
            data.get("to_match_confidence"),
            data.get("from_match_confidence"),
        )
        data["original_from_node"] = data.get("original_from_node", u)
        data["original_to_node"] = data.get("original_to_node", v)
        data["oriented_by_gravity"] = True
        graph.add_edge(v, u, **data)


def build_utility_graph(
    *,
    extractions: list[TileExtraction],
    utility_type: str,
    tile_meta_by_id: dict[str, dict[str, Any]] | None = None,
) -> nx.DiGraph:
    """
    Build a directed graph for one utility type from tile extraction records.

    Nodes are merged structures. Edges are pipes, including orphan pipes when no
    structure endpoint can be matched.
    """
    utility = utility_type.upper().strip()
    graph = nx.DiGraph(utility_type=utility)
    reference_pages = _reference_only_pages(extractions)

    merged_nodes = merge_structures(
        extractions=extractions,
        utility_type=utility,
        tile_meta_by_id=tile_meta_by_id or {},
    )
    for item in merged_nodes:
        graph.add_node(
            item.node_id,
            kind="structure",
            id=item.id,
            page_number=item.page_number,
            structure_type=item.structure_type,
            size=item.size,
            station=item.station,
            offset=item.offset,
            station_ft=item.parsed_station,
            signed_offset_ft=item.signed_offset,
            rim_elevation=item.rim_elevation,
            tc_elevation=item.tc_elevation,
            fl_elevation=item.fl_elevation,
            inverts=item.inverts,
            is_existing=item.is_existing,
            representative_invert=_node_representative_invert(item.inverts),
            notes=item.notes,
            source_tile_ids=item.source_tile_ids,
            source_page_numbers=item.source_page_numbers,
            source_text_ids=item.source_text_ids,
            sanitized=item.sanitized,
            variants_count=item.variants_count,
            rim_elevation_values=item.rim_elevation_values,
        )

    node_candidates_by_page: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    all_candidates: list[tuple[str, dict[str, Any]]] = []
    for node_id, data in graph.nodes(data=True):
        if data.get("kind") != "structure":
            continue
        page = int(data.get("page_number", 0))
        node_candidates_by_page.setdefault(page, []).append((node_id, dict(data)))
        all_candidates.append((node_id, dict(data)))

    edge_counter = 0
    for extraction in extractions:
        extraction_sheet_type = str(extraction.sheet_type or "").strip().lower()
        tile_meta = (tile_meta_by_id or {}).get(extraction.tile_id, {})
        tile_sanitized = bool(tile_meta.get("sanitized", False))
        is_reference_tile = (
            extraction_sheet_type in _REFERENCE_SHEET_TYPES
            or extraction.page_number in reference_pages
        )
        for pipe in extraction.pipes:
            if not _pipe_matches_utility(pipe, utility):
                continue

            edge_id = f"{utility}:e{edge_counter}:{extraction.tile_id}"
            edge_counter += 1

            from_station_ft = parse_station(pipe.from_station or "") if pipe.from_station else None
            to_station_ft = parse_station(pipe.to_station or "") if pipe.to_station else None
            if from_station_ft is None:
                from_station_ft = parse_station(pipe.from_structure_hint or "")
            if to_station_ft is None:
                to_station_ft = parse_station(pipe.to_structure_hint or "")

            candidates = node_candidates_by_page.get(extraction.page_number) or all_candidates
            can_match_from = bool(pipe.from_structure_hint) or (from_station_ft is not None)
            can_match_to = bool(pipe.to_structure_hint) or (to_station_ft is not None)

            if can_match_from:
                from_node, from_conf = _best_node_match(
                    candidates=candidates,
                    station_ft=from_station_ft,
                    hint=pipe.from_structure_hint,
                )
            else:
                from_node, from_conf = None, "none"

            if can_match_to:
                to_node, to_conf = _best_node_match(
                    candidates=candidates,
                    station_ft=to_station_ft,
                    hint=pipe.to_structure_hint,
                    exclude_node_id=from_node,
                )
            else:
                to_node, to_conf = None, "none"

            if from_node is not None and to_node is None:
                inferred_node, inferred_conf = _infer_other_endpoint_from_length(
                    candidates=candidates,
                    anchor_node_id=from_node,
                    length_lf=pipe.length_lf,
                )
                if inferred_node is not None:
                    to_node = inferred_node
                    to_conf = inferred_conf

            if to_node is not None and from_node is None:
                inferred_node, inferred_conf = _infer_other_endpoint_from_length(
                    candidates=candidates,
                    anchor_node_id=to_node,
                    length_lf=pipe.length_lf,
                )
                if inferred_node is not None:
                    from_node = inferred_node
                    from_conf = inferred_conf

            if from_node is None:
                from_node = _ensure_orphan_anchor(
                    graph,
                    utility_type=utility,
                    pipe_edge_id=edge_id,
                    side="from",
                    page_number=extraction.page_number,
                )
                from_conf = "none"
            if to_node is None:
                to_node = _ensure_orphan_anchor(
                    graph,
                    utility_type=utility,
                    pipe_edge_id=edge_id,
                    side="to",
                    page_number=extraction.page_number,
                )
                to_conf = "none"

            graph.add_edge(
                from_node,
                to_node,
                edge_id=edge_id,
                utility_type=utility,
                pipe_type=pipe.pipe_type,
                size=pipe.size,
                material=pipe.material,
                length_lf=pipe.length_lf,
                slope=pipe.slope,
                from_station=pipe.from_station,
                to_station=pipe.to_station,
                from_structure_hint=pipe.from_structure_hint,
                to_structure_hint=pipe.to_structure_hint,
                notes=pipe.notes,
                matched_confidence=_worse_confidence(from_conf, to_conf),
                from_match_confidence=from_conf,
                to_match_confidence=to_conf,
                source_tile_ids=[extraction.tile_id],
                source_page_numbers=[extraction.page_number],
                source_text_ids=sorted({int(v) for v in pipe.source_text_ids}),
                sanitized=tile_sanitized,
                is_reference_only=is_reference_tile,
            )

    _filter_suspect_crowns(graph)
    _deduplicate_pipe_edges(graph)
    _orient_gravity_edges(graph)

    graph.graph["quality_summary"] = build_quality_summary(
        extractions=extractions,
        tile_meta_by_id=tile_meta_by_id or {},
    )
    return graph


def graph_to_dict(graph: nx.DiGraph) -> dict[str, Any]:
    """Serialize utility graph to a JSON-compatible dict."""
    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        row = {"node_id": node_id}
        row.update(attrs)
        nodes.append(row)

    edges = []
    for from_node, to_node, attrs in graph.edges(data=True):
        row = {"from_node": from_node, "to_node": to_node}
        row.update(attrs)
        edges.append(row)

    return {
        "utility_type": graph.graph.get("utility_type"),
        "quality_summary": graph.graph.get("quality_summary", {}),
        "nodes": nodes,
        "edges": edges,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build utility graph from extraction JSON files.")
    parser.add_argument("--extractions-dir", type=Path, required=True, help="Directory with tile outputs.")
    parser.add_argument(
        "--utility-type",
        type=str,
        required=True,
        choices=["SD", "SS", "W", "sd", "ss", "w"],
        help="Utility graph to build.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output path for graph JSON.")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_arg_parser().parse_args()
    extractions, tile_meta = load_extractions_with_meta(args.extractions_dir)
    graph = build_utility_graph(
        extractions=extractions,
        utility_type=args.utility_type.upper(),
        tile_meta_by_id=tile_meta,
    )
    payload = graph_to_dict(graph)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Wrote graph JSON: %s (nodes=%s edges=%s)",
        args.out,
        graph.number_of_nodes(),
        graph.number_of_edges(),
    )


if __name__ == "__main__":
    main()
