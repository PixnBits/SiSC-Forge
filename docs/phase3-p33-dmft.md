# P3.3 — DMFTResult model + gate + mock + controlled launcher

**Status**: scaffold + **controlled launcher** (`p3_x_real_launch`,
issue #18) + **native h5/dat bridge** (issue #35) — typed model, Wannier
gate, mock path, JSON drop-in, solid_dmft `observables_imp*.dat` / 
`DMFT_results` h5 extractors, and a workstation-first run package that
invokes when the stack is present.  
**Prerequisite**: P3.2 Wannier prep + `ready_for_dmft` (`docs/phase3-p32-wannier.md`)  
**Next residuals**: production U/J/β calibration, solid_dmft version
matrix, NdNiO₂ literature golden (not this package). Exotic archive
layouts may still need a JSON drop-in. P3.4 pairing map is
**shipped** (`docs/phase3-p34-pairing-score.md`).

This package is **not** a turnkey production CTHYB jobflow. It ships the
consumption contract P3.4 needs plus a thin, testable launcher. Screening
defaults stay screening defaults.

Automated nscf + `pw2wannier90` is **P3.2.1 shipped**. Real
(non-mock) Wannier → DMFT still needs a usable `WannierResult`
(`ready_for_dmft`). P3.3 does **not** block on real QE: mock /
explicit inputs / a drop-in `observables.json` / native
`observables_imp*.dat` are enough.

## Goal

Provide a **first-class, optional DMFT step** that:

1. Attaches a typed `DMFTResult` to `CandidateEvaluation`.
2. Runs (or mocks) a solid_dmft-style impurity step **only when** Wannier is
   `ready_for_dmft`, unless an explicit mock / operator bypass is set.
3. Captures occupancy, mass enhancement, and **placeholder homes** for
   leading pairing eigenvalue / symmetry (filled later by P3.4).
4. Stays **disabled by default** so conventional nitride / MgB₂ / EPW
   campaigns do not change.

## What this package actually delivers

| Item | Location | Honest status |
|------|----------|---------------|
| `DMFTResult` model | `siscforge.models.results.DMFTResult` | shipped |
| Optional on evaluation | `CandidateEvaluation.dmft` (default `None`) | shipped |
| YAML knobs | `dft.do_dmft`, `dft.dmft.*` (`DMFTConfig`, **disabled by default**) | shipped |
| Helpers | `siscforge.calculators.qe.dmft` + `dmft_launch` | shipped |
| Sequential recipe | `run_dmft_after_wannier` in `qe/recipes.py` | sequential glue, not jobflow |
| Calculator | `qe-dmft` / `dmft` alias; additive when `do_dmft` on `qe` | shipped |
| Mock path | `MockCalculator` fills success/failure `DMFTResult` | shipped |
| Real path | Run package + optional invoke + JSON / native `.dat` / h5 parser; **skips** if TRIQS missing | **controlled launcher + native bridge** — not production QMC |
| Export | CSV columns `dmft_*` + synthesis-card launch notes | shipped |
| Dry-run example | `examples/ndnio2_dmft_mock.yaml` | shipped |
| Tests | `tests/test_dmft_p33.py`, `tests/test_dmft_real_launch.py` | shipped |
| Pairing → `performance_score` | `siscforge.scoring.pairing` | **P3.4 shipped** |
| Production U/J/β / version matrix | — | **residual** |

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
    n_loops: 4                   # written to dmft_config.toml as n_iter_dmft
    auto_launch: true            # invoke when stack present; mock unaffected
    # launch_timeout_s: 3600     # optional wall-clock cap; default none
    # Gate — see “Will this run or refuse?” below
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

`qe-dmft` forces `do_dmft`. It does **not** force `do_wannier` (independence
is intentional). Non-mock solvers still expect a ready `WannierResult` or
an explicit bypass (`allow_without_wannier_gate`). Pair with
`do_wannier: true` (or `qe-wannier`) for a real chain.

Conventional examples omit these knobs → behaviour unchanged.

## Will this run or refuse? (operator mental model)

Read top to bottom; first matching row wins.

| # | Condition | Outcome |
|---|-----------|---------|
| 0 | `do_dmft` / `dmft.enabled` / `qe-dmft` all off | **No DMFT step** (conventional campaigns) |
| 1 | `allow_without_wannier_gate: true` | **Allowed** — operator override, any solver |
| 2 | `solver: mock` **and** `mock_bypass_gate: true` (defaults) | **Allowed** — documented dry-run bypass even without Wannier |
| 3 | `require_wannier_gate: false` | **Allowed** — gate disabled |
| 4 | `WannierResult.ready_for_dmft` is True | **Allowed** |
| 5 | otherwise (non-mock, Wannier missing or not ready) | **Refused** — `status=refused`, `failure_class=wannier_gate` |

A refused launch stores the `DMFTResult` and **does not** delete finished
DFT+U or Wannier artifacts (sibling `dmft/` workdir only).

| Mode | Typical flags | Behaviour |
|------|---------------|-----------|
| `solver: solid_dmft` / `cthyb` | defaults | **Refuse** unless `ready_for_dmft` or `allow_without_wannier_gate` |
| `solver: mock` + `mock_bypass_gate` (default) | dry-run | May run without a ready Wannier |
| `solver: mock` + `mock_bypass_gate: false` | tests | Honours the gate even on mock |

`require_wannier_gate`, `allow_without_wannier_gate`, and `mock_bypass_gate`
are three knobs around one question: *is Wannier good enough to consume?*
Defaults are conservative for real solvers and convenient for dry-run.

## Mock vs real

- **Mock (required):** produces successful and failed `DMFTResult` without
  TRIQS. Occupancy and mass enhancement are **illustrative / deterministic
  placeholders, not literature-validated** (nickelate-like filling
  ≈ 8.65–8.95 and m*/m ≈ 2.4–4 are seeded hashes, not a calibrated NdNiO₂
  fit). Pairing fields stay `None`. The label is also stored on
  `DMFTResult.raw["physics_label"]` and in provenance notes.
- **Real (optional, controlled launcher):** writes a sibling `dmft/` run
  package, optionally invokes solid_dmft, and parses JSON drop-ins or
  native solid_dmft outputs (`.dat` / h5). If the
  stack is missing, the result is `status=skipped`,
  `failure_class=solver_missing`. **TRIQS is never a hard install
  dependency of `siscforge`.** See [SETUP.md](SETUP.md) Tier D.

### What the launcher automates vs operator-owned

**Automated**

1. Honour the P3.2 `ready_for_dmft` gate (or an explicit bypass).
2. Write / refresh `siscforge_dmft_config.json` plus a native
   `dmft_config.toml` (U/J, β, `n_iter_dmft` ← `n_loops`, CTHYB
   `n_cycles` / warmup) and `run_solid_dmft.sh` + `LAUNCH.md`.
3. Discover occupancy / filling / Z into `DMFTResult` **without
   requiring TRIQS**, first usable source wins:
   1. JSON drop-in: `observables.json`, `observables_imp0.json`,
      `siscforge_dmft_observables.json` (resume / operator hand-off).
   2. Native solid_dmft text tables: `observables_imp0.dat` and close
      variants (also under `out/` / the toml `jobname`).
   3. HDF5 archive under common `DMFT_results` / impurity-observable
      keys, **when** `h5py` is importable (still soft; missing extra
      skips this source, never crashes).
   A successful native `.dat` / h5 parse materializes a compatible
   `observables.json` in `dmft/` so later resume does not need TRIQS
   or h5py again.
4. If `auto_launch: true` (default) and the stack is importable — or
   `SISCFORGE_SOLID_DMFT` points at a wrapper — invoke, capture
   `solid_dmft.log` + exit code, then re-run the discovery above.
   Pairing keys, if present, land in the reserved P3.4
   fields and flow through the existing map.
5. Copy (never move / delete) an existing `{seed}.h5` into `dmft/` when
   found; best-effort DFTTools Wannier90 convert when that extra is
   importable.

**Still operator-owned / residual**

- Wannier90 → `{seed}.h5` when DFTTools is missing or the convert fails.
- MPI ranks and multi-day CTHYB wall-time.
- Production U/J/β/`n_cycles` calibration (screening defaults stay
  screening).
- solid_dmft / TRIQS version matrix.
- Literature-validated NdNiO₂ recovery (science golden).
- Exotic solid_dmft HDF5 layouts not covered by the best-effort walker.

Set `auto_launch: false` to write the package only and invoke yourself:

```bash
# in the candidate's sibling dmft/ directory
sh run_solid_dmft.sh
# then re-invoke siscforge, or drop observables.json / leave native .dat and re-invoke
```

`n_loops`, `n_cycles`, and `n_warmup_cycles` are consumed by the toml
writer. They remain thin workstation knobs.

## Residual P3.2.1 (orchestration shipped)

Automated nscf + `pw2wannier90` is **P3.2.1 shipped** (soft-skip without
binaries / charge density). The unconventional chain is:

```
SCF / DFT+U  →  nscf + pw2wannier90 (P3.2.1)  →  wannier90.x
             →  ready_for_dmft  →  [P3.3 mock | JSON / native .dat / h5 | auto-launch]
             →  [P3.4: pairing → performance_score]
```

Non-mock DMFT still needs a ready `WannierResult`. The gate correctly
refuses when Wannier did not produce a usable manifold. Mock +
`mock_bypass_gate` is how dry-run proceeds without real QE / Wannier90.

## Sacred upstream

DMFT failure must not delete finished DFT+U or Wannier artifacts. Same
philosophy as Wannier-after-SCF and EPW-after-DFPT. The broad
`except Exception` around the DMFT step in `QECalculator.run` is
intentional and logged. The launcher only writes under the sibling
`dmft/` workdir.

## Failure classification (best-effort)

`classify_dmft_failure` is a **v0 string-heuristic** over solver / import
text (`wannier_gate`, `solver_missing`, `import_error`, `binary_missing`,
`not_converged`, `other`). It is not a structured solver API. Treat
labels as diagnostic, not a contract for downstream ranking.

## P3.4 pairing map (follow-on package)

`DMFTResult` has:

- `leading_pairing_eigenvalue: float | None`
- `pairing_symmetry: str | None`

**P3.4** (Done) maps the leading eigenvalue onto the common
`performance_score` (`siscforge.scoring.pairing`;
`docs/phase3-p34-pairing-score.md`). `pairing_symmetry` is metadata only.
Mock eigenvalues are illustrative.

`status` is a free `str` (same pattern as other Result models);
`quality_tag` is a `Literal`. Tightening `status` can wait.

## Hard out of scope (this package)

- Pairing eigenvalue normalization into `performance_score` (**P3.4**)
- Oxygen-vacancy structure generation (**P3.5**)
- Mixed conventional/unconventional AL (**P3.6**)
- Production U/J/β calibration / solid_dmft version matrix (residual)
- Making TRIQS a required dependency
- Josephson, GNN heads, GPU QE

## Acceptance checks

- `pytest` green (existing + `test_dmft_p33` + `test_dmft_real_launch`)
- Conventional campaigns unchanged with DMFT off
- Mock path: run → store → CSV/cards with `DMFTResult` fields
- Wannier `ready_for_dmft` gate enforced outside explicit mock bypass
- Real TRIQS tests skipped without the stack
- Fake/stub launcher writes the run package, classifies failures, keeps
  upstream sacred
- Language in this doc / README / ROADMAP matches the controlled launcher
  + native h5/dat bridge (JSON still preferred; no longer “must drop
  observables.json” for a non-failed `DMFTResult`)
