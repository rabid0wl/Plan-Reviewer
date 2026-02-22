"""Unicode normalization helpers for civil engineering text extraction."""

from __future__ import annotations

UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2205": "DIA",  # Empty set used as diameter symbol in some drawings
    "\u00d8": "DIA",  # O with stroke, often used as diameter symbol
    "\u2300": "DIA",  # Diameter sign
    "\u00b0": "deg",  # Degree symbol
    "\u2032": "'",  # Prime (feet)
    "\u2033": '"',  # Double prime (inches)
    "\u00b1": "+/-",  # Plus-minus
}


def clean_unicode(text: str) -> str:
    """Replace problematic unicode characters with ASCII-safe equivalents."""
    for char, replacement in UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text

