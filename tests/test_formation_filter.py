"""Tests for the heuristic formation-energy pre-filter."""

from __future__ import annotations

from siscforge.models.config import EnumerationConfig, FormationFilterConfig
from siscforge.structure.generator import generate_candidates
from siscforge.surrogates.formation import (
    FormationEnergyFilter,
    estimate_energy_above_hull_proxy,
    filter_candidates,
)


def test_hull_proxy_in_range() -> None:
    cands = generate_candidates(
        EnumerationConfig(formulas=["NbN"], strain_values=[0.0], max_candidates=1)
    )
    hull = estimate_energy_above_hull_proxy(cands[0])
    assert 0.0 <= hull < 0.5


def test_strain_increases_hull() -> None:
    bulk = generate_candidates(
        EnumerationConfig(formulas=["NbN"], strain_values=[0.0], max_candidates=1)
    )[0]
    strained = generate_candidates(
        EnumerationConfig(formulas=["NbN"], strain_values=[0.04], max_candidates=1)
    )[0]
    assert estimate_energy_above_hull_proxy(strained) > estimate_energy_above_hull_proxy(
        bulk
    )


def test_filter_rejects_high_hull() -> None:
    cands = generate_candidates(
        EnumerationConfig(
            formulas=["NbN", "TiN"],
            strain_values=[0.0, 0.08],
            max_candidates=10,
        )
    )
    cfg = FormationFilterConfig(
        enabled=True,
        max_e_hull_eV_per_atom=0.05,
        max_strain_magnitude=0.05,
    )
    result = filter_candidates(cands, cfg)
    assert result.n_kept + result.n_rejected == len(cands)
    for c in result.kept:
        assert c.energy_above_hull_proxy is not None
        assert c.energy_above_hull_proxy <= 0.05
        if c.in_plane_strain is not None:
            assert abs(c.in_plane_strain) <= 0.05


def test_filter_disabled_keeps_all() -> None:
    cands = generate_candidates(
        EnumerationConfig(formulas=["NbN", "TiN"], strain_values=[0.0], max_candidates=4)
    )
    result = FormationEnergyFilter(FormationFilterConfig(enabled=False)).filter(cands)
    assert result.n_kept == len(cands)
    assert result.n_rejected == 0
    assert all(c.energy_above_hull_proxy is not None for c in result.kept)


def test_keep_top_n() -> None:
    cands = generate_candidates(
        EnumerationConfig(
            metals=["Nb", "Ti", "Zr", "Hf"],
            strain_values=[0.0],
            max_candidates=10,
        )
    )
    result = filter_candidates(
        cands,
        FormationFilterConfig(enabled=True, max_e_hull_eV_per_atom=1.0, keep_top_n=2),
    )
    assert result.n_kept == 2
