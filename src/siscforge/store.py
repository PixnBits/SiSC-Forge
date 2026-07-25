"""Local file-based evaluation store (JSON).

Phase 0 persistence: a campaign directory holds candidates, evaluations,
filter summaries, and ranked exports. Ranking / export read from this store
so re-ranking does not require re-running calculators.

Resume: :meth:`append_evaluation` replaces by candidate_id **or** resume
fingerprint so interrupted multi-candidate runs can checkpoint after each job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig
from siscforge.resume import (
    evaluation_matches_candidate,
    find_resumable_evaluation,
    index_evaluations,
)


class EvaluationStore:
    """Filesystem store under ``root`` for one campaign run."""

    CANDIDATES = "candidates.json"
    EVALUATIONS = "evaluations.json"
    RANKED = "evaluations_ranked.json"
    FILTER = "formation_filter.json"
    CAMPAIGN = "campaign_resolved.yaml"
    META = "store_meta.json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- paths ---
    def path(self, name: str) -> Path:
        return self.root / name

    # --- candidates ---
    def save_candidates(self, candidates: list[StructureCandidate]) -> Path:
        path = self.path(self.CANDIDATES)
        data = [c.model_dump(mode="json") for c in candidates]
        _write_json(path, data)
        return path

    def load_candidates(self) -> list[StructureCandidate]:
        path = self.path(self.CANDIDATES)
        if not path.is_file():
            return []
        raw = _read_json(path)
        if not isinstance(raw, list):
            raise ValueError(f"Expected list in {path}")
        return [StructureCandidate.model_validate(item) for item in raw]

    # --- evaluations ---
    def save_evaluations(
        self,
        evaluations: list[CandidateEvaluation],
        *,
        ranked: bool = False,
    ) -> Path:
        name = self.RANKED if ranked else self.EVALUATIONS
        path = self.path(name)
        data = [e.model_dump(mode="json") for e in evaluations]
        _write_json(path, data)
        return path

    def load_evaluations(self, *, ranked: bool = False) -> list[CandidateEvaluation]:
        """Load ranked file if requested and present, else unranked evaluations."""
        if ranked:
            path = self.path(self.RANKED)
            if not path.is_file():
                path = self.path(self.EVALUATIONS)
        else:
            path = self.path(self.EVALUATIONS)
        if not path.is_file():
            return []
        raw = _read_json(path)
        if not isinstance(raw, list):
            raise ValueError(f"Expected list in {path}")
        return [CandidateEvaluation.model_validate(item) for item in raw]

    def append_evaluation(self, evaluation: CandidateEvaluation) -> Path:
        """Append one evaluation; replace same candidate_id or resume fingerprint.

        Flushed immediately (full rewrite of ``evaluations.json``) so a killed
        process still leaves partial shortlist results on disk.
        """
        current = self.load_evaluations(ranked=False)
        current = [
            e
            for e in current
            if not evaluation_matches_candidate(e, evaluation.candidate)
        ]
        current.append(evaluation)
        return self.save_evaluations(current, ranked=False)

    def find_successful(
        self,
        candidate: StructureCandidate,
        *,
        force_rerun: bool = False,
        require_real: bool = False,
    ) -> CandidateEvaluation | None:
        """Return a prior successful evaluation for *candidate*, if any."""
        if force_rerun:
            return None
        by_id, by_fp = index_evaluations(
            self.load_evaluations(ranked=False), require_real=require_real
        )
        return find_resumable_evaluation(
            candidate, by_id=by_id, by_fp=by_fp, force_rerun=force_rerun
        )

    def resume_index(
        self,
        *,
        require_real: bool = False,
    ) -> tuple[dict[str, CandidateEvaluation], dict[str, CandidateEvaluation]]:
        """Id / fingerprint indexes of successful evaluations currently on disk."""
        return index_evaluations(
            self.load_evaluations(ranked=False), require_real=require_real
        )

    # --- filter / campaign / meta ---
    def save_filter_summary(self, summary: dict[str, Any]) -> Path:
        path = self.path(self.FILTER)
        _write_json(path, summary)
        return path

    def save_campaign(self, config: CampaignConfig) -> Path:
        path = self.path(self.CAMPAIGN)
        config.to_yaml(path)
        return path

    def save_meta(self, meta: dict[str, Any]) -> Path:
        path = self.path(self.META)
        _write_json(path, meta)
        return path

    def save_json(self, name: str, data: Any) -> Path:
        """Write an arbitrary JSON document under the campaign store root."""
        path = self.path(name)
        _write_json(path, data)
        return path

    def load_meta(self) -> dict[str, Any]:
        path = self.path(self.META)
        if not path.is_file():
            return {}
        raw = _read_json(path)
        return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
