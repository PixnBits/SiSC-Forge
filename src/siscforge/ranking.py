"""Simple multi-objective ranking for Phase 0."""

from __future__ import annotations

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import RankingConfig


def compute_composite_score(
    evaluation: CandidateEvaluation,
    config: RankingConfig | None = None,
) -> float:
    """Compute a 0–100 composite score from performance + Si-feasibility.

    * ``performance_score`` is treated as a Tc-like value in kelvin and
      normalized against a 40 K ceiling (tunable later).
    * Missing fields fall back to neutral defaults so ranking never crashes
      on partial evaluations.
    """
    config = config or RankingConfig()

    perf = evaluation.performance_score
    if perf is None:
        perf_norm = 50.0
    else:
        perf_norm = max(0.0, min(100.0, (float(perf) / 40.0) * 100.0))

    if evaluation.si_feasibility is not None:
        si = float(evaluation.si_feasibility.total)
    else:
        si = 50.0

    w_p = config.performance_weight
    w_s = config.si_feasibility_weight
    total_w = w_p + w_s
    if total_w <= 0:
        composite = 0.5 * perf_norm + 0.5 * si
    else:
        composite = (w_p * perf_norm + w_s * si) / total_w

    if config.prefer_dynamically_stable and evaluation.phonon is not None:
        if evaluation.phonon.has_imaginary_modes or not evaluation.phonon.dynamically_stable:
            composite *= 0.5

    return round(composite, 4)


def rank_evaluations(
    evaluations: list[CandidateEvaluation],
    config: RankingConfig | None = None,
) -> list[CandidateEvaluation]:
    """Return a new list of evaluations sorted by composite score (desc).

    Updates ``composite_score`` and 1-based ``rank`` on each object (copies
    via ``model_copy`` so callers can keep the originals if needed).
    """
    config = config or RankingConfig()
    scored: list[CandidateEvaluation] = []
    for ev in evaluations:
        composite = compute_composite_score(ev, config)
        scored.append(
            ev.model_copy(update={"composite_score": composite})
        )

    scored.sort(
        key=lambda e: (
            e.composite_score if e.composite_score is not None else -1.0,
            e.performance_score if e.performance_score is not None else -1.0,
        ),
        reverse=True,
    )

    ranked: list[CandidateEvaluation] = []
    for i, ev in enumerate(scored, start=1):
        ranked.append(ev.model_copy(update={"rank": i}))
    return ranked
