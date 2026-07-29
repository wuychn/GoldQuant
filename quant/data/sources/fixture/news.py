"""Fixture news source — reads ``data/fixtures/news_global.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE = Path(__file__).resolve().parents[4] / "data" / "fixtures" / "news_global.json"


class FixtureNewsSource:
    def fetch_em(self) -> list[dict[str, Any]]:
        return self._load()

    def fetch_ths(self) -> list[dict[str, Any]]:
        return []

    def fetch_global(self) -> list[dict[str, Any]]:
        return self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not _FIXTURE.is_file():
            return []
        try:
            raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        return []
