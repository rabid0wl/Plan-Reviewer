"""CLI argument helpers."""

from __future__ import annotations


def parse_pages_argument(
    pages_arg: str | None,
    *,
    total_pages: int | None = None,
) -> list[int] | None:
    """
    Parse a pages selector string into a sorted list of 1-indexed page numbers.

    Supported formats:
    - "14"
    - "1,4,7"
    - "1-3,7,10-12"
    """
    if not pages_arg:
        return None

    pages: set[int] = set()
    for token in pages_arg.split(","):
        token = token.strip()
        if not token:
            continue

        if "-" in token:
            start_raw, end_raw = token.split("-", maxsplit=1)
            start = int(start_raw.strip())
            end = int(end_raw.strip())
            if end < start:
                raise ValueError(f"Invalid page range '{token}': end < start")
            pages.update(range(start, end + 1))
            continue

        pages.add(int(token))

    if not pages:
        return None

    parsed = sorted(pages)
    if parsed[0] < 1:
        raise ValueError("Page numbers must be >= 1")
    if total_pages is not None and parsed[-1] > total_pages:
        raise ValueError(
            f"Requested page {parsed[-1]} exceeds PDF page count ({total_pages})"
        )
    return parsed

