# NbN ε=0 k=12³ phonon diagnostic (#72)

Single-cell, phonon-only **control** after the ZrN electronic-sampling
ladder (Slice 29.4). Same settings: k=12³, q=4³, ecutwfc=60, SSSP PBE
efficiency, Docker QE 7.3.1. One lever (composition).

This is **not** a production claim and **not** an EPW run. Actual DFPT
numbers are recorded by the operator after the Docker run — do **not**
invent expected min-ω.

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
- implementation-notes Slice 29.5 / 29.4; issue #72
