"""跨日归档摘要：仅提供昨日复盘等叙述参考，不含主线/龙头判定（由 engine_brief 负责）。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant.scoring.theme_tracker import (
    _history_in_window,
    _load_state,
    _theme_cfg,
    resolve_main_themes,
    snapshot_boards,
)
from quant.store.paths import daily_raw, daily_review

_SH_TZ = ZoneInfo("Asia/Shanghai")

_MODES_WITH_CROSS_DAY = frozenset(
    {"pre_market", "during_market", "post_market_lunch", "post_market_evening"}
)


def _read_json(path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _find_latest_date_with(*names: str, start: date | None = None, max_scan: int = 14) -> str | None:
    """从 start 日（默认昨天）向前找，存在任一 raw/review 文件的最近日期。"""
    d = start or (datetime.now(_SH_TZ).date() - timedelta(days=1))
    for _ in range(max_scan):
        ds = d.isoformat()
        for name in names:
            if name.endswith(".md"):
                if daily_review(name, ds).is_file():
                    return ds
            elif daily_raw(name, ds).is_file():
                return ds
        d -= timedelta(days=1)
    return None


def _strip_push_header(text: str) -> str:
    text = text.strip()
    if text.startswith("【"):
        m = re.match(r"^【[^】]+】[^\n]*\n+", text)
        if m:
            return text[m.end() :].strip()
    return text


def _read_review_excerpt(review_name: str, date_str: str, *, max_chars: int) -> str:
    path = daily_review(review_name, date_str)
    if not path.is_file():
        return ""
    try:
        text = _strip_push_header(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "…" + text[-max_chars:]


def _load_evening_raw(date_str: str) -> dict | None:
    return _read_json(daily_raw("evening.json", date_str))


def format_main_theme_context() -> str:
    """从 main_themes.json 输出确认主线与观察中概念（不写入新数据）。"""
    cfg = _theme_cfg()
    lookback = int(cfg.get("lookback_days", 5))
    min_days = int(cfg.get("min_hit_days", 2))
    require_fund = bool(cfg.get("require_fund_once", True))

    confirmed = sorted(resolve_main_themes({}, update=False))
    state = _load_state()
    last_update = str(state.get("last_update_date") or "")

    lines = [
        f"规则：近{lookback}日内至少{min_days}次上榜"
        + ("且至少1次资金流入榜" if require_fund else "")
        + f"；状态最后更新 {last_update or '无'}。",
        f"确认主线（{len(confirmed)}）: "
        + ("、".join(confirmed) if confirmed else "暂无"),
    ]

    tracking: list[tuple[int, int, str]] = []
    for name, entry in (state.get("concepts") or {}).items():
        if name in confirmed:
            continue
        hist = _history_in_window(entry.get("history") or {}, lookback)
        if not hist:
            continue
        fund_hits = sum(1 for f in hist.values() if f.get("fund"))
        tracking.append((len(hist), fund_hits, name))

    tracking.sort(key=lambda x: (-x[0], -x[1], x[2]))
    if tracking:
        top = tracking[:8]
        parts = [f"{n}({d}日" + (f"/资金{f}次" if f else "") + ")" for d, f, n in top]
        lines.append("观察中（未达主线阈值）: " + "、".join(parts))

    today_dual = state.get("today_dual") or []
    if today_dual:
        lines.append("最近归档日涨幅+资金双榜: " + "、".join(today_dual))

    return "\n".join(lines)


def format_concept_rotation(*, lookback: int | None = None) -> str:
    """近 N 个 evening 归档的概念榜时间线 + 轮动摘要。"""
    cfg = _theme_cfg()
    n = lookback or int(cfg.get("lookback_days", 5))
    snapshots: list[tuple[str, set[str], set[str]]] = []
    d = datetime.now(_SH_TZ).date() - timedelta(days=1)

    for _ in range(45):
        if len(snapshots) >= n:
            break
        ds = d.isoformat()
        payload = _load_evening_raw(ds)
        if payload:
            gain, fund = snapshot_boards(payload, limit=int(cfg.get("board_limit", 10)))
            snapshots.append((ds, gain, fund))
        d -= timedelta(days=1)

    if not snapshots:
        return "暂无 evening.json 归档，无法生成概念轮动时间线。"

    snapshots.reverse()
    lines = ["近{}个交易日概念榜（来源: daily/raw/evening.json）:".format(len(snapshots))]
    concept_days: dict[str, int] = {}

    for ds, gain, fund in snapshots:
        g5 = sorted(gain)[:5]
        f5 = sorted(fund)[:5]
        lines.append(f"- {ds} 涨幅前5: {'、'.join(g5) if g5 else '无'}")
        lines.append(f"  资金前5: {'、'.join(f5) if f5 else '无'}")
        for c in gain | fund:
            concept_days[c] = concept_days.get(c, 0) + 1

    persistent = sorted([c for c, cnt in concept_days.items() if cnt >= 2], key=lambda c: (-concept_days[c], c))
    if persistent:
        lines.append("多次上榜（轮动中偏持续）: " + "、".join(persistent[:12]))

    if len(snapshots) >= 2:
        prev_gain, prev_fund = snapshots[-2][1], snapshots[-2][2]
        last_gain, last_fund = snapshots[-1][1], snapshots[-1][2]
        new_gain = sorted(last_gain - prev_gain)
        new_fund = sorted(last_fund - prev_fund)
        drop_gain = sorted(prev_gain - last_gain)
        if new_gain or new_fund:
            lines.append(
                "最近一日新增强势: "
                + "、".join(new_gain[:6])
                + ("；资金新晋: " + "、".join(new_fund[:6]) if new_fund else "")
            )
        if drop_gain:
            lines.append("最近一日退出涨幅前榜: " + "、".join(drop_gain[:6]))

    return "\n".join(lines)


def format_yesterday_trades(*, date_str: str | None = None) -> str:
    from quant.store.snapshot import daily_trades_path

    if date_str:
        candidates = [date_str]
    else:
        candidates = []
        d = datetime.now(_SH_TZ).date() - timedelta(days=1)
        for _ in range(14):
            candidates.append(d.isoformat())
            d -= timedelta(days=1)

    rows: list[dict] = []
    used_date = ""
    for ds in candidates:
        path = daily_trades_path("executed.json", ds)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list) and data:
            rows = [r for r in data if isinstance(r, dict)]
            used_date = ds
            break

    if not rows:
        return ""

    parts: list[str] = []
    for r in rows[-8:]:
        action = r.get("方向") or ""
        code = r.get("股票代码") or ""
        name = r.get("股票名称") or ""
        reason = str(r.get("理由") or "")[:40]
        parts.append(f"{action} {name}({code}) {reason}".strip())

    if not parts:
        return ""
    return f"{used_date} 成交（最近{len(parts)}笔）: " + "；".join(parts)


def build_cross_day_context(mode: str) -> str:
    """按模式组装跨日叙述参考（复盘文案），不含交易决策。"""
    if mode not in _MODES_WITH_CROSS_DAY:
        return ""

    sections: list[str] = []

    prev_ds = _find_latest_date_with("evening.md", "evening.json")
    if prev_ds:
        excerpt = _read_review_excerpt("evening.md", prev_ds, max_chars=2000 if mode == "pre_market" else 1400)
        if excerpt:
            sections.append(
                f"【历史叙述参考 · 上一交易日复盘 {prev_ds}（勿据此重新判定主线/龙头）】\n{excerpt}"
            )

    if mode == "post_market_lunch":
        today = datetime.now(_SH_TZ).date().isoformat()
        pre_excerpt = _read_review_excerpt("pre_market.md", today, max_chars=800)
        if pre_excerpt:
            sections.append(f"【历史叙述参考 · 当日盘前推送 {today}】\n{pre_excerpt}")

    return "\n\n".join(sections)
