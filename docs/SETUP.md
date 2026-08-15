# SiSC-Forge setup guide

This document is the **canonical install and run guide** for local workstations.  
It covers four tiers:

| Tier | What you get | External binaries |
|------|----------------|-------------------|
| **A — Python only** | Full dry-run / mock path, structure gen, Si-score, ranking, tests | None |
| **B — QE phonon** | Real relax / SCF / phonon (`qe` calculator) | `pw.x`, `ph.x` + UPFs |
| **C — QE + EPW** | Real electron-phonon + Tc (`qe-epw` calculator) — **Phase 1 scientific gate** | `pw.x`, `ph.x`, `epw.x`, `wannier90.x` + UPFs |
| **D — optional DMFT** | Ingest solid_dmft observables; write a run package and invoke when the stack is present | Optional TRIQS / solid_dmft (never a hard dep) |

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
- **Soft-phonon EPW gate (default):** after a successful DFPT parse, `qe-epw` skips EPW (and NSCF / Wannier-EPW) when the phonon result has imaginary modes or is not `dynamically_stable`. Terminal state is `phonon-complete / EPW-blocked`; DFPT artifacts are kept. This is complementary to `shortlist --mode stable_only`. To investigate a soft cell anyway, set `epw.allow_on_soft: true`. Mock / `--dry-run` is unchanged.

If a previous run hung, stop it (`Ctrl+C`) and kill stragglers before retrying:

```bash
pkill -f 'pw.x|ph.x|epw.x'   # only if you are sure no other QE jobs matter
```
