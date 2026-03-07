"""Data models used by intake pipeline modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


BBox = tuple[float, float, float, float]


class TilingStrategy(str, Enum):
    GRID = "grid"
    ADAPTIVE = "adaptive"


@dataclass
class TileInfo:
    """Metadata for a single extracted tile."""

    tile_id: str
    page_number: int
    row: int
    col: int
    clip_rect: BBox
    image_path: Path
    text_layer_path: Path
    image_width_px: int
    image_height_px: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_path"] = str(self.image_path)
        data["text_layer_path"] = str(self.text_layer_path)
        return data


@dataclass
class TextItem:
    """A single text span from a PDF with provenance."""

    text_id: int
    text: str
    bbox_local: BBox
    bbox_global: BBox
    font: str
    font_size: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextLayer:
    """All text spans for a page region with coherence metrics."""

    tile_id: str
    page_number: int
    items: list[TextItem] = field(default_factory=list)
    coherence_score: float = 0.0
    total_spans: int = 0
    multi_char_spans: int = 0
    numeric_spans: int = 0
    primary_font: str = ""
    is_hybrid_viable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "page_number": self.page_number,
            "coherence_score": self.coherence_score,
            "total_spans": self.total_spans,
            "multi_char_spans": self.multi_char_spans,
            "numeric_spans": self.numeric_spans,
            "primary_font": self.primary_font,
            "is_hybrid_viable": self.is_hybrid_viable,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class TitleBlockCrop:
    """Metadata for a single title block image crop."""

    page_number: int
    image_path: Path
    image_width_px: int
    image_height_px: int
    clip_rect: BBox

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_path"] = str(self.image_path)
        return data


@dataclass
class SheetInfo:
    """Manifest metadata for a sheet/page."""

    page_number: int
    sheet_label: str | None
    sheet_type: str
    description: str | None
    utility_types: list[str]
    needs_deep_extraction: bool
    model_tier: str = "standard"  # "fast", "standard", or "premium"
    title_block_image_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["title_block_image_path"] = (
            str(self.title_block_image_path) if self.title_block_image_path is not None else None
        )
        return data

