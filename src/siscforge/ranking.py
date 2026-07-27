"""Simple multi-objective ranking with result-quality / trust penalties."""

from __future__ import annotations

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import QualityConfig, RankingConfig
from siscforge.quality import apply_quality_assessment, quality_tier_rank


def compute_composite_score(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> float:
    """Compute a 0–100 composite score from performance + Si-feasibility.

    * ``performance_score`` is treated as a Tc-like value in kelvin and
      normalized against a 40 K ceiling (tunable later).
    * Missing fields fall back to neutral defaults so ranking never crashes
      on partial evaluations.
    * Result-quality tiers apply multiplicative penalties so inflated screening
      λ/Tc cannot dominate (see :class:`QualityConfig`).
    """
    config = config or RankingConfig()
    qcfg = config.quality or QualityConfig()

    perf = evaluation.performance_score
    tier = evaluation.result_quality or "unknown"

    # Unreliable: drop performance term (Si-only ranking) by default
    if tier == "unreliable" and qcfg.unreliable_zero_performance:
        perf_norm = 0.0
    elif perf is None:
        perf_norm = 50.0
    else:
        perf_norm = max(0.0, min(100.0, (float(perf) / 40.0) * 100.0))

    if evaluation.si_feasibility is not None:
        si = float(evaluation.si_feasibility.total)
    else:
        si = 50.0

    w_p = config.performance_weight
    w_s = config.si_feasibility_weight
    # When performance is zeroed for unreliable, still normalize weights
    if tier == "unreliable" and qcfg.unreliable_zero_performance:
        composite = si  # Si-feasibility only
    else:
        total_w = w_p + w_s
        if total_w <= 0:
            composite = 0.5 * perf_norm + 0.5 * si
        else:
            composite = (w_p * perf_norm + w_s * si) / total_w

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

    return round(composite, 4)


def rank_evaluations(
    evaluations: list[CandidateEvaluation],
    config: RankingConfig | None = None,
) -> list[CandidateEvaluation]:
    """Return a new list of evaluations sorted by composite score (desc).

    Applies :func:`apply_quality_assessment` first, then scores and ranks.
    Updates ``composite_score``, ``rank``, and quality fields on copies.
    """
    config = config or RankingConfig()
    qcfg = config.quality or QualityConfig()

    assessed: list[CandidateEvaluation] = []
    for ev in evaluations:
        # Always re-assess so re-rank of store results stays honest
        assessed.append(apply_quality_assessment(ev, qcfg))

    scored: list[CandidateEvaluation] = []
    for ev in assessed:
        composite = compute_composite_score(ev, config)
        scored.append(ev.model_copy(update={"composite_score": composite}))

    # Sort: composite desc, then quality tier desc, then raw performance desc
    def sort_key(e: CandidateEvaluation) -> tuple:
        comp = e.composite_score if e.composite_score is not None else -1.0
        qrank = (
            quality_tier_rank(e.result_quality)
            if qcfg.prefer_higher_quality_tier
            else 0
        )
        perf = e.performance_score if e.performance_score is not None else -1.0
        return (comp, qrank, perf)

    scored.sort(key=sort_key, reverse=True)

    ranked: list[CandidateEvaluation] = []
    for i, ev in enumerate(scored, start=1):
        ranked.append(ev.model_copy(update={"rank": i}))
    return ranked
