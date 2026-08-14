"""Structure candidates and end-to-end evaluation records."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from siscforge.models.provenance import Provenance
from siscforge.models.results import (
    DFTUResult,
    DMFTResult,
    ElectronPhononResult,
    JosephsonMetrics,
    PhononResult,
    SCFResult,
    SiFeasibilityScore,
    WannierResult,
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
    electron_phonon: ElectronPhononResult | None = None
    """EPW / Eliashberg result (Phase 1); optional on mock path."""

    dftu: DFTUResult | None = None
    """DFT+U correlated proxy (P3.1). Optional — leave None for conventional
    nitride/MgB₂ campaigns. Populated only when DFT+U is explicitly enabled
    (``dft.do_dftu`` / ``dft.dftu.enabled`` / calculator ``qe-dftu``).

    Extension points:
    - **P3.2** Wannierization → ``wannier: WannierResult | None`` (sibling field)
    - **P3.3** TRIQS/solid_dmft → ``dmft: DMFTResult | None`` (sibling field)
    - **P3.4** pairing eigenvalue → maps into ``performance_score``
    """

    wannier: WannierResult | None = None
    """Standalone Wannierization quality (P3.2). Optional — leave None for
    conventional nitride/MgB₂ campaigns. Populated when Wannier is enabled
    (``dft.do_wannier`` / ``dft.wannier.enabled`` / calculator ``qe-wannier``).

    Distinct from ``electron_phonon.wannier_ok`` (EPW-internal flag).
    P3.3 TRIQS/solid_dmft consumes this field's artifacts and DMFT gate.
    """

    dmft: DMFTResult | None = None
    """DMFT / solid_dmft result (P3.3). Optional — leave None for
    conventional nitride/MgB₂ campaigns. Populated when DMFT is enabled
    (``dft.do_dmft`` / ``dft.dmft.enabled`` / calculator ``qe-dmft``).

    Pairing fields (``leading_pairing_eigenvalue``, ``pairing_symmetry``)
    are mapped into ``performance_score`` by **P3.4**
    (``siscforge.scoring.pairing``) when a usable signal is present.
    """

    si_feasibility: SiFeasibilityScore | None = None

    tc_lambda_surrogate: dict[str, Any] | None = None
    """λ/Tc surrogate stub prediction (dict form of TcLambdaPrediction).

    Distinct from real ``electron_phonon``. Real EPW takes precedence for
    ``performance_score`` when both are present.
    """

    # Optional later-phase attachments
    josephson: JosephsonMetrics | None = None
    """Tier-1 analytic Josephson metrics (P4.1). Optional — leave None
    unless ``josephson.enabled`` is set. Always approximate / ranking only.
    """

    performance_score: float | None = None
    """Superconducting performance proxy in kelvin (Tc-like).

    Higher is better. Filled from ``electron_phonon.best_tc_K()`` when a
    trusted EPW result exists; else from the P3.4 DMFT pairing map when a
    usable ``dmft.leading_pairing_eigenvalue`` is present; mock calculator
    may fill a dummy conventional value. May be filled from the λ/Tc
    **surrogate stub** only when no e-ph or pairing score exists (labeled
    in notes).
    """

    performance_score_source: str | None = None
    """Where ``performance_score`` came from: ``epw``, ``mock``,
    ``surrogate``, ``dmft_pairing``, ``dmft_pairing_mock``, …"""

    composite_score: float | None = None
    """Multi-objective score combining performance and Si-feasibility."""

    rank: int | None = None
    """1-based rank after sorting (filled by ranking module)."""

    on_pareto_front: bool | None = None
    """True if non-dominated on primary ranking axes (P2.4); None if Pareto off."""

    ranking_weights: dict[str, float] | None = None
    """Active ranking weight vector + performance ceiling used for this row (P2.4)."""

    composite_breakdown: dict[str, Any] | None = None
    """Normalized axis values and pre-penalty blend for transparent export (P2.4)."""

    acquisition_score: float | None = None
    """Active-learning acquisition score (higher → run expensive job sooner)."""

    al_selected_for_expensive: bool | None = None
    """Whether AL selected this candidate for the expensive calculator path."""

    acquisition_pool: str | None = None
    """P3.6 pathway pool: ``conventional`` | ``unconventional`` | ``unknown``."""

    acquisition_mode: str | None = None
    """P3.6 acquisition mode used for this row: ``off`` | ``joint`` | ``separate``."""

    acquisition_pool_reason: str | None = None
    """Documented pool-derivation winner (e.g. ``source:dmft_pairing``)."""

    result_quality: Literal[
        "production",
        "screening",
        "screening_suspect",
        "unreliable",
        "unknown",
    ] = "unknown"
    """Trust tier for phonon / EPW results (see ``siscforge.quality``)."""

    quality_flags: list[str] = Field(default_factory=list)
    """Machine-readable quality flags (e.g. ``high_lambda``, ``imaginary_modes``)."""

    quality_notes: str = ""
    """Human-readable quality rationale (not a substitute for denser grids)."""

    status: str = "pending"
    """Overall evaluation status: ``pending``, ``ok``, ``failed``, ``mock``,
    ``surrogate_only`` (AL-deferred)."""

    calculator_name: str | None = None
    """Name of the calculator that produced this evaluation."""

    errors: list[str] = Field(default_factory=list)
    notes: str = ""
    provenance: Provenance = Field(default_factory=Provenance)
