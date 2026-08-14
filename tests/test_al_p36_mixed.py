"""P3.6 — mixed conventional / unconventional acquisition pools."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.active_learning import (
    PromotionError,
    SurrogateRegistry,
    TrainingSetStore,
    acquisition_score,
    al_status,
    build_prioritization_record,
    derive_pool,
    prioritize_candidates,
    promote_evaluation,
    promotion_eligibility,
)
from siscforge.export import CSV_FIELDNAMES, write_evaluations_csv, write_synthesis_cards
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import (
    ActiveLearningConfig,
    ActiveLearningPoolQuotas,
    CampaignConfig,
)
from siscforge.models.results import DMFTResult, ElectronPhononResult, SiFeasibilityScore
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.surrogates.tc_lambda import predict_tc_lambda


def _nitride(formula: str = "NbN", strain: float = 0.0) -> StructureCandidate:
    metal = "Ti" if formula.startswith("Ti") else "Nb"
    return structure_to_candidate(
        build_binary_nitride(metal),
        material_family="tm_nitride",
        formula=formula,
        in_plane_strain=strain,
    )


def _nickelate(formula: str = "NdNiO2", cid: str | None = None) -> StructureCandidate:
    return StructureCandidate(
        formula=formula,
        material_family="nickelate",
        candidate_id=cid or f"ni-{formula}-{id(formula)}",
        composition={"Nd": 1.0, "Ni": 1.0, "O": 2.0},
    )


def _si(total: float = 50.0) -> SiFeasibilityScore:
    return SiFeasibilityScore(total=total)


def _pairing_eval(
    cand: StructureCandidate,
    *,
    score: float,
    source: str = "dmft_pairing",
    eig: float = 1.0,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=cand,
        dmft=DMFTResult(
            status="ok",
            quality_tag="screening",
            converged=True,
            leading_pairing_eigenvalue=eig,
            pairing_symmetry="d_x2-y2",
            solver="solid_dmft",
        ),
        performance_score=score,
        performance_score_source=source,
        si_feasibility=_si(45.0),
        status="ok",
    )


def _epw_eval(
    cand: StructureCandidate,
    *,
    tc: float,
    source: str = "epw",
    mock: bool = False,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=cand,
        electron_phonon=ElectronPhononResult(
            lambda_total=1.0,
            omega_log=280.0,
            Tc_allen_dynes=tc,
            Tc_eliashberg=tc,
            converged=True,
            status="mock" if mock else "ok",
            quality_tag="mock" if mock else "screening",
        ),
        performance_score=tc,
        performance_score_source="mock" if mock else source,
        si_feasibility=_si(70.0),
        status="mock" if mock else "ok",
        calculator_name="mock" if mock else "qe-epw",
    )


# ---------------------------------------------------------------------------
# Pool derivation
# ---------------------------------------------------------------------------


def test_pool_from_performance_score_source() -> None:
    ni = _nickelate()
    ev = _pairing_eval(ni, score=25.0)
    got = derive_pool(evaluation=ev)
    assert got.pool == "unconventional"
    assert got.reason == "source:dmft_pairing"

    nb = _nitride()
    ev_epw = _epw_eval(nb, tc=16.0)
    got_epw = derive_pool(evaluation=ev_epw)
    assert got_epw.pool == "conventional"
    assert got_epw.reason == "source:epw"


def test_pool_conflict_without_source_is_unknown() -> None:
    cand = _nickelate()
    ev = CandidateEvaluation(
        candidate=cand,
        electron_phonon=ElectronPhononResult(
            lambda_total=0.8,
            Tc_allen_dynes=10.0,
            status="ok",
            quality_tag="screening",
        ),
        dmft=DMFTResult(
            status="ok",
            quality_tag="screening",
            leading_pairing_eigenvalue=0.9,
        ),
        # source deliberately unset
    )
    got = derive_pool(evaluation=ev)
    assert got.pool == "unknown"
    assert got.reason.startswith("conflict:")


def test_pool_from_family_when_unevaluated() -> None:
    assert derive_pool(candidate=_nitride()).pool == "conventional"
    assert derive_pool(candidate=_nickelate()).pool == "unconventional"
    other = StructureCandidate(formula="Xx", material_family="other")
    got = derive_pool(candidate=other)
    assert got.pool == "unknown"
    assert got.reason == "no_recognized_signal"


def test_unrecognized_source_falls_through_to_family() -> None:
    cand = _nitride()
    ev = CandidateEvaluation(
        candidate=cand,
        performance_score=12.0,
        performance_score_source="not_a_real_source",
    )
    got = derive_pool(evaluation=ev)
    assert got.pool == "conventional"
    assert got.reason == "family:tm_nitride"


# ---------------------------------------------------------------------------
# Default / off path: no ranking drift
# ---------------------------------------------------------------------------


def test_conventional_only_off_matches_pre_p36_scores_and_order() -> None:
    cands = [
        _nitride("NbN", 0.0),
        _nitride("TiN", 0.0),
        _nitride("NbN", 0.03),
    ]
    for c in cands:
        c.energy_above_hull_proxy = 0.02
    si = {c.candidate_id: _si(60.0) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    cfg = ActiveLearningConfig(enabled=True, max_epw_jobs=2)  # pool_mode default off
    plan = prioritize_candidates(cands, config=cfg, si_scores=si, predictions=preds)

    expected_scores = []
    for c in cands:
        pred = preds[c.candidate_id]
        score, _ = acquisition_score(
            uncertainty=pred.uncertainty,
            predicted_tc=pred.predicted_Tc,
            si_total=60.0,
            energy_above_hull=0.02,
            weights=cfg.weights.model_dump(),
            tc_ceiling_K=cfg.tc_ceiling_K,
        )
        expected_scores.append((c.candidate_id, score, pred.predicted_Tc))
    expected_scores.sort(key=lambda t: (t[1], t[2]), reverse=True)

    assert [r.candidate_id for r in plan.ranked] == [t[0] for t in expected_scores]
    assert [r.acquisition_score for r in plan.ranked] == [t[1] for t in expected_scores]
    assert sum(1 for r in plan.ranked if r.selected_for_expensive) == 2
    assert plan.acquisition_mode == "off"
    assert all(r.score_signal == "surrogate_tc" for r in plan.ranked)
    assert all(r.pool == "conventional" for r in plan.ranked)


def test_off_and_joint_without_evaluations_match() -> None:
    cands = [_nitride("NbN"), _nitride("TiN"), _nickelate("NdNiO2")]
    si = {c.candidate_id: _si(50.0) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    off = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=2, pool_mode="off"),
        si_scores=si,
        predictions=preds,
    )
    joint = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=2, pool_mode="joint"),
        si_scores=si,
        predictions=preds,
    )
    assert [r.candidate_id for r in off.ranked] == [r.candidate_id for r in joint.ranked]
    assert [r.acquisition_score for r in off.ranked] == [
        r.acquisition_score for r in joint.ranked
    ]
    assert [r.selected_for_expensive for r in off.ranked] == [
        r.selected_for_expensive for r in joint.ranked
    ]
    assert off.acquisition_mode == "off"
    assert joint.acquisition_mode == "joint"


def test_off_ignores_performance_score_evaluations() -> None:
    """Default path must not swap in pairing scores (no conventional drift)."""
    ni = _nickelate()
    preds = {ni.candidate_id: predict_tc_lambda(ni)}
    evs = {ni.candidate_id: _pairing_eval(ni, score=40.0)}
    plan = prioritize_candidates(
        [ni],
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1, pool_mode="off"),
        predictions=preds,
        evaluations=evs,
    )
    assert plan.ranked[0].score_signal == "surrogate_tc"
    assert plan.ranked[0].predicted_tc == pytest.approx(preds[ni.candidate_id].predicted_Tc)


# ---------------------------------------------------------------------------
# Joint / separate modes
# ---------------------------------------------------------------------------


def test_joint_mixed_list_single_ordered_acquisition() -> None:
    nb = _nitride("NbN")
    ti = _nitride("TiN")
    nd = _nickelate("NdNiO2")
    pr = _nickelate("PrNiO2")
    cands = [nb, ti, nd, pr]
    si = {c.candidate_id: _si(50.0) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    evals = {
        nb.candidate_id: _epw_eval(nb, tc=16.0),
        ti.candidate_id: _epw_eval(ti, tc=6.0),
        nd.candidate_id: _pairing_eval(nd, score=30.0, eig=1.2),
        pr.candidate_id: _pairing_eval(pr, score=10.0, eig=0.4),
    }
    plan = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=2, pool_mode="joint"),
        si_scores=si,
        predictions=preds,
        evaluations=evals,
    )
    assert plan.acquisition_mode == "joint"
    assert len(plan.ranked) == 4
    assert len(plan.selected) == 2
    assert plan.ranked[0].acquisition_score >= plan.ranked[-1].acquisition_score
    pools = {r.pool for r in plan.ranked}
    assert "conventional" in pools
    assert "unconventional" in pools
    # Highest pairing proxy (30 K) should beat a 6 K nitride on the common axis.
    by_id = {r.candidate_id: r for r in plan.ranked}
    assert by_id[nd.candidate_id].score_signal == "performance_score"
    assert by_id[nd.candidate_id].acquisition_score > by_id[ti.candidate_id].acquisition_score
    assert all(r.pool_reason for r in plan.ranked)


def test_separate_mode_quota_selects_both_pools() -> None:
    # Three high-scoring nitrides would starve nickelates under global top-k.
    nitrides = [_nitride("NbN", s) for s in (0.0, 0.01, 0.02, 0.03)]
    nickels = [_nickelate(f"NdNiO2-{i}", cid=f"ni-{i}") for i in range(3)]
    cands = nitrides + nickels
    si = {c.candidate_id: _si(50.0) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    evals = {}
    for c in nitrides:
        evals[c.candidate_id] = _epw_eval(c, tc=35.0)
    for c in nickels:
        evals[c.candidate_id] = _pairing_eval(c, score=8.0)
    cfg = ActiveLearningConfig(
        enabled=True,
        max_epw_jobs=4,
        pool_mode="separate",
        pool_quotas=ActiveLearningPoolQuotas(conventional=0.5, unconventional=0.5),
    )
    plan = prioritize_candidates(
        cands, config=cfg, si_scores=si, predictions=preds, evaluations=evals
    )
    assert plan.acquisition_mode == "separate"
    assert len(plan.selected) == 4
    assert plan.selected_by_pool["conventional"] == 2
    assert plan.selected_by_pool["unconventional"] == 2
    # Global top-4 would have been all nitrides (35 K vs 8 K).
    joint = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=4, pool_mode="joint"),
        si_scores=si,
        predictions=preds,
        evaluations=evals,
    )
    assert joint.selected_by_pool["conventional"] == 4
    assert joint.selected_by_pool["unconventional"] == 0


def test_separate_empty_pool_does_not_starve_present_pool() -> None:
    cands = [_nitride("NbN"), _nitride("TiN"), _nitride("NbN", 0.02)]
    si = {c.candidate_id: _si(50.0) for c in cands}
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    plan = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(
            enabled=True,
            max_epw_jobs=2,
            pool_mode="separate",
            pool_quotas=ActiveLearningPoolQuotas(conventional=0.5, unconventional=0.5),
        ),
        si_scores=si,
        predictions=preds,
    )
    assert len(plan.selected) == 2
    assert plan.selected_by_pool["conventional"] == 2
    assert plan.selected_by_pool["unconventional"] == 0


# ---------------------------------------------------------------------------
# Provenance + promotion hygiene
# ---------------------------------------------------------------------------


def test_prioritization_record_includes_pool_and_mode() -> None:
    cands = [_nitride(), _nickelate()]
    plan = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1, pool_mode="joint"),
        predictions={c.candidate_id: predict_tc_lambda(c) for c in cands},
    )
    rec = build_prioritization_record(
        model=None,
        strategy=plan.strategy,
        weights=ActiveLearningConfig().weights.model_dump(),
        ranked=plan.ranked,
        selected_ids=[c.candidate_id for c in plan.selected],
        deferred_ids=[c.candidate_id for c in plan.deferred],
        acquisition_mode=plan.acquisition_mode,
        pool_counts=plan.pool_counts,
        selected_by_pool=plan.selected_by_pool,
    )
    assert rec.acquisition_mode == "joint"
    assert rec.pool_counts["conventional"] >= 1
    assert rec.pool_counts["unconventional"] >= 1
    assert rec.ranked_scores[0]["pool"] in {"conventional", "unconventional", "unknown"}
    assert "acquisition_mode" in rec.ranked_scores[0]


def test_promote_still_refuses_mock_under_mixed_config() -> None:
    cand = _nitride()
    ev = _epw_eval(cand, tc=15.0, mock=True)
    ev.acquisition_mode = "joint"
    ev.acquisition_pool = "conventional"
    ok, reason = promotion_eligibility(ev)
    assert not ok
    assert "mock" in reason.lower()
    with pytest.raises(PromotionError, match="mock"):
        promote_evaluation(ev)


def test_al_status_shows_per_pool_counts(tmp_path: Path) -> None:
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    from siscforge.active_learning import literature_example, seed_default_goldens

    seed_default_goldens(tstore)
    tstore.add_example(
        literature_example(
            formula="NdNiO2",
            tc_K=15.0,
            literature_ref="test:NdNiO2",
            material_family="nickelate",
            source="literature",
        )
    )
    # Stamp a mixed-mode prioritization so status treats mixed as used.
    rec = build_prioritization_record(
        model=None,
        strategy="uncertainty_si_tc",
        weights={},
        ranked=[],
        selected_ids=[],
        deferred_ids=[],
        acquisition_mode="separate",
        pool_counts={"conventional": 3, "unconventional": 1, "unknown": 0},
        selected_by_pool={"conventional": 1, "unconventional": 1, "unknown": 0},
    )
    registry.record_prioritization(rec)
    status = al_status(tstore, registry)
    assert status["pools"]["conventional"] >= 1
    assert status["pools"]["unconventional"] >= 1
    assert status["mixed_pools_used"] is True
    assert status["acquisition_mode_last"] == "separate"


def test_export_surfaces_pool_and_mode(tmp_path: Path) -> None:
    cand = _nitride()
    ev = _epw_eval(cand, tc=16.0)
    ev.acquisition_score = 0.72
    ev.al_selected_for_expensive = True
    ev.acquisition_pool = "conventional"
    ev.acquisition_mode = "joint"
    ev.acquisition_pool_reason = "source:epw"
    ev.rank = 1
    csv_path = write_evaluations_csv([ev], tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    assert "acquisition_pool" in header
    assert "acquisition_mode" in header
    assert "acquisition_pool" in CSV_FIELDNAMES
    cards = write_synthesis_cards([ev], tmp_path / "cards.md", campaign_name="p36")
    text = cards.read_text()
    assert "pool: conventional" in text
    assert "acquisition mode: joint" in text


def test_campaign_yaml_pool_mode_round_trip() -> None:
    cfg = CampaignConfig(
        name="mixed",
        active_learning=ActiveLearningConfig(
            enabled=True,
            pool_mode="separate",
            pool_quotas=ActiveLearningPoolQuotas(conventional=0.6, unconventional=0.4),
        ),
    )
    restored = CampaignConfig.model_validate(cfg.model_dump())
    assert restored.active_learning.pool_mode == "separate"
    assert restored.active_learning.pool_quotas.conventional == pytest.approx(0.6)
    assert restored.active_learning.pool_quotas.unconventional == pytest.approx(0.4)


def test_default_example_yaml_stays_off() -> None:
    cfg = CampaignConfig.from_yaml(Path("examples/nbti_n_al.yaml"))
    assert cfg.active_learning.pool_mode == "off"
