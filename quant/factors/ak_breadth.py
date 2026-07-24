"""AKShare 广度 enrich（委托 breadth_cache）。"""

from __future__ import annotations

from typing import Any

from quant.factors.breadth_cache import enrich_row_breadth as enrich_row_breadth
from quant.factors.breadth_cache import fetch_live_breadth, get_breadth, prefetch_batch

__all__ = ["enrich_row_breadth", "fetch_live_breadth", "get_breadth", "prefetch_batch"]
