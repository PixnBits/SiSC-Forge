# SiSC-Forge
## Technical Specifications

**Version 0.4 – Workstation production path**  
*(Extends v0.3 Josephson contracts with DFT/EPW/phonon operability, trust layer, remediation, CLI, and acceptance criteria learned from real desktop campaigns. Josephson module remains Phase 3+ and inert until enabled.)*

### Changelog (v0.3 → v0.4)

| Area | Added / tightened |
|------|-------------------|
| §2.3 DFT/DFPT/EPW | Preflight, nkc minima, Phase A/B remediation, NSCF invalidation, phonon sacred |
| §2.3b Phonon path | Fingerprints, FFT/symmetry + d_matrix nosym retries, setup ≠ instability |
| §2.6 Ranking / quality | result_quality flags, penalties, stable_only semantics |
| §2.7 / CLI | shortlist modes, refine, rank --stable-first, resume, heartbeats, walltime |
| §2.9 Silicon | 45° epitaxy + buffers (shipped) |
| §3 Models | PhononResult, ElectronPhononResult, evaluation notes, remediation JSON |
| §5 Config | run.resume, epw auto-retry, phonon_retry_*, quality_tag, do_epw |
| §8–9 Tests & AC | Incident-driven acceptance criteria |
| §10 Limitations | Explicit EPW-blocked terminal state; q=2³ is a gate only |

---

## 1. Overall Architecture

SiSC-Forge uses a modular, layered, message-passing architecture. All inter-module communication occurs via strongly-typed Pydantic v2 models.

**High-level component flow:**

```
User / Config Layer (YAML campaigns, CLI, Jupyter)
        │
Structure Generation & Enumeration
        │ StructureCandidate[]
ML Surrogate Layer (ALIGNN / MatGL + custom heads / stubs)
        │ Filtered + scored candidates
Workflow Engine & Active Learning Manager (jobflow + sequential QE path)
        │
   ┌────┴────┐
   │         │
DFT/DFPT/EPW  DMFT / Pairing
(QE + EPW)   (TRIQS)          ← EPW remediation, checkpoints, trust tags
   │         │
   └────┬────┘
        │ CalculationResult hierarchy
Silicon Integration & Interface Module
        │
Candidate Ranking & Reporting (+ result_quality)
        │
Josephson Junction Device Modeling Module  ← §2.8 (Phase 3+)
        │
Provenance Store + Active-Learning Feedback
```

**Design principles**
- Decoupled engines: pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering of expensive calculations when surrogates exist; phonon-first gating for broad maps.
- Provenance-first (lightweight jobflow or sequential recipes with workdir checkpoints).
- Extensible Calculator registry.
- Identical code path from workstation to HPC.
- **Finished DFPT is sacred** relative to remediable EPW electronic failures.

**v0.1 Must-Have (foundation)**  
Structure generation → formation-energy filter → QE-SCF + DFPT phonon → heuristic Si-score → store + ranking + dry-run.

**Workstation production path (required alongside Phase 1 EPW)**  
Resume / mid-step checkpoint · EPW topology + coarse-k safety · EPW-only remediation · trust layer · phonon-first + stable_only · phonon diagnose/retry · Docker QE≥7.2.

---

## 2. Core Modules

### 2.1 Structure Generation & Enumeration
P0 for TM nitrides (binary + ordered ternary) and B-doped Si with epitaxial strain to Si(001)/Si(111).  
**Shipped:** cube-on-cube and **45°** epitaxial matching helpers; strain series in campaign YAML.

### 2.2 ML Surrogate Layer
P0 = formation energy + uncertainty (heuristic stub acceptable).  
P1 = λ / ω_log / Tc proxies (family-heuristic stub shipped; trained GNN later).  
Active-learning **prioritization** (top-k expensive path) is in scope; full retrain loop is later.

### 2.3 DFT / DFPT / EPW Orchestration

**Purpose**  
Run Quantum ESPRESSO relaxation, SCF, multi-q DFPT (phonon), optional EPW + isotropic Eliashberg/Allen–Dynes on a workstation-friendly sequential path (and jobflow-ready recipes).

#### 2.3.1 Binary requirements
- **Must** use QE ≥ 7.2 for production DFPT (`ph.x`); Ubuntu-packaged 6.7 `ph.x` is known-broken (buffer overflow) and **must not** be the sole production binary.
- Calculators: `qe` (phonon path), `qe-epw` (append EPW after DFPT).
- Flat workdir layout for EPW prep (`02_scf/` with `outdir` shared for `_ph0`, `*.dyn*`, `save/`).

#### 2.3.2 Pre-DFPT EPW preflight (when `do_epw` / EPW enabled)
| Check | Rule |
|-------|------|
| Parallel | `epw.npool` must satisfy EPW topology with `dft.nproc` (typically `npool == nproc`, nimage=1); auto-set unless strict |
| Coarse k (`epw.nkc`) | **Tier minima:** `workstation_dense` / `production` and `n_atoms ≥ 8` → **minimum 8³**; never emit default **nk=6** for those cells when auto-raise is on |
| Coarse q (`epw.nqc`) | **Must** match `dft.qpoints` (DFPT mesh); auto-align nqc → qpoints before DFPT |
| Strict mode | `epw.strict_coarse_k: true` hard-fails instead of auto-raising nkc |

Preflight runs once at campaign/workflow start and logs a summary line.

#### 2.3.3 Mid-step resume & DFPT recover
- `run.resume` / evaluation-level skip of finished candidates.
- Workdir checkpoint probes: vc-relax, scf, phonon, epw_pp, nscf, epw.
- Incomplete DFPT: optional QE `recover=.true.` when safe; else clean phonon step only for that candidate.
- Heartbeats (`run.heartbeat_seconds`, default 900) during long `pw.x` / `ph.x` / `epw.x`.
- Walltime expectation bands for campaign planning (informational UX).

#### 2.3.4 Post-DFPT EPW-only remediation
Triggered after **successful DFPT** when EPW fails with remediable classes.

| Phase | Failure class | Action | DFPT | NSCF |
|-------|---------------|--------|------|------|
| A | `kmesh_bvector` (and related k-mesh) | Raise isotropic nkc **6→8→12** (max `max_kmesh_retries`, default 2) | **keep** | rebuild when nkc changes |
| A′ | k-grid inconsistency / stale mesh | Invalidate NSCF+EPW electronic; rebuild at **current** nkc | **keep** | rebuild |
| B | still `kmesh_bvector` after nk ladder | Raise Wannier90 **`search_shells` 36→48** via EPW `wdata` (max 2) | **keep** | **reuse** |
| — | ladders exhausted | Terminal **phonon-complete / EPW-blocked**; actionable notes | **keep** | — |

- Sidecar: `siscforge_epw_remediation.json` in the candidate workdir (`phase`: `nkc` | `search_shells`, attempt notes). Anti-loop on resume.
- **Must not** change `nqc` / DFPT qpoints for finished phonon.
- **Must not** delete `ph.out`, `*.dyn*`, `_ph0`, dvscf for these classes.
- Config: `epw.auto_retry_kmesh`, `max_kmesh_retries`, `auto_retry_search_shells`, `max_search_shells_retries`, `search_shells`, optional `kmesh_tol` (not auto-loosened by default).

#### 2.3.5 Wannier screening template (explicit limits)
- Screening uses `proj=random`, auto `nbndsub`, tight frozen windows, optional froz-window one-retry.
- Auto-nk and Phase B shells **do not** guarantee physical λ/Tc.
- Material-specific projections: **later**.

### 2.3b Phonon path (phonon-only and DFPT stage)

**Diagnose (must)**  
- Phonon-only failures **must not** surface as `EPW: k-grid inconsistency` (or other EPW-only labels).
- Known fingerprints include:
  - `phq_setup` / `FFT grid incompatible with symmetry`
  - `d_matrix` / non-orthogonal `D_S`
  - buffer overflow (broken system QE 6.7)
- CLI primary reason and evaluation notes use step-aware extractors (`step_name=phonon` when applicable).

**Auto-retry (default on, cap 1)**  
| Flag | Fingerprint | Action |
|------|-------------|--------|
| `dft.phonon_retry_on_fft_symmetry` | FFT grid incompatible with symmetry | One SCF+PH with `nosym=.true.` `noinv=.true.` |
| `dft.phonon_retry_on_d_matrix` | d_matrix / D_S not orthogonal | Same nosym recovery |

- Log: `phonon failed (FFT grid incompatible with symmetry) — retrying once with nosym+noinv SCF/PH`.
- Success notes that recovery was used; failure remains **setup failure**, not dynamical instability.
- Only the affected candidate’s SCF/phonon steps are redone—not campaign-wide delete.

**Stability semantics**  
- Setup failure / failed `PhononResult.status` → `dynamically_stable=false`; no modes → not stable.
- Completed phonon with imaginary modes → stability conclusion (`has_imaginary_modes`).
- `stable_only` shortlist **must ignore** setup-failed and non-ok evaluations.

### 2.4 Unconventional (DFT+U / DMFT) Pathway
Produces `leading_pairing_eigenvalue` that feeds the common `performance_score`. Phase 2+; not required for workstation conventional path.

### 2.5 Silicon Integration & Interface Module
Produces Si-Feasibility Score 0–100 plus process recommendations.  
**Shipped (v0.2 scorer):** lattice mismatch, cube/45° matching, buffer library hints, thermal/chemical heuristics.  
**Later:** multi-layer stacks, critical thickness, membrane mechanics, interface slabs.

### 2.6 Candidate Ranking & Reporting

- Consumes Eliashberg Tc or pairing eigenvalue via `performance_score`, always includes Silicon Feasibility breakdown.
- **Result-quality / trust layer (must):**
  - Flags such as imaginary modes, high λ, screening Wannier, EPW failed, etc.
  - Ranking penalties so pathological screening EPW does not dominate.
  - Export/docs language: do not cite Tc until trust flags improve.
- **Phonon-aware ranking:**
  - `rank --stable-first` prefers dynamically stable phonons.
  - Setup failures are not “stable.”
- JosephsonMetrics optional secondary ranking when module enabled (Phase 3+).

### 2.7 Workflow Engine, CLI & Active Learning

**CLI (must for desktop path)**  
| Command / option | Role |
|------------------|------|
| `siscforge run` | Campaign; `--dry-run`, `--calculator qe\|qe-epw`, `-o` output_dir |
| `run.resume` / skip finished | Multi-candidate checkpoint |
| `resume_qe_steps` | Mid-step workdir reuse |
| Heartbeats | Long QE progress |
| `siscforge shortlist` | From store → focused EPW YAML; `--mode stable_only` \| `stable_or_soft` \| performance modes |
| `siscforge refine` | Denser EPW from store winners; separate `output_dir`; `quality_tag` production / workstation_dense grids |
| `siscforge rank` | Table export; `--stable-first` |

**Active learning**  
Minimal prioritization (top-k expensive path) shipped; full retrain later.

### 2.8 Josephson Junction Device Modeling Module  ← Phase 3+

*(Unchanged in intent from v0.3. Disabled by default. Tier-1 analytic estimates on top-N shortlist; Usadel/BdG later. Always labeled approximate / ranking only.)*

See prior v0.3 detail for inputs, Ambegaokar–Baratoff formulas, fabrication heuristics, and Phase 3 acceptance criteria. Module remains a stub until Phase 3.

### 2.9 Docker / distribution
- Multi-stage image: Ubuntu LTS + QE ≥ 7.2 from source (`pw` `ph` `pp` `epw`) + Wannier90 + SSSP + `pip install -e ".[dev,qe,phonopy]"`.
- Env: `QE_BIN`, `SISCFORGE_PSEUDO_DIR`, PATH with private QE first.
- Verification script: binaries, `detect_qe_environment`, mock pytest, dry-run campaigns, UPF presence.
- **Must not** rely on broken system `ph.x` 6.7 for production DFPT.

---

## 3. Data Models / Key Objects

All models are Pydantic v2.

### 3.1 PhononResult (conventional phonon / DFPT)

| Field | Semantics |
|-------|-----------|
| `min_frequency_cm1` / `max_frequency_cm1` | cm⁻¹; imaginary as negative where applicable |
| `has_imaginary_modes` | True if modes below campaign imag threshold |
| `dynamically_stable` | Convenience; **false** if status failed / no modes / imag modes |
| `n_modes` | Count when parsed |
| `status` | `ok` \| `failed` \| `mock` \| … — setup crashes are `failed` |
| `quality_tag` | screening \| production \| mock |
| `raw` | source, job_done, frequencies, thresholds |

**Rule:** empty frequency list is **not** dynamically stable (setup / incomplete).

### 3.2 ElectronPhononResult

| Field | Semantics |
|-------|-----------|
| `lambda_total`, `omega_log` (K), `mu_star` | Moments / coupling |
| `Tc_allen_dynes`, `Tc_eliashberg` | K |
| `converged`, `status`, `quality_tag` | Trust inputs |
| `wannier_ok` | When known |
| `alpha2F_summary` | May include `primary_failure`, remediation notes |

Screening results may be present with **quality flags** that ranking must honor.

### 3.3 CandidateEvaluation

| Field | Semantics |
|-------|-----------|
| `candidate` | StructureCandidate |
| `scf`, `phonon`, `electron_phonon` | Optional typed results |
| `si_feasibility`, `performance_score`, `composite_score` | Ranking inputs |
| `status` | ok \| failed \| mock \| … |
| `errors`, `notes` | **Primary failure reason first**; workdir; diagnose; retry log |
| `result_quality` / flags | Trust layer (tier, flags, penalties) when present |
| `josephson` | Optional; Phase 3+ |

### 3.4 Remediation attempt record (workdir sidecar)

`siscforge_epw_remediation.json` (implementation detail, schema note):

```json
{
  "version": 1,
  "attempts": [
    {
      "reason": "kmesh_bvector",
      "phase": "nkc",
      "nkc_before": [8, 8, 8],
      "nkc_after": [12, 12, 12],
      "note": "…"
    },
    {
      "reason": "kmesh_bvector",
      "phase": "search_shells",
      "nkc_before": [12, 12, 12],
      "nkc_after": [12, 12, 12],
      "search_shells_before": 12,
      "search_shells_after": 36,
      "note": "…"
    }
  ]
}
```

NSCF mesh fingerprint sidecar (`siscforge_nscf_kmesh.json`) records requested coarse k for stale-mesh detection.

### 3.5 JosephsonMetrics
As in v0.3 (optional nested object; approximate / ranking only).

---

## 4. External Dependencies & Interfaces

Primary open-source stack:
- Quantum ESPRESSO ≥ 7.2 + EPW, Wannier90
- pymatgen, ASE, spglib, phonopy (optional FD)
- jobflow (optional orchestration)
- Pydantic v2, Typer, Rich, PyYAML, NumPy
- SSSP (or PseudoDojo) UPFs
- Docker recommended for second-machine parity

TRIQS / solid_dmft: Phase 2+. Usadel/BdG: Phase 3+ behind Calculator protocol.

---

## 5. Configuration & Input Formats

### 5.1 Run / resume

```yaml
run:
  resume: true                 # skip finished evaluations in output_dir
  continue_on_error: true
  force_rerun: false           # full candidate redo when true
  resume_qe_steps: true        # mid-step workdir checkpoint
  force_rerun_qe_steps: false
  heartbeat_seconds: 900
```

### 5.2 DFT / phonon / EPW (key knobs)

```yaml
dft:
  do_relax: true
  do_phonon: true
  do_epw: false                # true for EPW path; false for phonon maps
  quality_tag: screening       # or production
  nproc: 16
  qpoints: [2, 2, 2]           # DFPT mesh; nqc must match when EPW on
  phonon_retry_on_d_matrix: true
  phonon_retry_on_fft_symmetry: true
  epw:
    enabled: false
    nkc: [8, 8, 8]             # coarse electronic k (Wannier)
    nqc: [4, 4, 4]             # must match DFPT q when used
    nkf: [12, 12, 12]
    nqf: [12, 12, 12]
    npool: 16                  # typically == nproc
    auto_nbndsub: true
    auto_retry_kmesh: true
    max_kmesh_retries: 2
    auto_retry_search_shells: true
    max_search_shells_retries: 2
    search_shells: null        # null → W90 default; remediation may set 36/48
    kmesh_tol: null            # optional; not auto-loosened
    strict_coarse_k: false
    mu_star: 0.10
```

### 5.3 Shortlist / refine (CLI-driven YAML generation)
- `siscforge shortlist --mode stable_only|stable_or_soft|… -n N -o campaign.yaml`
- `siscforge refine` denser grids / `workstation_dense` tier; **separate `output_dir`** from screening store.

### 5.4 Josephson (Phase 3+; ignored until enabled)

```yaml
josephson:
  enabled: false
  shortlist_size: 20
  model_tier: "analytic_AB"
  reference_area_um2: 1.0
  assume_SIS: true
  temperature_K: null
  secondary_ranking: false
```

---

## 6. Output Formats & Database Schema

- CandidateEvaluation JSON, CSV summary, Markdown synthesis cards, optional CIF/POSCAR.
- Quality flags and primary failure reasons appear in notes/errors.
- Josephson section only when module enabled (labeled approximate).
- File-based store is first-class on workstation; MongoDB optional.

---

## 7. Performance & Scaling Targets

- Phases 0–1: productive on 8–32 cores; multi-day DFPT acceptable for refine shortlists of few candidates.
- Phonon maps (q=2³): many binary nitrides ~1–3 min/candidate on 16 cores (order-of-magnitude; hardware-dependent).
- Analytic Josephson Tier-1: ≪ 1 min for 50 candidates (Phase 3).
- EPW remediation after DFPT: electronic-only (NSCF minutes-scale at 12³; EPW failure can still be seconds on bvector).

---

## 8. Testing Strategy

**Must (no real multi-hour QE required for unit tests)**
- Coarse-k preflight never leaves production/workstation_dense ≥8-atom cells at nk=6 when auto-raise on.
- `kmesh_get_bvector` → Phase A plan then Phase B `search_shells`; phonon clean not called.
- Stale NSCF fingerprint invalidates NSCF/EPW only.
- Phonon FFT/symmetry fixture → primary reason contains phq_setup/FFT; **not** EPW k-grid.
- Phonon-only path extractors skip EPW-only needles.
- FFT/symmetry retry: one nosym SCF+PH; disabled flag skips; second failure does not loop.
- Failed/empty phonon → not dynamically stable; `stable_only` filter empty.
- Trust layer penalties for high-λ + imag modes (existing tests).
- Mock dry-run campaigns green; Docker verify script for image builds.

**Golden / optional real QE**
- NbN / MgB₂ screening EPW recovery under documented tolerances when binaries available.

---

## 9. Acceptance Criteria (incident-driven)

| # | Criterion | Must |
|---|-----------|------|
| AC1 | `workstation_dense` / `production` + `n_atoms≥8` + `do_epw` → preflight/auto-raise yields **nkc ≥ 8³** (never default nk=6 for those cells) | Yes |
| AC2 | After finished DFPT, remediable `kmesh_get_bvector` retries EPW-only (Phase A nkc, then Phase B search_shells); **phonon artifacts retained** | Yes |
| AC3 | nkc raise invalidates **NSCF+EPW only** (no manual `rm`; no phonon delete) | Yes |
| AC4 | Phonon-only failure with FFT/symmetry text reports **phonon** primary reason; never `EPW: k-grid inconsistency` | Yes |
| AC5 | FFT/symmetry triggers **at most one** nosym+noinv SCF/PH retry by default; config can disable | Yes |
| AC6 | Phonon setup failure ⇒ not `dynamically_stable`; `stable_only` shortlist ignores | Yes |
| AC7 | Campaign re-run same YAML+`output_dir` skips finished ok evaluations; mid-step resume for common QE steps | Yes |
| AC8 | CLI primary failure hint matches failing step class for known fingerprints (phonon vs EPW) | Yes |
| AC9 | Trust layer prevents silent promotion of pathological screening λ/Tc without flags/penalties | Yes |
| AC10 | Docker image provides QE≥7.2 + `epw.x` + SSSP + `siscforge` on PATH with verify suite | Yes |
| AC11 | EPW may still fail after Phase A+B → terminal **phonon-complete / EPW-blocked** with actionable notes | Yes |
| AC12 | Coarse q=2³ map is a **gate**, not production dynamical-stability certification | Yes (docs + ranking caveats) |

---

## 10. Explicit Limitations

- Auto-raised nkc and `search_shells` **do not** guarantee physical λ/Tc; screening Wannier remains `proj=random`.
- Material-specific Wannier projections, anisotropic Eliashberg, SCDFT, DMFT, Josephson: **later**.
- After Phase A+B exhaustion, further EPW success may require human projections or different cells; DFPT remains valuable for stability gating.
- Screening q=2³ phonon stability can false-positive/false-negative; denser DFPT required before citing dynamical stability.
- Resume covers common cases; exotic partial files or external manual edits may still need operator intervention (documented in implementation-notes).
- Room-temperature superconductor discovery is **not** promised.

---

## 11. Development Phases & Milestones

**Phase 0 (v0.1 foundation)** — Structure gen, formation filter, QE phonon, Si-score, ranking, store, CLI, dry-run.  
**Exit**: NbN phonon; small nitride campaign on workstation.

**Phase 1 (conventional EPW + desktop operability)** — EPW + isotropic Tc, quality tags, shortlist/refine, trust layer, resume/checkpoint, EPW parallel + coarse-k + Phase B, phonon-first + stable_only, phonon diagnose/retry, Docker.  
**Exit**: golden NbN/MgB₂ path (mock always; real optional); desktop remediation ACs green; see `docs/phase1-exit.md`.

**Phase 2** — DMFT + pairing, advanced Si integration (membranes/interfaces), multi-objective ranking, production Wannier automation.

**Phase 3** — Josephson Tier-1 analytic estimates on shortlist; later Usadel/BdG.

---

*This document (v0.4) is implementation-ready. Workstation production-path contracts above match shipped behavior in `docs/implementation-notes.md` (Slices 13–28). Josephson remains fully specified but inert until Phase 3. PRD v0.3 is the product authority; this file is the engineering contract.*
