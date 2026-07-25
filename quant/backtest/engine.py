"""回测引擎：按日重放 daily/raw 盘中快照（与实盘三确认一致）。"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from quant.backtest.broker import BrokerConfig, SimBroker
from quant.backtest.metrics import compute_metrics
from quant.scoring.context import ScoreContext
from quant.signals.pipeline import generate_confirmed_signals
from quant.store.paths import QUANT_HOME
from quant.timeutil import CN_TZ, cn_now


def _list_trading_dates(from_date: str | None, to_date: str | None) -> list[str]:
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]
    return dates


def _snapshot_datetime(fname: str, date_str: str) -> datetime:
    """during_HHMM.json + 当日日期 -> CN_TZ 时刻；解析失败回退 15:00。"""
    m = re.search(r"(\d{4})", fname)
    hh, mm = (int(m.group(1)[:2]), int(m.group(1)[2:])) if m else (15, 0)
    try:
        y, mo, d = (int(x) for x in date_str.split("-"))
        return datetime(y, mo, d, hh, mm, tzinfo=CN_TZ)
    except Exception:
        return cn_now()


def _load_during_payloads(day_dir: Path) -> list[tuple[datetime, dict]]:
    """返回 [(快照时刻, payload), ...]，按文件名升序。"""
    raw = day_dir / "raw"
    if not raw.is_dir():
        return []
    files = sorted(raw.glob("during*.json"))
    out: list[tuple[datetime, dict]] = []
    date_str = day_dir.name
    for fp in files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        inner = obj.get("data")
        data = inner if isinstance(inner, dict) else obj
        out.append((_snapshot_datetime(fp.name, date_str), data))
    return out


def _stock_map(payload: dict) -> dict[str, dict]:
    m: dict[str, dict] = {}
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                code = str(row.get("股票代码", "")).strip()
                if code:
                    m[code] = row
    return m


# payload 中承载个股行情的键；用于把 mem/broker 里的 thin 行合并到快照 rich 行上
PAYLOAD_STOCK_KEYS = (
    "自选股",
    "持仓股",
    "同花顺人气榜",
    "涨停候选",
    "创新高",
    "持续上涨",
    "持续放量",
    "量价齐升",
    "_observe_enriched",
)

# 自选行里由晚间流程产生的元数据字段；合并时覆盖到 rich 行上，其余字段保留 rich 行
WATCHLIST_META_KEYS = (
    "评分",
    "动能分",
    "战法",
    "加入自选原因",
    "榜单标签",
    "最后入选日期",
    "未达标连续天数",
)


def index_payload_stocks(payload: dict) -> dict[str, dict]:
    """按代码索引 payload 中所有承载行情的 rich 个股行。"""
    from app.utils.common_util import extract_stock_code

    out: dict[str, dict] = {}
    for key in PAYLOAD_STOCK_KEYS:
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = extract_stock_code(row)
            if code and code not in out:
                out[code] = row
    return out


def enrich_holdings_with_payload(
    holdings_rows: list[dict], payload: dict
) -> list[dict]:
    """把持仓 thin 行合并到 payload 同代码 rich 行上，保留持仓元数据。

    broker/mem 持仓行只有 买入价/持仓股数/买入类型 等元数据，缺 历史行情/盘口/价格，
    而 sell.py 依赖 payload['持仓股'] 提供行情来算止损/止盈/趋势破位。
    不合并会导致回测中卖出信号拿不到价格、全部失效。
    """
    from app.utils.common_util import extract_stock_code

    rich_by_code = index_payload_stocks(payload)
    out: list[dict] = []
    for h in holdings_rows:
        code = extract_stock_code(h)
        rich = rich_by_code.get(code) if code else None
        if rich is None:
            out.append(dict(h))
            continue
        base = dict(rich)
        for k in (
            "买入价",
            "买入时间",
            "买入类型",
            "买入原因",
            "买入日期",
            "战法",
            "持仓股数",
            "股票代码",
            "股票名称",
        ):
            if k in h:
                base[k] = h[k]
        out.append(base)
    return out


@contextmanager
def _patch_runtime_state(
    broker: SimBroker,
    pending_box: list[dict],
    clock: list[datetime],
) -> Iterator[None]:
    """隔离回测运行时：持仓/现金指向 broker，三确认走内存 + 快照时间。

    - pending_box：跨快照/跨日共享的内存 pending（等价实盘 signal_pending.json，
      但不读写真实文件，避免污染实盘状态）。
    - clock：快照时刻；三确认的 _now / 14:30 判定均以此为准，而非墙钟。
    """
    import quant.signals.confirmation as confirmation

    def _codes_sold_today(date_str: str | None = None) -> set[str]:
        del date_str
        return set(broker.sold_today)

    def _holding_codes_bought_today(holdings=None) -> set[str]:
        del holdings
        return set(broker.bought_today)

    def _mem_load_pending() -> dict:
        return dict(pending_box[0])

    def _mem_save_pending(pending: dict) -> None:
        pending_box[0] = dict(pending)

    def _sim_now():
        return clock[0]

    def _sim_late_session() -> bool:
        from quant.trading_hours import is_at_or_after_hhmm, late_session_cutoff

        return is_at_or_after_hhmm(late_session_cutoff(), clock[0])

    def _sim_auction_window() -> bool:
        from quant.trading_hours import is_a_share_continuous_auction_window

        return is_a_share_continuous_auction_window(clock[0])

    with (
        patch("quant.signals.buy.get_holdings", broker.holdings_rows),
        patch("quant.signals.sell.get_holdings", broker.holdings_rows),
        patch("quant.gates.rules.get_holdings", broker.holdings_rows),
        patch("quant.gates.rules.get_cash", lambda: broker.cash),
        patch("quant.store.state.get_holdings", broker.holdings_rows),
        patch("quant.store.state.get_cash", lambda: broker.cash),
        patch("quant.signals.pipeline.codes_sold_today", _codes_sold_today),
        patch("quant.signals.pipeline.holding_codes_bought_today", _holding_codes_bought_today),
        patch("quant.gates.rules.codes_sold_today", _codes_sold_today),
        patch.object(confirmation, "_now", _sim_now),
        patch.object(confirmation, "load_pending", _mem_load_pending),
        patch.object(confirmation, "save_pending", _mem_save_pending),
        patch("quant.execution.core.is_late_session_for_trend_sell", _sim_late_session),
        patch("quant.trading_hours.is_a_share_continuous_auction_window", _sim_auction_window),
    ):
        yield


def _make_close_provider(start_yyyymmdd: str | None, end_yyyymmdd: str | None):
    """构建带缓存的独立收盘价取价器 (code, YYYY-MM-DD) -> 收盘价。

    持仓掉出当日自选/持仓快照时，用 akshare 日线真实收盘价估值，
    避免 mark_to_market 回退到买入价导致权益失真。每只 code 只拉一次。
    网络失败返回 None（回退买入价），不改变原有最坏行为。
    """
    from app.utils.dfcf_util import hist

    cache: dict[str, dict[str, float]] = {}

    def _row_close(row: dict) -> float | None:
        for k in ("收盘", "close", "收盘价"):
            v = row.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return None

    def _get(code: str, date_str: str) -> float | None:
        if code not in cache:
            try:
                rows = hist(
                    code,
                    period="daily",
                    start_date=start_yyyymmdd,
                    end_date=end_yyyymmdd,
                    adjust="",  # 回测估值用不复权，与买入价同基准
                )
            except Exception:
                rows = None
            closes: dict[str, float] = {}
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                d = str(r.get("日期") or r.get("date") or "")[:10]
                c = _row_close(r)
                if d and c:
                    closes[d] = c
            cache[code] = closes
        return cache[code].get(date_str[:10])

    return _get


def run_backtest(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    broker_cfg: BrokerConfig | None = None,
    mode: str = "full_system",
) -> dict:
    """回测入口。

    mode:
      - full_system: 端到端（晚间自选 + 盘中成交）
      - intraday_replay: 仅重放 during 快照（legacy）
    """
    if mode == "intraday_replay":
        return _run_intraday_replay(
            from_date=from_date, to_date=to_date, broker_cfg=broker_cfg
        )
    from quant.backtest.simulator import run_full_system_backtest

    return run_full_system_backtest(
        from_date=from_date, to_date=to_date, broker_cfg=broker_cfg
    )


def _run_intraday_replay(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    broker_cfg: BrokerConfig | None = None,
) -> dict:
    """重放 daily/raw 下 during*.json，经与实盘一致的三确认 pipeline 后成交。"""
    dates = _list_trading_dates(from_date, to_date)
    start = dates[0].replace("-", "") if dates else None
    end = dates[-1].replace("-", "") if dates else None
    price_provider = _make_close_provider(start, end) if dates else None
    broker = SimBroker(
        cfg=broker_cfg or BrokerConfig(),
        price_provider=price_provider,
    )
    pending_box: list[dict] = [{}]  # 三确认内存状态，跨快照/跨日共享，每回测独立
    clock: list[datetime] = [cn_now()]
    days_run = 0

    for d in dates:
        snapshots = _load_during_payloads(QUANT_HOME / "daily" / d)
        if not snapshots:
            continue
        broker.reset_daily(d)
        days_run += 1
        last_payload = snapshots[-1][1]

        with _patch_runtime_state(broker, pending_box, clock):
            for snap_dt, payload in snapshots:
                clock[0] = snap_dt
                payload = dict(payload)
                payload["持仓股"] = enrich_holdings_with_payload(
                    broker.holdings_rows(), payload
                )
                ctx = ScoreContext.from_payload(payload, mode="during_market")
                stock_by_code = _stock_map(payload)

                # 与实盘一致：原始信号 -> 同日防翻转 -> 三确认 -> 可执行
                _raw_buy, _raw_sell, executable, _audit = generate_confirmed_signals(
                    ctx, mode="during_market"
                )
                # executable = 先卖后买（exec_sell + exec_buy），按序撮合
                for sig in executable:
                    if sig.action == "卖出":
                        broker.try_sell(sig, stock_by_code.get(sig.code, {}))
                    else:
                        broker.try_buy(sig, stock_by_code.get(sig.code, {}))

        broker.mark_to_market(last_payload)

    from quant.research.metrics.performance import compute_extended_metrics

    metrics = compute_extended_metrics(broker, trading_days=days_run)
    metrics["days_run"] = days_run
    metrics["date_from"] = dates[0] if dates else ""
    metrics["date_to"] = dates[-1] if dates else ""
    metrics["mode"] = "intraday_replay"
    return metrics
