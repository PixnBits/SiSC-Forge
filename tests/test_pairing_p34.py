"""P3.4 — DMFT pairing eigenvalue → performance_score."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from siscforge.calculators import get
from siscforge.export import write_evaluations_csv, write_synthesis_cards
from siscforge.models import (
    CampaignConfig,
    CandidateEvaluation,
    DFTConfig,
    DMFTConfig,
    DMFTResult,
    DMFTScoringConfig,
    ElectronPhononResult,
    RankingConfig,
    SiFeasibilityScore,
    StructureCandidate,
)
from siscforge.ranking import identify_pareto_front, rank_evaluations
from siscforge.scoring.pairing import (
    REASON_BAD_STATUS,
    REASON_MISSING,
    REASON_MOCK_DISALLOWED,
    REASON_NEGATIVE,
    REASON_NONFINITE,
    REASON_NOT_CONVERGED,
    SOURCE_DMFT_PAIRING,
    SOURCE_DMFT_PAIRING_MOCK,
    apply_performance_score,
    performance_score_from_pairing,
    resolve_performance_score,
    trusted_epw_tc_K,
)


def _dmft(
    *,
    eig: float | None = 1.0,
    sym: str | None = "d_x2-y2",
    converged: bool = True,
    status: str = "ok",
    quality_tag: str = "screening",
    solver: str = "solid_dmft",
    filling: float | None = 8.8,
    mass: float | None = 3.0,
) -> DMFTResult:
    return DMFTResult(
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        converged=converged,
        solver=solver,
        leading_pairing_eigenvalue=eig,
        pairing_symmetry=sym,
        filling=filling,
        mass_enhancement=mass,
        U_eV=5.0,
        J_eV=0.8,
    )


def _eph(*, tc: float = 18.0, status: str = "ok", quality_tag: str = "screening"):
    return ElectronPhononResult(
        lambda_total=1.1,
        omega_log=250.0,
        mu_star=0.1,
        Tc_allen_dynes=tc,
        Tc_eliashberg=tc,
        converged=True,
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
    )


def _ev(
    *,
    formula: str = "NdNiO2",
    family: str = "nickelate",
    dmft: DMFTResult | None = None,
    eph: ElectronPhononResult | None = None,
    tc: float | None = None,
    source: str | None = None,
    si: float = 40.0,
    cid: str | None = None,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=StructureCandidate(
            formula=formula,
            material_family=family,  # type: ignore[arg-type]
            candidate_id=cid or f"id-{formula}",
        ),
        electron_phonon=eph,
        dmft=dmft,
        si_feasibility=SiFeasibilityScore(total=si),
        performance_score=tc,
        performance_score_source=source,
        status="ok" if eph is not None else ("mock" if dmft else "pending"),
    )


# ---------------------------------------------------------------------------
# Pure mapping
# ---------------------------------------------------------------------------


def test_typical_eigenvalue_lands_in_expected_band() -> None:
    got = performance_score_from_pairing(_dmft(eig=1.0))
    assert got.usable is True
    assert got.score == pytest.approx(25.0)
    assert got.source == SOURCE_DMFT_PAIRING
    assert got.symmetry == "d_x2-y2"
    assert got.reason == "ok"
    # mid conventional band (default ceiling 40 K)
    assert 15.0 <= got.score <= 35.0


def test_linear_scale_and_ceiling() -> None:
    cfg = DMFTScoringConfig(kelvin_per_unit=25.0, score_ceiling_K=40.0)
    lo = performance_score_from_pairing(_dmft(eig=0.4), cfg)
    hi = performance_score_from_pairing(_dmft(eig=2.0), cfg)
    assert lo.score == pytest.approx(10.0)
    assert hi.score == pytest.approx(40.0)  # clamped


def test_zero_eigenvalue_is_finite_zero() -> None:
    got = performance_score_from_pairing(_dmft(eig=0.0))
    assert got.usable is True
    assert got.score == pytest.approx(0.0)


def test_missing_eigenvalue_no_score() -> None:
    got = performance_score_from_pairing(_dmft(eig=None))
    assert got.usable is False
    assert got.score is None
    assert got.reason == REASON_MISSING


def test_nonfinite_and_negative_no_score() -> None:
    nan = performance_score_from_pairing(_dmft(eig=float("nan")))
    inf = performance_score_from_pairing(_dmft(eig=float("inf")))
    neg = performance_score_from_pairing(_dmft(eig=-0.2))
    assert nan.reason == REASON_NONFINITE and nan.usable is False
    assert inf.reason == REASON_NONFINITE and inf.usable is False
    assert neg.reason == REASON_NEGATIVE and neg.usable is False


def test_not_converged_and_bad_status_no_score() -> None:
    unconv = performance_score_from_pairing(_dmft(eig=1.1, converged=False))
    refused = performance_score_from_pairing(
        _dmft(eig=1.1, status="refused", converged=False)
    )
    failed = performance_score_from_pairing(
        _dmft(eig=1.1, status="failed", converged=False)
    )
    assert unconv.reason == REASON_NOT_CONVERGED
    assert refused.reason == REASON_BAD_STATUS
    assert failed.reason == REASON_BAD_STATUS


def test_mock_tag_uses_mock_source() -> None:
    got = performance_score_from_pairing(
        _dmft(eig=0.8, status="mock", quality_tag="mock", solver="mock")
    )
    assert got.usable is True
    assert got.source == SOURCE_DMFT_PAIRING_MOCK
    assert "illustrative mock" in got.note
    assert "not literature-validated" in got.note


def test_mock_disallowed() -> None:
    cfg = DMFTScoringConfig(allow_mock=False)
    got = performance_score_from_pairing(
        _dmft(eig=0.8, status="mock", quality_tag="mock", solver="mock"),
        cfg,
    )
    assert got.usable is False
    assert got.reason == REASON_MOCK_DISALLOWED


def test_mapping_disabled() -> None:
    got = performance_score_from_pairing(_dmft(eig=1.0), DMFTScoringConfig(enabled=False))
    assert got.usable is False
    assert got.score is None


def test_symmetry_is_metadata_only() -> None:
    a = performance_score_from_pairing(_dmft(eig=1.0, sym="d_x2-y2"))
    b = performance_score_from_pairing(_dmft(eig=1.0, sym="s"))
    assert a.score == b.score
    assert a.symmetry != b.symmetry


def test_soft_quality_demotion_high_mass() -> None:
    clean = performance_score_from_pairing(_dmft(eig=1.0, mass=3.0))
    wild = performance_score_from_pairing(_dmft(eig=1.0, mass=20.0))
    assert wild.usable and clean.usable
    assert wild.quality_factor < 1.0
    assert wild.score < clean.score
    assert wild.score >= 0.70 * 25.0 - 1e-9


def test_none_dmft_no_score() -> None:
    got = performance_score_from_pairing(None)
    assert got.usable is False
    assert got.reason == "no_dmft"


# ---------------------------------------------------------------------------
# Precedence + apply
# ---------------------------------------------------------------------------


def test_trusted_epw_wins_over_dmft() -> None:
    ev = _ev(
        formula="NbN",
        family="tm_nitride",
        eph=_eph(tc=18.0, status="ok"),
        dmft=_dmft(eig=1.2),
        tc=18.0,
        source="epw",
        si=55.0,
    )
    assert trusted_epw_tc_K(ev) == pytest.approx(18.0)
    decision = resolve_performance_score(ev)
    assert decision.source == "epw"
    assert decision.score == pytest.approx(18.0)
    applied = apply_performance_score(ev)
    assert applied.performance_score == pytest.approx(18.0)
    assert applied.performance_score_source == "epw"


def test_mock_epw_does_not_beat_dmft_pairing() -> None:
    ev = _ev(
        eph=_eph(tc=12.0, status="mock", quality_tag="mock"),
        dmft=_dmft(eig=1.0, status="mock", quality_tag="mock", solver="mock"),
        tc=12.0,
        source="mock",
    )
    assert trusted_epw_tc_K(ev) is None
    applied = apply_performance_score(ev)
    assert applied.performance_score == pytest.approx(25.0)
    assert applied.performance_score_source == SOURCE_DMFT_PAIRING_MOCK


def test_precedence_override_dmft_then_epw() -> None:
    ev = _ev(
        eph=_eph(tc=18.0),
        dmft=_dmft(eig=1.0),
        tc=18.0,
        source="epw",
    )
    ranking = RankingConfig(performance_precedence="dmft_then_epw")
    applied = apply_performance_score(ev, ranking=ranking)
    assert applied.performance_score_source == SOURCE_DMFT_PAIRING
    assert applied.performance_score == pytest.approx(25.0)


def test_unreliable_epw_does_not_count_as_trusted() -> None:
    ev = _ev(
        eph=_eph(tc=80.0),
        dmft=_dmft(eig=0.8),
        tc=80.0,
        source="epw",
    )
    ev = ev.model_copy(update={"result_quality": "unreliable"})
    assert trusted_epw_tc_K(ev) is None
    applied = apply_performance_score(ev)
    assert applied.performance_score_source == SOURCE_DMFT_PAIRING


# ---------------------------------------------------------------------------
# Evaluation fixture + mixed ranking
# ---------------------------------------------------------------------------


def test_ndnio2_mock_gets_dmft_pairing_score() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="p34-ndnio2",
    )
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(enabled=True, solver="mock"),
    )
    ev = get("mock").run(cand, dft=dft)
    assert ev.dmft is not None
    assert ev.dmft.leading_pairing_eigenvalue is not None
    assert ev.performance_score is not None
    assert math.isfinite(ev.performance_score)
    assert ev.performance_score_source == SOURCE_DMFT_PAIRING_MOCK
    # Same 0–40 K-adjacent band the ranker already uses
    assert 0.0 <= ev.performance_score <= 40.0


def test_mixed_nitride_and_nickelate_rank_without_fork() -> None:
    nitride = _ev(
        formula="NbN",
        family="tm_nitride",
        eph=_eph(tc=18.0),
        tc=18.0,
        source="epw",
        si=55.0,
        cid="nbn",
    )
    nickelate = apply_performance_score(
        _ev(
            formula="NdNiO2",
            family="nickelate",
            dmft=_dmft(eig=1.0, status="mock", quality_tag="mock", solver="mock"),
            si=35.0,
            cid="ndnio2",
        )
    )
    assert nickelate.performance_score_source == SOURCE_DMFT_PAIRING_MOCK
    ranked = rank_evaluations([nitride, nickelate], RankingConfig())
    formulas = {e.candidate.formula for e in ranked}
    assert formulas == {"NbN", "NdNiO2"}
    assert all(e.rank is not None for e in ranked)
    assert all(e.composite_score is not None for e in ranked)
    flags = identify_pareto_front(ranked, RankingConfig())
    assert any(flags)


def test_conventional_mock_score_unchanged_when_dmft_off() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="conv-p34-nbn",
    )
    bare = get("mock").run(cand)
    with_cfg = get("mock").run(cand, dft=DFTConfig())
    assert bare.dmft is None
    assert with_cfg.dmft is None
    assert bare.performance_score == with_cfg.performance_score
    assert bare.performance_score_source == "mock"
    assert with_cfg.performance_score_source == "mock"


def test_scoring_disabled_keeps_mock_epw() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="p34-disabled",
    )
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(
            enabled=True,
            solver="mock",
            scoring=DMFTScoringConfig(enabled=False),
        ),
    )
    ev = get("mock").run(cand, dft=dft)
    assert ev.dmft is not None
    assert ev.dmft.leading_pairing_eigenvalue is not None
    assert ev.performance_score_source == "mock"


def test_yaml_knobs_load() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "p34",
            "dft": {
                "do_dmft": True,
                "dmft": {
                    "enabled": True,
                    "scoring": {
                        "kelvin_per_unit": 20.0,
                        "score_ceiling_K": 30.0,
                        "allow_mock": False,
                    },
                },
            },
            "ranking": {"performance_precedence": "dmft_then_epw"},
        }
    )
    assert cfg.dft.dmft.scoring.kelvin_per_unit == 20.0
    assert cfg.dft.dmft.scoring.score_ceiling_K == 30.0
    assert cfg.dft.dmft.scoring.allow_mock is False
    assert cfg.ranking.performance_precedence == "dmft_then_epw"


def test_example_yaml_loads_p34_knobs() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = CampaignConfig.from_yaml(root / "examples" / "ndnio2_dmft_mock.yaml")
    assert cfg.dft.dmft.scoring.enabled is True
    assert cfg.ranking.performance_precedence == "epw_then_dmft"


def test_export_cards_show_pairing_origin(tmp_path: Path) -> None:
    ev = apply_performance_score(
        _ev(
            dmft=_dmft(eig=1.0, status="mock", quality_tag="mock", solver="mock"),
            si=40.0,
        )
    )
    ranked = rank_evaluations([ev])
    cards = write_synthesis_cards(ranked, tmp_path / "cards.md", campaign_name="p34")
    text = cards.read_text()
    assert "dmft_pairing_mock" in text
    assert "performance origin" in text
    assert "illustrative" in text.lower()
    assert "P3.4" in text
    csv_path = write_evaluations_csv(ranked, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    assert "performance_score_source" in header
    assert "dmft_leading_pairing_eigenvalue" in header
    assert "dmft_pairing_mock" in csv_path.read_text()


def test_docs_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs" / "phase3-p34-pairing-score.md").read_text()
    assert "kelvin_per_unit" in doc
    assert "epw_then_dmft" in doc
    assert "not literature-validated" in doc
    assert "pairing_symmetry" in doc
