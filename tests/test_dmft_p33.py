"""P3.3 — DMFTResult model and solid_dmft / mock recipe tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from siscforge.calculators import get, list_calculators
from siscforge.calculators.qe.dmft import (
    DMFT_FAILURE_CLASSES,
    classify_dmft_failure,
    dmft_is_enabled,
    evaluate_wannier_gate,
    mock_dmft_result,
    parse_dmft_observables,
    run_dmft_workflow,
    run_solid_dmft,
    triqs_available,
)
from siscforge.calculators.qe.recipes import recipe_info
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
    DMFTConfig,
    DMFTResult,
    StructureCandidate,
    WannierConfig,
    WannierResult,
)
from siscforge.ranking import rank_evaluations
from siscforge.store import EvaluationStore


def _ready_wannier(**kwargs) -> WannierResult:
    defaults = dict(
        wannier_ok=True,
        ready_for_dmft=True,
        status="ok",
        quality_tag="screening",
        work_dir="/tmp/w",
        chk_path="/tmp/w/siscforge.chk",
    )
    defaults.update(kwargs)
    return WannierResult(**defaults)


def test_dmft_result_round_trip() -> None:
    r = DMFTResult(
        status="ok",
        quality_tag="screening",
        converged=True,
        U_eV=5.0,
        J_eV=0.8,
        occupancy_summary={"Ni_d": 8.8},
        filling=8.8,
        mass_enhancement=3.1,
        leading_pairing_eigenvalue=None,
        pairing_symmetry=None,
        solver="mock",
        beta=40.0,
        n_cycles=10000,
        wannier_work_dir="/tmp/w",
        wannier_chk_path="/tmp/w/siscforge.chk",
        wannier_ready_for_dmft=True,
    )
    payload = r.model_dump(mode="json")
    restored = DMFTResult.model_validate(payload)
    assert restored.U_eV == 5.0
    assert restored.mass_enhancement == 3.1
    assert restored.leading_pairing_eigenvalue is None
    assert "solver=mock" in restored.summary_line()
    assert restored.model_dump(mode="json") == payload


def test_dmft_result_defaults_validation() -> None:
    r = DMFTResult()
    assert r.status == "unknown"
    assert r.converged is False
    assert r.leading_pairing_eigenvalue is None
    assert r.pairing_symmetry is None
    with pytest.raises(ValidationError):
        DMFTResult(quality_tag="not-a-tier")  # type: ignore[arg-type]


def test_candidate_evaluation_optional_dmft() -> None:
    cand = StructureCandidate(formula="NdNiO2", material_family="nickelate")
    bare = CandidateEvaluation(candidate=cand, status="mock")
    assert bare.dmft is None
    data = bare.model_dump(mode="json")
    assert CandidateEvaluation.model_validate(data).dmft is None

    with_d = CandidateEvaluation(
        candidate=cand,
        dmft=DMFTResult(status="mock", quality_tag="mock", solver="mock"),
        status="mock",
    )
    restored = CandidateEvaluation.model_validate(with_d.model_dump(mode="json"))
    assert restored.dmft is not None
    assert restored.dmft.solver == "mock"


def test_dmft_config_disabled_by_default() -> None:
    cfg = CampaignConfig(name="nitride_default")
    assert cfg.dft.do_dmft is False
    assert cfg.dft.dmft.enabled is False
    assert cfg.dft.dmft.solver == "mock"
    assert cfg.dft.dmft.allow_without_wannier_gate is False
    assert dmft_is_enabled(cfg.dft) is False

    payload = cfg.model_dump(mode="json")
    again = CampaignConfig.model_validate(payload)
    assert again.dft.do_dmft is False
    assert again.dft.dmft.U_eV == 5.0
    assert again.dft.dmft.beta == 40.0


def test_dmft_config_enable_yaml_knobs() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "nickelate_dmft",
            "dft": {
                "do_dmft": True,
                "dmft": {
                    "enabled": True,
                    "solver": "mock",
                    "U_eV": 5.5,
                    "J_eV": 0.7,
                    "beta": 50.0,
                    "n_cycles": 8000,
                },
            },
        }
    )
    assert dmft_is_enabled(cfg.dft) is True
    assert cfg.dft.dmft.U_eV == 5.5
    assert cfg.dft.dmft.n_cycles == 8000


def test_dmft_config_screening_cutoffs_yaml() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "nickelate_dmft_cutoffs",
            "dft": {
                "do_dmft": True,
                "dmft": {
                    "enabled": True,
                    "d_imp_occ_conv": 0.03,
                    "d_Gimp_conv": 0.08,
                },
            },
        }
    )
    assert cfg.dft.dmft.d_imp_occ_conv == pytest.approx(0.03)
    assert cfg.dft.dmft.d_Gimp_conv == pytest.approx(0.08)
    assert cfg.dft.dmft.d_G0_conv == pytest.approx(0.05)
    assert cfg.dft.dmft.d_Sigma_conv == pytest.approx(0.05)


def test_dmft_config_rejects_negative_u() -> None:
    with pytest.raises(ValidationError):
        DMFTConfig(U_by_species={"Ni": -1.0})


def test_qe_dmft_registered() -> None:
    names = list_calculators()
    assert "qe-dmft" in names
    assert "dmft" in names
    calc = get("qe-dmft")
    assert calc.name == "qe-dmft"


def test_mock_without_dmft_has_no_result() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="mock-nbn-no-dmft",
    )
    result = get("mock").run(cand)
    assert result.dmft is None
    assert "DMFT" not in (result.notes or "")
    # Conventional fields still present
    assert result.scf is not None
    assert result.phonon is not None


def test_mock_with_dmft_enabled_populates_success() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        composition={"Nd": 0.25, "Ni": 0.25, "O": 0.5},
        candidate_id="mock-ndnio2-dmft",
    )
    dft = DFTConfig(
        do_wannier=True,
        do_dmft=True,
        wannier=WannierConfig(enabled=True, num_wann=10),
        dmft=DMFTConfig(enabled=True, solver="mock", U_eV=5.0, J_eV=0.8),
    )
    result = get("mock").run(cand, dft=dft)
    assert result.dmft is not None
    assert result.dmft.status == "mock"
    assert result.dmft.converged is True
    assert result.dmft.solver == "mock"
    assert result.dmft.U_eV == 5.0
    assert result.dmft.occupancy_summary
    assert result.dmft.mass_enhancement is not None
    assert result.dmft.leading_pairing_eigenvalue is not None  # P3.4 mock fill
    assert result.dmft.pairing_symmetry == "d_x2-y2"
    assert "illustrative" in (result.dmft.raw.get("physics_label") or "")
    assert "not literature-validated" in (result.dmft.raw.get("physics_label") or "")
    assert "p3_x_real_launch" in (result.dmft.raw.get("extension_hooks") or {})
    assert result.wannier is not None
    assert result.wannier.ready_for_dmft is True
    assert result.dmft.wannier_ready_for_dmft is True
    again = get("mock").run(cand, dft=dft)
    assert again.dmft is not None
    assert again.dmft.mass_enhancement == result.dmft.mass_enhancement


def test_mock_dmft_failure() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="mock-ndnio2-dmft-fail",
    )
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(
            enabled=True,
            solver="mock",
            mock_force_failure=True,
            mock_failure_class="not_converged",
        ),
    )
    result = get("mock").run(cand, dft=dft)
    assert result.dmft is not None
    assert result.dmft.status == "failed"
    assert result.dmft.converged is False
    assert result.dmft.failure_class == "not_converged"
    # Sacred upstream: SCF / phonon still present
    assert result.scf is not None
    assert result.phonon is not None


def test_mock_bypass_without_wannier() -> None:
    """Documented mock bypass: solver=mock may run without ready_for_dmft."""
    r = mock_dmft_result(
        seed="bypass",
        dmft=DMFTConfig(enabled=True, solver="mock", mock_bypass_gate=True),
        wannier=None,
        formula="NdNiO2",
        material_family="nickelate",
    )
    assert r.status == "mock"
    assert r.converged is True
    assert "bypass" in r.gate_notes
    assert "illustrative" in (r.raw.get("physics_label") or "")
    assert "literature-validated" in (r.provenance.notes or "")


def test_gate_refusal_non_mock_without_ready_wannier() -> None:
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(
            enabled=True,
            solver="solid_dmft",
            allow_without_wannier_gate=False,
            mock_bypass_gate=False,
        ),
    )
    not_ready = WannierResult(
        wannier_ok=False,
        ready_for_dmft=False,
        status="failed",
        dmft_gate_notes="not ready for DMFT: missing .chk artifact",
    )
    result = run_dmft_workflow(
        dft,
        "/tmp/dmft-refuse",
        wannier=not_ready,
        formula="NdNiO2",
        material_family="nickelate",
        seed="refuse",
    )
    assert result.status == "refused"
    assert result.failure_class == "wannier_gate"
    assert result.converged is False
    assert "refused" in result.gate_notes


def test_gate_refusal_non_mock_without_wannier_object() -> None:
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(enabled=True, solver="cthyb"),
    )
    result = run_dmft_workflow(dft, "/tmp/dmft-none", wannier=None, seed="none")
    assert result.status == "refused"
    assert result.failure_class == "wannier_gate"


def test_allow_without_wannier_gate_escape() -> None:
    allowed, notes, bypass = evaluate_wannier_gate(
        None,
        DMFTConfig(solver="solid_dmft", allow_without_wannier_gate=True),
        solver="solid_dmft",
    )
    assert allowed is True
    assert bypass is True
    assert "allow_without_wannier_gate" in notes


def test_mock_honours_gate_when_bypass_off() -> None:
    r = mock_dmft_result(
        seed="gated-mock",
        dmft=DMFTConfig(
            enabled=True,
            solver="mock",
            mock_bypass_gate=False,
            allow_without_wannier_gate=False,
        ),
        wannier=WannierResult(ready_for_dmft=False, wannier_ok=False, status="failed"),
    )
    assert r.status == "refused"
    assert r.failure_class == "wannier_gate"


def test_real_path_skips_without_triqs(tmp_path: Path) -> None:
    if triqs_available():
        pytest.skip("TRIQS/solid_dmft is installed in this environment")
    cfg = DMFTConfig(enabled=True, solver="solid_dmft")
    result = run_solid_dmft(
        cfg=cfg,
        wannier=_ready_wannier(),
        work_dir=tmp_path / "dmft",
        quality_tag="screening",
        formula="NdNiO2",
    )
    assert result.failure_class == "solver_missing"
    assert result.status == "skipped"
    sidecar = tmp_path / "dmft" / "siscforge_dmft_config.json"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text())
    assert payload["n_loops"] == cfg.n_loops
    assert "n_iter_dmft" in payload.get("n_loops_note", "")
    assert payload.get("auto_launch") is True
    assert "p3_x_real_launch" in payload.get("extension_hooks", {})
    assert (tmp_path / "dmft" / "dmft_config.toml").is_file()
    assert (tmp_path / "dmft" / "run_solid_dmft.sh").is_file()
    assert (tmp_path / "dmft" / "LAUNCH.md").is_file()


def test_run_workflow_real_solver_skips_without_stack(tmp_path: Path) -> None:
    if triqs_available():
        pytest.skip("TRIQS/solid_dmft is installed in this environment")
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(enabled=True, solver="solid_dmft"),
    )
    result = run_dmft_workflow(
        dft,
        tmp_path / "dmft",
        wannier=_ready_wannier(),
        seed="real-skip",
    )
    assert result.failure_class == "solver_missing"
    assert result.status == "skipped"


@pytest.mark.skipif(not triqs_available(), reason="TRIQS / solid_dmft not installed")
def test_real_triqs_observables_drop_in(tmp_path: Path) -> None:
    """When the stack is present, drop-in observables JSON is parsed."""
    wd = tmp_path / "dmft"
    wd.mkdir()
    (wd / "observables.json").write_text(
        '{"occupancy": {"Ni_d": 8.7}, "Z": 0.4, "converged": true}',
        encoding="utf-8",
    )
    cfg = DMFTConfig(enabled=True, solver="solid_dmft")
    result = run_solid_dmft(
        cfg=cfg,
        wannier=_ready_wannier(),
        work_dir=wd,
        quality_tag="screening",
        formula="NdNiO2",
    )
    assert result.converged is True
    assert result.occupancy_summary.get("Ni_d") == 8.7
    assert result.mass_enhancement == pytest.approx(2.5)


def test_parse_dmft_observables_json() -> None:
    metrics = parse_dmft_observables({"occupancy": {"Ni_d": 8.9}, "Z": 0.5, "converged": True})
    assert metrics["filling"] == pytest.approx(8.9)
    assert metrics["mass_enhancement"] == pytest.approx(2.0)
    assert metrics["converged"] is True
    assert metrics["leading_pairing_eigenvalue"] is None


def test_parse_dmft_observables_pairing_home() -> None:
    metrics = parse_dmft_observables(
        {
            "filling": 8.8,
            "mass_enhancement": 3.0,
            "converged": True,
            "leading_pairing_eigenvalue": 0.42,
            "pairing_symmetry": "d_x2-y2",
        }
    )
    assert metrics["leading_pairing_eigenvalue"] == pytest.approx(0.42)
    assert metrics["pairing_symmetry"] == "d_x2-y2"


def test_classify_dmft_failure() -> None:
    assert classify_dmft_failure("not ready for DMFT: ready_for_dmft=False") == ("wannier_gate")
    assert classify_dmft_failure("No module named 'triqs'") == "import_error"
    assert classify_dmft_failure("did not converge after 10 loops") == "not_converged"
    assert "wannier_gate" in DMFT_FAILURE_CLASSES
    assert "phonon" not in DMFT_FAILURE_CLASSES


def test_mock_dmft_end_to_end_store_export(tmp_path: Path) -> None:
    dft = DFTConfig(
        do_dftu=True,
        do_wannier=True,
        do_dmft=True,
        wannier=WannierConfig(enabled=True, num_wann=8),
        dmft=DMFTConfig(enabled=True, solver="mock", U_eV=5.0),
    )
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="e2e-ndnio2-dmft",
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    ev = get("mock").run(cand, dft=dft)
    ranked = rank_evaluations([ev])
    store = EvaluationStore(tmp_path / "camp")
    store.save_evaluations(ranked, ranked=True)
    loaded = store.load_evaluations(ranked=True)
    assert loaded[0].dmft is not None
    assert loaded[0].dmft.solver == "mock"
    assert loaded[0].wannier is not None
    assert loaded[0].dftu is not None

    csv_path = write_evaluations_csv(loaded, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "dmft_status",
        "dmft_solver",
        "dmft_converged",
        "dmft_U_eV",
        "dmft_J_eV",
        "dmft_filling",
        "dmft_mass_enhancement",
        "dmft_leading_pairing_eigenvalue",
        "dmft_pairing_symmetry",
        "dmft_summary",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    body = csv_path.read_text()
    assert "mock" in body

    json_path = write_evaluations_json(loaded, tmp_path / "out.json")
    jtext = json_path.read_text()
    assert "mass_enhancement" in jtext
    assert "leading_pairing_eigenvalue" in jtext

    cards = write_synthesis_cards(loaded, tmp_path / "cards.md", campaign_name="p33")
    text = cards.read_text()
    assert "DMFT" in text
    assert "P3.3" in text
    assert "P3.4" in text
    assert "performance_score" in text


def test_recipe_info_lists_dmft() -> None:
    info = recipe_info()
    assert "DMFTResult" in info["models"]
    assert any("dmft" in s.lower() for s in info["steps"])
    assert "scaffold" in info["extension_points"]["p3_3"]
    assert "residual" in info["extension_points"]["p3_3"]
    assert "parser" in info["extension_points"]["p3_3"]


def test_ndnio2_dmft_example_yaml_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "examples" / "ndnio2_dmft_mock.yaml"
    assert path.is_file()
    cfg = CampaignConfig.from_yaml(path)
    assert cfg.dft.do_dmft is True
    assert cfg.dft.dmft.enabled is True
    assert cfg.dft.dmft.solver == "mock"
    assert dmft_is_enabled(cfg.dft) is True
    assert cfg.dft.do_epw is False


def test_docs_honest_about_scaffold_and_mock_physics() -> None:
    """Language, SETUP Tier D, mock labelling, controlled launcher."""
    root = Path(__file__).resolve().parents[1]
    phase = (root / "docs" / "phase3-p33-dmft.md").read_text()
    assert "observables.json" in phase
    assert "observables_imp" in phase
    assert "conv_imp" in phase or "convergence_obs" in phase
    assert "last-row" in phase.lower() or "last row" in phase.lower()
    assert "DMFT_results" in phase or "native" in phase.lower()
    assert "residual" in phase.lower()
    assert "illustrative" in phase.lower()
    assert "Will this run or refuse" in phase
    assert "auto_launch" in phase
    assert "dmft_config.toml" in phase
    setup = (root / "docs" / "SETUP.md").read_text()
    assert "Tier D" in setup
    assert "never a hard dependency" in setup
    example = (root / "examples" / "ndnio2_dmft_mock.yaml").read_text()
    assert "illustrative" in example.lower()


def test_conventional_campaign_unchanged_when_dmft_off() -> None:
    """Default nitride-style config must not grow a DMFT result on mock."""
    cfg = CampaignConfig(name="nbn_default")
    assert cfg.dft.do_dmft is False
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="conv-off",
    )
    ev = get("mock").run(cand, dft=cfg.dft)
    assert ev.dmft is None
    assert ev.wannier is None
    assert ev.dftu is None


def test_sacred_upstream_on_failure(tmp_path: Path) -> None:
    upstream = tmp_path / "wannier"
    upstream.mkdir()
    marker = upstream / "siscforge.chk"
    marker.write_text("keep me\n", encoding="utf-8")
    dft = DFTConfig(
        do_dmft=True,
        dmft=DMFTConfig(
            enabled=True,
            solver="mock",
            mock_force_failure=True,
            mock_bypass_gate=True,
        ),
    )
    result = run_dmft_workflow(
        dft,
        tmp_path / "dmft",
        wannier=_ready_wannier(work_dir=str(upstream), chk_path=str(marker)),
        seed="sacred",
    )
    assert result.status == "failed"
    assert marker.is_file()
    assert marker.read_text() == "keep me\n"


def test_logger_exception_on_dmft_catch() -> None:
    import inspect

    from siscforge.calculators.qe import calculator as calc_mod

    src = inspect.getsource(calc_mod.QECalculator.run)
    assert "_LOG.exception" in src
    assert "DMFT step failed" in src
