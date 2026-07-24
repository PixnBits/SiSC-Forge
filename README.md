# SiSC-Forge

**Modular, high-throughput, machine-learning-accelerated platform for the discovery of silicon-compatible superconducting materials.**

SiSC-Forge systematically searches for and evaluates silicon-compatible materials (transition-metal nitrides, heavily boron-doped silicon & silicides, MgB₂ and related borides, rare-earth nickelates, and cuprates with buffer layers) that could enable Josephson-junction-based superconducting logic at elevated temperatures, ideally approaching ambient conditions, while remaining compatible with CMOS fabrication processes (epitaxial growth, buffer layers, membrane transfer).

## Spec-Driven Development

This repository is organized for **spec-driven development**. The authoritative source of truth lives in the documentation:

- **Product Requirements Document (PRD)**: [`docs/PRD/SiSC-Forge-PRD.md`](docs/PRD/SiSC-Forge-PRD.md) — Version 0.2
- **Technical Specifications**: [`docs/specs/SiSC-Forge-Technical-Specifications.md`](docs/specs/SiSC-Forge-Technical-Specifications.md) — Version 0.3
- **Development Roadmap**: [`docs/ROADMAP.md`](docs/ROADMAP.md) — Practical phased implementation plan (Phases 0–4)

All implementation, prioritization, and design decisions should be driven by these documents.

## Current Status

v0.3 Blueprint — Complete PRD, Technical Specifications (including detailed Josephson Junction Device Modeling module), and a practical Development Roadmap are in place.

The roadmap breaks work into five clear phases:

| Phase | Focus | Compute |
|-------|-------|--------|
| 0 | Foundation & local validation | Workstation only |
| 1 | Core conventional (EPW + Eliashberg) pipeline | Workstation + small cluster |
| 2 | Silicon Integration maturity + ranking | Workstation |
| 3 | Unconventional (DMFT) pathway + AL maturity | Small → medium HPC |
| 4 | Device-level Josephson modeling | Mostly analytic / shortlist |

Phases 0–2 are fully workstation-validatable before any large allocation is required.

## Key Capabilities

- Structure generation & high-throughput screening of Si-compatible phases, alloys, dopings, strains, and interfaces
- Automated DFT / DFPT / electron-phonon (EPW-style) calculations + Eliashberg Tc prediction
- DFT+U / DMFT + pairing susceptibility for unconventional materials (nickelates, cuprates)
- Silicon-specific modules: epitaxial strain, buffer-layer stacks, freestanding membrane transfer, proximity-effect modeling
- Active-learning loop with graph neural network surrogates (ALIGNN / MatGL style)
- Workflow orchestration (jobflow / atomate2 style)
- Multi-objective ranking by predicted Tc **and** silicon-integration feasibility score
- Export of synthesis-relevant metadata
- (Phase 4) Approximate Josephson device metrics (Jc, IcRn, gap, switching energy, fabrication compatibility)

## Material Families

1. Transition-metal nitrides (NbN, NbTiN, HfN, ZrN, TiN and alloys)
2. Heavily boron-doped silicon and silicides
3. MgB₂ thin films and related borides
4. Rare-earth nickelates (infinite-layer NdNiO₂ / PrNiO₂ and bilayer family)
5. Cuprates (YBCO and related) with buffer layers on silicon

## Getting Started

1. **Install & run** — follow **[docs/SETUP.md](docs/SETUP.md)** (Python, optional QE, optional EPW).
2. Read the [PRD](docs/PRD/SiSC-Forge-PRD.md) for vision, goals, and success metrics.
3. Read the [Technical Specifications](docs/specs/SiSC-Forge-Technical-Specifications.md) for module contracts and data models.
4. Follow the [Development Roadmap](docs/ROADMAP.md) for phased implementation and validation gates.

### Quick start (this machine — Python only)

Requires **Python ≥ 3.11**. No Quantum ESPRESSO needed for dry-run.

```bash
# from the repo root (e.g. projects/SiSC-Forge/main)
uv venv .venv && source .venv/bin/activate   # or: python3 -m venv .venv
uv pip install -e ".[dev]"                   # or: pip install -e ".[dev]"
pytest -q
siscforge run --dry-run examples/nbti_n_strain.yaml
siscforge run --dry-run examples/nbn_epw.yaml
```

Full tiers (QE phonon, EPW + NbN scientific gate, env vars, pseudos): **[docs/SETUP.md](docs/SETUP.md)**.

### Other short links

| Topic | Doc |
|-------|-----|
| Setup (all tiers) | [docs/SETUP.md](docs/SETUP.md) |
| NbN phonon (real QE) | [docs/examples/nbN_phonon_qe.md](docs/examples/nbN_phonon_qe.md) |
| NbN EPW + Tc | [docs/examples/nbN_epw.md](docs/examples/nbN_epw.md) |
| Implementation notes | [docs/implementation-notes.md](docs/implementation-notes.md) |
| Phase 0 exit checklist | [docs/phase0-exit.md](docs/phase0-exit.md) |

## License

[MIT License](LICENSE)

## Contributing

Contributions are welcome once the core modules are in place. Please read the PRD, Technical Specifications, and Roadmap before proposing changes.
