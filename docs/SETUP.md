# SiSC-Forge setup guide

This document is the **canonical install and run guide** for local workstations.  
It covers four tiers:

| Tier | What you get | External binaries |
|------|----------------|-------------------|
| **A — Python only** | Full dry-run / mock path, structure gen, Si-score, ranking, tests | None |
| **B — QE phonon** | Real relax / SCF / phonon (`qe` calculator) | `pw.x`, `ph.x` + UPFs |
| **C — QE + EPW** | Real electron-phonon + Tc (`qe-epw` calculator) — **Phase 1 scientific gate** | `pw.x`, `ph.x`, `epw.x`, `wannier90.x` + UPFs |
| **D — optional DMFT** | Parse a drop-in solid_dmft `observables.json` into `DMFTResult` | Optional TRIQS / solid_dmft (never a hard dep) |

On a machine **without** Quantum ESPRESSO (the default for many laptops/CI), stop after **Tier A**.  
The Phase 1 scientific gate (bulk NbN λ and Tc) requires **Tier C**.  
Phase 3 unconventional work is usable at **Tier A** via the mock DMFT path;
Tier D is only for operators who already run TRIQS / solid_dmft elsewhere.

---

## Prerequisites (all tiers)

- **Python ≥ 3.11**
- Git
- Network access to install PyPI packages
- Recommended: [`uv`](https://github.com/astral-sh/uv) or a standard `venv` + `pip`

Clone (or use your existing checkout):

```bash
git clone https://github.com/PixnBits/SiSC-Forge.git
cd SiSC-Forge
# or: cd /path/to/your/checkout   e.g. .../projects/SiSC-Forge/main
```

---

## Tier A — Python environment (required on every machine)

### A.1 Create a virtualenv and install SiSC-Forge

**With uv (recommended):**

```bash
# Install uv if needed: https://docs.astral.sh/uv/getting-started/installation/
uv venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

**With pip:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Optional extras:

```bash
# jobflow (optional; QE recipes also run without a job store)
uv pip install -e ".[qe]"
# or: pip install -e ".[qe]"

# phonopy finite-displacement path (optional)
uv pip install -e ".[phonopy]"
```

### A.2 Verify the install

```bash
siscforge --version
pytest -q
```

Expect tests to pass without QE (real QE/EPW tests are skipped unless env vars are set).

### A.3 Dry-run campaigns (no DFT)

```bash
# Nb–Ti–N strain series (Phase 0 end-to-end)
siscforge run --dry-run examples/nbti_n_strain.yaml

# NbN EPW campaign with mock λ / Tc (Phase 1 fields, no epw.x)
siscforge run --dry-run examples/nbn_epw.yaml

# MgB₂ golden EPW campaign (mock λ / Tc)
siscforge run --dry-run examples/mgb2_epw.yaml

# Nb–Ti–N with λ/Tc surrogate stub pre-filter (still mock calculator)
siscforge run --dry-run examples/nbti_n_surrogate.yaml

# Nb–Ti–N with active-learning top-k prioritization (mock = expensive path)
siscforge run --dry-run examples/nbti_n_al.yaml

# Structure enumeration only
siscforge enumerate -c examples/nbti_n_strain.yaml
```

Outputs land under `outputs/<campaign_name>/` (JSON, CSV, synthesis cards).

**This is all you need for development, CI, and ranking logic.**  
`--dry-run` always forces the mock calculator and never requires QE/EPW.

### A.4 Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs this same **Tier A** path on
every pull request and on pushes to `main`: `pip install -e ".[dev]"` →
`pytest -q` → `siscforge run --dry-run examples/dummy_campaign.yaml`.

**Local-only (not in CI):** real Quantum ESPRESSO, EPW, Wannier90, TRIQS /
solid_dmft / CTHYB, and any test gated on `SISCFORGE_RUN_QE` /
`SISCFORGE_RUN_EPW`. Those stay skipped unless the env vars are set on a
workstation that has the binaries.

---

## Tier B + C — Quantum ESPRESSO, EPW, and pseudos

You need:

| Binary | Role | Tier |
|--------|------|------|
| `pw.x` | SCF / relax | B, C |
| `ph.x` | DFPT phonon | B, C |
| `epw.x` | Electron-phonon Wannier | C |
| `wannier90.x` | Wannierization | C |
| UPF files | Pseudopotentials (Nb, N, …) | B, C |

**When to install:** only if you want **real** DFT / phonon / EPW numbers (NbN scientific gate, MgB₂ golden, production campaigns). Skip if you are only developing Python/CLI logic — Tier A is enough.

**Machine note:** a high-core workstation (e.g. 16–32 cores, ≥64 GB RAM) is ideal. Screening NbN phonon is minutes–hours; full EPW is longer.

---

### Ubuntu / Debian packages (recommended on this project’s host)

Ubuntu ships **QE 6.7** with `pw.x`, `ph.x`, **and** `epw.x`, plus an **SSSP** data package and **Wannier90**.  
SiSC-Forge docs prefer QE ≥ 7.2 for production EPW, but **6.7 is fine for a first screening gate** (order-of-magnitude λ / Tc). Upgrade later if you need newer EPW features.

```bash
# One shot: Tier B + C binaries and SSSP UPFs (~70–150 MB download)
sudo apt-get update
sudo apt-get install -y \
  quantum-espresso \
  quantum-espresso-data-sssp \
  wannier90 \
  openmpi-bin
```

Verify:

```bash
which pw.x ph.x epw.x wannier90.x
pw.x -h 2>&1 | head -3
ls /usr/share/espresso/pseudo/Nb*.UPF /usr/share/espresso/pseudo/N*.UPF
```

SSSP files live at:

```text
/usr/share/espresso/pseudo/
  Nb.pbe-spn-kjpaw_psl.0.3.0.UPF
  N.pbe-n-radius_5.UPF
  ...
```

Optional env for pytest / convenience:

```bash
export SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo
# binaries are already on PATH via /usr/bin — no QE_BIN needed
```

### Other install options

1. **Source / official build** — [Quantum ESPRESSO](https://www.quantum-espresso.org/) ≥ 7.2 (best long-term for production EPW).
2. **Conda-forge** (when packages exist for your platform): `conda install -c conda-forge qe`.
3. **HPC modules**: `module load quantum-espresso` (site-specific).

If binaries are not on `PATH`:

```bash
export QE_BIN=/path/to/qe/bin
# also accepted: QUANTUM_ESPRESSO_BIN
```

### Configure campaigns to use system SSSP

Edit YAML (or copy examples and patch `pseudo_dir`):

```yaml
# examples/nbn_phonon_qe.yaml  and/or  examples/nbn_epw.yaml
dft:
  pseudo_dir: /usr/share/espresso/pseudo
  # optional explicit map if auto-match ever fails:
  # pseudopotentials:
  #   Nb: Nb.pbe-spn-kjpaw_psl.0.3.0.UPF
  #   N:  N.pbe-n-radius_5.UPF
```

### B.3 Run real phonon only (Tier B)

```bash
source .venv/bin/activate
# ensure dft.pseudo_dir is set in the YAML
siscforge run --calculator qe examples/nbn_phonon_qe.yaml
```

Details: [docs/examples/nbN_phonon_qe.md](examples/nbN_phonon_qe.md).

### C.3 Run the Phase 1 scientific gate (Tier C — bulk NbN EPW)

```bash
source .venv/bin/activate
export SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo

siscforge run --calculator qe-epw examples/nbn_epw.yaml
```

**Order-of-magnitude success (screening):**

| Quantity | Expected range |
|----------|----------------|
| λ | ~0.5 – 2.0 |
| ω_log | ~150 – 500 K |
| Tc | ~8 – 25 K (exp. bulk NbN ~16 K) |

Results: `outputs/nbn_epw_screening/evaluations.json`  
(`electron_phonon.lambda_total`, `Tc_allen_dynes` / `Tc_eliashberg`, `performance_score`).

Optional pytest gate:

```bash
export SISCFORGE_RUN_EPW=1
export SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo
pytest tests/test_epw.py -k real_epw -v
```

Details: [docs/examples/nbN_epw.md](examples/nbN_epw.md).

### C.4 If EPW is requested but missing

```text
QE not available:
...
epw.x not found; EPW / Eliashberg steps require Quantum ESPRESSO EPW.
```

Exit code **3**. There is **no** silent fallback to mock when you pass `--calculator qe-epw`.  
Use `--dry-run` for mock.

### C.5 Reality check on first EPW / phonon runs

- **Always launch via mpirun**: Ubuntu’s `pw.x` / `ph.x` / `epw.x` are OpenMPI-linked. Starting them bare (without `mpirun`) often **hangs with empty output**. SiSC-Forge wraps all QE calls with `mpirun --oversubscribe -np N` when `mpirun` is on `PATH` (default `N = dft.nproc`, minimum 1).
- **Ubuntu 6.7 `ph.x` buffer overflow**: on Ubuntu 26.04 packages, `ph.x` can abort with  
  `*** buffer overflow detected ***` immediately while reading `*.save/data-file-schema.xml`,  
  even for short paths and simple cells (Si, NbN). **`pw.x` still works.**  
  - **Phonon stability workaround:** `dft.phonon_method: phonopy_fd` + `uv pip install phonopy`  
    (finite differences via `pw.x` only; default in `examples/nbn_phonon_qe.yaml`).  
  - **True DFPT + EPW:** build **Quantum ESPRESSO ≥ 7.2** from source (or use a non-broken package) and put that `bin/` on `PATH` / `QE_BIN`. Distro EPW still needs a working DFPT dynamical matrix from a healthy `ph.x`.
- First real NbN EPW may take a long wall-time even at screening grids; use a few MPI ranks (e.g. `dft.nproc: 4`–`16` on a workstation).
- Ubuntu QE **6.7** EPW inputs can differ slightly from 7.x; if `epw.x` rejects a keyword, treat the SiSC-Forge `epw.in` as a template and adjust (parser still accepts standard λ / ω_log / Tc lines).
- Full production Wannier projections for metals are non-trivial; a failed first EPW is common — inspect `outputs/.../qe_work/**/epw.out`.

If a previous run hung, stop it (`Ctrl+C`) and kill stragglers before retrying:

```bash
pkill -f 'pw.x|ph.x|epw.x'   # only if you are sure no other QE jobs matter
```

### C.5b Resume multi-candidate EPW after interrupt

Long shortlists (e.g. AL top-k with `qe-epw`) checkpoint after **each** expensive
candidate into the campaign `output_dir` (`evaluations.json`). Re-launch with the
**same YAML and output directory**:

```bash
siscforge run --calculator qe-epw examples/nbti_n_al_broad.yaml
# reboot / sleep / kill …
siscforge run --calculator qe-epw examples/nbti_n_al_broad.yaml
# → skips finished ok/mock; re-attempts failed; continues past single failures
```

| Flag / YAML | Meaning |
|-------------|---------|
| `run.resume: true` (default) | Skip successful store hits |
| `run.continue_on_error: true` (default) | Do not abort whole shortlist on one crash |
| `--force-rerun` | Recompute even successful candidates |
| `--fail-fast` | Abort on first failure |

Matching: `candidate_id`, else fingerprint `family|formula|substrate|strain`.
Details: [implementation-notes Slice 13](implementation-notes.md) and
[nbti_n_al_broad walkthrough](examples/nbti_n_al_broad.md).

### C.6 Build QE ≥ 7.2 from source (working `ph.x` + EPW)

Ubuntu’s packaged `ph.x` (6.7) can abort with a fortify buffer overflow. Build a
**private** QE ≥ 7.2 tree and put its `bin/` **before** `/usr/bin` on `PATH`.

Full copy-paste procedure: see the “Build a working `ph.x`” section below
(same content as the step-by-step used on the Ryzen workstation).

#### Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential gfortran \
  libblas-dev liblapack-dev libfftw3-dev \
  libopenmpi-dev openmpi-bin \
  wget tar
```

#### Download and compile (QE 7.3.1 — stable `./configure` path)

```bash
mkdir -p "$HOME/src" && cd "$HOME/src"
wget -O q-e-qe-7.3.1.tar.bz2 \
  https://gitlab.com/QEF/q-e/-/archive/qe-7.3.1/q-e-qe-7.3.1.tar.bz2
tar xf q-e-qe-7.3.1.tar.bz2
cd q-e-qe-7.3.1

./configure --enable-parallel --with-scalapack=no
make -j"$(nproc)" pw ph pp epw
```

Expect ~10–30 minutes on a modern desktop. Binaries land in `./bin/`.

#### Put the new build first on PATH

```bash
# Session only
export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"

# Verify you are NOT still using /usr/bin
which -a pw.x ph.x epw.x
# first line of each should be .../q-e-qe-7.3.1/bin/...
pw.x -v 2>&1 | head -5
```

Persist for future shells (optional):

```bash
echo 'export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"' >> ~/.bashrc
echo 'export PATH="$QE_BIN:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

#### Smoke-test `ph.x` (must show JOB DONE, no buffer overflow)

```bash
WORKDIR=/tmp/qe73_ph_smoke
rm -rf "$WORKDIR" && mkdir -p "$WORKDIR/out" && cd "$WORKDIR"
PSEUDO=/usr/share/espresso/pseudo

cat > scf.in <<EOF
&CONTROL
  calculation='scf', outdir='$WORKDIR/out', prefix='x',
  pseudo_dir='$PSEUDO'
/
&SYSTEM
  ibrav=0, nat=2, ntyp=2, ecutwfc=40, ecutrho=320,
  occupations='smearing', smearing='mv', degauss=0.02
/
&ELECTRONS
  conv_thr=1.0d-8
/
ATOMIC_SPECIES
 Nb 92.9 Nb.pbe-spn-kjpaw_psl.0.3.0.UPF
 N  14.0 N.pbe-n-radius_5.UPF
ATOMIC_POSITIONS crystal
 Nb 0.0 0.0 0.0
 N  0.5 0.5 0.5
K_POINTS automatic
  2 2 2 0 0 0
CELL_PARAMETERS angstrom
  4.392 0 0
  0 4.392 0
  0 0 4.392
EOF

mpirun --oversubscribe -np 1 pw.x -in scf.in < /dev/null > scf.out 2>&1
grep 'JOB DONE' scf.out

cat > ph.in <<EOF
&inputph
  tr2_ph=1.0d-12, prefix='x', outdir='$WORKDIR/out',
  fildyn='x.dyn', ldisp=.false.
/
0.0 0.0 0.0
EOF

mpirun --oversubscribe -np 1 ph.x -in ph.in < /dev/null > ph.out 2>&1
grep -E 'JOB DONE|buffer overflow' ph.out
# Expect: JOB DONE   and no "buffer overflow"
```

#### Re-run SiSC-Forge with DFPT (not phonopy_fd)

```bash
cd ~/projects/SiSC-Forge/main
source .venv/bin/activate
export QE_BIN="$HOME/src/q-e-qe-7.3.1/bin"
export PATH="$QE_BIN:$PATH"
export SISCFORGE_PSEUDO_DIR=/usr/share/espresso/pseudo

# Use gamma/dfpt, not phonopy_fd, for EPW readiness:
# in examples/nbn_phonon_qe.yaml and examples/nbn_epw.yaml set:
#   phonon_method: gamma

siscforge run --calculator qe examples/nbn_phonon_qe.yaml
siscforge run --calculator qe-epw examples/nbn_epw.yaml
```

---

## Environment variables (summary)

| Variable | Purpose |
|----------|---------|
| `QE_BIN` / `QUANTUM_ESPRESSO_BIN` | Directory containing `pw.x`, `ph.x`, `epw.x` |
| `SISCFORGE_PSEUDO_DIR` | UPF directory (pytest real-QE/EPW helpers) |
| `SISCFORGE_RUN_QE=1` | Enable optional real QE phonon pytest |
| `SISCFORGE_RUN_EPW=1` | Enable optional real EPW pytest |
| `SISCFORGE_QE_NPROC` | MPI ranks for optional real QE tests |

---

## Quick reference — which command when?

| Goal | Command |
|------|---------|
| Unit tests | `pytest -q` |
| Dry-run nitride campaign | `siscforge run --dry-run examples/nbti_n_strain.yaml` |
| Dry-run NbN e-ph fields | `siscforge run --dry-run examples/nbn_epw.yaml` |
| Real phonon only | `siscforge run --calculator qe examples/nbn_phonon_qe.yaml` |
| Real EPW + Tc (NbN gate) | `siscforge run --calculator qe-epw examples/nbn_epw.yaml` |
| List calculators | `python -c "from siscforge.calculators import list_calculators; print(list_calculators())"` |
| Dry-run nickelate DMFT (P3.3 mock) | `siscforge run --dry-run examples/ndnio2_dmft_mock.yaml` |

---

## Tier D — optional DMFT (TRIQS / solid_dmft)

**TRIQS is never a hard dependency of `siscforge`.** The Python package,
tests, and conventional campaigns install and run without it. P3.3
(`dft.do_dmft`, calculator `qe-dmft`) is **off by default**.

What P3.3 actually does on the “real” path:

1. Honours the P3.2 `WannierResult.ready_for_dmft` gate (or an explicit
   operator bypass).
2. Writes a small sidecar (`siscforge_dmft_config.json`) under a sibling
   `dmft/` workdir.
3. If `observables.json` (or `observables_imp0.json` /
   `siscforge_dmft_observables.json`) is already in that workdir, parses
   occupancy / mass enhancement into `DMFTResult`.
4. If TRIQS / solid_dmft is **not** importable, stores
   `status=skipped`, `failure_class=solver_missing` and leaves upstream
   DFT+U / Wannier artifacts untouched.

It does **not** launch solid_dmft or CTHYB. Full automated launch is a
residual. Operator workflow and residual notes:
[docs/phase3-p33-dmft.md](phase3-p33-dmft.md).

### When to bother with a real stack

- You already have a TRIQS / solid_dmft environment and want SiSC-Forge
  to **ingest** its observables into the campaign store / CSV / cards.
- You are developing the residual launcher.

Otherwise stay on **Tier A** and
`siscforge run --dry-run examples/ndnio2_dmft_mock.yaml`. Mock occupancy
and m*/m are **illustrative placeholders**, not literature values.

### Recommended install routes (operator-owned)

SiSC-Forge does not vendor or pin TRIQS. Typical routes (check current
upstream docs; versions drift):

| Route | Notes |
|-------|--------|
| conda-forge `triqs` | Common workstation path. Watch Python ABI (3.11+ here) and MPI. |
| solid_dmft from source / conda | Needs a matching TRIQS + CTHYB / impurity solver. |
| Site / HPC module | Prefer the stack your cluster already builds; do not mix MPIs. |

Known limitations of the **thin P3.3 wrapper**:

- Import check only (`import triqs` / `import solid_dmft`). No job
  submission, no MPI wrapper, no DFT+DMFT self-consistency loop.
- `n_loops` / `n_cycles` / `n_warmup_cycles` are stored on the sidecar
  for a future launcher and are **unused** by the parser.
- Observables parse is best-effort JSON key matching, not a solid_dmft
  schema guarantee.
- Real (non-mock) Wannier artifacts still depend on residual **P3.2.1**
  (automated nscf + `pw2wannier90`).
- Pairing eigenvalue → `performance_score` is **P3.4**.

Optional real-path pytest (skipped unless the stack is importable):

```bash
pytest tests/test_dmft_p33.py -q
# real-stack case is skipped automatically when triqs/solid_dmft is absent
```

---

## This machine (typical laptop / agent host without QE)

If `which pw.x epw.x` prints nothing (as on many developer hosts):

1. **Do Tier A only** — install Python deps, run dry-run and tests.
2. Install QE+EPW+Wannier90+SSSP (Tier B/C) on a workstation or HPC node that has them.
3. Re-run the NbN gate there with `pseudo_dir` set.

Offline checks that still work without QE:

```bash
# Parser + Allen–Dynes on fixture “EPW-like” output
pytest tests/test_epw.py -q

# Mock NbN with populated λ / Tc
siscforge run --dry-run examples/nbn_epw.yaml
```

---

## Related docs

- [Phase 0 exit criteria](phase0-exit.md)
- [Implementation notes](implementation-notes.md)
- [NbN phonon (QE)](examples/nbN_phonon_qe.md)
- [NbN EPW + Tc](examples/nbN_epw.md)
- [Phase 3.3 DMFT scaffold](phase3-p33-dmft.md)
- [Roadmap](ROADMAP.md)
