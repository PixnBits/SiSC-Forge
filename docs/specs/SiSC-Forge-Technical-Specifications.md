# SiSC-Forge
## Technical Specifications

**Version 0.1 – Implementation Blueprint**  
**Companion to the Product Requirements Document**

This document is the authoritative technical specification for SiSC-Forge. Implementation of every module, data model, workflow, and acceptance criterion must be driven by and validated against this document.

---

## 1. Overall Architecture

SiSC-Forge uses a modular, layered, message-passing architecture. All inter-module communication occurs via strongly-typed Pydantic v2 models to guarantee schema stability and lossless JSON serialization for the database and HPC payloads.

**High-level component flow:**

```
User / Config Layer (YAML campaigns, CLI, Jupyter)
        │
Structure Generation & Enumeration
        │ StructureCandidate[]
ML Surrogate Layer (ALIGNN / MatGL + custom heads)
        │ Filtered + scored candidates
Workflow Engine & Active Learning Manager (jobflow + priority queue)
        │
   ┌────┴────┐
   │         │
DFT/DFPT/EPW  DMFT / Pairing
(QE + EPW)   (TRIQS)
   │         │
   └────┬────┘
        │ CalculationResult hierarchy
Silicon Integration & Interface Module
        │
Candidate Ranking & Reporting
        │
Provenance Database + Active-Learning Feedback
```

**Design principles**
- Decoupled engines: DFT, DMFT and Si modules are pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering: expensive EPW and DMFT jobs are launched only on candidates that survive surrogate thresholds and high acquisition score.
- Provenance-first (AiiDA-inspired principles implemented lightly via jobflow).
- Extensible via a Calculator registry.
- Identical code path from workstation (local backend) to HPC (SLURM / FireWorks / Dask).

**v0.1 Must-Have**  
Structure generation → ML formation-energy filter → jobflow QE-SCF + basic DFPT phonon → heuristic Si-score → MongoDB + ranking. Full EPW, DMFT and advanced active learning arrive later.

**Acceptance Criteria**
- A new calculation type can be added by implementing one Calculator class + Pydantic result model without touching the orchestrator core.
- End-to-end dry-run of 50 nitride candidates completes on a laptop with mocked DFT.
- All data objects serialize / deserialize losslessly to JSON.

---

## 2. Core Modules

### 2.1 Structure Generation & Enumeration

**Purpose**  
Generate chemically and crystallographically valid candidates relevant to silicon-compatible superconductivity: bulk phases, alloys, doped systems, strained epitaxial films, buffer heterostructures and freestanding membranes.

**Responsibilities**
- Parse `CampaignConfig` (YAML) specifying family, composition ranges, doping, substrate (Si(001)/Si(111)), max supercell size and strain windows.
- Enumerate using pymatgen, spglib, ICET (or ATAT) for SQS alloys, custom doped-supercell generators for B:Si, interface builders for coherent epitaxial stacks, affine strain application matched to a_Si = 5.4307 Å (optional c-axis relaxation), and vacuum-padded membrane models.
- Attach rich metadata (family, prototype, composition, doping, strain_tensor, interface_info, expected thermal budget, stoichiometry range).
- Deduplicate via StructureMatcher + composition hashing.
- Output: `List[StructureCandidate]`.

**v0.1 Must-Have**  
TM nitrides (binary + ternary SQS ≤ 32 atoms), heavily B-doped Si (64–216 atom supercells), simple MgB₂ epitaxial films, basic strain and mismatch calculation, YAML-driven campaigns, CIF / POSCAR / JSON export.

**Later**  
Full multi-layer cuprate buffers, automated defect enumeration, evolutionary structure prediction, surface reconstructions, explicit disordered alloys beyond SQS.

**Acceptance Criteria (v0.1)**  
- Nb₁₋ₓTiₓN (x = 0–1 in 0.1 steps) + 2 % strain campaign produces ≥ 50 unique symmetry-reduced structures in < 5 min on a workstation.  
- All structures validate (no overlaps, reasonable volumes).  
- Metadata completeness ≥ 95 %.  
- Round-trip serialization preserves all fields.

### 2.2 ML Surrogate Layer

**Purpose**  
Ultra-fast, uncertainty-aware prediction of formation energy, approximate phonon spectra, λ, ω_log and Tc proxy so that expensive stages run only on the most promising candidates. Enable active learning via calibrated uncertainties.

**Core Components**
- Backbones: ALIGNN or MatGL (M3GNet / CHGNet) + custom multi-task GNN heads (PyTorch Geometric / DGL) for E_form / E_hull, phonon moments, λ, ω_log, Tc_proxy.
- Uncertainty: ensemble (5–10 models) or evidential / Gaussian-process layer.
- Training: Materials Project + AFLOW + curated EPW data + continuous online addition of new high-fidelity results; strain-augmented data.
- Inference: batch GPU-optimized; target < 50 ms / structure on A100-class, < 200 ms on consumer GPU.

**v0.1 Must-Have**  
Formation-energy + basic stability surrogate, ensemble uncertainty, pre-filter (E_hull > 150 meV/atom threshold), retraining script that accepts new DFT results.

**Later**  
Full multi-task λ / Tc surrogates, sophisticated acquisition functions, family-specific fine-tuning, uncertainty calibration against DFT residuals.

**Acceptance Criteria**  
- Held-out MAE of formation energy < 50 meV/atom on TM nitrides + MgB₂ variants.  
- Throughput ≥ 20 structures / s on modern GPU.  
- Deterministic predictions given fixed checkpoint + structure.  
- Every prediction records model hash and training-data snapshot ID.

### 2.3 DFT / DFPT / EPW Orchestration

**Primary Goal**  
Automated, reproducible calculation of λ, ω_log, α²F(ω) and Tc for conventional superconductors, with explicit support for strained thin-film geometries.

**Stack**  
Quantum ESPRESSO (pw.x, ph.x, q2r.x, matdyn.x) + EPW primary. Optional VASP + phonopy path. Glue: pymatgen, ASE, jobflow recipes, custodian.

**v0.1 Must-Have**  
Automated SCF → DFPT phonon for cells ≤ 20–30 atoms; EPW coarse-grid matrix elements + interpolation; λ, ω_log, α²F; Allen-Dynes / McMillan proxy + isotropic Eliashberg Tc; fixed in-plane Si-matched strain with c-axis + internal relaxation; robust parsing, restart and provenance; structured `ElectronPhononResult` storage.

**Later**  
Anisotropic Eliashberg / SCDFT, mode-resolved λ, anharmonicity (SSCHA), automatic convergence protocols, hybrid / Hubbard-corrected EPW.

**Key jobflow steps**  
Relaxation → dense-k SCF → coarse-q DFPT → EPW (Wannierization if needed) → Eliashberg solver.

**Acceptance Criteria**  
- Recovers NbN Tc ≈ 15–17 K and MgB₂ Tc ≈ 39 K within 15–20 % under standard parameters.  
- Strained 4–8 atom cell completes full EPW + isotropic Eliashberg without manual intervention.  
- Imaginary phonons and poor Wannierization are detected and flagged with diagnostics.  
- Screening vs production `quality_tag` supported.

**Si-specific**  
Automatic generation of strained cells matched to Si a₀; support for polar slabs with dipole correction.

### 2.4 Unconventional (DMFT) Pathway

**Purpose**  
High-throughput-capable pathway for materials where phonon-mediated pairing is secondary (infinite-layer RNiO₂, bilayer nickelates, cuprates). Focus on DFT+DMFT + spin susceptibility / pairing eigenvalues.

**Stack**  
QE or VASP → Wannier90 → TRIQS / solid_dmft; impurity solvers (CTHYB etc.); custom or community pairing solvers.

**Early / v0.5 Must-Have**  
Automated DFT → Wannier pipeline for infinite-layer RNiO₂; single-shot or charge-self-consistent DFT+DMFT (paramagnetic); local and momentum-dependent spin susceptibility; leading pairing eigenvalue and symmetry as Tc proxy; storage of Σ(iωn), occupancy, χ, λ_pair.

**Later**  
Full frequency-dependent pairing vertex and Tc estimation, strain / doping maps, multi-orbital crystal-field treatment, phonon + spin-fluctuation coupling.

**Acceptance Criteria**  
- Recovers literature DMFT occupancy and mass enhancement for bulk NdNiO₂ under standard U, J.  
- Pipeline runs from StructureCandidate to pairing-eigenvalue report with minimal manual intervention for supported prototypes.  
- Clear failure flags for poor Wannierization or solver non-convergence.  
- Early campaigns default to DFT+U + static susceptibility proxies before full CT-HYB.

**Si-specific**  
Strained cells, oxygen-stoichiometry flags and process-window estimates (reduction step after perovskite growth).

### 2.5 Silicon Integration & Interface Module

**Responsibilities**  
Evaluate every candidate for realistic integration onto Si(001) or Si(111): epitaxial strain, critical thickness, buffer recommendations, thermal-budget constraints, quantitative Si-Feasibility Score (0–100), freestanding membrane transfer and proximity-effect estimates.

**v0.1 Must-Have**
1. Epitaxial Matching Engine – pymatgen + Zur–McGill / coincidental lattice matching; biaxial strain tensor and elastic energy density (DFT or ML elastic constants); mismatch %, strain energy (meV/Å²), recommended orientation.
2. Buffer-Layer Stack Suggestor – pre-populated library (TiN, ZrN, AlN, MgO, STO, YSZ, CeO₂ …); minimal sequences keeping cumulative mismatch < 3–4 % and chemical compatibility with Si; simple thermodynamic check for silicide / silicate interlayers.
3. Thermal Budget & Process Window Estimator – max process temperature from literature + calculated barriers; flag > 800 °C for > 10 min as high-risk for CMOS backend.
4. Si-Feasibility Score (tunable weights)  
   Score = 0.30×(1 – norm_mismatch) + 0.25×thermal_budget + 0.20×chemical_compatibility + 0.15×known_growth_evidence + 0.10×membrane_transfer_ease  
   (all components 0–1; final 0–100).

**Later**  
Full DFT interface slabs, AIMD interdiffusion, Usadel / microscopic proximity modeling, membrane strain-relaxation mechanics.

**Acceptance Criteria (v0.1)**  
- Any nitride or MgB₂ candidate returns complete matching report + score in < 30 s on a workstation (pre-computed elastic constants).  
- Score correlates with known experimental successes (NbN on Si, MgB₂ on buffered Si) within ±15 points.

### 2.6 Candidate Ranking & Reporting

**Purpose**  
Transparent multi-objective ranking that balances predicted superconducting performance against realistic silicon-integration feasibility, while surfacing uncertainty and synthesis-relevant metadata.

**Responsibilities**
- Ingest `CandidateEvaluation` objects.
- Compute Performance score (Eliashberg Tc or scaled pairing eigenvalue; fall back to ML Tc_proxy), SiFeasibilityScore, composite score (configurable weights), Pareto front in (Tc, Si_score) space, and acquisition score for AL.
- Generate ranked tables (CSV / Markdown / HTML), top-N candidate cards with key plots, and synthesis metadata cards (growth method, buffer stack, max T, pO₂ / pN₂ windows, mismatch, thermal-expansion notes).
- Flag high-risk candidates (imaginary phonons, high E_hull, extreme mismatch, oxygen-sensitive).
- Support filtering by family, min_Tc, min_Si_score, etc.

**v0.1 Must-Have**  
Weighted composite ranking using available data, simple Pareto sorting, JSON + CSV export, basic Markdown summary, CLI `siscforge rank --campaign-id X --top 50 --export results/`.

**Later**  
Interactive dashboard, uncertainty visualization, automatic experimental-recipe cards, literature comparison.

**Acceptance Criteria**  
- Ranking of 100-candidate set completes in < 10 s.  
- Changing weights via config immediately reorders correctly.  
- Exported synthesis metadata contains every field from the Silicon Integration module and is schema-validated.

### 2.7 Workflow Engine & Active Learning Loop

**Purpose**  
Orchestrate end-to-end campaigns with robust, restartable, provenance-tracked execution that scales from workstation to large HPC, and continuously improve surrogates via active learning.

**Core Technology (v0.1 locked)**  
jobflow + atomate2-style recipes primary; local ProcessPool / Dask or SLURM / FireWorks backends; optional later AiiDA provenance export. Custom lightweight AL coordinator maintaining a priority queue.

**Key Responsibilities**
- Load `CampaignConfig` (material families, composition ranges, calculation levels, resource limits, surrogate thresholds, AL parameters).
- Dynamically assemble calculation graphs (relaxation → SCF → DFPT → EPW → Eliashberg, or DFT → Wannier → DMFT → pairing) with automatic dependency management.
- Priority queue sorted by composite acquisition score = f(uncertainty, predicted_Tc, Si_feasibility, novelty). Resource-aware scheduling (cheap jobs first).
- Active-learning loop: after every N high-fidelity results (or on schedule), extract new data, trigger surrogate retrain / fine-tune, re-score remaining candidates, optionally generate new structures in high-uncertainty regions.
- Supported acquisition functions: Uncertainty Sampling, Expected Improvement on Tc × Si-score, UCB, custom multi-objective. Conventional and unconventional candidates may be treated in separate pools or with normalized proxies.
- Fault tolerance, automatic restart with backoff, full provenance (code versions, PP hashes, inputs, walltime, exit codes), campaign checkpointing.

**v0.1 Must-Have**  
jobflow recipes for QE relaxation + SCF + basic DFPT / phonopy, simple priority queue driven by formation-energy surrogate + heuristic Si-score, local + SLURM backends, MongoDB / file store with basic provenance, threshold-triggered retrain script, campaign YAML parser + CLI (`siscforge run campaign.yaml`).

**Later**  
Full EPW and DMFT recipes, sophisticated Bayesian / multi-fidelity AL, distributed GNN training, live web monitoring.

**Acceptance Criteria**  
- 20-candidate nitride campaign (stability + phonon) runs to completion on 16-core workstation without manual intervention.  
- Kill / restart mid-campaign recovers state correctly.  
- New calculation type requires only a new Job class + registration.  
- Provenance query returns full chain for any candidate’s final Tc.

---

## 3. Data Models / Key Objects

All core objects are Pydantic v2 models (or equivalent validated dataclasses) for type safety and serialization. Compatible with pymatgen Structure.

Key models:

- **Provenance** – source, parent_ids, timestamp, software_versions, campaign_id.
- **StructureCandidate** – id, structure (pymatgen), composition, family, prototype, tags, doping, strain_tensor, interface_info, energy_above_hull_proxy, provenance, metadata.
- **CalculationMetadata** – calc_id, engine, functional, kpoints, cutoff, hubbard_u, convergence_thresholds, walltime, ncores, status, quality_tag (“screening” | “standard” | “production”).
- **SCFResult**, **PhononResult**, **ElectronPhononResult** (λ, ω_log, α²F, mode-resolved λ, Tc_allen_dynes, Tc_eliashberg, Tc_scdft, mu_star, dominant_modes, converged), **DMFTResult** (self-energy, occupations, spin_susceptibility, leading_pairing_eigenvalue, pairing_symmetry, U, J, mass_enhancement).
- **SiFeasibilityComponents** / **SiFeasibilityScore** – lattice_mismatch_pct, strain_energy_meV_A2, thermal_budget_score, chemical_compatibility, buffer_complexity, known_experimental_evidence, membrane_transfer_ease, oxygen_or_nitrogen_window_score, composite_score (0–100), recommended_buffers, max_process_temp_C, notes.
- **CandidateEvaluation** – candidate_id, structure, scf / phonon / eph / dmft, si_score, ml_predictions, final_rank_score, pareto_front, acquisition_score, status, last_updated.
- Supporting: CampaignConfig, WorkflowTask, RankingReport, SynthesisMetadata.

**v0.1** implements StructureCandidate, SCFResult, basic SiFeasibilityScore, CandidateEvaluation. Full EPW / DMFT fields are added with those modules.

These models are the contract between all modules.

---

## 4. External Dependencies & Interfaces

**Core Scientific Stack (open-source primary)**  
pymatgen ≥ 2024, ASE, spglib, phonopy, seekpath, ICET (preferred) or ATAT, Quantum ESPRESSO ≥ 7.2 + EPW ≥ 5, Wannier90, TRIQS 3.x + solid_dmft, PyTorch ≥ 2.1 + PyTorch Geometric / DGL, ALIGNN or MatGL, jobflow + atomate2, custodian, MongoDB (preferred) or PostgreSQL + JSONB, numpy / scipy / pandas / matplotlib / plotly, pydantic v2, typer / click, ruamel.yaml.

**Optional**  
VASP 6.x (user-supplied license), FireWorks, Dask / Parsl, external Eliashberg solvers.

**Interfaces**  
All external codes wrapped by thin Calculator classes implementing a common protocol: `run(structure: StructureCandidate, params: dict) → ResultModel`.  
Pseudopotential management with hash tracking.  
Docker / Apptainer images for QE+EPW and TRIQS.  
Optional later REST / gRPC for ML inference.

**Licensing**  
Fully open path is mandatory and sufficient. VASP path is feature-flagged and never required.

---

## 5. Configuration & Input Formats

Primary: human-readable YAML validated by Pydantic models. Optional JSON for programmatic use.

**Example campaign YAML (v0.1 nitride focus)**

```yaml
campaign:
  id: "nbn_tin_alloy_strain_2026q3"
  name: "NbTiN epitaxial on Si"
  family: "tm_nitride"
  composition_space:
    elements: ["Nb", "Ti", "N"]
    stoichiometry_ranges: {Nb: [0.0, 1.0], Ti: [0.0, 1.0], N: [0.95, 1.05]}
  structure_generation:
    prototypes: ["rocksalt"]
    max_atoms: 32
    sqs: true
    strain:
      substrate: "Si(001)"
      a_Si: 5.4307
      mismatch_window_pct: [-3.0, 3.0]
      fix_in_plane: true
  ml_filter:
    max_e_hull_eV_atom: 0.15
    min_tc_proxy_K: 5.0
  dft:
    engine: "qe"
    functional: "PBE"
    ecutwfc: 80
    k_density: 5.0
    phonon_q_grid: [4, 4, 4]
    quality_tag: "screening"   # or "production"
  ranking:
    weights: {tc: 0.55, si_feasibility: 0.35, uncertainty: 0.10}
  active_learning:
    enabled: true
    batch_size: 20
    acquisition: "ucb"
```

**CLI**  
`siscforge enumerate | submit | rank | train-surrogate | run campaign.yaml` with sensible defaults and full schema validation.

---

## 6. Output Formats & Database Schema

**Database**  
MongoDB primary (flexible nested documents). Alternative: PostgreSQL + JSONB.  
Collections: candidates, calculations, campaigns, surrogate_models, rankings, provenance.  
Indexes on candidate_id, campaign_id, family, composite_score, status, composition / structure hashes.

**Export Formats**  
Full-fidelity JSON (including Structure.as_dict()), CIF / POSCAR, CSV summary tables, YAML synthesis cards, HDF5 / NetCDF for large arrays (α²F, self-energies), optional later Materials-Project-style REST API.

Every result records parent calculation IDs so the full chain (enumeration → ML → DFT → EPW / DMFT → ranking) is reconstructible.

**v0.1**  
MongoDB + JSON / CSV / CIF exports fully functional. Full provenance graph optional.

---

## 7. Performance & Scaling Targets

| Scale              | Hardware example          | Stability + basic phonon | Full EPW + Eliashberg | DMFT          |
|--------------------|---------------------------|---------------------------|-----------------------|---------------|
| Workstation (v0.1) | 16–32 cores + 1 GPU      | 50–200 / day             | 1–5 / day (small cells) | 0–1         |
| Small cluster      | 100–200 cores + few GPUs | 1k–5k / week             | 20–50 / week         | 5–10 / week  |
| Large HPC / Cloud  | 1k+ cores + GPU partition| 10⁴–10⁵ (ML-filtered)    | 100–500 selected     | 20–100 selected |

**Latency targets**  
Enumeration > 100 candidates / s (CPU); ML inference > 20 / s GPU; single 8-atom QE relax + SCF < 30 min on 16 cores; DFPT coarse < 2–4 h; full EPW 12–48 h (restricted to top-ranked only).

**Efficiency**  
Aggressive ML rejection so < 5–10 % of enumerated structures reach DFPT and < 1 % reach EPW / DMFT. Checkpointing for multi-day jobs. Near-linear scaling for independent structures.

**Acceptance**  
Documented benchmark suite; ML filtering reduces total DFT wall-time by ≥ 10× on a 1 000-candidate nitride campaign while retaining known high-Tc examples in the top 10 %.

---

## 8. Testing Strategy

- **Unit (pytest)** – structure generation validity, Pydantic round-trips, ML inference determinism and ranges, QE / EPW parsers against reference files, Si-feasibility scoring on known cases.
- **Integration** – end-to-end jobflow recipes on tiny cells (real QE short cutoffs or mocked), AL loop with synthetic data, database write / read of full CandidateEvaluation.
- **Regression / Scientific Validation** – golden systems (NbN, MgB₂, B-doped Si, NdNiO₂) with literature tolerances; marked `@slow` / `@requires_qe`; run on CI machines with QE or nightly.
- **Performance / Smoke** – enumeration of 100 structures < 60 s, ML batch timing, full dry-run campaign.
- **CI / CD** – GitHub Actions (unit + fast integration on every PR); optional self-hosted runner with QE; coverage ≥ 80 % on core modules; pre-commit (black, ruff, mypy).

**v0.1**  
Unit + mocked integration + one real QE phonon test on NbN. Golden suite grows with modules.

---

## 9. Development Phases & Milestones

**Phase 0 – Foundation (Workstation, 4–8 weeks)**  
StructureCandidate + core Pydantic models; structure gen for TM nitrides (SQS) + B-doped Si; basic Si-feasibility scoring; jobflow QE relax + SCF + DFPT / phonopy; simple ALIGNN / MatGL formation-energy surrogate; MongoDB + basic ranking; CLI; dry-run mode.  
**Exit** – Reproduce NbN phonon spectrum; generate and rank a 50-candidate Nb-Ti-N strain series; all unit tests green; full dry-run succeeds.

**Phase 1 – Conventional SC Pipeline (Workstation + small cluster, 6–10 weeks)**  
Full EPW + isotropic Eliashberg; λ / ω_log / Tc surrogates; active-learning loop; improved buffers and thermal scoring; MgB₂ support; screening / production tags.  
**Exit** – Automated recovery of MgB₂ Tc within 20 %; AL demonstrably improves surrogate; 200-candidate campaign runs on 64–128 cores with clear prioritization.

**Phase 2 – Unconventional + Advanced Si Integration (HPC, 3–4 months)**  
TRIQS DMFT + pairing for infinite-layer nickelates; full interface slabs and membrane models; multi-objective Pareto ranking and synthesis cards.  
**Exit** – End-to-end nickelate list with pairing proxy + realistic Si-buffer scores; publication-quality data package for at least one family.

**Phase 3 – Production & Device Awareness (ongoing)**  
Anisotropic / SCDFT, proximity / JJ estimates, generative models, cloud recipes, web UI, community data sharing, continuous surrogate improvement.

**Workstation Validation Philosophy**  
Every Phase 0 and Phase 1 feature must be demonstrable and scientifically correct on ≤ 32 cores + 1 GPU + ≤ 128 GB RAM. Large-scale discovery is then “just more of the same.”

Physics engines (EPW, DMFT) are independent Calculator plugins so delays in one never block the other. Surrogates start simple (formation energy) and grow; the pipeline never depends on a perfect λ predictor.

---

*This document is implementation-ready. A competent engineer familiar with the Python scientific stack, jobflow / pymatgen and Quantum ESPRESSO can begin coding modules directly from these specifications. All acceptance criteria, v0.1 vs later flags, and data-model contracts are explicit.*
