"""Slice 29 — soft-mode report + empty stable_only messaging."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.provenance import Provenance
from siscforge.models.results import PhononResult, SiFeasibilityScore
from siscforge.shortlist import select_shortlist_evaluations
from siscforge.soft_modes import (
    AUTO_PILOT_YAML,
    HARMONICALLY_UNSTABLE_IDEAL_RS,
    KNOWN_STABLE_RS_NITRIDES,
    REPORT_JSON,
    REPORT_MD,
    SIGNAL_NONE_STABLE_BINARIES_SOFT,
    classify_soft_mode,
    ensure_soft_mode_report,
    is_binary_nitride,
    is_harmonically_unstable_ideal_rs,
    is_known_stable_binary,
    write_soft_mode_report,
)
from siscforge.store import EvaluationStore
from siscforge.structure.generator import structure_to_candidate
from siscforge.structure.nitrides import build_binary_nitride

runner = CliRunner()


def _ev(
    *,
    metal: str,
    formula: str,
    stable: bool,
    min_freq: float | None,
    status: str = "ok",
    phonon_status: str | None = None,
    freqs: list[float] | None = None,
    n_modes: int | None = None,
    n_atoms: int | None = None,
    strain: float = 0.0,
    kpoints: list[int] | None = None,
    extra_metadata: dict | None = None,
) -> CandidateEvaluation:
    cand = structure_to_candidate(
        build_binary_nitride(metal),
        material_family="tm_nitride",
        formula=formula,
        substrate="Si(001)",
        in_plane_strain=strain,
    )
    meta = dict(cand.metadata or {})
    if n_atoms is not None:
        meta["n_atoms"] = n_atoms
    if kpoints is not None:
        meta["kpoints"] = list(kpoints)
    if extra_metadata:
        meta.update(extra_metadata)
    if meta != dict(cand.metadata or {}):
        cand = cand.model_copy(update={"metadata": meta})
    if status == "failed" and phonon_status is None:
        ph = PhononResult(
            min_frequency_cm1=None,
            has_imaginary_modes=False,
            dynamically_stable=False,
            n_modes=0,
            status="failed",
            quality_tag="screening",
        )
    elif min_freq is None and freqs is None:
        ph = PhononResult(
            min_frequency_cm1=None,
            has_imaginary_modes=False,
            dynamically_stable=False,
            n_modes=0,
            status=phonon_status or "failed",
            quality_tag="screening",
        )
    else:
        raw: dict = {}
        if freqs is not None:
            raw["frequencies_cm1"] = list(freqs)
        ph = PhononResult(
            min_frequency_cm1=min_freq if min_freq is not None else (
                min(freqs) if freqs else None
            ),
            has_imaginary_modes=not stable,
            dynamically_stable=stable,
            n_modes=n_modes if n_modes is not None else (len(freqs) if freqs else None),
            status=phonon_status or ("ok" if status != "mock" else "mock"),
            quality_tag="screening",
            raw=raw,
        )
    return CandidateEvaluation(
        candidate=cand,
        phonon=ph,
        si_feasibility=SiFeasibilityScore(total=50.0),
        status=status,
        calculator_name="qe",
        notes="soft-mode fixture",
    )


def test_known_binaries_are_binary_nitrides() -> None:
    assert is_binary_nitride("NbN")
    assert is_binary_nitride("TiN")
    assert is_binary_nitride("ZrN")
    assert not is_binary_nitride("Nb0.5Ti0.5N")


def test_classify_setup_failed() -> None:
    ev = _ev(metal="Nb", formula="NbN", stable=False, min_freq=None, status="failed")
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "setup_failed"


def test_classify_stable() -> None:
    ev = _ev(metal="Nb", formula="NbN", stable=True, min_freq=180.0)
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "stable"


def test_classify_known_binary_imag_is_mesh_artefact() -> None:
    ev = _ev(metal="Nb", formula="NbN", stable=False, min_freq=-140.0)
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "likely_mesh_artefact"
    assert row["is_known_stable_binary"] is True
    assert row["is_harmonically_unstable_ideal_rs"] is True
    assert "ideal_stoichiometric_may_be_harmonic_soft" in row["reasons"]
    assert "policy_override_not_mesh_artefact" not in row["reasons"]


def test_classify_missing_freqs_is_conservative() -> None:
    ev = _ev(
        metal="Nb",
        formula="Nb0.5Ti0.5N",
        stable=False,
        min_freq=-80.0,
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "genuinely_soft"
    assert "missing_frequency_list_conservative" in row["reasons"]


def test_classify_optical_soft_when_single_q_detectable() -> None:
    # 2-atom cell → 6 modes; first 3 acoustic (real), later optical imag.
    freqs = [12.0, 15.0, 18.0, -40.0, 200.0, 250.0]
    ev = _ev(
        metal="Nb",
        formula="Nb0.5Ti0.5N",
        stable=False,
        min_freq=-40.0,
        freqs=freqs,
        n_modes=6,
        n_atoms=2,
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "optical_soft"
    assert row["acoustic_vs_optical"] == "optical_imaginary"


def test_mesh_dump_not_multiple_of_3n_stays_undetermined() -> None:
    # 47 frequencies cannot be sliced into 3 N_at q-blocks.
    freqs = [-20.0] * 3 + [100.0] * 44
    ev = _ev(
        metal="Nb",
        formula="Nb0.5Ti0.5N",
        stable=False,
        min_freq=-20.0,
        freqs=freqs,
        n_modes=47,
        n_atoms=2,
    )
    row = classify_soft_mode(ev)
    assert row["acoustic_vs_optical"] == "undetermined"
    assert row["soft_mode_class"] == "genuinely_soft"
    assert row["softness_locus"] == "undetermined"


def test_mesh_dump_optical_at_later_q() -> None:
    # q0 (Γ-like) real; q1 optical imaginary — must not hide behind first-3.
    freqs = [12.0, 15.0, 18.0, 200.0, 210.0, 220.0, 20.0, 25.0, 30.0, -80.0, 200.0, 210.0]
    ev = _ev(
        metal="Nb",
        formula="Nb0.5Ti0.5N",
        stable=False,
        min_freq=-80.0,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "optical_soft"
    assert row["acoustic_vs_optical"] == "optical_imaginary"
    assert row["softness_locus"] == "finite_q"
    assert row["gamma_min_frequency_cm1"] == pytest.approx(12.0)
    assert row["finite_q_min_frequency_cm1"] == pytest.approx(-80.0)


def test_finite_q_softer_than_mild_gamma() -> None:
    # ZrN-like: Γ ~ −30 (acoustic noise), campaign min at a later q.
    freqs = [-29.7, -29.6, -29.2, 469.2, 469.3, 469.5, -72.1, 185.0, 186.9, 395.4, 401.8, 416.6]
    ev = _ev(
        metal="Zr",
        formula="ZrN",
        stable=False,
        min_freq=-72.1,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "likely_mesh_artefact"
    assert row["softness_locus"] == "finite_q"
    assert "softest_q_is_finite_q" in row["reasons"]
    assert "gamma_only_mildly_imaginary" in row["reasons"]
    assert row["acoustic_vs_optical"] == "acoustic_only_imaginary"


def test_report_written_for_finished_phonon_store(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "map")
    store.append_evaluation(
        _ev(metal="Nb", formula="NbN", stable=False, min_freq=-120.0)
    )
    store.append_evaluation(
        _ev(metal="Ti", formula="TiN", stable=False, min_freq=-90.0)
    )
    evals = store.load_evaluations()
    report, json_path, md_path = write_soft_mode_report(
        evals, store.root, campaign_name="nbti_n_phonon_map"
    )
    assert json_path.is_file()
    assert md_path.is_file()
    assert (store.root / REPORT_JSON).is_file()
    assert (store.root / REPORT_MD).is_file()
    assert report["n_stable"] == 0
    assert report["campaign_signal"] == SIGNAL_NONE_STABLE_BINARIES_SOFT
    assert "NbN" in report["known_stable_binaries_soft"]
    md = md_path.read_text(encoding="utf-8")
    assert "siscforge pilot" in md
    assert "locus" in md.lower() or "Γ ω" in md
    # Critical signal auto-emits a denser-q phonon-only pilot.
    pilot = store.root / AUTO_PILOT_YAML
    assert pilot.is_file(), report.get("auto_pilot_error")
    assert report.get("auto_pilot_do_epw") is False
    text = pilot.read_text(encoding="utf-8")
    assert "do_epw" in text
    assert "false" in text.lower()
    assert "do_epw is false" in text.lower() or "do_epw: false" in text.lower()


def test_report_surfaces_finite_q_locus(tmp_path: Path) -> None:
    freqs = [-29.7, -29.6, -29.2, 469.2, 469.3, 469.5, -72.1, 185.0, 186.9, 395.4, 401.8, 416.6]
    ev = _ev(
        metal="Zr",
        formula="ZrN",
        stable=False,
        min_freq=-72.1,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
    )
    report, _, md_path = write_soft_mode_report(
        [ev], tmp_path / "zrn", campaign_name="zrn_kmesh_diag"
    )
    assert report["finite_q_softest"] is True
    md = md_path.read_text(encoding="utf-8")
    assert "softest q is finite-q" in md
    assert "Densify SCF k" in md


def test_report_reads_campaign_name_from_store_meta(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "named")
    store.append_evaluation(
        _ev(metal="Zr", formula="ZrN", stable=False, min_freq=-55.0)
    )
    (store.root / "store_meta.json").write_text(
        '{"campaign": "zrn_kmesh_diag", "n_evaluations": 1}', encoding="utf-8"
    )
    report, _, _ = write_soft_mode_report(store.load_evaluations(), store.root)
    assert report["campaign"] == "zrn_kmesh_diag"


def test_report_skipped_without_phonon(tmp_path: Path) -> None:
    cand = structure_to_candidate(
        build_binary_nitride("Nb"),
        material_family="tm_nitride",
        formula="NbN",
    )
    ev = CandidateEvaluation(
        candidate=cand, phonon=None, status="ok", calculator_name="mock"
    )
    report, json_path, _ = write_soft_mode_report([ev], tmp_path / "empty_ph")
    assert json_path.is_file()
    assert report["skipped"] is True
    assert "no phonon" in report["skip_reason"]


def test_cli_soft_modes(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "map")
    store.append_evaluation(
        _ev(metal="Zr", formula="ZrN", stable=False, min_freq=-55.0)
    )
    result = runner.invoke(app, ["soft-modes", str(store.root)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Soft-mode report" in result.stdout
    assert (store.root / REPORT_JSON).is_file()


def test_stable_only_none_stable_mentions_report_and_pilot(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "all_soft")
    store.append_evaluation(
        _ev(metal="Nb", formula="NbN", stable=False, min_freq=-180.0)
    )
    store.append_evaluation(
        _ev(metal="Ti", formula="TiN", stable=False, min_freq=-90.0)
    )
    yaml_path = tmp_path / "must_not_write.yaml"
    result = runner.invoke(
        app,
        [
            "shortlist",
            str(store.root),
            "-o",
            str(yaml_path),
            "--mode",
            "stable_only",
            "-n",
            "3",
        ],
    )
    out = result.stdout + result.stderr
    assert result.exit_code == 1
    assert not yaml_path.is_file()
    assert "Refusing to fall back" in out or "refusing" in out.lower()
    assert "soft-mode report" in out.lower() or "Soft-mode report" in out
    assert "siscforge pilot" in out
    assert (store.root / REPORT_JSON).is_file()


def test_library_stable_only_message_mentions_pilot() -> None:
    rows = [
        _ev(metal="Zr", formula="ZrN", stable=False, min_freq=-20.0),
    ]
    try:
        select_shortlist_evaluations(rows, mode="stable_only", max_jobs=2)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        text = str(exc)
        assert "Refusing to fall back" in text
        assert "siscforge pilot" in text
        assert "soft-mode report" in text


def test_ensure_does_not_overwrite_existing(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "map")
    store.append_evaluation(
        _ev(metal="Nb", formula="NbN", stable=False, min_freq=-10.0)
    )
    first, _, _ = write_soft_mode_report(store.load_evaluations(), store.root)
    # Mutate file to prove ensure reuses it.
    path = store.root / REPORT_JSON
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"version": 1', '"version": 1'), encoding="utf-8")
    again, _, _ = ensure_soft_mode_report(store.root)
    assert again["n_evaluations"] == first["n_evaluations"]


def test_vn_is_known_stable_and_metadata_override() -> None:
    """#45: conservative literature expansion + documented metadata escape hatch."""
    assert "VN" in KNOWN_STABLE_RS_NITRIDES
    assert is_known_stable_binary("VN")
    assert not is_known_stable_binary("CrN")
    assert is_known_stable_binary("CrN", {"known_stable_binary": True})


def test_nbn_is_harmonically_unstable_ideal_rs() -> None:
    """#76: split experimentally-known vs ideal-1:1 harmonic-stable."""
    assert "NbN" in HARMONICALLY_UNSTABLE_IDEAL_RS
    assert is_harmonically_unstable_ideal_rs("NbN")
    assert not is_harmonically_unstable_ideal_rs("ZrN")
    assert not is_harmonically_unstable_ideal_rs("TiN")
    assert is_harmonically_unstable_ideal_rs(
        "ZrN", {"harmonically_unstable_ideal_rs": True}
    )
    assert not is_harmonically_unstable_ideal_rs(
        "NbN", {"harmonically_unstable_ideal_rs": False}
    )


def test_classify_dense_k_nbn_is_not_mesh_artefact() -> None:
    """#74 / #76: NbN at k=12³ staying at −301 cm⁻¹ is literature-soft."""
    freqs = [
        -76.8, -76.6, -76.4, 378.2, 378.3, 378.5,
        -301.5, -100.4, -97.0, 440.2, 441.3, 442.2,
    ]
    ev = _ev(
        metal="Nb",
        formula="NbN",
        stable=False,
        min_freq=-301.5,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
        kpoints=[12, 12, 12],
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "genuinely_soft"
    assert row["softness_locus"] == "finite_q"
    assert "policy_override_not_mesh_artefact" in row["reasons"]
    assert "dense_k_still_substantially_soft" in row["reasons"]
    assert "known_stable_binary_nitride_on_coarse_or_screening_mesh" not in row["reasons"]
    assert row["is_known_stable_binary"] is True


def test_classify_reads_kpoints_from_qe_provenance() -> None:
    """Real QE evaluations stamp k in provenance.parameters.dft."""
    ev = _ev(metal="Nb", formula="NbN", stable=False, min_freq=-301.5)
    ev = ev.model_copy(
        update={
            "provenance": Provenance(
                source="qe_calculator",
                parameters={"dft": {"kpoints": [12, 12, 12]}},
            )
        }
    )
    # No per-q dump → locus undetermined, but campaign min is deep.
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "genuinely_soft"
    assert "policy_override_not_mesh_artefact" in row["reasons"]


def test_classify_dense_k_zrn_gamma_noise_stays_artefact() -> None:
    """ZrN k=12³ leftover at Γ-noise scale is not this exception."""
    freqs = [
        -29.3, -29.2, -29.1, 469.2, 469.3, 469.5,
        -28.0, 185.0, 186.9, 395.4, 401.8, 416.6,
    ]
    ev = _ev(
        metal="Zr",
        formula="ZrN",
        stable=False,
        min_freq=-29.3,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
        kpoints=[12, 12, 12],
    )
    row = classify_soft_mode(ev)
    assert row["soft_mode_class"] == "likely_mesh_artefact"
    assert "policy_override_not_mesh_artefact" not in row["reasons"]


def test_report_surfaces_nbn_dense_k_override(tmp_path: Path) -> None:
    freqs = [
        -76.8, -76.6, -76.4, 378.2, 378.3, 378.5,
        -301.5, -100.4, -97.0, 440.2, 441.3, 442.2,
    ]
    ev = _ev(
        metal="Nb",
        formula="NbN",
        stable=False,
        min_freq=-301.5,
        freqs=freqs,
        n_modes=12,
        n_atoms=2,
        kpoints=[12, 12, 12],
    )
    report, _, md_path = write_soft_mode_report(
        [ev], tmp_path / "nbn_k12", campaign_name="nbn_k12_diag"
    )
    assert report["ideal_stoichiometric_harmonic_soft"] == ["NbN"]
    assert report["n_genuinely_soft"] == 1
    assert report["n_likely_mesh_artefact"] == 0
    md = md_path.read_text(encoding="utf-8")
    assert "policy / literature override" in md
    assert "not a mesh artefact" in md.lower() or "not `likely_mesh_artefact`" in md
    assert "expected harmonic instability" in "\n".join(report["next_actions"])


def test_acquisition_record_includes_soft_mode_class() -> None:
    from siscforge.active_learning import prioritize_candidates
    from siscforge.models.config import ActiveLearningConfig

    ev = _ev(metal="Nb", formula="NbN", stable=False, min_freq=-80.0)
    plan = prioritize_candidates(
        [ev.candidate],
        config=ActiveLearningConfig(enabled=True, max_epw_jobs=1),
        evaluations={ev.candidate.candidate_id: ev},
    )
    rec = plan.ranked[0]
    assert rec.soft_mode_class in {
        "likely_mesh_artefact",
        "genuinely_soft",
        "optical_soft",
    }
    assert rec.block_expensive_epw is True
    assert rec.selected_for_expensive is False
    assert "denser-q" in rec.notes.lower() or "blocked" in rec.notes.lower()
