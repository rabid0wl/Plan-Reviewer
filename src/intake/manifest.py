"""Sheet manifest generation from cover index + title block extraction."""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import fitz

from .models import SheetInfo

logger = logging.getLogger(__name__)


SHEET_TYPE_KEYWORDS: dict[str, list[str]] = {
    "cover": ["CIVIL IMPROVEMENT PLANS", "SHEET INDEX", "VICINITY MAP"],
    "notes": ["GENERAL NOTES", "ABBREVIATIONS", "LEGEND"],
    "demolition": ["DEMOLITION", "DEMO KEY NOTES", "REMOVALS"],
    "plan_view": [
        "STORM DRAIN PLAN",
        "UTILITY PLAN",
        "SEWER PLAN",
        "WATER PLAN",
        "GRADING PLAN",
        "IMPROVEMENT PLAN",
    ],
    "profile": ["PROFILE", "STA:", "EXISTING GRADE"],
    "detail": ["TYPICAL", "DETAIL", "SECTION", "STANDARD"],
    "signing": ["SIGN", "STRIPING", "PAVEMENT MARKING", "TRAFFIC"],
    "erosion": ["EROSION", "SWPPP", "BMP"],
}

EXTRACT_SHEET_TYPES = {"plan_view", "profile", "detail"}
LIGHT_EXTRACT_TYPES = {"cover", "notes", "demolition", "signing", "erosion"}

SHEET_LABEL_PATTERN = re.compile(r"\b([A-Z]{1,4})\s*[-]?\s*(\d{1,3}[A-Z]?)\b")


def _normalize_sheet_label(raw_label: str) -> str | None:
    normalized = raw_label.upper().strip()
    normalized = re.sub(r"[^A-Z0-9-]", "", normalized)

    match = SHEET_LABEL_PATTERN.search(normalized)
    if not match:
        return None
    prefix, suffix = match.group(1), match.group(2)
    return f"{prefix}-{suffix}"


def _extract_sheet_label(text: str) -> str | None:
    for line in text.splitlines():
        normalized = _normalize_sheet_label(line)
        if normalized:
            return normalized

    match = SHEET_LABEL_PATTERN.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def _classify_sheet_type(*texts: str) -> str:
    merged = "\n".join(texts).upper()
    scores: dict[str, int] = {sheet_type: 0 for sheet_type in SHEET_TYPE_KEYWORDS}

    for sheet_type, keywords in SHEET_TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in merged:
                scores[sheet_type] += 1

    best_type = max(scores, key=scores.get)
    if scores[best_type] == 0:
        return "other"
    return best_type


def _extract_utility_types(*texts: str) -> list[str]:
    merged = "\n".join(texts).upper()
    utility_types: list[str] = []

    has_sd = (" SD " in f" {merged} ") or ("STORM DRAIN" in merged)
    has_ss = (" SS " in f" {merged} ") or ("SANITARY SEWER" in merged) or ("SEWER" in merged)
    has_w = (" W " in f" {merged} ") or (" WATER " in f" {merged} ") or ("POTABLE WATER" in merged)

    if has_sd:
        utility_types.append("SD")
    if has_ss:
        utility_types.append("SS")
    if has_w:
        utility_types.append("W")
    return utility_types


def _extract_title_block_text(page: fitz.Page, right_strip_ratio: float = 0.15) -> str:
    rect = page.rect
    clip = fitz.Rect(rect.width * (1.0 - right_strip_ratio), 0.0, rect.width, rect.height)
    return page.get_text("text", clip=clip)


def _parse_cover_sheet_index(index_text: str) -> dict[str, str]:
    """
    Parse likely sheet index lines from cover text.

    Expected line examples:
    - C-1 COVER SHEET
    - U2 - UTILITY PLAN AND PROFILE
    """
    parsed: dict[str, str] = {}
    for raw_line in index_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        line_match = re.search(r"\b([A-Z]{1,4}\s*-?\s*\d{1,3}[A-Z]?)\b", line.upper())
        if not line_match:
            continue

        label_raw = line_match.group(1)
        label = _normalize_sheet_label(label_raw)
        if not label:
            continue

        description = line[line_match.end() :].strip(" -:\t")
        if description:
            parsed[label] = description

    return parsed


def build_manifest(pdf_path: Path) -> list[SheetInfo]:
    """Build sheet manifest from cover sheet index + page title blocks."""
    manifest: list[SheetInfo] = []
    with fitz.open(pdf_path) as doc:
        cover_index_text = ""
        for page_idx in range(min(2, len(doc))):
            cover_index_text += "\n" + doc[page_idx].get_text("text")
        cover_index_map = _parse_cover_sheet_index(cover_index_text)

        for page_idx, page in enumerate(doc):
            page_number = page_idx + 1
            title_block_text = _extract_title_block_text(page)
            full_text = page.get_text("text")
            label = _extract_sheet_label(title_block_text) or _extract_sheet_label(full_text)

            description = cover_index_map.get(label) if label else None
            sheet_type = _classify_sheet_type(title_block_text, full_text, description or "")
            utility_types = _extract_utility_types(title_block_text, full_text, description or "")

            manifest.append(
                SheetInfo(
                    page_number=page_number,
                    sheet_label=label,
                    sheet_type=sheet_type,
                    description=description,
                    utility_types=utility_types,
                    needs_deep_extraction=sheet_type in EXTRACT_SHEET_TYPES,
                )
            )

    return manifest


def save_manifest(manifest: list[SheetInfo], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [sheet.to_dict() for sheet in manifest]
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sheet manifest from a civil plan set PDF.")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to input PDF.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args()

    manifest = build_manifest(args.pdf)
    output_path = args.output / "manifest.json"
    save_manifest(manifest, output_path)

    deep_count = sum(1 for sheet in manifest if sheet.needs_deep_extraction)
    logger.info("Manifest pages: %s", len(manifest))
    logger.info("Deep extraction pages: %s", deep_count)
    logger.info("Manifest written: %s", output_path)


if __name__ == "__main__":
    main()

