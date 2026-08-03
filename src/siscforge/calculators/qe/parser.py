"""Parse Quantum ESPRESSO / phonopy-style outputs into SCFResult and PhononResult."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from pymatgen.core import Lattice, Structure

from siscforge import __version__
from siscforge.models.provenance import Provenance
from siscforge.models.results import PhononResult, SCFResult

# Acoustic-mode noise floor: |ω| below this (cm⁻¹) is treated as numerical zero.
DEFAULT_IMAG_THRESHOLD_CM1 = 5.0

# Ry → eV
_RY_TO_EV = 13.605693122994
# Bohr → Å
_BOHR_TO_ANG = 0.529177210903

# Paths longer than this are never passed to ``Path(...).is_file()`` — multi-KB
# QE log blobs used to raise ``OSError: [Errno 36] File name too long``.
_MAX_SAFE_PATH_LEN = 1024


def resolve_text_or_path(path_or_text: Path | str) -> tuple[str, str]:
    """Load QE output from a file path **or** treat the argument as raw text.

    Returns ``(text, source_name)`` where *source_name* is the path string or
    ``"<string>"``.

    **Never** calls ``Path(log_blob).is_file()`` on multi-line / long strings:
    that produced Errno 36 when phonon failure paths passed full ``ph.out``
    contents into :func:`parse_ph_output`.
    """
    if isinstance(path_or_text, Path):
        p = path_or_text
        try:
            return p.read_text(encoding="utf-8", errors="replace"), str(p)
        except OSError as exc:
            return "", f"<unreadable:{p}: {exc}>"

    s = str(path_or_text)
    # Log bodies always have newlines / are huge — treat as text, never as path.
    if not s:
        return "", "<string>"
    if "\n" in s or "\r" in s or "\x00" in s:
        return s, "<string>"
    if len(s) > _MAX_SAFE_PATH_LEN:
        return s, "<string>"
    stripped = s.strip()
    if not stripped:
        return s, "<string>"
    # Prefer path only for short path-like strings
    looks_path = (
        stripped.startswith("/")
        or stripped.startswith("./")
        or stripped.startswith("../")
        or (
            len(stripped) < 256
            and (
                "/" in stripped
                or stripped.endswith((".out", ".in", ".xml", ".log"))
            )
        )
    )
    if not looks_path:
        return s, "<string>"
    try:
        p = Path(stripped)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace"), str(p)
    except OSError:
        pass
    return s, "<string>"


def summarize_frequencies(
    frequencies_cm1: list[float],
    *,
    imag_threshold_cm1: float = DEFAULT_IMAG_THRESHOLD_CM1,
) -> dict[str, Any]:
    """Summarize a flat list of phonon frequencies (cm⁻¹).

    Imaginary modes are represented as **negative** real numbers (QE convention
    in many dumps) or as values with an ``i`` suffix already converted to negative.
    """
    if not frequencies_cm1:
        return {
            "min_frequency_cm1": None,
            "max_frequency_cm1": None,
            "n_modes": 0,
            "has_imaginary_modes": False,
            "dynamically_stable": True,
            "n_imaginary": 0,
        }

    freqs = [float(f) for f in frequencies_cm1]
    min_f = min(freqs)
    max_f = max(freqs)
    # Count modes clearly below -threshold (ignore tiny acoustic noise)
    n_imag = sum(1 for f in freqs if f < -abs(imag_threshold_cm1))
    has_imag = n_imag > 0
    return {
        "min_frequency_cm1": min_f,
        "max_frequency_cm1": max_f,
        "n_modes": len(freqs),
        "has_imaginary_modes": has_imag,
        "dynamically_stable": not has_imag,
        "n_imaginary": n_imag,
    }


def parse_relaxed_structure_from_text(
    text: str,
    *,
    fallback: Structure | None = None,
) -> Structure | None:
    """Extract the final CELL_PARAMETERS + ATOMIC_POSITIONS from pw.x output.

    Handles units: ``angstrom``, ``bohr``, ``alat`` (with alat in Bohr).
    Positions: ``crystal`` (preferred) or ``angstrom``.
    Returns ``None`` if blocks cannot be parsed (caller should use *fallback*).
    """
    # Last CELL_PARAMETERS block
    cell_matches = list(
        re.finditer(
            r"CELL_PARAMETERS\s*\((?P<unit>[^)]+)\)\s*\n"
            r"(?P<a1>[^\n]+)\n(?P<a2>[^\n]+)\n(?P<a3>[^\n]+)",
            text,
            re.IGNORECASE,
        )
    )
    if not cell_matches:
        return fallback
    cm = cell_matches[-1]
    unit = cm.group("unit").strip().lower()
    try:
        matrix = np.array(
            [
                [float(x) for x in cm.group("a1").split()[:3]],
                [float(x) for x in cm.group("a2").split()[:3]],
                [float(x) for x in cm.group("a3").split()[:3]],
            ],
            dtype=float,
        )
    except (ValueError, IndexError):
        return fallback

    if unit.startswith("alat"):
        # e.g. alat= 8.30000000  (Bohr)
        m_alat = re.search(r"alat\s*=\s*([-\d.eE+]+)", unit)
        alat_bohr = float(m_alat.group(1)) if m_alat else 1.0
        matrix = matrix * alat_bohr * _BOHR_TO_ANG
    elif "bohr" in unit:
        matrix = matrix * _BOHR_TO_ANG
    # angstrom: as-is

    lattice = Lattice(matrix)

    # Last ATOMIC_POSITIONS block (lines may be indented)
    pos_matches = list(
        re.finditer(
            r"ATOMIC_POSITIONS\s*\((?P<punit>[^)]+)\)\s*\n(?P<body>(?:[ \t]*[A-Za-z][^\n]*\n?)+)",
            text,
            re.IGNORECASE,
        )
    )
    if not pos_matches:
        return fallback
    pm = pos_matches[-1]
    punit = pm.group("punit").strip().lower()
    species: list[str] = []
    coords: list[list[float]] = []
    for line in pm.group("body").strip().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        # Skip if not an atom line
        if not parts[0][0].isalpha():
            continue
        # Stop at QE section markers
        if parts[0].lower() in {"end", "begin", "cell_parameters", "k_points"}:
            break
        try:
            species.append(parts[0])
            coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
        except ValueError:
            continue
    if not species:
        return fallback

    coords_arr = np.array(coords, dtype=float)
    if "crystal" in punit:
        return Structure(lattice, species, coords_arr, coords_are_cartesian=False)
    if "angstrom" in punit or "bohr" in punit:
        if "bohr" in punit:
            coords_arr = coords_arr * _BOHR_TO_ANG
        return Structure(lattice, species, coords_arr, coords_are_cartesian=True)
    # default assume crystal
    return Structure(lattice, species, coords_arr, coords_are_cartesian=False)


def parse_relaxed_structure(
    path_or_text: Path | str,
    *,
    fallback: Structure | None = None,
) -> Structure | None:
    """Load pw.x output and parse the final relaxed geometry."""
    text, _src = resolve_text_or_path(path_or_text)
    return parse_relaxed_structure_from_text(text, fallback=fallback)


def parse_pw_energy_from_text(text: str) -> float | None:
    """Extract final total energy in eV from pw.x stdout / PWOutput text."""
    # Prefer the last "!    total energy" line (converged).
    pattern = re.compile(
        r"!\s*total energy\s*=\s*([-\d.Ee+]+)\s*Ry",
        re.IGNORECASE,
    )
    matches = pattern.findall(text)
    if matches:
        return float(matches[-1]) * _RY_TO_EV

    # Fallback: "total energy              =" without bang
    pattern2 = re.compile(
        r"total energy\s*=\s*([-\d.Ee+]+)\s*Ry",
        re.IGNORECASE,
    )
    matches2 = pattern2.findall(text)
    if matches2:
        return float(matches2[-1]) * _RY_TO_EV
    return None


def parse_fermi_energy_eV(path_or_text: Path | str) -> float | None:
    """Extract Fermi energy (eV) from pw.x / nscf stdout.

    Matches lines like ``the Fermi energy is    20.7390 ev``.
    """
    text, _src = resolve_text_or_path(path_or_text)
    matches = re.findall(
        r"the Fermi energy is\s+([-\d.Ee+]+)\s*ev",
        text,
        flags=re.IGNORECASE,
    )
    if matches:
        return float(matches[-1])
    return None


def parse_pw_output(
    path_or_text: Path | str,
    *,
    quality_tag: str = "screening",
    extra_raw: dict[str, Any] | None = None,
) -> SCFResult:
    """Parse a pw.x output file (or raw text) into :class:`SCFResult`."""
    text, source_name = resolve_text_or_path(path_or_text)

    energy = parse_pw_energy_from_text(text)
    job_done = "JOB DONE" in text.upper() or "job done" in text.lower()
    status = "ok" if energy is not None else ("failed" if not job_done else "ok")

    # Metallic indicator: smearing / Fermi energy present
    is_metallic = bool(
        re.search(r"the Fermi energy is", text, re.IGNORECASE)
        or re.search(r"occupations\s*=\s*['\"]?smearing", text, re.IGNORECASE)
    )

    raw: dict[str, Any] = {"source": source_name, "job_done": job_done}
    if extra_raw:
        raw.update(extra_raw)

    # Attempt pymatgen PWOutput if file path (never Path(log).is_file)
    try:
        if source_name != "<string>" and len(source_name) <= _MAX_SAFE_PATH_LEN:
            p_src = Path(source_name)
            if p_src.is_file():
                from pymatgen.io.pwscf import PWOutput

                pwo = PWOutput(source_name)
                if energy is None and getattr(pwo, "final_energy", None) is not None:
                    # pymatgen may return Ry or eV depending on version — store raw too
                    raw["pymatgen_final_energy"] = pwo.final_energy
                    energy = float(pwo.final_energy)
                    # Heuristic: if |E| < 50, likely already eV for tiny cells; NbN ~ hundreds Ry
                    # Leave as-is; user can inspect raw.
    except Exception as exc:  # noqa: BLE001
        raw["pymatgen_parse_error"] = str(exc)

    return SCFResult(
        total_energy_eV=energy,
        energy_above_hull_eV_per_atom=None,
        band_gap_eV=0.0 if is_metallic else None,
        is_metallic=is_metallic if energy is not None else None,
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="qe_parser.pw",
            software={"siscforge": __version__},
            notes=f"parsed from {source_name}",
        ),
    )


_FREQ_LINE = re.compile(
    r"(?:freq\s*\(|omega\s*\(|frequency\s*=)\s*[^\d\-]*"
    r"([-\d.]+)\s*(?:\[cm-1\]|cm-1|/cm|\[cm\^-1\])?",
    re.IGNORECASE,
)
# phonopy band.yaml style: frequency: 1.234  (often THz)
_PHONOPY_FREQ = re.compile(r"^\s*-\s*frequency:\s*([-\d.eE+]+)\s*$", re.MULTILINE)
# Simple list: "frequencies:" then numbers
_NUMBER = re.compile(r"([-\d.]+)\s*(?:i)?")


def _thz_to_cm1(thz: float) -> float:
    return thz * 33.35641


def parse_frequencies_from_text(text: str) -> list[float]:
    """Best-effort extraction of phonon frequencies in cm⁻¹ from various dumps.

    Handles:
    - QE ≥ 7: ``freq ( 1) = -7.45 [THz] = -248.56 [cm-1]``
    - QE 6.x: ``freq ( 1) = 248.56 [cm-1]`` or ``... = 12.3 i [cm-1]``
    - phonopy YAML frequencies in THz
    """
    freqs: list[float] = []

    # Prefer cm⁻¹ from dual-unit QE lines (THz then cm-1)
    for m in re.finditer(
        r"freq\s*\(\s*\d+\s*\)\s*=\s*[-\d.]+\s*\[THz\]\s*=\s*([-\d.]+)\s*(i)?\s*\[cm-1\]",
        text,
        re.IGNORECASE,
    ):
        val = float(m.group(1))
        if m.group(2):
            val = -abs(val)
        freqs.append(val)
    if freqs:
        return freqs

    # Single-unit QE lines: freq ( N) = value [cm-1]  or  value i [cm-1]
    for m in re.finditer(
        r"freq\s*\(\s*\d+\s*\)\s*=\s*([-\d.]+)\s*(i)?\s*\[cm-1\]",
        text,
        re.IGNORECASE,
    ):
        val = float(m.group(1))
        if m.group(2):
            val = -abs(val)
        freqs.append(val)
    if freqs:
        return freqs

    # omega( 1) = 123.4 [cm-1]  (optional imaginary); dual-unit first
    for m in re.finditer(
        r"omega\s*\(\s*\d+\s*\)\s*=\s*[-\d.]+\s*\[THz\]\s*=\s*([-\d.]+)\s*(i)?\s*\[cm-1\]",
        text,
        re.IGNORECASE,
    ):
        val = float(m.group(1))
        if m.group(2):
            val = -abs(val)
        freqs.append(val)
    if freqs:
        return freqs

    for m in re.finditer(
        r"omega\s*\(\s*\d+\s*\)\s*=\s*([-\d.]+)\s*(i)?\s*\[cm-1\]",
        text,
        re.IGNORECASE,
    ):
        val = float(m.group(1))
        if m.group(2):
            val = -abs(val)
        freqs.append(val)
    if freqs:
        return freqs

    # phonopy band.yaml / mesh.yaml frequencies in THz
    ph_matches = _PHONOPY_FREQ.findall(text)
    if ph_matches:
        return [_thz_to_cm1(float(x)) for x in ph_matches]

    # Explicit "frequencies_cm1:" JSON-ish list
    m = re.search(r"frequencies_cm1\s*[:=]\s*\[([^\]]+)\]", text, re.IGNORECASE)
    if m:
        return [float(x) for x in re.findall(r"[-\d.eE+]+", m.group(1))]

    return freqs


def parse_ph_output(
    path_or_text: Path | str,
    *,
    quality_tag: str = "screening",
    imag_threshold_cm1: float = DEFAULT_IMAG_THRESHOLD_CM1,
    extra_raw: dict[str, Any] | None = None,
) -> PhononResult:
    """Parse ph.x / matdyn / phonopy text into :class:`PhononResult`.

    Accepts a path **or** raw log text. Log blobs must never be treated as
    paths (see :func:`resolve_text_or_path`).
    """
    text, source_name = resolve_text_or_path(path_or_text)

    freqs = parse_frequencies_from_text(text)
    summary = summarize_frequencies(freqs, imag_threshold_cm1=imag_threshold_cm1)
    job_done = "JOB DONE" in text.upper() or bool(freqs)
    status = "ok" if freqs else "failed"

    raw: dict[str, Any] = {
        "source": source_name,
        "job_done": job_done,
        "frequencies_cm1": freqs,
        "n_imaginary": summary["n_imaginary"],
        "imag_threshold_cm1": imag_threshold_cm1,
    }
    if extra_raw:
        raw.update(extra_raw)

    return PhononResult(
        min_frequency_cm1=summary["min_frequency_cm1"],
        max_frequency_cm1=summary["max_frequency_cm1"],
        n_modes=summary["n_modes"] or None,
        has_imaginary_modes=summary["has_imaginary_modes"],
        dynamically_stable=summary["dynamically_stable"],
        status=status,
        quality_tag=quality_tag,  # type: ignore[arg-type]
        raw=raw,
        provenance=Provenance(
            source="qe_parser.ph",
            software={"siscforge": __version__},
            notes=f"parsed from {source_name}",
        ),
    )


def parse_frequency_list(
    frequencies_cm1: list[float],
    *,
    quality_tag: str = "screening",
    imag_threshold_cm1: float = DEFAULT_IMAG_THRESHOLD_CM1,
) -> PhononResult:
    """Build a :class:`PhononResult` from an explicit frequency list (cm⁻¹)."""
    summary = summarize_frequencies(
        frequencies_cm1, imag_threshold_cm1=imag_threshold_cm1
    )
    return PhononResult(
        min_frequency_cm1=summary["min_frequency_cm1"],
        max_frequency_cm1=summary["max_frequency_cm1"],
        n_modes=summary["n_modes"] or None,
        has_imaginary_modes=summary["has_imaginary_modes"],
        dynamically_stable=summary["dynamically_stable"],
        status="ok" if frequencies_cm1 else "failed",
        quality_tag=quality_tag,  # type: ignore[arg-type]
        raw={"frequencies_cm1": list(frequencies_cm1)},
        provenance=Provenance(
            source="qe_parser.frequency_list",
            software={"siscforge": __version__},
        ),
    )
