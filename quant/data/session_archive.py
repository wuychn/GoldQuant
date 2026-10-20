"""盘后涨停族：涨停池、炸板、跌停、昨涨停。"""

from __future__ import annotations

import sys

from quant.data.calendar import to_iso


def capture_session_extras(as_of: str) -> dict[str, int]:
    as_of = to_iso(as_of)
    out: dict[str, int] = {"zt_pool": 0, "zt_zb": 0, "zt_dt": 0, "zt_prev": 0}
    try:
        from quant.data.zt_pool import capture_zt_pool

        out["zt_pool"] = capture_zt_pool(as_of)
    except Exception as e:
        print(f"[WARN] 涨停池失败: {e}", file=sys.stderr, flush=True)
    try:
        from quant.data.board_pools import capture_board_pools

        bp = capture_board_pools(as_of)
        out["zt_zb"] = int(bp.get("zb", 0))
        out["zt_dt"] = int(bp.get("dt", 0))
        out["zt_prev"] = int(bp.get("prev", 0))
    except Exception as e:
        print(f"[WARN] 炸板/跌停/昨涨停失败: {e}", file=sys.stderr, flush=True)
    return out
