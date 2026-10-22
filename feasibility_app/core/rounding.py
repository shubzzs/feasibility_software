"""Rounding policy: per-formula rounding applied ONLY at the output step.

Each formula spec declares a RoundingRule (e.g. areas 2dp, currency nearest
rupee). Mid-chain values stay full-precision Decimals; we quantize once, when
the value is presented or written to the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


@dataclass(frozen=True)
class RoundingRule:
    places: int
    mode: str = "ROUND_HALF_UP"

    def quantize(self, v: Decimal) -> Decimal:
        exp = Decimal(1).scaleb(-self.places)  # 10**-places
        try:
            return v.quantize(exp, rounding=self.mode)
        except InvalidOperation as e:  # pragma: no cover - guard against bad specs
            raise ValueError(f"cannot round {v} with rule {self}") from e

    def describe(self) -> str:
        return f"{self.places} dp, {self.mode}, at output only"


AREAS_2DP = RoundingRule(2)
MONEY_RUPEE = RoundingRule(0)
RATIO_4DP = RoundingRule(4)
PERCENT_2DP = RoundingRule(2)


def apply_rule(rule: RoundingRule, v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    return rule.quantize(v)
