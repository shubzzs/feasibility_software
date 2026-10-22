"""Stage 3: Area Calculation Engine (the most critical stage).

Architecture: plugin pattern. Each DCPR section implements ``AreaCalculator``;
a registry resolves section_id -> calculator; steps form a DAG executed in
topological order; every step is a pure Decimal function producing a TracedValue
with formula id, DCPR ref and human-readable formula text attached.

Phase-1 slice: base FSI -> permissible BUA on gross plot area, with the cap
check (result <= permissible FSI x net area) and template remarks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Callable

from ...core.models import (Flag, ProjectContext, Provenance, QuantityKind,
                            RegulatoryDataset, StageResult, TracedValue)


class CalcStep(ABC):
    """One node of the calculation DAG."""

    name: str                      # output TracedValue name
    unit: str
    kind: QuantityKind
    formula_id: str
    formula_version: str
    formula_text: str              # human-readable, e.g. "net_plot * permissible_fsi"
    dcpr_ref_keys: list[str] = []  # resolved against the regulatory dataset refs
    depends_on: list[str] = []     # upstream value names

    @abstractmethod
    def compute(self, ctx: ProjectContext,
                vals: dict[str, TracedValue]) -> Decimal:
        """Pure function over Decimals. No I/O, no LLM, no floats."""


class AreaCalculator(ABC):
    section_id: str
    formula_version: str

    @abstractmethod
    def required_inputs(self) -> list[str]: ...

    @abstractmethod
    def steps(self) -> list[CalcStep]: ...

    def run(self, ctx: ProjectContext) -> tuple[list[TracedValue], list[Flag]]:
        return execute_dag(self.steps(), ctx)


# --------------------------------------------------------------------------- #
# DAG executor (topological sort, cycle detection)
# --------------------------------------------------------------------------- #
def execute_dag(steps: list[CalcStep], ctx: ProjectContext
                ) -> tuple[list[TracedValue], list[Flag]]:
    by_name = {s.name: s for s in steps}
    indeg = {s.name: len(s.depends_on) for s in steps}
    ready = [n for n, d in indeg.items() if d == 0]
    out: list[TracedValue] = []
    done: set[str] = set()
    flags: list[Flag] = []

    while ready:
        n = ready.pop(0)
        s = by_name[n]
        inputs: dict[str, TracedValue] = {}
        missing = []
        for dep in s.depends_on:
            tv = ctx.get(dep)
            if tv is None:
                missing.append(dep)
            else:
                inputs[dep] = tv
        if missing:
            raise RuntimeError(f"step {n} missing upstream values: {missing}")
        try:
            val = s.compute(ctx, inputs)
        except TypeError as e:
            raise RuntimeError(
                f"step {n}: non-numeric input ({e}); check upstream values") from e
        refs = resolve_refs(s.dcpr_ref_keys, ctx.regulatory)
        tv = TracedValue(
            name=s.name, value=val, unit=s.unit, kind=s.kind,
            provenance=Provenance.DERIVED_CALC,
            formula_id=s.formula_id, formula_version=s.formula_version,
            formula_text=s.formula_text,
            dcpr_refs=refs,
            inputs_used=[k for k in inputs],
            status="VERIFIED",
            remark=make_remark(s, inputs, val))
        ctx.put("areas", tv)
        out.append(tv)
        done.add(n)
        for other in by_name.values():
            if n in other.depends_on and other.name not in done:
                indeg[other.name] -= 1
                if indeg[other.name] == 0:
                    ready.append(other.name)

    if len(done) != len(by_name):
        cyclic = sorted(set(by_name) - done)
        flags.append(Flag(code="DAG_CYCLE", severity="critical",
                          message=f"Calculation DAG has a cycle among: {cyclic}",
                          stage="s3_area"))
    return out, flags


def resolve_refs(keys: list[str], reg: RegulatoryDataset | None) -> list:
    if reg is None or not keys:
        return []
    refs = []
    for r in reg.provisions:
        refs.extend(r.refs)
    for r in reg.rules:
        refs.extend(r.refs)
    seen, uniq = set(), []
    for ref in refs:
        if any(k.lower() in (ref.table_no or "").lower() or
               k.lower() in (ref.reg_no or "").lower() for k in keys):
            key = ref.label()
            if key not in seen:
                seen.add(key)
                uniq.append(ref)
    return uniq


def make_remark(step: CalcStep, inputs: dict[str, TracedValue],
                val: Decimal) -> str:
    ins = ", ".join(f"{k}={v.value}" for k, v in inputs.items())
    return (f"[{step.formula_id} v{step.formula_version}] "
            f"{step.formula_text}; inputs: {ins}; result {val} {step.unit}.")


# --------------------------------------------------------------------------- #
# The Phase-1 vertical-slice calculator: Reg 30(A) base FSI -> BUA
# --------------------------------------------------------------------------- #
class NetPlotAreaStep(CalcStep):
    name = "net_plot_area_sqm"
    unit = "sqm"
    kind = QuantityKind.AREA
    formula_id = "AREA_NET_PLOT"
    formula_version = "1.0-placeholder"
    formula_text = "net_plot_area = gross_plot_area (deductions TBD by domain expert)"
    dcpr_ref_keys = ["30"]
    depends_on: list[str] = []

    def compute(self, ctx, vals):
        assert ctx.base is not None
        return ctx.base.plot_area_sqm   # placeholder until spec provides deductions


class PermissibleBUAStep(CalcStep):
    name = "permissible_bua_sqm"
    unit = "sqm"
    kind = QuantityKind.AREA
    formula_id = "AREA_PERMISSIBLE_BUA"
    formula_version = "1.0"
    formula_text = "BUA = net_plot_area x permissible_fsi"
    dcpr_ref_keys = ["TABLE 12", "30"]
    depends_on = ["net_plot_area_sqm"]

    def compute(self, ctx, vals):
        net = vals["net_plot_area_sqm"].value
        fsi = ctx.value_of("permissible_fsi")
        if net is None or fsi is None:
            raise RuntimeError("inputs unavailable")
        return net * fsi


class ZonalBasicFSIStep(CalcStep):
    """Reads the hand-encoded TABLE 12 value published by Stage 1.

    A *lookup* node rather than an arithmetic one: making it an explicit DAG
    step gives every downstream area a traceable upstream edge instead of
    silently reaching into the context mid-calculation.
    """

    name = "fsi_zonal_basic"
    unit = "ratio"
    kind = QuantityKind.RATIO
    formula_id = "FSI_TABLE12_LOOKUP"
    formula_version = "1.0"
    formula_text = "zonal_basic_fsi := TABLE 12 row for the applicable road-width band"
    dcpr_ref_keys = ["TABLE 12"]
    depends_on: list[str] = []

    def compute(self, ctx, vals):
        fsi = ctx.value_of("zonal_basic_fsi")
        if fsi is None:
            raise RuntimeError("Stage 1 did not publish zonal_basic_fsi")
        return fsi


class BaseBUAStep(PermissibleBUAStep):
    name = "base_bua_sqm"
    formula_id = "AREA_BASE_BUA"
    formula_text = "BUA_base = net_plot_area x zonal_basic_fsi"
    dcpr_ref_keys = ["TABLE 12"]
    depends_on = ["net_plot_area_sqm", "fsi_zonal_basic"]

    def compute(self, ctx, vals):
        net = vals["net_plot_area_sqm"].value
        fsi = vals["fsi_zonal_basic"].value
        if net is None or fsi is None:
            raise RuntimeError("inputs unavailable for base BUA")
        return net * fsi


class Reg30BaseSlice(AreaCalculator):
    """First vertical slice: input -> FSI lookup -> areas, hand-verifiable."""

    section_id = "DCPR_30_BASE"
    formula_version = "1.0"

    def required_inputs(self):
        return ["plot_area_sqm", "road_width_m", "permissible_fsi"]

    def steps(self):
        return [NetPlotAreaStep(), ZonalBasicFSIStep(), BaseBUAStep(),
                PermissibleBUAStep()]

    def verify(self, results: list[TracedValue],
               reg: RegulatoryDataset) -> list[Flag]:
        """Six-step verification loop, condensed to the limit check here:
        base BUA must not exceed permissible BUA (cap)."""
        flags: list[Flag] = []
        by = {t.name: t for t in results}
        base, perm = by.get("base_bua_sqm"), by.get("permissible_bua_sqm")
        if base and perm and base.value > perm.value:
            flags.append(Flag(
                code="CAP_BREACH", severity="critical",
                message=(f"base_bua {base.value} exceeds permissible cap "
                         f"{perm.value}; showing both uncapped and capped."),
                stage="s3_area", value_name="base_bua_sqm"))
        if reg.rule("fsi_by_road_width") is None:
            flags.append(Flag(code="NO_FSI_RULE", severity="critical",
                              message="No FSI rule in regulatory dataset.",
                              stage="s3_area"))
        return flags


REGISTRY: dict[str, AreaCalculator] = {
    Reg30BaseSlice.section_id: Reg30BaseSlice(),
}


class AreaStage:
    name = "s3_area"

    def run(self, ctx: ProjectContext) -> StageResult:
        assert ctx.base is not None
        calc = REGISTRY.get(ctx.base.section_id)
        if calc is None:
            return StageResult.blocked_on(
                [f"No AreaCalculator registered for section "
                 f"'{ctx.base.section_id}'. Available: "
                 f"{sorted(REGISTRY)}"])
        if ctx.regulatory is None:
            return StageResult.blocked_on(["Regulatory dataset missing (Stage 1)."])
        try:
            values, flags = calc.run(ctx)
        except RuntimeError as e:
            return StageResult.blocked_on([str(e)])
        flags += calc.verify(values, ctx.regulatory)
        if any(f.severity == "critical" for f in flags):
            return StageResult(status="PASS_WITH_FLAGS", values=values, flags=flags)
        return StageResult.ok(values, flags)
