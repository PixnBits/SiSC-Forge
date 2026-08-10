"""Silicon Feasibility scorer (transparent heuristics).

Every component of :class:`~siscforge.models.results.SiFeasibilityComponents`
is always populated. Scores are 0–100 (higher = more Si-process friendly).

v0.2 adds rocksalt **45° epitaxy** matching and a minimal **buffer library**
so nitride cube-on-cube pessimism can be improved when scientifically justified.

v0.3 (P2.1) makes component **weights first-class and YAML-overridable** via
``CampaignConfig.si_feasibility.weights`` (keys: lattice_mismatch,
thermal_budget, chemical_compatibility, buffer_availability, process_maturity).
Active weights and scorer version are stored on every
:class:`~siscforge.models.results.SiFeasibilityScore` for auditability.

v0.4 (P2.2) adds **multi-layer buffer stacks** and surfaces
**chemical-compatibility / thermal-window** flags from stack metadata on the
score and synthesis cards. Still heuristic — not CALPHAD.

v0.5 (P2.3) drives **recommended thickness** from Matthews–Blakeslee /
People–Bean critical-thickness estimates and adds **membrane-transfer**
heuristics (ranking / process guidance only — not continuum FEM).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import SiFeasibilityConfig, SiFeasibilityWeights
from siscforge.models.results import SiFeasibilityComponents, SiFeasibilityScore
from siscforge.silicon.buffers import (
    BUFFER_LIBRARY,
    BufferStack,
    aggregate_stack_flags,
    list_buffers_for_family,
    list_stacks_for_family,
    resolve_stack_layers,
    stack_from_single,
    stack_process_temp_ceiling_c,
    stack_window_notes,
)
from siscforge.silicon.critical_thickness import (
    estimate_critical_thickness,
    membrane_transfer_heuristic,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    EpitaxyMatch,
    lattice_mismatch_percent,
    parse_substrate,
    substrate_in_plane_spacing,
)

SCORER_VERSION = "0.5"

COMPONENT_WEIGHTS: dict[str, float] = {
    "lattice_mismatch": 0.35,
    "thermal_budget": 0.20,
    "chemical_compatibility": 0.20,
    "buffer_availability": 0.10,
    "process_maturity": 0.15,
}

COMPONENT_KEYS: tuple[str, ...] = (
    "lattice_mismatch",
    "thermal_budget",
    "chemical_compatibility",
    "buffer_availability",
    "process_maturity",
)

_FAMILY_PROCESS_TEMP_C: dict[str, float] = {
    "tm_nitride": 600.0,
    "b_doped_si": 900.0,
    "mgb2_boride": 750.0,
    "nickelate": 600.0,
    "cuprate": 800.0,
    "other": 700.0,
}

_FAMILY_CHEMICAL: dict[str, float] = {
    "tm_nitride": 80.0,
    "b_doped_si": 95.0,
    "mgb2_boride": 55.0,
    "nickelate": 40.0,
    "cuprate": 35.0,
    "other": 50.0,
}

_FAMILY_MATURITY: dict[str, float] = {
    "tm_nitride": 90.0,
    "b_doped_si": 85.0,
    "mgb2_boride": 50.0,
    "nickelate": 25.0,
    "cuprate": 30.0,
    "other": 40.0,
}

EpitaxyMode = Literal["auto", "cube_on_cube", "45deg"]
WeightsLike = Mapping[str, float] | SiFeasibilityWeights | SiFeasibilityConfig | None


def _clamp(score: float) -> float:
    return float(max(0.0, min(100.0, score)))

# NOTE: Full file content is long. This push restores the module header and
# constants; the remainder of the known-good implementation is applied next.
# Temporary: re-export from a backup path is not possible.
# See commit 4e23e94 for the complete prior version.

raise NotImplementedError(
    "feasibility.py was truncated during multi-file push; "
    "restore from commit 4e23e94ca66be6066b01594cb61ce81bdc4765c2 "
    "or re-apply the local /tmp/siscfix/feasibility_final.py content."
)
