"""Guided denser-q phonon pilot from an existing map store (Slice 29 / 29.3).

When a coarse q=2³ map returns zero ``dynamically_stable`` survivors, the
operator should not have to hand-write a pilot YAML or abandon the family
on a mesh-artefact suspicion. This helper:

* selects a **small** set (binaries only, least-soft N, or explicit ids)
* emits a loadable ``CampaignConfig`` / YAML with denser ``qpoints``
  (default 3³), same ``pseudo_dir`` / ``nproc``, a **new** ``output_dir``
* reuses exact ``candidate_specs`` (no full-grid re-enumeration)
* forces ``do_epw: false`` — never auto-launches EPW on soft cells
* is resume-safe (``run.resume: true``)
* uses a nitride-phonon recovery electronic k of at least 8³ (prefer 12³
  for small / rock-salt binary cells). Electronic k under-sampling was
  the dominant artefact on ZrN (k=4³ → −149; k=8³ → −72; k=12³ → −29
  Γ-noise). The pilot never lowers a denser source-campaign k.

The pilot does **not** decide physical stability. Residual |ω| ≲ 30–40
cm⁻¹ after dense k is not auto-promoted to stable or EPW. The human
still chooses whether to expand or abandon.
"""


from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

from siscforge.models.candidate import CandidateEvaluation
from siscforge.models.config import CampaignConfig, DFTConfig, RunConfig
from siscforge.shortlist import evaluation_to_spec
from siscforge.soft_modes import (
    _n_atoms,
    classify_soft_mode,
    is_binary_nitride,
)

PilotMode = Literal["binaries", "least_soft", "ids"]

DEFAULT_QPOINTS = (3, 3, 3)

# Nitride phonon recovery electronic k (Slice 29.3). Global DFTConfig.kpoints
# stays [4,4,4]; this floor is scoped to the pilot / map-recovery path.
# ZrN: k=4³ invented large finite-q imag; k=8³ healed most; k=12³ collapsed
# leftover softness to Γ-noise (~−29 cm⁻¹).
NITRIDE_PHONON_K_MIN = (8, 8, 8)
NITRIDE_PHONON_K_SMALL_BINARY = (12, 12, 12)
_SMALL_CELL_N_ATOMS = 4


def load_source_campaign(store_dir: str | Path) -> CampaignConfig | None:
    """Load ``campaign_resolved.yaml`` from a store when present."""
    path = Path(store_dir) / "campaign_resolved.yaml"
    if not path.is_file():
        return None
    return CampaignConfig.from_yaml(path)


def parse_qpoints(value: str | list[int] | tuple[int, ...] | None) -> list[int]:
    """Parse ``3,3,3`` / ``[3,3,3]`` into a 3-vector of positive ints."""
    if value is None:
        return list(DEFAULT_QPOINTS)
    if isinstance(value, (list, tuple)):
        nums = [int(x) for x in value]
    else:
        text = str(value).strip().replace("x", ",").replace("×", ",")
        parts = [p for p in text.replace(" ", "").split(",") if p]
        nums = [int(p) for p in parts]
    if len(nums) == 1:
        nums = [nums[0], nums[0], nums[0]]
    if len(nums) != 3 or any(n < 1 for n in nums):
        raise ValueError(
            f"qpoints must be three positive integers (got {value!r})"
        )
    return nums


def select_pilot_evaluations(
    evaluations: list[CandidateEvaluation],
    *,
    mode: PilotMode = "binaries",
    max_jobs: int = 4,
    candidate_ids: list[str] | None = None,
) -> list[CandidateEvaluation]:
    """Pick a small denser-q set. Never invent cells that are not in the store."""
    if max_jobs < 1:
        raise ValueError("max_jobs must be >= 1")

    if mode == "ids":
        if not candidate_ids:
            raise ValueError("mode=ids requires --id / candidate_ids")
        by_id = {e.candidate.candidate_id: e for e in evaluations}
        chosen: list[CandidateEvaluation] = []
        for cid in candidate_ids:
            if cid in by_id:
                chosen.append(by_id[cid])
                continue
            matches = [
                e
                for e in evaluations
                if e.candidate.candidate_id.startswith(cid)
                or e.candidate.candidate_id.replace("-", "").startswith(
                    cid.replace("-", "")
                )
            ]
            if matches:
                chosen.append(matches[0])
        if not chosen:
            raise ValueError(f"No evaluations matched ids={candidate_ids!r}")
        return chosen[:max_jobs]

    # Prefer completed phonon rows; keep setup-failed last.
    def _usable(ev: CandidateEvaluation) -> bool:
        return ev.phonon is not None

    pool = [e for e in evaluations if _usable(e)]
    if not pool:
        raise ValueError(
            "No phonon evaluations available for a denser-q pilot. "
            "Run a phonon-only map first."
        )

    if mode == "binaries":
        binaries = [e for e in pool if is_binary_nitride(e.candidate.formula)]
        if not binaries:
            raise ValueError(
                "mode=binaries found no binary-nitride cells in the store. "
                "Use --mode least_soft or --mode ids."
            )
        pool = binaries

    elif mode != "least_soft":
        raise ValueError(
            f"Unknown pilot mode: {mode!r}. Use binaries | least_soft | ids"
        )

    # Least-soft = highest min ω (least imaginary). Setup-failed / missing
    # frequency sort last. This is a *selection* heuristic, not a verdict.
    def _least_soft_key(ev: CandidateEvaluation) -> tuple[int, float, str]:
        ph = ev.phonon
        if ph is None or ph.min_frequency_cm1 is None:
            return (1, 0.0, ev.candidate.formula)
        return (0, -float(ph.min_frequency_cm1), ev.candidate.formula)

    ranked = sorted(pool, key=_least_soft_key)
    return ranked[:max_jobs]


def _cell_is_small_or_binary(ev: CandidateEvaluation) -> bool:
    """True for rock-salt binaries or cells with n_atoms ≤ 4."""
    if is_binary_nitride(ev.candidate.formula):
        return True
    n = _n_atoms(ev)
    return n is not None and n <= _SMALL_CELL_N_ATOMS


def nitride_phonon_recovery_kpoints(
    selected: list[CandidateEvaluation] | None = None,
) -> list[int]:
    """Nitride-phonon recovery k: min 8³; 12³ when cells are small/binary."""
    if selected and all(_cell_is_small_or_binary(ev) for ev in selected):
        return list(NITRIDE_PHONON_K_SMALL_BINARY)
    return list(NITRIDE_PHONON_K_MIN)


def _pilot_kpoints(
    source: CampaignConfig | None,
    selected: list[CandidateEvaluation] | None = None,
) -> list[int]:
    """Recovery k, never lowering a denser source-campaign mesh."""
    recovery = nitride_phonon_recovery_kpoints(selected)
    if source is None:
        return recovery
    src = [int(x) for x in (source.dft.kpoints or [])[:3]]
    if len(src) != 3:
        return recovery
    return [max(s, r) for s, r in zip(src, recovery, strict=True)]


def _pilot_dft(
    source: CampaignConfig | None,
    *,
    qpoints: list[int],
    pseudo_dir: str | None,
    nproc: int | None,
    selected: list[CandidateEvaluation] | None = None,
) -> DFTConfig:
    """Copy map DFT knobs; force phonon-only + denser q. Never enable EPW.

    Fallback (no source campaign) uses nitride-phonon recovery k, never 4³.
    """
    kpts = _pilot_kpoints(source, selected)
    if source is not None:
        dft = source.dft.model_copy(deep=True)
        dft.kpoints = list(kpts)
    else:
        dft = DFTConfig(
            engine="qe",
            ecutwfc=60.0,
            ecutrho=480.0,
            kpoints=list(kpts),
            qpoints=list(qpoints),
            do_relax=True,
            do_phonon=True,
            do_epw=False,
            phonon_method="dfpt",
            quality_tag="screening",
            nproc=nproc or 16,
            pseudo_dir=pseudo_dir or "/usr/share/espresso/pseudo",
        )
    dft.qpoints = list(qpoints)
    dft.do_phonon = True
    dft.do_epw = False
    dft.engine = "qe" if dft.engine in {"qe", "qe-epw", "mock"} else dft.engine
    # Keep unconventional flags off on a conventional recovery pilot.
    dft.do_dftu = False
    dft.do_wannier = False
    dft.do_dmft = False
    if dft.epw is not None:
        dft.epw = dft.epw.model_copy(
            update={"enabled": False, "nqc": list(qpoints)}
        )
    if pseudo_dir:
        dft.pseudo_dir = str(pseudo_dir)
    if nproc is not None:
        dft.nproc = int(nproc)
    return dft


def build_pilot_campaign(
    evaluations: list[CandidateEvaluation],
    *,
    name: str = "nitride_phonon_pilot",
    source_store: str | None = None,
    source_campaign: CampaignConfig | None = None,
    max_jobs: int = 4,
    mode: PilotMode = "binaries",
    candidate_ids: list[str] | None = None,
    qpoints: list[int] | None = None,
    output_dir: str | None = None,
    pseudo_dir: str | None = None,
    nproc: int | None = None,
    dry_run: bool = False,
) -> tuple[CampaignConfig, list[CandidateEvaluation]]:
    """Build a resume-safe denser-q phonon-only campaign from store cells."""
    qpts = parse_qpoints(qpoints)
    chosen = select_pilot_evaluations(
        evaluations,
        mode=mode,
        max_jobs=max_jobs,
        candidate_ids=candidate_ids,
    )
    specs = [evaluation_to_spec(e) for e in chosen]
    # Stamp pilot provenance onto each spec (does not change identity).
    for spec, ev in zip(specs, chosen, strict=True):
        row = classify_soft_mode(ev)
        meta = dict(spec.metadata or {})
        meta["pilot_source_store"] = source_store
        meta["pilot_soft_mode_class"] = row["soft_mode_class"]
        meta["pilot_source_qpoints"] = (
            list(source_campaign.dft.qpoints) if source_campaign is not None else None
        )
        meta["pilot_target_qpoints"] = list(qpts)
        spec.metadata = meta

    out = output_dir or f"outputs/{name}"
    if source_store and Path(out).resolve() == Path(source_store).resolve():
        raise ValueError(
            "pilot output_dir must differ from the source map store "
            f"({source_store}). Choose a new directory so resume stays safe."
        )

    dft = _pilot_dft(
        source_campaign,
        qpoints=qpts,
        pseudo_dir=pseudo_dir,
        nproc=nproc if nproc is not None else (
            source_campaign.dft.nproc if source_campaign is not None else None
        ),
        selected=chosen,
    )
    # CLI / operator override of UPF / ranks after copy.
    if pseudo_dir:
        dft.pseudo_dir = str(pseudo_dir)
    if nproc is not None:
        dft.nproc = int(nproc)

    calc_name = "mock" if dry_run else "qe"
    desc = (
        f"Denser-q phonon pilot ({len(specs)} cells, q={qpts[0]}×{qpts[1]}×{qpts[2]}); "
        f"selection={mode}; do_epw=false. "
        "Reuse of map candidate_specs — does not re-enumerate the full grid. "
        "Heuristic recovery from a coarse map; not production dynamical-stability "
        "proof. Human decides expand vs abandon."
    )
    if source_store:
        desc += f" Source store: {source_store}."

    cfg = CampaignConfig(
        name=name,
        description=desc,
        version="0.2",
        dry_run=dry_run,
        enumeration={
            "material_families": ["tm_nitride"],
            "candidate_specs": [s.model_dump(mode="json") for s in specs],
            "max_candidates": len(specs),
            "epitaxy_orientation": "auto",
            "use_buffers": True,
            "seed": 42,
            "supercell": [2, 2, 1],
        },
        calculators=[{"name": calc_name}],
        formation_filter={"enabled": False},
        surrogate={"tc_lambda": {"enabled": False}},
        active_learning={"enabled": False},
        dft=dft,
        ranking={
            "performance_weight": 0.55,
            "si_feasibility_weight": 0.45,
            "prefer_dynamically_stable": True,
            "prefer_low_hull": True,
        },
        run=RunConfig(
            resume=True,
            continue_on_error=True,
            force_rerun=False,
            resume_qe_steps=True,
            heartbeat_seconds=900,
        ),
        output_dir=out,
        export_formats=["json", "csv", "markdown"],
        extras={
            "pilot": {
                "source_store": source_store,
                "mode": mode,
                "qpoints": list(qpts),
                "n_selected": len(specs),
                "formulas": [s.formula for s in specs],
                "candidate_ids": [s.candidate_id for s in specs],
                "do_epw": False,
                "kpoints": list(dft.kpoints),
                "limitation": (
                    "Denser q is still a gate, not production dynamical-stability "
                    "proof. Electronic k under-sampling was the dominant ZrN "
                    "artefact; pilot k is min 8³ (prefer 12³ for small/binary). "
                    "do_epw is forced false; do not promote these cells into "
                    "EPW solely because the pilot ran. Soft-mode class is "
                    "heuristic. Mild residual imaginary modes after dense k "
                    "stay suspect, not stable."
                ),
                # soft_mode_class / denser-q confirmation are first-class on
                # shortlist + AcquisitionRecord (#45). This YAML stays
                # phonon-only (do_epw forced false).
            }
        },
    )
    return cfg, chosen


def write_pilot_yaml(config: CampaignConfig, path: str | Path) -> Path:
    """Write a ready-to-run phonon-only pilot campaign YAML."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    extras = (config.extras or {}).get("pilot") or {}
    qpts = extras.get("qpoints") or config.dft.qpoints
    with path.open("w", encoding="utf-8") as fh:
        fh.write(
            "# Auto-generated DENSER-Q PHONON PILOT — do_epw is false\n"
            "# Coarse-map recovery: reuse candidate_specs, denser q, new output_dir\n"
            f"# qpoints: {qpts}  kpoints: {config.dft.kpoints}  "
            f"selection={extras.get('mode')}\n"
            "# Electronic k: min 8³ (prefer 12³ for small/binary). "
            "Never lower source k.\n"
            "# This is still a discovery gate, not production dynamical-stability proof.\n"
            "# q=3³ is denser than the map, not a stability certificate.\n"
            "# Do not launch EPW on these cells until a human decides they are stable.\n"
            f"# Run: siscforge run --calculator qe {path.name}\n"
            "# Resume: re-run the same command (skips finished ok)\n"
            f"# Dry-run: siscforge run --dry-run {path.name}\n\n"
        )
        yaml.safe_dump(
            data,
            fh,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )
    return path


def pilot_summary_table(chosen: list[CandidateEvaluation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, ev in enumerate(chosen, start=1):
        row = classify_soft_mode(ev)
        rows.append(
            {
                "#": i,
                "formula": ev.candidate.formula,
                "strain": ev.candidate.in_plane_strain,
                "min_freq": row["min_frequency_cm1"],
                "class": row["soft_mode_class"],
                "binary": row["is_binary_nitride"],
                "candidate_id": ev.candidate.candidate_id[:8],
            }
        )
    return rows
