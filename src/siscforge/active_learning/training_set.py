"""Training-set store, promotion gate, and seed/literature ingestion (Phase 1.5).

Contracts (Technical Specs AC13, AC18; design note §4):

- Promotion is an **explicit** step; silent inclusion is forbidden.
- Mock / dry-run / disallowed quality tags are hard-refused.
- ``quality_tag=unknown`` is refused by default (opt-in via allow_unknown).
- Each training-set snapshot used for a model version is immutable and hashed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from siscforge.models.active_learning import (
    TrainingExample,
    TrainingSetSnapshot,
)
from siscforge.models.candidate import CandidateEvaluation
from siscforge.quality import (
    FLAG_SCREENING_HIGH_LAMBDA,
    assess_result_quality,
    screening_high_lambda_hard_zero,
)

# Default quality allow-list for promotion (Specs §2.2.2)
DEFAULT_ALLOWED_QUALITY_TAGS: frozenset[str] = frozenset({"screening", "production"})
DEFAULT_ALLOWED_STATUS: frozenset[str] = frozenset({"ok"})
DISALLOWED_QUALITY_FLAGS: frozenset[str] = frozenset(
    {
        "mock",
        "unreliable",
        "epw_failed",
        "wannier_failed",
        FLAG_SCREENING_HIGH_LAMBDA,
    }
)


class PromotionError(ValueError):
    """Raised when a candidate evaluation cannot be promoted (AC13 / AC18)."""


def _is_mock_evaluation(ev: CandidateEvaluation) -> bool:
    if ev.status == "mock":
        return True
    if (ev.calculator_name or "").lower() in {"mock", "mock-calculator"}:
        return True
    if ev.electron_phonon is not None and ev.electron_phonon.quality_tag == "mock":
        return True
    if ev.phonon is not None and ev.phonon.quality_tag == "mock":
        return True
    if "mock" in (ev.quality_flags or []):
        return True
    return False


def promotion_eligibility(
    evaluation: CandidateEvaluation,
    *,
    allowed_quality_tags: Iterable[str] | None = None,
    allowed_status: Iterable[str] | None = None,
    require_epw: bool = True,
    allow_unknown_quality: bool = False,
) -> tuple[bool, str]:
    """Return (ok, reason). Does not raise."""
    tags = frozenset(allowed_quality_tags or DEFAULT_ALLOWED_QUALITY_TAGS)
    statuses = frozenset(allowed_status or DEFAULT_ALLOWED_STATUS)

    if _is_mock_evaluation(evaluation):
        return False, "mock / dry-run labels cannot enter a real training set (AC18)"

    if evaluation.status not in statuses:
        return False, f"status={evaluation.status!r} not in allowed {sorted(statuses)}"

    # Prefer EPW quality_tag when present
    qtag = evaluation.candidate.quality_tag
    if evaluation.electron_phonon is not None:
        qtag = evaluation.electron_phonon.quality_tag or qtag
    if qtag == "mock":
        return False, "quality_tag=mock refused"
    if qtag == "unknown" and not allow_unknown_quality:
        return False, "quality_tag=unknown refused (pass allow_unknown_quality to opt in)"
    if qtag not in tags and qtag != "unknown":
        return False, f"quality_tag={qtag!r} not in allow-list {sorted(tags)}"

    flags = set(evaluation.quality_flags or [])
    if evaluation.electron_phonon is not None:
        flags |= set(evaluation.electron_phonon.quality_flags or [])
    # Re-assess so the #44 hard-zero rule applies even when the caller
    # has not yet run rank / apply_quality_assessment.
    assessed = assess_result_quality(evaluation)
    flags |= set(assessed.quality_flags or [])
    bad = flags & DISALLOWED_QUALITY_FLAGS
    if bad:
        return False, f"disallowed quality flags: {sorted(bad)}"
    if screening_high_lambda_hard_zero(flags):
        return (
            False,
            "screening high-λ + random-Wannier / coarse grids "
            "cannot enter the conventional training set",
        )

    if evaluation.result_quality == "unreliable":
        return False, "result_quality=unreliable"

    if require_epw:
        ep = evaluation.electron_phonon
        if ep is None:
            return False, "no electron_phonon result to promote"
        if ep.status not in {"ok", "mock"}:  # mock already caught above
            return False, f"electron_phonon.status={ep.status!r}"
        if ep.best_tc_K() is None and ep.lambda_total is None:
            return False, "electron_phonon has neither Tc nor lambda"

    return True, "eligible"


def promote_evaluation(
    evaluation: CandidateEvaluation,
    *,
    source: Literal["project", "literature", "golden"] = "project",
    campaign_store: str | None = None,
    literature_ref: str | None = None,
    literature_notes: str = "",
    allowed_quality_tags: Iterable[str] | None = None,
    require_epw: bool = True,
    allow_unknown_quality: bool = False,
    notes: str = "",
) -> TrainingExample:
    """Explicitly promote a clean evaluation into a TrainingExample.

    Raises :class:`PromotionError` on refusal (AC13, AC18).
    """
    ok, reason = promotion_eligibility(
        evaluation,
        allowed_quality_tags=allowed_quality_tags,
        require_epw=require_epw,
        allow_unknown_quality=allow_unknown_quality,
    )
    if not ok:
        raise PromotionError(reason)

    cand = evaluation.candidate
    ep = evaluation.electron_phonon
    tc = ep.best_tc_K() if ep is not None else None
    tc_source = None
    if ep is not None:
        if ep.Tc_eliashberg is not None:
            tc_source = "epw_eliashberg"
        elif ep.Tc_allen_dynes is not None:
            tc_source = "epw_allen_dynes"
        else:
            tc_source = "epw"

    qtag: str = cand.quality_tag
    if ep is not None and ep.quality_tag:
        qtag = ep.quality_tag

    return TrainingExample(
        candidate_id=cand.candidate_id,
        formula=cand.formula,
        material_family=cand.material_family,
        source=source,
        quality_tag=qtag if qtag in {"screening", "production", "mock", "unknown"} else "unknown",  # type: ignore[arg-type]
        status=evaluation.status,
        lambda_total=ep.lambda_total if ep else None,
        omega_log=ep.omega_log if ep else None,
        tc_K=tc,
        tc_source=tc_source,
        energy_above_hull=cand.energy_above_hull_proxy,
        si_feasibility_total=(
            evaluation.si_feasibility.total if evaluation.si_feasibility else None
        ),
        quality_flags=list(evaluation.quality_flags or []),
        in_plane_strain=cand.in_plane_strain,
        composition=dict(cand.composition or {}),
        structure_cif=cand.structure_cif or cand.relaxed_structure_cif,
        campaign_store=campaign_store,
        literature_ref=literature_ref,
        literature_notes=literature_notes,
        quality_snapshot={
            "result_quality": evaluation.result_quality,
            "quality_flags": list(evaluation.quality_flags or []),
            "quality_notes": evaluation.quality_notes,
            "ep_quality_tag": ep.quality_tag if ep else None,
            "ep_status": ep.status if ep else None,
        },
        notes=notes or evaluation.notes or "",
    )


def literature_example(
    *,
    formula: str,
    tc_K: float | None = None,
    lambda_total: float | None = None,
    omega_log: float | None = None,
    material_family: str = "other",
    literature_ref: str,
    literature_notes: str = "",
    composition: dict[str, float] | None = None,
    candidate_id: str | None = None,
    source: Literal["literature", "golden"] = "literature",
) -> TrainingExample:
    """Ingest a clean literature / golden label (no EPW evaluation required)."""
    return TrainingExample(
        candidate_id=candidate_id or f"lit-{formula}-{literature_ref[:32]}",
        formula=formula,
        material_family=material_family,
        source=source,
        quality_tag="production",
        status="ok",
        lambda_total=lambda_total,
        omega_log=omega_log,
        tc_K=tc_K,
        tc_source="literature",
        composition=dict(composition or {}),
        literature_ref=literature_ref,
        literature_notes=literature_notes,
        notes=f"literature/golden seed: {literature_ref}",
    )


def hash_examples(examples: Sequence[TrainingExample]) -> str:
    """Stable content hash for an immutable training-set snapshot."""
    payload = [
        e.model_dump(mode="json", exclude={"promoted_at"})
        for e in sorted(examples, key=lambda x: (x.candidate_id, x.example_id))
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_snapshot(
    examples: Sequence[TrainingExample],
    *,
    notes: str = "",
) -> TrainingSetSnapshot:
    """Build an immutable hashed snapshot."""
    ex = list(examples)
    h = hash_examples(ex)
    return TrainingSetSnapshot(
        content_hash=h,
        n_examples=len(ex),
        examples=ex,
        notes=notes,
    )


class TrainingSetStore:
    """File-based training set under a dedicated root directory.

    Layout::

        root/
          examples.json          # current mutable working set (append-only via API)
          snapshots/
            <hash>.json          # immutable snapshots
          current_snapshot.json  # pointer to latest snapshot metadata
    """

    EXAMPLES = "examples.json"
    SNAPSHOTS_DIR = "snapshots"
    CURRENT = "current_snapshot.json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
        self._last_warnings: list[str] = []

    @property
    def last_warnings(self) -> list[str]:
        """Warnings from the most recent ``add_example`` / ``promote`` call."""
        return list(self._last_warnings)

    def _examples_path(self) -> Path:
        return self.root / self.EXAMPLES

    def load_examples(self) -> list[TrainingExample]:
        path = self._examples_path()
        if not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Expected list in {path}")
        return [TrainingExample.model_validate(item) for item in raw]

    def save_examples(self, examples: Sequence[TrainingExample]) -> Path:
        path = self._examples_path()
        data = [e.model_dump(mode="json") for e in examples]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def add_example(
        self,
        example: TrainingExample,
        *,
        replace_same_candidate: bool = True,
    ) -> TrainingExample:
        """Append one example; optionally replace prior entry with same candidate_id.

        Quality-regression warnings are stored on :attr:`last_warnings` (does not
        change the return type — remains ``TrainingExample`` for API stability).
        """
        warnings: list[str] = []
        current = self.load_examples()
        if replace_same_candidate:
            for prev in current:
                if prev.candidate_id != example.candidate_id:
                    continue
                if prev.quality_tag == "production" and example.quality_tag == "screening":
                    warnings.append(
                        f"replacing production label for {example.candidate_id} "
                        f"({prev.formula}) with screening — verify intent"
                    )
                if prev.tc_K is not None and example.tc_K is not None:
                    if abs(prev.tc_K - example.tc_K) > 5.0:
                        warnings.append(
                            f"Tc changed {prev.tc_K:.1f} → {example.tc_K:.1f} K "
                            f"for {example.formula}"
                        )
            current = [e for e in current if e.candidate_id != example.candidate_id]
        current = [e for e in current if e.example_id != example.example_id]
        current.append(example)
        self.save_examples(current)
        self._last_warnings = warnings
        return example

    def promote(
        self,
        evaluation: CandidateEvaluation,
        **kwargs: Any,
    ) -> TrainingExample:
        """Promote evaluation and persist into the working set."""
        example = promote_evaluation(evaluation, **kwargs)
        return self.add_example(example)

    def add_literature(self, example: TrainingExample) -> TrainingExample:
        if example.source not in {"literature", "golden"}:
            raise PromotionError("add_literature requires source=literature or golden")
        return self.add_example(example)


    def snapshot(self, *, notes: str = "") -> TrainingSetSnapshot:
        """Freeze the current working set into an immutable hashed snapshot."""
        examples = self.load_examples()
        snap = make_snapshot(examples, notes=notes)
        path = self.root / self.SNAPSHOTS_DIR / f"{snap.content_hash}.json"
        path.write_text(
            json.dumps(snap.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        meta = {
            "snapshot_id": snap.snapshot_id,
            "content_hash": snap.content_hash,
            "n_examples": snap.n_examples,
            "path": str(path.name),
            "notes": notes,
        }
        (self.root / self.CURRENT).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        return snap

    def load_snapshot(self, content_hash: str | None = None) -> TrainingSetSnapshot | None:
        if content_hash is None:
            cur = self.root / self.CURRENT
            if not cur.is_file():
                return None
            meta = json.loads(cur.read_text(encoding="utf-8"))
            content_hash = meta.get("content_hash")
        if not content_hash:
            return None
        path = self.root / self.SNAPSHOTS_DIR / f"{content_hash}.json"
        if not path.is_file():
            return None
        return TrainingSetSnapshot.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )

    def audit(self) -> list[dict[str, Any]]:
        """List every training example with origin and quality flags (design note)."""
        rows = []
        for e in self.load_examples():
            rows.append(
                {
                    "example_id": e.example_id,
                    "candidate_id": e.candidate_id,
                    "formula": e.formula,
                    "source": e.source,
                    "quality_tag": e.quality_tag,
                    "status": e.status,
                    "tc_K": e.tc_K,
                    "lambda_total": e.lambda_total,
                    "quality_flags": e.quality_flags,
                    "literature_ref": e.literature_ref,
                    "campaign_store": e.campaign_store,
                    "promoted_at": e.promoted_at.isoformat() if e.promoted_at else None,
                }
            )
        return rows

    def summary(self) -> dict[str, Any]:
        examples = self.load_examples()
        by_source: dict[str, int] = {}
        by_family: dict[str, int] = {}
        for e in examples:
            by_source[e.source] = by_source.get(e.source, 0) + 1
            by_family[e.material_family] = by_family.get(e.material_family, 0) + 1
        cur = None
        cur_path = self.root / self.CURRENT
        if cur_path.is_file():
            cur = json.loads(cur_path.read_text(encoding="utf-8"))
        return {
            "n_examples": len(examples),
            "by_source": by_source,
            "by_family": by_family,
            "current_snapshot": cur,
            "root": str(self.root),
        }


# Seed goldens used when bootstrapping a new training set
DEFAULT_GOLDEN_SEEDS: list[dict[str, Any]] = [
    {
        "formula": "NbN",
        "tc_K": 16.0,
        "lambda_total": 1.05,
        "omega_log": 280.0,
        "material_family": "tm_nitride",
        "literature_ref": "golden:NbN",
        "source": "golden",
    },
    {
        "formula": "MgB2",
        "tc_K": 39.0,
        "lambda_total": 0.85,
        "omega_log": 700.0,
        "material_family": "mgb2_boride",
        "literature_ref": "golden:MgB2",
        "source": "golden",
    },
    {
        "formula": "TiN",
        "tc_K": 5.0,
        "lambda_total": 0.70,
        "omega_log": 320.0,
        "material_family": "tm_nitride",
        "literature_ref": "golden:TiN",
        "source": "golden",
    },
]


def seed_default_goldens(store: TrainingSetStore) -> list[TrainingExample]:
    """Inject default NbN / MgB2 / TiN goldens if not already present."""
    existing = {e.formula for e in store.load_examples() if e.source == "golden"}
    added: list[TrainingExample] = []
    for spec in DEFAULT_GOLDEN_SEEDS:
        if spec["formula"] in existing:
            continue
        ex = literature_example(
            formula=spec["formula"],
            tc_K=spec.get("tc_K"),
            lambda_total=spec.get("lambda_total"),
            omega_log=spec.get("omega_log"),
            material_family=spec.get("material_family", "other"),
            literature_ref=spec["literature_ref"],
            source=spec.get("source", "golden"),  # type: ignore[arg-type]
        )
        store.add_literature(ex)
        added.append(ex)
    return added


def _parse_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    return float(val)


def load_literature_records(path: str | Path) -> list[dict[str, Any]]:
    """Load literature seed records from JSON, JSONL, or CSV.

    Required fields: ``formula``, ``literature_ref``.
    Optional: ``tc_K``, ``lambda_total``, ``omega_log``, ``material_family``,
    ``source`` (literature|golden), ``literature_notes``, ``candidate_id``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    records: list[dict[str, Any]] = []
    if suffix == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    elif suffix == ".json":
        raw = json.loads(text)
        if isinstance(raw, list):
            records = list(raw)
        elif isinstance(raw, dict) and "examples" in raw:
            records = list(raw["examples"])
        else:
            raise ValueError("JSON literature file must be a list or {examples: [...]}")
    elif suffix == ".csv":
        reader = csv.DictReader(text.splitlines())
        records = [dict(row) for row in reader]
    else:
        # Try JSON then JSONL
        try:
            raw = json.loads(text)
            if isinstance(raw, list):
                records = list(raw)
            else:
                raise ValueError("not a list")
        except json.JSONDecodeError:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def seed_from_literature_file(
    store: TrainingSetStore,
    path: str | Path,
    *,
    default_source: Literal["literature", "golden"] = "literature",
) -> list[TrainingExample]:
    """Bulk-ingest literature / golden labels from a file.

    Each record requires ``formula``, ``literature_ref``, and **at least one**
    of ``tc_K`` / ``lambda_total`` / ``omega_log`` so empty rows cannot inflate
    bootstrap label counts or produce target-free "trained" models.
    """
    records = load_literature_records(path)
    added: list[TrainingExample] = []
    for i, rec in enumerate(records):
        # Skip documentation-only keys / records
        if set(rec.keys()) <= {"_comment", "comment", "notes"} and not rec.get("formula"):
            continue
        formula = str(rec.get("formula") or "").strip()
        if formula.startswith("_"):
            continue
        lit_ref = str(rec.get("literature_ref") or rec.get("ref") or "").strip()
        if not formula or not lit_ref:
            raise PromotionError(
                f"Record {i}: formula and literature_ref are required "
                f"(got formula={formula!r}, literature_ref={lit_ref!r})"
            )
        tc_K = _parse_float(rec.get("tc_K") if "tc_K" in rec else rec.get("tc"))
        lambda_total = _parse_float(rec.get("lambda_total") or rec.get("lambda"))
        omega_log = _parse_float(rec.get("omega_log"))
        if tc_K is None and lambda_total is None and omega_log is None:
            raise PromotionError(
                f"Record {i} ({formula}): at least one of tc_K, lambda_total, "
                f"omega_log is required"
            )
        source = rec.get("source") or default_source
        if source not in {"literature", "golden"}:
            source = default_source
        ex = literature_example(
            formula=formula,
            tc_K=tc_K,
            lambda_total=lambda_total,
            omega_log=omega_log,
            material_family=str(rec.get("material_family") or "other"),
            literature_ref=lit_ref,
            literature_notes=str(rec.get("literature_notes") or rec.get("notes") or ""),
            candidate_id=rec.get("candidate_id"),
            source=source,  # type: ignore[arg-type]
        )
        store.add_literature(ex)
        added.append(ex)
    return added

