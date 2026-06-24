"""扫描自选并按近端动能分排序。"""

from __future__ import annotations

import json
from pathlib import Path

from quant.config import load_gates_config
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
        raise FileNotFoundError("未找到 during_*.json 行情快照")
    path = candidates[-1]
    return json.loads(path.read_text(encoding="utf-8")), path


def main() -> None:
    payload, src = _latest_during_payload()
    mw_cfg = load_gates_config().get("main_wave") or {}
    by_code = {
        str(r.get("股票代码", "")).strip(): r
        for r in (payload.get("自选股") or [])
        if isinstance(r, dict)
    }

    optional = get_optional()
    rows: list[dict] = []
    for opt in optional:
        code = str(opt.get("股票代码", "")).strip()
        name = str(opt.get("股票名称", "")).strip()
        stock = by_code.get(code)
        if not stock:
            rows.append({"code": code, "name": name, "missing": True})
            continue
        phase, note, _ = quantify_trend(stock, mw_cfg)
        ms, ms_detail = momentum_score(stock, mw_cfg)
        chg = quote_change_pct(stock)
        rows.append(
            {
                "code": code,
                "name": name,
                "phase": phase,
                "note": note,
                "momentum": round(ms, 1),
                "chg": chg,
                "opt_score": opt.get("评分"),
                "detail": ms_detail,
            }
        )

    rows.sort(
        key=lambda r: (
            -r.get("momentum", -999),
            -float(r.get("opt_score") or 0),
            r.get("code", ""),
        )
    )

    out_path = Path.home() / ".quant/state/watchlist_trend_rank.json"
    out_path.write_text(
        json.dumps({"source": str(src), "count": len(optional), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"自选 {len(optional)} 只 | 数据源: {src.name}")
    print(f"{'#':>3}  {'代码':<8} {'名称':<10} {'趋势':<6} {'动能':>5} {'涨幅%':>7} {'自选分':>6}  说明")
    print("-" * 96)
    for i, r in enumerate(rows, 1):
        if r.get("missing"):
            print(f"{i:>3}. {r['code']:<8} {r['name']:<10} —      —        —  无行情数据")
            continue
        chg_s = f"{r['chg']:.2f}" if r["chg"] is not None else "—"
        opt_s = f"{r['opt_score']:.1f}" if r.get("opt_score") is not None else "—"
        note = str(r["note"])[:36]
        print(
            f"{i:>3}. {r['code']:<8} {r['name']:<10} {r['phase']:<6} "
            f"{r['momentum']:>5.1f} {chg_s:>7} {opt_s:>6}  {note}"
        )
    print(f"\n完整结果已写入: {out_path}")


if __name__ == "__main__":
    main()
