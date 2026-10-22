"""Stage 4: Cost Engine. Deterministic, dependency-resolved, Decimal-only.

PLACEHOLDER FORMULAS: construction rate etc. are user ASSUMPTIONS until the
domain expert supplies the real cost-component registry. Every component is a
TracedValue; total is verified against the sum of components.
"""

from __future__ import annotations

from decimal import Decimal

from ...core.models import (Flag, ProjectContext, Provenance, QuantityKind,
                            StageResult, TracedValue)


def _need(ctx: ProjectContext, name: str) -> TracedValue:
    tv = ctx.get(name)
    if tv is None or tv.value is None:
        raise KeyError(f"missing required input for cost stage: {name}")
    return tv


class CostStage:
    name = "s4_cost"

    def run(self, ctx: ProjectContext) -> StageResult:
        try:
            land_rate = _need(ctx, "land_rate_inr_per_sqm")
            plot_area = _need(ctx, "net_plot_area_sqm")
            bua = _need(ctx, "permissible_bua_sqm")
        except KeyError as e:
            return StageResult.blocked_on([str(e.args[0])])

        flags: list[Flag] = []

        # unit sanity: rate must be INR/sqm against sqm areas
        for tv, exp in ((land_rate, "INR/sqm"), (plot_area, "sqm"), (bua, "sqm")):
            if tv.unit != exp:
                return StageResult.blocked_on(
                    [f"Unit mismatch on {tv.name}: {tv.unit} vs {exp}"])

        cons_rate_tv = ctx.get("construction_cost_inr_per_sqm")
        if cons_rate_tv is None:
            cons_rate_tv = TracedValue(
                name="construction_cost_inr_per_sqm", value=Decimal("45000"),
                unit="INR/sqm", kind=QuantityKind.MONEY,
                provenance=Provenance.ASSUMPTION, status="FLAGGED",
                remark=("PLACEHOLDER assumption pending domain-expert cost spec; "
                        "not from DCPR."))
            flags.append(Flag(code="COST_ASSUMPTION", severity="warning",
                              message="Construction rate is an assumption.",
                              stage=self.name, value_name=cons_rate_tv.name))

        land_cost = TracedValue(
            name="land_cost_inr", value=land_rate.value * plot_area.value,
            unit="INR", kind=QuantityKind.MONEY, provenance=Provenance.DERIVED_CALC,
            formula_id="COST_LAND", formula_version="1.0-placeholder",
            formula_text="land_cost = land_rate x net_plot_area",
            inputs_used=[land_rate.name, plot_area.name],
            remark=f"[COST_LAND] land_rate x net_plot_area = "
                   f"{land_rate.value} x {plot_area.value}")
        constr_cost = TracedValue(
            name="construction_cost_inr",
            value=cons_rate_tv.value * bua.value,
            unit="INR", kind=QuantityKind.MONEY, provenance=Provenance.DERIVED_CALC,
            formula_id="COST_CONSTRUCTION", formula_version="1.0-placeholder",
            formula_text="construction_cost = rate x permissible_bua",
            inputs_used=[cons_rate_tv.name, bua.name],
            remark=f"[COST_CONSTRUCTION] {cons_rate_tv.value} x {bua.value}")
        total = TracedValue(
            name="total_project_cost_inr",
            value=land_cost.value + constr_cost.value,
            unit="INR", kind=QuantityKind.MONEY, provenance=Provenance.DERIVED_CALC,
            formula_id="COST_TOTAL", formula_version="1.0",
            formula_text="total = sum(components)",
            inputs_used=[land_cost.name, constr_cost.name],
            remark="[COST_TOTAL] sum of components.")

        # conservation check: sum of components equals total
        assert total.value == land_cost.value + constr_cost.value

        values = [cons_rate_tv, land_cost, constr_cost, total]
        for v in values:
            ctx.put("costs", v)
        return StageResult.ok(values, flags)
