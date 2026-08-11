"""Campaign configuration models (YAML-loadable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import math
import yaml
from pydantic import BaseModel, Field, field_validator
