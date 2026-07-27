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

### EPW parallel (nproc / npool) — desktop one-liner

Fine-grid EPW requires **`nproc == npool`** (nimage=1). If you set
`dft.nproc: 8` but leave `epw.npool: 1`, EPW aborts after DFPT with:

```text
Number of processes must be equal to product of number of pools and number of images
```

**Fix (or rely on auto-set):**

```yaml
dft:
  nproc: 8
  epw:
    npool: 8   # must equal nproc for fine-grid EPW
```

SiSC-Forge **auto-sets** `npool = nproc` when inconsistent and logs:

```text
EPW parallel: auto-set npool=8 to match nproc=8 (nimage=1)
```

Set `epw.strict_parallel: true` to refuse launch instead of auto-fix.

### Wannier screening defaults (supercells)

For 8-atom Nb–Ti–N with `dft.nbnd: 64`, do **not** leave `epw.nbndsub: 10`
with a wide frozen window — Wannier90 aborts with:

```text
dis_windows: More states in the frozen window than target WFs
```

SiSC-Forge screening defaults (`auto_nbndsub: true`):

- `nbndsub ≈ min(nbnd, max(16, 4×n_atoms, nbnd/2))` → **32** for 8 atoms / nbnd 64
- Tighter frozen window around E_F for `proj=random`
- **One automatic retry** if that error still appears (doubles nbndsub, capped by nbnd)

CLI failure lines now show the reason without opening `epw.out`:

```text
[1/6] Nb0.25Ti0.75N strain=-0.030 — failed (EPW Wannier: frozen window has more states than nbndsub)
```

Evaluation notes include workdir, output tail, and remediation. Production
still needs hand-tuned Wannier projections.

### Progress heartbeats (long DFPT)

While `ph.x` / `pw.x` / `epw.x` run, the CLI prints a heartbeat every
**15 minutes** by default (`run.heartbeat_seconds: 900`):

```text
  [heartbeat] phonon / DFPT (ph.x) +EPW-prep still running — elapsed 1h05m;
  healthy (log growing); log=4200 KiB; peek: iter #  12 total cpu time :  3800.1 secs
```

| Config / flag | Meaning |
|---------------|---------|
| `run.heartbeat_seconds: 900` | Interval in seconds (default) |
| `0` | Disable heartbeats |
| `--heartbeat-seconds 300` | CLI override (e.g. every 5 min) |

Healthy = subprocess alive and log file size/mtime increasing. Stale log with
a live process is flagged so you can check for a hang.

### Resume after sleep / reboot / kill

Re-run the **same** command. Finished `status=ok` candidates are **skipped**;
`failed` are re-attempted. Dry-run `mock` rows are **not** treated as real EPW
success when using `qe-epw`.

```text
[1/6] Nb0.25Ti0.75N strain=-0.030 — skip (already ok)
[2/6] Nb0.5Ti0.5N strain=-0.020 — running qe-epw
…
Checkpoint summary (expensive path): skipped=1, ran=5, ok=3, failed=2
```

**Kill during DFPT / EPW (mid-candidate):** re-issue the same command. Campaign
resume does not skip that candidate (no successful evaluation yet), but
**mid-step QE resume** reuses valid `qe_work/` artifacts:

```text
skip vc-relax (checkpoint)
skip SCF (checkpoint)
running DFPT / phonon    # restarts ph.x from step start, not mid-iteration
```

| Flag | Use |
|------|-----|
| `--force-rerun` | Recompute all successes **and** disable QE step checkpoints |
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
| **result_quality** | `screening` OK; `screening_suspect` / `unreliable` if λ inflated or unstable |
| Artifacts | `evaluations.csv`, `synthesis_cards.md`, ranked JSON |

Soft modes under coarse DFPT are common — treat Tc as **order-of-magnitude**,
not publication-grade, until denser grids / Wannier (see nbN_epw.md).

### Result quality (trust layer)

Screening runs often produce **λ ≫ 2** and high Allen–Dynes Tc from soft modes
and random Wannier. SiSC-Forge now **flags and down-weights** these:

| Tier | Typical trigger | Ranking |
|------|-----------------|---------|
| `screening` | λ &lt; 3, stable phonon | normal weights |
| `screening_suspect` | λ ≥ 3 or soft modes | composite × 0.45; Perf shows `*` |
| `unreliable` | λ ≥ 8 or imaginary modes | Tc term dropped; composite × 0.15; Perf `!!` |

CLI example:

```text
#  Formula          Perf   Qual    Si   Composite  Stable
1  NbN              16.2   scr     54   48.1       yes
2  Nb0.5Ti0.5N      45.0*  susp λ  56   22.3       yes    # high λ, down-weighted
```

CSV/JSON include `result_quality`, `quality_flags`, `quality_notes`. Synthesis
cards warn not to cite suspect/unreliable Tc as production predictions.

This is a **trust layer**, not a denser-grid refine path. Next step for
citation-quality Tc: re-run winners with production grids / tuned Wannier.

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

If the process dies mid-candidate, re-run the same command: campaign resume
does not skip that candidate (no successful evaluation yet), but **mid-step
QE resume** reuses completed relax/SCF in `qe_work/` and restarts the
incomplete step (e.g. DFPT) only.

## Related

- Resume details: [implementation-notes Slice 13](../implementation-notes.md)
- Broad campaign: [nbti_n_al_broad.md](nbti_n_al_broad.md)
- NbN EPW grids: [nbN_epw.md](nbN_epw.md)
