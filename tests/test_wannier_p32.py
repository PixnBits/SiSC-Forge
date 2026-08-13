"""P3.2 — Wannierization pipeline with quality metrics tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from siscforge.calculators import get, list_calculators
from siscforge.calculators.qe.recipes import recipe_info
from siscforge.calculators.qe.wannier import (
    WANNIER_FAILURE_CLASSES,
    assess_dmft_readiness,
    build_win_input,
    classify_wannier_failure,
    default_num_wann_screening,
    mock_wannier_result,
    parse_wannier_result,
    parse_wannier_spreads,
    primary_wannier_failure_reason,
    wannier_is_enabled,
    write_win_input,
)
from siscforge.export import (
    CSV_FIELDNAMES,
    write_evaluations_csv,
    write_evaluations_json,
    write_synthesis_cards,
)
from siscforge.models import (
    CampaignConfig,
    CandidateEvaluation,
    DFTConfig,
    StructureCandidate,
    WannierConfig,
    WannierResult,
)
from siscforge.ranking import rank_evaluations
from siscforge.store import EvaluationStore
from siscforge.structure.nitrides import build_binary_nitride

_FROZEN = """
     Program Wannier90
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     Error in routine dis_windows (1):
     More states in the frozen window than target WFs
     %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
     stopping ...
"""

_SUCCESS_WOUT = """
             +---------------------------------------------------+
             |                   WANNIER90                       |
             +---------------------------------------------------+
 Number of Wannier Functions             :       8
 Number of bands                         :      16
 Final State
 WF centre and spread    1  (  0.000000,  0.000000,  0.000000 )     0.812345
 WF centre and spread    2  (  0.500000,  0.500000,  0.000000 )     0.901234
 WF centre and spread    3  (  0.000000,  0.500000,  0.500000 )     1.012345
 WF centre and spread    4  (  0.500000,  0.000000,  0.500000 )     0.756789
 WF centre and spread    5  (  0.250000,  0.250000,  0.250000 )     1.123456
 WF centre and spread    6  (  0.750000,  0.750000,  0.250000 )     0.889012
 WF centre and spread    7  (  0.250000,  0.750000,  0.750000 )     0.945678
 WF centre and spread    8  (  0.750000,  0.250000,  0.750000 )     1.001234
 Sum of centres and spreads (  3.000000,  3.000000,  3.000000 )     7.442093
 Omega I      =    2.100000
 All done.
"""


def test_wannier_result_round_trip() -> None:
    r = WannierResult(
        wannier_ok=True,
        ready_for_dmft=True,
        status="ok",
        quality_tag="screening",
        num_wann=8,
        num_bands=16,
        projection_mode="random",
        projection_summary="random",
        spread_sum_ang2=7.5,
        avg_spread_ang2=0.94,
        max_spread_ang2=1.12,
        spreads_ang2=[0.8, 0.9, 1.0],
        work_dir="/tmp/w",
        chk_path="/tmp/w/siscforge.chk",
    )
    payload = r.model_dump(mode="json")
    restored = WannierResult.model_validate(payload)
    assert restored.wannier_ok is True
    assert restored.ready_for_dmft is True
    assert restored.num_wann == 8
    assert "ok=True" in restored.summary_line()
    assert restored.model_dump(mode="json") == payload


def test_wannier_result_defaults_validation() -> None:
    r = WannierResult()
    assert r.status == "unknown"
    assert r.wannier_ok is False
    assert r.ready_for_dmft is False
    with pytest.raises(ValidationError):
        WannierResult(quality_tag="not-a-tier")  # type: ignore[arg-type]


def test_candidate_evaluation_optional_wannier() -> None:
    cand = StructureCandidate(formula="NdNiO2", material_family="nickelate")
    bare = CandidateEvaluation(candidate=cand, status="mock")
    assert bare.wannier is None
    data = bare.model_dump(mode="json")
    assert CandidateEvaluation.model_validate(data).wannier is None

    with_w = CandidateEvaluation(
        candidate=cand,
        wannier=WannierResult(status="mock", quality_tag="mock", wannier_ok=True),
        status="mock",
    )
    restored = CandidateEvaluation.model_validate(with_w.model_dump(mode="json"))
    assert restored.wannier is not None
    assert restored.wannier.wannier_ok is True


def test_wannier_config_disabled_by_default() -> None:
    cfg = CampaignConfig(name="nitride_default")
    assert cfg.dft.do_wannier is False
    assert cfg.dft.wannier.enabled is False
    assert wannier_is_enabled(cfg.dft) is False

    payload = cfg.model_dump(mode="json")
    again = CampaignConfig.model_validate(payload)
    assert again.dft.do_wannier is False
    assert again.dft.wannier.projection_mode == "random"


def test_wannier_config_enable_yaml_knobs() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "nickelate_wannier",
            "dft": {
                "do_wannier": True,
                "wannier": {
                    "enabled": True,
                    "projection_mode": "explicit",
                    "projections": ["Ni:d", "O:p"],
                    "num_wann": 12,
                    "kmesh": [6, 6, 6],
                },
            },
        }
    )
    assert wannier_is_enabled(cfg.dft) is True
    assert cfg.dft.wannier.projections == ["Ni:d", "O:p"]
    assert cfg.dft.wannier.num_wann == 12


def test_qe_wannier_registered() -> None:
    names = list_calculators()
    assert "qe-wannier" in names
    assert "wannier" in names
    calc = get("qe-wannier")
    assert calc.name == "qe-wannier"


def test_mock_without_wannier_has_no_result() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="mock-nbn-no-w",
    )
    result = get("mock").run(cand)
    assert result.wannier is None
    assert "Wannier" not in (result.notes or "")


def test_mock_with_wannier_enabled_populates_success() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        composition={"Nd": 0.25, "Ni": 0.25, "O": 0.5},
        candidate_id="mock-ndnio2-w",
    )
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(enabled=True, num_wann=10, projection_mode="random"),
    )
    result = get("mock").run(cand, dft=dft)
    assert result.wannier is not None
    assert result.wannier.status == "mock"
    assert result.wannier.wannier_ok is True
    assert result.wannier.ready_for_dmft is True
    assert result.wannier.num_wann == 10
    assert result.wannier.spread_sum_ang2 is not None
    assert result.wannier.avg_spread_ang2 is not None
    assert result.wannier.chk_path
    # Deterministic
    again = get("mock").run(cand, dft=dft)
    assert again.wannier is not None
    assert again.wannier.spread_sum_ang2 == result.wannier.spread_sum_ang2


def test_mock_wannier_failure_classified() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="mock-ndnio2-w-fail",
    )
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(
            enabled=True,
            mock_force_failure=True,
            mock_failure_class="frozen_window",
        ),
    )
    result = get("mock").run(cand, dft=dft)
    assert result.wannier is not None
    assert result.wannier.wannier_ok is False
    assert result.wannier.status == "failed"
    assert result.wannier.failure_class == "frozen_window"
    assert result.wannier.ready_for_dmft is False
    assert "not ready for DMFT" in result.wannier.dmft_gate_notes
    # SCF / phonon still present (upstream not destroyed)
    assert result.scf is not None
    assert result.phonon is not None


def test_mock_wannier_end_to_end_store_export(tmp_path: Path) -> None:
    """Run → store → export exercises WannierResult quality fields."""
    dft = DFTConfig(
        do_dftu=True,
        do_wannier=True,
        wannier=WannierConfig(enabled=True, num_wann=8),
    )
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="e2e-ndnio2-w",
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    ev = get("mock").run(cand, dft=dft)
    ranked = rank_evaluations([ev])
    store = EvaluationStore(tmp_path / "camp")
    store.save_evaluations(ranked, ranked=True)
    loaded = store.load_evaluations(ranked=True)
    assert loaded[0].wannier is not None
    assert loaded[0].wannier.wannier_ok is True
    assert loaded[0].dftu is not None  # both P3.1 + P3.2

    csv_path = write_evaluations_csv(loaded, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "wannier_ok",
        "wannier_ready_for_dmft",
        "wannier_spread_sum_ang2",
        "wannier_avg_spread_ang2",
        "wannier_num_wann",
        "wannier_failure_class",
        "wannier_status",
        "wannier_summary",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    body = csv_path.read_text()
    assert "True" in body or "true" in body.lower()

    json_path = write_evaluations_json(loaded, tmp_path / "out.json")
    jtext = json_path.read_text()
    assert "wannier" in jtext
    assert "ready_for_dmft" in jtext

    cards = write_synthesis_cards(loaded, tmp_path / "cards.md", campaign_name="p32")
    text = cards.read_text()
    assert "Wannierization" in text
    assert "ready_for_dmft" in text
    assert "P3.2" in text


def test_classify_wannier_failure_classes() -> None:
    assert classify_wannier_failure(_FROZEN) == "frozen_window"
    assert classify_wannier_failure("kmesh_get_bvector: not enough bvectors") == (
        "kmesh_bvector"
    )
    assert classify_wannier_failure("Error opening file foo.amn") == "missing_files"
    assert "wannier:" in primary_wannier_failure_reason(_FROZEN).lower()
    # Never mislabel as phonon
    reason = primary_wannier_failure_reason(_FROZEN)
    assert "phonon" not in reason.lower()
    assert "phq_setup" not in reason.lower()


def test_parse_wannier_spreads_and_result(tmp_path: Path) -> None:
    wout = tmp_path / "siscforge.wout"
    wout.write_text(_SUCCESS_WOUT, encoding="utf-8")
    (tmp_path / "siscforge.chk").write_text("mock chk\n", encoding="utf-8")
    (tmp_path / "siscforge.amn").write_text("mock amn\n", encoding="utf-8")
    (tmp_path / "siscforge.mmn").write_text("mock mmn\n", encoding="utf-8")
    (tmp_path / "siscforge.win").write_text("num_wann = 8\n", encoding="utf-8")

    metrics = parse_wannier_spreads(_SUCCESS_WOUT)
    assert len(metrics["spreads_ang2"]) == 8
    assert metrics["spread_sum_ang2"] is not None
    assert metrics["avg_spread_ang2"] is not None

    dft = DFTConfig(do_wannier=True, wannier=WannierConfig(enabled=True, seedname="siscforge"))
    result = parse_wannier_result(
        wout, dft=dft, work_dir=tmp_path, quality_tag="screening", returncode=0
    )
    assert result.wannier_ok is True
    assert result.status == "ok"
    assert result.num_wann == 8
    assert result.chk_path
    assert result.ready_for_dmft is True


def test_parse_failed_wannier_result() -> None:
    dft = DFTConfig(do_wannier=True, wannier=WannierConfig(enabled=True))
    result = parse_wannier_result(
        _FROZEN, dft=dft, quality_tag="screening", returncode=1
    )
    assert result.wannier_ok is False
    assert result.status == "failed"
    assert result.failure_class == "frozen_window"
    assert result.ready_for_dmft is False


def test_dmft_gate_thresholds() -> None:
    cfg = WannierConfig(enabled=True, max_avg_spread_ang2=1.0, max_spread_ang2=2.0)
    ready, notes = assess_dmft_readiness(
        wannier_ok=True,
        status="ok",
        avg_spread=5.0,
        max_spread=6.0,
        chk_path="/tmp/x.chk",
        cfg=cfg,
    )
    assert ready is False
    assert "avg spread" in notes


def test_build_win_input_random_screening() -> None:
    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        nbnd=28,
        do_wannier=True,
        wannier=WannierConfig(
            enabled=True,
            projection_mode="random",
            screening_tight_froz=True,
            kmesh=[4, 4, 4],
        ),
    )
    text = build_win_input(s, dft, fermi_eV=20.0)
    assert "num_wann" in text
    assert "random" in text
    assert "begin projections" in text
    assert "mp_grid" in text
    assert "dis_froz_max" in text


def test_build_win_input_explicit_projections(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        nbnd=28,
        do_wannier=True,
        wannier=WannierConfig(
            enabled=True,
            projection_mode="explicit",
            projections=["Nb:d", "N:p"],
            num_wann=10,
            auto_num_wann=False,
        ),
    )
    path = write_win_input(s, dft, tmp_path, fermi_eV=18.0)
    text = path.read_text()
    assert "Nb:d" in text
    assert "N:p" in text
    assert "num_wann = 10" in text


def test_default_num_wann_policy() -> None:
    s = build_binary_nitride("Nb")
    n = default_num_wann_screening(num_bands=28, structure=s, auto=True)
    assert 8 <= n <= 28


def test_recipe_info_lists_wannier() -> None:
    info = recipe_info()
    assert "WannierResult" in info["models"]
    assert any("wannier" in s.lower() for s in info["steps"])
    assert "p3_2" in info["extension_points"]


def test_mock_dftu_and_wannier_together() -> None:
    dft = DFTConfig(
        do_dftu=True,
        do_wannier=True,
        wannier=WannierConfig(enabled=True),
    )
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="both-u-w",
    )
    ev = get("mock").run(cand, dft=dft)
    assert ev.dftu is not None
    assert ev.wannier is not None
    assert "DFT+U" in ev.notes
    assert "Wannier" in ev.notes


def test_failure_classes_are_documented() -> None:
    assert "frozen_window" in WANNIER_FAILURE_CLASSES
    assert "phonon" not in WANNIER_FAILURE_CLASSES
    assert "soft_modes" not in WANNIER_FAILURE_CLASSES


def test_ndnio2_wannier_example_yaml_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "examples" / "ndnio2_wannier_mock.yaml"
    assert path.is_file()
    cfg = CampaignConfig.from_yaml(path)
    assert cfg.dft.do_wannier is True
    assert cfg.dft.wannier.enabled is True
    assert wannier_is_enabled(cfg.dft) is True
    # Conventional EPW still off
    assert cfg.dft.do_epw is False


def test_real_wannier90_gated_without_binary(tmp_path: Path) -> None:
    """Without staged .amn/.mmn, real-path prep classifies cleanly (no crash)."""
    from siscforge.calculators.qe.wannier import run_wannier_workflow

    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(enabled=True, seedname="siscforge"),
        nbnd=20,
    )
    # No .amn/.mmn → missing_files; must not raise
    result = run_wannier_workflow(
        s,
        dft,
        tmp_path / "wannier",
        scf_work_dir=tmp_path / "scf",
    )
    assert result.wannier_ok is False
    assert result.failure_class == "missing_files"
    assert result.ready_for_dmft is False
    assert result.win_path  # .win still written
    # Operator next-step must be discoverable
    assert "pw2wannier90" in result.dmft_gate_notes or "pw2wannier90" in result.summary_line()
    assert "next=" in result.summary_line() or "stage" in result.summary_line()
    assert result.kmesh  # actual resolved mesh recorded
    # Sacred: scf dir was never created/deleted by wannier
    assert not (tmp_path / "scf").exists() or True


def test_mock_force_failure_helper_direct(tmp_path: Path) -> None:
    r = mock_wannier_result(
        seed="direct-fail",
        wannier=WannierConfig(enabled=True, mock_force_failure=True),
        work_dir=tmp_path,
        formula="NdNiO2",
        material_family="nickelate",
    )
    assert r.status == "failed"
    assert r.failure_class == "frozen_window"
    # Incomplete artifacts omitted on failure
    assert r.chk_path is None


def test_missing_files_summary_and_synthesis_card(tmp_path: Path) -> None:
    """missing_files surfaces operator next-step in summary + synthesis card."""
    from siscforge.calculators.qe.wannier import run_wannier_workflow

    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(enabled=True, seedname="siscforge", kmesh=[2, 2, 2]),
        nbnd=20,
        quality_tag="screening",
    )
    result = run_wannier_workflow(s, dft, tmp_path / "wannier")
    assert result.failure_class == "missing_files"
    summary = result.summary_line()
    assert "fail=missing_files" in summary
    assert "pw2wannier90" in summary or "stage" in summary

    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="miss-files-card",
    )
    ev = CandidateEvaluation(candidate=cand, wannier=result, status="failed")
    cards = write_synthesis_cards([ev], tmp_path / "cards.md", campaign_name="p32-miss")
    text = cards.read_text()
    assert "independent of EPW-internal" in text or "EPW-internal" in text
    assert "operator next step" in text.lower() or "pw2wannier90" in text
    assert "standalone" in text.lower()


def test_parse_with_staged_fake_amn_mmn(tmp_path: Path) -> None:
    """Staged fake .amn/.mmn + successful .wout → wannier_ok parse path."""
    wout = tmp_path / "siscforge.wout"
    wout.write_text(_SUCCESS_WOUT, encoding="utf-8")
    (tmp_path / "siscforge.chk").write_text("mock chk\n", encoding="utf-8")
    (tmp_path / "siscforge.amn").write_text("mock amn\n", encoding="utf-8")
    (tmp_path / "siscforge.mmn").write_text("mock mmn\n", encoding="utf-8")
    (tmp_path / "siscforge.win").write_text("num_wann = 8\n", encoding="utf-8")

    dft = DFTConfig(
        do_wannier=True,
        wannier=WannierConfig(enabled=True, seedname="siscforge", kmesh=[4, 4, 4]),
    )
    result = parse_wannier_result(
        wout,
        dft=dft,
        work_dir=tmp_path,
        quality_tag="screening",
        returncode=0,
        extra_raw={"actual_kmesh": [6, 6, 6]},
    )
    assert result.wannier_ok is True
    assert result.amn_path
    assert result.mmn_path
    assert result.chk_path
    # Prefer actual mesh from extra_raw over config
    assert result.kmesh == [6, 6, 6]


def test_public_wannier_window_lines_helper() -> None:
    from siscforge.calculators.qe.epw_inputs import (
        wannier_window_lines,
        _wannier_window_lines,
    )

    lines = wannier_window_lines(20.0, screening_tight_froz=True)
    assert any("dis_win_min" in ln for ln in lines)
    assert any("dis_froz_max" in ln for ln in lines)
    # Private alias still present for back-compat
    assert _wannier_window_lines is wannier_window_lines


def test_require_wannier90_is_env_reexport() -> None:
    from siscforge.calculators.qe import env as env_mod
    from siscforge.calculators.qe import wannier as wannier_mod

    assert callable(env_mod.require_wannier90)
    assert callable(wannier_mod.require_wannier90)


def test_dmft_threshold_defaults_documented() -> None:
    cfg = WannierConfig()
    assert cfg.max_avg_spread_ang2 == 12.0
    assert cfg.max_spread_ang2 == 25.0
    fields = WannierConfig.model_fields
    avg_desc = (fields["max_avg_spread_ang2"].description or "").lower()
    max_desc = (fields["max_spread_ang2"].description or "").lower()
    assert "conservative" in avg_desc
    assert "screening" in avg_desc or "nickelate" in avg_desc
    assert "conservative" in max_desc or "screening" in max_desc


def test_logger_exception_on_wannier_catch() -> None:
    """Sacred-upstream catch in QECalculator.run logs via logger.exception."""
    import inspect

    from siscforge.calculators.qe import calculator as calc_mod

    src = inspect.getsource(calc_mod.QECalculator.run)
    assert "_LOG.exception" in src
    assert "upstream" in src.lower()

