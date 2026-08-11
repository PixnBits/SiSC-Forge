"""QECalculator — Calculator protocol for Quantum ESPRESSO (+ optional EPW)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from siscforge.calculators.base import Calculator
from siscforge.calculators.qe.env import require_qe
from siscforge.calculators.qe.epw_recipes import run_relax_scf_phonon_epw
from siscforge.calculators.qe.recipes import run_dftu_workflow, run_relax_scf_phonon
from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import DFTConfig
from siscforge.models.results import SiFeasibilityScore
from siscforge.silicon.feasibility import score_si_feasibility
from siscforge.structure.generator import candidate_to_structure
