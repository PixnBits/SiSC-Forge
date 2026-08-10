"""Tests for the Silicon Feasibility scorer (P2.1–P2.3)."""

from __future__ import annotations

import math

import pytest

from siscforge.models.candidate import StructureCandidate
from siscforge.silicon.buffers import list_buffers_for_family, list_stacks_for_family
from siscforge.silicon.critical_thickness import (
    estimate_critical_thickness,
    membrane_transfer_heuristic,
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
    w = normalize_component_weights({"lattice_mismatch": 1.0, "thermal_budget": 0.0})
    assert w["lattice_mismatch"] == pytest.approx(1.0)
    assert w["thermal_budget"] == 0.0


def test_nbn_score_basic() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert 0 <= score.total <= 100
    assert score.recommended_thickness_nm is not None
    assert score.critical_thickness_method in {
        "Matthews-Blakeslee",
        "People-Bean",
        "heuristic fallback",
    }
    assert score.membrane_transfer_note  # always populated


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


def test_solver_returns_none_when_iteration_exhausted() -> None:
    """Extreme misfit with no fixed point must not invent a spurious h_c."""
    from siscforge.silicon.critical_thickness import _solve_implicit_hc

    # Prefactor so large/small that fixed-point iteration cannot settle in domain
    assert _solve_implicit_hc(prefactor_nm=1e-12, b_nm=0.3, max_iter=5) is None


def test_critical_thickness_uses_film_buffer_mismatch_not_worst_interface() -> None:
    """Single-buffer paths: film–buffer mismatch drives h_c, not buffer–Si."""
    cand = _nbn_candidate(epitaxy_orientation="cube_on_cube", use_buffers=True)
    opts = evaluate_mismatch_options(cand)
    tin = next(o for o in opts if o.get("buffer") == "TiN" and not o.get("is_multilayer"))
    assert "mismatch_film_buffer_pct" in tin
    film_buf = abs(float(tin["mismatch_film_buffer_pct"]))
    worst = abs(float(tin["mismatch_pct"]))
    # TiN–Si is typically larger than NbN–TiN; worst should exceed film–buf
    assert worst >= film_buf - 1e-9

    from siscforge.silicon.critical_thickness import estimate_critical_thickness

    ct_film = estimate_critical_thickness(tin["mismatch_film_buffer_pct"], formula="NbN")
    ct_worst = estimate_critical_thickness(tin["mismatch_pct"], formula="NbN")
    # Film–template should yield larger (or equal) h_c than worst-interface
    if ct_film.hc_primary_nm is not None and ct_worst.hc_primary_nm is not None:
        assert ct_film.hc_primary_nm >= ct_worst.hc_primary_nm - 1e-9


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
    assert low.critical_thickness_method in {"Matthews-Blakeslee", "People-Bean"}


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
    if score.membrane_transfer_candidate:
        assert "membrane" in score.membrane_transfer_note.lower()
        assert "membrane_transfer" not in (score.chemical_flags or [])


def test_debug_info_has_options() -> None:
    info = scorer_debug_info(_nbn_candidate())
    assert "options" in info
    assert info["scorer_version"] == "0.5"
