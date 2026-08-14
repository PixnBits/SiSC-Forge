# SiSC-Forge
## Product Requirements Document

**Version 0.4.3 – Phase 2 complete + Phase 3 software path (P3.1–P3.6) + Phase 4 Tier-1 (P4.1 + P4.2)**  
*(Extends v0.4.2 with fabrication-compatibility heuristics and an optional
Josephson shortlist presentation sort. Module remains inert unless enabled.
Tier-1 analytic path is complete; Usadel/BdG remain later.)*

### Changelog (v0.4.2 → v0.4.3)

| Theme | What was added / corrected |
|-------|----------------------------|
| P4.2 | Fabrication-compatibility hints (SIS / SNS / ramp-edge, BEOL / thermal flags, stack notes) reused from Si-feasibility. Optional presentation-only `secondary_ranking` on IcRn / Jc. No ranker-math change. |
| Status | §9 note: Phase 2 complete; Phase 3 P3.1–P3.6 software path landed; real CTHYB / production GNN residual |

### Changelog (v0.4.1 → v0.4.2)

| Theme | What was added / corrected |
|-------|----------------------------|
| P4.1 | `JosephsonMetrics` + Ambegaokar–Baratoff / BCS-from-Tc proxies; optional attachment; caveated export. No ranker fork. |

### Changelog (v0.4.0 → v0.4.1)

| Theme | What was added / corrected |
|-------|----------------------------|
| P3.6 | Mixed conventional/unconventional AL acquisition (`off` / `joint` / `separate` pools, quotas, provenance). Default `off` preserves pre-P3.6 rankings. |

### Changelog (v0.3.1 → v0.4.0)

| Theme | What was added / corrected |
|-------|----------------------------|
| Phase 2 | Si component weights + YAML override + export (P2.1); multi-layer buffer stacks + chemical/thermal windows (P2.2); critical thickness (Matthews–Blakeslee / People–Bean) + membrane heuristics (P2.3); multi-objective ranking + Pareto + provenance (P2.4); process-recommendation synthesis cards + schema 1.0 freeze (P2.5) — **complete** |
| Phase 3 partial | DFT+U + `DFTUResult` (P3.1); Wannier prep + quality metrics + `ready_for_dmft` + automated nscf/pw2wannier90 (P3.2 / P3.2.1); `DMFTResult` scaffold (model + gate + mock + optional observables parser — **not** full automated solid_dmft/CTHYB) (P3.3); pairing eigenvalue → common `performance_score` with `epw_then_dmft` precedence (P3.4); oxygen-vacancy / infinite-layer enumeration (P3.5); mixed AL pools (P3.6) |
| Status honesty | Mock DMFT numbers are illustrative; no guaranteed room-temp SC; production GNN λ/Tc heads and full real DMFT launch remain later |
| Ranking | Ranking already consumes DMFT pairing-derived `performance_score`; process-recommendation cards exist |

### Changelog (v0.3 → v0.3.1)

| Theme | What was added |
|-------|----------------|
| Vision / Goals | Intermediate success criterion (Si-compatible candidates with predicted Tc ≳ 40–80 K); interleaved AL loop from small seed sets |
| Metrics | First useful prioritization model after ≤150 high-quality labels; workstation cadence of short cycles |
| P1 | Seed-set management, literature ingestion, explicit promotion, bootstrap-mode observability |
| References | Design note `docs/design/active-learning-flywheel.md` |

### Changelog (v0.2 → v0.3)

| Theme | What was added |
|-------|----------------|
| Risks | Pathological screening λ/Tc; multi-day DFPT value; EPW bvector after DFPT; phonon mislabel as EPW; FFT/symmetry setup |
| Usability | Resume without workdir surgery; step-correct CLI failure reasons; heartbeats/walltime |
| Workflows | Phonon-first map → stable_only EPW; post-DFPT EPW-only remediation |
| Metrics | Classification + remediable-retry success; ranking not dominated by pathological EPW |
| P0 / must-have | Trust layer, resume, EPW topology + coarse-k safety, Phase B shells, phonon diagnose/retry, Docker |
| Later | Material-specific Wannier projections, full AL retrain, DMFT, Josephson, guaranteed EPW on all cells |

---

## 1. Vision & Problem Statement

**Vision**  
SiSC-Forge is a modular, open-source, high-throughput computational discovery platform that systematically identifies, evaluates, and ranks silicon-compatible materials with potential for elevated-temperature (ideally ambient) superconductivity suitable for Josephson-junction-based logic. By tightly coupling structure enumeration, graph-neural-network surrogates, first-principles electron-phonon (EPW) and dynamical-mean-field (DMFT) calculations, silicon-process-aware feasibility scoring, and optional Tier-1 analytic Josephson metrics (Usadel/BdG later), the platform converts available compute into experimentally actionable candidate lists optimized for CMOS-compatible processes.

The software is designed to be fully productive on a single high-end workstation for method development and small campaigns, and ready to scale the moment large-scale compute becomes available.

**Near-term scientific target**  
Identify and rank silicon-compatible candidates with predicted Tc ≳ 40–80 K (above liquid-nitrogen temperature) that survive a realistic process window. Ambient-pressure room-temperature operation remains the long-term aspiration; it is not a near-term deliverable or success criterion.

**Problem Statement**  
Conventional superconducting electronics remain locked to deep cryogenic temperatures because the best industrially mature silicon-compatible superconductors (Nb, NbN, NbTiN) have Tc ≤ 16 K. Higher-Tc families (MgB₂ ≈ 39 K, infinite-layer nickelates, cuprates) exist but lack mature, reproducible integration pathways onto silicon that respect lattice mismatch, thermal budgets, oxygen/nitrogen chemistry, interdiffusion, and modern foundry constraints. No existing high-throughput platform simultaneously optimizes predicted Tc (phonon-mediated or unconventional) *and* quantitative silicon-integration feasibility while supporting active learning and a clear path to device-relevant metrics. SiSC-Forge closes this gap.

## 2. Goals & Non-Goals

**Goals**
- Systematically search the five priority material families with dual conventional (Eliashberg) and unconventional (DFT+U / DMFT + pairing susceptibility) pathways.
- Deliver quantitative Tc predictions (or reliable proxies) together with a multi-objective Silicon Feasibility Score (0–100).
- Support continuous active-learning loops that improve graph-neural-network surrogates from new high-fidelity data, **starting from a small, diverse seed set** (literature + goldens + early project EPW results).
- Interleaved cycle: prioritize → calculate (screening or production quality) → promote clean labels → retrain/update surrogate → re-prioritize. Full batch collection of hundreds of labels is **not** a prerequisite for useful prioritization.
- Export synthesis-relevant metadata (thermodynamic stability, thermal budgets, preferred buffer stacks, oxygen/nitrogen windows, lattice-mismatch data).
- Provide optional Tier-1 analytic Josephson-junction device metrics (critical current, IcRn, gap, switching energy) — **shipped**; Usadel/BdG remain later.
- Run productively on a single high-end workstation for development and validation; scale transparently to institutional clusters and cloud HPC with the same codebase and campaign YAML.
- Maintain a fully functional open-source primary path (Quantum ESPRESSO + EPW + TRIQS + pymatgen + jobflow + ALIGNN/MatGL-style models). VASP is optional and feature-flagged.
- **Workstation production path (must):** support multi-day DFPT and multi-candidate maps with resume, mid-step checkpoints, honest failure classification, EPW-only remediation after finished DFPT, phonon-first stability gating, and result-quality trust so pathological screening numbers do not dominate ranking.
- **Active-learning operator experience (must for the flywheel):** observable surrogate provenance, explicit promotion into the training set, bootstrap-mode messaging, and the failure-mode behaviours described in `docs/design/active-learning-flywheel.md`.

**Non-Goals**
- Full device-level Josephson-junction circuit simulation or SPICE modeling in v0.1–v1.0 (simple estimates only; full circuit simulation is roadmap).
- Closed-loop experimental control or robotic synthesis.
- Replacement of expert human judgment on final candidate selection.
- Primary support for non-silicon substrates in v1.0.
- Guaranteed discovery of room-temperature superconductors.
- Production-grade interactive GUI in early versions (CLI + Jupyter first).
- Guaranteed quantitative absolute Tc accuracy for strongly correlated materials; the platform provides ranking, relative trends, and documented uncertainties.
- **Guaranteed EPW success** on every strained supercell; auto-raised coarse k and `search_shells` do **not** guarantee physical λ/Tc.
- **Automated material-specific Wannier projections** (production hand-tuning remains later).
- **Treating coarse q=2³ phonon maps as production dynamical-stability proof** (maps are a gate only).

## 3. Target Users
- Computational materials scientists specializing in superconductivity or epitaxial thin films.
- Condensed-matter theorists working on conventional or unconventional pairing mechanisms.
- Device physicists and process engineers evaluating materials for superconducting electronics on silicon.
- Research groups with access to moderate-to-large HPC who need a ready-to-run, modular, provenance-aware pipeline.
- Graduate students and postdocs who require a structured, reproducible workflow rather than ad-hoc scripts.
- Open-source scientific software contributors who wish to extend calculators, surrogates, or material-family modules.
- **Desktop operators** running multi-day DFPT / multi-candidate phonon maps on 8–32 cores who need resume, heartbeats, and trustworthy CLI failure reasons without manual workdir surgery.

## 4. Success Metrics

**Scientific / Discovery**
- Recover literature Tc (or accepted proxy) for reference systems within documented tolerances: NbN and MgB₂ within 15–20 % under production-quality settings; qualitative recovery of metallicity and low-Tc scale for heavily B-doped Si; correct orbital occupancy and leading pairing tendency for infinite-layer NdNiO₂ under standard U, J.
- Produce ranked candidate lists for nitride alloy + strain campaigns in which known experimentally successful films appear in the top 15 % by composite score.
- Demonstrate that the Silicon Feasibility Score correlates qualitatively with published growth success rates.
- Pathological screening EPW results (inflated λ/Tc with imaginary modes or bad Wannier) **must not dominate ranking** without quality flags / penalties (“do not cite” semantics).
- Tier-1 Josephson metrics for top candidates are consistent with order-of-magnitude experimental values where available (factor ~2–3). Usadel/BdG remain later.

**Active-learning / Surrogate**
- A first usable λ/Tc (or performance) prioritization surrogate exists after ≤150 high-quality project + literature labels and demonstrably improves shortlist quality over pure heuristics.
- Active-learning cycles of 5–15 new EPW evaluations can be completed and folded back on a workstation cadence of weeks, not years.
- Ranking continues to respect the trust layer and Silicon Feasibility Score; high predicted Tc alone never overrides poor process compatibility.

**Software / Operational**
- End-to-end nitride campaign (50–200 candidates) completes on a 16–32-core workstation with real Quantum ESPRESSO phonon calculations and produces a ranked list + synthesis-metadata export.
- Multi-candidate and multi-day QE jobs **resume** on re-launch of the same campaign command for the common cases (finished candidates skipped; mid-step DFPT/EPW recoverable per Technical Specs).
- Remediable EPW failures after finished DFPT (`kmesh_get_bvector`, stale NSCF after nkc raise) are **classified correctly**, retried **EPW-only** (phonon/dyn sacred), and do not require full DFPT redo.
- Remediable phonon **setup** failures (`phq_setup` / FFT–symmetry, d_matrix) are classified as phonon (never as EPW), retried once with nosym/noinv when enabled, and are **not** reported as dynamical instability.
- ML pre-filter reduces the number of expensive DFPT/EPW calculations by ≥10× while retaining known high-Tc examples in the top decile (when surrogates are trained).
- A new Calculator plugin can be added and exercised in a campaign with <1–2 days of developer effort.
- 100 % of v0.1 features pass unit + integration tests; a golden-system regression suite exists.
- Reproducible environment: Docker (or equivalent) image with QE ≥ 7.2 + EPW + SSSP + package install for a second machine.

**Usability**
- A new user familiar with pymatgen and Quantum ESPRESSO can launch a standard nitride campaign from an example YAML in <30–60 minutes.
- Clean installation via conda/mamba + documented Docker/Apptainer images.
- Exported synthesis metadata judged “actionable” by at least one experimental collaborator.
- CLI primary failure reasons **match the failing step** (phonon vs EPW vs SCF) for known fingerprints.
- Progress heartbeats and walltime expectation bands are available for long QE steps so multi-hour DFPT is not silent.
- Surrogate provenance (model version, training-set size, acquisition weights) is visible in status and synthesis cards.

## 5. High-Level Features (Prioritized)

**P0 – Must-Have for v0.1 (Workstation-validatable) — shipped / required for desktop production path**
- YAML-driven structure enumeration for TM nitrides (binary + ternary) and heavily B-doped Si, with epitaxial strain to Si(001)/Si(111); **Si 45° epitaxy + buffer library** for nitrides.
- Formation-energy / energy-above-hull surrogate (heuristic stub acceptable; GNN later) with pre-filtering.
- Heuristic + lattice-mismatch Silicon Feasibility Score and composite ranking.
- Quantum ESPRESSO relaxation + SCF + DFPT/phonopy phonon workflows with full provenance (jobflow optional for sequential path).
- Store + CLI for campaign launch, ranking, and export of ranked candidates + synthesis-relevant metadata (JSON/CSV/CIF).
- Dry-run / mock-calculator mode.
- Fully open-source primary execution path; **QE ≥ 7.2** for working `ph.x` (not broken Ubuntu 6.7-only).
- **Result-quality / trust layer** for screening EPW (flags, penalties, “do not cite” when unreliable).
- **Campaign resume** (skip finished evaluations) + **mid-step QE workdir checkpoints** + optional DFPT `recover=.true.`.
- **EPW parallel topology** validation (nproc / npool).
- **EPW coarse-k Wannier safety** (tier minima, preflight), **EPW-only remediation** (nkc 6→8→12; Phase B `search_shells`; NSCF invalidation when nkc changes; phonon sacred).
- **Phonon-first maps** (`do_epw: false`) + **`shortlist --mode stable_only` / `stable_or_soft`** + **`rank --stable-first`**.
- **Phonon-specific diagnose** (never mislabel as EPW k-grid) + **FFT/symmetry and d_matrix nosym retries**.
- **Refine-from-store** denser EPW path; heartbeats; walltime UX.
- **Docker image** bundling QE ≥ 7.2 + EPW + SSSP + package.

**P1 – Core for First Production Campaigns**
- Automated EPW + isotropic Eliashberg Tc pipeline (base shipped; denser production grids + hand projs remain operator-driven).
- Multi-task or dedicated GNN (or simpler) surrogates for λ, ω_log, and Tc proxy with calibrated uncertainty.
- Seed-set management + literature ingestion path.
- Explicit promotion of clean results into the training set (never silent).
- Active-learning prioritization (already shipped) **plus** a lightweight retrain/update path that can be triggered after each shortlist cycle.
- Bootstrap-mode observability and the operator-experience requirements in `docs/design/active-learning-flywheel.md`.
- Improved buffer-layer recommendation engine and thermal-budget scoring (v0.2 scorer + buffers shipped; multi-layer stacks + critical thickness shipped in Phase 2).
- MgB₂ and simple boride support.
- Screening-quality vs production-quality calculation tags.

**P2 – Silicon Integration Maturity + Ranking Polish — DELIVERED (P2.1–P2.5)**
- Full Si-feasibility component breakdown with YAML weights and export (P2.1).
- Multi-layer buffer stacks + chemical/thermal window flags (P2.2).
- Critical thickness (Matthews–Blakeslee / People–Bean) + membrane-transfer heuristics (P2.3).
- Multi-objective ranking weights + Pareto front + ranking provenance (P2.4).
- Process-recommendation synthesis cards + frozen schema `1.0` (`process_recommendations.json`) (P2.5).
- (Deferred from P2) interface-slab DFT, FEM membrane mechanics, CALPHAD — later.

**P3 – Unconventional Pathway — software path shipped (P3.1–P3.6)**
- **Shipped (P3.1–P3.6):** DFT+U + `DFTUResult`; Wannier prep + `ready_for_dmft` gate + P3.2.1 automated nscf/pw2wannier90; `DMFTResult` scaffold (model + gate + mock + drop-in parser — **not** full automated solid_dmft/CTHYB launch); pairing eigenvalue → common `performance_score` with documented precedence; oxygen-vacancy / infinite-layer enumeration for nickelates; mixed conventional/unconventional AL acquisition (`off` / `joint` / `separate`).
- **Residual:** full real solid_dmft / CTHYB launch (`p3_x_real_launch`); production GNN λ/Tc heads (ALIGNN/MatGL); material-specific production Wannier libraries; NdNiO₂ science golden.

**P4 – Device-Aware Ranking — Tier-1 complete (P4.1–P4.2)**
- **Shipped:** `JosephsonMetrics` + Ambegaokar–Baratoff / BCS-from-Tc analytics; fabrication-compatibility heuristics (SIS / SNS / ramp-edge, BEOL / thermal); optional presentation-only secondary sort. Approximate / ranking only; inert unless enabled. Not process qualification.
- **Later (Phase 4 residual):** Tier-2 Usadel, Tier-3 BdG; full PDK rule decks; anisotropic Eliashberg / SCDFT; proximity refinements; generative models, web dashboard.

## 6. User Stories / Key Workflows

**US1 – Materials Scientist (Campaign Lead)**  
As a computational materials scientist, I want to define a single YAML campaign that explores Nb₁₋ₓTiₓN compositions under epitaxial strain on Si(001), so that the system enumerates cells, filters by ML stability and Si-feasibility, runs phonon (and later EPW) calculations on the most promising candidates, and returns a ranked list with recommended buffers and synthesis metadata.

**US2 – Unconventional Superconductivity Specialist**  
As a theorist focused on nickelates, I want the same ranking framework to accept either Eliashberg Tc or DMFT leading pairing eigenvalues as the performance metric (via the common `performance_score` and documented precedence), so that conventional and unconventional candidates can be compared on equal footing with a consistent Silicon Feasibility Score. (Pairing map and ranking wiring shipped; full automated DMFT launch residual.)

**US3 – Process / Device Engineer**  
As a superconducting-electronics process engineer, I want every high-ranking candidate accompanied by a clear synthesis metadata card and machine-readable process-recommendation record (schema 1.0: max thermal budget, preferred buffer stack, lattice mismatch, chemical/thermal windows, membrane-transfer notes), so that I can assess foundry compatibility immediately. (Process-recommendation cards and schema freeze shipped in P2.5.)

**US4 – Developer / Extender**  
As a developer, I want to implement a new Calculator class (new property, new solver, or new material-family enumerator) that returns a validated Pydantic result model and have it automatically appear in the workflow registry, ranking, and active-learning loop without modifying core orchestration code.

**US5 – Workstation-Only User**  
As a researcher with only a high-end workstation today, I want to validate the entire pipeline on known systems (NbN, MgB₂) and generate a small ranked list of nitride variants, so that when a large allocation becomes available I can immediately scale the identical codebase and campaign definitions.

**US6 – Desktop multi-day DFPT operator**  
As a desktop operator running refine-tier EPW, I want multi-hour/day DFPT to checkpoint and resume after sleep, reboot, or kill, so that I never redo finished phonon solely because EPW or NSCF failed later. When EPW fails with Wannier b-vector errors, the system **must** retry EPW-only (denser nkc, then `search_shells`) and **must not** delete `ph.out` / `*.dyn*` / `_ph0`.

**US7 – Phonon-first map operator (second machine)**  
As an operator mapping composition × strain, I want a broad **phonon-only** campaign (`do_epw: false`), then `shortlist --mode stable_only` into an EPW campaign, so that EPW wall-time is spent only on dynamically plausible cells—not on an all-unreliable shortlist from pathological screening EPW.

**US8 – Failure-reason trust**  
As a user of 40+ candidate maps, I want CLI one-liners and evaluation notes to name the real failure class (`phq_setup` FFT/symmetry, d_matrix, EPW kmesh_get_bvector, frozen window)—never an EPW label on a phonon-only job—so that I can decide retry vs skip without opening multi-MB logs.

**US9 – Active-learning operator**  
As a desktop operator running the surrogate flywheel, I want every shortlist and ranking to record which model version produced it, how many labels it was trained on, and whether the system is still in bootstrap mode, so that I never treat an under-trained model as authoritative. I also want explicit promotion of clean EPW results into the training set and clear refusal when a retrain would pollute the model.

**Key v0.1 / desktop workflows**

*A. Conventional shortlist → EPW (screening or refine)*  
1. Edit campaign YAML (or generate via `siscforge shortlist` / `siscforge refine`).  
2. `siscforge run` (mock dry-run or `--calculator qe` / `qe-epw`).  
3. Resume re-runs skip finished evaluations and mid-step QE when possible.  
4. Ranking applies trust penalties; export synthesis cards.

*B. Phonon-first → stable_only EPW (recommended for broad maps)*  
1. `siscforge run --calculator qe examples/nbti_n_phonon_map.yaml` (`do_epw: false`).  
2. `siscforge shortlist … --mode stable_only` (or `stable_or_soft`).  
3. Optional denser `siscforge refine` from store winners.  
4. Real EPW only on gated cells; rank with `--stable-first` as needed.

*C. Post-DFPT EPW remediation (automatic)*  
1. DFPT completes (JOB DONE, dyn mesh).  
2. EPW fails `kmesh_get_bvector` → Phase A denser nkc (re-NSCF); if exhausted → Phase B `search_shells`.  
3. User is told when ladders are exhausted; phonon remains usable for ranking/gating.  
4. Terminal state **phonon-complete / EPW-blocked** is valid and actionable.

*D. Active-learning cycle (target)*  
1. Seed or update training set (goldens + promoted labels).  
2. Train / fine-tune surrogate (or fall back to heuristic).  
3. Acquisition → shortlist.  
4. Phonon-first → EPW on shortlist.  
5. Promote clean results; retrain; repeat.

## 7. Constraints & Assumptions

**Constraints**
- The primary execution path must be fully functional using only open-source components (Quantum ESPRESSO ≥7.2 + EPW, TRIQS, pymatgen, ASE, phonopy, jobflow, ALIGNN/MatGL, MongoDB or PostgreSQL, PyTorch). VASP is optional and feature-flagged; users supply their own license and binaries.
- Development and v0.1 scientific validation must be possible on a single high-end workstation (≤32 cores, 1 GPU, ≤128–256 GB RAM) before any large HPC allocation is required.
- The same codebase and campaign YAML must run unchanged from workstation (local backend) to SLURM-type clusters (jobflow SLURM backend).
- All external codes are treated as black-box engines behind stable Calculator interfaces.
- Pseudopotential libraries (SSSP or PseudoDojo) are version-pinned and hash-tracked.
- Python ≥3.11 scientific stack with type hints and Pydantic v2; containerization (Docker/Apptainer) is the recommended distribution method for DFT/DMFT engines.
- **Completed DFPT is a high-value artifact.** EPW remediation and resume **must not** destroy phonon / dyn / `_ph0` / dvscf for remediable EPW classes.
- Screening EPW templates use `proj=random` and coarse grids; results are **order-of-magnitude** and subject to the trust layer.

**Assumptions**
- Users have basic familiarity with DFT workflows and YAML.
- Sufficient public + internal DFT data will become available to train useful surrogates.
- Experimental collaborators will interpret synthesis metadata with domain expertise.
- Network file systems or object storage are available for large intermediate files on HPC.
- Operators will re-run the **same** campaign YAML + `output_dir` to resume (not invent parallel workdirs without intent).

## 8. Risks & Mitigations

**Scientific / Physics Risks**
- Imaginary phonons / dynamical instability under epitaxial strain → Automatic detection, soft-mode flags, trust-layer penalties; phonon-first maps + `stable_only` shortlist before expensive EPW; denser q later for production stability claims.
- **Pathological screening EPW λ/Tc** (often with soft/imaginary modes or random Wannier) → Result-quality flags (`imaginary_modes`, `high_lambda`, …), ranking penalties, explicit “do not cite Tc until trust flags improve” in refine docs and cards.
- Poor Wannierization / `kmesh_get_bvector` after multi-day DFPT → Pre-DFPT coarse-k minima by tier; EPW-only nkc ladder; Phase B `search_shells`; never redo DFPT for this class; material-specific projections **later**.
- Extreme sensitivity of nickelates/cuprates to U, J, double-counting, and oxygen stoichiometry → Treat U,J as campaign parameters; oxygen-vacancy enumeration as first-class later; clear process-window metadata.
- ML domain shift on highly strained or doped films → Continuous active learning with strain-augmented data; uncertainty quantification; human review of high-uncertainty high-ranking candidates.
- Over-optimistic Si-feasibility scores → Transparent component breakdown; conservative defaults; versioned scoring rules; experimental collaborator re-weighting.
- Josephson estimates are highly approximate → Clearly labeled as order-of-magnitude / ranking aids only; never presented as quantitative device design values in early versions.
- Coarse q=2³ phonon maps mis-label stability → Document as **gate only**, not production dynamical-stability proof; denser DFPT for shortlisted cells.
- Premature trust in under-trained surrogates → Bootstrap-mode messaging, higher exploration weight early, explicit provenance on every ranking.

**Software / Engineering Risks**
- High failure rate of expensive EPW/DMFT jobs → Robust error classification, quality tags, EPW-only auto-remediation, acquisition that can avoid known-failure regions.
- **Multi-day DFPT lost on interrupt or EPW failure** → Campaign resume, mid-step checkpoints, optional `recover=.true.`, heartbeats; EPW remediation never deletes phonon artifacts.
- **Stale NSCF after nkc raise** → Mesh fingerprint + auto-invalidate NSCF/EPW electronic outputs only.
- **Phonon failures mislabeled as EPW** → Phonon-specific diagnose when `do_epw=false` / phonon step; CLI primary reason from step-aware extractors.
- **`phq_setup` / FFT grid incompatible with symmetry** on ordered ternaries → Fingerprint + one nosym/noinv SCF+PH retry; setup failure ≠ dynamical instability.
- Workflow brittleness as calculation types grow → Strict Pydantic contracts, mock-calculator mode, comprehensive unit tests (no real QE required for remediation logic).
- Database schema evolution → Versioned models; store raw files alongside structured data; file-based store acceptable on workstation.
- Performance cliffs on HPC (I/O, queue latency) → Early containerization (Docker with QE≥7.2), staged scaling tests, local caching of pseudopotentials and models.
- Scope creep → Explicit Non-Goals and hard Phase 0/1 exit criteria focused on materials ranking + workstation operability.
- Training-set pollution or untraceable model versions → Explicit promotion gate, immutable training-set snapshots, surrogate provenance on every decision (see design note).

## 9. Future Roadmap

**Live status (2026-08):** Phase 2 is complete. Phase 3 **P3.1–P3.6 software
path** is landed (DFT+U → Wannier gate → DMFT scaffold → pairing score →
O-vacancy enum → mixed AL). Real CTHYB / production GNN λ/Tc heads remain
residual. Phase 4 **Tier-1** (P4.1–P4.2) is shipped; Usadel/BdG remain later.
See [`docs/ROADMAP.md`](../ROADMAP.md) for the operational table — this
section is the original version narrative, not the live status board.

- **v0.1 (Workstation Foundation)** — Structure gen (nitrides + B:Si), formation-energy surrogate, QE phonon, heuristic Si-score, ranking, store, CLI, dry-run. Exit: validated NbN phonon + small nitride campaign on workstation.
- **v0.1+ / desktop production path (shipped alongside Phase 1)** — EPW + isotropic Tc, trust layer, resume/checkpoint, shortlist/refine, phonon-first + stable_only, EPW coarse-k + Phase B shells, phonon FFT/symmetry retry, Docker QE≥7.2, Si 45°/buffers.
- **v0.5 (Conventional Production polish + AL bootstrap)** — Seed-set management, first trained λ/Tc surrogates, interleaved retrain cycles, bootstrap observability, hand-tuned Wannier for production shortlists, denser automated grid policies with stronger validation.
- **v1.0 (Dual Pathway + Advanced Si)** — DMFT + pairing for nickelates, full interface/membrane modeling, multi-objective ranking, synthesis cards.
- **v1.x+** — Usadel/BdG Josephson backends, anisotropic/SCDFT, proximity refinements, generative models, web UI, community contributions.

### Explicit later (not blocking workstation production path)

| Item | Notes |
|------|--------|
| Material-specific Wannier projections | Still required for reliable production λ/Tc |
| Guaranteed EPW success on all strained cells | Terminal phonon-complete / EPW-blocked is valid |
| Full mature AL retrain on large EPW corpora | Prioritization + lightweight retrain first; mature loops later |
| Full real DMFT launch / Josephson Usadel | Phase 3 residual / Phase 4 later |
| Room-temperature SC discovery | Non-goal |

---

*This PRD (v0.4.3) is the authoritative source of product requirements for SiSC-Forge. All implementation work should be driven by and consistent with this document, the companion Technical Specifications, and the design note `docs/design/active-learning-flywheel.md`. Incident-level detail lives in `docs/implementation-notes.md` (Slices 13–28 + P3.1–P3.6 + P4.1–P4.2); this PRD states the requirements those slices satisfy. Phase 4 Tier-1 exit: `docs/phase4-exit.md`.*
