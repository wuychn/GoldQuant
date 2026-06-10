"""个股行格式化：名称/代码/涨跌幅/自选列表等推送与 brief 共用。"""

from __future__ import annotations

from quant.scoring.tech_indicators import stock_daily_change_pct

WATCHLIST_SECTION_TITLE = "八、自选更新"


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
            section_lines.append(format_score_bullet(r))
    else:
        section_lines.append("暂无自选标的。")
    if added:
        section_lines.append("")
        section_lines.append("本轮新入选：")
        for r in added:
            section_lines.append(format_score_bullet(r))
    if removed:
        section_lines.append("")
        section_lines.append("删除自选：")
        for r in removed:
            section_lines.append(format_name_code_bullet(r))
    return "\n".join(section_lines)
