"""DCPR PDF page-offset map.

Measured on the uploaded `DCPR 2034.pdf`: printed page footers differ from the
physical PDF page index by a constant offset of -43 (printed = pdf - 43) for the
main body. We keep it as data, not a magic number in code (non-negotiable rule #2).
If a future PDF has a non-constant offset, replace this with a lookup table built
by scanning footers page-by-page at ingest time.
"""

from __future__ import annotations

PRINTED_TO_PDF_OFFSET = 43


def printed_page(pdf_page: int) -> int | None:
    p = pdf_page - PRINTED_TO_PDF_OFFSET
    return p if p >= 1 else None


def pdf_page(printed: int) -> int:
    return printed + PRINTED_TO_PDF_OFFSET
