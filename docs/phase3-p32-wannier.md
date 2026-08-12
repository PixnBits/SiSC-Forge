# P3.2 — Wannierization pipeline with quality metrics

**Status**: shipped (Phase 3 vertical slice 2)  
**Prerequisite**: P3.1 DFT+U (`docs/phase3-p31-dftu.md`)

## Goal

Provide a **first-class, reusable Wannierization step** with explicit quality
metrics that:

1. Can run after SCF / DFT+U for correlated (nickelate) candidates.
2. Produces a typed `WannierResult` with success/failure and quality diagnostics.
3. Remains optional and inert for conventional nitride / MgB₂ campaigns.
4. Sets a clean extension point for **P3.3** (TRIQS / solid_dmft).

The conventional EPW pathway still runs its **own** internal Wannier90 step
(`proj=random`, coarse grids, frozen-window remediation). That path is
**unchanged** by this package.

## What shipped

| Item | Location |
|------|----------|
| `WannierResult` model | `siscforge.models.results.WannierResult` |
| Optional on evaluation | `CandidateEvaluation.wannier` (default `None`) |
| YAML knobs | `dft.do_wannier`, `dft.wannier.*` (`WannierConfig`, **disabled by default**) |
| QE helpers | `siscforge.calculators.qe.wannier` |
| Sequential recipe | `run_wannier_after_scf` in `qe/recipes.py` |
| Calculator | `qe-wannier` / `wannier` alias; additive when `do_wannier` on `qe` |
| Mock path | `MockCalculator` fills success/failure `WannierResult` |
| Export | CSV columns `wannier_*` + synthesis-card section |
| Dry-run example | `examples/ndnio2_wannier_mock.yaml` |
| Tests | `tests/test_wannier_p32.py` |

## Enablement (inert by default)

```yaml
dft:
  do_wannier: true
  wannier:
    enabled: true
    projection_mode: random          # or explicit
    # projections: [Ni:d, O:p]       # when projection_mode: explicit
    auto_num_wann: true
    num_wann: 10                     # optional override
    screening_tight_froz: true       # EPW-aligned tight frozen window
    kmesh: [4, 4, 4]                 # auto-raised to Wannier-safe floors
    max_avg_spread_ang2: 12.0        # DMFT gate threshold
    max_spread_ang2: 25.0
    require_chk: true
    # mock_force_failure: true       # dry-run failed WannierResult
    # mock_failure_class: frozen_window
```

Or calculator choice:

```bash
siscforge run --calculator qe-wannier campaign.yaml   # real wannier90.x when present
siscforge run --dry-run examples/ndnio2_wannier_mock.yaml
```

Conventional examples omit these knobs → behaviour unchanged.

## Quality metrics & failure classes

`WannierResult` carries:

- `wannier_ok`, `status`, `quality_tag`
- Spreads: `spread_sum_ang2`, `avg_spread_ang2`, `max_spread_ang2`, `spreads_ang2`
- Projection / band summary: `num_wann`, `num_bands`, `projection_mode`, `projection_summary`
- Window notes: `disentanglement_notes`, `frozen_window_notes`
- Artifact handles: `work_dir`, `.win` / `.amn` / `.mmn` / `.chk` / `.wout` paths
- Step-aware `failure_class` (never phonon-only labels):
  `frozen_window`, `kmesh_bvector`, `disentanglement`, `spread_divergence`,
  `missing_files`, `binary_missing`, `projection`, `nscf_failed`,
  `pw2wannier_failed`, `convergence`, `other`

### DMFT gate (P3.3 hook)

`ready_for_dmft` is **False** when:

- `wannier_ok` is False, or
- required `.chk` is missing (`require_chk`), or
- average / max spreads exceed thresholds.

P3.3 should refuse to launch TRIQS/solid_dmft when `ready_for_dmft` is False.
See `WannierResult.raw["extension_hooks"]["p3_3_dmft"]`.

## Sacred upstream

Remediable Wannier failures **do not** delete finished SCF / DFT+U artifacts.
Wannier work is written under a sibling `wannier/` directory. Same philosophy
as EPW-after-DFPT remediation.

## Limits (documented)

- Screening defaults use `proj=random` + coarse k (EPW lessons).
- Material-specific production projection libraries are a **later residual**.
- Real path still needs nscf + `pw2wannier90` prep for `.amn`/`.mmn`; the
  pipeline writes `.win`, classifies missing prep cleanly, and runs
  `wannier90.x` when artifacts exist. Mock path fills complete metrics without
  binaries.

## Extension points (explicitly out of this package)

| Package | Work | Hook |
|---------|------|------|
| **P3.3** | TRIQS / solid_dmft → `DMFTResult` | Consume `WannierResult` artifacts + gate |
| **P3.4** | Pairing eigenvalue → `performance_score` | Map leading eigenvalue |
| **P3.5** | Oxygen-vacancy enumeration | Structure generation |
| **P3.6** | Mixed conventional/unconventional AL | Acquisition updates |

## Hard out of scope (this PR)

- Full DMFT / CTHYB / solid_dmft  
- Pairing eigenvalue scoring  
- Oxygen-vacancy structure generation  
- Mixed AL acquisition  
- Material-specific production Wannier projection libraries  
- Changes to EPW-internal Wannier remediation  
- Josephson, GNN heads, GPU QE  

## Acceptance checks

- `pytest` green (existing + `test_wannier_p32`)  
- Conventional campaigns unchanged with Wannier off  
- Mock path: run → store → CSV/cards with `WannierResult` quality fields  
- Failed Wannier classified clearly; upstream SCF/DFT+U kept  
- Real Wannier90 path gated/skipped without `wannier90.x`  
