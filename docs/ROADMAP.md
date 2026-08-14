# SiSC-Forge Development Roadmap

**Version 0.4.2 – Phase 2 complete + Phase 3 P3.1–P3.6 + Phase 4 P4.1**  
Aligned with [PRD v0.4.1](PRD/SiSC-Forge-PRD.md), [Technical Specifications v0.5.1](specs/SiSC-Forge-Technical-Specifications.md), and [design note](design/active-learning-flywheel.md).

Workstation production-path features (resume, trust layer, EPW coarse-k + Phase B,
phonon-first stable_only, phonon FFT/symmetry retry, Docker QE≥7.2) are **required
for desktop EPW/maps** and are documented as shipped/must-have in PRD §5 and Specs
§2.3–2.3b / §9; they sit alongside Phase 1 EPW rather than deferring to Phase 2.

This roadmap turns the specifications into an ordered, workstation-first sequence of work. Every phase ends with concrete, locally-validatable deliverables so that progress is possible before any large HPC allocation arrives.

---

## Guiding Principles

- **Workstation-first**: Phases 0–2 must be fully demonstrable on a single high-end workstation (≤32 cores, 1 GPU, ≤128–256 GB RAM).
- **Calculator plugin architecture**: New physics engines are added as independent Calculators; the orchestrator and ranking layers stay stable.
- **Open-source primary path**: Quantum ESPRESSO + EPW + TRIQS + pymatgen + jobflow + ALIGNN/MatGL remain the default; VASP is optional.
- **Spec-driven**: Every deliverable maps to an acceptance criterion in the Technical Specifications.
- **Fail fast on known systems**: NbN, MgB₂ and (later) NdNiO₂ are the golden references used at every stage.
- **Interleaved active learning**: prioritize → calculate → promote → retrain in short cycles; do not wait for a large static label set before the first useful surrogate.

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
- Multi-task or dedicated GNN heads for λ / ω_log / Tc proxy with uncertainty (**stub prioritization shipped; trained GNN + bootstrap later**).
- Active-learning loop (uncertainty sampling or simple UCB) that prioritizes expensive jobs (**prioritization shipped; lightweight retrain + seed management in residual / Phase 1.5**).
- Screening vs production quality tags and automatic fallbacks for Wannierization failures.
- **Desktop operability (must):** campaign resume + mid-step QE checkpoints; EPW nproc/npool; coarse-k preflight + EPW-only remediation (nkc ladder + search_shells Phase B); trust/result_quality layer; phonon-first maps + `stable_only` shortlist; phonon-specific diagnose + FFT/symmetry nosym retry; refine-from-store; Docker QE≥7.2.
- MgB₂ prototype support and basic boride enumeration.
- Improved buffer-layer suggestions and thermal-budget scoring inside the Silicon Integration module (45°/buffers shipped).
- Campaign YAML fully operational for nitride and MgB₂ families.

### Phase 1 residual / Phase 1.5 — AL Bootstrap (workstation cadence)
**Goal**: Make the interleaved flywheel real on desktop hardware.

**1.5a (shipped):** seed-set management, explicit promotion gate, immutable
snapshots, lightweight retrain with AC17 safety, bootstrap status CLI, unit
tests AC13–AC18. See `docs/phase15-exit.md`.

**1.5b (shipped):** trained family-mean predictions **change** acquisition
rankings; `siscforge run --al-root` loads the registry and writes prioritization
provenance; bootstrap banner on synthesis cards; `al-promote --dry-run`;
`al-seed --from-file`; `al-rollback`. See `docs/phase15b-exit.md`.

- Seed-set management (goldens + literature ingestion + early project labels).
- Explicit promotion gate into the training set.
- First trained or fine-tuned λ/Tc (or performance) surrogate with uncertainty and model metadata.
- Acquisition function that records model version, training-set size, and weights with every decision.
- Lightweight retrain/update trigger after shortlist cycles.
- Bootstrap-mode observability and the operator-experience requirements in `docs/design/active-learning-flywheel.md`.
- Dry-run path that exercises the full prioritize → shortlist → mock-calculate → promote → retrain loop.

**Workstation cadence note**: aim for a steady trickle of a few high-quality labels per week. Most candidates die at the phonon-first gate; screening-quality labels are acceptable early; production-quality is reserved for shortlist winners. A first useful prioritization model should appear after a few months of steady work, not after serial collection of 100+ full-production EPW results.


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
9. (Residual) Seed management, promotion, first trained surrogate, bootstrap UX.

### What Can Be Validated Without Large-Scale Compute
- Bulk NbN and MgB₂ recover literature Tc (within 15–20 %) under production settings on a workstation (small cells).
- A shortlist of 5–10 strained nitride candidates completes full EPW + Eliashberg on a high-end workstation or small departmental cluster.
- Active-learning loop demonstrably improves surrogate predictions on held-out data when new DFT/EPW results are injected.
- Quality tags correctly distinguish screening vs production runs and surface failures.
- Full mock AL cycle (prioritize → shortlist → promote → retrain) is green.

**Exit criteria**: Automated recovery of MgB₂ Tc; AL prioritization working; 50–200 candidate nitride campaign can be driven with intelligent prioritization on modest resources; AL bootstrap milestone (seed + first surrogate + one complete cycle) demonstrated.

---

## Phase 2 — Silicon Integration Maturity + Ranking Polish — **COMPLETE**
**Goal**: Make the Silicon Feasibility Score and ranking production-grade, add membrane/interface realism, and ensure the multi-objective ranking is transparent and exportable for experimental collaborators.

**Status**: **Shipped** (P2.1–P2.5). Exit checklist: [phase2-exit.md](phase2-exit.md). Schema freeze: [process-recommendation-schema.md](process-recommendation-schema.md).

### Key Deliverables
- Full component breakdown of Si-Feasibility Score with documented weights and export of every term. **(P2.1 — done)**
- Expanded buffer-stack library and rule-based + simple thermodynamic interlayer checks. **(P2.2 — done)**
- Membrane-transfer heuristics (and later simple strain-relaxation estimates). **(P2.3 — done; FEM deferred)**
- Critical-thickness estimates (Matthews–Blakeslee / People–Bean). **(P2.3 — done)**
- Multi-objective ranking (performance_score × Si-score × uncertainty) with Pareto front identification. **(P2.4 — done)**
- Rich Markdown synthesis cards and machine-readable process recommendations. **(P2.5 — done: schema v1.0)**
- Optional interface-slab DFT calculations for selected high-ranking candidates (still optional / deferred).

### Dependencies
- Phase 0 (basic Si-score) and Phase 1 (reliable performance_score from Eliashberg).
- Elastic constants (DFT or ML) for strain-energy calculations.

### Suggested Order of Module Implementation
1. Expand SiFeasibilityComponents and make every term first-class in the data model and exports. **(P2.1 — done: YAML weights + export provenance)**
2. Buffer library + stack suggestor with chemical-compatibility flags. **(P2.2 — done: multi-layer stacks + chemical/thermal window flags)**
3. Thermal-budget and oxygen/nitrogen window estimators. **(P2.2 — done)**
4. Membrane and critical-thickness helpers. **(P2.3 — done: Matthews–Blakeslee / People–Bean + membrane-transfer heuristics)**
5. Ranking engine upgrades (configurable weights, Pareto, acquisition score). **(P2.4 — done: multi-objective weights + Pareto + ranking provenance)**
6. Synthesis-card generator and CSV/JSON schema freeze. **(P2.5 — done: scannable Process recommendation + `process_recommendations.json` v1.0)**
7. (Optional) Automated slab builder for interface DFT on shortlist. **(deferred)**


### What Can Be Validated Without Large-Scale Compute
- Known successful systems (NbN on Si, MgB₂ on buffered Si) receive high, well-explained Si-feasibility scores.
- Changing ranking weights via YAML immediately reorders a test set correctly.
- Synthesis cards contain every field an experimentalist would need for a first growth attempt.
- Full ranking + export of a 100-candidate set finishes in seconds on a laptop.

**Exit criteria**: Si-feasibility scores are trusted enough to be the primary filter before expensive EPW; experimental collaborators can act on the exported cards without further translation. **Met** — see [phase2-exit.md](phase2-exit.md).

---

## Phase 3 — Unconventional Pathway + Active Learning Maturity
**Goal**: Bring the DFT+U / DMFT + pairing pathway online so nickelates (and later cuprates) can be ranked on the same footing as conventional candidates, and harden the active-learning loop for mixed conventional/unconventional campaigns.

### Key Deliverables
- **P3.1 Done:** DFT+U workflow + `DFTUResult` (see [phase3-p31-dftu.md](phase3-p31-dftu.md)).
- **P3.2 Done (scaffold + residual):** Wannier prep + quality metrics + `ready_for_dmft` gate; residual **P3.2.1** automated nscf + pw2wannier90 (see [phase3-p32-wannier.md](phase3-p32-wannier.md)).
- **P3.3 Scaffold:** `DMFTResult` model + Wannier gate + mock path + optional drop-in `observables.json` parser; **full automated solid_dmft / CTHYB launch is residual** (see [phase3-p33-dmft.md](phase3-p33-dmft.md)).
- **P3.4 Done:** Pairing eigenvalue → common `performance_score` with documented `epw_then_dmft` precedence (see [phase3-p34-pairing-score.md](phase3-p34-pairing-score.md)).
- **P3.5 Done:** Oxygen-vacancy / infinite-layer enumeration for nickelates (see [phase3-p35-oxygen-vacancy.md](phase3-p35-oxygen-vacancy.md)).
- **P3.6 Done:** Mixed conventional/unconventional AL acquisition (`off` / `joint` / `separate` pools; see [phase3-p36-mixed-al.md](phase3-p36-mixed-al.md)).
- Basic bilayer nickelate and early cuprate prototypes (optional / later).

### Dependencies
- Phase 1 (Calculator registry, ranking, AL skeleton + bootstrap) and Phase 2 (Si-feasibility mature).
- External: Wannier90, TRIQS, solid_dmft, CTHYB solver, additional training data for correlated systems.

### Suggested Order of Module Implementation
1. DFT+U workflow and DFTUResult model. **(P3.1 — Done; see docs/phase3-p31-dftu.md)**
2. Wannierization pipeline with quality metrics. **(P3.2 — Done (metrics + mock + gate + prep); residual P3.2.1 nscf+pw2wannier90; see docs/phase3-p32-wannier.md)**
3. TRIQS/solid_dmft jobflow recipe + DMFTResult parser. **(P3.3 — Scaffold: model + Wannier gate + mock + drop-in parser; full automated solid_dmft launch residual; see docs/phase3-p33-dmft.md)**
4. Pairing-eigenvalue extraction and mapping onto performance_score. **(P3.4 — Done; see docs/phase3-p34-pairing-score.md)**
5. Oxygen-vacancy structure generation for nickelates. **(P3.5 — Done; see docs/phase3-p35-oxygen-vacancy.md)**
6. AL acquisition updates for mixed or separate pools. **(P3.6 — Done; see docs/phase3-p36-mixed-al.md)**
7. Golden-system test on bulk NdNiO₂ (occupancy + mass enhancement) — residual (science + real DMFT).
8. End-to-end strained nickelate campaign on shortlist — residual until real DMFT launch.

### What Can Be Validated Without Large-Scale Compute
- Bulk NdNiO₂ recovers literature DMFT occupancy and mass enhancement under standard U, J on a workstation (or small cluster).
- A small set of strained infinite-layer candidates produces complete DMFTResult objects and is correctly ranked against nitride references.
- AL loop can be demonstrated with synthetic or small real data mixes of conventional and unconventional results.

**Exit criteria (partial)**: Nickelate candidates appear in ranked lists with both a pairing-based performance score (from mock or drop-in) and a realistic Si-feasibility score; the same ranking code handles both families without forks. Mixed AL acquisition (`joint` / `separate`) is shipped. Full automated solid_dmft/CTHYB and production GNN heads remain open.

Mixed-family ranking (EPW Tc next to a pairing proxy) is for **prioritization only**. Absolute comparability of the two origins is not claimed. Source-aware / family-normalized acquisition is **P3.6** (`docs/phase3-p36-mixed-al.md`).

**Phase 3 software path** is complete aside from residuals: real CTHYB launch, production ALIGNN/MatGL λ/Tc GNN heads, and the golden NdNiO₂ science campaign.

---

## Phase 4 — Device-Level (Josephson) Modeling
**Goal**: Add practical JJ figures of merit so that the highest-ranked, most Si-compatible candidates can also be filtered by approximate device performance.

### Key Deliverables
- Josephson Junction Device Modeling module (§2.8 of Technical Specifications). **(P4.1 + P4.2)**
- Tier-1 analytic estimates (Ambegaokar–Baratoff, gap from Eliashberg or BCS-like fallback, Jc, IcRn, switching-energy proxies). **(P4.1 — done)**
- Fabrication-compatibility heuristics (SIS / SNS / ramp-edge, process-temperature flags). **(P4.2 — done)**
- Expanded `JosephsonMetrics` data model. **(P4.1 — done; P4.2 nested fabrication hints)**
- Campaign flag to enable the module on a configurable top-N shortlist only. **(P4.1 — done)**
- Optional secondary ranking or soft filter on IcRn / Jc. **(P4.2 — done; presentation-only, opt-in)**
- Clear “approximate / ranking only” labeling in all exports and synthesis cards. **(P4.1 — done; P4.2 repeats + heuristic caveat)**
- (Later within Phase 4) Tier-2 Usadel and optional Tier-3 BdG backends.

### Dependencies
- Phase 1 (reliable gap / Tc from Eliashberg) and Phase 2 (mature Si-feasibility and shortlist mechanism).
- Phase 3 desirable but not strictly required for the first analytic implementation on conventional materials.

### Suggested Order of Module Implementation
1. `JosephsonMetrics` model (disabled by default). **(P4.1 — done; analytics-only, no calculator plugin)**
2. Tier-1 analytic functions (Ambegaokar–Baratoff, Jc, simple switching energy). **(P4.1 — done)**
3. Gap extraction helpers (Eliashberg → Δ, BCS fallback, optional family ratios). **(P4.1 — done)**
4. Fabrication-compatibility rule engine. **(P4.2 — done)**
5. Shortlist trigger + attachment to CandidateEvaluation. **(P4.1 — done: `enabled` + top-N)**
6. Export and synthesis-card integration with explicit caveats. **(P4.1 — done)**
7. Unit + regression tests on Nb, NbN, MgB₂ (order-of-magnitude recovery). **(P4.1 — done)**
8. (Later) Usadel solver wrapper and geometry parameters.

See [phase4-p41-josephson-tier1.md](phase4-p41-josephson-tier1.md) and
[phase4-p42-fabrication.md](phase4-p42-fabrication.md).

### What Can Be Validated Without Large-Scale Compute
- Analytic estimates for Nb, NbN and MgB₂ recover experimental gap and IcRn within a factor of ~2–3. **(P4.1 tests)**
- Enabling the module on a 10–20 candidate shortlist adds negligible runtime.
- Synthesis cards and JSON exports correctly surface the metrics with “approximate” labeling.
- Changing the shortlist size or enabling/disabling the module via YAML behaves as specified.

**Exit criteria (partial)**: Top-ranked Si-compatible candidates can carry useful, clearly caveated JJ metrics **and** fabrication-class / thermal hints when `josephson.enabled` is set; the module remains completely inert when disabled. Usadel/BdG remain later.

---

## Cross-Cutting Work (Ongoing)

- Continuous expansion of the golden-system regression suite.
- Documentation of every Calculator’s inputs, outputs, and failure modes.
- Container images (Docker/Apptainer) for QE+EPW and TRIQS.
- CI that runs unit + fast integration tests on every PR; optional self-hosted runner with QE for scientific regressions.
- Community contribution guidelines once Phase 1 is solid.
- Maintenance of the active-learning design note and its acceptance criteria as the flywheel matures.

---

## Summary Timeline (Indicative)

| Phase | Focus                              | Typical Duration | Compute Need          |
|-------|------------------------------------|------------------|-----------------------|
| 0     | Foundation & local validation      | 4–8 weeks        | Workstation only      |
| 1     | Conventional EPW + AL prioritization | 6–10 weeks     | Workstation + small cluster |
| 1.5   | AL bootstrap (seed, first surrogate, interleaved cycles) | ongoing / parallel | Workstation |
| 2     | Silicon Integration + ranking      | 3–6 weeks (**complete**) | Workstation |
| 3     | Unconventional (DMFT) + AL maturity| 3–4 months       | Small → medium HPC    |
| 4     | Josephson device metrics           | 2–4 months       | Mostly shortlist / analytic |

Phases 0–2 can (and should) be completed and scientifically validated before any large allocation is required. Phase 3 and 4 benefit from HPC but are designed so that the critical path and acceptance tests remain accessible on modest resources.

---

*This roadmap is the operational companion to the PRD, Technical Specifications, and `docs/design/active-learning-flywheel.md`. When in doubt, the acceptance criteria in the Technical Specifications take precedence.*
