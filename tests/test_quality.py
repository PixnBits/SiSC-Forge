"""Result-quality / trust layer tests."""

from __future__ import annotations

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import QualityConfig, RankingConfig
from siscforge.models.results import (
    ElectronPhononResult,
    PhononResult,
    SiFeasibilityScore,
)
from siscforge.quality import (
    FLAG_COARSE_GRIDS,
    FLAG_EPW_FAILED,
    FLAG_EPW_REMEDIATION_EXHAUSTED,
    FLAG_EXTREME_LAMBDA,
    FLAG_HIGH_LAMBDA,
    FLAG_IMAGINARY_MODES,
    FLAG_QUALITY_TAG_MOCK,
    FLAG_SCREENING_HIGH_LAMBDA,
    FLAG_WANNIER_RANDOM,
    apply_quality_assessment,
    assess_result_quality,
    screening_high_lambda_hard_zero,
)
from siscforge.ranking import (
    compute_composite_breakdown,
    compute_composite_score,
    pareto_objectives,
    rank_evaluations,
)


def _cand(formula: str = "NbN") -> StructureCandidate:
    return StructureCandidate(formula=formula, material_family="tm_nitride")


def _ev(
    *,
    formula: str = "NbN",
    lam: float | None = 1.2,
    tc: float | None = 15.0,
    stable: bool = True,
    min_freq: float = 100.0,
    quality_tag: str = "screening",
    si: float = 55.0,
    status: str = "ok",
) -> CandidateEvaluation:
    ph = PhononResult(
        min_frequency_cm1=min_freq if stable else -20.0,
        has_imaginary_modes=not stable,
        dynamically_stable=stable,
        status="ok",
        quality_tag=quality_tag,  # type: ignore[arg-type]
    )
    eph = None
    if lam is not None:
        eph = ElectronPhononResult(
            lambda_total=lam,
            omega_log=250.0,
            mu_star=0.1,
            Tc_allen_dynes=tc,
            Tc_eliashberg=tc,
            converged=True,
            wannier_ok=True,
            status="ok",
            quality_tag=quality_tag,  # type: ignore[arg-type]
            alpha2F_summary={"method": "epw", "material_notes": "proj=random"},
        )
    return CandidateEvaluation(
        candidate=_cand(formula),
        phonon=ph,
        electron_phonon=eph,
        si_feasibility=SiFeasibilityScore(total=si),
        performance_score=tc,
        performance_score_source="epw",
        status=status,
        calculator_name="qe-epw",
    )


def test_clean_lambda_not_suspect() -> None:
    a = assess_result_quality(_ev(lam=1.2, tc=16.0, stable=True))
    assert a.result_quality in {"screening", "production"}
    assert FLAG_HIGH_LAMBDA not in a.quality_flags
    assert FLAG_IMAGINARY_MODES not in a.quality_flags


def test_high_lambda_suspect() -> None:
    # Mild elevated band: lambda_suspect_above=3.0, below unreliable (5.0).
    a = assess_result_quality(_ev(lam=4.0, tc=25.0, stable=True))
    assert a.result_quality == "screening_suspect"
    assert FLAG_HIGH_LAMBDA in a.quality_flags
    assert FLAG_EXTREME_LAMBDA not in a.quality_flags


def test_extreme_lambda_unreliable() -> None:
    a = assess_result_quality(_ev(lam=12.0, tc=80.0, stable=True))
    assert a.result_quality == "unreliable"
    assert FLAG_EXTREME_LAMBDA in a.quality_flags
    assert FLAG_HIGH_LAMBDA in a.quality_flags


def test_imaginary_modes_unreliable() -> None:
    a = assess_result_quality(
        _ev(lam=1.5, tc=18.0, stable=False),
        QualityConfig(imaginary_modes_unreliable=True),
    )
    assert a.result_quality == "unreliable"
    assert FLAG_IMAGINARY_MODES in a.quality_flags


def test_ranking_suspect_does_not_beat_clean() -> None:
    """High-Tc inflated-λ suspect must not rank above moderate clean Tc."""
    clean = _ev(formula="NbN", lam=1.1, tc=16.0, si=50.0, stable=True)
    suspect = _ev(
        formula="Nb0.5Ti0.5N",
        lam=4.0,
        tc=45.0,  # inflated screening Tc
        si=50.0,
        stable=True,
    )
    ranked = rank_evaluations([suspect, clean], RankingConfig())
    # After quality assessment + penalties, clean should win
    assert ranked[0].candidate.formula == "NbN"
    assert ranked[0].result_quality in {"screening", "production"}
    assert ranked[1].result_quality == "screening_suspect"
    # Raw Tc still stored on suspect
    assert ranked[1].performance_score == 45.0
    assert ranked[1].electron_phonon is not None
    assert ranked[1].electron_phonon.lambda_total == 4.0


def test_ranking_extreme_lambda_si_only() -> None:
    bad = _ev(lam=12.0, tc=90.0, si=40.0)
    ok = _ev(lam=1.2, tc=14.0, si=55.0)
    ranked = rank_evaluations([bad, ok])
    assert ranked[0].candidate.formula == "NbN"
    assert ranked[1].result_quality == "unreliable"
    # Extreme still has raw data
    assert ranked[1].performance_score == 90.0


def test_composite_penalty_applied() -> None:
    cfg = RankingConfig()
    clean = assess_result_quality(_ev(lam=1.2, tc=20.0, si=50.0))
    from siscforge.quality import apply_quality_assessment

    clean_ev = apply_quality_assessment(_ev(lam=1.2, tc=20.0, si=50.0))
    sus_ev = apply_quality_assessment(_ev(lam=4.0, tc=20.0, si=50.0))
    c_clean = compute_composite_score(clean_ev, cfg)
    c_sus = compute_composite_score(sus_ev, cfg)
    assert c_sus < c_clean
    assert c_sus <= c_clean * (cfg.quality.suspect_performance_penalty + 0.05)


def test_quality_fields_on_ranked_export_shape() -> None:
    ranked = rank_evaluations([_ev(lam=4.0, tc=30.0)])
    assert ranked[0].result_quality == "screening_suspect"
    assert ranked[0].quality_flags
    assert ranked[0].quality_notes
    assert ranked[0].electron_phonon is not None
    assert ranked[0].electron_phonon.result_quality == "screening_suspect"


def test_mock_results_unreliable() -> None:
    """quality_tag=mock / FLAG_QUALITY_TAG_MOCK / src=mock must not stay screening."""
    ev = _ev(lam=1.2, tc=20.0, quality_tag="mock", status="mock")
    ev = ev.model_copy(update={"performance_score_source": "mock"})
    a = assess_result_quality(ev)
    assert a.result_quality == "unreliable"
    assert FLAG_QUALITY_TAG_MOCK in a.quality_flags
    assert "mock" in a.quality_notes.lower()

    ranked = rank_evaluations([ev, _ev(lam=1.2, tc=16.0, si=50.0)])
    assert ranked[0].result_quality in {"screening", "production"}
    assert ranked[-1].result_quality == "unreliable"
    # Composite zeros performance for unreliable (Si-only × penalty).
    mock_ranked = next(r for r in ranked if r.performance_score_source == "mock")
    assert mock_ranked.composite_score is not None
    assert mock_ranked.composite_score < 20.0


def test_lambda_six_unreliable() -> None:
    """Documented pathological band λ≈6 is unreliable, not screening_suspect."""
    a = assess_result_quality(_ev(lam=6.0, tc=40.0, stable=True))
    assert a.result_quality == "unreliable"
    assert FLAG_EXTREME_LAMBDA in a.quality_flags
    assert FLAG_HIGH_LAMBDA in a.quality_flags

    ranked = rank_evaluations([_ev(lam=6.0, tc=40.0, si=50.0)])
    assert ranked[0].result_quality == "unreliable"
    assert ranked[0].electron_phonon is not None
    assert ranked[0].electron_phonon.result_quality == "unreliable"


def test_high_lambda_random_proj_hard_zero_flag() -> None:
    """#44: random-Wannier + high λ is flagged for hard-zero performance."""
    a = assess_result_quality(_ev(lam=4.0, tc=40.0, si=95.0))
    assert FLAG_HIGH_LAMBDA in a.quality_flags
    assert FLAG_WANNIER_RANDOM in a.quality_flags
    assert FLAG_COARSE_GRIDS in a.quality_flags
    assert FLAG_SCREENING_HIGH_LAMBDA in a.quality_flags
    assert screening_high_lambda_hard_zero(a.quality_flags)
    assert "hard-zero" in a.quality_notes.lower()


def test_high_lambda_random_proj_cannot_dominate_high_si() -> None:
    """#44: ceiling-saturated screening λ + high Si cannot beat a clean row."""
    clean = _ev(formula="NbN", lam=1.1, tc=16.0, si=50.0, stable=True)
    # Pairing/EPW ceiling maps extreme screening λ onto 40 K; Si is excellent.
    inflated = _ev(
        formula="Nb0.5Ti0.5N",
        lam=4.0,
        tc=40.0,
        si=95.0,
        stable=True,
    )
    ranked = rank_evaluations([inflated, clean], RankingConfig())
    assert ranked[0].candidate.formula == "NbN"
    assert ranked[1].candidate.formula == "Nb0.5Ti0.5N"
    assert FLAG_SCREENING_HIGH_LAMBDA in ranked[1].quality_flags
    bd = ranked[1].composite_breakdown
    assert bd is not None
    assert bd["performance_norm"] == 0.0
    assert bd["performance_hard_zeroed"] is True
    assert bd["performance_hard_zero_reason"] == FLAG_SCREENING_HIGH_LAMBDA
    # Soft-multiply of a 40 K / 95 Si blend would still beat clean 16 K / 50 Si.
    inflated_q = apply_quality_assessment(inflated)
    bd_infl = compute_composite_breakdown(inflated_q, RankingConfig())
    clean_q = apply_quality_assessment(clean)
    bd_clean = compute_composite_breakdown(clean_q, RankingConfig())
    assert bd_infl["composite"] < bd_clean["composite"]
    # Excluded from Pareto so raw 40 K cannot dominate the front.
    assert pareto_objectives(inflated_q, RankingConfig()) is None


def test_hard_zero_disabled_keeps_soft_penalty() -> None:
    """QualityConfig knob restores multiply-only behaviour."""
    cfg = RankingConfig(
        quality=QualityConfig(hard_zero_screening_high_lambda=False)
    )
    ev = apply_quality_assessment(
        _ev(lam=4.0, tc=20.0, si=50.0),
        cfg.quality,
    )
    assert FLAG_SCREENING_HIGH_LAMBDA not in ev.quality_flags
    assert not screening_high_lambda_hard_zero(ev.quality_flags, config=cfg.quality)
    bd = compute_composite_breakdown(ev, cfg)
    assert bd["performance_hard_zeroed"] is False
    assert bd["performance_norm"] > 0.0


def test_epw_remediation_exhaustion_surfaces_as_hard_flag() -> None:
    """#49: terminal EPW-blocked after remediation is a durable quality flag."""
    ev = _ev(lam=None, tc=None, status="failed")
    assert ev.electron_phonon is None
    eph = ElectronPhononResult(
        status="failed",
        quality_tag="screening",
        quality_flags=[FLAG_EPW_REMEDIATION_EXHAUSTED, FLAG_EPW_FAILED],
        quality_notes="EPW remediation exhausted",
        alpha2F_summary={"remediation_exhausted": True},
    )
    ev = ev.model_copy(
        update={
            "electron_phonon": eph.model_copy(update={"status": "ok"}),
            "quality_flags": [FLAG_EPW_REMEDIATION_EXHAUSTED],
            "status": "ok",
            "performance_score": 10.0,
        }
    )
    a = assess_result_quality(ev)
    assert FLAG_EPW_REMEDIATION_EXHAUSTED in a.quality_flags
    assert FLAG_EPW_FAILED in a.quality_flags
    assert "remediation exhausted" in a.quality_notes.lower()

    from siscforge.active_learning.training_set import promotion_eligibility

    ok, reason = promotion_eligibility(ev)
    assert not ok
    assert "epw_remediation_exhausted" in reason or "epw_failed" in reason
