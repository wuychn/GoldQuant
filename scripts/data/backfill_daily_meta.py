"""补缺离线库历史段缺失列：float_mv/total_mv（精确历史市值）+ pre_close（推导）。

背景：build_daily 的 ``stock_zh_a_hist`` 行天生缺 ``float_mv/total_mv/name/pre_close``；
update_daily 的 spot 行齐全但只有当天。本脚本在合并后的统一库上跑一次，把历史段
``float_mv/total_mv`` 用 ``stock_value_em``（东财估值分析，逐日历史）**精确**补上，
``pre_close`` 用 ``close[t-1]`` 推导（同 code 相邻行）。

**name 刻意不灌历史**：当前名灌历史 = ST 过滤前视（当年 ST 现已摘帽的票会被当前名
误剔 / 当年健康现已 ST 的被漏入）。PIT 名靠 ``update_daily`` 的 ``name_snapshot``
从今天起逐日积累。确需当前名兜底时用 ``--fill-name``（理解前视取舍后使用）。

幂等：只补缺失值（``float_mv/total_mv/pre_close`` 已有值的行跳过），可重跑。

用法：
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --codes 000001,600519
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --workers 2 --req-interval 1,3
    poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --fill-name
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

# 让 stock_value_em 的 requests 走 curl_cffi 统一层（东财 TLS 反爬）
from common.utils.source_headers import apply_source_header_patch

apply_source_header_patch()

from quant.data.schema import DAILY_RAW_COLUMNS  # noqa: E402
from quant.data.store import read_daily_raw, write_daily_raw  # noqa: E402
from quant.store.paths import override_quant_home  # noqa: E402

_MV_COLS = ("float_mv", "total_mv")


def _fetch_value_meta(code: str, *, interval: tuple[float, float]) -> dict[str, tuple[float, float]] | None:
    """拉单只历史市值 → {date: (float_mv, total_mv)}；失败返回 None（可重跑）。"""
    import akshare as ak

    if interval[1] > 0:
        time.sleep(interval[0] + (interval[1] - interval[0]) * ((time.time() * 1000) % 1000) / 1000)
    try:
        df = ak.stock_value_em(symbol=str(code).strip())
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] {code} stock_value_em 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if df is None or df.empty or "流通市值" not in df.columns:
        return None
    out: dict[str, tuple[float, float]] = {}
    for _, r in df.iterrows():
        d = str(r.get("数据日期"))
        if len(d) == 10 and d[4] == "-":
            out[d] = (float(r.get("流通市值")), float(r.get("总市值")))
    return out or None


def _fetch_code_names() -> dict[str, str]:
    """当前名表（仅 --fill-name 用；不灌历史，避免 ST 前视）。"""
    import akshare as ak

    try:
        df = ak.stock_info_a_code_name()
        return {
            str(r["code"]).strip(): str(r.get("name", "")).strip()
            for _, r in df.iterrows()
            if str(r.get("code", "")).strip() and str(r.get("name", "")).strip()
        }
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] stock_info_a_code_name 失败: {e}", file=sys.stderr)
        return {}


def _derive_pre_close(daily: pd.DataFrame) -> pd.DataFrame:
    """同 code 相邻行推导 pre_close = close[t-1]；只补 NaN。"""
    if "pre_close" not in daily.columns:
        daily["pre_close"] = pd.NA
    c = pd.to_numeric(daily["close"], errors="coerce")
    prev = daily.assign(__c=c).groupby("code", sort=False)["__c"].shift(1)
    missing = daily["pre_close"].isna() | (daily["pre_close"].astype(str) == "")
    daily.loc[missing, "pre_close"] = prev.loc[missing]
    return daily


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True, help="quant-home 根（直接含 store/ 的那级）")
    ap.add_argument("--codes", default=None, help="逗号分隔代码；默认补库内全部")
    ap.add_argument("--workers", type=int, default=1, help="并发（默认 1；datacenter 接口较稳，可适度升）")
    ap.add_argument("--req-interval", default=None, help="请求间隔秒 MIN,MAX 或 N（默认 0,2）")
    ap.add_argument("--fill-name", action="store_true", help="用当前名灌历史行（默认不灌，避免 ST 过滤前视）")
    args = ap.parse_args()

    if args.req_interval:
        parts = [p.strip() for p in args.req_interval.split(",") if p.strip()]
        interval = (float(parts[0]), float(parts[-1])) if parts else (0.0, 2.0)
    else:
        interval = (0.0, 2.0)

    home = Path(args.home).expanduser()
    with override_quant_home(home):
        daily = read_daily_raw()
    if daily.empty:
        print(f"[FATAL] {home} daily_raw 为空", file=sys.stderr)
        sys.exit(1)

    daily["code"] = daily["code"].astype(str).str.strip()
    codes = list(daily["code"].unique())
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    print(f"[backfill] home={home}  补 {len(codes)} 码市值（间隔 {interval[0]:.0f},{interval[1]:.0f}s）")

    # 1) 逐只拉精确历史市值
    mv_by_code: dict[str, dict[str, tuple[float, float]]] = {}
    t0 = time.time()
    done = fail = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(_fetch_value_meta, c, interval=interval): c for c in codes}
        for i, fut in enumerate(as_completed(futs), 1):
            code = futs[fut]
            try:
                mv = fut.result()
            except Exception:  # noqa: BLE001
                mv = None
            if mv:
                mv_by_code[code] = mv
                done += 1
            else:
                fail += 1
            if i % 200 == 0 or i == len(codes):
                print(f"  进度 {i}/{len(codes)}  ok={done} fail={fail}  {time.time() - t0:.0f}s")
    print(f"市值拉取: ok={done} fail={fail}（失败码可重跑本脚本补）")

    # 2) 合并 float_mv/total_mv（只补 NaN，幂等）
    rows: list[tuple] = []
    for code, m in mv_by_code.items():
        for d, (fm, tm) in m.items():
            rows.append((code, d, fm, tm))
    if rows:
        meta = pd.DataFrame(rows, columns=["code", "date", *list(_MV_COLS)])
        daily["date"] = daily["date"].astype(str)
        daily = daily.merge(meta, on=["code", "date"], how="left", suffixes=("", "_meta"))
        for c in _MV_COLS:
            daily[c] = daily[c].fillna(daily[f"{c}_meta"])
        daily = daily.drop(columns=[f"{c}_meta" for c in _MV_COLS])

    # 3) 推导 pre_close
    daily = _derive_pre_close(daily)

    # 4) 可选当前名（默认不灌历史）
    if args.fill_name:
        names = _fetch_code_names()
        daily["name"] = daily["code"].map(names).fillna(daily.get("name", ""))

    # 写回（按 DAILY_RAW_COLUMNS 归一并幂等分区写）
    daily = daily.reindex(columns=DAILY_RAW_COLUMNS)
    with override_quant_home(home):
        write_daily_raw(daily)

    # 覆盖报告
    n = len(daily)
    mv = daily["float_mv"].notna().sum()
    pc = daily["pre_close"].notna().sum()
    print(f"\n[backfill] 写回完成：{n:,} 行")
    print(f"[backfill] float_mv 非空 {mv:,}（{mv / max(n, 1):.1%}）· pre_close 非空 {pc:,}（{pc / max(n, 1):.1%}）")
    print(f"[backfill] 建议运行 validate：python -m scripts.data.validate_library --home {home}")


if __name__ == "__main__":
    main()
