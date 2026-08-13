# P3.3 — DMFTResult model + gate + mock + parser scaffold

**Status**: scaffold shipped — typed model, Wannier gate, mock path, and a
thin optional drop-in parser. **Full automated solid_dmft / CTHYB launch
is residual** (operator-driven or a later follow-up).  
**Prerequisite**: P3.2 Wannier prep + `ready_for_dmft` (`docs/phase3-p32-wannier.md`)  
**Next**: **P3.4** — pairing eigenvalue → common `performance_score`
(**shipped** — `docs/phase3-p34-pairing-score.md`)

This package is **not** a turnkey TRIQS/solid_dmft jobflow. It ships the
consumption contract P3.4 needs (typed `DMFTResult`, gate, mock, parser)
and leaves a documented hook for a real launcher.

Automated nscf + `pw2wannier90` is still residual **P3.2.1**. Real
(non-mock) Wannier → DMFT still depends on that residual for artifact
readiness. P3.3 does **not** block on it: mock / explicit inputs / a
drop-in `observables.json` are enough.

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
| Helpers | `siscforge.calculators.qe.dmft` | shipped |
| Sequential recipe | `run_dmft_after_wannier` in `qe/recipes.py` | sequential glue, not jobflow |
| Calculator | `qe-dmft` / `dmft` alias; additive when `do_dmft` on `qe` | shipped |
| Mock path | `MockCalculator` fills success/failure `DMFTResult` | shipped |
| Real path | Sidecar writer + drop-in `observables.json` parser; **skips** if TRIQS missing | **thin scaffold** — does **not** launch CTHYB |
| Export | CSV columns `dmft_*` + synthesis-card section | shipped |
| Dry-run example | `examples/ndnio2_dmft_mock.yaml` | shipped |
| Tests | `tests/test_dmft_p33.py` | shipped |
| Automated solid_dmft launch | — | **residual** |
| Pairing → `performance_score` | reserved fields only | **P3.4** |

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
    n_loops: 4                   # stored for a future launcher; unused by the thin parser
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
- **Real (optional, thin):** if `triqs` / `solid_dmft` is importable, the
  wrapper writes a config sidecar (`siscforge_dmft_config.json`) and
  **parses** a drop-in `observables.json` if present. It does **not**
  launch solid_dmft or CTHYB. If the stack is missing, the result is
  `status=skipped`, `failure_class=solver_missing`. **TRIQS is never a
  hard install dependency of `siscforge`.** See [SETUP.md](SETUP.md)
  Tier D.

### Real-path operator workflow (drop-in, not auto-launch)

1. Produce Wannier artifacts and a `WannierResult` with
   `ready_for_dmft=True` (today: P3.2 prep + **you** or residual **P3.2.1**
   stage nscf + `pw2wannier90` + gated `wannier90.x`).
2. Run solid_dmft / CTHYB **externally** (your TRIQS environment, your
   wall-time, your interaction / β / loop settings).
3. Drop `observables.json` (or `observables_imp0.json` /
   `siscforge_dmft_observables.json`) into the candidate's sibling
   `dmft/` workdir. The parser is best-effort on common keys
   (`occupancy` / `n_imp`, `filling`, `Z` / `mass_enhancement`,
   `converged`).
4. Re-invoke `siscforge` (or `run_dmft_workflow` / `run_solid_dmft`) so
   the parser populates `DMFTResult`. Pairing keys, if present, land in
   the reserved P3.4 fields and are **not** ranked.

Sidecar knobs `n_loops`, `n_cycles`, `n_warmup_cycles` are stored for a
future launcher. The thin parser does not consume them.

**Residual launcher hook:** a minimal helper that shells out or writes a
ready-to-run solid_dmft config from `WannierResult` + `DMFTConfig` is
intentionally not in this package. See
`DMFTResult.raw["extension_hooks"]["p3_x_real_launch"]`.

## Residual P3.2.1 (still open)

The full automated chain is **not** closed:

```
SCF / DFT+U  →  [P3.2.1 residual: nscf + pw2wannier90]  →  wannier90.x
             →  ready_for_dmft  →  [P3.3 mock | drop-in parse]
             →  [residual: auto solid_dmft launch]
             →  [P3.4: pairing → performance_score]
```

Non-mock DMFT still needs a ready `WannierResult`. The gate correctly
refuses when that residual has not produced artifacts. Mock +
`mock_bypass_gate` is how dry-run proceeds without P3.2.1.

## Sacred upstream

DMFT failure must not delete finished DFT+U or Wannier artifacts. Same
philosophy as Wannier-after-SCF and EPW-after-DFPT. The broad
`except Exception` around the DMFT step in `QECalculator.run` is
intentional and logged.

## Failure classification (best-effort)

`classify_dmft_failure` is a **v0 string-heuristic** over solver / import
text (`wannier_gate`, `solver_missing`, `import_error`, `binary_missing`,
`not_converged`, `other`). It is not a structured solver API. Treat
labels as diagnostic, not a contract for downstream ranking.

## P3.4 pairing map (shipped in a follow-on package)

`DMFTResult` has:

- `leading_pairing_eigenvalue: float | None`
- `pairing_symmetry: str | None`

**P3.4** maps the leading eigenvalue onto the common `performance_score`
(`siscforge.scoring.pairing`; `docs/phase3-p34-pairing-score.md`).
`pairing_symmetry` is metadata only. Mock eigenvalues are illustrative.

`status` is a free `str` (same pattern as other Result models);
`quality_tag` is a `Literal`. Tightening `status` can wait.

## Hard out of scope (this PR)

- Pairing eigenvalue normalization into `performance_score` (**P3.4**)
- Oxygen-vacancy structure generation (**P3.5**)
- Mixed conventional/unconventional AL (**P3.6**)
- Finishing residual automated nscf + `pw2wannier90` (**P3.2.1**)
- Automated solid_dmft / CTHYB launch (residual; see operator workflow)
- Making TRIQS a required dependency
- Josephson, GNN heads, GPU QE

## Acceptance checks

- `pytest` green (existing + `test_dmft_p33`)
- Conventional campaigns unchanged with DMFT off
- Mock path: run → store → CSV/cards with `DMFTResult` fields
- Wannier `ready_for_dmft` gate enforced outside explicit mock bypass
- Real TRIQS tests skipped without the stack
- Language in this doc / README / ROADMAP matches the thin real path
