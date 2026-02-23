"""Merge helpers for structure deduplication across overlapping tiles."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from ..extraction.schemas import Structure, TileExtraction
from ..utils.parsing import parse_signed_offset, parse_station

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

_UTILITY_STRUCTURE_TYPES: dict[str, set[str]] = {
    "SD": {"SDMH", "SDCB", "CB", "INLET", "DI", "CATCHBASIN"},
    "SS": {"SSMH", "CO", "CLEANOUT"},
    "W": {
        "WV",
        "GV",
        "FH",
        "HYDRANT",
        "BEND",
        "TEE",
        "WATERVALVE",
        "GATEVALVE",
        "FIREHYDRANT",
        "BLOWOFF",
        "AIRVALVE",
        "PRV",
        "METER",
        "REDUCER",
        "SERVICECONNECTION",
    },
}


@dataclass(frozen=True)
class MergedStructure:
    """Merged structure record with provenance and normalized location."""

    node_id: str
    page_number: int
    structure_type: str
    station: str
    offset: str
    parsed_station: float | None
    signed_offset: float | None
    id: str | None
    size: str | None
    rim_elevation: float | None
    tc_elevation: float | None
    fl_elevation: float | None
    inverts: list[dict[str, Any]]
    is_existing: bool
    notes: str | None
    source_tile_ids: list[str]
    source_page_numbers: list[int]
    source_text_ids: list[int]
    sanitized: bool
    variants_count: int
    rim_elevation_values: list[float]


def _norm_token(value: str | None) -> str:
    if not value:
        return ""
    return _NON_ALNUM.sub("", value.upper())


def structure_matches_utility(
    *,
    structure_type: str,
    utility_type: str,
    extraction_utility_types: list[str] | None = None,
    has_inverts: bool = False,
) -> bool:
    """Return True if structure should be included in the requested utility graph."""
    utility = utility_type.upper().strip()
    stype = _norm_token(structure_type)
    if not stype:
        return False

    if utility in stype:
        return True
    if stype in _UTILITY_STRUCTURE_TYPES.get(utility, set()):
        return True

    if stype == "GB" and has_inverts:
        return True

    return False


def _structure_key(page_number: int, structure: Structure) -> tuple[Any, ...]:
    stype = _norm_token(structure.structure_type)
    station_ft = parse_station(structure.station)
    offset_value = structure.offset or "0' CL"
    offset_ft = parse_signed_offset(offset_value)
    if offset_ft is None and "CL" in offset_value.upper():
        offset_ft = 0.0

    if station_ft is not None and offset_ft is not None:
        return (page_number, stype, round(station_ft, 2), round(offset_ft, 2))

    return (
        page_number,
        stype,
        _norm_token(structure.station),
        _norm_token(offset_value),
    )


def _structure_rank(structure: Structure) -> tuple[int, int, int]:
    notes_len = len((structure.notes or "").strip())
    return (len(structure.inverts), notes_len, len(structure.source_text_ids))


def _unique_ints(values: list[int]) -> list[int]:
    return sorted({int(v) for v in values})


def _unique_floats(values: list[float]) -> list[float]:
    return sorted({round(float(v), 4) for v in values})


def _choose_best_inverts(structures: list[Structure]) -> list[dict[str, Any]]:
    if not structures:
        return []
    best = max(structures, key=_structure_rank)
    return [invert.model_dump() for invert in best.inverts]


def _pick_first_non_none(structures: list[Structure], field: str) -> Any:
    ranked = sorted(structures, key=_structure_rank, reverse=True)
    for structure in ranked:
        value = getattr(structure, field)
        if value is not None:
            return value
    return None


def _pick_first_non_none_merged(items: list[MergedStructure], field: str) -> Any:
    for item in sorted(
        items,
        key=lambda value: (len(value.inverts), len((value.notes or "").strip()), len(value.source_text_ids)),
        reverse=True,
    ):
        value = getattr(item, field)
        if value is not None:
            return value
    return None


def _make_node_id(
    *,
    utility_type: str,
    page_number: int,
    structure_type: str,
    station: str,
    offset: str,
    parsed_station: float | None,
    signed_offset: float | None,
) -> str:
    utility = utility_type.upper()
    stype = _norm_token(structure_type) or "STRUCT"
    if parsed_station is not None and signed_offset is not None:
        return f"{utility}:{page_number}:{stype}:{parsed_station:.2f}:{signed_offset:+.2f}"

    raw = f"{utility}|{page_number}|{structure_type}|{station}|{offset}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{utility}:{page_number}:{stype}:{digest}"


def _collapse_merged_group(
    *,
    group: list[MergedStructure],
    utility_type: str,
) -> MergedStructure:
    primary = max(
        group,
        key=lambda value: (
            len(value.inverts),
            len((value.notes or "").strip()),
            len(value.source_text_ids),
        ),
    )
    all_tile_ids = sorted({tile_id for item in group for tile_id in item.source_tile_ids})
    all_page_numbers = sorted({page for item in group for page in item.source_page_numbers})
    all_text_ids = sorted({text_id for item in group for text_id in item.source_text_ids})
    all_rim_values = _unique_floats(
        [rim for item in group for rim in item.rim_elevation_values if rim is not None]
    )
    total_variants = sum(item.variants_count for item in group)
    any_sanitized = any(item.sanitized for item in group)

    node_id = _make_node_id(
        utility_type=utility_type,
        page_number=primary.page_number,
        structure_type=primary.structure_type,
        station=primary.station,
        offset=primary.offset,
        parsed_station=primary.parsed_station,
        signed_offset=primary.signed_offset,
    )

    return MergedStructure(
        node_id=node_id,
        page_number=primary.page_number,
        structure_type=primary.structure_type,
        station=primary.station,
        offset=primary.offset,
        parsed_station=primary.parsed_station,
        signed_offset=primary.signed_offset,
        id=primary.id or _pick_first_non_none_merged(group, "id"),
        size=primary.size or _pick_first_non_none_merged(group, "size"),
        rim_elevation=(
            primary.rim_elevation
            if primary.rim_elevation is not None
            else _pick_first_non_none_merged(group, "rim_elevation")
        ),
        tc_elevation=(
            primary.tc_elevation
            if primary.tc_elevation is not None
            else _pick_first_non_none_merged(group, "tc_elevation")
        ),
        fl_elevation=(
            primary.fl_elevation
            if primary.fl_elevation is not None
            else _pick_first_non_none_merged(group, "fl_elevation")
        ),
        inverts=primary.inverts,
        is_existing=any(item.is_existing for item in group),
        notes=max((item.notes for item in group), key=lambda note: len((note or "").strip()), default=None),
        source_tile_ids=all_tile_ids,
        source_page_numbers=all_page_numbers,
        source_text_ids=all_text_ids,
        sanitized=any_sanitized,
        variants_count=total_variants,
        rim_elevation_values=all_rim_values,
    )


def _proximity_merge(
    merged: list[MergedStructure],
    *,
    utility_type: str,
    station_tol: float = 3.0,
    offset_tol: float = 1.0,
) -> list[MergedStructure]:
    if len(merged) <= 1:
        return merged

    items = sorted(
        merged,
        key=lambda item: (
            item.page_number,
            _norm_token(item.structure_type),
            item.parsed_station if item.parsed_station is not None else float("inf"),
        ),
    )
    used: set[int] = set()
    result: list[MergedStructure] = []

    for i, item in enumerate(items):
        if i in used:
            continue
        group = [item]
        for j in range(i + 1, len(items)):
            if j in used:
                continue
            other = items[j]
            if other.page_number != item.page_number:
                break
            if _norm_token(other.structure_type) != _norm_token(item.structure_type):
                continue
            if item.parsed_station is None or other.parsed_station is None:
                continue

            station_delta = abs(item.parsed_station - other.parsed_station)
            if station_delta > station_tol:
                if other.parsed_station > item.parsed_station:
                    break
                continue

            if (
                item.signed_offset is not None
                and other.signed_offset is not None
                and abs(item.signed_offset - other.signed_offset) > offset_tol
            ):
                continue

            group.append(other)
            used.add(j)

        if len(group) == 1:
            result.append(item)
        else:
            result.append(_collapse_merged_group(group=group, utility_type=utility_type))

    return result


def merge_structures(
    *,
    extractions: list[TileExtraction],
    utility_type: str,
    tile_meta_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[MergedStructure]:
    """
    Deduplicate structures by exact parsed station/offset key and merge provenance.

    Merge key:
    (page_number, structure_type, parse_station(station), parse_signed_offset(offset))
    """
    grouped: dict[tuple[Any, ...], list[tuple[TileExtraction, Structure, bool]]] = {}
    for extraction in extractions:
        tile_id = extraction.tile_id
        meta = (tile_meta_by_id or {}).get(tile_id, {})
        is_sanitized = bool(meta.get("sanitized", False))
        for structure in extraction.structures:
            if not structure_matches_utility(
                structure_type=structure.structure_type,
                utility_type=utility_type,
                extraction_utility_types=extraction.utility_types_present,
                has_inverts=bool(structure.inverts),
            ):
                continue
            key = _structure_key(extraction.page_number, structure)
            grouped.setdefault(key, []).append((extraction, structure, is_sanitized))

    merged: list[MergedStructure] = []
    for members in grouped.values():
        extractions_for_group = [row[0] for row in members]
        structures_for_group = [row[1] for row in members]
        any_sanitized = any(row[2] for row in members)

        best_structure = max(structures_for_group, key=_structure_rank)
        parsed_station = parse_station(best_structure.station)
        offset_value = best_structure.offset or "0' CL"
        signed_offset = parse_signed_offset(offset_value)
        if signed_offset is None and "CL" in offset_value.upper():
            signed_offset = 0.0

        node_id = _make_node_id(
            utility_type=utility_type,
            page_number=extractions_for_group[0].page_number,
            structure_type=best_structure.structure_type,
            station=best_structure.station,
            offset=offset_value,
            parsed_station=parsed_station,
            signed_offset=signed_offset,
        )

        source_text_ids: list[int] = []
        rim_values: list[float] = []
        for structure in structures_for_group:
            source_text_ids.extend(structure.source_text_ids)
            for invert in structure.inverts:
                source_text_ids.extend(invert.source_text_ids)
            if structure.rim_elevation is not None:
                rim_values.append(structure.rim_elevation)

        merged.append(
            MergedStructure(
                node_id=node_id,
                page_number=extractions_for_group[0].page_number,
                structure_type=best_structure.structure_type,
                station=best_structure.station,
                offset=offset_value,
                parsed_station=parsed_station,
                signed_offset=signed_offset,
                id=_pick_first_non_none(structures_for_group, "id"),
                size=_pick_first_non_none(structures_for_group, "size"),
                rim_elevation=_pick_first_non_none(structures_for_group, "rim_elevation"),
                tc_elevation=_pick_first_non_none(structures_for_group, "tc_elevation"),
                fl_elevation=_pick_first_non_none(structures_for_group, "fl_elevation"),
                inverts=_choose_best_inverts(structures_for_group),
                is_existing=any(structure.is_existing for structure in structures_for_group),
                notes=max(
                    (structure.notes for structure in structures_for_group),
                    key=lambda note: len((note or "").strip()),
                    default=None,
                ),
                source_tile_ids=sorted({ext.tile_id for ext in extractions_for_group}),
                source_page_numbers=sorted({ext.page_number for ext in extractions_for_group}),
                source_text_ids=_unique_ints(source_text_ids),
                sanitized=any_sanitized,
                variants_count=len(members),
                rim_elevation_values=_unique_floats(rim_values),
            )
        )

    merged = _proximity_merge(merged, utility_type=utility_type)

    return sorted(
        merged,
        key=lambda item: (
            item.page_number,
            float("inf") if item.parsed_station is None else item.parsed_station,
            float("inf") if item.signed_offset is None else item.signed_offset,
            item.node_id,
        ),
    )
