"""Golden-system references for conventional electron-phonon pathway.

NbN (rocksalt)
--------------
Literature DFPT/EPW-type estimates vary with functional and structure, but
order-of-magnitude targets for bulk rocksalt NbN are approximately:

- λ ≈ 0.8 – 1.5
- ω_log ≈ 200 – 400 K (~15–35 meV)
- Tc ≈ 10 – 17 K (experimental bulk ~16 K; theory often 12–18 K)

MgB₂
----
Classic **two-gap** superconductor (σ / π bands). Isotropic EPW still recovers
order-of-magnitude Tc when λ and ω_log are isotropic averages:

- λ ≈ 0.7 – 1.0 (isotropic average; band-resolved values differ)
- ω_log ≈ 600 – 800 K (high-energy B modes)
- Tc ≈ 30 – 45 K (experimental 39 K)

These ranges are for **ranking / regression gates**, not publication-grade
validation. Mock tests use mid-range fixture values. Anisotropic Eliashberg is
out of scope for Phase 1 screening.
"""

from __future__ import annotations

from typing import Final

# --- NbN ---
NBN_LAMBDA_RANGE: Final[tuple[float, float]] = (0.5, 2.0)
NBN_OMEGA_LOG_K_RANGE: Final[tuple[float, float]] = (150.0, 500.0)
NBN_TC_K_RANGE: Final[tuple[float, float]] = (8.0, 25.0)

# Fixture mid-values (mock-safe / offline parser)
NBN_FIXTURE_LAMBDA: Final[float] = 1.05
NBN_FIXTURE_OMEGA_LOG_K: Final[float] = 280.0  # ~24 meV
NBN_FIXTURE_MU_STAR: Final[float] = 0.10

NBN_EPW_NOTES: Final[str] = (
    "Bulk rocksalt NbN EPW golden reference (Phase 1). "
    "Expect λ ~ 1, ω_log ~ 200–400 K, Tc ~ 10–17 K under PBE-level EPW. "
    "Gate real runs with SISCFORGE_RUN_EPW=1."
)

# --- MgB2 ---
MGB2_LAMBDA_RANGE: Final[tuple[float, float]] = (0.5, 1.2)
MGB2_OMEGA_LOG_K_RANGE: Final[tuple[float, float]] = (500.0, 900.0)
MGB2_TC_K_RANGE: Final[tuple[float, float]] = (25.0, 50.0)

MGB2_FIXTURE_LAMBDA: Final[float] = 0.85
MGB2_FIXTURE_OMEGA_LOG_K: Final[float] = 700.0  # ~60 meV
MGB2_FIXTURE_MU_STAR: Final[float] = 0.10

MGB2_EPW_NOTES: Final[str] = (
    "Bulk MgB₂ EPW golden reference (Phase 1). Two-gap physics is reduced to an "
    "isotropic average (λ, ω_log → Allen–Dynes / isotropic Eliashberg). "
    "Expect λ ~ 0.7–1.0, ω_log ~ 600–800 K, Tc ~ 30–45 K (exp. 39 K) under "
    "well-converged EPW. Screening templates use proj=random; production needs "
    "tuned Wannier projections. Gate real runs with SISCFORGE_RUN_EPW=1."
)

# Back-compat alias
MGB2_NOTES: Final[str] = MGB2_EPW_NOTES
