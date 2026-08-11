"""DFT+U (Hubbard) helpers for Quantum ESPRESSO — Phase 3.1.

Provides:
- species / U,J resolution from :class:`~siscforge.models.config.DFTUConfig`
- pw.x SYSTEM namelist extras + optional HUBBARD card (QE ≥ 7.1)
- parse of total energy, magnetization, and basic occupancy proxies
- mock :class:`~siscforge.models.results.DFTUResult` for dry-run

**Out of scope (later packages):** Wannierization (P3.2), TRIQS/solid_dmft
(P3.3), pairing eigenvalue (P3.4), oxygen-vacancy enumeration (P3.5).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from siscforge import __version__
from siscforge.models.config import DFTConfig, DFTUConfig
from siscforge.models.provenance import Provenance
from siscforge.models.results import DFTUResult

# Correlated metals / rare earths commonly treated with DFT+U in nickelates
# and related oxides. Used only when hubbard_species is empty.
_DEFAULT_HUBBARD_ELEMENTS: frozenset[str] = frozenset(
    {
        "Ti",
        "V",
        "Cr",
        "Mn",
        "Fe",
        "Co",
        "Ni",
        "Cu",
        "Ru",
        "Rh",
        "Pd",
        "Ir",
        "Pt",
        "Ce",
        "Pr",
        "Nd",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
    }
)


def dftu_is_enabled(dft: DFTConfig | None, *, force: bool = False) -> bool:
    """Return True when DFT+U should run for this campaign / calculator."""
    if force:
        return True
    if dft is None:
        return False
    if bool(getattr(dft, "do_dftu", False)):
        return True
    dftu = getattr(dft, "dftu", None)
    return bool(dftu is not None and getattr(dftu, "enabled", False))


def resolve_hubbard_species(
    structure: Structure,
    dftu: DFTUConfig,
) -> list[str]:
    """Ordered unique Hubbard species present in *structure*."""
    present = {str(sp.symbol) for sp in structure.composition.elements}
    if dftu.hubbard_species:
        chosen = [s for s in dftu.hubbard_species if s in present]
        if not chosen:
            # Explicit list but none in cell — fall back to intersection of defaults
            chosen = sorted(present & _DEFAULT_HUBBARD_ELEMENTS)
        return chosen
    # Auto: correlated metals present in the structure
    auto = sorted(present & _DEFAULT_HUBBARD_ELEMENTS)
    if auto:
        return auto
    # Last resort: heaviest non-O/N/H/C/F element (oxide/nitride host)
    light = {"H", "He", "C", "N", "O", "F", "Ne", "Cl", "Ar"}
    metals = sorted(el for el in present if el not in light)
    return metals[:1] if metals else sorted(present)[:1]


def resolve_u_j_maps(
    structure: Structure,
    dftu: DFTUConfig,
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    """Return ``(species, U_by_species, J_by_species)`` for the cell."""
    species = resolve_hubbard_species(structure, dftu)
    u_map: dict[str, float] = {}
    j_map: dict[str, float] = {}
    for el in species:
        u_map[el] = float(dftu.U_by_species.get(el, dftu.U_eV))
        j_map[el] = float(dftu.J_by_species.get(el, dftu.J_eV))
    return species, u_map, j_map


def species_type_index(structure: Structure) -> dict[str, int]:
    """Map element symbol → 1-based ATOMIC_SPECIES index (QE convention)."""
    # pymatgen Structure.types_of_specie order is not always input order;
    # use sorted unique symbols by first appearance in sites.
    order: list[str] = []
    for site in structure:
        sym = str(site.specie.symbol)
        if sym not in order:
            order.append(sym)
    return {sym: i + 1 for i, sym in enumerate(order)}


def hubbard_system_extras(
    structure: Structure,
    dftu: DFTUConfig,
) -> dict[str, Any]:
    """Build SYSTEM namelist extras for classic ``lda_plus_u`` pw.x input.

    Works with QE 6.x–7.x namelist Hubbard parameters. A HUBBARD card can be
    appended separately via :func:`append_hubbard_card` for QE ≥ 7.1.
    """
    species, u_map, j_map = resolve_u_j_maps(structure, dftu)
    type_idx = species_type_index(structure)
    extras: dict[str, Any] = {
        "nspin": int(dftu.nspin),
        "lda_plus_u": True,
        "lda_plus_u_kind": int(dftu.lda_plus_u_kind),
        "Hubbard_projectors": dftu.hubbard_projectors,
    }
    for el in species:
        idx = type_idx.get(el)
        if idx is None:
            continue
        extras[f"Hubbard_U({idx})"] = float(u_map[el])
        j_val = float(j_map.get(el, 0.0))
        if j_val > 0.0:
            # Simplified (kind=0) uses Hubbard_J0; full uses Hubbard_J(1,itype)
            if dftu.lda_plus_u_kind == 0:
                extras[f"Hubbard_J0({idx})"] = j_val
            else:
                extras[f"Hubbard_J(1,{idx})"] = j_val
        # Starting magnetization for spin-polarized runs
        if dftu.nspin >= 2:
            mag = dftu.starting_magnetization.get(
                el, dftu.default_starting_magnetization
            )
            extras[f"starting_magnetization({idx})"] = float(mag)
    return extras


def append_hubbard_card(
    pw_text: str,
    structure: Structure,
    dftu: DFTUConfig,
) -> str:
    """Append a QE ≥ 7.1 ``HUBBARD`` card if not already present.

    The card is additive documentation for modern QE; classic namelist
    Hubbard_U(*) parameters remain the primary path for broader compatibility.
    """
    if re.search(r"^\s*HUBBARD\b", pw_text, flags=re.IGNORECASE | re.MULTILINE):
        return pw_text
    species, u_map, j_map = resolve_u_j_maps(structure, dftu)
    if not species:
        return pw_text
    lines = [f"HUBBARD ({dftu.hubbard_projectors})", ""]
    # Drop the blank we just added — rebuild cleanly
    lines = [f"HUBBARD ({dftu.hubbard_projectors})"]
    for el in species:
        # Default manifold: 3d for 3d metals, 4f for rare earths, 4d for 4d
        manifold = _default_manifold(el)
        lines.append(f"  U {el}-{manifold} {u_map[el]:.4f}")
        j_val = float(j_map.get(el, 0.0))
        if j_val > 0.0:
            lines.append(f"  J {el}-{manifold} {j_val:.4f}")
    card = "\n".join(lines) + "\n"
    text = pw_text if pw_text.endswith("\n") else pw_text + "\n"
    return text + card


def _default_manifold(element: str) -> str:
    """Heuristic correlated manifold label for HUBBARD card lines."""
    re_4f = {
        "Ce",
        "Pr",
        "Nd",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
    }
    d4 = {"Ru", "Rh", "Pd", "Ag", "Cd", "Y", "Zr", "Nb", "Mo", "Tc"}
    d5 = {"Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au"}
    if element in re_4f:
        return "4f"
    if element in d4:
        return "4d"
    if element in d5:
        return "5d"
    return "3d"


def parse_magnetization(text: str) -> tuple[float | None, float | None]:
    """Extract total and absolute magnetization (μ_B) from pw.x stdout."""
    total = None
    absolute = None
    m_tot = re.findall(
        r"total magnetization\s*=\s*([-\d.Ee+]+)\s*Bohr",
        text,
        flags=re.IGNORECASE,
    )
    if m_tot:
        total = float(m_tot[-1])
    m_abs = re.findall(
        r"absolute magnetization\s*=\s*([-\d.Ee+]+)\s*Bohr",
        text,
        flags=re.IGNORECASE,
    )
    if m_abs:
        absolute = float(m_abs[-1])
    return total, absolute


def parse_atomic_magnetic_moments(text: str) -> dict[str, float]:
    """Parse per-atom magnetic moments when QE prints them.

    Returns a compact map ``atom_index → μ_B`` or ``species → mean μ_B``
    depending on available lines. Empty if not present.
    """
    moments: dict[str, float] = {}
    # Common block: "Magnetic moment per site:" then lines with atom index
    site_pat = re.compile(
        r"atom:\s*(\d+)\s+.*?(?:charge|moment).*?([-\d.Ee+]+)",
        re.IGNORECASE,
    )
    for m in site_pat.finditer(text):
        moments[f"atom_{int(m.group(1))}"] = float(m.group(2))
    if moments:
        return moments
    # Fallback: "magnetic moment per atom" style summaries
    simple = re.findall(
        r"magnetic moment\s*(?:of|on)?\s*([A-Za-z]+)\s*[:=]\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    )
    for el, val in simple:
        moments[el] = float(val)
    return moments


def parse_occupancy_proxy(text: str) -> dict[str, float]:
    """Best-effort Hubbard occupancy / atomic charge summary from pw.x text.

    QE versions differ widely; we store whatever compact numbers we can find
    under stable keys for later DMFT comparison (P3.3).
    """
    occ: dict[str, float] = {}
    # Hubbard occupation lines (various QE versions)
    for m in re.finditer(
        r"Hubbard\s+(?:occupation|occupancy)[^\n]*?([A-Za-z]+)\s*[:=]\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    ):
        occ[f"hubbard_{m.group(1)}"] = float(m.group(2))
    for m in re.finditer(
        r"Tr\[ns\(na\)\]\s*=\s*([-\d.Ee+]+)",
        text,
        flags=re.IGNORECASE,
    ):
        # Sequential indices if multiple
        key = f"Tr_ns_{len(occ)}"
        occ[key] = float(m.group(1))
    return occ


def parse_dftu_output(
    path_or_text: Path | str,
    *,
    dftu: DFTUConfig,
    structure: Structure | None = None,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
) -> DFTUResult:
    """Parse a DFT+U pw.x output into :class:`DFTUResult`."""
    from siscforge.calculators.qe.parser import (
        parse_fermi_energy_eV,
        parse_pw_energy_from_text,
        resolve_text_or_path,
    )

    text, source_name = resolve_text_or_path(path_or_text)
    energy = parse_pw_energy_from_text(text)
    job_done = "JOB DONE" in text.upper()
    fermi = parse_fermi_energy_eV(text)
    total_m, abs_m = parse_magnetization(text)
    moments = parse_atomic_magnetic_moments(text)
    occ = parse_occupancy_proxy(text)
    is_metallic = bool(
        re.search(r"the Fermi energy is", text, re.IGNORECASE)
        or re.search(r"occupations\s*=\s*['\"]?smearing", text, re.IGNORECASE)
    )
    status = "ok" if energy is not None and job_done else (
        "ok" if energy is not None else "failed"
    )

    species: list[str] = list(dftu.hubbard_species)
    u_map = dict(dftu.U_by_species)
    j_map = dict(dftu.J_by_species)
    if structure is not None:
        species, u_map, j_map = resolve_u_j_maps(structure, dftu)

    scalar_u = float(dftu.U_eV)
    if len(u_map) == 1:
        scalar_u = next(iter(u_map.values()))
    elif u_map:
        # Representative mean when multiple species
        scalar_u = sum(u_map.values()) / len(u_map)

    scalar_j = float(dftu.J_eV)
    if len(j_map) == 1:
        scalar_j = next(iter(j_map.values()))
    elif j_map:
        scalar_j = sum(j_map.values()) / len(j_map)

    raw: dict[str, Any] = {
        "source": source_name,
        "job_done": job_done,
        "pathway": "dftu",
        "extension_hooks": {
            "p3_2_wannier": "attach WannierResult / quality metrics here",
            "p3_3_dmft": "parallel CandidateEvaluation.dmft field (not yet)",
            "p3_4_pairing": "map leading eigenvalue → performance_score",
        },
    }
    if extra_raw:
        raw.update(extra_raw)

    return DFTUResult(
        U_eV=scalar_u,
        J_eV=scalar_j,
        U_by_species=u_map,
        J_by_species=j_map,
        hubbard_species=species,
        hubbard_projectors=dftu.hubbard_projectors,
        occupancy_summary=occ,
        magnetic_moments=moments,
        total_magnetization=total_m,
        absolute_magnetization=abs_m,
        total_energy_eV=energy,
        is_metallic=is_metallic,
        fermi_energy_eV=fermi,
        status=status,
        quality_tag=quality_tag if quality_tag in {"screening", "production", "mock", "unknown"} else "screening",  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="qe_dftu",
            software={"siscforge": __version__},
            parameters={
                "U_eV": scalar_u,
                "J_eV": scalar_j,
                "hubbard_species": species,
                "hubbard_projectors": dftu.hubbard_projectors,
            },
            notes="DFT+U pw.x parse (P3.1)",
        ),
    )


def mock_dftu_result(
    *,
    seed: str,
    dftu: DFTUConfig | None = None,
    formula: str = "",
    material_family: str = "other",
    quality_tag: str = "mock",
) -> DFTUResult:
    """Deterministic placeholder DFTUResult for dry-run / mock calculator."""
    cfg = dftu or DFTUConfig(enabled=True)
    digest = hashlib.sha256(f"{seed}:dftu".encode()).hexdigest()
    r = int(digest[:8], 16) / 0xFFFFFFFF

    # Nickelates get Ni-like moments and partial d occupancy
    species = list(cfg.hubbard_species) or (
        ["Ni"] if material_family == "nickelate" or "Ni" in formula else ["M"]
    )
    u_map = {
        el: float(cfg.U_by_species.get(el, cfg.U_eV)) for el in species
    }
    j_map = {
        el: float(cfg.J_by_species.get(el, cfg.J_eV)) for el in species
    }
    # Plausible Ni²⁺ d⁸-like occupancy ~8 e⁻, moment ~1–2 μ_B
    base_occ = 8.0 if any(s in {"Ni", "Cu", "Fe", "Co"} for s in species) else 6.0 + r
    occ = {f"hubbard_{species[0]}": round(base_occ - 0.3 * r, 3)}
    mom = round(0.8 + 1.4 * r, 3)
    total_m = round(mom * (1.0 if material_family == "nickelate" else 0.5 + r), 3)
    energy = round(-150.0 - 40.0 * r, 4)

    return DFTUResult(
        U_eV=float(cfg.U_eV) if len(u_map) != 1 else next(iter(u_map.values())),
        J_eV=float(cfg.J_eV) if len(j_map) != 1 else next(iter(j_map.values())),
        U_by_species=u_map,
        J_by_species=j_map,
        hubbard_species=species,
        hubbard_projectors=cfg.hubbard_projectors,
        occupancy_summary=occ,
        magnetic_moments={species[0]: mom},
        total_magnetization=total_m,
        absolute_magnetization=round(abs(total_m) + 0.1 * r, 3),
        total_energy_eV=energy,
        is_metallic=True,
        fermi_energy_eV=round(5.0 + 3.0 * r, 3),
        status="mock",
        quality_tag="mock" if quality_tag == "mock" else quality_tag,  # type: ignore[arg-type]
        raw={
            "method": "mock_dftu",
            "pathway": "dftu",
            "extension_hooks": {
                "p3_2_wannier": "attach WannierResult / quality metrics here",
                "p3_3_dmft": "parallel CandidateEvaluation.dmft field (not yet)",
                "p3_4_pairing": "map leading eigenvalue → performance_score",
            },
        },
        provenance=Provenance(
            source="mock_calculator",
            software={"siscforge": __version__},
            parameters={"U_eV": cfg.U_eV, "J_eV": cfg.J_eV, "species": species},
            notes="dry-run DFT+U placeholder (P3.1)",
        ),
    )
