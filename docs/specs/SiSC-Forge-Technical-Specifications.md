# SiSC-Forge
## Technical Specifications

**Version 0.5.4 – Phase 2 complete + Phase 3 P3.1–P3.6 + Phase 4 Tier-1 (P4.1–P4.2) + phonon-map recovery**  
*(Extends v0.5.3 with the conventional-path soft-mode report, denser-q
pilot helper, and empty-`stable_only` messaging. Additive desktop
operability; Phase 3/4 status unchanged. Usadel/BdG remain later.)*

### Changelog (v0.5.3 → v0.5.4)

| Area | Added / tightened |
|------|-------------------|
| §2.3c | Soft-mode report contract (per-candidate + campaign JSON/Markdown); denser-q pilot helper from a phonon-map store |
| §2.6 / §2.7 | Empty `stable_only` must surface the report + `siscforge pilot`; never fall back to unstable top-k for EPW. Soft-mode class / pilot provenance may later feed AL (not required this slice) |
| Acceptance | AC19–AC22 for report, pilot YAML, empty-shortlist messaging, `do_epw: false` |
| Limitations | Classification is heuristic; denser pilot ≠ production dynamical-stability proof |
| Docs | `docs/examples/nbti_n_phonon_map.md`; implementation-notes Slice 29 |

### Changelog (v0.5.2 → v0.5.3)

| Area | Added / tightened |
|------|-------------------|
| §2.8 / §3.5 / §11 | P4.2 fabrication hints + presentation-only secondary sort recorded as shipped |
| Acceptance | Phase 4 **Tier-1 exit met**; Usadel/BdG residual (not fabrication) |
| Docs | `docs/phase4-exit.md` |
| §9 | Phase 3 P3.1–P3.6 contract rows (software path vs CTHYB residual) |

### Changelog (v0.5.1 → v0.5.2)

| Area | Added / tightened |
|------|-------------------|
| §2.8 Josephson | P4.1 Tier-1 Ambegaokar–Baratoff / BCS-from-Tc; units; inert default |
| §3.5 JosephsonMetrics | Implemented field list (gap meV, IcRn mV, Jc, EJ) |
| §5.4 Config | `shortlist_only`, `rna_ohm_um2`, `bcs_gap_ratio` |
| Acceptance | P4.1 shipped; Usadel remains open (fabrication engine is P4.2) |

### Changelog (v0.5.0 → v0.5.1)

| Area | Added / tightened |
|------|-------------------|
| §2.7 Active Learning | `pool_mode` off/joint/separate; pool derivation precedence; per-pool quotas |
| §3 Models | `PrioritizationRecord.acquisition_mode` / `pool_counts`; evaluation pool fields |
| Acceptance | P3.6 mixed AL shipped; real CTHYB launch and production GNN remain open |

### Changelog (v0.4.1 → v0.5.0)

| Area | Added / tightened |
|------|-------------------|
| §2.5 Silicon | SiFeasibility components, weights, multi-layer buffers/stacks, critical thickness, membrane flags — **implemented** |
| §2.6 Ranking | Multi-objective weights, Pareto, `performance_score_source` / precedence (`epw_then_dmft`) |
| §2.4 Unconventional | DFT+U, Wannier gate, DMFTResult scaffold limits, pairing map formula reference |
| §3 Models | DFTUResult, WannierResult, DMFTResult (scaffold), process-recommendation schema 1.0 |
| Acceptance | Mark which ACs satisfied; real CTHYB launch remains open |
| Calculator plugins | `qe`, `qe-dftu`, `qe-wannier`, `qe-dmft` / aliases; unconventional steps **default off** |

### Changelog (v0.4 → v0.4.1)

| Area | Added / tightened |
|------|-------------------|
| §2.2 ML Surrogate Layer | Seed set, promotion gate, model metadata, bootstrap mode, acquisition provenance |
| §2.7 Active Learning | Lightweight retrain trigger, training-set hygiene, failure modes |
| §3 Models | SurrogatePrediction, TrainingExample, PrioritizationRecord (notes) |
| §9 Acceptance Criteria | AC13–AC18 for AL bootstrap & operator experience |
| References | Design note `docs/design/active-learning-flywheel.md` |

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
        │ Filtered + scored candidates + SurrogatePrediction
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
Candidate Ranking & Reporting (+ result_quality + surrogate provenance)
        │
Josephson Junction Device Modeling Module  ← §2.8 (Phase 4)
        │
Provenance Store + Active-Learning Feedback (promotion → training set → retrain)
```

**Design principles**
- Decoupled engines: pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering of expensive calculations when surrogates exist; phonon-first gating for broad maps.
- Provenance-first (lightweight jobflow or sequential recipes with workdir checkpoints).
- Extensible Calculator registry.
- Identical code path from workstation to HPC.
- **Finished DFPT is sacred** relative to remediable EPW electronic failures.
- **Training-set hygiene is sacred** relative to silent promotion of low-quality or mock labels.

**v0.1 Must-Have (foundation)**  
Structure generation → formation-energy filter → QE-SCF + DFPT phonon → heuristic Si-score → store + ranking + dry-run.

**Workstation production path (required alongside Phase 1 EPW)**  
Resume / mid-step checkpoint · EPW topology + coarse-k safety · EPW-only remediation · trust layer · phonon-first + stable_only · phonon diagnose/retry · soft-mode report + denser-q pilot (v0.5.4) · Docker QE≥7.2.

**Active-learning bootstrap (Phase 1 residual / 1.5)**  
Seed-set management · explicit promotion · first trained surrogate with uncertainty · acquisition provenance · bootstrap-mode observability · full mock cycle.

---

## 2. Core Modules

### 2.1 Structure Generation & Enumeration
P0 for TM nitrides (binary + ordered ternary) and B-doped Si with epitaxial strain to Si(001)/Si(111).  
**Shipped:** cube-on-cube and **45°** epitaxial matching helpers; strain series in campaign YAML.

### 2.2 ML Surrogate Layer

**P0** = formation energy + uncertainty (heuristic stub acceptable; pre-trained GNN fine-tune preferred).  
**P1** = λ / ω_log / Tc (or performance) proxies with calibrated uncertainty.  
Family-heuristic stub is already shipped; trained models and the bootstrap contracts below are the next step.

#### 2.2.1 Seed set & literature ingestion
- A versioned SeedSet contains goldens (NbN family, MgB₂, hyperdoped-Si examples), clean literature EPW results, and early project labels.
- Literature entries carry provenance (source, settings, notes).
- Target size for a first useful prioritization surrogate: roughly 50–150 high-quality labels (diversity over raw count).

#### 2.2.2 Promotion gate
- Only results whose `quality_tag` and trust-layer flags pass a configurable allow-list may become permanent TrainingExamples.
- Promotion is an **explicit** step (CLI or API). Silent inclusion is forbidden.
- Mock / dry-run labels must never enter a real training set.
- Each training-set snapshot used for a model version is immutable and hashed.

#### 2.2.3 Model metadata & provenance
Every surrogate version records:
- training-set size and composition hash,
- timestamp,
- uncertainty calibration summary,
- whether it is still in bootstrap regime.

Every prioritization / shortlist decision records the model version (or “heuristic”), acquisition weights, and a short provenance string suitable for CLI status and synthesis cards.

#### 2.2.4 Bootstrap mode
- Distinct operating regime (or continuous confidence score) while label count is low or average uncertainty remains high.
- Default acquisition weights emphasize exploration.
- CLI and cards must surface bootstrap status so operators do not treat early rankings as authoritative.

#### 2.2.5 Acquisition function
Configurable combination of predicted performance, model uncertainty, Silicon Feasibility Score, and optional diversity terms. Weights are YAML-configurable and stored with each decision.

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
- `stable_only` with **zero** survivors **must not** fall back to unstable top-k for EPW. See §2.3c.

### 2.3c Soft-mode report & denser-q pilot (phonon-map recovery)

Incident-driven (Nb–Ti–N q=2³ map, zero `dynamically_stable` survivors;
known-stable binaries NbN / TiN / ZrN also large-imaginary on that mesh).
Coarse maps remain a **discovery gate** (AC12). This subsection specifies
the operator surface that replaces file archaeology and hand-written
pilot YAML. It does **not** decide physical stability and does **not**
launch EPW.

Walkthrough: `docs/examples/nbti_n_phonon_map.md`.  
Implementation notes: Slice 29 in `docs/implementation-notes.md`.

#### Soft-mode report contract

After a finished **phonon-only** or **phonon-containing** campaign the
store **must** contain a campaign-level report, or an explicit skip
record with a clear reason (e.g. no phonon results in the store).

The write is **non-blocking**: missing frequency lists, missing `raw`
spectra, or a write failure **must not** fail the campaign. Missing
frequencies → **conservative** classification (never “stable”, never a
go-ahead for EPW).

**Per-candidate fields (must):**

| Field | Semantics |
|-------|-----------|
| `min_frequency_cm1` | Lowest ω (cm⁻¹); imaginary as negative when applicable |
| `has_imaginary_modes` / `dynamically_stable` | Copied from `PhononResult` |
| `soft_mode_class` | One of `stable` \| `likely_mesh_artefact` \| `optical_soft` \| `setup_failed` \| `genuinely_soft` \| `unknown` |
| `reasons` | Short machine-readable tokens (e.g. `known_stable_binary_nitride_on_coarse_or_screening_mesh`, `missing_frequency_list_conservative`) |
| `acoustic_vs_optical` | `optical_imaginary` \| `acoustic_only_imaginary` \| `none_below_threshold` \| `undetermined` |
| `asr_signal` | Optional; only when a single-q / Γ acoustic triplet is **detectable**. Otherwise `null` |
| `softness_locus` | Optional. `gamma` \| `finite_q` \| `both` \| `none` \| `undetermined`. Set when per-q spectra exist (`raw.qpoints` from the parser, or a flat dump chunked into 3 N_at blocks with Γ first). |
| `gamma_min_frequency_cm1` / `finite_q_min_frequency_cm1` | Optional. Lowest ω at Γ vs at any non-Γ q. Used to surface “softest q is finite-q, Γ only mildly imaginary”. |

Parser `raw.qpoints` is additive and does **not** apply ASR. `acoustic_vs_optical` may be resolved per q when each block is length 3 N_at (optical imag at any q → `optical_soft`). A flat dump that is **not** a multiple of 3 N_at stays `undetermined`.

**Classification rules (heuristic — must stay conservative):**

- `setup_failed` — phonon `status` not `ok`/`mock`, or no modes and no `min_frequency_cm1`.
- `stable` — completed phonon, no imaginary modes under the campaign threshold.
- `optical_soft` — **only** when a single-q / Γ list is detectable (list length = 3 N_at) **and** imaginary weight sits on optical branches. A flat mesh dump (n_modes ≫ 3 N_at) is `undetermined`.
- `likely_mesh_artefact` — known-stable rock-salt binaries (NbN, TiN, ZrN, HfN) with imaginary modes on a screening / coarse mesh; **or** imaginary weight confined to the lowest three branches when a single-q list is detectable.
- `genuinely_soft` — imaginary modes present and not classified above. **Default when the frequency list is missing** (conservative).
- `unknown` — no `PhononResult` at all.

Labels such as `likely_mesh_artefact` are **suspects, not proof**. The
report **must** state that coarse q is a gate only.

**Campaign-level summary (must):** counts by class; `campaign_signal`
(`has_stable_survivors` \| `none_stable` \| `none_stable_known_binaries_also_soft` \| `no_phonon_results` \| `skipped`); list of known-stable binaries that are also soft; `next_actions` (report path + `siscforge pilot` command). Written as `soft_mode_report.json` plus a short Markdown section (`soft_mode_report.md`) in the campaign store.

`siscforge soft-modes <store>` must be able to (re)write the report from
an existing store without re-running QE.

#### Denser-q pilot helper

`siscforge pilot <store> -o <yaml>` **must** emit a loadable
`CampaignConfig` / YAML from an existing phonon-map store.

| Contract | Rule |
|----------|------|
| Selection | `binaries` (binary nitrides only, least-soft first) \| `least_soft` (highest `min_frequency_cm1`, N cells) \| `ids` (user-specified). Small set; default N ≤ 4 |
| `candidate_specs` | Exact reuse of store cells (formula × strain × CIF). **Must not** re-enumerate the full composition × strain grid |
| `qpoints` | Denser than the source map; default **3³**, configurable |
| `do_epw` | **false** (and `epw.enabled: false`). **Must not** auto-launch EPW on soft cells |
| Copied knobs | `pseudo_dir`, `nproc`, phonon retry flags from `campaign_resolved.yaml` when present |
| `output_dir` | **New** directory; **must** differ from the source store (resume-safe) |
| Provenance | `extras.pilot` records source store, mode, qpoints, selected ids, and the limitation string |

The pilot **must not** claim that denser q equals production
dynamical-stability proof. The human still chooses expand vs abandon.

#### Empty `stable_only` behaviour

When `shortlist --mode stable_only` finds zero survivors the CLI **must**:

1. Exit non-zero and **write no EPW YAML**.
2. Refuse any fallback to unstable top-k (existing Slice 23 contract).
3. Surface the soft-mode report (write it if missing) and print the
   `siscforge pilot` command as the next action.

`stable_or_soft` remains available for numeric-noise floors only; it is
**not** a silent path to EPW of hard-imaginary cells.

#### AL (this slice)

Soft-mode class and pilot provenance **may later** feed AL acquisition
(§2.7). **This slice only requires** the report, the pilot scaffolding,
and the empty-shortlist messaging. Do not change default acquisition
scores.

---### 2.4 Unconventional (DFT+U / DMFT) Pathway — Phase 3 software path (P3.1–P3.6)
Produces `leading_pairing_eigenvalue` that feeds the common `performance_score` via documented mapping (see `docs/phase3-p34-pairing-score.md`). **Default off.**

**Implemented contracts:**
- `DFTUResult` + optional DFT+U (`qe-dftu` / `do_dftu`) — P3.1
- `WannierResult` + quality metrics + `ready_for_dmft` gate (`qe-wannier`) — P3.2 + P3.2.1 automated nscf+pw2wannier90
- `DMFTResult` scaffold + controlled launcher (model + gate + mock + drop-in parser + toml/run package + optional invoke; production U/J/β residual) — P3.3
- Pairing → `performance_score` with `ranking.performance_precedence` (default `epw_then_dmft`) — P3.4
- Oxygen-vacancy / infinite-layer enumeration (opt-in via `material_families: [nickelate]`) — P3.5
- Mixed conventional/unconventional AL acquisition (`active_learning.pool_mode`: `off` \| `joint` \| `separate`) — P3.6

Mock / illustrative DMFT numbers participate in ranking only when enabled and tagged `dmft_pairing_mock`. Real CTHYB launch remains residual.

### 2.5 Silicon Integration & Interface Module — Phase 2 complete
Produces Si-Feasibility Score 0–100 plus process recommendations.

**Implemented (P2.1–P2.5):**
- Full component breakdown (`SiFeasibilityComponents`) with YAML weights + export provenance (P2.1)
- Multi-layer buffer stacks + chemical/thermal window flags (P2.2)
- Critical thickness (Matthews–Blakeslee / People–Bean) + membrane-transfer heuristics (P2.3)
- Multi-objective ranking + Pareto + ranking provenance (P2.4; ranking lives in §2.6)
- Process-recommendation synthesis cards + frozen schema `1.0` (`process_recommendations.json`) (P2.5)

**Deferred:** interface-slab DFT, FEM membrane mechanics, CALPHAD interlayer thermodynamics.

### 2.6 Candidate Ranking & Reporting

- Consumes Eliashberg Tc or pairing eigenvalue via `performance_score`, always includes Silicon Feasibility breakdown.
- **Missing defaults (#46):** `normalize_performance(None)` and missing
  `si_feasibility` use pessimistic defaults (15, YAML
  `ranking.missing_performance_default` / `missing_si_feasibility_default`).
  Incomplete rows cannot outrank complete ones via a mid-scale placeholder.
  Optional `ranking.performance_ceiling_by_source` and
  `dft.dmft.scoring.kelvin_per_unit_by_family` / `score_ceiling_K_by_family`
  are opt-in; conventional-only campaigns keep the 40 K ceiling.
- **Result-quality / trust layer (must):**
  - Flags such as imaginary modes, high λ, screening Wannier, EPW failed, etc.
  - Ranking penalties so pathological screening EPW does not dominate.
  - **Hard-zero (#44):** `wannier_random_proj` or `coarse_grids` **and**
    `high_lambda` / `extreme_lambda` force the performance contribution to 0
    (flag `screening_high_lambda`). Soft multiplicative penalties remain for
    milder cases. Same rule refuses conventional-pool promotion.
  - Export/docs language: do not cite Tc until trust flags improve.
- **Phonon-aware ranking:**
  - `rank --stable-first` prefers dynamically stable phonons.
  - Setup failures are not “stable.”
  - Soft-mode class is **diagnostic / provenance**, not a ranking override in this slice. Pathological imag-mode EPW remains blocked by the trust layer.
- Surrogate provenance (model version, training-set size, acquisition weights, bootstrap flag) appears in status and synthesis cards.
- JosephsonMetrics optional secondary ranking when module enabled (Phase 4).

### 2.7 Workflow Engine, CLI & Active Learning

**CLI (must for desktop path)**  
| Command / option | Role |
|------------------|------|
| `siscforge run` | Campaign; `--dry-run`, `--calculator qe\|qe-epw`, `-o` output_dir |
| `run.resume` / skip finished | Multi-candidate checkpoint |
| `resume_qe_steps` | Mid-step workdir reuse |
| Heartbeats | Long QE progress |
| `siscforge shortlist` | From store → focused EPW YAML; `--mode stable_only` \| `stable_or_soft` \| performance modes. Empty `stable_only` **must** print the soft-mode report path and the `siscforge pilot` command; **must not** fall back to unstable top-k |
| `siscforge soft-modes` | (Re)write `soft_mode_report.json` + `.md` from a phonon-containing store; skip with a clear reason if no phonon data |
| `siscforge pilot` | From a phonon-map store → denser-q phonon-only YAML (`do_epw: false`); `--mode binaries` \| `least_soft` \| `ids`; `--qpoints` default 3,3,3; new `output_dir`; `candidate_specs` reuse |
| `siscforge refine` | Denser EPW from store winners; separate `output_dir`; `quality_tag` production / workstation_dense grids |
| `siscforge rank` | Table export; `--stable-first` |

**Active learning (bootstrap + P3.6 mixed pools)**  
- Minimal prioritization (top-k expensive path) already shipped.
- Lightweight retrain / update trigger after shortlist cycles (or explicit CLI).
- Explicit promotion of clean results into the training set.
- Training-set audit command.
- Failure modes (retrain NaNs, over-confidence, empty shortlist, mode collapse, mock-data refusal) must be classified and reported with the same honesty as phonon vs EPW failures.
- Empty phonon-map `stable_only` is an **operator-recovery** failure mode (report + pilot), not an AL acquisition change. Soft-mode class / pilot provenance **may later** feed AL; this slice does not require it.
- Full prioritize → shortlist → (mock) calculate → promote → retrain cycle must be exercisable in dry-run / mock mode.
- **P3.6:** `active_learning.pool_mode` (`off` default / `joint` / `separate`) with documented pool derivation and optional per-pool quotas. Conventional campaigns with `off` must not change acquisition scores or order. See `docs/phase3-p36-mixed-al.md`.

Human overrides (pin candidates, exclude subspaces, roll back model version, export training set) are first-class operations.

### 2.8 Josephson Junction Device Modeling Module  ← Phase 4

**P4.1 shipped** (Tier-1 analytics). Disabled by default. When
`josephson.enabled` is true, attach approximate Ambegaokar–Baratoff /
BCS-from-Tc metrics to the top-N ranked evaluations.

- Gap Δ in **meV**: `ElectronPhononResult.gap_meV` or `alpha2F_summary` /
  `raw` keys, else Δ = 1.764 k_B Tc from conventional Eliashberg /
  Allen–Dynes Tc. DMFT `performance_score` is **not** a gap.
- IcRn in **mV**: `(π/2) Δ tanh(Δ / 2 k_B T)` (T = 0 when
  `temperature_K` is null).
- Jc proxy in **A/cm²** under documented `rna_ohm_um2` (default 20 Ω·μm²).
- EJ-style switching energy at `reference_area_um2` (default 1 μm²).
- Always labelled **approximate / ranking only**. `approximate: true` is
  forced.
- No ranker fork. Optional `secondary_ranking: none | icrn | jc` reorders
  only the Josephson-annotated shortlist for presentation (`rank` /
  `composite_score` unchanged).
- Fabrication-compatibility heuristics (P4.2) attach SIS / SNS /
  ramp-edge labels and BEOL / thermal flags by reusing Si-feasibility
  signals. Usadel and BdG remain later.

See `docs/phase4-p41-josephson-tier1.md`,
`docs/phase4-p42-fabrication.md`, and `docs/phase4-exit.md`.


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
| `surrogate_prediction` | Optional; λ / ω_log / Tc proxy + uncertainty + model version |
| `josephson` | Optional; Phase 4 |

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

Optional nested object on `CandidateEvaluation` (default `None`). P4.1
fields: `approximate` (always true), `status`, `method`, `model_tier`,
`quality_tag`, `gap_meV`, `gap_source`, `tc_used_K`, `tc_source`,
`icrn_mV`, `jc_A_per_cm2`, `switching_energy_eV`, `ej_K`, `ic_uA`,
`reference_area_um2`, `rna_ohm_um2`, `temperature_K`, `formula_tags`,
`notes`, `assumptions`, `raw`, `provenance`. P4.2 adds nested
`fabrication` (`JosephsonFabricationHints`) plus optional
`secondary_order` / `secondary_ranking` when the presentation sort
runs. Ranking-only; never a device-design value. Not process
qualification.


### 3.6 Active-learning objects (notes for implementers)
- `SurrogatePrediction` — optional block on CandidateEvaluation: λ / ω_log / Tc proxy values, uncertainty, and the model version that produced them.
- `TrainingExample` — links a CandidateEvaluation (or literature record) that has been explicitly promoted, with quality snapshot.
- `SurrogateModelMetadata` — version, training-set hash/size, timestamp, bootstrap flag, calibration summary.
- `PrioritizationRecord` — model version, acquisition weights, shortlist, timestamp; attached to ranking/export.

Exact field lists may evolve; the contracts above (immutable snapshots, explicit promotion, provenance on every decision) are mandatory.

---

## 4. External Dependencies & Interfaces

Primary open-source stack:
- Quantum ESPRESSO ≥ 7.2 + EPW, Wannier90
- pymatgen, ASE, spglib, phonopy (optional FD)
- jobflow (optional orchestration)
- Pydantic v2, Typer, Rich, PyYAML, NumPy
- SSSP (or PseudoDojo) UPFs
- Docker recommended for second-machine parity

TRIQS / solid_dmft: Phase 3 controlled launcher shipped (production U/J/β residual). Usadel/BdG: Phase 4 behind Calculator protocol.

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

### 5.3 Shortlist / refine / pilot (CLI-driven YAML generation)
- `siscforge shortlist --mode stable_only|stable_or_soft|… -n N -o campaign.yaml`
- `siscforge refine` denser grids / `workstation_dense` tier; **separate `output_dir`** from screening store.
- `siscforge pilot --mode binaries|least_soft|ids --qpoints 3,3,3 -o pilot.yaml` — denser-q **phonon-only** recovery from a map store; `do_epw` stays false; `candidate_specs` reuse; **separate `output_dir`**.

### 5.4 Josephson (Phase 4; ignored until enabled)

```yaml
josephson:
  enabled: false
  shortlist_only: true
  shortlist_size: 20
  model_tier: "analytic_AB"
  reference_area_um2: 1.0
  rna_ohm_um2: 20.0
  assume_SIS: true
  temperature_K: null
  bcs_gap_ratio: 1.764
  family_gap_ratios: {}
  fabrication_hints: true    # P4.2; only runs when enabled
  beol_temp_ceiling_c: 400
  secondary_ranking: none    # none | icrn | jc — presentation only
```

---

## 6. Output Formats & Database Schema

- CandidateEvaluation JSON, CSV summary, Markdown synthesis cards, optional CIF/POSCAR.
- Quality flags and primary failure reasons appear in notes/errors.
- Soft-mode report (`soft_mode_report.json` / `soft_mode_report.md`) appears in the store after phonon-containing runs, or an explicit skip reason is written.
- Surrogate provenance appears in status and cards.
- Josephson section only when module enabled (labeled approximate).
- File-based store is first-class on workstation; MongoDB optional.

---

## 7. Performance & Scaling Targets

- Phases 0–1: productive on 8–32 cores; multi-day DFPT acceptable for refine shortlists of few candidates.
- Phonon maps (q=2³): many binary nitrides ~1–3 min/candidate on 16 cores (order-of-magnitude; hardware-dependent).
- Analytic Josephson Tier-1: ≪ 1 min for 50 candidates (Phase 4).
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
- Empty `stable_only` message names the soft-mode report and `siscforge pilot`; no EPW YAML is written.
- `siscforge pilot` YAML loads as `CampaignConfig`, reuses `candidate_specs`, has `do_epw: false`, and does not re-enumerate the source grid.
- Trust layer penalties for high-λ + imag modes (existing tests).
- Mock dry-run campaigns green; Docker verify script for image builds.
- Full mock AL cycle (prioritize → shortlist → promote → retrain) is green.
- Promotion of mock labels into a real training set is refused.

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
| AC13 | Promotion of a result into the training set is an explicit step; mock / disallowed quality tags are refused | Yes |
| AC14 | Every shortlist / ranking records surrogate model version (or heuristic), training-set size, and acquisition weights | Yes |
| AC15 | Bootstrap / low-data regime is visible in CLI status and synthesis cards | Yes |
| AC16 | Full prioritize → shortlist → mock-calculate → promote → retrain cycle succeeds in dry-run / mock mode | Yes |
| AC17 | Retrain that produces NaNs or absurd metrics keeps the previous model and surfaces diagnostics | Yes |
| AC18 | Attempt to train on mock data is hard-refused | Yes |
| AC19 | A finished phonon-only (or phonon-containing) store produces `soft_mode_report.json` + Markdown, **or** an explicit skip record with a clear reason | Yes |
| AC20 | `siscforge pilot` emits a loadable campaign YAML that reuses `candidate_specs` and does **not** re-enumerate the full source grid | Yes |
| AC21 | `shortlist --mode stable_only` with zero survivors prints actionable next-step text (soft-mode report + `siscforge pilot`) and writes **no** EPW YAML | Yes |
| AC22 | Pilot campaigns have `do_epw: false` (and `epw.enabled: false`) by default; no automatic EPW of soft cells | Yes |

### Phase 3 contracts (P3.1–P3.6)

These sit beside AC1–AC18 rather than rewriting them. Verify against
`docs/phase3-p3*.md` and the `tests/test_*_p3*.py` suite.

| WP | Must | Residual |
|----|------|----------|
| P3.1 | DFT+U path produces typed `DFTUResult`; conventional campaigns unchanged when `do_dftu` is off | — |
| P3.2 | Wannier prep + quality metrics + `ready_for_dmft` + P3.2.1 nscf/`pw2wannier90` | Spinor manifolds; production projection libraries; real-QE golden optional |
| P3.3 | `DMFTResult` scaffold + controlled launcher (model + gate + mock + drop-in parser + toml/run package + optional invoke); unconventional steps default off | Production U/J/β calibration; solid_dmft version matrix |
| P3.4 | Pairing eigenvalue → `performance_score` with documented `epw_then_dmft` precedence; mock scores tagged | — |
| P3.5 | Opt-in nickelate O-vacancy / infinite-layer enum; nitride path inert when family off | — |
| P3.6 | Mixed conventional/unconventional AL pools (`off` / `joint` / `separate`); default `off` | — |

**Not claimed:** production CTHYB calibration / NdNiO₂ golden, production ALIGNN/MatGL λ/Tc heads.

---

## 10. Explicit Limitations

- Auto-raised nkc and `search_shells` **do not** guarantee physical λ/Tc; screening Wannier remains `proj=random`.
- Material-specific Wannier projections, anisotropic Eliashberg, SCDFT, production CTHYB calibration, Josephson Usadel/BdG: **later**. Tier-1 Josephson analytics (P4.1) and fabrication heuristics (P4.2) are shipped (inert unless enabled). See `docs/phase4-exit.md`.
- After Phase A+B exhaustion, further EPW success may require human projections or different cells; DFPT remains valuable for stability gating.
- Screening q=2³ phonon stability can false-positive/false-negative; denser DFPT required before citing dynamical stability.
- Soft-mode classification is **heuristic**. `likely_mesh_artefact` does not certify that a cell is physically stable. A denser-q pilot is still a gate, not production dynamical-stability proof. The human still chooses whether to expand or abandon.
- Resume covers common cases; exotic partial files or external manual edits may still need operator intervention (documented in implementation-notes).
- Room-temperature superconductor discovery is **not** promised.
- Early surrogates are prioritization aids, not quantitative predictors; bootstrap messaging exists precisely for this reason.

---

## 11. Development Phases & Milestones

**Phase 0 (v0.1 foundation)** — Structure gen, formation filter, QE phonon, Si-score, ranking, store, CLI, dry-run.  
**Exit**: NbN phonon; small nitride campaign on workstation.

**Phase 1 (conventional EPW + desktop operability)** — EPW + isotropic Tc, quality tags, shortlist/refine, trust layer, resume/checkpoint, EPW parallel + coarse-k + Phase B, phonon-first + stable_only, phonon diagnose/retry, Docker. **Additive (v0.5.4):** soft-mode report + denser-q pilot after none-stable coarse maps.  
**Exit**: golden NbN/MgB₂ path (mock always; real optional); desktop remediation ACs green; see `docs/phase1-exit.md`.

**Phase 1 residual / 1.5 (AL bootstrap)** — Seed-set management, explicit promotion, first trained surrogate, acquisition provenance, bootstrap observability, full mock cycle.  
**Exit**: AC13–AC18 green; one complete interleaved cycle demonstrated on workstation.

**Phase 2** — Silicon Integration maturity + ranking polish (P2.1–P2.5) — **COMPLETE** (see `docs/phase2-exit.md`).

**Phase 3** — Unconventional pathway: P3.1–P3.6 software path shipped (DMFT scaffold + controlled launcher); production CTHYB calibration + production GNN residual.

**Phase 4** — **Tier-1 complete** (P4.1–P4.2): analytic Josephson estimates + fabrication heuristics on shortlist (inert default). Later Usadel/BdG. See `docs/phase4-exit.md`.

---

*This document (v0.5.4) is implementation-ready. Workstation production-path contracts above match shipped behavior in `docs/implementation-notes.md` (Slices 13–28 + Slice 29 phonon-map recovery + P3.1–P3.6 + P4.1–P4.2). Active-learning bootstrap and mixed-pool contracts are specified here and detailed in `docs/design/active-learning-flywheel.md` and `docs/phase3-p36-mixed-al.md`. Josephson is inert unless `josephson.enabled`. PRD v0.4.4 is the product authority; this file is the engineering contract.*
