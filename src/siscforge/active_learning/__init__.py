"""Minimal active-learning prioritization (Phase 1 first cut).

Prioritizes which candidates receive expensive EPW / calculator jobs.
Does **not** retrain surrogates or run Bayesian optimization.
"""

from siscforge.active_learning.acquisition import (
    AcquisitionPlan,
    AcquisitionRecord,
    acquisition_score,
    prioritize_candidates,
)

__all__ = [
    "AcquisitionPlan",
    "AcquisitionRecord",
    "acquisition_score",
    "prioritize_candidates",
]
