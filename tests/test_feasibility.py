"""Tests for the Silicon Feasibility scorer (P2.1–P2.3)."""

from __future__ import annotations

import pytest

from siscforge.models.candidate import StructureCandidate
from siscforge.silicon.critical_thickness import (
    estimate_critical_thickness,
)
from siscforge.silicon.feasibility import (
    SCORER_VERSION,
    evaluate_mismatch_options,
    normalize_component_weights,
    rank_by_si_feasibility,
    score_si_feasibility,
    scorer_debug_info,
)


def _nbn_candidate(
    *,
    epitaxy_orientation: str | None = "auto",
    use_buffers: bool = True,
    formula: str = "NbN",
) -> StructureCandidate:
    meta: dict = {}
    if epitaxy_orientation is not None:
        meta["epitaxy_orientation"] = epitaxy_orientation
    if not use_buffers:
        meta["use_buffers"] = False
    return StructureCandidate(
        formula=formula,
        composition={"Nb": 0.5, "N": 0.5},
        material_family="tm_nitride",
        substrate="Si(001)",
        lattice_abc=(4.392, 4.392, 4.392),
        metadata=meta,
    )


def _bsi_candidate() -> StructureCandidate:
    return StructureCandidate(
        formula="B:Si",
        composition={"Si": 0.99, "B": 0.01},
        material_family="b_doped_si",
        substrate="Si(001)",
        lattice_abc=(5.431, 5.431, 5.431),
    )


def test_scorer_version() -> None:
    assert SCORER_VERSION == "0.5"
    assert score_si_feasibility(_nbn_candidate()).version == "0.5"


def test_normalize_weights_defaults() -> None:
    w = normalize_component_weights()
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert set(w) == {
        "lattice_mismatch",
        "thermal_budget",
        "chemical_compatibility",
        "buffer_availability",
        "process_maturity",
    }


def test_normalize_weights_override() -> None:
    # Partial override merges with defaults then renormalizes.
    w = normalize_component_weights({"lattice_mismatch": 1.0, "thermal_budget": 0.0})
    assert w["thermal_budget"] == 0.0
    assert w["lattice_mismatch"] > 0.5  # raised relative to default 0.35
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_nbn_score_basic() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert 0 <= score.total <= 100
    assert score.recommended_thickness_nm is not None
    assert isinstance(score.recommended_thickness_nm, (int, float, tuple))
    assert score.critical_thickness_method in {
        "Matthews-Blakeslee",
        "People-Bean",
        "heuristic fallback",
    }
    assert score.membrane_transfer_note


def test_45deg_path_present() -> None:
    opts = evaluate_mismatch_options(_nbn_candidate(epitaxy_orientation="45deg"))
    assert any("45deg" in o["path"] for o in opts)


def test_buffer_options_include_film_buffer_mismatch() -> None:
    opts = evaluate_mismatch_options(_nbn_candidate(use_buffers=True))
    bufs = [o for o in opts if o.get("buffer") and o["buffer"] != "direct_Si"]
    assert bufs
    for o in bufs:
        if not o.get("is_multilayer"):
            assert "mismatch_film_buffer_pct" in o


def test_weights_affect_ranking() -> None:
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()
    maturity_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 1.0,
    }
    ranked = rank_by_si_feasibility([nbn, bsi], weights=maturity_heavy)
    assert ranked[0][0].formula == "NbN"
    score = ranked[0][1]
    assert score.version == "0.5"
    assert any("/" in b for b in score.recommended_buffers)
    assert score.chemical_flags is not None
    assert score.process_temp_ceiling_c is not None
    assert score.critical_thickness_method
    assert score.recommended_thickness_nm is not None

    # Also exercise the P2.1 config= path (YAML / CampaignConfig wiring).
    from siscforge.models.config import SiFeasibilityConfig, SiFeasibilityWeights

    cfg = SiFeasibilityConfig(
        weights=SiFeasibilityWeights(
            lattice_mismatch=0.0,
            thermal_budget=0.0,
            chemical_compatibility=0.0,
            buffer_availability=0.0,
            process_maturity=1.0,
        )
    )
    ranked_cfg = rank_by_si_feasibility([nbn, bsi], config=cfg)
    assert ranked_cfg[0][0].formula == "NbN"
    assert ranked_cfg[0][1].weights["process_maturity"] == pytest.approx(1.0)


def test_solver_returns_none_when_iteration_exhausted() -> None:
    """Extreme misfit with no fixed point must not invent a spurious h_c."""
    from siscforge.silicon.critical_thickness import _solve_implicit_hc

    assert _solve_implicit_hc(prefactor_nm=1e-12, b_nm=0.3, max_iter=5) is None


def test_critical_thickness_uses_film_buffer_mismatch_not_worst_interface() -> None:
    """Single-buffer paths: film–buffer mismatch drives h_c, not buffer–Si."""
    cand = _nbn_candidate(epitaxy_orientation="cube_on_cube", use_buffers=True)
    opts = evaluate_mismatch_options(cand)
    tin = next(o for o in opts if o.get("buffer") == "TiN" and not o.get("is_multilayer"))
    assert "mismatch_film_buffer_pct" in tin
    film_buf = abs(float(tin["mismatch_film_buffer_pct"]))
    worst = abs(float(tin["mismatch_pct"]))
    assert worst >= film_buf - 1e-9

    from siscforge.silicon.critical_thickness import estimate_critical_thickness

    ct_film = estimate_critical_thickness(tin["mismatch_film_buffer_pct"], formula="NbN")
    ct_worst = estimate_critical_thickness(tin["mismatch_pct"], formula="NbN")
    if ct_film.hc_primary_nm is not None and ct_worst.hc_primary_nm is not None:
        assert ct_film.hc_primary_nm >= ct_worst.hc_primary_nm - 1e-9

    # Verify the *scorer* actually passes the film–buffer mismatch into CT inputs
    # (not merely that the standalone estimator behaves correctly).
    # Force TiN path by ranking options; the best path for NbN cube-on-cube is
    # often a buffer. Check that whatever path is chosen, if it exposes
    # mismatch_film_buffer_pct then critical_thickness_inputs uses it.
    score = score_si_feasibility(cand)
    inputs = score.critical_thickness_inputs or {}
    if "mismatch_pct" in inputs and tin.get("mismatch_film_buffer_pct") is not None:
        # When the scorer selected a single-buffer path, inputs must reflect
        # the film–template interface, not the worst (often buffer–Si) value.
        best = opts[0]
        if best.get("buffer") == "TiN" and not best.get("is_multilayer"):
            assert abs(float(inputs["mismatch_pct"]) - film_buf) < 1e-6


def test_non_candidate_still_exports_membrane_note() -> None:
    """Dedicated membrane field is set even when candidate flag is false."""
    score = score_si_feasibility(_bsi_candidate())
    if not score.membrane_transfer_candidate:
        assert score.membrane_transfer_note
        assert "membrane" in score.membrane_transfer_note.lower()


def test_membrane_flag_does_not_pollute_chemical_flags() -> None:
    """membrane_transfer must not appear in chemical_flags (P2.2 contract)."""
    score = score_si_feasibility(_nbn_candidate())
    assert "membrane_transfer" not in (score.chemical_flags or [])


def test_low_vs_high_mismatch_thickness_band() -> None:
    low = estimate_critical_thickness(0.5, formula="NbN")
    high = estimate_critical_thickness(12.0, formula="NbN")
    assert low.recommended_thickness_nm > high.recommended_thickness_nm
    assert low.method in {"Matthews-Blakeslee", "People-Bean"}


def test_missing_lattice_fallback() -> None:
    cand = StructureCandidate(
        formula="UnknownZed",
        composition={"Z": 1.0},
        material_family="other",
        substrate="Si(001)",
    )
    score = score_si_feasibility(cand)
    assert score.critical_thickness_method == "heuristic fallback"
    assert score.version == "0.5"
    ct = estimate_critical_thickness(None, formula="UnknownZed", material_family="other")
    assert ct.method == "heuristic fallback"


def test_membrane_candidate_on_high_direct_mismatch() -> None:
    """CrN on Si has ~17% direct mismatch → must be a membrane candidate."""
    cand = StructureCandidate(
        formula="CrN",
        composition={"Cr": 0.5, "N": 0.5},
        material_family="tm_nitride",
        substrate="Si(001)",
        lattice_abc=(4.14, 4.14, 4.14),
        metadata={"use_buffers": False, "epitaxy_orientation": "cube_on_cube"},
    )
    score = score_si_feasibility(cand)
    assert score.membrane_transfer_note
    # Unconditional: direct |mismatch| far exceeds the 8% threshold.
    assert score.membrane_transfer_candidate is True
    assert "membrane" in score.membrane_transfer_note.lower()
    assert "membrane_transfer" not in (score.chemical_flags or [])


def test_debug_info_has_options() -> None:
    info = scorer_debug_info(_nbn_candidate())
    assert "options" in info
    assert info["scorer_version"] == "0.5"


def test_recommended_thickness_is_scalar_float() -> None:
    """P2.3 assigns a single recommended thickness (not a legacy band tuple)."""
    score = score_si_feasibility(_nbn_candidate())
    assert score.recommended_thickness_nm is not None
    assert isinstance(score.recommended_thickness_nm, (int, float))


def test_use_buffers_false_excludes_stacks_and_preserves_p22_metadata() -> None:
    """P2.2 contract: use_buffers=False yields only direct paths; metadata still present."""
    cand = _nbn_candidate(use_buffers=False, epitaxy_orientation="cube_on_cube")
    opts = evaluate_mismatch_options(cand)
    assert opts
    assert all(str(o.get("path", "")).startswith("direct/") for o in opts)
    assert not any(o.get("is_multilayer") for o in opts)

    score = score_si_feasibility(cand)
    # No multi-layer stack names in recommendations when buffers disabled.
    assert not any("/" in b for b in score.recommended_buffers)
    # P2.2 fields remain populated (even on direct path).
    assert score.chemical_flags is not None
    assert score.process_temp_ceiling_c is not None
    assert score.version == "0.5"
    assert score.critical_thickness_method
    assert score.membrane_transfer_note


def test_unsupported_material_does_not_claim_matthews_blakeslee() -> None:
    """Materials without elastic library entry must use heuristic fallback, not invented MB."""
    ct = estimate_critical_thickness(
        4.0, formula="UnknownZedX", material_family="other", film_a_ang=4.5
    )
    assert ct.method == "heuristic fallback"
    assert ct.hc_primary_nm is None


def test_non_si_substrate_zero_strain_uses_conservative_lattice() -> None:
    """SrTiO3 + in_plane_strain=0 (ndnio2_dmft_mock) must not get lattice_mismatch=100.

    parse_substrate rejects non-Si labels so evaluate_mismatch_options is
    empty. The |in_plane_strain| fallback would treat strain=0 as perfect match;
    unsupported substrates must take the conservative lattice_data_missing
    path instead.
    """
    cand = StructureCandidate(
        formula="NdNiO2",
        composition={"Nd": 0.25, "Ni": 0.25, "O": 0.5},
        material_family="nickelate",
        substrate="SrTiO3",
        in_plane_strain=0.0,
        lattice_abc=(3.92, 3.92, 3.31),
        metadata={"prototype": "infinite_layer"},
    )
    assert evaluate_mismatch_options(cand) == []
    score = score_si_feasibility(cand)
    assert score.components.lattice_mismatch != 100.0
    assert score.components.lattice_mismatch == pytest.approx(28.650479686019008)
    assert score.lattice_mismatch_pct == pytest.approx(5.0)
    assert score.total != 66.65
    notes = score.notes.lower()
    assert "unsupported" in notes or "non-si" in notes
    assert "missing-data" in notes or "missing" in notes
    assert "|in_plane_strain|" in score.notes or "in_plane_strain" in notes

    # Recognised Si faces still use the |in_plane_strain| fallback when options are empty.
    si_no_lattice = StructureCandidate(
        formula="UnknownZed",
        composition={"Z": 1.0},
        material_family="other",
        substrate="Si(001)",
        in_plane_strain=0.0,
    )
    si_score = score_si_feasibility(si_no_lattice)
    assert si_score.components.lattice_mismatch == pytest.approx(100.0)
    assert "mismatch from |in_plane_strain|" in si_score.notes
