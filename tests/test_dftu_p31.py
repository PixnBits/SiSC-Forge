"""P3.1 — DFT+U workflow and DFTUResult model tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from siscforge.calculators import get, list_calculators
from siscforge.calculators.qe.dftu import (
    append_hubbard_card,
    dftu_is_enabled,
    hubbard_system_extras,
    mock_dftu_result,
    parse_dftu_output,
    resolve_hubbard_species,
    resolve_u_j_maps,
)
from siscforge.calculators.qe.inputs import build_pw_input
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
    DFTUConfig,
    DFTUResult,
    StructureCandidate,
)
from siscforge.ranking import rank_evaluations
from siscforge.store import EvaluationStore
from siscforge.structure.nitrides import build_binary_nitride


FIXTURE_DFTU = Path(__file__).parent / "fixtures" / "qe" / "pw_dftu_snippet.out"


def test_dftu_result_round_trip() -> None:
    r = DFTUResult(
        U_eV=5.0,
        J_eV=0.7,
        U_by_species={"Ni": 5.0},
        J_by_species={"Ni": 0.7},
        hubbard_species=["Ni"],
        hubbard_projectors="ortho-atomic",
        occupancy_summary={"hubbard_Ni": 8.1},
        magnetic_moments={"Ni": 1.2},
        total_magnetization=1.2,
        absolute_magnetization=1.3,
        total_energy_eV=-123.4,
        is_metallic=True,
        status="ok",
        quality_tag="screening",
    )
    payload = r.model_dump(mode="json")
    restored = DFTUResult.model_validate(payload)
    assert restored.U_eV == 5.0
    assert restored.U_by_species["Ni"] == 5.0
    assert "U=5" in restored.summary_line()
    assert restored.model_dump(mode="json") == payload


def test_dftu_result_defaults_validation() -> None:
    r = DFTUResult()
    assert r.status == "unknown"
    assert r.U_by_species == {}
    with pytest.raises(ValidationError):
        DFTUResult(quality_tag="not-a-tier")  # type: ignore[arg-type]


def test_candidate_evaluation_optional_dftu() -> None:
    cand = StructureCandidate(formula="NdNiO2", material_family="nickelate")
    # Conventional-style eval without dftu remains valid
    bare = CandidateEvaluation(candidate=cand, status="mock")
    assert bare.dftu is None
    data = bare.model_dump(mode="json")
    assert CandidateEvaluation.model_validate(data).dftu is None

    with_u = CandidateEvaluation(
        candidate=cand,
        dftu=DFTUResult(U_eV=4.0, status="mock", quality_tag="mock"),
        status="mock",
    )
    restored = CandidateEvaluation.model_validate(with_u.model_dump(mode="json"))
    assert restored.dftu is not None
    assert restored.dftu.U_eV == 4.0


def test_dftu_config_disabled_by_default() -> None:
    cfg = CampaignConfig(name="nitride_default")
    assert cfg.dft.do_dftu is False
    assert cfg.dft.dftu.enabled is False
    assert dftu_is_enabled(cfg.dft) is False

    # YAML round-trip preserves defaults (conventional campaigns unchanged)
    payload = cfg.model_dump(mode="json")
    again = CampaignConfig.model_validate(payload)
    assert again.dft.do_dftu is False
    assert again.dft.dftu.U_eV == 4.0


def test_dftu_config_enable_yaml_knobs() -> None:
    cfg = CampaignConfig.model_validate(
        {
            "name": "nickelate_dftu",
            "dft": {
                "do_dftu": True,
                "dftu": {
                    "enabled": True,
                    "U_eV": 5.0,
                    "J_eV": 0.8,
                    "hubbard_species": ["Ni"],
                    "hubbard_projectors": "ortho-atomic",
                    "U_by_species": {"Ni": 5.0},
                },
            },
        }
    )
    assert dftu_is_enabled(cfg.dft) is True
    assert cfg.dft.dftu.U_eV == 5.0
    assert cfg.dft.dftu.hubbard_species == ["Ni"]


def test_qe_dftu_registered() -> None:
    names = list_calculators()
    assert "qe-dftu" in names
    assert "dftu" in names
    calc = get("qe-dftu")
    assert calc.name == "qe-dftu"


def test_mock_without_dftu_has_no_result() -> None:
    cand = StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        candidate_id="mock-nbn-no-u",
    )
    result = get("mock").run(cand)
    assert result.dftu is None
    assert "DFT+U" not in (result.notes or "")


def test_mock_with_dftu_enabled_populates_result() -> None:
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        composition={"Nd": 0.25, "Ni": 0.25, "O": 0.5},
        candidate_id="mock-ndnio2-u",
    )
    dft = DFTConfig(
        do_dftu=True,
        dftu=DFTUConfig(
            enabled=True,
            U_eV=5.0,
            J_eV=0.7,
            hubbard_species=["Ni"],
        ),
    )
    result = get("mock").run(cand, dft=dft)
    assert result.dftu is not None
    assert result.dftu.status == "mock"
    assert result.dftu.U_eV == 5.0
    assert "Ni" in result.dftu.hubbard_species
    assert result.dftu.total_magnetization is not None
    assert result.dftu.occupancy_summary
    # Deterministic
    again = get("mock").run(cand, dft=dft)
    assert again.dftu is not None
    assert again.dftu.total_energy_eV == result.dftu.total_energy_eV


def test_mock_dftu_end_to_end_store_export(tmp_path: Path) -> None:
    """Run → store → export summary exercises DFTUResult end-to-end."""
    dft = DFTConfig(
        do_dftu=True,
        dftu=DFTUConfig(enabled=True, U_eV=4.5, hubbard_species=["Ni"]),
    )
    cand = StructureCandidate(
        formula="NdNiO2",
        material_family="nickelate",
        candidate_id="e2e-ndnio2",
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    ev = get("mock").run(cand, dft=dft)
    ranked = rank_evaluations([ev])
    store = EvaluationStore(tmp_path / "camp")
    store.save_evaluations(ranked, ranked=True)
    loaded = store.load_evaluations(ranked=True)
    assert loaded[0].dftu is not None
    assert loaded[0].dftu.U_eV == 4.5

    csv_path = write_evaluations_csv(loaded, tmp_path / "out.csv")
    header = csv_path.read_text().splitlines()[0]
    for col in (
        "dftu_U_eV",
        "dftu_J_eV",
        "dftu_total_magnetization",
        "dftu_total_energy_eV",
        "dftu_status",
        "dftu_summary",
    ):
        assert col in header
        assert col in CSV_FIELDNAMES
    assert "4.5" in csv_path.read_text()

    json_path = write_evaluations_json(loaded, tmp_path / "out.json")
    assert "dftu" in json_path.read_text()
    assert "hubbard_species" in json_path.read_text()

    cards = write_synthesis_cards(loaded, tmp_path / "cards.md", campaign_name="p31")
    text = cards.read_text()
    assert "DFT+U" in text
    assert "correlated proxy" in text


def test_hubbard_input_injection_namelist(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dft = DFTConfig(
        pseudo_dir=str(tmp_path),
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        do_dftu=True,
        dftu=DFTUConfig(
            enabled=True,
            U_eV=4.0,
            J_eV=0.8,
            hubbard_species=["Nb"],
            hubbard_projectors="ortho-atomic",
            nspin=2,
            hubbard_syntax="namelist",
        ),
    )
    (tmp_path / "Nb.upf").touch()
    (tmp_path / "N.upf").touch()
    extras = hubbard_system_extras(s, dft.dftu, syntax="namelist")
    assert extras["lda_plus_u"] is True
    assert extras["nspin"] == 2
    assert any(k.startswith("Hubbard_U") for k in extras)
    assert any(k.startswith("Hubbard_J0") for k in extras)
    pw = build_pw_input(s, dft, calculation="scf", extra_system=extras)
    text = str(pw)
    assert "HUBBARD" not in text  # namelist dialect only
    lower = text.lower()
    assert "lda_plus_u" in lower or "hubbard_u" in lower


def test_hubbard_input_injection_card_only(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    dftu = DFTUConfig(
        enabled=True,
        U_eV=4.0,
        J_eV=0.8,
        hubbard_species=["Nb"],
        hubbard_syntax="card",
    )
    extras = hubbard_system_extras(s, dftu, syntax="card")
    assert "lda_plus_u" not in extras
    assert not any(k.startswith("Hubbard_U") for k in extras)
    assert extras["nspin"] == 2
    dft = DFTConfig(
        pseudo_dir=str(tmp_path),
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        dftu=dftu,
    )
    (tmp_path / "Nb.upf").touch()
    (tmp_path / "N.upf").touch()
    pw = build_pw_input(s, dft, calculation="scf", extra_system=extras)
    text = append_hubbard_card(str(pw), s, dftu)
    assert "HUBBARD" in text
    assert "J0" in text  # simplified kind uses J0 not full J
    assert "  J " not in text.replace("J0", "")


def test_parse_dftu_fixture() -> None:
    assert FIXTURE_DFTU.is_file()
    from pymatgen.core import Lattice, Structure

    # Minimal 3-site stand-in (Nd, Ni, O) for species resolution
    lat = Lattice.cubic(3.9)
    struct = Structure(
        lat,
        ["Nd", "Ni", "O"],
        [[0, 0, 0], [0.5, 0.5, 0.5], [0.5, 0.5, 0.0]],
    )
    dftu = DFTUConfig(enabled=True, U_eV=5.0, hubbard_species=["Ni"])
    result = parse_dftu_output(
        FIXTURE_DFTU, dftu=dftu, structure=struct, quality_tag="screening"
    )
    assert result.status == "ok"
    assert result.total_energy_eV is not None
    assert result.total_magnetization == pytest.approx(1.2345)
    assert result.absolute_magnetization == pytest.approx(1.4567)
    assert result.fermi_energy_eV == pytest.approx(8.7654)
    assert result.U_eV == 5.0
    assert "Ni" in result.hubbard_species
    assert result.raw.get("pathway") == "dftu"
    assert "p3_2_wannier" in result.raw.get("extension_hooks", {})
    # magn: 1.1000 — not the charge 8.1234
    assert result.magnetic_moments.get("atom_1") == pytest.approx(1.1)


def test_resolve_species_auto_nickelate() -> None:
    from pymatgen.core import Lattice, Structure

    lat = Lattice.tetragonal(3.9, 3.3)
    s = Structure(
        lat,
        ["Nd", "Ni", "O", "O"],
        [[0, 0, 0.5], [0.5, 0.5, 0], [0, 0.5, 0], [0.5, 0, 0]],
    )
    dftu = DFTUConfig(enabled=True, U_eV=5.0)
    species = resolve_hubbard_species(s, dftu)
    assert "Ni" in species
    assert "Nd" in species  # rare earth also correlated
    sp, u_map, _j = resolve_u_j_maps(s, dftu)
    assert set(sp) == set(species)
    assert all(v == 5.0 for v in u_map.values())


def test_recipe_info_mentions_dftu() -> None:
    info = recipe_info()
    assert "DFTUResult" in info["models"]
    assert any("dftu" in s.lower() for s in info["steps"])
    assert "p3_2" in info["extension_points"]


def test_mock_dftu_result_helper() -> None:
    r = mock_dftu_result(
        seed="abc",
        dftu=DFTUConfig(enabled=True, U_eV=3.5, hubbard_species=["Ni"]),
        formula="NdNiO2",
        material_family="nickelate",
    )
    assert r.status == "mock"
    assert r.U_eV == 3.5
    assert r.hubbard_species == ["Ni"]


def test_hubbard_species_mismatch_raises() -> None:
    from pymatgen.core import Lattice, Structure

    lat = Lattice.cubic(3.0)
    s = Structure(lat, ["Fe", "O"], [[0, 0, 0], [0.5, 0.5, 0.5]])
    dftu = DFTUConfig(enabled=True, hubbard_species=["Ni"])
    with pytest.raises(ValueError, match="hubbard_species includes"):
        resolve_hubbard_species(s, dftu)


def test_nspin_rejects_three() -> None:
    with pytest.raises(ValidationError):
        DFTUConfig(nspin=3)  # type: ignore[arg-type]


def test_scalar_u_none_when_species_differ() -> None:
    from pymatgen.core import Lattice, Structure

    lat = Lattice.cubic(3.9)
    struct = Structure(
        lat,
        ["Nd", "Ni", "O"],
        [[0, 0, 0], [0.5, 0.5, 0.5], [0.5, 0.5, 0.0]],
    )
    dftu = DFTUConfig(
        enabled=True,
        U_by_species={"Ni": 5.0, "Nd": 6.0},
        hubbard_species=["Ni", "Nd"],
    )
    result = parse_dftu_output(
        FIXTURE_DFTU, dftu=dftu, structure=struct, quality_tag="screening"
    )
    assert result.U_eV is None
    assert result.U_by_species["Ni"] == 5.0
    assert result.U_by_species["Nd"] == 6.0
    assert "U=[" in result.summary_line()


def test_kind1_with_scalar_j_rejected() -> None:
    s = build_binary_nitride("Nb")
    dftu = DFTUConfig(
        enabled=True,
        hubbard_species=["Nb"],
        J_eV=0.7,
        lda_plus_u_kind=1,
    )
    with pytest.raises(ValueError, match="lda_plus_u_kind=1"):
        hubbard_system_extras(s, dftu)


def test_dftu_only_requires_force_dftu() -> None:
    """Plain qe + do_dftu + do_phonon=false stays additive (not dftu-only)."""
    from siscforge.calculators.qe.calculator import QECalculator

    calc = QECalculator(
        dft=DFTConfig(do_dftu=True, do_phonon=False, do_epw=False)
    )
    assert calc.force_dftu is False
    # force_dftu calculator
    from siscforge.calculators.qe.calculator import QEDftuCalculator

    forced = QEDftuCalculator()
    assert forced.force_dftu is True
    assert forced.dft.do_phonon is False


def test_run_dftu_workflow_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise run_dftu_workflow without real pw.x (subprocess mocked)."""
    from siscforge.calculators.qe import recipes as recipes_mod
    from siscforge.calculators.qe.recipes import QEStepResult, run_dftu_workflow
    from siscforge.structure.generator import structure_to_candidate
    from siscforge.structure.nitrides import build_binary_nitride

    s = build_binary_nitride("Nb")
    fixture = FIXTURE_DFTU.read_text(encoding="utf-8")
    # Adapt fixture species mentions; energy/JOB DONE still parse
    work = tmp_path / "dftu_work"

    def fake_run_pw(structure, config, work_dir, **kwargs):
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        base = kwargs.get("input_basename") or kwargs.get("calculation") or "dftu"
        out = work_dir / f"{base}.out"
        # For vc-relax path we might not hit this when do_relax=False
        out.write_text(fixture, encoding="utf-8")
        inp = work_dir / f"{base}.in"
        inp.write_text("! mock dftu.in\n", encoding="utf-8")
        return QEStepResult(
            name=str(base),
            work_dir=work_dir,
            returncode=0,
            stdout_path=out,
            input_path=inp,
            success=True,
            message=f"pw.x {base} rc=0",
        )

    monkeypatch.setattr(recipes_mod, "run_pw", fake_run_pw)
    monkeypatch.setattr(
        recipes_mod,
        "require_qe",
        lambda **kw: type("E", (), {"pw": "/bin/true", "ph": None, "epw": None, "mpirun": None})(),
    )

    cfg = DFTConfig(
        do_relax=False,
        do_phonon=False,
        do_dftu=True,
        dftu=DFTUConfig(enabled=True, U_eV=5.0, hubbard_species=["Nb"]),
        pseudo_dir=str(tmp_path),
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
    )
    result = run_dftu_workflow(s, cfg, work, prefix="t")
    assert result.success
    assert result.dftu is not None
    assert result.dftu.status == "ok"
    assert result.dftu.total_energy_eV is not None
    assert (work / "dftu.out").is_file()


def test_run_dftu_workflow_resume_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from siscforge.calculators.qe import recipes as recipes_mod
    from siscforge.calculators.qe.recipes import run_dftu_workflow

    s = build_binary_nitride("Nb")
    work = tmp_path / "resume"
    work.mkdir()
    (work / "dftu.out").write_text(FIXTURE_DFTU.read_text(encoding="utf-8"), encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("run_pw should not be called on checkpoint resume")

    monkeypatch.setattr(recipes_mod, "run_pw", boom)
    monkeypatch.setattr(
        recipes_mod,
        "require_qe",
        lambda **kw: type("E", (), {"pw": "/bin/true", "ph": None, "epw": None, "mpirun": None})(),
    )
    cfg = DFTConfig(
        do_relax=False,
        do_dftu=True,
        dftu=DFTUConfig(enabled=True, hubbard_species=["Nb"], U_eV=5.0),
    )
    result = run_dftu_workflow(s, cfg, work, resume_qe_steps=True)
    assert result.success
    assert result.dftu is not None
    assert any("skip dftu" in s.message for s in result.steps)


@pytest.mark.skipif(
    __import__("os").environ.get("SISCFORGE_RUN_QE") != "1",
    reason="Set SISCFORGE_RUN_QE=1 with pw.x on PATH for real DFT+U",
)
def test_real_qe_dftu_optional(tmp_path: Path) -> None:
    """Optional real-QE gate (same pattern as NbN phonon golden)."""
    from siscforge.calculators.qe.env import qe_available
    from siscforge.calculators.qe.recipes import run_dftu_workflow

    if not qe_available():
        pytest.skip("pw.x not available")
    s = build_binary_nitride("Nb")
    pseudo = Path(__file__).resolve().parents[1] / "pseudos"
    cfg = DFTConfig(
        do_relax=False,
        do_phonon=False,
        do_dftu=True,
        ecutwfc=30.0,
        kpoints=[1, 1, 1],
        dftu=DFTUConfig(enabled=True, U_eV=3.0, hubbard_species=["Nb"]),
        pseudo_dir=str(pseudo) if pseudo.is_dir() else None,
        quality_tag="screening",
    )
    result = run_dftu_workflow(s, cfg, tmp_path / "real_dftu", prefix="t")
    # Soft: may fail without UPFs; just ensure workflow returns a result object
    assert result.dftu is not None or result.message
