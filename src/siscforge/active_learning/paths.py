"""Shared AL root resolution (Phase 1.5b).

Critical product rule: the training set and model registry must be **shared
across campaigns**, not buried under each campaign ``output_dir``. Otherwise
``al-train`` never compounds and the flywheel stalls.

Resolution order for the AL state root:

1. Explicit ``al_root`` argument / ``--al-root`` CLI flag
2. Campaign YAML ``active_learning.al_root`` (caller passes it in)
3. Environment variable ``SISC_AL_ROOT``
4. Default ``./al_state``
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_AL_ROOT_NAME = "al_state"
ENV_AL_ROOT = "SISC_AL_ROOT"


def resolve_al_root(al_root: str | Path | None = None) -> Path:
    """Return the shared AL state root (parent of training_set/ and models/)."""
    if al_root is not None and str(al_root).strip():
        return Path(al_root).expanduser()
    env = os.environ.get(ENV_AL_ROOT)
    if env and env.strip():
        return Path(env).expanduser()
    return Path(DEFAULT_AL_ROOT_NAME)


def al_subroots(al_root: str | Path | None = None) -> tuple[Path, Path, Path]:
    """Return ``(base, training_set_root, models_root)``."""
    base = resolve_al_root(al_root)
    return base, base / "training_set", base / "models"


def write_al_pointer(
    store_dir: str | Path,
    *,
    al_root: str | Path,
    training_set: str | Path,
    models: str | Path,
    model_version: str | None = None,
    bootstrap: bool | None = None,
) -> Path:
    """Write a small pointer file in the campaign store for audit / resume."""
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / "al_state_pointer.json"
    doc = {
        "al_root": str(Path(al_root).resolve()),
        "training_set": str(Path(training_set).resolve()),
        "models": str(Path(models).resolve()),
        "model_version": model_version,
        "bootstrap": bootstrap,
        "note": (
            "Shared AL state lives outside this campaign store so labels and "
            "models accumulate across runs. Override with --al-root or SISC_AL_ROOT."
        ),
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path
