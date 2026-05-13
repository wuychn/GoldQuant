"""文件读写：资金、持仓、自选、止损、交易记录、记忆。"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from quant.config import (
    ACCOUNT_STATE_FILE, DATA_DIR, FUND_FILE, HOLDING_FILE, INITIAL_CAPITAL,
    MEMORY_COMPRESS_THRESHOLD_CHARS,
    MEMORY_COMPRESS_THRESHOLD_ENTRIES, MEMORY_FILE, MEMORY_MAX_INJECT_CHARS,
    NEWS_IMPACT_SUMMARY_FILE, OPTIONAL_FILE, OPTIONAL_HISTORY_FILE,
    POPULARITY_FILE, POSITION_MV_FILE, STOPLOSS_FILE,
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
# Fund / account state
# fund.md = 现金（纯数字），position_market_value.md = 持仓总市值（纯数字），
# account_state.json = 与上次原子写入一致的快照。
# get_fund() = 现金 + 持仓市值（磁盘口径）。
# ---------------------------------------------------------------------------

def _format_plain_balance(value: float) -> str:
    s = f"{value:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _parse_plain_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "").replace("，", ""))
    except ValueError:
        return None


def compute_holdings_market_value(holdings: list) -> float:
    """按持仓数量 ×（盘口最新价，缺省用买入价）估算总市值。"""
    total = 0.0
    for h in holdings:
        try:
            qty = int(h.get("持仓股数", h.get("数量", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        pk = h.get("盘口") if isinstance(h.get("盘口"), dict) else {}
        p = pk.get("最新") if pk else None
        if p is None:
            p = h.get("买入价", 0)
        try:
            p = float(p)
        except (TypeError, ValueError):
            p = 0.0
        total += qty * p
    return total


def _read_account_state_json() -> tuple[float, float] | None:
    if not os.path.isfile(ACCOUNT_STATE_FILE):
        return None
    try:
        d = json.loads(read_user_text(ACCOUNT_STATE_FILE))
        return float(d["cash"]), float(d["position_market_value"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _read_plain_cash_mv_files() -> tuple[float, float] | None:
    """无 account_state 时读两镜像个位数；fund.md 必填一条有效数字才认。"""
    if not os.path.isfile(FUND_FILE):
        return None
    try:
        raw = read_user_text(FUND_FILE)
    except OSError:
        return None
    cash = _parse_plain_float(raw)
    if cash is None:
        return None
    mv = 0.0
    if os.path.isfile(POSITION_MV_FILE):
        try:
            mvp = _parse_plain_float(read_user_text(POSITION_MV_FILE))
            if mvp is not None:
                mv = mvp
        except OSError:
            pass
    return cash, mv


def _load_cash_mv_pair() -> tuple[float, float]:
    """优先 account_state.json，否则 fund.md + position_market_value.md，否则初始本金。"""
    got = _read_account_state_json()
    if got is not None:
        return got
    pair = _read_plain_cash_mv_files()
    if pair is not None:
        return pair
    return float(INITIAL_CAPITAL), 0.0


def get_cash() -> float:
    return _load_cash_mv_pair()[0]


def get_position_market_value() -> float:
    return _load_cash_mv_pair()[1]


def get_total_equity() -> float:
    c, m = _load_cash_mv_pair()
    return c + m


def get_fund() -> float:
    """账户总权益（现金 + 磁盘持仓市值快照），用于 tail/提示等。"""
    return get_total_equity()


def atomic_save_holdings_and_account_state(
    holdings: list,
    cash: float,
    position_mv: float,
    *,
    last_batch_realized_pnl: float | None = None,
) -> None:
    """同一批写入口径：先临时文件，再 os.replace，降低半截更新概率。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    cash = max(0.0, float(cash))
    position_mv = max(0.0, float(position_mv))
    equity = cash + position_mv
    ts = datetime.now().isoformat(timespec="seconds")
    state = {
        "cash": cash,
        "position_market_value": position_mv,
        "total_equity": equity,
        "updated_at": ts,
    }
    if last_batch_realized_pnl is not None:
        state["last_batch_realized_pnl"] = last_batch_realized_pnl
    h_tmp = HOLDING_FILE + ".tmp"
    st_tmp = ACCOUNT_STATE_FILE + ".tmp"
    cash_tmp = FUND_FILE + ".tmp"
    mv_tmp = POSITION_MV_FILE + ".tmp"
    _write_jsonl_stock_file(h_tmp, holdings)
    with open(st_tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    with open(cash_tmp, "w", encoding="utf-8") as f:
        f.write(_format_plain_balance(cash))
    with open(mv_tmp, "w", encoding="utf-8") as f:
        f.write(_format_plain_balance(position_mv))
    os.replace(h_tmp, HOLDING_FILE)
    os.replace(st_tmp, ACCOUNT_STATE_FILE)
    os.replace(cash_tmp, FUND_FILE)
    os.replace(mv_tmp, POSITION_MV_FILE)


def persist_account_cash_and_mv(
    cash: float,
    position_mv: float,
    *,
    last_batch_realized_pnl: float | None = None,
) -> None:
    """不写持仓时，仅同步现金与市值快照（须与 holding.jsonl 一致，由调用方保证）。"""
    holdings = get_holdings()
    atomic_save_holdings_and_account_state(
        holdings,
        cash,
        position_mv,
        last_batch_realized_pnl=last_batch_realized_pnl,
    )


def set_total_equity(equity: float) -> None:
    """按目标总权益反推现金（总权益−当前持仓市值）；现金不低于 0。"""
    holdings = get_holdings()
    mv = compute_holdings_market_value(holdings)
    cash = max(0.0, float(equity) - mv)
    atomic_save_holdings_and_account_state(
        holdings,
        cash,
        mv,
        last_batch_realized_pnl=None,
    )


def update_fund(profit: float) -> None:
    """在现金上叠加盈亏（复盘外少用）；现金不低于 0。"""
    rows = get_holdings()
    cash = max(0.0, get_cash() + float(profit))
    mv = compute_holdings_market_value(rows)
    atomic_save_holdings_and_account_state(
        rows,
        cash,
        mv,
        last_batch_realized_pnl=None,
    )


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


def merge_holdings_by_code(holdings: list) -> list:
    """同一股票代码合并为一行：股数加总，买入价为加权平均成本。"""
    buckets: dict[str, dict] = {}
    order: list[str] = []
    for h in holdings:
        if not isinstance(h, dict):
            continue
        code = str(h.get("股票代码", "")).strip()
        if not code:
            continue
        try:
            qty = int(h.get("持仓股数", h.get("数量", 0)) or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            bp = float(h.get("买入价", 0) or 0)
        except (TypeError, ValueError):
            bp = 0.0
        if code not in buckets:
            buckets[code] = {"tpl": dict(h), "qty": 0, "cost": 0.0}
            order.append(code)
        b = buckets[code]
        b["qty"] += qty
        b["cost"] += qty * bp
    out: list[dict] = []
    for code in order:
        b = buckets[code]
        row = dict(b["tpl"])
        q = b["qty"]
        row["持仓股数"] = q
        row["买入价"] = round(b["cost"] / q, 6) if q else 0.0
        out.append(row)
    return out


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
# Trades：trades_buy.md / trades_sell.md（JSON 数组）
# ---------------------------------------------------------------------------

def _trade_buy_path(today: str) -> str:
    return f"{DATA_DIR}/trade/{today}/trades_buy.md"


def _trade_sell_path(today: str) -> str:
    return f"{DATA_DIR}/trade/{today}/trades_sell.md"


def _safe_read_json_trade_list(path: str) -> list:
    try:
        data = json.loads(read_user_text(path))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def _append_trades_file_atomic(path: str, new_rows: list[dict]) -> None:
    if not new_rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cur = _safe_read_json_trade_list(path)
    cur.extend(new_rows)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _trade_entry_sort_key(entry: dict) -> tuple[str, str]:
    d = str(entry.get("日期", "") or "")
    t = str(entry.get("时间", "") or "00:00:00")
    return d, t


def read_trades(today: str) -> list:
    merged = (
        _safe_read_json_trade_list(_trade_buy_path(today))
        + _safe_read_json_trade_list(_trade_sell_path(today))
    )
    merged.sort(key=_trade_entry_sort_key)
    return merged


def sum_today_realized_pnl(today: str) -> float:
    """当日成交明细中「已实现盈亏」合计（买入一般为 0）。"""
    total = 0.0
    for e in read_trades(today):
        v = e.get("已实现盈亏", 0)
        try:
            total += float(v)
        except (TypeError, ValueError):
            pass
    return total


def sync_profit_md_from_trades(today: str) -> None:
    """由当日成交汇总写入 profit.md（供统计与推送 tail 使用）。"""
    p = sum_today_realized_pnl(today)
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    path = f"{DATA_DIR}/trade/{today}/profit.md"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(p))
    os.replace(tmp, path)


def append_trades(today: str, entries: list[dict]) -> None:
    """买入写入 trades_buy.md，卖出写入 trades_sell.md。"""
    if not entries:
        return
    buys = [e for e in entries if e.get("方向") == "买入"]
    sells = [e for e in entries if e.get("方向") == "卖出"]
    if buys:
        _append_trades_file_atomic(_trade_buy_path(today), buys)
    if sells:
        _append_trades_file_atomic(_trade_sell_path(today), sells)


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

def archive_optional(new_list: list, old_list: list | None = None):
    """记录自选新增/移除。若提供 old_list，则与 new_list 比较；否则读取磁盘当前自选。"""
    old_list = old_list if old_list is not None else get_optional()
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

def _account_context_tail() -> str:
    c, m = _load_cash_mv_pair()
    te = c + m
    return (
        f"【现金】{c:.2f} 元 【持仓市值】{m:.2f} 元 【总权益】{te:.2f} 元"
    )


def tail_fund_only() -> str:
    sl = read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    memory = read_memory()
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n{_account_context_tail()}\n【近期止损记录】：{sl_s}{mem_part}"


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
    return f"\n\n{_account_context_tail()}\n【今日交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


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
    return f"\n\n{_account_context_tail()}\n【上午交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


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
        f"\n\n{_account_context_tail()}\n"
        f"【初始本金】：{INITIAL_CAPITAL}元\n"
        f"【今日实际交易记录】：\n{tr_s}\n"
        f"【近期止损记录】：{sl_s}{log_part}{stats_part}{mem_part}"
    )
