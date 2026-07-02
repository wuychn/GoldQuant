"""个股行格式化：名称/代码/涨跌幅/自选列表等推送与 brief 共用。"""

from __future__ import annotations

import re
from typing import Any

from quant.pool.ths_rank_util import format_ths_rank_tags_brief, stock_ths_rank_tags
from quant.scoring.dimensions.concept_theme import (
    DISPLAY_CONCEPT_LIMIT,
    format_stock_concepts_brief,
    stock_concept_display_names,
)
from quant.scoring.tech_indicators import quote_last_price, stock_daily_change_pct
from quant.config import load_gates_config
from quant.strategy.momentum import momentum_score
from quant.strategy.trend import quantify_trend

WATCHLIST_SECTION_TITLE = "六、自选更新"


def watchlist_price_range_cfg() -> dict | None:
    """推送价格区间配置；默认激活（enabled 仅在显式 False 时关闭）。

    min/max 为 None 表示该侧无边界。返回 None 表示不过滤。
    """
    from quant.config import load_push_config

    cfg = load_push_config().get("watchlist_price_range") or {}
    if cfg.get("enabled") is False:
        return None
    return cfg or None


def _in_watchlist_price_range(price: float | None, cfg: dict | None) -> bool:
    """价格是否落在配置区间内；cfg=None 不过滤，价格未知视为保留（不误删）。"""
    if not cfg:
        return True
    if price is None:
        return True
    lo = cfg.get("min")
    hi = cfg.get("max")
    try:
        if lo is not None and price < float(lo):
            return False
        if hi is not None and price > float(hi):
            return False
    except (TypeError, ValueError):
        return True
    return True


def watchlist_score_filter_cfg() -> dict | None:
    """晚间自选推送评分过滤配置；默认激活，min_score 缺省取 scoring.watchlist_threshold。"""
    from quant.config import load_push_config, load_scoring_config

    cfg = load_push_config().get("watchlist_score_filter") or {}
    if cfg.get("enabled") is False:
        return None
    if cfg.get("min_score") is None:
        threshold = load_scoring_config().get("watchlist_threshold", 70)
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            threshold = 70.0
        cfg = {**cfg, "min_score": threshold}
    return cfg or None


def _meets_watchlist_score(row: dict, cfg: dict | None) -> bool:
    """当天评分是否达推送下限；cfg=None 不过滤，评分缺失视为保留（不误删）。"""
    if not cfg:
        return True
    try:
        score = float(row.get("评分") or 0)
    except (TypeError, ValueError):
        return True
    try:
        return score >= float(cfg.get("min_score") or 0)
    except (TypeError, ValueError):
        return True


def watchlist_row_price(
    row: dict, price_by_code: dict[str, float] | None = None
) -> float | None:
    """取自选行现价：行内盘口优先，其次 price_by_code 索引（晚间 merged 无盘口时用）。"""
    p = quote_last_price(row)
    if p is not None:
        return p
    code = stock_code(row)
    if price_by_code and code:
        return price_by_code.get(code)
    return None


def stock_code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def stock_name(row: dict) -> str:
    return str(row.get("股票名称") or row.get("名称") or "").strip()


def name_code_label(row: dict) -> str:
    code = stock_code(row)
    name = stock_name(row) or code
    return f"{name}（{code}）"


def daily_change_suffix(row: dict, *, prefix: str = "当日") -> str:
    chg = stock_daily_change_pct(row)
    if chg is None:
        return f"{prefix}涨跌暂无"
    return f"{prefix}{chg:+.2f}%"


def _format_watchlist_score(total: float) -> str:
    return str(int(round(float(total))))


def _parse_name_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s in ("无", "-", "—"):
            return []
        return [x.strip() for x in raw.replace(";", "、").replace(",", "、").split("、") if x.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _candidate_sources(row: dict) -> list[str]:
    raw = row.get("候选来源")
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    s = str(raw or "").strip()
    return [s] if s else []


def _theme_part_from_score(score: Any) -> tuple[str | None, str | None]:
    for d in getattr(score, "dimensions", []) or []:
        if getattr(d, "name", "") != "concept_theme" or not getattr(d, "available", False):
            continue
        detail = getattr(d, "detail", None) or {}
        track = detail.get("最佳赛道")
        best = detail.get("最佳命中概念")
        if track == "行业" and best:
            return str(track), str(best).strip()
    return None, None


def _row_with_score_concept_fit(candidate_row: dict, score: Any) -> dict:
    row = dict(candidate_row)
    if stock_concept_display_names(row, limit=1):
        return row
    for d in getattr(score, "dimensions", []) or []:
        if getattr(d, "name", "") != "concept_theme":
            continue
        fit = (getattr(d, "detail", None) or {}).get("概念粘合度")
        if isinstance(fit, list) and fit:
            row["概念粘合度"] = fit
            break
    return row


def _theme_part_from_row(row: dict) -> str | None:
    return format_stock_concepts_brief(row, limit=DISPLAY_CONCEPT_LIMIT)


_LEGACY_WATCHLIST_REASON_RE = re.compile(r"^评分[\d.]+[；;]")


def watchlist_reason_needs_name_prefix(reason: str, row: dict) -> bool:
    """旧版原因以「评分xx；…」开头且不含股票名称。"""
    text = str(reason or "").strip()
    if not text:
        return False
    name = stock_name(row)
    if name and name in text:
        return False
    return bool(_LEGACY_WATCHLIST_REASON_RE.match(text)) or text.startswith("评分")


def ensure_watchlist_reason_display(row: dict) -> str:
    """补齐旧版无名称的「加入自选原因」。"""
    reason = str(row.get("加入自选原因") or "").strip()
    if reason and not watchlist_reason_needs_name_prefix(reason, row):
        return reason
    if reason:
        name = stock_name(row) or stock_code(row)
        return f"{name}，{reason}" if name else reason
    name = stock_name(row) or stock_code(row)
    score = row.get("评分")
    if name and score is not None:
        try:
            return f"{name}，评分{_format_watchlist_score(float(score))}"
        except (TypeError, ValueError):
            return f"{name}，评分{score}"
    return name


def enrich_watchlist_trend_fields(row: dict, stock: dict, *, mw_cfg: dict | None = None) -> None:
    """写入趋势阶段与动能分（自选/推送展示与排序）。"""
    if not stock:
        return
    cfg = mw_cfg if mw_cfg is not None else (load_gates_config().get("main_wave") or {})
    phase, note, _ = quantify_trend(stock, cfg)
    ms, _ = momentum_score(stock, cfg)
    row["趋势阶段"] = phase
    row["趋势说明"] = note
    row["动能分"] = round(ms, 1)


def refresh_merged_watchlist_reasons(
    merged: list[dict],
    *,
    score_by_code: dict[str, Any],
    candidate_by_code: dict[str, dict],
) -> None:
    """合并后按当晚评分与候选数据重写「加入自选原因」。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    for row in merged:
        code = stock_code(row)
        if not code:
            continue
        score = score_by_code.get(code)
        if score is None:
            fixed = ensure_watchlist_reason_display(row)
            if fixed:
                row["加入自选原因"] = fixed
            cand = dict(candidate_by_code.get(code) or {})
            if cand:
                enrich_watchlist_trend_fields(row, {**row, **cand}, mw_cfg=mw_cfg)
            continue
        cand = dict(candidate_by_code.get(code) or {})
        if not stock_name(cand) and stock_name(row):
            cand.setdefault("股票名称", stock_name(row))
        if not row.get("股票名称"):
            row["股票名称"] = getattr(score, "name", "") or stock_name(cand)
        row["评分"] = round(float(getattr(score, "total", row.get("评分", 0)) or 0), 2)
        row["加入自选原因"] = build_watchlist_human_reason(score, cand)
        ths_tags = stock_ths_rank_tags(cand)
        if ths_tags:
            row["榜单标签"] = ths_tags
        enrich_watchlist_trend_fields(row, {**row, **cand}, mw_cfg=mw_cfg)


def build_watchlist_human_reason(score: Any, candidate_row: dict) -> str:
    """晚间加自选：人类可读单行原因，如「xx股份，所属概念A、B、C，创新高，评分80」。"""
    name = stock_name(candidate_row) or getattr(score, "name", "") or stock_code(candidate_row)
    parts: list[str] = [name]

    concept_row = _row_with_score_concept_fit(candidate_row, score)
    concept_brief = format_stock_concepts_brief(concept_row, limit=DISPLAY_CONCEPT_LIMIT)
    if concept_brief:
        parts.append(concept_brief)
    else:
        track, best = _theme_part_from_score(score)
        if track and best:
            parts.append(f"所属{track}{best}")
        else:
            industries = _parse_name_list(candidate_row.get("行业"))
            if industries:
                parts.append(f"所属行业{industries[0]}")

    parts.extend(format_ths_rank_tags_brief(stock_ths_rank_tags(candidate_row)))

    sources = _candidate_sources(candidate_row)
    rank = candidate_row.get("人气排名")
    if rank is not None and ("人气榜" in sources or rank != ""):
        try:
            parts.append(f"同花顺人气榜第{int(rank)}")
        except (TypeError, ValueError):
            parts.append("同花顺人气榜")

    if "涨停池" in sources:
        boards = candidate_row.get("连板数")
        if boards is not None:
            parts.append(f"涨停{boards}板")
        else:
            parts.append("涨停池")

    total = getattr(score, "total", candidate_row.get("评分", 0))
    parts.append(f"评分{_format_watchlist_score(float(total or 0))}")
    return "，".join(parts)


def format_watchlist_reason_bullet(row: dict) -> str:
    reason = str(row.get("加入自选原因") or "").strip()
    if reason:
        if watchlist_reason_needs_name_prefix(reason, row):
            reason = ensure_watchlist_reason_display(row)
        return reason if reason.startswith("·") else f"· {reason}"
    return format_score_bullet(row)


def format_score_bullet(row: dict, *, score_key: str = "评分") -> str:
    score = row.get(score_key, "—")
    return f"· {name_code_label(row)}评分{score}"


def format_name_code_bullet(row: dict, *, suffix: str = "") -> str:
    tail = f"{suffix}" if suffix else ""
    return f"· {name_code_label(row)}{tail}"


def format_optional_performance_lines(rows: list[dict], *, limit: int = 12) -> list[str]:
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not stock_code(row):
            continue
        chg_s = daily_change_suffix(row)
        lines.append(f"· {name_code_label(row)} {chg_s}")
        if len(lines) >= limit:
            break
    return lines


def build_watchlist_push_section(
    merged: list[dict],
    added: list[dict],
    moved_to_observe: list[dict] | None = None,
    *,
    restored_from_observe: list[dict] | None = None,
    restored_new: list[dict] | None = None,
    purged: list[dict] | None = None,
    removed: list[dict] | None = None,
    title: str = WATCHLIST_SECTION_TITLE,
    price_by_code: dict[str, float] | None = None,
) -> str:
    """晚间复盘文末「自选更新」段。

    推送展示过滤（仅影响展示，不影响加自选/买卖/评分/落盘）：
    - ``push.watchlist_price_range``：现价落在 [min, max] 区间外的不推送；
    - ``push.watchlist_score_filter``：当天评分 < min_score 的不推送
      （hysteresis 死区保留但不再推送）；min_score 缺省取 scoring.watchlist_threshold。
    价格/评分缺失视为保留不误删。``price_by_code`` 供无盘口的 merged 行查价。
    移入观察池 / 观察池期满删除为变更日志，不受此过滤影响。
    """
    del removed  # 兼容旧调用
    price_cfg = watchlist_price_range_cfg()
    score_cfg = watchlist_score_filter_cfg()

    def _keep(row: dict) -> bool:
        return _in_watchlist_price_range(
            watchlist_row_price(row, price_by_code), price_cfg
        ) and _meets_watchlist_score(row, score_cfg)

    if price_cfg or score_cfg:
        merged = [r for r in merged if _keep(r)]
        added = [r for r in added if _keep(r)]
        restored_from_observe = [
            r for r in (restored_from_observe or []) if _keep(r)
        ] or None
        restored_new = [
            r for r in (restored_new or []) if _keep(r)
        ] or None
    section_lines = [title, ""]
    if merged:
        for r in merged:
            section_lines.append(format_watchlist_reason_bullet(r))
    else:
        section_lines.append("暂无自选标的。")
    if added:
        section_lines.append("")
        section_lines.append("本轮新入选：")
        for r in added:
            section_lines.append(format_watchlist_reason_bullet(r))
    restored_existing = restored_from_observe or []
    restored_brand_new = restored_new or []
    if restored_existing:
        section_lines.append("")
        section_lines.append("观察池恢复：")
        for r in restored_existing:
            section_lines.append(format_watchlist_reason_bullet(r))
    if restored_brand_new:
        section_lines.append("")
        section_lines.append("观察池恢复（新入选）：")
        for r in restored_brand_new:
            section_lines.append(format_watchlist_reason_bullet(r))
    if moved_to_observe:
        section_lines.append("")
        section_lines.append("移入观察池：")
        for r in moved_to_observe:
            section_lines.append(format_name_code_bullet(r))
    if purged:
        section_lines.append("")
        section_lines.append("观察池期满删除：")
        for r in purged:
            section_lines.append(format_name_code_bullet(r))
    return "\n".join(section_lines)
