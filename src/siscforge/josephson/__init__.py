"""P4.1 — Tier-1 analytic Josephson metrics (inert unless enabled).

Always approximate / ranking-only. Usadel / BdG and fabrication-rule
engines are later Phase 4 packages.
"""

from siscforge.josephson.attach import attach_josephson_metrics, josephson_is_enabled
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
    "KB_MEV_PER_K",
    "RANKING_ONLY_CAVEAT",
    "GapExtraction",
    "ambegaokar_baratoff_icrn_mV",
    "attach_josephson_metrics",
    "bcs_gap_meV",
    "estimate_tier1",
    "extract_gap",
    "jc_proxy_A_per_cm2",
    "josephson_is_enabled",
    "resolve_tc_K",
    "switching_energy_eV",
]
