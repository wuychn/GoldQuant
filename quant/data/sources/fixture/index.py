"""Fixture index source — reads ``data/fixtures/index_spot.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE = Path(__file__).resolve().parents[4] / "data" / "fixtures" / "index_spot.json"


class FixtureIndexSource:
    def fetch_hs_important(self) -> list[dict[str, Any]] | None:
        if not _FIXTURE.is_file():
            return []
        try:
            raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        return []
