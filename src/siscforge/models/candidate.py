"""Structure candidates and end-to-end evaluation records."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from siscforge.models.provenance import Provenance
from siscforge.models.results import (
    PhononResult,
    SCFResult,
    SiFeasibilityScore,
)


def _new_candidate_id() -> str:
    return str(uuid4())


class StructureCandidate(BaseModel):
    """A single structure / composition / strain point to evaluate.

    Phase 0 stores a lightweight representation (formula, lattice parameters,
    optional CIF/POSCAR string). Full pymatgen ``Structure`` objects will be
    layered on in the Structure Generation module.
    """

    candidate_id: str = Field(default_factory=_new_candidate_id)
    """Stable unique identifier for this candidate within a campaign."""

    formula: str
    """Reduced chemical formula, e.g. ``NbN`` or ``Nb0.5Ti0.5N``."""

    material_family: Literal[
        "tm_nitride",
        "b_doped_si",
        "mgb2_boride",
        "nickelate",
        "cuprate",
        "other",
    ] = "other"
    """Priority material family (PRD § material families)."""

    composition: dict[str, float] = Field(default_factory=dict)
    """Element → atomic fraction (should sum ~1 when provided)."""

    # Lightweight structure description (no pymatgen hard dep in Phase 0)
    lattice_abc: tuple[float, float, float] | None = None
    """Lattice lengths a, b, c in Å."""

    lattice_angles: tuple[float, float, float] | None = None
    """Lattice angles α, β, γ in degrees."""

    spacegroup: str | None = None
    """Optional space-group symbol or number as string."""

    structure_cif: str | None = None
    """Optional CIF text for round-trip without pymatgen."""

    # Epitaxial / strain degrees of freedom
    substrate: str | None = None
    """Substrate label, e.g. ``Si(001)`` or ``Si(111)``."""

    strain_tensor: tuple[float, float, float, float, float, float] | None = None
    """Voigt strain components (εxx, εyy, εzz, εyz, εxz, εxy); fraction not %."""

    in_plane_strain: float | None = None
    """Scalar epitaxial in-plane strain when tensor is not needed."""

    # Bookkeeping
    tags: list[str] = Field(default_factory=list)
    """Free-form labels (e.g. ``sqs``, ``epitaxial``, ``bulk``)."""

    quality_tag: Literal["screening", "production", "mock", "unknown"] = "unknown"
    source: str = "manual"
    """How this candidate was generated (enumerator name, import, mock, …)."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form extras (e.g. ``energy_above_hull_proxy``, filter notes)."""

    energy_above_hull_proxy: float | None = None
    """Heuristic E_hull proxy (eV/atom) from the Phase-0 formation filter."""

    relaxed_structure_cif: str | None = None
    """CIF of the geometry after QE relaxation (when available)."""

    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("formula")
    @classmethod
    def _formula_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("formula must be a non-empty string")
        return v


class CandidateEvaluation(BaseModel):
    """Aggregated evaluation of a :class:`StructureCandidate`.

    Holds calculator outputs, Si-feasibility score, and ranking fields used by
    the CLI dry-run path and later production campaigns.
    """

    candidate: StructureCandidate
    """The structure / composition being evaluated."""

    scf: SCFResult | None = None
    phonon: PhononResult | None = None
    si_feasibility: SiFeasibilityScore | None = None

    # Optional later-phase attachments (kept for schema stability)
    josephson: Any | None = None
    """Placeholder for JosephsonMetrics (Phase 3+); unused in v0.1."""

    performance_score: float | None = None
    """Normalized superconducting performance proxy (Tc or pairing eigenvalue).

    Higher is better. Populated from Eliashberg Tc / DMFT eigenvalue later;
    mock calculator may fill a dummy value.
    """

    composite_score: float | None = None
    """Multi-objective score combining performance and Si-feasibility."""

    rank: int | None = None
    """1-based rank after sorting (filled by ranking module)."""

    status: str = "pending"
    """Overall evaluation status: ``pending``, ``ok``, ``failed``, ``mock``."""

    calculator_name: str | None = None
    """Name of the calculator that produced this evaluation."""

    errors: list[str] = Field(default_factory=list)
    notes: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
