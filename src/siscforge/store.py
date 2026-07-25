"""Local file-based evaluation store (JSON).

Phase 0 persistence: a campaign directory holds candidates, evaluations,
filter summaries, and ranked exports. Ranking / export read from this store
so re-ranking does not require re-running calculators.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from siscforge.models.candidate import CandidateEvaluation, StructureCandidate
from siscforge.models.config import CampaignConfig


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
        """Append one evaluation to the unranked store (read-modify-write)."""
        current = self.load_evaluations(ranked=False)
        # Replace same candidate_id if re-run
        cid = evaluation.candidate.candidate_id
        current = [e for e in current if e.candidate.candidate_id != cid]
        current.append(evaluation)
        return self.save_evaluations(current, ranked=False)

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
