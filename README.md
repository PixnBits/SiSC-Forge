# SiSC-Forge

**Modular, high-throughput platform for discovery of silicon-compatible superconducting materials.**

SiSC-Forge searches and ranks transition-metal nitrides, B-doped Si, MgB₂/borides, and (later) nickelates/cuprates for elevated-temperature Josephson-friendly superconductivity with CMOS-compatible integration.

## Spec-driven development

| Document | Path |
|----------|------|
| PRD | [`docs/PRD/SiSC-Forge-PRD.md`](docs/PRD/SiSC-Forge-PRD.md) |
| Technical Specifications | [`docs/specs/SiSC-Forge-Technical-Specifications.md`](docs/specs/SiSC-Forge-Technical-Specifications.md) |
| Roadmap | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Setup | [`docs/SETUP.md`](docs/SETUP.md) |

## Current status — **Phase 1 complete** (`v0.1.0`)

| Phase | Focus | Status |
|-------|-------|--------|
| **0** | Foundation, mock dry-run, QE phonon | **Done** — [phase0-exit](docs/phase0-exit.md) |
| **1** | EPW + isotropic Eliashberg, goldens, λ/Tc stub, AL prioritization | **Done** — [phase1-exit](docs/phase1-exit.md) |
| 2 | Si-integration maturity (45° epitaxy, buffers, …) | In progress |
| 3 | DMFT / unconventional | Future |
| 4 | Josephson device metrics | Future |

### Phase 1 delivered

- QE phonon + EPW screening path (`pw` → multi-q `ph` → `pp.py` → NSCF → `epw`)
- Isotropic Allen–Dynes / Eliashberg Tc → `performance_score` ranking
- NbN & MgB₂ golden examples (mock always; real EPW optional)
- Family-heuristic λ/Tc **surrogate stub** for pre-filtering
- Minimal **active-learning** top-k prioritization (not a retrain loop)
- File store, CSV/Markdown export, synthesis cards

Validation: [docs/validation-phase1.md](docs/validation-phase1.md).

## Quick start (Python only — no QE)

Requires **Python ≥ 3.11**.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest -q
siscforge run --dry-run examples/nbti_n_strain.yaml
siscforge run --dry-run examples/nbti_n_al.yaml
siscforge run --dry-run examples/nbn_epw.yaml
siscforge run --dry-run examples/mgb2_epw.yaml
```

Real QE/EPW: see [docs/SETUP.md](docs/SETUP.md) (Tiers B–C).

## Example campaigns

| YAML | Purpose |
|------|---------|
| `examples/nbti_n_strain.yaml` | Nitride strain series (mock) |
| `examples/nbti_n_surrogate.yaml` | λ/Tc surrogate pre-filter |
| `examples/nbti_n_al.yaml` | AL top-k prioritization |
| `examples/nbn_epw.yaml` | NbN EPW golden |
| `examples/nbn_phonon_qe.yaml` | NbN phonon (real QE) |
| `examples/mgb2_epw.yaml` | MgB₂ EPW golden |
| `examples/mgb2_epw_skeleton.yaml` | **Compat alias** → prefer `mgb2_epw.yaml` |
| `examples/dummy_campaign.yaml` | Minimal CLI smoke |

Walkthroughs: [docs/examples/](docs/examples/).

## Material families (priority)

1. TM nitrides (NbN, NbTiN, TiN, ZrN, HfN, …)
2. Heavily B-doped Si / silicides
3. MgB₂ and borides
4. Rare-earth nickelates (later)
5. Cuprates with buffers (later)

## License

[MIT License](LICENSE)

## Contributing

Read the PRD, Technical Specs, and Roadmap before proposing changes. Prefer small PRs that keep `pytest` and dry-run green.
