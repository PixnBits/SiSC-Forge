"""Campaign configuration models (YAML-loadable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import math
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class QualityConfig(BaseModel):
    lambda_suspect_above: float = Field(default=3.0, ge=0.0)
    lambda_unreliable_above: float = Field(default=8.0, ge=0.0)
    min_frequency_cm1_soft: float = Field(default=50.0)
    imaginary_modes_unreliable: bool = True
    suspect_performance_penalty: float = Field(default=0.45, ge=0.0, le=1.0)
    unreliable_performance_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    unreliable_zero_performance: bool = True
    hard_zero_screening_high_lambda: bool = Field(
        default=True,
        description=(
            "When wannier_random_proj or coarse_grids co-occurs with "
            "high_lambda / extreme_lambda, force the performance contribution "
            "to 0 (issue #44). Soft multiplicative penalties still apply to "
            "milder cases. Set false to restore multiply-only behaviour."
        ),
    )
    prefer_higher_quality_tier: bool = True
    version: str = "0.1"


class SiFeasibilityWeights(BaseModel):
    """YAML-overridable component weights for the Silicon Feasibility Score.

    Defaults match COMPONENT_WEIGHTS. Non-finite values are rejected at config load.
    """

    lattice_mismatch: float = Field(default=0.35, ge=0.0)
    thermal_budget: float = Field(default=0.20, ge=0.0)
    chemical_compatibility: float = Field(default=0.20, ge=0.0)
    buffer_availability: float = Field(default=0.10, ge=0.0)
    process_maturity: float = Field(default=0.15, ge=0.0)

    @field_validator(
        "lattice_mismatch",
        "thermal_budget",
        "chemical_compatibility",
        "buffer_availability",
        "process_maturity",
    )
    @classmethod
    def _finite_non_negative(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Si-feasibility weight must be finite (got NaN or ±∞)")
        return v

    def as_dict(self) -> dict[str, float]:
        return {
            "lattice_mismatch": float(self.lattice_mismatch),
            "thermal_budget": float(self.thermal_budget),
            "chemical_compatibility": float(self.chemical_compatibility),
            "buffer_availability": float(self.buffer_availability),
            "process_maturity": float(self.process_maturity),
        }


class SiFeasibilityConfig(BaseModel):
    weights: SiFeasibilityWeights = Field(default_factory=SiFeasibilityWeights)
    cmos_limit_c: float = Field(default=450.0, ge=0.0)


class RankingConfig(BaseModel):
    """Multi-objective ranking weights and policy (YAML-overridable).

    **Axes (each mapped to 0–100 before weighting):**

    * ``performance`` — ``performance_score`` (Tc-like, K) normalized as
      ``min(100, max(0, score / performance_ceiling_K * 100))``.
      Default ceiling is **40 K** (legacy Phase-0 convention).
    * ``si_feasibility`` — ``si_feasibility.total`` (already 0–100).
    * ``uncertainty`` (optional) — when ``uncertainty_weight > 0`` and a
      surrogate/performance uncertainty in ``[0, 1]`` is present on the
      evaluation, contributes **certainty** ``(1 − u) × 100`` so lower
      uncertainty ranks better. Missing uncertainty drops that weight from
      the denominator (no invented neutral term).

    Weights are re-normalized by their sum. Defaults
    (``performance=0.6``, ``si_feasibility=0.4``, ``uncertainty=0.0``)
    preserve pre-P2.4 composite ordering when omitted from YAML.

    Trust-layer multipliers, ``prefer_dynamically_stable``, and
    ``prefer_low_hull`` are applied **after** the weighted blend and are
    independent of these weights.

    ``performance_precedence`` (P3.4) selects how the headline
    ``performance_score`` is filled *before* ranking: trusted EPW Tc,
    else DMFT pairing proxy, else existing mock/surrogate. Ranking itself
    stays family-agnostic.
    """

    performance_weight: float = Field(default=0.6, ge=0.0)
    si_feasibility_weight: float = Field(default=0.4, ge=0.0)
    uncertainty_weight: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Weight for certainty (1 − surrogate uncertainty). "
            "Default 0 disables the term (backward compatible)."
        ),
    )
    performance_ceiling_K: float = Field(
        default=40.0,
        gt=0.0,
        description="Tc-like ceiling (K) used to normalize performance_score to 0–100.",
    )
    pareto_enabled: bool = Field(
        default=True,
        description=(
            "Mark non-dominated candidates on performance vs Si-feasibility "
            "(and certainty when uncertainty_weight > 0)."
        ),
    )
    prefer_dynamically_stable: bool = True
    prefer_low_hull: bool = True
    quality: QualityConfig = Field(default_factory=QualityConfig)
    performance_precedence: Literal[
        "epw_then_dmft",
        "dmft_then_epw",
        "epw_only",
        "dmft_only",
    ] = Field(
        default="epw_then_dmft",
        description=(
            "P3.4 headline-score precedence. Default: trusted EPW Eliashberg/"
            "Allen–Dynes Tc, else DMFT pairing proxy, else existing "
            "mock/surrogate. Conventional campaigns without DMFT are unchanged."
        ),
    )
    version: str = "0.2"

    @field_validator(
        "performance_weight",
        "si_feasibility_weight",
        "uncertainty_weight",
        "performance_ceiling_K",
    )
    @classmethod
    def _finite_weight(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("ranking weight/ceiling must be finite (got NaN or ±∞)")
        return v

    def active_weights(self) -> dict[str, float]:
        """Return the weight vector + ceiling used for provenance export."""
        return {
            "performance": float(self.performance_weight),
            "si_feasibility": float(self.si_feasibility_weight),
            "uncertainty": float(self.uncertainty_weight),
            "performance_ceiling_K": float(self.performance_ceiling_K),
        }


class FormationFilterConfig(BaseModel):
    enabled: bool = True
    max_e_hull_eV_per_atom: float = Field(default=0.25, ge=0.0)
    max_strain_magnitude: float | None = Field(default=0.05, ge=0.0)
    prefer_families: list[str] = Field(default_factory=lambda: ["tm_nitride", "b_doped_si", "mgb2_boride"])
    keep_top_n: int | None = Field(default=None, ge=1)
    version: str = "0.1-heuristic"


class TcLambdaSurrogateConfig(BaseModel):
    enabled: bool = False
    mu_star: float = Field(default=0.10, ge=0.0, le=0.3)
    min_predicted_tc_K: float | None = Field(default=None, ge=0.0)
    max_uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)
    keep_top_n: int | None = Field(default=None, ge=1)
    use_for_ranking_when_no_epw: bool = True
    version: str = "0.1-family-heuristic"


class SurrogateConfig(BaseModel):
    tc_lambda: TcLambdaSurrogateConfig = Field(default_factory=TcLambdaSurrogateConfig)


class ActiveLearningWeights(BaseModel):
    uncertainty: float = Field(default=0.4, ge=0.0)
    predicted_tc: float = Field(default=0.3, ge=0.0)
    si_feasibility: float = Field(default=0.3, ge=0.0)
    hull_penalty: float = Field(default=0.1, ge=0.0)


class ActiveLearningPoolQuotas(BaseModel):
    """Max fraction of ``max_epw_jobs`` reserved per pool (``separate`` mode).

    Slots are ``floor(fraction × k)``. Leftover batch slots fill by global
    acquisition score so an empty pool cannot starve a present one.
    Unused when ``pool_mode`` is ``off`` or ``joint``.

    Fractions may sum to more than 1. Reservation then over-subscribes the
    batch and ``select_with_quotas`` truncates the selected set to *k* by
    global score — a high-scoring pool can still take every slot. Prefer
    fractions that sum to ≤ 1 when you want reserved representation.
    """

    conventional: float = Field(default=0.5, ge=0.0, le=1.0)
    unconventional: float = Field(default=0.5, ge=0.0, le=1.0)
    unknown: float = Field(default=0.0, ge=0.0, le=1.0)


class ActiveLearningConfig(BaseModel):
    enabled: bool = False
    strategy: Literal["uncertainty_si_tc"] = "uncertainty_si_tc"
    max_epw_jobs: int = Field(default=5, ge=1)
    weights: ActiveLearningWeights = Field(default_factory=ActiveLearningWeights)
    tc_ceiling_K: float = Field(default=40.0, gt=0.0)
    evaluate_deferred_with_surrogate: bool = True
    al_root: str | None = None
    version: str = "0.2-flywheel"
    # --- P3.6 mixed conventional / unconventional pools ---
    pool_mode: Literal["off", "joint", "separate"] = Field(
        default="off",
        description=(
            "P3.6 acquisition pools. ``off`` (default) is pre-P3.6 top-k on "
            "the family-mean surrogate. ``joint`` is one ranked list using "
            "common performance_score + uncertainty when present. "
            "``separate`` keeps per-pool reserved quotas so one pathway "
            "cannot starve the other."
        ),
    )
    pool_quotas: ActiveLearningPoolQuotas = Field(
        default_factory=ActiveLearningPoolQuotas,
        description="Per-pool max fractions of max_epw_jobs (separate mode).",
    )


class CandidateSpec(BaseModel):
    formula: str
    in_plane_strain: float = 0.0
    substrate: str = "Si(001)"
    material_family: Literal["tm_nitride", "b_doped_si", "mgb2_boride", "nickelate", "cuprate", "other"] = "tm_nitride"
    candidate_id: str | None = None
    structure_cif: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnumerationConfig(BaseModel):
    material_families: list[str] = Field(default_factory=lambda: ["tm_nitride"])
    formulas: list[str] = Field(default_factory=list)
    metals: list[str] = Field(default_factory=list)
    ternary_metals: list[str] = Field(default_factory=list)
    x_values: list[float] = Field(default_factory=list)
    candidate_specs: list[CandidateSpec] = Field(default_factory=list)
    substrates: list[str] = Field(default_factory=lambda: ["Si(001)"])
    strain_values: list[float] = Field(default_factory=lambda: [0.0])
    poisson_ratio: float = Field(default=0.25, ge=0.0, le=0.5)
    supercell: list[int] = Field(
        default_factory=lambda: [2, 2, 1],
        min_length=3,
        max_length=3,
        description="Supercell for nitride ternary enumeration.",
    )
    b_concentrations: list[float] = Field(default_factory=list)
    bsi_supercell: list[int] = Field(
        default_factory=lambda: [2, 2, 2],
        min_length=3,
        max_length=3,
        description="Supercell for B:Si enumeration.",
    )
    seed: int = 42
    max_candidates: int = Field(default=50, ge=1)
    epitaxy_orientation: Literal["auto", "cube_on_cube", "45deg"] = "auto"
    use_buffers: bool = True
    # --- P3.5 nickelate / oxygen-vacancy (opt-in via material_families) ---
    nickelate_rare_earths: list[str] = Field(default_factory=list)
    """R species for infinite-layer RNiO₂ (Nd / Pr / La). Empty → [Nd] when
    ``nickelate`` is in ``material_families``. Ignored when the family is off."""
    nickelate_patterns: list[str] = Field(default_factory=list)
    """Vacancy / apical-O pattern ids. Empty → default screening set
    (stoichiometric, inplane_vacancy, apical_o) when the family is enabled.
    See ``docs/phase3-p35-oxygen-vacancy.md``."""
    nickelate_max_patterns: int = Field(
        default=8,
        ge=1,
        description=(
            "Hard cap on distinct vacancy/apical patterns per rare earth "
            "(not including the strain × substrate grid)."
        ),
    )
    nickelate_supercell: list[int] = Field(
        default_factory=lambda: [2, 2, 1],
        min_length=3,
        max_length=3,
        description="Supercell used for patterns that need one (inplane_vacancy).",
    )
    """Supercell used for patterns that need one (``inplane_vacancy``)."""

    @field_validator("supercell", "bsi_supercell", "nickelate_supercell")
    @classmethod
    def _supercell_components_positive(cls, v: list[int], info) -> list[int]:
        out = [int(n) for n in v]
        if any(n < 1 for n in out):
            raise ValueError(f"{info.field_name} components must be ≥ 1, got {out}")
        return out


class CalculatorConfig(BaseModel):
    name: str = "mock"
    parameters: dict[str, Any] = Field(default_factory=dict)


class EPWConfig(BaseModel):
    enabled: bool = False
    nkf: list[int] = Field(default_factory=lambda: [6, 6, 6])
    nqf: list[int] = Field(default_factory=lambda: [6, 6, 6])
    nkc: list[int] = Field(default_factory=lambda: [4, 4, 4])
    nqc: list[int] = Field(default_factory=lambda: [2, 2, 2])
    nbndsub: int | None = None
    auto_nbndsub: bool = True
    wannier_retry_on_froz_overflow: bool = True
    auto_retry_kmesh: bool = True
    max_kmesh_retries: int = Field(default=2, ge=0, le=4)
    auto_retry_search_shells: bool = True
    max_search_shells_retries: int = Field(default=2, ge=0, le=4)
    search_shells: int | None = Field(default=None, ge=1, le=256)
    kmesh_tol: float | None = Field(default=None, gt=0.0)
    strict_coarse_k: bool = False
    bands_skipped: int = 0
    mu_star: float = Field(default=0.10, ge=0.0, le=0.3)
    fsthick: float = 0.4
    degaussw: float = 0.05
    degaussq: float = 0.05
    eps_acustic: float = 5.0
    eliashberg: bool = True
    allen_dynes_fallback: bool = True
    wdata_prefix: str = "siscforge"
    npool: int = 1
    strict_parallel: bool = False
    allow_on_soft: bool = Field(
        default=False,
        description=(
            "If True, run EPW even when DFPT reports imaginary modes or "
            "dynamically_stable=false. Default False is a calculator-level "
            "safety gate complementary to shortlist --mode stable_only."
        ),
    )


class DFTUConfig(BaseModel):
    """DFT+U (Hubbard) settings for the unconventional cheap proxy (P3.1).

    **Disabled by default** — existing nitride / MgB₂ / AL examples are
    unchanged until ``enabled`` is set (or ``DFTConfig.do_dftu`` /
    calculator ``qe-dftu``).

    Extension points:
    - **P3.2** Wannierization after DFT+U (``dft.wannier`` / ``WannierResult``)
    - **P3.3** TRIQS / solid_dmft recipe consuming Wannier + this U/J (**shipped**)
    - **P3.4** pairing eigenvalue → ``performance_score``
    """

    enabled: bool = False
    """Master switch for DFT+U. Also set via ``dft.do_dftu`` or ``qe-dftu``."""

    U_eV: float = Field(default=4.0, ge=0.0)
    """Default Hubbard U (eV) applied to every species in ``hubbard_species``
    that lacks an entry in ``U_by_species``."""

    J_eV: float = Field(default=0.0, ge=0.0)
    """Default Hund's J (eV). Zero is the simplified rotationally-invariant case."""

    U_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-element U overrides, e.g. ``{Ni: 5.0, Nd: 6.0}``. Values must be ≥ 0."""

    J_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-element J overrides. Values must be ≥ 0."""

    hubbard_species: list[str] = Field(default_factory=list)
    """Elements receiving Hubbard corrections. Empty → auto-detect correlated
    metals in the structure (Ni, Cu, Fe, Co, Mn, Cr, V, Ti, rare earths)."""

    hubbard_projectors: Literal["ortho-atomic", "atomic", "pseudo"] = "ortho-atomic"
    """QE Hubbard projector type.

    * namelist dialect → SYSTEM ``U_projection_type``
    * card dialect → ``HUBBARD (ortho-atomic)`` header
    """

    hubbard_manifolds: dict[str, str] = Field(default_factory=dict)
    """Per-element orbital manifold for the QE ≥7.1 HUBBARD card, e.g.
    ``{Ni: 3d, O: 2p}``. Required for species without a built-in TM/RE
    heuristic (p-block oxygen, etc.); avoids silent wrong ``3d`` guesses."""

    lda_plus_u_kind: int = Field(default=0, ge=0, le=1)
    """0 = simplified (J0), 1 = full Liechtenstein (requires anisotropic J — not
    expressible via scalar J_eV; use kind 0 or J=0)."""

    nspin: Literal[1, 2, 4] = 2
    """QE spin polarization: 1 (non-spin), 2 (collinear), or 4 (noncollinear).
    Value 3 is invalid in pw.x and is rejected at config validation."""

    hubbard_syntax: Literal["namelist", "card"] = "namelist"
    """Exactly one QE Hubbard input dialect:

    * ``namelist`` — classic ``lda_plus_u`` / ``Hubbard_U(*)`` (QE 6.x–7.x; default)
    * ``card`` — QE ≥ 7.1 ``HUBBARD (...)`` card only

    Never emit both dialects in one input.
    """

    starting_magnetization: dict[str, float] = Field(default_factory=dict)
    """Element → starting magnetization fraction for spin-polarized SCF."""

    default_starting_magnetization: float = Field(default=0.5, ge=-1.0, le=1.0)
    """Fallback starting magnetization for Hubbard species without overrides."""

    do_relax_with_u: bool = False
    """If True, run vc-relax under DFT+U before the final SCF+U (heavier).

    Sufficient on its own to enter the relax stage even when ``DFTConfig.do_relax``
    is False (explicit U-relaxation request).
    """

    version: str = "0.1"

    @field_validator("U_by_species", "J_by_species")
    @classmethod
    def _nonneg_species_maps(cls, v: dict[str, float]) -> dict[str, float]:
        bad = {k: val for k, val in (v or {}).items() if float(val) < 0.0}
        if bad:
            raise ValueError(
                f"Hubbard per-species values must be ≥ 0; got negative entries {bad}"
            )
        return v

    @field_validator("starting_magnetization")
    @classmethod
    def _starting_mag_bounds(cls, v: dict[str, float]) -> dict[str, float]:
        bad = {
            k: val
            for k, val in (v or {}).items()
            if float(val) < -1.0 or float(val) > 1.0
        }
        if bad:
            raise ValueError(
                f"starting_magnetization values must be in [-1, 1]; got {bad}"
            )
        return v

    @field_validator("hubbard_manifolds")
    @classmethod
    def _manifold_labels(cls, v: dict[str, str]) -> dict[str, str]:
        import re

        bad: dict[str, str] = {}
        out: dict[str, str] = {}
        for k, val in (v or {}).items():
            label = str(val).strip()
            if not re.fullmatch(r"[1-6][spdf]", label):
                bad[str(k)] = str(val)
            else:
                out[str(k)] = label
        if bad:
            raise ValueError(
                "hubbard_manifolds values must look like QE orbital labels "
                f"(e.g. '3d', '2p', '4f'); got {bad}"
            )
        return out


class WannierConfig(BaseModel):
    """Standalone Wannierization settings for the unconventional pathway (P3.2).

    **Disabled by default** — conventional nitride / MgB₂ / AL examples are
    unchanged until ``enabled`` is set (or ``DFTConfig.do_wannier`` /
    calculator ``qe-wannier``).

    Distinct from the EPW-internal Wannier90 step (``proj=random`` inside EPW
    screening). This config drives a **first-class prep + quality-metrics** step after SCF
    or DFT+U that produces :class:`~siscforge.models.results.WannierResult`
    for the P3.3 DMFT gate. When binaries and upstream charge density are
    present, **P3.2.1** runs nscf + ``pw2wannier90`` automatically.

    Lessons reused from EPW screening (coarse-k safety, frozen-window
    classification) without weakening the conventional EPW remediation path.

    Extension points (not implemented here):
    - **P3.4** pairing eigenvalue → ``performance_score``
    - Material-specific production projection libraries (later residual)
    """

    enabled: bool = False
    """Master switch. Also set via ``dft.do_wannier`` or ``qe-wannier``."""

    projection_mode: Literal["random", "explicit"] = "random"
    """Screening default is ``random`` (EPW lesson). Use ``explicit`` with
    ``projections`` for orbital strings when provided."""

    projections: list[str] = Field(default_factory=list)
    """Explicit Wannier90 projection lines when ``projection_mode=explicit``,
    e.g. ``[\"Ni:d\", \"O:p\"]``. Empty + explicit mode falls back to random
    with a warning note (material-specific production libraries are later).
    """

    num_wann: int | None = Field(default=None, ge=1)
    """Target number of Wannier functions. None → auto policy when
    ``auto_num_wann`` is True."""

    auto_num_wann: bool = True
    """When True and ``num_wann`` is None (or undersized), derive a screening
    floor from band count / cell size (mirrors EPW ``auto_nbndsub`` spirit)."""

    num_bands: int | None = Field(default=None, ge=1)
    """Bloch bands for Wannierization. None → use ``DFTConfig.nbnd`` or auto."""

    # Window hints (eV absolute, or relative to Fermi when use_fermi_relative)
    use_fermi_relative_windows: bool = True
    """When True and a Fermi energy is known, windows are Ef-relative (EPW
    lesson: hard-coded absolute windows fail for high-Ef metals)."""

    dis_win_min: float | None = None
    """Outer disentanglement window min (eV). None → screening default."""

    dis_win_max: float | None = None
    """Outer disentanglement window max (eV)."""

    dis_froz_min: float | None = None
    """Frozen window min (eV). Screening defaults use a tight frozen window."""

    dis_froz_max: float | None = None
    """Frozen window max (eV)."""

    screening_tight_froz: bool = True
    """Reuse EPW tight frozen-window defaults for screening random projs."""

    # Coarse k safety (shared philosophy with EPW; does not alter EPW configs)
    kmesh: list[int] = Field(default_factory=lambda: [4, 4, 4])
    """Coarse electronic k-mesh for the Wannier nscf / .win mp_grid."""

    auto_nscf_pw2wannier: bool = True
    """When True (default) and ``.amn``/``.mmn`` are missing, run nscf +
    ``pw2wannier90`` if ``pw.x``, ``pw2wannier90.x``, and an upstream
    ``{prefix}.save`` charge density are available (P3.2.1).

    Soft dependency: missing binaries or charge density classify cleanly
    (``missing_files`` / ``binary_missing``) and never crash dry-run or
    ``pytest``. Set False to keep the P3.2 manual-stage path.
    """

    strict_coarse_k: bool = False
    """If True, refuse undersized k-meshes instead of auto-raising."""

    auto_raise_coarse_k: bool = True
    """Raise coarse k to Wannier-safe floors (same policy as EPW)."""

    # Quality / DMFT gate thresholds
    max_avg_spread_ang2: float = Field(
        default=12.0,
        gt=0.0,
        description=(
            "DMFT gate: average WF spread (Å²) above this → not ready for DMFT. "
            "Conservative screening default pending nickelate-specific calibration. "
            "With proj=random many candidates will gate out — intentional for P3.3 "
            "safety. Tighten for production / explicit projections; loosen only with "
            "documented local validation. Not derived from a single literature cutoff."
        ),
    )
    """DMFT gate: average WF spread (Å²) above this → not ready for DMFT.

    Conservative **screening** default pending nickelate-specific calibration.
    With ``proj=random`` many candidates will gate out — intentional for P3.3
    safety. Tighten for production / explicit projections; loosen only with
    documented local validation. Not derived from a single literature cutoff.
    """

    max_spread_ang2: float = Field(
        default=25.0,
        gt=0.0,
        description=(
            "DMFT gate: any individual WF spread (Å²) above this → not ready. "
            "Same rationale as max_avg_spread_ang2: conservative screening floor so "
            "P3.3 does not consume obviously delocalized / failed MLWFs. Expect to "
            "retune once material-specific projections and workstation validation data "
            "exist."
        ),
    )
    """DMFT gate: any individual WF spread (Å²) above this → not ready.

    Same rationale as ``max_avg_spread_ang2``: conservative screening floor so
    P3.3 does not consume obviously delocalized / failed MLWFs. Expect to
    retune once material-specific projections and workstation validation data
    exist.
    """

    require_chk: bool = True
    """DMFT gate: require a ``.chk`` (or mock equivalent) artifact handle."""

    # Parallel notes (documented; execution uses DFTConfig.nproc)
    nproc_note: str = (
        "Wannier90.x is typically serial or lightly parallel; "
        "pw.x nscf / pw2wannier90 follow dft.nproc."
    )

    seedname: str = "siscforge"
    """Wannier90 seedname (produces seedname.win / .amn / .mmn / .chk / .wout)."""

    # Mock path control (tests / dry-run)
    mock_force_failure: bool = False
    """When True under mock calculator, emit a failed WannierResult
    (for failure-classification tests). Inert on real QE path."""

    mock_failure_class: str = "frozen_window"
    """Failure class used when ``mock_force_failure`` is True."""

    version: str = "0.1"


class DMFTScoringConfig(BaseModel):
    """P3.4 pairing-eigenvalue → ``performance_score`` knobs.

    Defaults leave conventional (no-DMFT) campaigns numerically unchanged.
    Mapping is applied only when a usable ``DMFTResult`` pairing signal is
    present. See ``docs/phase3-p34-pairing-score.md``.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Map a usable DMFT pairing eigenvalue onto performance_score. "
            "Default on: inert unless a DMFTResult with pairing is attached."
        ),
    )
    kelvin_per_unit: float = Field(
        default=25.0,
        ge=0.0,
        description=(
            "Linear scale: score_K = (λ − threshold) × kelvin_per_unit × Q. "
            "25 K/unit is an engineering choice so λ≈1 (typical linearized "
            "pairing instability) lands mid the conventional 40 K ranking "
            "band — not a fitted Tc model. See docs/phase3-p34-pairing-score.md."
        ),
    )
    eigenvalue_threshold: float = Field(
        default=0.0,
        description="Subtracted from λ before scaling. Default 0 (no offset).",
    )
    score_ceiling_K: float = Field(
        default=40.0,
        gt=0.0,
        description=(
            "Clamp the kelvin proxy. Default 40 matches "
            "RankingConfig.performance_ceiling_K so a λ-proxy cannot "
            "saturate the ranker harder than a conventional Tc. The two "
            "ceilings are independent knobs — set both if you retune one."
        ),
    )
    require_converged: bool = Field(
        default=True,
        description="Refuse to score when DMFTResult.converged is False.",
    )
    allow_mock: bool = Field(
        default=True,
        description=(
            "Allow mock/illustrative eigenvalues to produce a score tagged "
            "dmft_pairing_mock. Default true so the dry-run path (the only "
            "working DMFT path until real launch) exercises ranking. Mock "
            "data is never labelled as production Tc; CLI ranks print a "
            "banner when these rows participate. Set false for a stricter "
            "production posture."
        ),
    )
    quality_demotion: bool = Field(
        default=True,
        description=(
            "Soft occupancy / mass-enhancement demotion only. Not a physics "
            "model; floors at 0.70."
        ),
    )
    mass_enhancement_soft_cap: float = Field(
        default=8.0,
        gt=0.0,
        description=(
            "m*/m above this applies a light multiplicative demotion. "
            "8.0 is a loose screening fence (typical nickelate m* is ~2–5); "
            "not a literature cutoff."
        ),
    )
    occupancy_soft_min: float = Field(
        default=1.0,
        description=(
            "Filling below this is treated as wildly unphysical for the "
            "soft Q demotion. Loose fence (d-shell / impurity fillings "
            "are typically a few electrons), not a physics bound."
        ),
    )
    occupancy_soft_max: float = Field(
        default=12.0,
        description=(
            "Filling above this is treated as wildly unphysical for the "
            "soft Q demotion. 12 ≈ a full d + leftover count; loose fence."
        ),
    )

    @field_validator(
        "kelvin_per_unit",
        "eigenvalue_threshold",
        "score_ceiling_K",
        "mass_enhancement_soft_cap",
        "occupancy_soft_min",
        "occupancy_soft_max",
    )
    @classmethod
    def _finite_scoring(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("DMFT scoring parameter must be finite")
        return v

    @model_validator(mode="after")
    def _occupancy_range(self) -> DMFTScoringConfig:
        if self.occupancy_soft_min > self.occupancy_soft_max:
            raise ValueError(
                "occupancy_soft_min must be <= occupancy_soft_max "
                f"(got {self.occupancy_soft_min} > {self.occupancy_soft_max})"
            )
        return self


class DMFTConfig(BaseModel):
    """TRIQS / solid_dmft settings for the unconventional pathway (P3.3).

    **Disabled by default** — conventional nitride / MgB₂ / AL examples are
    unchanged until ``enabled`` is set (or ``DFTConfig.do_dmft`` /
    calculator ``qe-dmft``).

    Consumes :class:`~siscforge.models.results.WannierResult` from P3.2.
    Non-mock solvers require ``WannierResult.ready_for_dmft`` unless
    ``allow_without_wannier_gate`` is True. The mock solver is a documented
    dry-run bypass (``mock_bypass_gate``, default True).

    Screening defaults below are **thin workstation knobs**, not production
    CTHYB settings. Retune for nickelate calibration.

    **P3.4:** ``scoring`` maps ``leading_pairing_eigenvalue`` onto the common
    ``performance_score`` (default on when a usable pairing signal is present;
    inert for conventional campaigns). Precedence vs EPW is
    ``ranking.performance_precedence`` (default ``epw_then_dmft``).

    Extension points (not implemented here):
    - **P3.5** oxygen-vacancy enumeration (**shipped** — structure generation)
    - **P3.6** mixed conventional/unconventional AL (**shipped** — see
      ``docs/phase3-p36-mixed-al.md``)
    """

    enabled: bool = False
    """Master switch. Also set via ``dft.do_dmft`` or ``qe-dmft``."""

    solver: Literal["mock", "solid_dmft", "cthyb"] = "mock"
    """Backend. ``mock`` is always available (no TRIQS).

    Real ``solid_dmft`` / ``cthyb`` write a run package
    (``dmft_config.toml`` + invoke script) and, when ``auto_launch`` is
    True and the stack is present, invoke it. Drop-in ``observables.json``
    is still preferred; native ``observables_imp*.dat`` and
    ``DMFT_results`` h5 are parsed when JSON is absent (h5py is soft).
    ``DMFTResult.converged`` prefers ``conv_imp*.dat`` /
    ``convergence_obs`` when present. TRIQS is never a hard dependency
    of siscforge.
    """

    U_eV: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Screening default Hubbard U (eV) for the impurity. Conservative "
            "nickelate-style starting point; not a fitted production value."
        ),
    )
    """Screening default Hubbard U (eV)."""

    J_eV: float = Field(
        default=0.8,
        ge=0.0,
        description=(
            "Screening default Hund's J (eV). Typical infinite-layer nickelate "
            "starting guess; retune with local validation."
        ),
    )
    """Screening default Hund's J (eV)."""

    U_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-element U overrides. Values must be ≥ 0."""

    J_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-element J overrides. Values must be ≥ 0."""

    beta: float = Field(
        default=40.0,
        gt=0.0,
        description=(
            "Inverse temperature β (1/eV). Screening default ~290 K; "
            "not a production low-T CTHYB setting."
        ),
    )
    """Inverse temperature β (1/eV). Screening default."""

    n_cycles: int = Field(
        default=10_000,
        ge=1,
        description="Requested QMC / solver cycles (thin screening knob).",
    )
    """Requested QMC / solver cycles (screening default)."""

    n_warmup_cycles: int = Field(
        default=2_000,
        ge=0,
        description="Requested warmup / thermalization cycles.",
    )
    """Requested warmup cycles (screening default)."""

    n_loops: int = Field(
        default=4,
        ge=1,
        description=(
            "Requested DMFT self-consistency loops (screening default). "
            "Written to dmft_config.toml as n_iter_dmft by the "
            "p3_x_real_launch package writer."
        ),
    )
    """Requested outer DMFT loops (screening default).

    Consumed by the real-launch toml writer as ``n_iter_dmft``.
    """

    auto_launch: bool = True
    """When True (default), non-mock solvers invoke solid_dmft if the
    stack is importable or ``SISCFORGE_SOLID_DMFT`` is set. Mock / dry-run
    is unaffected. Set False to write the run package only.
    """

    launch_timeout_s: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Optional wall-clock timeout (seconds) for the solid_dmft "
            "subprocess. None (default) means no timeout — real CTHYB "
            "can run for hours. Tests may set a short value."
        ),
    )
    """Optional invoke timeout in seconds. ``None`` = no timeout."""

    # Wannier gate
    require_wannier_gate: bool = True
    """When True (default), non-mock solvers refuse unless Wannier is
    ``ready_for_dmft``. Ignored when ``allow_without_wannier_gate`` is True.
    """

    allow_without_wannier_gate: bool = False
    """Explicit escape hatch: launch even when Wannier is missing or not
    ready. Default False. Intended for operator override, not screening.
    """

    mock_bypass_gate: bool = True
    """Documented mock/dry-run bypass: when ``solver=mock`` and this is True,
    DMFT may run without ``ready_for_dmft``. Set False to exercise the gate
    even on the mock path.
    """

    # Mock path control
    mock_force_failure: bool = False
    """When True under mock solver, emit a failed DMFTResult."""

    mock_failure_class: str = "not_converged"
    """Failure class used when ``mock_force_failure`` is True."""

    seedname: str = "siscforge"
    """Work-directory seedname for written config / mock artifacts."""

    scoring: DMFTScoringConfig = Field(default_factory=DMFTScoringConfig)
    """P3.4 pairing → performance_score knobs (inert unless pairing is present)."""

    version: str = "0.1"

    @field_validator("U_by_species", "J_by_species")
    @classmethod
    def _nonneg_species_maps(cls, v: dict[str, float]) -> dict[str, float]:
        bad = {k: val for k, val in (v or {}).items() if float(val) < 0.0}
        if bad:
            raise ValueError(
                f"DMFT per-species values must be ≥ 0; got negative entries {bad}"
            )
        return v


class DFTConfig(BaseModel):
    engine: Literal["mock", "qe", "qe-epw", "qe-dftu", "qe-wannier", "qe-dmft"] = "mock"
    ecutwfc: float = 50.0
    ecutrho: float = 400.0
    kpoints: list[int] = Field(default_factory=lambda: [4, 4, 4])
    conv_thr: float = 1.0e-8
    forc_conv_thr: float = 1.0e-3
    press_conv_thr: float = 0.5
    occupations: str = "smearing"
    smearing: str = "mv"
    degauss: float = 0.02
    nbnd: int | None = None
    pseudo_dir: str | None = None
    pseudopotentials: dict[str, str] = Field(default_factory=dict)
    work_dir: str | None = None
    nproc: int = 1
    phonon_method: Literal["dfpt", "gamma", "phonopy_fd"] = "dfpt"
    phonopy_supercell: list[int] = Field(default_factory=lambda: [2, 2, 2])
    phonopy_distance: float = 0.01
    qpoints: list[int] = Field(default_factory=lambda: [2, 2, 2])
    tr2_ph: float = 1.0e-12
    ph_alpha_mix: float = 0.3
    ph_nmix: int = 8
    ph_niter: int = 100
    do_relax: bool = True
    do_phonon: bool = True
    phonon_retry_on_d_matrix: bool = True
    phonon_retry_on_fft_symmetry: bool = True
    do_epw: bool = False
    epw: EPWConfig = Field(default_factory=EPWConfig)
    # --- P3.1 DFT+U (disabled by default; inert for conventional campaigns) ---
    do_dftu: bool = False
    """Enable sequential pw.x DFT+U after (or instead of) the conventional path.

    Equivalent to ``dftu.enabled: true``. Calculator ``qe-dftu`` forces this on.
    """
    dftu: DFTUConfig = Field(default_factory=DFTUConfig)
    # --- P3.2 Wannierization (disabled by default; inert for conventional) ---
    do_wannier: bool = False
    """Enable first-class Wannierization after SCF / DFT+U (P3.2).

    Equivalent to ``wannier.enabled: true``. Calculator ``qe-wannier`` forces this.
    Does **not** replace EPW-internal Wannier; conventional EPW path unchanged.
    """
    wannier: WannierConfig = Field(default_factory=WannierConfig)
    # --- P3.3 DMFT (disabled by default; inert for conventional) ---
    do_dmft: bool = False
    """Enable sequential DMFT after Wannier (P3.3).

    Equivalent to ``dmft.enabled: true``. Calculator ``qe-dmft`` forces this.
    Disabled by default — conventional nitride / MgB₂ / EPW paths unchanged.
    """
    dmft: DMFTConfig = Field(default_factory=DMFTConfig)
    quality_tag: Literal["screening", "production"] = "screening"


class JosephsonConfig(BaseModel):
    """P4.1/P4.2 Josephson analytics — **disabled by default**.

    When ``enabled`` is false the module is completely inert: no metrics
    are attached and existing campaigns are unchanged. When enabled,
    Ambegaokar–Baratoff / BCS-from-Tc estimates are attached to the
    top-``shortlist_size`` ranked evaluations (or all rows when
    ``shortlist_only`` is false / ``shortlist_size`` ≤ 0). P4.2 also
    attaches fabrication-compatibility hints (default on whenever the
    module is enabled) and may soft-reorder the Josephson shortlist
    for presentation when ``secondary_ranking`` is ``icrn`` or ``jc``.

    Secondary sort does **not** change ``rank`` / ``composite_score``.
    """

    enabled: bool = False
    shortlist_size: int = Field(default=20, ge=0)
    """Top-N (by rank) to annotate when ``shortlist_only`` is true. 0 → all."""
    shortlist_only: bool = True
    """If true, only attach metrics to rank ≤ ``shortlist_size``."""
    model_tier: str = "analytic_AB"
    reference_area_um2: float = Field(default=1.0, gt=0.0)
    """Ranking junction area (μm²). Not a fabricated device layout."""
    rna_ohm_um2: float = Field(default=20.0, gt=0.0)
    """Assumed SIS-like specific resistance RnA (Ω·μm²) for the Jc proxy."""
    assume_SIS: bool = True
    temperature_K: float | None = None
    """AB tanh temperature; None → T = 0 ranking limit."""
    bcs_gap_ratio: float = Field(default=1.764, gt=0.0)
    """Δ / k_B Tc used when no Eliashberg / explicit gap is present."""
    family_gap_ratios: dict[str, float] = Field(default_factory=dict)
    """Optional per-``material_family`` BCS ratio overrides (documented)."""
    fabrication_hints: bool = True
    """When Josephson is enabled, also attach P4.2 fabrication heuristics.
    Opt-out; default on so enabling Josephson surfaces SIS/SNS/thermal notes.
    """
    beol_temp_ceiling_c: float = Field(default=400.0, gt=0.0)
    """CMOS-ish BEOL comparison threshold (°C). Heuristic, not a PDK limit."""
    secondary_ranking: Literal["none", "icrn", "jc"] = "none"
    """Soft presentation sort **within** the Josephson-annotated shortlist.
    ``none`` (default) leaves list order as composite rank. ``icrn`` / ``jc``
    reorder those rows only; ``rank`` and ``composite_score`` stay put.
    YAML ``false`` / ``true`` coerce to ``none`` / ``icrn``.
    """

    @classmethod
    def normalize_secondary_ranking(cls, v: object) -> Literal["none", "icrn", "jc"]:
        """Single source of truth for ``secondary_ranking`` YAML coercion.

        ``false`` / ``None`` / ``\"none\"`` → ``none``;
        ``true`` / ``\"icrn\"`` → ``icrn``; ``\"jc\"`` → ``jc``.
        """
        if v is None or v is False:
            return "none"
        if v is True:
            return "icrn"
        if isinstance(v, str):
            text = v.strip().lower()
            if text in {"", "none", "false", "off", "0"}:
                return "none"
            if text in {"true", "on", "1", "icrn"}:
                return "icrn"
            if text == "jc":
                return "jc"
        raise ValueError(
            "josephson.secondary_ranking must be one of: none, icrn, jc "
            f"(bool false→none, true→icrn); got {v!r}"
        )

    @field_validator("secondary_ranking", mode="before")
    @classmethod
    def _coerce_secondary_ranking(cls, v: object) -> str:
        return cls.normalize_secondary_ranking(v)

    @field_validator(
        "reference_area_um2", "rna_ohm_um2", "bcs_gap_ratio", "beol_temp_ceiling_c"
    )
    @classmethod
    def _finite_positive_knob(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise ValueError("josephson numeric knobs must be finite and > 0")
        return v

    @field_validator("family_gap_ratios")
    @classmethod
    def _family_ratios_positive(cls, v: dict[str, float]) -> dict[str, float]:
        """Reject non-finite / non-positive overrides (no silent fallback)."""
        cleaned: dict[str, float] = {}
        bad: dict[str, object] = {}
        for key, val in (v or {}).items():
            try:
                x = float(val)
            except (TypeError, ValueError):
                bad[str(key)] = val
                continue
            if not math.isfinite(x) or x <= 0.0:
                bad[str(key)] = val
                continue
            cleaned[str(key)] = x
        if bad:
            raise ValueError(
                "josephson.family_gap_ratios values must be finite and > 0; "
                f"got invalid entries {bad}"
            )
        return cleaned

    @field_validator("temperature_K")
    @classmethod
    def _finite_temp(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v) or v < 0.0:
            raise ValueError("josephson.temperature_K must be finite and ≥ 0")
        return v


class RunConfig(BaseModel):
    resume: bool = True
    continue_on_error: bool = True
    force_rerun: bool = False
    resume_qe_steps: bool = True
    force_rerun_qe_steps: bool = False
    heartbeat_seconds: int = Field(default=900, ge=0)
    estimate_walltime: bool = True
    walltime_scale: float = Field(default=1.0, gt=0.0, le=20.0)
    heartbeat_eta: bool = True


class CampaignConfig(BaseModel):
    name: str = "unnamed_campaign"
    description: str = ""
    version: str = "0.1"
    dry_run: bool = False
    enumeration: EnumerationConfig = Field(default_factory=EnumerationConfig)
    calculators: list[CalculatorConfig] = Field(default_factory=lambda: [CalculatorConfig(name="mock")])
    dft: DFTConfig = Field(default_factory=DFTConfig)
    formation_filter: FormationFilterConfig = Field(default_factory=FormationFilterConfig)
    surrogate: SurrogateConfig = Field(default_factory=SurrogateConfig)
    active_learning: ActiveLearningConfig = Field(default_factory=ActiveLearningConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    si_feasibility: SiFeasibilityConfig = Field(default_factory=SiFeasibilityConfig)
    josephson: JosephsonConfig = Field(default_factory=JosephsonConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    output_dir: str = "outputs"
    export_formats: list[Literal["json", "csv", "markdown"]] = Field(default_factory=lambda: ["json", "csv"])
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("campaign name must be non-empty")
        return v

    @classmethod
    def from_yaml(cls, path: str | Path) -> CampaignConfig:
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Campaign YAML root must be a mapping: {path}")
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.model_dump(mode="json"), fh, default_flow_style=False, sort_keys=False)
