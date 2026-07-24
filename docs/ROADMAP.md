# SiSC-Forge Development Roadmap

**Version 0.3 – Practical Implementation Plan**  
Aligned with [PRD v0.2](PRD/SiSC-Forge-PRD.md) and [Technical Specifications v0.3](specs/SiSC-Forge-Technical-Specifications.md).

This roadmap turns the specifications into an ordered, workstation-first sequence of work. Every phase ends with concrete, locally-validatable deliverables so that progress is possible before any large HPC allocation arrives.

---

## Guiding Principles

- **Workstation-first**: Phases 0–2 must be fully demonstrable on a single high-end workstation (≤32 cores, 1 GPU, ≤128–256 GB RAM).
- **Calculator plugin architecture**: New physics engines are added as independent Calculators; the orchestrator and ranking layers stay stable.
- **Open-source primary path**: Quantum ESPRESSO + EPW + TRIQS + pymatgen + jobflow + ALIGNN/MatGL remain the default; VASP is optional.
- **Spec-driven**: Every deliverable maps to an acceptance criterion in the Technical Specifications.
- **Fail fast on known systems**: NbN, MgB₂ and (later) NdNiO₂ are the golden references used at every stage.

---

## Phase 0 — Foundation & Local Validation
**Goal**: A working, end-to-end skeleton that can enumerate structures, run basic DFT/phonon calculations, score silicon feasibility, rank candidates, and export results — all on a workstation.

### Key Deliverables
- Core Pydantic data models (`StructureCandidate`, `SCFResult`, `PhononResult`, `SiFeasibilityScore`, `CandidateEvaluation`, `CampaignConfig`, `Provenance`).
- Structure Generation module for TM nitrides (binary + simple ternary SQS) and heavily B-doped Si, including epitaxial strain application to Si(001)/Si(111).
- Basic Silicon Feasibility scoring (lattice mismatch, rule-based thermal/chemical heuristics, composite 0–100 score).
- jobflow recipes for Quantum ESPRESSO relaxation + SCF + DFPT/phonopy phonon.
- Simple formation-energy GNN surrogate (ALIGNN or MatGL) with ensemble uncertainty for pre-filtering.
- MongoDB (or file-based) store + basic ranking by E_hull + Si-score.
- CLI: `siscforge enumerate | submit | rank | run`.
- Dry-run / mock-calculator mode so the full orchestration can be tested without DFT binaries.
- Unit tests + one real QE phonon regression on bulk NbN.

### Dependencies
- None (this is the starting point).
- External: pymatgen, ASE, spglib, ICET (or ATAT), jobflow, phonopy, Quantum ESPRESSO, MongoDB (or JSON fallback), PyTorch + ALIGNN/MatGL.

### Suggested Order of Module Implementation
1. Data models + CampaignConfig schema validation.
2. Structure Generation (nitrides + B:Si + strain) + serialization.
3. Basic Silicon Feasibility scorer (mismatch + heuristics).
4. jobflow QE relaxation/SCF/phonon recipes + parsers.
5. Formation-energy surrogate + simple filter.
6. Ranking + JSON/CSV/CIF export + synthesis-card skeleton.
7. CLI entry points and dry-run mode.
8. Golden-system test (NbN phonon) + CI skeleton.

### What Can Be Validated Without Large-Scale Compute
- Structure enumeration produces valid, symmetry-reduced cells and correct strain application.
- NbN phonon spectrum is recovered within acceptable tolerance.
- A 20–50 candidate Nb-Ti-N strain series runs end-to-end on a workstation (real QE phonon on the most promising, mocked or coarse on the rest).
- Si-feasibility scores are sensible for known successful films (NbN on Si).
- Full provenance chain and export files are correct.
- Dry-run mode exercises every orchestration path.

**Exit criteria**: All of the above pass; a developer can clone the repo, install the environment, and reproduce the NbN + small nitride campaign on a laptop/workstation.

---

## Phase 1 — Core Conventional Screening Pipeline
**Goal**: Production-quality conventional superconductivity pathway (EPW + Eliashberg) plus usable active-learning and MgB₂ support, still largely workstation- or small-cluster-friendly.

### Key Deliverables
- Full EPW integration (coarse → fine grids) + isotropic Eliashberg Tc solver.
- `ElectronPhononResult` fully populated (λ, ω_log, α²F, Tc_allen_dynes, Tc_eliashberg, quality_tag).
- Multi-task or dedicated GNN heads for λ / ω_log / Tc proxy with uncertainty.
- Active-learning loop (uncertainty sampling or simple UCB) that retrains the surrogate and re-prioritizes the queue.
- Screening vs production quality tags and automatic fallbacks for Wannierization failures.
- MgB₂ prototype support and basic boride enumeration.
- Improved buffer-layer suggestions and thermal-budget scoring inside the Silicon Integration module.
- Campaign YAML fully operational for nitride and MgB₂ families.

### Dependencies
- Phase 0 complete (especially data models, jobflow skeleton, ranking, Si-score).
- External: EPW, Wannier90, robust QE phonon/EPW wrappers, additional training data for λ/Tc surrogates.

### Suggested Order of Module Implementation
1. EPW jobflow recipe (building on existing DFPT) + ElectronPhononResult parser.
2. Isotropic Eliashberg solver integration (EPW built-in or external).
3. Quality-tag system and Wannier failure diagnostics.
4. λ / ω_log / Tc surrogate training pipeline and inference API.
5. Active-learning coordinator (priority queue + retrain trigger).
6. MgB₂ structure generation and golden-system test.
7. Buffer library expansion and tighter Si-feasibility integration.
8. End-to-end nitride + MgB₂ campaigns with real EPW on shortlists.

### What Can Be Validated Without Large-Scale Compute
- Bulk NbN and MgB₂ recover literature Tc (within 15–20 %) under production settings on a workstation (small cells).
- A shortlist of 5–10 strained nitride candidates completes full EPW + Eliashberg on a high-end workstation or small departmental cluster.
- Active-learning loop demonstrably improves surrogate predictions on held-out data when new DFT/EPW results are injected.
- Quality tags correctly distinguish screening vs production runs and surface failures.

**Exit criteria**: Automated recovery of MgB₂ Tc; AL loop working; 50–200 candidate nitride campaign can be driven with intelligent prioritization on modest resources.

---

## Phase 2 — Silicon Integration Maturity + Ranking Polish
**Goal**: Make the Silicon Feasibility Score and ranking production-grade, add membrane/interface realism, and ensure the multi-objective ranking is transparent and exportable for experimental collaborators.

### Key Deliverables
- Full component breakdown of Si-Feasibility Score with documented weights and export of every term.
- Expanded buffer-stack library and rule-based + simple thermodynamic interlayer checks.
- Membrane-transfer heuristics (and later simple strain-relaxation estimates).
- Critical-thickness estimates (Matthews–Blakeslee / People–Bean).
- Multi-objective ranking (performance_score × Si-score × uncertainty) with Pareto front identification.
- Rich Markdown synthesis cards and machine-readable process recommendations.
- Optional interface-slab DFT calculations for selected high-ranking candidates (still optional at this stage).

### Dependencies
- Phase 0 (basic Si-score) and Phase 1 (reliable performance_score from Eliashberg).
- Elastic constants (DFT or ML) for strain-energy calculations.

### Suggested Order of Module Implementation
1. Expand SiFeasibilityComponents and make every term first-class in the data model and exports.
2. Buffer library + stack suggestor with chemical-compatibility flags.
3. Thermal-budget and oxygen/nitrogen window estimators.
4. Membrane and critical-thickness helpers.
5. Ranking engine upgrades (configurable weights, Pareto, acquisition score).
6. Synthesis-card generator and CSV/JSON schema freeze.
7. (Optional) Automated slab builder for interface DFT on shortlist.

### What Can Be Validated Without Large-Scale Compute
- Known successful systems (NbN on Si, MgB₂ on buffered Si) receive high, well-explained Si-feasibility scores.
- Changing ranking weights via YAML immediately reorders a test set correctly.
- Synthesis cards contain every field an experimentalist would need for a first growth attempt.
- Full ranking + export of a 100-candidate set finishes in seconds on a laptop.

**Exit criteria**: Si-feasibility scores are trusted enough to be the primary filter before expensive EPW; experimental collaborators can act on the exported cards without further translation.

---

## Phase 3 — Unconventional Pathway + Active Learning Maturity
**Goal**: Bring the DFT+U / DMFT + pairing pathway online so nickelates (and later cuprates) can be ranked on the same footing as conventional candidates, and harden the active-learning loop for mixed conventional/unconventional campaigns.

### Key Deliverables
- Automated DFT → Wannier → TRIQS/solid_dmft pipeline for infinite-layer RNiO₂.
- `DMFTResult` with leading pairing eigenvalue, symmetry, occupancy, mass enhancement.
- DFT+U always run as a cheap proxy and stored alongside full DMFT.
- Oxygen-vacancy enumeration as a first-class structure-generation option.
- Normalization of pairing eigenvalue into the common `performance_score` so ranking and AL need no special cases.
- Mature active-learning acquisition functions that can handle separate or joint conventional/unconventional pools.
- Basic bilayer nickelate and early cuprate prototypes (optional at this stage).

### Dependencies
- Phase 1 (Calculator registry, ranking, AL skeleton) and Phase 2 (Si-feasibility mature).
- External: Wannier90, TRIQS, solid_dmft, CTHYB solver, additional training data for correlated systems.

### Suggested Order of Module Implementation
1. DFT+U workflow and DFTUResult model.
2. Wannierization pipeline with quality metrics.
3. TRIQS/solid_dmft jobflow recipe + DMFTResult parser.
4. Pairing-eigenvalue extraction and mapping onto performance_score.
5. Oxygen-vacancy structure generation for nickelates.
6. AL acquisition updates for mixed or separate pools.
7. Golden-system test on bulk NdNiO₂ (occupancy + mass enhancement).
8. End-to-end strained nickelate campaign on shortlist.

### What Can Be Validated Without Large-Scale Compute
- Bulk NdNiO₂ recovers literature DMFT occupancy and mass enhancement under standard U, J on a workstation (or small cluster).
- A small set of strained infinite-layer candidates produces complete DMFTResult objects and is correctly ranked against nitride references.
- AL loop can be demonstrated with synthetic or small real data mixes of conventional and unconventional results.

**Exit criteria**: Nickelate candidates appear in ranked lists with both a pairing-based performance score and a realistic Si-feasibility score; the same ranking code handles both families without forks.

---

## Phase 4 — Device-Level (Josephson) Modeling
**Goal**: Add practical JJ figures of merit so that the highest-ranked, most Si-compatible candidates can also be filtered by approximate device performance.

### Key Deliverables
- Josephson Junction Device Modeling module (§2.8 of Technical Specifications).
- Tier-1 analytic estimates (Ambegaokar–Baratoff, gap from Eliashberg/DMFT or BCS-like fallback, Jc, IcRn, switching-energy proxies).
- Fabrication-compatibility heuristics (SIS / SNS / ramp-edge, process-temperature flags).
- Expanded `JosephsonMetrics` data model.
- Campaign flag to enable the module on a configurable top-N shortlist only.
- Optional secondary ranking or soft filter on IcRn / Jc.
- Clear “approximate / ranking only” labeling in all exports and synthesis cards.
- (Later within Phase 4) Tier-2 Usadel and optional Tier-3 BdG backends.

### Dependencies
- Phase 1 (reliable gap / Tc from Eliashberg) and Phase 2 (mature Si-feasibility and shortlist mechanism).
- Phase 3 desirable but not strictly required for the first analytic implementation on conventional materials.

### Suggested Order of Module Implementation
1. `JosephsonMetrics` model and Calculator stub (disabled by default).
2. Tier-1 analytic functions (Ambegaokar–Baratoff, Jc, simple switching energy).
3. Gap extraction helpers (Eliashberg → Δ, BCS fallback, family corrections).
4. Fabrication-compatibility rule engine.
5. Shortlist trigger + attachment to CandidateEvaluation.
6. Export and synthesis-card integration with explicit caveats.
7. Unit + regression tests on Nb, NbN, MgB₂ (order-of-magnitude recovery).
8. (Later) Usadel solver wrapper and geometry parameters.

### What Can Be Validated Without Large-Scale Compute
- Analytic estimates for Nb, NbN and MgB₂ recover experimental gap and IcRn within a factor of ~2–3.
- Enabling the module on a 10–20 candidate shortlist adds negligible runtime.
- Synthesis cards and JSON exports correctly surface the metrics with “approximate” labeling.
- Changing the shortlist size or enabling/disabling the module via YAML behaves as specified.

**Exit criteria**: Top-ranked Si-compatible candidates carry useful, clearly caveated JJ metrics that experimental and circuit collaborators can use for prioritization; the module remains completely inert when disabled.

---

## Cross-Cutting Work (Ongoing)

- Continuous expansion of the golden-system regression suite.
- Documentation of every Calculator’s inputs, outputs, and failure modes.
- Container images (Docker/Apptainer) for QE+EPW and TRIQS.
- CI that runs unit + fast integration tests on every PR; optional self-hosted runner with QE for scientific regressions.
- Community contribution guidelines once Phase 1 is solid.

---

## Summary Timeline (Indicative)

| Phase | Focus                              | Typical Duration | Compute Need          |
|-------|------------------------------------|------------------|-----------------------|
| 0     | Foundation & local validation      | 4–8 weeks        | Workstation only      |
| 1     | Conventional EPW + AL              | 6–10 weeks       | Workstation + small cluster |
| 2     | Silicon Integration + ranking      | 3–6 weeks        | Workstation           |
| 3     | Unconventional (DMFT) + AL maturity| 3–4 months       | Small → medium HPC    |
| 4     | Josephson device metrics           | 2–4 months       | Mostly shortlist / analytic |

Phases 0–2 can (and should) be completed and scientifically validated before any large allocation is required. Phase 3 and 4 benefit from HPC but are designed so that the critical path and acceptance tests remain accessible on modest resources.

---

*This roadmap is the operational companion to the PRD and Technical Specifications. When in doubt, the acceptance criteria in the Technical Specifications take precedence.*
