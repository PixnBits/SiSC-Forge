# Phonon-first stability map → stable-only EPW shortlist

**Two-machine discovery loop** for Nb–Ti–(Zr)–N when screening EPW shortlists
return only `result_quality=unreliable` (imaginary modes + inflated λ).

Dense refine (e.g. 4³ q-mesh) on top-Si **unstable** cells is multi-day DFPT
with poor ROI. Instead:

| Machine | Job | Cost |
|---------|-----|------|
| **2** (this example) | Broad composition × strain **phonon map** (`do_epw: false`) | Screening DFPT 2³ q — cheaper than EPW |
| **1 or 2** | `shortlist --mode stable_only` → **EPW only on survivors** | Screening EPW on a handful of cells |

## Prerequisites

- `pip install -e ".[dev]"`
- Real map: QE with `pw.x` / `ph.x` (EPW **not** required for the map), UPF dir
- See [docs/SETUP.md](../SETUP.md)

## Step 1 — Dry-run the phonon map

```bash
siscforge run --dry-run examples/nbti_n_phonon_map.yaml
```

Expect:

- Broad Nb/Ti/Zr binaries + Nb–Ti ternaries × fine strain (−4% … +2%)
- Formation filter on; **AL off** (every kept candidate gets phonon)
- Mock phonon fills `dynamically_stable` / `min_frequency_cm1`
- Rank table **Stable** / **min ω** columns; store under `outputs/nbti_n_phonon_map/`
- Console line: **Phonon-only campaign** (no EPW npool / Wannier preflight)

## Step 2 — Real phonon map (machine 2)

```bash
# edit dft.pseudo_dir / dft.nproc in the YAML for your box
siscforge run --calculator qe examples/nbti_n_phonon_map.yaml
```

Resume-safe: re-run the same command after sleep/kill. Mid-step DFPT may use
QE `recover=.true.` (see [desktop_shortlist_epw.md](desktop_shortlist_epw.md)).

**Grids stay screening** (`qpoints: [2,2,2]`). Do **not** turn this YAML into
a refine-tier 4³ campaign — densify only on stable EPW survivors later.

## Step 3 — Inspect stability

```bash
siscforge rank outputs/nbti_n_phonon_map --stable-first
# or open outputs/nbti_n_phonon_map/evaluations.csv
# columns: dynamically_stable, has_imaginary_modes, min_frequency_cm1, result_quality
```

- Imaginary modes → trust layer `unreliable` even without EPW
- Soft modes (low positive min ω) → often `soft_modes` / screening flags

## Step 4 — Stable-only EPW shortlist

```bash
siscforge shortlist outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_map_epw.yaml \
  --name nbti_n_phonon_map_epw \
  --mode stable_only \
  --max-jobs 6 \
  --nproc 8 \
  --pseudo-dir /usr/share/espresso/pseudo
```

- **Only** `status=ok|mock` rows with `phonon.dynamically_stable` (no imag modes)
- Sorted by **highest Si-feasibility** among survivors (`--stable-sort si`)
- If **none** stable: CLI exits with a clear error — **no** silent fall-back to unstable top-k — and prints the **soft-mode report** plus the `siscforge pilot` command (see [When none are stable](#when-none-are-stable-coarse-q-recovery) below)

Nearly stable (soft but non-imaginary, or tiny numeric imag):

```bash
siscforge shortlist outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_map_epw.yaml \
  --mode stable_or_soft --soft-min-cm1 0
# optional: --soft-min-cm1 -5 for mild acoustic noise only
```

## Step 5 — EPW on survivors (machine 1/2)

```bash
siscforge run --dry-run examples/nbti_n_phonon_map_epw.yaml   # mock smoke
siscforge run --calculator qe-epw examples/nbti_n_phonon_map_epw.yaml
```

Then rank/export and check `result_quality` before citing Tc. Unreliable
screening λ on *stable* cells may still need `siscforge refine` (denser grids).

## When none are stable (coarse-q recovery)

A real Nb–Ti–N q=2³ map can finish with **zero** `dynamically_stable`
survivors. Known-stable binaries (NbN, TiN, ZrN) going large-imaginary on
that mesh is a **mesh-artefact suspect**, not automatic abandonment of the
family. `stable_only` staying empty is correct. The product must not send
those cells to EPW.

```bash
# Campaign-level characterisation (also written at the end of a phonon run)
siscforge soft-modes outputs/nbti_n_phonon_map
# → outputs/nbti_n_phonon_map/soft_mode_report.json
# → outputs/nbti_n_phonon_map/soft_mode_report.md

# Ready-to-run denser-q pilot (binaries first, still do_epw: false)
siscforge pilot outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_pilot_q3.yaml \
  --mode binaries --qpoints 3,3,3 --nproc 16

# Or the least-soft N cells (ternaries included)
siscforge pilot outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_pilot_q3.yaml \
  --mode least_soft -n 4 --qpoints 3,3,3

siscforge run --dry-run examples/nbti_n_phonon_pilot_q3.yaml
siscforge run --calculator qe examples/nbti_n_phonon_pilot_q3.yaml
```

The pilot **reuses** `candidate_specs` from the map store (no full-grid
re-enumeration), copies `pseudo_dir` / `nproc`, writes a **new**
`output_dir`, and keeps `do_epw: false`. Resume-safe.

This is still a **gate**. The operator decides expand vs abandon. Do not
cite dynamical stability from q=3³ either; production proof needs a
denser, analysed DFPT on the cells you keep.

PRD US10 / Specs §2.3c / implementation-notes Slice 29.

## Why not AL → EPW first?

The prior 6-candidate Nb–Ti–N screening EPW shortlist finished with **all**
`result_quality=unreliable` (imaginary modes + high/extreme λ). Trust layer
correctly down-ranked them. Phonon-first spends desktop hours on stability
coverage; EPW budget goes only to cells that can host meaningful e-ph.


## Phonon failures (setup vs instability)

### FFT grid / symmetry (`phq_setup`) — ordered ternaries

Ordered NbₓTi₁₋ₓN supercells (e.g. 0.25 / 0.5 / 0.75 at ε=0) often abort
`ph.x` in ~2 s with:

```text
Error in routine phq_setup (1):
  FFT grid incompatible with symmetry
```

CLI / notes must show a **phonon** reason (Slice 28), **not** EPW k-grid:

```text
… — failed (phonon: FFT grid incompatible with symmetry (phq_setup))
phonon failed (FFT grid incompatible with symmetry) — retrying once with nosym+noinv SCF/PH
```

`dft.phonon_retry_on_fft_symmetry: true` (default) re-runs **one** SCF with
`nosym=.true.` / `noinv=.true.` and retries phonon once. Success notes that
recovery was used. Final failure is a **setup** failure — not “dynamically
unstable” — and `stable_only` shortlist ignores it.

### d_matrix / Errno 36

If `ph.x` aborts with:

```text
Error in routine d_matrix (2):
  D_S (l=2) for this symmetry operation is not orthogonal
```

SiSC-Forge classifies this as:

```text
… — failed (phonon: d_matrix — D_S symmetry not orthogonal)
```

**Not** `[Errno 36] File name too long` (parser bug fixed in Slice 24).
**Not** `EPW: k-grid inconsistency` (mislabel fixed in Slice 28).

### Automatic retry (default)

Both setup classes share a one-shot nosym recovery:

| Flag | Default | Fingerprint |
|------|---------|-------------|
| `dft.phonon_retry_on_fft_symmetry` | true | FFT grid incompatible with symmetry |
| `dft.phonon_retry_on_d_matrix` | true | d_matrix / D_S not orthogonal |

If retry still fails, the candidate is recorded failed and the campaign
continues (`run.continue_on_error: true`).

Disable in the map YAML:

```yaml
dft:
  phonon_retry_on_fft_symmetry: false
  phonon_retry_on_d_matrix: false
```

### Re-run only failed candidates on an existing map store

```bash
# Same YAML + same output_dir (e.g. outputs/nbti_n_phonon_map)
# Successful phonons are skipped (resume); failed cells re-run with diagnose/retry
siscforge run --calculator qe examples/nbti_n_phonon_map.yaml
```

Do **not** pass `--force-rerun` unless you intentionally want to redo **all**
DFPT (including the ~1–3 min binary successes).

### Manual remediation if retry fails

- Tighten vc-relax (forces / pressure) before DFPT
- Try a nearby strain step (soft modes / PAW symmetry can be strain-sensitive)
- Inspect lattice noise in the relaxed CIF (near-zero components)
- Coarse 2³ maps can still mis-label stability — survivors need denser checks later

## Limitations

- Coarse 2³ DFPT can **mis-label** stability (false stable / false imag)
- Soft-mode classes are **heuristic**; `likely_mesh_artefact` is not a
  stability certificate
- A denser-q pilot is still a gate, not production dynamical-stability proof
- No SQS disorder, no DMFT, no JJ metrics in this path
- Mock dry-run still invents phonon stability (~15% imag) — real QE is the map
- Production dynamical stability still needs denser q and careful analysis
- The pilot **must not** auto-launch EPW on soft cells

## Related

- [desktop_shortlist_epw.md](desktop_shortlist_epw.md) — AL shortlist → EPW → refine
- [nbti_n_al_broad.md](nbti_n_al_broad.md) — broader AL dry-run (EPW-oriented)
- Example YAML: `examples/nbti_n_phonon_map.yaml`
- Specs §2.3c; PRD US10; implementation-notes Slice 29
- [zrn_nitride_phonon_convergence.md](zrn_nitride_phonon_convergence.md) — Γ vs finite-q / k-ladder before another q-pilot
