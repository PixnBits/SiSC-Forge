# From broad AL dry-run → real shortlist EPW (desktop)

Workstation-first path to **actionable nitride candidates**: pick top-k with
AL, then run real `qe-epw` only on those structures — with **resume** after
interrupt and **continue-on-error** so one crash does not kill the shortlist.

## Prerequisites

- Python env: `pip install -e ".[dev]"`
- For **real** EPW: QE with `pw.x` / `ph.x` / `epw.x`, Wannier90, UPF dir
  (see [docs/SETUP.md](../SETUP.md) Tier C). Prefer QE ≥ 7.2 if distro `ph.x` is broken.
- Time budget: **order of hours per candidate** on screening grids (16–32 cores);
  plan 1–2 candidates for a first overnight, up to ~6 for a full shortlist weekend.

## Step 1 — Broad AL dry-run (cheap)

```bash
siscforge run --dry-run examples/nbti_n_al_broad.yaml
```

- Enumerates ~49 Nb/Ti/Zr/Hf × strain points
- Formation filter + λ/Tc surrogate + Si-feasibility v0.2 (45° / buffers)
- AL selects **top-6** for the expensive path (mock stand-in)
- Store: `outputs/nbti_n_al_broad/`

Confirm ternaries with higher Si scores appear in the acquisition table
(e.g. Nb–Ti–N at compressive strain).

## Step 2 — Build the shortlist campaign

```bash
siscforge shortlist outputs/nbti_n_al_broad \
  -o examples/nbti_n_al_broad_shortlist.yaml \
  --name nbti_n_al_broad_shortlist \
  --max-jobs 6 \
  --nproc 8 \
  --pseudo-dir /usr/share/espresso/pseudo
```

This writes a focused YAML with:

- Exact ``candidate_specs`` (formula × strain × CIF from the dry-run)
- Screening EPW grids (`quality_tag: screening`)
- `run.resume: true` + `run.continue_on_error: true`
- Separate `output_dir` (does not overwrite the dry-run store)

Options: `--mode al_selected|top_acquisition|top_rank`, `--calculator qe-epw`.

## Step 3 — Smoke the shortlist (mock)

```bash
siscforge run --dry-run examples/nbti_n_al_broad_shortlist.yaml
```

Expect **6** expensive candidates only (not 49), checkpoint summary, ranked export.

## Step 4 — Real EPW on the desktop

```bash
export QE_BIN=$HOME/src/q-e-7.3.1/bin   # if private build
export PATH="$QE_BIN:$PATH"

# edit dft.pseudo_dir / dft.nproc in the shortlist YAML if needed
siscforge run --calculator qe-epw examples/nbti_n_al_broad_shortlist.yaml
```

### Resume after sleep / reboot / kill

Re-run the **same** command. Finished `status=ok` candidates are **skipped**;
`failed` are re-attempted. Dry-run `mock` rows in another store are **not**
treated as real EPW success.

```text
[1/6] Nb0.25Ti0.75N strain=-0.030 — skip (already ok)
[2/6] Nb0.5Ti0.5N strain=-0.020 — running qe-epw
…
Checkpoint summary (expensive path): skipped=1, ran=5, ok=3, failed=2
```

| Flag | Use |
|------|-----|
| `--force-rerun` | Recompute all successes |
| `--fail-fast` | Abort shortlist on first hard error |

Failed candidates: notes include workdir + `diagnose_epw_failure` hints; see
`outputs/.../qe_work/<formula>_<id>/`.

## What “success” looks like

| Field | Screening acceptance (nitrides) |
|-------|----------------------------------|
| λ | ~0.5 – 2.0 (soft modes can inflate) |
| ω_log | ~150 – 500 K |
| Tc (Allen–Dynes) | order-of-magnitude vs family (NbN ~8–25 K) |
| Si total (v0.2) | notes credit 45° and/or buffer |
| status | `ok` with `ElectronPhononResult` |
| Artifacts | `evaluations.csv`, `synthesis_cards.md`, ranked JSON |

Soft modes under coarse DFPT are common — treat Tc as **order-of-magnitude**,
not publication-grade, until denser grids / Wannier (see nbN_epw.md).

## Walltime (order-of-magnitude)

| Step | Screening, ~16 cores |
|------|----------------------|
| SCF + light relax | minutes |
| Multi-q DFPT (2³) | tens of minutes – few hours |
| EPW Wannier + a2F | tens of minutes – few hours |
| **Per candidate total** | **~1–6 h** (cell-dependent) |

8-atom ternary supercells are **much slower** than 2-atom bulk NbN — budget
overnight for a 6-candidate shortlist on a single workstation.

## Pseudos and `nbnd`

- Point `dft.pseudo_dir` at a **consistent** SSSP (or similar) set for Nb, Ti, N.
  Distro trees often lack Ti; see `pseudos/screening/README.md`.
- Ternary shortlists use **`nbnd: 64`** by default (binary NbN often uses 28).
  `pw.x` fails with `too few bands` if this is too low.
- Prefer a private **QE ≥ 7.3** build for DFPT+EPW if Ubuntu package `ph.x` is broken:

```bash
export PATH=$HOME/src/q-e-qe-7.3.1/bin:$PATH
export QE_BIN=$HOME/src/q-e-qe-7.3.1/bin
```

## Partial real-EPW exercise (this environment)

On 2026-07-25, one shortlist candidate (`Nb0.25Ti0.75N`, ε=−0.03) was launched
with QE 7.3.1 / 4 MPI ranks:

| Stage | Result |
|-------|--------|
| vc-relax | OK after `nbnd=64` (first try failed: too few bands) |
| SCF | OK (~−766 Ry total energy) |
| DFPT | In progress at ~18 min wall-time when the session timeout hit |
| EPW / λ / Tc | Not finished — re-run same command to resume after DFPT completes |

Store records failed only if the process dies mid-candidate **before**
`append_evaluation` (resume does not mid-step checkpoint QE files yet — workdir
artifacts remain under `qe_work/`). Full resume of a finished candidate is
evaluation-level; re-run continues the shortlist.

## Related

- Resume details: [implementation-notes Slice 13](../implementation-notes.md)
- Broad campaign: [nbti_n_al_broad.md](nbti_n_al_broad.md)
- NbN EPW grids: [nbN_epw.md](nbN_epw.md)
