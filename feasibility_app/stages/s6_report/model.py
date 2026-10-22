"""Stage 6: Report Consolidation. Assembles the structured ReportModel from the
ProjectContext -- every value keeps its provenance, formula and DCPR refs.

The AI-written executive narrative is Phase 4; here we generate deterministic
template text so a complete report exists without any LLM.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from ...core.models import Flag, ProjectContext, Provenance, StageResult, TracedValue


class TableRow(BaseModel):
    parameter: str
    formula: str | None
    value: str
    unit: str
    dcpr_ref: str | None
    remarks: str
    provenance: str
    status: str


class ComplianceRow(BaseModel):
    reg_no: str
    subsection: str | None
    explanation: str
    effect_on_project: str
    influenced_calculations: list[str]
    page_ref: str | None


class ReportModel(BaseModel):
    run_id: str
    project_summary: dict[str, Any]
    regulatory_compliance: list[ComplianceRow] = []
    area_statement: list[TableRow] = []
    cost_statement: list[TableRow] = []
    revenue_statement: list[TableRow] = []
    financial_summary: list[TableRow] = []
    assumptions: list[str] = []
    outstanding_verifications: list[str] = []
    flags: list[str] = []
    source_legend: dict[str, list[str]] = {}   # provenance -> value names
    doc_version: str | None = None
    pdf_hash: str | None = None


def _fmt(tv: TracedValue) -> str:
    if tv.value is None:
        return tv.text or "-"
    return f"{tv.value:,}"


def _row(tv: TracedValue) -> TableRow:
    return TableRow(
        parameter=tv.name, formula=tv.formula_text, value=_fmt(tv),
        unit=tv.unit, dcpr_ref="; ".join(tv.ref_labels()) or None,
        remarks=tv.remark, provenance=tv.provenance.value, status=tv.status)


def build_report(ctx: ProjectContext) -> ReportModel:
    b = ctx.base
    summary = {
        "cs_number": b.cs_number if b else "-",
        "address": b.address if b else "-",
        "plot_area_sqm": str(b.plot_area_sqm) if b else "-",
        "road_width_m": str(b.road_width_m) if b else "-",
        "section_id": b.section_id if b else "-",
        "run_id": ctx.run_id,
    }

    compliance: list[ComplianceRow] = []
    if ctx.regulatory:
        for p in ctx.regulatory.provisions:
            influenced = [t.name for t in list(ctx.areas.values()) +
                          list(ctx.costs.values()) + list(ctx.revenues.values())
                          if any(r.label() in t.ref_labels() for r in p.refs)]
            compliance.append(ComplianceRow(
                reg_no=p.reg_no, subsection=p.clause_path,
                explanation=p.body,
                effect_on_project=(f"Permissible limit: {p.numeric_limit} "
                                   f"({p.limit_type})" if p.numeric_limit else
                                   "Qualitative provision"),
                influenced_calculations=influenced,
                page_ref="; ".join(r.label() for r in p.refs) or None))

    legend: dict[str, list[str]] = {}
    all_values = (list(ctx.derived.values()) + list(ctx.extra_inputs.values()) +
                  ([ctx.land_rate] if ctx.land_rate else []) +
                  list(ctx.areas.values()) + list(ctx.costs.values()) +
                  list(ctx.revenues.values()) + list(ctx.financials.values()))
    for tv in all_values:
        legend.setdefault(tv.provenance.value, []).append(tv.name)

    assumptions = [tv.remark for tv in all_values
                   if tv.provenance == Provenance.ASSUMPTION]
    outstanding = (ctx.regulatory.outstanding_verification
                   if ctx.regulatory else [])
    return ReportModel(
        run_id=ctx.run_id, project_summary=summary,
        regulatory_compliance=compliance,
        area_statement=[_row(t) for t in ctx.areas.values()],
        cost_statement=[_row(t) for t in ctx.costs.values()],
        revenue_statement=[_row(t) for t in ctx.revenues.values()],
        financial_summary=[_row(t) for t in ctx.financials.values()],
        assumptions=assumptions, outstanding_verifications=outstanding,
        flags=[f"[{f.severity}] {f.code}: {f.message}" for f in ctx.flags],
        source_legend=legend,
        doc_version=ctx.regulatory.doc_version if ctx.regulatory else None,
        pdf_hash=ctx.regulatory.pdf_hash if ctx.regulatory else None)


class ReportStage:
    name = "s6_report"

    def __init__(self):
        self.report_model: ReportModel | None = None

    def run(self, ctx: ProjectContext) -> StageResult:
        self.report_model = build_report(ctx)
        return StageResult.ok([])
