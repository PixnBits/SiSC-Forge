"""P2.5 — process-recommendation schema freeze + scannable synthesis cards."""

from __future__ import annotations

import json
from pathlib import Path

from siscforge.export import (
    PROCESS_RECOMMENDATION_SCHEMA_VERSION,
    export_campaign_bundle,
    process_recommendation,
    write_candidate_onepagers,
    write_process_recommendations_json,
    write_synthesis_cards,
)
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import RankingConfig
from siscforge.models.results import (
    ElectronPhononResult,
    PhononResult,
    SiFeasibilityComponents,
    SiFeasibilityScore,
)
from siscforge.ranking import rank_evaluations


# Stable key set frozen for Phase 2 handoff (schema v1.0).
REQUIRED_PROCESS_KEYS = {
    "schema_version",
    "candidate_id",
    "formula",
    "material_family",
    "substrate",
    "in_plane_strain",
    "rank",
    "on_pareto_front",
    "recommended_buffers",
    "recommended_stack",
    "recommended_thickness_nm",
    "critical_thickness_nm",
    "critical_thickness_method",
    "critical_thickness_people_bean_nm",
    "process_temp_ceiling_c",
    "thermal_window_note",
    "chemical_flags",
    "membrane_transfer_candidate",
    "membrane_transfer_note",
    "result_quality",
    "do_not_cite_tc",
    "trust_warning",
    "composite_score",
    "performance_score",
    "performance_score_source",
    "si_feasibility_total",
    "si_scorer_version",
}


def _cand(formula: str = "NbN", *, cid: str = "cid-nbn") -> StructureCandidate:
    return StructureCandidate(
        formula=formula,
        material_family="tm_nitride",
        candidate_id=cid,
        composition={"Nb": 0.5, "N": 0.5},
        substrate="Si(001)",
        in_plane_strain=0.0,
    )


def _si(**overrides) -> SiFeasibilityScore:
    base = dict(
        total=72.0,
        components=SiFeasibilityComponents(
            lattice_mismatch=40.0,
            thermal_budget=80.0,
            chemical_compatibility=75.0,
            buffer_availability=90.0,
            process_maturity=85.0,
        ),
        weights={
            "lattice_mismatch": 0.35,
            "thermal_budget": 0.2,
            "chemical_compatibility": 0.2,
            "buffer_availability": 0.1,
            "process_maturity": 0.15,
        },
        recommended_buffers=["MgO/TiN", "TiN", "AlN"],
        recommended_thickness_nm=3.5,
        critical_thickness_nm=5.8,
        critical_thickness_method="Matthews-Blakeslee",
        critical_thickness_people_bean_nm=12.0,
        process_temp_ceiling_c=550.0,
        thermal_window_note="Both steps usually ≤550 °C",
        chemical_flags=["nitrogen_window", "oxygen_window"],
        membrane_transfer_candidate=True,
        membrane_transfer_note="high mismatch path",
        version="0.5",
        notes="test Si score",
    )
    base.update(overrides)
    return SiFeasibilityScore(**base)


def _ev(
    *,
    formula: str = "NbN",
    cid: str = "cid-nbn",
    tc: float = 15.0,
    si: SiFeasibilityScore | None = None,
    result_quality: str = "screening",
    quality_notes: str = "",
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate=_cand(formula, cid=cid),
        phonon=PhononResult(
            min_frequency_cm1=100.0,
            has_imaginary_modes=False,
            dynamically_stable=True,
            status="ok",
            quality_tag="screening",
        ),
        electron_phonon=ElectronPhononResult(
            lambda_total=1.1,
            omega_log=250.0,
            mu_star=0.1,
            Tc_allen_dynes=tc,
            Tc_eliashberg=tc,
            converged=True,
            status="ok",
            quality_tag="screening",
        ),
        si_feasibility=si if si is not None else _si(),
        performance_score=tc,
        performance_score_source="epw",
        result_quality=result_quality,  # type: ignore[arg-type]
        quality_notes=quality_notes,
        status="ok",
        calculator_name="qe-epw",
    )


def test_process_recommendation_schema_keys() -> None:
    ranked = rank_evaluations([_ev()], RankingConfig())
    rec = process_recommendation(ranked[0])
    assert rec["schema_version"] == PROCESS_RECOMMENDATION_SCHEMA_VERSION
    assert set(rec.keys()) == REQUIRED_PROCESS_KEYS
    assert rec["recommended_stack"] == "MgO/TiN"
    assert rec["recommended_buffers"][0] == "MgO/TiN"
    assert rec["critical_thickness_method"] == "Matthews-Blakeslee"
    assert rec["membrane_transfer_candidate"] is True
    assert rec["process_temp_ceiling_c"] == 550.0
    assert "nitrogen_window" in rec["chemical_flags"]
    assert rec["si_feasibility_total"] == 72.0
    assert rec["rank"] == 1
    # Default fixture is screening — not citable as production
    assert rec["result_quality"] == "screening"
    assert rec["do_not_cite_tc"] is True
    assert rec["trust_warning"] is not None


def test_do_not_cite_tc_by_quality_tier() -> None:
    """do_not_cite_tc is false only for production; true for all other tiers."""
    cases = [
        ("production", False),
        ("screening", True),
        ("screening_suspect", True),
        ("unreliable", True),
        ("unknown", True),
    ]
    for rq, expect_flag in cases:
        ev = _ev(result_quality=rq, cid=f"id-{rq}")
        rec = process_recommendation(ev)
        assert rec["do_not_cite_tc"] is expect_flag, rq
        if expect_flag:
            assert rec["trust_warning"] is not None, rq
        else:
            assert rec["trust_warning"] is None, rq


def test_do_not_cite_tc_on_suspect() -> None:
    ranked = rank_evaluations(
        [
            _ev(
                result_quality="screening_suspect",
                quality_notes="high_lambda",
                tc=45.0,
            )
        ],
        RankingConfig(),
    )
    # ranking may demote suspect; re-set quality after rank for explicit check
    ev = ranked[0]
    ev = ev.model_copy(
        update={
            "result_quality": "screening_suspect",
            "quality_notes": "high_lambda",
        }
    )
    rec = process_recommendation(ev)
    assert rec["do_not_cite_tc"] is True
    assert rec["trust_warning"] is not None
    assert "do **not** quote" in rec["trust_warning"]


def test_thickness_band_serialization() -> None:
    si = _si(recommended_thickness_nm=(2.0, 4.0))
    rec = process_recommendation(_ev(si=si))
    assert rec["recommended_thickness_nm"] == [2.0, 4.0]


def test_missing_si_still_emits_stable_keys() -> None:
    ev = _ev()
    ev = ev.model_copy(update={"si_feasibility": None})
    rec = process_recommendation(ev)
    assert set(rec.keys()) == REQUIRED_PROCESS_KEYS
    assert rec["recommended_stack"] is None
    assert rec["recommended_buffers"] == []
    assert rec["si_feasibility_total"] is None


def test_write_process_recommendations_json(tmp_path: Path) -> None:
    ranked = rank_evaluations(
        [_ev(formula="NbN", cid="a"), _ev(formula="TiN", cid="b", tc=10.0)],
        RankingConfig(),
    )
    path = write_process_recommendations_json(ranked, tmp_path / "pr.json")
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["schema_version"] == "1.0"
    assert data[0]["formula"] in {"NbN", "TiN"}
    assert set(data[0].keys()) == REQUIRED_PROCESS_KEYS


def test_synthesis_card_layout_sections(tmp_path: Path) -> None:
    ranked = rank_evaluations([_ev()], RankingConfig())
    path = write_synthesis_cards(ranked, tmp_path / "cards.md", campaign_name="p25")
    text = path.read_text()
    assert "### Identity" in text
    assert "### Headline scores" in text
    assert "### Process recommendation" in text
    assert "### Supporting detail" in text
    assert "#### Silicon feasibility breakdown" in text
    assert "recommended buffer / stack" in text
    assert "process temp ceiling" in text
    assert "membrane-transfer candidate" in text
    assert "```json" in text
    assert '"schema_version": "1.0"' in text
    assert '"recommended_stack"' in text
    # Supporting detail still has component breakdown
    assert "lattice mismatch" in text
    assert "process maturity" in text
    assert "weights" in text


def test_card_do_not_cite_when_suspect(tmp_path: Path) -> None:
    ev = _ev(result_quality="screening_suspect", quality_notes="high_lambda")
    ranked = rank_evaluations([ev], RankingConfig())
    # Ensure quality stays suspect after rank (trust penalties may alter scores)
    ranked[0] = ranked[0].model_copy(
        update={"result_quality": "screening_suspect", "quality_notes": "high_lambda"}
    )
    path = write_synthesis_cards(ranked, tmp_path / "cards.md")
    text = path.read_text()
    assert "do not cite Tc" in text.lower() or "do **not** quote" in text


def test_onepager_uses_same_layout(tmp_path: Path) -> None:
    ranked = rank_evaluations([_ev()], RankingConfig())
    paths = write_candidate_onepagers(ranked, tmp_path / "ops", campaign_name="p25")
    assert len(paths) == 1
    text = paths[0].read_text()
    assert "### Process recommendation" in text
    assert "```json" in text
    assert "Candidate one-pager" in text


def test_export_bundle_writes_process_recommendations(tmp_path: Path) -> None:
    ranked = rank_evaluations([_ev()], RankingConfig())
    written = export_campaign_bundle(
        ranked,
        tmp_path / "out",
        formats=["json", "csv", "markdown"],
        campaign_name="bundle",
    )
    assert "process_recommendations" in written
    pr_path = written["process_recommendations"]
    assert pr_path.name == "process_recommendations.json"
    data = json.loads(pr_path.read_text())
    assert data[0]["schema_version"] == "1.0"
    cards = (tmp_path / "out" / "synthesis_cards.md").read_text()
    assert "Process recommendation" in cards
