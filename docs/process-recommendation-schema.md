# Process recommendation schema (P2.5 freeze)

**Schema version**: `1.0`  
**Module**: `siscforge.export.process_recommendation`  
**Constant**: `PROCESS_RECOMMENDATION_SCHEMA_VERSION = "1.0"`

This is the **Phase 2 handoff contract** for machine-readable synthesis cards.
It does **not** change scoring science, ranking maths, or calculator behaviour —
it only gathers already-computed Si / ranking / trust fields into one stable
object experimental tools can consume without scraping Markdown.

## Where it appears

| Artifact | Location |
|----------|----------|
| Per-card fenced JSON | Inside each synthesis card / one-pager under **Process recommendation** |
| Campaign list | `process_recommendations.json` in the campaign export bundle |
| Helper | `process_recommendation(ev) -> dict` |
| Writer | `write_process_recommendations_json(evals, path)` |

`export_campaign_bundle` always writes `process_recommendations.json` next to
`evaluations.json`.

## Keys (v1.0 — frozen)

### Identity / ranking

| Key | Type | Meaning |
|-----|------|---------|
| `schema_version` | string | Always `"1.0"` for this freeze |
| `candidate_id` | string | Stable candidate id |
| `formula` | string | Reduced formula |
| `material_family` | string | e.g. `tm_nitride` |
| `substrate` | string \| null | e.g. `Si(001)` |
| `in_plane_strain` | number \| null | Epitaxial strain fraction |
| `rank` | int \| null | 1-based rank after sorting |
| `on_pareto_front` | bool \| null | Pareto flag (null if Pareto disabled) |

### Process recommendation (actionable)

| Key | Type | Meaning |
|-----|------|---------|
| `recommended_buffers` | string[] | Stack labels, best first |
| `recommended_stack` | string \| null | Primary stack (`recommended_buffers[0]`) |
| `recommended_thickness_nm` | number \| `[lo, hi]` \| null | Suggested film thickness (nm) |
| `critical_thickness_nm` | number \| null | Primary h_c (usually Matthews–Blakeslee) |
| `critical_thickness_method` | string \| null | Method label |
| `critical_thickness_people_bean_nm` | number \| null | Optional metastable h_c |
| `process_temp_ceiling_c` | number \| null | Heuristic process temperature ceiling (°C) |
| `thermal_window_note` | string \| null | Short thermal-window prose |
| `chemical_flags` | string[] | e.g. `nitrogen_window`, `oxygen_window` |
| `membrane_transfer_candidate` | bool | Membrane transfer may help |
| `membrane_transfer_note` | string \| null | Short heuristic note |

### Trust

| Key | Type | Meaning |
|-----|------|---------|
| `result_quality` | string | `production` / `screening` / `screening_suspect` / `unreliable` / `unknown` |
| `do_not_cite_tc` | bool | `false` **only** when `result_quality == "production"`; `true` for screening, screening_suspect, unreliable, unknown, and any other non-production tier. Machine consumers must treat Tc/λ as non-citable when this flag is true. |
| `trust_warning` | string \| null | Human caveat; null **only** for production |


### Headline scores

| Key | Type | Meaning |
|-----|------|---------|
| `composite_score` | number \| null | Multi-objective composite |
| `performance_score` | number \| null | Tc proxy (K) |
| `performance_score_source` | string \| null | `epw` / `mock` / `surrogate` / `dmft_pairing` / `dmft_pairing_mock` / … |
| `si_feasibility_total` | number \| null | Si score 0–100 |
| `si_scorer_version` | string \| null | e.g. `"0.5"` (Si rules version, not this schema) |

## Example

```json
{
  "schema_version": "1.0",
  "candidate_id": "…",
  "formula": "NbN",
  "material_family": "tm_nitride",
  "substrate": "Si(001)",
  "in_plane_strain": 0.0,
  "rank": 1,
  "on_pareto_front": true,
  "recommended_buffers": ["MgO/TiN", "TiN", "AlN"],
  "recommended_stack": "MgO/TiN",
  "recommended_thickness_nm": 1.76,
  "critical_thickness_nm": 2.94,
  "critical_thickness_method": "Matthews-Blakeslee",
  "critical_thickness_people_bean_nm": 7.0,
  "process_temp_ceiling_c": 600.0,
  "thermal_window_note": "Both steps usually ≤550 °C; O→N purge is critical",
  "chemical_flags": ["oxygen_window", "nitrogen_window"],
  "membrane_transfer_candidate": true,
  "membrane_transfer_note": "high direct mismatch …",
  "result_quality": "screening",
  "do_not_cite_tc": true,
  "trust_warning": "result_quality=screening: Tc/λ are order-of-magnitude only …",

  "composite_score": 35.8,
  "performance_score": 8.3,
  "performance_score_source": "mock",
  "si_feasibility_total": 58.5,
  "si_scorer_version": "0.5"
}
```

## Compatibility policy

- **Additive only** within a major schema version: new optional keys may appear;
  existing keys keep their meaning and types.
- A breaking change bumps `schema_version` (e.g. `2.0`) and is documented in the
  ROADMAP / exit notes.
- Full evaluation dumps remain in `evaluations.json`; this block is the **actionable
  subset**, not a replacement for provenance-rich exports.

## Markdown card layout (same freeze)

Each synthesis card / one-pager section order:

1. **Identity** — formula, family, strain, substrate, rank, Pareto  
2. **Headline scores** — composite, performance/Tc proxy, Si total, result quality  
3. **Process recommendation** — human bullets + fenced JSON (`schema_version` 1.0)  
4. **Supporting detail** — Si component weights, ranking provenance, phonon, EPW, AL, SCF  
