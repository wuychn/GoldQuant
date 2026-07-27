"""日决策纸面撮合：DecisionCard → TradeSignal → 写 paper 账户 state。

与历史回测（backtest2）分离：用 execution 成本规则，持久化到
``$QUANT_HOME/paper_account/``，不污染实盘/人工持仓。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from quant.backtest2.tradability import shares_for_amount
from quant.decision.daily_output import DecisionCard
from quant.execution.executor import execute_signals
from quant.signals.models import TradeSignal
from quant.store.paths import (
    battle_pool_file,
    ensure_layout,
    override_quant_home,
    quant_home,
    sell_watch_file,
    state_file,
)
from quant.timeutil import cn_datetime_str
from quant.store.state import (
    get_account,
    get_holdings,
    get_total_assets,
    refresh_account_market_value,
    save_holdings,
)


@contextmanager
def paper_home_context() -> Iterator[Path]:
    """把 state 根切到 ``{quant_home}/paper_account``，与人工仓隔离。"""
    base = quant_home()
    # 若已在 paper 根下则不再嵌套
    if base.name == "paper_account":
        ensure_layout()
        yield base
        return
    paper = base / "paper_account"
    paper.mkdir(parents=True, exist_ok=True)
    with override_quant_home(paper):
        ensure_layout()
        yield paper


def _holding_qty(holdings: list[dict], code: str) -> int:
    for h in holdings:
        if str(h.get("股票代码") or "").strip() != code:
            continue
        try:
            return int(float(h.get("持仓股数") or h.get("股数") or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def _detail_reason(a, *, alpha_map: dict[str, float], rank_map: dict[str, int]) -> str:
    """细化买入/调仓原因：动作|排名|alpha|目标权重|权重差|业务原因。"""
    rk = rank_map.get(a.code)
    al = alpha_map.get(a.code)
    parts = [
        a.side,
        f"rank={rk}" if rk else None,
        f"α={al:.3f}" if al is not None else None,
        f"tw={a.target_weight:.1%}",
        f"Δw={a.delta_weight:+.1%}",
        a.reason or None,
    ]
    return "|".join(p for p in parts if p)


def actions_to_signals(
    card: DecisionCard,
    *,
    prices: dict[str, float],
    names: dict[str, str],
    total_assets: float,
    holdings: list[dict],
    strategy: str = "r3",
) -> list[TradeSignal]:
    """权重差 → 整手股数信号。卖出优先排序由 executor 保证。"""
    exit_codes = {str(s.get("code") or "") for s in (card.exit_signals or [])}
    signals: list[TradeSignal] = []
    assets = max(float(total_assets), 1.0)
    alpha_map = {c: float(v) for c, v in (card.alpha_top or [])}
    # 全市场排名：用 alpha_top 顺序；不足时仅标 top 内名次
    rank_map = {c: i + 1 for i, (c, _) in enumerate(card.alpha_top or [])}

    for a in card.actions:
        if a.side == "hold":
            continue
        price = float(prices.get(a.code) or 0)
        if price <= 0:
            continue
        name = names.get(a.code) or a.code
        held_q = _holding_qty(holdings, a.code)
        detail = _detail_reason(a, alpha_map=alpha_map, rank_map=rank_map)

        # 出场信号：全平
        if a.code in exit_codes and held_q > 0:
            reason = next(
                (str(s.get("reason") or "exit") for s in card.exit_signals if s.get("code") == a.code),
                "exit",
            )
            signals.append(
                TradeSignal(
                    action="卖出",
                    code=a.code,
                    name=name,
                    price=price,
                    quantity=held_q,
                    strategy=strategy,
                    reason=f"exit:{reason}|{detail}",
                    sell_type="止损",
                    signal_kind=reason,
                )
            )
            continue

        if a.side in ("sell", "reduce") and a.delta_weight < 0:
            if held_q <= 0:
                continue
            if a.side == "sell" or a.target_weight <= 1e-9:
                qty = held_q
            else:
                excess = abs(a.delta_weight) * assets
                qty = min(held_q, shares_for_amount(price, excess))
            if qty <= 0:
                continue
            signals.append(
                TradeSignal(
                    action="卖出",
                    code=a.code,
                    name=name,
                    price=price,
                    quantity=qty,
                    strategy=strategy,
                    reason=detail,
                    sell_type="再平衡",
                    signal_kind=a.side,
                )
            )
        elif a.side in ("buy", "add") and a.delta_weight > 0:
            excess = a.delta_weight * assets
            qty = shares_for_amount(price, excess)
            if qty <= 0:
                continue
            signals.append(
                TradeSignal(
                    action="买入",
                    code=a.code,
                    name=name,
                    price=price,
                    quantity=qty,
                    strategy=strategy,
                    reason=detail,
                    signal_kind=a.side,
                )
            )
    return signals


def build_daily_payload(
    daily: pd.DataFrame,
    as_of: str,
    *,
    holdings: list[dict] | None = None,
) -> dict:
    """构造 executor 用的行情 payload（盘口字段供涨跌停判定）。"""
    day = daily[daily["date"].astype(str).str.slice(0, 10) == as_of] if not daily.empty else daily
    rows: list[dict] = []
    for _, r in day.iterrows():
        code = str(r["code"])
        close = float(r["close"])
        try:
            pre = float(r.get("pre_close") or close)
        except (TypeError, ValueError):
            pre = close
        if not pre or pre != pre or pre <= 0:
            pre = close
        name = str(r.get("name") or code)
        chg = (close / pre - 1.0) * 100 if pre > 0 else 0.0
        rows.append(
            {
                "股票代码": code,
                "股票名称": name,
                "盘口": {
                    "最新": close,
                    "最新价": close,
                    "今开": float(r.get("open") or close),
                    "昨收": pre,
                    "涨跌幅": chg,
                },
            }
        )
    # 持仓行叠加现价，便于估值
    held_rows = []
    price_by = {str(x["股票代码"]): x for x in rows}
    for h in holdings or []:
        code = str(h.get("股票代码") or "").strip()
        if not code:
            continue
        base = dict(h)
        if code in price_by:
            base["盘口"] = price_by[code]["盘口"]
            base["股票名称"] = price_by[code].get("股票名称") or base.get("股票名称") or code
        held_rows.append(base)
    return {"自选股": rows, "持仓股": held_rows or list(holdings or [])}


def _stamp_holding_quotes(prices: dict[str, float], names: dict[str, str]) -> None:
    """成交后把最新价写回持仓盘口，便于市值刷新。"""
    holdings = get_holdings()
    changed = False
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        px = prices.get(code)
        if px is None or px <= 0:
            continue
        pk = h.get("盘口") if isinstance(h.get("盘口"), dict) else {}
        pk = dict(pk)
        pk["最新"] = px
        pk["最新价"] = px
        h["盘口"] = pk
        if names.get(code):
            h["股票名称"] = names[code]
        changed = True
    if changed:
        save_holdings(holdings)


def append_paper_equity(as_of: str, account: dict[str, Any], *, n_trades: int = 0) -> Path:
    """追加纸面权益曲线一行。"""
    path = state_file("equity.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "date": as_of,
        "总资产": account.get("总资产"),
        "可用资金": account.get("可用资金"),
        "持仓市值": account.get("持仓市值"),
        "累计已实现盈亏": account.get("累计已实现盈亏"),
        "n_holdings": len(get_holdings()),
        "n_trades": n_trades,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


# ---------- 作战池（T 晚选 → T+1 盘中择时） ----------


def write_battle_pool(target_date: str, pool: list[dict]) -> Path:
    """落盘作战池：``paper_account/battle_pool/{target_date}.json``。"""
    path = battle_pool_file(target_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"date": target_date, "generated_at": cn_datetime_str(), "pool": pool}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_battle_pool(target_date: str) -> list[dict] | None:
    """读作战池；不存在返回 None。"""
    path = battle_pool_file(target_date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("pool") or []


def clear_battle_pool(target_date: str) -> None:
    """删作战池（执行后清除，幂等）。"""
    path = battle_pool_file(target_date)
    if path.is_file():
        path.unlink()


def write_sell_watch(target_date: str, rows: list[dict]) -> Path:
    """落盘卖出监控清单：``paper_account/sell_watch/{target_date}.json``。"""
    path = sell_watch_file(target_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"date": target_date, "generated_at": cn_datetime_str(), "rows": rows}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_sell_watch(target_date: str) -> list[dict] | None:
    """读卖出监控清单；不存在返回 None。"""
    path = sell_watch_file(target_date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("rows") or []


def clear_sell_watch(target_date: str) -> None:
    path = sell_watch_file(target_date)
    if path.is_file():
        path.unlink()


def _bought_today_path(date: str) -> Path:
    return quant_home() / "state" / f"bought_today_{date}.txt"


def read_bought_today(date: str) -> set[str]:
    p = _bought_today_path(date)
    if not p.is_file():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def _append_bought_today(date: str, codes: list[str]) -> None:
    p = _bought_today_path(date)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for c in codes:
            f.write(f"{c}\n")


def execute_intraday_buys(
    buys: list,
    alpha_z: dict[str, float],
    *,
    name_map: dict[str, str],
    today: str,
    single_weight: float = 0.10,
    dry_run: bool = False,
) -> dict:
    """盘中择时买入：对触发的作战池票按最新价撮合，仓位=分档(5%-10%)，标记当日已买。

    在 ``paper_home_context`` 下执行（调用方已进 paper context 则内部 no-op 不嵌套）。
    """
    with paper_home_context():
        holdings = get_holdings()
        held = {str(h.get("股票代码", "")).strip() for h in holdings}
        bought = read_bought_today(today)
        total_assets = get_total_assets()
        if total_assets <= 0:
            total_assets = float(get_account().get("可用资金") or 0) or 100_000.0
        assets = max(total_assets, 1.0)

        signals: list[TradeSignal] = []
        quote_rows: list[dict] = []
        for r in buys:
            if r.code in held or r.code in bought:
                continue
            strength = max(0.0, min(1.0, alpha_z.get(r.code, 0.0) / 2.0))
            weight = single_weight * (0.5 + 0.5 * strength)
            amount = weight * assets
            qty = shares_for_amount(r.last, amount)
            if qty <= 0:
                continue
            name = name_map.get(r.code) or r.code
            pre = r.pre_close if r.pre_close > 0 else r.last
            chg = (r.last / pre - 1.0) * 100 if pre > 0 else 0.0
            signals.append(
                TradeSignal(
                    action="买入",
                    code=r.code,
                    name=name,
                    price=r.last,
                    quantity=qty,
                    strategy="r3_intraday",
                    reason=f"intraday α_z={alpha_z.get(r.code, 0):.2f}|tw={weight:.1%}",
                    signal_kind="intraday_buy",
                )
            )
            quote_rows.append(
                {
                    "股票代码": r.code,
                    "股票名称": name,
                    "盘口": {
                        "最新": r.last,
                        "最新价": r.last,
                        "今开": r.open,
                        "昨收": pre,
                        "涨跌幅": chg,
                    },
                }
            )
        if dry_run or not signals:
            return {
                "dry_run": dry_run,
                "n_signals": len(signals),
                "n_executed": 0,
                "executed": [],
                "rejected": {},
                "bought": [],
            }
        payload = {"自选股": quote_rows, "持仓股": []}
        executed, rejected = execute_signals(
            signals,
            payload=payload,
            enforce_hours=False,
            allow_add=True,
            trade_date=today,
        )
        bought_codes = [e.signal.code for e in executed if e.signal.action == "买入"]
        if bought_codes:
            _append_bought_today(today, bought_codes)
        refresh_account_market_value()
        return {
            "dry_run": False,
            "n_signals": len(signals),
            "n_executed": len(executed),
            "executed": [
                {
                    "code": e.signal.code,
                    "qty": e.signal.quantity,
                    "fill": e.fill_price,
                    "reason": e.signal.reason,
                }
                for e in executed
            ],
            "rejected": rejected,
            "bought": bought_codes,
        }


def _sold_today_path(date: str) -> Path:
    return quant_home() / "state" / f"sold_today_{date}.txt"


def read_sold_today(date: str) -> set[str]:
    p = _sold_today_path(date)
    if not p.is_file():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def _append_sold_today(date: str, codes: list[str]) -> None:
    p = _sold_today_path(date)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for c in codes:
            f.write(f"{c}\n")


def execute_intraday_sells(
    sells: list[dict],
    spot_by_code: dict[str, dict],
    *,
    today: str,
    dry_run: bool = False,
) -> dict:
    """盘中卖出择时：``force_sell`` 或 实时价 ≤ ``hard_stop``/``atr_stop`` → 全平卖出。

    sells: sell_watch 项（code/qty/hard_stop/atr_stop/force_sell/reason）；
    spot_by_code: ``{code: spot_em 行}``（取 close=最新价, pre_close 做涨跌停判定）。
    """
    with paper_home_context():
        sold = read_sold_today(today)
        signals: list[TradeSignal] = []
        quote_rows: list[dict] = []
        for s in sells:
            code = str(s.get("code") or "")
            if not code or code in sold:
                continue
            sr = spot_by_code.get(code) or {}
            try:
                last = float(sr.get("close") or 0)
            except (TypeError, ValueError):
                last = 0.0
            if last <= 0:
                continue
            hard = s.get("hard_stop")
            atr = s.get("atr_stop")
            force = bool(s.get("force_sell"))
            reason = None
            if force:
                reason = str(s.get("reason") or "force_sell")
            elif hard is not None and last <= float(hard):
                reason = f"hard_stop@{hard}"
            elif atr is not None and last <= float(atr):
                reason = f"atr_stop@{atr}"
            if not reason:
                continue
            try:
                qty = int(s.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            try:
                pre = float(sr.get("pre_close") or last)
            except (TypeError, ValueError):
                pre = last
            chg = (last / pre - 1.0) * 100 if pre > 0 else 0.0
            signals.append(
                TradeSignal(
                    action="卖出",
                    code=code,
                    name=s.get("name") or code,
                    price=last,
                    quantity=qty,
                    strategy="r3_intraday",
                    reason=f"intraday {reason}",
                    sell_type="止损",
                    signal_kind="intraday_sell",
                )
            )
            quote_rows.append(
                {
                    "股票代码": code,
                    "股票名称": s.get("name") or code,
                    "盘口": {"最新": last, "最新价": last, "昨收": pre, "涨跌幅": chg},
                }
            )
        if dry_run or not signals:
            return {
                "dry_run": dry_run,
                "n_signals": len(signals),
                "n_executed": 0,
                "executed": [],
                "rejected": {},
                "sold": [],
            }
        payload = {"自选股": quote_rows, "持仓股": []}
        executed, rejected = execute_signals(
            signals, payload=payload, enforce_hours=False, trade_date=today
        )
        sold_codes = [e.signal.code for e in executed if e.signal.action == "卖出"]
        if sold_codes:
            _append_sold_today(today, sold_codes)
        refresh_account_market_value()
        return {
            "dry_run": False,
            "n_signals": len(signals),
            "n_executed": len(executed),
            "executed": [
                {
                    "code": e.signal.code,
                    "qty": e.signal.quantity,
                    "fill": e.fill_price,
                    "reason": e.signal.reason,
                }
                for e in executed
            ],
            "rejected": rejected,
            "sold": sold_codes,
        }


def execute_decision_card(
    card: DecisionCard,
    *,
    daily: pd.DataFrame,
    as_of: str,
    prices: dict[str, float] | None = None,
    names: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """在 paper_account 下撮合决策卡，返回成交摘要。

    ⚠️ 时点口径：本函数用 ``as_of`` 当日收盘信号 + 当日收盘价成交，属**同日收盘理想化**
    上界（含前视），仅用于 dry-run/测试，**不得用于绩效声明**。生产晚间流程已改为
    「T 晚只定计划（write_battle_pool/write_sell_watch）→ T+1 盘中 execute_intraday_*
    执行」，与回测 ``strict_signals``（T-1 信号/T 开盘成交）同属无前视口径。
    """
    with paper_home_context() as home:
        holdings = get_holdings()
        if prices is None or names is None:
            prices, names = {}, {}
            day = daily[daily["date"].astype(str).str.slice(0, 10) == as_of]
            for _, r in day.iterrows():
                c = str(r["code"])
                prices[c] = float(r["close"])
                names[c] = str(r.get("name") or c)

        total_assets = get_total_assets()
        if total_assets <= 0:
            total_assets = float(get_account().get("可用资金") or 0) or 100_000.0

        signals = actions_to_signals(
            card,
            prices=prices,
            names=names,
            total_assets=total_assets,
            holdings=holdings,
        )
        payload = build_daily_payload(daily, as_of, holdings=holdings)

        if dry_run:
            return {
                "dry_run": True,
                "home": str(home),
                "n_signals": len(signals),
                "signals": [asdict(s) for s in signals],
                "account": get_account(),
                "executed": [],
                "rejected": {},
            }

        executed, rejected = execute_signals(
            signals,
            payload=payload,
            enforce_hours=False,
            enforce_late_session=False,
            allow_add=True,
            trade_date=as_of,
        )
        _stamp_holding_quotes(prices, names)
        acc = refresh_account_market_value()
        eq_path = append_paper_equity(as_of, acc, n_trades=len(executed))

        return {
            "dry_run": False,
            "home": str(home),
            "n_signals": len(signals),
            "n_executed": len(executed),
            "executed": [
                {
                    "code": e.signal.code,
                    "action": e.signal.action,
                    "qty": e.signal.quantity,
                    "fill": e.fill_price,
                    "pnl": e.pnl,
                    "reason": e.signal.reason,
                }
                for e in executed
            ],
            "rejected": rejected,
            "account": acc,
            "equity_path": str(eq_path),
            "holdings": [
                {
                    "code": str(h.get("股票代码")),
                    "name": h.get("股票名称"),
                    "shares": h.get("持仓股数"),
                    "cost": h.get("买入价"),
                }
                for h in get_holdings()
            ],
        }


def paper_summary_text(result: dict[str, Any]) -> str:
    acc = result.get("account") or {}
    lines = [
        "=== 纸面模拟成交 ===",
        f"账户目录: {result.get('home')}",
        f"信号 {result.get('n_signals')} → 成交 {result.get('n_executed', 0)}",
        f"总资产 {acc.get('总资产')} | 现金 {acc.get('可用资金')} | 市值 {acc.get('持仓市值')}",
        f"累计已实现盈亏 {acc.get('累计已实现盈亏')}",
    ]
    for e in result.get("executed") or []:
        lines.append(
            f"  [{e['action']}] {e['code']} x{e['qty']} @ {e['fill']} pnl={e['pnl']} ({e['reason']})"
        )
    if result.get("rejected"):
        lines.append("拒单:")
        for c, why in result["rejected"].items():
            lines.append(f"  {c}: {why}")
    lines.append("持仓:")
    for h in result.get("holdings") or []:
        lines.append(f"  {h['code']} {h.get('name')} {h.get('shares')}股 成本{h.get('cost')}")
    return "\n".join(lines)
