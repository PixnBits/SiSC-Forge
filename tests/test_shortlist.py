"""Desktop shortlist campaign helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from siscforge.calculators import get
from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import CampaignConfig
from siscforge.models.results import PhononResult, SiFeasibilityScore
from siscforge.shortlist import (
    build_shortlist_campaign,
    evaluation_to_spec,
    filter_stable_evaluations,
    select_shortlist_evaluations,
    write_campaign_yaml,
)
from siscforge.store import EvaluationStore
from siscforge.structure.generator import generate_candidates
from siscforge.structure.nitrides import build_binary_nitride
from siscforge.structure.generator import structure_to_candidate

runner = CliRunner()


def _seed_al_store(tmp_path: Path) -> Path:
    """Write a mini store with 3 mock evals, 2 AL-selected."""
    out = tmp_path / "al_store"
    store = EvaluationStore(out)
    formulas = [("Nb", "NbN", True), ("Ti", "TiN", True), ("Zr", "ZrN", False)]
    evals = []
    for i, (metal, formula, selected) in enumerate(formulas):
        cand = structure_to_candidate(
            build_binary_nitride(metal),
            material_family="tm_nitride",
            formula=formula,
            substrate="Si(001)",
            in_plane_strain=-0.01 * i,
        )
        ev = get("mock").run(cand)
        ev = ev.model_copy(
            update={
                "al_selected_for_expensive": selected,
                "acquisition_score": 0.5 - 0.05 * i,
                "rank": i + 1,
            }
        )
        store.append_evaluation(ev)
        evals.append(ev)
    return out


def _ev_with_phonon(
    *,
    metal: str,
    formula: str,
    stable: bool,
    min_freq: float,
    si: float,
    status: str = "ok",
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
        status=status if status != "mock" else "mock",
        quality_tag="screening",
    )
    return CandidateEvaluation(
        candidate=cand,
        phonon=ph,
        si_feasibility=SiFeasibilityScore(total=si),
        status=status,
        calculator_name="qe",
        notes="phonon-map fixture",
    )


def _seed_phonon_store(tmp_path: Path) -> Path:
    """Store with stable + unstable phonon-only evaluations."""
    out = tmp_path / "phonon_store"
    store = EvaluationStore(out)
    rows = [
        _ev_with_phonon(
            metal="Nb", formula="NbN", stable=True, min_freq=120.0, si=58.0, strain=-0.02
        ),
        _ev_with_phonon(
            metal="Ti", formula="TiN", stable=True, min_freq=80.0, si=62.0, strain=-0.01
        ),
        _ev_with_phonon(
            metal="Zr",
            formula="ZrN",
            stable=False,
            min_freq=-35.0,
            si=70.0,  # high Si but imag modes — must be dropped
            strain=0.0,
        ),
        _ev_with_phonon(
            metal="Nb",
            formula="NbN",
            stable=True,
            min_freq=15.0,  # soft but stable
            si=55.0,
            strain=0.01,
        ),
    ]
    for ev in rows:
        store.append_evaluation(ev)
    return out


def test_select_al_selected(tmp_path: Path) -> None:
    store = EvaluationStore(_seed_al_store(tmp_path))
    evals = store.load_evaluations()
    chosen = select_shortlist_evaluations(evals, mode="al_selected", max_jobs=6)
    assert len(chosen) == 2
    assert all(e.al_selected_for_expensive for e in chosen)


def test_build_shortlist_campaign_and_enumerate(tmp_path: Path) -> None:
    store_dir = _seed_al_store(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    cfg, chosen = build_shortlist_campaign(
        evals,
        name="test_short",
        source_store=str(store_dir),
        max_jobs=2,
        mode="al_selected",
        output_dir=str(tmp_path / "epw_out"),
        pseudo_dir="/tmp/fake_pseudo",
    )
    assert len(chosen) == 2
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.dft.do_epw is True
    assert cfg.dft.epw.enabled is True
    assert cfg.run.resume is True
    assert cfg.run.continue_on_error is True
    assert cfg.active_learning.enabled is False
    assert cfg.formation_filter.enabled is False

    cands = generate_candidates(cfg)
    assert len(cands) == 2
    # Preserved ids from store
    ids = {c.candidate_id for c in cands}
    assert ids == {e.candidate.candidate_id for e in chosen}


def test_write_and_cli_shortlist(tmp_path: Path) -> None:
    store_dir = _seed_al_store(tmp_path)
    yaml_path = tmp_path / "shortlist.yaml"
    result = runner.invoke(
        app,
        [
            "shortlist",
            str(store_dir),
            "-o",
            str(yaml_path),
            "-n",
            "2",
            "--name",
            "cli_short",
            "--campaign-output-dir",
            str(tmp_path / "cli_epw"),
            "--pseudo-dir",
            "/usr/share/espresso/pseudo",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert yaml_path.is_file()
    cfg = CampaignConfig.from_yaml(yaml_path)
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.output_dir == str(tmp_path / "cli_epw")

    # Dry-run shortlist
    r2 = runner.invoke(app, ["run", "--dry-run", str(yaml_path)])
    assert r2.exit_code == 0, r2.stdout + r2.stderr
    assert "Checkpoint summary" in r2.stdout
    store = EvaluationStore(tmp_path / "cli_epw")
    assert len(store.load_evaluations()) == 2


def test_require_real_does_not_skip_mock(tmp_path: Path) -> None:
    """qe-epw resume must not treat dry-run mock as finished."""
    from siscforge.resume import is_successful_evaluation

    cand = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
    )
    mock_ev = get("mock").run(cand)
    assert is_successful_evaluation(mock_ev) is True
    assert is_successful_evaluation(mock_ev, require_real=True) is False


def test_evaluation_to_spec_keeps_cif() -> None:
    cand = structure_to_candidate(
        build_binary_nitride("Ti"),
        material_family="tm_nitride",
        formula="TiN",
        in_plane_strain=-0.02,
    )
    ev = get("mock").run(cand)
    spec = evaluation_to_spec(ev)
    assert spec.formula == "TiN"
    assert spec.structure_cif
    assert spec.in_plane_strain == -0.02


# ---------------------------------------------------------------------------
# Slice 23 — stable_only / phonon-first
# ---------------------------------------------------------------------------


def test_stable_only_drops_imag_mode_rows(tmp_path: Path) -> None:
    store_dir = _seed_phonon_store(tmp_path)
    evals = EvaluationStore(store_dir).load_evaluations()
    assert len(evals) == 4

    pool = filter_stable_evaluations(evals, mode="stable_only")
    assert len(pool) == 3
    assert all(e.phonon and e.phonon.dynamically_stable for e in pool)
    assert all(not e.phonon.has_imaginary_modes for e in pool)  # type: ignore[union-attr]
    # High-Si ZrN with imag modes excluded
    assert all(e.candidate.formula != "ZrN" for e in pool)

    chosen = select_shortlist_evaluations(
        evals, mode="stable_only", max_jobs=2, stable_sort="si"
    )
    assert len(chosen) == 2
    # Highest Si among stable: TiN (62) then NbN (58)
    assert chosen[0].candidate.formula == "TiN"
    assert chosen[0].si_feasibility and chosen[0].si_feasibility.total == 62.0
    assert chosen[1].candidate.formula == "NbN"


def test_stable_only_none_stable_raises_clear_error() -> None:
    unstable = [
        _ev_with_phonon(
            metal="Zr",
            formula="ZrN",
            stable=False,
            min_freq=-40.0,
            si=80.0,
        ),
        _ev_with_phonon(
            metal="Hf",
            formula="HfN",
            stable=False,
            min_freq=-10.0,
            si=75.0,
        ),
    ]
    with pytest.raises(ValueError, match="No dynamically stable"):
        select_shortlist_evaluations(unstable, mode="stable_only", max_jobs=3)
    with pytest.raises(ValueError, match="Refusing to fall back"):
        select_shortlist_evaluations(unstable, mode="stable_only", max_jobs=3)


def test_stable_or_soft_respects_soft_min() -> None:
    rows = [
        _ev_with_phonon(
            metal="Nb", formula="NbN", stable=True, min_freq=5.0, si=50.0
        ),
        # CrN is not on the known-stable list — tiny imag is numeric noise.
        _ev_with_phonon(
            metal="Cr", formula="CrN", stable=False, min_freq=-2.0, si=60.0
        ),
    ]
    # soft_min 0: only non-imaginary
    chosen = select_shortlist_evaluations(
        rows, mode="stable_or_soft", max_jobs=5, soft_min_cm1=0.0
    )
    assert len(chosen) == 1
    assert chosen[0].candidate.formula == "NbN"

    # soft_min -5: allow tiny imag (-2) on a non-known-stable cell
    chosen2 = select_shortlist_evaluations(
        rows, mode="stable_or_soft", max_jobs=5, soft_min_cm1=-5.0
    )
    assert len(chosen2) == 2


def test_known_stable_soft_binary_not_shortlisted_for_epw() -> None:
    """#45: NbN that looks soft on coarse mesh cannot go to EPW shortlist."""
    soft_nbn = _ev_with_phonon(
        metal="Nb", formula="NbN", stable=False, min_freq=-20.0, si=90.0
    )
    with pytest.raises(ValueError, match="stable_or_soft"):
        select_shortlist_evaluations(
            [soft_nbn], mode="stable_or_soft", max_jobs=3, soft_min_cm1=-50.0
        )
    # After denser-q confirmation + now-stable phonon, it may proceed.
    confirmed = soft_nbn.model_copy(
        update={
            "phonon": soft_nbn.phonon.model_copy(
                update={
                    "dynamically_stable": True,
                    "has_imaginary_modes": False,
                    "min_frequency_cm1": 40.0,
                }
            )
            if soft_nbn.phonon is not None
            else None,
            "candidate": soft_nbn.candidate.model_copy(
                update={
                    "metadata": {
                        **(soft_nbn.candidate.metadata or {}),
                        "denser_q_confirmed": True,
                        "pilot_target_qpoints": [3, 3, 3],
                    }
                }
            ),
        }
    )
    chosen = select_shortlist_evaluations(
        [confirmed], mode="stable_only", max_jobs=1
    )
    assert chosen[0].candidate.formula == "NbN"


def test_cli_shortlist_stable_only(tmp_path: Path) -> None:
    store_dir = _seed_phonon_store(tmp_path)
    yaml_path = tmp_path / "stable_short.yaml"
    result = runner.invoke(
        app,
        [
            "shortlist",
            str(store_dir),
            "-o",
            str(yaml_path),
            "--mode",
            "stable_only",
            "-n",
            "2",
            "--name",
            "stable_epw",
            "--campaign-output-dir",
            str(tmp_path / "stable_epw_out"),
            "--pseudo-dir",
            "/usr/share/espresso/pseudo",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "stable_only" in result.stdout
    cfg = CampaignConfig.from_yaml(yaml_path)
    assert len(cfg.enumeration.candidate_specs) == 2
    assert cfg.dft.do_epw is True
    # ZrN (unstable) must not appear
    formulas = [s.formula for s in cfg.enumeration.candidate_specs]
    assert "ZrN" not in formulas
    assert "TiN" in formulas


def test_cli_shortlist_none_stable_exits_nonzero(tmp_path: Path) -> None:
    out = tmp_path / "all_unstable"
    store = EvaluationStore(out)
    store.append_evaluation(
        _ev_with_phonon(
            metal="Zr", formula="ZrN", stable=False, min_freq=-20.0, si=90.0
        )
    )
    yaml_path = tmp_path / "should_fail.yaml"
    result = runner.invoke(
        app,
        [
            "shortlist",
            str(out),
            "-o",
            str(yaml_path),
            "--mode",
            "stable_only",
            "-n",
            "2",
        ],
    )
    assert result.exit_code == 1
    assert "stable" in (result.stdout + result.stderr).lower()
    assert not yaml_path.is_file()


def test_phonon_map_example_config_loads() -> None:
    """examples/nbti_n_phonon_map.yaml is a valid phonon-only campaign."""
    root = Path(__file__).resolve().parents[1]
    yaml_path = root / "examples" / "nbti_n_phonon_map.yaml"
    assert yaml_path.is_file()
    cfg = CampaignConfig.from_yaml(yaml_path)
    assert cfg.dft.do_phonon is True
    assert cfg.dft.do_epw is False
    assert cfg.dft.epw.enabled is False
    assert cfg.dft.engine == "qe"
    assert cfg.active_learning.enabled is False
    # Screening map — not refine 4³
    assert list(cfg.dft.qpoints) == [2, 2, 2]
    assert cfg.output_dir == "outputs/nbti_n_phonon_map"
    # Strain grid includes fine steps around 0
    strains = list(cfg.enumeration.strain_values)
    assert -0.04 in strains
    assert 0.02 in strains


def test_phonon_map_dry_run(tmp_path: Path) -> None:
    """Dry-run phonon map example completes with mock calculator."""
    root = Path(__file__).resolve().parents[1]
    src = root / "examples" / "nbti_n_phonon_map.yaml"
    # Use a temp output_dir so we don't pollute repo outputs/
    # Campaign still loads from examples path; override -o
    result = runner.invoke(
        app,
        [
            "run",
            "--dry-run",
            str(src),
            "--output-dir",
            str(tmp_path / "phonon_map_out"),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Checkpoint summary" in result.stdout
    # Phonon-only banner only for real qe path; dry-run uses mock — still ok
    store = EvaluationStore(tmp_path / "phonon_map_out")
    evals = store.load_evaluations()
    assert len(evals) > 0
    # Mock always fills phonon stability fields
    assert any(e.phonon is not None for e in evals)


def test_rank_stable_first_flag(tmp_path: Path) -> None:
    from siscforge.ranking import rank_evaluations

    rows = [
        _ev_with_phonon(
            metal="Zr", formula="ZrN", stable=False, min_freq=-30.0, si=90.0
        ),
        _ev_with_phonon(
            metal="Nb", formula="NbN", stable=True, min_freq=100.0, si=40.0
        ),
    ]
    # Without stable_first, high Si unstable may still rank well after penalty
    ranked = rank_evaluations(rows, stable_first=True)
    assert ranked[0].phonon and ranked[0].phonon.dynamically_stable
    assert ranked[0].candidate.formula == "NbN"
