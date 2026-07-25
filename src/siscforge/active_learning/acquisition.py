"""Acquisition scoring for minimal Phase-1 active-learning prioritization.

This is a **queue prioritization coordinator**, not a full retrain loop.
It ranks candidates for expensive EPW (or other calculator) jobs using:

- surrogate uncertainty (higher → more interesting)
- predicted Tc (higher → more interesting)
- Si-feasibility (higher → more interesting)
- optional E_hull proxy penalty

After real EPW results land, the normal ranking module re-orders by real Tc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import ActiveLearningConfig
from siscforge.models.results import SiFeasibilityScore
from siscforge.surrogates.tc_lambda import TcLambdaPrediction, predict_tc_lambda

# Ceiling for normalizing predicted Tc into [0, 1]
_TC_NORM_K: float = 40.0
# Hull scale for penalty (eV/atom)
_HULL_NORM: float = 0.25


class AcquisitionRecord(BaseModel):
    """One candidate's acquisition score and feature breakdown."""

    candidate_id: str
    formula: str
    acquisition_score: float
    selected_for_expensive: bool = False
    """True if this candidate is in the top-k expensive-calculator queue."""

    components: dict[str, float] = Field(default_factory=dict)
    """Normalized feature contributions (before weights)."""

    weights: dict[str, float] = Field(default_factory=dict)
    predicted_tc: float | None = None
    uncertainty: float | None = None
    si_feasibility: float | None = None
    energy_above_hull_proxy: float | None = None
    strategy: str = "uncertainty_si_tc"
    notes: str = ""


@dataclass
class AcquisitionPlan:
    """Ordered prioritization result for a campaign batch."""

    ranked: list[AcquisitionRecord] = field(default_factory=list)
    selected: list[StructureCandidate] = field(default_factory=list)
    """Top-k candidates for the expensive calculator."""

    deferred: list[StructureCandidate] = field(default_factory=list)
    """Remaining candidates (surrogate-only evaluations)."""

    strategy: str = "uncertainty_si_tc"
    enabled: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy,
            "n_selected": len(self.selected),
            "n_deferred": len(self.deferred),
            "ranked": [r.model_dump(mode="json") for r in self.ranked],
        }


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def acquisition_score(
    *,
    uncertainty: float,
    predicted_tc: float,
    si_total: float,
    energy_above_hull: float | None = None,
    weights: dict[str, float] | None = None,
    tc_ceiling_K: float = _TC_NORM_K,
    hull_norm: float = _HULL_NORM,
) -> tuple[float, dict[str, float]]:
    """Compute a scalar acquisition score and normalized components.

    score = w_u * unc + w_tc * (Tc/Tc_max) + w_si * (Si/100) − w_hull * (hull/hull_max)

    All weights default to equal interest terms; hull weight defaults to 0
    unless provided (optional soft penalty).
    """
    w = {
        "uncertainty": 0.4,
        "predicted_tc": 0.3,
        "si_feasibility": 0.3,
        "hull_penalty": 0.0,
    }
    if weights:
        w.update({k: float(v) for k, v in weights.items()})

    unc_n = _clamp01(uncertainty)
    tc_n = _clamp01(predicted_tc / max(tc_ceiling_K, 1e-6))
    si_n = _clamp01(si_total / 100.0)
    hull_n = 0.0
    if energy_above_hull is not None:
        hull_n = _clamp01(float(energy_above_hull) / max(hull_norm, 1e-6))

    components = {
        "uncertainty": round(unc_n, 4),
        "predicted_tc": round(tc_n, 4),
        "si_feasibility": round(si_n, 4),
        "hull_penalty": round(hull_n, 4),
    }
    score = (
        w["uncertainty"] * unc_n
        + w["predicted_tc"] * tc_n
        + w["si_feasibility"] * si_n
        - w.get("hull_penalty", 0.0) * hull_n
    )
    return round(float(score), 6), components


def prioritize_candidates(
    candidates: list[StructureCandidate],
    *,
    config: ActiveLearningConfig | None = None,
    si_scores: dict[str, SiFeasibilityScore] | None = None,
    predictions: dict[str, TcLambdaPrediction] | None = None,
) -> AcquisitionPlan:
    """Order *candidates* by acquisition score and split top-k vs deferred.

    Parameters
    ----------
    si_scores:
        candidate_id → SiFeasibilityScore (computed by caller; cheap).
    predictions:
        candidate_id → TcLambdaPrediction (from surrogate; if missing, predict).
    """
    cfg = config or ActiveLearningConfig()
    plan = AcquisitionPlan(strategy=cfg.strategy, enabled=cfg.enabled)
    si_scores = si_scores or {}
    predictions = dict(predictions or {})

    if not cfg.enabled:
        plan.selected = list(candidates)
        plan.deferred = []
        # Still compute scores for visibility when requested later
        for cand in candidates:
            pred = predictions.get(cand.candidate_id) or predict_tc_lambda(cand)
            predictions[cand.candidate_id] = pred
            si = si_scores.get(cand.candidate_id)
            si_tot = float(si.total) if si is not None else 50.0
            score, comps = acquisition_score(
                uncertainty=pred.uncertainty,
                predicted_tc=pred.predicted_Tc,
                si_total=si_tot,
                energy_above_hull=cand.energy_above_hull_proxy,
                weights=cfg.weights.model_dump(),
                tc_ceiling_K=cfg.tc_ceiling_K,
            )
            plan.ranked.append(
                AcquisitionRecord(
                    candidate_id=cand.candidate_id,
                    formula=cand.formula,
                    acquisition_score=score,
                    selected_for_expensive=True,
                    components=comps,
                    weights=cfg.weights.model_dump(),
                    predicted_tc=pred.predicted_Tc,
                    uncertainty=pred.uncertainty,
                    si_feasibility=si_tot,
                    energy_above_hull_proxy=cand.energy_above_hull_proxy,
                    strategy=cfg.strategy,
                    notes="AL disabled — all candidates selected for calculator",
                )
            )
        plan.ranked.sort(key=lambda r: r.acquisition_score, reverse=True)
        return plan

    # Score all
    by_id = {c.candidate_id: c for c in candidates}
    records: list[AcquisitionRecord] = []
    for cand in candidates:
        pred = predictions.get(cand.candidate_id) or predict_tc_lambda(
            cand, mu_star=0.10
        )
        predictions[cand.candidate_id] = pred
        si = si_scores.get(cand.candidate_id)
        si_tot = float(si.total) if si is not None else 50.0
        score, comps = acquisition_score(
            uncertainty=pred.uncertainty,
            predicted_tc=pred.predicted_Tc,
            si_total=si_tot,
            energy_above_hull=cand.energy_above_hull_proxy,
            weights=cfg.weights.model_dump(),
            tc_ceiling_K=cfg.tc_ceiling_K,
        )
        records.append(
            AcquisitionRecord(
                candidate_id=cand.candidate_id,
                formula=cand.formula,
                acquisition_score=score,
                selected_for_expensive=False,
                components=comps,
                weights=cfg.weights.model_dump(),
                predicted_tc=pred.predicted_Tc,
                uncertainty=pred.uncertainty,
                si_feasibility=si_tot,
                energy_above_hull_proxy=cand.energy_above_hull_proxy,
                strategy=cfg.strategy,
            )
        )

    records.sort(
        key=lambda r: (
            r.acquisition_score,
            r.predicted_tc if r.predicted_tc is not None else -1.0,
        ),
        reverse=True,
    )

    k = max(1, int(cfg.max_epw_jobs))
    selected_ids: set[str] = set()
    for i, rec in enumerate(records):
        if i < k:
            rec.selected_for_expensive = True
            selected_ids.add(rec.candidate_id)
            rec.notes = f"selected for expensive path (rank {i + 1}/{len(records)})"
        else:
            rec.selected_for_expensive = False
            rec.notes = "deferred — surrogate-only evaluation"

    plan.ranked = records
    plan.selected = [by_id[r.candidate_id] for r in records if r.selected_for_expensive]
    plan.deferred = [
        by_id[r.candidate_id] for r in records if not r.selected_for_expensive
    ]
    return plan
