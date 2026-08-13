# P3.3 — TRIQS / solid_dmft recipe + `DMFTResult`

**Status**: shipped (model + mock + Wannier gate + optional real skip)  
**Prerequisite**: P3.2 Wannier prep + `ready_for_dmft` (`docs/phase3-p32-wannier.md`)  
**Next**: **P3.4** — pairing eigenvalue → common `performance_score`

## Goal

Provide a **first-class, optional DMFT step** that:

1. Attaches a typed `DMFTResult` to `CandidateEvaluation`.
2. Runs (or mocks) a solid_dmft-style impurity step **only when** Wannier is
   `ready_for_dmft`, unless an explicit mock / operator bypass is set.
3. Captures occupancy, mass enhancement, and **placeholder homes** for
   leading pairing eigenvalue / symmetry (filled later by P3.4).
4. Stays **disabled by default** so conventional nitride / MgB₂ / EPW
   campaigns do not change.

Automated nscf + `pw2wannier90` is still residual **P3.2.1**. P3.3 does
**not** block on that residual: mock / explicit inputs are enough.

## What shipped

| Item | Location |
|------|----------|
| `DMFTResult` model | `siscforge.models.results.DMFTResult` |
| Optional on evaluation | `CandidateEvaluation.dmft` (default `None`) |
| YAML knobs | `dft.do_dmft`, `dft.dmft.*` (`DMFTConfig`, **disabled by default**) |
| Helpers | `siscforge.calculators.qe.dmft` |
| Sequential recipe | `run_dmft_after_wannier` in `qe/recipes.py` |
| Calculator | `qe-dmft` / `dmft` alias; additive when `do_dmft` on `qe` |
| Mock path | `MockCalculator` fills success/failure `DMFTResult` |
| Real path | Thin solid_dmft / TRIQS wrapper; **skips cleanly** if not installed |
| Export | CSV columns `dmft_*` + synthesis-card section |
| Dry-run example | `examples/ndnio2_dmft_mock.yaml` |
| Tests | `tests/test_dmft_p33.py` |

## Enablement (inert by default)

```yaml
dft:
  do_dmft: true
  dmft:
    enabled: true
    solver: mock                 # mock | solid_dmft | cthyb
    U_eV: 5.0                    # screening default
    J_eV: 0.8
    beta: 40.0                   # 1/eV; screening ~290 K
    n_cycles: 10000              # thin QMC knob, not production CTHYB
    n_warmup_cycles: 2000
    n_loops: 4
    # Gate
    # require ready_for_dmft unless:
    allow_without_wannier_gate: false
    mock_bypass_gate: true       # documented dry-run bypass (solver=mock)
    # mock_force_failure: true
    # mock_failure_class: not_converged
```

Or calculator choice:

```bash
siscforge run --calculator qe-dmft campaign.yaml
siscforge run --dry-run examples/ndnio2_dmft_mock.yaml
```

Conventional examples omit these knobs → behaviour unchanged.

## Wannier gate (P3.2 contract)

`WannierResult.ready_for_dmft` is the consumption gate.

| Mode | Behaviour |
|------|-----------|
| `solver: solid_dmft` / `cthyb` | **Refuse** unless `ready_for_dmft` or `allow_without_wannier_gate` |
| `solver: mock` + `mock_bypass_gate` (default) | Documented dry-run bypass — may run without a ready Wannier |
| `solver: mock` + `mock_bypass_gate: false` | Honours the gate even on mock |

A refused launch stores `DMFTResult(status=refused, failure_class=wannier_gate)`
and **does not** delete finished DFT+U or Wannier artifacts (sibling `dmft/`
workdir only).

## Mock vs real

- **Mock (required):** produces successful and failed `DMFTResult` without
  TRIQS. Deterministic occupancy + mass enhancement. Pairing fields stay
  `None`.
- **Real (optional):** if `triqs` / `solid_dmft` is importable, the wrapper
  writes a config sidecar and will parse a drop-in `observables.json`. If
  the stack is missing, the result is `status=skipped`,
  `failure_class=solver_missing`. **TRIQS is never a hard install
  dependency of `siscforge`.**

## Sacred upstream

DMFT failure must not delete finished DFT+U or Wannier artifacts. Same
philosophy as Wannier-after-SCF and EPW-after-DFPT.

## P3.4 extension point (explicitly not this package)

`DMFTResult` already has:

- `leading_pairing_eigenvalue: float | None`
- `pairing_symmetry: str | None`

P3.4 will map the leading eigenvalue onto the common `performance_score`
so ranking / AL need no special cases. **This package does not change
`performance_score`.** See `DMFTResult.raw["extension_hooks"]["p3_4_pairing"]`.

## Hard out of scope (this PR)

- Pairing eigenvalue normalization into `performance_score` (**P3.4**)
- Oxygen-vacancy structure generation (**P3.5**)
- Mixed conventional/unconventional AL (**P3.6**)
- Finishing residual automated nscf + `pw2wannier90` (**P3.2.1**)
- Making TRIQS a required dependency
- Josephson, GNN heads, GPU QE

## Acceptance checks

- `pytest` green (existing + `test_dmft_p33`)
- Conventional campaigns unchanged with DMFT off
- Mock path: run → store → CSV/cards with `DMFTResult` fields
- Wannier `ready_for_dmft` gate enforced outside explicit mock bypass
- Real TRIQS tests skipped without the stack
