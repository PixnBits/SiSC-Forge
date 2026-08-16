# Implementation Notes

## Slice 29.2 (2026-08-15) — ZrN mesh ladder: k=8³ shrinks finite-q softness

Phonon-first nitride diagnosis. Known-stable rock-salt binaries stay
imaginary across successive densifications. EPW remains frozen.

### Numbers (ZrN ε=0, Fm-3m primitive, a_prim≈3.248 Å → a_cub≈4.59 Å)

| Probe | q | k | ecutwfc | min ω (cm⁻¹) | Γ min | finite-q min | n_q imag / irr |
|-------|:-:|:-:|--------:|-------------:|------:|-------------:|----------------|
| Coarse map | 2³ | 4³ | 50 | ~−80 to −90 | — | — | — |
| Pilot | 3³ | 4³ | 50 | −210.3 | — | — | — |
| Diag q4 | 4³ | 4³ | 50 | **−148.655** | −33.8 | −148.7 (dyn14) | 10 / 24 |
| K-mesh diag | 4³ | 8³ | 60 | **−72.064** | −29.7 | −72.1 (dyn18) | 3 / 24 |

k=8³ / ecut=60 vs k=4³ / ecut=50 on the **same** q=4³: finite-q softness
**shrunk** (−148.7 → −72.1) and **moved**. q4's softest points (dyn14/13/7)
are now fully real; the leftover imaginary branches are dyn18 (−72.1) and
dyn20 (−33.5) plus Γ acoustic noise. That relocation is metallic
electronic-sampling noise, not a fixed lattice instability.

Γ stays ~−30 cm⁻¹ (ordinary acoustic numerical noise). The parser / ph.x
do **not** apply ASR; ASR would not move finite-q −72.

### Pseudos (resolved)

SSSP PBE efficiency pair under `/usr/share/espresso/pseudo`:

- `Zr_pbe_v1.uspp.F.UPF` — GBRV USPP, 12e (4s4p4d5s), PBE
- `N.pbe-n-radius_5.UPF` — Dal Corso USPP, suggested ecutwfc 39 Ry

Not a known-soft pair for phonons. Optical Γ ~469 cm⁻¹ is in the PBE
ballpark. Mixed-set / precision-PAW swap is a later lever, not the first.

### Tooling (this slice)

- `parse_qpoint_spectra` groups `Diagonalizing` blocks; `raw.qpoints` on
  new parses. `asr_applied: false`. Flat `n_modes` / min ω unchanged.
- Soft-mode report: `softness_locus`, Γ vs finite-q min, campaign line
  “softest q is finite-q; Γ only mildly imaginary”. Class
  `likely_mesh_artefact` unchanged (suspect, not proof).
- Next-action hint: densify k / ecut or audit UPF when locus is finite-q;
  do not blindly re-pilot q=3³.
- Example: `examples/zrn_k12_diag.yaml`. Checklist:
  `docs/examples/zrn_nitride_phonon_convergence.md`.

### Next experiment (single cheapest discriminator)

**k=12³, ecutwfc=60, q=4³, ZrN ε=0, Docker QE 7.3.1.** One lever (k).
If min ω stays ~−70 at the same q → k is no longer dominant (try UPF /
ecut=80). If the leftover dyn18/dyn20 branches heal toward Γ-noise,
electronic sampling was the cause. Do not launch EPW. Do not
`--force-rerun` finished dyn sets.

**Out of scope:** composition expansion, EPW, refine, AL of soft cells,
host Ubuntu 6.7 `ph.x` workarounds, stability or Tc claims.

---

## Slice 29.1 (2026-08-14) — Pilot recovery: CRASH-only d_matrix + Provenance

**Provenance (landed):** phonon-only `QECalculator.run()` raised
`UnboundLocalError` on `Provenance` after `ph.x` rc=0 because Wannier/DMFT
except blocks re-imported the name locally. Module-level import is used.
Regression: `tests/test_qe_calculator.py`.

**NbN MPI_ABORT (this slice):** `outputs/nitride_phonon_pilot` NbN ε=0
(`3386c52d`) died with `MPI_ABORT` rank 15. `02_scf/CRASH` is the real
reason:

```
from d_matrix : error # 2
D_S (l=3) for this symmetry operation is not orthogonal
```

`ph.out` only had the MPI abort (plus NULs), so
`phonon_retry_on_d_matrix` never fired and the evaluation was labelled
`MPI_ABORT` instead of `d_matrix`. nproc=16 is **not** excessive on this
16-core machine (ZrN ε=0 finished the same 3³ mesh with 16 ranks).

Fix: read QE `CRASH` sidecars with `ph.out` for classification / retry /
recoverability; treat `d_matrix` / `phq_setup` as recover-unsafe; skip
`recover=.true.` when leftovers are a remediable setup failure and hand
off to the existing nosym SCF+PH retry. Tests in
`tests/test_phonon_failure.py` and `tests/test_qe_checkpoint.py`.

A resume without `QE_BIN` picked up Ubuntu `ph.x` 6.7MaX
(`/usr/bin/ph.x`). QE 6.7 `maxter=100` rejected campaign
`niter_ph=150` (`Wrong niter_ph` / `phq_readin`) in ~1 s. The same
setting is valid on QE 7.3.1 (`maxter=150`). A recover-then-hard-fail
wipe deleted the nearly-complete ZrN ε=-0.04 dyn set (SCF kept).
Follow-up: classify `Wrong niter_ph`; **do not wipe** DFPT artefacts
on namelist `phq_readin`; print resolved `pw.x`/`ph.x` and warn only
for `/usr/bin/ph.x` when `ph_niter>100`.

**Pilot outcome** (QE 7.3.1, `QE_BIN=$HOME/src/q-e-qe-7.3.1/bin`,
resume, `do_epw=false`, nproc=16): all four cells have honest `qe`
evaluations, `status=ok`, no mock rows. ZrN ε=0 DFPT left untouched.
NbN finished on the nosym SCF from the d_matrix retry. ZrN ε=-0.04
and ε=-0.01 re-ran DFPT from intact SCF after the 6.7 wipe.

| cell | min ω (cm⁻¹) | stable | class |
|------|-------------:|:------:|-------|
| ZrN ε=0 | −210.3 | no | likely_mesh_artefact |
| NbN ε=0 | −297.7 | no | likely_mesh_artefact |
| ZrN ε=-0.01 | −212.8 | no | likely_mesh_artefact |
| ZrN ε=-0.04 | −212.2 | no | likely_mesh_artefact |

`n_stable=0`. q=3³ screening does **not** clear the denser-q gate.
Do not launch EPW. Human decides whether to try a still-denser mesh
or abandon. Not a production stability certificate; not a Tc claim.

**Out of scope:** no ecut/q-mesh/pseudo/nproc YAML change; no EPW.

---

## Slice 29 (2026-08-15) — Phonon-map recovery (soft-mode report + denser-q pilot)

**Scope**: Workstation-first recovery when a coarse q=2³ phonon map
returns zero `dynamically_stable` survivors. Additive to the conventional
desktop path (Slice 23). Does **not** change Phase 3/4 status. Does
**not** auto-decide physical stability. Does **not** auto-launch EPW on
soft cells.

Incident: a real Nb–Ti–N map finished with `stable_only` correctly empty;
known-stable binaries (NbN, TiN, ZrN) were also large-imaginary on that
mesh → mesh artefact is the primary suspect.

| Item | Location |
|------|----------|
| Soft-mode report | `siscforge.soft_modes` (`classify_soft_mode`, `write_soft_mode_report`) |
| Store files | `soft_mode_report.json`, `soft_mode_report.md` |
| Pilot helper | `siscforge.pilot` (`build_pilot_campaign`, `do_epw` forced false) |
| CLI | `siscforge soft-modes`, `siscforge pilot`; empty `stable_only` next-action text |
| Auto-write | phonon-containing `siscforge run` (non-blocking) |
| Walkthrough | `docs/examples/nbti_n_phonon_map.md` |
| Contracts | PRD v0.4.4 US10; Specs v0.5.4 §2.3c / AC19–AC22 |
| Tests | `tests/test_soft_modes.py`, `tests/test_pilot.py` |

**Follow-up:** phonon-only `QECalculator.run()` raised `UnboundLocalError`
on `Provenance` after successful `ph.x` because Wannier/DMFT except
blocks re-imported the name locally (making it function-scoped). Those
local imports were removed; module-level `Provenance` / `__version__`
are used. Regression: `tests/test_qe_calculator.py`.

**Out of scope / TODO later:** feed `soft_mode_class` and pilot
provenance into AL acquisition; automatic q-convergence beyond a single
denser pilot; production dynamical-stability certification.

---

## Slice P3.x real launch (2026-08-14) — controlled solid_dmft launcher

Closes the P3.3 residual `p3_x_real_launch` (issue #18). Thin,
testable launcher — not a production CTHYB driver.

| Item | Location |
|------|----------|
| Package writer + invoke | `calculators/qe/dmft_launch.py` |
| Workflow | `run_solid_dmft` in `calculators/qe/dmft.py` |
| Knobs | `DMFTConfig.auto_launch` (default true), `launch_timeout_s` |
| Tests | `tests/test_dmft_real_launch.py` (fakes; no TRIQS required) |
| Docs | `docs/phase3-p33-dmft.md`, SETUP Tier D |

**Automated:** toml/run script/LAUNCH.md; optional invoke; drop-in parse
without TRIQS; copy existing `{seed}.h5`; best-effort DFTTools convert.

**Still residual:** production U/J/β, version matrix, h5 convert without
DFTTools, NdNiO₂ science golden. Mock / conventional / pairing maths
unchanged.

---

## Housekeeping (2026-08-14) — package 0.4.3 + Tier A CI


Package `__version__` / `pyproject.toml` aligned to **0.4.3** (README status).
GitHub Actions `.github/workflows/ci.yml` runs Tier A `pytest` + dummy dry-run
on PR / `main`. Real QE / TRIQS remain local-only.

---

## Slice P4.2 (2026-08-14) — fabrication-compatibility heuristics

**Tier-1 exit closed 2026-08-14** — see `docs/phase4-exit.md`. Usadel residual.

**Scope**: Rule-based SIS / SNS / ramp-edge labels, BEOL / thermal flags,
and stack notes on `JosephsonMetrics.fabrication`. Optional presentation
sort of the Josephson shortlist by IcRn / Jc. Inert when Josephson is
off. No Usadel, no BdG, no PDK, no ranker-math change.

| Item | Location |
|------|----------|
| Heuristics | `siscforge.josephson.fabrication` |
| Model | `JosephsonFabricationHints` nested on `JosephsonMetrics.fabrication` |
| Config | `fabrication_hints` (default on when enabled), `beol_temp_ceiling_c`, `secondary_ranking: none\|icrn\|jc` |
| Attach | same `attach_josephson_metrics` path; secondary sort after attach |
| Export | Additive `josephson_junction_class` / `josephson_fab_*` / secondary columns + card subsection |
| Docs | `docs/phase4-p42-fabrication.md` |
| Tests | `tests/test_josephson_p42.py` |

**Review follow-up**: non-SIS class gets an explicit AB-formula caveat;
`JosephsonConfig.normalize_secondary_ranking` is the single coerce path;
secondary-sort list-order contract documented + logged; README/PRD
`v0.4.3`.

**Out of scope**: Usadel/BdG, foundry PDK, changing AB/BCS or Si-score maths.

---

## Slice P4.1 (2026-08-14) — JosephsonMetrics + Tier-1 analytics

**Scope**: Typed `JosephsonMetrics`, Ambegaokar–Baratoff / BCS-from-Tc
proxies, inert-by-default attachment, caveated CSV + synthesis cards.
No Usadel, no BdG, no fabrication engine, no ranker fork.

| Item | Location |
|------|----------|
| Model | `JosephsonMetrics` (`results.py`); optional `CandidateEvaluation.josephson` |
| Analytics | `siscforge.josephson.tier1` |
| Attach | `attach_josephson_metrics` after `rank_evaluations` (`run` / `rank --config`) |
| Config | `JosephsonConfig.enabled` default **false**; top-N `shortlist_size` |
| Export | Additive `josephson_*` CSV + card section |
| Docs | `docs/phase4-p41-josephson-tier1.md` |
| Tests | `tests/test_josephson_p41.py` |
| Example | `examples/nbn_mgb2_josephson_tier1.yaml` |

**Out of scope**: Usadel/BdG (later), CTHYB launch, production GNN.
Fabrication heuristics + secondary sort landed in **P4.2**.

---

## Slice P3.6 (2026-08-14) — mixed conventional / unconventional AL


**Scope**: Acquisition can operate on a joint pool or separate
conventional / unconventional pools. Default `pool_mode: off` preserves
pre-P3.6 scores and top-k. No CTHYB launch, no GNN heads.

| Item | Location |
|------|----------|
| Pool derivation | `siscforge.active_learning.pools.derive_pool` |
| Quotas | `select_with_quotas` (`floor(frac × k)` + leftover fill) |
| Acquisition | `prioritize_candidates` honours `ActiveLearningConfig.pool_mode`; `siscforge run` loads store evaluations so resume/later cycles can use `performance_score` |
| Config | `pool_mode`, `pool_quotas` (inert default `off`) |
| Provenance | `AcquisitionRecord` / `PrioritizationRecord` / evaluation fields |
| Export | CSV + synthesis cards: `acquisition_pool`, `acquisition_mode` |
| Status | `al-status` per-pool counts when mixed mode has been used |
| Docs | `docs/phase3-p36-mixed-al.md` |
| Tests | `tests/test_al_p36_mixed.py` |
| Example | `examples/mixed_al_pools.yaml` |

**Out of scope**: real CTHYB launch, production GNN, Josephson, pairing/Si maths.

---

## Slice P3.5 (2026-08-13) — oxygen-vacancy / infinite-layer enumeration

**Scope**: First-class, opt-in structure generation for infinite-layer
RNiO₂ (Nd/Pr/La) plus a **curated** O-vacancy / apical-O set. Ordinary
`StructureCandidate`s; no ranking / pairing / AL / DMFT-launch changes.
Status: **shipped on main** (P3.5 complete).

| Item | Location |
|------|----------|
| Builders | `siscforge.structure.nickelates` |
| Enumerate hook | `generator.enumerate_from_config` family `nickelate` |
| Config | `EnumerationConfig.nickelate_*` (inert unless family enabled) |
| Example | `examples/ndnio2_ovac_enumerate.yaml` |
| Docs | `docs/phase3-p35-oxygen-vacancy.md` |
| Tests | `tests/test_nickelates_p35.py` |

Default patterns: stoichiometric IL, ordered single in-plane vacancy
(2×2×1, symmetry-unique), apical-O RNiO₃. Screening only — not defect
thermodynamics.

**Out of scope**: bilayer/cuprate, real CTHYB launch.

---

## Slice P3.4 review (2026-08-13) — language, mock posture, scale, wiring

Addressed Product Owner + Software Engineer review on #14. Mapping /
precedence / ranking contracts unchanged.

| Issue | Fix |
|-------|-----|
| README / ROADMAP said **Done / shipped** while PR open | Tempered to **in review** |
| Mock pairing scores participate in rank unnoticed | CLI yellow banner via `mock_ranking_warning`; phase doc “dry-run ranks on illustrative λ” |
| 25 / 40 look like a physical Tc map | “Why 25 / 40” + mixed-origin prioritization-only note; P3.6 residual |
| Calculators omit `ranking=` | Documented double-apply contract; comments at call sites |
| `"Ni" in formula` mock λ heuristic | Pairing fill uses `material_family == "nickelate"` only |
| Occupancy / m* bounds undocumented | Field descriptions + occupancy min≤max validator |
| Float `!=` fragile with NaN | `_same_score` treats non-finite as not-equal |
| Quality timing | Confirmed `rank_evaluations` assesses after headline source; documented |

**Still residual:** real CTHYB launch, production GNN heads.

---

## Slice P3.4 (2026-08-13) — pairing eigenvalue → performance_score


**Scope**: Map `DMFTResult.leading_pairing_eigenvalue` onto the common
`performance_score` so ranking / Pareto / export stay family-agnostic.
Mock eigenvalues are illustrative. Conventional campaigns unchanged.

| Item | Location |
|------|----------|
| Pure map | `siscforge.scoring.pairing.performance_score_from_pairing` |
| Precedence | `resolve_performance_score` / `apply_performance_score` |
| Config | `dft.dmft.scoring` (`DMFTScoringConfig`); `ranking.performance_precedence` |
| Mock fill | `mock_dmft_result` now sets illustrative λ / `d_x2-y2` |
| Wiring | `MockCalculator`, `QECalculator`, CLI `_finalize_eval`, `rank --config` |
| Trust | `dmft_pairing` / `dmft_pairing_mock` source + quality flags |
| Docs | `docs/phase3-p34-pairing-score.md` |
| Tests | `tests/test_pairing_p34.py` |

Default formula: `score_K = clamp(λ × 25, 0, 40)`. Default precedence:
trusted EPW Tc, else DMFT pairing, else existing mock/surrogate.

**Out of scope**: O-vacancy (P3.5), mixed AL (P3.6), real CTHYB launch.

---

## Slice P3.3 review (2026-08-13) — language, SETUP, residuals

Addressed Product Owner + Software Engineer review on #13. No change to
the mock/gate/export contract; docs and labels made honest about the
thin real path.

| Issue | Fix |
|-------|-----|
| Real path is sidecar + drop-in parser, not a jobflow launcher | Tempered README / ROADMAP / phase docs; operator workflow + residual `p3_x_real_launch` hook |
| SETUP stopped at Tier C | Added **Tier D — optional DMFT (TRIQS / solid_dmft)** |
| Optimistic “Done / shipped” while real launch is residual | Status now “scaffold: model + gate + mock + parser” |
| Three gate knobs easy to misread | Operator mental model + first-match table in `docs/phase3-p33-dmft.md` |
| Mock filling / m* look calibrated | Labelled “illustrative / not literature-validated” in phase doc, example YAML, `raw["physics_label"]`, provenance |
| Residual P3.2.1 easy to miss | Explicit chain diagram in the phase doc |
| Failure classification / unused `n_loops` / `QEDmftCalculator` independence | Documented as best-effort / future / independent of `do_wannier` |

**Still residual (not this slice):** automated solid_dmft launch,
P3.4 pairing → `performance_score`.

---

## Slice P3.3 (2026-08-12) — DMFTResult model and solid_dmft/mock recipe


**Scope**: Third vertical slice of the unconventional (nickelate) pathway.
Adds typed `DMFTResult`, campaign knobs (`dft.do_dmft` / `dft.dmft`, **off
by default**), Wannier `ready_for_dmft` gate, mock dry-run path, and a
thin optional solid_dmft/TRIQS wrapper that skips cleanly when the stack
is absent. Conventional nitride / MgB₂ / AL campaigns are unchanged when
DMFT is disabled. Pairing → `performance_score` is **P3.4**.

| Item | Location |
|------|----------|
| Model | `models/results.py` → `DMFTResult` |
| Evaluation field | `CandidateEvaluation.dmft` (optional) |
| Config | `DMFTConfig` under `DFTConfig.dmft`; `do_dmft` flag |
| Helpers | `calculators/qe/dmft.py` |
| Recipe | `run_dmft_after_wannier`, `run_dmft_workflow` |
| Calculator | `qe-dmft` / `dmft`; additive on `qe` when enabled |
| Mock | fills `DMFTResult` only when enabled |
| Export | `dmft_*` CSV columns + card section |
| Docs | `docs/phase3-p33-dmft.md` |
| Example | `examples/ndnio2_dmft_mock.yaml` |
| Tests | `tests/test_dmft_p33.py` |

**Out of scope**: pairing → performance_score (P3.4), O-vacancy (P3.5),
mixed AL (P3.6), TRIQS as a hard dep.

### Enable

```yaml
dft:
  do_dmft: true
  dmft:
    enabled: true
    solver: mock
    U_eV: 5.0
    J_eV: 0.8
```

```bash
siscforge run --dry-run examples/ndnio2_dmft_mock.yaml
```

---

## Slice P3.1 (2026-08-11) — DFT+U workflow and DFTUResult model

**Scope**: First vertical slice of the unconventional (nickelate) pathway.
Adds typed `DFTUResult`, campaign knobs (`dft.do_dftu` / `dft.dftu`, **off by
default**), QE sequential `pw.x` DFT+U recipe, mock dry-run path, and light
CSV / synthesis-card export. Conventional nitride / MgB₂ / AL campaigns are
unchanged when DFT+U is disabled.

| Item | Location |
|------|----------|
| Model | `models/results.py` → `DFTUResult` |
| Evaluation field | `CandidateEvaluation.dftu` (optional) |
| Config | `DFTUConfig` under `DFTConfig.dftu`; `do_dftu` flag |
| Helpers | `calculators/qe/dftu.py` |
| Recipe | `run_dftu_scf`, `run_dftu_workflow` |
| Calculator | `qe-dftu` / `dftu`; additive on `qe` when enabled |
| Mock | fills `DFTUResult` only when enabled |
| Export | `dftu_*` CSV columns + card section |
| Docs | `docs/phase3-p31-dftu.md` |
| Example | `examples/ndnio2_dftu_mock.yaml` |
| Tests | `tests/test_dftu_p31.py` |

**Out of scope**: Wannier (P3.2), TRIQS/DMFT (P3.3), pairing (P3.4),
O-vacancy (P3.5), mixed AL (P3.6).

### Enable

```yaml
dft:
  do_dftu: true
  dftu:
    enabled: true
    U_eV: 5.0
    hubbard_species: [Ni]
```

```bash
siscforge run --dry-run examples/ndnio2_dftu_mock.yaml
```

---

## Slice P3.1 review fixes (2026-08-11)

Addressed Copilot review on #11:

| Issue | Fix |
|-------|-----|
| Explicit `hubbard_species` mismatch silently remapped | Hard error if any listed element missing from structure |
| `nspin` accepted 3 | `Literal[1, 2, 4]` |
| Dual Hubbard namelist + HUBBARD card | Exactly one dialect via `hubbard_syntax` (`namelist` default / `card`) |
| Site moments parsed charge not magn | Regex prefers `magn:` value |
| Averaged multi-species U/J | Scalar only when all species agree; else `None` + map |
| `qe` + `do_dftu` + no phonon became dftu-only | dftu-only reserved for `force_dftu` (`qe-dftu`) |
| Additive DFT+U re-relaxed | `do_relax=False` unless `do_relax_with_u` |
| CLI `qe-dftu` overwrote explicit phonon/EPW | Respect `model_fields_set` |
| Workflow untested | Mocked `run_dftu_workflow` + resume + optional real-QE gate |
| HUBBARD card emitted full `J` for kind 0 | Emit `J0`; reject kind=1 + nonzero scalar J |

---

## Slice P3.1 review round 2 (2026-08-11)

| Issue | Fix |
|-------|-----|
| `species_type_index` used site order | Atomic-number order via `qe_atomic_type_symbols` (matches PWInput) |
| DFT+U checkpoint ignored U/structure | `siscforge_dftu_config.json` fingerprint; mismatch re-runs |
| `status=ok` without JOB DONE | Require energy **and** JOB DONE |
| `require_real` missing qe-dftu | Include `qe-dftu`/`dftu` so mock rows are not resumed as real |
| Calculator params alias order | Exact name first, then QE alias group |

---

## Slice P3.1 review round 5 (2026-08-12)

| Issue | Fix |
|-------|-----|
| Namelist used `Hubbard_projectors` | Emit classic SYSTEM `U_projection_type` |
| Fingerprint missed UPF content | v3: resolved per-species UPF names + sha256 digests |
| Card dialect silent `3d` for O | `hubbard_manifolds` + hard error for non-TM/RE |
| `do_relax_with_u` ignored when `do_relax=False` | Enter relax when either flag is true; additive forces `do_relax=True` |
| `is_metallic` guessed from Fermi/smearing | Leave `None` until gap/DOS parse exists |
| Empty alias `parameters: {}` fell through to `qe` | Track match separately from map emptiness |
| Soft real-QE gate | UPF preflight skip; require `status=ok` + energy |

---




## Slice 28 (2026-08-07) — Phonon `phq_setup` / FFT–symmetry diagnosis + nosym retry

**Scope**: Phonon-map campaigns (`do_epw=false`) were mislabeling
`phq_setup` / `FFT grid incompatible with symmetry` as
`EPW: k-grid inconsistency` because ordinary `ph.out` lines like
`number of k points` matched EPW needles. Ordered ternaries (e.g.
NbₓTi₁₋ₓN at ε=0) fail setup in ~2 s while binaries finish DFPT fine.

Fix: phonon-aware primary reasons, one nosym+noinv SCF/PH retry (same shape
as d_matrix), and no fake dynamical stability from setup failures.

| Item | Location |
|------|----------|
| Fingerprints | `is_phq_setup_fft_symmetry_failure`, `is_phq_setup_failure` |
| Step-aware primary | `extract_primary_failure_reason(..., step_name="phonon")` skips EPW-only needles |
| Retry | `_maybe_retry_phonon_setup` (d_matrix + FFT/symmetry) |
| Config | `dft.phonon_retry_on_fft_symmetry` (default true) |
| Stability safety | failed / empty phonon → `dynamically_stable=False`; shortlist ignores |
| Tests | `tests/test_phonon_failure.py`, fixture `ph_fft_symmetry_error.out` |

### CLI examples

```text
[3/40] Nb0.25Ti0.75N strain=+0.000 — failed (phonon: FFT grid incompatible with symmetry (phq_setup))
phonon failed (FFT grid incompatible with symmetry) — retrying once with nosym+noinv SCF/PH
fft_symmetry retry: phonon succeeded after nosym SCF
# or:
fft_symmetry retry: phonon still failed after nosym SCF (setup failure — not a dynamical-stability conclusion)
```

### Disable retry

```yaml
dft:
  phonon_retry_on_fft_symmetry: false
  phonon_retry_on_d_matrix: false   # independent knob
```

### Resume failed candidates only

```bash
# Same campaign + output_dir; successful phonons skip; failed re-run with diagnose/retry
siscforge run --calculator qe examples/nbti_n_phonon_map.yaml
# Do NOT --force-rerun (would redo all DFPT)
```

---

## Slice 27 (2026-08-07) — Phase B: Wannier90 `search_shells` after nk ladder

**Scope**: After Phase A coarse-k remediation (6→8→12) is exhausted, EPW can
still die with `kmesh_get_bvector: Not enough bvectors found` on strained
low-symmetry supercells (e.g. Nb0.25Ti0.75N refine: matched NSCF at 8³ and
12³, ~188 h DFPT intact). **Denser nk alone is the wrong lever.** Phase B
retries **EPW-only** with larger Wannier90 neighbour-shell search
(`search_shells` 12→36→48 via EPW `wdata`). Phonon / DFPT remain sacred.
No material-specific projections, DMFT, or Josephson.

| Item | Location |
|------|----------|
| Input knobs | `EPWConfig.search_shells`, `kmesh_tol` → `build_epw_input` `wdata(*)` |
| Shell ladder | `next_search_shells_after_bvector_failure` (36, 48) |
| Planner | `plan_kmesh_remediation` → Phase A `nkc` then Phase B `search_shells` |
| Loop / resume | `_retry_epw_with_denser_k`, prior-`epw.out` path in `run_relax_scf_phonon_epw` |
| Sidecar phases | `siscforge_epw_remediation.json` field `phase`: `nkc` | `search_shells` |
| Config | `auto_retry_search_shells`, `max_search_shells_retries` (default 2) |
| Tests | `tests/test_epw_coarse_k.py` (Phase B plan, anti-loop, EPW-only) |

### Policy

| Phase | Trigger | Action | NSCF | DFPT |
|-------|---------|--------|------|------|
| A | `kmesh_bvector` / k-grid, denser nkc available | nkc 6→8→12 (max 2) | rebuild | keep |
| B | still `kmesh_bvector`, nk ladder exhausted | `search_shells` 36→48 (max 2) | **reuse** | keep |
| done | both exhausted | fail with actionable notes | — | keep |

```text
EPW failed (kmesh_get_bvector @ nk=12) — nk ladder exhausted; retrying EPW-only with search_shells=36 (DFPT reused)
```

`kmesh_tol` is **not** auto-loosened (conservative). Set `epw.kmesh_tol` in YAML
only if you intentionally accept a looser shell match.

### Resume (existing 8→12 workdir)

If `siscforge_epw_remediation.json` already has Phase A only (e.g. 8→12), the
next resume **without** `--force-rerun` applies Phase B. Do not manually delete
phonon/`dyn`/`_ph0`.

### Limitations (explicit)

- Phase B may still fail on pathological cells; it is **not** a guarantee of λ/Tc.
- Material-specific Wannier projections remain **out of scope** for auto path.
- Do not invent default `nk=16`; further denser k is a human YAML choice.

### Safe re-run

```bash
siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
# Expect: skip phonon; Phase B search_shells log lines if nk ladder already done
```

---

## Slice 26 (2026-08-07) — Stale NSCF invalidation when EPW coarse k rises

**Scope**: After multi-day DFPT, raising EPW coarse k (`nkc` / `nk1–3`) must
**never** leave a successful but **stale** NSCF in place. Resume / preflight
compare the NSCF k-mesh fingerprint to the campaign `nkc` and invalidate
NSCF + EPW electronic outputs only. Phonon / DFPT (`ph.out`, `*.dyn*`, `_ph0`,
dvscf) are sacred. Users must **not** manually `rm nscf.out`. No DMFT,
Josephson, GNN, or material-specific Wannier projections.

| Item | Location |
|------|----------|
| Mesh fingerprint + match | `qe_checkpoint.nscf_matches_epw_coarse_k`, `inspect_nscf_vs_epw_coarse_k` |
| Sidecar on NSCF success | `siscforge_nscf_kmesh.json` via `write_nscf_kmesh_sidecar` |
| Invalidate electronic only | `invalidate_nscf_epw_for_kmesh` (never phonon) |
| Resume / probe | `probe_nscf(..., expected_nkc=)` + `probe_workdir` |
| Preflight + resume wiring | `run_relax_scf_phonon_epw` after preflight |
| k-grid / XML remediable class | `is_kgrid_inconsistency` (+ stale-NSCF rebuild at same nkc) |
| Ladder (unchanged) | 6→8→12, max 2, `siscforge_epw_remediation.json` |
| Tests | `tests/test_qe_checkpoint.py`, `tests/test_epw_coarse_k.py` |

### Production incident (follow-on to Slice 25)

- ~188 h DFPT (JOB DONE, full dyn mesh) for Nb0.25Ti0.75N refine/production
- Slice 25 preflight raised `nkc` 6→8; `epw.in` correctly had `nk1–3=8`
- Resume still treated existing `nscf.out` as complete (`number of k points = 216` = 6³)
- EPW died in ~2 s (k-grid inconsistency / fatal XML read on save); no remediation
  sidecar written; user had to manually `rm nscf.out epw.out epw.in`

### Comparison rule

Prefer **requested** mesh over fragile symmetry-reduced counts alone:

1. `siscforge_nscf_kmesh.json` (written when NSCF-for-EPW succeeds)
2. `nscf.in` `K_POINTS crystal` count / dims
3. `nscf.out` `number of k points` product (last resort; EPW NSCF is full crystal)

### CLI one-liner

```text
nkc changed or NSCF/EPW k-mesh mismatch — invalidating NSCF (phonon reused)
```

### Resume refine after coarse-k raise (no manual file deletion)

```bash
# Same campaign YAML + same output_dir; finished DFPT is skipped automatically.
# Stale NSCF (e.g. 6³ after nkc→8) is invalidated and rebuilt; no rm needed.
siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
```

Do **not** use `--force-rerun` just to clear NSCF — that redos DFPT.

### Policy

| Situation | Action |
|-----------|--------|
| Preflight raises nkc | Invalidate NSCF + EPW; keep phonon |
| On-disk NSCF mesh ≠ campaign nkc | Same invalidation; rebuild NSCF |
| EPW k-grid / XML fail, phonon complete, NSCF stale | Rebuild NSCF at **current** nkc then epw |
| EPW `kmesh_get_bvector` after mesh matches | Phase A denser nkc (6→8→12); Phase B `search_shells` (36→48) — see Slice 27 |
| `--force-rerun` | Full redo (unchanged) |

---


## Slice 25 (2026-08-05) — EPW coarse-k Wannier safety + post-DFPT auto-remediation

**Scope**: Prevent multi-day DFPT from being wasted when EPW aborts in seconds on
a known-bad Wannier coarse k-mesh (`kmesh_get_bvector: Not enough bvectors`).
Auto-raise unsafe `nkc` before DFPT; on remediable EPW failure **after** phonon
is complete, retry **EPW-only** (re-NSCF + epw.x) with denser coarse k.
**Never** delete or re-run finished DFPT/phonon. No DMFT, Josephson, GNN, or
material-specific orbital projections.

| Item | Location |
|------|----------|
| Min coarse k / auto-raise | `epw_inputs.minimum_coarse_k_dim`, `ensure_wannier_safe_nkc` |
| Pre-DFPT preflight | `epw_inputs.preflight_epw_grids` + CLI campaign start + `run_relax_scf_phonon_epw` |
| `recommended_grids` workstation_dense | `nkc: [8,8,8]` (was 6³) |
| Diagnose fingerprint | `kmesh_get_bvector` / not enough bvectors → class `kmesh_bvector` |
| EPW-only retry policy | `plan_kmesh_remediation`, `_retry_epw_with_denser_k` (6→8→12, max 2) |
| Attempt sidecar | `siscforge_epw_remediation.json` in workdir (resume anti-loop) |
| Config knobs | `epw.auto_retry_kmesh`, `max_kmesh_retries`, `strict_coarse_k` |
| Refine defaults | `default_refine_dft` emits nkc≥8³ for supercells |
| Tests | `tests/test_epw_coarse_k.py` |

### Production incident (Nb0.25Ti0.75N refine)

- ~188 h DFPT (36 q, JOB DONE) then EPW failed in ~2 s with
  `kmesh_get_bvector: Not enough bvectors found`
- Generated epw.in had `nk=6`, `nq=4` (nq correctly matched DFPT; k was wrong)
- Resume skipped DFPT but replayed the same broken EPW inputs

### Policy

| Tier | n_atoms ≥ 8 | Notes |
|------|-------------|--------|
| screening | min 6³ allowed | Documented risk; order-of-magnitude only |
| workstation_dense / production | **min 8³** | Auto-raise 6→8 with log line; nq unchanged |

```text
EPW coarse k raised to 8×8×8 (Wannier safety; was 6×6×6; nq unchanged to match DFPT)
EPW failed (kmesh_get_bvector @ nk=6) — retrying EPW-only with nk=8 (DFPT reused)
```

### Resume

- Successful phonon remains skippable (JOB DONE + dyn probes).
- Failed EPW with remediable class + retries remaining → denser nkc, not identical replay.
- `--force-rerun` still forces full redo (vc-relax → DFPT → EPW).
- Never auto-change `nqc` / qpoints for an existing finished DFPT workdir.

### Limitations (explicit)

- Material-specific Wannier projections still out of scope (`proj=random`).
- Auto-raised nk does **not** guarantee physical λ/Tc — trust layer still applies.
- Cap 2 k-mesh retries (8 then 12), then Phase B `search_shells` (36→48); further
  failures need human / hand-tuned projs (Slice 27).

### Safe re-run of an existing refine campaign (no --force-rerun)

```bash
# After pull: same campaign YAML + same output_dir
siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
# Finished phonon skipped; EPW re-launches with Wannier-safe nkc if needed
# Slice 26: stale NSCF after nkc raise is auto-invalidated (no manual rm)
```

### Manual checks after λ/Tc appears

1. Imaginary modes in `ph.out` / `PhononResult` (trust layer may still flag unreliable)
2. `result_quality` / quality flags after ranking
3. λ not pathologically inflated vs family expectations

---

## Slice 24 (2026-08-03) — Phonon failure UX: Errno 36 + d_matrix

**Scope**: Fix the machine-2 phonon-map failure mode where `ph.x` dies with
PAW ``d_matrix`` / non-orthogonal ``D_S``, but SiSC-Forge reported
``[Errno 36] File name too long`` with the entire PHONON log pasted as a
"path". Optional one-shot recovery for d_matrix. No grid / EPW / DMFT changes.

| Item | Location |
|------|----------|
| Safe path-or-text loader | `parser.resolve_text_or_path` (all `parse_*_output`) |
| Fingerprints | `epw_recipes._EPW_FAILURE_HINTS` (`d_matrix`, `not orthogonal`, …) |
| Diagnose | `diagnose_qe_step_failure`, `is_d_matrix_failure`, `truncate_for_notes` |
| Optional retry | `recipes._maybe_retry_phonon_d_matrix` + `DFTConfig.phonon_retry_on_d_matrix` |
| CLI | `_primary_failure_hint` + truncated exception path |
| Fixture / tests | `tests/fixtures/qe/ph_d_matrix_error.out`, `tests/test_phonon_failure.py` |

### Root cause (Errno 36)

`run_relax_scf_phonon` passed the full `ph.out` body into `parse_ph_output`,
which called `Path(log_blob).is_file()`. On Linux, multi-KB pathnames raise
``OSError: [Errno 36] File name too long``, burying the real QE error.

### d_matrix retry policy (default **on**)

When `ph.x` fails and the log matches d_matrix / non-orthogonal D_S:

1. Log: `phonon d_matrix / D_S not orthogonal — one retry: re-SCF with nosym…`
2. Clean phonon partials; re-run **SCF once** with `nosym=.true.` + `noinv=.true.`
3. Re-run **phonon once** (no recover from the broken DFPT)
4. Still fail → `status=failed` with primary reason + workdir + short tail
5. **Never** mark success without JOB DONE + parseable phonon result

Disable: `dft.phonon_retry_on_d_matrix: false`.

### Example CLI line (NbN strain=0)

```text
[3/42] NbN strain=+0.000 — failed (QE phonon: d_matrix — D_S (l=2) symmetry not orthogonal)
```

Notes include `work_dir=…`, fingerprint hints, and a truncated `ph.out` tail —
not Errno 36.

---

## Slice 23 (2026-08-02) — Phonon-first / stability-gated discovery

**Scope**: Second-workstation path to map dynamical stability before any EPW.
Addresses poor ROI when screening EPW shortlists return only
`result_quality=unreliable` (imaginary modes + high/extreme λ). No DMFT, JJ,
trained GNN, or full SQS.

| Item | Location |
|------|----------|
| Stability filter + shortlist modes | `siscforge.shortlist` (`stable_only`, `stable_or_soft`) |
| CLI | `siscforge shortlist --mode stable_only`, `--soft-min-cm1`, `--stable-sort` |
| Phonon-only run path | `cli/main.py` — skip EPW npool/Wannier preflight when `do_epw=false` |
| Rank | `--stable-first`; auto stable-first on phonon-only `run` |
| Example | `examples/nbti_n_phonon_map.yaml` |
| Walkthrough | `docs/examples/nbti_n_phonon_map.md` |
| Tests | `tests/test_shortlist.py` (stable_only / none-stable / config load) |

### Two-machine loop

```bash
# Machine 2 — broad phonon map (screening 2³ q, not refine 4³)
siscforge run --dry-run examples/nbti_n_phonon_map.yaml
siscforge run --calculator qe examples/nbti_n_phonon_map.yaml

# Stable survivors → EPW shortlist (fails clearly if none stable)
siscforge shortlist outputs/nbti_n_phonon_map \
  -o examples/nbti_n_phonon_map_epw.yaml --mode stable_only -n 6

# Machine 1/2 — EPW on survivors only
siscforge run --calculator qe-epw examples/nbti_n_phonon_map_epw.yaml
```

### Modes

| Mode | Behavior |
|------|----------|
| `stable_only` | `phonon.dynamically_stable` + no imag modes + status ok/mock; sort by Si |
| `stable_or_soft` | `min_frequency_cm1 >= soft_min_cm1` (default 0); no silent unstable fallback |
| existing | `al_selected` / `top_acquisition` / `top_rank` unchanged |

### Limitations

- Coarse phonon grids can still mis-label stability
- Does not replace denser-grid refine for production claims
- Mock dry-run invents ~15% imaginary modes for realism
- **Follow-on (Slice 29):** none-stable maps now get a soft-mode report and
  a guided denser-q pilot instead of a dead-end CLI error. Still a gate.

---

## Slice 22 (2026-07-29) — Desktop pause/resume + QE DFPT recover

**Scope**: When multi-hour DFPT (`ph.x`) is interrupted, prefer QE-native
`recover=.true.` over wiping partial phonon outputs — with a **safe fallback**
to the previous clean + full-step restart. Clarifies product pause/resume vs
Folding@home-style mid-iteration checkpoints. Time estimation remains Slice 21.

| Item | Location |
|------|----------|
| Recoverability probe | `qe_checkpoint.assess_phonon_recoverability` |
| ph.x recover flag | `inputs.build_ph_input(recover=…)`, `recipes.run_ph(recover=…)` |
| Resume wiring | `recipes._run_ph_with_optional_recover` (phonon + EPW DFPT) |
| CLI interrupt UX | `cli/main.py` Ctrl+C → re-run same command message |
| Tests | `tests/test_qe_checkpoint.py` (fixture workdirs, no real QE) |
| Walkthrough | `docs/examples/desktop_shortlist_epw.md` |

### Product model (honest)

| Layer | What resume does |
|-------|------------------|
| Campaign | Skip candidates with successful store evaluations (Slice 13) |
| Mid-step | Skip completed vc-relax / SCF / finished phonon / EPW steps (Slice 15) |
| **DFPT recover (this slice)** | Incomplete phonon with promising dyn/`_ph0`/dvscf → `ph.x` + `recover=.true.` |
| Fallback | Unrecoverable or hard recover failure → clean phonon outputs + full `ph.x` |

**Not** Folding@home: we do **not** checkpoint arbitrary mid-iteration SCF/DFPT
state outside what QE itself writes. Pause = kill/sleep/power loss is safe at
the **process** level; resume = re-issue the same
`siscforge run --calculator qe-epw <yaml>` command.

### Conservative recoverable criteria

Incomplete phonon (`ph.out` lacks `JOB DONE`) **and** at least one of:

- non-empty `{prefix}.dyn*`
- non-empty `_ph0/`
- non-empty `{prefix}.dvscf*` / `dvscf*`

**and** no hard recover-unsafe markers in `ph.out` (`cannot recover`,
`error reading recover`, …). Prefer false full restart over trusting a corrupt
mesh.

### Log lines

```text
resuming DFPT with QE recover=.true. (incomplete DFPT with promising on-disk artifacts [dyn×3, _ph0/])
DFPT recover failed or unsafe — full phonon step restart
running DFPT / phonon
```

Never mark a candidate `JOB DONE` / success unless QE output still parses as
complete after recover.

### Kill-during-ph.x (after this slice)

1. Campaign store: no success → candidate still selected.
2. Workdir: skip vc-relax, skip SCF, incomplete phonon.
3. If dyn/`_ph0`/dvscf look good → **recover path** (artifacts kept).
4. Else or recover hard-fails → clean + full phonon restart (prior behavior).
5. Incomplete EPW (`epw.x`) still full step restart (no fragile EPW recover).

### Limitations

- Mid-SCF-iteration / mid-Broyden pause still not supported (pw.x restart is a
  separate problem; incomplete SCF re-runs that step).
- phonopy_fd path unchanged (band.yaml completeness only).
- EPW incomplete: clean + re-run step only.
- QE built with interrupted runs under `reduce_io=.true.` cannot recover (we do
  not set `reduce_io`).

---


## Slice 21 (2026-07-29) — Desktop walltime expectation UX

**Scope**: Order-of-magnitude walltime bands before expensive QE/EPW work, plus
optional progress-based remaining-time hints on heartbeats. **Not** a guarantee;
machine load and convergence dominate. No physics/ranking changes.

| Item | Location |
|------|----------|
| Estimator | `siscforge.walltime` |
| Config | `RunConfig.estimate_walltime` (default true), `walltime_scale`, `heartbeat_eta` |
| CLI | printed at start of real `qe` / `qe-epw` campaigns (mock unchanged) |
| Heartbeats | `recipes._run_cmd` appends remaining hint when ph.out progress is real |
| Tracker | in-memory observed walltimes refine messaging for later candidates |
| Tests | `tests/test_walltime.py` (no real QE) |

### Estimation inputs
- quality_tag / inferred tier (`screening` | `workstation_dense` | `production`)
- n_atoms (from CIF / metadata; default 8 for ternary shortlists)
- nproc, q-mesh product (`epw.nqc` or `qpoints`), EPW fine grids (`nkf`)
- number of expensive candidates (sequential desktop total)

### Example startup (screening shortlist, 6 candidates, nproc=8)
```text
Estimated walltime (heuristic, not a guarantee):
  per candidate: DFPT ~47 min – 6.3 h; full candidate (relax→EPW) ~1.6–9.4 h on ~8 cores (order-of-magnitude)
  this campaign (~6 candidates, sequential): ~9.4 h – 2.4 d
  Tip: safe to interrupt; re-run the same command to resume finished steps/candidates.
```

### Example startup (workstation_dense refine, 2 candidates, nproc=16)
```text
Estimated walltime (heuristic, not a guarantee):
  per candidate: DFPT ~12 h – 2 d; full candidate (relax→EPW) ~24 h – 3 d on ~16 cores (order-of-magnitude)
  this campaign (~2 candidates, sequential): ~2–6 d
  Tip: safe to interrupt; re-run the same command to resume finished steps/candidates.
```

### Heartbeat remaining-time (optional)
When `ph.out` yields a parseable fraction (q-point i/N), heartbeats may show:
```text
  [heartbeat] phonon / DFPT (ph.x) +EPW-prep still running — elapsed 12h05m;
  healthy (log growing); log=8200 KiB; peek: Calculation of q = …;
  progress q 3/8; ~X–Y remaining (rough)
```
If progress is not parseable, the existing heartbeat is unchanged (no fake ETA).

### Knobs
| Config | Default | Meaning |
|--------|---------|---------|
| `run.estimate_walltime` | true | Print bands at campaign start (qe/qe-epw only) |
| `run.walltime_scale` | 1.0 | Multiply bands (e.g. 2.0 for a slow box) |
| `run.heartbeat_eta` | true | Allow remaining-time hint when progress is real |

Disable estimates with `run.estimate_walltime: false` in the campaign YAML.

---

## Slice 20 (2026-07-27) — Refine-from-store denser EPW

**Scope**: Promote shortlist winners to denser grids without re-enumerating.

| Item | Location |
|------|----------|
| Selection + DFT presets | `siscforge.refine` |
| CLI | `siscforge refine <store> -o refine.yaml` |
| Tiers | `workstation_dense` (default), `production` via `recommended_grids` |
| Specs | same `candidate_specs` as shortlist (CIF × strain) |
| Tests | `tests/test_refine.py` |

```bash
siscforge refine outputs/nbti_n_al_broad_shortlist \
  -o examples/nbti_n_al_refine.yaml --mode top_si -n 2 --tier workstation_dense
siscforge run --calculator qe-epw examples/nbti_n_al_refine.yaml
```

Separate `output_dir`; `quality_tag: production`; denser nkf/nqf/nqc than
screening shortlist; `npool=nproc`. Trust layer re-assesses after refine —
random Wannier may remain until a projection library lands.

---

## Slice 19 (2026-07-27) — Result-quality / trust layer

**Scope**: Prevent inflated screening EPW λ/Tc from silently dominating ranking.
Trust layer only — **not** denser-grid refinement or production Wannier.

| Item | Location |
|------|----------|
| Assessment | `siscforge.quality.assess_result_quality` |
| Config | `QualityConfig` (under `ranking.quality`) |
| Ranking | penalties in `compute_composite_score` |
| Fields | `CandidateEvaluation.result_quality`, `quality_flags`, `quality_notes` |
| Export | CSV/JSON/cards/one-pagers |
| CLI | `Qual` column; Perf marked `*` (suspect) / `!!` (unreliable) |
| Tests | `tests/test_quality.py` |

### Defaults
| Knob | Default | Effect |
|------|---------|--------|
| `lambda_suspect_above` | 3.0 | → `high_lambda`, tier `screening_suspect` |
| `lambda_unreliable_above` | 8.0 | → `extreme_lambda`, tier `unreliable` |
| `min_frequency_cm1_soft` | 50 | soft modes flag |
| `imaginary_modes_unreliable` | true | imag modes → `unreliable` |
| `suspect_performance_penalty` | 0.45 | × composite |
| `unreliable_performance_penalty` | 0.15 | × composite after zeroing Tc term |
| `hard_zero_screening_high_lambda` | true | `#44` zero the performance term when random-Wannier / coarse grids co-occur with high/extreme λ (`screening_high_lambda` flag). Soft multiply remains for milder cases. Same rule refuses conventional promotion. |

Raw λ/Tc always retained. Next step for citation-quality: refine-from-store
production-tier campaign (denser grids, tuned Wannier).

---

## Slice 18 (2026-07-27) — QE/EPW progress heartbeats

**Scope**: Desktop visibility during multi-hour `ph.x` / `pw.x` / `epw.x` steps.

| Item | Location |
|------|----------|
| Config | `RunConfig.heartbeat_seconds` (default **900**, `0` = off) |
| CLI | `--heartbeat-seconds N` |
| Runner | `recipes._run_cmd` (Popen + timed wait + log peek) |
| Labels | vc-relax / SCF / phonon / nscf / epw.x |

Example line:

```text
  [heartbeat] phonon / DFPT (ph.x) +EPW-prep still running — elapsed 45m12s;
  healthy (log growing); log=2100 KiB; peek: Representation #  3 mode #  2
```

---

## Slice 17 (2026-07-25) — Screening Wannier defaults + failure UX

**Scope**: Stop predictable Wannier frozen-window aborts on supercells, and
surface the real reason in CLI / notes without opening `epw.out`.

| Item | Location |
|------|----------|
| `default_nbndsub_screening` | `epw_inputs.py` |
| Tight frozen window (screening) | `_wannier_window_lines(..., screening_tight_froz=True)` |
| Primary reason + expanded diagnose | `extract_primary_failure_reason`, `diagnose_epw_failure` |
| One retry on froz overflow | `run_epw` + `wannier_retry_on_froz_overflow` |
| CLI one-liner | `_primary_failure_hint` in `cli/main.py` |
| Tests | `tests/test_wannier_screening.py` |

### nbndsub policy (screening, `auto_nbndsub: true`)
```
nbndsub = min(nbnd, max(16, 4 * n_atoms, nbnd // 2))
```
Example: 8-atom cell, `nbnd=64` → **32** (not 10).

### Frozen window (screening)
Outer `dis_win` still wide around E_F; frozen window tightened to roughly
`[E_F−3, E_F+1]` eV so random projs + moderate nbndsub are viable.

### Retry
If epw.out matches frozen-window overflow and screening +
`wannier_retry_on_froz_overflow`: one re-launch with
`nbndsub → min(nbnd, max(2×old, old+8))`, reusing save/nscf. Log:
`EPW Wannier retry: frozen-window overflow — nbndsub A→B`.

### Failed CLI example
```text
[2/6] Nb0.25Ti0.75N strain=-0.030 — failed (EPW Wannier: frozen window has more states than nbndsub)
```
Notes include primary fingerprint, workdir, 30-line tail, remediation.

---

## Slice 16 (2026-07-25) — EPW parallel topology (nproc / npool)

**Scope**: Prevent `epw.x` abort after multi-hour DFPT when
`mpirun -np N` is used with `npool=1` (or `-npool` omitted).

| Item | Location |
|------|----------|
| Validation | `calculators/qe/epw_parallel.py` |
| Auto-fix + launch | `resolve_epw_launch_topology`, `run_epw` |
| Campaign warning | `cli/main.py` (qe-epw path) |
| Config | `EPWConfig.npool`, `EPWConfig.strict_parallel` |
| Tests | `tests/test_epw_parallel.py` |

### Rule (fine-grid / SiSC-Forge path)
`nproc == npool × nimage` with **nimage = 1** → **`npool` must equal `nproc`**.

### Default auto-fix
If topology is inconsistent and `strict_parallel` is false:

```text
EPW parallel: auto-set npool=N to match nproc=N (nimage=1)
```

Always pass `-npool N` on the `epw.x` command (including N=1).

### Desktop YAML
```yaml
dft:
  nproc: 8
  epw:
    npool: 8
    # strict_parallel: true  # fail instead of auto-set
```

---

## Slice 15 (2026-07-25) — Mid-step QE/EPW workdir checkpoint resume

**Scope**: Inside one candidate’s `qe_work/<formula>_<id>/`, reuse successful
upstream steps after a kill during DFPT/EPW. Complements campaign-level resume
(Slice 13).

| Item | Location |
|------|----------|
| Step probes | `calculators/qe/qe_checkpoint.py` |
| Phonon recipe | `run_relax_scf_phonon(..., resume_qe_steps=)` |
| EPW recipe | `run_relax_scf_phonon_epw(..., resume_qe_steps=)` |
| Config | `RunConfig.resume_qe_steps` (default true), `force_rerun_qe_steps` |
| CLI | `--force-rerun` also forces QE step re-runs |
| Tests | `tests/test_qe_checkpoint.py` (fixture workdirs, no real QE) |

### Step graph
`vc-relax` → `scf` → `phonon` → (`epw_pp` → `nscf` → `epw` when EPW enabled)

### Success probes (conservative)
| Step | Complete when |
|------|----------------|
| vc-relax | `01_relax/vc-relax.out` has JOB DONE + parseable CELL_PARAMETERS |
| scf | `02_scf/scf.out` JOB DONE + energy + `*.save` present |
| phonon | `ph.out` JOB DONE (+ dyn mesh files when EPW); partial ph.out ⇒ incomplete |
| epw_pp | non-empty `02_scf/save/` |
| nscf | `nscf.out` JOB DONE + parseable energy |
| epw | `epw.out` JOB DONE + parseable λ or Tc |

Incomplete non-phonon steps: clean that step’s partial outputs only, then re-run
from the start of that step. **Incomplete DFPT** may use QE `recover=.true.`
when artifacts look safe — see **Slice 22**.

### Kill-during-ph.x scenario
1. Campaign store has no successful evaluation → candidate is selected to run.
2. Workdir probe: skip vc-relax, skip SCF, incomplete phonon.
3. Logs: `skip vc-relax (checkpoint)`, `skip SCF (checkpoint)`, then either
   `resuming DFPT with QE recover=.true.` or `running DFPT / phonon` (full restart).
4. Charge density `*.save` kept; dyn/`_ph0` kept on recover path only.
5. `--force-rerun` / `force_rerun_qe_steps`: no step skips / no recover.

### Limitations
- Not arbitrary mid-iteration pause (see Slice 22 vs Folding@home).
- Phonopy FD mid-step only checks `band.yaml` completeness.
- Campaign-level skip still wins when the evaluation is already successful.

---

## Slice 14 (2026-07-25) — Desktop shortlist → real EPW

**Scope**: Practical path from AL dry-run top-k to real `qe-epw` without
re-enumerating the full composition×strain grid.

| Item | Location |
|------|----------|
| Selection + YAML builder | `siscforge.shortlist` |
| Exact formula×strain rows | `EnumerationConfig.candidate_specs` / `CandidateSpec` |
| CLI | `siscforge shortlist <store_dir> -o shortlist.yaml` |
| Walkthrough | `docs/examples/desktop_shortlist_epw.md` |
| Tests | `tests/test_shortlist.py` |

### Workflow
```bash
siscforge run --dry-run examples/nbti_n_al_broad.yaml
siscforge shortlist outputs/nbti_n_al_broad -o examples/nbti_n_al_broad_shortlist.yaml
siscforge run --dry-run examples/nbti_n_al_broad_shortlist.yaml   # smoke
siscforge run --calculator qe-epw examples/nbti_n_al_broad_shortlist.yaml
# re-run same command after interrupt → skip ok, continue failures
```

### Design notes
- Shortlist YAML embeds CIFs so structures match the AL dry-run.
- Separate `output_dir` from the dry-run store.
- Resume for `qe`/`qe-epw` uses `require_real=True` so **mock** dry-run hits
  do not block real EPW.
- Failed QE/EPW evaluations store workdir + `diagnose_epw_failure` in notes.
- Mid-step workdir resume: Slice 15.

### Remaining Phase 2 (not this slice)
Multi-layer stacks, critical thickness / membrane, interface slabs, denser
production Wannier, full AL retrain.

---

## Slice 13 (2026-07-25) — Resume / checkpoint for multi-candidate runs

**Scope**: Desktop-friendly re-launch after interrupt for EPW shortlists (and mock).

| Item | Location |
|------|----------|
| Success + fingerprint | `siscforge.resume` |
| Store append / lookup | `EvaluationStore.append_evaluation`, `.find_successful` |
| Run knobs | `CampaignConfig.run` → `RunConfig` |
| CLI | `siscforge run --force-rerun` / `--fail-fast` |
| Tests | `tests/test_resume.py` |

### Success criteria (skip-finished)
An evaluation is **successful** (skippable on resume) when:

- `status` ∈ {`ok`, `mock`}, **and**
- `electron_phonon` has status ok/mock with λ or Tc, **or** phonon ok/mock, **or** scf ok/mock.

`failed`, `pending`, and `surrogate_only` are **not** skipped as expensive successes.

For `qe` / `qe-epw`, resume uses `require_real=True`: dry-run **mock** evaluations
are not treated as finished.

### Matching policy
1. **candidate_id** exact match
2. Else **fingerprint** `material_family|formula|substrate|±strain` (6 dp)

### Defaults
```yaml
run:
  resume: true
  continue_on_error: true
  force_rerun: false
  resume_qe_steps: true
  force_rerun_qe_steps: false
```

- After each expensive candidate: flush `evaluations.json`
- Progress: `[i/N] Formula strain=… — skip|running|ok|failed`
- End: `Checkpoint summary: skipped=, ran=, ok=, failed=`
- `QENotAvailableError` (missing binaries) still aborts the whole campaign (exit 3)

### How to resume after reboot / kill
```bash
siscforge run --calculator qe-epw examples/nbti_n_al_broad_shortlist.yaml
# finished candidates skipped; in-progress candidate reuses relax/SCF if valid
siscforge run --calculator qe-epw --force-rerun examples/nbti_n_al_broad_shortlist.yaml
```

Mid-step workdir resume: **Slice 15**.

---

## Slice 12 (2026-07-25) — Broader nitride AL campaign

**Scope**: Workstation-scale Nb–Ti–N (+ Zr/Hf) example that closes the Phase 1
loop with Phase 2 Si-scoring.

| Item | Location |
|------|----------|
| Campaign | `examples/nbti_n_al_broad.yaml` |
| Walkthrough | `docs/examples/nbti_n_al_broad.md` |
| Tests | `tests/test_active_learning.py::test_example_al_broad_yaml_loads_and_enumerates` |

Grid: 4 binaries (Nb/Ti/Zr/Hf) + 3 Nb–Ti ternaries × 7 strains → ~49 candidates
(capped at 60). AL `max_epw_jobs: 6`. Epitaxy `auto` + buffers for Si v0.2.

Dry-run shows acquisition table, top-k selection, surrogate_only deferred rows.
Real EPW: restrict shortlist + `--calculator qe-epw` (see walkthrough).

---

## Slice 11 (2026-07-25) — Light EPW practical hardening

**Scope**: Thin pass only — defaults comments, failure diagnostics, quality_tag
clarity, denser-grid docs. **Not** production Wannier automation or anisotropic Eliashberg.

| Item | Location |
|------|----------|
| Grid guidance | `epw_inputs.recommended_grids(family, tier)` |
| EPW input header | `build_epw_input` comments: quality_tag + nkf/nqc |
| Failure diagnostics | `epw_recipes.diagnose_epw_failure` on pp/nscf/epw fail |
| Docs | `docs/examples/nbN_epw.md`, `mgb2_epw.md` grid ladders |

### quality_tag
- `dft.quality_tag: screening | production` is **propagated** to SCF / Phonon /
  ElectronPhononResult and exports.
- It does **not** auto-change grids — raise `qpoints` / `epw.nqc` / `nkf` / `nqf`
  in YAML and set `quality_tag: production` when intentionally denser.

### Explicitly still deferred
- Automated Wannier projection discovery
- Anisotropic / multi-band Eliashberg
- Full soft-mode remediation pipeline

---

## Slice 10 (2026-07-25) — Phase 2 kickoff: 45° epitaxy + buffer library

**Scope**: Reduce cube-on-cube Si-mismatch pessimism for rocksalt nitrides.

| Item | Location |
|------|----------|
| 45° mismatch | `structure.strain.lattice_mismatch_percent(..., match="45deg")` |
| Buffer library | `silicon.buffers` — TiN, AlN, ZrN, MgO, direct_Si |
| Scorer | `silicon.feasibility` **v0.2** — auto picks best of cube / 45° / buffer |
| Enum flags | `enumeration.epitaxy_orientation`, `use_buffers` |
| Example | `examples/nbn_si_45deg.yaml` |

Notes on the score always state when 45° or a buffer was assumed. Remaining Phase 2:
multi-layer stacks, membrane mechanics, critical thickness, interface slabs, Pareto ranking.

---

## P2.4 (2026-08-10) — Multi-objective ranking + Pareto front

**Scope**: Transparent, YAML-configurable ranking weights; Pareto non-dominated
set on primary axes; ranking provenance on ranked rows / CSV / cards. Does **not**
change Si-feasibility science (P2.1–P2.3) or introduce new ML models.

| Item | Location |
|------|----------|
| Ranking config | `RankingConfig` — `performance_weight` (0.6), `si_feasibility_weight` (0.4), `uncertainty_weight` (0.0), `performance_ceiling_K` (40), `pareto_enabled` (True) |
| Composite + breakdown | `siscforge.ranking.compute_composite_score` / `compute_composite_breakdown` |
| Pareto | `identify_pareto_front` — maximize performance_score vs Si-total (+ certainty when weighted) |
| Row provenance | `CandidateEvaluation.ranking_weights`, `composite_breakdown`, `on_pareto_front` |
| Export | CSV columns `on_pareto_front`, `ranking_w_*`, `composite_*_norm`; synthesis-card ranking banner |
| CLI | `siscforge rank --config campaign.yaml --pareto/--no-pareto`; weight banner + Pareto column |
| Tests | `tests/test_ranking_p24.py` |

**Normalization**: performance_score (Tc-like K) → `min(100, score / performance_ceiling_K × 100)`.
Missing performance / missing Si-feasibility default to **15** (not 50; #46).
Restore with `ranking.missing_performance_default: 50` /
`missing_si_feasibility_default: 50`. Optional
`ranking.performance_ceiling_by_source` and pairing
`kelvin_per_unit_by_family` / `score_ceiling_K_by_family` are opt-in.
Si total is already 0–100. Optional certainty = `(1 − surrogate uncertainty) × 100` when
`uncertainty_weight > 0` and uncertainty is present; missing uncertainty drops that weight
from the denominator. Trust penalties, `prefer_dynamically_stable`, and hull demotion apply
**after** the weighted blend (unchanged).

**Out of scope (P2.5+)**: synthesis-card / process-recipe freeze, new AL acquisition
functions, GNN / DMFT / Josephson.


---

## P2.5 (2026-08-11) — Process recommendations + Phase 2 close-out

**Scope**: Scannable synthesis cards with an explicit **Process recommendation**
section; freeze machine-readable process-recommendation schema `1.0`. Does **not**
change Si scoring, ranking maths, or calculators.

| Item | Location |
|------|----------|
| Helper | `siscforge.export.process_recommendation` |
| Schema version | `PROCESS_RECOMMENDATION_SCHEMA_VERSION = "1.0"` |
| Campaign JSON | `process_recommendations.json` via `export_campaign_bundle` |
| Card layout | Identity → Headline scores → Process recommendation → Supporting detail |
| One-pagers | Same layout as cards |
| Schema doc | `docs/process-recommendation-schema.md` |
| Exit checklist | `docs/phase2-exit.md` |
| Tests | `tests/test_process_recommendation_p25.py` |

**Process block fields**: recommended stack/buffers, thickness + h_c method,
process temp ceiling + thermal window, chemical flags, membrane transfer,
`do_not_cite_tc` (false only for `result_quality=production`) / trust warning,
headline scores.


**Out of scope**: interface-slab DFT, FEM membranes, CALPHAD, Phase 3/4 science.


---

## Phase 1 complete (frozen baseline)


**Version**: `0.1.0` · **Tag**: `v0.1.0-phase1` · **Exit checklist**: [phase1-exit.md](phase1-exit.md)  
**Validation**: [validation-phase1.md](validation-phase1.md)

Delivered: EPW + isotropic Eliashberg, NbN/MgB₂ goldens, λ/Tc surrogate stub, AL prioritization.
Deferred: production Wannier, anisotropic Eliashberg, trained GNN, AL retrain, Phase 2 Si maturity.

---

## Slice 9 (2026-07-25) — Minimal active-learning prioritization

**Scope**: Queue prioritization for expensive EPW jobs. **Not** a full retrain loop, Bayesian optimization, or batch BO.

### Module
| Item | Location |
|------|----------|
| Acquisition | `siscforge.active_learning.acquisition` |
| Config | `CampaignConfig.active_learning` |
| Evaluation fields | `acquisition_score`, `al_selected_for_expensive` |
| Example | `examples/nbti_n_al.yaml` |
| Tests | `tests/test_active_learning.py` |
| Store artifact | `active_learning.json` |

### Acquisition (`uncertainty_si_tc`)
```
score = w_u·unc + w_tc·(Tĉ/Tc_max) + w_si·(Si/100) − w_hull·(E_hull/0.25)
```
Default weights: uncertainty 0.4, predicted_tc 0.3, si_feasibility 0.3, hull_penalty 0.1.

### Campaign YAML
```yaml
active_learning:
  enabled: true                 # default false
  strategy: uncertainty_si_tc
  max_epw_jobs: 5
  evaluate_deferred_with_surrogate: true
  weights:
    uncertainty: 0.4
    predicted_tc: 0.3
    si_feasibility: 0.3
    hull_penalty: 0.1
```

### Run-path behavior
1. Enumerate → formation filter → λ/Tc surrogate
2. Score Si-feasibility (cheap) for all remaining
3. If AL enabled: compute acquisition, print ranking, select top-k for calculator
4. Deferred candidates → `status=surrogate_only` evaluations (optional)
5. Final ranking uses real/mock EPW Tc when present (overrides surrogate)

### Dry-run
```bash
siscforge run --dry-run examples/nbti_n_al.yaml
```
Mock calculator stands in for the expensive path on the selected top-k; deferred rows are labeled `surrogate_only*`.

### Explicitly out of scope (this cut)
- Surrogate retraining on new EPW labels
- Batch / diversity-aware selection
- Bayesian optimization / Gaussian processes
- Avoidance of known-failure regions from past CRASH logs

### Future full AL loop would add
- Retrain λ/Tc surrogate on accumulated EPW results
- Uncertainty calibration from ensemble models
- Multi-fidelity / cost-aware acquisition
- Persistent job queue across campaign restarts

### Next session (Phase 1 largely complete)
Pick one: **(a)** production Wannier/EPW hardening, **(b)** broader nitride alloy campaigns driven by AL, or **(c)** start Phase 2 Si-integration maturity.

---

## Slice 8 (2026-07-25) — λ/Tc surrogate stub (pre-filter)

**Scope**: Lightweight family-heuristic λ / ω_log / Tc predictions for **pre-filtering** before expensive EPW. Not a trained production GNN. No active learning.

### Module
| Item | Location |
|------|----------|
| Surrogate | `siscforge.surrogates.tc_lambda` |
| Config | `CampaignConfig.surrogate.tc_lambda` (`TcLambdaSurrogateConfig`) |
| Evaluation field | `CandidateEvaluation.tc_lambda_surrogate` + `performance_score_source` |
| Example | `examples/nbti_n_surrogate.yaml` |
| Tests | `tests/test_tc_lambda_surrogate.py` |

### API
```python
from siscforge.surrogates import predict_tc_lambda, TcLambdaSurrogate
pred = predict_tc_lambda(candidate)  # → λ, ω_log, Tc, uncertainty, model_version
```

### Campaign YAML
```yaml
surrogate:
  tc_lambda:
    enabled: true              # default false
    min_predicted_tc_K: 5.0    # optional cut
    max_uncertainty: 0.65      # optional cut (0–1)
    keep_top_n: 10             # optional shortlist
    use_for_ranking_when_no_epw: true
```

### Precedence
1. Real EPW / mock `ElectronPhononResult` → `performance_score` (source `epw` / `mock`)
2. Else, if `use_for_ranking_when_no_epw`, surrogate score (source `surrogate`, labeled in notes + CSV)

### Export columns (distinct from EPW)
`surrogate_lambda`, `surrogate_omega_log_K`, `surrogate_Tc`, `surrogate_uncertainty`, `surrogate_model_version`, `performance_score_source`

### Limitations (stub)
- Family + formula anchors only (NbN, MgB₂, TiN, …); not structure-graph trained
- Uncertainty is heuristic, not calibrated ensemble variance
- Does not replace EPW for scientific claims
- Future: ALIGNN/MatGL multi-task head on the same `TcLambdaPrediction` schema

### Next session (best single focus)
See **Slice 9** (minimal AL prioritization) — implemented.

---

## Slice 7 (2026-07-25) — MgB₂ golden EPW path

**Scope**: Complete the MgB₂ golden conventional pathway (same Calculator/CLI patterns as NbN). Still isotropic-only; no trained λ/Tc surrogate, no active learning, no anisotropic Eliashberg.

### Deliverables
| Item | Location |
|------|----------|
| Structure | `siscforge.structure.mgb2` — 3-atom hexagonal AlB₂-type (P6/mmm) |
| Campaign | `examples/mgb2_epw.yaml` (skeleton YAML kept as compatibility alias) |
| Docs | `docs/examples/mgb2_epw.md` |
| References | `epw_references.MGB2_*` ranges + fixture moments |
| Fixture | `tests/fixtures/qe/epw_mgb2_snippet.out` |
| Tests | mock MgB₂ e-ph, fixture parse, optional `SISCFORGE_RUN_EPW=1` real gate |

### Two-gap → isotropic
MgB₂ is multi-band / two-gap. Screening EPW reports **isotropic** λ, ω_log, and Allen–Dynes / isotropic Eliashberg Tc. Metadata notes this on mock and real EPW results (`alpha2F_summary.material_notes`, `tc_model=isotropic_average`).

### CLI
```bash
siscforge run --dry-run examples/mgb2_epw.yaml
siscforge run --calculator qe-epw examples/mgb2_epw.yaml   # needs epw.x + Mg/B UPF
```

### Remaining Phase 1 gaps (explicit)
- Production Wannier automation (projections, windows, exclude_bands) beyond screening template
- Anisotropic / multi-band Eliashberg (MgB₂ σ–π)
- Active-learning loop (uncertainty / UCB retrain) — see Slice 8
- Broader boride enumeration (beyond bulk MgB₂ prototype)
- Production-trained λ/Tc GNN replacing the Slice 8 stub

### Next session (best single focus)
**Minimal active-learning coordinator** (surrogate uncertainty + Si-feasibility → EPW priority queue).

---

## Slice 6 (2026-07-24) — Phase 1 EPW + isotropic Tc

**Scope**: Conventional superconductivity pathway (EPW + Allen–Dynes / isotropic Eliashberg). No anisotropic Eliashberg, DMFT, AL, or trained GNN.

### Data model
- `ElectronPhononResult` — λ, ω_log (K), μ*, Tc_allen_dynes, Tc_eliashberg, converged, wannier_ok, quality_tag
- `CandidateEvaluation.electron_phonon` + `performance_score` = best Tc (K)

### Modules (`siscforge.calculators.qe`)
| Module | Role |
|--------|------|
| `eliashberg.py` | Allen–Dynes + strong-coupling closed-form proxy |
| `epw_inputs.py` | Screening `epw.in` template |
| `epw_parser.py` | Parse EPW stdout → `ElectronPhononResult` |
| `epw_recipes.py` | `run_relax_scf_phonon_epw` on top of phonon flow |
| `epw_references.py` | NbN / MgB₂ order-of-magnitude gates |

### Calculators
- **`qe`** — phonon path; set `dft.do_epw: true` to append EPW
- **`qe-epw`** / **`epw`** — always enables EPW (requires `epw.x`)
- **`mock`** — fills mock `ElectronPhononResult` + Tc-based `performance_score` (dry-run unchanged)

### Config
```yaml
dft:
  do_epw: true
  epw:
    enabled: true
    nkf: [6, 6, 6]   # screening fine k
    nqf: [6, 6, 6]
    mu_star: 0.10
    eliashberg: true
```

### CLI
```bash
siscforge run --dry-run examples/nbn_epw.yaml
siscforge run --calculator qe-epw examples/nbn_epw.yaml   # needs epw.x
```

### Golden systems
- NbN: fixtures + mock-safe tests; optional `SISCFORGE_RUN_EPW=1`
- MgB₂: see **Slice 7** (`examples/mgb2_epw.yaml`)

### Limitations
- Isotropic only (no anisotropic Eliashberg / SCDFT)
- EPW input is a screening template; production Wannier projections need hand-tuning
- NSCF + full Wannier prep not fully automated
- Real EPW optional for CI (same pattern as real QE)

---

## Slice 5 (2026-07-24) — QE hardening

**After** the Phase 0 foundation commit.

### Hardening changes
| Area | Change |
|------|--------|
| Relaxed geometry | Parse final `CELL_PARAMETERS` + `ATOMIC_POSITIONS` from pw.x output; feed into SCF/phonon; store `relaxed_structure_cif` on the candidate |
| Pseudos | `pseudos.py` — SSSP-friendly auto-match, explicit map validation, clear `PseudoResolutionError` messages |
| Phonopy FD (optional) | `phonopy_fd.py` when `dft.phonon_method: phonopy_fd` (requires `phonopy`); default remains `dfpt` / `gamma` via `ph.x` |
| Diagnostics | Richer failure messages (workdir + output tails); `quality_tag` propagated to SCF/Phonon/candidate |
| Tests | `tests/test_qe_hardening.py` + vc-relax fixture |

### Current limitations (post-hardening)
- Phonopy FD is screening-quality (force parse from stdout, coarse mesh).
- No automatic SSSP download — user must point `pseudo_dir` at local UPFs.

---

## Slice 4 (2026-07-24) — Formation filter, store, export polish

- `siscforge.surrogates.formation` — heuristic E_hull pre-filter
- `EvaluationStore` — JSON campaign directory
- CSV/Markdown synthesis cards; Phase 0 exit checklist in `docs/phase0-exit.md`

---

## Slice 3 (2026-07-24) — jobflow QE recipes + QECalculator + NbN golden test

**Scope**: Quantum ESPRESSO relax → SCF → DFPT phonon behind the Calculator protocol. **No EPW**, Eliashberg, ML surrogates, or active learning.

### What was implemented

#### `siscforge.calculators.qe`
| Module | Role |
|--------|------|
| `env.py` | Detect `pw.x` / `ph.x` / MPI; `require_qe()` raises clear `QENotAvailableError` |
| `inputs.py` | `StructureCandidate` → pymatgen Structure; `PWInput` + `ph.x` deck builders; pseudo resolve |
| `parser.py` | pw.x / ph.x text → `SCFResult` / `PhononResult`; frequency summary + imag-mode flag |
| `recipes.py` | Local sequential `run_relax_scf_phonon`; optional jobflow `Flow` via `build_relax_scf_phonon_flow` |
| `calculator.py` | `QECalculator` (`name="qe"`) implementing the Calculator protocol |
| `references.py` | NbN golden reference ranges and notes |

#### Registry
- Registered names: **`mock`**, **`qe`**, **`quantum-espresso`** (alias).
- QE registration never breaks the mock path (import is best-effort).

#### Campaign config
- New `CampaignConfig.dft` (`DFTConfig`): cutoffs, k/q grids, `pseudo_dir`, `do_relax`, `do_phonon`, `phonon_method` (`dfpt` \| `gamma`), `nproc`, etc.

#### CLI
```bash
# Always mock (unchanged)
siscforge run --dry-run examples/nbti_n_strain.yaml

# Explicit mock
siscforge run --calculator mock examples/nbti_n_strain.yaml

# Real QE (fails clearly if pw.x missing — no silent fallback)
siscforge run --calculator qe examples/nbn_phonon_qe.yaml
```
`--dry-run` **always** forces mock, ignoring campaign calculator / `dft.engine`.

#### Golden NbN test
- `tests/test_nbn_phonon.py` — mock + fixture paths always pass.
- Optional real QE: `SISCFORGE_RUN_QE=1` + `SISCFORGE_PSEUDO_DIR=...`.
- Fixtures under `tests/fixtures/qe/`.
- Walkthrough: `docs/examples/nbN_phonon_qe.md`.
- Example campaign: `examples/nbn_phonon_qe.yaml`.

### How to switch between mock and QE

| Mode | How |
|------|-----|
| Dry-run / CI default | `siscforge run --dry-run <yaml>` → `MockCalculator` |
| Explicit mock | `--calculator mock` or `calculators: [{name: mock}]` |
| Real QE | `--calculator qe` **or** `calculators: [{name: qe}]` + `dft.engine: qe` + `dft.pseudo_dir` set |
| Env for tools | `QE_BIN` / `QUANTUM_ESPRESSO_BIN`; optional `SISCFORGE_PSEUDO_DIR` for pytest |

Install optional jobflow (not required for sequential runs):

```bash
pip install -e ".[qe]"
```

### Current limitations

- **No EPW / Wannierization / Eliashberg** — phonon only (DFPT `ph.x` or Gamma-only).
- **No phonopy finite-displacement path yet** — DFPT-focused; phonopy YAML can be *parsed* if provided as text.
- **jobflow** is optional; workstation path runs steps with `subprocess` in order (no Mongo job store).
- **Relaxed geometry re-read** is a stub — after `vc-relax`, SCF uses the input structure unless you re-feed a relaxed CIF later.
- **Pseudopotentials** must be supplied by the user (`dft.pseudo_dir` / map); none are vendored.
- **Performance score** is not filled by QE yet (ranking uses Si-score + neutral performance fallback).
- Real QE is **not** required for `pytest` to pass.

### Next recommended session

Roadmap Phase 0 remaining items:

1. **Formation-energy GNN surrogate** (ALIGNN / MatGL) + uncertainty pre-filter.
2. **File/Mongo store** for evaluations + basic ranking by E_hull + Si-score (ranking already exists; harden persistence).
3. **CIF/POSCAR export** of ranked shortlists + synthesis-card skeleton.
4. Harden QE: parse relaxed structure from `pw` XML, SSSP pseudo presets, phonopy FD backend, NbN production k/q convergence.

Or jump to Phase 1 EPW once phonon golden NbN is validated on a workstation with real QE.

---

## Slice 2 (2026-07-24) — Structure Generation + Si-feasibility scorer

Real nitride / B:Si candidates, epitaxial strain, transparent `SiFeasibilityScore`. See package `siscforge.structure` and `siscforge.silicon`.

Example: `examples/nbti_n_strain.yaml` (15 candidates dry-run).

---

## Slice 1 (2026-07-24) — Foundation

Package layout, Pydantic models, Calculator protocol + MockCalculator, CLI skeleton.
