"""P2.4 ranking upgrades: multi-objective weights, Pareto front, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.export import CSV_FIELDNAMES, write_evaluations_csv, write_synthesis_cards
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig, RankingConfig
from siscforge.models.results import (
    ElectronPhononResult,
    PhononResult,
    SiFeasibilityScore,
)
from siscforge.ranking import (
    compute_composite_breakdown,
    compute_composite_score,
    identify_pareto_front,
    normalize_performance,
    pareto_objectives,
    rank_evaluations,
)


def _cand(formula: str, *, cid: str | None = None) -> StructureCandidate:
    return StructureCandidate(
        formula=formula,
        material_family="tm_nitride",
        candidate_id=cid or f"id-{formula}",
        composition={"Nb": 0.5, "N": 0.5} if "Nb" in formula else {},
    )


def _ev(
    *,
    formula: str = "NbN",
    cid: str | None = None,
    tc: float | None = 15.0,
    si: float = 50.0,
    lam: float = 1.2,
    stable: bool = True,
    quality_tag: str = "screening",
    uncertainty: float | None = None,
    hull: float | None = None,
) -> CandidateEvaluation:
    ph = PhononResult(
        min_frequency_cm1=100.0 if stable else -20.0,
        has_imaginary_modes=not stable,
        dynamically_stable=stable,
        status="ok",
        quality_tag=quality_tag,  # type: ignore[arg-type]
    )
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
    )
    surr = None
    if uncertainty is not None:
        surr = {
            "predicted_lambda": lam,
            "predicted_Tc": tc,
            "uncertainty": uncertainty,
            "model_version": "test-0",
            "quality_tag": "stub",
        }
    cand = _cand(formula, cid=cid)
    if hull is not None:
        cand = cand.model_copy(update={"energy_above_hull_proxy": hull})
    return CandidateEvaluation(
        candidate=cand,
        phonon=ph,
        electron_phonon=eph,
        si_feasibility=SiFeasibilityScore(total=si),
        performance_score=tc,
        performance_score_source="epw",
        tc_lambda_surrogate=surr,
        status="ok",
        calculator_name="qe-epw",
    )


def test_defaults_match_legacy_two_axis_blend() -> None:
    """Default weights reproduce pre-P2.4 composite (0.6 perf / 0.4 Si, 40 K)."""
    cfg = RankingConfig()
    assert cfg.performance_weight == 0.6
    assert cfg.si_feasibility_weight == 0.4
    assert cfg.uncertainty_weight == 0.0
    assert cfg.performance_ceiling_K == 40.0

    # Clean stable row: no quality/hull/stability penalties
    ev = _ev(tc=20.0, si=50.0, stable=True, lam=1.0)
    # Force production-ish quality by keeping λ low; assess then score
    from siscforge.quality import apply_quality_assessment

    ev = apply_quality_assessment(ev)
    score = compute_composite_score(ev, cfg)
    perf_norm = (20.0 / 40.0) * 100.0  # 50
    expected = 0.6 * perf_norm + 0.4 * 50.0  # 50.0
    assert score == pytest.approx(expected, abs=1e-3)


def test_default_ordering_regression_fixture() -> None:
    """Fixed fixture set keeps the same rank order under default weights."""
    rows = [
        _ev(formula="A", cid="a", tc=30.0, si=40.0, lam=1.0),  # high Tc
        _ev(formula="B", cid="b", tc=10.0, si=90.0, lam=1.0),  # high Si
        _ev(formula="C", cid="c", tc=20.0, si=60.0, lam=1.0),  # balanced
    ]
    ranked = rank_evaluations(rows, RankingConfig())
    order = [e.candidate.formula for e in ranked]
    # composites (pre quality): A: 0.6*75+0.4*40=61; B: 0.6*25+0.4*90=51; C: 0.6*50+0.4*60=54
    assert order == ["A", "C", "B"]
    assert [e.rank for e in ranked] == [1, 2, 3]


def test_weight_override_reorders() -> None:
    rows = [
        _ev(formula="HighTc", cid="ht", tc=32.0, si=30.0, lam=1.0),
        _ev(formula="HighSi", cid="hs", tc=8.0, si=95.0, lam=1.0),
    ]
    default = rank_evaluations(rows, RankingConfig())
    assert default[0].candidate.formula == "HighTc"

    si_heavy = RankingConfig(performance_weight=0.1, si_feasibility_weight=0.9)
    reordered = rank_evaluations(rows, si_heavy)
    assert reordered[0].candidate.formula == "HighSi"
    assert reordered[0].ranking_weights is not None
    assert reordered[0].ranking_weights["si_feasibility"] == 0.9


def test_yaml_ranking_weights_load() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "w",
            "ranking": {
                "performance_weight": 0.2,
                "si_feasibility_weight": 0.7,
                "uncertainty_weight": 0.1,
                "performance_ceiling_K": 35.0,
                "pareto_enabled": True,
            },
        }
    )
    assert cfg.ranking.performance_weight == 0.2
    assert cfg.ranking.uncertainty_weight == 0.1
    assert cfg.ranking.performance_ceiling_K == 35.0
    weights = cfg.ranking.active_weights()
    assert weights["performance_ceiling_K"] == 35.0


def test_pareto_front_non_dominated_subset() -> None:
    """Classic 2D front: high-Tc/low-Si, mid/mid, low-Tc/high-Si on front; dominated off."""
    rows = [
        _ev(formula="P1", cid="p1", tc=40.0, si=20.0, lam=1.0),  # front
        _ev(formula="P2", cid="p2", tc=20.0, si=60.0, lam=1.0),  # front
        _ev(formula="P3", cid="p3", tc=5.0, si=90.0, lam=1.0),  # front
        _ev(formula="Dom", cid="d", tc=10.0, si=30.0, lam=1.0),  # dominated by P2
    ]
    flags = identify_pareto_front(rows, RankingConfig())
    by_f = {r.candidate.formula: flags[i] for i, r in enumerate(rows)}
    assert by_f["P1"] is True
    assert by_f["P2"] is True
    assert by_f["P3"] is True
    assert by_f["Dom"] is False

    ranked = rank_evaluations(rows, RankingConfig(pareto_enabled=True))
    front = {e.candidate.formula for e in ranked if e.on_pareto_front}
    assert front == {"P1", "P2", "P3"}
    assert ranked[0].on_pareto_front is not None


def test_pareto_disabled_sets_none() -> None:
    rows = [_ev(formula="X", cid="x", tc=10.0, si=50.0)]
    ranked = rank_evaluations(rows, RankingConfig(pareto_enabled=False))
    assert ranked[0].on_pareto_front is None


def test_uncertainty_weight_affects_composite() -> None:
    low_u = _ev(formula="Sure", cid="s", tc=16.0, si=50.0, uncertainty=0.1, lam=1.0)
    high_u = _ev(formula="Unsure", cid="u", tc=16.0, si=50.0, uncertainty=0.9, lam=1.0)
    cfg = RankingConfig(
        performance_weight=0.5,
        si_feasibility_weight=0.3,
        uncertainty_weight=0.2,
    )
    from siscforge.quality import apply_quality_assessment

    low_u = apply_quality_assessment(low_u)
    high_u = apply_quality_assessment(high_u)
    s_low = compute_composite_score(low_u, cfg)
    s_high = compute_composite_score(high_u, cfg)
    assert s_low > s_high

    ranked = rank_evaluations([high_u, low_u], cfg)
    assert ranked[0].candidate.formula == "Sure"
    bd = ranked[0].composite_breakdown
    assert bd is not None
    assert bd["certainty_norm"] is not None


def test_zero_uncertainty_weight_ignores_surrogate_u() -> None:
    a = _ev(formula="A", cid="a", tc=16.0, si=50.0, uncertainty=0.05, lam=1.0)
    b = _ev(formula="B", cid="b", tc=16.0, si=50.0, uncertainty=0.95, lam=1.0)
    cfg = RankingConfig()  # uncertainty_weight=0
    from siscforge.quality import apply_quality_assessment

    sa = compute_composite_score(apply_quality_assessment(a), cfg)
    sb = compute_composite_score(apply_quality_assessment(b), cfg)
    assert sa == pytest.approx(sb)


def test_trust_penalties_still_apply() -> None:
    clean = _ev(formula="Clean", cid="c", tc=16.0, si=50.0, lam=1.1)
    suspect = _ev(formula="Sus", cid="s", tc=45.0, si=50.0, lam=4.0)
    ranked = rank_evaluations([suspect, clean], RankingConfig())
    assert ranked[0].candidate.formula == "Clean"
    assert ranked[1].result_quality == "screening_suspect"


def test_stable_first_still_works() -> None:
    rows = [
        _ev(formula="Unst", cid="u", tc=25.0, si=80.0, stable=False, lam=1.0),
        _ev(formula="Stab", cid="s", tc=10.0, si=40.0, stable=True, lam=1.0),
    ]
    ranked = rank_evaluations(rows, RankingConfig(), stable_first=True)
    assert ranked[0].candidate.formula == "Stab"
    assert ranked[0].phonon and ranked[0].phonon.dynamically_stable


def test_export_includes_pareto_and_weights(tmp_path: Path) -> None:
    rows = [
        _ev(formula="A", cid="a", tc=30.0, si=40.0, lam=1.0),
        _ev(formula="B", cid="b", tc=10.0, si=90.0, lam=1.0),
    ]
    ranked = rank_evaluations(rows, RankingConfig())
    csv_path = write_evaluations_csv(ranked, tmp_path / "r.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "on_pareto_front",
        "ranking_w_performance",
        "ranking_w_si_feasibility",
        "ranking_w_uncertainty",
        "ranking_performance_ceiling_K",
        "composite_perf_norm",
        "composite_score",
        "result_quality",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    # existing columns preserved
    assert "si_feasibility_total" in header
    assert "acquisition_score" in header

    md = write_synthesis_cards(ranked, tmp_path / "cards.md", campaign_name="p24")
    text = md.read_text()
    assert "Ranking axes" in text or "ranking weights" in text.lower()
    assert "Pareto" in text or "pareto" in text.lower()
    assert "0.6" in text  # default performance weight visible


def test_json_roundtrip_ranking_fields() -> None:
    ranked = rank_evaluations(
        [_ev(formula="NbN", cid="n", tc=18.0, si=55.0, lam=1.0)],
        RankingConfig(),
    )
    dump = ranked[0].model_dump(mode="json")
    restored = CandidateEvaluation.model_validate(dump)
    assert restored.on_pareto_front is True
    assert restored.ranking_weights is not None
    assert restored.composite_breakdown is not None
    assert restored.composite_score == ranked[0].composite_score


def test_zero_si_score_not_replaced_by_neutral() -> None:
    """Si-feasibility total of 0.0 is valid and must not become the 50 fallback."""
    from siscforge.quality import apply_quality_assessment

    ev = apply_quality_assessment(_ev(formula="Z", cid="z", tc=20.0, si=0.0, lam=1.0))
    cfg = RankingConfig()
    bd = compute_composite_breakdown(ev, cfg)
    assert bd["si_feasibility"] == 0.0
    # 0.6 * 50 + 0.4 * 0 = 30 (not 0.6*50 + 0.4*50 = 50)
    assert bd["pre_penalty"] == pytest.approx(30.0, abs=1e-3)
    assert compute_composite_score(ev, cfg) == pytest.approx(30.0, abs=1e-3)


def test_pareto_excludes_incomplete_objectives() -> None:
    """Rows missing performance or Si cannot sit on / dominate the front."""
    complete = _ev(formula="Ok", cid="ok", tc=15.0, si=50.0, lam=1.0)
    no_perf = complete.model_copy(
        update={
            "performance_score": None,
            "candidate": complete.candidate.model_copy(update={"candidate_id": "np", "formula": "NoP"}),
        }
    )
    no_si = complete.model_copy(
        update={
            "si_feasibility": None,
            "candidate": complete.candidate.model_copy(update={"candidate_id": "ns", "formula": "NoSi"}),
            "performance_score": 100.0,  # would look great if -inf encoding were used
        }
    )
    # High-Tc incomplete must not mark as front-only non-dominated
    high_incomplete = no_si
    low_complete = _ev(formula="Low", cid="low", tc=5.0, si=40.0, lam=1.0)

    assert pareto_objectives(no_perf, RankingConfig()) is None
    assert pareto_objectives(no_si, RankingConfig()) is None

    flags = identify_pareto_front(
        [high_incomplete, low_complete, no_perf], RankingConfig()
    )
    by = {
        high_incomplete.candidate.formula: flags[0],
        low_complete.candidate.formula: flags[1],
        no_perf.candidate.formula: flags[2],
    }
    assert by["NoSi"] is False
    assert by["NoP"] is False
    assert by["Low"] is True


def test_pareto_certainty_axis_changes_dominance() -> None:
    """With uncertainty_weight > 0, certainty is a third maximize objective."""
    # Same perf/Si; higher certainty should dominate
    sure = _ev(formula="Sure", cid="s", tc=20.0, si=50.0, uncertainty=0.1, lam=1.0)
    unsure = _ev(formula="Unsure", cid="u", tc=20.0, si=50.0, uncertainty=0.8, lam=1.0)
    # Better on 2D but worse certainty — still on 3D front if not dominated
    high_tc = _ev(formula="Hot", cid="h", tc=35.0, si=30.0, uncertainty=0.5, lam=1.0)
    cfg = RankingConfig(uncertainty_weight=0.2)

    objs_s = pareto_objectives(sure, cfg)
    objs_u = pareto_objectives(unsure, cfg)
    assert objs_s is not None and objs_u is not None
    assert len(objs_s) == 3
    # sure dominates unsure on (perf, Si, certainty) when first two equal
    assert objs_s[0] == objs_u[0] and objs_s[1] == objs_u[1]
    assert objs_s[2] > objs_u[2]

    flags = identify_pareto_front([sure, unsure, high_tc], cfg)
    by = {r.candidate.formula: flags[i] for i, r in enumerate([sure, unsure, high_tc])}
    assert by["Sure"] is True
    assert by["Unsure"] is False  # dominated by Sure
    assert by["Hot"] is True


def test_pareto_missing_uncertainty_excluded_when_weighted() -> None:
    """When certainty is a configured axis, missing u excludes the row from the front."""
    with_u = _ev(formula="HasU", cid="hu", tc=20.0, si=50.0, uncertainty=0.3, lam=1.0)
    no_u = _ev(formula="NoU", cid="nu", tc=30.0, si=80.0, lam=1.0)  # no uncertainty key
    cfg = RankingConfig(uncertainty_weight=0.25)
    assert pareto_objectives(with_u, cfg) is not None
    assert pareto_objectives(no_u, cfg) is None
    flags = identify_pareto_front([with_u, no_u], cfg)
    assert flags == [True, False]


def test_rank_cli_config_and_pareto_override(tmp_path: Path) -> None:
    """CliRunner: YAML ranking weights reorder; --pareto / --no-pareto override."""
    import json

    from typer.testing import CliRunner

    from siscforge.cli.main import app

    rows = [
        _ev(formula="HighTc", cid="ht", tc=32.0, si=30.0, lam=1.0),
        _ev(formula="HighSi", cid="hs", tc=8.0, si=95.0, lam=1.0),
    ]
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps([e.model_dump(mode="json") for e in rows], indent=2))

    # Si-heavy campaign YAML
    cfg_path = tmp_path / "camp.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "name: rank_cli_p24",
                "ranking:",
                "  performance_weight: 0.1",
                "  si_feasibility_weight: 0.9",
                "  uncertainty_weight: 0.0",
                "  pareto_enabled: true",
                "",
            ]
        )
    )

    runner = CliRunner()
    out_si = tmp_path / "ranked_si.json"
    r1 = runner.invoke(
        app,
        ["rank", str(raw), "-c", str(cfg_path), "-o", str(out_si)],
    )
    assert r1.exit_code == 0, r1.stdout + r1.stderr
    data = json.loads(out_si.read_text())
    assert data[0]["candidate"]["formula"] == "HighSi"
    assert data[0]["ranking_weights"]["si_feasibility"] == 0.9
    assert data[0]["on_pareto_front"] is True

    out_no = tmp_path / "ranked_nopareto.json"
    r2 = runner.invoke(
        app,
        ["rank", str(raw), "-c", str(cfg_path), "--no-pareto", "-o", str(out_no)],
    )
    assert r2.exit_code == 0, r2.stdout + r2.stderr
    data2 = json.loads(out_no.read_text())
    assert data2[0]["on_pareto_front"] is None

    out_yes = tmp_path / "ranked_pareto.json"
    r3 = runner.invoke(
        app,
        ["rank", str(raw), "--pareto", "-o", str(out_yes)],
    )
    assert r3.exit_code == 0, r3.stdout + r3.stderr
    data3 = json.loads(out_yes.read_text())
    assert data3[0]["on_pareto_front"] is True
    # default weights → HighTc first
    assert data3[0]["candidate"]["formula"] == "HighTc"


def test_missing_performance_default_is_pessimistic() -> None:
    """#46: missing Tc uses ≤15, not a mid-scale 50."""
    assert normalize_performance(None) == pytest.approx(15.0)
    assert RankingConfig().missing_performance_default == 15.0
    from siscforge.quality import apply_quality_assessment

    complete = apply_quality_assessment(_ev(formula="Done", cid="d", tc=16.0, si=50.0, lam=1.0))
    incomplete = apply_quality_assessment(
        _ev(formula="Gap", cid="g", tc=16.0, si=50.0, lam=1.0).model_copy(
            update={"performance_score": None}
        )
    )
    cfg = RankingConfig()
    bd_ok = compute_composite_breakdown(complete, cfg)
    bd_gap = compute_composite_breakdown(incomplete, cfg)
    assert bd_gap["performance_missing"] is True
    assert bd_ok["performance_missing"] is False
    assert bd_gap["performance_norm"] == pytest.approx(15.0)
    assert bd_gap["composite"] < bd_ok["composite"]
    ranked = rank_evaluations([incomplete, complete], cfg)
    assert ranked[0].candidate.formula == "Done"


def test_missing_si_default_is_pessimistic() -> None:
    from siscforge.quality import apply_quality_assessment

    complete = apply_quality_assessment(_ev(formula="HasSi", cid="h", tc=16.0, si=50.0, lam=1.0))
    missing = apply_quality_assessment(
        complete.model_copy(
            update={
                "si_feasibility": None,
                "candidate": complete.candidate.model_copy(
                    update={"candidate_id": "m", "formula": "NoSi"}
                ),
            }
        )
    )
    cfg = RankingConfig()
    bd = compute_composite_breakdown(missing, cfg)
    assert bd["si_feasibility_missing"] is True
    assert bd["si_feasibility"] == pytest.approx(15.0)
    assert compute_composite_score(missing, cfg) < compute_composite_score(complete, cfg)


def test_legacy_missing_default_restorable() -> None:
    """Set missing_performance_default=50 to restore pre-#46 neutrality."""
    cfg = RankingConfig(missing_performance_default=50.0)
    from siscforge.quality import apply_quality_assessment

    ev = apply_quality_assessment(
        _ev(formula="X", cid="x", tc=20.0, si=50.0, lam=1.0).model_copy(
            update={"performance_score": None}
        )
    )
    bd = compute_composite_breakdown(ev, cfg)
    assert bd["performance_norm"] == pytest.approx(50.0)


def test_source_aware_ceiling_opt_in() -> None:
    """Per-source ceilings change mixed-origin ranking; default 40 K is unchanged."""
    from siscforge.quality import apply_quality_assessment

    epw = apply_quality_assessment(
        _ev(formula="EPW", cid="e", tc=20.0, si=50.0, lam=1.0)
    )
    pairing = apply_quality_assessment(
        _ev(formula="Pair", cid="p", tc=20.0, si=50.0, lam=1.0).model_copy(
            update={"performance_score_source": "dmft_pairing"}
        )
    )
    default = RankingConfig()
    bd_epw = compute_composite_breakdown(epw, default)
    bd_pair = compute_composite_breakdown(pairing, default)
    assert bd_epw["performance_ceiling_K_used"] == 40.0
    assert bd_pair["performance_ceiling_K_used"] == 40.0
    assert bd_epw["performance_norm"] == pytest.approx(bd_pair["performance_norm"])

    tight_pairing = RankingConfig(
        performance_ceiling_by_source={"dmft_pairing": 20.0}
    )
    bd_pair_tight = compute_composite_breakdown(pairing, tight_pairing)
    bd_epw_tight = compute_composite_breakdown(epw, tight_pairing)
    assert bd_pair_tight["performance_ceiling_K_used"] == 20.0
    assert bd_epw_tight["performance_ceiling_K_used"] == 40.0
    # Same 20 K: pairing now saturates (100) while EPW is 50.
    assert bd_pair_tight["performance_norm"] == pytest.approx(100.0)
    assert bd_epw_tight["performance_norm"] == pytest.approx(50.0)
    assert bd_pair_tight["performance_source"] == "dmft_pairing"


def test_hard_zero_wins_over_missing_default() -> None:
    """#63 + #46: screening high-λ hard-zero is 0, not the missing-perf 15."""
    from siscforge.quality import FLAG_SCREENING_HIGH_LAMBDA, apply_quality_assessment

    ev = apply_quality_assessment(
        _ev(formula="Hot", cid="h", tc=40.0, si=95.0, lam=4.0)
    )
    assert FLAG_SCREENING_HIGH_LAMBDA in ev.quality_flags
    bd = compute_composite_breakdown(ev, RankingConfig())
    assert bd["performance_hard_zeroed"] is True
    assert bd["performance_norm"] == 0.0
    assert bd["performance_missing"] is False
