"""Land-rate engine (Stage 2): CS-number normalization + indexed lookup.

Rules:
  * exact match only; fuzzy is used for SUGGESTIONS, never to auto-select;
  * 0 matches => BLOCKED (+ near-matches); 1 => accept; >1 => ask disambiguator.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def normalize_cs(raw: str) -> str:
    """Normalize a Cadastral Survey number to one canonical form.

    Handles the awkward real-world variants we saw in the RR tables:
      "CS No. 123/A"  -> "123A"
      "C.S.No.123 A"  -> "123A"
      "123~"          -> "123"   (sub-division marker dropped by default)
      "O.S./C.S. 45"  -> "45"
    """
    s = raw.upper()
    s = re.sub(r"\b(?:C\.?\s*S\.?\s*NO\.?|O\.?\s*S\.?|CS)\b", "", s)  # drop prefixes
    s = re.sub(r"[^0-9A-Z/~\-]", "", s)                              # keep core chars
    s = s.replace("~", "").replace("-", "")                          # sub-division marks
    s = re.sub(r"/", "", s)                                           # separators
    return s.strip()


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
@dataclass
class LandRateRow:
    cs_raw: str
    cs_norm: str
    rate_inr_per_sqm: Decimal
    usage: str = "ALL"
    ward: str | None = None
    village: str | None = None
    road_width_band: str | None = None
    rate_year: int | None = None
    source_file: str | None = None
    row_id: str | None = None


class LandRateIndex:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._create()

    def _create(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS rates (
                 cs_raw TEXT, cs_norm TEXT, rate REAL, usage TEXT,
                 ward TEXT, village TEXT, road_band TEXT, year INTEGER,
                 file TEXT, row_id TEXT)""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cs ON rates(cs_norm)")
        self._conn.commit()

    def upsert_many(self, rows: list[LandRateRow]) -> int:
        self._conn.execute("DELETE FROM rates")  # rebuild-style load
        self._conn.executemany(
            "INSERT INTO rates VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(r.cs_raw, r.cs_norm, float(r.rate_inr_per_sqm), r.usage, r.ward,
              r.village, r.road_width_band, r.rate_year, r.source_file, r.row_id)
             for r in rows])
        self._conn.commit()
        return len(rows)

    def exact(self, cs_norm: str, usage: str | None = None) -> list[LandRateRow]:
        q, p = "SELECT * FROM rates WHERE cs_norm=?", [cs_norm]
        if usage:
            q += " AND usage=?"
            p.append(usage)
        return [self._row(r) for r in self._conn.execute(q, p)]

    def near_matches(self, cs_norm: str, limit: int = 5) -> list[str]:
        """Suggestions ONLY. Prefix containment on digits -- never auto-picked."""
        digits = re.sub(r"\D", "", cs_norm)
        if not digits:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for (raw, norm) in self._conn.execute("SELECT cs_raw, cs_norm FROM rates"):
            if norm == cs_norm or norm in seen:
                continue
            if digits and digits in norm:
                seen.add(norm)
                out.append(raw)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _row(r: tuple) -> LandRateRow:
        return LandRateRow(cs_raw=r[0], cs_norm=r[1],
                           rate_inr_per_sqm=Decimal(str(r[2])), usage=r[3],
                           ward=r[4], village=r[5], road_width_band=r[6],
                           rate_year=r[7], source_file=r[8], row_id=r[9])

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0]
