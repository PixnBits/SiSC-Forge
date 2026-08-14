"""Josephson metrics: P4.1 Tier-1 analytics + P4.2 fabrication heuristics.

Always approximate / ranking-only. Usadel / BdG remain later Phase 4.
"""

from siscforge.josephson.attach import attach_josephson_metrics, josephson_is_enabled
from siscforge.josephson.fabrication import (
    DEFAULT_BEOL_TEMP_C,
    HEURISTIC_CAVEAT,
    apply_secondary_ranking,
    infer_fabrication_hints,
    normalize_secondary_ranking,
    suggest_junction_class,
    thermal_compatibility,
)
from siscforge.josephson.tier1 import (
    BCS_GAP_RATIO,
    KB_MEV_PER_K,
    RANKING_ONLY_CAVEAT,
    GapExtraction,
    ambegaokar_baratoff_icrn_mV,
    bcs_gap_meV,
    estimate_tier1,
    extract_gap,
    jc_proxy_A_per_cm2,
    resolve_tc_K,
    switching_energy_eV,
)

__all__ = [
    "BCS_GAP_RATIO",
    "DEFAULT_BEOL_TEMP_C",
    "HEURISTIC_CAVEAT",
    "KB_MEV_PER_K",
    "RANKING_ONLY_CAVEAT",
    "GapExtraction",
    "ambegaokar_baratoff_icrn_mV",
    "apply_secondary_ranking",
    "attach_josephson_metrics",
    "bcs_gap_meV",
    "estimate_tier1",
    "extract_gap",
    "infer_fabrication_hints",
    "jc_proxy_A_per_cm2",
    "josephson_is_enabled",
    "normalize_secondary_ranking",
    "resolve_tc_K",
    "suggest_junction_class",
    "switching_energy_eV",
    "thermal_compatibility",
]
