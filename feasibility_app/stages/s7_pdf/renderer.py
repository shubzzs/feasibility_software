"""Stage 7: PDF export via ReportLab (Platypus).

Page structure: cover, project info, regulatory analysis, area/cost/revenue
statements, financial summary, assumptions & outstanding verifications.
Metadata embedded: run id, date, DCPR pdf hash/version, formula versions.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from ...core.models import ProjectContext, StageResult
from ..s6_report.model import ReportModel, build_report

STATUS_COLORS = {
    "VERIFIED": colors.HexColor("#1a7f37"),
    "FLAGGED": colors.HexColor("#b26a00"),
    "MANUAL_REVIEW": colors.HexColor("#9a6700"),
}


class PdfStage:
    name = "s7_pdf"

    def __init__(self, out_dir="data/processed/reports"):
        self.out_dir = out_dir

    def run(self, ctx: ProjectContext) -> StageResult:
        model = build_report(ctx)
        pdf_bytes = render_pdf(model)
        path = f"{self.out_dir}/{model.run_id}.pdf"
        import os
        os.makedirs(self.out_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        return StageResult.ok([])


def render_pdf(model: ReportModel) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Feasibility Report {model.run_id}",
                            author="DCPR Feasibility System")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8)
    story: list = []

    # ---- cover ------------------------------------------------------------- #
    story.append(Paragraph("Real Estate Feasibility Report", h1))
    story.append(Paragraph("DCPR 2034 — Deterministic calculation chain with "
                           "provenance-tagged values", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))
    meta = [
        ["Run ID", model.run_id],
        ["Generated (UTC)", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Source document", model.doc_version or "-"],
        ["PDF SHA-256 (16)", model.pdf_hash or "-"],
    ]
    t = Table([[Paragraph(a, small), Paragraph(b, small)] for a, b in meta])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]))
    story.append(t)
    story.append(Spacer(1, 8 * mm))

    # ---- project info ------------------------------------------------------- #
    story.append(Paragraph("1. Project Information", h2))
    rows = [[Paragraph(k, small), Paragraph(str(v), small)]
            for k, v in model.project_summary.items()]
    pt = Table(rows)
    pt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]))
    story.append(pt)

    # ---- regulatory compliance ---------------------------------------------- #
    if model.regulatory_compliance:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("2. Regulatory Compliance Summary", h2))
        data = [[Paragraph(x, small) for x in
                 ("Regulation", "Sub-clause", "Effect on project",
                  "Influenced calculations", "Page ref")]]
        for c in model.regulatory_compliance:
            data.append([Paragraph(x, small) for x in (
                c.reg_no, c.subsection or "-", c.effect_on_project,
                ", ".join(c.influenced_calculations) or "-",
                c.page_ref or "-")])
        ct = Table(data, repeatRows=1, colWidths=[28 * mm, 34 * mm, 40 * mm,
                                                  38 * mm, 40 * mm])
        ct.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0),
                                 colors.HexColor("#eef2f7"))]))
        story.append(ct)

    # ---- statements ---------------------------------------------------------- #
    for title, table_rows in (("Area Calculation Statement", model.area_statement),
                              ("Cost Statement", model.cost_statement),
                              ("Revenue Statement", model.revenue_statement),
                              ("Financial Summary", model.financial_summary)):
        if not table_rows:
            continue
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(title, h2))
        data = [[Paragraph(x, small) for x in
                 ("Parameter", "Formula", "Value", "Unit", "DCPR Ref",
                  "Status", "Remarks")]]
        style_cmds = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                      ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                      ("VALIGN", (0, 0), (-1, -1), "TOP")]
        for i, r in enumerate(table_rows, start=1):
            data.append([Paragraph(r.parameter, small),
                         Paragraph(r.formula or "-", small),
                         Paragraph(r.value, small),
                         Paragraph(r.unit, small),
                         Paragraph(r.dcpr_ref or "-", small),
                         Paragraph(r.status, small),
                         Paragraph(r.remarks, small)])
            color = STATUS_COLORS.get(r.status)
            if color:
                style_cmds.append(("TEXTCOLOR", (5, i), (5, i), color))
        tt = Table(data, repeatRows=1,
                   colWidths=[30 * mm, 34 * mm, 20 * mm, 12 * mm, 30 * mm,
                              20 * mm, 54 * mm])
        tt.setStyle(TableStyle(style_cmds))
        story.append(tt)

    # ---- assumptions / verification ----------------------------------------- #
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Assumptions", h2))
    for a in model.assumptions:
        story.append(Paragraph(f"• {a}", small))
    story.append(Paragraph("Outstanding Verification Items", h2))
    for o in model.outstanding_verifications:
        story.append(Paragraph(f"• {o}", small))
    if model.flags:
        story.append(Paragraph("Flags", h2))
        for f in model.flags:
            story.append(Paragraph(f"• {f}", small))

    # page footer: Page X of Y
    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(A4[0] / 2, 10 * mm,
                                 f"Page {canvas.getPageNumber()} | "
                                 f"run {model.run_id}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
