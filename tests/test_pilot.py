"""Slice 29 — denser-q phonon pilot helper."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import CampaignConfig, DFTConfig, EPWConfig
from siscforge.models.results import PhononResult, SiFeasibilityScore
from siscforge.pilot import (
    build_pilot_campaign,
    parse_qpoints,
    select_pilot_evaluations,
    write_pilot_yaml,
)
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates, structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride

runner = CliRunner()


def _ev(
    *,
    metal: str,
    formula: str,
    min_freq: float,
    stable: bool = False,
    strain: float = 0.0,
) -> CandidateEvaluation:
    cand = structure_to_candidate(
        build_binary_nitride(metal),
        material_family="tm_nitride",
        formula=formula,
        substrate="Si(001)",
        in_plane_strain=strain,
    )
    ph = PhononResult(
        min_frequency_cm1=min_freq,
        has_imaginary_modes=not stable,
        dynamically_stable=stable,
        status="ok",
        quality_tag="screening",
    )
    return CandidateEvaluation(
        candidate=cand,
        phonon=ph,
        si_feasibility=SiFeasibilityScore(total=55.0),
        status="ok",
        calculator_name="qe",
    )


def _seed(tmp_path: Path) -> Path:
    out = tmp_path / "phonon_map"
    store = EvaluationStore(out)
    rows = [
        _ev(metal="Nb", formula="NbN", min_freq=-180.0, strain=-0.02),
        _ev(metal="Ti", formula="TiN", min_freq=-40.0, strain=0.0),
        _ev(metal="Zr", formula="ZrN", min_freq=-90.0, strain=0.01),
        _ev(metal="Nb", formula="Nb0.5Ti0.5N", min_freq=-200.0, strain=0.0),
    ]
    for ev in rows:
        store.append_evaluation(ev)
    src = CampaignConfig(
        name="nbti_n_phonon_map",
        dft=DFTConfig(
            engine="qe",
            do_phonon=True,
            do_epw=False,
            qpoints=[2, 2, 2],
            nproc=16,
            pseudo_dir="/usr/share/espresso/pseudo",
            epw=EPWConfig(enabled=False, nqc=[2, 2, 2]),
        ),
        output_dir=str(out),
    )
    store.save_campaign(src)
    return out


def test_parse_qpoints() -> None:
    assert parse_qpoints(None) == [3, 3, 3]
    assert parse_qpoints("4,4,4") == [4, 4, 4]
    assert parse_qpoints("3") == [3, 3, 3]


def test_select_binaries_first(tmp_path: Path) -> None:
    evals = EvaluationStore(_seed(tmp_path)).load_evaluations()
    chosen = select_pilot_evaluations(evals, mode="binaries", max_jobs=3)
    assert len(chosen) == 3
    assert all("0.5" not in e.candidate.formula for e in chosen)
    # Least-soft among binaries: TiN (-40) first
    assert chosen[0].candidate.formula == "TiN"


def test_select_least_soft_includes_ternary(tmp_path: Path) -> None:
    evals = EvaluationStore(_seed(tmp_path)).load_evaluations()
    chosen = select_pilot_evaluations(evals, mode="least_soft", max_jobs=2)
    assert chosen[0].candidate.formula == "TiN"
    assert chosen[1].candidate.formula == "ZrN"


def test_build_pilot_yaml_reuses_specs_no_epw(tmp_path: Path) -> None:
    store_dir = _seed(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    src = CampaignConfig.from_yaml(store_dir / "campaign_resolved.yaml")
    cfg, chosen = build_pilot_campaign(
        evals,
        name="nbti_n_pilot_q3",
        source_store=str(store_dir),
        source_campaign=src,
        mode="binaries",
        max_jobs=2,
        qpoints=[3, 3, 3],
        output_dir=str(tmp_path / "pilot_out"),
    )
    assert len(chosen) == 2
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.dft.do_epw is False
    assert cfg.dft.epw.enabled is False
    assert list(cfg.dft.qpoints) == [3, 3, 3]
    assert cfg.dft.nproc == 16
    assert cfg.dft.pseudo_dir == "/usr/share/espresso/pseudo"
    assert cfg.run.resume is True
    assert cfg.output_dir != str(store_dir)
    # Must not re-enumerate the full metal × strain grid.
    assert not cfg.enumeration.metals
    assert cfg.enumeration.max_candidates == 2
    generated = generate_candidates(cfg)
    assert len(generated) == 2
    assert {c.candidate_id for c in generated} == {
        e.candidate.candidate_id for e in chosen
    }


def test_pilot_refuses_same_output_dir(tmp_path: Path) -> None:
    store_dir = _seed(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    try:
        build_pilot_campaign(
            evals,
            source_store=str(store_dir.resolve()),
            output_dir=str(store_dir.resolve()),
            mode="binaries",
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "must differ" in str(exc)


def test_cli_pilot_writes_loadable_yaml(tmp_path: Path) -> None:
    store_dir = _seed(tmp_path)
    yaml_path = tmp_path / "pilot.yaml"
    result = runner.invoke(
        app,
        [
            "pilot",
            str(store_dir),
            "-o",
            str(yaml_path),
            "--mode",
            "binaries",
            "-n",
            "2",
            "--qpoints",
            "3,3,3",
            "--name",
            "cli_pilot",
            "--campaign-output-dir",
            str(tmp_path / "cli_pilot_out"),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert yaml_path.is_file()
    assert "do_epw=false" in result.stdout
    cfg = CampaignConfig.from_yaml(yaml_path)
    assert cfg.dft.do_epw is False
    assert cfg.dft.epw.enabled is False
    assert list(cfg.dft.qpoints) == [3, 3, 3]
    assert len(cfg.enumeration.candidate_specs) == 2
    # Dry-run the emitted campaign (mock) — must not enable EPW.
    r2 = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert r2.exit_code == 0, r2.stdout + r2.stderr
    store = EvaluationStore(tmp_path / "cli_pilot_out")
    assert len(store.load_evaluations()) == 2


def test_write_pilot_yaml_header(tmp_path: Path) -> None:
    store_dir = _seed(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    cfg, _ = build_pilot_campaign(
        evals,
        source_store=str(store_dir),
        output_dir=str(tmp_path / "pout"),
        mode="least_soft",
        max_jobs=1,
    )
    path = write_pilot_yaml(cfg, tmp_path / "p.yaml")
    text = path.read_text(encoding="utf-8")
    assert "do_epw is false" in text
    assert "not production dynamical-stability proof" in text
