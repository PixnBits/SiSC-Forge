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

    notes: str = ""

    def effective_burgers(self) -> float:
        if self.burgers_ang is not None:
            return float(self.burgers_ang)
        return float(self.a_ang) / _SQRT2


# Priority: transition-metal nitrides (rocksalt), then Si, MgB2, simple oxides.
ELASTIC_LIBRARY: dict[str, ElasticParams] = {
    # Rocksalt nitrides (a from nitride lattice tables; ν ~0.2–0.3 literature)
    "NbN": ElasticParams("NbN", a_ang=4.392, poisson=0.25, notes="rocksalt NbN; ν~0.25"),
    "TiN": ElasticParams("TiN", a_ang=4.242, poisson=0.22, notes="rocksalt TiN"),
    "ZrN": ElasticParams("ZrN", a_ang=4.577, poisson=0.25, notes="rocksalt ZrN"),
    "HfN": ElasticParams("HfN", a_ang=4.524, poisson=0.25, notes="rocksalt HfN"),
    "TaN": ElasticParams("TaN", a_ang=4.33, poisson=0.25, notes="approx rocksalt TaN"),
    "VN": ElasticParams("VN", a_ang=4.14, poisson=0.25, notes="rocksalt VN"),
    "CrN": ElasticParams("CrN", a_ang=4.14, poisson=0.25, notes="rocksalt CrN"),
    # Silicon
    "Si": ElasticParams("Si", a_ang=5.431, poisson=0.28, notes="diamond Si"),
    # MgB2 (hexagonal; use effective in-plane a)
    "MgB2": ElasticParams("MgB2", a_ang=3.086, poisson=0.20, notes="hex a; effective cubic proxy"),
    # Simple oxides / buffers sometimes used
    "MgO": ElasticParams("MgO", a_ang=4.212, poisson=0.18, notes="rocksalt MgO"),
    "AlN": ElasticParams("AlN", a_ang=3.11, poisson=0.25, notes="wurtzite a; proxy"),
}


def _lookup_elastic(
    formula: str | None = None,
    material_family: str | None = None,
    film_a_ang: float | None = None,
) -> ElasticParams | None:
    """Best-effort elastic params from formula / family / lattice."""
    if formula:
        f = formula.replace(" ", "")
        if f in ELASTIC_LIBRARY:
            return ELASTIC_LIBRARY[f]
        # simple binary match e.g. Nb0.5Ti0.5N → try NbN / TiN average later
        for key, params in ELASTIC_LIBRARY.items():
            if key in f or f.startswith(key):
                return params
    if material_family == "tm_nitride" and film_a_ang is not None:
        return ElasticParams(
            key="tm_nitride_generic",
            a_ang=float(film_a_ang),
            poisson=0.25,
            notes="generic tm-nitride ν=0.25 with film lattice a",
        )
    if film_a_ang is not None:
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
    # Exhausted without converging — no physical fixed point (e.g. extreme misfit).
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

        h_c = (b / (8 π f (1-ν))) * (1-ν cos²α) * (ln(h_c / b) + 1)

    with typical geometric factors cos²α ≈ 0.25, cos λ ≈ 0.5.
    """
    f = abs(float(mismatch_fraction))
    if f < 1e-8:
        return 1.0e6  # effectively unlimited
    b_nm = float(b_ang) * 0.1
    nu = float(poisson)
    # prefactor = b * (1 - ν cos²α) / (8 π f (1 - ν))
    prefactor = (b_nm * (1.0 - nu * cos2_alpha)) / (8.0 * math.pi * f * max(1.0 - nu, 1e-6))
    return _solve_implicit_hc(prefactor_nm=prefactor, b_nm=b_nm, log_offset=1.0, log_scale=1.0)


def people_bean_hc_nm(
    mismatch_fraction: float,
    *,
    b_ang: float,
    poisson: float = 0.25,
) -> float | None:
    """People–Bean (energy-balance) critical thickness (nm).

    Metastable bound often larger than Matthews–Blakeslee::

        h_c = (1 - ν) / (1 + ν) * (b / (16 π √2 f²)) * ln(h_c / b)
    """
    f = abs(float(mismatch_fraction))
    if f < 1e-8:
        return 1.0e6
    b_nm = float(b_ang) * 0.1
    nu = float(poisson)
    prefactor = ((1.0 - nu) / (1.0 + nu)) * (b_nm / (16.0 * math.pi * math.sqrt(2.0) * f * f))
    return _solve_implicit_hc(prefactor_nm=prefactor, b_nm=b_nm, log_offset=0.0, log_scale=1.0)


def _band_from_hc(hc_nm: float | None, mismatch_pct: float | None) -> float:
    """Map h_c (or mismatch) to a recommended thickness band (nm)."""
    if hc_nm is not None and hc_nm > 0:
        # Recommend ~0.5–0.7 of coherent limit for process margin
        return float(min(max(hc_nm * 0.6, 2.0), 200.0))
    # Fallback bands from |mismatch| only
    if mismatch_pct is None:
        return 20.0
    m = abs(float(mismatch_pct))
    if m < 1.0:
        return 80.0
    if m < 3.0:
        return 40.0
    if m < 6.0:
        return 15.0
    if m < 10.0:
        return 8.0
    return 5.0


@dataclass
class CriticalThicknessResult:
    recommended_thickness_nm: float
    hc_primary_nm: float | None = None
    hc_people_bean_nm: float | None = None
    method: ThicknessMethod = "heuristic fallback"
    inputs: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def estimate_critical_thickness(
    mismatch_pct: float | None,
    *,
    formula: str | None = None,
    material_family: str | None = None,
    film_a_ang: float | None = None,
) -> CriticalThicknessResult:
    """Estimate recommended thickness and report MB / PB critical thicknesses.

    Primary method is Matthews–Blakeslee when a physical root exists; People–Bean
    is reported as a secondary metastable bound. Missing data → conservative band.
    """
    notes: list[str] = []
    inputs: dict[str, Any] = {}
    if mismatch_pct is not None:
        inputs["mismatch_pct"] = float(mismatch_pct)
        inputs["mismatch_fraction"] = abs(float(mismatch_pct)) / 100.0

    elastic = _lookup_elastic(formula, material_family, film_a_ang)
    if elastic is not None:
        inputs["elastic_key"] = elastic.key
        inputs["a_ang"] = elastic.a_ang
        inputs["poisson"] = elastic.poisson
        inputs["burgers_ang"] = elastic.effective_burgers()
        if elastic.notes:
            notes.append(f"elastic: {elastic.notes}")

    hc_mb: float | None = None
    hc_pb: float | None = None
    method: ThicknessMethod = "heuristic fallback"

    if mismatch_pct is not None and elastic is not None:
        f = abs(float(mismatch_pct)) / 100.0
        b = elastic.effective_burgers()
        hc_mb = matthews_blakeslee_hc_nm(f, b_ang=b, poisson=elastic.poisson)
        hc_pb = people_bean_hc_nm(f, b_ang=b, poisson=elastic.poisson)
        if hc_mb is not None:
            method = "Matthews-Blakeslee"
            notes.append(f"Matthews–Blakeslee h_c ≈ {hc_mb:.2f} nm")
        elif hc_pb is not None:
            method = "People-Bean"
            notes.append(f"People–Bean h_c ≈ {hc_pb:.2f} nm (MB had no fixed point)")
        else:
            notes.append("no physical MB/PB fixed point; using mismatch band")
    elif mismatch_pct is None:
        notes.append("no mismatch data; thickness from conservative default band")
    else:
        notes.append("missing elastic params; thickness from mismatch band")

    rec = _band_from_hc(hc_mb if hc_mb is not None else hc_pb, mismatch_pct)
    if method == "heuristic fallback":
        notes.append(f"recommended thickness band ≈ {rec:.1f} nm (fallback)")
    else:
        notes.append(f"recommended thickness ≈ {rec:.1f} nm (~0.6 × h_c)")

    return CriticalThicknessResult(
        recommended_thickness_nm=rec,
        hc_primary_nm=hc_mb if hc_mb is not None else hc_pb,
        hc_people_bean_nm=hc_pb,
        method=method,
        inputs=inputs,
        notes=notes,
    )


def membrane_transfer_heuristic(
    *,
    direct_mismatch_pct: float | None = None,
    path_mismatch_pct: float | None = None,
    hc_nm: float | None = None,
    path: str | None = None,
    is_multilayer: bool = False,
    material_family: str | None = None,
    chemical_flags: list[str] | None = None,
) -> tuple[bool, str]:
    """Rule-based membrane-transfer candidate flag + short note.

    Triggers (any):
    - direct |mismatch| ≥ 8%
    - path effective |mismatch| ≥ 6% (multilayer soft-penalty path)
    - h_c < 15 nm (when available)

    Returns (candidate_flag, note). Note is always populated for auditability.
    """
    reasons: list[str] = []
    candidate = False

    dmm = abs(float(direct_mismatch_pct)) if direct_mismatch_pct is not None else None
    pmm = abs(float(path_mismatch_pct)) if path_mismatch_pct is not None else None

    if dmm is not None and dmm >= 8.0:
        candidate = True
        reasons.append(f"high direct mismatch {dmm:.1f}% ≥ 8%")
    if pmm is not None and pmm >= 6.0:
        candidate = True
        reasons.append(f"path effective |mismatch| {pmm:.1f}% ≥ 6%")
    if hc_nm is not None and hc_nm < 15.0:
        candidate = True
        reasons.append(f"low critical thickness h_c ≈ {hc_nm:.1f} nm < 15 nm")

    if candidate:
        note = "membrane-transfer candidate: " + "; ".join(reasons)
        if path:
            note += f" (via {path})"
        if is_multilayer:
            note += "; multilayer residual strain may favour transfer"
        return True, note

    # Non-candidate rationale (always export for cards/CSV)
    bits: list[str] = []
    if dmm is not None:
        bits.append(f"direct |mismatch| {dmm:.1f}%")
    if pmm is not None:
        bits.append(f"path |mismatch| {pmm:.1f}%")
    if hc_nm is not None:
        bits.append(f"h_c ≈ {hc_nm:.1f} nm")
    if not bits:
        bits.append("insufficient mismatch/h_c data")
    note = "not a membrane-transfer candidate (" + ", ".join(bits) + ")"
    return False, note
