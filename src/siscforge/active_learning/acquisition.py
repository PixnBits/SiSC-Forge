"""Acquisition scoring for Phase-1 / P3.6 active-learning prioritization.

This is a **queue prioritization coordinator**, not a full retrain loop.
It ranks candidates for expensive EPW (or DMFT) jobs using:

- surrogate uncertainty (higher → more interesting)
- predicted Tc **or** a common ``performance_score`` when mixed mode is on
- Si-feasibility (higher → more interesting)
- optional E_hull proxy penalty

P3.6 adds conventional / unconventional **pools** and ``joint`` / ``separate``
acquisition modes. Default ``pool_mode=off`` preserves pre-P3.6 scoring and
top-k selection. See ``docs/phase3-p36-mixed-al.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from siscforge.active_learning.pools import (
    AcquisitionMode,
    PoolDecision,
    count_pools,
    derive_pool,
    empty_pool_counts,
    normalize_pool_mode,
    select_with_quotas,
)
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
    model_version: str = "heuristic"
    """Surrogate model version used for this score (AC14)."""
    training_set_size: int = 0
    bootstrap: bool = True
    notes: str = ""
    # --- P3.6 pool provenance (additive; defaults keep old records valid) ---
    pool: str = "unknown"
    pool_reason: str = ""
    acquisition_mode: str = "off"
    score_signal: str = "surrogate_tc"
    """``surrogate_tc`` or ``performance_score`` — which Tc-like input was used."""

    soft_mode_class: str | None = None
    """Heuristic soft-mode class when a phonon evaluation is available (#45)."""

    quality_flags: list[str] = Field(default_factory=list)
    """Trust-layer flags copied from the evaluation when available (#47)."""

    result_quality: str | None = None
    """Trust-layer tier copied from the evaluation when available (#47)."""

    block_expensive_epw: bool = False
    """True when a known-stable binary still needs denser-q before EPW (#45)."""


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
    model_version: str = "heuristic"
    training_set_size: int = 0
    bootstrap: bool = True
    prioritization_record_id: str | None = None
    acquisition_mode: str = "off"
    pool_counts: dict[str, int] = field(default_factory=empty_pool_counts)
    selected_by_pool: dict[str, int] = field(default_factory=empty_pool_counts)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy,
            "model_version": self.model_version,
            "training_set_size": self.training_set_size,
            "bootstrap": self.bootstrap,
            "prioritization_record_id": self.prioritization_record_id,
            "acquisition_mode": self.acquisition_mode,
            "pool_counts": dict(self.pool_counts),
            "selected_by_pool": dict(self.selected_by_pool),
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

    The Tc-like input may be a surrogate Tc **or** a P3.4
    ``performance_score`` (EPW Tc or DMFT pairing proxy). The formula is
    unchanged — only the caller chooses the signal.
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


def _quota_map(config: ActiveLearningConfig) -> dict[str, float]:
    raw = getattr(config, "pool_quotas", None)
    if raw is None:
        return {"conventional": 0.5, "unconventional": 0.5, "unknown": 0.0}
    if hasattr(raw, "model_dump"):
        return {str(k): float(v) for k, v in raw.model_dump().items()}
    return {str(k): float(v) for k, v in dict(raw).items()}


def _score_one(
    cand: StructureCandidate,
    *,
    pred: TcLambdaPrediction,
    si_tot: float,
    cfg: ActiveLearningConfig,
    model_version: str,
    training_set_size: int,
    bootstrap: bool,
    mode: AcquisitionMode,
    evaluation: Any | None,
    use_performance_score: bool,
    notes: str = "",
) -> tuple[AcquisitionRecord, PoolDecision]:
    decision = derive_pool(candidate=cand, evaluation=evaluation)
    predicted_tc = float(pred.predicted_Tc)
    score_signal = "surrogate_tc"
    if use_performance_score and evaluation is not None:
        perf = getattr(evaluation, "performance_score", None)
        if perf is not None:
            try:
                predicted_tc = float(perf)
                score_signal = "performance_score"
            except (TypeError, ValueError):
                pass
    score, comps = acquisition_score(
        uncertainty=pred.uncertainty,
        predicted_tc=predicted_tc,
        si_total=si_tot,
        energy_above_hull=cand.energy_above_hull_proxy,
        weights=cfg.weights.model_dump(),
        tc_ceiling_K=cfg.tc_ceiling_K,
    )
    sm_class: str | None = None
    qflags: list[str] = []
    rq: str | None = None
    extra_notes = notes
    block_epw = False
    if evaluation is not None:
        qflags = list(getattr(evaluation, "quality_flags", None) or [])
        eph = getattr(evaluation, "electron_phonon", None)
        if eph is not None:
            for flag in getattr(eph, "quality_flags", None) or []:
                if flag not in qflags:
                    qflags.append(flag)
        rq = getattr(evaluation, "result_quality", None)
        ph = getattr(evaluation, "phonon", None)
        if ph is not None:
            from siscforge.soft_modes import classify_soft_mode, needs_denser_q_before_epw

            row = classify_soft_mode(evaluation)
            sm_class = row["soft_mode_class"]
            if needs_denser_q_before_epw(evaluation):
                block_epw = True
                extra_notes = (
                    (notes + "; " if notes else "")
                    + "known-stable binary looks soft on coarse mesh — "
                    "denser-q confirmation required before EPW"
                )
    rec = AcquisitionRecord(
        candidate_id=cand.candidate_id,
        formula=cand.formula,
        acquisition_score=score,
        selected_for_expensive=False,
        components=comps,
        weights=cfg.weights.model_dump(),
        predicted_tc=predicted_tc,
        uncertainty=pred.uncertainty,
        si_feasibility=si_tot,
        energy_above_hull_proxy=cand.energy_above_hull_proxy,
        strategy=cfg.strategy,
        model_version=model_version,
        training_set_size=training_set_size,
        bootstrap=bootstrap,
        notes=extra_notes,
        pool=decision.pool,
        pool_reason=decision.reason,
        acquisition_mode=mode,
        score_signal=score_signal,
        soft_mode_class=sm_class,
        quality_flags=qflags,
        result_quality=rq,
        block_expensive_epw=block_epw,
    )
    return rec, decision


def prioritize_candidates(
    candidates: list[StructureCandidate],
    *,
    config: ActiveLearningConfig | None = None,
    si_scores: dict[str, SiFeasibilityScore] | None = None,
    predictions: dict[str, TcLambdaPrediction] | None = None,
    evaluations: Mapping[str, Any] | None = None,
    model_version: str = "heuristic",
    training_set_size: int = 0,
    bootstrap: bool = True,
) -> AcquisitionPlan:
    """Order *candidates* by acquisition score and split top-k vs deferred.

    Parameters
    ----------
    si_scores:
        candidate_id → SiFeasibilityScore (computed by caller; cheap).
    predictions:
        candidate_id → TcLambdaPrediction (from surrogate; if missing, predict).
    evaluations:
        Optional candidate_id → CandidateEvaluation. Used for pool derivation
        and, when ``pool_mode`` is ``joint`` or ``separate``, as the source of
        a common ``performance_score`` (P3.4 EPW Tc or DMFT pairing).
        Ignored for scoring when ``pool_mode`` is ``off`` so conventional
        campaigns do not drift.
    """
    cfg = config or ActiveLearningConfig()
    mode = normalize_pool_mode(getattr(cfg, "pool_mode", "off"))
    use_perf = mode in {"joint", "separate"}
    evals = dict(evaluations or {})
    plan = AcquisitionPlan(
        strategy=cfg.strategy,
        enabled=cfg.enabled,
        model_version=model_version,
        training_set_size=training_set_size,
        bootstrap=bootstrap,
        acquisition_mode=mode,
    )
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
            rec, _ = _score_one(
                cand,
                pred=pred,
                si_tot=si_tot,
                cfg=cfg,
                model_version=model_version,
                training_set_size=training_set_size,
                bootstrap=bootstrap,
                mode=mode,
                evaluation=evals.get(cand.candidate_id),
                # Disabled path: never swap in performance_score (pre-P3.6).
                use_performance_score=False,
                notes="AL disabled — all candidates selected for calculator",
            )
            rec.selected_for_expensive = True
            plan.ranked.append(rec)
        plan.ranked.sort(key=lambda r: r.acquisition_score, reverse=True)
        plan.pool_counts = count_pools(r.pool for r in plan.ranked)
        plan.selected_by_pool = count_pools(r.pool for r in plan.ranked)
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
        rec, _ = _score_one(
            cand,
            pred=pred,
            si_tot=si_tot,
            cfg=cfg,
            model_version=model_version,
            training_set_size=training_set_size,
            bootstrap=bootstrap,
            mode=mode,
            evaluation=evals.get(cand.candidate_id),
            use_performance_score=use_perf,
        )
        records.append(rec)

    records.sort(
        key=lambda r: (
            r.acquisition_score,
            r.predicted_tc if r.predicted_tc is not None else -1.0,
        ),
        reverse=True,
    )

    k = max(1, int(cfg.max_epw_jobs))
    eligible = [r for r in records if not r.block_expensive_epw]
    if mode == "separate":
        selected_ids = set(
            select_with_quotas(eligible, k=k, quotas=_quota_map(cfg))
        )
    else:
        # off and joint: single global top-k (joint only changes provenance
        # and the optional performance_score signal). Known-stable binaries
        # that still look soft are not queued for EPW (#45 review).
        selected_ids = {r.candidate_id for r in eligible[:k]}

    for i, rec in enumerate(records):
        if rec.candidate_id in selected_ids:
            rec.selected_for_expensive = True
            rec.notes = (
                f"selected for expensive path (rank {i + 1}/{len(records)}"
                f", pool={rec.pool}, mode={mode})"
            )
        else:
            rec.selected_for_expensive = False
            rec.notes = (
                f"deferred — surrogate-only evaluation (pool={rec.pool}, mode={mode})"
            )
        if rec.soft_mode_class:
            rec.notes += f"; soft_mode_class={rec.soft_mode_class}"
        if rec.block_expensive_epw:
            rec.notes += (
                "; blocked from expensive EPW until denser-q confirmation"
            )

    plan.ranked = records
    plan.selected = [by_id[r.candidate_id] for r in records if r.selected_for_expensive]
    plan.deferred = [
        by_id[r.candidate_id] for r in records if not r.selected_for_expensive
    ]
    plan.pool_counts = count_pools(r.pool for r in records)
    plan.selected_by_pool = count_pools(
        r.pool for r in records if r.selected_for_expensive
    )
    return plan
