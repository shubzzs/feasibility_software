"""Stage orchestration + gating (state machine).

Every stage implements ``Stage.run(ctx) -> StageResult``. The runner enforces
the gate: a BLOCKED result stops all downstream stages so we never emit a
confident-looking report built on missing or ambiguous inputs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from .audit import AuditLogger
from .models import Flag, ProjectContext, StageResult


@runtime_checkable
class Stage(Protocol):
    name: str

    def run(self, ctx: ProjectContext) -> StageResult:  # pragma: no cover
        ...


class PipelineReport(BaseModel):
    completed: list[str]
    blocked_at: str | None = None
    reasons: list[str] = []
    flags: list[Flag] = []

    @property
    def ok(self) -> bool:
        return self.blocked_at is None


class Pipeline:
    def __init__(self, stages: list[Stage], audit: AuditLogger):
        self.stages = stages
        self.audit = audit

    def run(self, ctx: ProjectContext) -> tuple[ProjectContext, PipelineReport]:
        digest = self.audit.inputs_snapshot(ctx.model_dump(mode="json"))
        completed: list[str] = []
        all_flags: list[Flag] = []

        for stage in self.stages:
            try:
                result = stage.run(ctx)
            except Exception as exc:  # unexpected failure => BLOCKED, not silent pass
                reason = f"{stage.name} raised {type(exc).__name__}: {exc}"
                self.audit.log("stage_error", stage=stage.name, error=str(exc))
                return ctx, PipelineReport(
                    completed=completed, blocked_at=stage.name,
                    reasons=[reason], flags=all_flags)

            # write values onto the shared context; the stage declares its own
            # bucket, falling back to the map below for older stages
            bucket = result.bucket or STAGE_OUTPUT_BUCKET.get(stage.name)
            if bucket:
                for tv in result.values:
                    ctx.put(bucket, tv)

            for f in result.flags:
                ctx.add_flag(f)
            all_flags.extend(result.flags)

            self.audit.stage_result(
                stage=stage.name, status=result.status,
                values=[v.model_dump(mode="json") for v in result.values],
                flags=[f.model_dump() for f in result.flags])
            ctx.stage_history.append({
                "stage": stage.name, "status": result.status,
                "input_hash": digest})
            completed.append(stage.name)

            if result.status == "BLOCKED":
                return ctx, PipelineReport(
                    completed=completed, blocked_at=stage.name,
                    reasons=result.blocking_reasons, flags=all_flags)

        return ctx, PipelineReport(completed=completed, flags=all_flags)


# Which context bucket each stage writes its TracedValues into. A stage may
# override this by setting StageResult.bucket on its own result.
STAGE_OUTPUT_BUCKET = {
    "s0_input": "derived",
    "s1_dcpr": "regulatory_inputs",   # FSI bands, premiums, TDR limits
    "s2_landrate": "land_rate",
    "s3_area": "areas",
    "s4_cost": "costs",
    "s5_revenue": "revenues",
    "s6_report": None,        # consumes everything
    "s7_pdf": None,           # consumes everything
}
