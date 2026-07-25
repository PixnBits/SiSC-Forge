"""Surrogate models and pre-filters.

Phase 0: heuristic formation-energy filter.
Phase 1: λ/Tc family-heuristic stub (not a trained GNN).
"""

from siscforge.surrogates.formation import (
    FilterResult,
    FormationEnergyFilter,
    estimate_energy_above_hull_proxy,
    filter_candidates,
)
from siscforge.surrogates.tc_lambda import (
    MODEL_VERSION as TC_LAMBDA_MODEL_VERSION,
    TcLambdaFilterResult,
    TcLambdaPrediction,
    TcLambdaSurrogate,
    filter_by_tc_lambda,
    predict_tc_lambda,
)

__all__ = [
    "FilterResult",
    "FormationEnergyFilter",
    "TC_LAMBDA_MODEL_VERSION",
    "TcLambdaFilterResult",
    "TcLambdaPrediction",
    "TcLambdaSurrogate",
    "estimate_energy_above_hull_proxy",
    "filter_by_tc_lambda",
    "filter_candidates",
    "predict_tc_lambda",
]
