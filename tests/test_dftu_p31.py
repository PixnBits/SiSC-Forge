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


def test_hubbard_input_injection(tmp_path: Path) -> None:
    s = build_binary_nitride("Nb")
    # Force Ni-like Hubbard on Nb for input test (correlated metal present)
    dft = DFTConfig(
        pseudo_dir=str(tmp_path),
        pseudopotentials={"Nb": "Nb.upf", "N": "N.upf"},
        do_dftu=True,
        dftu=DFTUConfig(
            enabled=True,
            U_eV=4.0,
            hubbard_species=["Nb"],
            hubbard_projectors="ortho-atomic",
            nspin=2,
        ),
    )
    (tmp_path / "Nb.upf").touch()
    (tmp_path / "N.upf").touch()
    extras = hubbard_system_extras(s, dft.dftu)
    assert extras["lda_plus_u"] is True
    assert extras["nspin"] == 2
    assert any(k.startswith("Hubbard_U") for k in extras)

    pw = build_pw_input(s, dft, calculation="scf", extra_system=extras)
    text = append_hubbard_card(str(pw), s, dft.dftu)
    assert "HUBBARD" in text
    assert "Nb" in text
    lower = text.lower()
    assert "lda_plus_u" in lower or "hubbard_u" in lower


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
