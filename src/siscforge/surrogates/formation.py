"""Heuristic formation-energy pre-filter (Phase 0 stub; not a trained GNN).

Produces a transparent ``energy_above_hull_proxy`` (eV/atom) from composition,
material family, and epitaxial strain. Real ALIGNN/MatGL models replace this
in a later phase without changing the filter interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import FormationFilterConfig

# Family baseline hull proxy (eV/atom) — lower is more stable / preferred.
_FAMILY_BASE_HULL: dict[str, float] = {
    "tm_nitride": 0.02,
    "b_doped_si": 0.05,
    "mgb2_boride": 0.08,
    "nickelate": 0.15,
    "cuprate": 0.18,
    "other": 0.20,
}

# Well-known binaries get a small stability bonus.
_STABLE_BINARIES: frozenset[str] = frozenset(
    {"NbN", "TiN", "ZrN", "HfN", "VN", "TaN", "MgB2", "Si"}
)


def estimate_energy_above_hull_proxy(candidate: StructureCandidate) -> float:
    """Return a deterministic heuristic E_hull proxy in eV/atom.

    Components (additive, floored at 0):
    - Family baseline
    - Strain penalty: ~0.5 eV/atom per |ε| of 0.10 (quadratic in |ε|)
    - Ternary mixing penalty for multi-metal nitrides
    - Binary stability bonus for known rocksalt nitrides
    """
    base = _FAMILY_BASE_HULL.get(candidate.material_family, 0.20)

    strain = candidate.in_plane_strain
    if strain is None and candidate.strain_tensor is not None:
        strain = float(candidate.strain_tensor[0])
    strain = 0.0 if strain is None else float(strain)
    # Soft quadratic penalty for epitaxial strain
    strain_pen = 50.0 * (abs(strain) ** 2)

    # Extra metals beyond M+N in nitrides → mixing cost
    n_elems = len(candidate.composition) or (
        2 if candidate.material_family == "tm_nitride" else 1
    )
    mix_pen = 0.0
    if candidate.material_family == "tm_nitride" and n_elems > 2:
        mix_pen = 0.03 * (n_elems - 2)

    bonus = 0.0
    formula = candidate.formula
    if formula in _STABLE_BINARIES or any(
        formula.startswith(b) and len(formula) <= len(b) + 1 for b in _STABLE_BINARIES
    ):
        bonus = 0.015
    # NbN / TiN exact-ish
    if formula in {"NbN", "TiN", "ZrN", "HfN"}:
        bonus = 0.02

    hull = max(0.0, base + strain_pen + mix_pen - bonus)
    return round(hull, 5)


@dataclass
class FilterResult:
    """Outcome of applying :class:`FormationEnergyFilter`."""

    kept: list[StructureCandidate] = field(default_factory=list)
    rejected: list[StructureCandidate] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    """candidate_id → rejection reason (only for rejected)."""

    config_version: str = "0.1-heuristic"

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    def summary(self) -> dict[str, Any]:
        return {
            "n_kept": self.n_kept,
            "n_rejected": self.n_rejected,
            "version": self.config_version,
            "reasons": dict(self.reasons),
        }


class FormationEnergyFilter:
    """Rule-based pre-filter using :func:`estimate_energy_above_hull_proxy`."""

    def __init__(self, config: FormationFilterConfig | None = None) -> None:
        self.config = config or FormationFilterConfig()

    def annotate(self, candidate: StructureCandidate) -> StructureCandidate:
        """Attach hull proxy (and metadata) without filtering."""
        hull = estimate_energy_above_hull_proxy(candidate)
        meta = dict(candidate.metadata)
        meta["energy_above_hull_proxy"] = hull
        meta["formation_filter_version"] = self.config.version
        return candidate.model_copy(
            update={
                "energy_above_hull_proxy": hull,
                "metadata": meta,
            }
        )

    def filter(
        self,
        candidates: list[StructureCandidate],
    ) -> FilterResult:
        """Filter *candidates*; always annotates hull proxy on kept and rejected."""
        cfg = self.config
        result = FilterResult(config_version=cfg.version)

        if not cfg.enabled:
            result.kept = [self.annotate(c) for c in candidates]
            return result

        annotated: list[StructureCandidate] = []
        for cand in candidates:
            c = self.annotate(cand)
            hull = c.energy_above_hull_proxy
            assert hull is not None

            if hull > cfg.max_e_hull_eV_per_atom:
                result.rejected.append(c)
                result.reasons[c.candidate_id] = (
                    f"hull_proxy {hull:.4f} > max {cfg.max_e_hull_eV_per_atom}"
                )
                continue

            if cfg.max_strain_magnitude is not None and c.in_plane_strain is not None:
                if abs(c.in_plane_strain) > cfg.max_strain_magnitude:
                    result.rejected.append(c)
                    result.reasons[c.candidate_id] = (
                        f"|strain| {abs(c.in_plane_strain):.4f} > "
                        f"max {cfg.max_strain_magnitude}"
                    )
                    continue

            annotated.append(c)

        # Prefer listed families only when keep_top_n forces a cut? Soft: sort by hull.
        annotated.sort(
            key=lambda c: (
                0 if c.material_family in cfg.prefer_families else 1,
                c.energy_above_hull_proxy if c.energy_above_hull_proxy is not None else 9.0,
            )
        )

        if cfg.keep_top_n is not None and len(annotated) > cfg.keep_top_n:
            for drop in annotated[cfg.keep_top_n :]:
                result.rejected.append(drop)
                result.reasons[drop.candidate_id] = (
                    f"outside keep_top_n={cfg.keep_top_n}"
                )
            annotated = annotated[: cfg.keep_top_n]

        result.kept = annotated
        return result


def filter_candidates(
    candidates: list[StructureCandidate],
    config: FormationFilterConfig | None = None,
) -> FilterResult:
    """Convenience wrapper around :class:`FormationEnergyFilter`."""
    return FormationEnergyFilter(config).filter(candidates)
