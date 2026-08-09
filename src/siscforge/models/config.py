"""Campaign configuration models (YAML-loadable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class QualityConfig(BaseModel):
    """Thresholds for result-quality / trust assessment (screening honesty).

    Pathological screening λ (often inflated by soft modes / coarse grids /
    random Wannier) must not be treated as production truth. This is a trust
    layer — not a substitute for denser-grid refinement.
    """

    lambda_suspect_above: float = Field(default=3.0, ge=0.0)
    """λ ≥ this → ``high_lambda`` flag and at least ``screening_suspect``."""

    lambda_unreliable_above: float = Field(default=8.0, ge=0.0)
    """λ ≥ this → ``extreme_lambda`` / ``unreliable`` tier."""

    min_frequency_cm1_soft: float = Field(default=50.0)
    """Phonon min frequency below this (but ≥ 0) → ``soft_modes`` flag."""

    imaginary_modes_unreliable: bool = True
    """If True, imaginary modes force ``unreliable``; else ``screening_suspect``."""

    suspect_performance_penalty: float = Field(default=0.45, ge=0.0, le=1.0)
    """Multiply composite by this when ``result_quality=screening_suspect``."""

    unreliable_performance_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    """Multiply composite by this when ``result_quality=unreliable``
    (near-zero performance weight effectively)."""

    unreliable_zero_performance: bool = True
    """If True, set performance term to 0 for ``unreliable`` (Si score only)."""

    prefer_higher_quality_tier: bool = True
    """When sorting, break ties / near-ties by quality tier before raw Tc."""

    version: str = "0.1"


class SiFeasibilityWeights(BaseModel):
    """YAML-overridable component weights for the Silicon Feasibility Score.

    Defaults match ``siscforge.silicon.feasibility.COMPONENT_WEIGHTS`` so
    behaviour is unchanged when this block is omitted. Weights need not sum
    to 1.0 — they are normalized at score time.

    Config path: ``si_feasibility.weights.<component>`` (campaign YAML).
    Component keys match :class:`~siscforge.models.results.SiFeasibilityComponents`.
    """

    lattice_mismatch: float = Field(default=0.35, ge=0.0)
    thermal_budget: float = Field(default=0.20, ge=0.0)
    chemical_compatibility: float = Field(default=0.20, ge=0.0)
    buffer_availability: float = Field(default=0.10, ge=0.0)
    process_maturity: float = Field(default=0.15, ge=0.0)

    def as_dict(self) -> dict[str, float]:
        """Return weights as a plain dict (stable key order)."""
        return {
            "lattice_mismatch": float(self.lattice_mismatch),
            "thermal_budget": float(self.thermal_budget),
            "chemical_compatibility": float(self.chemical_compatibility),
            "buffer_availability": float(self.buffer_availability),
            "process_maturity": float(self.process_maturity),
        }


class SiFeasibilityConfig(BaseModel):
    """Campaign-level Silicon Feasibility scoring knobs (Phase 2 / P2.1).

    Override component weights so experimental collaborators can re-weight
    the breakdown without reading source. Ranking still uses the composite
    ``total``; multi-objective / Pareto ranking is a later package (P2.4).
    """

    weights: SiFeasibilityWeights = Field(default_factory=SiFeasibilityWeights)
    """Component weights for the Si-feasibility composite (see COMPONENT_WEIGHTS)."""

    cmos_limit_c: float = Field(default=450.0, ge=0.0)
    """Backend thermal-budget limit (°C) for the thermal_budget component."""


class RankingConfig(BaseModel):
    """Weights and options for multi-objective ranking."""

    performance_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    si_feasibility_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    prefer_dynamically_stable: bool = True
    """If True, candidates with imaginary phonons are demoted."""

    prefer_low_hull: bool = True
    """If True, lower energy_above_hull_proxy improves ranking slightly."""

    quality: QualityConfig = Field(default_factory=QualityConfig)
    """Result-quality / trust layer knobs (λ inflation, soft modes, …)."""


class FormationFilterConfig(BaseModel):
    """Heuristic formation-energy pre-filter (Phase 0 stub; not a trained GNN).

    Candidates with ``energy_above_hull_proxy`` above ``max_e_hull_eV_per_atom``
    are dropped before expensive calculators run. Set ``enabled: false`` to
    keep the full enumerated set.
    """

    enabled: bool = True
    max_e_hull_eV_per_atom: float = Field(default=0.25, ge=0.0)
    """Reject candidates whose hull proxy exceeds this (eV/atom)."""

    max_strain_magnitude: float | None = Field(default=0.05, ge=0.0)
    """Optional: reject |in_plane_strain| above this fraction (None = no limit)."""

    prefer_families: list[str] = Field(
        default_factory=lambda: ["tm_nitride", "b_doped_si", "mgb2_boride"]
    )
    """Families that receive a stability bonus in the proxy."""

    keep_top_n: int | None = Field(default=None, ge=1)
    """After filtering, keep only the N lowest-hull candidates (None = keep all)."""

    version: str = "0.1-heuristic"


class TcLambdaSurrogateConfig(BaseModel):
    """λ / Tc surrogate stub for pre-filtering before EPW (Phase 1).

    Disabled by default so existing campaigns are unchanged. When enabled,
    candidates can be dropped by min predicted Tc, max uncertainty, or top-k.
    Real ``ElectronPhononResult`` always takes precedence for ranking when present.
    """

    enabled: bool = False
    """Master switch for surrogate pre-filter + optional ranking fill-in."""

    mu_star: float = Field(default=0.10, ge=0.0, le=0.3)
    """μ* used when converting predicted (λ, ω_log) → Allen–Dynes Tc."""

    min_predicted_tc_K: float | None = Field(default=None, ge=0.0)
    """Reject candidates with predicted Tc below this (None = no Tc cut)."""

    max_uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)
    """Reject candidates with relative uncertainty above this (None = no cut)."""

    keep_top_n: int | None = Field(default=None, ge=1)
    """After scoring, keep only the N highest surrogate scores (None = keep all)."""

    use_for_ranking_when_no_epw: bool = True
    """If True and no real e-ph Tc is available, fill performance_score from the
    surrogate (clearly labeled in notes / export columns)."""

    version: str = "0.1-family-heuristic"


class SurrogateConfig(BaseModel):
    """ML / heuristic surrogate knobs for campaigns."""

    tc_lambda: TcLambdaSurrogateConfig = Field(default_factory=TcLambdaSurrogateConfig)
    """λ / ω_log / Tc pre-filter stub (not a trained production GNN)."""


class ActiveLearningWeights(BaseModel):
    """Weights for the uncertainty_si_tc acquisition score (need not sum to 1)."""

    uncertainty: float = Field(default=0.4, ge=0.0)
    predicted_tc: float = Field(default=0.3, ge=0.0)
    si_feasibility: float = Field(default=0.3, ge=0.0)
    hull_penalty: float = Field(default=0.1, ge=0.0)
    """Soft penalty weight for high energy_above_hull_proxy."""


class ActiveLearningConfig(BaseModel):
    """Active-learning prioritization + Phase 1.5 flywheel hooks.

    Disabled by default. When enabled, orders the queue and selects a top-k
    subset for the expensive calculator. Surrogate retrain is **not** automatic
    inside ``run`` — promote clean labels with ``al-promote`` then
    ``al-train``. Shared state lives under ``al_root`` (default ``./al_state``).
    """

    enabled: bool = False
    strategy: Literal["uncertainty_si_tc"] = "uncertainty_si_tc"
    """Acquisition strategy name (extensible later)."""

    max_epw_jobs: int = Field(default=5, ge=1)
    """How many candidates receive the expensive calculator (QE/EPW/mock)."""

    weights: ActiveLearningWeights = Field(default_factory=ActiveLearningWeights)
    tc_ceiling_K: float = Field(default=40.0, gt=0.0)
    """Normalize predicted Tc by this ceiling for the acquisition feature."""

    evaluate_deferred_with_surrogate: bool = True
    """If True, non-selected candidates still get surrogate-only evaluations
    so the final ranked table includes the full shortlist pool."""

    al_root: str | None = None
    """Shared AL state root (training_set/ + models/). None → ``$SISC_AL_ROOT``
    or ``./al_state``. Do not point this at a campaign output directory."""

    version: str = "0.2-flywheel"



class CandidateSpec(BaseModel):
    """One exact structure point for desktop shortlists (formula × strain).

    When ``structure_cif`` is set, the CIF is used as-is (preserves the AL
    structure). Otherwise the structure is rebuilt from ``formula`` + strain.
    """

    formula: str
    in_plane_strain: float = 0.0
    substrate: str = "Si(001)"
    material_family: Literal[
        "tm_nitride",
        "b_doped_si",
        "mgb2_boride",
        "nickelate",
        "cuprate",
        "other",
    ] = "tm_nitride"
    candidate_id: str | None = None
    """Optional preserved id from the AL dry-run store."""

    structure_cif: str | None = None
    """Optional CIF text; when set, strain is **not** re-applied."""

    metadata: dict[str, Any] = Field(default_factory=dict)


class EnumerationConfig(BaseModel):
    """Parameters controlling structure enumeration.

    Supported material families (Phase 0):
    - ``tm_nitride``: binary rocksalt nitrides and simple ternary AₓB₁₋ₓN
    - ``b_doped_si``: heavily boron-doped silicon supercells

    For desktop shortlists prefer ``candidate_specs`` (exact formula×strain rows)
    instead of a full composition × strain grid.
    """

    material_families: list[str] = Field(default_factory=lambda: ["tm_nitride"])

    formulas: list[str] = Field(default_factory=list)
    """Optional explicit formulas (e.g. ``NbN``, ``Nb0.5Ti0.5N``).

    When empty, binaries are taken from ``metals`` and ternaries from
    ``ternary_metals`` × ``x_values``. Ignored when ``candidate_specs`` is set.
    """

    metals: list[str] = Field(default_factory=list)
    """Transition metals for binary MN nitrides (default: Nb, Ti, Zr, Hf)."""

    ternary_metals: list[str] = Field(default_factory=list)
    """Exactly two metals for AₓB₁₋ₓN enumeration (e.g. ``[Nb, Ti]``)."""

    x_values: list[float] = Field(default_factory=list)
    """Stoichiometry grid for ternary AₓB₁₋ₓN (x = fraction of first metal)."""

    candidate_specs: list[CandidateSpec] = Field(default_factory=list)
    """Exact shortlist rows (formula × strain [× optional CIF]). When non-empty,
    only these candidates are generated — no full grid expansion."""

    substrates: list[str] = Field(default_factory=lambda: ["Si(001)"])
    """Substrate labels used for strain application and Si-scoring."""

    strain_values: list[float] = Field(default_factory=lambda: [0.0])
    """Biaxial in-plane strain fractions applied to bulk (e.g. -0.02, 0.0, 0.02)."""

    poisson_ratio: float = Field(default=0.25, ge=0.0, le=0.5)
    """Poisson ratio used when relaxing the out-of-plane lattice under biaxial strain."""

    supercell: list[int] = Field(default_factory=lambda: [2, 2, 1])
    """Supercell size for ternary substitution (must yield enough metal sites)."""

    b_concentrations: list[float] = Field(default_factory=list)
    """B atomic fractions for ``b_doped_si`` (e.g. 0.05, 0.10)."""

    bsi_supercell: list[int] = Field(default_factory=lambda: [2, 2, 2])
    """Supercell for B:Si generation."""

    seed: int = 42
    """RNG seed for reproducible random-substitution ternaries."""

    max_candidates: int = Field(default=50, ge=1)

    epitaxy_orientation: Literal["auto", "cube_on_cube", "45deg"] = "auto"
    """Si-feasibility epitaxy matching for rocksalt nitrides (Phase 2).

    - ``auto``: choose best of cube-on-cube vs 45° (and buffers when enabled)
    - ``cube_on_cube``: conventional *a* vs *a*_Si
    - ``45deg``: diagonal *a*√2 vs *a*_Si
    """

    use_buffers: bool = True
    """If True, Si-feasibility may assume a buffer from the minimal library."""


class CalculatorConfig(BaseModel):
    """Which calculators to run and with what overrides."""

    name: str = "mock"
    """Registered calculator name (``mock``, ``qe``, ``quantum-espresso``)."""

    parameters: dict[str, Any] = Field(default_factory=dict)


class EPWConfig(BaseModel):
    """Electron-phonon Wannier (EPW) + isotropic Tc settings (Phase 1).

    Workstation defaults are intentionally coarse (**screening**). For denser
    workstation or production campaigns:

    1. Raise ``nkf`` / ``nqf`` (and matching DFPT ``qpoints`` / ``nqc``).
    2. Set parent ``DFTConfig.quality_tag`` to ``production`` so exports and
       results clearly distinguish screening vs better settings.
    3. See ``siscforge.calculators.qe.epw_inputs.recommended_grids`` and
       docs/examples/nbN_epw.md for NbN / MgB₂ grid ladders.

    **Coarse electronic k (nkc):** for ``production`` / refine
    ``workstation_dense`` on ≥8-atom cells, SiSC-Forge enforces a **minimum
    8³** Wannier mesh (auto-raise unless ``strict_coarse_k``). Never use 6³
    on those cells — Wannier90 ``kmesh_get_bvector`` fails after DFPT.
    ``nqc`` must match the DFPT q-mesh and is **not** auto-changed after
    phonons complete.
    """

    enabled: bool = False
    """When True (or calculator is ``qe-epw``), run EPW after phonon."""

    # Coarse (screening) vs denser grids — EPW nk/nq are interpolation grids
    nkf: list[int] = Field(default_factory=lambda: [6, 6, 6])
    """Fine k-grid for electron-phonon interpolation (screening default 6³).
    Workstation denser: 12³; production-oriented: 18³+."""

    nqf: list[int] = Field(default_factory=lambda: [6, 6, 6])
    """Fine q-grid for electron-phonon interpolation (screening default 6³)."""

    nkc: list[int] = Field(default_factory=lambda: [4, 4, 4])
    """Coarse k-grid consistent with Wannierization / NSCF.

    Dense tiers auto-raise undersized meshes (min 8³ for ≥8-atom cells).
    """

    nqc: list[int] = Field(default_factory=lambda: [2, 2, 2])
    """Coarse q-grid (**must match DFPT q-mesh** when possible)."""

    nbndsub: int | None = None
    """Number of Wannier bands (target WFs). None → auto from dft.nbnd / cell
    size when ``auto_nbndsub`` is True (screening default). Explicit values
    below the auto floor may be raised when auto_nbndsub is True."""

    auto_nbndsub: bool = True
    """When True and quality is screening, compute a safe nbndsub from nbnd
    and structure size instead of a tiny fixed default (e.g. 10). Prevents
    Wannier90 ``More states in the frozen window than target WFs`` on
    supercells with large nbnd."""

    wannier_retry_on_froz_overflow: bool = True
    """If EPW fails with frozen-window overflow, retry epw.x once with a
    larger nbndsub (screening only). Mid-step resume reuses save/nscf."""

    auto_retry_kmesh: bool = True
    """If EPW fails with Wannier90 ``kmesh_get_bvector`` / not enough
    bvectors **after DFPT is complete**, retry EPW-only:

    * **Phase A** — denser coarse k (6→8→12, max ``max_kmesh_retries``) +
      re-NSCF when nkc changes.
    * **Phase B** — when the nk ladder is exhausted, raise Wannier90
      ``search_shells`` (default→36→48) without changing nkc/nq or DFPT
      (see ``auto_retry_search_shells``).

    **Never** deletes or re-runs finished phonon / DFPT."""

    max_kmesh_retries: int = Field(default=2, ge=0, le=4)
    """Cap on post-DFPT Phase-A coarse-k remediation attempts (default 2)."""

    auto_retry_search_shells: bool = True
    """After Phase A (nk ladder) is exhausted, still retry EPW-only with
    larger Wannier90 ``search_shells`` (via EPW ``wdata``). Same failure
    class (``kmesh_bvector`` only). Does not change nkc or DFPT."""

    max_search_shells_retries: int = Field(default=2, ge=0, le=4)
    """Cap on Phase-B ``search_shells`` remediation attempts (default 2)."""

    search_shells: int | None = Field(default=None, ge=1, le=256)
    """Wannier90 ``search_shells`` (neighbour-shell search for b-vectors).

    ``None`` → omit from ``epw.in`` (Wannier90 default is 12). Remediation
    may set 36 or 48. Passed as EPW ``wdata`` so epw.x/Wannier90 see it."""

    kmesh_tol: float | None = Field(default=None, gt=0.0)
    """Optional Wannier90 ``kmesh_tol`` (shell-distance tolerance).

    ``None`` → omit (W90 default 1e-6). Only set when intentionally
    relaxing shell matching; auto-remediation does **not** loosen this
    by default (conservative)."""

    strict_coarse_k: bool = False
    """When True, refuse to auto-raise undersized coarse k and fail preflight
    instead. Default False: auto-raise with a clear log line."""

    bands_skipped: int = 0
    """Bands below the Wannier window to skip."""

    mu_star: float = Field(default=0.10, ge=0.0, le=0.3)
    """Coulomb pseudopotential μ* for Allen–Dynes / Eliashberg."""

    fsthick: float = 0.4
    """Fermi surface thickness (eV) for EPW sampling."""

    degaussw: float = 0.05
    """Smearing for electronic delta functions (eV)."""

    degaussq: float = 0.05
    """Smearing for phonons in EPW (eV)."""

    eps_acustic: float = 5.0
    """Lower bound on phonon frequency (cm⁻¹) for a2F / λ. Raise (e.g. 50)
    to suppress soft/imaginary modes that otherwise inflate λ."""

    eliashberg: bool = True
    """If True, request isotropic Eliashberg in EPW when supported."""

    allen_dynes_fallback: bool = True
    """Always compute Allen–Dynes Tc from λ, ω_log as a robust fallback."""

    wdata_prefix: str = "siscforge"
    """Prefix for wannier90 / EPW data files."""

    npool: int = 1
    """EPW k-point pools (``epw.x -npool``).

    For fine-grid EPW, EPW requires ``nproc == npool × nimage`` with ``nimage=1``,
    so **npool should equal dft.nproc** on a desktop (e.g. both 8). If left at 1
    while ``dft.nproc > 1``, SiSC-Forge auto-sets ``npool = nproc`` before launch
    unless ``strict_parallel`` is True.
    """

    strict_parallel: bool = False
    """When True, refuse to auto-fix an inconsistent (nproc, npool) topology
    and fail before calling epw.x. Default False: auto-set npool=nproc."""


class DFTConfig(BaseModel):
    """DFT / phonon / EPW engine settings for the Quantum ESPRESSO calculator.

    Used when the active calculator is ``qe`` or ``qe-epw`` (ignored for mock).
    """

    engine: Literal["mock", "qe", "qe-epw"] = "mock"
    """Preferred engine name (CLI ``--calculator`` overrides)."""

    ecutwfc: float = 50.0
    """Plane-wave kinetic-energy cutoff (Ry)."""

    ecutrho: float = 400.0
    """Charge-density cutoff (Ry)."""

    kpoints: list[int] = Field(default_factory=lambda: [4, 4, 4])
    """Monkhorst–Pack k-grid for SCF / relax."""

    conv_thr: float = 1.0e-8
    """Electronic convergence threshold (Ry)."""

    forc_conv_thr: float = 1.0e-3
    """Force convergence threshold for ionic relaxation (a.u.)."""

    press_conv_thr: float = 0.5
    """Pressure convergence threshold for cell relaxation (kbar)."""

    occupations: str = "smearing"
    smearing: str = "mv"
    degauss: float = 0.02
    """Marzari–Vanderbilt smearing width (Ry) for metals."""

    nbnd: int | None = None
    """Number of Kohn–Sham bands. Metals + DFPT/EPW need empty states; when
    unset and occupations use smearing with phonon/EPW, a screening default
    is applied in the input builders."""

    pseudo_dir: str | None = None
    """Directory containing UPF pseudopotentials. Required for real QE runs."""

    pseudopotentials: dict[str, str] = Field(default_factory=dict)
    """Element → UPF filename map. Auto-guessed from ``pseudo_dir`` when empty."""

    work_dir: str | None = None
    """Base working directory for QE runs (default: ``{output_dir}/qe_work``)."""

    nproc: int = 1
    """MPI ranks for ``pw.x`` / ``ph.x`` (``mpirun -np N`` when > 1)."""

    phonon_method: Literal["dfpt", "gamma", "phonopy_fd"] = "dfpt"
    """``dfpt`` = ph.x DFPT; ``gamma`` = Gamma-only; ``phonopy_fd`` = optional FD."""

    phonopy_supercell: list[int] = Field(default_factory=lambda: [2, 2, 2])
    """Supercell for optional phonopy finite-displacement phonons."""

    phonopy_distance: float = 0.01
    """Displacement amplitude (Å) for phonopy FD (when enabled)."""

    qpoints: list[int] = Field(default_factory=lambda: [2, 2, 2])
    """q-grid for DFPT (ignored for pure Gamma)."""

    tr2_ph: float = 1.0e-12
    """DFPT threshold."""

    ph_alpha_mix: float = 0.3
    """DFPT Broyden mixing factor ``alpha_mix(1)``. Lower (0.1–0.3) for metals
    / soft modes that otherwise diverge (``|ddv_scf| → ∞``)."""

    ph_nmix: int = 8
    """Number of iterations used in DFPT potential mixing (``nmix_ph``)."""

    ph_niter: int = 100
    """Max DFPT SCF iterations per irreducible representation (``niter_ph``)."""

    do_relax: bool = True
    """Run ionic/cell relaxation before SCF + phonon."""

    do_phonon: bool = True
    """Run phonon step after SCF."""

    phonon_retry_on_d_matrix: bool = True
    """If True (default), when ``ph.x`` fails with PAW ``d_matrix`` /
    non-orthogonal ``D_S`` after a successful SCF, re-run SCF once with
    ``nosym=.true.`` + ``noinv=.true.`` and retry phonon **once**.
    Conservative discovery recovery — never marks success without JOB DONE.
    Set False to fail immediately with diagnostic notes only."""

    phonon_retry_on_fft_symmetry: bool = True
    """If True (default), when ``ph.x`` fails with ``phq_setup`` /
    ``FFT grid incompatible with symmetry`` (ordered ternary / low-symmetry
    cells after SCF), re-run SCF once with ``nosym=.true.`` + ``noinv=.true.``
    and retry phonon **once**. Same recovery shape as d_matrix; does not
    change defaults for successful phonons. Set False to fail immediately."""

    do_epw: bool = False
    """Run EPW + isotropic Tc after phonon (requires epw.x). Also set via EPWConfig.enabled."""

    epw: EPWConfig = Field(default_factory=EPWConfig)
    """EPW / Eliashberg knobs (used when do_epw or calculator is qe-epw)."""

    quality_tag: Literal["screening", "production"] = "screening"
    """Propagated to SCF / Phonon / ElectronPhononResult. Use ``screening`` for
    coarse workstation defaults; ``production`` when grids/projections are
    intentionally denser (label only — raise grids in YAML yourself)."""


class JosephsonConfig(BaseModel):
    """Josephson module settings (ignored until Phase 3)."""

    enabled: bool = False
    shortlist_size: int = 20
    model_tier: str = "analytic_AB"
    reference_area_um2: float = 1.0
    assume_SIS: bool = True
    temperature_K: float | None = None
    secondary_ranking: bool = False


class RunConfig(BaseModel):
    """Resume / checkpoint / error-handling knobs for ``siscforge run``.

    Workstation multi-candidate EPW jobs are long; these defaults make re-launch
    after sleep, reboot, or a single failure safe:

    - ``resume``: skip candidates with a successful evaluation already in the
      campaign ``output_dir`` store (match by candidate_id, then fingerprint).
    - ``continue_on_error``: record failed candidates and proceed (default);
      set false or pass ``--fail-fast`` to abort on first calculator error.
    - ``force_rerun``: ignore existing successes and recompute everything.
    """

    resume: bool = True
    """When True, skip candidates that already have a successful evaluation
    in the campaign store under ``output_dir``."""

    continue_on_error: bool = True
    """When True, a failed candidate is stored as ``status=failed`` and the
    campaign continues; when False, the first hard error aborts the run."""

    force_rerun: bool = False
    """When True, ignore prior successes and re-run the expensive calculator
    (also disables mid-step QE workdir checkpoints for that candidate).
    Full redo of vc-relax → SCF → DFPT → EPW — not an EPW-only remediation."""

    resume_qe_steps: bool = True
    """When True, re-use successful upstream QE artifacts in the candidate
    workdir (vc-relax → SCF → phonon → EPW). Default on; applies when the
    candidate itself is not skipped by campaign-level resume.
    Finished phonon is sacred: EPW k-mesh remediation never deletes DFPT."""

    force_rerun_qe_steps: bool = False
    """When True, ignore workdir checkpoints and re-run every QE step.
    Implied by ``force_rerun``."""

    heartbeat_seconds: int = Field(default=900, ge=0)
    """While pw.x / ph.x / epw.x run, print a progress heartbeat every N seconds
    (step name, elapsed time, healthy/stale log). Default 900 (15 min).
    Set to 0 to disable. Desktop shortlists with multi-hour DFPT should keep
    this on so the CLI is not silent for hours."""

    estimate_walltime: bool = True
    """When True (default), print order-of-magnitude walltime bands at the
    start of real ``qe`` / ``qe-epw`` campaigns. Guidance only — not a
    guarantee. Mock / dry-run paths never print estimates."""

    walltime_scale: float = Field(default=1.0, gt=0.0, le=20.0)
    """Multiply heuristic walltime bands (e.g. 2.0 if your machine is slow)."""

    heartbeat_eta: bool = True
    """When True, heartbeats may append a rough remaining-time hint if the
    step log yields a real progress fraction (e.g. q-point i/N in ph.out).
    No fake precision when progress is not parseable."""


class CampaignConfig(BaseModel):
    """Top-level YAML campaign definition.

    Load with :meth:`CampaignConfig.from_yaml` or validate a dict via
    ``CampaignConfig.model_validate(...)``.
    """

    name: str = "unnamed_campaign"
    description: str = ""
    version: str = "0.1"

    dry_run: bool = False
    """Force mock calculator path when True (also set by CLI ``--dry-run``)."""

    enumeration: EnumerationConfig = Field(default_factory=EnumerationConfig)
    calculators: list[CalculatorConfig] = Field(
        default_factory=lambda: [CalculatorConfig(name="mock")]
    )
    dft: DFTConfig = Field(default_factory=DFTConfig)
    formation_filter: FormationFilterConfig = Field(default_factory=FormationFilterConfig)
    surrogate: SurrogateConfig = Field(default_factory=SurrogateConfig)
    """Optional λ/Tc (and future) surrogates for pre-filtering."""

    active_learning: ActiveLearningConfig = Field(
        default_factory=ActiveLearningConfig
    )
    """Optional top-k prioritization for expensive EPW jobs."""

    ranking: RankingConfig = Field(default_factory=RankingConfig)
    """Composite ranking (performance vs Si-total). Component weights live under
    ``si_feasibility`` (P2.1), not here."""

    si_feasibility: SiFeasibilityConfig = Field(default_factory=SiFeasibilityConfig)
    """Silicon Feasibility scorer knobs — component weights + CMOS limit (P2.1).

    YAML::

        si_feasibility:
          weights:
            lattice_mismatch: 0.35
            thermal_budget: 0.20
            chemical_compatibility: 0.20
            buffer_availability: 0.10
            process_maturity: 0.15
          cmos_limit_c: 450.0
    """

    josephson: JosephsonConfig = Field(default_factory=JosephsonConfig)

    run: RunConfig = Field(default_factory=RunConfig)
    """Resume / continue-on-error / force-rerun (desktop-friendly checkpoints)."""

    output_dir: str = "outputs"
    export_formats: list[Literal["json", "csv", "markdown"]] = Field(
        default_factory=lambda: ["json", "csv"]
    )

    extras: dict[str, Any] = Field(default_factory=dict)
    """Escape hatch for forward-compatible campaign keys."""

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("campaign name must be non-empty")
        return v

    @classmethod
    def from_yaml(cls, path: str | Path) -> CampaignConfig:
        """Load and validate a campaign YAML file."""
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Campaign YAML root must be a mapping: {path}")
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        """Serialize this config to YAML (for debugging / templates)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                self.model_dump(mode="json"),
                fh,
                default_flow_style=False,
                sort_keys=False,
            )
