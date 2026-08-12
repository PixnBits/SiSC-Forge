# P3.1 — DFT+U workflow and DFTUResult model

**Status**: shipped (Phase 3 vertical slice 1)  
**Prerequisite**: Phase 2 complete (P2.1–P2.5)

## Goal

Bring a **typed DFT+U cheap correlated proxy** online so nickelate-style
candidates can carry Hubbard results alongside SCF/phonon/EPW without
breaking conventional nitride / MgB₂ campaigns.

## What shipped

| Item | Location |
|------|----------|
| `DFTUResult` model | `siscforge.models.results.DFTUResult` |
| Optional on evaluation | `CandidateEvaluation.dftu` (default `None`) |
| YAML knobs | `dft.do_dftu`, `dft.dftu.*` (`DFTUConfig`, **disabled by default**) |
| QE helpers | `siscforge.calculators.qe.dftu` |
| Sequential recipe | `run_dftu_scf` / `run_dftu_workflow` in `qe/recipes.py` |
| Calculator | `qe-dftu` / `dftu` alias; additive when `do_dftu` on `qe` |
| Mock path | `MockCalculator` fills `DFTUResult` when DFT+U enabled |
| Export | CSV columns `dftu_*` + synthesis-card section |
| Dry-run example | `examples/ndnio2_dftu_mock.yaml` |
| Tests | `tests/test_dftu_p31.py` |

## Enablement (inert by default)

```yaml
dft:
  do_dftu: true
  dftu:
    enabled: true
    U_eV: 5.0
    J_eV: 0.8
    hubbard_species: [Ni]
    hubbard_projectors: ortho-atomic   # namelist → U_projection_type; card → HUBBARD (…)
    hubbard_syntax: namelist           # or card (QE ≥ 7.1); never both
    # hubbard_manifolds: {O: 2p}       # required for non-TM/RE on card dialect
    # do_relax_with_u: true            # U-relax even if dft.do_relax is false
```

Or calculator choice:

```bash
siscforge run --calculator qe-dftu campaign.yaml   # real pw.x when QE present
siscforge run --dry-run examples/ndnio2_dftu_mock.yaml
```

Conventional examples omit these knobs → behaviour unchanged.

## Extension points (explicitly out of this package)

| Package | Work | Hook |
|---------|------|------|
| **P3.2** | Wannierization quality metrics | After DFT+U; attach metrics (future `WannierResult` or `DFTUResult.raw`) |
| **P3.3** | TRIQS / solid_dmft → `DMFTResult` | Parallel optional field on `CandidateEvaluation` (not yet) |
| **P3.4** | Pairing eigenvalue → `performance_score` | Map leading eigenvalue into common ranking axis |
| **P3.5** | Oxygen-vacancy enumeration | Structure generation (not calculator) |
| **P3.6** | Mixed conventional/unconventional AL | Acquisition updates |

`DFTUResult.raw["extension_hooks"]` documents these for operators and tests.

## Hard out of scope (this PR)

- Full DMFT / CTHYB / solid_dmft  
- Pairing eigenvalue scoring  
- Oxygen-vacancy structure generation  
- Mixed AL acquisition  
- Material-specific Wannier production templates  
- Josephson, GNN heads, GPU QE  

## Acceptance checks

- `pytest` green (existing + `test_dftu_p31`)  
- Conventional campaigns unchanged with DFT+U off  
- Mock path: run → store → CSV/cards with `DFTUResult`  
- Real QE path gated like other QE tests (binaries optional)  
