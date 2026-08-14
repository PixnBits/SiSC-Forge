# P3.2 / P3.2.1 — Wannierization prep, quality metrics, nscf + pw2wannier90

**Status**: shipped (metrics + mock + gate + prep + **P3.2.1 orchestration**)  
**Prerequisite**: P3.1 DFT+U (`docs/phase3-p31-dftu.md`)  
**Residual (honest)**: real-QE nscf+pw2wannier90 **golden** is optional / local (`SISCFORGE_RUN_QE=1`); spinor / collinear-spin manifolds; material-specific production projection libraries

## Goal

Provide a **first-class prep + quality-metrics step** for standalone Wannierization that:

1. Can run after SCF / DFT+U for correlated (nickelate) candidates.
2. Produces a typed `WannierResult` with success/failure and quality diagnostics.
3. Remains optional and inert for conventional nitride / MgB₂ campaigns.
4. Sets a clean extension point for **P3.3** (TRIQS / solid_dmft).
5. **P3.2.1** — when `pw.x`, `pw2wannier90.x`, and an upstream `{prefix}.save`
   are present, automatically run nscf → `wannier90.x -pp` → `pw2wannier90` →
   gated `wannier90.x` so operators do **not** have to stage `.amn`/`.mmn`.

The conventional EPW pathway still runs its **own** internal Wannier90 step
(`proj=random`, coarse grids, frozen-window remediation). That path is
**unchanged** by this package. Standalone `CandidateEvaluation.wannier` is
independent of `electron_phonon.wannier_ok`.

## What shipped

| Item | Location |
|------|----------|
| `WannierResult` model | `siscforge.models.results.WannierResult` |
| Optional on evaluation | `CandidateEvaluation.wannier` (default `None`) |
| YAML knobs | `dft.do_wannier`, `dft.wannier.*` (`WannierConfig`, **disabled by default**) |
| QE helpers | `siscforge.calculators.qe.wannier` |
| Sequential recipe | `run_wannier_after_scf` in `qe/recipes.py` |
| P3.2.1 orchestration | `prepare_amn_mmn` / `run_nscf_for_wannier` / `run_pw2wannier90` |
| Calculator | `qe-wannier` / `wannier` alias; additive when `do_wannier` on `qe` |
| Mock path | `MockCalculator` fills success/failure `WannierResult` |
| Export | CSV columns `wannier_*` + synthesis-card section (step-aware next step) |
| Dry-run example | `examples/ndnio2_wannier_mock.yaml` |
| Tests | `tests/test_wannier_p32.py`, `tests/test_wannier_p321.py` |

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
    auto_nscf_pw2wannier: true       # P3.2.1; soft-skip without binaries
    # DMFT gate: conservative screening defaults pending Ni calibration
    max_avg_spread_ang2: 12.0
    max_spread_ang2: 25.0
    require_chk: true
    # mock_force_failure: true       # dry-run failed WannierResult
    # mock_failure_class: frozen_window
```

Or calculator choice:

```bash
siscforge run --calculator qe-wannier campaign.yaml
siscforge run --dry-run examples/ndnio2_wannier_mock.yaml
```

Conventional examples omit these knobs → behaviour unchanged.

## Real path: SCF → nscf → pw2wannier90 → wannier90.x

When standalone Wannier is enabled:

1. Finish SCF (or DFT+U SCF) — artifacts remain sacred on Wannier failure.
2. SiSC-Forge writes `{seed}.win` under a sibling `wannier/` directory.
3. **P3.2.1 (automated, default on):** if `pw.x` + `pw2wannier90.x` and an
   upstream `{prefix}.save` exist, copy the save into `wannier/out/` (isolated
   so EPW / DFT+U wavefunctions are not overwritten), run nscf on the Wannier
   k-mesh (`resolve_kmesh`), `wannier90.x -pp`, then `pw2wannier90.x`.
4. Gated `wannier90.x` parses spreads into `WannierResult` + `ready_for_dmft`.

Soft dependency: missing binaries or charge density → `missing_files` /
`binary_missing` (no crash). Set `wannier.auto_nscf_pw2wannier: false` to
keep the older manual-stage path. You can still drop `.amn`/`.mmn` into
`wannier/` yourself; automation is then skipped.

Failure at nscf or pw2wannier90 sets `failure_class` to `nscf_failed` or
`pw2wannier_failed`. Synthesis cards / `summary_line` surface the concrete
next step (inspect `nscf.out` / `pw2wan.out`); they no longer claim that
manual staging is the only path.

## Quality metrics & failure classes

`WannierResult` carries:

- `wannier_ok`, `status`, `quality_tag`
- Spreads: `spread_sum_ang2`, `avg_spread_ang2`, `max_spread_ang2`, `spreads_ang2`
- Projection / band summary: `num_wann`, `num_bands`, `projection_mode`, `projection_summary`
- Window notes: `disentanglement_notes`, `frozen_window_notes`
- `kmesh`: **actual** mesh written (post-`resolve_kmesh`, may be auto-raised)
- Artifact handles: `work_dir`, `.win` / `.amn` / `.mmn` / `.chk` / `.wout` paths
- Step-aware `failure_class` (never phonon-only labels):
  `frozen_window`, `kmesh_bvector`, `disentanglement`, `spread_divergence`,
  `missing_files`, `binary_missing`, `projection`, `nscf_failed`,
  `pw2wannier_failed`, `convergence`, `other`

`convergence` is classified from logs but is **not** a hard-fail fingerprint on
its own; a non-zero returncode / incomplete job still marks `wannier_ok=False`.

### DMFT gate (P3.3 hook)

`ready_for_dmft` is **False** when:

- `wannier_ok` is False, or
- required `.chk` is missing (`require_chk`), or
- average / max spreads exceed thresholds.

**Threshold rationale:** `max_avg_spread_ang2=12` and `max_spread_ang2=25` are
**conservative screening defaults** pending nickelate-specific calibration.
With `proj=random` many candidates will gate out — intentional so P3.3 does not
consume obviously delocalized / failed MLWFs. Tighten for production / explicit
projections; loosen only with documented local validation. Not derived
from a single literature cutoff.

P3.3 should refuse to launch TRIQS/solid_dmft when `ready_for_dmft` is False.
See `WannierResult.raw["extension_hooks"]["p3_3_dmft"]`.

## Sacred upstream

Remediable Wannier failures **do not** delete finished SCF / DFT+U artifacts.
Wannier work is written under a sibling `wannier/` directory. NSCF reads a
**copy** of `{prefix}.save` under `wannier/out/` so EPW's nscf k-mesh in the
candidate directory is not overwritten. Same philosophy as EPW-after-DFPT
remediation.

## Limits (documented)

- Screening defaults use `proj=random` + coarse k (EPW lessons).
- Material-specific production projection libraries are a **later residual**.
- **Spin / nspin:** NdNiO₂ examples may enable DFT+U with `nspin: 2`. The `.win`
  builder currently has **no** spinor / spin-component / separate-manifold
  support. P3.3 must not assume collinear-spin Wannier is ready out of the box.
- `default_num_wann_screening` shares *spirit* with EPW `auto_nbndsub` but uses
  a slightly different floor (often smaller manifolds for correlated screening).
- Mock path fills complete metrics without binaries.
- Real-QE end-to-end nscf+pw2wannier90 on NdNiO₂ is **optional / local**
  (`SISCFORGE_RUN_QE=1`); CI uses fake binaries.

## Extension points (explicitly out of this package)

| Package | Work | Hook |
|---------|------|------|
| **P3.3** | TRIQS / solid_dmft → `DMFTResult` | **Scaffold** — model + gate + mock + drop-in parser; full launch residual; `docs/phase3-p33-dmft.md` |
| **P3.4** | Pairing eigenvalue → `performance_score` | Map leading eigenvalue |
| **P3.5** | Oxygen-vacancy enumeration | Structure generation |
| **P3.6** | Mixed conventional/unconventional AL | Acquisition updates |

## Hard out of scope (this PR)

- Full DMFT / CTHYB / solid_dmft launch (`p3_x_real_launch`, issue #18)
- Pairing eigenvalue scoring (already P3.4)
- Oxygen-vacancy structure generation
- Mixed AL acquisition
- Material-specific production Wannier projection libraries
- Spinor / collinear-spin Wannier manifolds
- Changes to EPW-internal Wannier remediation
- Changing `ready_for_dmft` gate semantics
- Josephson, GNN heads, GPU QE

## Acceptance checks

- `pytest` green (existing + `test_wannier_p32` + `test_wannier_p321`)
- Conventional campaigns unchanged with Wannier off
- Mock path: run → store → CSV/cards with `WannierResult` quality fields
- Failed Wannier classified clearly; upstream SCF/DFT+U kept
- Real path: automated nscf + `pw2wannier90` when binaries + charge density
  are present; clean `missing_files` / `binary_missing` without binaries
- `nscf_failed` / `pw2wannier_failed` set the matching `failure_class`
- `ready_for_dmft` still respects P3.2 spread / `.chk` thresholds
