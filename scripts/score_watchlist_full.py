"""使用 ScoringEngine 完整维度对自选股评分并输出。"""

from __future__ import annotations

import json
from pathlib import Path

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.scoring.tech_indicators import quote_change_pct
from quant.store.state import get_optional
from quant.strategy.momentum import momentum_score
from quant.strategy.trend import quantify_trend


def _latest_during_payload() -> tuple[dict, Path]:
    root = Path.home() / ".quant/daily"
    candidates: list[Path] = []
    for day_dir in sorted(root.iterdir(), reverse=True):
        raw = day_dir / "raw"
        if not raw.is_dir():
            continue
        for p in sorted(raw.glob("during_*.json")):
            candidates.append(p)
        if candidates:
            break
    if not candidates:
        raise FileNotFoundError("未找到 during_*.json（需含 enrich 后自选股）")
    path = candidates[-1]
    return json.loads(path.read_text(encoding="utf-8")), path


def _dim_line(score_obj) -> str:
    parts: list[str] = []
    for d in getattr(score_obj, "dimensions", []) or []:
        if not getattr(d, "enabled", False) or not getattr(d, "available", False):
            continue
        parts.append(f"{d.name}:{d.score:.0f}(w{d.weight})")
    return " | ".join(parts)


def main() -> None:
    payload, src = _latest_during_payload()
    cfg = load_scoring_config()
    threshold = float(cfg.get("watchlist_threshold", 70))
    by_code = {
        str(r.get("股票代码", "")).strip(): r
        for r in (payload.get("自选股") or [])
        if isinstance(r, dict)
    }
    optional = get_optional()
    ctx = ScoreContext(payload=payload, mode="during_market")
    engine = ScoringEngine()

    rows: list[dict] = []
    missing: list[str] = []

    for opt in optional:
        code = str(opt.get("股票代码", "")).strip()
        name = str(opt.get("股票名称", "")).strip()
        stock = by_code.get(code)
        if not stock:
            missing.append(f"{code} {name}")
            continue
        scored = engine.score_stock(ctx, stock)
        scored = engine.apply_threshold([scored], kind="watchlist")[0]
        phase, trend_note, _ = quantify_trend(stock)
        ms, _ = momentum_score(stock)
        chg = quote_change_pct(stock)
        rows.append(
            {
                "code": code,
                "name": name,
                "total": round(scored.total, 2),
                "passed": scored.passed_threshold,
                "phase": phase,
                "trend_note": trend_note,
                "momentum": round(ms, 1),
                "chg": chg,
                "dims": _dim_line(scored),
                "score_obj": scored,
            }
        )

    rows.sort(key=lambda r: (-r["total"], -r["momentum"], r["code"]))

    out_path = Path.home() / ".quant/state/watchlist_full_scores.json"
    out_path.write_text(
        json.dumps(
            {
                "source": str(src),
                "watchlist_threshold": threshold,
                "count": len(optional),
                "scored": len(rows),
                "missing": missing,
                "rows": [
                    {
                        k: v
                        for k, v in r.items()
                        if k != "score_obj"
                    }
                    for r in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"自选 {len(optional)} 只 | 完整评分 | 数据源: {src.name} | 阈值 {threshold}")
    print(f"{'#':>3}  {'代码':<8} {'名称':<10} {'总分':>6} {'达标':>4} {'动能':>5} {'趋势':<6} {'涨幅%':>7}")
    print("-" * 72)
    for i, r in enumerate(rows, 1):
        chg_s = f"{r['chg']:.2f}" if r["chg"] is not None else "—"
        mark = "Y" if r["passed"] else "N"
        print(
            f"{i:>3}. {r['code']:<8} {r['name']:<10} {r['total']:>6.1f} {mark:>4} "
            f"{r['momentum']:>5.1f} {r['phase']:<6} {chg_s:>7}"
        )

    if missing:
        print(f"\n无行情数据 {len(missing)} 只: {', '.join(missing[:5])}{'...' if len(missing)>5 else ''}")

    print(f"\n完整 JSON: {out_path}")
    print("\n--- Top5 维度明细 ---")
    for r in rows[:5]:
        print(f"\n{r['name']}({r['code']}) 总分{r['total']} 趋势[{r['phase']}] {r['trend_note']}")
        print(f"  {r['dims']}")


if __name__ == "__main__":
    main()
