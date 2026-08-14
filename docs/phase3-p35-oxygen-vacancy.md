# P3.5 — Oxygen-vacancy enumeration for infinite-layer nickelates

**Status**: **Done** (shipped on main)  
**Prerequisite**: P3.1–P3.4 on main  
**Next**: Phase 3 residuals — real CTHYB launch, production GNN heads

This package adds a **first-class structure-generation option** for
infinite-layer RNiO₂-family cells and a **small, documented** set of
oxygen-vacancy / apical-O variants. Candidates are ordinary
`StructureCandidate` objects and flow through enumerate → score → rank
→ export with **no family-specific ranking forks**.

## Goal

1. Build a minimal infinite-layer prototype (NdNiO₂ golden; R = Nd / Pr / La).
2. Emit a curated set of scientifically relevant O-site variants for
   **screening**, not a combinatorial defect search.
3. Keep the feature **opt-in** via campaign YAML. Nitride / MgB₂
   enumeration is unchanged when `nickelate` is not in
   `material_families`.

## What is generated

Default screening set (per rare earth, then × substrate × strain):

| Pattern id | Cell | Composition (Nd) | Why it is here |
|------------|------|------------------|----------------|
| `stoichiometric` | primitive P4/mmm IL | NdNiO₂ | Golden infinite-layer target |
| `inplane_vacancy` | 2×2×1 IL minus **one** in-plane O | Nd₄Ni₄O₇ | Ordered single vacancy; all in-plane O are equivalent in the parent, so only one representative is emitted |
| `apical_o` | idealized P4/mmm perovskite | NdNiO₃ | Apical oxygen *added* — parent of the IL reduction path |

Optional (named, **not** in the default set): `apical_half` (R₂Ni₂O₅ in
1×1×2) for residual apical occupancy.

### Lattice constants (screening)

NdNiO₂ lattice and origin match the existing `examples/ndnio2_*` mock
CIF (`a = 3.92 Å`, `c = 3.31 Å`; R at `(0,0,½)`, Ni at `(½,½,0)`,
in-plane O at `(½,0,0)` / `(0,½,0)`).

| R | IL *a* / *c* (Å) | Perovskite *a* (Å) | Primary experimental anchors |
|---|------------------|--------------------|------------------------------|
| Nd | 3.92 / 3.31 | 3.81 | Hayward *et al.*, *JACS* **121**, 8843 (1999); Li *et al.*, *Nature* **572**, 624 (2019) and subsequent film XRD |
| Pr | 3.96 / 3.31 | 3.82 | Same IL family; film/bulk reports around these values |
| La | 3.96 / 3.37 | 3.84 | Hayward, Wilson *et al.*, *JACS* **125**, 12768 (2003) |

These are workstation **screening prototypes**, not refined experimental
fits. Constants live in `INFINITE_LAYER_LATTICE` / `PEROVSKITE_LATTICE`.

### Idealized space groups and vacancy limitation

- Real RNiO₃ precursors are typically **orthorhombic Pbnm** with
  octahedral rotations. The P4/mmm cell is a common high-symmetry
  screening approximation.
- The ordered 2×2×1 single in-plane vacancy is the **symmetry-unique
  minimal** representative. Real topotactic reduction can produce
  disordered, clustered, residual-apical, or larger-period vacancies.
- Do not treat this set as a defect ensemble or a formation-energy
  workflow.

### Recommended substrates

| Goal | Substrate |
|------|-----------|
| Si-feasibility / integration ranking | `Si(001)` (default in the example) |
| Physics-oriented IL campaigns | `SrTiO3` or NGO-style labels |

Non-Si labels do **not** crash: nickelates fall back to the requested
biaxial strain and set `metadata.biaxial_fallback: true`. Nitride / MgB₂
paths still require a parseable Si substrate.

## What is intentionally out of scope

- Full defect formation-energy / grand-canonical thermodynamics
- Combinatorial vacancy enumerations (2×2×2 all-O subsets, etc.)
- Bilayer nickelates and cuprate prototypes
- Mixed conventional/unconventional AL acquisition (**P3.6** — Done; see `docs/phase3-p36-mixed-al.md`)
- Real solid_dmft / CTHYB launch
- Changing the pairing formula, ranking maths, or Si-feasibility science

Si-feasibility for nickelates is the existing **heuristic** family table
(low maturity, oxygen-window penalty). Enumeration is **not** blocked;
scores may be weak. That is expected.

## Enable (default off)

```yaml
enumeration:
  material_families: [nickelate]     # the opt-in switch
  nickelate_rare_earths: [Nd]        # Nd | Pr | La; empty → [Nd]
  nickelate_patterns:                # empty → default 3-pattern set
    - stoichiometric
    - inplane_vacancy
    - apical_o
  nickelate_max_patterns: 3          # cap per rare earth
  nickelate_supercell: [2, 2, 1]     # used by inplane_vacancy; length 3, all ≥ 1
  substrates: [Si(001)]              # Si for integration scoring; SrTiO3 ok
  strain_values: [0.0, -0.01]
```

```bash
siscforge enumerate -c examples/ndnio2_ovac_enumerate.yaml
siscforge run --dry-run examples/ndnio2_ovac_enumerate.yaml
```

Omitting `nickelate` from `material_families` (the default for nitride /
MgB₂ examples) leaves this path inert, even if the `nickelate_*` knobs
are present.

`enumeration.formulas` is interpreted as nickelate formulas **only** when
`material_families` is exactly `[nickelate]`. Mixed-family campaigns
should leave `formulas` for the nitride path and use
`nickelate_rare_earths` / `nickelate_patterns` (or `candidate_specs`)
for IL cells.

## Candidate metadata

Every generated cell is tagged `material_family: nickelate` and carries
provenance-friendly fields:

- `vacancy_pattern`, `structure_key`, `rare_earth`, `kind`
- `pattern_class`: `stoichiometric` | `oxygen_vacancy` | `apical_addition`
- `prototype`: `infinite_layer` for IL / vacancy / apical_half;
  `perovskite_like` for `apical_o`
- `n_oxygen`, `n_oxygen_il_parent` (IL oxygen count for the cell size),
  `n_oxygen_parent` (same number; kept for back-compat)
- `vacancy_fraction` (in-plane vacancy only) and `apical_added` (addition
  patterns). Apical patterns **add** oxygen relative to the IL parent —
  they are not vacancies. `vacancy_fraction` is 0 for those rows.
- `screening_only: true` and a short notes string
- `biaxial_fallback: true` when a non-Si substrate skipped epitaxy parse
- CIF / lattice / strain tensor via the shared `structure_to_candidate`
  adapter (same serialization as nitrides)

`structure_key` is the symmetry-reduced identity
(`nickelate:Nd:inplane_vacancy:2x2x1`). `candidate_id` remains a UUID,
consistent with nitride enumeration.

## Downstream

Ranking continues to use generic `performance_score` + Si total.
DFT+U / Wannier / DMFT remain optional later-path consumers; this package
only needs candidates that *can* enter those paths.

## Acceptance checks

- `pytest` green (including `tests/test_nickelates_p35.py`)
- Enabling the family yields ≥1 stoichiometric IL and ≥1 documented
  O-vacancy / apical variant as `StructureCandidate`s
- Existing nitride / MgB₂ enumerate examples unchanged when the family
  is off
- Dry-run rank / export succeeds (scores may be heuristic)
