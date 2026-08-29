"""
Confluence Scorer package — Phase 3 RX-0 Unicorn.

Public API:
    score_confluence(df, ...) -> pd.DataFrame   # full per-bar scoring
    latest_confluence(df, ...) -> dict          # summary of last bar
"""

from confluence.scorer import (
    GRADE_A_PLUS,
    GRADE_SKIP,
    GRADE_VALID,
    latest_confluence,
    merge_indicators,
    score_confluence,
)

__all__ = [
    "score_confluence",
    "latest_confluence",
    "merge_indicators",
    "GRADE_SKIP",
    "GRADE_VALID",
    "GRADE_A_PLUS",
]
