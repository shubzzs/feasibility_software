"""Core data contracts for the DCPR 2034 Feasibility System.

This module is a FROZEN CONTRACT. Changes require team discussion and Tech Lead
approval because every other module codes against these types.

Design invariant (the load-bearing rule of this project):
    The LLM never produces a number that enters the calculation chain.
    Every numeric value here originates from user input, a dataset lookup,
    or a deterministic Decimal formula -- and says so via `provenance`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Provenance: where did this value come from?
# --------------------------------------------------------------------------- #
class Provenance(str, Enum):
    USER_INPUT = "user_input"
    DATASET_LOOKUP = "dataset_lookup"
    DERIVED_CALC = "derived_calc"
    VERIFIED_REGULATION = "verified_regulation"
    UNVERIFIED_INTERPRETATION = "unverified_interpretation"
    ASSUMPTION = "assumption"


Status = Literal["VERIFIED", "FLAGGED", "MANUAL_REVIEW"]
StageStatus = Literal["PASS", "PASS_WITH_FLAGS", "BLOCKED"]


# --------------------------------------------------------------------------- #
# References into source documents
# --------------------------------------------------------------------------- #
class Ref(BaseModel):
    """A citation into a regulation document.

    Carries BOTH page numbers deliberately: in ``DCPR 2034.pdf`` the printed page
    footer differs from the physical PDF page index (measured offset -43). A human
    flipping to the printed page and a machine jumping to the PDF page must both
    land in the right place, so we store neither at the expense of the other.
    """

    model_config = ConfigDict(frozen=True)

    doc: str = "DCPR 2034"
    reg_no: str | None = None          # e.g. "33(7)"
    clause_path: str | None = None     # e.g. "Reg 33 > 33(7) > Explanation ii"
    table_no: str | None = None        # e.g. "TABLE 12"
    printed_page: int | None = None    # page number as it appears on the page
    pdf_page: int | None = None        # 1-based physical page in the PDF
    quote: str | None = None           # verbatim span, machine-verifiable
    doc_version: str | None = None
    pdf_hash: str | None = None

    def label(self) -> str:
        parts: list[str] = [self.doc]
        if self.reg_no:
            parts.append(f"Reg {self.reg_no}")
        if self.table_no:
            parts.append(self.table_no)
        if self.printed_page is not None:
            parts.append(f"p.{self.printed_page}")
        elif self.pdf_page is not None:
            parts.append(f"pdf p.{self.pdf_page}")
        return ", ".join(str(p) for p in parts)


class SourceRecord(BaseModel):
    """Where a dataset value physically came from (file, row, year)."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    file_name: str | None = None
    row_id: str | None = None
    rate_year: int | None = None
    raw_key: str | None = None
    normalized_key: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# TracedValue: the atom of the whole system
# --------------------------------------------------------------------------- #
class QuantityKind(str, Enum):
    AREA = "area"
    MONEY = "money"
    RATIO = "ratio"
    LENGTH = "length"
    COUNT = "count"
    OTHER = "other"


class TracedValue(BaseModel):
    """Every value that moves through the pipeline, wrapped with its story.

    One structure satisfies three requirements at once: the report's Remarks
    column, the provenance/source legend, and the audit trail.
    """

    name: str
    value: Decimal | None = None
    text: str | None = None            # for non-numeric provisions (e.g. "mandatory")
    unit: str = ""                     # "sqm", "INR", "INR/sqm", "ratio", "m"
    kind: QuantityKind = QuantityKind.OTHER
    provenance: Provenance
    formula_id: str | None = None
    formula_version: str | None = None
    formula_text: str | None = None    # human-readable, generated next to the calc
    dcpr_refs: list[Ref] = Field(default_factory=list)
    inputs_used: list[str] = Field(default_factory=list)
    source_record: SourceRecord | None = None
    status: Status = "VERIFIED"
    remark: str = ""
    flags: list[str] = Field(default_factory=list)

    @field_validator("value")
    @classmethod
    def _reject_float(cls, v: Any) -> Any:
        """Non-negotiable rule #1: Decimal, never float, for money or area."""
        if isinstance(v, float):
            raise ValueError(
                f"float passed to TracedValue ({v!r}). Use Decimal(str(...)). "
                "See core/rounding.py."
            )
        return v

    @property
    def has_number(self) -> bool:
        return self.value is not None

    def ref_labels(self) -> list[str]:
        return [r.label() for r in self.dcpr_refs]


# --------------------------------------------------------------------------- #
# Flags and stage results
# --------------------------------------------------------------------------- #
Severity = Literal["info", "warning", "critical"]


class Flag(BaseModel):
    code: str
    severity: Severity = "warning"
    message: str
    stage: str | None = None
    value_name: str | None = None
    resolution_hint: str | None = None


class StageResult(BaseModel):
    status: StageStatus
    values: list[TracedValue] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status == "BLOCKED"

    @classmethod
    def ok(cls, values: list[TracedValue] | None = None,
           flags: list[Flag] | None = None) -> "StageResult":
        flags = flags or []
        status: StageStatus = "PASS_WITH_FLAGS" if flags else "PASS"
        return cls(status=status, values=values or [], flags=flags)

    @classmethod
    def blocked_on(cls, reasons: list[str],
                   flags: list[Flag] | None = None) -> "StageResult":
        return cls(status="BLOCKED", blocking_reasons=reasons, flags=flags or [])


# --------------------------------------------------------------------------- #
# Regulatory objects (produced by Stage 1, consumed by Stage 3)
# --------------------------------------------------------------------------- #
class Provision(BaseModel):
    """One regulatory statement, with its citation and verification state."""

    reg_no: str
    clause_path: str | None = None
    title: str | None = None
    body: str
    limit_type: str | None = None      # "base_fsi" | "incentive_fsi" | "premium" ...
    numeric_limit: Decimal | None = None
    condition: str | None = None       # human-readable condition text
    applies_to_section: str | None = None
    refs: list[Ref] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    verification: Status = "MANUAL_REVIEW"
    amendment_of: str | None = None


class RuleObject(BaseModel):
    """A typed, hand-reviewed rule. THIS is what feeds calculations -- not prose.

    High-value tables (FSI by road width, premium tables) are hand-encoded here
    and reviewed once by the domain expert. The LLM may only cross-check them.
    """

    rule_id: str
    version: str = "1.0"
    rule_type: str                     # "fsi_by_road_width", "tcc_premium", ...
    section_id: str | None = None
    params: dict[str, Decimal] = Field(default_factory=dict)
    conditions: dict[str, Any] = Field(default_factory=dict)
    refs: list[Ref] = Field(default_factory=list)
    reviewed_by: str | None = None
    review_date: datetime | None = None


class RegulatoryDataset(BaseModel):
    """Output of Stage 1: everything Stage 3 needs to know about the rules."""

    section_id: str
    provisions: list[Provision] = Field(default_factory=list)
    rules: list[RuleObject] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    outstanding_verification: list[str] = Field(default_factory=list)
    doc_version: str | None = None
    pdf_hash: str | None = None

    def rule(self, rule_type: str) -> RuleObject | None:
        for r in self.rules:
            if r.rule_type == rule_type:
                return r
        return None


# --------------------------------------------------------------------------- #
# ProjectContext: the single shared, versioned object
# --------------------------------------------------------------------------- #
class BaseInputs(BaseModel):
    cs_number: str
    address: str
    plot_area_sqm: Decimal
    road_width_m: Decimal
    section_id: str

    @field_validator("plot_area_sqm")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("plot_area_sqm must be > 0")
        return v


class ProjectContext(BaseModel):
    """Shared blackboard. Stages read earlier outputs and append their own."""

    run_id: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"

    base: BaseInputs | None = None
    extra_inputs: dict[str, TracedValue] = Field(default_factory=dict)
    derived: dict[str, TracedValue] = Field(default_factory=dict)

    regulatory: RegulatoryDataset | None = None
    land_rate: TracedValue | None = None

    areas: dict[str, TracedValue] = Field(default_factory=dict)
    costs: dict[str, TracedValue] = Field(default_factory=dict)
    revenues: dict[str, TracedValue] = Field(default_factory=dict)
    financials: dict[str, TracedValue] = Field(default_factory=dict)

    flags: list[Flag] = Field(default_factory=list)
    stage_history: list[dict[str, Any]] = Field(default_factory=list)

    def get(self, name: str) -> TracedValue | None:
        """Look up a named value across all stages (later stages win)."""
        for bucket in (self.financials, self.revenues, self.costs,
                       self.areas, self.derived, self.extra_inputs):
            if name in bucket:
                return bucket[name]
        if self.land_rate is not None and self.land_rate.name == name:
            return self.land_rate
        return None

    def value_of(self, name: str) -> Decimal | None:
        tv = self.get(name)
        return tv.value if tv else None

    def put(self, bucket: str, tv: TracedValue) -> None:
        target = getattr(self, bucket)
        if isinstance(target, dict):
            target[tv.name] = tv
        else:  # land_rate is a single TracedValue
            self.land_rate = tv

    def add_flag(self, flag: Flag) -> None:
        self.flags.append(flag)
