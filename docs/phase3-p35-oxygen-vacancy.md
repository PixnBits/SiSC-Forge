# P3.5 — Oxygen-vacancy enumeration for infinite-layer nickelates

**Status**: shipped (Phase 3 vertical slice 5)  
**Prerequisite**: P3.1–P3.4 on main (this package does **not** change them)  
**Next**: **P3.6** — mixed conventional / unconventional AL acquisition

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
| `apical_o` | idealized P4/mmm perovskite | NdNiO₃ | Apical oxygen filled — parent of the IL reduction path |

Optional (named, **not** in the default set): `apical_half` (R₂Ni₂O₅ in
1×1×2) for residual apical occupancy.

NdNiO₂ lattice and origin match the existing `examples/ndnio2_*` mock
CIF (`a = 3.92 Å`, `c = 3.31 Å`; R at `(0,0,½)`, Ni at `(½,½,0)`,
in-plane O at `(½,0,0)` / `(0,½,0)`).

Pr / La use documented screening constants in
`siscforge.structure.nickelates.INFINITE_LAYER_LATTICE`.

## What is intentionally out of scope

- Full defect formation-energy / grand-canonical thermodynamics
- Combinatorial vacancy enumerations (2×2×2 all-O subsets, etc.)
- Bilayer nickelates and cuprate prototypes
- Mixed conventional/unconventional AL acquisition (**P3.6**)
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
  nickelate_supercell: [2, 2, 1]     # used by inplane_vacancy
  substrates: [Si(001)]
  strain_values: [0.0, -0.01]
```

```bash
siscforge enumerate -c examples/ndnio2_ovac_enumerate.yaml
siscforge run --dry-run examples/ndnio2_ovac_enumerate.yaml
```

Omitting `nickelate` from `material_families` (the default for nitride /
MgB₂ examples) leaves this path inert, even if the `nickelate_*` knobs
are present.

Non-Si substrate labels (e.g. `SrTiO3` on existing NdNiO₂ mock YAMLs)
do not crash: nickelates fall back to the requested biaxial strain.
Nitride / MgB₂ paths still require a parseable Si substrate.

## Candidate metadata

Every generated cell is tagged `material_family: nickelate` and carries
provenance-friendly fields:

- `vacancy_pattern`, `structure_key`, `rare_earth`, `kind`
- `n_oxygen`, `n_oxygen_parent`, `vacancy_fraction` (where relevant)
- `screening_only: true` and a short notes string
- CIF / lattice / strain tensor via the shared `structure_to_candidate`
  adapter (same serialization as nitrides)

`structure_key` is the symmetry-reduced identity
(`nickelate:Nd:inplane_vacancy:2x2x1`). `candidate_id` remains a UUID,
consistent with nitride enumeration.

## Downstream

Ranking continues to use generic `performance_score` + Si total.
DFT+U / Wannier / DMFT remain optional later-path consumers; this PR
only needs candidates that *can* enter those paths.

## Acceptance checks

- `pytest` green (including `tests/test_nickelates_p35.py`)
- Enabling the family yields ≥1 stoichiometric IL and ≥1 documented
  O-vacancy / apical variant as `StructureCandidate`s
- Existing nitride / MgB₂ enumerate examples unchanged when the family
  is off
- Dry-run rank / export succeeds (scores may be heuristic)
