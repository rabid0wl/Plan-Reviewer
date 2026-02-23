"""Pydantic models for tile-level extraction outputs."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

_WATER_STRUCTURE_TYPES = {
    "gate_valve",
    "fire_hydrant",
    "blow_off",
    "air_valve",
    "prv",
    "meter",
    "tee",
    "reducer",
    "bend",
    "service_connection",
}


def _normalize_structure_type(value: str | None) -> str:
    """Normalize structure type labels to lowercase snake_case tokens."""
    if not value:
        return ""
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_]+", "", token)
    return token


class InvertElevation(BaseModel):
    """A single invert at a structure."""

    direction: str = Field(description="Compass direction: N, S, E, W, NE, NW, SE, SW")
    pipe_size: str = Field(description='Pipe diameter, e.g., \'12"\' or \'8"\'')
    pipe_type: str | None = Field(default=None, description="SD, SS, or W if identifiable")
    elevation: float = Field(description="Invert elevation to nearest 0.01 ft")
    source_text_ids: list[int] = Field(
        description="text_id(s) from text layer that provided this value"
    )


class Structure(BaseModel):
    """A utility structure."""

    id: str | None = Field(default=None, description="Structure ID, e.g., MH-1, CB-3")
    structure_type: str = Field(
        description=(
            "SDMH|SSMH|CB|GB|inlet|cleanout|junction|gate_valve|fire_hydrant|"
            "blow_off|air_valve|PRV|meter|tee|reducer|bend|service_connection|other"
        )
    )
    size: str | None = Field(default=None, description='Structure size, e.g., \'48"\'')
    station: str = Field(description="Station, e.g., 16+82.45")
    offset: str | None = Field(
        default=None,
        description="Offset with direction, e.g., 28.00' RT. May be null for water fittings.",
    )
    rim_elevation: float | None = Field(default=None, description="Rim elevation")
    tc_elevation: float | None = Field(default=None, description="Top of curb elevation if applicable")
    fl_elevation: float | None = Field(default=None, description="Flowline elevation if applicable")
    inverts: list[InvertElevation] = Field(default_factory=list)
    is_existing: bool = Field(
        default=False,
        description="True if this is an existing structure shown for reference, False if proposed/new.",
    )
    notes: str | None = Field(default=None, description="Additional annotation text")
    source_text_ids: list[int] = Field(
        description="text_id(s) from text layer for station/offset/rim"
    )

    @model_validator(mode="after")
    def _validate_offset_by_structure_type(self) -> "Structure":
        stype = _normalize_structure_type(self.structure_type)
        if stype in _WATER_STRUCTURE_TYPES:
            if isinstance(self.offset, str) and not self.offset.strip():
                self.offset = None
            return self

        if not isinstance(self.offset, str) or not self.offset.strip():
            raise ValueError("offset is required for non-water structure types")
        return self


class Pipe(BaseModel):
    """A pipe run between two structures or points."""

    pipe_type: str = Field(description="SD, SS, or W")
    size: str = Field(description='Pipe diameter, e.g., \'12"\'')
    material: str | None = Field(default=None, description="RCP, PVC, DIP, HDPE, etc.")
    length_lf: float | None = Field(default=None, description="Pipe length in linear feet")
    slope: float | None = Field(default=None, description="Pipe slope as decimal, e.g., 0.0020")
    from_station: str | None = Field(default=None, description="Starting station if identifiable")
    to_station: str | None = Field(default=None, description="Ending station if identifiable")
    from_structure_hint: str | None = Field(
        default=None, description="Nearby structure description for graph assembly"
    )
    to_structure_hint: str | None = Field(
        default=None, description="Nearby structure description for graph assembly"
    )
    notes: str | None = Field(default=None, description="Installation notes")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer")


class Callout(BaseModel):
    """Any callout that is not directly a structure or pipe."""

    callout_type: str = Field(
        description=(
            "Category: edge_of_pavement, detail_reference, cross_reference, "
            "grading_note, installation_note, match_existing, cover_depth, other"
        )
    )
    text: str = Field(description="Full text of the callout")
    station: str | None = Field(default=None, description="Station if applicable")
    offset: str | None = Field(default=None, description="Offset if applicable")
    elevation: float | None = Field(default=None, description="Elevation if applicable")
    reference_sheet: str | None = Field(default=None, description="Referenced sheet number, e.g., SEE SHEET 16")
    reference_detail: str | None = Field(default=None, description="Detail bubble reference, e.g., D7")
    source_text_ids: list[int] = Field(description="text_id(s) from text layer")


class TileExtraction(BaseModel):
    """Complete extraction from a single tile using a flat output structure."""

    tile_id: str = Field(description="Tile identifier, e.g., p14_r0_c2")
    page_number: int
    sheet_type: str = Field(
        description="plan_view, profile_view, detail_sheet, grading, signing_striping, cover, notes, other"
    )
    utility_types_present: list[str] = Field(description="Utility types visible: SD, SS, W, etc.")

    structures: list[Structure] = Field(default_factory=list)
    pipes: list[Pipe] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)

    street_names: list[str] = Field(default_factory=list, description="Street names visible in tile")
    lot_numbers: list[int] = Field(default_factory=list, description="Lot numbers visible in tile")

    extraction_notes: str | None = Field(
        default=None, description="Ambiguities or extraction issues."
    )
