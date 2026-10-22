"""Stage 1: DCPR Knowledge Engine.

Phase-1 implementation is deterministic and hand-encoded (the reviewed rule set),
NOT LLM retrieval -- per the build order, the AI layer comes after the
deterministic spine works. Retrieval/LLM slots are stubbed behind the same
contract so swapping them in later changes no downstream code.
"""

from __future__ import annotations

from decimal import Decimal

from ...core.models import (Provision, ProjectContext, Provenance, QuantityKind,
                            RegulatoryDataset, RuleObject, StageResult,
                            TracedValue)
from . import handcoded_tables as t12


class DCPRStage:
    name = "s1_dcpr"

    def __init__(self, band_key: str = "island_city_res_com"):
        self.band_key = band_key

    def run(self, ctx: ProjectContext) -> StageResult:
        assert ctx.base is not None
        road_w = ctx.base.road_width_m

        band = t12.lookup_fsi(self.band_key, road_w)
        if band is None:
            return StageResult.blocked_on(
                [f"No TABLE 12 FSI band encoded for road width {road_w} m in "
                 f"'{self.band_key}'. Encode + review the missing rows first."])

        rule = RuleObject(
            rule_id="FSI_TABLE12", version="0.1-draft",
            rule_type="fsi_by_road_width", section_id=ctx.base.section_id,
            params={
                "zonal_basic_fsi": band.zonal_basic_fsi,
                "additional_premium_fsi": band.additional_premium_fsi or Decimal(0),
                "admissible_tdr_fsi": band.admissible_tdr_fsi or Decimal(0),
                "permissible_fsi": band.permissible_fsi,
                "min_road_width_m": band.min_width_m,
            },
            conditions={"band_key": self.band_key,
                        "max_road_width_exclusive_m": band.max_width_m},
            refs=[t12.TABLE12_REF],
            reviewed_by=None)   # pending domain-expert sign-off

        prov = Provision(
            reg_no="30", clause_path="PART V > Reg 30(A) > TABLE 12",
            title="Floor Space Indices in Residential, Commercial and Industrial Zones",
            body=(f"For road width >= {band.min_width_m} m"
                  + (f" and < {band.max_width_m} m" if band.max_width_m else "")
                  + f": zonal basic FSI {band.zonal_basic_fsi}, additional on premium "
                    f"{band.additional_premium_fsi}, admissible TDR "
                    f"{band.admissible_tdr_fsi}, permissible FSI "
                    f"{band.permissible_fsi}."),
            limit_type="permissible_fsi", numeric_limit=band.permissible_fsi,
            condition=f"road_width >= {band.min_width_m}"
                      + (f" and < {band.max_width_m}" if band.max_width_m else ""),
            applies_to_section=ctx.base.section_id,
            refs=[t12.TABLE12_REF], confidence="high",
            verification="MANUAL_REVIEW")   # until expert signs off

        dataset = RegulatoryDataset(
            section_id=ctx.base.section_id, provisions=[prov], rules=[rule],
            outstanding_verification=[
                "TABLE 12 hand-transcription awaiting domain-expert sign-off"],
            doc_version=t12.DOC_VERSION, pdf_hash=t12.PDF_HASH)
        ctx.regulatory = dataset

        # Publish the FSI itself as a TracedValue so Stage 3 consumes it like any
        # other input (provenance: VERIFIED_REGULATION once signed off).
        fsi_tv = TracedValue(
            name="permissible_fsi", value=band.permissible_fsi, unit="ratio",
            kind=QuantityKind.RATIO, provenance=Provenance.VERIFIED_REGULATION,
            formula_id="FSI_TABLE12", formula_version="0.1-draft",
            formula_text="permissible = zonal + premium_addl + TDR (TABLE 12 row)",
            dcpr_refs=[t12.TABLE12_REF], status="MANUAL_REVIEW",
            remark=("Hand-encoded TABLE 12, printed p.83 / pdf p.126; pending "
                    "domain-expert sign-off."))
        base_fsi_tv = fsi_tv.model_copy(update={
            "name": "zonal_basic_fsi", "value": band.zonal_basic_fsi,
            "formula_text": "zonal (basic) FSI from TABLE 12"})
        prem_fsi_tv = fsi_tv.model_copy(update={
            "name": "premium_additional_fsi",
            "value": band.additional_premium_fsi or Decimal(0),
            "formula_text": "additional FSI on payment of premium, TABLE 12"})
        tdr_fsi_tv = fsi_tv.model_copy(update={
            "name": "tdr_admissible_fsi",
            "value": band.admissible_tdr_fsi or Decimal(0),
            "formula_text": "admissible TDR FSI, TABLE 12"})

        return StageResult.ok(
            [fsi_tv, base_fsi_tv, prem_fsi_tv, tdr_fsi_tv],
            bucket="regulatory_inputs")
