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

Keep one lever at a time. Recorded ZrN ε=0 ladder (QE 7.3.1 Docker):

| k | ecutwfc | q | min ω (cm⁻¹) | Notes |
|---|---------|---|-------------:|-------|
| 4³ | 50 | 4³ | −148.7 | many finite-q soft |
| 8³ | 60 | 4³ | −72.1 | most soft q healed |
| 12³ | 60 | 4³ | **−29.3** | finite-q softness collapsed to Γ-noise scale |

Leftover ~−29 cm⁻¹ is mild Γ acoustic numerical noise (acoustic-like).
k is **no longer** the dominant lever for this cell. Soft-mode class stays
`likely_mesh_artefact`. Do **not** auto-promote to `stable` or EPW.

**Policy (nitride phonon screening / pilot):** electronic k min **8³**,
prefer **12³** for small / rock-salt binary cells (`n_atoms` ≤ 4). Global
`DFTConfig.kpoints` default stays `[4,4,4]` for generality. The next
family-wide default change waits on a cheap cross-check — optional NbN
ε=0 at the same settings (q=4³, k=12³, ecut=60) — not another ZrN k step.

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
- implementation-notes Slice 29.3; specs §2.3c
