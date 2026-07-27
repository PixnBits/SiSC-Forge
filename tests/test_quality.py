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
    FLAG_EXTREME_LAMBDA,
    FLAG_HIGH_LAMBDA,
    FLAG_IMAGINARY_MODES,
    assess_result_quality,
)
from siscforge.ranking import compute_composite_score, rank_evaluations


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
    a = assess_result_quality(_ev(lam=6.0, tc=40.0, stable=True))
    assert a.result_quality == "screening_suspect"
    assert FLAG_HIGH_LAMBDA in a.quality_flags


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
        lam=6.5,
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
    assert ranked[1].electron_phonon.lambda_total == 6.5


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
    sus_ev = apply_quality_assessment(_ev(lam=5.0, tc=20.0, si=50.0))
    c_clean = compute_composite_score(clean_ev, cfg)
    c_sus = compute_composite_score(sus_ev, cfg)
    assert c_sus < c_clean
    assert c_sus <= c_clean * (cfg.quality.suspect_performance_penalty + 0.05)


def test_quality_fields_on_ranked_export_shape() -> None:
    ranked = rank_evaluations([_ev(lam=6.0, tc=30.0)])
    assert ranked[0].result_quality == "screening_suspect"
    assert ranked[0].quality_flags
    assert ranked[0].quality_notes
    assert ranked[0].electron_phonon is not None
    assert ranked[0].electron_phonon.result_quality == "screening_suspect"
