"""Campaign configuration models (YAML-loadable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import math
import yaml
from pydantic import BaseModel, Field, field_validator


class QualityConfig(BaseModel):
    lambda_suspect_above: float = Field(default=3.0, ge=0.0)
    lambda_unreliable_above: float = Field(default=8.0, ge=0.0)
    min_frequency_cm1_soft: float = Field(default=50.0)
    imaginary_modes_unreliable: bool = True
    suspect_performance_penalty: float = Field(default=0.45, ge=0.0, le=1.0)
    unreliable_performance_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    unreliable_zero_performance: bool = True
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
    performance_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    si_feasibility_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    prefer_dynamically_stable: bool = True
    prefer_low_hull: bool = True
    quality: QualityConfig = Field(default_factory=QualityConfig)


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


class ActiveLearningConfig(BaseModel):
    enabled: bool = False
    strategy: Literal["uncertainty_si_tc"] = "uncertainty_si_tc"
    max_epw_jobs: int = Field(default=5, ge=1)
    weights: ActiveLearningWeights = Field(default_factory=ActiveLearningWeights)
    tc_ceiling_K: float = Field(default=40.0, gt=0.0)
    evaluate_deferred_with_surrogate: bool = True
    al_root: str | None = None
    version: str = "0.2-flywheel"


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
    supercell: list[int] = Field(default_factory=lambda: [2, 2, 1])
    b_concentrations: list[float] = Field(default_factory=list)
    bsi_supercell: list[int] = Field(default_factory=lambda: [2, 2, 2])
    seed: int = 42
    max_candidates: int = Field(default=50, ge=1)
    epitaxy_orientation: Literal["auto", "cube_on_cube", "45deg"] = "auto"
    use_buffers: bool = True


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


class DFTConfig(BaseModel):
    engine: Literal["mock", "qe", "qe-epw"] = "mock"
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
    quality_tag: Literal["screening", "production"] = "screening"


class JosephsonConfig(BaseModel):
    enabled: bool = False
    shortlist_size: int = 20
    model_tier: str = "analytic_AB"
    reference_area_um2: float = 1.0
    assume_SIS: bool = True
    temperature_K: float | None = None
    secondary_ranking: bool = False


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
