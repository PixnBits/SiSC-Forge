# SiSC-Forge
## Technical Specifications

**Version 0.2 – Implementation-Ready Blueprint**  
*(Companion to PRD v0.2. Focus: stronger Silicon Integration, clearer Unconventional pathway interfaces, Josephson device modeling section, explicit v0.1 boundaries, actionable data/config/output contracts.)*

---

## 1. Overall Architecture

SiSC-Forge uses a modular, layered, message-passing architecture. All inter-module communication occurs via strongly-typed Pydantic v2 models.

**High-level component flow:**

```
User / Config Layer (YAML campaigns, CLI, Jupyter)
        │
Structure Generation & Enumeration
        │ StructureCandidate[]
ML Surrogate Layer (ALIGNN / MatGL + custom heads)
        │ Filtered + scored candidates
Workflow Engine & Active Learning Manager (jobflow + priority queue)
        │
   ┌────┴────┐
   │         │
DFT/DFPT/EPW  DMFT / Pairing
(QE + EPW)   (TRIQS)
   │         │
   └────┬────┘
        │ CalculationResult hierarchy
Silicon Integration & Interface Module
        │
Candidate Ranking & Reporting
        │
( later ) Josephson Device Metrics Calculator
        │
Provenance Database + Active-Learning Feedback
```

**Design principles**
- Decoupled engines: pure functions of `StructureCandidate` + parameters → typed Result objects.
- ML-first filtering of expensive calculations.
- Provenance-first (lightweight jobflow implementation of AiiDA principles).
- Extensible Calculator registry (new calculators, including future Josephson metrics, register without core changes).
- Identical code path from workstation to HPC.

**v0.1 Must-Have**  
Structure generation → ML formation-energy filter → jobflow QE-SCF + basic DFPT phonon → heuristic Si-score → MongoDB + ranking.

---

## 2. Core Modules

### 2.1 Structure Generation & Enumeration
(Unchanged in scope. P0 for TM nitrides + B-doped Si.)

### 2.2 ML Surrogate Layer
(P0 = formation energy + uncertainty; P1 adds λ / ω_log / Tc proxies.)

### 2.3 DFT / DFPT / EPW Orchestration
(P0 = phonon; P1 = full EPW + isotropic Eliashberg.)

### 2.4 Unconventional (DFT+U / DMFT) Pathway  ← Strengthened

**Purpose**  
High-throughput pathway for materials where phonon-mediated pairing is secondary (infinite-layer nickelates, bilayer nickelates, cuprates). Produces a leading pairing eigenvalue that is used as the performance metric in ranking on equal footing with Eliashberg Tc.

**Stack**  
DFT (QE primary) → Wannier90 → TRIQS / solid_dmft. Impurity solver: CTHYB primary. Pairing from spin susceptibility or linearized Eliashberg in the spin-fluctuation channel.

**Clear Interfaces**
- **Input**: `StructureCandidate` (family tag `