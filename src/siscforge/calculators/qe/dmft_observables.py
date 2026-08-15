"""Best-effort native solid_dmft observables extractors (issue #35).

Discovery order (first *usable* source wins):

1. JSON drop-ins (unchanged): ``observables.json``,
   ``observables_imp0.json``, ``siscforge_dmft_observables.json``
2. solid_dmft-style text tables: ``observables_imp0.dat`` and close
   filename variants (including ``out/`` / jobname subdirs)
3. HDF5 archive under common ``DMFT_results`` keys — **soft** on
   ``h5py`` / TRIQS; missing extras skip this source cleanly

Produces the same metrics dict :func:`parse_dmft_observables` already
understands. Never a hard dependency on TRIQS, solid_dmft, or h5py.
Writes stay under the caller-supplied ``dmft/`` workdir.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

JSON_CANDIDATES: tuple[str, ...] = (
    "observables.json",
    "observables_imp0.json",
    "siscforge_dmft_observables.json",
)

# Prefer the canonical impurity-0 table; then numbered / spin-split variants.
_DAT_NAME_RE = re.compile(
    r"^observables(?:_imp(?P<imp>\d+)(?:_(?P<spin>up|down))?)?\.dat$",
    re.IGNORECASE,
)
_DAT_LOOSE_RE = re.compile(
    r"^(?:obs(?:ervables)?)_?imp(?P<imp>\d+)(?:_(?P<spin>up|down))?\.dat$",
    re.IGNORECASE,
)

_JOBNAME_SUBDIRS: tuple[str, ...] = ("out", "dmft", "results")

# h5py / TRIQS HDFArchive group names we walk first.
_H5_ROOTS: tuple[str, ...] = (
    "DMFT_results",
    "dmft_results",
    "DMFT_output",
    "Observables",
)

_OCC_KEYS: tuple[str, ...] = (
    "imp_occ",
    "impurity_occ",
    "impurity_occupation",
    "n_imp",
    "occupancy",
    "occupancies",
    "n_tot",
    "density",
    "filling",
)
_ORB_OCC_KEYS: tuple[str, ...] = ("orb_occ", "orbital_occ", "orbital_occs")
_Z_KEYS: tuple[str, ...] = (
    "orb_Z",
    "Z",
    "z",
    "quasi_particle_weight",
    "quasiparticle_weight",
)
_MASS_KEYS: tuple[str, ...] = ("mass_enhancement", "mstar", "m*/m", "mstar_over_m")
_CONV_KEYS: tuple[str, ...] = ("converged", "success", "job_done")

_SPIN_KEYS = frozenset({"up", "down", "ud", "tot", "total"})


def empty_metrics() -> dict[str, Any]:
    """Canonical empty metrics dict (same keys as ``parse_dmft_observables``)."""
    return {
        "occupancy_summary": {},
        "filling": None,
        "mass_enhancement": None,
        "mass_enhancement_by_orbital": {},
        "converged": False,
        "leading_pairing_eigenvalue": None,
        "pairing_symmetry": None,
    }


def metrics_usable(metrics: dict[str, Any] | None) -> bool:
    """True when occupancy or filling is present — enough for a DMFTResult."""
    if not metrics:
        return False
    occ = metrics.get("occupancy_summary") or {}
    return bool(occ) or metrics.get("filling") is not None


def h5py_available() -> bool:
    """True when the optional ``h5py`` extra can be imported."""
    try:
        import h5py  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# JSON (drop-in) — thin wrapper around the existing parser
# ---------------------------------------------------------------------------


def find_json_observables(work_dir: Path) -> Path | None:
    """Return the first JSON drop-in in *work_dir* (no recursion)."""
    for name in JSON_CANDIDATES:
        cand = work_dir / name
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# solid_dmft-style .dat tables
# ---------------------------------------------------------------------------


def _dat_identity(path: Path) -> tuple[int, str | None] | None:
    """Return ``(imp_index, spin_or_None)`` or None if the name is not a table."""
    name = path.name
    for cre in (_DAT_NAME_RE, _DAT_LOOSE_RE):
        m = cre.match(name)
        if m:
            imp_s = m.groupdict().get("imp")
            spin = m.groupdict().get("spin")
            imp = int(imp_s) if imp_s is not None else 0
            return imp, (spin.lower() if spin else None)
    return None


def find_dat_observables(work_dir: Path) -> list[Path]:
    """Locate solid_dmft-style ``observables_imp*.dat`` tables under *work_dir*.

    Search order: *work_dir* itself, then common jobname subdirs (``out/``),
    then other immediate subdirectories. Does not walk outside *work_dir*.
    """
    roots: list[Path] = [work_dir]
    if work_dir.is_dir():
        for name in _JOBNAME_SUBDIRS:
            child = work_dir / name
            if child.is_dir():
                roots.append(child)
        for child in sorted(work_dir.iterdir()):
            if child.is_dir() and child not in roots:
                roots.append(child)

    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for cand in sorted(entries):
            if not cand.is_file():
                continue
            if _dat_identity(cand) is None:
                continue
            key = cand.resolve() if cand.exists() else cand
            if key in seen:
                continue
            seen.add(key)
            found.append(cand)
    # Prefer impurity 0, then non-spin-split, then lower names.
    found.sort(
        key=lambda p: (
            (_dat_identity(p) or (99, "z"))[0],
            1 if (_dat_identity(p) or (0, None))[1] else 0,
            p.name,
        )
    )
    return found


def _floats(cell: str) -> list[float]:
    out: list[float] = []
    for tok in cell.replace(",", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _split_pipe(line: str) -> list[str]:
    return [p.strip() for p in line.split("|")]


def _header_kind(cell: str) -> str:
    blob = cell.lower()
    blob = blob.replace("(beta/2)", "beta2")
    if "impurity occ" in blob or "imp_occ" in blob or "imp occ" in blob:
        return "imp_occ"
    if "orbital occ" in blob or "orb_occ" in blob:
        return "orb_occ"
    if "g(beta2" in blob or "gb2" in blob or "g beta" in blob:
        return "gb2"
    if blob.strip() in {"z", "orb_z"} or "quasi" in blob:
        return "z"
    if "mass" in blob or "m*/m" in blob or "mstar" in blob:
        return "mass"
    if blob.strip() in {"it", "iter", "iteration"}:
        return "it"
    if blob.strip() in {"mu", "μ"}:
        return "mu"
    if "e_tot" in blob or "e_dft" in blob or "e_dc" in blob:
        return "energy"
    if "pair" in blob or "lambda" in blob:
        return "pair"
    if "converged" in blob:
        return "converged"
    return "other"


def parse_dmft_dat_text(text: str, *, label: str = "imp0") -> dict[str, Any]:
    """Parse a solid_dmft ``observables_imp*.dat`` body into a metrics dict.

    Uses the last data row (final DMFT iteration). Unknown layouts return
    an empty metrics dict (never raise). Last-iteration occupancy from a
    written table is treated as converged unless the table says otherwise.
    """
    out = empty_metrics()
    if not text or not str(text).strip():
        return out

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return out

    header_idx = None
    kinds: list[str] = []
    for i, ln in enumerate(lines):
        if "|" not in ln:
            continue
        cells = _split_pipe(ln)
        joined = " ".join(cells).lower()
        if "impurity occ" in joined or "imp_occ" in joined or "orbital occ" in joined:
            header_idx = i
            kinds = [_header_kind(c) for c in cells]
            break
        if cells and cells[0].lower().strip() in {"it", "iter"}:
            header_idx = i
            kinds = [_header_kind(c) for c in cells]
            break

    data_lines = lines[header_idx + 1 :] if header_idx is not None else lines
    last: str | None = None
    for ln in data_lines:
        if ln.lstrip().startswith("#"):
            continue
        if "|" in ln or (header_idx is None and _floats(ln)):
            last = ln
    if last is None:
        return out

    cells = _split_pipe(last) if "|" in last else [last]
    if header_idx is None and "|" in last and len(cells) >= 5:
        # Positional fallback: it | mu | G(beta/2)… | orbital occs… | impurity occ
        kinds = ["it", "mu", "gb2", "orb_occ", "imp_occ"] + ["energy"] * max(
            0, len(cells) - 5
        )

    if not kinds:
        # Last-resort: last float on the line is impurity occupancy.
        vals = _floats(last)
        if vals:
            filling = float(vals[-1])
            out["filling"] = filling
            out["occupancy_summary"] = {label: filling}
            out["converged"] = True
        return out

    orb_occ: list[float] = []
    imp_occ: list[float] = []
    z_vals: list[float] = []
    mass_vals: list[float] = []
    pair_vals: list[float] = []
    explicit_conv: bool | None = None

    for kind, cell in zip(kinds, cells, strict=False):
        nums = _floats(cell)
        if kind == "imp_occ":
            imp_occ.extend(nums)
        elif kind == "orb_occ":
            orb_occ.extend(nums)
        elif kind == "z":
            z_vals.extend(nums)
        elif kind == "mass":
            mass_vals.extend(nums)
        elif kind == "pair":
            pair_vals.extend(nums)
        elif kind == "converged" and cell.strip():
            blob = cell.strip().lower()
            if blob in {"1", "true", "yes", "converged"}:
                explicit_conv = True
            elif blob in {"0", "false", "no", "unconverged", "not_converged"}:
                explicit_conv = False

    occ: dict[str, float] = {}
    if orb_occ:
        for i, v in enumerate(orb_occ):
            occ[f"{label}_orb{i}"] = float(v)
    if imp_occ:
        filling = float(sum(imp_occ))
        occ[label] = filling
        out["filling"] = filling
    elif orb_occ:
        filling = float(sum(orb_occ))
        occ[label] = filling
        out["filling"] = filling
    out["occupancy_summary"] = occ

    if mass_vals:
        out["mass_enhancement"] = float(sum(mass_vals) / len(mass_vals))
        if len(mass_vals) > 1:
            out["mass_enhancement_by_orbital"] = {
                f"{label}_orb{i}": float(v) for i, v in enumerate(mass_vals)
            }
    elif z_vals:
        masses: dict[str, float] = {}
        for i, z in enumerate(z_vals):
            if float(z) != 0.0:
                masses[f"{label}_orb{i}"] = float(1.0 / float(z))
        if masses:
            out["mass_enhancement_by_orbital"] = masses
            out["mass_enhancement"] = float(sum(masses.values()) / len(masses))

    if pair_vals:
        out["leading_pairing_eigenvalue"] = float(pair_vals[-1])

    if explicit_conv is not None:
        out["converged"] = explicit_conv
    elif metrics_usable(out):
        # Last iteration of a written solid_dmft table — treat as finished.
        out["converged"] = True
    return out


def parse_dmft_dat(source: str | Path) -> dict[str, Any]:
    """Read a ``.dat`` path and return a metrics dict. Never raises."""
    path = Path(source)
    ident = _dat_identity(path)
    if ident is not None:
        imp, spin = ident
        label = f"imp{imp}" + (f"_{spin}" if spin else "")
    else:
        label = "imp0"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty_metrics()
    return parse_dmft_dat_text(text, label=label)


def parse_dat_group(paths: list[Path]) -> dict[str, Any]:
    """Parse one impurity's table(s), summing spin-split files when needed."""
    if not paths:
        return empty_metrics()

    # Prefer a non-spin-split file when both exist for the same impurity.
    unsplit = [p for p in paths if (_dat_identity(p) or (0, None))[1] is None]
    if unsplit:
        return parse_dmft_dat(unsplit[0])

    merged = empty_metrics()
    occ: dict[str, float] = {}
    filling = 0.0
    have_fill = False
    masses: list[float] = []
    mass_orb: dict[str, float] = {}
    any_conv = False
    for p in paths:
        part = parse_dmft_dat(p)
        for k, v in (part.get("occupancy_summary") or {}).items():
            occ[str(k)] = float(v)
        if part.get("filling") is not None:
            filling += float(part["filling"])
            have_fill = True
        if part.get("mass_enhancement") is not None:
            masses.append(float(part["mass_enhancement"]))
        for k, v in (part.get("mass_enhancement_by_orbital") or {}).items():
            mass_orb[str(k)] = float(v)
        any_conv = any_conv or bool(part.get("converged"))
        if part.get("leading_pairing_eigenvalue") is not None:
            merged["leading_pairing_eigenvalue"] = part["leading_pairing_eigenvalue"]
        if part.get("pairing_symmetry"):
            merged["pairing_symmetry"] = part["pairing_symmetry"]
    merged["occupancy_summary"] = occ
    if have_fill:
        merged["filling"] = filling
    elif occ:
        # Spin-split orbital keys only — sum them.
        merged["filling"] = float(sum(occ.values()))
    if masses:
        merged["mass_enhancement"] = float(sum(masses) / len(masses))
    if mass_orb:
        merged["mass_enhancement_by_orbital"] = mass_orb
    merged["converged"] = any_conv or metrics_usable(merged)
    return merged


# ---------------------------------------------------------------------------
# HDF5 (soft h5py)
# ---------------------------------------------------------------------------


def _is_mapping(obj: Any) -> bool:
    if isinstance(obj, (str, bytes)):
        return False
    if isinstance(obj, Mapping):
        return True
    # h5py Group / File: keys + getitem, but not a Dataset (those have dtype).
    if hasattr(obj, "keys") and hasattr(obj, "__getitem__") and not hasattr(obj, "dtype"):
        return True
    return False


def _mapping_keys(obj: Any) -> list[str]:
    try:
        return [str(k) for k in obj.keys()]
    except Exception:  # noqa: BLE001 — h5py / odd mappings
        return []


def _mapping_get(obj: Any, key: str) -> Any:
    if _is_mapping(obj):
        if key in obj:
            try:
                return obj[key]
            except Exception:  # noqa: BLE001
                return None
        for k in _mapping_keys(obj):
            if k == key or k.lower() == key.lower():
                try:
                    return obj[k]
                except Exception:  # noqa: BLE001
                    return None
    return None


def _dataset_value(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bytes)):
        return None
    if isinstance(obj, (bool, int, float)):
        return obj
    if hasattr(obj, "dtype") and hasattr(obj, "shape"):
        try:
            return obj[()]
        except Exception:  # noqa: BLE001
            try:
                return list(obj)
            except Exception:  # noqa: BLE001
                return None
    if isinstance(obj, (list, tuple)):
        return list(obj)
    try:
        import numpy as np

        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
    except ImportError:
        pass
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return float(value.item())
    except ImportError:
        pass
    return None


def _flatten_last_numeric(obj: Any, *, depth: int = 0) -> list[float]:
    """Peel last-iteration / spin / orbital nesting down to floats."""
    if depth > 8:
        return []
    leaf = _dataset_value(obj)
    if leaf is None and _is_mapping(obj):
        keys = _mapping_keys(obj)
        # Prefer last_iter / last / data, then spin blocks, then first child.
        for pref in ("last_iter", "last", "data", "value", "imp0", "0"):
            child = _mapping_get(obj, pref)
            if child is not None:
                return _flatten_last_numeric(child, depth=depth + 1)
        spin_keys = [k for k in keys if k.lower() in _SPIN_KEYS]
        if spin_keys:
            out: list[float] = []
            for sk in spin_keys:
                out.extend(_flatten_last_numeric(_mapping_get(obj, sk), depth=depth + 1))
            return out
        # Numeric-looking keys (impurity index / iteration groups).
        if keys:
            try:
                last_key = sorted(keys, key=lambda k: (len(k), k))[-1]
            except Exception:  # noqa: BLE001
                last_key = keys[-1]
            return _flatten_last_numeric(_mapping_get(obj, last_key), depth=depth + 1)
        return []

    if leaf is None:
        return []
    if isinstance(leaf, (int, float)) and not isinstance(leaf, bool):
        return [float(leaf)]
    if isinstance(leaf, (list, tuple)):
        if not leaf:
            return []
        last = leaf[-1]
        if isinstance(last, (list, tuple)):
            out = []
            for item in last:
                fv = _as_float(item)
                if fv is not None:
                    out.append(fv)
            if out:
                return out
            return _flatten_last_numeric(last, depth=depth + 1)
        fv = _as_float(last)
        if fv is not None:
            # A 1-d list of per-iteration scalars — last value only.
            # If *all* entries look like an orbital vector of one iteration
            # (short list, no nested lists) we cannot distinguish from a
            # history of scalars. Prefer last scalar; callers that want
            # per-orbital use a 2-d last row above.
            return [fv]
        return _flatten_last_numeric(last, depth=depth + 1)
    fv = _as_float(leaf)
    return [fv] if fv is not None else []


def _find_named(tree: Any, names: tuple[str, ...], *, limit: int = 40) -> Any:
    """Breadth-first search for the first key in *names*."""
    wanted = {n.lower() for n in names}
    if not _is_mapping(tree):
        return None
    queue: list[Any] = [tree]
    seen = 0
    while queue and seen < limit:
        node = queue.pop(0)
        seen += 1
        if not _is_mapping(node):
            continue
        for k in _mapping_keys(node):
            if k.lower() in wanted:
                return _mapping_get(node, k)
        for k in _mapping_keys(node):
            child = _mapping_get(node, k)
            if _is_mapping(child):
                queue.append(child)
    return None


def metrics_from_h5_tree(tree: Any) -> dict[str, Any]:
    """Extract a metrics dict from an h5py File / Group or a nested mapping.

    Best-effort over common solid_dmft layouts:

    * ``DMFT_results/observables/{imp_occ,orb_Z,orb_occ}``
    * ``DMFT_results/last_iter`` (occupancy / Z aliases)
    * flat datasets named like the keys above
    """
    out = empty_metrics()
    if tree is None:
        return out

    dmft_root = None
    if _is_mapping(tree):
        for name in _H5_ROOTS:
            child = _mapping_get(tree, name)
            if child is not None:
                dmft_root = child
                break
    has_top_obs = _is_mapping(tree) and _mapping_get(tree, "observables") is not None
    if dmft_root is None and not has_top_obs:
        # Wannier / DFTTools seed archives are often the same filename
        # ({seed}.h5) and must not be treated as a DMFT result.
        return out

    root = dmft_root if dmft_root is not None else tree
    obs = _mapping_get(root, "observables") if _is_mapping(root) else None
    if obs is None:
        obs = _find_named(root, ("observables",))
    search_nodes: list[Any] = []
    for n in (obs, root):
        if n is not None:
            search_nodes.append(n)

    def _first(keys: tuple[str, ...]) -> Any:
        for node in search_nodes:
            hit = _find_named(node, keys) if _is_mapping(node) else None
            if hit is not None:
                return hit
            if _is_mapping(node):
                for k in keys:
                    direct = _mapping_get(node, k)
                    if direct is not None:
                        return direct
        return None

    imp = _first(_OCC_KEYS)
    orb = _first(_ORB_OCC_KEYS)
    z_obj = _first(_Z_KEYS)
    mass_obj = _first(_MASS_KEYS)
    conv_obj = _first(_CONV_KEYS)

    occ: dict[str, float] = {}
    imp_vals = _flatten_last_numeric(imp) if imp is not None else []
    orb_vals = _flatten_last_numeric(orb) if orb is not None else []
    if orb_vals and (len(orb_vals) > 1 or not imp_vals):
        for i, v in enumerate(orb_vals):
            occ[f"imp0_orb{i}"] = float(v)
    if imp_vals:
        filling = float(sum(imp_vals)) if len(imp_vals) > 1 else float(imp_vals[0])
        # A single last-iteration impurity occupancy is the filling; a
        # spin-resolved pair (up, down) should be summed.
        if len(imp_vals) == 2:
            filling = float(sum(imp_vals))
        occ.setdefault("imp0", filling)
        out["filling"] = filling
    elif orb_vals:
        filling = float(sum(orb_vals))
        occ.setdefault("imp0", filling)
        out["filling"] = filling
    out["occupancy_summary"] = occ

    if mass_obj is not None:
        masses = _flatten_last_numeric(mass_obj)
        if masses:
            out["mass_enhancement"] = float(sum(masses) / len(masses))
            if len(masses) > 1:
                out["mass_enhancement_by_orbital"] = {
                    f"imp0_orb{i}": float(v) for i, v in enumerate(masses)
                }
    elif z_obj is not None:
        zs = _flatten_last_numeric(z_obj)
        by_orb: dict[str, float] = {}
        for i, z in enumerate(zs):
            if float(z) != 0.0:
                by_orb[f"imp0_orb{i}"] = float(1.0 / float(z))
        if by_orb:
            out["mass_enhancement_by_orbital"] = by_orb
            out["mass_enhancement"] = float(sum(by_orb.values()) / len(by_orb))

    if conv_obj is not None:
        leaf = _dataset_value(conv_obj)
        if isinstance(leaf, bool):
            out["converged"] = leaf
        elif isinstance(leaf, (int, float)):
            out["converged"] = bool(leaf)
        elif isinstance(leaf, (list, tuple)) and leaf:
            out["converged"] = bool(leaf[-1])
        elif isinstance(leaf, str):
            out["converged"] = leaf.strip().lower() in {"1", "true", "yes", "converged"}
    elif _is_mapping(root) and (
        _mapping_get(root, "last_iter") is not None
        or _mapping_get(root, "last_iteration") is not None
    ):
        out["converged"] = metrics_usable(out)
    elif metrics_usable(out):
        out["converged"] = True

    pair = _first(("leading_pairing_eigenvalue", "lambda_pair"))
    if pair is not None:
        nums = _flatten_last_numeric(pair)
        if nums:
            out["leading_pairing_eigenvalue"] = float(nums[-1])
    sym = _first(("pairing_symmetry", "symmetry"))
    if sym is not None:
        leaf = _dataset_value(sym)
        if isinstance(leaf, bytes):
            try:
                leaf = leaf.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                leaf = None
        if isinstance(leaf, str) and leaf.strip():
            out["pairing_symmetry"] = leaf.strip()
    return out


def extract_dmft_h5(
    source: str | Path | Mapping[str, Any] | None,
    *,
    opener: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Best-effort HDF5 extract. Soft on h5py — never raises, never hard-deps.

    *source* may be a path, an in-memory mapping (tests), or None.
    *opener* is an optional ``path -> context-manager / mapping`` hook so
    tests can inject a fake reader without installing h5py.
    """
    if source is None:
        return empty_metrics()
    if _is_mapping(source) and not isinstance(source, (str, Path)):
        try:
            return metrics_from_h5_tree(source)
        except Exception:  # noqa: BLE001 — never crash the campaign
            _LOG.debug("in-memory h5 tree parse failed", exc_info=True)
            return empty_metrics()

    path = Path(source)
    if not path.is_file():
        return empty_metrics()

    handle: Any = None
    close = False
    try:
        if opener is not None:
            handle = opener(path)
            if hasattr(handle, "__enter__"):
                handle = handle.__enter__()
                close = True
        else:
            try:
                import h5py
            except ImportError:
                return empty_metrics()
            handle = h5py.File(path, "r")
            close = True
        return metrics_from_h5_tree(handle)
    except Exception:  # noqa: BLE001 — exotic layouts / missing extras
        _LOG.debug("h5 extract skipped for %s", path, exc_info=True)
        return empty_metrics()
    finally:
        if close and handle is not None:
            try:
                closer = getattr(handle, "close", None) or getattr(
                    handle, "__exit__", None
                )
                if closer is not None:
                    if closer == getattr(handle, "__exit__", None):
                        closer(None, None, None)
                    else:
                        closer()
            except Exception:  # noqa: BLE001
                pass


def find_h5_archives(work_dir: Path, *, seedname: str | None = None) -> list[Path]:
    """List ``*.h5`` files in *work_dir* (and ``out/``), seedname first.

    The Wannier / DFTTools *input* archive is often the same filename as
    the solid_dmft output (``{seed}.h5``). Callers must still check that
    ``DMFT_results`` (or occupancy keys) are actually present — this
    helper only locates candidates and never opens them.
    """
    roots = [work_dir]
    out = work_dir / "out"
    if out.is_dir():
        roots.append(out)
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            matches = sorted(root.glob("*.h5"))
        except OSError:
            continue
        for p in matches:
            try:
                key = p.resolve()
            except OSError:
                key = p
            if key in seen:
                continue
            seen.add(key)
            found.append(p)
    if seedname:
        preferred = f"{seedname}.h5"

        def _rank(p: Path) -> tuple[int, str]:
            return (0 if p.name == preferred else 1, p.name)

        found.sort(key=_rank)
    return found


# ---------------------------------------------------------------------------
# Discovery + optional JSON materialization
# ---------------------------------------------------------------------------


def metrics_to_json_payload(metrics: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Compatible ``observables.json`` body for resume / re-invoke."""
    occ = dict(metrics.get("occupancy_summary") or {})
    filling = metrics.get("filling")
    if not occ and filling is not None:
        occ = {"imp": float(filling)}
    payload: dict[str, Any] = {
        "occupancy": occ,
        "filling": filling,
        "mass_enhancement": metrics.get("mass_enhancement"),
        "mass_enhancement_by_orbital": dict(
            metrics.get("mass_enhancement_by_orbital") or {}
        ),
        "converged": bool(metrics.get("converged")),
        "source": source,
        "siscforge_bridge": "native_solid_dmft",
    }
    if metrics.get("leading_pairing_eigenvalue") is not None:
        payload["leading_pairing_eigenvalue"] = metrics["leading_pairing_eigenvalue"]
    if metrics.get("pairing_symmetry"):
        payload["pairing_symmetry"] = metrics["pairing_symmetry"]
    z_orb = metrics.get("mass_enhancement_by_orbital") or {}
    if isinstance(z_orb, dict) and z_orb:
        inv: dict[str, float] = {}
        for k, v in z_orb.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv != 0.0:
                inv[str(k)] = float(1.0 / fv)
        if inv:
            payload["Z"] = inv
    elif metrics.get("mass_enhancement"):
        try:
            m = float(metrics["mass_enhancement"])
            if m != 0.0:
                payload["Z"] = float(1.0 / m)
        except (TypeError, ValueError):
            pass
    return payload


def materialize_observables_json(
    work_dir: Path,
    metrics: dict[str, Any],
    *,
    source: str,
    dest_name: str = "observables.json",
) -> Path | None:
    """Write a compatible ``observables.json`` if one is not already present.

    Never overwrites an operator drop-in. Only writes under *work_dir*.
    """
    if not metrics_usable(metrics):
        return None
    dest = work_dir / dest_name
    if dest.is_file():
        return dest
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        payload = metrics_to_json_payload(metrics, source=source)
        dest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        _LOG.debug("could not materialize %s", dest, exc_info=True)
        return None
    return dest


def _group_dat_by_imp(paths: list[Path]) -> Iterator[list[Path]]:
    groups: dict[int, list[Path]] = {}
    for p in paths:
        ident = _dat_identity(p)
        imp = ident[0] if ident is not None else 0
        groups.setdefault(imp, []).append(p)
    for imp in sorted(groups):
        yield groups[imp]


def discover_dmft_metrics(
    work_dir: Path,
    *,
    parse_json: Callable[[str | Path | dict[str, Any]], dict[str, Any]] | None = None,
    write_json: bool = True,
    seedname: str | None = None,
    h5_opener: Callable[[Path], Any] | None = None,
) -> tuple[dict[str, Any], str | None, Path | None]:
    """Walk JSON → ``.dat`` → h5. Return ``(metrics, kind, path)``.

    *kind* is ``json``, ``dat``, ``h5``, or ``None``. Unusable sources
    (empty JSON, input-only seed h5, missing h5py) are skipped. On a
    successful native parse, optionally materializes ``observables.json``
    so resume does not need TRIQS / h5py again.
    """
    work_dir = Path(work_dir)
    if parse_json is None:
        from siscforge.calculators.qe.dmft import parse_dmft_observables

        parse_json = parse_dmft_observables

    # 1. JSON drop-ins (preferred; unchanged).
    for name in JSON_CANDIDATES:
        cand = work_dir / name
        if not cand.is_file():
            continue
        try:
            metrics = parse_json(cand)
        except Exception:  # noqa: BLE001
            metrics = empty_metrics()
        if metrics_usable(metrics):
            return metrics, "json", cand

    # 2. Native .dat tables.
    dats = find_dat_observables(work_dir)
    if dats:
        # Use the lowest impurity index (imp0); spin-split files are merged.
        for group in _group_dat_by_imp(dats):
            metrics = parse_dat_group(group)
            if metrics_usable(metrics):
                src = group[0]
                if write_json:
                    materialize_observables_json(
                        work_dir, metrics, source=str(src.name)
                    )
                return metrics, "dat", src

    # 3. HDF5 — soft.
    for h5 in find_h5_archives(work_dir, seedname=seedname):
        metrics = extract_dmft_h5(h5, opener=h5_opener)
        if metrics_usable(metrics):
            if write_json:
                materialize_observables_json(work_dir, metrics, source=str(h5.name))
            return metrics, "h5", h5

    return empty_metrics(), None, None
