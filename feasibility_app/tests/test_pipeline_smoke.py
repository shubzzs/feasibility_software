"""Smoke test: the whole chain runs and produces a PDF.

Numbers here are placeholders (cost/revenue formulas await formula specs), so
this test asserts plumbing and provenance, not financial correctness. The
golden suite in tests/golden/ is what protects the numbers.
"""

from decimal import Decimal

import pytest

from feasibility_app.core.audit import AuditLogger
from feasibility_app.core.models import BaseInputs, ProjectContext
from feasibility_app.core.pipeline import Pipeline
from feasibility_app.run_demo import (build_index, demo_usage_value,
                                       make_stages)


def _ctx(road_width="18.5", cs_number="CS No. 123/A"):
    ctx = ProjectContext(
        run_id="smoke",
        base=BaseInputs(cs_number=cs_number, address="Demo Plot, Mumbai",
                        plot_area_sqm=Decimal("1000"),
                        road_width_m=Decimal(road_width),
                        section_id="DCPR_30_BASE"))
    # CS 123A has two RR rows (residential + commercial); the disambiguator is a
    # user input, exactly as Stage 2 requires. Never auto-picked.
    ctx.extra_inputs["usage"] = demo_usage_value()
    return ctx


def test_full_chain_completes(tmp_path):
    ctx = _ctx()
    pipe = Pipeline(make_stages(build_index(tmp_path / "rr.db")),
                    AuditLogger("smoke"))
    ctx, report = pipe.run(ctx)
    assert report.ok, report.reasons
    assert [s.name for s in pipe.stages] == report.completed
    # FSI from hand-encoded TABLE 12 band >=18 <27 -> permissible 2.7
    assert ctx.value_of("permissible_fsi") == Decimal("2.7")
    assert ctx.value_of("permissible_bua_sqm") == Decimal("2700.0")
    assert ctx.value_of("base_bua_sqm") == Decimal("1330.00")


def test_regulation_values_are_traced_to_page(tmp_path):
    ctx = _ctx()
    Pipeline(make_stages(build_index(tmp_path / "rr.db")),
             AuditLogger("smoke")).run(ctx)
    tv = ctx.get("permissible_fsi")
    assert tv is not None
    assert tv.provenance.value == "verified_regulation"
    ref = tv.dcpr_refs[0]
    assert ref.pdf_page == 126 and ref.printed_page == 83
    assert ref.pdf_hash == "b417e528b717c76f"


def test_unknown_section_blocks_stage3(tmp_path):
    ctx = _ctx()
    ctx.base.section_id = "DCPR_99_NOT_BUILT"  # not in the calculator registry
    _, report = Pipeline(make_stages(build_index(tmp_path / "rr.db")),
                         AuditLogger("smoke")).run(ctx)
    assert report.blocked_at == "s3_area"
    assert not report.ok


def test_missing_cs_number_blocks_stage2(tmp_path):
    ctx = _ctx(cs_number="CS No. 9999/Z")
    _, report = Pipeline(make_stages(build_index(tmp_path / "rr.db")),
                         AuditLogger("smoke")).run(ctx)
    assert report.blocked_at == "s2_landrate"


@pytest.mark.parametrize("width,expected", [
    ("8.5", Decimal("1.33")),    # <9m band: no premium/TDR
    ("9.0", Decimal("2.0")),     # lower bound inclusive
    ("11.99", Decimal("2.0")),   # upper bound exclusive
    ("18.5", Decimal("2.7")),
    ("30.0", Decimal("3.0")),    # open-ended top band
])
def test_road_width_band_boundaries(tmp_path, width, expected):
    ctx = _ctx(width)
    Pipeline(make_stages(build_index(tmp_path / "rr.db")),
             AuditLogger("smoke")).run(ctx)
    assert ctx.value_of("permissible_fsi") == expected


def test_ambiguous_cs_match_is_blocked_not_auto_picked(tmp_path):
    """Two RR rows for CS 123A and no disambiguator => BLOCKED (never guessed)."""
    ctx = _ctx()
    ctx.extra_inputs.pop("usage")
    _, report = Pipeline(make_stages(build_index(tmp_path / "rr.db")),
                         AuditLogger("smoke")).run(ctx)
    assert report.blocked_at == "s2_landrate"
    assert any("isambiguate" in r or "Multiple" in r for r in report.reasons)


def test_single_match_needs_no_disambiguator(tmp_path):
    ctx = _ctx(cs_number="O.S./C.S. 45")   # one row only, different formatting
    ctx.extra_inputs.pop("usage")
    _, report = Pipeline(make_stages(build_index(tmp_path / "rr.db")),
                         AuditLogger("smoke")).run(ctx)
    assert report.ok, report.reasons
