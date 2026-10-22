# DCPR 2034 Real-Estate Feasibility System

A **regulation-auditable feasibility engine** for Mumbai Development Control &
Promotion Regulations (DCPR) 2034. You give it a plot — Cadastral Survey (CS)
number, address, plot area, abutting road width, and the DCPR section being
applied — and it returns a PDF feasibility report in which **every single number
carries its own provenance**: the formula that produced it, its version, the DCPR
clause and page it comes from, and whether it is verified, flagged, or awaiting
manual review.

Current status: **Phase 1 complete** — a working vertical slice of one DCPR
section (Reg 30(A) / TABLE 12 base FSI), running input → PDF end to end, with
the real uploaded `DCPR 2034.pdf` as the citation source. Cost and revenue
formulas are placeholders pending the domain expert's specs. The AI retrieval
layer is deliberately **not built yet** (see [Roadmap](#9-roadmap)).

---

## Table of contents

1. [The one rule everything follows from](#1-the-one-rule-everything-follows-from)
2. [Three-layer architecture](#2-three-layer-architecture)
3. [Pipeline flow (Stage 0 → 7)](#3-pipeline-flow-stage-0--7)
4. [Core data model](#4-core-data-model-the-atom-of-the-system)
5. [Repository structure](#5-repository-structure)
6. [Running it](#6-running-it)
7. [Testing](#7-testing)
8. [Honest state: what is real vs placeholder](#8-honest-state-what-is-real-vs-placeholder)
9. [Roadmap](#9-roadmap)
10. [Non-negotiable coding rules](#10-non-negotiable-coding-rules)
11. [Domain notes on the source PDFs](#11-domain-notes-on-the-source-pdfs)

---

## 1. The one rule everything follows from

> **The LLM never produces a number that enters the calculation chain.**

Numbers come only from user input, a dataset lookup, or a deterministic
`Decimal` formula. The AI layer (planned for Phase 4) supplies *citations,
explanations and flags* — nothing else. If this invariant is broken, the product
stops being auditable and becomes a guessing machine with good formatting. Every
design decision below exists to enforce it.

Two supporting structures carry most of the weight:

- **`TracedValue`** — every value wrapped with its story (§4). One object serves
  the report's Remarks column, the provenance legend, and the audit trail at once.
- **Stage gating** — each stage returns `PASS`, `PASS_WITH_FLAGS` or `BLOCKED`.
  A `BLOCKED` result halts all downstream stages, so the system never emits a
  confident-looking report built on missing or ambiguous inputs.

---

## 2. Three-layer architecture

| Layer | Responsibility | Implementation |
|---|---|---|
| **Retrieval & interpretation** | Find and explain DCPR provisions, flag ambiguity | LLM + hybrid search *(Phase 4 — not built)* |
| **Computation** | All numbers: areas, FSI, costs, revenue, ROI | Deterministic Python, `decimal.Decimal` *(built)* |
| **Governance** | Validation, provenance tagging, audit trail, gating | Pydantic models + rules + JSONL logging *(built)* |

Today the whole system is the middle two rows plus governance. Stage 1 currently
uses **hand-encoded, human-reviewable tables** rather than LLM retrieval — which
is exactly how the architecture prescribes starting: prove the deterministic
spine first, then add AI on top with guardrails. Swapping in retrieval later
changes no downstream code, because Stage 3 consumes a fixed contract
(`RegulatoryDataset`), not an implementation.

```
                        ┌──────────────────────────────┐
                        │   ProjectContext (shared,     │
                        │   versioned blackboard)       │
                        └──────────────────────────────┘
                             ▲                │
              reads earlier  │                │  appends own
                             │                ▼
[S0 Input]►[S1 DCPR]►[S2 LandRate]►[S3 Area]►[S4 Cost]►[S5 Revenue]►[S6 Report]►[S7 PDF]
   │           │            │           │         │           │             │          │
   └───────────┴────────────┴───────────┴─────────┴───────────┴─────────────┴──────────┘
                              AuditLogger (append-only JSONL, spans all stages)
```

---

## 3. Pipeline flow (Stage 0 → 7)

Orchestrated by `core/pipeline.py::Pipeline.run()`. Stages implement the
`Stage` protocol (`name` + `run(ctx) -> StageResult`). The runner writes each
stage's `TracedValue`s into the context bucket declared by
`STAGE_OUTPUT_BUCKET` (or `StageResult.bucket` if the stage overrides it),
collects flags, logs to the audit trail, and **returns immediately on BLOCKED**.
An unexpected exception is treated as BLOCKED too — never silently swallowed.

### Stage 0 — Input Manager (`stages/s0_input/stage.py`)
Validates the five base inputs against `BaseInputs` (Pydantic): CS number,
address, plot area, road width, section id. Range checks (road width must be
0–100 m; plot area > 0). Emits validated inputs as `USER_INPUT` TracedValues.
Missing/invalid mandatory data ⇒ **BLOCKED**.

*Next:* dynamic per-section YAML form builder (contract already frozen).

### Stage 1 — DCPR Knowledge Engine (`stages/s1_dcpr/`)
Resolves the applicable regulatory limits for the project. Currently
deterministic: looks up the road-width band in hand-encoded **TABLE 12**
(`handcoded_tables.py`) and publishes a `RegulatoryDataset` plus four
`VERIFIED_REGULATION` TracedValues (`permissible_fsi`, `zonal_basic_fsi`,
`premium_additional_fsi`, `tdr_admissible_fsi`). If no encoded band matches the
road width ⇒ **BLOCKED** ("encode + review the missing rows first"), rather than
guessing. Every published value carries a `Ref` with both printed page and PDF
page, plus the PDF hash.

*Next:* ingestion, chunking, BM25+vector retrieval, citation verification,
amendments layer.

### Stage 2 — Land Rate Retrieval (`stages/s2_landrate/`)
Normalizes the CS number (`normalize_cs`: `"CS No. 123/A"` → `"123A"`,
`"O.S./C.S. 45"` → `"45"`), then does an **exact** indexed SQLite lookup and
branches on result count:

| Matches | Behaviour |
|---|---|
| 0 | **BLOCKED**, with near-matches shown as suggestions only |
| 1 | Accept; record file name, row id, rate year, raw + normalized key |
| >1 | **BLOCKED** until a disambiguator (ward / usage) narrows it to exactly 1 |

Fuzzy matching is used for *suggestions*, never to auto-select — a hard rule.
Also validates the rate (positive, plausible range, unit `INR/sqm`) and flags
stale rate years. Output: a `DATASET_LOOKUP` TracedValue with a `SourceRecord`
attached.

### Stage 3 — Area Calculation Engine (`stages/s3_area/engine.py`)
The most critical stage. Plugin pattern:

```python
class AreaCalculator(ABC):
    section_id: str
    def required_inputs(self) -> list[str]: ...
    def steps(self) -> list[CalcStep]: ...
    def verify(self, results, reg) -> list[Flag]: ...
```

`REGISTRY` maps `section_id → calculator`; an unregistered section ⇒ **BLOCKED**
with the available keys listed. Each `CalcStep` is a node in a **DAG** executed
in topological order (`execute_dag`), with cycle detection and loud failure on
missing upstream values. Each step is a pure Decimal function; the executor wraps
the result in a TracedValue carrying `formula_id`, `formula_version`,
human-readable `formula_text`, resolved DCPR refs, `inputs_used`, and a
deterministic template remark.

Live slice `Reg30BaseSlice` (`section_id="DCPR_30_BASE"`):

```
gross_plot_area ─► net_plot_area_sqm ─┬─► permissible_bua_sqm = net × permissible_fsi
TABLE 12 band ───► fsi_zonal_basic ───┴─► base_bua_sqm        = net × zonal_basic_fsi
verify(): base_bua ≤ permissible_bua  (CAP_BREACH flag otherwise)
```

`fsi_zonal_basic` is an explicit lookup node rather than a mid-calculation reach
into the context — that keeps the dependency edge visible in the trace.

*Next:* deductions in `AREA_NET_PLOT`, rehab/incentive/fungible/TDR steps, full
six-step verification loop, Hypothesis property tests.

### Stage 4 — Cost Engine (`stages/s4_cost/engine.py`)
Dependency-resolved, fails loudly on missing inputs, pre-flight **unit sanity**
(`INR/sqm` rates against `sqm` areas — mismatch ⇒ BLOCKED). Produces
`land_cost_inr`, `construction_cost_inr`, `total_project_cost_inr`, and asserts
the conservation check (components sum to total). Construction rate is injected
as an `ASSUMPTION` TracedValue with a `COST_ASSUMPTION` warning flag.

### Stage 5 — Revenue & Financial Analysis (`stages/s5_revenue/engine.py`)
Saleable area = BUA × conversion factor; revenue = saleable × selling rate;
then profit / margin / ROI with **explicit zero-division guards** (⇒ BLOCKED,
never `inf`). Viability threshold check (`VIABILITY_LOW_MARGIN`, margin < 15%).
Conversion factor and selling rate are `ASSUMPTION`s pending the revenue spec.

### Stage 6 — Report Consolidation (`stages/s6_report/model.py`)
Assembles a structured `ReportModel` (Pydantic): project summary, regulatory
compliance table, area/cost/revenue statements, financial summary, assumptions,
outstanding verifications, flags, and a **source legend grouping every value by
provenance**. The compliance table's "which calculations did this influence"
column is derived mechanically from `TracedValue.dcpr_refs` — no guessing.
Narrative text is deterministic template output today; the AI-written executive
summary arrives in Phase 4 behind a number-guard.

### Stage 7 — PDF Export (`stages/s7_pdf/renderer.py`)
ReportLab Platypus. Cover, project info, regulatory analysis, area statement,
cost statement, revenue statement, financial summary, assumptions & outstanding
verifications. Status badges colour-coded (`VERIFIED` green, `FLAGGED`/
`MANUAL_REVIEW` amber); metadata embedded (run id, generation date, DCPR PDF
hash/version, formula versions). Written to `data/processed/reports/<run_id>.pdf`.

---

## 4. Core data model (the atom of the system)

`core/models.py` is a **frozen contract** — changes need team discussion, because
everything codes against it.

### `Provenance` (enum)
Where a value came from: `USER_INPUT`, `DATASET_LOOKUP`, `DERIVED_CALC`,
`VERIFIED_REGULATION`, `UNVERIFIED_INTERPRETATION`, `ASSUMPTION`.

### `TracedValue`
Every value moving through the pipeline, wrapped with its story:

| Field | Purpose |
|---|---|
| `name`, `value`, `unit`, `kind` | the payload; `unit` always explicit (`sqm`, `INR`, `INR/sqm`, `ratio`, `%`, `m`) |
| `text` | non-numeric provisions (e.g. `"mandatory"`) — so regulations don't have to be forced into numbers |
| `provenance` | drives the report's source legend |
| `formula_id`, `formula_version`, `formula_text` | reproducibility; version bumps on any formula change |
| `dcpr_refs: list[Ref]` | citations; also powers the compliance→calculation linkage |
| `inputs_used` | upstream value names → makes the DAG auditable |
| `source_record` | physical dataset origin (file, row id, rate year, raw/normalized key) |
| `status` | `VERIFIED` / `FLAGGED` / `MANUAL_REVIEW` |
| `remark`, `flags` | human-facing explanation, generated deterministically |

`TracedValue` **rejects floats at validation time** with a message pointing to
`core/rounding.py`. That is rule #1 enforced by the type system, not by convention.

### `Ref` (frozen)
Carries **both** `printed_page` and `pdf_page` deliberately: in the uploaded DCPR
PDF the printed footer differs from the physical page index (offset −43), so a
human flipping pages and a machine jumping to a page must both land correctly.
Also holds `reg_no`, `clause_path`, `table_no`, `quote` (machine-verifiable span),
`doc_version`, `pdf_hash`.

### `ProjectContext`
The shared blackboard: `base` inputs, then per-stage buckets — `extra_inputs`,
`derived`, `regulatory` (`RegulatoryDataset`), `regulatory_inputs`, `land_rate`,
`areas`, `costs`, `revenues`, `financials`, plus accumulated `flags` and
`stage_history`. `ctx.get(name)` searches buckets in reverse stage order;
`ctx.put(bucket, tv)` writes. `regulatory_inputs` is kept separate from `derived`
so the report can state honestly that a limit came from the regulation, not from
a user input plus a formula.

### Supporting modules
- `core/units.py` — the only place conversions happen (`SQM_PER_SQFT`, sqft↔sqm,
  hectare/acre, `ensure_same_unit()` which raises on mismatch) and the only place
  currency formatting happens (`format_inr` with Indian lakh/crore shorthand).
- `core/rounding.py` — `RoundingRule(places, mode)`; named rules `AREAS_2DP`,
  `MONEY_RUPEE`, `RATIO_4DP`, `PERCENT_2DP`. Applied **only at the output step**;
  mid-chain values stay full-precision.
- `core/audit.py` — append-only JSONL per run (`AuditLogger.log`,
  `inputs_snapshot` sha256, `stage_result`, `formula`, `retrieval`, `llm`,
  `verification`). Thread-safe. A run should be reproducible from this log.

---

## 5. Repository structure

```
.
├── DCPR 2034.pdf                  # regulation source (text-based; tracked in git)
│                                  # RR_values_merged.pdf lives in the GitHub repo —
│                                  # not present in this working copy (see §11)
├── data/
│   └── processed/                 # gitignored: rr_rates.db, audit/*.jsonl, reports/*.pdf
└── feasibility_app/
    ├── core/                      # FROZEN CONTRACTS
    │   ├── models.py              # Provenance, Ref, TracedValue, Flag, StageResult,
    │   │                          #   Provision, RuleObject, RegulatoryDataset, ProjectContext
    │   ├── pipeline.py            # Stage protocol, Pipeline runner, gating, bucket map
    │   ├── units.py               # conversions + Indian currency formatting
    │   ├── rounding.py            # RoundingRule, applied at output only
    │   └── audit.py               # append-only JSONL audit trail
    ├── stages/
    │   ├── s0_input/stage.py      # validation, mandatory-input gating
    │   ├── s1_dcpr/
    │   │   ├── handcoded_tables.py# TABLE 12 FSI bands (reviewed source of truth)
    │   │   ├── page_map.py        # printed↔PDF page offset as data, not magic number
    │   │   └── stage.py           # builds RegulatoryDataset, publishes FSI values
    │   ├── s2_landrate/
    │   │   ├── index.py           # normalize_cs + SQLite exact/near-match lookup
    │   │   └── stage.py           # 0/1/many branching, validation, stale-rate flag
    │   ├── s3_area/
    │   │   ├── engine.py          # CalcStep/AreaCalculator, DAG executor, registry,
    │   │   │                      #   ref resolution, remark templates, cap verification
    │   │   └── common/            # shared pieces (FSI lookup, fungible, deductions)
    │   ├── s4_cost/engine.py
    │   ├── s5_revenue/engine.py
    │   ├── s6_report/model.py     # ReportModel + assembler (legend, compliance, rows)
    │   └── s7_pdf/renderer.py     # ReportLab layout + download-ready bytes
    ├── app/ui/                    # Streamlit/FastAPI front end (not built yet)
    ├── tests/test_pipeline_smoke.py
    └── run_demo.py                # build_index / make_stages / main
```

Adding a DCPR section means: new `AreaCalculator` subclass + registry entry +
Stage 1 rules + golden cases. **No UI or pipeline changes.**

---

## 6. Running it

### Requirements
Python 3.12+. Runtime dependencies actually imported by the code:

```bash
pip install pydantic reportlab pymupdf pdfplumber pytest
```

Optional for later phases: `streamlit` or `fastapi` (UI), `hypothesis` (property
tests), sentence-transformers + a vector store (retrieval).

### Run the end-to-end demo
From the repository root:

```bash
python -m feasibility_app.run_demo
```

Expected output (all eight stages complete, three assumption warnings, no block):

```
run 2a694e45297f: completed=['s0_input', 's1_dcpr', 's2_landrate', 's3_area',
  's4_cost', 's5_revenue', 's6_report', 's7_pdf'] blocked_at=None
flag [warning] COST_ASSUMPTION: Construction rate is an assumption.
flag [warning] REV_ASSUMPTION: Saleable conversion factor is an assumption.
flag [warning] REV_ASSUMPTION: Selling rate is an assumption.
  net_plot_area_sqm: 1000 sqm (derived_calc)
  fsi_zonal_basic: 1.33 ratio (derived_calc)
  permissible_bua_sqm: 2700.0 sqm (derived_calc)
  base_bua_sqm: 1330.00 sqm (derived_calc)
  net_profit_inr: 301000000.000 INR
  profit_margin_pct: 49.54... %
  roi_pct: 98.20... %
audit log: data/processed/audit/<run_id>.jsonl
pdf: data/processed/reports/<run_id>.pdf
```

The demo project is 1000 sqm, road width 18.5 m, CS `CS No. 123/A`, section
`DCPR_30_BASE`. Road width 18.5 m falls in the `>=18 <27` band ⇒ permissible FSI
**2.7** ⇒ permissible BUA **2700 sqm**; zonal basic 1.33 ⇒ base BUA **1330 sqm**.
Check those four numbers yourself: verifying them by hand is the point of the
exercise.

Open the PDF at `data/processed/reports/<run_id>.pdf` and the audit log at
`data/processed/audit/<run_id>.jsonl` (one JSON object per line: input snapshot
hash, then per-stage status, values and flags).

Note: `CS No. 123/A` intentionally has **two** RR rows (residential and
commercial). The demo passes a `usage="RESIDENTIAL"` disambiguator via
`ctx.extra_inputs["usage"]`. Remove it and Stage 2 blocks instead of guessing —
that behaviour is covered by a test.

### Drive it from your own code

```python
import uuid
from decimal import Decimal
from feasibility_app.core.audit import AuditLogger
from feasibility_app.core.models import BaseInputs, ProjectContext
from feasibility_app.core.pipeline import Pipeline
from feasibility_app.run_demo import build_index, make_stages

run_id = uuid.uuid4().hex[:12]
ctx = ProjectContext(
    run_id=run_id,
    base=BaseInputs(cs_number="O.S./C.S. 45", address="Some Plot",
                    plot_area_sqm=Decimal("1500"), road_width_m=Decimal("12"),
                    section_id="DCPR_30_BASE"))
ctx, report = Pipeline(make_stages(build_index()), AuditLogger(run_id)).run(ctx)
print(report.ok, report.blocked_at, report.reasons)
print(ctx.value_of("permissible_bua_sqm"))   # 1500 × 2.4 = 3600
```

`make_stages()` is the single wiring point shared by the demo and the tests, so
there is no drift between "how the app runs" and "how CI runs".

### Inspect the source documents directly

```bash
python -c "import fitz; d=fitz.open('DCPR 2034.pdf'); print(len(d)); print(d[125].get_text())"
```

PDF page 126 (1-based) is TABLE 12 — printed page 83.

---

## 7. Testing

```bash
python -m pytest -p no:libtmux feasibility_app/tests -q
```

> `-p no:libtmux` disables a tmux pytest plugin present in some environments that
> errors out during collection. It affects nothing about our tests. Without it you
> may see a plugin load error before tests run.

Currently **11 passing**, covering the invariants rather than the placeholder
money numbers:

| Test | Invariant protected |
|---|---|
| `test_full_chain_completes` | all 8 stages run; FSI 2.7 → BUA 2700.0; base BUA 1330.00 |
| `test_regulation_values_are_traced_to_page` | provenance `verified_regulation`; `pdf_page=126`, `printed_page=83`, correct `pdf_hash` |
| `test_unknown_section_blocks_stage3` | unregistered section ⇒ BLOCKED at `s3_area` |
| `test_missing_cs_number_blocks_stage2` | unknown CS ⇒ BLOCKED at `s2_landrate` |
| `test_road_width_band_boundaries` (×5) | band edges: `<9`→1.33, `9.0`→2.0, `11.99`→2.0, `18.5`→2.7, `30.0`→3.0 (inclusive lower, exclusive upper) |
| `test_ambiguous_cs_match_is_blocked_not_auto_picked` | 2 matches + no disambiguator ⇒ BLOCKED, never guessed |
| `test_single_match_needs_no_disambiguator` | `O.S./C.S. 45` normalizes and matches on one row |

Deliberate gap: no assertions on profit/margin/ROI correctness, because those
formulas are placeholders. The smoke test says so in its docstring. Golden cases
from past approved projects (`tests/golden/`) are what will protect the numbers,
and they arrive with the domain expert's specs.

---

## 8. Honest state: what is real vs placeholder

This matters more than feature count. A report from this system tells you which
is which — but read this table before trusting anything.

### Real and verified
- **Pipeline, gating, audit, provenance model** — working, tested.
- **TABLE 12 FSI bands (Island City residential/commercial)** — transcribed from
  the actual uploaded PDF, page 126 / printed 83, cross-checked against the
  extracted text. Band boundaries tested. **Status `MANUAL_REVIEW`** until the
  domain expert signs off the transcription.
- **CS normalization + exact/ambiguous match logic** — implemented and tested
  against awkward formats.
- **Page-offset handling** (−43) — measured on the uploaded file.
- **RR schema design** — validated against the `RR_values_merged.pdf` structure.
  ⚠️ That file is in the GitHub repo but **not present in this working copy**
  (`ls RR_values_merged.pdf` → not found), so the extractor cannot be built or
  run until it is pulled down here. The three rows currently in `rr_rates.db` are
  hand-seeded demo fixtures, not extracted data.

### Placeholder / not real
| Item | What it is now | What it needs |
|---|---|---|
| `rr_rates.db` contents | 3 hand-seeded sample rows in `run_demo.build_index()` | real extraction from `RR_values_merged.pdf` |
| `AREA_NET_PLOT` | net = gross (no deductions) | domain-expert deduction formulas |
| Other TABLE 12 blocks | only Island City res/com rows 1–5 | remaining zones, printed pp. 83–85 |
| Construction cost rate | flat ₹45,000/sqm `ASSUMPTION` | cost-component registry spec |
| Conversion factor / selling rate | 1.25 / ₹1,80,000 `ASSUMPTION`s | revenue spec |
| Rehab / incentive / fungible / TDR areas | absent | Stage 3 steps + rules |
| Stage 1 retrieval + LLM | absent (by design, Phase 4) | ingestion, chunking, BM25+vector, citation verifier |
| UI | absent (`app/ui/` empty) | Streamlit forms + download button |
| Amendments layer | absent | notification documents |

So: **area-side numbers are trustworthy modulo expert sign-off; financial numbers
are structurally correct but numerically meaningless** until real specs land.

---

## 9. Roadmap

| Phase | Scope | State |
|---|---|---|
| **1 — Contracts + skeleton** | models, pipeline, gating, audit, stub→real stages, one vertical slice | ✅ done |
| **2 — Data spine** | real RR extractor → `rr_rates.db`; complete TABLE 12 encoding; expert sign-off | ⏭ next |
| **3 — Stage 3 depth** | deductions, rehab/incentive/fungible, six-step verification, golden suite, Hypothesis properties | planned |
| **4 — AI layer** | ingestion/chunking, hybrid retrieval + eval set (30–50 known-clause questions), structured LLM output, deterministic citation verification, executive summary behind a number-guard, prompt versioning, graceful fallback | planned |
| **5 — Scale-out** | more sections via plugins, Streamlit UI, security/performance/observability, deployment decision | planned |

Build order rationale: the deterministic spine must be provably correct *before*
any LLM output can feed it. Building retrieval first would invert that.

---

## 10. Non-negotiable coding rules

Enforced by code where possible, by review otherwise:

1. **`Decimal`, never `float`** for area or money — enforced: `TracedValue`
   raises on float input.
2. **No magic numbers in code.** Thresholds live in config or the formula spec —
   e.g. the page offset sits in `page_map.py` as named data.
3. **No calculations in the UI, report or LLM layers.** Only Stage 3–5 functions compute.
4. **Rounding only at the output step**, per the formula spec.
5. **Formula changes bump `formula_version`**, and the golden suite must pass.
6. **LLM output is always parsed against a schema**, never trusted raw.
7. **Never auto-pick an ambiguous match** — CS number, regulation, anything.
   Enforced in Stage 2 and covered by tests.

Error-handling matrix (implemented):

| Condition | Behaviour |
|---|---|
| Missing mandatory input | BLOCK at Stage 0 |
| CS not found | BLOCK, suggest near-matches |
| Multiple CS matches | BLOCK, request ward/usage disambiguator |
| No FSI band encoded | BLOCK at Stage 1 |
| Unregistered section | BLOCK at Stage 3 |
| Unit mismatch | BLOCK at Stage 4/5 |
| Cap breach | FLAG, show uncapped and capped |
| Stale rate / low margin / assumed input | WARNING flag, continue |
| Unexpected exception anywhere | BLOCK (never silent pass) |

---

## 11. Domain notes on the source PDFs

Findings from inspecting the uploaded source files — these shaped the design and
should survive any refactor:

- **Availability:** `DCPR 2034.pdf` is present in this working copy.
  `RR_values_merged.pdf` was confirmed in the GitHub repo but is **not checked out
  here**, so claims about it below come from earlier inspection of that file, not
  from a re-read in this session. Put it in `/workspace` before building the RR
  extractor (Phase 2).
- **Both PDFs are text-based.** No OCR needed for body content (one cover page is
  the exception). PyMuPDF `get_text()` works.
- **`DCPR 2034.pdf` is 406 pages and has zero embedded bookmarks/outline**
  (`doc.get_toc()` → `[]`), so the Regulation → sub-regulation → clause hierarchy
  must be constructed by parsing headings, not read from the document. Plan for
  regex-driven hierarchy building.
- **Printed page ≠ physical PDF page.** Measured constant offset −43
  (`printed = pdf − 43`) for the main body — re-confirmed just now: PDF page 126
  carries footer "83". Hence dual page fields in `Ref`.
  Verify the offset again if a different PDF build is ever uploaded; if it turns
  out non-constant, replace `page_map.py` with a per-page footer scan.
  ⚠️ Gotcha for whoever writes that scanner: the footer number appears early in
  `get_text()` output, **not** at the end of the page, so a naive "last line is
  the page number" heuristic silently returns nothing. Use PyMuPDF's block rect
  (bottom-most text block) instead of line order.
- **Automated table extraction on this PDF is unreliable** — cells emerge as a
  flat column stream (observed directly on p.126). This is why high-value tables
  are hand-encoded and reviewed once, with the LLM permitted only to cross-check.
  Treat that as mandatory, not optional: a confidently misread FSI table is
  precisely the failure mode this architecture exists to prevent.
- **`RR_values_merged.pdf`** (36 pages) carries road-width bands, usage types and
  premium rates — which maps onto the `LandRateRow` fields (`usage`,
  `road_width_band`, `ward`, `village`, `rate_year`) and explains why one CS
  number legitimately has multiple rows, hence the disambiguation requirement.
- **Document currency:** the PDF metadata suggests a build that may not include
  post-2020 amendments. DCPR is explicitly "subject to amendments", so the base
  PDF alone is not sufficient — the amendments layer is a requirement, not a
  nice-to-have. Confirm the version with the domain expert before any real use.
- `pdf_hash` (`b417e528b717c76f`, sha256[:16] of the uploaded DCPR) is stamped
  into every `Ref` and into the report metadata, so a report states which document
  version it was computed against.

---

*Verified against the code in this repository: 11 tests passing, demo completes
all 8 stages.*
