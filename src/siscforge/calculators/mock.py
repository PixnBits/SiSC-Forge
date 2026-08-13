"""Dry-run / mock calculator that returns schema-valid fake results."""

from __future__ import annotations

import hashlib
from typing import Any

from siscforge import __version__
from siscforge.calculators import registry
from siscforge.calculators.base import BaseCalculator
from siscforge.calculators.qe.eliashberg import allen_dynes_tc
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.provenance import Provenance
from siscforge.models.config import DFTConfig
from siscforge.models.results import (
    ElectronPhononResult,
    PhononResult,
    SCFResult,
    SiFeasibilityScore,
)
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

        # Fake conventional e-ph moments → Allen–Dynes Tc as performance_score
        lam = 0.4 + perf_rand * 1.2
        omega_log = 200.0 + perf_rand * 300.0
        if candidate.material_family == "tm_nitride":
            lam = 0.7 + perf_rand * 0.8
            omega_log = 220.0 + perf_rand * 150.0
        elif candidate.material_family == "mgb2_boride":
            lam = 0.6 + perf_rand * 0.4
            omega_log = 600.0 + perf_rand * 150.0
        if has_imag:
            lam *= 0.3
        mu_star = 0.10
        tc_ad = allen_dynes_tc(lam, omega_log, mu_star)
        performance = round(tc_ad, 2)

        a2f_summary: dict[str, object] = {"method": "mock", "tc_model": "isotropic_average"}
        if candidate.material_family == "mgb2_boride":
            a2f_summary["material_notes"] = (
                "MgB2 two-gap physics reduced to isotropic average in mock/EPW screening"
            )
            a2f_summary["pairing"] = "conventional_two_gap"

        eph = ElectronPhononResult(
            lambda_total=round(lam, 4),
            omega_log=round(omega_log, 2),
            mu_star=mu_star,
            Tc_allen_dynes=performance,
            Tc_eliashberg=round(performance * 1.05, 2) if performance > 0 else 0.0,
            converged=not has_imag,
            wannier_ok=True,
            status="mock",
            quality_tag="mock",
            alpha2F_summary=a2f_summary,
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                notes="dry-run EPW/Eliashberg placeholder",
            ),
        )

        # Composite: blend normalized performance (assume ~40 K ceiling) with Si score.
        perf_norm = min(100.0, (performance / 40.0) * 100.0)
        composite = round(0.6 * perf_norm + 0.4 * si.total, 2)

        # P3.1: optional DFT+U mock — inert unless campaign enables it
        dftu_result = None
        wannier_result = None
        dft = kwargs.get("dft")
        enable_dftu = bool(kwargs.get("enable_dftu"))
        enable_wannier = bool(kwargs.get("enable_wannier"))
        dftu_cfg = None
        wannier_cfg = None
        if isinstance(dft, DFTConfig):
            from siscforge.calculators.qe.dftu import dftu_is_enabled
            from siscforge.calculators.qe.wannier import wannier_is_enabled

            enable_dftu = enable_dftu or dftu_is_enabled(dft)
            enable_wannier = enable_wannier or wannier_is_enabled(dft)
            dftu_cfg = dft.dftu
            wannier_cfg = dft.wannier
        elif isinstance(dft, dict):
            enable_dftu = enable_dftu or bool(
                dft.get("do_dftu") or (dft.get("dftu") or {}).get("enabled")
            )
            enable_wannier = enable_wannier or bool(
                dft.get("do_wannier") or (dft.get("wannier") or {}).get("enabled")
            )
            from siscforge.models.config import DFTUConfig, WannierConfig

            raw_u = dft.get("dftu") or {}
            dftu_cfg = DFTUConfig.model_validate(raw_u) if raw_u else DFTUConfig(enabled=True)
            raw_w = dft.get("wannier") or {}
            wannier_cfg = (
                WannierConfig.model_validate(raw_w) if raw_w else WannierConfig(enabled=True)
            )
        if enable_dftu:
            from siscforge.calculators.qe.dftu import mock_dftu_result

            dftu_result = mock_dftu_result(
                seed=seed,
                dftu=dftu_cfg,
                formula=candidate.formula,
                material_family=candidate.material_family,
            )
        if enable_wannier:
            from siscforge.calculators.qe.wannier import mock_wannier_result

            wannier_result = mock_wannier_result(
                seed=seed,
                wannier=wannier_cfg,
                formula=candidate.formula,
                material_family=candidate.material_family,
            )

        notes = "Produced by MockCalculator dry-run path"
        if dftu_result is not None:
            notes += f"; DFT+U mock: {dftu_result.summary_line()}"
        if wannier_result is not None:
            notes += f"; Wannier mock: {wannier_result.summary_line()}"

        extras = []
        if dftu_result is not None:
            extras.append("DFT+U")
        if wannier_result is not None:
            extras.append("Wannier")
        extra_note = (" with " + "+".join(extras)) if extras else ""

        return CandidateEvaluation(
            candidate=candidate,
            scf=scf,
            phonon=phonon,
            electron_phonon=eph,
            dftu=dftu_result,
            wannier=wannier_result,
            si_feasibility=si,
            performance_score=performance,
            performance_score_source="mock",
            composite_score=composite,
            status="mock",
            calculator_name=self.name,
            notes=notes,
            provenance=Provenance(
                source="mock_calculator",
                software={"siscforge": __version__},
                parent_ids=[candidate.candidate_id],
                notes="end-to-end mock evaluation" + extra_note,
            ),
        )


# Self-register on import
registry.register(MockCalculator(), overwrite=True)
