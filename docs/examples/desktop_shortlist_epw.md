# From broad AL dry-run → real shortlist EPW (desktop)

Workstation-first path to **actionable nitride candidates**: pick top-k with
AL, then run real `qe-epw` only on those structures — with **resume** after
interrupt and **continue-on-error** so one crash does not kill the shortlist.

## Alternative: phonon-first two-machine loop

When screening EPW shortlists return only unstable / unreliable cells, map
stability first (no EPW), then shortlist survivors:

```bash
# Machine 2 — broad phonon map (do_epw: false)
siscforge run --dry-run examples/nbti_n_phonon_map.yaml
siscforge run --calculator qe examples/nbti_n_phonon_map.yaml

# Build EPW campaign only from dynamically stable cells
siscforge shortlist outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_map_epw.yaml \
  --mode stable_only --max-jobs 6 --nproc 8

# Machine 1/2 — EPW on survivors
siscforge run --calculator qe-epw examples/nbti_n_phonon_map_epw.yaml
```

Full walkthrough: [nbti_n_phonon_map.md](nbti_n_phonon_map.md). Modes:
`stable_only` | `stable_or_soft` (see `siscforge shortlist --help`).

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

### EPW coarse k auto-bump (Wannier safety)

After multi-day DFPT, EPW can die in seconds with:

```text
kmesh_get_bvector: Not enough bvectors found
```

Typical cause: refine / `workstation_dense` used **nkc=6** on an 8-atom
supercell. SiSC-Forge now:

1. **Preflight** (before DFPT): raise coarse k to ≥ **8³** for production /
   workstation_dense on ≥8-atom cells; log
   `EPW coarse k raised to 8×8×8 (Wannier safety; was 6×6×6; nq unchanged to match DFPT)`.
2. **Post-DFPT Phase A**: on that error, **EPW-only** retry with denser nkc
   (6→8→12, max 2) — **DFPT / phonon is never deleted or redone**.
3. **Post-DFPT Phase B** (Slice 27): if still `kmesh_get_bvector` after the
   nk ladder (e.g. already failed at nk=8 and nk=12 with matching NSCF),
   retry **EPW-only** with Wannier90 `search_shells` **36→48** (same nkc,
   NSCF reused). Still **not** a guarantee — pathological cells may need
   hand-tuned projections (out of auto scope).
4. Resume without `--force-rerun` reuses finished phonon and applies remediation
   instead of replaying the same broken `epw.in`.
5. **Stale NSCF after nkc raise** (Slice 26): if an existing `nscf.out` was run
   at 6³ but campaign/epw now wants 8³, resume **invalidates NSCF+EPW only**
   and rebuilds electronic steps. You should **not** manually
   `rm nscf.out epw.out epw.in`. CLI:

```text
nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF (phonon reused)
```

`nqc` always stays equal to the DFPT q-mesh. Auto-nk / auto-`search_shells`
does **not** guarantee physical λ/Tc; material-specific Wannier projections
remain out of scope.

```text
EPW failed (kmesh_get_bvector @ nk=6) — retrying EPW-only with nk=8 (DFPT reused)
EPW failed (kmesh_get_bvector @ nk=12) — nk ladder exhausted; retrying EPW-only with search_shells=36 (DFPT reused)
```

### Progress heartbeats (long DFPT)

While `ph.x` / `pw.x` / `epw.x` run, the CLI prints a heartbeat every
**15 minutes** by default (`run.heartbeat_seconds: 900`):

```text
  [heartbeat] phonon / DFPT (ph.x) +EPW-prep still running — elapsed 1h05m;
  healthy (log growing); log=4200 KiB; peek: iter #  12 total cpu time :  3800.1 secs
```

When DFPT progress is parseable (q-point index / total), a rough remaining-time
band may be appended; see [Walltime](#walltime-order-of-magnitude) below.

| Config / flag | Meaning |
|---------------|---------|
| `run.heartbeat_seconds: 900` | Interval in seconds (default) |
| `0` | Disable heartbeats |
| `--heartbeat-seconds 300` | CLI override (e.g. every 5 min) |
| `run.heartbeat_eta: true` | Allow remaining-time when progress is real |

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
**mid-step QE resume** reuses valid `qe_work/` artifacts. Incomplete DFPT with
promising dyn / `_ph0` / dvscf files is re-launched with QE **`recover=.true.`**
instead of discarding multi-hour progress; if recover is unsafe or hard-fails,
SiSC-Forge falls back to a clean full phonon step.

```text
skip vc-relax (checkpoint)
skip SCF (checkpoint)
resuming DFPT with QE recover=.true.   # preferred when dyn/_ph0 look good
# or:
# DFPT recover failed or unsafe — full phonon step restart
# running DFPT / phonon
```

**Pause/resume model:** interrupt is safe at the process level (Ctrl+C, sleep,
power loss). Resume = same `siscforge run` command. Granularity is **campaign
candidate + QE step + QE-native DFPT recover** — not Folding@home-style
arbitrary mid-iteration checkpoints. Incomplete `epw.x` still restarts that
step from its beginning (no fragile EPW recover).

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

This is a **trust layer**, not denser-grid refinement by itself.

### From unreliable shortlist → refine tier

When the screening shortlist finishes (often all `unreliable` / high λ):

```bash
# 1) Optionally re-rank the store with trust weights (already applied on run)
siscforge rank outputs/nbti_n_al_broad_shortlist

# 2) Promote top Si-feasibility winners (desktop: 2 jobs)
siscforge refine outputs/nbti_n_al_broad_shortlist \
  -o examples/nbti_n_al_refine.yaml \
  --mode top_si \
  --max-jobs 2 \
  --tier workstation_dense \
  --nproc 16 \
  --pseudo-dir pseudos/screening \
  --name nbti_n_al_refine

# 3) Smoke + real EPW (resume-safe, separate output_dir)
siscforge run --dry-run examples/nbti_n_al_refine.yaml
siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
```

| Mode | Selection |
|------|-----------|
| `top_si` | Highest Si score among ok EPW rows (default) |
| `top_rank` | Trust-weighted rank order |
| `ids` | Explicit `--id` list |

Refine YAML uses denser k/q/nkf than screening, `quality_tag: production`,
`epw.npool = nproc`, coarse k ≥ **8³** (Wannier-safe), and exact CIF×strain
specs. **Do not cite Tc** until `result_quality` improves. **EPW coarse k
auto-bump; DFPT never redone on Wannier k-mesh failure.** Limitation: random
Wannier may remain until material-specific projections are added; auto-nk does
not guarantee physical λ/Tc.

## Walltime (order-of-magnitude)

Before the first long step, real `qe` / `qe-epw` campaigns print a **heuristic
walltime band** (not a guarantee). Machine load and soft-mode convergence
dominate; treat bands as planning guidance only. Dense-k / dense-q jobs that
still carry `quality_tag: screening` (phonon-only maps, k-mesh diagnostics)
can still be **multi-hour** on a workstation — the printed `q-mesh=` is the
mesh actually used for DFPT, not an unused EPW default.

### How to read the bands

```text
Estimated walltime (heuristic, not a guarantee):
  per candidate: DFPT ~47 min – 6.3 h; full candidate (relax→EPW) ~1.6–9.4 h on ~8 cores (order-of-magnitude)
  this campaign (~6 candidates, sequential): ~9.4 h – 2.4 d
  Tip: safe to interrupt; re-run the same command to resume finished steps/candidates.
  tier=screening, n_atoms≈8, q-mesh=8 pts, k-mesh=64 pts, nproc=8, nkf=216
```

| Phrase | Meaning |
|--------|---------|
| DFPT band | Multi-q `ph.x` only (usually the long pole) |
| full candidate | relax → SCF → DFPT → EPW (when enabled) |
| this campaign | Sequential sum on one desktop (`N ×` full band) |
| order-of-magnitude | Wide by design — not an HPC scheduler ETA |

**Screening shortlist (6 candidates, ~8 cores):** often overnight → weekend.

**workstation_dense refine (2 candidates, 16 cores):** often multi-day. A real
8-atom Nb–Ti–N refine DFPT has exceeded **37 h** with healthy heartbeats —
the dense band (~12 h – 2 d DFPT) is intentionally wide enough to cover that.

| Step | Screening, ~16 cores | workstation_dense refine |
|------|----------------------|--------------------------|
| SCF + light relax | minutes | tens of minutes |
| Multi-q DFPT | tens of min – few hours (2³) | many hours – ~2 days (4³) |
| EPW Wannier + a2F | tens of min – few hours | hours |
| **Per candidate total** | **~1–6 h** (cell-dependent) | **~1–3 days** |

8-atom ternary supercells are **much slower** than 2-atom bulk NbN.

### Heartbeats with optional remaining-time

When `ph.out` reports real q-point progress, heartbeats may append a rough
remaining band; otherwise the heartbeat is unchanged (no invented precision).

```text
  [heartbeat] phonon / DFPT (ph.x) +EPW-prep still running — elapsed 12h05m;
  healthy (log growing); …; progress q 3/8; ~X–Y remaining (rough)
```

### Knobs

| Config / flag | Default | Meaning |
|---------------|---------|---------|
| `run.estimate_walltime` | true | Print bands at campaign start |
| `run.walltime_scale` | 1.0 | Stretch/shrink bands |
| `run.heartbeat_eta` | true | Remaining-time when progress is real |
| `run.heartbeat_seconds` | 900 | Heartbeat interval (0 = off) |

After a candidate finishes, observed walltime may adjust messaging for later
candidates **in the same process** (simple in-memory tracker).

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
