"""诊断指定自选的趋势判定细节。"""

from __future__ import annotations

import json
from pathlib import Path

from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.main_wave import MainWaveScorer
from quant.scoring.dimensions.stock_history import StockHistoryScorer
from quant.scoring.tech_indicators import hist_rows_sorted, mas_from_stock, quote_change_pct
from quant.strategy.main_wave import (
    is_main_wave_acceleration,
    is_main_wave_pullback,
    is_main_wave_trend_active,
    main_wave_phase,
)
from quant.strategy.trend import quantify_trend

CODES = ["600487", "300373", "600869"]


def main() -> None:
    p = Path.home() / ".quant/daily/2026-06-24/raw/during_1343.json"
    payload = json.loads(p.read_text(encoding="utf-8"))
    by = {str(r.get("股票代码", "")).strip(): r for r in payload.get("自选股") or []}
    ctx = ScoreContext(payload=payload, mode="during_market")
    mw = MainWaveScorer()
    sh = StockHistoryScorer()

    for code in CODES:
        s = by.get(code)
        if not s:
            print(code, "MISSING")
            continue
        name = s.get("股票名称")
        phase, note, _ = quantify_trend(s)
        ok_a, na = is_main_wave_acceleration(s)
        ok_t, nt = is_main_wave_trend_active(s)
        ok_p, np = is_main_wave_pullback(s)
        ok_ph, ph, phn = main_wave_phase(s)
        mw_r = mw.score(ctx, s)
        sh_r = sh.score(ctx, s)
        m = mas_from_stock(s)
        rows = hist_rows_sorted(s.get("历史行情") or [])
        last5 = rows[-5:] if len(rows) >= 5 else rows
        chgs = [
            {
                "date": r.get("日期") or r.get("date"),
                "chg": r.get("涨跌幅") or r.get("change_pct"),
                "close": r.get("收盘") or r.get("close"),
            }
            for r in last5
        ]
        print("=" * 60)
        print(f"{code} {name} | 涨幅%={quote_change_pct(s)}")
        print(f"趋势: {phase} | {note}")
        print(f"加速={ok_a}({na})")
        print(f"趋势有效={ok_t}({nt})")
        print(f"回调={ok_p}({np})")
        print(f"main_wave_phase: ok={ok_ph} phase={ph} note={phn}")
        print(f"主升分={mw_r.score} | {mw_r.detail}")
        print(f"历史分={sh_r.score} | {sh_r.detail}")
        print(
            f"MA last={m.get('last')} ma5={m.get('ma5')} ma10={m.get('ma10')} "
            f"ma20={m.get('ma20')} macd={m.get('macd')}"
        )
        print("近5日:", chgs)


if __name__ == "__main__":
    main()
