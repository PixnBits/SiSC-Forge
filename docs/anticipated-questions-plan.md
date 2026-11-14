# SiSC-Forge Anticipated Questions & Concerns – Proactive Response Plan

This document records a proactive plan to help SiSC-Forge anticipate questions and concerns from stakeholders (reviewers, collaborators, experimentalists, open-source users). It builds on discussion of light-induced / photoinduced superconductivity applications relevant to silicon-compatible materials discovery.

**Context**
- SiSC-Forge is a modular high-throughput ML-accelerated platform for discovering silicon-compatible high/room-temperature superconductors (TM nitrides, B-doped Si/silicides, MgB₂/borides, later nickelates/cuprates).
- Current status (v0.4.4): Phases 0–2 complete; Phase 3 software path (DMFT scaffold) complete; Phase 4 Tier-1 Josephson metrics complete.
- Focus: equilibrium EPW/Eliashberg + DMFT pathways, Silicon Feasibility Score, process-recommendation cards (schema 1.0), active learning, Josephson Tier-1 proxies.
- Experimental SC in Si relies on laser hyperdoping (GILD or implant + ns laser anneal) beyond solubility limits. Photoinduced transient SC (cuprates, fullerides) is distinct but relevant for hybrid opto-SC devices or non-eq pairing insights.
- No light/non-eq support currently in codebase.

**Goal of this plan**
Equip the project with living documentation, optional roadmap extensions, and process updates that preemptively address scientific credibility, computational practicality, silicon-integration realism (esp. laser processing for B:Si), device relevance, community adoption, and light/non-equilibrium opportunities—while remaining workstation-first, modular, and scientifically honest.

## 1. Living FAQ / Anticipated Concerns Document
Create `docs/anticipated-questions.md` (or `docs/FAQ.md`), linked from the README. Linking from the PRD and ROADMAP is step 5 of §5.

Map concerns to existing features / non-goals / residuals:
- Scientific credibility: Golden recovery (NbN, MgB₂ within 15–20 %) (Correction: not achieved. MgB₂ golden is a coarse-mesh pipeline validation only, λ≈0.46 and Tc≈7.5 K vs ~39 K exp.; see [anticipated-questions.md](anticipated-questions.md).), trust layer (pathological λ, mock penalties), coarse phonon maps as gates only (v0.4.4 denser-q pilot), no RTSC guarantee.
- Silicon feasibility & B:Si: Component breakdown, growth-correlation intent; laser hyperdoping as enabling process (melt depth, residual damage, active B fraction).
- Compute & accessibility: Workstation path (resume, Docker, dry-run), HPC scaling without rewrite, walltime expectations.
- AL maturity: Family-mean from small seeds, promotion gates, bootstrap messaging.
- Device metrics: Tier-1 proxies labeled as ranking aids only; Usadel/BdG residual.
- Light/photo distinction: Laser processing (fabrication) vs transient optical driving (control); literature refs for GILD/PLIE and Al–Si laser-programmable SC.
- Non-goals: No full circuit sim, human judgment required, residuals (production DMFT, advanced GNNs).

Include a compact risk register table: Concern → Existing mitigation → Residual risk → Planned action.

## 2. Non-Equilibrium / Light Extension Design Note
Add `docs/design/light-non-equilibrium.md` as exploratory (not committed Phase).

- Extend process-recommendation schema to v1.1 with optional laser fields for B:Si (energy density, pulse duration, implant energy, residual damage flag, active B fraction). Feed into Si Feasibility Score components without changing default ranking.
- Optional ranking proxies: light-response or laser-compatibility score (inert by default).
- Computational options: driven-Eliashberg / light-response surrogate calculator stub (plugin); full non-eq methods remain optional/expensive.
- Applications: optically gated JJs, hybrid Si-photonics superconducting circuits, better modeling of laser steps that enable Si SC.
- Label as candidate Phase 5 topic; no near-term commitment.

## 3. Validation & Experimental Bridge Actions
- Publish golden-system regression artifacts (NbN, MgB₂) and a B:Si hyperdoping example campaign (literature targets + mock path).
- Template process-recommendation cards for GILD/PLIE or nitride epitaxy groups (include new laser fields once available).
- Invitation language in FAQ/README for experimental feedback on synthesis cards and Si-score correlation.
- Surface residual items and golden-recovery status.

## 4. Documentation, Status & Community Upgrades
- Link FAQ and design note from README, PRD, ROADMAP.
- “Common reviewer questions” subsection in contribution guide pointing to FAQ and trust-layer docs.
- Preserve calculator-plugin architecture for future light-response modules.
- Keep all new fields optional / inert by default.

## 5. Short-Term Implementation Sequence
1. Draft and land the FAQ document (no code change).
2. Open design note for light/non-equilibrium + schema v1.1 laser fields (discussion).
3. Implement optional schema v1.1 laser parameters + process-card updates for B:Si (additive).
4. Publish golden regression artifacts and one B:Si example campaign.
5. Update README/PRD links and contribution guide.
6. Track residual accuracy/compute issues (soft-mode heuristics, walltime calibration, GPU QE) as already listed in open issues.

This plan turns likely stakeholder questions into documented answers and lightweight optional extensions, especially around laser processes defining experimental Si superconductivity, without disturbing equilibrium EPW/DMFT or Josephson Tier-1 paths.

References:
- Repo: https://github.com/PixnBits/SiSC-Forge
- ROADMAP, PRD, Technical Specs, process-recommendation-schema.md
- Experimental literature: laser ultra-doped Si (GILD/PLIE), photoinduced SC in YBCO/K3C60, Al-Si laser-programmable interfaces.
