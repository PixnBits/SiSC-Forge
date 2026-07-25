"""Structure generation, strain application, and StructureCandidate adapters."""

from siscforge.structure.bsi import build_b_doped_si, enumerate_b_doped_si
from siscforge.structure.generator import (
    enumerate_from_config,
    generate_candidates,
    structure_to_candidate,
)
from siscforge.structure.mgb2 import build_mgb2, mgb2_metadata
from siscforge.structure.nitrides import (
    ROCKSALT_LATTICE_CONSTANTS,
    build_binary_nitride,
    build_rocksalt_conventional,
    build_rocksalt_primitive,
    build_ternary_nitride,
    enumerate_nitrides,
)
from siscforge.structure.strain import (
    SI_LATTICE_CONSTANT,
    apply_biaxial_strain,
    apply_epitaxial_strain,
    lattice_mismatch_percent,
    parse_substrate,
    voigt_biaxial,
)

__all__ = [
    "ROCKSALT_LATTICE_CONSTANTS",
    "SI_LATTICE_CONSTANT",
    "apply_biaxial_strain",
    "apply_epitaxial_strain",
    "build_b_doped_si",
    "build_binary_nitride",
    "build_mgb2",
    "build_rocksalt_conventional",
    "build_rocksalt_primitive",
    "build_ternary_nitride",
    "enumerate_b_doped_si",
    "mgb2_metadata",
    "enumerate_from_config",
    "enumerate_nitrides",
    "generate_candidates",
    "lattice_mismatch_percent",
    "parse_substrate",
    "structure_to_candidate",
    "voigt_biaxial",
]
