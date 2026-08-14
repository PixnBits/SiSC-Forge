"""Resume / checkpoint / continue-on-error tests (mock calculator only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from siscforge.calculators import get, registry
from siscforge.calculators.base import BaseCalculator
from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig, RunConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import ElectronPhononResult, SiFeasibilityScore
from siscforge.resume import (
    find_resumable_evaluation,
    index_evaluations,
    is_successful_evaluation,
    resume_fingerprint,
)
from siscforge.silicon.feasibility import SCORER_VERSION
from siscforge.store import EvaluationStore
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride

runner = CliRunner()


def _nbn(strain: float = 0.0) -> StructureCandidate:
    return structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
        substrate="Si(001)",
        in_plane_strain=strain,
    )


def _mock_ok(cand: StructureCandidate) -> CandidateEvaluation:
    return get("mock").run(cand)


def test_resume_fingerprint_stable() -> None:
    a = _nbn(0.01)
    b = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
        substrate="Si(001)",
        in_plane_strain=0.01,
    )
    assert a.candidate_id != b.candidate_id
    assert resume_fingerprint(a) == resume_fingerprint(b)
    assert resume_fingerprint(a) != resume_fingerprint(_nbn(-0.02))


def test_is_successful_evaluation_criteria() -> None:
    cand = _nbn()
    ok = _mock_ok(cand)
    assert is_successful_evaluation(ok)

    failed = CandidateEvaluation(
        candidate=cand,
        status="failed",
        errors=["boom"],
        calculator_name="mock",
    )
    assert not is_successful_evaluation(failed)

    pending = CandidateEvaluation(candidate=cand, status="pending")
    assert not is_successful_evaluation(pending)

    surr = CandidateEvaluation(
        candidate=cand,
        status="surrogate_only",
        performance_score=10.0,
        performance_score_source="surrogate",
    )
    assert not is_successful_evaluation(surr)

    # status ok but empty results → not successful
    empty_ok = CandidateEvaluation(candidate=cand, status="ok")
    assert not is_successful_evaluation(empty_ok)

    eph_only = CandidateEvaluation(
        candidate=cand,
        status="ok",
        electron_phonon=ElectronPhononResult(
            lambda_total=1.0,
            omega_log=300.0,
            status="ok",
            quality_tag="screening",
        ),
    )
    assert is_successful_evaluation(eph_only)


def test_find_resumable_by_fingerprint() -> None:
    c1 = _nbn(0.0)
    c2 = _nbn(0.0)  # new id, same fingerprint
    ev = _mock_ok(c1)
    by_id, by_fp = index_evaluations([ev])
    assert find_resumable_evaluation(c1, by_id=by_id, by_fp=by_fp) is ev
    hit = find_resumable_evaluation(c2, by_id=by_id, by_fp=by_fp)
    assert hit is ev
    assert (
        find_resumable_evaluation(
            c2, by_id=by_id, by_fp=by_fp, force_rerun=True
        )
        is None
    )


def test_store_append_replaces_by_fingerprint(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "s")
    c1 = _nbn(0.0)
    c2 = _nbn(0.0)
    store.append_evaluation(_mock_ok(c1))
    assert len(store.load_evaluations()) == 1
    store.append_evaluation(_mock_ok(c2))
    # Same fingerprint → one row
    assert len(store.load_evaluations()) == 1
    assert store.find_successful(c2) is not None


def test_cli_resume_skips_finished(tmp_path: Path) -> None:
    """Partial store: 2 of 3 ok → second run skips 2 and only runs the missing one."""
    out = tmp_path / "camp_out"
    # Build three fixed formulas with distinct strains
    metals = [("Nb", "NbN", 0.0), ("Ti", "TiN", 0.0), ("Zr", "ZrN", 0.0)]
    stored: list[CandidateEvaluation] = []
    for metal, formula, strain in metals[:2]:
        cand = structure_to_candidate(
            build_binary_nitride(metal),
            material_family="tm_nitride",
            formula=formula,
            substrate="Si(001)",
            in_plane_strain=strain,
        )
        stored.append(_mock_ok(cand))
    store = EvaluationStore(out)
    for ev in stored:
        store.append_evaluation(ev)
    assert len(store.load_evaluations()) == 2

    cfg = CampaignConfig(
        name="resume_test",
        dry_run=True,
        enumeration={
            "formulas": ["NbN", "TiN", "ZrN"],
            "strain_values": [0.0],
            "max_candidates": 3,
            "substrates": ["Si(001)"],
        },
        formation_filter={"enabled": False},
        run={"resume": True, "continue_on_error": True, "force_rerun": False},
        output_dir=str(out),
        export_formats=["json", "csv"],
    )
    yaml_path = tmp_path / "camp.yaml"
    cfg.to_yaml(yaml_path)

    result = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "skip (already" in result.stdout
    assert "Checkpoint summary" in result.stdout
    assert "skipped=2" in result.stdout
    assert "ran=1" in result.stdout

    evals = EvaluationStore(out).load_evaluations()
    # 3 expensive + no deferred
    okish = [e for e in evals if e.status in {"ok", "mock"}]
    assert len(okish) >= 3


def test_resume_refreshes_si_score_from_campaign_weights(tmp_path: Path) -> None:
    """Resume must apply current YAML Si weights, not freeze a stale stored score.

    P2.1: _finalize_eval always replaces si_feasibility with the campaign-current
    score so changing weights/CMOS limit (or upgrading from v0.2) takes effect
    even for candidates skipped by resume.
    """
    out = tmp_path / "si_refresh_out"
    cand = _nbn(0.0)
    # Simulate a prior run with a stale non-mock Si score (wrong version/weights).
    stale_si = SiFeasibilityScore(
        total=10.0,
        version="0.2",
        weights={},
        notes="stale pre-P2.1 score",
    )
    prior = _mock_ok(cand).model_copy(
        update={"si_feasibility": stale_si, "status": "mock"}
    )
    store = EvaluationStore(out)
    store.append_evaluation(prior)

    cfg = CampaignConfig(
        name="si_refresh_resume",
        dry_run=True,
        enumeration={
            "formulas": ["NbN"],
            "strain_values": [0.0],
            "max_candidates": 1,
            "substrates": ["Si(001)"],
        },
        formation_filter={"enabled": False},
        si_feasibility={
            "weights": {
                "lattice_mismatch": 0.0,
                "thermal_budget": 0.0,
                "chemical_compatibility": 0.0,
                "buffer_availability": 0.0,
                "process_maturity": 1.0,
            }
        },
        run={"resume": True, "continue_on_error": True, "force_rerun": False},
        output_dir=str(out),
        export_formats=["json"],
    )
    yaml_path = tmp_path / "si_refresh.yaml"
    cfg.to_yaml(yaml_path)

    result = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "skip (already" in result.stdout

    evals = EvaluationStore(out).load_evaluations()
    assert len(evals) >= 1
    si = evals[0].si_feasibility
    assert si is not None
    assert si.version == SCORER_VERSION
    assert si.weights.get("process_maturity") == pytest.approx(1.0)
    assert si.total != 10.0  # no longer the frozen stale total
    assert abs(sum(si.weights.values()) - 1.0) < 1e-6


def test_cli_force_rerun_recomputes(tmp_path: Path) -> None:
    out = tmp_path / "force_out"
    cfg = CampaignConfig(
        name="force_test",
        dry_run=True,
        enumeration={
            "formulas": ["NbN", "TiN"],
            "strain_values": [0.0],
            "max_candidates": 2,
        },
        formation_filter={"enabled": False},
        output_dir=str(out),
        export_formats=["json"],
    )
    yaml_path = tmp_path / "force.yaml"
    cfg.to_yaml(yaml_path)

    r1 = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert r1.exit_code == 0, r1.stdout
    r2 = runner.invoke(
        app, ["run", "--dry-run", "--force-rerun", str(yaml_path)]
    )
    assert r2.exit_code == 0, r2.stdout
    assert "skipped=0" in r2.stdout or "skip (already" not in r2.stdout
    assert "ran=2" in r2.stdout


class _FlakyCalc(BaseCalculator):
    """Fails once for NbN, succeeds otherwise."""

    name = "flaky_test"

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        if candidate.formula.startswith("Nb"):
            raise RuntimeError("simulated EPW crash for NbN")
        return get("mock").run(candidate, **kwargs)


def test_continue_on_error_does_not_block(tmp_path: Path) -> None:
    registry.register(_FlakyCalc(), overwrite=True)
    out = tmp_path / "cont_out"
    cfg = CampaignConfig(
        name="continue_test",
        dry_run=False,  # so we can select flaky_test
        enumeration={
            "formulas": ["NbN", "TiN"],
            "strain_values": [0.0],
            "max_candidates": 2,
        },
        formation_filter={"enabled": False},
        calculators=[{"name": "flaky_test"}],
        run={"resume": False, "continue_on_error": True},
        output_dir=str(out),
        export_formats=["json"],
    )
    yaml_path = tmp_path / "cont.yaml"
    cfg.to_yaml(yaml_path)

    result = runner.invoke(
        app, ["run", "-C", "flaky_test", str(yaml_path)]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "failed" in result.stdout.lower()
    assert "continuing" in result.stdout.lower() or "Checkpoint summary" in result.stdout
    evals = EvaluationStore(out).load_evaluations()
    statuses = {e.candidate.formula: e.status for e in evals}
    assert statuses.get("NbN") == "failed"
    assert statuses.get("TiN") in {"mock", "ok"}
    assert any(e.errors for e in evals if e.status == "failed")


def test_fail_fast_aborts(tmp_path: Path) -> None:
    registry.register(_FlakyCalc(), overwrite=True)
    out = tmp_path / "fast_out"
    cfg = CampaignConfig(
        name="failfast_test",
        enumeration={
            "formulas": ["NbN", "TiN"],
            "strain_values": [0.0],
            "max_candidates": 2,
        },
        formation_filter={"enabled": False},
        calculators=[{"name": "flaky_test"}],
        run={"resume": False, "continue_on_error": True},
        output_dir=str(out),
        export_formats=["json"],
    )
    yaml_path = tmp_path / "ff.yaml"
    cfg.to_yaml(yaml_path)

    result = runner.invoke(
        app, ["run", "-C", "flaky_test", "--fail-fast", str(yaml_path)]
    )
    assert result.exit_code == 1
    # TiN should not have been evaluated if NbN failed first and order is NbN, TiN
    evals = EvaluationStore(out).load_evaluations()
    formulas = {e.candidate.formula for e in evals}
    # May have flushed NbN failed only
    assert "TiN" not in formulas or all(
        e.status == "failed" for e in evals if e.candidate.formula == "NbN"
    )


def test_run_config_yaml_roundtrip(tmp_path: Path) -> None:
    cfg = CampaignConfig(
        name="r",
        run=RunConfig(resume=True, continue_on_error=False, force_rerun=True),
    )
    p = tmp_path / "r.yaml"
    cfg.to_yaml(p)
    loaded = CampaignConfig.from_yaml(p)
    assert loaded.run.resume is True
    assert loaded.run.continue_on_error is False
    assert loaded.run.force_rerun is True


def test_run_help_lists_resume_flags() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    # Rich/Typer may emit CSI codes even when stdout is not a TTY (CI).
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
    assert "--force-rerun" in plain
    assert "--fail-fast" in plain
