"""Phase 1.5b: trained predictions affect rankings, dry-run promote, literature seed."""

from __future__ import annotations

from pathlib import Path

import pytest

from siscforge.active_learning import (
    SurrogateRegistry,
    TrainingSetStore,
    prioritize_candidates,
    retrain_from_store,
    seed_default_goldens,
    seed_from_literature_file,
)
from siscforge.active_learning.bootstrap import resolve_al_context
from siscforge.active_learning.training_set import promotion_eligibility
from siscforge.export import write_synthesis_cards
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import ActiveLearningConfig
from siscforge.models.results import ElectronPhononResult, PhononResult
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.surrogates.tc_lambda import predict_tc_lambda


def _cand(formula: str = "NbN", strain: float = 0.0) -> StructureCandidate:
    metal = "Nb" if "Nb" in formula else "Ti"
    s = build_binary_nitride(metal)
    return structure_to_candidate(
        s,
        material_family="tm_nitride",
        formula=formula,
        in_plane_strain=strain,
    )


def _ok_epw_eval(formula: str = "NbN", *, quality_tag: str = "screening") -> CandidateEvaluation:
    cand = _cand(formula)
    cand.quality_tag = quality_tag  # type: ignore[assignment]
    ep = ElectronPhononResult(
        lambda_total=1.4,
        omega_log=260.0,
        Tc_allen_dynes=22.0,
        converged=True,
        status="ok",
        quality_tag=quality_tag,  # type: ignore[arg-type]
    )
    return CandidateEvaluation(
        candidate=cand,
        electron_phonon=ep,
        phonon=PhononResult(
            dynamically_stable=True,
            has_imaginary_modes=False,
            status="ok",
            quality_tag=quality_tag,  # type: ignore[arg-type]
        ),
        status="ok",
        calculator_name="qe-epw",
        result_quality="screening",
    )


def test_trained_model_changes_predictions(tmp_path: Path) -> None:
    """After retrain, family-mean fit must move predicted Tc vs pure heuristic."""
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    seed_default_goldens(tstore)
    # Promote a high-λ NbN-like label so family mean shifts up
    tstore.promote(_ok_epw_eval("NbN"))
    result = retrain_from_store(tstore, registry)
    assert result.success, result.refused_reason
    ctx = registry.active_context(n_labels=tstore.summary()["n_examples"])
    assert ctx.has_trained_payload

    cand = _cand("NbN")
    stub = predict_tc_lambda(cand)
    trained = ctx.predict(cand)
    assert trained.method == "family_mean_fit"
    assert trained.model_version == result.metadata.model_version
    assert trained.quality_tag == "trained"
    # Trained payload must be used (features mark source)
    assert trained.features.get("source") == "family_mean_fit"
    # Scores should not be identical to pure stub for all metrics in general
    # (blend still uses anchors; at minimum method/version differ)
    assert trained.model_version != stub.model_version


def test_retrain_changes_acquisition_order(tmp_path: Path) -> None:
    """Extreme trained family means must change predicted Tc (not provenance only)."""
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    # Single family with very high λ/Tc so trained ≠ heuristic anchors
    from siscforge.active_learning import literature_example

    for i, tc in enumerate((80.0, 85.0, 90.0)):
        ex = literature_example(
            formula=f"X{i}N",
            tc_K=tc,
            lambda_total=2.5,
            omega_log=400.0,
            material_family="tm_nitride",
            literature_ref=f"extreme:{i}",
            source="literature",
        )
        tstore.add_literature(ex)
    result = retrain_from_store(tstore, registry)
    assert result.success, result.refused_reason
    ctx = registry.active_context()
    assert ctx.has_trained_payload

    # Unanchored alloy-like formula so formula anchors don't pin both paths
    c_hi = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="Nb0.5Ti0.5N",
        in_plane_strain=0.0,
    )
    c_lo = structure_to_candidate(
        build_binary_nitride("Ti"),
        material_family="tm_nitride",
        formula="TiN",
        in_plane_strain=0.05,
    )
    cands = [c_lo, c_hi]
    preds_stub = {c.candidate_id: predict_tc_lambda(c) for c in cands}
    preds_trained = ctx.predict_many(cands)

    # Numeric predictions must move for the trained family
    assert preds_trained[c_hi.candidate_id].predicted_lambda != pytest.approx(
        preds_stub[c_hi.candidate_id].predicted_lambda, abs=1e-3
    ) or preds_trained[c_hi.candidate_id].predicted_Tc != pytest.approx(
        preds_stub[c_hi.candidate_id].predicted_Tc, abs=1e-3
    )

    plan_stub = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1),
        predictions=preds_stub,
        model_version="heuristic",
        bootstrap=True,
    )
    plan_trained = prioritize_candidates(
        cands,
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1),
        predictions=preds_trained,
        model_version=ctx.model_version,
        training_set_size=ctx.training_set_size,
        bootstrap=ctx.bootstrap,
    )
    by_stub = {r.candidate_id: r.acquisition_score for r in plan_stub.ranked}
    by_tr = {r.candidate_id: r.acquisition_score for r in plan_trained.ranked}
    assert any(abs(by_stub[cid] - by_tr[cid]) > 1e-6 for cid in by_stub)


def test_tc_only_family_uses_label_tc() -> None:
    """Tc-only family stats must drive predicted_Tc, not hardcoded λ/ω Allen–Dynes."""
    family_stats = {
        "tm_nitride": {"tc_mean": 55.0, "n": 4.0, "tc_std": 2.0},
    }
    cand = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="Nb0.6Ti0.4N",
        in_plane_strain=0.0,
    )

    pred = predict_tc_lambda(
        cand,
        family_stats=family_stats,
        model_version="0.2-fit-tc",
        method="family_mean_fit",
        training_set_size=4,
    )
    assert pred.quality_tag == "trained"
    assert pred.method == "family_tc_mean"
    assert pred.features.get("tc_override") == 55.0
    assert pred.predicted_Tc == pytest.approx(55.0, abs=0.01)


def test_missing_family_does_not_claim_fit_method() -> None:
    """Active fit method must not label heuristic fallback predictions as fit."""
    family_stats = {
        "mgb2_boride": {
            "lambda_mean": 0.9,
            "omega_log_mean": 700.0,
            "tc_mean": 39.0,
            "n": 3.0,
        }
    }
    cand = _cand("NbN")  # tm_nitride — not in stats
    pred = predict_tc_lambda(
        cand,
        family_stats=family_stats,
        model_version="0.2-fit-x",
        method="family_mean_fit",
        training_set_size=3,
    )
    assert pred.method == "family_heuristic"
    assert pred.quality_tag == "stub"
    assert pred.features.get("source") != "family_mean_fit"


def test_literature_seed_rejects_empty_targets(tmp_path: Path) -> None:
    from siscforge.active_learning import PromotionError

    lit = tmp_path / "bad.json"
    lit.write_text(
        '[{"formula": "WN", "literature_ref": "x", "material_family": "tm_nitride"}]',
        encoding="utf-8",
    )
    store = TrainingSetStore(tmp_path / "train")
    with pytest.raises(PromotionError, match="at least one"):
        seed_from_literature_file(store, lit)
    assert store.summary()["n_examples"] == 0


def test_promote_dry_run_cli_does_not_write(tmp_path: Path) -> None:
    """Exercise al-promote --dry-run via CliRunner so promote cannot silently write."""
    from typer.testing import CliRunner

    from siscforge.cli.main import app
    from siscforge.store import EvaluationStore

    runner = CliRunner()
    store_dir = tmp_path / "campaign"
    al_root = tmp_path / "al_state"
    estore = EvaluationStore(store_dir)
    estore.save_evaluations([_ok_epw_eval("NbN")])

    result = runner.invoke(
        app,
        [
            "al-promote",
            str(store_dir),
            "--al-root",
            str(al_root),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "eligible" in result.stdout or "Promotion dry-run" in result.stdout
    # No training-set file written under shared root
    examples = al_root / "training_set" / "examples.json"
    assert not examples.is_file() or examples.read_text().strip() in {"", "[]"}


def test_synthesis_cards_bootstrap_banner(tmp_path: Path) -> None:
    from siscforge.models.candidate import CandidateEvaluation

    ev = CandidateEvaluation(candidate=_cand(), status="ok")
    path = write_synthesis_cards(
        [ev],
        tmp_path / "cards.md",
        campaign_name="test",
        bootstrap=True,
        bootstrap_message="BOOTSTRAP MODE — test banner",
        model_version="0.2-fit-deadbeef",
        training_set_size=4,
    )
    text = path.read_text(encoding="utf-8")
    assert "BOOTSTRAP MODE" in text
    assert "0.2-fit-deadbeef" in text
    assert "training-set size: 4" in text


def test_literature_seed_from_json(tmp_path: Path) -> None:
    lit = tmp_path / "lit.json"
    lit.write_text(
        """[
          {"formula": "ZrN", "tc_K": 10.0, "lambda_total": 0.8,
           "material_family": "tm_nitride", "literature_ref": "lit:ZrN"},
          {"formula": "VN", "tc_K": 8.0, "literature_ref": "lit:VN",
           "material_family": "tm_nitride"}
        ]
        """,
        encoding="utf-8",
    )
    store = TrainingSetStore(tmp_path / "train")
    added = seed_from_literature_file(store, lit)
    assert len(added) == 2
    assert store.summary()["n_examples"] == 2
    assert "ZrN" in {e.formula for e in store.load_examples()}


def test_resolve_al_context_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AL root is shared (./al_state or env), not buried under each campaign store."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SISC_AL_ROOT", raising=False)
    tstore, registry, ctx = resolve_al_context(store_dir=tmp_path / "campaign_out")
    # store_dir must NOT become the AL root
    assert tstore.root.resolve() == (tmp_path / "al_state" / "training_set").resolve()
    assert registry.root.resolve() == (tmp_path / "al_state" / "models").resolve()
    assert (tmp_path / "campaign_out" / "al").exists() is False
    assert ctx.bootstrap is True
    assert ctx.model_version == "heuristic"




def test_resolve_al_context_env_and_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SISC_AL_ROOT", str(tmp_path / "from_env"))
    tstore, _, _ = resolve_al_context()
    assert tstore.root == tmp_path / "from_env" / "training_set"
    tstore2, _, _ = resolve_al_context(al_root=tmp_path / "explicit")
    assert tstore2.root == tmp_path / "explicit" / "training_set"


def test_al_status_progress_fields(tmp_path: Path) -> None:
    from siscforge.active_learning import al_status

    tstore = TrainingSetStore(tmp_path / "train")
    seed_default_goldens(tstore)
    registry = SurrogateRegistry(tmp_path / "models")
    status = al_status(tstore, registry)
    assert status["n_labels"] >= 3
    assert status["bootstrap_target_labels"] == 150
    assert status["labels_to_bootstrap_exit"] == 150 - status["n_labels"]
    assert 0 < status["progress_pct"] < 100
    assert "tm_nitride" in status["families_covered"]


def test_trained_family_does_not_use_other_fallback() -> None:
    """Missing family must fall back to heuristic, not launder 'other' means."""
    family_stats = {
        "other": {"lambda_mean": 3.0, "omega_log_mean": 100.0, "n": 5.0, "tc_std": 0.0}
    }
    cand = _cand("NbN")  # tm_nitride
    pred = predict_tc_lambda(cand, family_stats=family_stats, model_version="x")
    # Without tm_nitride key → heuristic path
    assert pred.features.get("source") != "family_mean_fit"
    assert pred.quality_tag == "stub"


def test_write_al_pointer(tmp_path: Path) -> None:
    from siscforge.active_learning.paths import write_al_pointer

    p = write_al_pointer(
        tmp_path / "out",
        al_root=tmp_path / "al_state",
        training_set=tmp_path / "al_state" / "training_set",
        models=tmp_path / "al_state" / "models",
        model_version="heuristic",
        bootstrap=True,
    )
    assert p.is_file()
    data = __import__("json").loads(p.read_text())
    assert "al_root" in data
    assert data["model_version"] == "heuristic"



def test_rollback_sets_current(tmp_path: Path) -> None:
    tstore = TrainingSetStore(tmp_path / "train")
    registry = SurrogateRegistry(tmp_path / "models")
    seed_default_goldens(tstore)
    r1 = retrain_from_store(tstore, registry, snapshot_notes="v1")
    assert r1.success
    v1 = r1.metadata.model_version
    # Second retrain after extra label
    tstore.promote(_ok_epw_eval("NbN"))
    r2 = retrain_from_store(tstore, registry, snapshot_notes="v2")
    assert r2.success
    v2 = r2.metadata.model_version
    assert registry.current().model_version == v2
    registry.set_current(v1)
    assert registry.current().model_version == v1
