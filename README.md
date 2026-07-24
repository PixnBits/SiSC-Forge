# SiSC-Forge

**Modular, high-throughput, machine-learning-accelerated platform for the discovery of silicon-compatible superconducting materials.**

SiSC-Forge systematically searches for and evaluates silicon-compatible materials (transition-metal nitrides, heavily boron-doped silicon & silicides, MgB₂ and related borides, rare-earth nickelates, and cuprates with buffer layers) that could enable Josephson-junction-based superconducting logic at elevated temperatures, ideally approaching ambient conditions, while remaining compatible with CMOS fabrication processes (epitaxial growth, buffer layers, membrane transfer).

## Spec-Driven Development

This repository is organized for **spec-driven development**. The authoritative source of truth for requirements and architecture lives in the documentation:

- **Product Requirements Document (PRD)**: [`docs/PRD/SiSC-Forge-PRD.md`](docs/PRD/SiSC-Forge-PRD.md) — Version 0.2
- **Technical Specifications**: [`docs/specs/SiSC-Forge-Technical-Specifications.md`](docs/specs/SiSC-Forge-Technical-Specifications.md) — **Version 0.3**

All implementation, prioritization, and design decisions should be driven by these documents. Proposed changes to scope, architecture, or interfaces must first be reflected in the corresponding specification files.

## Current Status

v0.3 Blueprint — Technical Specifications now contain a fully detailed **Josephson Junction Device Modeling module** (§2.8).

Key content in the JJ module:
- Inputs required from the materials screening pipeline (gap, Tc / pairing eigenvalue, Si-feasibility data, optional geometry)
- Tiered theoretical approaches: simple analytic estimates (Ambegaokar–Baratoff) → Usadel → optional BdG
- Critical current density Jc, IcRn product, gap Δ, switching energy / speed proxies, fabrication compatibility (SIS / SNS / etc.)
- Clear interface to the candidate ranking system (shortlist-only, optional secondary ranking, explicit “approximate / ranking only” labeling)
- Explicit version boundaries: inert stub until Phase 3; Tier-1 analytic first, research-grade solvers later

Core materials-screening pipeline (Phases 0–2) remains unchanged and is still the immediate implementation focus.

## Key Capabilities (from Specs)

- Structure generation & high-throughput screening of Si-compatible phases, alloys, dopings, strains, and interfaces
- Automated DFT / DFPT / electron-phonon (EPW-style) calculations + Eliashberg Tc prediction
- DFT+U / DMFT + pairing susceptibility for unconventional materials (nickelates, cuprates)
- Silicon-specific modules: epitaxial strain, buffer-layer stacks, freestanding membrane transfer, proximity-effect modeling
- Active-learning loop with graph neural network surrogates (ALIGNN / MatGL style)
- Workflow orchestration (jobflow / atomate2 style)
- Multi-objective ranking by predicted Tc **and** silicon-integration feasibility score
- Export of synthesis-relevant metadata
- **(Phase 3)** Approximate Josephson device metrics (Jc, IcRn, gap, switching energy, fabrication compatibility) for top-ranked candidates

## Material Families

1. Transition-metal nitrides (NbN, NbTiN, HfN, ZrN, TiN and alloys)
2. Heavily boron-doped silicon and silicides
3. MgB₂ thin films and related borides
4. Rare-earth nickelates (infinite-layer NdNiO₂ / PrNiO₂ and bilayer family)
5. Cuprates (YBCO and related) with buffer layers on silicon

## Development Approach

See the [Technical Specifications](docs/specs/SiSC-Forge-Technical-Specifications.md) for the phased roadmap:

- Phase 0 – workstation foundation (nitrides + B:Si, phonon, Si-feasibility)
- Phase 1 – conventional EPW + Eliashberg + active learning
- Phase 2 – unconventional DMFT pathway + advanced Si integration
- Phase 3 – Josephson Junction Device Modeling module (detailed in §2.8)

The platform is designed so that Phases 0 and 1 can be fully validated on a single high-end workstation before large-scale compute is required.

## License

[MIT License](LICENSE)

## Contributing

Contributions are welcome once the core modules are in place. Please read the PRD and Technical Specifications before proposing changes.
