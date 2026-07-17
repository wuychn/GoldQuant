"""自选股完整评分：先补同花顺 F10 概念粘合度，再按新逻辑打分。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.services.stock_enrich import attach_stock_concepts
from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.scoring.tech_indicators import quote_change_pct
from quant.store.paths import quant_home
from quant.store.state import get_optional
from quant.strategy.momentum import momentum_score
from quant.strategy.trend import quantify_trend


def _latest_during_payload() -> tuple[dict, Path]:
    root = quant_home() / "daily"
    for day_dir in sorted(root.iterdir(), reverse=True):
        raw = day_dir / "raw"
        if not raw.is_dir():
            continue
        ps = sorted(raw.glob("during_*.json"))
        if ps:
            return json.loads(ps[-1].read_text(encoding="utf-8")), ps[-1]
    raise FileNotFoundError("未找到 during_*.json")


def _dim_line(score_obj) -> str:
    parts: list[str] = []
    for d in getattr(score_obj, "dimensions", []) or []:
        if not getattr(d, "enabled", False) or not getattr(d, "available", False):
            continue
        parts.append(f"{d.name}:{d.score:.1f}(w{d.weight})")
    return " | ".join(parts)


def _concept_dim(score_obj) -> tuple[float | None, str | None, str | None, str | None, list]:
    for d in getattr(score_obj, "dimensions", []) or []:
        if d.name != "concept_theme":
            continue
        det = d.detail or {}
        top = (det.get("概念粘合度") or [])[:3]
        return (
            d.score,
            det.get("评分模式"),
            det.get("最佳命中概念"),
            det.get("概念来源"),
            top,
        )
    return None, None, None, None, []


async def _enrich_watchlist(by_code: dict[str, dict], optional: list[dict]) -> dict[str, dict]:
    sem = asyncio.Semaphore(4)
    out: dict[str, dict] = {}

    async def one(code: str, name: str) -> None:
        row = by_code.get(code) or {"股票代码": code, "股票名称": name}
        async with sem:
            item, _ = await attach_stock_concepts(dict(row))
        out[code] = item

    await asyncio.gather(
        *[
            one(str(opt.get("股票代码", "")).strip(), str(opt.get("股票名称", "")).strip())
            for opt in optional
            if str(opt.get("股票代码", "")).strip()
        ]
    )
    return out


def main() -> None:
    payload, src = _latest_during_payload()
    threshold = float(load_scoring_config().get("watchlist_threshold", 70))
    by_code = {
        str(r.get("股票代码", "")).strip(): dict(r)
        for r in (payload.get("自选股") or [])
        if isinstance(r, dict)
    }
    optional = get_optional()
    enriched = asyncio.run(_enrich_watchlist(by_code, optional))
    fit_cnt = sum(1 for s in enriched.values() if s.get("概念粘合度"))

    ctx = ScoreContext(payload=payload, mode="during_market")
    engine = ScoringEngine()
    rows: list[dict] = []
    missing: list[str] = []

    for opt in optional:
        code = str(opt.get("股票代码", "")).strip()
        name = str(opt.get("股票名称", "")).strip()
        stock = enriched.get(code)
        if not stock:
            missing.append(code)
            continue
        scored = engine.score_stock(ctx, stock)
        scored = engine.apply_threshold([scored], kind="watchlist")[0]
        phase, trend_note, _ = quantify_trend(stock)
        ms, _ = momentum_score(stock)
        chg = quote_change_pct(stock)
        ct, mode, best, src_c, top_fit = _concept_dim(scored)
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
                "concept_theme": round(ct, 2) if ct is not None else None,
                "concept_mode": mode,
                "concept_best": best,
                "concept_source": src_c or stock.get("概念来源"),
                "top_fit": top_fit,
                "dims": _dim_line(scored),
            }
        )

    rows.sort(key=lambda r: (-r["total"], -r["momentum"], r["code"]))

    out_path = quant_home() / "state/watchlist_full_scores_fit.json"
    out_path.write_text(
        json.dumps(
            {
                "source": str(src),
                "fit_enriched": fit_cnt,
                "watchlist_threshold": threshold,
                "count": len(optional),
                "scored": len(rows),
                "missing": missing,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"自选 {len(optional)} 只 | 粘合度 {fit_cnt}/{len(optional)} | 数据源 {src.name} | 阈值 {threshold}")
    print(f"{'#':>3}  {'代码':<8} {'名称':<10} {'总分':>6} {'达标':>4} {'概念':>6} {'动能':>5} {'涨幅%':>7}  粘合度Top1")
    print("-" * 98)
    for i, r in enumerate(rows, 1):
        chg_s = f"{r['chg']:.2f}" if r["chg"] is not None else "-"
        mark = "Y" if r["passed"] else "N"
        top1 = r["top_fit"][0]["concept"] if r["top_fit"] else "-"
        ct_s = f"{r['concept_theme']:.1f}" if r["concept_theme"] is not None else "-"
        print(
            f"{i:>3}. {r['code']:<8} {r['name']:<10} {r['total']:>6.1f} {mark:>4} "
            f"{ct_s:>6} {r['momentum']:>5.1f} {chg_s:>7}  {top1}"
        )

    if missing:
        print(f"\n无数据 {len(missing)} 只: {', '.join(missing)}")

    print(f"\n完整 JSON: {out_path}")
    print("\n--- 全部维度明细（按总分降序）---")
    for r in rows:
        ct_info = ""
        if r.get("concept_mode"):
            ct_info = f" | 概念[{r['concept_mode']}] 最佳={r.get('concept_best')} 分={r.get('concept_theme')}"
        print(f"{r['name']}({r['code']}) 总分{r['total']} 趋势[{r['phase']}]{ct_info}")
        print(f"  {r['dims']}")


if __name__ == "__main__":
    main()
