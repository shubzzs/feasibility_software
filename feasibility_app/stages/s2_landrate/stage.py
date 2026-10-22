"""Stage 2: Land Rate Retrieval. Consumes ctx.base.cs_number, emits a TracedValue."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ...core.models import (Flag, Provenance, ProjectContext, QuantityKind,
                            SourceRecord, StageResult, TracedValue)
from .index import LandRateIndex, normalize_cs


class LandRateStage:
    name = "s2_landrate"

    def __init__(self, index: LandRateIndex, report_year: int | None = None,
                 stale_after_years: int = 3):
        self.index = index
        self.report_year = report_year or datetime.now(timezone.utc).year
        self.stale_after_years = stale_after_years

    def run(self, ctx: ProjectContext) -> StageResult:
        assert ctx.base is not None, "Stage 0 must run first"
        raw = ctx.base.cs_number
        norm = normalize_cs(raw)
        matches = self.index.exact(norm)

        # ---- 0 matches => BLOCK ------------------------------------------- #
        if not matches:
            suggestions = self.index.near_matches(norm)
            hint = (" Near-matches (for your reference only, never auto-selected): "
                    + ", ".join(suggestions)) if suggestions else ""
            return StageResult.blocked_on(
                [f"No Ready Reckoner rate found for CS '{raw}' (normalized '{norm}')."
                 + hint],
                flags=[Flag(code="RR_NOT_FOUND", severity="critical",
                            message=f"CS {raw} not in land-rate index.{hint}",
                            stage=self.name)])

        # ---- >1 matches => ask disambiguator ------------------------------- #
        if len(matches) > 1:
            ward = (ctx.extra_inputs.get("ward") or
                    ctx.extra_inputs.get("disambiguate_ward"))
            usage_tv = ctx.extra_inputs.get("usage")
            usage = usage_tv.text if usage_tv is not None else None
            filtered = matches
            if ward:
                filtered = [m for m in matches if (m.ward or "").upper() ==
                            str(ward.value).upper()]
            if usage:
                filtered = [m for m in filtered if m.usage == str(usage)]
            if len(filtered) != 1:
                cands = "; ".join(
                    f"row {m.row_id}: usage={m.usage}, ward={m.ward}, "
                    f"band={m.road_width_band}, year={m.rate_year}"
                    for m in matches)
                return StageResult(status="BLOCKED", blocking_reasons=[
                    f"Multiple RR rows match CS '{raw}'. Provide ward/usage to "
                    f"disambiguate. Candidates: {cands}"],
                    flags=[Flag(code="RR_AMBIGUOUS", severity="critical",
                                message="Ambiguous CS match; manual disambiguation "
                                        "required.", stage=self.name)])
            matches = filtered

        row = matches[0]
        flags: list[Flag] = []

        # ---- validation ---------------------------------------------------- #
        if row.rate_inr_per_sqm <= 0:
            return StageResult.blocked_on(
                [f"Non-positive RR rate for CS {raw}: {row.rate_inr_per_sqm}"])
        if not Decimal("100") <= row.rate_inr_per_sqm <= Decimal("10000000"):
            flags.append(Flag(code="RR_RATE_IMPLAUSIBLE", severity="warning",
                              message=f"Rate {row.rate_inr_per_sqm} INR/sqm outside "
                                      "plausible range -- verify manually.",
                              stage=self.name))
        status = "VERIFIED"
        if row.rate_year and (self.report_year - row.rate_year) > self.stale_after_years:
            status = "FLAGGED"
            flags.append(Flag(code="RR_STALE_RATE", severity="warning",
                              message=f"RR rate effective {row.rate_year}; report year "
                                      f"{self.report_year}. Possibly stale.",
                              stage=self.name))

        tv = TracedValue(
            name="land_rate_inr_per_sqm", value=row.rate_inr_per_sqm,
            unit="INR/sqm", kind=QuantityKind.MONEY,
            provenance=Provenance.DATASET_LOOKUP, status=status,
            source_record=SourceRecord(
                dataset="ready_reckoner", file_name=row.source_file,
                row_id=row.row_id, rate_year=row.rate_year,
                raw_key=row.cs_raw, normalized_key=row.cs_norm,
                extra={"usage": row.usage, "ward": row.ward}),
            remark=(f"Ready Reckoner rate, exact normalized CS match "
                    f"('{raw}' -> '{norm}'), usage={row.usage}, year={row.rate_year}."))
        return StageResult.ok([tv], flags)
