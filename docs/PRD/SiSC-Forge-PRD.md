# SiSC-Forge
## Product Requirements Document

**Version 0.2 – Implementation-Ready Blueprint**  
*(Refined: stronger Silicon Integration, expanded Unconventional pathway, Josephson device modeling roadmap, explicit v0.1 vs later markers)*

---

## 1. Vision & Problem Statement

**Vision**  
SiSC-Forge is a modular, open-source, high-throughput computational discovery platform that systematically identifies, evaluates, and ranks silicon-compatible materials with potential for elevated-temperature (ideally ambient) superconductivity suitable for Josephson-junction-based logic. By tightly coupling structure enumeration, graph-neural-network surrogates, first-principles electron-phonon (EPW) and dynamical-mean-field (DMFT) calculations, silicon-process-aware feasibility scoring, and (in later phases) simple device-level Josephson metrics, the platform converts available compute into experimentally actionable candidate lists optimized for CMOS-compatible processes.

The software is designed to be fully productive on a single high-end workstation for method development and small campaigns, and ready to scale the moment large-scale compute becomes available.

**Problem Statement**  
Conventional superconducting electronics remain locked to deep cryogenic temperatures because the best industrially mature silicon-compatible superconductors (Nb, NbN, NbTiN) have \(T_c \leq 16\,\mathrm{K}\). Higher-\(T_c\) families (MgB₂ ≈ 39 K, infinite-layer nickelates, cuprates) exist but lack mature, reproducible integration pathways onto silicon that respect lattice mismatch, thermal budgets, oxygen/nitrogen chemistry, interdiffusion, and modern foundry constraints. No existing high-throughput platform simultaneously optimizes predicted \(T_c\) (phonon-mediated or unconventional) *and* quantitative silicon-integration feasibility while supporting active learning and a clear path to device-relevant metrics. SiSC-Forge closes this gap.

## 2. Goals & Non-Goals

**Goals**
- Systematically search the five priority material families with dual conventional (Eliashberg) and unconventional (DFT+U / DMFT + pairing susceptibility) pathways.
- Deliver quantitative \(T_c\) predictions (or reliable proxies) together with a multi-objective Silicon Feasibility Score (0–100).
- Support continuous active-learning loops that improve graph-neural-network surrogates from new high-fidelity data.
- Export synthesis-relevant metadata (thermodynamic stability, thermal budgets, preferred buffer stacks, oxygen/nitrogen windows, lattice-mismatch data).
- Provide a clear, extensible path to simple Josephson-junction device metrics (critical current, \(I_c R_n\), gap, switching energy) in later versions.
- Run productively on a single high-end workstation for development and validation; scale transparently to institutional clusters and cloud HPC with the same codebase and campaign YAML.
- Maintain a fully functional open-source primary path (Quantum ESPRESSO + EPW + TRIQS + pymatgen + jobflow + ALIGNN/MatGL-style models). VASP is optional and feature-flagged.

**Non-Goals**
- Full device-level Josephson-junction circuit simulation or SPICE modeling in v0.1–v1.0 (simple estimates only; full circuit simulation is roadmap).
- Closed-loop experimental control or robotic synthesis.
- Replacement of expert human judgment on final candidate selection.
- Primary support for non-silicon substrates in v1.0.
- Guaranteed discovery of room-temperature superconductors.
- Production-grade interactive GUI in early versions (CLI + Jupyter first).
- Guaranteed quantitative absolute \(T_c\) accuracy for strongly correlated materials; the platform provides ranking, relative trends, and documented uncertainties.

## 3. Target Users
- Computational materials scientists specializing in superconductivity or epitaxial thin films.
- Condensed-matter theorists working on conventional or unconventional pairing mechanisms.
- Device physicists and process engineers evaluating materials for superconducting electronics on silicon.
- Research groups with access to moderate-to-large HPC who need a ready-to-run, modular, provenance-aware pipeline.
- Graduate students and postdocs who require a structured, reproducible workflow rather than ad-hoc scripts.
- Open-source scientific software contributors who wish to extend calculators, surrogates, or material-family modules.

## 4. Success Metrics

**Scientific / Discovery**
- Recover literature \(T_c\) (or accepted proxy) for reference systems within documented tolerances: NbN and MgB₂ within 15–20 % under production-quality settings; qualitative recovery of metallicity and low-\(T_c\) scale for heavily B-doped Si; correct orbital occupancy and leading pairing tendency for infinite-layer NdNiO₂ under standard \(U,J\).
- Produce ranked candidate lists for nitride alloy + strain campaigns in which known experimentally successful films appear in the top 15 % by composite score.
- Demonstrate that the Silicon Feasibility Score correlates qualitatively with published growth success rates.
- (Later) Simple Josephson metrics for top candidates are consistent with order-of-magnitude experimental values where available.

**Software / Operational**
- End-to-end nitride campaign (50–200 candidates) completes on a 16–32-core workstation with real Quantum ESPRESSO phonon calculations and produces a ranked list + synthesis-metadata export.
- ML pre-filter reduces the number of expensive DFPT/EPW calculations by ≥10× while retaining known high-\(T_c\) examples in the top decile.
- A new Calculator plugin can be added and exercised in a campaign with <1–2 days of developer effort.
- 100 % of v0.1 features pass unit + integration tests; a golden-system regression suite exists.

**Usability**
- A new user familiar with pymatgen and Quantum ESPRESSO can launch a standard nitride campaign from an example YAML in <30–60 minutes.
- Clean installation via conda/mamba + documented Docker/Apptainer images.
- Exported synthesis metadata judged “actionable” by at least one experimental collaborator.

## 5. High-Level Features (Prioritized)

**P0 – Must-Have for v0.1 (Workstation-validatable)**
- YAML-driven structure enumeration for TM nitrides (binary + ternary SQS) and heavily B-doped Si, with epitaxial strain to Si(001)/Si(111).
- Formation-energy / energy-above-hull GNN surrogate (ALIGNN or MatGL style) with uncertainty for pre-filtering.
- Heuristic + lattice-mismatch Silicon Feasibility Score and composite ranking.
- jobflow-orchestrated Quantum ESPRESSO relaxation + SCF + DFPT/phonopy phonon workflows with full provenance.
- MongoDB (or equivalent) store + CLI for campaign launch, ranking, and export of ranked candidates + synthesis-relevant metadata (JSON/CSV/CIF).
- Dry-run / mock-calculator mode.
- Fully open-source primary execution path.

**P1 – Core for First Production Campaigns**
- Automated EPW + isotropic Eliashberg \(T_c\) pipeline.
- Multi-task or dedicated GNN surrogates for \(\lambda\), \(\omega_{\log}\), and \(T_c\) proxy with calibrated uncertainty.
- Active-learning loop with uncertainty-driven prioritization.
- Improved buffer-layer recommendation engine and thermal-budget scoring.
- MgB₂ and simple boride support.
- Screening-quality vs production-quality calculation tags.

**P2 – Unconventional Pathway & Advanced Si Integration**
- TRIQS/solid_dmft DFT+DMFT + pairing-eigenvalue pathway for infinite-layer nickelates.
- Full multi-layer buffer stacks, interface-slab calculations, and freestanding membrane models.
- Explicit oxygen-vacancy enumeration for nickelates.
- Multi-objective Pareto ranking and richer synthesis recipe cards.

**P3 – Device-Aware Ranking & Later**
- Simple Josephson-junction device modeling (critical current \(I_c\), \(I_c R_n\), gap \(\Delta\), switching energy, basic BdG/Usadel estimates).
- Anisotropic Eliashberg / SCDFT.
- Proximity-effect modeling refinements.
- Generative structure models, web dashboard, community data contribution.

## 6. User Stories / Key Workflows

**US1 – Materials Scientist (Campaign Lead)**  
As a computational materials scientist, I want to define a single YAML campaign that explores Nb₁₋ₓTiₓN compositions under epitaxial strain on Si(001), so that the system enumerates SQS cells, filters by ML stability and Si-feasibility, runs phonon (and later EPW) calculations on the most promising candidates, and returns a ranked list with recommended buffers and synthesis metadata.

**US2 – Unconventional Superconductivity Specialist**  
As a theorist focused on nickelates, I want the same ranking framework to accept either Eliashberg \(T_c\) or DMFT leading pairing eigenvalues as the performance metric, so that conventional and unconventional candidates can be compared on equal footing with a consistent Silicon Feasibility Score.

**US3 – Process / Device Engineer**  
As a superconducting-electronics process engineer, I want every high-ranking candidate accompanied by a clear synthesis metadata card (max thermal budget, preferred buffer stack, lattice mismatch, oxygen/nitrogen window, membrane-transfer notes), so that I can assess foundry compatibility immediately.

**US4 – Developer / Extender**  
As a developer, I want to implement a new Calculator class (new property, new solver, or new material-family enumerator) that returns a validated Pydantic result model and have it automatically appear in the workflow registry, ranking, and active-learning loop without modifying core orchestration code.

**US5 – Workstation-Only User**  
As a researcher with only a high-end workstation today, I want to validate the entire pipeline on known systems (NbN, MgB₂) and generate a small ranked list of nitride variants, so that when a large allocation becomes available I can immediately scale the identical codebase and campaign definitions.

**Key v0.1 Workflow**  
1. Edit campaign YAML.  
2. `siscforge enumerate` → StructureCandidate list.  
3. ML pre-filter + Si-feasibility scoring.  
4. `siscforge submit` → jobflow launches QE jobs (local ProcessPool or SLURM).  
5. Results ingested → ranking updated.  
6. `siscforge rank --export` → ranked table, synthesis cards, CIF/POSCAR files.

## 7. Constraints & Assumptions

**Constraints**
- The primary execution path must be fully functional using only open-source components (Quantum ESPRESSO ≥7.2 + EPW, TRIQS, pymatgen, ASE, phonopy, jobflow, ALIGNN/MatGL, MongoDB or PostgreSQL, PyTorch). VASP is optional and feature-flagged; users supply their own license and binaries.
- Development and v0.1 scientific validation must be possible on a single high-end workstation (≤32 cores, 1 GPU, ≤128–256 GB RAM) before any large HPC allocation is required.
- The same codebase and campaign YAML must run unchanged from workstation (local backend) to SLURM-type clusters (jobflow SLURM backend).
- All external codes are treated as black-box engines behind stable Calculator interfaces.
- Pseudopotential libraries (SSSP or PseudoDojo) are version-pinned and hash-tracked.
- Python ≥3.11 scientific stack with type hints and Pydantic v2; containerization (Docker/Apptainer) is the recommended distribution method for DFT/DMFT engines.

**Assumptions**
- Users have basic familiarity with DFT workflows and YAML.
- Sufficient public + internal DFT data will become available to train useful surrogates.
- Experimental collaborators will interpret synthesis metadata with domain expertise.
- Network file systems or object storage are available for large intermediate files on HPC.

## 8. Risks & Mitigations

**Scientific / Physics Risks**
- Imaginary phonons / dynamical instability under epitaxial strain → Automatic detection, soft-mode analysis, optional re-relaxation, clear flagging rather than silent discard; ML surrogate for dynamical stability later.
- Poor Wannierization or gauge problems in EPW → Automated quality metrics (spread, band window), quality_tag (“screening” vs “production”), fallback to coarser grids or Allen-Dynes from DFPT only.
- Extreme sensitivity of nickelates/cuprates to \(U, J\), double-counting, and oxygen stoichiometry → Treat \(U,J\) as campaign parameters; support oxygen-vacancy enumeration as a first-class degree of freedom; always report DFT+U proxies alongside full DMFT; clear process-window metadata.
- ML domain shift on highly strained or doped films → Continuous active learning with strain-augmented data; uncertainty quantification that rises outside training convex hull; human review of high-uncertainty high-ranking candidates.
- Over-optimistic Si-feasibility scores → Transparent component breakdown; conservative defaults; versioned scoring rules; experimental collaborator re-weighting.
- Josephson estimates are highly approximate → Clearly labeled as order-of-magnitude / ranking aids only; never presented as quantitative device design values in early versions.

**Software / Engineering Risks**
- High failure rate of expensive EPW/DMFT jobs → Robust error classification, quality tags, automatic fallbacks, acquisition functions that avoid known-failure regions.
- Workflow brittleness as calculation types grow → Strict Pydantic contracts, jobflow native dependency system, mock-calculator mode, comprehensive tests.
- Database schema evolution → Versioned models + migration scripts; store raw files alongside structured data; MongoDB flexibility.
- Performance cliffs on HPC (I/O, queue latency) → Early containerization, staged scaling tests, local caching of pseudopotentials and models.
- Scope creep → Explicit Non-Goals and hard Phase 0/1 exit criteria focused on materials ranking.

## 9. Future Roadmap

- **v0.1 (Workstation Foundation)** — Structure gen (nitrides + B:Si), formation-energy surrogate, QE phonon, heuristic Si-score, ranking, MongoDB, CLI, dry-run. Exit: validated NbN phonon + 50-candidate campaign on workstation.
- **v0.5 (Conventional Production)** — Full EPW + Eliashberg, \(\lambda/T_c\) surrogates, active learning, MgB₂, improved buffers, screening/production quality tags.
- **v1.0 (Dual Pathway + Advanced Si)** — DMFT + pairing for nickelates, full interface/membrane modeling, multi-objective ranking, synthesis cards.
- **v1.x+** — Simple Josephson device metrics, anisotropic/SCDFT, proximity refinements, generative models, web UI, community contributions.

---

*This PRD (v0.2) is the authoritative source of product requirements for SiSC-Forge. All implementation work should be driven by and consistent with this document and the companion Technical Specifications.*
