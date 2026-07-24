# Phase 0 Exit Criteria

Aligned with [ROADMAP.md](ROADMAP.md) Phase 0 and the Technical Specifications v0.3.

**Status date**: 2026-07-24  
**Verdict**: Phase 0 **exit criteria met** for workstation-validatable foundation (mock path). Real QE phonon golden recovery is **optional** and environment-dependent.

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Core Pydantic data models | **Met** | `siscforge.models` — StructureCandidate, SCFResult, PhononResult, SiFeasibilityScore, CandidateEvaluation, CampaignConfig, Provenance |
| 2 | Structure generation (nitrides + B:Si + strain) | **Met** | `siscforge.structure` — binaries, ternaries, B:Si, epitaxial strain |
| 3 | Basic Silicon Feasibility scoring | **Met** | `siscforge.silicon.feasibility` — all components populated, 0–100 |
| 4 | jobflow QE relax / SCF / phonon recipes | **Met** | `siscforge.calculators.qe` — sequential runner + optional jobflow Flow |
| 5 | Formation-energy pre-filter (surrogate stub) | **Met** | `siscforge.surrogates.formation` — heuristic E_hull proxy; GNN deferred |
| 6 | File-based store + ranking by hull + Si-score | **Met** | `EvaluationStore` + ranking weights / hull demotion |
| 7 | CLI enumerate / rank / run | **Met** | `siscforge` CLI; `submit` reserved for later job submission |
| 8 | Dry-run / mock calculator | **Met** | `MockCalculator`; `--dry-run` forces mock |
| 9 | Unit tests + NbN phonon golden (mock) | **Met** | `tests/test_nbn_phonon.py` mock path always green; real QE optional |
| 10 | End-to-end nitride strain series dry-run | **Met** | `examples/nbti_n_strain.yaml` |
| 11 | Provenance + export (JSON/CSV/CIF-ready + cards) | **Met** | JSON/CSV + Markdown synthesis cards; CIF on candidates |
| 12 | Developer can clone + reproduce dry-run campaign | **Met** | `pip install -e ".[dev]"` + docs |

## Explicitly remaining / deferred (not Phase 0 blockers)

| Item | Notes |
|------|--------|
| Trained ALIGNN/MatGL formation-energy GNN | Replaced by heuristic filter; Phase 1+ |
| MongoDB | JSON file store is the Phase 0 fallback |
| Real NbN DFPT matching literature to 15–20% | Requires local QE + UPF; optional pytest gate |
| EPW + Eliashberg | **Phase 1** |
| `siscforge submit` HPC path | Phase 0 uses local `run` only |

## Smoke commands

```bash
pip install -e ".[dev]"
pytest -q
siscforge run --dry-run examples/nbti_n_strain.yaml
siscforge enumerate -c examples/nbti_n_strain.yaml
```

## Configuration highlights

```yaml
formation_filter:
  enabled: true
  max_e_hull_eV_per_atom: 0.25
  max_strain_magnitude: 0.05
  keep_top_n: null

export_formats:
  - json
  - csv
  - markdown
```
