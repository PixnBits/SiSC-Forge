"""Reference values for golden-system tests (bulk rocksalt NbN phonons).

These are **order-of-magnitude** targets for Phase-0 validation, not
production-quality Eliashberg inputs. Literature DFPT / inelastic X-ray
scattering on rocksalt NbN typically finds:

- Dynamically stable at the experimental lattice constant (~4.39 Å)
- Acoustic branches up to roughly 150–250 cm⁻¹
- Optical branches near ~450–550 cm⁻¹ at Γ (method- and functional-dependent)

When a real QE run is available (``SISCFORGE_RUN_QE=1``), the golden test
checks that:

1. ``has_imaginary_modes`` is False (within a small numerical threshold)
2. ``max_frequency_cm1`` lies in ``NBN_OPTICAL_MAX_CM1_RANGE``
3. ``min_frequency_cm1`` > ``-NBN_IMAG_THRESHOLD_CM1``

Mock-mode tests only check schema validity and that the reference structure
builds correctly.
"""

from __future__ import annotations

from typing import Final

# Experimental rocksalt lattice constant (Å)
NBN_LATTICE_A_ANG: Final[float] = 4.392

# Acoustic numerical floor (cm⁻¹): modes softer than -this count as imaginary
NBN_IMAG_THRESHOLD_CM1: Final[float] = 5.0

# Expected range for the highest optical mode at/near Γ (cm⁻¹)
NBN_OPTICAL_MAX_CM1_RANGE: Final[tuple[float, float]] = (300.0, 800.0)

# Expected range for the lowest non-acoustic optical-ish max of acoustic manifold
NBN_ACOUSTIC_MAX_CM1_RANGE: Final[tuple[float, float]] = (50.0, 300.0)

# Fixture-derived "screening quality" Gamma frequencies (cm⁻¹) used when QE is
# not run — representative of a stable rocksalt nitride, not a fit to experiment.
NBN_FIXTURE_FREQUENCIES_CM1: Final[list[float]] = [
    0.41,
    0.52,
    0.63,
    508.1,
    511.8,
    515.5,
]

NBN_REFERENCE_NOTES: Final[str] = (
    "Bulk rocksalt NbN phonon golden reference (Phase 0). "
    "Real QE DFPT should recover a dynamically stable spectrum with optical "
    "modes roughly 450–550 cm⁻¹ (PBE, typical cutoffs). "
    "See docs/examples/nbN_phonon_qe.md for how to run a real calculation."
)
