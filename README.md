# SiSC-Forge

**Modular, high-throughput platform for discovery of silicon-compatible superconducting materials.**

SiSC-Forge searches and ranks transition-metal nitrides, B-doped Si, MgB₂/borides, and (later) nickelates/cuprates for elevated-temperature Josephson-friendly superconductivity with CMOS-compatible integration.

## Why search for silicon-compatible superconductors?

Superconducting electronics based on Josephson junctions already demonstrate switching energies and speeds that leave conventional CMOS far behind at cryogenic temperatures. The persistent barrier is integration. Most high-performance superconducting materials are process-hostile to silicon: lattice mismatch, thermal-budget conflicts, chemistry incompatibility, and the absence of mature foundry pathways keep the technology confined to specialized laboratories rather than scalable platforms.

SiSC-Forge is a high-throughput computational discovery engine built to close that gap. It systematically searches and ranks transition-metal nitrides, boron-doped silicon/silicides, MgB₂-family compounds, and (later) nickelates for elevated-temperature, Josephson-friendly superconductivity while scoring quantitative silicon-integration feasibility (epitaxy options, buffers, thermal budgets, chemical windows). The same codebase runs on a single high-end workstation for method development and scales transparently to institutional or cloud HPC for large campaigns.

**For researchers already working the problem**  
The platform couples structure enumeration, graph-neural-network surrogates, first-principles electron-phonon (EPW) and (later) DMFT pathways, multi-objective ranking that includes a Silicon Feasibility Score, and active-learning prioritization. It produces ranked candidate lists accompanied by synthesis-relevant metadata so experimental groups can focus scarce growth and characterization resources on the materials most likely to survive a real CMOS-compatible process flow. Small campaigns and golden-system validation are designed to run on workstation-class hardware; the architecture is ready for production-scale sweeps the moment larger compute is available.

**On scale and resources**  
Materials discovery remains the rate-limiting step between laboratory demonstrations of superconducting logic and anything that can share a process line or package with silicon. A successful silicon-compatible material would open hybrid architectures, simplify quantum-processor integration, and give energy-efficient beyond-CMOS options a realistic manufacturing path.

The software itself is open-source and already productive at small scale. Exhaustive campaigns across the priority families, however, require substantial high-performance computing resources—order-of-magnitude estimates for a full sweep sit in the multi-million-dollar range (roughly $7 M). That cost is outside the reach of most individual researchers, but the same campaign definitions and ranking logic transfer without rewrite from a workstation prototype to large allocations. Applying that level of compute multiplies the probability of identifying experimentally actionable candidates.

**For everyone else**  
Today’s processors typically top out around 5 GHz, limited by heat and internal resistance. Superconducting circuits have already shown they can operate at tens of gigahertz (with simpler laboratory circuits exceeding 100 GHz) while consuming a tiny fraction of the energy.

Finding a material that works with existing silicon manufacturing could make those gains practical. The earliest and largest impact would appear in data centers and high-performance systems that could deliver far more computation for far less power. Over a longer horizon, materials that also operate at higher temperatures could extend similar efficiency benefits toward more everyday devices.

In short: the search is for the missing manufacturing link that would let an already-proven physical advantage leave the laboratory and enter the same industrial ecosystem that already produces the chips we rely on.

## Spec-driven development

| Document | Path |
|----------|------|
| PRD | [`docs/PRD/SiSC-Forge-PRD.md`](docs/PRD/SiSC-Forge-PRD.md) |
| Technical Specifications | [`docs/specs/SiSC-Forge-Technical-Specifications.md`](docs/specs/SiSC-Forge-Technical-Specifications.md) |
| Roadmap | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Setup | [`docs/SETUP.md`](docs/SETUP.md) |

## Current status — **Phase 2 complete; Phase 3 software path complete; Phase 4 Tier-1 complete** (`v0.4.3`)

| Phase | Focus | Status |
|-------|-------|--------|
| **0** | Foundation, mock dry-run, QE phonon | **Done** — [phase0-exit](docs/phase0-exit.md) |
| **1** | EPW + isotropic Eliashberg, goldens, λ/Tc stub, AL prioritization | **Done** — [phase1-exit](docs/phase1-exit.md) |
| **1.5a** | AL bootstrap data hygiene (promotion gate, snapshots, retrain CLI) | **Done** — [phase15-exit](docs/phase15-exit.md) |
| **1.5b** | Trained predictions change rankings; run-loop provenance; operator UX | **Done** — [phase15b-exit](docs/phase15b-exit.md) |
| **2** | Si-integration maturity + ranking polish (P2.1–P2.5) | **Done** — [phase2-exit](docs/phase2-exit.md) |
| 3 | Unconventional / DMFT | **Software path done** — P3.1–P3.6 shipped (DMFT launch + GNN heads residual) |
| 4 | Josephson device metrics | **Tier-1 complete** (P4.1 + P4.2) — analytic + fabrication heuristics; Usadel residual — [phase4-exit](docs/phase4-exit.md) · [phase4-p41](docs/phase4-p41-josephson-tier1.md) · [phase4-p42](docs/phase4-p42-fabrication.md) |


### Phase 1 delivered

- QE phonon + EPW screening path (`pw` → multi-q `ph` → `pp.py` → NSCF → `epw`)
- Isotropic Allen–Dynes / Eliashberg Tc → `performance_score` ranking
- NbN & MgB₂ golden examples (mock always; real EPW optional)
- Family-heuristic λ/Tc **surrogate stub** for pre-filtering
- Minimal **active-learning** top-k prioritization
- File store, CSV/Markdown export, synthesis cards

### Phase 1.5 active-learning flywheel (workstation) — complete

**1.5a** (data hygiene) and **1.5b** (trained predictions + run-loop wiring + operator UX) are both shipped. The trained surrogate is a **family-mean fit**, not an ALIGNN/MatGL production GNN. Exit notes: [phase15-exit](docs/phase15-exit.md), [phase15b-exit](docs/phase15b-exit.md).

Shared AL state lives in **`./al_state`** (or `$SISC_AL_ROOT` / `--al-root`), **not** under each campaign `output_dir`, so labels and models accumulate across runs.

```bash
# 1. Seed goldens (+ optional literature pack — example midpoints only)
siscforge al-seed --al-root ./al_state
siscforge al-seed --al-root ./al_state --from-file docs/examples/literature_seeds.json --no-goldens

# 2. Campaign — prioritization uses the active model when present
siscforge run campaign.yaml -o ./outputs/my_campaign --al-root ./al_state

# 3. After *real* EPW (not --dry-run / mock): preview then promote
siscforge al-promote ./outputs/my_campaign --al-root ./al_state --dry-run
siscforge al-promote ./outputs/my_campaign --al-root ./al_state
siscforge al-train --al-root ./al_state
siscforge al-status --al-root ./al_state   # progress toward ~150 labels

# 4. Next campaign reuses the trained family-mean fit for ranking
siscforge run next.yaml -o ./outputs/next --al-root ./al_state
```

Mock / dry-run evaluations are **hard-refused** by the promotion gate so junk never enters the training set. See [docs/design/active-learning-flywheel.md](docs/design/active-learning-flywheel.md).

### Phase 2 silicon integration + ranking — complete

P2.1–P2.5 are shipped: first-class Si component weights, multi-layer buffer stacks, critical thickness / membrane heuristics, multi-objective ranking + Pareto, and **process-recommendation** synthesis cards with frozen schema `1.0` (`process_recommendations.json`). Exit notes: [phase2-exit](docs/phase2-exit.md). Schema: [process-recommendation-schema](docs/process-recommendation-schema.md).

Validation: [docs/validation-phase1.md](docs/validation-phase1.md).

### Phase 3 unconventional pathway — software path complete

| WP | Focus | Status |
|----|-------|--------|
| P3.1 | DFT+U + `DFTUResult` | **Done** — [phase3-p31-dftu](docs/phase3-p31-dftu.md) |
| P3.2 | Wannier prep + `ready_for_dmft` + nscf/pw2wannier90 | **Done** (P3.2.1 orchestration shipped) — [phase3-p32-wannier](docs/phase3-p32-wannier.md) |
| **P3.3** | TRIQS/solid_dmft + `DMFTResult` | **Scaffold** — model + gate + mock + parser; full launch residual — [phase3-p33-dmft](docs/phase3-p33-dmft.md) |
| **P3.4** | Pairing eigenvalue → `performance_score` | **Done** — [phase3-p34-pairing-score](docs/phase3-p34-pairing-score.md) |
| **P3.5** | O-vacancy / infinite-layer enum | **Done** — [phase3-p35-oxygen-vacancy](docs/phase3-p35-oxygen-vacancy.md) |
| **P3.6** | Mixed conventional/unconventional AL | **Done** — [phase3-p36-mixed-al](docs/phase3-p36-mixed-al.md) |

```bash
siscforge run --dry-run examples/ndnio2_dmft_mock.yaml
siscforge enumerate -c examples/ndnio2_ovac_enumerate.yaml
siscforge run --dry-run examples/ndnio2_ovac_enumerate.yaml
siscforge run --dry-run examples/mixed_al_pools.yaml
```

Nickelate enumeration and DMFT are **off by default**. Conventional nitride / MgB₂ / EPW campaigns are unchanged (`active_learning.pool_mode` defaults to `off`). Mixed campaigns set `pool_mode: joint` or `separate` — see [phase3-p36-mixed-al](docs/phase3-p36-mixed-al.md).

**Still residual:** real solid_dmft/CTHYB launch, production ALIGNN/MatGL λ/Tc heads, golden NdNiO₂ science campaign.

### Phase 4 Josephson metrics — Tier-1 complete (P4.1 + P4.2)

Tier-1 analytic IcRn / Jc / gap proxies (Ambegaokar–Baratoff + BCS-from-Tc fallback) attach to evaluations when `josephson.enabled: true`. **P4.2** adds fabrication-compatibility hints (SIS / SNS / ramp-edge labels, BEOL / thermal flags, stack notes) and an optional presentation-only secondary sort on IcRn / Jc. **Disabled by default.** Always labelled **approximate / ranking only**; fabrication labels are heuristics, not process qualification. **Usadel and BdG remain residual.** Exit notes: [phase4-exit](docs/phase4-exit.md).

```bash
siscforge run --dry-run examples/nbn_mgb2_josephson_tier1.yaml
```


## Quick start (Python only — no QE)

Requires **Python ≥ 3.11**.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest -q
siscforge run --dry-run examples/nbti_n_strain.yaml
siscforge run --dry-run examples/nbti_n_al.yaml
siscforge run --dry-run examples/nbti_n_al_broad.yaml
siscforge shortlist outputs/nbti_n_al_broad -o examples/nbti_n_al_broad_shortlist.yaml
siscforge run --dry-run examples/nbti_n_al_broad_shortlist.yaml
siscforge run --dry-run examples/nbn_epw.yaml
siscforge run --dry-run examples/mgb2_epw.yaml
```

Real QE/EPW: see [docs/SETUP.md](docs/SETUP.md) (Tiers B–C).  
**Desktop shortlist → real EPW:** [docs/examples/desktop_shortlist_epw.md](docs/examples/desktop_shortlist_epw.md).

## Example campaigns

| YAML | Purpose |
|------|---------|
| `examples/nbti_n_strain.yaml` | Nitride strain series (mock) |
| `examples/nbti_n_surrogate.yaml` | λ/Tc surrogate pre-filter |
| `examples/nbti_n_al.yaml` | AL top-k prioritization (small) |
| `examples/nbti_n_al_broad.yaml` | Broader AL + 45°/buffer Si |
| `examples/nbti_n_al_broad_shortlist.yaml` | Top-k EPW shortlist (from AL; resume-safe) |
| `examples/nbti_n_al_refine.yaml` | Refine-tier denser EPW (from shortlist store) |
| `examples/nbn_si_45deg.yaml` | Si-feasibility v0.2 (45° / buffers) |
| `examples/nbn_epw.yaml` | NbN EPW golden |
| `examples/nbn_phonon_qe.yaml` | NbN phonon (real QE) |
| `examples/mgb2_epw.yaml` | MgB₂ EPW golden |
| `examples/mgb2_epw_skeleton.yaml` | **Compat alias** → prefer `mgb2_epw.yaml` |
| `examples/dummy_campaign.yaml` | Minimal CLI smoke |
| `examples/ndnio2_dftu_mock.yaml` | P3.1 DFT+U mock (nickelate) |
| `examples/ndnio2_wannier_mock.yaml` | P3.2 Wannier mock + DMFT gate |
| `examples/ndnio2_dmft_mock.yaml` | P3.3/P3.4 DMFT mock + pairing score |
| `examples/ndnio2_ovac_enumerate.yaml` | P3.5 infinite-layer + O-vacancy enum |
| `examples/mixed_al_pools.yaml` | P3.6 mixed conventional/unconventional AL pools |
| `examples/nbn_mgb2_josephson_tier1.yaml` | P4.1/P4.2 Josephson Tier-1 + fabrication hints (NbN + MgB₂ mock) |

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
