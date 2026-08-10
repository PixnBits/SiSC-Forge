"""Tests for the Silicon Feasibility scorer (P2.1 weights + P2.2 stacks)."""

from __future__ import annotations

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import (
    CampaignConfig,
    SiFeasibilityWeights,
)
from siscforge.silicon.buffers import (
    BUFFER_LIBRARY,
    STACK_LIBRARY,
    aggregate_stack_flags,
    list_stacks_for_family,
)
from siscforge.silicon.feasibility import (
    COMPONENT_KEYS,
    COMPONENT_WEIGHTS,
    SCORER_VERSION,
    _layer_layer_mismatch_pct,
    evaluate_mismatch_options,
    normalize_component_weights,
    rank_by_si_feasibility,
    score_si_feasibility,
)


def _nbn_candidate(**meta_extra: object) -> StructureCandidate:
    meta = {
        "conventional_lattice_a": 4.392,
        "epitaxy_orientation": "auto",
        "use_buffers": True,
    }
    meta.update(meta_extra)
    return StructureCandidate(
        formula="NbN",
        material_family="tm_nitride",
        composition={"Nb": 0.5, "N": 0.5},
        lattice_abc=(4.392, 4.392, 4.392),
        lattice_angles=(90.0, 90.0, 90.0),
        substrate="Si(001)",
        in_plane_strain=0.0,
        metadata=meta,
    )


def _bsi_candidate() -> StructureCandidate:
    return StructureCandidate(
        formula="Si0.9B0.1",
        material_family="b_doped_si",
        composition={"Si": 0.9, "B": 0.1},
        lattice_abc=(5.43, 5.43, 5.43),
        substrate="Si(001)",
        in_plane_strain=0.0,
    )


def test_scorer_version_is_p22() -> None:
    assert SCORER_VERSION == "0.4"
    assert score_si_feasibility(_nbn_candidate()).version == "0.4"


def test_default_weights_match_component_weights_constant() -> None:
    assert COMPONENT_WEIGHTS == {
        "lattice_mismatch": 0.35,
        "thermal_budget": 0.20,
        "chemical_compatibility": 0.20,
        "buffer_availability": 0.10,
        "process_maturity": 0.15,
    }
    assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9
    cfg_w = SiFeasibilityWeights().as_dict()
    assert cfg_w == COMPONENT_WEIGHTS


def test_components_always_populated() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert 0.0 <= score.total <= 100.0
    assert set(score.weights) == set(COMPONENT_KEYS)
    assert abs(sum(score.weights.values()) - 1.0) < 1e-6


def test_normalize_component_weights_zero_sum_and_nonfinite() -> None:
    zeros = normalize_component_weights({k: 0.0 for k in COMPONENT_KEYS})
    assert zeros == COMPONENT_WEIGHTS
    # Non-finite falls back
    nan_w = normalize_component_weights({"lattice_mismatch": float("nan")})
    assert nan_w == COMPONENT_WEIGHTS
    inf_w = normalize_component_weights({"thermal_budget": float("inf")})
    assert inf_w == COMPONENT_WEIGHTS


def test_weight_override_reorders_candidates() -> None:
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()
    default_order = rank_by_si_feasibility([nbn, bsi])
    assert default_order[0][0].formula == "Si0.9B0.1"
    maturity_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 1.0,
    }
    maturity_order = rank_by_si_feasibility([nbn, bsi], weights=maturity_heavy)
    assert maturity_order[0][0].formula == "NbN"


def test_yaml_config_weights() -> None:
    cfg = CampaignConfig(
        name="si_maturity",
        si_feasibility={
            "weights": {
                "lattice_mismatch": 0.0,
                "thermal_budget": 0.0,
                "chemical_compatibility": 0.0,
                "buffer_availability": 0.0,
                "process_maturity": 1.0,
            }
        },
    )
    ranked = rank_by_si_feasibility([_nbn_candidate(), _bsi_candidate()], config=cfg.si_feasibility)
    assert ranked[0][0].formula == "NbN"


def test_exact_weights_provenance() -> None:
    score = score_si_feasibility(_nbn_candidate())
    assert abs(sum(score.weights.values()) - 1.0) < 1e-12
    # No independent rounding drift
    for v in score.weights.values():
        assert isinstance(v, float)


# --- P2.2 multi-layer stacks + chemical/thermal flags ---


def test_stack_library_has_tm_nitride_multilayer() -> None:
    stacks = list_stacks_for_family("tm_nitride", multilayer_only=True)
    names = {s.name for s in stacks}
    assert "AlN/TiN" in names or "TiN/AlN" in names or "MgO/TiN" in names
    assert all("/" in s.name for s in stacks)
    assert "AlN/TiN" in STACK_LIBRARY
    assert STACK_LIBRARY["AlN/TiN"].layers == ("AlN", "TiN")
    # Single-layer library still present
    assert "TiN" in BUFFER_LIBRARY
    assert "direct_Si" in BUFFER_LIBRARY


def test_multilayer_stack_proposed_for_tm_nitride() -> None:
    cand = _nbn_candidate()
    opts = evaluate_mismatch_options(cand)
    stack_opts = [o for o in opts if o.get("is_multilayer")]
    assert stack_opts, "expected at least one multi-layer stack option for NbN"
    stack_names = {o["buffer"] for o in stack_opts}
    assert any("/" in n for n in stack_names)

    score = score_si_feasibility(cand)
    # Multi-layer style recommendation appears in recommended_buffers or notes
    rec = score.recommended_buffers
    assert any("/" in b for b in rec), f"expected multi-layer in recommended_buffers, got {rec}"
    assert "stack" in score.notes.lower() or any("/" in b for b in rec)


def test_single_buffer_paths_still_present() -> None:
    """Single-buffer and direct paths remain available (no regression)."""
    cand = _nbn_candidate()
    opts = evaluate_mismatch_options(cand)
    paths = {o["path"] for o in opts}
    assert any(p.startswith("direct/") for p in paths)
    assert any(p.startswith("buffer/TiN") for p in paths)
    # Direct / 45° still competitive for rocksalt nitrides
    assert any(o.get("match") == "45deg" for o in opts)


def test_chemical_and_thermal_flags_on_score() -> None:
    score = score_si_feasibility(_nbn_candidate())
    # Flags and thermal window should surface when a buffer/stack path is chosen
    assert isinstance(score.chemical_flags, list)
    # NbN typically picks a buffer or 45° direct; notes should mention windows/flags
    assert score.process_temp_ceiling_c is not None
    assert score.process_temp_ceiling_c > 0
    # When buffers/stacks are used, expect at least one chemical flag or window note
    has_signal = bool(score.chemical_flags) or bool(score.thermal_window_note) or (
        "chemical flags" in score.notes or "thermal window" in score.notes
    )
    assert has_signal, score.notes


def test_stack_option_carries_chemical_thermal_metadata() -> None:
    opts = evaluate_mismatch_options(_nbn_candidate())
    multi = [o for o in opts if o.get("is_multilayer")]
    assert multi
    sample = multi[0]
    assert sample.get("chemical_flags")
    assert sample.get("max_process_temp_c") is not None or sample.get("thermal_window_note")
    assert sample.get("layers") and len(sample["layers"]) >= 2
    assert "process_note" in sample


def test_buffers_disabled_skips_stacks() -> None:
    cand = _nbn_candidate(use_buffers=False)
    opts = evaluate_mismatch_options(cand)
    assert all(not o.get("is_multilayer") for o in opts)
    assert all(str(o.get("buffer")) == "direct_Si" for o in opts)


def test_buffers_disabled_does_not_recommend_or_credit_stacks() -> None:
    """use_buffers=False must not list library stacks or inflate buffer_availability."""
    disabled = score_si_feasibility(_nbn_candidate(use_buffers=False))
    enabled = score_si_feasibility(_nbn_candidate(use_buffers=True))
    assert all("/" not in b for b in disabled.recommended_buffers)
    assert all(b == "direct_Si" for b in disabled.recommended_buffers)
    # Direct-only path should not get stack-library credit over enabled path's baseline
    assert disabled.components.buffer_availability <= enabled.components.buffer_availability
    # No stack names in notes as assumed recommendation
    assert "assumed stack:" not in disabled.notes
    assert "assumed buffer:" not in disabled.notes


def test_oxide_on_si_flag_only_when_oxide_is_substrate_side() -> None:
    """Position-aware flags: oxide_on_si only if MgO (etc.) is the Si-side layer."""
    mgo_tin = aggregate_stack_flags(STACK_LIBRARY["MgO/TiN"])
    tin_mgo = aggregate_stack_flags(STACK_LIBRARY["TiN/MgO"])
    assert "oxide_on_si" in mgo_tin  # MgO on Si
    assert "oxide_on_si" not in tin_mgo  # MgO is film-side, not on Si
    # Single-layer MgO still carries oxide_on_si
    from siscforge.silicon.buffers import stack_from_single
    single = aggregate_stack_flags(stack_from_single(BUFFER_LIBRARY["MgO"]))
    assert "oxide_on_si" in single


def test_interlayer_mismatch_uses_film_substrate_convention() -> None:
    """Interlayer misfit: 100*(a_bottom - a_top)/a_top (matches lattice_mismatch_percent)."""
    a_aln = BUFFER_LIBRARY["AlN"].lattice_a_ang  # 3.112
    a_tin = BUFFER_LIBRARY["TiN"].lattice_a_ang  # 4.242
    pct = _layer_layer_mismatch_pct(a_aln, a_tin)
    expected = 100.0 * (a_aln - a_tin) / a_tin
    assert abs(pct - expected) < 1e-9
    # Magnitude ~26.6%, not the inverted ~36.3%
    assert abs(abs(pct) - 26.64) < 0.1
    assert abs(pct) < 30.0


def test_weight_override_still_reorders_with_p22_scorer() -> None:
    """P2.1 weight override behaviour preserved after P2.2 stack enrichment."""
    nbn = _nbn_candidate()
    bsi = _bsi_candidate()
    default_order = rank_by_si_feasibility([nbn, bsi])
    assert default_order[0][0].formula == "Si0.9B0.1"
    buffer_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 1.0,
        "process_maturity": 0.0,
    }
    # Both should still score; reordering by maturity still works
    maturity_heavy = {
        "lattice_mismatch": 0.0,
        "thermal_budget": 0.0,
        "chemical_compatibility": 0.0,
        "buffer_availability": 0.0,
        "process_maturity": 1.0,
    }
    maturity_order = rank_by_si_feasibility([nbn, bsi], weights=maturity_heavy)
    assert maturity_order[0][0].formula == "NbN"
    # Weights appear on score audit trail
    s = score_si_feasibility(nbn, weights=buffer_heavy)
    assert abs(s.weights["buffer_availability"] - 1.0) < 1e-9
    assert s.version == "0.4"


def test_process_note_not_duplicated_in_score_notes() -> None:
    """process_note lives in a dedicated field; score notes should not repeat it."""
    score = score_si_feasibility(_nbn_candidate(epitaxy_orientation="cube_on_cube"))
    # When a buffer/stack is chosen, process note appears at most once.
    opts = evaluate_mismatch_options(_nbn_candidate(epitaxy_orientation="cube_on_cube"))
    best = opts[0]
    pn = str(best.get("process_note") or "")
    if pn:
        assert score.notes.count(pn) <= 1, score.notes
    # Option notes must not already embed process_note (would double when appended)
    assert pn == "" or pn not in str(best.get("notes") or "")


def test_process_temp_ceiling_note_matches_exported_field() -> None:
    """Human-readable ceiling note must match process_temp_ceiling_c / scored temp."""
    score = score_si_feasibility(_nbn_candidate())
    assert score.process_temp_ceiling_c is not None
    expected = f"process temp ceiling ~{score.process_temp_ceiling_c:.0f} °C"
    # Either the note is present with the matching value, or no ceiling note at all
    # (direct path with only family temp still has ceiling field from t_proc).
    if "process temp ceiling ~" in score.notes:
        assert expected in score.notes, (expected, score.notes)

