from __future__ import annotations

from dataclasses import dataclass

from referee.models import AuthMethod
from referee.scoring import ScoringResult


@dataclass(frozen=True)
class ComparisonRow:
    method: AuthMethod
    score: float
    primary_reason: str


def build_comparison_rows(scoring: ScoringResult) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    for method, score in scoring.scores.items():
        # Pick the first non-base reason as the primary driver when available.
        reasons = scoring.reasons.get(method, [])
        primary = "Base score"
        for r in reasons:
            if r.startswith("Base score"):
                continue
            if r.startswith("Capped at"):
                continue
            # Only use actual scoring-rule reasons.
            primary = r
            break
        # Strip the numeric prefix for table readability.
        if ": " in primary:
            primary = primary.split(": ", 1)[1]
        rows.append(ComparisonRow(method=method, score=score, primary_reason=primary))

    rows.sort(key=lambda r: r.score, reverse=True)
    return rows
