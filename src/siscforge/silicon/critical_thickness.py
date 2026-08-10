"""Critical-thickness estimates and membrane-transfer heuristics (P2.3).

Provides transparent **Matthews–Blakeslee** (mechanical equilibrium) and
**People–Bean** (energy-balance / metastable) critical-thickness estimates for
epitaxial films, plus lightweight rule-based membrane-transfer notes.

Values use a small table of **literature-order-of-magnitude** elastic / lattice
parameters (Poisson ratio, Burgers vector). They are **not** DFT-derived and are
intended for ranking / process guidance only — not continuum FEM.

When mismatch or elastic data is missing, helpers fall back to a conservative
thickness band without raising.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Elastic / lattice parameter table (heuristic — document sources loosely)
# ---------------------------------------------------------------------------

# Burgers vector for FCC / rocksalt perfect dislocations is typically a/√2.
_SQRT2 = math.sqrt(2.0)

ThicknessMethod = Literal[
    "Matthews-Blakeslee",
    "People-Bean",
    "heuristic fallback",
]


@dataclass(frozen=True)
class ElasticParams:
    """Approximate elastic / lattice parameters for critical-thickness models.

    All values are literature-order heuristics, not DFT-derived.
    """

    key: str
    a_ang: float
    """Cubic (or effective cubic) lattice constant *a* (Å)."""

    poisson: float = 0.25
    """Poisson ratio ν (dimensionless)."""

    burgers_ang: float | None = None
    """Burgers magnitude *b* (Å). Default: ``a / √2``."""

    young_gpa: float | None = None
    """Optional Young's modulus (GPa) — not required for MB/PB hc."""

    notes: str = "literature-order heuristic; not DFT"

    @property
    def b_ang(self) -> float:
        if self.burgers_ang is not None and self.burgers_ang > 0:
            return float(self.burgers_ang)
        return float(self.a_ang) / _SQRT2


# Priority: tm nitrides; modest coverage for Si, MgB₂, oxides, family defaults.
ELASTIC_LIBRARY: dict[str, ElasticParams] = {
    # --- transition-metal nitrides (rocksalt) ---
    "NbN": ElasticParams(
        key="NbN",
        a_ang=4.392,
        poisson=0.25,
        young_gpa=350.0,
        notes="rocksalt NbN; ν~0.25 order-of-magnitude",
    ),
    "TiN": ElasticParams(
        key="TiN",
        a_ang=4.242,
        poisson=0.25,
        young_gpa=400.0,
        notes="rocksalt TiN; stiff nitride template",
    ),
    "ZrN": ElasticParams(
        key="ZrN",
        a_ang=4.577,
        poisson=0.25,
        young_gpa=350.0,
        notes="rocksalt ZrN",
    ),
    "HfN": ElasticParams(
        key="HfN",
        a_ang=4.525,
        poisson=0.25,
        young_gpa=350.0,
        notes="rocksalt HfN",
    ),
    "VN": ElasticParams(key="VN", a_ang=4.139, poisson=0.25, notes="rocksalt VN"),
    "TaN": ElasticParams(key="TaN", a_ang=4.330, poisson=0.25, notes="rocksalt TaN"),
    "AlN": ElasticParams(
        key="AlN",
        a_ang=3.112,
        poisson=0.25,
        burgers_ang=3.112 / _SQRT2,  # effective; wurtzite is anisotropic
        notes="wurtzite AlN a-proxy; isotropic MB/PB only",
    ),
    # --- substrate / common buffers ---
    "Si": ElasticParams(
        key="Si",
        a_ang=5.4307,
        poisson=0.28,
        young_gpa=160.0,
        notes="cubic Si; classic MB/PB reference",
    ),
    "MgO": ElasticParams(
        key="MgO",
        a_ang=4.212,
        poisson=0.18,
        young_gpa=250.0,
        notes="rocksalt MgO",
    ),
    # --- other families (modest) ---
    "MgB2": ElasticParams(
        key="MgB2",
        a_ang=3.086,
        poisson=0.20,
        burgers_ang=3.086,  # basal-plane order-of-magnitude
        notes="MgB2 hexagonal a; isotropic hc is a rough proxy only",
    ),
    # family-level defaults (used when formula not in table)
    "tm_nitride": ElasticParams(
        key="tm_nitride",
        a_ang=4.35,
        poisson=0.25,
        notes="generic tm-nitride default (NbN-like a)",
    ),
    "b_doped_si": ElasticParams(
        key="b_doped_si",
        a_ang=5.43,
        poisson=0.28,
        notes="B:Si ≈ Si elastic defaults",
    ),
    "mgb2_boride": ElasticParams(
        key="mgb2_boride",
        a_ang=3.09,
        poisson=0.20,
        burgers_ang=3.09,
        notes="generic boride / MgB2 family default",
    ),
    "other": ElasticParams(
        key="other",
        a_ang=4.0,
        poisson=0.25,
        notes="generic fallback elastic params",
    ),
}


@dataclass
class CriticalThicknessResult:
    """Result of critical-thickness estimation for one film/path."""

    hc_matthews_blakeslee_nm: float | None = None
    hc_people_bean_nm: float | None = None
    method: ThicknessMethod = "heuristic fallback"
    """Primary method used for recommended thickness."""

    recommended_thickness_nm: tuple[float, float] = (5.0, 30.0)
    mismatch_fraction: float | None = None
    """|f| = |ε| used in the estimate (dimensionless)."""

    burgers_ang: float | None = None
    poisson: float | None = None
    material_key: str = ""
    elastic_source: str = ""
    notes: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def hc_primary_nm(self) -> float | None:
        if self.method == "Matthews-Blakeslee":
            return self.hc_matthews_blakeslee_nm
        if self.method == "People-Bean":
            return self.hc_people_bean_nm
        return self.hc_matthews_blakeslee_nm or self.hc_people_bean_nm


# Absolute mismatch fraction above which coherent growth is unrealistic for MB.
_HIGH_MISMATCH_F = 0.08  # 8%
_FALLBACK_HIGH_BAND = (5.0, 30.0)
_FALLBACK_LOW_BAND = (20.0, 100.0)
_MISMATCH_BAND_SPLIT_PCT = 5.0

# Membrane-transfer heuristics (process guidance only)
_MEMBRANE_DIRECT_MISMATCH_PCT = 8.0
_MEMBRANE_HC_NM = 15.0
_MEMBRANE_EFFECTIVE_MISMATCH_PCT = 6.0


def resolve_elastic_params(
    *,
    formula: str | None = None,
    material_family: str | None = None,
    film_a_ang: float | None = None,
) -> ElasticParams | None:
    """Look up elastic params by formula, then family; optionally adjust *a*.

    Returns None only when no table entry and no usable film lattice constant.
    """
    if formula:
        key = formula.replace(" ", "").replace("₂", "2")
        # Normalize common unicode / subscript forms
        for cand in (key, key.replace("0", ""), formula):
            if cand in ELASTIC_LIBRARY:
                params = ELASTIC_LIBRARY[cand]
                if film_a_ang is not None and film_a_ang > 0:
                    return ElasticParams(
                        key=params.key,
                        a_ang=float(film_a_ang),
                        poisson=params.poisson,
                        burgers_ang=(
                            float(film_a_ang) / _SQRT2
                            if params.burgers_ang is None
                            else params.burgers_ang * (float(film_a_ang) / params.a_ang)
                        ),
                        young_gpa=params.young_gpa,
                        notes=params.notes + "; a from film lattice",
                    )
                return params
        # Single-metal nitride: "NbN" style already covered; try metal+"N"
        if key.endswith("N") and len(key) <= 4 and key in ELASTIC_LIBRARY:
            return ELASTIC_LIBRARY[key]

    if material_family and material_family in ELASTIC_LIBRARY:
        params = ELASTIC_LIBRARY[material_family]
        if film_a_ang is not None and film_a_ang > 0:
            return ElasticParams(
                key=params.key,
                a_ang=float(film_a_ang),
                poisson=params.poisson,
                burgers_ang=float(film_a_ang) / _SQRT2,
                young_gpa=params.young_gpa,
                notes=params.notes + "; a from film lattice",
            )
        return params

    if film_a_ang is not None and film_a_ang > 0:
        return ElasticParams(
            key="film_lattice",
            a_ang=float(film_a_ang),
            poisson=0.25,
            notes="generic ν=0.25 with film lattice a; not DFT",
        )
    return None


def _solve_implicit_hc(
    *,
    prefactor_nm: float,
    b_nm: float,
    log_offset: float = 1.0,
    log_scale: float = 1.0,
    max_iter: int = 80,
) -> float | None:
    """Solve ``h = prefactor * (ln(log_scale * h / b) + log_offset)`` iteratively.

    Returns None if no physical fixed point (very large misfit / bad params).
    """
    if prefactor_nm <= 0 or b_nm <= 0:
        return None
    # Initial guess: prefactor * a few e-foldings
    h = max(b_nm * 2.0, prefactor_nm * 3.0)
    for _ in range(max_iter):
        arg = log_scale * h / b_nm
        if arg <= 1.0:
            # h too small for log domain — try larger seed once, else fail
            h = b_nm * 2.5 / max(log_scale, 1e-12)
            arg = log_scale * h / b_nm
            if arg <= 1.0:
                return None
        h_new = prefactor_nm * (math.log(arg) + log_offset)
        if h_new <= 0:
            return None
        if abs(h_new - h) / max(h, 1e-12) < 1e-6:
            # Cap absurdly large coherent thicknesses (near-zero mismatch)
            return float(min(h_new, 1.0e6))
        h = 0.5 * h + 0.5 * h_new
    if h > 0:
        return float(min(h, 1.0e6))
    return None


def matthews_blakeslee_hc_nm(
    mismatch_fraction: float,
    *,
    b_ang: float,
    poisson: float = 0.25,
    cos2_alpha: float = 0.25,
    cos_lambda: float = 0.5,
) -> float | None:
    """Matthews–Blakeslee critical thickness (nm) for 60° misfit dislocations.

    Uses the mechanical-equilibrium form (001, 60° dislocations)::

        h_c = [b (1 − ν cos²α) / (8 π |f| (1+ν) cos λ)] × [ln(h_c / b) + 1]

    with α = λ = 60° defaults (cos²α = 1/4, cos λ = 1/2).

    Parameters
    ----------
    mismatch_fraction:
        Absolute epitaxial misfit |f| = |ε| (dimensionless, e.g. 0.02 for 2%).
    b_ang:
        Burgers vector magnitude in Å.
    """
    f = abs(float(mismatch_fraction))
    if f < 1e-12:
        return 1.0e6  # essentially unlimited
    if f > 0.5:
        return None
    nu = float(poisson)
    if not (0.0 < nu < 0.5):
        nu = 0.25
    b_nm = float(b_ang) * 0.1  # Å → nm
    denom = 8.0 * math.pi * f * (1.0 + nu) * float(cos_lambda)
    if denom <= 0:
        return None
    prefactor = b_nm * (1.0 - nu * float(cos2_alpha)) / denom
    return _solve_implicit_hc(prefactor_nm=prefactor, b_nm=b_nm, log_offset=1.0, log_scale=1.0)


def people_bean_hc_nm(
    mismatch_fraction: float,
    *,
    b_ang: float,
    a_ang: float,
    poisson: float = 0.25,
) -> float | None:
    """People–Bean critical thickness (nm) — energy-balance / metastable bound.

    Classic form (People & Bean, Appl. Phys. Lett. 1985)::

        h_c = [(1−ν)/(1+ν)] × [1/(16 π √2)] × (b²/a) × (1/f²) × ln(h_c / b)

    Typically much larger than Matthews–Blakeslee (allows metastable strained
    layers). Reported as a secondary bound, not the primary recommend band.
    """
    f = abs(float(mismatch_fraction))
    if f < 1e-12:
        return 1.0e6
    if f > 0.5:
        return None
    nu = float(poisson)
    if not (0.0 < nu < 0.5):
        nu = 0.25
    b_nm = float(b_ang) * 0.1
    a_nm = float(a_ang) * 0.1
    if a_nm <= 0 or b_nm <= 0:
        return None
    prefactor = (
        ((1.0 - nu) / (1.0 + nu))
        * (1.0 / (16.0 * math.pi * _SQRT2))
        * (b_nm * b_nm / a_nm)
        / (f * f)
    )
    return _solve_implicit_hc(prefactor_nm=prefactor, b_nm=b_nm, log_offset=0.0, log_scale=1.0)


def _band_from_hc(
    hc_nm: float,
    *,
    mismatch_pct: float | None,
    pb_nm: float | None = None,
) -> tuple[float, float]:
    """Map critical thickness to a practical (min, max) nm recommendation.

    Matthews–Blakeslee ``h_c`` is a conservative coherent limit (often a few nm
    at percent-level mismatch). Experimental metastable films can be thicker;
    the recommended band therefore scales ``h_c`` by a mismatch-dependent factor
    and optionally soft-caps with People–Bean when PB ≫ MB.

    This is process guidance for cards — not FEM.
    """
    hc = max(float(hc_nm), 0.05)
    mag = abs(float(mismatch_pct)) if mismatch_pct is not None else 5.0

    if mag < 2.0:
        scale, cap, lo_floor = 12.0, 200.0, 10.0
    elif mag < 5.0:
        scale, cap, lo_floor = 8.0, 100.0, 5.0
    elif mag < 10.0:
        scale, cap, lo_floor = 5.0, 40.0, 2.0
    else:
        scale, cap, lo_floor = 3.0, 15.0, 1.0

    hi = min(max(hc * scale, lo_floor + 1.0), cap)
    # Soft upper bound from People–Bean only when it is a real metastable margin
    if pb_nm is not None and pb_nm > 5.0 * hc:
        hi = min(hi, max(0.25 * float(pb_nm), lo_floor + 1.0))

    lo = max(1.0, min(lo_floor, 0.25 * hi))
    if hi <= lo:
        hi = lo + 1.0
    return (round(lo, 1), round(hi, 1))


def _fallback_band(mismatch_pct: float | None) -> tuple[float, float]:
    if mismatch_pct is None:
        return _FALLBACK_HIGH_BAND
    mag = abs(float(mismatch_pct))
    if mag > 10.0:
        return (1.0, 10.0)
    if mag > _MISMATCH_BAND_SPLIT_PCT:
        return _FALLBACK_HIGH_BAND
    return _FALLBACK_LOW_BAND


def estimate_critical_thickness(
    mismatch_pct: float | None,
    *,
    formula: str | None = None,
    material_family: str | None = None,
    film_a_ang: float | None = None,
    elastic: ElasticParams | None = None,
) -> CriticalThicknessResult:
    """Estimate h_c and a recommended thickness band for one epitaxial path.

    Prefer Matthews–Blakeslee as the primary method (conservative coherent
    limit). People–Bean is computed when possible and noted as a metastable
    upper bound. Missing data → heuristic fallback band (no crash).
    """
    notes: list[str] = []
    params = elastic or resolve_elastic_params(
        formula=formula, material_family=material_family, film_a_ang=film_a_ang
    )

    if mismatch_pct is None or not math.isfinite(float(mismatch_pct)):
        band = _fallback_band(None)
        notes.append(
            "critical-thickness fallback: missing mismatch; conservative band "
            f"{band[0]:g}–{band[1]:g} nm"
        )
        return CriticalThicknessResult(
            method="heuristic fallback",
            recommended_thickness_nm=band,
            notes=notes,
            material_key=params.key if params else "",
            elastic_source=params.notes if params else "none",
            inputs={"mismatch_pct": None},
        )

    f = abs(float(mismatch_pct)) / 100.0
    if params is None:
        band = _fallback_band(mismatch_pct)
        notes.append(
            "critical-thickness fallback: missing elastic/lattice data; "
            f"mismatch-based band {band[0]:g}–{band[1]:g} nm"
        )
        return CriticalThicknessResult(
            method="heuristic fallback",
            recommended_thickness_nm=band,
            mismatch_fraction=f,
            notes=notes,
            inputs={"mismatch_pct": float(mismatch_pct)},
        )

    b = params.b_ang
    nu = params.poisson
    hc_mb = matthews_blakeslee_hc_nm(f, b_ang=b, poisson=nu)
    hc_pb = people_bean_hc_nm(f, b_ang=b, a_ang=params.a_ang, poisson=nu)

    inputs: dict[str, Any] = {
        "mismatch_pct": round(float(mismatch_pct), 4),
        "mismatch_fraction": round(f, 6),
        "burgers_ang": round(b, 4),
        "poisson": nu,
        "a_ang": round(params.a_ang, 4),
        "material_key": params.key,
        "elastic_notes": params.notes,
        "dislocation": "60deg (cos²α=0.25, cosλ=0.5)",
    }

    if hc_mb is not None and hc_mb > 0:
        band = _band_from_hc(hc_mb, mismatch_pct=mismatch_pct, pb_nm=hc_pb)
        method: ThicknessMethod = "Matthews-Blakeslee"
        notes.append(
            f"Matthews–Blakeslee h_c ≈ {hc_mb:.2f} nm "
            f"(|f|={100*f:.2f}%, b={b:.3f} Å, ν={nu:g})"
        )
        if hc_pb is not None and hc_pb > 0:
            notes.append(
                f"People–Bean (metastable) h_c ≈ {hc_pb:.1f} nm — upper bound only"
            )
        if f >= _HIGH_MISMATCH_F:
            notes.append(
                "high mismatch: coherent h_c is ultrathin; prefer buffer, "
                "ultrathin film, or membrane transfer"
            )
        return CriticalThicknessResult(
            hc_matthews_blakeslee_nm=round(hc_mb, 3),
            hc_people_bean_nm=None if hc_pb is None else round(hc_pb, 3),
            method=method,
            recommended_thickness_nm=band,
            mismatch_fraction=f,
            burgers_ang=round(b, 4),
            poisson=nu,
            material_key=params.key,
            elastic_source=params.notes,
            notes=notes,
            inputs=inputs,
        )

    # MB failed (extreme mismatch) — try PB, else fallback
    if hc_pb is not None and hc_pb > 0:
        band = _band_from_hc(hc_pb * 0.1, mismatch_pct=mismatch_pct, pb_nm=hc_pb)
        notes.append(
            f"Matthews–Blakeslee did not converge at |f|={100*f:.2f}%; "
            f"using thin guidance from People–Bean h_c ≈ {hc_pb:.2f} nm"
        )
        return CriticalThicknessResult(
            hc_matthews_blakeslee_nm=None,
            hc_people_bean_nm=round(hc_pb, 3),
            method="People-Bean",
            recommended_thickness_nm=band,
            mismatch_fraction=f,
            burgers_ang=round(b, 4),
            poisson=nu,
            material_key=params.key,
            elastic_source=params.notes,
            notes=notes,
            inputs=inputs,
        )

    band = _fallback_band(mismatch_pct)
    notes.append(
        f"critical-thickness fallback at |f|={100*f:.2f}% "
        f"(no stable MB/PB root); band {band[0]:g}–{band[1]:g} nm"
    )
    return CriticalThicknessResult(
        method="heuristic fallback",
        recommended_thickness_nm=band,
        mismatch_fraction=f,
        burgers_ang=round(b, 4),
        poisson=nu,
        material_key=params.key,
        elastic_source=params.notes,
        notes=notes,
        inputs=inputs,
    )


def membrane_transfer_heuristic(
    *,
    direct_mismatch_pct: float | None,
    path_mismatch_pct: float | None = None,
    hc_nm: float | None = None,
    path: str | None = None,
    is_multilayer: bool = False,
    material_family: str | None = None,
    chemical_flags: list[str] | None = None,
) -> tuple[bool, str]:
    """Rule-based membrane-transfer candidate flag + short note.

    **Ranking / process guidance only** — not continuum mechanics or FEM.

    Conditions (any one triggers candidate=True):
    - |direct epitaxy mismatch| ≥ 8%
    - primary h_c < 15 nm with |path mismatch| ≥ 3%
    - path effective |mismatch| ≥ 6% on a multilayer stack
    - explicit ``transfer_candidate`` chemical flag (if present)
    """
    reasons: list[str] = []
    flags = list(chemical_flags or [])

    if "transfer_candidate" in flags:
        reasons.append("path flagged transfer_candidate")

    if direct_mismatch_pct is not None and abs(direct_mismatch_pct) >= _MEMBRANE_DIRECT_MISMATCH_PCT:
        reasons.append(
            f"direct epitaxy |mismatch|={abs(direct_mismatch_pct):.1f}% "
            f"≥ {_MEMBRANE_DIRECT_MISMATCH_PCT:g}% — strain risk for thick films"
        )

    if (
        hc_nm is not None
        and hc_nm < _MEMBRANE_HC_NM
        and path_mismatch_pct is not None
        and abs(path_mismatch_pct) >= 3.0
    ):
        reasons.append(
            f"Matthews–Blakeslee h_c ≈ {hc_nm:.1f} nm < {_MEMBRANE_HC_NM:g} nm "
            "— freestanding / transferred membrane may relax strain"
        )

    if (
        is_multilayer
        and path_mismatch_pct is not None
        and abs(path_mismatch_pct) >= _MEMBRANE_EFFECTIVE_MISMATCH_PCT
    ):
        reasons.append(
            f"multilayer path still has |mismatch|={abs(path_mismatch_pct):.1f}% "
            "— consider membrane transfer as alternative to thick coherent stack"
        )

    # Known difficult families even with moderate mismatch
    if material_family in {"cuprate", "nickelate"} and (
        direct_mismatch_pct is not None and abs(direct_mismatch_pct) >= 5.0
    ):
        reasons.append(
            f"{material_family} on Si often uses buffered growth or membrane "
            "transfer rather than direct thick epitaxy"
        )

    if not reasons:
        note = (
            "membrane-transfer not indicated by current heuristics "
            "(low-moderate mismatch / adequate h_c) — guidance only, not FEM"
        )
        return False, note

    path_bit = f" (path={path})" if path else ""
    note = (
        "membrane-transfer candidate"
        + path_bit
        + ": "
        + "; ".join(reasons)
        + ". Heuristic process guidance only — not continuum simulation."
    )
    return True, note


def format_thickness_band(band: tuple[float, float] | None) -> str:
    """Human-readable thickness band for cards / CSV."""
    if not band:
        return ""
    return f"{band[0]:g}–{band[1]:g}"
