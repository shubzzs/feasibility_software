"""Hand-encoded DCPR 2034 TABLE 12 -- Floor Space Indices in Residential,
Commercial and Industrial Zones.

Source: `DCPR 2034.pdf`, PART V (FLOOR SPACE INDEX), Regulation 30(A), TABLE 12,
printed page 83 = PDF page 126. Verified by reading the extracted text of that
page directly (pdf_hash b417e528b717c76f).

WHY HAND-ENCODED: automated table extraction on this PDF is unreliable (the
table's cells come out as a flat column stream, exactly as seen when ingesting
p.126). Per the architecture, high-value tables are hand-encoded and reviewed
once; the LLM may only cross-check them. This file IS the reviewed source of
truth for FSI. Domain-expert sign-off pending -> status MANUAL_REVIEW until then.

Rows transcribed from the page text (Island City block shown here):
  <9m            basic 1.33  premium -    TDR -     permissible 1.33
  >=9 <12        basic 1.33  premium 0.5  TDR 0.17  permissible 2.0
  >=12 <18       basic 1.33  premium 0.62 TDR 0.45  permissible 2.4
  >=18 <27       basic 1.33  premium 0.73 TDR 0.64  permissible 2.7
  >=27           basic 1.33  premium 0.84 TDR 0.83  permissible 3.0
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ...core.models import Ref
from .page_map import printed_page

PDF_HASH = "b417e528b717c76f"   # sha256[:16] of the uploaded DCPR 2034.pdf
DOC_VERSION = "DCPR-2034 (uploaded build)"


def _ref(pdf_p: int, quote: str | None = None) -> Ref:
    return Ref(doc="DCPR 2034", reg_no="30", table_no="TABLE 12",
               clause_path="PART V > Reg 30 > (A) Floor Space Indices",
               pdf_page=pdf_p, printed_page=printed_page(pdf_p),
               doc_version=DOC_VERSION, pdf_hash=PDF_HASH, quote=quote)


@dataclass(frozen=True)
class FSIBand:
    min_width_m: Decimal          # inclusive
    max_width_m: Decimal | None   # exclusive, None = open-ended
    zonal_basic_fsi: Decimal
    additional_premium_fsi: Decimal | None
    admissible_tdr_fsi: Decimal | None
    permissible_fsi: Decimal
    zone: str
    area_label: str

    def applies(self, road_width_m: Decimal) -> bool:
        if road_width_m < self.min_width_m:
            return False
        return self.max_width_m is None or road_width_m < self.max_width_m


# Island City / Residential-Commercial, straight from TABLE 12 rows 1-5.
ISLAND_CITY_RES_COM: list[FSIBand] = [
    FSIBand(Decimal("0"), Decimal("9"), Decimal("1.33"), None, None,
            Decimal("1.33"), "Island City", "Residential/Commercial"),
    FSIBand(Decimal("9"), Decimal("12"), Decimal("1.33"), Decimal("0.5"),
            Decimal("0.17"), Decimal("2.0"), "Island City",
            "Residential/Commercial"),
    FSIBand(Decimal("12"), Decimal("18"), Decimal("1.33"), Decimal("0.62"),
            Decimal("0.45"), Decimal("2.4"), "Island City",
            "Residential/Commercial"),
    FSIBand(Decimal("18"), Decimal("27"), Decimal("1.33"), Decimal("0.73"),
            Decimal("0.64"), Decimal("2.7"), "Island City",
            "Residential/Commercial"),
    FSIBand(Decimal("27"), None, Decimal("1.33"), Decimal("0.84"),
            Decimal("0.83"), Decimal("3.0"), "Island City",
            "Residential/Commercial"),
]

ALL_BANDS: dict[str, list[FSIBand]] = {
    "island_city_res_com": ISLAND_CITY_RES_COM,
    # TODO(domain expert): remaining TABLE 12 blocks (other zones/cities) live on
    # printed pages 83-85 (pdf 126-128); encode after review.
}


def lookup_fsi(band_key: str, road_width_m: Decimal) -> FSIBand | None:
    for b in ALL_BANDS.get(band_key, []):
        if b.applies(road_width_m):
            return b
    return None


TABLE12_REF = _ref(126, quote="Floor Space Indices in Residential, Commercial "
                              "and Industrial Zones")
