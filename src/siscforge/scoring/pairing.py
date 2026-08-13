"""P3.4 — map DMFT pairing signals onto the common ``performance_score``.

The ranker (:mod:`siscforge.ranking`) treats ``performance_score`` as a
**Tc-like kelvin proxy** and normalizes it with
``RankingConfig.performance_ceiling_K`` (default 40 K). This module is the
only family-aware step: it turns a usable ``DMFTResult.leading_pairing_eigenvalue``
into that same kelvin axis so ranking / Pareto / export need **no**
nickelate-specific forks.

This is a **deterministic ranking proxy**, not a Tc calculation and not a
literature-validated pairing model. Mock eigenvalues are illustrative.

See ``docs/phase3-p34-pairing-score.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from siscforge.models.config import DMFTScoringConfig, RankingConfig

if TYPE_CHECKING:
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.results import DMFTResult

SOURCE_DMFT_PAIRING = "dmft_pairing"
SOURCE_DMFT_PAIRING_MOCK = "dmft_pairing_mock"
SOURCE_EPW = "epw"

REASON_OK = "ok"
REASON_NO_DMFT = "no_dmft"
REASON_DISABLED = "mapping_disabled"
REASON_MISSING = "missing_eigenvalue"
REASON_NONFINITE = "nonfinite_eigenvalue"
REASON_NEGATIVE = "negative_eigenvalue"
REASON_NOT_CONVERGED = "not_converged"
REASON_BAD_STATUS = "unreliable_status"
REASON_MOCK_DISALLOWED = "mock_disallowed"

_REFUSE_STATUS = frozenset({"failed", "skipped", "refused", "pending"})

# Default linear map: λ = 1.0 → 25 K, clamped to the 40 K ranking ceiling.
# Documented in docs/phase3-p34-pairing-score.md — not a fitted Tc model.
DEFAULT_KELVIN_PER_UNIT = 25.0
DEFAULT_SCORE_CEILING_K = 40.0


@dataclass(frozen=True)
class PairingMapResult:
    """Outcome of :func:`performance_score_from_pairing`.

    ``usable`` is True only when ``score`` is a finite kelvin proxy that may
    populate ``CandidateEvaluation.performance_score``.
    """

    score: float | None
    source: str | None
    eigenvalue: float | None
    quality_factor: float
    note: str
    reason: str
    symmetry: str | None
    usable: bool


@dataclass(frozen=True)
class PerformanceDecision:
    """Resolved headline performance for one evaluation (P3.4 precedence)."""

    score: float | None
    source: str | None
    note: str
    reason: str
    changed: bool
    pairing: PairingMapResult | None = None


def _same_score(a: float | None, b: float | None) -> bool:
    """True when *a* and *b* are the same finite headline score.

    ``None`` matches only ``None``. Non-finite values (NaN / ±inf) never
    compare equal, so a re-apply will rewrite a corrupt stored score.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(fa) or not math.isfinite(fb):
        return False
    return fa == fb


def mock_pairing_headline_count(evaluations: list[CandidateEvaluation]) -> int:
    """How many rows have a mock DMFT pairing headline source."""
    n = 0
    for ev in evaluations:
        if getattr(ev, "performance_score_source", None) == SOURCE_DMFT_PAIRING_MOCK:
            n += 1
    return n


def mock_ranking_warning(evaluations: list[CandidateEvaluation]) -> str | None:
    """Operator banner when illustrative mock pairing scores are ranked.

    Returns ``None`` when no ``dmft_pairing_mock`` headlines are present.
    """
    n = mock_pairing_headline_count(evaluations)
    if n <= 0:
        return None
    noun = "row" if n == 1 else "rows"
    return (
        f"{n} ranked {noun} use illustrative mock DMFT pairing scores "
        "(source=dmft_pairing_mock) — not quantitative Tc. Ranking is for "
        "prioritization only; do not cite these numbers."
    )


def _is_mock_dmft(dmft: DMFTResult) -> bool:
    return (
        (dmft.solver or "").lower() == "mock"
        or (dmft.quality_tag or "") == "mock"
        or (dmft.status or "").lower() == "mock"
    )


def _is_mock_eph(eph: object) -> bool:
    status = str(getattr(eph, "status", "") or "").lower()
    tag = str(getattr(eph, "quality_tag", "") or "").lower()
    return status == "mock" or tag == "mock"


def _quality_factor(dmft: DMFTResult, scoring: DMFTScoringConfig) -> tuple[float, list[str]]:
    """Optional soft demotion from occupancy / mass enhancement.

    Not a physics model — only a light penalty when numbers look wildly
    unphysical so a mock or drop-in parse cannot silently dominate.
    """
    if not scoring.quality_demotion:
        return 1.0, []
    factor = 1.0
    bits: list[str] = []

    mass = dmft.mass_enhancement
    cap = float(scoring.mass_enhancement_soft_cap)
    if mass is not None and math.isfinite(mass) and cap > 0 and mass > cap:
        # 1.0 at the cap, −0.10 per extra cap, floor 0.70
        extra = float(mass) / cap - 1.0
        factor *= max(0.70, 1.0 - 0.10 * extra)
        bits.append(f"m*/m={mass:g}>{cap:g}")

    filling = dmft.filling
    if filling is None and dmft.occupancy_summary:
        try:
            filling = float(sum(dmft.occupancy_summary.values()))
        except (TypeError, ValueError):
            filling = None
    lo = float(scoring.occupancy_soft_min)
    hi = float(scoring.occupancy_soft_max)
    if filling is not None and math.isfinite(filling) and not (lo <= float(filling) <= hi):
        factor *= 0.90
        bits.append(f"filling={filling:g}∉[{lo:g},{hi:g}]")

    return max(0.70, min(1.0, factor)), bits


def performance_score_from_pairing(
    dmft: DMFTResult | None,
    scoring: DMFTScoringConfig | None = None,
) -> PairingMapResult:
    """Map a ``DMFTResult`` onto a Tc-like kelvin ``performance_score``.

    Formula (defaults)::

        score_K = clamp((λ − threshold) × kelvin_per_unit × Q, 0, ceiling_K)

    with ``threshold=0``, ``kelvin_per_unit=25``, ``ceiling_K=40``,
    and ``Q ∈ [0.70, 1]`` an optional soft quality factor.

    ``pairing_symmetry`` is **metadata only** and never enters the score.

    Returns a :class:`PairingMapResult`. ``usable`` is False (score None)
    when pairing is missing, non-finite, negative, non-converged (default),
    refused/failed, or mock-disallowed.
    """
    scoring = scoring or DMFTScoringConfig()
    empty = PairingMapResult(
        score=None,
        source=None,
        eigenvalue=None,
        quality_factor=1.0,
        note="",
        reason=REASON_NO_DMFT,
        symmetry=None,
        usable=False,
    )
    if dmft is None:
        return empty
    if not scoring.enabled:
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=dmft.leading_pairing_eigenvalue,
            quality_factor=1.0,
            note="DMFT pairing→performance mapping disabled",
            reason=REASON_DISABLED,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )

    raw = dmft.leading_pairing_eigenvalue
    if raw is None:
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=None,
            quality_factor=1.0,
            note="no leading_pairing_eigenvalue",
            reason=REASON_MISSING,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )
    try:
        eig = float(raw)
    except (TypeError, ValueError):
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=None,
            quality_factor=1.0,
            note="leading_pairing_eigenvalue is not numeric",
            reason=REASON_NONFINITE,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )
    if not math.isfinite(eig):
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=None,
            quality_factor=1.0,
            note="leading_pairing_eigenvalue is not finite",
            reason=REASON_NONFINITE,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )
    if eig < 0.0:
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=eig,
            quality_factor=1.0,
            note="negative pairing eigenvalue is not mapped",
            reason=REASON_NEGATIVE,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )

    status = (dmft.status or "").lower()
    if status in _REFUSE_STATUS:
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=eig,
            quality_factor=1.0,
            note=f"DMFT status={dmft.status} is not scored",
            reason=REASON_BAD_STATUS,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )
    if scoring.require_converged and not bool(dmft.converged):
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=eig,
            quality_factor=1.0,
            note="DMFT pairing ignored: not converged",
            reason=REASON_NOT_CONVERGED,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )

    is_mock = _is_mock_dmft(dmft)
    if is_mock and not scoring.allow_mock:
        return PairingMapResult(
            score=None,
            source=None,
            eigenvalue=eig,
            quality_factor=1.0,
            note="mock DMFT pairing is not mapped (allow_mock=false)",
            reason=REASON_MOCK_DISALLOWED,
            symmetry=dmft.pairing_symmetry,
            usable=False,
        )

    q, qbits = _quality_factor(dmft, scoring)
    scale = float(scoring.kelvin_per_unit)
    thresh = float(scoring.eigenvalue_threshold)
    ceiling = float(scoring.score_ceiling_K)
    if not math.isfinite(scale) or scale < 0.0:
        scale = DEFAULT_KELVIN_PER_UNIT
    if not math.isfinite(thresh):
        thresh = 0.0
    if not math.isfinite(ceiling) or ceiling <= 0.0:
        ceiling = DEFAULT_SCORE_CEILING_K

    raw_score = (eig - thresh) * scale * q
    score = max(0.0, min(ceiling, raw_score))
    source = SOURCE_DMFT_PAIRING_MOCK if is_mock else SOURCE_DMFT_PAIRING
    extras = f"{scale:g} K/unit, ceiling {ceiling:g} K"
    if q < 1.0:
        extras += f", Q={q:.2f}"
        if qbits:
            extras += f" [{', '.join(qbits)}]"
    extras += "; not Eliashberg Tc"
    if is_mock:
        extras += "; illustrative mock, not literature-validated"
    note = (
        f"performance_score from DMFT pairing λ={eig:g} → {score:.2f} K "
        f"proxy ({extras})"
    )
    return PairingMapResult(
        score=float(score),
        source=source,
        eigenvalue=eig,
        quality_factor=q,
        note=note,
        reason=REASON_OK,
        symmetry=dmft.pairing_symmetry,
        usable=True,
    )


def trusted_epw_tc_K(evaluation: CandidateEvaluation) -> float | None:
    """Return a finite Eliashberg/Allen–Dynes Tc when it is trusted enough.

    Mock / placeholder e-ph results are **not** trusted: they must not
    silently beat a real (or mock-tagged) DMFT pairing signal. Unreliable
    EPW (pathological λ) is also refused. Screening-quality real EPW with
    ``status=ok`` **is** trusted (default precedence).
    """
    eph = getattr(evaluation, "electron_phonon", None)
    if eph is None:
        return None
    if _is_mock_eph(eph):
        return None
    status = (getattr(eph, "status", None) or "").lower()
    if status in _REFUSE_STATUS:
        return None
    best = eph.best_tc_K() if hasattr(eph, "best_tc_K") else None
    if best is None:
        return None
    try:
        tc = float(best)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(tc) or tc < 0.0:
        return None
    eph_rq = getattr(eph, "result_quality", None)
    ev_rq = getattr(evaluation, "result_quality", None)
    if eph_rq == "unreliable" or ev_rq == "unreliable":
        return None
    return tc


def resolve_performance_score(
    evaluation: CandidateEvaluation,
    *,
    scoring: DMFTScoringConfig | None = None,
    ranking: RankingConfig | None = None,
) -> PerformanceDecision:
    """Apply documented precedence and return the headline score decision.

    Default precedence (``ranking.performance_precedence``):

    1. Trusted production/screening EPW Eliashberg / Allen–Dynes Tc
    2. DMFT pairing-derived kelvin proxy (if usable)
    3. Existing evaluation score (mock EPW, surrogate, or unset)

    Does not mutate *evaluation*.
    """
    scoring = scoring or DMFTScoringConfig()
    ranking = ranking or RankingConfig()
    precedence = getattr(ranking, "performance_precedence", None) or "epw_then_dmft"

    pairing = performance_score_from_pairing(getattr(evaluation, "dmft", None), scoring)
    epw_tc = trusted_epw_tc_K(evaluation)
    existing_score = evaluation.performance_score
    existing_source = evaluation.performance_score_source

    def _epw_decision() -> PerformanceDecision:
        assert epw_tc is not None
        changed = (
            not _same_score(existing_score, epw_tc) or existing_source != SOURCE_EPW
        )
        return PerformanceDecision(
            score=float(epw_tc),
            source=SOURCE_EPW,
            note="trusted EPW Eliashberg/Allen–Dynes Tc takes precedence",
            reason="epw",
            changed=changed,
            pairing=pairing,
        )

    def _dmft_decision() -> PerformanceDecision:
        assert pairing.usable and pairing.score is not None
        changed = (
            not _same_score(existing_score, pairing.score)
            or existing_source != pairing.source
        )
        return PerformanceDecision(
            score=float(pairing.score),
            source=pairing.source,
            note=pairing.note,
            reason="dmft_pairing",
            changed=changed,
            pairing=pairing,
        )

    def _keep() -> PerformanceDecision:
        return PerformanceDecision(
            score=existing_score,
            source=existing_source,
            note="",
            reason="keep_existing",
            changed=False,
            pairing=pairing,
        )

    if not scoring.enabled:
        return _keep()

    if precedence == "epw_only":
        if epw_tc is not None:
            return _epw_decision()
        return _keep()
    if precedence == "dmft_only":
        if pairing.usable:
            return _dmft_decision()
        return _keep()
    if precedence == "dmft_then_epw":
        if pairing.usable:
            return _dmft_decision()
        if epw_tc is not None:
            return _epw_decision()
        return _keep()

    # Default: epw_then_dmft
    if epw_tc is not None:
        return _epw_decision()
    if pairing.usable:
        return _dmft_decision()
    return _keep()


def apply_performance_score(
    evaluation: CandidateEvaluation,
    *,
    scoring: DMFTScoringConfig | None = None,
    ranking: RankingConfig | None = None,
) -> CandidateEvaluation:
    """Return a copy of *evaluation* with headline performance applied.

    Idempotent when the decision matches the current score/source.

    Calculators call this with *scoring* only (default
    ``epw_then_dmft``). Campaign ``ranking.performance_precedence`` is
    applied later by CLI ``_finalize_eval`` / ``rank --config``.
    """
    decision = resolve_performance_score(
        evaluation, scoring=scoring, ranking=ranking
    )
    if not decision.changed:
        return evaluation
    updates: dict = {
        "performance_score": decision.score,
        "performance_score_source": decision.source,
    }
    if decision.note and decision.reason == "dmft_pairing":
        notes = (evaluation.notes or "").strip()
        if decision.note not in notes:
            updates["notes"] = f"{notes}; {decision.note}" if notes else decision.note
    return evaluation.model_copy(update=updates)
