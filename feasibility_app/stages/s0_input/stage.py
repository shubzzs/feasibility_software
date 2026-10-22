"""Stage 0: Input Manager.

Phase-1 implementation validates the five base inputs with Pydantic and blocks
on missing/invalid data. The dynamic per-section YAML form builder is Track D's
next step; the contract (BaseInputs + extra TracedValues) is already fixed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ...core.models import (BaseInputs, Flag, ProjectContext, Provenance,
                            QuantityKind, StageResult, TracedValue)


class InputStage:
    name = "s0_input"

    def run(self, ctx: ProjectContext) -> StageResult:
        if ctx.base is None:
            return StageResult.blocked_on(["No base inputs provided."])
        b = ctx.base
        flags: list[Flag] = []
        errors: list[str] = []

        if not b.cs_number.strip():
            errors.append("CS number is mandatory.")
        if not (Decimal("0") < b.road_width_m <= Decimal("100")):
            errors.append(f"Road width {b.road_width_m} m outside sane range 0-100.")
        if b.plot_area_sqm <= 0:
            errors.append("Plot area must be positive.")

        if errors:
            return StageResult.blocked_on(errors, flags=[
                Flag(code="INPUT_INVALID", severity="critical",
                     message="; ".join(errors), stage=self.name)])

        # echo validated inputs as TracedValues so they carry provenance too
        tvs = [
            TracedValue(name="gross_plot_area_sqm", value=b.plot_area_sqm,
                        unit="sqm", kind=QuantityKind.AREA,
                        provenance=Provenance.USER_INPUT,
                        remark="User-supplied plot area."),
            TracedValue(name="road_width_m", value=b.road_width_m, unit="m",
                        kind=QuantityKind.LENGTH, provenance=Provenance.USER_INPUT,
                        remark="User-supplied abutting road width."),
        ]
        return StageResult.ok(tvs, flags)
