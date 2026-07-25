# Implementation Notes

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
- Lightweight λ/Tc **surrogate** for pre-filtering before EPW
- Active-learning loop (uncertainty / UCB retrain)
- Broader boride enumeration (beyond bulk MgB₂ prototype)

### Next session (best single focus)
**Lightweight λ/Tc surrogate stub** for pre-filtering candidates before real EPW (mock + optional ALIGNN/MatGL-style head), keeping EPW as the ground-truth path.

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
