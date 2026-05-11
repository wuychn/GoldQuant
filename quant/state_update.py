"""Post-analysis state update orchestration.

The legacy parser is still implemented in ``quant.pipeline`` while the system is
being decomposed. This module gives the runner a stable boundary: analysis text
comes in, state files are updated, and Feishu-friendly text comes out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StateUpdateResult:
    content_for_push: str
    details: dict[str, Any]


def apply_analysis_updates(
    analysis: str,
    *,
    mode: str,
    market_payload: dict[str, Any] | None,
) -> StateUpdateResult:
    # Lazily import to avoid a startup cycle while the legacy parser continues
    # to live in ``quant.pipeline``.
    from quant import pipeline

    parsed = pipeline.parse_and_update(
        analysis,
        mode,
        market_payload=market_payload,
    )
    content_for_push = pipeline.replace_json_for_feishu(
        analysis,
        optional_span=parsed["optional_span"],
        optional_lines=parsed["optional_lines"],
        holdings_span=parsed["holdings_span"],
        holdings_lines=parsed["holdings_lines"],
    )
    return StateUpdateResult(content_for_push=content_for_push, details=parsed)
