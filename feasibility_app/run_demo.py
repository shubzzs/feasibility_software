"""End-to-end runner: builds the pipeline and runs a demo project.

    make run   /   python -m feasibility_app.run_demo
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from .core.audit import AuditLogger
from .core.models import BaseInputs, ProjectContext
from .core.pipeline import Pipeline
from .stages.s0_input.stage import InputStage
from .stages.s1_dcpr.stage import DCPRStage
from .stages.s2_landrate.index import LandRateIndex, LandRateRow
from .stages.s2_landrate.stage import LandRateStage
from .stages.s3_area.engine import AreaStage
from .stages.s4_cost.engine import CostStage
from .stages.s5_revenue.engine import RevenueStage
from .stages.s6_report.model import ReportStage
from .stages.s7_pdf.renderer import PdfStage


def build_index(db_path="data/processed/rr_rates.db") -> LandRateIndex:
    idx = LandRateIndex(db_path)
    if idx.count() == 0:
        # Seed sample rows (replace with real RR extraction output later).
        idx.upsert_many([
            LandRateRow("CS No. 123/A", "123A", Decimal("185000"), "RESIDENTIAL",
                        ward="W-4", rate_year=2024, source_file="RR_values_merged.pdf",
                        row_id="r1"),
            LandRateRow("C.S.123 A", "123A", Decimal("190000"), "COMMERCIAL",
                        ward="W-4", rate_year=2024, source_file="RR_values_merged.pdf",
                        row_id="r2"),
            LandRateRow("O.S./C.S. 45", "45", Decimal("95000"), "RESIDENTIAL",
                        ward="W-8", rate_year=2023, source_file="RR_values_merged.pdf",
                        row_id="r3"),
        ])
    return idx


def main() -> None:
    run_id = uuid.uuid4().hex[:12]
    audit = AuditLogger(run_id)
    ctx = ProjectContext(
        run_id=run_id,
        base=BaseInputs(cs_number="CS No. 123/A", address="Demo Plot, Mumbai",
                        plot_area_sqm=Decimal("1000"), road_width_m=Decimal("18.5"),
                        section_id="DCPR_30_BASE"))
    # demo disambiguator: residential usage for the two-match CS 123A
    from .core.models import Provenance, QuantityKind, TracedValue
    ctx.extra_inputs["usage"] = TracedValue(
        name="usage", value=None, text="RESIDENTIAL", unit="",
        kind=QuantityKind.OTHER, provenance=Provenance.USER_INPUT,
        remark="User-selected RR row disambiguator.")

    stages = [InputStage(), DCPRStage(), LandRateStage(build_index()),
              AreaStage(), CostStage(), RevenueStage(), ReportStage(), PdfStage()]
    pipe = Pipeline(stages, audit)
    ctx, report = pipe.run(ctx)

    print(f"run {run_id}: completed={report.completed} "
          f"blocked_at={report.blocked_at}")
    if report.reasons:
        print("reasons:", report.reasons)
    for f in report.flags:
        print(f"flag [{f.severity}] {f.code}: {f.message}")
    if ctx.areas:
        for name, tv in ctx.areas.items():
            print(f"  {name}: {tv.value} {tv.unit} ({tv.provenance.value})")
    if ctx.financials:
        for name, tv in ctx.financials.items():
            print(f"  {name}: {tv.value} {tv.unit}")
    print(f"audit log: {audit.path}")
    print(f"pdf: data/processed/reports/{run_id}.pdf")


if __name__ == "__main__":
    main()
