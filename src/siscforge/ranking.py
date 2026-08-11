"""Multi-objective ranking with Pareto front and result-quality / trust penalties.

P2.4 — configurable weights (performance, Si-feasibility, optional certainty),
Pareto non-dominated set on primary axes, and ranking-weight provenance on
each ranked row. Trust-layer / stable-first / hull behaviour is unchanged.
"""

from __future__ import annotations

import math
from typing import Any

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import QualityConfig, RankingConfig
from siscforge.quality import apply_quality_assessment, quality_tier_rank


def _is_stable(evaluation: CandidateEvaluation) -> bool:
    ph = evaluation.phonon
    if ph is None:
        return False
    if ph.has_imaginary_modes:
        return False
    return bool(ph.dynamically_stable)


def extract_uncertainty(evaluation: CandidateEvaluation) -> float | None:
    """Return surrogate/performance uncertainty in [0, 1] if present.

    Sources (first hit wins):
    1. ``tc_lambda_surrogate["uncertainty"]``
    2. top-level ``evaluation.metadata``-style is not used (schema-stable path)
    """
    surr = getattr(evaluation, "tc_lambda_surrogate", None)
    if not isinstance(surr, dict):
        return None
    raw = surr.get("uncertainty")
    if raw is None:
        return None
    try:
        u = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(u):
        return None
    return max(0.0, min(1.0, u))


def normalize_performance(
    performance_score: float | None,
    *,
    ceiling_K: float = 40.0,
    missing_default: float = 50.0,
) -> float:
    """Map Tc-like performance (K) to 0–100 using *ceiling_K*."""
    if performance_score is None:
        return float(missing_default)
    ceil = float(ceiling_K) if ceiling_K > 0 else 40.0
    return max(0.0, min(100.0, (float(performance_score) / ceil) * 100.0))


def ranking_axis_values(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> dict[str, float | None]:
    """Normalized axes used by the composite (before quality/hull penalties).

    Returns keys: ``performance_norm``, ``si_feasibility``, ``certainty_norm``,
    ``uncertainty`` (raw 0–1 or None).
    """
    config = config or RankingConfig()
    tier = evaluation.result_quality or "unknown"
    qcfg = config.quality or QualityConfig()

    if tier == "unreliable" and qcfg.unreliable_zero_performance:
        perf_norm = 0.0
    else:
        perf_norm = normalize_performance(
            evaluation.performance_score,
            ceiling_K=config.performance_ceiling_K,
        )

    if evaluation.si_feasibility is not None:
        si = float(evaluation.si_feasibility.total)
    else:
        si = 50.0

    u = extract_uncertainty(evaluation)
    certainty: float | None
    if u is not None:
        certainty = (1.0 - u) * 100.0
    else:
        certainty = None

    return {
        "performance_norm": perf_norm,
        "si_feasibility": si,
        "certainty_norm": certainty,
        "uncertainty": u,
    }


def compute_composite_breakdown(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> dict[str, Any]:
    """Compute composite score plus a transparent term breakdown.

    Returns a dict with at least ``composite`` (final 0–100-ish score after
    penalties) and axis / weight provenance for export.
    """
    config = config or RankingConfig()
    qcfg = config.quality or QualityConfig()
    tier = evaluation.result_quality or "unknown"
    axes = ranking_axis_values(evaluation, config)

    # Explicit None checks — valid 0.0 scores must not hit neutral fallbacks
    pn = axes["performance_norm"]
    perf_norm = 0.0 if pn is None else float(pn)
    si_raw = axes["si_feasibility"]
    si = 50.0 if si_raw is None else float(si_raw)
    certainty = axes["certainty_norm"]
    u = axes["uncertainty"]

    w_p = float(config.performance_weight)
    w_s = float(config.si_feasibility_weight)
    w_u = float(config.uncertainty_weight)

    # Unreliable + zero-performance: Si-only base (legacy behaviour)
    if tier == "unreliable" and qcfg.unreliable_zero_performance:
        pre_penalty = si
        used_weights = {"performance": 0.0, "si_feasibility": 1.0, "uncertainty": 0.0}
    else:
        terms: list[tuple[str, float, float]] = [
            ("performance", w_p, perf_norm),
            ("si_feasibility", w_s, si),
        ]
        if w_u > 0.0 and certainty is not None:
            terms.append(("uncertainty", w_u, float(certainty)))

        total_w = sum(w for _, w, _ in terms if w > 0)
        if total_w <= 0:
            pre_penalty = 0.5 * perf_norm + 0.5 * si
            used_weights = {
                "performance": 0.5,
                "si_feasibility": 0.5,
                "uncertainty": 0.0,
            }
        else:
            pre_penalty = sum(w * val for _, w, val in terms if w > 0) / total_w
            used_weights = {name: (w if w > 0 else 0.0) for name, w, _ in terms}
            if "uncertainty" not in used_weights:
                used_weights["uncertainty"] = 0.0

    composite = pre_penalty

    # Explicit quality penalties (in addition to dynamic stability / hull)
    if tier == "unreliable":
        composite *= float(qcfg.unreliable_performance_penalty)
    elif tier == "screening_suspect":
        composite *= float(qcfg.suspect_performance_penalty)

    if config.prefer_dynamically_stable and evaluation.phonon is not None:
        if evaluation.phonon.has_imaginary_modes or not evaluation.phonon.dynamically_stable:
            # Avoid double-counting if already unreliable from imaginary modes
            if tier not in {"unreliable"}:
                composite *= 0.5

    if config.prefer_low_hull:
        hull = evaluation.candidate.energy_above_hull_proxy
        if hull is None and evaluation.scf is not None:
            hull = evaluation.scf.energy_above_hull_eV_per_atom
        if hull is not None:
            # Soft demotion: ~0 at 0 eV/atom, ~15% at 0.25 eV/atom
            composite *= max(0.5, 1.0 - float(hull))

    return {
        "composite": round(composite, 4),
        "pre_penalty": round(pre_penalty, 4),
        "performance_norm": round(perf_norm, 4),
        "si_feasibility": round(si, 4),
        "certainty_norm": (
            round(float(certainty), 4) if certainty is not None else None
        ),
        "uncertainty": float(u) if u is not None else None,
        "weights_used": used_weights,
        "config_weights": config.active_weights(),
    }


def compute_composite_score(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> float:
    """Compute a 0–100 composite score from performance + Si-feasibility.

    * ``performance_score`` is treated as a Tc-like value in kelvin and
      normalized against ``RankingConfig.performance_ceiling_K`` (default 40 K).
    * Optional certainty term when ``uncertainty_weight > 0`` and surrogate
      uncertainty is present (see :class:`RankingConfig`).
    * Missing fields fall back to neutral defaults so ranking never crashes
      on partial evaluations.
    * Result-quality tiers apply multiplicative penalties so inflated screening
      λ/Tc cannot dominate (see :class:`QualityConfig`).
    """
    return float(compute_composite_breakdown(evaluation, config)["composite"])


def _dominates(a: list[float], b: list[float]) -> bool:
    """Return True if objective vector *a* Pareto-dominates *b* (all maximize)."""
    if len(a) != len(b):
        raise ValueError("Pareto objective vectors must have equal length")
    ge_all = all(x >= y for x, y in zip(a, b, strict=True))
    gt_any = any(x > y for x, y in zip(a, b, strict=True))
    return ge_all and gt_any


def pareto_objectives(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> list[float] | None:
    """Primary axes for Pareto (maximize): performance, Si-total, [certainty].

    Returns ``None`` when any *required* axis is missing so incomplete rows are
    excluded from the front (they must not dominate or remain non-dominated by
    encoding missing values as ``-inf``). Certainty is required only when
    ``uncertainty_weight > 0``.
    """
    config = config or RankingConfig()
    if evaluation.performance_score is None:
        return None
    try:
        perf = float(evaluation.performance_score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(perf):
        return None

    if evaluation.si_feasibility is None:
        return None
    try:
        si = float(evaluation.si_feasibility.total)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(si):
        return None

    objs = [perf, si]
    if config.uncertainty_weight > 0.0:
        u = extract_uncertainty(evaluation)
        if u is None:
            return None
        objs.append(1.0 - u)
    return objs


def identify_pareto_front(
    evaluations: list[CandidateEvaluation],
    config: RankingConfig | None = None,
) -> list[bool]:
    """Return a bool per evaluation: True iff non-dominated on primary axes.

    Incomplete objective vectors (``pareto_objectives`` → ``None``) are never
    on the front and do not participate in dominance comparisons.
    """
    config = config or RankingConfig()
    if not evaluations:
        return []
    objs = [pareto_objectives(ev, config) for ev in evaluations]
    n = len(objs)
    on_front = [False] * n
    eligible = [i for i, o in enumerate(objs) if o is not None]
    for i in eligible:
        dominated = False
        for j in eligible:
            if i == j:
                continue
            # objs[j]/objs[i] are non-None by construction of eligible
            if _dominates(objs[j], objs[i]):  # type: ignore[arg-type]
                dominated = True
                break
        on_front[i] = not dominated
    return on_front


def rank_evaluations(
    evaluations: list[CandidateEvaluation],
    config: RankingConfig | None = None,
    *,
    stable_first: bool = False,
) -> list[CandidateEvaluation]:
    """Return a new list of evaluations sorted by composite score (desc).

    Applies :func:`apply_quality_assessment` first, then scores and ranks.
    Updates ``composite_score``, ``rank``, Pareto flag, ranking provenance,
    and quality fields on copies.

    Parameters
    ----------
    stable_first
        When True, all dynamically stable rows sort above unstable ones
        (useful for phonon-only stores where Si/composite ties are common).
        Unstable rows keep relative composite order among themselves.
    """
    config = config or RankingConfig()
    qcfg = config.quality or QualityConfig()
    weight_provenance = config.active_weights()

    assessed: list[CandidateEvaluation] = []
    for ev in evaluations:
        # Always re-assess so re-rank of store results stays honest
        assessed.append(apply_quality_assessment(ev, qcfg))

    scored: list[CandidateEvaluation] = []
    for ev in assessed:
        breakdown = compute_composite_breakdown(ev, config)
        scored.append(
            ev.model_copy(
                update={
                    "composite_score": breakdown["composite"],
                    "ranking_weights": dict(weight_provenance),
                    "composite_breakdown": {
                        "performance_norm": breakdown["performance_norm"],
                        "si_feasibility": breakdown["si_feasibility"],
                        "certainty_norm": breakdown["certainty_norm"],
                        "pre_penalty": breakdown["pre_penalty"],
                        "weights_used": breakdown["weights_used"],
                    },
                }
            )
        )

    # Pareto on raw primary axes (not quality-penalized composite)
    if config.pareto_enabled:
        flags = identify_pareto_front(scored, config)
        scored = [
            ev.model_copy(update={"on_pareto_front": flags[i]})
            for i, ev in enumerate(scored)
        ]
    else:
        scored = [
            ev.model_copy(update={"on_pareto_front": None}) for ev in scored
        ]

    # Sort: optional stable-first, then composite desc, quality tier, performance
    def sort_key(e: CandidateEvaluation) -> tuple:
        comp = e.composite_score if e.composite_score is not None else -1.0
        qrank = (
            quality_tier_rank(e.result_quality)
            if qcfg.prefer_higher_quality_tier
            else 0
        )
        perf = e.performance_score if e.performance_score is not None else -1.0
        stable_rank = 1 if _is_stable(e) else 0
        if stable_first:
            return (stable_rank, comp, qrank, perf)
        return (comp, qrank, perf)

    scored.sort(key=sort_key, reverse=True)

    ranked: list[CandidateEvaluation] = []
    for i, ev in enumerate(scored, start=1):
        ranked.append(ev.model_copy(update={"rank": i}))
    return ranked
