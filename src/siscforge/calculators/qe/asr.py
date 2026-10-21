"""Crystal acoustic sum rule for a Quantum ESPRESSO ``ph.x`` Gamma ``.dyn`` file.

The ``.dyn`` block stores the dynamical matrix in Cartesian axes **without**
mass weighting (Ry / bohr²). Masses in the file are already Rydberg mass
units (amu × ~911.444). Frequencies are

    ω = sign(λ) √|λ| × RY_TO_CMM1

where λ are eigenvalues of ``D_ij = C_ij / √(m_i m_j)``.

Crystal ASR (``dynmat.x`` ``asr='crystal'``) is the minimal-Frobenius
projection that puts uniform Cartesian translations in the kernel of the
force constants, then mass-weights::

    P = I − Σ_x u_x u_xᵀ
    C' = P C P

``u_x`` is the normalized rigid translation along Cartesian direction x
(the same displacement on every atom, not the mass-weighted acoustic
vector). Projecting ``C`` rather than ``D`` is the force-constant sum rule:
a uniform translation must produce no force. The three acoustic eigenvalues
of ``D`` are then zero; optical eigenvalues move only as required by that
projection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Rydberg constant in cm⁻¹. √(Ry / bohr² / m_Ry) → cm⁻¹ when ħ = 1.
RY_TO_CMM1 = 109737.31568

# |q| below this (crystal units, as written in the dyn file) is Gamma.
_GAMMA_Q_TOL = 1e-6

_SPECIES_RE = re.compile(
    r"^\s*(\d+)\s+['\"]([^'\"]*)['\"]\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)\s*$"
)
_Q_RE = re.compile(
    r"q\s*=\s*\(\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_DYN_SUFFIX_RE = re.compile(r"\.dyn(\d+)$", re.IGNORECASE)


@dataclass
class QEDynMatrix:
    """One q-point from a QE ``.dyn`` file."""

    nat: int
    ntyp: int
    ibrav: int
    masses_ry: np.ndarray
    species: list[tuple[str, float]]
    q: tuple[float, float, float]
    is_gamma: bool
    force_constants: np.ndarray
    path: str | None = None


def _floats(line: str) -> list[float]:
    cleaned = line.replace("D", "E").replace("d", "e")
    return [float(tok) for tok in cleaned.split()]


def _skip_blank(lines: list[str], idx: int) -> int:
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return idx


def _is_matrix_section(line: str) -> bool:
    """True for the Cartesian matrix title, not the ``Dynamical matrix file`` banner."""
    lowered = line.lower()
    if "dynamical" not in lowered or "matrix" not in lowered:
        return False
    return "cartesian" in lowered or "axes" in lowered


def _header_index(lines: list[str]) -> int:
    """Index of ``ntyp nat ibrav celldm...`` before the dynamical matrix."""
    for i, line in enumerate(lines):
        if _is_matrix_section(line):
            break
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            ntyp, nat, ibrav = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if ntyp >= 1 and nat >= 1 and abs(ibrav) <= 20:
            return i
    raise ValueError("dyn file is missing the ntyp/nat/ibrav header")


def parse_qe_dyn(path: Path | str) -> QEDynMatrix:
    """Parse one QE ``ph.x`` ``.dyn`` file (one q, Cartesian dynamical matrix).

    Accepts ``ibrav = 0`` with an optional ``Basis vectors`` block, species
    lines ``i 'Name' mass``, atom lines ``i type x y z``, then
    ``Dynamical Matrix in cartesian axes`` and ``nat × nat`` blocks of
    ``a b`` plus three rows of three complex pairs.
    """
    src = Path(path)
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"unreadable dyn file {src}: {exc}") from exc
    lines = text.splitlines()
    if not any("dynamical" in ln.lower() and "matrix" in ln.lower() for ln in lines):
        raise ValueError(f"{src} has no dynamical matrix (not a .dyn q-point)")

    idx = _header_index(lines)
    header = lines[idx].split()
    ntyp, nat, ibrav = int(header[0]), int(header[1]), int(header[2])
    idx += 1
    idx = _skip_blank(lines, idx)
    if ibrav == 0:
        if idx < len(lines) and "basis" in lines[idx].lower():
            idx += 1
            idx = _skip_blank(lines, idx)
        for _ in range(3):
            if idx >= len(lines):
                raise ValueError(f"{src}: truncated basis vectors")
            nums = _floats(lines[idx])
            if len(nums) < 3:
                raise ValueError(f"{src}: expected a basis vector, got {lines[idx]!r}")
            idx += 1
        idx = _skip_blank(lines, idx)

    species_by_id: dict[int, tuple[str, float]] = {}
    for _ in range(ntyp):
        idx = _skip_blank(lines, idx)
        if idx >= len(lines):
            raise ValueError(f"{src}: truncated species list")
        match = _SPECIES_RE.match(lines[idx])
        if match is None:
            raise ValueError(f"{src}: expected species line, got {lines[idx]!r}")
        species_by_id[int(match.group(1))] = (match.group(2).strip(), float(match.group(3)))
        idx += 1

    atom_types: list[int] = []
    for _ in range(nat):
        idx = _skip_blank(lines, idx)
        if idx >= len(lines):
            raise ValueError(f"{src}: truncated atom list")
        parts = lines[idx].split()
        if len(parts) < 5:
            raise ValueError(f"{src}: expected atom line, got {lines[idx]!r}")
        try:
            atom_types.append(int(parts[1]))
            float(parts[2])
            float(parts[3])
            float(parts[4])
        except ValueError as exc:
            raise ValueError(f"{src}: expected atom line, got {lines[idx]!r}") from exc
        idx += 1
    try:
        masses = np.array([species_by_id[t][1] for t in atom_types], dtype=float)
    except KeyError as exc:
        raise ValueError(f"{src}: atom type {exc} is not in the species list") from exc

    while idx < len(lines) and not _is_matrix_section(lines[idx]):
        idx += 1
    if idx >= len(lines):
        raise ValueError(f"{src}: dynamical matrix header not found")
    idx += 1
    qmatch = None
    while idx < len(lines):
        qmatch = _Q_RE.search(lines[idx])
        if qmatch is not None:
            break
        idx += 1
    if qmatch is None:
        raise ValueError(f"{src}: q-vector not found")
    q = (
        float(qmatch.group(1).replace("D", "E").replace("d", "e")),
        float(qmatch.group(2).replace("D", "E").replace("d", "e")),
        float(qmatch.group(3).replace("D", "E").replace("d", "e")),
    )
    idx += 1

    n3 = 3 * nat
    force = np.zeros((n3, n3), dtype=complex)
    for _block in range(nat * nat):
        idx = _skip_blank(lines, idx)
        if idx >= len(lines):
            raise ValueError(f"{src}: truncated dynamical matrix")
        parts = lines[idx].split()
        if len(parts) < 2:
            raise ValueError(f"{src}: expected atom-pair index, got {lines[idx]!r}")
        try:
            na_i, nb_i = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError(f"{src}: expected atom-pair index, got {lines[idx]!r}") from exc
        if not (1 <= na_i <= nat and 1 <= nb_i <= nat):
            raise ValueError(f"{src}: atom index out of range in {lines[idx]!r}")
        idx += 1
        for i_cart in range(3):
            if idx >= len(lines):
                raise ValueError(f"{src}: truncated dynamical matrix block")
            nums = _floats(lines[idx])
            if len(nums) < 6:
                raise ValueError(
                    f"{src}: expected 3 complex pairs, got {lines[idx]!r}"
                )
            for j_cart in range(3):
                force[3 * (na_i - 1) + i_cart, 3 * (nb_i - 1) + j_cart] = complex(
                    nums[2 * j_cart], nums[2 * j_cart + 1]
                )
            idx += 1

    species = [(species_by_id[i][0], species_by_id[i][1]) for i in sorted(species_by_id)]
    return QEDynMatrix(
        nat=nat,
        ntyp=ntyp,
        ibrav=ibrav,
        masses_ry=masses,
        species=species,
        q=q,
        is_gamma=all(abs(component) < _GAMMA_Q_TOL for component in q),
        force_constants=force,
        path=str(src),
    )


def hermitian_symmetrize(force_constants: np.ndarray) -> np.ndarray:
    """Return ``(C + C†) / 2``."""
    matrix = np.asarray(force_constants)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("force-constant matrix must be square")
    if matrix.shape[0] % 3 != 0 or matrix.shape[0] < 3:
        raise ValueError("force-constant matrix dimension must be 3 N_at")
    return 0.5 * (matrix + matrix.conj().T)


def crystal_asr_matrix(force_constants: np.ndarray) -> np.ndarray:
    """Project uniform translations out of *force_constants*: ``C' = P C P``.

    ``P = I − Σ_x u_x u_xᵀ`` with ``u_x`` the normalized rigid translation
    along Cartesian x. *force_constants* is Hermitian-symmetrized first.
    """
    matrix = hermitian_symmetrize(force_constants)
    n3 = matrix.shape[0]
    nat = n3 // 3
    translations = np.zeros((3, n3), dtype=float)
    amplitude = 1.0 / np.sqrt(nat)
    for direction in range(3):
        for atom in range(nat):
            translations[direction, 3 * atom + direction] = amplitude
    projector = np.eye(n3) - (translations.T @ translations)
    projected = projector @ matrix @ projector
    return 0.5 * (projected + projected.conj().T)


def _mass_per_dof(masses_ry: np.ndarray, n3: int) -> np.ndarray:
    masses = np.asarray(masses_ry, dtype=float).reshape(-1)
    if masses.size == n3 // 3:
        per_dof = np.repeat(masses, 3)
    elif masses.size == n3:
        per_dof = masses
    else:
        raise ValueError(
            f"expected {n3 // 3} atomic masses or {n3} cartesian masses, got {masses.size}"
        )
    if np.any(per_dof <= 0):
        raise ValueError("masses must be positive")
    return per_dof


def frequencies_cm1(force_constants: np.ndarray, masses_ry: np.ndarray) -> list[float]:
    """Sorted phonon frequencies (cm⁻¹) of a Hermitian force-constant matrix.

    Does **not** apply ASR. Eigenvalues λ of the mass-weighted matrix use
    the sign-magnitude square root so imaginary modes stay negative.
    """
    matrix = hermitian_symmetrize(force_constants)
    per_dof = _mass_per_dof(masses_ry, matrix.shape[0])
    scale = np.sqrt(per_dof[:, None] * per_dof[None, :])
    dynamical = matrix / scale
    dynamical = 0.5 * (dynamical + dynamical.conj().T)
    eigenvalues = np.linalg.eigvalsh(dynamical).real
    freqs = np.sign(eigenvalues) * np.sqrt(np.abs(eigenvalues)) * RY_TO_CMM1
    freqs.sort()
    return [float(value) for value in freqs]


def gamma_asr_frequencies(
    force_constants: np.ndarray,
    masses_ry: np.ndarray,
) -> tuple[list[float], list[float]]:
    """Return ``(raw, crystal-ASR)`` frequencies in cm⁻¹, both sorted ascending.

    ASR is the force-constant projection :func:`crystal_asr_matrix`. Callers
    must pass a Gamma matrix; finite-q matrices are not sum-rule projected.
    """
    raw = frequencies_cm1(force_constants, masses_ry)
    corrected = frequencies_cm1(crystal_asr_matrix(force_constants), masses_ry)
    return raw, corrected


def gamma_asr_from_dyn(path: Path | str) -> tuple[list[float], list[float]]:
    """Raw and crystal-ASR frequencies for a Gamma ``.dyn`` file.

    Raises ``ValueError`` when the file is not q = 0.
    """
    parsed = parse_qe_dyn(path)
    if not parsed.is_gamma:
        raise ValueError(f"{path} is not a Gamma dynamical matrix (q={parsed.q})")
    return gamma_asr_frequencies(parsed.force_constants, parsed.masses_ry)


def discover_dyn_files(path: Path | str) -> list[Path]:
    """Return ``*.dyn1`` … ``*.dynN`` under a ph directory or qe work dir.

    ``*.dyn0`` is the q-mesh list, not a matrix, and is skipped. Looks in
    *path* itself, ``02_scf/``, ``ph/``, ``phonon/``, and one level of
    ``<child>/02_scf`` (the calculator layout).
    """
    root = Path(path)
    if root.is_file():
        match = _DYN_SUFFIX_RE.search(root.name)
        if match is not None and int(match.group(1)) != 0:
            return [root]
        return []
    if not root.is_dir():
        return []

    search_dirs = [root]
    for name in ("02_scf", "ph", "phonon"):
        sub = root / name
        if sub.is_dir():
            search_dirs.append(sub)
    try:
        children = list(root.iterdir())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir():
            continue
        nested = child / "02_scf"
        if nested.is_dir():
            search_dirs.append(nested)

    found: list[Path] = []
    seen: set[Path] = set()

    def _dyn_index(item: Path) -> tuple[int, str]:
        match = _DYN_SUFFIX_RE.search(item.name)
        number = int(match.group(1)) if match is not None else 0
        return number, item.name

    for directory in search_dirs:
        try:
            entries = list(directory.glob("*.dyn*"))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            match = _DYN_SUFFIX_RE.search(entry.name)
            if match is None or int(match.group(1)) == 0:
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(entry)
    found.sort(key=_dyn_index)
    return found


def find_gamma_dyn(path: Path | str) -> Path | None:
    """First Gamma ``.dyn`` file at *path* (file or directory), if any."""
    root = Path(path)
    candidates = [root] if root.is_file() else discover_dyn_files(root)
    for candidate in candidates:
        try:
            parsed = parse_qe_dyn(candidate)
        except (OSError, ValueError):
            continue
        if parsed.is_gamma:
            return candidate
    return None
