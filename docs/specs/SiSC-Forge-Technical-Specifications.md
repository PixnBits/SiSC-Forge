# SiSC-Forge
## Technical Specifications

**Version 0.3 – Implementation-Ready Blueprint**  
*(Extends v0.2 with a detailed Josephson Junction Device Modeling module. Focus remains on actionable contracts, explicit version boundaries, and clear interfaces.)*

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
Josephson Junction Device Modeling Module  ← detailed in §2.8 (Phase 3+)
        │
Provenance Database + Active-Learning Feedback
```

**Design principles**
- Decoupled engines: pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering of expensive calculations.
- Provenance-first (lightweight jobflow implementation of AiiDA principles).
- Extensible Calculator registry (new calculators, including the Josephson metrics calculator, register without core changes).
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

### 2.4 Unconventional (DFT+U / DMFT) Pathway
(See previous version for full details. Produces `leading_pairing_eigenvalue` that feeds the common `performance_score`.)

### 2.5 Silicon Integration & Interface Module
(See previous version for full details. Produces quantitative Si-Feasibility Score 0–100 plus process recommendations.)

### 2.6 Candidate Ranking & Reporting
Consumes either Eliashberg Tc or `leading_pairing_eigenvalue` via a common `performance_score` field, always includes the full Silicon Feasibility breakdown, and (when enabled) can incorporate JosephsonMetrics as an optional secondary ranking or filter criterion.

### 2.7 Workflow Engine & Active Learning Loop
Acquisition functions support normalized performance proxies. The Josephson module is only invoked on a configurable top-N shortlist after primary ranking.

### 2.8 Josephson Junction Device Modeling Module  ← Detailed (Phase 3+)

**Purpose**  
Take promising superconducting candidates that already rank highly on both predicted superconducting performance and Silicon Feasibility Score, and estimate practical Josephson-junction (JJ) figures of merit. The goal is to provide order-of-magnitude, ranking-oriented device metrics that help experimental and circuit-design collaborators prioritize materials for SIS, SNS, or hybrid junctions compatible with silicon technology. These estimates are explicitly approximate and are never presented as quantitative device-design values.

**Scope of estimates**
- Superconducting gap Δ
- Critical current Ic and critical current density Jc
- IcRn product
- Approximate switching energy and characteristic speed / frequency proxies
- Qualitative compatibility with common JJ fabrication approaches (SIS, SNS, ramp-edge, etc.) and with the candidate’s Silicon Integration recommendations

#### 2.8.1 Inputs Required from the Materials Screening Pipeline

The module consumes already-computed results; it does not re-run expensive DFT/DMFT.

**Required / strongly preferred inputs**
- From `ElectronPhononResult` (conventional path) or `DMFTResult` (unconventional path):
  - Superconducting gap estimate Δ (meV) — preferred from Eliashberg or from a calibrated DMFT-derived scale; fallback to BCS-like 1.76 k_B T_c with family-specific correction factors
  - T_c or performance_score
  - Normal-state density of states at the Fermi level N(0) when available
  - Optional: Fermi velocity, coherence length ξ estimates, mean free path
- From `SiFeasibilityScore` / Silicon Integration module:
  - Recommended film thickness range
  - Buffer stack and interface quality flags
  - Thermal-budget constraints (affects allowed junction process temperatures)
  - Lattice-mismatch / strain state (can affect gap and critical current)
- From `StructureCandidate`:
  - Material family and composition
  - Strain tensor
  - Provenance and quality tags

**Optional user / campaign-supplied geometry parameters**
- Junction area A (default assumes a reference area, e.g. 1 µm², and reports both Ic and Jc)
- Barrier type and thickness for SIS (oxide, AlN, etc.)
- Normal-metal spacer thickness and material for SNS
- Operating temperature of interest (default: 0.5 T_c or a fixed cryogenic temperature)

**Minimal viable input set for early implementation**  
Δ (or T_c), material family, and a reference junction area are sufficient for the simplest Ambegaokar–Baratoff estimates.

#### 2.8.2 Recommended Theoretical Approaches

**Tier 1 – Simple analytic estimates (first implementation, Phase 3 early)**  
Fast, transparent, and sufficient for ranking and literature-order-of-magnitude checks.

- Gap Δ:
  - Prefer value extracted from Eliashberg spectral function or from DMFT pairing calculations.
  - Fallback: family-dependent factor × k_B T_c (e.g. ~1.76 for weak-coupling BCS-like; higher or lower for strong-coupling or unconventional cases).
- SIS junctions (Ambegaokar–Baratoff):
  - I_c R_n = (π Δ / 2e) tanh(Δ / 2 k_B T)
  - At T → 0: I_c R_n ≈ π Δ / 2e
- Critical current density: J_c = I_c / A (A = assumed or user-specified area)
- Switching energy (order-of-magnitude):
  - E_sw ≈ (1/2) I_c Φ_0   (or more refined expressions involving junction capacitance when available)
- Characteristic voltage / frequency proxies derived from I_c R_n

These formulas are implemented as pure functions with clear documentation of assumptions and validity ranges.

**Tier 2 – Semi-microscopic (Usadel) (later research-grade)**  
- Dirty-limit Usadel equations for SNS and SIS junctions.
- Incorporation of interface transparency, barrier resistance, and proximity-effect induced gap in the normal region.
- Temperature and (optionally) weak magnetic-field dependence.
- Requires additional materials parameters (diffusion constant, interface resistance) that may be estimated from DFT or taken from literature defaults per family.

**Tier 3 – Microscopic (BdG) (advanced / optional)**  
- Bogoliubov–de Gennes calculations for clean or quasi-clean junctions, specific geometries, or atomistic interface models.
- Significantly more expensive; reserved for final short-list candidates or dedicated follow-up studies.
- Interfaces to existing open-source BdG codes or custom solvers via the Calculator registry.

**Fabrication compatibility heuristics (all tiers)**  
- Map material family + Silicon Integration recommendations onto known process flows:
  - Nb / NbN / NbTiN → mature SIS (Nb/AlOx/Nb) and SNS processes
  - MgB₂ → specialized but demonstrated junctions; higher process temperatures
  - Nickelates / cuprates → more challenging (oxygen control, higher anisotropy); flag as research-grade
- Output a simple compatibility tag + short rationale (e.g. “compatible with standard Nb-based SIS flow”, “requires high-temperature MgB₂ process”, “oxygen-sensitive – challenging for conventional JJ fab”).

#### 2.8.3 Interface to the Candidate Ranking System

- The module is registered as a standard Calculator and is **disabled by default**.
- Activation is controlled by a campaign-level flag (`josephson.enabled: true`) and a shortlist size (e.g. top 20 by composite rank or by Pareto front of performance_score × Si-feasibility).
- On execution it attaches a `JosephsonMetrics` object to the existing `CandidateEvaluation`.
- Ranking can optionally:
  - Use a secondary sort key (e.g. high I_c R_n among otherwise comparable candidates),
  - Apply a soft filter (discard candidates whose estimated J_c falls below a campaign-defined threshold),
  - Or simply expose the metrics in the synthesis card and export files for human review.
- All Josephson values are written with an explicit `notes` field containing the string “approximate / ranking only” and the model tier used.
- Provenance records the exact input Δ source, geometry assumptions, and formula version so results remain reproducible.

#### 2.8.4 Version Boundaries

| Version / Phase | Scope |
|-----------------|-------|
| v0.1 – v1.0 (Phases 0–2) | Module present only as a stub / disabled Calculator. No estimates produced. |
| Phase 3 early | Tier-1 analytic estimates (Ambegaokar–Baratoff, gap from T_c or Eliashberg, J_c, simple switching energy, fabrication-compatibility heuristics). Runs only on configurable top-N shortlist. |
| Phase 3 later / research-grade | Tier-2 Usadel solvers, improved proximity modeling, optional Tier-3 BdG for selected candidates, geometry optimization loops, tighter coupling to Silicon Integration membrane/buffer recommendations. |

**Acceptance Criteria (Phase 3 early)**  
- For well-characterized reference materials (Nb, NbN, MgB₂) the Tier-1 estimates recover experimental I_c R_n and gap values within a factor of ~2–3 (order-of-magnitude fidelity).  
- Metrics appear in the CandidateEvaluation and in the exported synthesis cards with clear “approximate” labeling.  
- Enabling the module for a shortlist of 20 candidates adds negligible wall-time compared with the preceding EPW/DMFT stages.  
- Adding or replacing a formula requires only a new pure function + registration; the ranking and export layers remain unchanged.

---

## 3. Data Models / Key Objects

All models remain Pydantic v2. The JosephsonMetrics model is expanded as follows (other models unchanged from v0.2):

```python
class JosephsonMetrics(BaseModel):
    # Core figures of merit
    delta: Optional[float] = None              # superconducting gap (meV)
    Ic: Optional[float] = None                 # critical current (µA) for reference area
    Jc: Optional[float] = None                 # critical current density (A/cm²)
    IcRn: Optional[float] = None               # µV
    switching_energy_est: Optional[float] = None  # aJ or eV
    characteristic_frequency_est: Optional[float] = None  # GHz proxy

    # Context
    model_tier: str                            # "analytic_AB" | "Usadel" | "BdG" | ...
    reference_area_um2: float = 1.0
    assumed_temperature_K: Optional[float] = None
    fabrication_compatibility: str             # short tag + rationale
    junction_type_assumed: str                 # "SIS" | "SNS" | "hybrid" | ...

    # Provenance & caveats
    delta_source: str                          # "eliashberg" | "dmft_calibrated" | "bcs_fallback" | ...
    notes: str = "approximate / ranking only"
    quality_flag: str = "order_of_magnitude"
```

`CandidateEvaluation.josephson` remains an Optional[JosephsonMetrics] field that is populated only when the module is enabled and the candidate is on the shortlist.

---

## 4. External Dependencies & Interfaces
Primary open-source stack unchanged. Future Usadel/BdG backends will be wrapped behind the same Calculator protocol; no hard dependency is introduced in early Phase 3.

---

## 5. Configuration & Input Formats

Campaign YAML gains an optional section (ignored until Phase 3):

```yaml
josephson:
  enabled: false                    # default
  shortlist_size: 20                # run only on top-N after primary ranking
  model_tier: "analytic_AB"         # later: "usadel"
  reference_area_um2: 1.0
  assume_SIS: true
  temperature_K: null               # null → 0.5 * Tc
  secondary_ranking: false          # whether to use IcRn as soft sort key
```

All other configuration remains as in v0.2.

---

## 6. Output Formats & Database Schema

When JosephsonMetrics are present they are included in:
- The full CandidateEvaluation JSON
- The Markdown synthesis card (clearly labeled section “Approximate Josephson Metrics (ranking aid only)”)
- An optional extra column set in the CSV summary (Ic, Jc, IcRn, fabrication_compatibility)

Database schema is unchanged; the nested JosephsonMetrics object is stored inside the evaluation document.

---

## 7. Performance & Scaling Targets
Unchanged for Phases 0–2. In Phase 3 the analytic Tier-1 estimates must complete for a shortlist of 50 candidates in well under one minute on a single core.

---

## 8. Testing Strategy
In addition to previous tests:
- Unit tests for each analytic formula against published Ambegaokar–Baratoff and BCS reference values.
- Regression tests on Nb, NbN, and MgB₂ that check order-of-magnitude recovery of experimental gap and IcRn.
- Integration test that enables the module on a tiny shortlist and verifies the JosephsonMetrics object is correctly attached and exported.

---

## 9. Development Phases & Milestones

**Phase 0 (v0.1)** — Structure gen (nitrides + B:Si), formation-energy surrogate, QE phonon, heuristic Si-score, ranking, MongoDB, CLI, dry-run.  
**Exit**: NbN phonon recovered; 50-candidate strain series ranked with complete Si-feasibility cards.

**Phase 1** — EPW + Eliashberg, λ/Tc surrogates, active learning, improved buffers, MgB₂.

**Phase 2** — DMFT + pairing pathway, full membrane/interface modeling, multi-objective ranking.

**Phase 3** — Josephson Junction Device Modeling module (this section):
- Early: Tier-1 analytic estimates + fabrication compatibility heuristics on configurable shortlist.
- Later: Usadel (and optional BdG) backends, tighter geometry/Silicon-Integration coupling.

---

*This document (v0.3) is implementation-ready. The Josephson module is fully specified for a clean Phase-3 implementation while remaining completely inert in earlier phases. All acceptance criteria, version boundaries, and data-model contracts are explicit.*
