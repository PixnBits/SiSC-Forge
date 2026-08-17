# Binary-nitride phonon convergence checklist (ZrN / NbN / TiN)

Use this **before** another full q-ladder or any EPW. Known-stable
rock-salt binaries that stay imaginary on screening meshes are usually
electronic-sampling or Γ-acoustic noise, not a reason to expand
composition space.

Do **not** shortlist imaginary-mode cells for EPW. Do **not**
`--force-rerun` a finished dyn set.

## 1. Structure

- Primitive Fm-3m, basis (0,0,0) / (½,½,½).
- ZrN: a_prim ≈ 3.25 Å → a_cub ≈ 4.59 Å (matches exp.).
- Confirm space group / CIF in the store one-pager, not just the formula.

## 2. Split Γ vs finite-q

```bash
siscforge soft-modes outputs/<store> --refresh
```

Read `soft_mode_report.md`:

| Pattern | Meaning |
|---------|---------|
| Γ ~ −30 cm⁻¹, campaign min much lower at another q | Finite-q softness. Densify **k / ecut**, do not just raise q. |
| Only Γ below threshold | Acoustic numerical noise. ASR is **not** applied; this alone is not a physical instability. |
| Soft q-points **move** when k changes | Metallic k-mesh artefact. |

`likely_mesh_artefact` is a **suspect**, not proof. Soft-mode locus already
prefers densify SCF k / ecut on the **same** q-grid when the softest mode
is finite-q.

## 3. Electronic sampling (same cell, same q)

Keep one lever at a time. Recorded ε=0 ladders (QE 7.3.1 Docker):

| cell | k | ecutwfc | q | min ω (cm⁻¹) | Γ / finite-q | Notes |
|------|---|---------|---|-------------:|--------------|-------|
| ZrN | 4³ | 50 | 4³ | −148.7 | finite-q | many finite-q soft |
| ZrN | 8³ | 60 | 4³ | −72.1 | finite-q | most soft q healed |
| ZrN | 12³ | 60 | 4³ | **−29.3** | Γ-noise | finite-q collapsed to Γ-noise scale |
| NbN | 12³ | 60 | 4³ | **−301.5** | Γ −76.8 / finite-q **−301.5** | still substantially soft (#74) |

Leftover −29.3 cm⁻¹ is acoustic-like at Γ (ASR not applied). Soft-mode
locus treats |Γ| below `_GAMMA_MILD_CM1` (50 cm⁻¹) as ordinary acoustic
numerical noise — the same class as this leftover. k is **no longer** the
dominant lever for this cell. Soft-mode class stays `likely_mesh_artefact`.
Do **not** auto-promote to `stable` or EPW.

The first step also raised ecutwfc 50 → 60 with k. Healing still
identifies electronic k as the dominant artefact; k=8³ → 12³ was at
**fixed** ecut=60. Future ladders should change one lever at a time.

**Policy (nitride phonon screening / pilot):** numbers live in
`siscforge.pilot` (`NITRIDE_PHONON_K_POLICY`: min 8³, prefer 12³ for
small / rock-salt binaries). Global `DFTConfig.kpoints` stays `[4,4,4]`.
A mixed selection (any large non-binary) takes the 8³ floor for the whole
pilot. Historical campaign YAMLs stay frozen at their original k.

The NbN ε=0 control at the same k=12³ / q=4³ / ecut=60 settings
(`examples/nbn_k12_diag.yaml`, issue #74) did **not** collapse:
min −301.5 cm⁻¹, finite-q −301.5 vs Γ −76.8. That leftover is treated
as expected harmonic softness of *ideal stoichiometric* δ-NbN, **not**
as a mesh artefact and **not** as a reason to block prefer-12³ for
other binaries (ZrN already closed its ladder). Soft-mode class after
#76 is `genuinely_soft` (policy override) when recorded k ≥ 12³; still
not `stable` / EPW. See `docs/examples/nbn_nitride_phonon_diag.md`.

## 4. Pseudopotentials

Current SSSP PBE efficiency pair (`/usr/share/espresso/pseudo`):

- `Zr_pbe_v1.uspp.F.UPF` (GBRV USPP)
- `N.pbe-n-radius_5.UPF` (Dal Corso USPP)

Remain adequate — do **not** swap UPFs. Not flagged as known-soft. A
matched SSSP precision / PAW pair is a later lever, not this cell's next
step.

## 5. What not to do

- Do not launch EPW.
- Do not re-pilot coarser q as primary after q=4³ already showed
  finite-q softness (and after k densification collapsed it).
- Do not expand to ternaries until a known-stable binary is hard
  (or the artefact is identified).
- Do not cite λ / Tc from these stores.
- Do not apply ASR as a “fix” (it would only clean Γ).
- Do not `--force-rerun` finished DFPT dyn sets.

## Related

- `examples/zrn_kmesh_diag.yaml`, `examples/zrn_k12_diag.yaml`
- `outputs/zrn_kmesh_diag/`, `outputs/zrn_k12_diag/`,
  `outputs/nitride_phonon_diag_q4/`
- implementation-notes Slice 29.4 / 29.5 / #74; specs §2.3c
- NbN ε=0 control: `examples/nbn_k12_diag.yaml`,
  `docs/examples/nbn_nitride_phonon_diag.md`, issues #72 / #74
