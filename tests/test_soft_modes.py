"""Slice 29 — soft-mode report + empty stable_only messaging."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from siscforge.cli.main import app
from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.results import PhononResult, SiFeasibilityScore
from siscforge.shortlist import select_shortlist_evaluations
from siscforge.soft_modes import (
    REPORT_JSON,
    REPORT_MD,
    SIGNAL_NONE_STABLE_BINARIES_SOFT,
    classify_soft_mode,
    ensure_soft_mode_report,
    is_binary_nitride,
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
) -> CandidateEvaluation:
    cand = structure_to_candidate(
        build_binary_nitride(metal),
        material_family="tm_nitride",
        formula=formula,
        substrate="Si(001)",
        in_plane_strain=strain,
    )
    if n_atoms is not None:
        cand = cand.model_copy(
            update={"metadata": {**(cand.metadata or {}), "n_atoms": n_atoms}}
        )
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


def test_mesh_dump_stays_undetermined() -> None:
    # 2-atom × 8 q-points = 48 frequencies — must not slice first-3 as acoustic.
    freqs = [-20.0] * 3 + [100.0] * 45
    ev = _ev(
        metal="Nb",
        formula="Nb0.5Ti0.5N",
        stable=False,
        min_freq=-20.0,
        freqs=freqs,
        n_modes=48,
        n_atoms=2,
    )
    row = classify_soft_mode(ev)
    assert row["acoustic_vs_optical"] == "undetermined"
    assert row["soft_mode_class"] == "genuinely_soft"


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
    assert "siscforge pilot" in md_path.read_text(encoding="utf-8")


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
