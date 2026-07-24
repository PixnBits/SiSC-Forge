# SiSC-Forge

**Modular, high-throughput, machine-learning-accelerated platform for the discovery of silicon-compatible superconducting materials.**

SiSC-Forge systematically searches for and evaluates silicon-compatible materials (transition-metal nitrides, heavily boron-doped silicon & silicides, MgB₂ and related borides, rare-earth nickelates, and cuprates with buffer layers) that could enable Josephson-junction-based superconducting logic at elevated temperatures, ideally approaching ambient conditions, while remaining compatible with CMOS fabrication processes (epitaxial growth, buffer layers, membrane transfer).

## Spec-Driven Development

This repository is organized for **spec-driven development**. The authoritative source of truth for requirements and architecture lives in the documentation:

- **Product Requirements Document (PRD)**: [`docs/PRD/SiSC-Forge-PRD.md`](docs/PRD/SiSC-Forge-PRD.md)
- **Technical Specifications**: [`docs/specs/SiSC-Forge-Technical-Specifications.md`](docs/specs/SiSC-Forge-Technical-Specifications.md)

All implementation, prioritization, and design decisions should be driven by these documents. Proposed changes to scope, architecture, or interfaces must first be reflected in the corresponding specification files.

## Current Status

v0.1 Blueprint — Complete, implementation-ready Product Requirements and Technical Specifications. Core codebase under development.

## Key Capabilities (from Specs)

- Structure generation & high-throughput screening of Si-compatible phases, alloys, dopings, strains, and interfaces
- Automated DFT / DFPT / electron-phonon (EPW-style) calculations + Eliashberg Tc prediction
- DFT+U / DMFT + pairing susceptibility for unconventional materials (nickelates, cuprates)
- Silicon-specific modules: epitaxial strain, buffer-layer stacks, freestanding membrane transfer, proximity-effect modeling
- Active-learning loop with graph neural network surrogates (ALIGNN / MatGL style)
- Workflow orchestration (jobflow / atomate2 style)
- Multi-objective ranking by predicted Tc **and** silicon-integration feasibility score
- Export of synthesis-relevant metadata

## Material Families

1. Transition-metal nitrides (NbN, NbTiN, HfN, ZrN, TiN and alloys)
2. Heavily boron-doped silicon and silicides
3. MgB₂ thin films and related borides
4. Rare-earth nickelates (infinite-layer NdNiO₂ / PrNiO₂ and bilayer family)
5. Cuprates (YBCO and related) with buffer layers on silicon

## Development Approach

See the [Technical Specifications](docs/specs/SiSC-Forge-Technical-Specifications.md) for the phased roadmap (Phase 0 workstation foundation → Phase 1 conventional SC pipeline → Phase 2 unconventional + advanced Si integration).

The platform is designed so that Phase 0 and Phase 1 can be fully validated on a single high-end workstation before large-scale compute is required.

## License

MIT License (to be finalized).

## Contributing

Contributions are welcome once the core modules are in place. Please read the PRD and Technical Specifications before proposing changes.
