"""r3 运维模式正文生成（无专家评分 / 无逐笔信号）。"""

from __future__ import annotations

import json
from typing import Any

from quant.data_fetch import fetch_mode, unwrap_payload
from quant.ops.paper_view import paper_account_lines, paper_block, paper_holdings_lines
from quant.push.format import (
    ICON_ACCOUNT,
    ICON_FILL,
    ICON_HOLD,
    ICON_MARKET,
    ICON_NEWS,
    ICON_ORDER,
    ICON_TIP,
    ICON_WATCH,
    INDEX_LABELS,
    emoji_for_pct,
    fmt_pct,
    fmt_price,
    icon_section,
    money,
)
from quant.store.state import write_news_summary


def _first(row: dict, *keys: str):
    """取 row 中首个非空字段（保留 0）。"""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


def _index_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for key in ("大盘指数", "指数", "大盘", "indices", "index"):
        block = payload.get(key)
        if not block:
            continue
        if isinstance(block, list):
            for row in block[:6]:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("代码") or row.get("code") or "")
                name = _first(row, "名称", "name") or code
                label = INDEX_LABELS.get(code) or name
                chg = _first(row, "涨跌幅", "change_pct", "涨幅")
                px = _first(row, "最新", "close", "最新价")
                if label:
                    lines.append(
                        f"{label} {fmt_price(px)} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}"
                    )
        elif isinstance(block, dict):
            for name, row in list(block.items())[:6]:
                if isinstance(row, dict):
                    chg = _first(row, "涨跌幅", "change_pct")
                    lines.append(f"{name} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}")
                else:
                    lines.append(f"{name} {row}")
        break
    return lines


def _top_movers(payload: dict, *, key_candidates: tuple[str, ...], limit: int = 5) -> list[str]:
    for key in key_candidates:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        out: list[str] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            code = row.get("股票代码") or row.get("code") or ""
            name = row.get("股票名称") or row.get("name") or ""
            chg = _first(row, "涨跌幅", "change_pct")
            out.append(f"{code} {name} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}")
        if out:
            return out
    return []


def build_news_body(raw: dict) -> str:
    from quant.narrative.llm import call_llm
    from quant.narrative.prompts import prompt_news

    payload = unwrap_payload(raw)
    if isinstance(payload, dict) and "news" in payload:
        news_list = payload["news"]
    elif isinstance(payload, list):
        news_list = payload
    else:
        news_list = [payload]
    user = json.dumps({"news": news_list}, ensure_ascii=False)[:140000]
    summary = call_llm(prompt_news(), user, max_tokens=2500)
    # 落摘要供盘前引用
    if "综合解读" in summary:
        tail = summary.split("综合解读", 1)[-1]
        write_news_summary(f"综合解读{tail.strip()[:800]}")
    # 推送只保留精简版：截到合理长度
    text = summary.strip()
    if len(text) > 1800:
        text = text[:1800] + "…"
    return text


def build_pre_market_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        icon_section(ICON_MARKET, "指数", _index_lines(payload)),
        paper_block("纸面账户"),
        icon_section(
            ICON_WATCH,
            "关注",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "热门股", "人气榜")),
        ),
        icon_section(
            ICON_TIP,
            "提示",
            [
                "今日决策与纸面成交由日频任务自动执行",
                "以目标组合差额交易，出场看 ATR/硬止损/破位/时间",
            ],
        ),
    ]
    from quant.store.state import read_news_summary

    news = read_news_summary()
    if news:
        parts.insert(0, icon_section(ICON_NEWS, "新闻要点", [news[:400]]))
    return "\n\n".join(p for p in parts if p)


def _intraday_buy_block(*, theta: float = 1.0) -> str:
    """盘中择时：读作战池 → spot_em → compose_intraday_alpha → α_z≥θ 触发买入 → 撮合。

    返回推送段字符串；非交易日/无作战池/无触发返回 ""。
    """
    from quant.data.calendar import is_trading_day
    from quant.data.fetch import fetch_spot_em
    from quant.decision.paper_execute import (
        execute_intraday_buys,
        paper_home_context,
        read_battle_pool,
    )
    from quant.factors.compose import compose_intraday_alpha
    from quant.factors.library.intraday import spot_row_from_dict
    from quant.timeutil import cn_now, intraday_minutes_since_open

    today = cn_now().date()
    if not is_trading_day(today):
        return ""
    today_s = today.isoformat()
    with paper_home_context():
        pool = read_battle_pool(today_s)
        if not pool:
            return ""
        try:
            spot = fetch_spot_em()
        except Exception as e:  # noqa: BLE001
            return icon_section(ICON_TIP, "盘中择时", [f"取价失败: {e}"])
        spot_by_code = {str(r.get("code")): r for r in spot.to_dict("records")}
        rows = []
        name_map: dict[str, str] = {}
        for p in pool:
            code = str(p.get("code"))
            sd = spot_by_code.get(code)
            if not sd:
                continue
            sr = spot_row_from_dict(sd)
            if sr:
                rows.append(sr)
                name_map[code] = p.get("name") or code
        if not rows:
            return ""
        # 开盘 10 分钟内 intraday_strength 噪声主导（集合竞价 open 在 9:30-9:35 浮动极大），
        # α_z 易瞬间穿越阈值触发假买；待开盘稳定后再触发
        try:
            mins_open = intraday_minutes_since_open()
        except Exception:
            mins_open = None
        if mins_open is not None and mins_open < 10:
            return icon_section(ICON_TIP, "盘中择时", [f"开盘 {mins_open} 分钟，跳过（噪声主导）"])
        alpha_z = compose_intraday_alpha(rows)
        buys = [r for r in rows if alpha_z.get(r.code, 0.0) >= theta]
        if not buys:
            top = sorted(alpha_z.items(), key=lambda kv: -kv[1])[:3]
            top_s = ", ".join(f"{name_map.get(c,c)}:{v:.2f}" for c, v in top)
            return icon_section(
                ICON_WATCH, "盘中择时", [f"作战池{len(rows)}只 无触发(θ={theta})；最强 {top_s}"]
            )
        target_weights = {str(p.get("code")): float(p.get("target_weight") or 0.0) for p in pool}
        result = execute_intraday_buys(
            buys, alpha_z, name_map=name_map, today=today_s, target_weights=target_weights
        )
    lines = [f"触发{len(buys)}只 → 买入{result.get('n_executed', 0)}笔"]
    for e in result.get("executed", [])[:8]:
        lines.append(f"🛒 {e['code']} x{e['qty']} @{e['fill']} {e.get('reason', '')}")
    for c, why in (result.get("rejected") or {}).items():
        lines.append(f"拒 {c}: {why}")
    return icon_section(ICON_ORDER, "盘中择时买入", lines)


def _intraday_sell_block() -> str:
    """盘中卖出择时：读 sell_watch → spot_em → force_sell 或 价破 stop → 全平卖出。

    返回推送段字符串；非交易日/无监控/无触发返回 ""。
    """
    from quant.data.calendar import is_trading_day
    from quant.data.fetch import fetch_spot_em
    from quant.decision.paper_execute import (
        execute_intraday_sells,
        paper_home_context,
        read_sell_watch,
    )
    from quant.timeutil import cn_now

    today = cn_now().date()
    if not is_trading_day(today):
        return ""
    today_s = today.isoformat()
    with paper_home_context():
        sells = read_sell_watch(today_s)
        if not sells:
            return ""
        try:
            spot = fetch_spot_em()
        except Exception as e:  # noqa: BLE001
            return icon_section(ICON_TIP, "盘中卖出", [f"取价失败: {e}"])
        spot_by_code = {str(r.get("code")): r for r in spot.to_dict("records")}
        result = execute_intraday_sells(sells, spot_by_code, today=today_s)
    if not result.get("n_signals"):
        return icon_section(ICON_WATCH, "盘中卖出", [f"监控{len(sells)}只 无触发"])
    lines = [f"触发{result.get('n_signals')} 卖出{result.get('n_executed')}笔"]
    for e in result.get("executed", [])[:8]:
        lines.append(f"🛒 {e['code']} x{e['qty']} @{e['fill']} {e.get('reason', '')}")
    for c, why in (result.get("rejected") or {}).items():
        lines.append(f"拒 {c}: {why}")
    return icon_section(ICON_ORDER, "盘中卖出", lines)


def build_during_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        icon_section(ICON_MARKET, "指数", _index_lines(payload)),
        paper_block("纸面持仓"),
        icon_section(
            ICON_WATCH,
            "异动",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "持仓股"), limit=6),
        ),
    ]
    sell_block = _intraday_sell_block()
    if sell_block:
        parts.append(sell_block)
    buy_block = _intraday_buy_block()
    if buy_block:
        parts.append(buy_block)
    return "\n\n".join(p for p in parts if p)


def build_lunch_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        icon_section(ICON_MARKET, "午前指数", _index_lines(payload)),
        paper_block("纸面账户"),
        icon_section(ICON_TIP, "提示", ["午间不交易；关注下午是否触发止损/破位"]),
    ]
    return "\n\n".join(p for p in parts if p)


def build_evening_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    # 权益曲线最近一行
    eq_lines: list[str] = []
    try:
        from quant.decision.paper_execute import paper_home_context
        from quant.store.paths import state_file

        with paper_home_context():
            path = state_file("equity.jsonl")
            if path.is_file():
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    last = json.loads(lines[-1])
                    eq_lines = [
                        f"日期 {last.get('date')}",
                        f"总资产 {money(last.get('总资产'))}",
                        f"当日成交 {last.get('n_trades')} 笔 | 持仓 {last.get('n_holdings')} 只",
                    ]
    except Exception:
        pass
    parts = [
        icon_section(ICON_MARKET, "收盘指数", _index_lines(payload)),
        icon_section(ICON_ACCOUNT, "纸面绩效", eq_lines or paper_account_lines()),
        icon_section(ICON_HOLD, "持仓", paper_holdings_lines(10)),
        icon_section(ICON_TIP, "明日", ["收盘后跑日决策，按目标组合再平衡并更新权益"]),
    ]
    return "\n\n".join(p for p in parts if p)


_SIDE_ICON = {"buy": "📤", "add": "📤", "sell": "📥", "reduce": "📥"}
_SIDE_LABEL = {"buy": "买入", "add": "加仓", "sell": "卖出", "reduce": "减仓", "hold": "持有"}
_REASON_LABEL = {
    "new_position": "新建",
    "exit_signal": "出场",
    "out_of_target": "掉出目标",
    "rebalance": "调仓",
    "within_buffer": "缓冲内",
}


def build_decision_push_body(payload: dict[str, Any]) -> str:
    """日决策推送正文：账户 + 明日作战池 + 卖出监控 + 持仓（晚间只定计划，不撮合）。"""
    paper = payload.get("paper") or {}
    acc = paper.get("account") or {}
    pool = payload.get("battle_pool") or []
    sell_watch = payload.get("sell_watch") or []
    pool_lines = [
        f"{p.get('name')}({p.get('code')}) α={p.get('alpha')} #{p.get('rank')}"
        for p in pool[:8]
    ]
    sell_lines = []
    for s in sell_watch[:10]:
        flag = "⚡" if s.get("force_sell") else "·"
        hs = s.get("hard_stop")
        sell_lines.append(
            f"{flag} {s.get('name')}({s.get('code')}) 止损{hs} {(s.get('reason') or '').strip()}"
        )
    parts = [
        icon_section(
            ICON_ACCOUNT,
            "账户",
            [
                f"总资产 {money(acc.get('总资产'))}",
                f"现金 {money(acc.get('可用资金'))} | 市值 {money(acc.get('持仓市值'))}",
            ],
        ),
        icon_section(
            ICON_WATCH, f"明日作战池({payload.get('battle_pool_date', '')})", pool_lines
        ),
        icon_section(ICON_ORDER, "卖出监控", sell_lines),
        icon_section(
            ICON_HOLD,
            "持仓",
            [
                f"{h.get('code')} {h.get('name')} {h.get('shares')}股 @{h.get('cost')}"
                for h in (paper.get("holdings") or [])[:8]
            ],
        ),
    ]
    return "\n\n".join(p for p in parts if p)
