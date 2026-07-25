# Implementation Notes

## Slice 13 (2026-07-25) — Resume / checkpoint for multi-candidate runs

**Scope**: Desktop-friendly re-launch after interrupt for EPW shortlists (and mock).
Does **not** generate shortlist YAMLs (next session).

| Item | Location |
|------|----------|
| Success + fingerprint | `siscforge.resume` |
| Store append / lookup | `EvaluationStore.append_evaluation`, `.find_successful` |
| Run knobs | `CampaignConfig.run` → `RunConfig` |
| CLI | `siscforge run --force-rerun` / `--fail-fast` |
| Tests | `tests/test_resume.py` |

### Success criteria (skip-finished)
An evaluation is **successful** (skippable on resume) when:

- `status` ∈ {`ok`, `mock`}, **and**
- `electron_phonon` has status ok/mock with λ or Tc, **or** phonon ok/mock, **or** scf ok/mock.

`failed`, `pending`, and `surrogate_only` are **not** skipped as expensive successes.

### Matching policy
1. **candidate_id** exact match
2. Else **fingerprint** `material_family|formula|substrate|±strain` (6 dp)

Re-enumeration regenerates UUIDs → fingerprint is the usual hit when reusing the
same `output_dir` / campaign shape.

### Defaults
```yaml
run:
  resume: true
  continue_on_error: true
  force_rerun: false
```

- After each expensive candidate: flush `evaluations.json`
- Progress: `[i/N] Formula strain=… — skip|running|ok|failed`
- End: `Checkpoint summary: skipped=, ran=, ok=, failed=`
- `QENotAvailableError` (missing binaries) still aborts the whole campaign (exit 3)

### How to resume after reboot / kill
```bash
# same campaign YAML + same output_dir
siscforge run --calculator qe-epw examples/nbti_n_al_broad.yaml
# finished candidates skipped; failed ones re-attempted (not successful)
# force everything:
siscforge run --calculator qe-epw --force-rerun examples/nbti_n_al_broad.yaml
```

---

## Slice 12 (2026-07-25) — Broader nitride AL campaign

**Scope**: Workstation-scale Nb–Ti–N (+ Zr/Hf) example that closes the Phase 1
loop with Phase 2 Si-scoring.

| Item | Location |
|------|----------|
| Campaign | `examples/nbti_n_al_broad.yaml` |
| Walkthrough | `docs/examples/nbti_n_al_broad.md` |
| Tests | `tests/test_active_learning.py::test_example_al_broad_yaml_loads_and_enumerates` |

Grid: 4 binaries (Nb/Ti/Zr/Hf) + 3 Nb–Ti ternaries × 7 strains → ~49 candidates
(capped at 60). AL `max_epw_jobs: 6`. Epitaxy `auto` + buffers for Si v0.2.

Dry-run shows acquisition table, top-k selection, surrogate_only deferred rows.
Real EPW: restrict shortlist + `--calculator qe-epw` (see walkthrough).

---

## Slice 11 (2026-07-25) — Light EPW practical hardening

**Scope**: Thin pass only — defaults comments, failure diagnostics, quality_tag
clarity, denser-grid docs. **Not** production Wannier automation or anisotropic Eliashberg.

| Item | Location |
|------|----------|
| Grid guidance | `epw_inputs.recommended_grids(family, tier)` |
| EPW input header | `build_epw_input` comments: quality_tag + nkf/nqc |
| Failure diagnostics | `epw_recipes.diagnose_epw_failure` on pp/nscf/epw fail |
| Docs | `docs/examples/nbN_epw.md`, `mgb2_epw.md` grid ladders |

### quality_tag
- `dft.quality_tag: screening | production` is **propagated** to SCF / Phonon /
  ElectronPhononResult and exports.
- It does **not** auto-change grids — raise `qpoints` / `epw.nqc` / `nkf` / `nqf`
  in YAML and set `quality_tag: production` when intentionally denser.

### Explicitly still deferred
- Automated Wannier projection discovery
- Anisotropic / multi-band Eliashberg
- Full soft-mode remediation pipeline

---

## Slice 10 (2026-07-25) — Phase 2 kickoff: 45° epitaxy + buffer library

**Scope**: Reduce cube-on-cube Si-mismatch pessimism for rocksalt nitrides.

| Item | Location |
|------|----------|
| 45° mismatch | `structure.strain.lattice_mismatch_percent(..., match="45deg")` |
| Buffer library | `silicon.buffers` — TiN, AlN, ZrN, MgO, direct_Si |
| Scorer | `silicon.feasibility` **v0.2** — auto picks best of cube / 45° / buffer |
| Enum flags | `enumeration.epitaxy_orientation`, `use_buffers` |
| Example | `examples/nbn_si_45deg.yaml` |

Notes on the score always state when 45° or a buffer was assumed. Remaining Phase 2:
multi-layer stacks, membrane mechanics, critical thickness, interface slabs, Pareto ranking.

---

## Phase 1 complete (frozen baseline)

**Version**: `0.1.0` · **Tag**: `v0.1.0-phase1` · **Exit checklist**: [phase1-exit.md](phase1-exit.md)  
**Validation**: [validation-phase1.md](validation-phase1.md)

Delivered: EPW + isotropic Eliashberg, NbN/MgB₂ goldens, λ/Tc surrogate stub, AL prioritization.
Deferred: production Wannier, anisotropic Eliashberg, trained GNN, AL retrain, Phase 2 Si maturity.

---

## Slice 9 (2026-07-25) — Minimal active-learning prioritization

**Scope**: Queue prioritization for expensive EPW jobs. **Not** a full retrain loop, Bayesian optimization, or batch BO.

### Module
| Item | Location |
|------|----------|
| Acquisition | `siscforge.active_learning.acquisition` |
| Config | `CampaignConfig.active_learning` |
| Evaluation fields | `acquisition_score`, `al_selected_for_expensive` |
| Example | `examples/nbti_n_al.yaml` |
| Tests | `tests/test_active_learning.py` |
| Store artifact | `active_learning.json` |

### Acquisition (`uncertainty_si_tc`)
```
score = w_u·unc + w_tc·(Tĉ/Tc_max) + w_si·(Si/100) − w_hull·(E_hull/0.25)
```
Default weights: uncertainty 0.4, predicted_tc 0.3, si_feasibility 0.3, hull_penalty 0.1.

### Campaign YAML
```yaml
active_learning:
  enabled: true                 # default false
  strategy: uncertainty_si_tc
  max_epw_jobs: 5
  evaluate_deferred_with_surrogate: true
  weights:
    uncertainty: 0.4
    predicted_tc: 0.3
    si_feasibility: 0.3
    hull_penalty: 0.1
```

### Run-path behavior
1. Enumerate → formation filter → λ/Tc surrogate
2. Score Si-feasibility (cheap) for all remaining
3. If AL enabled: compute acquisition, print ranking, select top-k for calculator
4. Deferred candidates → `status=surrogate_only` evaluations (optional)
5. Final ranking uses real/mock EPW Tc when present (overrides surrogate)

### Dry-run
```bash
siscforge run --dry-run examples/nbti_n_al.yaml
```
Mock calculator stands in for the expensive path on the selected top-k; deferred rows are labeled `surrogate_only*`.

### Explicitly out of scope (this cut)
- Surrogate retraining on new EPW labels
- Batch / diversity-aware selection
- Bayesian optimization / Gaussian processes
- Avoidance of known-failure regions from past CRASH logs

### Future full AL loop would add
- Retrain λ/Tc surrogate on accumulated EPW results
- Uncertainty calibration from ensemble models
- Multi-fidelity / cost-aware acquisition
- Persistent job queue across campaign restarts

### Next session (Phase 1 largely complete)
Pick one: **(a)** production Wannier/EPW hardening, **(b)** broader nitride alloy campaigns driven by AL, or **(c)** start Phase 2 Si-integration maturity.

---

## Slice 8 (2026-07-25) — λ/Tc surrogate stub (pre-filter)

**Scope**: Lightweight family-heuristic λ / ω_log / Tc predictions for **pre-filtering** before expensive EPW. Not a trained production GNN. No active learning.

### Module
| Item | Location |
|------|----------|
| Surrogate | `siscforge.surrogates.tc_lambda` |
| Config | `CampaignConfig.surrogate.tc_lambda` (`TcLambdaSurrogateConfig`) |
| Evaluation field | `CandidateEvaluation.tc_lambda_surrogate` + `performance_score_source` |
| Example | `examples/nbti_n_surrogate.yaml` |
| Tests | `tests/test_tc_lambda_surrogate.py` |

### API
```python
from siscforge.surrogates import predict_tc_lambda, TcLambdaSurrogate
pred = predict_tc_lambda(candidate)  # → λ, ω_log, Tc, uncertainty, model_version
```

### Campaign YAML
```yaml
surrogate:
  tc_lambda:
    enabled: true              # default false
    min_predicted_tc_K: 5.0    # optional cut
    max_uncertainty: 0.65      # optional cut (0–1)
    keep_top_n: 10             # optional shortlist
    use_for_ranking_when_no_epw: true
```

### Precedence
1. Real EPW / mock `ElectronPhononResult` → `performance_score` (source `epw` / `mock`)
2. Else, if `use_for_ranking_when_no_epw`, surrogate score (source `surrogate`, labeled in notes + CSV)

### Export columns (distinct from EPW)
`surrogate_lambda`, `surrogate_omega_log_K`, `surrogate_Tc`, `surrogate_uncertainty`, `surrogate_model_version`, `performance_score_source`

### Limitations (stub)
- Family + formula anchors only (NbN, MgB₂, TiN, …); not structure-graph trained
- Uncertainty is heuristic, not calibrated ensemble variance
- Does not replace EPW for scientific claims
- Future: ALIGNN/MatGL multi-task head on the same `TcLambdaPrediction` schema

### Next session (best single focus)
See **Slice 9** (minimal AL prioritization) — implemented.

---

## Slice 7 (2026-07-25) — MgB₂ golden EPW path

**Scope**: Complete the MgB₂ golden conventional pathway (same Calculator/CLI patterns as NbN). Still isotropic-only; no trained λ/Tc surrogate, no active learning, no anisotropic Eliashberg.

### Deliverables
| Item | Location |
|------|----------|
| Structure | `siscforge.structure.mgb2` — 3-atom hexagonal AlB₂-type (P6/mmm) |
| Campaign | `examples/mgb2_epw.yaml` (skeleton YAML kept as compatibility alias) |
| Docs | `docs/examples/mgb2_epw.md` |
| References | `epw_references.MGB2_*` ranges + fixture moments |
| Fixture | `tests/fixtures/qe/epw_mgb2_snippet.out` |
| Tests | mock MgB₂ e-ph, fixture parse, optional `SISCFORGE_RUN_EPW=1` real gate |

### Two-gap → isotropic
MgB₂ is multi-band / two-gap. Screening EPW reports **isotropic** λ, ω_log, and Allen–Dynes / isotropic Eliashberg Tc. Metadata notes this on mock and real EPW results (`alpha2F_summary.material_notes`, `tc_model=isotropic_average`).

### CLI
```bash
siscforge run --dry-run examples/mgb2_epw.yaml
siscforge run --calculator qe-epw examples/mgb2_epw.yaml   # needs epw.x + Mg/B UPF
```

### Remaining Phase 1 gaps (explicit)
- Production Wannier automation (projections, windows, exclude_bands) beyond screening template
- Anisotropic / multi-band Eliashberg (MgB₂ σ–π)
- Active-learning loop (uncertainty / UCB retrain) — see Slice 8
- Broader boride enumeration (beyond bulk MgB₂ prototype)
- Production-trained λ/Tc GNN replacing the Slice 8 stub

### Next session (best single focus)
**Minimal active-learning coordinator** (surrogate uncertainty + Si-feasibility → EPW priority queue).

---

## Slice 6 (2026-07-24) — Phase 1 EPW + isotropic Tc

**Scope**: Conventional superconductivity pathway (EPW + Allen–Dynes / isotropic Eliashberg). No anisotropic Eliashberg, DMFT, AL, or trained GNN.

### Data model
- `ElectronPhononResult` — λ, ω_log (K), μ*, Tc_allen_dynes, Tc_eliashberg, converged, wannier_ok, quality_tag
- `CandidateEvaluation.electron_phonon` + `performance_score` = best Tc (K)

### Modules (`siscforge.calculators.qe`)
| Module | Role |
|--------|------|
| `eliashberg.py` | Allen–Dynes + strong-coupling closed-form proxy |
| `epw_inputs.py` | Screening `epw.in` template |
| `epw_parser.py` | Parse EPW stdout → `ElectronPhononResult` |
| `epw_recipes.py` | `run_relax_scf_phonon_epw` on top of phonon flow |
| `epw_references.py` | NbN / MgB₂ order-of-magnitude gates |

### Calculators
- **`qe`** — phonon path; set `dft.do_epw: true` to append EPW
- **`qe-epw`** / **`epw`** — always enables EPW (requires `epw.x`)
- **`mock`** — fills mock `ElectronPhononResult` + Tc-based `performance_score` (dry-run unchanged)

### Config
```yaml
dft:
  do_epw: true
  epw:
    enabled: true
    nkf: [6, 6, 6]   # screening fine k
    nqf: [6, 6, 6]
    mu_star: 0.10
    eliashberg: true
```

### CLI
```bash
siscforge run --dry-run examples/nbn_epw.yaml
siscforge run --calculator qe-epw examples/nbn_epw.yaml   # needs epw.x
```

### Golden systems
- NbN: fixtures + mock-safe tests; optional `SISCFORGE_RUN_EPW=1`
- MgB₂: see **Slice 7** (`examples/mgb2_epw.yaml`)

### Limitations
- Isotropic only (no anisotropic Eliashberg / SCDFT)
- EPW input is a screening template; production Wannier projections need hand-tuning
- NSCF + full Wannier prep not fully automated
- Real EPW optional for CI (same pattern as real QE)

---

## Slice 5 (2026-07-24) — QE hardening

**After** the Phase 0 foundation commit.

### Hardening changes
| Area | Change |
|------|--------|
| Relaxed geometry | Parse final `CELL_PARAMETERS` + `ATOMIC_POSITIONS` from pw.x output; feed into SCF/phonon; store `relaxed_structure_cif` on the candidate |
| Pseudos | `pseudos.py` — SSSP-friendly auto-match, explicit map validation, clear `PseudoResolutionError` messages |
| Phonopy FD (optional) | `phonopy_fd.py` when `dft.phonon_method: phonopy_fd` (requires `phonopy`); default remains `dfpt` / `gamma` via `ph.x` |
| Diagnostics | Richer failure messages (workdir + output tails); `quality_tag` propagated to SCF/Phonon/candidate |
| Tests | `tests/test_qe_hardening.py` + vc-relax fixture |

### Current limitations (post-hardening)
- Phonopy FD is screening-quality (force parse from stdout, coarse mesh).
- No automatic SSSP download — user must point `pseudo_dir` at local UPFs.

---

## Slice 4 (2026-07-24) — Formation filter, store, export polish

- `siscforge.surrogates.formation` — heuristic E_hull pre-filter
- `EvaluationStore` — JSON campaign directory
- CSV/Markdown synthesis cards; Phase 0 exit checklist in `docs/phase0-exit.md`

---

## Slice 3 (2026-07-24) — jobflow QE recipes + QECalculator + NbN golden test

**Scope**: Quantum ESPRESSO relax → SCF → DFPT phonon behind the Calculator protocol. **No EPW**, Eliashberg, ML surrogates, or active learning.

### What was implemented

#### `siscforge.calculators.qe`
| Module | Role |
|--------|------|
| `env.py` | Detect `pw.x` / `ph.x` / MPI; `require_qe()` raises clear `QENotAvailableError` |
| `inputs.py` | `StructureCandidate` → pymatgen Structure; `PWInput` + `ph.x` deck builders; pseudo resolve |
| `parser.py` | pw.x / ph.x text → `SCFResult` / `PhononResult`; frequency summary + imag-mode flag |
| `recipes.py` | Local sequential `run_relax_scf_phonon`; optional jobflow `Flow` via `build_relax_scf_phonon_flow` |
| `calculator.py` | `QECalculator` (`name="qe"`) implementing the Calculator protocol |
| `references.py` | NbN golden reference ranges and notes |

#### Registry
- Registered names: **`mock`**, **`qe`**, **`quantum-espresso`** (alias).
- QE registration never breaks the mock path (import is best-effort).

#### Campaign config
- New `CampaignConfig.dft` (`DFTConfig`): cutoffs, k/q grids, `pseudo_dir`, `do_relax`, `do_phonon`, `phonon_method` (`dfpt` \| `gamma`), `nproc`, etc.

#### CLI
```bash
# Always mock (unchanged)
siscforge run --dry-run examples/nbti_n_strain.yaml

# Explicit mock
siscforge run --calculator mock examples/nbti_n_strain.yaml

# Real QE (fails clearly if pw.x missing — no silent fallback)
siscforge run --calculator qe examples/nbn_phonon_qe.yaml
```
`--dry-run` **always** forces mock, ignoring campaign calculator / `dft.engine`.

#### Golden NbN test
- `tests/test_nbn_phonon.py` — mock + fixture paths always pass.
- Optional real QE: `SISCFORGE_RUN_QE=1` + `SISCFORGE_PSEUDO_DIR=...`.
- Fixtures under `tests/fixtures/qe/`.
- Walkthrough: `docs/examples/nbN_phonon_qe.md`.
- Example campaign: `examples/nbn_phonon_qe.yaml`.

### How to switch between mock and QE

| Mode | How |
|------|-----|
| Dry-run / CI default | `siscforge run --dry-run <yaml>` → `MockCalculator` |
| Explicit mock | `--calculator mock` or `calculators: [{name: mock}]` |
| Real QE | `--calculator qe` **or** `calculators: [{name: qe}]` + `dft.engine: qe` + `dft.pseudo_dir` set |
| Env for tools | `QE_BIN` / `QUANTUM_ESPRESSO_BIN`; optional `SISCFORGE_PSEUDO_DIR` for pytest |

Install optional jobflow (not required for sequential runs):

```bash
pip install -e ".[qe]"
```

### Current limitations

- **No EPW / Wannierization / Eliashberg** — phonon only (DFPT `ph.x` or Gamma-only).
- **No phonopy finite-displacement path yet** — DFPT-focused; phonopy YAML can be *parsed* if provided as text.
- **jobflow** is optional; workstation path runs steps with `subprocess` in order (no Mongo job store).
- **Relaxed geometry re-read** is a stub — after `vc-relax`, SCF uses the input structure unless you re-feed a relaxed CIF later.
- **Pseudopotentials** must be supplied by the user (`dft.pseudo_dir` / map); none are vendored.
- **Performance score** is not filled by QE yet (ranking uses Si-score + neutral performance fallback).
- Real QE is **not** required for `pytest` to pass.

### Next recommended session

Roadmap Phase 0 remaining items:

1. **Formation-energy GNN surrogate** (ALIGNN / MatGL) + uncertainty pre-filter.
2. **File/Mongo store** for evaluations + basic ranking by E_hull + Si-score (ranking already exists; harden persistence).
3. **CIF/POSCAR export** of ranked shortlists + synthesis-card skeleton.
4. Harden QE: parse relaxed structure from `pw` XML, SSSP pseudo presets, phonopy FD backend, NbN production k/q convergence.

Or jump to Phase 1 EPW once phonon golden NbN is validated on a workstation with real QE.

---

## Slice 2 (2026-07-24) — Structure Generation + Si-feasibility scorer

Real nitride / B:Si candidates, epitaxial strain, transparent `SiFeasibilityScore`. See package `siscforge.structure` and `siscforge.silicon`.

Example: `examples/nbti_n_strain.yaml` (15 candidates dry-run).

---

## Slice 1 (2026-07-24) — Foundation

Package layout, Pydantic models, Calculator protocol + MockCalculator, CLI skeleton.
