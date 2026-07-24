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
from siscforge.structure.nitrides import (
    composition_fractions,
    enumerate_nitrides,
    formula_from_structure,
)
from siscforge.structure.strain import apply_epitaxial_strain


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


def enumerate_from_config(enum: EnumerationConfig) -> list[StructureCandidate]:
    """Generate :class:`StructureCandidate` objects from an enumeration config.

    For each bulk structure × substrate × strain value, applies biaxial strain
    and packages a fully populated candidate (CIF, lattice, strain tensor).
    """
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
                strained, tensor, applied_eps = apply_epitaxial_strain(
                    structure,
                    substrate=substrate,
                    in_plane_strain=float(eps),
                    poisson_ratio=enum.poisson_ratio,
                    relax_out_of_plane=True,
                    match_substrate=False,
                )
                tags = [family, meta.get("kind", "bulk"), "epitaxial"]
                if abs(float(eps)) < 1e-15:
                    tags.append("bulk_strain_0")
                cand = structure_to_candidate(
                    strained,
                    material_family=family,
                    substrate=substrate,
                    in_plane_strain=applied_eps,
                    strain_tensor=tensor,
                    source="structure_generator",
                    tags=tags,
                    metadata={**meta, "requested_strain": float(eps)},
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
