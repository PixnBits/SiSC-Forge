"""Phase 1.5 active-learning bootstrap: promotion, retrain, mock cycle (AC13–AC18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.active_learning import (
    PromotionError,
    SurrogateRegistry,
    TrainingSetStore,
    al_status,
    literature_example,
    prioritize_candidates,
    promote_evaluation,
    promotion_eligibility,
    retrain_from_store,
    seed_default_goldens,
)
from siscforge.active_learning.bootstrap import build_prioritization_record, retrain_from_snapshot
from siscforge.active_learning.training_set import make_snapshot
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import ActiveLearningConfig
from siscforge.models.results import ElectronPhononResult, PhononResult
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.surrogates.tc_lambda import predict_tc_lambda


def _cand(formula: str = "NbN") -> StructureCandidate:
    s = build_binary_nitride("Nb" if "Nb" in formula else "Ti")
    return structure_to_candidate(
        s,
        material_family="tm_nitride",
        formula=formula,
        in_plane_strain=0.0,
    )


def _ok_epw_eval(
    formula: str = "NbN",
    *,
    quality_tag: str = "screening",
    status: str = "ok",
    mock: bool = False,
) -> CandidateEvaluation:
    cand = _cand(formula)
    cand.quality_tag = "mock" if mock else quality_tag  # type: ignore[assignment]
    ep = ElectronPhononResult(
        lambda_total=1.0,
        omega_log=280.0,
        Tc_allen_dynes=15.0,
        converged=True,
        status="mock" if mock else "ok",
        quality_tag="mock" if mock else quality_tag,  # type: ignore[arg-type]
    )
    return CandidateEvaluation(
        candidate=cand,
        electron_phonon=ep,
        phonon=PhononResult(
            dynamically_stable=True,
            has_imaginary_modes=False,
            status="ok",
            quality_tag=quality_tag if not mock else "mock",  # type: ignore[arg-type]
        ),
        status="mock" if mock else status,
        calculator_name="mock" if mock else "qe-epw",
        result_quality="screening" if not mock else "unknown",
    )


# --- AC13 / AC18: promotion gate ---


def test_promote_clean_evaluation() -> None:
    ev = _ok_epw_eval()
    ok, reason = promotion_eligibility(ev)
    assert ok, reason
    ex = promote_evaluation(ev, campaign_store="/tmp/store")
    assert ex.formula == "NbN"
    assert ex.tc_K == pytest.approx(15.0)
    assert ex.source == "project"
    assert ex.quality_tag == "screening"


def test_refuse_mock_promotion_ac18() -> None:
    ev = _ok_epw_eval(mock=True)
    ok, reason = promotion_eligibility(ev)
    assert not ok
    assert "mock" in reason.lower()
    with pytest.raises(PromotionError, match="mock"):
        promote_evaluation(ev)


def test_refuse_failed_status_ac13() -> None:
    ev = _ok_epw_eval(status="failed")
    ok, reason = promotion_eligibility(ev)
    assert not ok
    with pytest.raises(PromotionError):
        promote_evaluation(ev)


def test_refuse_screening_high_lambda_random_proj() -> None:
    """#44: high-λ + random/coarse screening cannot enter the training set."""
    ev = _ok_epw_eval()
    assert ev.electron_phonon is not None
    ev = ev.model_copy(
        update={
            "electron_phonon": ev.electron_phonon.model_copy(
                update={
                    "lambda_total": 4.5,
                    "Tc_allen_dynes": 40.0,
                    "alpha2F_summary": {
                        "method": "epw",
                        "material_notes": "proj=random",
                    },
                }
            ),
            "performance_score": 40.0,
            "performance_score_source": "epw",
        }
    )
    ok, reason = promotion_eligibility(ev)
    assert not ok
    assert "screening_high_lambda" in reason or "high-λ" in reason or "high-l" in reason.lower()
    with pytest.raises(PromotionError):
        promote_evaluation(ev)


def test_training_set_store_promote_and_snapshot(tmp_path: Path) -> None:
    store = TrainingSetStore(tmp_path / "train")
    seed_default_goldens(store)
    assert store.summary()["n_examples"] >= 3
    ev = _ok_epw_eval("NbN")
    store.promote(ev, campaign_store=str(tmp_path / "campaign"))
    snap = store.snapshot(notes="test")
    assert snap.n_examples >= 4
    assert snap.content_hash
    loaded = store.load_snapshot(snap.content_hash)
    assert loaded is not None
    assert loaded.n_examples == snap.n_examples


def test_literature_seed() -> None:
    ex = literature_example(
        formula="NbN",
        tc_K=16.0,
        lambda_total=1.05,
        literature_ref="test:NbN",
        material_family="tm_nitride",
        source="golden",
    )
    assert ex.source == "golden"
    assert ex.tc_K == 16.0


# --- AC17: retrain safety ---


def test_retrain_success_and_bootstrap_flag(tmp_path: Path) -> None:
    tstore = TrainingSetStore(tmp_path / "train")
    seed_default_goldens(tstore)
    tstore.promote(_ok_epw_eval("NbN"))
    registry = SurrogateRegistry(tmp_path / "models")
    result = retrain_from_store(tstore, registry, snapshot_notes="unit")
    assert result.success is True
    assert result.metadata is not None
    assert result.metadata.training_set_size >= 4
    assert result.metadata.bootstrap is True  # still under 150 labels
    assert registry.current() is not None
    assert registry.current().model_version == result.metadata.model_version


def test_retrain_refuses_empty(tmp_path: Path) -> None:
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    snap = make_snapshot([])
    result = retrain_from_snapshot(snap, registry)
    assert result.success is False
    assert "empty" in (result.refused_reason or "")


def test_retrain_refuses_mock_in_set(tmp_path: Path) -> None:
    registry = SurrogateRegistry(tmp_path / "models")
    bad = literature_example(
        formula="Fake",
        tc_K=10.0,
        literature_ref="x",
    )
    bad.quality_tag = "mock"  # type: ignore[assignment]
    bad.status = "mock"
    snap = make_snapshot([bad])
    result = retrain_from_snapshot(snap, registry)
    assert result.success is False
    assert "mock" in (result.refused_reason or "").lower()


def test_retrain_refuses_absurd_tc(tmp_path: Path) -> None:
    registry = SurrogateRegistry(tmp_path / "models")
    absurd = literature_example(
        formula="X",
        tc_K=9999.0,
        literature_ref="absurd",
        material_family="tm_nitride",
    )
    snap = make_snapshot([absurd])
    result = retrain_from_snapshot(snap, registry)
    assert result.success is False
    assert "absurd" in (result.refused_reason or "").lower()


# --- AC14 / AC15: provenance + bootstrap status ---


def test_prioritization_record_provenance() -> None:
    cands = [_cand("NbN"), _cand("TiN")]
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    cfg = ActiveLearningConfig(enabled=True, max_epw_jobs=1)
    plan = prioritize_candidates(
        cands,
        config=cfg,
        predictions=preds,
        model_version="0.2-fit-test",
        training_set_size=12,
        bootstrap=True,
    )
    assert plan.model_version == "0.2-fit-test"
    assert plan.bootstrap is True
    assert plan.ranked[0].model_version == "0.2-fit-test"
    rec = build_prioritization_record(
        model=None,
        strategy=plan.strategy,
        weights={"uncertainty": 0.4},
        ranked=plan.ranked,
        selected_ids=[c.candidate_id for c in plan.selected],
        deferred_ids=[c.candidate_id for c in plan.deferred],
    )
    assert rec.n_selected == 1
    assert rec.bootstrap is True


def test_al_status_bootstrap_message(tmp_path: Path) -> None:
    tstore = TrainingSetStore(tmp_path / "train")
    seed_default_goldens(tstore)
    registry = SurrogateRegistry(tmp_path / "models")
    status = al_status(tstore, registry)
    assert status["bootstrap"] is True
    assert "BOOTSTRAP" in status["message"]
    assert status["n_labels"] >= 3


# --- AC16: full mock cycle prioritize → promote → retrain ---


def test_full_mock_al_cycle(tmp_path: Path) -> None:
    """prioritize → (mock) evaluate → promote clean → retrain → re-prioritize."""
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    seed_default_goldens(tstore)

    # 1. Prioritize
    cands = [_cand("NbN"), _cand("TiN"), _cand("NbN")]
    # unique formulas for stability
    cands[2] = _cand("TiN")
    cands[2] = structure_to_candidate(
        build_binary_nitride("Ti"),
        material_family="tm_nitride",
        formula="TiN",
        in_plane_strain=0.02,
    )
    preds = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    plan1 = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=2),
        predictions=preds,
        model_version="heuristic",
        bootstrap=True,
    )
    assert len(plan1.selected) == 2

    # 2. Mock-evaluate selected as if EPW succeeded (not calculator mock tag)
    for c in plan1.selected:
        ev = _ok_epw_eval(c.formula, quality_tag="screening")
        # keep candidate id stable for promotion linkage
        ev.candidate = c.model_copy(
            update={"quality_tag": "screening"}
        )
        tstore.promote(ev, campaign_store=str(tmp_path / "campaign"))

    # 3. Retrain
    result = retrain_from_store(tstore, registry)
    assert result.success, result.refused_reason
    assert result.metadata is not None

    # 4. Re-prioritize with new model provenance
    plan2 = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=2),
        predictions=preds,
        model_version=result.metadata.model_version,
        training_set_size=result.metadata.training_set_size,
        bootstrap=result.metadata.bootstrap,
    )
    assert plan2.model_version == result.metadata.model_version
    assert plan2.training_set_size >= 5

    status = al_status(tstore, registry)
    assert status["model"]["model_version"] == result.metadata.model_version


def test_audit_lists_examples(tmp_path: Path) -> None:
    store = TrainingSetStore(tmp_path / "train")
    seed_default_goldens(store)
    rows = store.audit()
    assert len(rows) >= 3
    assert all("formula" in r and "source" in r for r in rows)
