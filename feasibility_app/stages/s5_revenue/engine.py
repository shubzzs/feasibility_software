"""Stage 5: Revenue + Financial Analysis. Pure Decimal functions, zero-division
guards, division-by-zero -> BLOCKED (not silent inf).

PLACEHOLDER: saleable-area factor and selling rate are assumptions until the
domain expert's revenue spec arrives.
"""

from __future__ import annotations

from decimal import Decimal

from ...core.models import (Flag, ProjectContext, Provenance, QuantityKind,
                            StageResult, TracedValue)


def _tv(name, value, unit, kind, prov, formula_id, formula_text, inputs,
        remark, status="VERIFIED"):
    return TracedValue(
        name=name, value=value, unit=unit, kind=kind, provenance=prov,
        formula_id=formula_id, formula_version="1.0-placeholder",
        formula_text=formula_text, inputs_used=list(inputs), status=status,
        remark=remark)


class RevenueStage:
    name = "s5_revenue"

    def run(self, ctx: ProjectContext) -> StageResult:
        bua = ctx.get("permissible_bua_sqm")
        total_cost = ctx.get("total_project_cost_inr")
        if bua is None or total_cost is None:
            return StageResult.blocked_on(["Missing BUA or total cost."])

        flags: list[Flag] = []
        conv = ctx.get("saleable_conversion_factor")
        if conv is None:
            conv = _tv("saleable_conversion_factor", Decimal("1.25"), "ratio",
                       QuantityKind.RATIO, Provenance.ASSUMPTION, "REV_CONV",
                       "saleable = bua x conversion factor", [],
                       "PLACEHOLDER assumption; carpet-to-saleable factor.")
            flags.append(Flag(code="REV_ASSUMPTION", severity="warning",
                              message="Saleable conversion factor is an assumption.",
                              stage=self.name))
        rate = ctx.get("selling_rate_inr_per_sqm")
        if rate is None:
            rate = _tv("selling_rate_inr_per_sqm", Decimal("180000"), "INR/sqm",
                       QuantityKind.MONEY, Provenance.ASSUMPTION, "REV_RATE",
                       "user selling rate", [],
                       "PLACEHOLDER assumption; not from DCPR/RR.")
            flags.append(Flag(code="REV_ASSUMPTION", severity="warning",
                              message="Selling rate is an assumption.",
                              stage=self.name))

        saleable = _tv("saleable_area_sqm", bua.value * conv.value, "sqm",
                       QuantityKind.AREA, Provenance.DERIVED_CALC, "REV_SALEABLE",
                       "saleable = permissible_bua x conversion_factor",
                       [bua.name, conv.name],
                       "[REV_SALEABLE] computed.")
        revenue = _tv("total_revenue_inr", saleable.value * rate.value, "INR",
                      QuantityKind.MONEY, Provenance.DERIVED_CALC, "REV_TOTAL",
                      "revenue = saleable_area x selling_rate",
                      [saleable.name, rate.name], "[REV_TOTAL] computed.")

        # financial indicators with zero guards
        profit = revenue.value - total_cost.value
        margin = (profit / revenue.value * 100) if revenue.value else None
        roi = (profit / total_cost.value * 100) if total_cost.value else None
        if margin is None or roi is None:
            return StageResult.blocked_on(["Division by zero in financials."])

        fin = [
            _tv("net_profit_inr", profit, "INR", QuantityKind.MONEY,
                Provenance.DERIVED_CALC, "FIN_PROFIT",
                "profit = revenue - cost", [revenue.name, total_cost.name],
                "[FIN_PROFIT] computed."),
            _tv("profit_margin_pct", margin, "%", QuantityKind.RATIO,
                Provenance.DERIVED_CALC, "FIN_MARGIN",
                "margin = profit / revenue x 100", [revenue.name],
                "[FIN_MARGIN] computed."),
            _tv("roi_pct", roi, "%", QuantityKind.RATIO,
                Provenance.DERIVED_CALC, "FIN_ROI",
                "roi = profit / cost x 100", [revenue.name, total_cost.name],
                "[FIN_ROI] computed."),
        ]
        min_margin = Decimal("15")
        if margin < min_margin:
            flags.append(Flag(code="VIABILITY_LOW_MARGIN", severity="warning",
                              message=f"Margin {margin:.2f}% below configurable "
                                      f"minimum {min_margin}%.",
                              stage=self.name, value_name="profit_margin_pct"))

        values = [conv, rate, saleable, revenue] + fin
        for v in values[:4]:
            ctx.put("revenues", v)
        for v in fin:
            ctx.put("financials", v)
        return StageResult.ok(values, flags)
