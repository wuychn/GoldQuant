"""文件读写：资金、持仓、自选、止损、交易记录、记忆。"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from quant.config import (
    DATA_DIR, FUND_FILE, HOLDING_FILE, INITIAL_CAPITAL, MEMORY_COMPRESS_THRESHOLD_CHARS,
    MEMORY_COMPRESS_THRESHOLD_ENTRIES, MEMORY_FILE, MEMORY_MAX_INJECT_CHARS,
    NEWS_IMPACT_SUMMARY_FILE, OPTIONAL_FILE, OPTIONAL_HISTORY_FILE,
    POPULARITY_FILE, STOPLOSS_FILE,
)


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def read_user_text(path) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def unwrap_payload(raw: dict) -> dict:
    inner = raw.get("data")
    return inner if isinstance(inner, dict) else raw


# ---------------------------------------------------------------------------
# Fund management
# ---------------------------------------------------------------------------

def get_fund() -> float:
    try:
        return float(read_user_text(FUND_FILE).strip())
    except (OSError, ValueError, TypeError):
        return INITIAL_CAPITAL


def update_fund(profit: float):
    fund = get_fund() + profit
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = {}
    try:
        content = read_user_text(FUND_FILE)
        hist = re.findall(r'(\d{4}-\d{2}-\d{2}):\s*([\d.]+)', content)
        for d, v in hist:
            existing[d] = float(v)
        existing[today.split(' ')[0]] = fund
        lines = [
            "# 资金曲线",
            f"- 初始本金：{INITIAL_CAPITAL:.2f} 元",
            f"- 更新时间：{today}",
            f"- 当前总资产：{fund:.2f} 元",
            f"- 当日盈亏：{profit:+.2f} 元 ({profit/INITIAL_CAPITAL*100:+.2f}%)",
            "- 历史记录（累计）：",
        ]
        for d, v in sorted(existing.items()):
            lines.append(f"{d}: {v:.2f}")
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write('\n'.join(lines))
    except Exception:
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(fund)))
    return fund


# ---------------------------------------------------------------------------
# JSONL stock files
# ---------------------------------------------------------------------------

def _read_jsonl_stock_file(path: str) -> list:
    if not os.path.isfile(path):
        return []
    out = []
    try:
        text = read_user_text(path)
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    out.append(it)
        elif isinstance(obj, dict):
            out.append(obj)
    return out


def _write_jsonl_stock_file(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, dict):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def get_holdings() -> list:
    return _read_jsonl_stock_file(HOLDING_FILE)


def save_holdings(holdings: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    _write_jsonl_stock_file(HOLDING_FILE, holdings)


def get_optional() -> list:
    return _read_jsonl_stock_file(OPTIONAL_FILE)


def save_optional(optional: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    _write_jsonl_stock_file(OPTIONAL_FILE, optional)


# ---------------------------------------------------------------------------
# Stoploss
# ---------------------------------------------------------------------------

def append_stoploss_record(code: str, name: str, sell_time: str, reason: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    record = {
        "股票代码": code,
        "股票名称": name,
        "止损时间": sell_time,
        "止损原因": reason,
    }
    with open(STOPLOSS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_recent_stoploss(days: int = 5) -> list[dict]:
    if not os.path.isfile(STOPLOSS_FILE):
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    try:
        text = read_user_text(STOPLOSS_FILE)
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("止损时间", "")
        try:
            dt = datetime.strptime(t[:10], "%Y-%m-%d") if len(t) >= 10 else None
        except ValueError:
            dt = None
        if dt and dt >= cutoff:
            records.append(obj)
    return records


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def read_trades(today: str) -> list:
    try:
        return json.loads(read_user_text(f"{DATA_DIR}/trade/{today}/trades.md"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def save_trades(today: str, trades: list):
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    with open(f"{DATA_DIR}/trade/{today}/trades.md", "w", encoding="utf-8") as f:
        f.write(json.dumps(trades, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Trade log
# ---------------------------------------------------------------------------

def _trade_log_file_path() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{DATA_DIR}/trade/{today}/trade_log.md"


def read_trade_log() -> str:
    path = _trade_log_file_path()
    if not os.path.isfile(path):
        return ""
    try:
        return read_user_text(path).strip()
    except OSError:
        return ""


def append_trade_log(action: str, detail: str):
    path = _trade_log_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    time_str = datetime.now().strftime("%H:%M")
    line = f"[{time_str}] {action}: {detail}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Optional archive
# ---------------------------------------------------------------------------

def archive_optional(new_list: list):
    old_list = get_optional()
    old_codes = {r.get("股票代码", "") for r in old_list}
    new_codes = {r.get("股票代码", "") for r in new_list}
    today = datetime.now().strftime("%Y-%m-%d")
    records = []
    for r in new_list:
        code = r.get("股票代码", "")
        if code and code not in old_codes:
            records.append({
                "日期": today, "操作": "新增", "股票代码": code,
                "股票名称": r.get("股票名称", ""),
                "战法": r.get("战法", ""),
                "原因": r.get("加入自选原因", ""),
            })
    old_map = {r.get("股票代码", ""): r for r in old_list}
    for code in old_codes - new_codes:
        r = old_map.get(code, {})
        records.append({
            "日期": today, "操作": "移除", "股票代码": code,
            "股票名称": r.get("股票名称", ""),
            "战法": r.get("战法", ""),
            "原因": "被新一轮筛选替换",
        })
    if records:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(OPTIONAL_HISTORY_FILE, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def read_memory(*, max_chars: int = MEMORY_MAX_INJECT_CHARS) -> str:
    if not os.path.isfile(MEMORY_FILE):
        return ""
    try:
        text = read_user_text(MEMORY_FILE).strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
        nl = text.find("\n")
        if nl > 0:
            text = text[nl + 1:]
    return text


def _count_memory_entries() -> tuple[int, int]:
    if not os.path.isfile(MEMORY_FILE):
        return 0, 0
    try:
        text = read_user_text(MEMORY_FILE).strip()
    except OSError:
        return 0, 0
    entries = [e.strip() for e in text.split("\n\n") if e.strip()]
    return len(entries), len(text)


def compress_memory():
    """当 MEMORY.md 超阈值时调用 LLM 压缩合并。"""
    from quant.llm import call_llm
    entry_count, char_count = _count_memory_entries()
    if entry_count < MEMORY_COMPRESS_THRESHOLD_ENTRIES and char_count < MEMORY_COMPRESS_THRESHOLD_CHARS:
        return
    try:
        text = read_user_text(MEMORY_FILE).strip()
    except OSError:
        return
    system = (
        "你是一名交易经验整理助手。请将以下交易经验教训条目进行压缩合并："
        "相似的合并为一条，保留最有价值的洞察，删除过时或重复内容。"
        "输出格式：每条以日期前缀开头（合并的用最近日期），每条1-2句话。"
        "总条目控制在15条以内。不要输出任何前缀说明。"
    )
    try:
        compressed = call_llm(system, text, max_tokens=1500, temperature=0.1)
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(compressed.strip() + "\n")
        print(f"MEMORY.md 已压缩: {entry_count}条 → 压缩完成")
    except Exception as e:
        print(f"MEMORY.md 压缩失败: {e}")


def extract_and_save_memory(content: str, *, lunch: bool):
    """从复盘输出中提取经验教训。"""
    from quant.llm import call_llm
    if lunch:
        section_text = _extract_section(content, "五、下午操作策略调整")
    else:
        section_text = _extract_section(content, "八、经验及教训总结")
    if not section_text or len(section_text.strip()) < 10:
        return
    system = (
        "你是一名交易经验提炼助手。请将以下内容提炼为1-3条简短经验教训要点。"
        "每条不超过30字，用「·」开头。只输出要点，不要任何前缀或解释。"
        "如果内容没有实质性的经验教训（仅是计划或普通描述），输出「无」。"
    )
    try:
        bullets = call_llm(system, section_text, max_tokens=300, temperature=0.1)
    except Exception as e:
        print(f"MEMORY提炼LLM失败: {e}")
        return
    if not bullets.strip() or bullets.strip() == "无":
        return
    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"{today}\n{bullets.strip()}\n"
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + entry)
    print(f"MEMORY.md 已更新: {entry.strip()[:60]}...")
    compress_memory()


def _extract_section(content: str, header: str) -> str:
    idx = content.find(header)
    if idx < 0:
        return ""
    start = content.find("\n", idx)
    if start < 0:
        return ""
    start += 1
    next_section = re.search(r"\n[一二三四五六七八九十]+、", content[start:])
    if next_section:
        end = start + next_section.start()
    else:
        end = len(content)
    return content[start:end].strip()


# ---------------------------------------------------------------------------
# Trade stats
# ---------------------------------------------------------------------------

def calc_trade_stats(days: int = 30) -> str:
    today = datetime.now()
    profits: list[tuple[str, float]] = []
    for d in range(days):
        date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        path = f"{DATA_DIR}/trade/{date}/profit.md"
        if os.path.isfile(path):
            try:
                val = float(read_user_text(path).strip())
                profits.append((date, val))
            except (OSError, ValueError):
                pass
    if not profits:
        return ""
    profits.reverse()
    total = len(profits)
    wins = sum(1 for _, v in profits if v > 0)
    losses = sum(1 for _, v in profits if v < 0)
    flat = total - wins - losses
    win_rate = wins / total * 100 if total else 0
    avg_win = sum(v for _, v in profits if v > 0) / wins if wins else 0
    avg_loss = abs(sum(v for _, v in profits if v < 0) / losses) if losses else 0
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf") if avg_win > 0 else 0
    streak = 0
    streak_type = ""
    for _, v in reversed(profits):
        if v > 0:
            if streak_type == "win" or not streak_type:
                streak += 1
                streak_type = "win"
            else:
                break
        elif v < 0:
            if streak_type == "loss" or not streak_type:
                streak += 1
                streak_type = "loss"
            else:
                break
        else:
            break
    streak_desc = f"连胜{streak}天" if streak_type == "win" else f"连亏{streak}天" if streak_type == "loss" else "无"
    total_pnl = sum(v for _, v in profits)
    recent_7 = profits[-7:] if len(profits) >= 7 else profits
    r7_wins = sum(1 for _, v in recent_7 if v > 0)
    r7_rate = r7_wins / len(recent_7) * 100 if recent_7 else 0
    return (
        f"近{total}个交易日：胜率{win_rate:.0f}%（{wins}胜{losses}负{flat}平），"
        f"盈亏比{pnl_ratio:.1f}:1，累计盈亏{total_pnl:+.0f}元，{streak_desc}\n"
        f"近{len(recent_7)}日胜率：{r7_rate:.0f}%"
    )


# ---------------------------------------------------------------------------
# Popularity history
# ---------------------------------------------------------------------------

def update_popularity_history(hot_list: list):
    if not hot_list:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(DATA_DIR, exist_ok=True)
    need_header = not os.path.isfile(POPULARITY_FILE)
    with open(POPULARITY_FILE, "a", encoding="utf-8") as f:
        if need_header:
            f.write("| 日期 | 代码 | 名称 | 排名 | 变化 | 连板 |\n")
            f.write("|------|------|------|------|------|------|\n")
        for item in hot_list:
            code = item.get("股票代码", "")
            name = item.get("股票名称", "")
            rank = item.get("人气排名", "")
            change = item.get("人气排名变化", "")
            lb = item.get("连板情况", "")
            if code:
                f.write(f"| {today} | {code} | {name} | {rank} | {change} | {lb} |\n")


def read_popularity_summary(min_days: int = 3) -> str:
    if not os.path.isfile(POPULARITY_FILE):
        return ""
    try:
        text = read_user_text(POPULARITY_FILE)
    except OSError:
        return ""
    stock_days: dict[str, set[str]] = defaultdict(set)
    stock_info: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| 日期") or line.startswith("|--"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        date, code, name, rank = parts[1], parts[2], parts[3], parts[4]
        if code and date:
            stock_days[code].add(date)
            stock_info[code] = {"名称": name, "最近排名": rank}
    hot_stocks = []
    for code, days_set in stock_days.items():
        if len(days_set) >= min_days:
            info = stock_info.get(code, {})
            hot_stocks.append((code, info.get("名称", ""), len(days_set), info.get("最近排名", "")))
    if not hot_stocks:
        return ""
    hot_stocks.sort(key=lambda x: x[2], reverse=True)
    lines = []
    for code, name, count, rank in hot_stocks[:10]:
        lines.append(f"{name}({code})累计上榜{count}天，最近排名{rank}")
    return "重点关注个股（长期上榜）：" + "；".join(lines)


# ---------------------------------------------------------------------------
# Tail builders (context injected into LLM user messages)
# ---------------------------------------------------------------------------

def tail_fund_only() -> str:
    sl = read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    memory = read_memory()
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【近期止损记录】：{sl_s}{mem_part}"


def tail_during_market() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    sl = read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = read_trade_log()
    memory = read_memory()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【今日交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


def tail_lunch_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    sl = read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = read_trade_log()
    memory = read_memory()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【上午交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


def tail_evening_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无交易"
    sl = read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = read_trade_log()
    memory = read_memory()
    stats = calc_trade_stats()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    stats_part = f"\n【近期交易统计】：\n{stats}" if stats else ""
    return (
        f"\n\n【当前资金】：{get_fund():.2f} 元\n"
        f"【初始本金】：{INITIAL_CAPITAL}元\n"
        f"【今日实际交易记录】：\n{tr_s}\n"
        f"【近期止损记录】：{sl_s}{log_part}{stats_part}{mem_part}"
    )
