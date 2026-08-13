"""Calculation result models for Phase 0 / v0.1.

Only the minimal fields needed for dry-run orchestration, ranking, and export
are defined here. DFT parsers and EPW/DMFT fields arrive in later phases.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from siscforge.models.provenance import Provenance


class SCFResult(BaseModel):
    """Minimal self-consistent field (or total-energy) result.

    Populated by real QE calculators later; the mock calculator fills
    placeholder values with ``status=\"mock\"``.
    """

    total_energy_eV: float | None = None
    """Total energy in eV (per formula unit when applicable)."""

    energy_above_hull_eV_per_atom: float | None = None
    """Formation / hull energy proxy used for pre-filtering."""

    band_gap_eV: float | None = None
    """Optional band gap; None for metals."""

    is_metallic: bool | None = None
    """Whether the SCF indicates a metallic ground state."""

    status: str = "unknown"
    """Run status: ``ok``, ``failed``, ``mock``, etc."""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    """Calculation quality tier."""

    raw: dict[str, Any] = Field(default_factory=dict)
    """Catch-all for engine-specific extras."""

    provenance: Provenance = Field(default_factory=Provenance)


class PhononResult(BaseModel):
    """Minimal phonon / DFPT result for Phase 0 dynamical-stability checks."""

    min_frequency_cm1: float | None = None
    """Lowest phonon frequency in cm⁻¹ (negative ⇒ imaginary mode)."""

    has_imaginary_modes: bool = False
    """True if any physically meaningful imaginary modes were found."""

    dynamically_stable: bool = True
    """Convenience flag: not ``has_imaginary_modes`` under campaign thresholds."""

    n_modes: int | None = None
    """Total number of phonon modes reported (optional)."""

    max_frequency_cm1: float | None = None
    """Highest phonon frequency in cm⁻¹ (optional)."""

    status: str = "unknown"
    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    raw: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)


class ElectronPhononResult(BaseModel):
    """Electron-phonon / Eliashberg summary (Phase 1 conventional pathway).

    Populated by EPW (+ isotropic Eliashberg or Allen–Dynes). All frequencies
    that enter Tc formulas are stored in kelvin unless noted.
    """

    lambda_total: float | None = None
    """Mass-enhancement / electron-phonon coupling strength λ."""

    omega_log: float | None = None
    """Logarithmic average phonon frequency ω_log in kelvin."""

    omega_2: float | None = None
    """Optional second moment of the a²F spectrum (K)."""

    mu_star: float | None = 0.1
    """Coulomb pseudopotential μ* used for Tc."""

    Tc_allen_dynes: float | None = None
    """Allen–Dynes Tc estimate (K)."""

    Tc_eliashberg: float | None = None
    """Isotropic Eliashberg Tc (K) when available; else None."""

    alpha2F_summary: dict[str, Any] = Field(default_factory=dict)
    """Compact a²F metadata (n_bins, peak positions, source file, …)."""

    converged: bool = False
    """Whether EPW / Eliashberg reported a successful convergence."""

    wannier_ok: bool | None = None
    """Basic Wannierization quality flag when diagnostics are available."""

    status: str = "unknown"
    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    """Engine quality tier (screening grids vs production settings)."""

    result_quality: Literal[
        "production",
        "screening",
        "screening_suspect",
        "unreliable",
        "unknown",
    ] = "unknown"
    """Trust assessment of these λ/Tc numbers (filled by quality layer)."""

    quality_flags: list[str] = Field(default_factory=list)
    """Machine-readable trust flags (e.g. high_lambda)."""

    quality_notes: str = ""
    """Human caveat when quoting Tc/λ."""

    raw: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)

    def best_tc_K(self) -> float | None:
        """Prefer Eliashberg Tc, else Allen–Dynes."""
        if self.Tc_eliashberg is not None:
            return float(self.Tc_eliashberg)
        if self.Tc_allen_dynes is not None:
            return float(self.Tc_allen_dynes)
        return None


class DFTUResult(BaseModel):
    """DFT+U (Hubbard) correlated proxy — Phase 3.1 unconventional pathway.

    Cheap screening result stored alongside SCF/phonon/EPW on a
    :class:`~siscforge.models.candidate.CandidateEvaluation`. Full DMFT
    (TRIQS / solid_dmft) and pairing eigenvalues arrive in P3.3–P3.4;
    Wannierization quality metrics in P3.2.

    This model is intentionally inert for conventional nitride/MgB₂
    campaigns: leave ``CandidateEvaluation.dftu`` as ``None`` unless
    DFT+U is explicitly enabled.
    """

    U_eV: float | None = None
    """Scalar Hubbard U (eV) when a single effective value is used."""

    J_eV: float | None = None
    """Scalar Hund's J (eV); 0 for simplified rotationally-invariant DFT+U."""

    U_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-species Hubbard U (eV), e.g. ``{\"Ni\": 5.0}``."""

    J_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-species Hund's J (eV)."""

    hubbard_species: list[str] = Field(default_factory=list)
    """Species that received Hubbard corrections."""

    hubbard_projectors: str | None = None
    """Projector type: ``ortho-atomic``, ``atomic``, ``pseudo``, …"""

    occupancy_summary: dict[str, float] = Field(default_factory=dict)
    """Compact d/f occupancy summary (species or orbital label → electrons)."""

    magnetic_moments: dict[str, float] = Field(default_factory=dict)
    """Per-site or per-species magnetic moments in μ_B."""

    total_magnetization: float | None = None
    """Cell total magnetization (μ_B)."""

    absolute_magnetization: float | None = None
    """Cell absolute magnetization (μ_B)."""

    total_energy_eV: float | None = None
    """DFT+U total energy in eV."""

    energy_above_hull_eV_per_atom: float | None = None
    """Optional hull proxy when available."""

    is_metallic: bool | None = None
    """Whether the DFT+U ground state is metallic."""

    fermi_energy_eV: float | None = None
    """Fermi energy from the DFT+U SCF (eV)."""

    status: str = "unknown"
    """Run status: ``ok``, ``failed``, ``mock``, …"""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    """Calculation quality tier."""

    raw: dict[str, Any] = Field(default_factory=dict)
    """Engine-specific extras (input path, parse notes, QE version, …)."""

    provenance: Provenance = Field(default_factory=Provenance)

    def summary_line(self) -> str:
        """Short human-readable summary for cards / CLI."""
        bits: list[str] = []
        if self.U_eV is not None:
            bits.append(f"U={self.U_eV:g} eV")
        elif self.U_by_species:
            u_bits = ",".join(f"{k}:{v:g}" for k, v in sorted(self.U_by_species.items()))
            bits.append(f"U=[{u_bits}] eV")
        if self.J_eV is not None and self.J_eV > 0:
            bits.append(f"J={self.J_eV:g} eV")
        elif self.J_eV is None and self.J_by_species and any(
            v > 0 for v in self.J_by_species.values()
        ):
            j_bits = ",".join(
                f"{k}:{v:g}" for k, v in sorted(self.J_by_species.items()) if v > 0
            )
            bits.append(f"J=[{j_bits}] eV")
        if self.total_magnetization is not None:
            bits.append(f"M={self.total_magnetization:g} μB")
        if self.occupancy_summary:
            occ = ",".join(
                f"{k}={v:g}" for k, v in sorted(self.occupancy_summary.items())[:4]
            )
            bits.append(f"occ({occ})")
        if self.total_energy_eV is not None:
            bits.append(f"E={self.total_energy_eV:.4f} eV")
        bits.append(f"status={self.status}")
        return "; ".join(bits) if bits else f"status={self.status}"


class WannierResult(BaseModel):
    """Standalone Wannierization quality result — Phase 3.2.

    First-class prep + quality-metrics step after SCF / DFT+U for correlated
    (nickelate) candidates. Consumed later by TRIQS / solid_dmft (**P3.3**).
    Full automated nscf + pw2wannier90 orchestration is residual **P3.2.1**.

    Inert for conventional nitride / MgB₂ campaigns: leave
    ``CandidateEvaluation.wannier`` as ``None`` unless Wannier is enabled
    (``dft.do_wannier`` / ``dft.wannier.enabled`` / calculator ``qe-wannier``).

    The conventional EPW pathway still runs its own internal Wannier90 step
    (``proj=random``, coarse grids); that path is unchanged by this model.
    Standalone ``CandidateEvaluation.wannier`` is independent of
    ``electron_phonon.wannier_ok``.
    """

    wannier_ok: bool = False
    """True when Wannier90 completed with usable quality for downstream use."""

    ready_for_dmft: bool = False
    """Explicit DMFT gate: manifold usable enough for P3.3 TRIQS/solid_dmft.

    False when quality is poor, spreads diverge, or required artifacts are
    missing. P3.3 should refuse to launch when this is False.
    """

    dmft_gate_notes: str = ""
    """Human reason for the DMFT gate decision (empty when ready)."""

    status: str = "unknown"
    """Run status: ``ok``, ``failed``, ``mock``, ``skipped``, …"""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    """Calculation quality tier (screening random projs vs production)."""

    failure_class: str | None = None
    """Primary failure class when not ok (step-aware Wannier classes only).

    Known values include: ``frozen_window``, ``kmesh_bvector``,
    ``disentanglement``, ``spread_divergence``, ``missing_files``,
    ``binary_missing``, ``projection``, ``nscf_failed``, ``pw2wannier_failed``,
    ``convergence``, ``other``. Never reuse phonon-only or EPW-only labels.

    ``convergence`` is classified from logs but is **not** a hard-fail
    fingerprint on its own — a non-zero returncode / incomplete job still
    marks ``wannier_ok=False``. Hard-fail classes (frozen window, missing
    files, …) force failure even when partial spread text is present.
    """

    num_wann: int | None = None
    """Number of Wannier functions requested / obtained."""

    num_bands: int | None = None
    """Number of Bloch bands fed into Wannierization."""

    projection_mode: str | None = None
    """``random`` (screening) or ``explicit`` (orbital strings)."""

    projection_summary: str = ""
    """Compact projection description (e.g. ``random`` or ``Ni:d;O:p``)."""

    spread_sum_ang2: float | None = None
    """Sum of Wannier spreads Ω (Å²) when parsed from ``.wout``."""

    avg_spread_ang2: float | None = None
    """Mean spread per WF (Å²)."""

    max_spread_ang2: float | None = None
    """Largest individual WF spread (Å²)."""

    spreads_ang2: list[float] = Field(default_factory=list)
    """Per-WF final spreads (Å²) when available."""

    disentanglement_notes: str = ""
    """Outer-window / disentanglement summary from logs."""

    frozen_window_notes: str = ""
    """Frozen-window notes (tight screening windows, overflow, …)."""

    kmesh: list[int] = Field(default_factory=list)
    """Actual coarse k-mesh written into ``.win`` / used for the run.

    Prefer the post-``resolve_kmesh`` mesh (may be auto-raised above config).
    """

    # Artifact handles (paths or opaque workdir references)
    work_dir: str | None = None
    """Work directory holding Wannier artifacts (opaque handle for P3.3)."""

    win_path: str | None = None
    """Path to ``.win`` input when present."""

    amn_path: str | None = None
    """Path to ``.amn`` when present."""

    mmn_path: str | None = None
    """Path to ``.mmn`` when present."""

    chk_path: str | None = None
    """Path to ``.chk`` (checkpoint; preferred P3.3 input) when present."""

    wout_path: str | None = None
    """Path to ``.wout`` log when present."""

    raw: dict[str, Any] = Field(default_factory=dict)
    """Catch-all: parse notes, return codes, extension hooks for P3.3."""

    provenance: Provenance = Field(default_factory=Provenance)

    def summary_line(self) -> str:
        """Short human-readable summary for cards / CLI."""
        bits: list[str] = []
        bits.append(f"ok={self.wannier_ok}")
        bits.append(f"dmft_ready={self.ready_for_dmft}")
        if self.num_wann is not None:
            bits.append(f"n_wann={self.num_wann}")
        if self.projection_mode:
            bits.append(f"proj={self.projection_mode}")
        if self.spread_sum_ang2 is not None:
            bits.append(f"Ω_sum={self.spread_sum_ang2:.3f} Å²")
        if self.avg_spread_ang2 is not None:
            bits.append(f"Ω_avg={self.avg_spread_ang2:.3f} Å²")
        if self.failure_class:
            bits.append(f"fail={self.failure_class}")
            if self.failure_class == "missing_files":
                bits.append(
                    "next=stage nscf+pw2wannier90 (.amn/.mmn) into work_dir, "
                    "then re-invoke / run_wannier90_on_artifacts"
                )
        bits.append(f"status={self.status}")
        return "; ".join(bits)


class DMFTResult(BaseModel):
    """DMFT / solid_dmft result — Phase 3.3 unconventional pathway.

    Optional attachment on :class:`~siscforge.models.candidate.CandidateEvaluation`.
    Produced only when DMFT is explicitly enabled (``dft.do_dmft`` /
    ``dft.dmft.enabled`` / calculator ``qe-dmft``). Conventional nitride /
    MgB₂ campaigns leave this ``None``.

    Pairing eigenvalue → ``performance_score`` mapping is **P3.4**
    (``siscforge.scoring.pairing``). ``leading_pairing_eigenvalue`` is the
    scalar input; ``pairing_symmetry`` is metadata only.
    """

    status: str = "unknown"
    """Run status: ``ok``, ``failed``, ``mock``, ``skipped``, ``refused``, …"""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    """Calculation quality tier."""

    converged: bool = False
    """Whether the impurity solver reported a successful self-consistency."""

    U_eV: float | None = None
    """Effective Hubbard U used by the solver (eV)."""

    J_eV: float | None = None
    """Effective Hund's J used by the solver (eV)."""

    U_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-species U (eV) when a map is used."""

    J_by_species: dict[str, float] = Field(default_factory=dict)
    """Per-species J (eV) when a map is used."""

    occupancy_summary: dict[str, float] = Field(default_factory=dict)
    """Compact orbital / species occupancy (label → electrons)."""

    filling: float | None = None
    """Total impurity filling (electrons) when a single number is reported."""

    mass_enhancement: float | None = None
    """Quasiparticle mass enhancement m*/m (≈ 1/Z) when available."""

    mass_enhancement_by_orbital: dict[str, float] = Field(default_factory=dict)
    """Per-orbital mass enhancement when the solver reports it."""

    leading_pairing_eigenvalue: float | None = None
    """Leading pairing eigenvalue (P3.4 maps this onto ``performance_score``)."""

    pairing_symmetry: str | None = None
    """Pairing symmetry label (e.g. ``d_x2-y2``). Metadata only — not scored."""

    solver: str = "unknown"
    """Solver identity: ``mock``, ``cthyb``, ``solid_dmft``, …"""

    beta: float | None = None
    """Inverse temperature β (1/eV) used by the solver."""

    n_cycles: int | None = None
    """Requested QMC / solver cycles (screening knob)."""

    n_warmup_cycles: int | None = None
    """Requested warmup / thermalization cycles."""

    # Wannier input refs (P3.2 contract)
    wannier_work_dir: str | None = None
    """Handle to the Wannier work directory consumed as input."""

    wannier_chk_path: str | None = None
    """Handle to the Wannier ``.chk`` (preferred P3.2 artifact) when used."""

    wannier_ready_for_dmft: bool | None = None
    """Snapshot of ``WannierResult.ready_for_dmft`` at launch (or bypass)."""

    gate_notes: str = ""
    """Human reason for the Wannier-gate decision or refusal."""

    failure_class: str | None = None
    """Primary failure class when not ok.

    Known values: ``wannier_gate``, ``solver_missing``, ``not_converged``,
    ``binary_missing``, ``import_error``, ``other``.
    """

    work_dir: str | None = None
    """DMFT work directory (sibling ``dmft/``; never overwrites Wannier/DFT+U)."""

    raw: dict[str, Any] = Field(default_factory=dict)
    """Catch-all: parse notes, solver stdout excerpts, P3.4 extension hooks."""

    provenance: Provenance = Field(default_factory=Provenance)

    def summary_line(self) -> str:
        """Short human-readable summary for cards / CLI."""
        bits: list[str] = []
        bits.append(f"solver={self.solver}")
        bits.append(f"converged={self.converged}")
        if self.U_eV is not None:
            bits.append(f"U={self.U_eV:g} eV")
        if self.J_eV is not None and self.J_eV > 0:
            bits.append(f"J={self.J_eV:g} eV")
        if self.filling is not None:
            bits.append(f"n={self.filling:g}")
        elif self.occupancy_summary:
            occ = ",".join(
                f"{k}={v:g}" for k, v in sorted(self.occupancy_summary.items())[:4]
            )
            bits.append(f"occ({occ})")
        if self.mass_enhancement is not None:
            bits.append(f"m*/m={self.mass_enhancement:g}")
        if self.leading_pairing_eigenvalue is not None:
            bits.append(f"λ_pair={self.leading_pairing_eigenvalue:g}")
        if self.pairing_symmetry:
            bits.append(f"sym={self.pairing_symmetry}")
        if self.failure_class:
            bits.append(f"fail={self.failure_class}")
        bits.append(f"status={self.status}")
        return "; ".join(bits)


class SiFeasibilityComponents(BaseModel):
    """Individual terms that feed the composite Silicon Feasibility Score.

    Each component is on a 0–100 scale (higher = more feasible). Weights are
    applied in :class:`SiFeasibilityScore` / ``silicon.feasibility`` and are
    YAML-overridable via ``CampaignConfig.si_feasibility.weights`` (P2.1).
    """

    lattice_mismatch: float = Field(default=50.0, ge=0.0, le=100.0)
    """Score from epitaxial lattice mismatch to Si (or buffer)."""

    thermal_budget: float = Field(default=50.0, ge=0.0, le=100.0)
    """Score from process thermal-budget compatibility with CMOS."""

    chemical_compatibility: float = Field(default=50.0, ge=0.0, le=100.0)
    """Rule-based chemical / interdiffusion compatibility with Si."""

    buffer_availability: float = Field(default=50.0, ge=0.0, le=100.0)
    """Whether suitable buffer stacks exist in the library."""

    process_maturity: float = Field(default=50.0, ge=0.0, le=100.0)
    """Heuristic for industrial / lab process maturity of the family."""

    @field_validator(
        "lattice_mismatch",
        "thermal_budget",
        "chemical_compatibility",
        "buffer_availability",
        "process_maturity",
    )
    @classmethod
    def _clamp_component(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError("Si-feasibility component scores must be in [0, 100]")
        return float(v)

    def as_dict(self) -> dict[str, float]:
        """Return component scores as a plain dict (stable key order)."""
        return {
            "lattice_mismatch": float(self.lattice_mismatch),
            "thermal_budget": float(self.thermal_budget),
            "chemical_compatibility": float(self.chemical_compatibility),
            "buffer_availability": float(self.buffer_availability),
            "process_maturity": float(self.process_maturity),
        }


class SiFeasibilityScore(BaseModel):
    """Composite Silicon Feasibility Score (0–100) with component breakdown."""

    total: float = Field(default=50.0, ge=0.0, le=100.0)
    """Weighted composite score; higher means more Si-process friendly."""

    components: SiFeasibilityComponents = Field(default_factory=SiFeasibilityComponents)
    """Per-term breakdown for transparency and re-weighting."""

    weights: dict[str, float] = Field(default_factory=dict)
    """Active component weights used to form ``total`` (normalized; sum ≈ 1).

    Keys match :class:`SiFeasibilityComponents`. Empty only for legacy scores
    created before P2.1; the scorer always populates this field.
    """

    lattice_mismatch_pct: float | None = None
    """Raw lattice mismatch percentage vs Si (or vs chosen buffer)."""

    recommended_buffers: list[str] = Field(default_factory=list)
    """Suggested buffer-layer stacks (names / formulas; may include multi-layer e.g. AlN/TiN)."""

    recommended_thickness_nm: float | tuple[float, float] | None = None
    """Recommended film thickness in nm (P2.3: scalar from critical thickness; legacy (min, max) band also accepted)."""

    notes: str = ""
    """Human-readable rationale or caveats."""

    version: str = "0.1"
    """Scoring-rule version for provenance (see ``silicon.feasibility.SCORER_VERSION``)."""

    chemical_flags: list[str] = Field(default_factory=list)
    """P2.2: rule-based chemical / process-window flags for the recommended path
    (e.g. nitrogen_window, oxygen_window, interdiffusion_caution)."""

    thermal_window_note: str = ""
    """P2.2: short thermal-window note for the recommended buffer/stack path."""

    process_temp_ceiling_c: float | None = None
    """P2.2: heuristic process-temperature ceiling (°C) for film + recommended stack."""

    # --- P2.3 critical thickness + membrane transfer (additive) ---
    critical_thickness_nm: float | None = None
    """P2.3: primary critical thickness h_c (nm), usually Matthews–Blakeslee."""

    critical_thickness_method: str = ""
    """P2.3: method label (Matthews–Blakeslee / People–Bean / heuristic fallback)."""

    critical_thickness_people_bean_nm: float | None = None
    """P2.3: optional People–Bean metastable h_c (nm) for audit."""

    critical_thickness_inputs: dict[str, Any] = Field(default_factory=dict)
    """P2.3: key model inputs (mismatch, Burgers vector, Poisson ratio, …)."""

    membrane_transfer_candidate: bool = False
    """P2.3: rule-based flag — membrane transfer may help (not FEM)."""

    membrane_transfer_note: str = ""
    """P2.3: short membrane-transfer heuristic note for cards / export."""

    @field_validator("total")
    @classmethod
    def _validate_total(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError("Si-feasibility total score must be in [0, 100]")
        return float(v)
