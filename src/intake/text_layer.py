"""PDF text-layer extraction with coherence scoring and CLI entry point."""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from .models import TextItem, TextLayer
from ..utils.cli import parse_pages_argument
from ..utils.unicode import clean_unicode

COHERENCE_THRESHOLD = 0.40

logger = logging.getLogger(__name__)


def _iter_spans(text_dict: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                spans.append(span)
    return spans


def _calculate_coherence_from_spans(
    spans: list[dict[str, Any]],
) -> tuple[float, int, int, int, str]:
    total_spans = 0
    multi_char_spans = 0
    numeric_spans = 0
    font_counter: Counter[str] = Counter()

    for span in spans:
        raw_text = str(span.get("text", ""))
        text = clean_unicode(raw_text).strip()
        if not text:
            continue

        total_spans += 1
        if len(text) > 1:
            multi_char_spans += 1
        if len(text) > 1 and any(ch.isdigit() for ch in text):
            numeric_spans += 1

        font = str(span.get("font", "")).strip()
        if font:
            font_counter[font] += 1

    coherence_score = (multi_char_spans / total_spans) if total_spans else 0.0
    primary_font = font_counter.most_common(1)[0][0] if font_counter else ""
    return coherence_score, total_spans, multi_char_spans, numeric_spans, primary_font


def calculate_coherence(text_dict: dict[str, Any]) -> tuple[float, int, int, int, str]:
    """
    Calculate text coherence from a PyMuPDF text dict.

    Coherence = multi_char_spans / total_spans
    """
    return _calculate_coherence_from_spans(_iter_spans(text_dict))


def extract_text_layer(
    page: fitz.Page,
    clip: fitz.Rect | None = None,
    clip_origin: tuple[float, float] = (0.0, 0.0),
    *,
    tile_id: str | None = None,
    page_number: int | None = None,
) -> TextLayer:
    """
    Extract all text spans from a page (or clipped region) with bounding boxes.
    """
    text_dict = page.get_text("dict", clip=clip)

    if clip is None:
        combined_spans = list(_iter_spans(text_dict))
    else:
        # Build tile spans from full-page text and spatial intersection only.
        # This avoids clip-induced truncation like "TALL 342..." at boundaries.
        padded = fitz.Rect(clip.x0 - 5.0, clip.y0 - 5.0, clip.x1 + 5.0, clip.y1 + 5.0)
        full_text_dict = page.get_text("dict")
        combined_spans = []
        seen_keys: set[tuple[str, tuple[float, float, float, float]]] = set()
        for span in _iter_spans(full_text_dict):
            raw_bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
            x0 = float(raw_bbox[0])
            y0 = float(raw_bbox[1])
            x1 = float(raw_bbox[2])
            y1 = float(raw_bbox[3])
            span_rect = fitz.Rect(x0, y0, x1, y1)
            if not span_rect.intersects(padded):
                continue

            key = (
                clean_unicode(str(span.get("text", ""))).strip(),
                (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            combined_spans.append(span)

    coherence_score, total_spans, multi_char_spans, numeric_spans, primary_font = (
        _calculate_coherence_from_spans(combined_spans)
    )

    if page_number is None:
        page_number = page.number + 1
    if tile_id is None:
        tile_id = f"p{page_number}_full"

    origin_x, origin_y = clip_origin
    items: list[TextItem] = []
    text_id = 0

    for span in combined_spans:
        text = clean_unicode(str(span.get("text", ""))).strip()
        if not text:
            continue

        raw_bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
        x0, y0, x1, y1 = (float(raw_bbox[0]), float(raw_bbox[1]), float(raw_bbox[2]), float(raw_bbox[3]))
        bbox_global = (x0, y0, x1, y1)
        bbox_local = (x0 - origin_x, y0 - origin_y, x1 - origin_x, y1 - origin_y)

        items.append(
            TextItem(
                text_id=text_id,
                text=text,
                bbox_local=bbox_local,
                bbox_global=bbox_global,
                font=str(span.get("font", "")),
                font_size=float(span.get("size", 0.0)),
            )
        )
        text_id += 1

    return TextLayer(
        tile_id=tile_id,
        page_number=page_number,
        items=items,
        coherence_score=coherence_score,
        total_spans=total_spans,
        multi_char_spans=multi_char_spans,
        numeric_spans=numeric_spans,
        primary_font=primary_font,
        is_hybrid_viable=coherence_score >= COHERENCE_THRESHOLD,
    )


def save_text_layer_json(text_layer: TextLayer, output_path: Path) -> None:
    """Persist one text-layer payload as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(text_layer.to_dict(), f, indent=2, ensure_ascii=False)


def score_pdf_coherence(
    pdf_path: Path,
    *,
    pages: list[int] | None = None,
    output_dir: Path | None = None,
    write_page_json: bool = True,
) -> list[dict[str, Any]]:
    """Score page-level text coherence across a PDF."""
    results: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        page_numbers = pages or list(range(1, len(doc) + 1))

        page_output_dir: Path | None = None
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            page_output_dir = output_dir / "text_layers_pages"
            if write_page_json:
                page_output_dir.mkdir(parents=True, exist_ok=True)

        for page_number in page_numbers:
            page = doc[page_number - 1]
            text_layer = extract_text_layer(
                page,
                clip=None,
                clip_origin=(0.0, 0.0),
                tile_id=f"p{page_number}_full",
                page_number=page_number,
            )

            if page_output_dir and write_page_json:
                save_text_layer_json(text_layer, page_output_dir / f"p{page_number}_full.json")

            if not text_layer.is_hybrid_viable:
                logger.warning(
                    "Page %s coherence %.3f < %.2f. Skipping hybrid extraction for this page.",
                    page_number,
                    text_layer.coherence_score,
                    COHERENCE_THRESHOLD,
                )

            results.append(
                {
                    "page_number": page_number,
                    "coherence_score": text_layer.coherence_score,
                    "total_spans": text_layer.total_spans,
                    "multi_char_spans": text_layer.multi_char_spans,
                    "numeric_spans": text_layer.numeric_spans,
                    "primary_font": text_layer.primary_font,
                    "is_hybrid_viable": text_layer.is_hybrid_viable,
                }
            )

    if output_dir:
        with (output_dir / "coherence_scores.json").open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract text layers and coherence scores from PDF pages.")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to PDF file.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Page selection, e.g. '14' or '1-3,7'. Omit for all pages.",
    )
    parser.add_argument(
        "--scores-only",
        action="store_true",
        help="Write coherence_scores.json only (skip per-page text layer JSON files).",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        pages = parse_pages_argument(args.pages, total_pages=len(doc))

    results = score_pdf_coherence(
        args.pdf,
        pages=pages,
        output_dir=args.output,
        write_page_json=not args.scores_only,
    )

    low_pages = [r for r in results if not r["is_hybrid_viable"]]
    logger.info("Processed %s page(s).", len(results))
    logger.info("Hybrid-viable pages: %s/%s", len(results) - len(low_pages), len(results))
    if low_pages:
        logger.warning("Low-coherence pages: %s", [p["page_number"] for p in low_pages])


if __name__ == "__main__":
    main()
