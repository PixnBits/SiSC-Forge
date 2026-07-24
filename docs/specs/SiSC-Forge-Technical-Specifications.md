# SiSC-Forge
## Technical Specifications

**Version 0.2 – Implementation-Ready Blueprint**  
*(Companion to PRD v0.2. Focus: stronger Silicon Integration, clearer Unconventional pathway interfaces, Josephson device modeling section, explicit v0.1 boundaries, actionable data/config/output contracts.)*

---

## 1. Overall Architecture

SiSC-Forge uses a modular, layered, message-passing architecture. All inter-module communication occurs via strongly-typed Pydantic v2 models.

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
( later ) Josephson Device Metrics Calculator
        │
Provenance Database + Active-Learning Feedback
```

**Design principles**
- Decoupled engines: pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering of expensive calculations.
- Provenance-first (lightweight jobflow implementation of AiiDA principles).
- Extensible Calculator registry (new calculators, including future Josephson metrics, register without core changes).
- Identical code path from workstation to HPC.

**v0.1 Must-Have**  
Structure generation → ML formation-energy filter → jobflow QE-SCF + basic DFPT phonon → heuristic Si-score → MongoDB + ranking.

---

## 2. Core Modules

### 2.1 Structure Generation & Enumeration
(Unchanged in scope. P0 for TM nitrides + B-doped Si with epitaxial strain.)

### 2.2 ML Surrogate Layer
(P0 = formation energy + uncertainty; P1 adds λ / ω_log / Tc proxies.)

### 2.3 DFT / DFPT / EPW Orchestration
(P0 = phonon; P1 = full EPW + isotropic Eliashberg.)

### 2.4 Unconventional (DFT+U / DMFT) Pathway  ← Strengthened

**Purpose**  
High-throughput pathway for materials where phonon-mediated pairing is secondary (infinite-layer nickelates, bilayer nickelates, cuprates). Produces a leading pairing eigenvalue that is used as the performance metric in ranking on equal footing with Eliashberg Tc.

**Stack**  
DFT (QE primary) → Wannier90 → TRIQS / solid_dmft. Impurity solver: CTHYB primary. Pairing from spin susceptibility or linearized Eliashberg in the spin-fluctuation channel.

**Clear Interfaces**
- **Input**: `StructureCandidate` (must carry family tag `"nickelate"` or `"cuprate"`, optional oxygen stoichiometry, strain tensor).
- **Intermediate**: DFT+U result always produced first and stored as `DFTUResult`.
- **Output**: `DMFTResult` containing:
  - `self_energy`, `occupancy`, `mass_enhancement`
  - `spin_susceptibility_summary`
  - `leading_pairing_eigenvalue` (float)
  - `pairing_symmetry` (str)
  - `U`, `J`, `double_counting`, `solver_info`
  - `quality_tag` (“screening” | “standard” | “production”)
  - `converged` (bool)
- The `leading_pairing_eigenvalue` (or a literature-calibrated temperature scale) is written into `CandidateEvaluation.performance_score` exactly as Eliashberg Tc is written for conventional candidates. Ranking and active-learning acquisition functions therefore require no special-case logic beyond a configurable normalization factor per family.

**v0.1 / Early**  
- Automated DFT → Wannier pipeline for infinite-layer RNiO₂ prototypes.  
- Single-shot or charge-self-consistent DFT+DMFT (paramagnetic).  
- Leading pairing eigenvalue extraction.  
- Oxygen-vacancy enumeration treated as a structure-generation degree of freedom.  
- DFT+U always run as a cheap proxy and stored alongside full DMFT.

**Later**  
Full frequency-dependent pairing vertex, realistic multi-orbital crystal fields under epitaxial strain, phonon + spin-fluctuation coupling, bilayer models.

**Acceptance Criteria**  
- Recovers literature occupancy and mass enhancement for bulk NdNiO₂ under standard U, J.  
- Pipeline produces a complete `DMFTResult` from a `StructureCandidate` with minimal manual intervention for supported prototypes.  
- Failure modes (poor Wannierization, solver non-convergence) are flagged and propagate to ranking as low-confidence.

### 2.5 Silicon Integration & Interface Module  ← Strengthened

**Purpose**  
Evaluate every candidate for realistic integration onto Si and produce a quantitative, transparent Silicon Feasibility Score (0–100) together with actionable process recommendations.

**Core Sub-components (all present in v0.1 unless noted)**

1. **Epitaxial Matching Engine**  
   - Input: candidate structure + target Si surface (`"Si(001)"` or `"Si(111)"`).  
   - Algorithm: coincidental lattice matching (Zur–McGill style) + optional custom interface matcher.  
   - Output: lowest-mismatch orientations, supercell size, biaxial strain tensor, elastic energy density (meV/Å²) using DFT-relaxed or ML elastic constants.  
   - Critical thickness estimate (Matthews–Blakeslee or People–Bean approximation).

2. **Buffer-Layer Stack Suggestor**  
   - Pre-populated, versioned library of known buffers (TiN, ZrN, AlN, MgO, STO, YSZ, CeO₂, …) with lattice parameters, thermal expansion, chemical compatibility flags, and typical growth temperatures.  
   - For a given candidate, propose minimal sequences that keep cumulative mismatch < 3–4 % and avoid deep Si oxidation or uncontrolled silicidation.  
   - Simple thermodynamic check for possible silicide/silicate interlayer formation.

3. **Thermal Budget & Process Window Estimator**  
   - Maximum recommended process temperature derived from literature + calculated diffusion barriers / melting points.  
   - Flag candidates requiring > 800 °C for > 10 min as high-risk for CMOS backend.  
   - Oxygen / nitrogen partial-pressure window estimates (especially critical for nickelates and cuprates).

4. **Membrane Transfer Simulation (v0.1 heuristic; full mechanics later)**  
   - Estimate residual strain after release from substrate.  
   - Simple score for membrane-transfer ease based on thickness, elastic energy, and known experimental success for similar materials.

5. **Proximity-Effect Estimates (later, P3)**  
   - Simple Usadel or microscopic estimates of induced gap / critical current in hybrid SC–Si or SC–normal structures.  
   - Not required for v0.1 ranking.

**Silicon Feasibility Score (0–100)**  
Tunable weighted sum (defaults given; campaign-overridable):

```
S = 30×(1 − m̃) + 25×T_budget + 20×C_chem + 15×E_exp + 10×M_mem
```

where all terms are normalized to [0,1], m̃ is normalized lattice mismatch, T_budget is thermal-budget score, C_chem chemical compatibility, E_exp known experimental evidence, M_mem membrane-transfer ease.

**Acceptance Criteria (v0.1)**  
- Any nitride or MgB₂ candidate returns a complete matching report + score in < 30 s on a workstation (using pre-computed elastic constants).  
- Score correlates with known experimental successes (NbN on Si, MgB₂ on buffered Si) within ±15 points.  
- All components of the score are exported and human-readable.

### 2.6 Candidate Ranking & Reporting
Explicitly consumes either Eliashberg Tc or `leading_pairing_eigenvalue` via a common `performance_score` field, and always includes the full Silicon Feasibility breakdown.

### 2.7 Workflow Engine & Active Learning Loop
Acquisition functions already support normalized performance proxies so conventional and unconventional candidates can share a campaign or be run in separate pools.

### 2.8 Josephson Junction Device Modeling  ← New section (P3 / later)

**Purpose**  
Provide simple, order-of-magnitude device-relevant metrics for top-ranked candidates so that materials can be filtered not only by Tc and Si-feasibility but also by approximate Josephson performance. These estimates are ranking aids and literature-consistency checks only; they are not quantitative device design values.

**Metrics to be produced (when enabled)**
- Superconducting gap Δ (from Eliashberg or DMFT-derived estimates).
- Critical current Ic (Ambegaokar–Baratoff or simple Usadel for SNS/SIS geometries).
- IcRn product.
- Characteristic voltage / switching energy estimates.
- Simple BdG or Usadel model outputs for thin-film or hybrid geometries (optional).

**Interfaces**
- Input: high-fidelity electronic structure results (`ElectronPhononResult` or `DMFTResult`) + optional Silicon Integration geometry (thickness, barrier, etc.).
- Output: `JosephsonMetrics` Pydantic model attached to `CandidateEvaluation`.
- Disabled by default; activated per-campaign via configuration flag. Only run on the final shortlist to control cost.

**v0.1 / v1.0**  
Not present. Explicitly out of scope until the conventional and unconventional materials pipelines are mature.

**Acceptance Criteria (when implemented)**  
- For well-known systems (Nb, NbN, MgB₂) the estimates recover experimental order of magnitude.  
- Metrics are clearly labeled “approximate / ranking only” in all exports.  
- Adding the calculator requires only a new Calculator class + model registration.

---

## 3. Data Models / Key Objects  ← Sharpened for actionability

All models are Pydantic v2. Fields marked **(required)** must be present; others are optional or filled later.

```python
class StructureCandidate(BaseModel):
    id: str                          # uuid
    structure: Structure             # pymatgen
    composition: Composition
    family: str                      # "tm_nitride" | "b_doped_si" | "mgb2" | "nickelate" | "cuprate"
    prototype: str
    doping: dict[str, float] = {}
    strain_tensor: Optional[list[list[float]]] = None
    interface_info: Optional[dict] = None
    energy_above_hull_proxy: Optional[float] = None
    provenance: Provenance
    metadata: dict = {}

class ElectronPhononResult(BaseModel):
    lambda_total: float
    omega_log: float                 # K
    alpha2F: Optional[dict] = None
    Tc_allen_dynes: Optional[float] = None
    Tc_eliashberg: Optional[float] = None
    mu_star: float = 0.1
    quality_tag: str                 # "screening" | "standard" | "production"
    converged: bool
    metadata: CalculationMetadata

class DMFTResult(BaseModel):
    U: float
    J: float
    occupancy: dict
    mass_enhancement: float
    leading_pairing_eigenvalue: float
    pairing_symmetry: str
    self_energy_summary: Optional[dict] = None
    spin_susceptibility_summary: Optional[dict] = None
    quality_tag: str
    converged: bool
    metadata: CalculationMetadata

class SiFeasibilityComponents(BaseModel):
    lattice_mismatch_pct: float
    strain_energy_meV_A2: float
    thermal_budget_score: float      # 0–1
    chemical_compatibility: float    # 0–1
    known_experimental_evidence: float
    membrane_transfer_ease: float
    oxygen_or_nitrogen_window_score: float
    recommended_buffers: list[str]
    max_process_temp_C: float

class SiFeasibilityScore(BaseModel):
    components: SiFeasibilityComponents
    composite_score: float           # 0–100
    notes: str = ""

class JosephsonMetrics(BaseModel):   # later only
    delta: Optional[float] = None    # gap (meV)
    Ic: Optional[float] = None       # critical current
    IcRn: Optional[float] = None
    switching_energy_est: Optional[float] = None
    model: str                       # "Ambegaokar-Baratoff" | "Usadel" | ...
    notes: str = "approximate / ranking only"

class CandidateEvaluation(BaseModel):
    candidate_id: str
    structure: StructureCandidate
    scf: Optional[SCFResult] = None
    phonon: Optional[PhononResult] = None
    eph: Optional[ElectronPhononResult] = None
    dmft: Optional[DMFTResult] = None
    si_score: Optional[SiFeasibilityScore] = None
    josephson: Optional[JosephsonMetrics] = None   # later
    ml_predictions: dict[str, float] = {}
    performance_score: Optional[float] = None      # Tc or scaled pairing eigenvalue
    final_rank_score: Optional[float] = None
    pareto_front: bool = False
    acquisition_score: Optional[float] = None
    status: str
    last_updated: datetime
```

These contracts are the single source of truth for module interfaces.

---

## 4. External Dependencies & Interfaces
Primary open-source stack remains mandatory (QE + EPW, TRIQS, pymatgen, jobflow, ALIGNN/MatGL, MongoDB, Pydantic v2, etc.). VASP optional and feature-flagged.

---

## 5. Configuration & Input Formats  ← More actionable

Campaign YAML is validated by a Pydantic `CampaignConfig` model. Required sections for v0.1:

```yaml
campaign:
  id: str
  name: str
  family: "tm_nitride" | "b_doped_si" | ...
  structure_generation: { ... }
  ml_filter: { max_e_hull_eV_atom: float, ... }
  dft: { engine: "qe", quality_tag: "screening"|"production", ... }
  ranking: { weights: {tc: float, si_feasibility: float, uncertainty: float} }
  active_learning: { enabled: bool, acquisition: str, batch_size: int }
  # later:
  # josephson: { enabled: false }
```

CLI remains `siscforge enumerate|submit|rank|train-surrogate|run`. All numerical defaults are documented in the schema; overrides are validated.

---

## 6. Output Formats & Database Schema  ← Clearer

**Required exports for every ranked candidate (v0.1)**
- Full `CandidateEvaluation` as JSON (including nested Structure and all scores).
- CIF / POSCAR of the structure.
- CSV summary table with columns: candidate_id, family, composition, performance_score, si_composite_score, E_hull, status, recommended_buffers, max_process_temp_C.
- Human-readable Markdown synthesis card containing the Silicon Feasibility breakdown and process recommendations.

**Database**  
MongoDB collections: `candidates`, `calculations`, `campaigns`, `surrogate_models`, `rankings`.  
Every calculation record stores parent IDs so the full provenance chain is queryable.

---

## 7. Performance & Scaling Targets
Unchanged. Workstation numbers remain the acceptance gate for v0.1.

---

## 8. Testing Strategy
Unchanged, with the addition that Silicon Integration scoring and (later) Josephson metric estimators have their own unit tests against known reference values.

---

## 9. Development Phases & Milestones

**Phase 0 (v0.1)** — Structure gen (nitrides + B:Si), formation-energy surrogate, QE phonon, heuristic Si-score (full component breakdown), ranking, MongoDB, CLI, dry-run.  
**Exit criteria**: NbN phonon recovered; 50-candidate Nb-Ti-N strain series ranked with complete Si-feasibility cards; all unit tests green.

**Phase 1** — EPW + Eliashberg, λ/Tc surrogates, active learning, improved buffers, MgB₂.

**Phase 2** — DMFT + pairing pathway with the interfaces defined above, full membrane/interface modeling, multi-objective ranking.

**Phase 3** — Josephson device metrics calculator (section 2.8), anisotropic options, proximity refinements, generative models.

---

*This document (v0.2) is implementation-ready. A competent engineer familiar with the Python scientific stack, jobflow/pymatgen and Quantum ESPRESSO can begin coding modules directly from these specifications. All acceptance criteria, v0.1 vs later flags, and data-model contracts are explicit.*
