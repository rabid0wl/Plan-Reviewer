"""PDF tiling module with per-tile image and text-layer extraction."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import fitz

from .models import TileInfo
from .text_layer import COHERENCE_THRESHOLD, extract_text_layer, save_text_layer_json
from ..utils.cli import parse_pages_argument

logger = logging.getLogger(__name__)


def _compute_tile_clips(
    page_rect: fitz.Rect,
    *,
    grid_rows: int,
    grid_cols: int,
    overlap_pct: float,
) -> list[tuple[int, int, fitz.Rect]]:
    page_width = page_rect.width
    page_height = page_rect.height

    base_w = page_width / grid_cols
    base_h = page_height / grid_rows
    overlap_w = base_w * overlap_pct
    overlap_h = base_h * overlap_pct

    clips: list[tuple[int, int, fitz.Rect]] = []
    for row in range(grid_rows):
        for col in range(grid_cols):
            x0 = col * base_w - (overlap_w if col > 0 else 0.0)
            y0 = row * base_h - (overlap_h if row > 0 else 0.0)
            x1 = (col + 1) * base_w + (overlap_w if col < grid_cols - 1 else 0.0)
            y1 = (row + 1) * base_h + (overlap_h if row < grid_rows - 1 else 0.0)

            x0 = max(0.0, x0)
            y0 = max(0.0, y0)
            x1 = min(page_width, x1)
            y1 = min(page_height, y1)
            clips.append((row, col, fitz.Rect(x0, y0, x1, y1)))

    return clips


def tile_page(
    doc: fitz.Document,
    page_index: int,
    output_dir: Path,
    dpi: int = 300,
    grid_rows: int = 2,
    grid_cols: int = 3,
    overlap_pct: float = 0.10,
) -> list[TileInfo]:
    """Extract PNG + text-layer tiles from one page."""
    if grid_rows <= 0 or grid_cols <= 0:
        raise ValueError("grid_rows and grid_cols must be positive.")
    if not (0.0 <= overlap_pct < 1.0):
        raise ValueError("overlap_pct must be in [0.0, 1.0).")

    page = doc[page_index]
    page_number = page_index + 1

    tiles_dir = output_dir / "tiles"
    text_layers_dir = output_dir / "text_layers"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    text_layers_dir.mkdir(parents=True, exist_ok=True)

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    clips = _compute_tile_clips(
        page.rect,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        overlap_pct=overlap_pct,
    )

    tile_infos: list[TileInfo] = []
    for row, col, clip in clips:
        tile_id = f"p{page_number}_r{row}_c{col}"
        image_path = tiles_dir / f"{tile_id}.png"
        text_layer_path = text_layers_dir / f"{tile_id}.json"

        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        pix.save(str(image_path))

        text_layer = extract_text_layer(
            page,
            clip=clip,
            clip_origin=(clip.x0, clip.y0),
            tile_id=tile_id,
            page_number=page_number,
        )
        save_text_layer_json(text_layer, text_layer_path)

        tile_infos.append(
            TileInfo(
                tile_id=tile_id,
                page_number=page_number,
                row=row,
                col=col,
                clip_rect=(clip.x0, clip.y0, clip.x1, clip.y1),
                image_path=image_path,
                text_layer_path=text_layer_path,
                image_width_px=pix.width,
                image_height_px=pix.height,
            )
        )

    return tile_infos


def tile_pdf(
    pdf_path: Path,
    output_dir: Path,
    page_numbers: list[int] | None = None,
    dpi: int = 300,
    grid_rows: int = 2,
    grid_cols: int = 3,
    overlap_pct: float = 0.10,
    *,
    skip_low_coherence: bool = True,
    coherence_threshold: float = COHERENCE_THRESHOLD,
) -> dict[int, list[TileInfo]]:
    """
    Tile an entire PDF (or selected pages).

    Returns page_number -> tile info list.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, list[TileInfo]] = {}

    with fitz.open(pdf_path) as doc:
        selected_pages = page_numbers or list(range(1, len(doc) + 1))
        for page_number in selected_pages:
            if page_number < 1 or page_number > len(doc):
                raise ValueError(f"Invalid page number: {page_number}")

            if skip_low_coherence:
                page = doc[page_number - 1]
                full_text_layer = extract_text_layer(
                    page,
                    clip=None,
                    clip_origin=(0.0, 0.0),
                    tile_id=f"p{page_number}_full",
                    page_number=page_number,
                )
                if full_text_layer.coherence_score < coherence_threshold:
                    logger.warning(
                        "Skipping page %s: coherence %.3f < %.2f",
                        page_number,
                        full_text_layer.coherence_score,
                        coherence_threshold,
                    )
                    continue

            tiles = tile_page(
                doc,
                page_number - 1,
                output_dir,
                dpi=dpi,
                grid_rows=grid_rows,
                grid_cols=grid_cols,
                overlap_pct=overlap_pct,
            )
            results[page_number] = tiles

    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tile PDF pages into PNGs with matching text-layer JSON.")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to input PDF.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Page selection, e.g. '14' or '1-3,7'. Omit for all pages.",
    )
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI.")
    parser.add_argument("--grid-rows", type=int, default=2, help="Tile grid rows.")
    parser.add_argument("--grid-cols", type=int, default=3, help="Tile grid columns.")
    parser.add_argument("--overlap-pct", type=float, default=0.10, help="Tile overlap percentage.")
    parser.add_argument(
        "--skip-low-coherence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip page tiling when full-page coherence is below threshold.",
    )
    parser.add_argument(
        "--coherence-threshold",
        type=float,
        default=COHERENCE_THRESHOLD,
        help="Coherence threshold used when --skip-low-coherence is enabled.",
    )
    return parser


def _write_tiles_index(results: dict[int, list[TileInfo]], output_dir: Path) -> Path:
    index_path = output_dir / "tiles_index.json"
    payload: dict[str, Any] = {
        "pages": {
            str(page_number): [tile.to_dict() for tile in tiles]
            for page_number, tiles in sorted(results.items())
        }
    }
    with index_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return index_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args()

    with fitz.open(args.pdf) as doc:
        page_numbers = parse_pages_argument(args.pages, total_pages=len(doc))

    results = tile_pdf(
        args.pdf,
        args.output,
        page_numbers=page_numbers,
        dpi=args.dpi,
        grid_rows=args.grid_rows,
        grid_cols=args.grid_cols,
        overlap_pct=args.overlap_pct,
        skip_low_coherence=args.skip_low_coherence,
        coherence_threshold=args.coherence_threshold,
    )
    index_path = _write_tiles_index(results, args.output)

    total_tiles = sum(len(tiles) for tiles in results.values())
    logger.info(
        "Tiled %s page(s) into %s tile(s). Index: %s",
        len(results),
        total_tiles,
        index_path,
    )


if __name__ == "__main__":
    main()

