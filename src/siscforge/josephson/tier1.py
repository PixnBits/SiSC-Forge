"""P4.1 Tier-1 analytic Josephson estimates (pure functions).

Formulas, units, and ranking-only assumptions are documented in
``docs/phase4-p41-josephson-tier1.md``. Nothing here is a device-design
value. DMFT ``performance_score`` is **not** treated as a gap or Tc
unless a real gap / conventional Tc field exists.

Units
-----
* Gap Δ — **meV**
* IcRn — **mV** (Ambegaokar–Baratoff voltage product)
* Jc — **A/cm²** (ranking proxy under a documented RnA)
* Switching / EJ — **eV** (and EJ/kB in K) at ``reference_area_um2``
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from siscforge import __version__
from siscforge.models.provenance import Provenance
from siscforge.models.results import JosephsonMetrics

if TYPE_CHECKING:
    from siscforge.models.candidate import CandidateEvaluation
    from siscforge.models.config import JosephsonConfig
    from siscforge.models.results import ElectronPhononResult

# k_B in meV/K (CODATA). Δ_BCS = BCS_GAP_RATIO * k_B * Tc.
KB_MEV_PER_K = 0.08617333262145
# Weak-coupling BCS: 2Δ / k_B Tc = 3.528 → Δ / k_B Tc ≈ 1.764
BCS_GAP_RATIO = 1.764
# Magnetic flux quantum Φ0 = h/2e (Wb = V·s)
PHI0_WB = 2.067833848e-15
ELEMENTARY_CHARGE_C = 1.602176634e-19
# Default SIS-like specific resistance used only for the Jc / Ic ranking proxy.
DEFAULT_RNA_OHM_UM2 = 20.0
DEFAULT_AREA_UM2 = 1.0

RANKING_ONLY_CAVEAT = (
    "APPROXIMATE / RANKING ONLY — Tier-1 analytic Josephson estimates "
    "(Ambegaokar–Baratoff + documented geometry). Not a device-design value."
)

# performance_score sources that are conventional Tc-like (kelvin), not a gap.
_CONVENTIONAL_TC_SOURCES = frozenset({"epw", "mock", "surrogate"})
# Never treat these as Δ or as a Tc for BCS fallback.
_NOT_A_GAP_SOURCES = frozenset({"dmft_pairing", "dmft_pairing_mock"})

_GAP_MEV_KEYS = (
    "gap_meV",
    "delta_meV",
    "Delta_meV",
    "eliashberg_gap_meV",
    "gap_mev",
)
_GAP_EV_KEYS = ("gap_eV", "delta_eV", "Delta_eV", "eliashberg_gap_eV")


@dataclass(frozen=True)
class GapExtraction:
    """Outcome of :func:`extract_gap`."""

    gap_meV: float | None
    source: str | None
    tc_used_K: float | None
    tc_source: str | None
    note: str
    reason: str
    usable: bool


def _finite_positive(value: object) -> float | None:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x <= 0.0:
        return None
    return x


def _lookup_gap_meV(mapping: dict[str, Any] | None) -> float | None:
    if not mapping:
        return None
    for key in _GAP_MEV_KEYS:
        got = _finite_positive(mapping.get(key))
        if got is not None:
            return got
    for key in _GAP_EV_KEYS:
        got = _finite_positive(mapping.get(key))
        if got is not None:
            return got * 1000.0
    return None


def bcs_gap_meV(tc_K: float, *, ratio: float = BCS_GAP_RATIO) -> float:
    """Weak-coupling BCS gap Δ = *ratio* × k_B × Tc, in meV.

    Default *ratio* is 1.764 (2Δ/k_B Tc = 3.528). This is a ranking
    fallback when no Eliashberg / explicit gap is present.
    """
    tc = _finite_positive(tc_K)
    if tc is None:
        raise ValueError("tc_K must be a finite positive temperature (K)")
    r = _finite_positive(ratio)
    if r is None:
        raise ValueError("BCS gap ratio must be a finite positive number")
    return float(r * KB_MEV_PER_K * tc)


def ambegaokar_baratoff_icrn_mV(
    gap_meV: float,
    *,
    temperature_K: float | None = None,
) -> float:
    """Ambegaokar–Baratoff IcRn product in mV.

    At T = 0:  IcRn = (π Δ) / (2e)  →  (π/2) × Δ[meV]  millivolts.

    At finite T: multiply by tanh(Δ / 2 k_B T). ``temperature_K is None``
    means the T = 0 (ranking) limit.

    Δ is **not** temperature-dependent here. When *T* ≥ Tc the caller
    (:func:`estimate_tier1`) zeros transport proxies; this function is
    the bare AB tunnel formula and will still return a finite IcRn.
    """
    gap = _finite_positive(gap_meV)
    if gap is None:
        raise ValueError("gap_meV must be a finite positive gap")
    icrn = (math.pi / 2.0) * gap
    if temperature_K is None:
        return float(icrn)
    t = _finite_positive(temperature_K)
    if t is None:
        return float(icrn)
    arg = gap / (2.0 * KB_MEV_PER_K * t)
    # Overflow-safe: tanh(x) → 1 for large x
    if arg > 40.0:
        return float(icrn)
    return float(icrn * math.tanh(arg))


def jc_proxy_A_per_cm2(
    icrn_mV: float,
    *,
    rna_ohm_um2: float = DEFAULT_RNA_OHM_UM2,
) -> float:
    """Ranking-only Jc = IcRn / (Rn A) under a documented specific resistance.

    ``rna_ohm_um2`` is Ω·μm².  1 Ω·μm² = 10⁻⁸ Ω·cm², so

        Jc [A/cm²] = (IcRn [mV] × 10⁻³) / (RnA [Ω·μm²] × 10⁻⁸)
                   = IcRn [mV] / RnA [Ω·μm²] × 10⁵
    """
    icrn = _finite_positive(icrn_mV)
    rna = _finite_positive(rna_ohm_um2)
    if icrn is None or rna is None:
        raise ValueError("icrn_mV and rna_ohm_um2 must be finite and positive")
    return float(icrn / rna * 1.0e5)


def switching_energy_eV(
    jc_A_per_cm2: float,
    *,
    reference_area_um2: float = DEFAULT_AREA_UM2,
) -> tuple[float, float, float]:
    """EJ-style switching proxy at a reference junction area.

    Returns ``(EJ_eV, EJ_K, Ic_uA)`` with

        Ic = Jc × A
        EJ = Φ0 Ic / 2π

    ``reference_area_um2`` is a **ranking geometry**, not a fabricated device.
    """
    jc = _finite_positive(jc_A_per_cm2)
    area = _finite_positive(reference_area_um2)
    if jc is None or area is None:
        raise ValueError("jc and reference_area_um2 must be finite and positive")
    # 1 μm² = 1e-8 cm² → Ic [A] = Jc [A/cm²] × area [μm²] × 1e-8
    ic_A = jc * area * 1.0e-8
    ej_J = PHI0_WB * ic_A / (2.0 * math.pi)
    ej_eV = ej_J / ELEMENTARY_CHARGE_C
    kb_eV = KB_MEV_PER_K * 1.0e-3
    ej_K = ej_eV / kb_eV
    ic_uA = ic_A * 1.0e6
    return float(ej_eV), float(ej_K), float(ic_uA)


def resolve_tc_K(
    evaluation: CandidateEvaluation,
) -> tuple[float | None, str | None]:
    """Return a conventional Tc (K) suitable for BCS fallback.

    Prefers ``ElectronPhononResult.best_tc_K()`` (Eliashberg, else
    Allen–Dynes). Falls back to ``performance_score`` only when the
    source is a conventional Tc-like tag (``epw`` / ``mock`` /
    ``surrogate``). **Never** uses ``dmft_pairing`` scores as Tc.
    """
    eph = getattr(evaluation, "electron_phonon", None)
    if eph is not None and hasattr(eph, "best_tc_K"):
        best = eph.best_tc_K()
        tc = _finite_positive(best)
        if tc is not None:
            if getattr(eph, "Tc_eliashberg", None) is not None and _finite_positive(
                eph.Tc_eliashberg
            ):
                return tc, "eliashberg"
            if getattr(eph, "Tc_allen_dynes", None) is not None:
                return tc, "allen_dynes"
            return tc, "electron_phonon"

    source = (getattr(evaluation, "performance_score_source", None) or "").strip()
    if source in _NOT_A_GAP_SOURCES:
        return None, None
    if source in _CONVENTIONAL_TC_SOURCES or source == "":
        # Empty source + a finite score is only accepted when e-ph is
        # absent *and* there is no DMFT pairing object pretending to be Tc.
        if source == "":
            dmft = getattr(evaluation, "dmft", None)
            if dmft is not None and getattr(dmft, "leading_pairing_eigenvalue", None) is not None:
                return None, None
        tc = _finite_positive(getattr(evaluation, "performance_score", None))
        if tc is not None:
            tag = source or "performance_score"
            return tc, tag
    return None, None


def extract_gap(
    evaluation: CandidateEvaluation,
    *,
    bcs_ratio: float = BCS_GAP_RATIO,
) -> GapExtraction:
    """Gap from Eliashberg / explicit fields, else BCS-like Δ from Tc.

    Precedence:

    1. ``ElectronPhononResult.gap_meV``
    2. ``alpha2F_summary`` / ``raw`` keys (``gap_meV``, ``delta_meV``, …)
    3. BCS fallback Δ = *bcs_ratio* × k_B × Tc when a conventional Tc exists

    DMFT pairing ``performance_score`` is never used as Δ.
    """
    eph: ElectronPhononResult | None = getattr(evaluation, "electron_phonon", None)
    tc, tc_source = resolve_tc_K(evaluation)

    if eph is not None:
        direct = _finite_positive(getattr(eph, "gap_meV", None))
        if direct is not None:
            return GapExtraction(
                gap_meV=direct,
                source="eliashberg" if _looks_eliashberg(eph) else "explicit",
                tc_used_K=tc,
                tc_source=tc_source,
                note="gap taken from ElectronPhononResult.gap_meV",
                reason="ok",
                usable=True,
            )
        from_summary = _lookup_gap_meV(getattr(eph, "alpha2F_summary", None) or {})
        if from_summary is not None:
            return GapExtraction(
                gap_meV=from_summary,
                source="eliashberg" if _looks_eliashberg(eph) else "explicit",
                tc_used_K=tc,
                tc_source=tc_source,
                note="gap taken from electron_phonon.alpha2F_summary",
                reason="ok",
                usable=True,
            )
        from_raw = _lookup_gap_meV(getattr(eph, "raw", None) or {})
        if from_raw is not None:
            return GapExtraction(
                gap_meV=from_raw,
                source="eliashberg" if _looks_eliashberg(eph) else "explicit",
                tc_used_K=tc,
                tc_source=tc_source,
                note="gap taken from electron_phonon.raw",
                reason="ok",
                usable=True,
            )

    if tc is None:
        return GapExtraction(
            gap_meV=None,
            source=None,
            tc_used_K=None,
            tc_source=None,
            note="no Eliashberg/explicit gap and no conventional Tc for BCS fallback",
            reason="insufficient_input",
            usable=False,
        )

    try:
        gap = bcs_gap_meV(tc, ratio=bcs_ratio)
    except ValueError:
        return GapExtraction(
            gap_meV=None,
            source=None,
            tc_used_K=tc,
            tc_source=tc_source,
            note="BCS fallback failed (invalid Tc or ratio)",
            reason="insufficient_input",
            usable=False,
        )
    return GapExtraction(
        gap_meV=gap,
        source="bcs_from_tc",
        tc_used_K=tc,
        tc_source=tc_source,
        note=f"BCS-like Δ = {bcs_ratio:g} k_B Tc (Tc={tc:g} K via {tc_source})",
        reason="ok",
        usable=True,
    )


def _looks_eliashberg(eph: object) -> bool:
    if getattr(eph, "Tc_eliashberg", None) is not None:
        return True
    summary = getattr(eph, "alpha2F_summary", None) or {}
    raw = getattr(eph, "raw", None) or {}
    for blob in (summary, raw):
        if not isinstance(blob, dict):
            continue
        for key in ("eliashberg_gap_meV", "eliashberg_gap_eV", "tc_model"):
            if key in blob:
                return True
    return False


def _quality_tag(evaluation: CandidateEvaluation) -> str:
    eph = getattr(evaluation, "electron_phonon", None)
    if eph is not None:
        tag = getattr(eph, "quality_tag", None) or ""
        if tag:
            return str(tag)
        status = (getattr(eph, "status", None) or "").lower()
        if status == "mock":
            return "mock"
    src = (getattr(evaluation, "performance_score_source", None) or "").lower()
    if src == "mock":
        return "mock"
    return "screening"


def _ratio_for(evaluation: CandidateEvaluation, config: JosephsonConfig) -> float:
    default = float(getattr(config, "bcs_gap_ratio", BCS_GAP_RATIO) or BCS_GAP_RATIO)
    family = getattr(getattr(evaluation, "candidate", None), "material_family", None)
    overrides = getattr(config, "family_gap_ratios", None) or {}
    if family and family in overrides:
        got = _finite_positive(overrides[family])
        if got is not None:
            return got
    return default


def estimate_tier1(
    evaluation: CandidateEvaluation,
    config: JosephsonConfig | None = None,
) -> JosephsonMetrics:
    """Build a caveated :class:`JosephsonMetrics` for one evaluation.

    Never raises on missing science inputs — returns ``status=skipped``
    instead. Callers must not invoke this when the module is disabled.
    """
    from siscforge.models.config import JosephsonConfig as _JC

    cfg = config or _JC()
    ratio = _ratio_for(evaluation, cfg)
    extracted = extract_gap(evaluation, bcs_ratio=ratio)
    notes = [RANKING_ONLY_CAVEAT]
    assumptions: dict[str, Any] = {
        "model_tier": getattr(cfg, "model_tier", None) or "analytic_AB",
        "assume_SIS": bool(getattr(cfg, "assume_SIS", True)),
        "bcs_gap_ratio": ratio,
        "rna_ohm_um2": float(getattr(cfg, "rna_ohm_um2", DEFAULT_RNA_OHM_UM2)),
        "reference_area_um2": float(
            getattr(cfg, "reference_area_um2", DEFAULT_AREA_UM2)
        ),
        "temperature_K": getattr(cfg, "temperature_K", None),
        "geometry": "ranking reference — not a fabricated layout",
    }
    formula_tags = [
        "tier1_analytic",
        "ambegaokar_baratoff",
        "approximate_ranking_only",
    ]
    if extracted.usable and extracted.gap_meV is not None:
        if extracted.source == "bcs_from_tc":
            formula_tags.append("bcs_gap_fallback")
        else:
            formula_tags.append("explicit_or_eliashberg_gap")

    if not extracted.usable or extracted.gap_meV is None:
        notes.append(extracted.note or "insufficient gap / Tc inputs")
        return JosephsonMetrics(
            approximate=True,
            status="skipped",
            method="tier1_analytic_ab",
            model_tier=str(getattr(cfg, "model_tier", None) or "analytic_AB"),
            quality_tag=_quality_tag(evaluation),  # type: ignore[arg-type]
            gap_source=extracted.source,
            tc_used_K=extracted.tc_used_K,
            tc_source=extracted.tc_source,
            formula_tags=formula_tags,
            notes="; ".join(notes),
            assumptions=assumptions,
            raw={"reason": extracted.reason, "extract_note": extracted.note},
            provenance=Provenance(
                source="siscforge.josephson.tier1",
                software={"siscforge": __version__},
                notes=RANKING_ONLY_CAVEAT,
            ),
        )

    gap = float(extracted.gap_meV)
    t_k = getattr(cfg, "temperature_K", None)
    tc_used = extracted.tc_used_K
    t_ge_tc = (
        t_k is not None
        and tc_used is not None
        and math.isfinite(float(t_k))
        and float(t_k) >= float(tc_used)
    )
    rna = float(getattr(cfg, "rna_ohm_um2", DEFAULT_RNA_OHM_UM2))
    area = float(getattr(cfg, "reference_area_um2", DEFAULT_AREA_UM2))
    try:
        if t_ge_tc:
            # Fixed-Δ AB would stay finite above Tc. Zero transport instead.
            icrn = 0.0
            jc = 0.0
            ej_eV = 0.0
            ej_K = 0.0
            ic_uA = 0.0
            notes.append(
                f"temperature_K={float(t_k):g} K ≥ tc_used_K={float(tc_used):g} K — "
                "Δ is T-independent in Tier-1; IcRn/Jc/EJ forced to 0 "
                "(gap does not close above Tc in this model)"
            )
            formula_tags.append("t_ge_tc_transport_zeroed")
        else:
            icrn = ambegaokar_baratoff_icrn_mV(gap, temperature_K=t_k)
            jc = jc_proxy_A_per_cm2(icrn, rna_ohm_um2=rna)
            ej_eV, ej_K, ic_uA = switching_energy_eV(jc, reference_area_um2=area)
    except (TypeError, ValueError) as exc:
        notes.append(f"analytic estimate failed: {exc}")
        return JosephsonMetrics(
            approximate=True,
            status="skipped",
            method="tier1_analytic_ab",
            model_tier=str(getattr(cfg, "model_tier", None) or "analytic_AB"),
            quality_tag=_quality_tag(evaluation),  # type: ignore[arg-type]
            gap_meV=gap,
            gap_source=extracted.source,
            tc_used_K=extracted.tc_used_K,
            tc_source=extracted.tc_source,
            formula_tags=formula_tags,
            notes="; ".join(notes),
            assumptions=assumptions,
            raw={"reason": "estimate_failed", "error": str(exc)},
            provenance=Provenance(
                source="siscforge.josephson.tier1",
                software={"siscforge": __version__},
                notes=RANKING_ONLY_CAVEAT,
            ),
        )

    if extracted.note:
        notes.append(extracted.note)
    if not t_ge_tc:
        notes.append(
            f"IcRn=(πΔ/2e)tanh(Δ/2kT); Jc=IcRn/RnA with RnA={rna:g} Ω·μm²; "
            f"EJ=Φ0 Ic/2π at A={area:g} μm²"
        )
    family = getattr(getattr(evaluation, "candidate", None), "material_family", None)
    if family == "mgb2_boride":
        notes.append(
            "MgB2 is two-gap; this estimate uses an isotropic Δ "
            "(explicit or BCS-from-isotropic-Tc) — ranking only"
        )
        formula_tags.append("isotropic_two_gap_average")

    return JosephsonMetrics(
        approximate=True,
        status="ok",
        method="tier1_analytic_ab",
        model_tier=str(getattr(cfg, "model_tier", None) or "analytic_AB"),
        quality_tag=_quality_tag(evaluation),  # type: ignore[arg-type]
        gap_meV=gap,
        gap_source=extracted.source,
        tc_used_K=extracted.tc_used_K,
        tc_source=extracted.tc_source,
        icrn_mV=icrn,
        jc_A_per_cm2=jc,
        switching_energy_eV=ej_eV,
        ej_K=ej_K,
        ic_uA=ic_uA,
        reference_area_um2=area,
        rna_ohm_um2=rna,
        temperature_K=t_k,
        formula_tags=formula_tags,
        notes="; ".join(notes),
        assumptions=assumptions,
        raw={
            "reason": extracted.reason,
            "bcs_gap_ratio": ratio,
            "extract_note": extracted.note,
            "t_ge_tc": t_ge_tc,
        },
        provenance=Provenance(
            source="siscforge.josephson.tier1",
            software={"siscforge": __version__},
            parameters={
                "model_tier": getattr(cfg, "model_tier", None),
                "rna_ohm_um2": rna,
                "reference_area_um2": area,
                "temperature_K": t_k,
                "bcs_gap_ratio": ratio,
            },
            notes=RANKING_ONLY_CAVEAT,
        ),
    )
