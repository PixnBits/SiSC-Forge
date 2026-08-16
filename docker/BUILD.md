# Building and verifying the SiSC-Forge Docker image

Multi-stage image: **Ubuntu 24.04** + **Quantum ESPRESSO 7.3.1** (built from
source) + **Wannier90 3.1.0** (from source; package missing on noble) +
**SSSP** UPFs + **SiSC-Forge** (`pip install -e ".[dev,qe,phonopy]"`).

Ubuntu’s packaged QE 6.7 is **not** used for `ph.x` (known buffer-overflow).
Binaries live under `/opt/qe/bin` and come first on `PATH` via `QE_BIN`.

## Build

From the repository root:

```bash
docker build -t siscforge:latest .
```

First build compiles QE (`pw ph pp epw`) and may take **20–60+ minutes**
depending on cores and network (source download from GitLab).

Useful cache-friendly rebuild after Python-only changes:

```bash
docker build -t siscforge:latest .
# QE builder stage is cached unless Dockerfile builder steps change
```

## Run

```bash
# Interactive shell (default CMD)
docker run --rm -it -v "$PWD:/workspace" siscforge:latest

# CLI help
docker run --rm siscforge:latest siscforge --help

# Dry-run campaign (writes under the mounted workspace)
docker run --rm -v "$PWD:/workspace" -w /workspace siscforge:latest \
  siscforge run --dry-run examples/nbn_epw.yaml

# Full verification suite (fast; no real multi-hour DFT)
docker run --rm siscforge:latest siscforge-verify
```

### Environment (set in the image)

| Variable | Value |
|----------|--------|
| `QE_BIN` | `/opt/qe/bin` |
| `PATH` | `$QE_BIN` first, then venv `bin` |
| `SISCFORGE_PSEUDO_DIR` | `/usr/share/espresso/pseudo` |
| `OMPI_MCA_btl` | `^openib` (desktop/container MPI) |
| `OMPI_ALLOW_RUN_AS_ROOT` | `1` (containers often run as root) |

Override at run time if needed:

```bash
docker run --rm -e SISCFORGE_PSEUDO_DIR=/my/upfs -v /my/upfs:/my/upfs siscforge:latest ...
```

## Verification tests (required after build)

```bash
docker run --rm siscforge:latest siscforge-verify
```

### Results (verification snapshot)

This table is a **snapshot**, not a live dashboard. Pytest counts drift as
the suite grows — run `pytest -q` or CI for the current number. Image size,
QE, and Wannier90 rows are from the last documented `docker build`
(2026-08-02) and were **not** re-measured here. Re-run `siscforge-verify`
after the next image rebuild.

| Item | Result |
|------|--------|
| Image tag | `siscforge:latest` (~2.7 GB) — image build 2026-08-02 |
| QE | **7.3.1** at `/opt/qe/bin` (`Program PWSCF v.7.3.1`) |
| Wannier90 | **3.1.0** at `/opt/qe/bin/wannier90.x` |
| SiSC-Forge | `0.4.4` (`pip install -e ".[dev,qe,phonopy]"`) |
| pytest | **647 passed**, 8 skipped (host/CI mock path, 2026-08-15; no real DFT) |
| Dry-run | `dummy_campaign` + `nbn_epw` OK |
| SSSP | Nb/N UPFs under `/usr/share/espresso/pseudo` |
| Full suite | `=== ALL VERIFICATION CHECKS PASSED ===` (image build 2026-08-02) |

The script checks:

| Step | What |
|------|------|
| **(a)** | `pw.x`, `ph.x`, `epw.x`, `wannier90.x`, `siscforge` on `PATH`; QE version ≥ 7.2 |
| **(b)** | `detect_qe_environment()` finds pw/ph/epw |
| **(c)** | `pytest -q` (mock path only; no `SISCFORGE_RUN_QE` / `SISCFORGE_RUN_EPW`) |
| **(d)** | `siscforge run --dry-run` for `dummy_campaign` and `nbn_epw` |
| **(e)** | SSSP UPFs under `/usr/share/espresso/pseudo` |

Manual one-liners (same checks):

```bash
docker run --rm siscforge:latest bash -lc 'which pw.x ph.x epw.x wannier90.x; pw.x -v | head -3; siscforge --version'
docker run --rm siscforge:latest bash -lc 'python -c "from siscforge.calculators.qe.env import detect_qe_environment as d; e=d(); print(e); assert e.pw and e.ph and e.epw"'
docker run --rm -w /app siscforge:latest pytest -q --tb=no
docker run --rm siscforge:latest bash -lc 'siscforge run --dry-run /app/examples/dummy_campaign.yaml -o /tmp/d; siscforge run --dry-run /app/examples/nbn_epw.yaml -o /tmp/n'
docker run --rm siscforge:latest bash -lc 'ls /usr/share/espresso/pseudo/Nb*.UPF /usr/share/espresso/pseudo/N*.UPF | head'
```

## Image layout

| Path | Contents |
|------|----------|
| `/opt/qe/bin` | `pw.x`, `ph.x`, `pp.x`, `epw.x` (QE 7.3.1), `wannier90.x` (3.1.0) |
| `/opt/siscforge-venv` | Python venv + editable SiSC-Forge |
| `/app` | Source tree, tests, examples, docs |
| `/workspace` | Default workdir (mount host projects/outputs here) |
| `/usr/share/espresso/pseudo` | SSSP from `quantum-espresso-data-sssp` |

## Notes

- **Do not** `apt install quantum-espresso` in this image for production DFPT —
  that pulls 6.7 `ph.x` with the fortify crash. Only the data package is used.
- **Wannier90** is compiled from the official GitHub tag (Ubuntu 24.04 noble
  has no `wannier90` package). On newer Ubuntu releases you could `apt install
  wannier90` instead, but the image keeps a private binary for portability.
- Real EPW wall-time jobs are optional; the image supports them when you pass
  appropriate resources (`--cpus`, memory) and write outputs to a volume.
- Build procedure for QE matches `docs/build-qe-from-source.md` (libmbd with
  `LIBMBD_C_API=0`, targets `pw ph pp epw`).
