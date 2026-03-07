"""PDF tiling module with per-tile image and text-layer extraction."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import fitz

from .models import TileInfo, TilingStrategy, TitleBlockCrop
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


def _build_occupancy_grid(
    page: fitz.Page,
    *,
    grid_cols: int,
    grid_rows: int,
) -> tuple[list[list[bool]], fitz.Rect, float, float]:
    """Build boolean occupancy grid from drawings and text blocks.

    Args:
        page: Open fitz.Page.
        grid_cols: Number of horizontal cells in the occupancy grid.
        grid_rows: Number of vertical cells in the occupancy grid.

    Returns:
        A 4-tuple of ``(occupied, page_rect, cell_w, cell_h)`` where
        ``occupied`` is a ``grid_rows x grid_cols`` boolean grid.
    """
    page_rect = page.rect
    page_w = page_rect.width
    page_h = page_rect.height

    cell_w = page_w / grid_cols if grid_cols > 0 else 1.0
    cell_h = page_h / grid_rows if grid_rows > 0 else 1.0

    occupied: list[list[bool]] = [[False] * grid_cols for _ in range(grid_rows)]

    def _mark_rect(r: fitz.Rect) -> None:
        """Mark all cells overlapped by rect *r* as occupied."""
        if r.is_empty or r.is_infinite:
            return
        # Clamp to page bounds.
        x0 = max(r.x0, page_rect.x0)
        y0 = max(r.y0, page_rect.y0)
        x1 = min(r.x1, page_rect.x1)
        y1 = min(r.y1, page_rect.y1)
        if x0 >= x1 or y0 >= y1:
            return
        col0 = max(0, int((x0 - page_rect.x0) / cell_w))
        col1 = min(grid_cols - 1, int((x1 - page_rect.x0 - 1e-9) / cell_w))
        row0 = max(0, int((y0 - page_rect.y0) / cell_h))
        row1 = min(grid_rows - 1, int((y1 - page_rect.y0 - 1e-9) / cell_h))
        for r_idx in range(row0, row1 + 1):
            for c_idx in range(col0, col1 + 1):
                occupied[r_idx][c_idx] = True

    # Mark cells from vector drawings.
    try:
        for drawing in page.get_drawings():
            _mark_rect(drawing.get("rect") or fitz.Rect())
    except Exception:  # pragma: no cover – defensive for unusual PDFs
        pass

    # Mark cells from text blocks.
    try:
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            bbox = block.get("bbox")
            if bbox and len(bbox) == 4:
                _mark_rect(fitz.Rect(bbox))
    except Exception:  # pragma: no cover
        pass

    return occupied, page_rect, cell_w, cell_h


def _flood_fill_regions(
    occupied: list[list[bool]],
    *,
    grid_rows: int,
    grid_cols: int,
) -> tuple[list[list[int]], int]:
    """Label connected components via flood fill.

    Args:
        occupied: Boolean occupancy grid (``grid_rows x grid_cols``).
        grid_rows: Number of rows in the grid.
        grid_cols: Number of columns in the grid.

    Returns:
        A 2-tuple ``(labels, num_labels)`` where ``labels`` has the same
        shape as *occupied* and each occupied cell carries its component id.
        Unoccupied cells are ``-1``.
    """
    labels: list[list[int]] = [[-1] * grid_cols for _ in range(grid_rows)]
    num_labels = 0

    def _flood_fill(start_r: int, start_c: int, label: int) -> None:
        stack = [(start_r, start_c)]
        while stack:
            cr, cc = stack.pop()
            if cr < 0 or cr >= grid_rows or cc < 0 or cc >= grid_cols:
                continue
            if labels[cr][cc] != -1 or not occupied[cr][cc]:
                continue
            labels[cr][cc] = label
            stack.extend([(cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)])

    for row in range(grid_rows):
        for col in range(grid_cols):
            if occupied[row][col] and labels[row][col] == -1:
                _flood_fill(row, col, num_labels)
                num_labels += 1

    return labels, num_labels


def _compute_content_regions(
    page: fitz.Page,
    *,
    grid_cols: int = 24,
    grid_rows: int = 16,
    padding_pct: float = 0.05,
    min_area_pct: float = 0.05,
    max_regions: int = 8,
) -> list[fitz.Rect] | None:
    """Identify content-dense regions on a page using drawings and text blocks.

    Uses a boolean occupancy grid and connected-component flood-fill to group
    nearby drawing/text bounding boxes into rectangular regions.  Fast enough
    for real-time use (O(drawings + text_blocks)).

    Args:
        page: Open fitz.Page.
        grid_cols: Number of horizontal cells in the occupancy grid.
        grid_rows: Number of vertical cells in the occupancy grid.
        padding_pct: Fractional padding added around each region (relative to
            page width/height).
        min_area_pct: Regions whose area is less than this fraction of the
            total page area are discarded.
        max_regions: Fall back to fixed grid when more regions than this are
            found (avoids generating more tiles than the standard 3×2 grid).

    Returns:
        A list of :class:`fitz.Rect` objects, or ``None`` when the page has
        sparse content or the result would not improve over the fixed grid.
    """
    page_rect = page.rect
    page_w = page_rect.width
    page_h = page_rect.height
    if page_w <= 0 or page_h <= 0:
        return None

    occupied, page_rect, cell_w, cell_h = _build_occupancy_grid(
        page, grid_cols=grid_cols, grid_rows=grid_rows,
    )

    # Count occupied cells.
    total_occupied = sum(occupied[r][c] for r in range(grid_rows) for c in range(grid_cols))
    if total_occupied == 0:
        logger.debug("Adaptive tiling: no content found, falling back to grid.")
        return None

    labels, num_labels = _flood_fill_regions(
        occupied, grid_rows=grid_rows, grid_cols=grid_cols,
    )

    if num_labels == 0:
        return None

    # Compute bounding cell-range for each component, then convert to page coords.
    comp_r0 = [grid_rows] * num_labels
    comp_c0 = [grid_cols] * num_labels
    comp_r1 = [-1] * num_labels
    comp_c1 = [-1] * num_labels

    for row in range(grid_rows):
        for col in range(grid_cols):
            lbl = labels[row][col]
            if lbl == -1:
                continue
            if row < comp_r0[lbl]:
                comp_r0[lbl] = row
            if row > comp_r1[lbl]:
                comp_r1[lbl] = row
            if col < comp_c0[lbl]:
                comp_c0[lbl] = col
            if col > comp_c1[lbl]:
                comp_c1[lbl] = col

    pad_w = page_w * padding_pct
    pad_h = page_h * padding_pct
    min_area = page_w * page_h * min_area_pct

    regions: list[fitz.Rect] = []
    for lbl in range(num_labels):
        if comp_r1[lbl] < 0 or comp_c1[lbl] < 0:
            continue
        # Convert grid cells to page coordinates.
        x0 = page_rect.x0 + comp_c0[lbl] * cell_w - pad_w
        y0 = page_rect.y0 + comp_r0[lbl] * cell_h - pad_h
        x1 = page_rect.x0 + (comp_c1[lbl] + 1) * cell_w + pad_w
        y1 = page_rect.y0 + (comp_r1[lbl] + 1) * cell_h + pad_h
        # Clamp to page.
        x0 = max(x0, page_rect.x0)
        y0 = max(y0, page_rect.y0)
        x1 = min(x1, page_rect.x1)
        y1 = min(y1, page_rect.y1)
        region = fitz.Rect(x0, y0, x1, y1)
        if region.is_empty or region.get_area() < min_area:
            continue
        regions.append(region)

    if len(regions) < 2:
        logger.debug(
            "Adaptive tiling: only %d region(s) found, falling back to grid.",
            len(regions),
        )
        return None

    if len(regions) > max_regions:
        logger.debug(
            "Adaptive tiling: %d regions exceed max_regions=%d, falling back to grid.",
            len(regions),
            max_regions,
        )
        return None

    logger.debug("Adaptive tiling: %d content region(s) identified.", len(regions))
    return regions


def tile_page_adaptive(
    doc: fitz.Document,
    page_index: int,
    output_dir: Path,
    dpi: int = 300,
    grid_rows: int = 2,
    grid_cols: int = 3,
    overlap_pct: float = 0.10,
) -> list[TileInfo]:
    """Extract PNG + text-layer tiles using content-aware adaptive regions.

    Calls :func:`_compute_content_regions` to identify where content actually
    is on the page, then renders each region as a separate tile.  Falls back
    to the regular fixed-grid :func:`tile_page` when content detection finds
    sparse or ambiguous content.

    Tile ids use the format ``p{page_number}_a{region_index}`` to distinguish
    adaptive tiles from fixed-grid tiles.  ``row`` and ``col`` in the
    :class:`TileInfo` are both set to the region index and ``0``, respectively.

    Args:
        doc: Open fitz.Document.
        page_index: Zero-based page index.
        output_dir: Root output directory.  Tiles go under
            ``{output_dir}/tiles/``; text layers under
            ``{output_dir}/text_layers/``.
        dpi: Render resolution.
        grid_rows: Passed to :func:`tile_page` when falling back.
        grid_cols: Passed to :func:`tile_page` when falling back.
        overlap_pct: Passed to :func:`tile_page` when falling back.

    Returns:
        List of :class:`TileInfo` objects for the rendered tiles.
    """
    if grid_rows <= 0 or grid_cols <= 0:
        raise ValueError("grid_rows and grid_cols must be positive.")
    if not (0.0 <= overlap_pct < 1.0):
        raise ValueError("overlap_pct must be in [0.0, 1.0).")

    page = doc[page_index]
    page_number = page_index + 1

    regions = _compute_content_regions(page)
    if regions is None:
        logger.debug(
            "Page %s: adaptive region detection fell back to fixed grid.", page_number
        )
        return tile_page(
            doc,
            page_index,
            output_dir,
            dpi=dpi,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
            overlap_pct=overlap_pct,
        )

    tiles_dir = output_dir / "tiles"
    text_layers_dir = output_dir / "text_layers"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    text_layers_dir.mkdir(parents=True, exist_ok=True)

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    tile_infos: list[TileInfo] = []
    for region_idx, clip in enumerate(regions):
        tile_id = f"p{page_number}_a{region_idx}"
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
                row=region_idx,
                col=0,
                clip_rect=(clip.x0, clip.y0, clip.x1, clip.y1),
                image_path=image_path,
                text_layer_path=text_layer_path,
                image_width_px=pix.width,
                image_height_px=pix.height,
            )
        )

    logger.debug(
        "Page %s: adaptive tiling produced %d tile(s).", page_number, len(tile_infos)
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
    strategy: TilingStrategy = TilingStrategy.GRID,
) -> dict[int, list[TileInfo]]:
    """Tile an entire PDF (or selected pages).

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Root output directory.
        page_numbers: 1-based page numbers to process.  ``None`` processes all
            pages.
        dpi: Render resolution.
        grid_rows: Fixed-grid tile rows (used when ``strategy`` is ``GRID``
            or when adaptive detection falls back).
        grid_cols: Fixed-grid tile columns.
        overlap_pct: Tile overlap fraction for the fixed grid.
        skip_low_coherence: Skip pages whose full-page coherence score is below
            ``coherence_threshold``.
        coherence_threshold: Threshold used when ``skip_low_coherence=True``.
        strategy: Tiling strategy — :attr:`TilingStrategy.GRID` for the fixed
            grid or :attr:`TilingStrategy.ADAPTIVE` for content-aware regions
            via :func:`tile_page_adaptive`.

    Returns:
        Mapping of page_number -> list of :class:`TileInfo`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, list[TileInfo]] = {}

    tiler = tile_page_adaptive if strategy == TilingStrategy.ADAPTIVE else tile_page

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

            tiles = tiler(
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


def extract_title_block_crop(
    doc: fitz.Document,
    page_index: int,
    output_dir: Path,
    *,
    dpi: int = 200,
    height_ratio: float = 0.25,
    width_ratio: float = 0.35,
) -> TitleBlockCrop:
    """Crop the bottom-right title block region from one page and save as PNG.

    The title block on standard ARCH D civil plan sheets occupies the
    bottom-right corner.  Default ratios (25% height × 35% width) cover
    that region reliably without capturing too much plan content.

    Args:
        doc: Open fitz.Document.
        page_index: Zero-based page index.
        output_dir: Root output directory; crops are saved under
            ``{output_dir}/title_blocks/``.
        dpi: Render resolution.  200 DPI is sufficient for classification.
        height_ratio: Fraction of page height to include (from the bottom).
        width_ratio: Fraction of page width to include (from the right).

    Returns:
        TitleBlockCrop with image path and pixel dimensions.
    """
    if not (0.0 < height_ratio <= 1.0):
        raise ValueError("height_ratio must be in (0.0, 1.0].")
    if not (0.0 < width_ratio <= 1.0):
        raise ValueError("width_ratio must be in (0.0, 1.0].")

    page = doc[page_index]
    page_number = page_index + 1
    rect = page.rect

    x0 = rect.width * (1.0 - width_ratio)
    y0 = rect.height * (1.0 - height_ratio)
    x1 = rect.width
    y1 = rect.height
    clip = fitz.Rect(x0, y0, x1, y1)

    title_blocks_dir = output_dir / "title_blocks"
    title_blocks_dir.mkdir(parents=True, exist_ok=True)

    image_path = title_blocks_dir / f"p{page_number}_title_block.png"

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    pix.save(str(image_path))

    logger.debug(
        "Title block crop saved: page %s → %s (%dx%d px)",
        page_number,
        image_path,
        pix.width,
        pix.height,
    )

    return TitleBlockCrop(
        page_number=page_number,
        image_path=image_path,
        image_width_px=pix.width,
        image_height_px=pix.height,
        clip_rect=(clip.x0, clip.y0, clip.x1, clip.y1),
    )


def extract_title_block_crops(
    pdf_path: Path,
    output_dir: Path,
    page_numbers: list[int] | None = None,
    *,
    dpi: int = 200,
    height_ratio: float = 0.25,
    width_ratio: float = 0.35,
) -> list[TitleBlockCrop]:
    """Crop title block regions from an entire PDF (or selected pages).

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Root output directory; crops are saved under
            ``{output_dir}/title_blocks/``.
        page_numbers: 1-based page numbers to process.  ``None`` processes
            all pages.
        dpi: Render resolution passed to :func:`extract_title_block_crop`.
        height_ratio: Fraction of page height to include (from the bottom).
        width_ratio: Fraction of page width to include (from the right).

    Returns:
        List of TitleBlockCrop objects in page order.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    crops: list[TitleBlockCrop] = []

    with fitz.open(pdf_path) as doc:
        selected_pages = page_numbers or list(range(1, len(doc) + 1))
        for page_number in selected_pages:
            if page_number < 1 or page_number > len(doc):
                raise ValueError(f"Invalid page number: {page_number}")
            crop = extract_title_block_crop(
                doc,
                page_number - 1,
                output_dir,
                dpi=dpi,
                height_ratio=height_ratio,
                width_ratio=width_ratio,
            )
            crops.append(crop)

    logger.info(
        "Extracted %s title block crop(s) from %s",
        len(crops),
        pdf_path,
    )
    return crops


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
    parser.add_argument(
        "--adaptive",
        action="store_true",
        default=False,
        help=(
            "Use content-aware adaptive tiling instead of the fixed grid. "
            "Detects content regions via page drawings and text blocks, then "
            "tiles only those regions. Falls back to the fixed grid when "
            "content is sparse or detection produces too many regions."
        ),
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
        strategy=TilingStrategy.ADAPTIVE if args.adaptive else TilingStrategy.GRID,
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

