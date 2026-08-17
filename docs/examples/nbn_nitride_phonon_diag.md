# NbN ε=0 k=12³ phonon diagnostic (#72 / #74)

Single-cell, phonon-only **control** after the ZrN electronic-sampling
ladder (Slice 29.4). Same settings: k=12³, q=4³, ecutwfc=60, SSSP PBE
efficiency, Docker QE 7.3.1. One lever (composition).

This is **not** a production claim and **not** an EPW run. Operator
numbers from the finished store are below — do **not** invent min-ω.

## Why

ZrN ε=0 finite-q imaginary modes collapsed at k=12³ to Γ-noise
(−29.3 cm⁻¹). Before any family-wide nitride phonon k default, check
whether that policy generalises to stoichiometric NbN.

## Literature caveat

Ideal stoichiometric δ-NbN (rock-salt) is often reported as
harmonically unstable or extremely soft. Experimental superconducting
NbN is typically N-deficient; recent work invokes anharmonicity. A
dense-k harmonic result that stays substantially soft is **not**
automatically another mesh artefact. Record min ω honestly either way.

## Settings

- Formula: NbN, ε=0, Fm-3m primitive, one candidate
- k=12³, q=4³, ecutwfc=60, ecutrho=480
- `do_relax: true`, `do_phonon: true`, `do_epw: false`
- `quality_tag: screening`, `nproc: 16`, `ph_niter: 150`
- `pseudo_dir: /usr/share/espresso/pseudo`
- Store: `outputs/nbn_k12_diag` (new; do not reuse ZrN stores)
- Resume-safe: `run.resume: true`, `force_rerun: false`, `resume_qe_steps: true`

## Launch

```bash
docker run --rm -it -v "$PWD:/workspace" -w /workspace --cpus=16 \
  siscforge:latest siscforge run --calculator qe examples/nbn_k12_diag.yaml
```

After the run:

```bash
siscforge soft-modes outputs/nbn_k12_diag --refresh
```

Compare Γ vs finite-q to the ZrN table in
`docs/examples/zrn_nitride_phonon_convergence.md`.

## Results (operator, #74)

Store `outputs/nbn_k12_diag`, Docker QE 7.3.1, 16 cores. `do_epw: false`.
First `ph.x` aborted (`d_matrix` / D_S not orthogonal); the existing
`phonon_retry_on_d_matrix` path re-SCF'd with `nosym`/`noinv` and
finished DFPT (`JOB DONE`, wall ~16 h 4 m). Setup recovery only — not
a default physics change for other cells. Full 4³ grid: **64**
q-points (ZrN k=12³ used 24 with symmetry). ASR not applied.

### Structure

Relaxed primitive is sensible Fm-3m rock-salt:

- space group `Fm-3m`; basis Nb (0,0,0) / N (½,½,½)
- `a_prim` = 3.1266 Å (60°/60°/60°) → `a_cub` = **4.422 Å**
- literature a_cub ≈ 4.39 Å
- vc-relax: BFGS converged in 4 SCF / 3 steps

### Phonon (`siscforge soft-modes outputs/nbn_k12_diag --refresh`)

|  | ω (cm⁻¹) |
|---|---:|
| campaign min | **−301.5** |
| Γ min | −76.8 |
| finite-q min | **−301.5** |

- softness locus: `finite_q` (softest q ≈ (0.289, −0.408, −0.500))
- 34 / 64 q-points have min ω < −5 cm⁻¹ (33 finite-q)
- 33 finite-q points sit below −100 cm⁻¹; 3 sit below −200 cm⁻¹
- acoustic-only imaginary at the softest q; optical branches stay
  real there (~440 cm⁻¹)
- After #76, `siscforge soft-modes --refresh` on this dense-k store
  classifies the cell as `genuinely_soft` with reasons
  `dense_k_still_substantially_soft` /
  `ideal_stoichiometric_harmonic_instability` /
  `policy_override_not_mesh_artefact`. The old auto-label
  `likely_mesh_artefact` (NbN in `KNOWN_STABLE_RS_NITRIDES` +
  `quality_tag=screening`) was a first-pass *suspect* and is no
  longer applied once recorded SCF k is ≥12³ and leftover imag is
  past Γ-noise. Still not `stable` / EPW.

Compare to ZrN ε=0 at the **same** k=12³ / q=4³ / ecut=60: min
**−29.3 cm⁻¹**, finite-q collapsed to Γ-noise scale. NbN did **not**.

### QE log notes

- Pseudos (unchanged, do not swap): `Nb.pbe-spn-kjpaw_psl.0.3.0.UPF`
  (PAW, Zval=13), `N.pbe-n-radius_5.UPF` (USPP, Zval=5)
- `nbnd=48` for 18 electrons. No “not enough bands” / occupation
  warning in `scf.out` or `ph.out`. SCF converged in 8 iterations
  (Fermi 20.3007 eV).
- nosym SCF/PH: “No symmetry found” on every q (expected after the
  d_matrix retry).
- vc-relax final SCF printed several `c_bands: 1 eigenvalues not
  converged` lines; BFGS still converged. Not a phonon abort.
- `ph.out` floating-point `IEEE_DENORMAL` notes at exit after
  `JOB DONE`. Not treated as a failure.

No λ / Tc / dynamical-stability claim from this store.

## Policy decision (#74)

Finite-q softness **did not** collapse to Γ-noise scale. The cell
stays substantially soft at dense k. Treat that as expected
literature behaviour for *ideal stoichiometric* δ-NbN (often
harmonically unstable; experimental SC NbN is N-deficient;
anharmonicity important).

- Do **not** automatically treat the −301.5 cm⁻¹ finite-q branch as
  a mesh artefact.
- Do **not** block the nitride phonon k-policy for other binaries —
  ZrN already closed its electronic-sampling ladder.
- **Confirm** the existing recovery stance for *new* maps:
  `NITRIDE_PHONON_K_POLICY` stays min 8³, prefer 12³ for small /
  rock-salt binaries. Global `DFTConfig.kpoints` stays `[4,4,4]`.
- Historical campaign YAMLs stay frozen at their original k.

Do not auto-promote residual imaginary modes to `stable`. Do not
launch EPW from this control.

## What not to do

- Do not launch EPW.
- Do not `--force-rerun` this store or finished ZrN dyn sets.
- Do not swap UPFs or apply ASR as a “fix”.
- Do not auto-promote residual imaginary modes to `stable`.
- Do not cite λ / Tc or dynamical stability from this diagnostic.
- Do not expand to ternaries or a family map in this control.

## Related

- Twin: `examples/zrn_k12_diag.yaml`
- ZrN checklist: `docs/examples/zrn_nitride_phonon_convergence.md`
- implementation-notes Slice 29.5 / #74; Slice 29.4
- Issues #72 (YAML), #74 (numbers + policy), #76 (heuristic)
