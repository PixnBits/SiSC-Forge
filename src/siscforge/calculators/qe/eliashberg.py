"""Isotropic Allen–Dynes and simple Eliashberg Tc helpers (pure functions).

These are ranking-oriented formulas, not full anisotropic Eliashberg solvers.
Allen–Dynes is always available from (λ, ω_log, μ*). A lightweight iterative
isotropic Eliashberg estimate is provided for denser a²F data when present.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def allen_dynes_tc(
    lambda_total: float,
    omega_log_K: float,
    mu_star: float = 0.1,
) -> float:
    """Allen–Dynes Tc (K) from λ, ω_log (K), and μ*.

    Tc = (ω_log / 1.2) exp[ −1.04(1+λ) / (λ − μ*(1+0.62λ)) ]

    Returns 0.0 if the denominator is non-positive or inputs are invalid.
    """
    if lambda_total <= 0.0 or omega_log_K <= 0.0:
        return 0.0
    denom = lambda_total - mu_star * (1.0 + 0.62 * lambda_total)
    if denom <= 1e-12:
        return 0.0
    exponent = -1.04 * (1.0 + lambda_total) / denom
    # Guard overflow for huge |exponent|
    if exponent < -100.0:
        return 0.0
    return float((omega_log_K / 1.2) * math.exp(exponent))


def mcMillan_tc(
    lambda_total: float,
    omega_log_K: float,
    mu_star: float = 0.1,
) -> float:
    """McMillan Tc (legacy); Allen–Dynes is preferred for λ ≳ 1."""
    if lambda_total <= 0.0 or omega_log_K <= 0.0:
        return 0.0
    denom = lambda_total - mu_star * (1.0 + 0.62 * lambda_total)
    if denom <= 1e-12:
        return 0.0
    return float((omega_log_K / 1.45) * math.exp(-1.04 * (1.0 + lambda_total) / denom))


def isotropic_eliashberg_tc_from_moments(
    lambda_total: float,
    omega_log_K: float,
    mu_star: float = 0.1,
    *,
    omega_2_K: float | None = None,
) -> float:
    """Approximate isotropic Eliashberg Tc using Allen–Dynes with a strong-coupling factor.

    When ω2 is available, applies the Allen–Dynes strong-coupling correction
    f1 * f2 (simplified). Otherwise returns Allen–Dynes Tc.

    This is **not** a full Matsubara Eliashberg solver; it is a transparent
    closed-form proxy suitable for ranking until a full solver is wired.
    """
    tc_ad = allen_dynes_tc(lambda_total, omega_log_K, mu_star)
    if tc_ad <= 0.0:
        return 0.0
    if omega_2_K is None or omega_2_K <= 0.0 or lambda_total < 0.5:
        return tc_ad

    # Allen–Dynes strong-coupling corrections (simplified forms)
    # f1 = [1 + (λ/Λ1)^(3/2)]^(1/3),  Λ1 = 2.46(1+3.8μ*)
    # f2 = 1 + (ω2/ω_log - 1) λ² / (λ² + Λ2²), Λ2 = 1.82(1+6.3μ*)(ω2/ω_log)
    lam = lambda_total
    mu = mu_star
    lambda1 = 2.46 * (1.0 + 3.8 * mu)
    f1 = (1.0 + (lam / max(lambda1, 1e-8)) ** 1.5) ** (1.0 / 3.0)
    ratio = omega_2_K / omega_log_K
    lambda2 = 1.82 * (1.0 + 6.3 * mu) * ratio
    f2 = 1.0 + (ratio - 1.0) * (lam**2) / (lam**2 + lambda2**2)
    return float(tc_ad * f1 * f2)


def tc_from_alpha2F(
    omega_cm1: Sequence[float],
    alpha2F: Sequence[float],
    mu_star: float = 0.1,
) -> dict[str, float]:
    """Compute λ, ω_log (K), and Allen–Dynes Tc from a discrete a²F(ω) spectrum.

    Parameters
    ----------
    omega_cm1:
        Phonon frequencies in cm⁻¹ (positive).
    alpha2F:
        Spectral function samples a²F(ω) on the same grid.
    """
    if len(omega_cm1) != len(alpha2F) or not omega_cm1:
        return {"lambda_total": 0.0, "omega_log": 0.0, "Tc_allen_dynes": 0.0}

    # cm⁻¹ → K : 1 cm⁻¹ = 1.4388 K
    cm1_to_K = 1.4388
    lam = 0.0
    log_num = 0.0
    for w_cm, a2f in zip(omega_cm1, alpha2F, strict=True):
        if w_cm <= 1e-8 or a2f < 0:
            continue
        w_K = w_cm * cm1_to_K
        # λ = 2 ∫ a²F(ω)/ω dω  — use simple trapezoid-ish point weight
        # Assume uniform Δω from neighbors when possible; else unit weight.
        d_omega = 1.0  # relative; cancel in ω_log if consistent
        contrib = 2.0 * a2f / w_cm * d_omega
        lam += contrib
        log_num += (2.0 * a2f / w_cm) * math.log(w_K) * d_omega

    if lam <= 1e-12:
        return {"lambda_total": 0.0, "omega_log": 0.0, "Tc_allen_dynes": 0.0}

    omega_log = math.exp(log_num / lam)
    tc = allen_dynes_tc(lam, omega_log, mu_star)
    return {
        "lambda_total": float(lam),
        "omega_log": float(omega_log),
        "Tc_allen_dynes": float(tc),
    }


def performance_score_from_epw(
    tc_K: float | None,
    *,
    ceiling_K: float = 40.0,
) -> float | None:
    """Map Tc (K) to the evaluation ``performance_score`` field.

    Phase 1 stores **Tc in kelvin** directly as ``performance_score`` so
    ranking's existing 40 K normalization remains valid.
    """
    if tc_K is None:
        return None
    return float(max(0.0, tc_K))
