"""Central unit conversion + currency formatting. Non-negotiable rule #4:
rounding happens ONLY at the output step, never mid-chain.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# Conversion factors are exact integers/decimals -- no floats here.
SQM_PER_SQFT = Decimal("0.092903")   # 1 sqft = 0.092903 sqm
SQFT_PER_SQM = Decimal(1) / SQM_PER_SQFT
SQM_PER_HECTARE = Decimal("10000")
SQM_PER_ACRE = Decimal("4046.8564224")


def sqft_to_sqm(v: Decimal) -> Decimal:
    return v * SQM_PER_SQFT


def sqm_to_sqft(v: Decimal) -> Decimal:
    return v * SQFT_PER_SQM


def ensure_same_unit(value_unit: str, expected_unit: str) -> None:
    """Raise on unit mismatch (error-handling matrix: unit mismatch => BLOCK)."""
    if value_unit != expected_unit:
        raise ValueError(f"unit mismatch: got {value_unit!r}, expected {expected_unit!r}")


def format_inr(amount: Decimal, use_lakh_crore: bool = True) -> str:
    """Indian-format currency: commas + lakh/crore shorthand."""
    amount = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    n = abs(int(amount))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    plain = f"{sign}Rs {s}"
    if not use_lakh_crore:
        return plain
    cr = Decimal(n) / Decimal(10_000_000)
    lk = Decimal(n) / Decimal(100_000)
    if cr >= 1:
        return f"{sign}Rs {cr.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} Cr"
    if lk >= 1:
        return f"{sign}Rs {lk.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} Lakh"
    return plain


def format_area(v: Decimal, unit: str = "sqm") -> str:
    q = v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{q:,} {unit}"


def format_ratio(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_pct(v: Decimal) -> str:
    return f"{v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
