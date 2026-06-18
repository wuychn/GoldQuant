"""个股行格式化：名称/代码/涨跌幅/自选列表等推送与 brief 共用。"""

from __future__ import annotations

from typing import Any

from quant.pool.ths_rank_util import format_ths_rank_tags_brief, stock_ths_rank_tags
from quant.scoring.tech_indicators import stock_daily_change_pct

WATCHLIST_SECTION_TITLE = "六、自选更新"


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
        if track in ("概念", "行业") and best:
            return str(track), str(best).strip()
    return None, None


def _theme_part_from_row(row: dict) -> str | None:
    concepts = _parse_name_list(row.get("所属概念") or row.get("概念"))
    if concepts:
        return f"所属概念{concepts[0]}"
    industries = _parse_name_list(row.get("行业"))
    if industries:
        return f"所属行业{industries[0]}"
    return None


def build_watchlist_human_reason(score: Any, candidate_row: dict) -> str:
    """晚间加自选：人类可读单行原因，如「xx股份，所属行业元件，创新高，评分80」。"""
    name = stock_name(candidate_row) or getattr(score, "name", "") or stock_code(candidate_row)
    parts: list[str] = [name]

    track, best = _theme_part_from_score(score)
    if track and best:
        parts.append(f"所属{track}{best}")
    else:
        theme = _theme_part_from_row(candidate_row)
        if theme:
            parts.append(theme)

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
    removed: list[dict],
    *,
    title: str = WATCHLIST_SECTION_TITLE,
) -> str:
    """晚间复盘文末「自选更新」段。"""
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
    if removed:
        section_lines.append("")
        section_lines.append("删除自选：")
        for r in removed:
            section_lines.append(format_name_code_bullet(r))
    return "\n".join(section_lines)
