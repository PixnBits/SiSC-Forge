"""Refine-from-store campaign builder tests."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig
from siscforge.models.results import (
    ElectronPhononResult,
    PhononResult,
    SiFeasibilityScore,
)
from siscforge.refine import (
    build_refine_campaign,
    default_refine_dft,
    select_refine_evaluations,
)
from siscforge.shortlist import default_screening_dft, write_campaign_yaml
from siscforge.store import EvaluationStore

runner = CliRunner()


def _ev(
    formula: str,
    *,
    si: float,
    lam: float,
    tc: float,
    strain: float = 0.0,
    stable: bool = True,
    rank: int | None = None,
    cid: str | None = None,
) -> CandidateEvaluation:
    from siscforge.structure.generator import structure_to_candidate
    from siscforge.structure.nitrides import build_binary_nitride

    # Use a real NbN-like CIF so dry-run structure rebuild works
    base = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula=formula,
        substrate="Si(001)",
        in_plane_strain=strain,
    )
    c = base.model_copy(
        update={
            "formula": formula,
            "candidate_id": cid or f"id-{formula}-{strain}",
            "in_plane_strain": strain,
        }
    )
    return CandidateEvaluation(
        candidate=c,
        phonon=PhononResult(
            min_frequency_cm1=80.0 if stable else -15.0,
            has_imaginary_modes=not stable,
            dynamically_stable=stable,
            status="ok",
            quality_tag="screening",
        ),
        electron_phonon=ElectronPhononResult(
            lambda_total=lam,
            omega_log=200.0,
            Tc_allen_dynes=tc,
            converged=True,
            status="ok",
            quality_tag="screening",
        ),
        si_feasibility=SiFeasibilityScore(total=si),
        performance_score=tc,
        performance_score_source="epw",
        status="ok",
        calculator_name="qe-epw",
        rank=rank,
        result_quality="unreliable" if lam > 5 else "screening_suspect",
        quality_flags=["high_lambda", "quality_tag_screening"],
    )


def _store_with_evals(tmp_path: Path) -> Path:
    store = EvaluationStore(tmp_path / "shortlist_store")
    evals = [
        _ev("Nb0.25Ti0.75N", si=58.0, lam=6.0, tc=40.0, strain=-0.03, rank=2),
        _ev("Nb0.5Ti0.5N", si=54.0, lam=12.0, tc=80.0, strain=-0.02, rank=1),
        _ev("NbN", si=50.0, lam=5.0, tc=30.0, strain=0.0, rank=3),
    ]
    store.save_evaluations(evals)
    return store.root


def test_select_top_si() -> None:
    evals = [
        _ev("A", si=40.0, lam=6.0, tc=40.0),
        _ev("B", si=60.0, lam=10.0, tc=50.0),
        _ev("C", si=55.0, lam=4.0, tc=20.0),
    ]
    chosen = select_refine_evaluations(evals, mode="top_si", max_jobs=2)
    assert len(chosen) == 2
    assert chosen[0].candidate.formula == "B"
    assert chosen[1].candidate.formula == "C"


def test_select_ids() -> None:
    evals = [
        _ev("A", si=40.0, lam=6.0, tc=40.0, cid="aaaa-1111"),
        _ev("B", si=60.0, lam=10.0, tc=50.0, cid="bbbb-2222"),
    ]
    chosen = select_refine_evaluations(
        evals, mode="ids", max_jobs=2, candidate_ids=["bbbb"]
    )
    assert len(chosen) == 1
    assert chosen[0].candidate.formula == "B"


def test_refine_dft_denser_than_screening() -> None:
    scr = default_screening_dft(nproc=8)
    dense = default_refine_dft(tier="workstation_dense", nproc=16)
    prod = default_refine_dft(tier="production", nproc=16)
    assert dense.quality_tag == "production"
    assert prod.quality_tag == "production"
    assert dense.epw.nkf[0] > scr.epw.nkf[0]
    assert dense.epw.nqf[0] > scr.epw.nqf[0]
    assert dense.qpoints[0] >= scr.qpoints[0]
    assert dense.epw.npool == 16
    assert dense.nproc == 16
    assert prod.epw.nkf[0] >= dense.epw.nkf[0]


def test_build_refine_campaign_yaml(tmp_path: Path) -> None:
    store = _store_with_evals(tmp_path)
    evals = EvaluationStore(store).load_evaluations()
    cfg, chosen = build_refine_campaign(
        evals,
        name="test_refine",
        source_store=str(store),
        max_jobs=2,
        mode="top_si",
        tier="workstation_dense",
        output_dir=str(tmp_path / "refine_out"),
        nproc=8,
        pseudo_dir="/tmp/pseudos",
    )
    assert len(chosen) == 2
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.output_dir == str(tmp_path / "refine_out")
    assert cfg.dft.quality_tag == "production"
    assert cfg.dft.epw.nkf[0] >= 12
    assert cfg.dft.epw.npool == 8
    assert cfg.run.resume is True
    assert cfg.run.continue_on_error is True
    assert cfg.active_learning.enabled is False
    # Specs preserve CIF
    assert all(s.structure_cif for s in cfg.enumeration.candidate_specs)
    # Distinct from screening defaults
    scr = default_screening_dft()
    assert cfg.dft.epw.nkf != scr.epw.nkf

    yml = write_campaign_yaml(cfg, tmp_path / "refine.yaml")
    loaded = CampaignConfig.from_yaml(yml)
    assert len(loaded.enumeration.candidate_specs) == 2
    assert loaded.dft.quality_tag == "production"


def test_cli_refine(tmp_path: Path) -> None:
    store = _store_with_evals(tmp_path)
    out_yaml = tmp_path / "refine.yaml"
    result = runner.invoke(
        app,
        [
            "refine",
            str(store),
            "-o",
            str(out_yaml),
            "--mode",
            "top_si",
            "-n",
            "2",
            "--tier",
            "workstation_dense",
            "--name",
            "cli_refine",
            "--campaign-output-dir",
            str(tmp_path / "cli_refine_out"),
            "--nproc",
            "8",
            "--pseudo-dir",
            "/tmp/fake_pseudo",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert out_yaml.is_file()
    assert "Refine" in result.stdout
    assert "do not cite" in result.stdout.lower() or "refinement" in result.stdout.lower()
    cfg = CampaignConfig.from_yaml(out_yaml)
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.dft.epw.nkf[0] > 6

    # Dry-run loads refine YAML
    r2 = runner.invoke(app, ["run", "--dry-run", str(out_yaml)])
    assert r2.exit_code == 0, r2.stdout + r2.stderr
