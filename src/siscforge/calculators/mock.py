"""Dry-run / mock calculator that returns schema-valid fake results."""

from __future__ import annotations

import hashlib
from typing import Any

from siscforge import __version__
from siscforge.calculators import registry
from siscforge.calculators.base import BaseCalculator
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.provenance import Provenance
from siscforge.models.results import PhononResult, SCFResult, SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility


def _stable_unit_interval(seed: str) -> float:
    """Map an arbitrary string to a deterministic float in [0, 1)."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


class MockCalculator(BaseCalculator):
    """Return plausible, schema-valid fake evaluations without running DFT.

    Values are deterministic given ``candidate.candidate_id`` so dry-runs are
    reproducible across machines.
    """

    name = "mock"

    def run(self, candidate: StructureCandidate, **kwargs: Any) -> CandidateEvaluation:
        seed = candidate.candidate_id
        e_rand = _stable_unit_interval(seed + ":energy")
        ph_rand = _stable_unit_interval(seed + ":phonon")
        perf_rand = _stable_unit_interval(seed + ":perf")

        # Dummy energies / hull: nitrides look more stable on average.
        hull = 0.05 + e_rand * 0.25
        if candidate.material_family == "tm_nitride":
            hull *= 0.4

        scf = SCFResult(
            total_energy_eV=round(-100.0 - e_rand * 50.0, 4),
            energy_above_hull_eV_per_atom=round(hull, 4),
            band_gap_eV=0.0,
            is_metallic=True,
            status="mock",
            quality_tag="mock",
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                parameters=dict(kwargs) if kwargs else {},
                notes="dry-run SCF placeholder",
            ),
        )

        # ~15% of mock candidates get soft imaginary modes for realism.
        has_imag = ph_rand < 0.15
        min_freq = round(-50.0 * ph_rand, 2) if has_imag else round(20.0 + ph_rand * 200.0, 2)
        phonon = PhononResult(
            min_frequency_cm1=min_freq,
            has_imaginary_modes=has_imag,
            dynamically_stable=not has_imag,
            n_modes=3 * max(1, len(candidate.composition) or 2),
            max_frequency_cm1=round(400.0 + ph_rand * 400.0, 2),
            status="mock",
            quality_tag="mock",
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                notes="dry-run phonon placeholder",
            ),
        )

        # Prefer the real Phase-0 Si-feasibility scorer (still mock DFT/phonon).
        precomputed = kwargs.get("si_feasibility")
        if isinstance(precomputed, SiFeasibilityScore):
            si = precomputed
        else:
            si = score_si_feasibility(candidate)

        # Fake "Tc proxy" performance score in roughly 0–40 K scale, normalized later.
        performance = round(5.0 + perf_rand * 35.0, 2)
        if has_imag:
            performance *= 0.3

        # Composite: blend normalized performance (assume ~40 K ceiling) with Si score.
        perf_norm = min(100.0, (performance / 40.0) * 100.0)
        composite = round(0.6 * perf_norm + 0.4 * si.total, 2)

        return CandidateEvaluation(
            candidate=candidate,
            scf=scf,
            phonon=phonon,
            si_feasibility=si,
            performance_score=performance,
            composite_score=composite,
            status="mock",
            calculator_name=self.name,
            notes="Produced by MockCalculator dry-run path",
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                parent_ids=[candidate.candidate_id],
                notes="end-to-end mock evaluation",
            ),
        )


# Self-register on import
registry.register(MockCalculator(), overwrite=True)
