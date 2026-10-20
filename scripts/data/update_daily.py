"""每日增量：spot 追加当日、除权检测、指数、涨停族。

调度器每日 16:00。建库和补漏由每周 ``maintain`` 跑。涨停池当天没抓就补不回来。

用法：
    python -m scripts.data.update_daily
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.adjust import detect_ex_dividend_codes, refresh_adj_for_codes
from quant.data.calendar import is_trading_day
from quant.data.fetch import fetch_index, fetch_spot_em, fetch_trade_calendar
from quant.data.store import read_daily_raw, write_calendar, write_daily_raw, write_index_daily
from quant.store.paths import quant_home
from scripts.cli_home import add_home_argument, home_context
from common.timeutil import cn_now

_SCOPE = "update_daily"


def _pending_path() -> Path:
    return quant_home() / "data" / "update_pending.json"


def _read_pending() -> dict:
    p = _pending_path()
    if not p.is_file():
        return {"indices": [], "industry": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"indices": [], "industry": False}


def _write_pending(pend: dict) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pend, ensure_ascii=False), encoding="utf-8")


def _update_indices(today: str, daily: pd.DataFrame, pend: list) -> None:
    """拉三个基准指数增量；失败的记入 ``pend``（调用方负责写盘）。

    ``start`` 取 index_daily 最新日（而非 daily_raw 最新日）：若指数多日拉取失败，
    daily_raw 会因 spot 增量继续前进，用 daily 最新日会漏掉中间缺失的指数缺口；
    基于 index_daily 自身最新日才能补全断档。
    """
    from quant.data.store import read_index_daily

    default_start = today
    try:
        cur = read_index_daily("000300")
        if not cur.empty:
            default_start = str(cur["date"].astype(str).max())
    except Exception:  # noqa: BLE001
        pass
    todo = sorted(
        set(
            (
                "000300",
                "000905",
                "000852",
                "000002",
                "399107",
                "399102",
                "399006",
                "000688",
            )
        )
        | set(pend)
    )
    failed: list[str] = []
    boot = (date.fromisoformat(today) - timedelta(days=180)).isoformat()
    for idx in todo:
        start = default_start
        try:
            cur_i = read_index_daily(idx)
            if cur_i is None or cur_i.empty:
                start = boot
            else:
                start = str(cur_i["date"].astype(str).max())
        except Exception:  # noqa: BLE001
            start = boot
        try:
            df = fetch_index(idx, start=start, end=today)
            write_index_daily(df)
            print(f"指数 {idx} 增量: {len(df)} 行")
        except Exception as e:
            print(f"[WARN] 指数 {idx} 增量失败（记 pending 下次重试）: {e}", file=sys.stderr)
            failed.append(idx)
    pend.clear()
    pend.extend(failed)


def _update_industry(today: str, pend: dict) -> None:
    """行业 PIT 快照；失败记 pending.industry，下次运行先重试。"""
    from quant.data.industry import fetch_current_industry_map, write_industry_snapshot

    try:
        ind_map = fetch_current_industry_map()
        if ind_map:
            write_industry_snapshot(today, ind_map)
            pend["industry"] = False
            print(f"行业快照: {len(ind_map)} 只 @ {today}")
        else:
            print("[WARN] 行业映射为空，跳过落库", file=sys.stderr)
            pend["industry"] = True
    except Exception as e:
        print(f"[WARN] 行业快照失败（记 pending 下次重试）: {e}", file=sys.stderr)
        pend["industry"] = True


def filter_valid_spot_bars(spot: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """丢掉无效盘口行，避免停牌/空报价把 close=0 写入 daily_raw。

    规则与 ``validate_library`` 价格 sanity 对齐：close>0、high>=low、
    high/low 夹住 open·close、volume/amount>=0。源站对停牌常返回全 0，
    当日无有效 K 则不落库（与 hist 缺日 / no_bar 语义一致）。
    """
    if spot is None or spot.empty:
        return spot, 0
    d = spot.copy()
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    need = ("open", "high", "low", "close")
    if any(c not in d.columns for c in need):
        return spot, 0
    ok = (
        d["close"].notna()
        & (d["close"] > 0)
        & d["high"].notna()
        & d["low"].notna()
        & d["open"].notna()
        & (d["high"] >= d["low"])
        & (d["high"] >= d[["open", "close"]].max(axis=1))
        & (d["low"] <= d[["open", "close"]].min(axis=1))
    )
    if "volume" in d.columns:
        ok &= d["volume"].isna() | (d["volume"] >= 0)
    if "amount" in d.columns:
        ok &= d["amount"].isna() | (d["amount"] >= 0)
    n_drop = int((~ok).sum())
    return d.loc[ok].copy(), n_drop


def _mark_suspended(spot: pd.DataFrame, today: str) -> tuple[int, int]:
    """用当日停复牌信息标记停牌票（volume 置 0，使 universe 停牌过滤生效）。

    返回 (停牌票数, 复牌首日盘口缺失数)。停牌票当日无成交 → volume 置 0；
    复牌票若 open/high/low 缺失，属盘口异常，记告警而非当停牌。
    停牌数据走 MarketSource.facade（可换源，默认东财 stock_tfp_em）。
    """
    try:
        from quant.data.sources.factory import get_market_source

        from quant.data.sources.interface import try_with_fallback
        stop_map = try_with_fallback("market", "fetch_stop_resume", date=today)
    except Exception as e:
        print(f"[WARN] 停复牌信息拉取失败（停牌识别跳过）: {e}", file=sys.stderr)
        return 0, 0
    if not stop_map:
        return 0, 0

    n_stop = 0
    n_resume_missing = 0
    for code, info in stop_map.items():
        hit = spot["code"].astype(str) == code
        if not hit.any():
            continue
        # 停牌：停牌截止未到（NaT=无截止）→ 当日无成交。注意 NaT 是 truthy，须用 pd.isna
        if info.get("停牌时间") and pd.isna(info.get("停牌截止时间")):
            spot.loc[hit, "volume"] = 0.0
            spot.loc[hit, "amount"] = 0.0
            n_stop += 1
        # 复牌首日（截止时间=当日或已过）：盘口 open/high/low 缺失属异常，告警
        elif spot.loc[hit, "open"].isna().any() or spot.loc[hit, "high"].isna().any():
            n_resume_missing += 1
    return n_stop, n_resume_missing


def _latest_date(daily: pd.DataFrame) -> str | None:
    if daily.empty:
        return None
    return str(daily["date"].max())


def _prev_close_map(daily: pd.DataFrame, as_of: str) -> dict[str, float]:
    """as_of 之前最近一日的 {code: close}。"""
    if daily.empty:
        return {}
    prev = daily[daily["date"] < as_of]
    if prev.empty:
        return {}
    last = prev["date"].max()
    d = prev[prev["date"] == last]
    return dict(zip(d["code"].astype(str), pd.to_numeric(d["close"], errors="coerce")))


def merge_today_bars(daily: pd.DataFrame, today_bars: pd.DataFrame, today: str) -> pd.DataFrame:
    """把刚写入的当日 K 并进内存 daily，供 universe / listing 使用。

    ``update_daily`` 必须先 ``read_daily_raw`` 才能做除权检测，写盘发生在那之后；
    若把写盘前的表直接交给 ``universe_snapshot``，当日 0 行会回退写成上一交易日。
    """
    t = str(today)[:10]
    if today_bars is None or today_bars.empty:
        return daily
    bars = today_bars.copy()
    bars["date"] = t
    if daily is None or daily.empty:
        return bars.reset_index(drop=True)
    keep = daily["date"].astype(str).str[:10] != t
    return pd.concat([daily.loc[keep], bars], ignore_index=True)


def detect_ex_and_align_pre_close(spot: pd.DataFrame, prev_map: dict[str, float]) -> list[str]:
    """用源站原始昨收做除权检测，再把 ``spot.pre_close`` 对齐到库内 T-1 close。

    顺序不可反：若先覆盖再检测，两边同源，已有库内票永远检不出除权。
    就地修改 ``spot``；返回除权代码列表。
    """
    ex_codes = detect_ex_dividend_codes(spot, prev_map)
    spot["pre_close"] = spot["code"].map(prev_map).fillna(spot["pre_close"])
    return ex_codes


def main() -> None:
    ap = argparse.ArgumentParser(description="每日增量：spot 追加当日 + 除权检测 + 因子/指数/行业快照")
    add_home_argument(ap)
    ap.add_argument("--date", default=None, help="指定交易日 YYYY-MM-DD，默认今天（非交易日自动跳过）")
    ap.add_argument(
        "--closed-session",
        action="store_true",
        help="写入最近已收盘交易日（16:00 前用上一交易日），供开机补跑，避免盘中 spot 当收盘日线",
    )
    args = ap.parse_args()

    with home_context(args.home):
        if args.date:
            today = args.date
        elif args.closed_session:
            from quant.data.calendar import closed_session_as_of

            closed = closed_session_as_of()
            if closed is None:
                log_progress(_SCOPE, "无已收盘交易日，跳过")
                log_progress_done(_SCOPE, "成功", detail="skipped no closed session")
                return
            today = closed.isoformat()
            print(f"closed-session as_of={today}", flush=True)
        else:
            today = cn_now().strftime("%Y-%m-%d")
        if not is_trading_day(__import__("datetime").date.fromisoformat(today)):
            log_progress(_SCOPE, "非交易日，跳过", detail=today)
            log_progress_done(_SCOPE, "成功", detail="skipped non-trading day")
            return

        log_progress_start(_SCOPE, "开始", detail=today)

        # 1. 全市场当日 spot（经 DailySource facade；默认新浪直连 20s 全量含市值，东财 spot_em
        #    走 clist 58 页易断——换源只改 quant.yml data.sources.daily）
        try:
            log_progress(_SCOPE, "拉取 spot …")
            spot = fetch_spot_em()
        except Exception as e:
            log_progress_error(_SCOPE, "spot 拉取失败", detail=f"{type(e).__name__}: {e}")
            sys.exit(2)

        spot["date"] = today
        # 前缀过滤：与 build_daily 一致，排除北交所/三板/B股（历史段无、当天也不该有）
        from quant.config import load_quant_config

        prefixes = (
            (load_quant_config().get("gates") or {}).get("symbol_pool", {}).get("prefixes", ["60", "00", "30", "688"])
        )
        before = len(spot)
        spot = spot[spot["code"].astype(str).str.strip().str.startswith(tuple(prefixes))]
        if len(spot) < before:
            print(f"前缀过滤: {before} → {len(spot)} 只（保留 {prefixes}）")
        # 先用源站原始昨收做除权检测，再把 pre_close 对齐到库内 T-1 close 写库
        # （保证 daily_raw 不复权序列连续；检测不可用对齐后的值，否则永远无除权）。
        daily = read_daily_raw(end=today)
        prev_map = _prev_close_map(daily, today)
        ex_codes = detect_ex_and_align_pre_close(spot, prev_map)
        # 无效盘口（停牌全 0 / high 不夹等）不落库，避免污染 daily_raw 与复权跳空校验
        spot, n_bad_bars = filter_valid_spot_bars(spot)
        if n_bad_bars:
            print(f"丢弃无效盘口: {n_bad_bars} 只（close<=0 或 OHLC 不自洽）", file=sys.stderr)
        # 停牌 volume 置 0 必须在写库前，否则 universe 读不到停牌标记
        n_stop, n_resume = _mark_suspended(spot, today)
        if n_stop or n_resume:
            print(f"停牌 {n_stop} 只（volume 置 0）· 复牌盘口缺失 {n_resume} 只", file=sys.stderr)
        # 仅保留 daily_raw 列
        cols = ["code", "date", "name", "open", "high", "low", "close", "pre_close",
                "volume", "amount", "turnover_rate", "float_mv", "total_mv"]
        spot_out = spot[[c for c in cols if c in spot.columns]]
        write_daily_raw(spot_out)
        print(f"spot 追加: {len(spot_out)} 行 @ {today}")
        daily = merge_today_bars(daily, spot_out, today)

        # 1b. PIT 名称快照（供 universe ST/退市过滤、涨跌停 ST 分档）
        #     spot 的 name 是当日真实名（PIT），落库后 universe 可按日取，避免依赖
        #     daily_raw.name（历史常缺失或为查询当下的当前名）。
        try:
            from quant.data.store import write_name_snapshot

            code_name = {
                str(r.get("code", "")).strip(): str(r.get("name", "")).strip()
                for _, r in spot.iterrows()
                if str(r.get("code", "")).strip() and str(r.get("name", "")).strip()
            }
            write_name_snapshot(today, code_name)
            print(f"name 快照: {len(code_name)} 只 @ {today}")
        except Exception as e:
            print(f"[WARN] name 快照失败: {e}", file=sys.stderr)

        # 2. 除权日补拉复权因子（检测已在写库前完成）
        if ex_codes:
            print(f"检测到除权 {len(ex_codes)} 只，补拉复权因子: {ex_codes[:10]}{'...' if len(ex_codes)>10 else ''}")
            n = refresh_adj_for_codes(ex_codes)
            print(f"复权因子更新 {n} 条")
        else:
            print("无除权")

        # 3. 指数增量（三个基准指数，与 build_daily 一致）——失败即时记 pending，下次运行先重试
        pend = _read_pending()
        _update_indices(today, daily, pend.get("indices", []))
        _write_pending(pend)  # 即时落盘，避免后续步骤（因子快照等）慢/卡导致丢失

        # 4. 交易日历刷新（低频）
        try:
            cal = fetch_trade_calendar()
            write_calendar(cal)
            log_progress(_SCOPE, "交易日历已刷新", detail=f"{len(cal)} 天")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 交易日历刷新失败: {e}", file=sys.stderr, flush=True)

        # 5. 行业 PIT 快照（供中性化 / 组合约束）——失败即时记 pending，下次运行先重试
        _update_industry(today, pend)
        _write_pending(pend)  # 即时落盘

        # 6. Universe PIT 快照
        try:
            from quant.data.universe import universe_snapshot

            snap = universe_snapshot(today, rebuild=True, daily=daily)
            n_inc = int(snap["included"].sum()) if not snap.empty and "included" in snap.columns else 0
            print(f"universe 快照: {n_inc} 只纳入 / {len(snap)} 行 @ {today}")
        except Exception as e:
            print(f"[WARN] universe 快照失败: {e}", file=sys.stderr)

        # 7. 上市日表（jbxx + daily 首条，供 universe PIT）
        try:
            from quant.data.listing import build_listing_map, write_listing_table

            mp = build_listing_map(daily)
            write_listing_table(mp)
            print(f"listing_dates 更新: {len(mp)} 只")
        except Exception as e:
            print(f"[WARN] listing_dates 失败: {e}", file=sys.stderr)


        try:
            from quant.data.session_archive import capture_session_extras

            cap = capture_session_extras(today)
            print(f"涨停族 @ {today}: {cap}", flush=True)
        except Exception as e:
            print(f"[WARN] 涨停族失败: {e}", file=sys.stderr)

        # 指数/行业失败待重试，下次运行开头自动补
        _write_pending(pend)
        n_pend = len(pend.get("indices", [])) + (1 if pend.get("industry") else 0)
        if n_pend:
            print(
                f"[INFO] 待重试 {n_pend} 项（指数 {pend.get('indices')}，行业 "
                f"{pend.get('industry')}）→ 下次 update_daily 自动补",
                file=sys.stderr,
            )
        log_progress_done(_SCOPE, "成功", detail=today)


if __name__ == "__main__":
    main()
