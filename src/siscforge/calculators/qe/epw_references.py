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
Classic two-gap superconductor; isotropic EPW still recovers order-of-magnitude Tc:

- λ ≈ 0.7 – 1.0 (isotropic average)
- ω_log ≈ 600 – 800 K
- Tc ≈ 30 – 45 K (experimental 39 K)

These ranges are for **ranking / regression gates**, not publication-grade
validation. Mock tests use mid-range fixture values.
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
# Allen–Dynes from fixture moments is computed in tests

NBN_EPW_NOTES: Final[str] = (
    "Bulk rocksalt NbN EPW golden reference (Phase 1). "
    "Expect λ ~ 1, ω_log ~ 200–400 K, Tc ~ 10–17 K under PBE-level EPW. "
    "Gate real runs with SISCFORGE_RUN_EPW=1."
)

# --- MgB2 skeleton ---
MGB2_LAMBDA_RANGE: Final[tuple[float, float]] = (0.5, 1.2)
MGB2_OMEGA_LOG_K_RANGE: Final[tuple[float, float]] = (500.0, 900.0)
MGB2_TC_K_RANGE: Final[tuple[float, float]] = (25.0, 50.0)
MGB2_FIXTURE_LAMBDA: Final[float] = 0.85
MGB2_FIXTURE_OMEGA_LOG_K: Final[float] = 700.0
MGB2_NOTES: Final[str] = (
    "MgB₂ isotropic EPW skeleton (Phase 1). Full golden recovery is a follow-up "
    "session; structure + campaign YAML are provided."
)
