"""对比「分页资金流排名」与「逐票 daykline 近 5 日加总」口径。

默认把运行时数据写到 ``D:/ProgramData/.quant_tmp``（可用 ``--home`` 覆盖），
**不会**改写 ``.quant`` / ``.quant2`` / ``.quant_offline``。

用法：
    poetry run python -m scripts.data.compare_fund_flow_rank
    poetry run python -m scripts.data.compare_fund_flow_rank --sample 30 --page-interval 0.5
    poetry run python -m scripts.data.compare_fund_flow_rank --home D:/ProgramData/.quant_tmp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.factor_capture import _sum_flow_5d
from quant.store.paths import quant_home
from scripts.cli_home import home_context

_DEFAULT_HOME = Path("D:/ProgramData/.quant_tmp")


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 3:
        return float("nan")
    sa = pd.Series(a).rank()
    sb = pd.Series(b).rank()
    return float(sa.corr(sb))


def main() -> int:
    ap = argparse.ArgumentParser(description="fund_flow rank vs 逐票 5 日加总口径对比")
    ap.add_argument(
        "--home",
        default=str(_DEFAULT_HOME),
        help=f"运行时数据根（默认 {_DEFAULT_HOME}）",
    )
    ap.add_argument("--sample", type=int, default=30, help="抽样对比只数（默认 30）")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--page-interval", type=float, default=10.0, help="页间休眠秒（下限 10）")
    ap.add_argument("--force-rank", action="store_true", help="忽略 rank 断点重拉")
    ap.add_argument(
        "--cache-only",
        action="store_true",
        help="只用已断点缓存的页做对比，不再打网拉 rank（适合限流中断后核对）",
    )
    ap.add_argument(
        "--as-of",
        default=None,
        help="断点键 / 报告日期，默认今天",
    )
    args = ap.parse_args()

    from common.timeutil import cn_now

    as_of = args.as_of or cn_now().strftime("%Y-%m-%d")

    with home_context(args.home):
        home = quant_home()
        print(f"QUANT_HOME={home}（对比数据仅写入此目录）", flush=True)
        if home.resolve() in {
            Path("D:/ProgramData/.quant").resolve(),
            Path("D:/ProgramData/.quant2").resolve(),
            Path("D:/ProgramData/.quant_offline").resolve(),
        }:
            print(
                "[ERROR] 拒绝写入生产 home；请用 D:/ProgramData/.quant_tmp",
                file=sys.stderr,
            )
            return 2

        from common.utils.source_headers import apply_source_header_patch, set_eastmoney_interval
        from quant.data.sources.interface import try_with_fallback_async
        import asyncio
        import time

        apply_source_header_patch()
        # 逐票 daykline 也走东财，拉高间隔避免与 rank 分页叠加封连
        set_eastmoney_interval(10, 12)

        if args.cache_only:
            from quant.data.sources.eastmoney import fund_flow_rank as ffr

            print("1) 读取已缓存 rank 页…", flush=True)
            meta = ffr._read_meta(as_of, "5日")
            last = int(meta.get("last_page") or 0)
            frames = ffr._load_page_frames(as_of, "5日", last) if last else []
            if not frames:
                print("[ERROR] 无缓存页；请先跑不含 --cache-only 的拉取", file=sys.stderr)
                return 2
            rank = pd.concat(frames, ignore_index=True).drop_duplicates(
                subset=["code"], keep="last"
            )
            print(f"   缓存页={last} 行数={len(rank)}", flush=True)
        else:
            from quant.data.tools.market import fetch_stock_fund_flow_rank

            print("1) 拉取分页 rank（5日）…", flush=True)
            try:
                rank = fetch_stock_fund_flow_rank(
                    indicator="5日",
                    as_of=as_of,
                    page_size=args.page_size,
                    page_interval=args.page_interval,
                    force=args.force_rank,
                )
            except Exception as exc:  # noqa: BLE001
                err_path = home / "reports" / "fund_flow_compare" / f"{as_of}_error.txt"
                err_path.parent.mkdir(parents=True, exist_ok=True)
                err_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
                print(f"[ERROR] rank 拉取失败: {exc}", file=sys.stderr)
                print(f"已写入 {err_path}", file=sys.stderr)
                print(
                    "提示: 已有断点页可用 --cache-only 先做抽样对比",
                    file=sys.stderr,
                )
                return 2
            if rank is None or rank.empty:
                print("[ERROR] rank 为空", file=sys.stderr)
                return 2
            print(f"   rank 行数={len(rank)}", flush=True)

        # 抽样：按 |净额| 分层（大/中/小）
        rank = rank.copy()
        rank["abs_net"] = rank["main_net_inflow"].abs()
        rank = rank.sort_values("abs_net", ascending=False)
        n = max(3, int(args.sample))
        idx = np.linspace(0, len(rank) - 1, num=min(n, len(rank)), dtype=int)
        sample = rank.iloc[sorted(set(idx.tolist()))]
        codes = [str(c) for c in sample["code"].tolist()]
        print(f"2) 逐票核对 {len(codes)} 只 …", flush=True)

        async def _one(code: str):
            try:
                recs = await try_with_fallback_async(
                    "enrich", "fetch_stock_fund_flow_daily", symbol=code, days=10
                )
                return code, _sum_flow_5d(recs)
            except Exception as exc:  # noqa: BLE001
                return code, None

        async def _all():
            out = []
            gap = max(10.0, float(args.page_interval))
            for i, c in enumerate(codes):
                if i > 0:
                    print(f"   逐票间隔 {gap:.0f}s …", flush=True)
                    await asyncio.sleep(gap)
                print(f"   逐票 {i + 1}/{len(codes)} {c}", flush=True)
                out.append(await _one(c))
            return out

        pairs = asyncio.run(_all())
        rank_map = {
            str(r["code"]): float(r["main_net_inflow"]) for _, r in sample.iterrows()
        }

        rows = []
        for code, per in pairs:
            rk = rank_map.get(code)
            if rk is None or per is None:
                rows.append(
                    {
                        "code": code,
                        "rank_5d": rk,
                        "per_symbol_5d": per,
                        "abs_diff": None,
                        "rel_diff": None,
                        "status": "missing",
                    }
                )
                continue
            abs_diff = abs(rk - per)
            denom = max(abs(rk), abs(per), 1.0)
            rel = abs_diff / denom
            rows.append(
                {
                    "code": code,
                    "rank_5d": rk,
                    "per_symbol_5d": per,
                    "abs_diff": abs_diff,
                    "rel_diff": rel,
                    "status": "ok",
                }
            )

        df = pd.DataFrame(rows)
        ok = df[df["status"] == "ok"]
        report = {
            "as_of": as_of,
            "home": str(home),
            "rank_rows": int(len(rank)),
            "sample_n": int(len(df)),
            "compared_n": int(len(ok)),
            "missing_n": int((df["status"] == "missing").sum()),
            "median_rel_diff": float(ok["rel_diff"].median()) if len(ok) else None,
            "p95_rel_diff": float(ok["rel_diff"].quantile(0.95)) if len(ok) else None,
            "max_rel_diff": float(ok["rel_diff"].max()) if len(ok) else None,
            "spearman": _spearman(
                ok["rank_5d"].tolist(), ok["per_symbol_5d"].tolist()
            )
            if len(ok)
            else None,
            "pass_median_lt_2pct": bool(ok["rel_diff"].median() < 0.02)
            if len(ok)
            else False,
            "pass_spearman_gt_0_95": bool(
                _spearman(ok["rank_5d"].tolist(), ok["per_symbol_5d"].tolist()) > 0.95
            )
            if len(ok) >= 3
            else False,
        }

        out_dir = home / "reports" / "fund_flow_compare"
        out_dir.mkdir(parents=True, exist_ok=True)
        detail_path = out_dir / f"{as_of}_detail.csv"
        summary_path = out_dir / f"{as_of}_summary.json"
        df.to_csv(detail_path, index=False, encoding="utf-8-sig")
        summary_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        print(f"明细: {detail_path}", flush=True)
        print(f"摘要: {summary_path}", flush=True)

        if report["compared_n"] < 5:
            print("[WARN] 可对比样本过少", file=sys.stderr)
            return 1
        if not report["pass_median_lt_2pct"] or not report["pass_spearman_gt_0_95"]:
            print("[FAIL] 口径未达门槛（median_rel<2% 且 spearman>0.95）", file=sys.stderr)
            return 1
        print("[PASS] 口径对齐通过", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
