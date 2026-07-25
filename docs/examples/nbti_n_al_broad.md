# Broader Nb–Ti–N AL campaign (Phase 1 loop + Phase 2 Si)

Workstation-scale composition × strain sweep that exercises:

1. Nitride enumeration (Nb/Ti/Zr/Hf binaries + Nb–Ti ternaries)
2. Formation-energy filter
3. λ/Tc family-heuristic surrogate
4. Active-learning prioritization (`max_epw_jobs: 6`)
5. Si-feasibility **v0.2** with **45° epitaxy** and **buffer** options

## Dry-run

```bash
siscforge run --dry-run examples/nbti_n_al_broad.yaml
```

Expect:

- **~40–50** candidates enumerated (7 bulks × 7 strains, capped at 60)
- Formation + surrogate filters keep a large pool
- An **acquisition table** ranking the pool
- **Top-6** selected for the expensive path (mock in dry-run)
- Deferred rows labeled `surrogate_only` in export
- Ranked CSV/JSON under `outputs/nbti_n_al_broad/`

## Si-score vs older cube-on-cube-only runs

| Scoring | Typical NbN-like total | Notes |
|---------|------------------------|--------|
| v0.1 cube-on-cube | ~53 | ~24% mismatch to Si(001) dominates |
| v0.2 auto + buffers | ~54+ with clearer notes | 45° (`a√2`) and/or TiN/AlN/ZrN/MgO buffer assumed |

The absolute total may not jump dramatically (other Si components still apply),
but **mismatch sub-scores and notes** correctly credit 45° / buffers. See
`examples/nbn_si_45deg.yaml` for a single-candidate comparison.

## Turning top-k into real EPW jobs

1. Run dry-run; open `outputs/nbti_n_al_broad/evaluations.csv` (or the AL
   acquisition table in the console).
2. Note the **top-k** candidate IDs (`al_selected_for_expensive` / high Acq).
3. For each selected structure (or a filtered shortlist YAML):
   - Set `calculators: [{name: qe-epw}]` and `dft.do_epw: true`
   - Point `dft.pseudo_dir` at SSSP (or equivalent) UPFs
   - Keep **screening** grids first (`quality_tag: screening`); see
     `docs/examples/nbN_epw.md` to densify later
4. Run:

```bash
export QE_BIN=$HOME/src/q-e-*/bin   # if needed
export PATH="$QE_BIN:$PATH"
siscforge run --calculator qe-epw examples/nbti_n_al_broad.yaml
```

Tip: reduce `enumeration.max_candidates` or restrict `formulas` /
`strain_values` to the shortlist so you do not re-enumerate the full grid
when only 6 EPW jobs are budgeted (`active_learning.max_epw_jobs: 6`).

5. Inspect `ElectronPhononResult` columns (λ, ω_log, Tc) and Si notes in
   synthesis cards. Soft modes on strained cells may inflate λ — treat
   screening Tc as order-of-magnitude.

## Related examples

| Example | Role |
|---------|------|
| `nbti_n_al.yaml` | Smaller 15-ish candidate AL toy |
| `nbn_epw.yaml` | Bulk NbN EPW golden path |
| `nbn_si_45deg.yaml` | Si v0.2 45°/buffer demo |
| `nbti_n_strain.yaml` | Strain series without AL |

## Out of scope here

- Surrogate retrain on new EPW labels
- Full multi-layer buffer stacks / membrane mechanics
- Anisotropic Eliashberg / production Wannier automation
