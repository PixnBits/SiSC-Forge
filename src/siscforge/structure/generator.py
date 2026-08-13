"""Main structure-generation entry points for campaigns."""

from __future__ import annotations

from typing import Any

from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from siscforge import __version__
from siscforge.models.candidate import StructureCandidate
from siscforge.models.config import CampaignConfig, EnumerationConfig
from siscforge.models.provenance import Provenance
from siscforge.structure.bsi import enumerate_b_doped_si
from siscforge.structure.mgb2 import build_mgb2, mgb2_metadata
from siscforge.structure.nickelates import (
    enumerate_nickelates,
    structure_from_nickelate_formula,
)
from siscforge.structure.nitrides import (
    composition_fractions,
    enumerate_nitrides,
    formula_from_structure,
)
from siscforge.structure.strain import apply_biaxial_strain, apply_epitaxial_strain


def structure_to_candidate(
    structure: Structure,
    *,
    material_family: str = "other",
    substrate: str | None = None,
    in_plane_strain: float | None = None,
    strain_tensor: tuple[float, float, float, float, float, float] | None = None,
    source: str = "structure_generator",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    quality_tag: str = "screening",
    formula: str | None = None,
) -> StructureCandidate:
    """Convert a pymatgen ``Structure`` into a :class:`StructureCandidate`."""
    lat = structure.lattice
    cif = structure.to(fmt="cif")

    spacegroup: str | None = None
    try:
        sga = SpacegroupAnalyzer(structure, symprec=0.1)
        spacegroup = sga.get_space_group_symbol()
    except Exception:  # noqa: BLE001 — disordered / strained cells may fail
        spacegroup = None

    family = material_family if material_family in {
        "tm_nitride",
        "b_doped_si",
        "mgb2_boride",
        "nickelate",
        "cuprate",
        "other",
    } else "other"

    return StructureCandidate(
        formula=formula or formula_from_structure(structure),
        material_family=family,  # type: ignore[arg-type]
        composition=composition_fractions(structure),
        lattice_abc=(float(lat.a), float(lat.b), float(lat.c)),
        lattice_angles=(float(lat.alpha), float(lat.beta), float(lat.gamma)),
        spacegroup=spacegroup,
        structure_cif=cif,
        substrate=substrate,
        in_plane_strain=in_plane_strain,
        strain_tensor=strain_tensor,
        tags=list(tags or []),
        quality_tag=quality_tag,  # type: ignore[arg-type]
        source=source,
        metadata=dict(metadata or {}),
        provenance=Provenance(
            source=source,
            software={"siscforge": __version__, "pymatgen": _pymatgen_version()},
            parameters={
                "n_sites": len(structure),
                "substrate": substrate,
                "in_plane_strain": in_plane_strain,
            },
        ),
    )


def _pymatgen_version() -> str:
    try:
        import pymatgen

        return getattr(pymatgen, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _as_tuple3(values: list[int] | tuple[int, int, int]) -> tuple[int, int, int]:
    if len(values) != 3:
        raise ValueError(f"Expected length-3 supercell, got {values!r}")
    return int(values[0]), int(values[1]), int(values[2])


def _is_unsupported_substrate_error(exc: ValueError) -> bool:
    msg = str(exc).lower()
    return "unsupported substrate" in msg or "only si substrates" in msg


def _apply_campaign_strain(
    structure: Structure,
    *,
    substrate: str,
    in_plane_strain: float,
    poisson_ratio: float,
    family: str,
) -> tuple[Structure, tuple[float, float, float, float, float, float], float, bool]:
    """Apply epitaxial strain; nickelates may sit on non-Si labels (e.g. SrTiO3).

    Existing nitride / MgB₂ paths still go through ``apply_epitaxial_strain``
    (Si-only). For nickelates, an unrecognized substrate falls back to
    requested biaxial strain so enumeration does not crash.

    Returns
    -------
    strained, tensor, eps_ip, biaxial_fallback
    """
    try:
        strained, tensor, eps = apply_epitaxial_strain(
            structure,
            substrate=substrate,
            in_plane_strain=float(in_plane_strain),
            poisson_ratio=poisson_ratio,
            relax_out_of_plane=True,
            match_substrate=False,
        )
        return strained, tensor, eps, False
    except ValueError as exc:
        if family != "nickelate" or not _is_unsupported_substrate_error(exc):
            raise
        strained, tensor = apply_biaxial_strain(
            structure,
            float(in_plane_strain),
            poisson_ratio=poisson_ratio,
            relax_out_of_plane=True,
        )
        return strained, tensor, float(in_plane_strain), True


def _candidates_from_specs(enum: EnumerationConfig) -> list[StructureCandidate]:
    """Build candidates from exact shortlist ``candidate_specs`` (no grid)."""
    from pymatgen.core import Structure as PMGStructure

    from siscforge.structure.nitrides import _structure_from_formula

    epi = getattr(enum, "epitaxy_orientation", "auto")
    use_buf = bool(getattr(enum, "use_buffers", True))
    candidates: list[StructureCandidate] = []

    for spec in enum.candidate_specs:
        family = spec.material_family
        meta: dict[str, Any] = dict(spec.metadata or {})
        meta.setdefault("requested_strain", float(spec.in_plane_strain))
        meta["epitaxy_orientation"] = epi
        meta["use_buffers"] = use_buf
        meta["shortlist"] = True

        if spec.structure_cif:
            structure = PMGStructure.from_str(spec.structure_cif, fmt="cif")
            tensor = None
            applied_eps = float(spec.in_plane_strain)
            tags = [family, "shortlist", "from_cif"]
            if abs(applied_eps) < 1e-15:
                tags.append("bulk_strain_0")
            cand = structure_to_candidate(
                structure,
                material_family=family,
                substrate=spec.substrate,
                in_plane_strain=applied_eps,
                strain_tensor=tensor,
                source="shortlist_cif",
                tags=tags,
                metadata={**meta, "formula": spec.formula},
                formula=spec.formula,
            )
        else:
            if family == "mgb2_boride":
                structure = build_mgb2()
                meta = {**mgb2_metadata(), **meta, "formula": "MgB2", "kind": "binary"}
            elif family == "nickelate":
                structure, nmeta = structure_from_nickelate_formula(
                    spec.formula,
                    metadata=spec.metadata,
                    supercell=_as_tuple3(
                        getattr(enum, "nickelate_supercell", None) or enum.supercell
                    ),
                )
                meta = {**nmeta, **meta}
            else:
                structure, nmeta = _structure_from_formula(
                    spec.formula,
                    supercell=_as_tuple3(enum.supercell),
                    seed=enum.seed,
                )
                meta = {**nmeta, **meta}
            strained, tensor, applied_eps, biaxial_fallback = _apply_campaign_strain(
                structure,
                substrate=spec.substrate,
                in_plane_strain=float(spec.in_plane_strain),
                poisson_ratio=enum.poisson_ratio,
                family=family,
            )
            tags = [family, meta.get("kind", "bulk"), "shortlist", "epitaxial"]
            if abs(float(spec.in_plane_strain)) < 1e-15:
                tags.append("bulk_strain_0")
            if meta.get("vacancy_pattern"):
                tags.append(str(meta["vacancy_pattern"]))
            if biaxial_fallback:
                tags.append("biaxial_fallback")
                meta["biaxial_fallback"] = True
            cand = structure_to_candidate(
                strained,
                material_family=family,
                substrate=spec.substrate,
                in_plane_strain=applied_eps,
                strain_tensor=tensor,
                source="shortlist_rebuild",
                tags=tags,
                metadata=meta,
                formula=spec.formula,
            )

        if spec.candidate_id:
            cand = cand.model_copy(update={"candidate_id": spec.candidate_id})
        candidates.append(cand)
        if len(candidates) >= enum.max_candidates:
            break
    return candidates


def enumerate_from_config(enum: EnumerationConfig) -> list[StructureCandidate]:
    """Generate :class:`StructureCandidate` objects from an enumeration config.

    For each bulk structure × substrate × strain value, applies biaxial strain
    and packages a fully populated candidate (CIF, lattice, strain tensor).

    When ``candidate_specs`` is non-empty, only those exact shortlist rows are
    produced (desktop EPW path).
    """
    if enum.candidate_specs:
        return _candidates_from_specs(enum)

    bulk_items: list[tuple[Structure, dict[str, Any], str]] = []

    families = enum.material_families or ["tm_nitride"]
    for family in families:
        if family == "tm_nitride":
            pairs = enumerate_nitrides(
                metals=enum.metals or None,
                ternary_metals=enum.ternary_metals or None,
                x_values=enum.x_values or None,
                formulas=enum.formulas or None,
                supercell=_as_tuple3(enum.supercell),
                seed=enum.seed,
            )
            for structure, meta in pairs:
                bulk_items.append((structure, meta, "tm_nitride"))
        elif family == "b_doped_si":
            conc = enum.b_concentrations or [0.05, 0.10]
            pairs = enumerate_b_doped_si(
                conc,
                supercell=_as_tuple3(enum.bsi_supercell),
                seed=enum.seed,
            )
            for structure, meta in pairs:
                bulk_items.append((structure, meta, "b_doped_si"))
        elif family == "mgb2_boride":
            # Bulk MgB2 golden prototype (optionally listed formulas)
            formulas = enum.formulas or ["MgB2"]
            for formula in formulas:
                norm = formula.replace("₂", "2").replace(" ", "").upper()
                if norm not in {"MGB2"} and not (
                    "MG" in norm and "B" in norm
                ):
                    continue
                structure = build_mgb2()
                meta = {**mgb2_metadata(), "formula": "MgB2", "kind": "binary"}
                bulk_items.append((structure, meta, "mgb2_boride"))
        elif family == "nickelate":
            # P3.5 — opt-in infinite-layer + curated O-vacancy / apical-O set.
            # formulas on a nickelate-only campaign are parsed as RNiO₂-family
            # cells; mixed campaigns should leave formulas for the nitride path.
            ni_formulas = None
            if enum.formulas and families == ["nickelate"]:
                ni_formulas = list(enum.formulas)
            pairs = enumerate_nickelates(
                rare_earths=enum.nickelate_rare_earths or None,
                patterns=enum.nickelate_patterns or None,
                formulas=ni_formulas,
                max_patterns=int(enum.nickelate_max_patterns),
                supercell=_as_tuple3(enum.nickelate_supercell),
                seed=enum.seed,
            )
            if not pairs:
                raise ValueError(
                    "nickelate family requested but no infinite-layer / "
                    "O-vacancy cells were generated; check rare earths / patterns"
                )
            for structure, meta in pairs:
                bulk_items.append((structure, meta, "nickelate"))
        else:
            # Unknown family: skip rather than hard-fail so mixed campaigns work.
            continue

    if not bulk_items:
        # Sensible default: binaries NbN, TiN, ZrN
        pairs = enumerate_nitrides(metals=["Nb", "Ti", "Zr"], formulas=None)
        for structure, meta in pairs:
            bulk_items.append((structure, meta, "tm_nitride"))

    substrates = enum.substrates or ["Si(001)"]
    strains = enum.strain_values if enum.strain_values is not None else [0.0]
    candidates: list[StructureCandidate] = []

    for structure, meta, family in bulk_items:
        for substrate in substrates:
            for eps in strains:
                strained, tensor, applied_eps, biaxial_fallback = _apply_campaign_strain(
                    structure,
                    substrate=substrate,
                    in_plane_strain=float(eps),
                    poisson_ratio=enum.poisson_ratio,
                    family=family,
                )
                tags = [family, meta.get("kind", "bulk"), "epitaxial"]
                if abs(float(eps)) < 1e-15:
                    tags.append("bulk_strain_0")
                if meta.get("vacancy_pattern"):
                    tags.append(str(meta["vacancy_pattern"]))
                epi = getattr(enum, "epitaxy_orientation", "auto")
                use_buf = bool(getattr(enum, "use_buffers", True))
                if epi == "45deg":
                    tags.append("epitaxy_45deg")
                if biaxial_fallback:
                    tags.append("biaxial_fallback")
                cand = structure_to_candidate(
                    strained,
                    material_family=family,
                    substrate=substrate,
                    in_plane_strain=applied_eps,
                    strain_tensor=tensor,
                    source="structure_generator",
                    tags=tags,
                    metadata={
                        **meta,
                        "requested_strain": float(eps),
                        "epitaxy_orientation": epi,
                        "use_buffers": use_buf,
                        **({"biaxial_fallback": True} if biaxial_fallback else {}),
                    },
                    formula=meta.get("formula"),
                )
                candidates.append(cand)
                if len(candidates) >= enum.max_candidates:
                    return candidates

    return candidates


def generate_candidates(
    config: CampaignConfig | EnumerationConfig,
    *,
    n: int | None = None,
) -> list[StructureCandidate]:
    """Public entry: enumerate candidates from a campaign or enumeration config.

    Parameters
    ----------
    n:
        Optional hard cap (overrides ``max_candidates`` when smaller).
    """
    if isinstance(config, CampaignConfig):
        enum = config.enumeration
    else:
        enum = config

    if n is not None:
        enum = enum.model_copy(update={"max_candidates": n})

    return enumerate_from_config(enum)


# Back-compat alias used by older tests / docs
def generate_fake_candidates(
    config: CampaignConfig,
    *,
    n: int | None = None,
) -> list[StructureCandidate]:
    """Deprecated alias for :func:`generate_candidates`."""
    return generate_candidates(config, n=n)
