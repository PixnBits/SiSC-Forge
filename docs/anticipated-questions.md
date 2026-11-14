# Anticipated questions

Living FAQ for reviewers, collaborators, experimentalists, and people running SiSC-Forge on a workstation. It is step 1 of [`docs/anticipated-questions-plan.md`](anticipated-questions-plan.md) only: answers and a risk register. It does not add code, schema fields, or the plan’s later design note.

Claims cite a file in this repository or a merged pull request. Planned work, and numbers that are not in the git tree, are labelled **pending**.

The status board is the [README](../README.md) (`v0.4.4`: Phases 0–2 complete, Phase 3 software path complete, Phase 4 Tier-1 complete). This page does not restate that board.

## Scientific credibility

### What did the NbN and MgB₂ “goldens” actually return?

They are screening-grid checks of the conventional pipeline (isotropic Allen–Dynes / Eliashberg). They are not a quantitative recovery of experimental Tc. The PRD success metric — literature Tc for NbN and MgB₂ within 15–20% under **production-quality** settings ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md) §4) — is an intent. The runs below do not meet it. Phase 1’s exit table marks the golden *examples* met (mock path always; real EPW optional) in [`docs/phase1-exit.md`](phase1-exit.md). That is an exit-criterion label, not the PRD tolerance.

**MgB₂.** [`docs/examples/mgb2_epw.md`](examples/mgb2_epw.md) and the headers of [`examples/mgb2_epw.yaml`](../examples/mgb2_epw.yaml) and [`examples/mgb2_epw_validation.yaml`](../examples/mgb2_epw_validation.yaml) record one live campaign, `mgb2_epw_validation_ibrav4_nscf_sym`: **λ≈0.46** and **Tc≈7.5 K** on screening grids. The same note calls this an isotropic average whose grids may undershoot literature Tc. Experimental bulk Tc in that document is **~39 K**. Phase 1 averages the two-gap (σ and π) material onto one isotropic λ, ω_log, and Tc ([`docs/examples/mgb2_epw.md`](examples/mgb2_epw.md)). The golden-status table marks that preferred path (`ibrav=4`, symmetry-on NSCF) accepted and quotes those two numbers. The same page’s screening-acceptance window (Tc ~25–50 K) and its “order-of-magnitude under good settings” sentence (~30–45 K) are targets, not this campaign’s result. The raw campaign store is **pending publication as an artifact**. It is not in the git tree; do not look for an `outputs/` path. The numbers above are the ones written into those three files.

**NbN.** A real screening run on a workstation returned **~18–22 K** Allen–Dynes Tc. Soft modes inflate λ. Experimental bulk Tc is **~16 K**. Sources: [`docs/examples/nbN_epw.md`](examples/nbN_epw.md), [`docs/phase1-exit.md`](phase1-exit.md), [`docs/validation-phase1.md`](validation-phase1.md). The ~0.5–2.0 λ row in the NbN example is a screening-acceptance window, not a measured λ for that run. No raw EPW store and no recorded λ for the ~18–22 K run are in the repository (**pending**). [`docs/validation-phase1.md`](validation-phase1.md) also says literature-quality λ still needs denser grids and production Wannier projections.

### Does the trust layer make screening λ or Tc citable?

It stops pathological screening numbers from silently dominating rank. It does not turn them into citation-grade results. Slice 19 in [`docs/implementation-notes.md`](implementation-notes.md) and `QualityConfig` in [`src/siscforge/models/config.py`](../src/siscforge/models/config.py) set the current defaults:

| Knob | Default | Effect |
|------|---------|--------|
| `lambda_suspect_above` | 3.0 | `high_lambda`, tier `screening_suspect` |
| `lambda_unreliable_above` | 5.0 | `extreme_lambda`, tier `unreliable` |
| `mock_unreliable` | true | mock / dry-run → `unreliable` |
| `suspect_performance_penalty` | 0.45 | multiplies the composite |
| `unreliable_performance_penalty` | 0.15 | multiplies the composite after the Tc term is zeroed |
| `hard_zero_screening_high_lambda` | true | performance term set to 0 when random Wannier or coarse grids co-occur with high or extreme λ |

`src/siscforge/quality.py` states the hard-zero rule (issue #44): the performance axis is zeroed, not only scaled. Milder cases keep the soft multipliers. Raw λ and Tc stay on the evaluation. Mock and dry-run rows are unreliable by default, so they are not ranked as if they were screening or production numbers.

Merged pulls that put these controls in place: [pull 55](https://github.com/PixnBits/SiSC-Forge/pull/55) (mock results demoted to unreliable, together with inflated λ), [pull 77](https://github.com/PixnBits/SiSC-Forge/pull/77) (`mock_unreliable` exposed; current `lambda_unreliable_above` default 5.0), [pull 63](https://github.com/PixnBits/SiSC-Forge/pull/63) (hard-zero), [pull 79](https://github.com/PixnBits/SiSC-Forge/pull/79) (reject inverted λ thresholds). Process cards copy the tier into `result_quality` and set `do_not_cite_tc` for every non-production tier ([`docs/process-recommendation-schema.md`](process-recommendation-schema.md)).

Residual: the layer is not denser-grid refinement and not a production Wannier library (Slice 19). `performance_hard_zeroed` is stored on `composite_breakdown` in `src/siscforge/ranking.py`. The synthesis-card ranking line and `evaluations.csv` omit that field (`src/siscforge/export.py`). Surfacing it on cards and CSV is open [issue 66](https://github.com/PixnBits/SiSC-Forge/issues/66).

### Are coarse phonon maps, or the v0.4.4 denser-q pilot, dynamical-stability proof?

No. Coarse maps are gates. The README (`v0.4.4`) records guided recovery when a coarse map has no stable cells: a soft-mode report plus a denser-q pilot. Slice 29 in [`docs/implementation-notes.md`](implementation-notes.md), [`docs/examples/nbti_n_phonon_map.md`](examples/nbti_n_phonon_map.md), and [`docs/specs/SiSC-Forge-Technical-Specifications.md`](specs/SiSC-Forge-Technical-Specifications.md) §2.3c say the same thing. The pilot reuses stored cells, writes a new output directory, and keeps `do_epw: false`. The operator chooses expand or abandon. A denser-q pilot is still a gate. Specs §2.3c and the Nb–Ti–N walkthrough both say it is not production dynamical-stability proof. Soft-mode classes are heuristic; `likely_mesh_artefact` is not a stability certificate. Shipped in [pull 65](https://github.com/PixnBits/SiSC-Forge/pull/65).

The PRD lists “guaranteed discovery of room-temperature superconductors” as a non-goal, and §1 says ambient-pressure room-temperature operation is a long-term aspiration, not a near-term success criterion ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md)).

## Silicon feasibility and B:Si

### What shipped for silicon scoring and synthesis cards?

Phase 2 shipped a component breakdown and frozen process-recommendation cards. [`docs/phase2-exit.md`](phase2-exit.md) (P2.1–P2.5) and schema **1.0** in [`docs/process-recommendation-schema.md`](process-recommendation-schema.md). Default component weights in [`src/siscforge/silicon/feasibility.py`](../src/siscforge/silicon/feasibility.py) are lattice mismatch 0.35, thermal budget 0.20, chemical compatibility 0.20, buffer availability 0.10, process maturity 0.15. Cards and `process_recommendations.json` carry stack, thickness and critical thickness, process-temperature ceiling, chemical flags, membrane hint, and the trust fields above. B:Si enters as enumerated prototypes (`src/siscforge/structure/bsi.py`, family `b_doped_si`), scored with those same components. That is not a laser-process model.

### Has the Silicon Feasibility Score been correlated with published growth success?

No. Qualitative correlation with published growth success rates is a PRD success metric ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md) §4), not a result in this repository. Phase 2 exit criterion 7 says known systems (NbN / buffer paths) “score sensibly” on dry-run campaigns ([`docs/phase2-exit.md`](phase2-exit.md)). That is a smoke check of the scorer.

### Is laser hyperdoping of B:Si in schema 1.0?

No. The frozen 1.0 key list has no laser, melt-depth, residual-damage, or active-boron fields ([`docs/process-recommendation-schema.md`](process-recommendation-schema.md)). Those fields are not shipped. A B:Si hyperdoping example campaign is **pending** (plan §3).

## Compute and accessibility

### Can a workstation run the path that HPC would run?

Yes. The same codebase and campaign YAML are the workstation path and the HPC path. The README states that, and the PRD goal is to scale to institutional or cloud HPC without a second code base ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md) §2). [`docs/SETUP.md`](SETUP.md) is the workstation install guide:

- Tier A is Python-only dry-run. `--dry-run` forces the mock calculator and does not need QE.
- Resume is on by default (`run.resume: true`). Re-launch the same YAML and output directory to skip finished successes (SETUP §C.5b). For `qe` / `qe-epw`, a mock dry-run row is not treated as finished (`require_real=True`, Slice 13 in [`docs/implementation-notes.md`](implementation-notes.md)).
- HPC is site modules (`module load quantum-espresso` in SETUP) plus that same tree, not a rewrite.

The Docker image (Ubuntu, QE built from source, Wannier90, SSSP, package install) is specified in [`docs/specs/SiSC-Forge-Technical-Specifications.md`](specs/SiSC-Forge-Technical-Specifications.md) §2.9 and built from [`docker/BUILD.md`](../docker/BUILD.md). SETUP itself is the host install guide; the image recipe lives in those two places.

### How long does a campaign take, and is there GPU QE?

Wall-time text is a heuristic band, not a guarantee. Slice 21 in [`docs/implementation-notes.md`](implementation-notes.md) prints it at the start of real `qe` / `qe-epw` runs (`run.estimate_walltime`, default true). Worked examples there: a 6-candidate screening shortlist on ~8 cores, about 9.4 h–2.4 d sequential; a 2-candidate workstation-dense refine on ~16 cores, about 2–6 d. Machine load and convergence dominate. Interrupt and re-run to resume.

GPU-accelerated Quantum ESPRESSO is not in the tree. Tracking issue: [issue 4](https://github.com/PixnBits/SiSC-Forge/issues/4) (open).

## Active learning

The shipped surrogate is a **family-mean** fit (plus the older family-heuristic stub), intended to start from goldens and other labels the operator promotes. It is not an ALIGNN or MatGL production head. Exit notes: [`docs/phase15-exit.md`](phase15-exit.md), [`docs/phase15b-exit.md`](phase15b-exit.md). Design note: [`docs/design/active-learning-flywheel.md`](design/active-learning-flywheel.md).

What is in the code:

- Shared state under `./al_state` (or `$SISC_AL_ROOT`), across campaigns.
- Explicit promotion. Mock and dry-run evaluations are refused ([`docs/phase15-exit.md`](phase15-exit.md) AC18; `src/siscforge/active_learning/training_set.py`).
- Bootstrap messaging: `al-status` and a banner on synthesis cards while the fit is in bootstrap (AC15). In `src/siscforge/surrogates/tc_lambda.py`, bootstrap stays on until a trained fit has at least 150 labels. That 150 is an exit threshold in code, not a count of labels stored in the git repository.
- The optional file `docs/examples/literature_seeds.json` is an example pack and is non-citation ([`docs/phase15b-exit.md`](phase15b-exit.md)).

Production ALIGNN/MatGL λ/Tc heads remain residual (both exit notes and the README Phase 3 residual list).

## Device metrics

Tier-1 Josephson metrics are shipped and **off by default**. When `josephson.enabled` is set, Ambegaokar–Baratoff and a BCS-from-Tc gap proxy attach to evaluations. `approximate` is forced true. Cards label them **approximate / ranking only**. P4.2 adds fabrication-class hints (SIS / SNS / ramp-edge, BEOL / thermal flags). Those hints reuse Silicon Feasibility signals; they are not process qualification. Sources: [`docs/phase4-exit.md`](phase4-exit.md), [`docs/phase4-p41-josephson-tier1.md`](phase4-p41-josephson-tier1.md), [`docs/phase4-p42-fabrication.md`](phase4-p42-fabrication.md).

Usadel (Tier-2) and BdG (Tier-3) are residual. There is no full Josephson circuit simulation: the PRD non-goal list leaves circuit / SPICE simulation out of v0.1–v1.0 ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md) §2), and the Phase 4 exit table says device-design values stay approximate ([`docs/phase4-exit.md`](phase4-exit.md)).

## Light and photoinduced superconductivity

Not supported. Laser hyperdoping is a fabrication step, distinct from transient optical driving. The plan records that the codebase has no light or non-equilibrium pathway ([`docs/anticipated-questions-plan.md`](anticipated-questions-plan.md)).

## Non-goals and other residuals

From the PRD non-goals ([`docs/PRD/SiSC-Forge-PRD.md`](PRD/SiSC-Forge-PRD.md) §2), and from phase exit notes:

- No full circuit simulation in the current scope (simple Tier-1 estimates only).
- Expert human judgment on the final candidate list is required. The denser-q pilot does not decide expand versus abandon ([`docs/specs/SiSC-Forge-Technical-Specifications.md`](specs/SiSC-Forge-Technical-Specifications.md) §2.3c).
- No guarantee of room-temperature superconductivity, and no guarantee of physical λ/Tc from auto-raised coarse k.
- Coarse q=2³ maps are not production dynamical-stability proof.
- Production DMFT is residual: the Phase 3 software path (model, gate, mock, controlled launcher, pairing score) is shipped; production CTHYB calibration (U/J/β) and the solid_dmft version matrix are not ([`docs/phase3-p33-dmft.md`](phase3-p33-dmft.md), README).
- Production GNN heads are residual (section above).

## Known numerical pitfalls

Two different V3Si stories appear in project discussions. Only one of them is in the git tree. They are different calculations.

### Code default cutoff, and a campaign note that is not in git

`DFTConfig.ecutwfc` defaults to **50.0** and `ecutrho` to **400.0** ([`src/siscforge/models/config.py`](../src/siscforge/models/config.py)). That number is written into the QE `&SYSTEM` namelist as `ecutwfc` ([`src/siscforge/calculators/qe/inputs.py`](../src/siscforge/calculators/qe/inputs.py)). Quantum ESPRESSO reads `ecutwfc` in Rydberg, so the code default is 50 Ry. A campaign YAML can override it. Campaign notes — **pending a repo artifact**; no path is invented here — record that this default cutoff was too low for transition-metal compounds, and that a V3Si Γ-point instability was a cutoff artefact.

### Smearing and k-mesh on an A15 soft mode (same unpublished notes)

Those campaign notes also record a V3Si mode at **q = (0, 0, 1/3)**: **−140 cm⁻¹** at degauss **0.02 Ry** and **k = 12³**, and **+183 cm⁻¹** at degauss **0.04 Ry**. A15 densities of states peak sharply, so phonon frequencies on these compounds are smearing-sensitive and k-sensitive. A soft-mode gate needs a smearing check and a k check before a negative frequency is treated as a physical instability. A **k = 16³** test is in progress (**pending**; no repo artifact yet).

`DFTConfig.degauss` defaults to 0.02, with `occupations` `smearing` and `smearing` `mv` ([`src/siscforge/models/config.py`](../src/siscforge/models/config.py)). The 0.02 matches the lower smearing in the note. The frequencies themselves are not in the repository.

### In-repo artifact: Gamma crystal-ASR fixture (a different result)

[`tests/fixtures/qe/v3si_a15_gamma.dyn1`](../tests/fixtures/qe/v3si_a15_gamma.dyn1), [`tests/test_gamma_asr.py`](../tests/test_gamma_asr.py), and [pull 97](https://github.com/PixnBits/SiSC-Forge/pull/97) are the in-repo acoustic-sum-rule test. After crystal ASR, three optical frequencies remain near **−54.3 cm⁻¹** (`test_v3si_raw_matches_file_and_asr_removes_acoustic_triplet` counts values near −54.299347). `test_v3si_stays_optical_soft_and_epw_blocked` keeps that cell `optical_soft`. This fixture is not the q = (0, 0, 1/3) campaign note and not the cutoff-artefact note above.

Open [issue 76](https://github.com/PixnBits/SiSC-Forge/issues/76) tracks a separate soft-mode-heuristic gap (known-unstable stoichiometric cases such as δ-NbN). It does not publish the V3Si numbers in this section.

## Experimental feedback

Groups who grow these films are invited to say whether schema 1.0 synthesis cards and the Silicon Feasibility Score match their process experience: buffers, thickness, thermal ceiling, chemical flags, and the trust warning. Please treat that correspondence as feedback. A correlation study against published success rates is not in the repository (see the silicon section).

## Risk register

| Concern | Existing mitigation | Residual risk | Planned action |
|---------|---------------------|---------------|----------------|
| Golden-system quantitative recovery | Screening pipeline checks are documented. MgB₂ campaign `mgb2_epw_validation_ibrav4_nscf_sym`: λ≈0.46, Tc≈7.5 K vs experimental ~39 K (isotropic average). NbN workstation screening: Allen–Dynes Tc ~18–22 K vs experimental bulk ~16 K, with soft modes inflating λ. Trust layer blocks silent use of inflated screening rank. | PRD 15–20% production-quality recovery is not met. MgB₂ Tc on the recorded grids is far below experiment. NbN λ and both raw stores are unpublished. Acceptance windows in the example pages are targets. | **Pending:** publish the golden stores as artifacts (plan §3). Until then, cite the files named above, not an `outputs/` path. |
| Trust-layer residuals | `QualityConfig` thresholds, mock → unreliable, soft penalties, and hard-zero when random Wannier or coarse grids co-occur with high λ. [Pulls 55](https://github.com/PixnBits/SiSC-Forge/pull/55), [77](https://github.com/PixnBits/SiSC-Forge/pull/77), [63](https://github.com/PixnBits/SiSC-Forge/pull/63), [79](https://github.com/PixnBits/SiSC-Forge/pull/79). Slice 19. `do_not_cite_tc` on non-production cards. | The layer does not replace denser grids or tuned Wannier. Raw λ/Tc remain stored. Hard-zero is not yet a card/CSV column (`export.py`). | Production-tier refine is the citation path named in Slice 19. Surface `performance_hard_zeroed`: [issue 66](https://github.com/PixnBits/SiSC-Forge/issues/66). [Issue 43](https://github.com/PixnBits/SiSC-Forge/issues/43) stays an open accuracy-audit tracker. Its older severity list is not current state: related mitigations later shipped in pulls [63](https://github.com/PixnBits/SiSC-Forge/pull/63), [64](https://github.com/PixnBits/SiSC-Forge/pull/64), and [65](https://github.com/PixnBits/SiSC-Forge/pull/65). |
| Coarse-q gates, soft modes, smearing/k | q=2³ maps are gates. v0.4.4 adds a soft-mode report and a phonon-only denser-q pilot ([pull 65](https://github.com/PixnBits/SiSC-Forge/pull/65), Slice 29, specs §2.3c). Pilot ≠ production stability proof. In-repo V3Si check is only the Gamma ASR fixture (optical ~−54 cm⁻¹ after crystal ASR, [pull 97](https://github.com/PixnBits/SiSC-Forge/pull/97)). | Classes are heuristic ([issue 76](https://github.com/PixnBits/SiSC-Forge/issues/76)). **Pending a repo artifact:** default `ecutwfc` 50 Ry too low for TM compounds; V3Si Γ instability was a cutoff artefact; q=(0,0,1/3) mode −140 cm⁻¹ at degauss 0.02 Ry, k=12³ became +183 cm⁻¹ at degauss 0.04 Ry. k=16³ test in progress. | Keep smearing and k checks in front of any soft-mode gate for A15 / TM work. Publish the campaign notes before citing those frequencies. Heuristic follow-up: [issue 76](https://github.com/PixnBits/SiSC-Forge/issues/76). |
| Si-feasibility vs growth success | Component scores and schema 1.0 process cards shipped (Phase 2). Dry-run smoke check: NbN / buffer paths “score sensibly”. | Correlation with published success rates is a PRD intent, not a study. Laser-hyperdoping fields are absent from schema 1.0. | Collect experimental comments on cards and the Si-score (section above). A B:Si hyperdoping example campaign is **pending** (plan §3). |
| Active-learning / GNN maturity | Family-mean fit, mock refused at promotion, bootstrap banner and `al-status`. Seed target in code is 150 labels, not a census of this repo. | Production ALIGNN/MatGL heads are not shipped. Example literature seeds are non-citation. | Residual production GNN heads, called out in the Phase 1.5 exit notes and the README. |
| Josephson Usadel / BdG | Tier-1 AB / BCS proxies, `approximate` forced, ranking only, off by default. P4.2 fabrication hints are heuristics. | Usadel and BdG are not implemented. No full circuit simulation. | Left as Phase 4 residual in [`docs/phase4-exit.md`](phase4-exit.md). No schedule is stated. |
| Wall time and GPU | Heuristic bands (Slice 21), resume, dry-run, Docker image, HPC via modules and the same YAML. | Bands are not guarantees. GPU QE is not implemented. | GPU investigation: [issue 4](https://github.com/PixnBits/SiSC-Forge/issues/4). |
| Light / non-equilibrium | No support in the codebase. Laser hyperdoping (fabrication) is distinct from transient optical driving. | No transient-driving model and no laser process fields on schema 1.0. | Not started in this FAQ. Plan §2 (a design note) is a later step and is not a shipped document. |

## Pending

Items with no repository artifact, or explicitly not done:

- MgB₂ raw store for `mgb2_epw_validation_ibrav4_nscf_sym`. The λ≈0.46 and Tc≈7.5 K figures live in [`docs/examples/mgb2_epw.md`](examples/mgb2_epw.md) and the two example YAML headers. The store is pending publication.
- NbN raw EPW store, and any λ, for the ~18–22 K workstation screening run.
- PRD 15–20% production-quality Tc recovery for NbN and MgB₂ (intent only).
- Campaign notes on TM cutoff (`ecutwfc` default 50 Ry too low; V3Si Γ instability as a cutoff artefact).
- Campaign notes on V3Si q=(0,0,1/3): −140 cm⁻¹ at degauss 0.02 Ry, k=12³, versus +183 cm⁻¹ at degauss 0.04 Ry. The k=16³ follow-up is in progress.
- Silicon Feasibility Score versus published growth success rates (PRD metric, no study).
- B:Si laser-hyperdoping fields and a hyperdoping example campaign (not in schema 1.0).
- Production ALIGNN/MatGL heads; production CTHYB U/J/β and solid_dmft version matrix; Usadel and BdG.
- GPU Quantum ESPRESSO ([issue 4](https://github.com/PixnBits/SiSC-Forge/issues/4)).
- Light / non-equilibrium support, including the plan §2 design note.
