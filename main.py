#!/usr/bin/env python3
"""
A股短线交易机器人 - 自主决策版
- 新闻/盘前/盘中/午间复盘/晚间复盘完整推送
- 自主选股 + 持仓管理
- 模拟交易 + 资金跟踪
- 选股范围：60/00/30开头主板
"""

# =====================================================================
# 1. IMPORTS & CONSTANTS
# =====================================================================
import ast
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

# 禁用代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        del os.environ[k]

import requests

from app.core.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parent
STRATEGY_FILE = _PROJECT_ROOT / "strategy.md"

BASE_URL = "http://localhost:8085"
FEISHU_APP_ID = "cli_a96dcfa5d3f91bd4"
FEISHU_APP_SECRET = "eXhbDo1Ldh4sMGkBjVUjdhAiiBFZ6ld6"
FEISHU_USER_ID = "ou_bc3cefb641bbc53148de964a637d8cfd"

DATA_DIR = os.path.expanduser("~/.quant")
FUND_FILE = f"{DATA_DIR}/fund.md"
OPTIONAL_FILE = f"{DATA_DIR}/optional.jsonl"
HOLDING_FILE = f"{DATA_DIR}/holding.jsonl"
STOPLOSS_FILE = f"{DATA_DIR}/stoploss.jsonl"
INITIAL_CAPITAL = 10000
OPTIONAL_HISTORY_FILE = f"{DATA_DIR}/optional_history.jsonl"
POPULARITY_FILE = f"{DATA_DIR}/popularity_history.md" # TODO 一只票很久没上榜了是否会更新，是否会剔除？
NEWS_IMPACT_SUMMARY_FILE = f"{DATA_DIR}/news_market_impact_summary.txt"

OPTIONAL_STRATEGY_ALLOWED = frozenset({"涨停板战法", "龙回头战法"})

LLM_OUTPUT_FORMAT = "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。\n"

_LLM_PARALLEL_WORKERS = max(2, min(8, (os.cpu_count() or 4)))

MEMORY_FILE = f"{DATA_DIR}/MEMORY.md"
MEMORY_MAX_INJECT_CHARS = 2000
MEMORY_COMPRESS_THRESHOLD_ENTRIES = 30
MEMORY_COMPRESS_THRESHOLD_CHARS = 3000

_RE_THINKING = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


def _persona(fund: float | None = None) -> str:
    f = fund if fund is not None else get_fund()
    return (
        f"你是一名 A 股实盘短线交易员，操盘资金 {f:.0f} 元。"
        "你严格执行策略条文，纪律严明、知行合一，目标是资产大幅增值。\n"
    )


_ZT_ISOLATION = "【隔离】本任务仅限涨停板战法条文，禁止引用龙回头战法的均线回踩、MACD、量比等口径。\n"
_LHT_ISOLATION = "【隔离】本任务仅限龙回头战法条文，禁止引用涨停板战法的人气前十、封板、概念共振等口径。\n"


# =====================================================================
# 2. FILE I/O & TEXT UTILS
# =====================================================================
def _read_user_text(path) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _unwrap_payload(raw: dict) -> dict:
    inner = raw.get("data")
    return inner if isinstance(inner, dict) else raw


# =====================================================================
# 3. FEISHU
# =====================================================================
def get_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise Exception("获取token失败")
    return result["tenant_access_token"]


def send_msg(content: str, token: str):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"receive_id": FEISHU_USER_ID, "msg_type": "text", "content": json.dumps({"text": content})}
    resp = requests.post(url, headers=headers, json=data, timeout=600)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"发送失败: {result}")


# =====================================================================
# 4. LLM ENGINE
# =====================================================================
def call_llm(
    system: str,
    user: str,
    max_tokens: int = 16000,
    retries: int = 3,
    *,
    temperature: float | None = None,
) -> str:
    cfg = get_settings()
    api_key = (cfg.LLM_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中设置")
    base = cfg.LLM_BASE_URL.rstrip("/")
    url = f"{base}/v1/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    temp = 0.3 if temperature is None else float(temperature)
    payload = {
        "model": cfg.LLM_MODEL,
        "messages": [{"role": "user", "content": f"{system}\n\n{user}"}],
        "max_tokens": max_tokens,
        "temperature": temp,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=600)
            if resp.status_code == 529:
                print(f"LLM限流，重试({attempt+1}/{retries})...")
                time.sleep(5)
                continue
            resp.raise_for_status()
            result = resp.json()
            # 提取 text 类型的 content block
            for c in result.get("content", []):
                if c.get("type") == "text":
                    text = _RE_THINKING.sub("", c["text"]).strip()
                    if text:
                        return text
            # 没有 text block：thinking 占满了 max_tokens 或模型异常
            stop = result.get("stop_reason", "")
            if attempt < retries - 1:
                print(f"LLM输出无文本(stop_reason={stop})，重试({attempt+1}/{retries})...")
                time.sleep(3)
                continue
            print(f"LLM响应无text内容 stop_reason={stop} model={cfg.LLM_MODEL}")
            raise Exception(f"LLM响应无有效文本(stop_reason={stop})")
        except requests.exceptions.RequestException as e:
            print(f"LLM请求异常: {e}")
            if attempt < retries - 1:
                time.sleep(5)
    raise Exception("LLM调用失败")


def _parallel_call(*fns: Callable[[], str]) -> list[str]:
    if not fns:
        return []
    n = min(_LLM_PARALLEL_WORKERS, len(fns))
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(fn) for fn in fns]
        return [fu.result() for fu in futs]


# =====================================================================
# 5. DATA FETCH
# =====================================================================
def fetch_data(endpoint: str) -> dict:
    resp = requests.get(f"{BASE_URL}{endpoint}", timeout=600)
    resp.raise_for_status()
    return resp.json()


def fetch_news():
    return fetch_data("/api/v1/quant/market/news")


def fetch_pre_market():
    return fetch_data("/api/v1/quant/market/pre_market")


def fetch_during_market():
    return fetch_data("/api/v1/quant/market/during_market")


def fetch_post_market():
    return fetch_data("/api/v1/quant/market/post_market")


# =====================================================================
# 6. FUND & HOLDINGS MANAGEMENT
# =====================================================================
def get_fund() -> float:
    try:
        return float(_read_user_text(FUND_FILE).strip())
    except (OSError, ValueError, TypeError):
        return INITIAL_CAPITAL


def update_fund(profit: float):
    fund = get_fund() + profit
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = {}
    try:
        content = _read_user_text(FUND_FILE)
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


def _read_jsonl_stock_file(path: str) -> list:
    if not os.path.isfile(path):
        return []
    out = []
    try:
        text = _read_user_text(path)
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


def _append_stoploss_record(code: str, name: str, sell_time: str, reason: str):
    """追加一条止损记录到 stoploss.jsonl（用于冷却期判断）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    record = {
        "股票代码": code,
        "股票名称": name,
        "止损时间": sell_time,
        "止损原因": reason,
    }
    with open(STOPLOSS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_recent_stoploss(days: int = 5) -> list[dict]:
    """读取近 N 自然日内的止损记录（供冷却期判断）。"""
    if not os.path.isfile(STOPLOSS_FILE):
        return []
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    try:
        text = _read_user_text(STOPLOSS_FILE)
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


def read_trades(today: str) -> list:
    try:
        return json.loads(_read_user_text(f"{DATA_DIR}/trade/{today}/trades.md"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def save_trades(today: str, trades: list):
    """保存交易记录（预留接口，供未来自动交易模块调用）。"""
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    with open(f"{DATA_DIR}/trade/{today}/trades.md", "w", encoding="utf-8") as f:
        f.write(json.dumps(trades, ensure_ascii=False, indent=2))


def _tail_fund_only() -> str:
    sl = _read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    memory = _read_memory()
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【近期止损记录】：{sl_s}{mem_part}"


def _tail_during_market() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    sl = _read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = _read_trade_log()
    memory = _read_memory()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【今日交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


def _tail_lunch_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    sl = _read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = _read_trade_log()
    memory = _read_memory()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【上午交易记录】：{tr_s}\n【近期止损记录】：{sl_s}{log_part}{mem_part}"


def _tail_evening_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无交易"
    sl = _read_recent_stoploss()
    sl_s = json.dumps(sl, ensure_ascii=False) if sl else "无"
    trade_log = _read_trade_log()
    memory = _read_memory()
    stats = _calc_trade_stats()
    log_part = f"\n【今日操作记录】：\n{trade_log}" if trade_log else ""
    mem_part = f"\n【历史经验教训】：\n{memory}" if memory else ""
    stats_part = f"\n【近期交易统计】：\n{stats}" if stats else ""
    return (
        f"\n\n【当前资金】：{get_fund():.2f} 元\n"
        f"【初始本金】：{INITIAL_CAPITAL}元\n"
        f"【今日实际交易记录】：\n{tr_s}\n"
        f"【近期止损记录】：{sl_s}{log_part}{stats_part}{mem_part}"
    )


# =====================================================================
# 6B. TRADE LOG & MEMORY
# =====================================================================
def _trade_log_file_path() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"{DATA_DIR}/trade/{today}/trade_log.md"


def _read_trade_log() -> str:
    """读取当天操作日志（加自选/买入/卖出等实际操作记录）。"""
    path = _trade_log_file_path()
    if not os.path.isfile(path):
        return ""
    try:
        return _read_user_text(path).strip()
    except OSError:
        return ""


def _append_trade_log(action: str, detail: str):
    """追加一条操作记录到当日 trade_log.md。
    action: 加自选/移除自选/买入/卖出/加仓/减仓
    detail: 股票名称(代码) + 关键信息
    """
    path = _trade_log_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    time_str = datetime.now().strftime("%H:%M")
    line = f"[{time_str}] {action}: {detail}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _archive_optional(new_list: list):
    """对比新旧自选列表，将变更记录追加到 optional_history.jsonl。"""
    old_list = get_optional()
    old_codes = {r.get("股票代码", "") for r in old_list}
    new_codes = {r.get("股票代码", "") for r in new_list}
    today = datetime.now().strftime("%Y-%m-%d")
    records = []
    # 新增的
    for r in new_list:
        code = r.get("股票代码", "")
        if code and code not in old_codes:
            records.append({
                "日期": today, "操作": "新增", "股票代码": code,
                "股票名称": r.get("股票名称", ""),
                "战法": r.get("战法", ""),
                "原因": r.get("加入自选原因", ""),
            })
    # 移除的
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


def _read_memory(*, max_chars: int = MEMORY_MAX_INJECT_CHARS) -> str:
    """读取 MEMORY.md 最近 max_chars 字符（最新条目优先）。"""
    if not os.path.isfile(MEMORY_FILE):
        return ""
    try:
        text = _read_user_text(MEMORY_FILE).strip()
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
    """返回 (条目数, 字符数)。"""
    if not os.path.isfile(MEMORY_FILE):
        return 0, 0
    try:
        text = _read_user_text(MEMORY_FILE).strip()
    except OSError:
        return 0, 0
    entries = [e.strip() for e in text.split("\n\n") if e.strip()]
    return len(entries), len(text)


def _compress_memory():
    """当 MEMORY.md 超阈值时调用 LLM 压缩合并。"""
    entry_count, char_count = _count_memory_entries()
    if entry_count < MEMORY_COMPRESS_THRESHOLD_ENTRIES and char_count < MEMORY_COMPRESS_THRESHOLD_CHARS:
        return
    try:
        text = _read_user_text(MEMORY_FILE).strip()
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


def _extract_section(content: str, header: str) -> str:
    """按节标题切分内容，提取 header 到下一节之间的文本。"""
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


def _extract_and_save_memory(content: str, *, lunch: bool):
    """从复盘输出中提取经验教训，调用 LLM 提炼后追加到 MEMORY.md。"""
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
    _compress_memory()


def _calc_trade_stats(days: int = 30) -> str:
    """从 trade/ 目录读取近N日 profit.md，计算胜率/盈亏比/连胜连亏。"""
    from datetime import timedelta
    today = datetime.now()
    profits: list[tuple[str, float]] = []
    for d in range(days):
        date = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        path = f"{DATA_DIR}/trade/{date}/profit.md"
        if os.path.isfile(path):
            try:
                val = float(_read_user_text(path).strip())
                profits.append((date, val))
            except (OSError, ValueError):
                pass
    if not profits:
        return ""
    profits.reverse()  # 按时间正序
    total = len(profits)
    wins = sum(1 for _, v in profits if v > 0)
    losses = sum(1 for _, v in profits if v < 0)
    flat = total - wins - losses
    win_rate = wins / total * 100 if total else 0
    # 盈亏比
    avg_win = sum(v for _, v in profits if v > 0) / wins if wins else 0
    avg_loss = abs(sum(v for _, v in profits if v < 0) / losses) if losses else 0
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf") if avg_win > 0 else 0
    # 连胜/连亏
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
    # 近7日统计
    recent_7 = profits[-7:] if len(profits) >= 7 else profits
    r7_wins = sum(1 for _, v in recent_7 if v > 0)
    r7_rate = r7_wins / len(recent_7) * 100 if recent_7 else 0
    return (
        f"近{total}个交易日：胜率{win_rate:.0f}%（{wins}胜{losses}负{flat}平），"
        f"盈亏比{pnl_ratio:.1f}:1，累计盈亏{total_pnl:+.0f}元，{streak_desc}\n"
        f"近{len(recent_7)}日胜率：{r7_rate:.0f}%"
    )


def _update_popularity_history(hot_list: list):
    """将人气榜数据追加到 popularity_history.md（每日一批）。"""
    if not hot_list:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(DATA_DIR, exist_ok=True)
    # 检查文件是否存在，不存在则写表头
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


def _read_popularity_summary(min_days: int = 3) -> str:
    """读取 popularity_history.md，统计上榜≥min_days天的个股。"""
    if not os.path.isfile(POPULARITY_FILE):
        return ""
    try:
        text = _read_user_text(POPULARITY_FILE)
    except OSError:
        return ""
    # 统计每只股票的上榜天数和最近排名
    from collections import defaultdict
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
    # 筛选上榜天数≥min_days的
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


# =====================================================================
# 7. STRATEGY LOADER
# =====================================================================
def load_strategy() -> str:
    try:
        return STRATEGY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return f"策略文件加载失败：{STRATEGY_FILE}（{e!r}）"


def _strategy_split_sections(full: str) -> dict[str, str]:
    """按 ## 标记切分 strategy.md，返回 {section_name: content}。"""
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^## (.+)$", full, re.MULTILINE))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
        sections[name] = full[start:end].strip().strip("-").strip()
    return sections


def _load_sections(*names: str) -> str:
    """按 section 名称加载策略条文并拼接。"""
    full = load_strategy()
    if full.startswith("策略文件加载失败"):
        return full
    sections = _strategy_split_sections(full)
    parts = [sections[n] for n in names if n in sections and sections[n]]
    return "\n\n---\n\n".join(parts)


# =====================================================================
# 8. DATA FILTER (per-lane payload filtering)
# =====================================================================
_HOT_STOCK_SLIM_KEYS = frozenset({
    "市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
    "人气排名变化", "所属概念", "连板情况",
})


def _slim_hot_stock(item: dict) -> dict:
    """保留人气股的核心排名字段，移除盘口/历史行情/资金流等重数据。"""
    return {k: v for k, v in item.items() if k in _HOT_STOCK_SLIM_KEYS}


def _trim_history_bars(item: dict, max_bars: int = 5) -> dict:
    """截断历史行情到最近 N 条，用于涨停板战法的价格检查。"""
    out = dict(item)
    hist = out.get("历史行情")
    if isinstance(hist, list) and len(hist) > max_bars:
        out["历史行情"] = hist[-max_bars:]
    return out


def _slim_stock_metadata(item: dict) -> dict:
    """仅保留自选/持仓股的元数据（代码、名称、战法、原因），移除盘口等 enrichment。"""
    keys = {"股票代码", "股票名称", "战法", "加入自选原因", "买入时间", "买入价", "买入原因"}
    return {k: v for k, v in item.items() if k in keys}


def _filter_stocks_by_strategy(items: list, strategy: str) -> list:
    """按战法字段筛选股票列表。"""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("战法", "") or "").strip()
        reason = str(item.get("加入自选原因", "") or item.get("买入原因", "") or "").strip()
        if tag == strategy:
            out.append(item)
        elif not tag or tag == "未标注":
            if strategy == "涨停板战法" and reason.startswith("【涨停板战法】"):
                out.append(item)
            elif strategy == "龙回头战法" and reason.startswith("【龙回头战法】"):
                out.append(item)
    return out


def _hot_stock_for_zt_optional(item: dict) -> dict:
    """涨停板战法加自选：保留元数据 + 历史行情(最近5条用于价格检查) + 所属概念。"""
    keep = {"市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
            "人气排名变化", "所属概念", "连板情况", "历史行情"}
    out = {k: v for k, v in item.items() if k in keep}
    return _trim_history_bars(out, 5)


def _hot_stock_for_lht_optional(item: dict) -> dict:
    """龙回头战法加自选：保留历史行情(30d) + 技术指标 + 个股资金流。"""
    keep = {"市场", "股票代码", "股票名称", "热度", "涨跌", "人气排名",
            "人气排名变化", "所属概念", "连板情况",
            "历史行情", "技术指标", "个股资金流"}
    return {k: v for k, v in item.items() if k in keep}


def filter_payload(payload: dict, lane: str) -> dict:
    """按并行 lane 过滤数据，仅保留该 lane 需要的字段，大幅减少 LLM 输入量。"""
    p = payload
    hot = p.get("同花顺人气榜", [])
    zxg = p.get("自选股", [])
    ccg = p.get("持仓股", [])

    if lane == "narrative":
        # 叙事：所有顶层 key 保留，但人气榜精简，自选/持仓保留全部
        out = dict(p)
        if hot:
            out["同花顺人气榜"] = [_slim_hot_stock(h) for h in hot]
        return out

    if lane == "zt_optional":
        # 涨停板加自选：人气榜 top20 + 涨停统计 + 概念板块
        return {
            "同花顺人气榜": [_hot_stock_for_zt_optional(h) for h in hot[:20]],
            "涨停统计": p.get("涨停统计", []),
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "lht_optional":
        # 龙回头加自选：人气榜全量（含历史+技术指标+资金流）
        return {
            "同花顺人气榜": [_hot_stock_for_lht_optional(h) for h in hot],
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "overview":
        # 市场概览：大盘数据，不含个股
        return {
            "大盘指数": p.get("大盘指数"),
            "赚钱效应": p.get("赚钱效应"),
            "大盘资金流": p.get("大盘资金流"),
            "涨停统计": p.get("涨停统计"),
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "zt_buy":
        # 涨停板买入：ZT自选 + 人气榜top10精简 + 概念板块 + 涨停统计
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "涨停板战法"),
            "同花顺人气榜": [_slim_hot_stock(h) for h in hot[:10]],
            "概念板块": p.get("概念板块", {}),
            "涨停统计": p.get("涨停统计", []),
        }

    if lane == "lht_buy":
        # 龙回头买入：LHT自选（含盘口/历史/技术指标/资金流/10分钟线）+ 大盘资金流
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "龙回头战法"),
            "大盘资金流": p.get("大盘资金流"),
        }

    if lane == "zt_hold":
        # 涨停板持仓监控：仅ZT持仓 + 概念板块（无匹配则空列表，不回退全量）
        return {
            "持仓股": _filter_stocks_by_strategy(ccg, "涨停板战法"),
            "概念板块": p.get("概念板块", {}),
        }

    if lane == "lht_hold":
        # 龙回头持仓监控：仅LHT持仓（无匹配则空列表，不回退全量）
        return {
            "持仓股": _filter_stocks_by_strategy(ccg, "龙回头战法"),
        }

    if lane == "positions":
        # 持仓更新：全量持仓 + 精简自选 + 市场状态机
        return {
            "持仓股": ccg,
            "自选股": [_slim_stock_metadata(s) for s in zxg],
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "pre_main":
        # 盘前主叙事：大盘指数 + 自选/持仓仅元数据 + 市场状态机
        return {
            "大盘指数": p.get("大盘指数"),
            "自选股": [_slim_stock_metadata(s) for s in zxg],
            "持仓股": [_slim_stock_metadata(s) for s in ccg],
            "市场状态机": p.get("市场状态机"),
        }

    if lane == "pre_zt":
        # 盘前涨停板：仅ZT自选（含盘口/历史行情/集合竞价），不含人气榜/概念/涨停统计
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "涨停板战法"),
        }

    if lane == "pre_lht":
        # 盘前龙回头：仅LHT自选（含历史行情/技术指标/盘口/个股资金流），不含大盘资金流
        return {
            "自选股": _filter_stocks_by_strategy(zxg, "龙回头战法"),
        }

    # 未知 lane，返回全量
    return p


# =====================================================================
# 9. PROMPTS
# =====================================================================

def _news_summary_for_prompt(*, max_chars: int = 800) -> str:
    if not os.path.isfile(NEWS_IMPACT_SUMMARY_FILE):
        return "\n\n【当日新闻摘要】\n（尚无新闻摘要）"
    try:
        text = _read_user_text(NEWS_IMPACT_SUMMARY_FILE).strip()
    except OSError:
        text = ""
    if not text:
        return "\n\n【当日新闻摘要】\n（尚无新闻摘要）"
    if len(text) > max_chars:
        text = text[:max_chars]
    return f"\n\n【当日新闻摘要】\n{text}"


def _build_user_msg(payload: dict, *, tail: str, include_news: bool = True) -> str:
    """组装 user message：[新闻摘要] → JSON → 尾部（资金/交易记录）。"""
    parts = []
    if include_news:
        parts.append(_news_summary_for_prompt())
    parts.append("\n\n【以下为接口业务数据 JSON】\n")
    parts.append(json.dumps(payload, ensure_ascii=False)[:140000])
    parts.append(tail)
    return "".join(parts)


# --- 新闻 ---
def _prompt_news_system() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        _persona()
        + f"当前日期：{today}。你正在进行每日新闻研读。请对以下多渠道新闻去噪、去重、提炼。\n\n"
        + "【输出要求】\n"
        + "1. 按重要度降序排列，输出前50条，每条仅写标题或核心内容（一句话）。\n"
        + "2. 列完全部条目后，最后统一输出一段「综合解读」，站在短线交易者角度分析对大盘/板块/情绪的影响及操作方向。\n"
        + "3. 禁止对每一条新闻单独写解读，只在最后给一个整体解读。\n"
        + "4. 严格纯文本格式：\n\n"
        + "1、{新闻标题或核心内容}\n2、{标题}\n……\n50、{标题}\n\n"
        + "综合解读：{从宏观政策、行业板块、市场情绪三个维度，结合上述新闻进行整体研判，"
        + "给出对今日盘面的影响预判和短线操作方向建议，150字以内}\n"
        + LLM_OUTPUT_FORMAT
    )


# --- 盘前 ---
def _prompt_pre_market_main() -> str:
    return (
        _persona()
        + "【当前任务】盘前决策——撰写一至三、六、七（不写四、五）。七含持仓 JSON。\n"
        + "【数据说明】JSON 含大盘指数、自选股（仅元数据：代码/名称/战法/原因）、持仓股（仅元数据）、市场状态机。\n\n"
        + "【策略条文】\n" + _load_sections("共用约束", "市场状态机", "仓位联动") + "\n\n"
        + "请严格按下列小节输出（各节紧凑、要点明确）：\n\n"
        + "一、市场整体概览\n"
        + "（上证/深证/创业板指数点位与涨跌幅、市场情绪判定、成交额水平）\n\n"
        + "二、自选股（仅 JSON「自选股」；为空则写「当前无自选股」并说明原因）\n\n"
        + "三、持仓股（仅 JSON「持仓股」；为空则写「当前无持仓」）\n\n"
        + "六、市场判断与当日执行纲要\n\n"
        + "七、【持仓更新】（仅此处输出持仓 JSON 数组）\n"
        + "规则：有变更输出非空 JSON；无需变更输出 []，下一行写「持仓未更新原因：{具体原因}」。\n"
        + '格式：[{"股票代码":"600000","股票名称":"浦发银行","买入时间":"2026-05-03 09:31:00","买入价":11.2,"买入原因":"……","卖出时间":"","卖出价":"","卖出原因":""}]\n'
        + LLM_OUTPUT_FORMAT
    )


def _prompt_pre_market_zt() -> str:
    return (
        _persona()
        + _ZT_ISOLATION
        + "【当前任务】盘前——仅撰写「四、涨停板战法（集合竞价与盘中计划）」小节。\n"
        + "仅针对 JSON「自选股」中战法为涨停板战法的标的。\n"
        + "【数据说明】JSON 含涨停板战法自选股（含盘口、历史行情、集合竞价分钟行情）。"
        + "注意：盘前无人气榜/概念板块/涨停统计，集合竞价判断仅用盘口和集合竞价分钟行情。\n\n"
        + "【策略条文】\n" + _load_sections("涨停板战法-买入") + "\n\n"
        + "【空数据处理】若自选股列表为空或无涨停板战法标的，必须明确说明原因（如：当前无涨停板战法自选股，"
        + "需在复盘时从人气榜筛选加入），不可省略本小节。\n"
        + "【输出】仅输出「四、涨停板战法」及其内容。\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_pre_market_lht() -> str:
    return (
        _persona()
        + _LHT_ISOLATION
        + "【当前任务】盘前——仅撰写「五、龙回头战法（定价与资金口径）」小节。\n"
        + "仅针对 JSON「自选股」中战法为龙回头战法的标的。\n"
        + "【数据说明】JSON 含龙回头战法自选股（含历史行情、技术指标、盘口、个股资金流）。盘前无大盘资金流。\n\n"
        + "【策略条文】\n" + _load_sections("龙回头战法-买入") + "\n\n"
        + "【空数据处理】若自选股列表为空或无龙回头战法标的，必须明确说明原因（如：当前无龙回头战法自选股，"
        + "需在复盘时从人气榜筛选加入），不可省略本小节。\n"
        + "【输出】仅输出「五、龙回头战法」及其内容。\n"
        + LLM_OUTPUT_FORMAT
    )


# --- 盘中 ---
def _prompt_during_overview() -> str:
    return (
        _persona()
        + "【当前任务】盘中——仅输出【市场状态】与【总仓位限制】两段（不写买卖细则）。\n"
        + "【数据说明】JSON 含大盘指数、赚钱效应、大盘资金流、涨停统计、市场状态机。\n\n"
        + "【策略条文】\n" + _load_sections("市场状态机", "仓位联动") + "\n\n"
        + "【输出格式】\n"
        + "【市场状态】{强势/震荡/弱势}（{简述}）\n"
        + "【总仓位限制】按仓位联动 | 当前持仓{x%}\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_during_buy_zt() -> str:
    return (
        _persona()
        + _ZT_ISOLATION
        + "【当前任务】盘中——仅输出【涨停板战法｜盘中买入】整段。\n"
        + "仅适用于 JSON「自选股」中战法为涨停板战法的标的。\n"
        + "【数据说明】JSON 含涨停板战法自选股（含盘口）、同花顺人气榜前10（扁平：排名/所属概念）、概念板块、涨停统计。\n\n"
        + "【策略条文】\n" + _load_sections("涨停板战法-买入") + "\n\n"
        + "【空数据处理】若自选股为空，输出：【涨停板战法｜盘中买入】当前无涨停板战法自选标的，无法执行买入扫描。"
        + "原因：{说明为何为空，如复盘未筛出合格标的、数据不完整等}。不可省略本段。\n"
        + "【输出】以「【涨停板战法｜盘中买入】」开头的完整指令。\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_during_buy_lht() -> str:
    return (
        _persona()
        + _LHT_ISOLATION
        + "【当前任务】盘中——仅输出【龙回头战法｜盘中买入】整段。\n"
        + "仅适用于 JSON「自选股」中战法为龙回头战法的标的。\n"
        + "【数据说明】JSON 含龙回头战法自选股（含历史行情、技术指标、盘口、个股资金流、盘中10分钟线）、大盘资金流。\n\n"
        + "【策略条文】\n" + _load_sections("龙回头战法-买入") + "\n\n"
        + "【空数据处理】若自选股为空，输出：【龙回头战法｜盘中买入】当前无龙回头战法自选标的，无法执行买入扫描。"
        + "原因：{说明为何为空，如复盘未筛出合格标的、数据不完整等}。不可省略本段。\n"
        + "【输出】以「【龙回头战法｜盘中买入】」开头的完整指令。\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_during_hold_zt() -> str:
    return (
        _persona()
        + _ZT_ISOLATION
        + "【当前任务】盘中——仅输出【涨停板战法｜持仓与卖出】整段。\n"
        + "仅针对持仓中与涨停板战法相关的标的。\n"
        + "【数据说明】JSON 含涨停板战法持仓股（含盘口、历史行情、个股资金流）、概念板块。\n\n"
        + "【策略条文】\n" + _load_sections("涨停板战法-持股监控", "涨停板战法-卖出", "仓位联动") + "\n\n"
        + "【空数据处理】若持仓股为空（无涨停板战法持仓），输出：【涨停板战法｜持仓与卖出】当前无涨停板战法持仓。"
        + "不可省略本段。\n"
        + "【输出】以「【涨停板战法｜持仓与卖出】」开头；仅写持仓监控与卖出信号。\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_during_hold_lht() -> str:
    return (
        _persona()
        + _LHT_ISOLATION
        + "【当前任务】盘中——仅输出【龙回头战法｜持仓与卖出】整段。\n"
        + "仅针对持仓中与龙回头战法相关的标的。\n"
        + "【数据说明】JSON 含龙回头战法持仓股（含历史行情、技术指标、盘口、个股资金流、盘中10分钟线）。\n\n"
        + "【策略条文】\n" + _load_sections("龙回头战法-持股监控", "龙回头战法-卖出", "仓位联动") + "\n\n"
        + "【空数据处理】若持仓股为空（无龙回头战法持仓），输出：【龙回头战法｜持仓与卖出】当前无龙回头战法持仓。"
        + "不可省略本段。\n"
        + "【输出】以「【龙回头战法｜持仓与卖出】」开头；仅写持仓监控与卖出。\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_during_positions() -> str:
    return (
        _persona()
        + "【当前任务】盘中——输出【持仓更新】JSON、【账户风控底线】。\n"
        + "【数据说明】JSON 含全量持仓股、精简自选股（仅代码/名称/战法）、市场状态机。\n\n"
        + "【策略条文】\n" + _load_sections("仓位联动", "每日亏损限额") + "\n\n"
        + "【持仓更新】规则：有变更输出非空 JSON；无变更则 [] 及一行「持仓未更新原因」。\n"
        + "格式须含股票代码、名称、买入时间、买入价、买入原因；卖出补全卖出时间与卖出价。\n"
        + "【数据纪律】持仓与委托仅引用 JSON「自选股」「持仓股」。\n"
        + LLM_OUTPUT_FORMAT
    )


# --- 复盘（午间/晚间共用） ---
_ZT_OPTIONAL_EXAMPLE = (
    "\n【筛选示范（仅供理解流程，不可照搬结论）】\n"
    "示例A - 有合格标的：\n"
    "假设人气榜第3名「永杉锂业(603399)」：\n"
    "1) 人气排名=3 ≤20 → 通过\n"
    "2) 在涨停统计中流通市值=50亿 ≤200亿 → 通过\n"
    "3) 涨停统计中连板数=3 ≥2 → 通过（非首板）\n"
    "4) 所属概念[锂电池]匹配涨幅榜前十「锂电池」→ 通过\n"
    "结论：4条全满足，加入自选。\n\n"
    "示例B - 无合格标的：\n"
    "人气榜前20逐只检查（排除ST/科创/北交/新股后剩3只）：\n"
    "1) 金杯电工(002533)：排名8≤20通过 → 流通市值80亿≤200亿通过 → 连板数2≥2通过"
    " → 所属概念[电力设备]不在涨幅榜/资金流入榜前十，不合格\n"
    "2) 中科信息(300678)：排名12≤20通过 → 不在涨停统计中（今日未涨停），非首板不满足，不合格\n"
    "3) 博汇股份(300839)：排名15≤20通过 → 流通市值350亿>200亿，不合格\n"
    "输出：[]\n"
    "涨停板战法自选未更新原因：金杯电工(002533)概念未与主线共振；"
    "中科信息(300678)今日未涨停非首板不满足；博汇股份(300839)流通市值超200亿\n"
)

_LHT_OPTIONAL_EXAMPLE = (
    "\n【筛选示范（仅供理解流程，不可照搬结论）】\n"
    "示例A - 有合格标的：\n"
    "假设人气榜第15名「某某股(000123)」：\n"
    "1) 人气排名=15 ≤50 → 通过\n"
    "2) 历史行情30日内存在涨跌幅=10.02% → 曾涨停 → 通过\n"
    "3) 近30日最高收盘12.0，最新9.8，回落幅度=(12.0-9.8)/12.0=18.3%≥10% → 通过\n"
    "4) 最新收盘价9.8，均线5日9.5，9.8/9.5=103%在[98%,110%]区间；均线5日9.5≥均线10日9.3 → 多头排列通过\n"
    "5) 最后两日收盘>开盘 → 连续收阳 → 通过\n"
    "6) 最新成交额1.2亿 / 前5日均值0.9亿 = 1.33倍，在[1.0,3.0] → 通过\n"
    "7) MACD差离值0.05 > 信号线0.02且>0 → 通过\n"
    "结论：7条全满足，加入自选。\n\n"
    "示例B - 无合格标的：\n"
    "人气榜逐只检查（排除ST/科创/北交/新股后剩3只）：\n"
    "1) 某某股(000456)：排名20≤50通过 → 30日内无涨停经历，不合格\n"
    "2) 另一股(600789)：排名8≤50通过 → 曾涨停通过 → 近30日最高15.0最新14.2，"
    "回落(15.0-14.2)/15.0=5.3%<10%未充分回调，不合格\n"
    "3) 第三股(300456)：排名30≤50通过 → 曾涨停通过 → 回落12%≥10%通过"
    " → 最新收盘6.2/均线5日5.5=112.7%>110%偏离均线过远，不合格\n"
    "输出：[]\n"
    "龙回头战法自选未更新原因：某某股(000456)近30日无涨停；"
    "另一股(600789)回落幅度仅5.3%未充分回调；第三股(300456)偏离5日均线12.7%过远\n"
)


def _prompt_review_optional_zt() -> str:
    return (
        _persona()
        + _ZT_ISOLATION
        + "【当前任务】复盘·仅完成涨停板战法加入自选。\n"
        + "从 JSON「同花顺人气榜」逐只按下列条文筛选，全部条件满足才可加入。\n"
        + "【数据说明】JSON 含同花顺人气榜前20（含历史行情最近5条、所属概念）、涨停统计（含连板数）、概念板块（含涨幅榜/资金流入榜前十）。\n\n"
        + "【策略条文】\n" + _load_sections("涨停板战法-加自选") + "\n"
        + _ZT_OPTIONAL_EXAMPLE + "\n"
        + "【输出格式（严格遵守，缺少原因视为输出不合格）】\n"
        + "情况A - 有合格标的：输出 JSON 数组，每项含 股票代码、股票名称、战法（固定「涨停板战法」）、"
        + "加入自选原因（必须以【涨停板战法】开头，逐条列出4个条件判定）。\n"
        + "情况B - 无合格标的：先输出 []，紧接下一行必须输出：\n"
        + "涨停板战法自选未更新原因：{逐只列出人气榜前20被排除标的的关键不达标条件}\n"
        + "示例：涨停板战法自选未更新原因：永杉锂业(603399)股价21.3元超20元上限；中科信息(300678)非涨停股；金杯电工(002533)概念未与当日主线共振\n"
        + "【强制要求】输出 [] 时下一行的原因说明不可省略、不可只写「均不满足」，必须点名具体标的+具体不达标条件。\n"
        + "【上榜跟踪】若数据中附带【上榜跟踪】信息，对长期上榜个股在同等条件下优先考虑加入自选。\n"
        + "不要输出数组以外的其他正文。"
    )


def _prompt_review_optional_lht() -> str:
    return (
        _persona()
        + _LHT_ISOLATION
        + "【当前任务】复盘·仅完成龙回头战法加入自选。\n"
        + "从 JSON「同花顺人气榜」逐只按下列条文筛选，7个条件全满足才可加入。\n"
        + "【数据说明】JSON 含同花顺人气榜（含历史行情30d、技术指标含MACD和均线、个股资金流）、概念板块。\n\n"
        + "【策略条文】\n" + _load_sections("龙回头战法-加自选") + "\n"
        + _LHT_OPTIONAL_EXAMPLE + "\n"
        + "【输出格式（严格遵守，缺少原因视为输出不合格）】\n"
        + "情况A - 有合格标的：输出 JSON 数组，每项含 股票代码、股票名称、战法（固定「龙回头战法」）、"
        + "加入自选原因（必须以【龙回头战法】开头，逐条列出7个条件判定）。\n"
        + "情况B - 无合格标的：先输出 []，紧接下一行必须输出：\n"
        + "龙回头战法自选未更新原因：{逐只列出主要被排除标的的关键不达标条件}\n"
        + "示例：龙回头战法自选未更新原因：某某股(000123)近30日无涨停经历；另一股(600456)MACD死叉不满足；第三股(300789)量能倍率4.2超上限3.0\n"
        + "【强制要求】输出 [] 时下一行的原因说明不可省略、不可只写「均不满足」，必须点名具体标的+具体不达标条件。\n"
        + "【上榜跟踪】若数据中附带【上榜跟踪】信息，对长期上榜个股在同等条件下优先考虑加入自选。\n"
        + "不要输出数组以外的其他正文。"
    )


def _prompt_evening_narrative() -> str:
    return (
        _persona()
        + "【当前任务】晚间复盘。不要输出「自选更新」或自选 JSON。\n"
        + "【数据说明】JSON 含大盘指数、赚钱效应、大盘资金流(3日)、概念板块、涨停统计、"
        + "同花顺人气榜(精简排名)、自选股(全量)、持仓股(全量)、市场状态机。\n\n"
        + "【策略条文】\n" + _load_sections("共用约束", "市场状态机", "仓位联动", "每日亏损限额") + "\n\n"
        + "请严格按下列小节输出（各节紧凑精炼，数据支撑结论）：\n\n"
        + "一、今日大盘概况\n"
        + "（上证/深证/创业板收盘点位涨跌幅、最高最低、成交额、涨跌停家数）\n\n"
        + "二、今日市场分析\n"
        + "（赚钱效应、板块轮动、资金流向、市场情绪判定）\n\n"
        + "三、自选股全天表现（仅 JSON「自选股」；为空则写「当前无自选股」并说明原因）\n\n"
        + "四、持仓股全天表现（仅 JSON「持仓股」；为空则写「当前无持仓」）\n\n"
        + "五、同花顺人气榜（仅 JSON「同花顺人气榜」；缺失则写无数据）\n\n"
        + "六、今日实际操作\n\n"
        + "七、今日盈亏\n"
        + "（当日总盈亏：{±金额}元、当前总资产）\n\n"
        + "八、经验及教训总结\n"
        + "（结合【近期交易统计】和【历史经验教训】反思：近期策略执行有无偏差？哪些经验被验证有效？哪些需要调整？"
        + "若连亏超2天，分析共性原因并提出具体改进方向。）\n"
        + LLM_OUTPUT_FORMAT
    )


def _prompt_lunch_narrative() -> str:
    return (
        _persona()
        + "【当前任务】午间复盘正文（一至五）。不要输出「自选更新」或自选 JSON。\n"
        + "【数据说明】JSON 含大盘指数、赚钱效应、大盘资金流、概念板块、涨停统计、"
        + "同花顺人气榜(精简排名)、自选股(全量)、持仓股(全量)、市场状态机。\n\n"
        + "【策略条文】\n" + _load_sections("共用约束", "市场状态机", "仓位联动") + "\n\n"
        + "请严格按下列小节输出（各节紧凑精炼，数据支撑结论）：\n\n"
        + "一、上午大盘回顾\n"
        + "（指数表现、成交额、涨跌停数据、市场情绪）\n\n"
        + "二、上午关键事件\n"
        + "（板块异动、资金流向、重要消息面变化）\n\n"
        + "三、自选股表现（仅 JSON「自选股」；为空则写「当前无自选股」并说明原因）\n\n"
        + "四、持仓股表现（仅 JSON「持仓股」；为空则写「当前无持仓」）\n\n"
        + "五、下午操作策略调整\n"
        + "（基于上午走势，明确下午的操作方向和注意事项）\n"
        + LLM_OUTPUT_FORMAT
    )


# =====================================================================
# 10. ORCHESTRATORS
# =====================================================================

def _extract_news_brief(summary: str) -> str:
    """从新闻LLM输出中提取「综合解读」段落。"""
    for keyword in ("综合解读：", "综合解读:", "综合解读\n"):
        idx = summary.find(keyword)
        if idx != -1:
            return summary[idx:].strip()
    paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()]
    if paragraphs:
        last = paragraphs[-1]
        if not last[:2].replace(".", "").replace("、", "").isdigit():
            return last
    return summary[-300:].strip()


_NEWS_SUMMARY_COMPRESS_CHARS = 600


def _append_and_compress_news_brief(new_brief: str) -> None:
    """追加本次综合解读到摘要文件；超过阈值时用LLM压缩合并。"""
    time_tag = datetime.now().strftime("%H:%M")
    entry = f"[{time_tag}] {new_brief}"

    # 读取已有内容
    existing = ""
    if os.path.isfile(NEWS_IMPACT_SUMMARY_FILE):
        try:
            existing = _read_user_text(NEWS_IMPACT_SUMMARY_FILE).strip()
        except OSError:
            existing = ""

    if not existing:
        combined = entry
    else:
        combined = existing + "\n" + entry

    # 超过阈值则LLM精炼
    if len(combined) > _NEWS_SUMMARY_COMPRESS_CHARS:
        try:
            compressed = call_llm(
                "你是A股短线交易新闻研判助手。请将以下多批次新闻综合解读合并精炼为一段话，"
                "保留所有关键信息（政策方向、利好/利空板块、情绪判断、操作建议），去除重复，"
                "200字以内，纯文本输出。",
                combined,
                max_tokens=500,
            )
            combined = f"综合解读（截至{time_tag}）：{compressed.strip()}"
        except Exception:
            # 压缩失败则截取最新部分
            combined = combined[-_NEWS_SUMMARY_COMPRESS_CHARS:]

    with open(NEWS_IMPACT_SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(combined)


def process_news(raw_data: dict, timestamp: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")
    os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
    news_file = f"{DATA_DIR}/news/{today}/{time_str}.md"

    user = f"原始新闻：\n{json.dumps(raw_data, ensure_ascii=False)[:140000]}"
    summary = call_llm(_prompt_news_system(), user, max_tokens=4000)

    with open(news_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    with open(news_file, "w", encoding="utf-8") as f:
        f.write(f"# 新闻 {timestamp}\n\n{summary}\n")
    # 提取综合解读并追加到摘要文件（多次调用时累积+压缩）
    brief = _extract_news_brief(summary)
    _append_and_compress_news_brief(brief)
    return summary


def analyze_pre_market(raw_data: dict, timestamp: str) -> str:
    payload = _unwrap_payload(raw_data)
    tail = _tail_fund_only()

    u_main = _build_user_msg(filter_payload(payload, "pre_main"), tail=tail)
    u_zt = _build_user_msg(filter_payload(payload, "pre_zt"), tail=tail, include_news=False)
    u_lht = _build_user_msg(filter_payload(payload, "pre_lht"), tail=tail, include_news=False)

    m, zt, lht = _parallel_call(
        lambda: call_llm(_prompt_pre_market_main(), u_main, max_tokens=8000, temperature=0.16),
        lambda: call_llm(_prompt_pre_market_zt(), u_zt, max_tokens=4000, temperature=0.16),
        lambda: call_llm(_prompt_pre_market_lht(), u_lht, max_tokens=4000, temperature=0.16),
    )
    return m.rstrip() + "\n\n" + zt.strip() + "\n\n" + lht.strip()


def analyze_during_market(raw_data: dict, timestamp: str) -> str:
    payload = _unwrap_payload(raw_data)
    tail = _tail_during_market()

    u_overview = _build_user_msg(filter_payload(payload, "overview"), tail=tail)
    u_zt_buy = _build_user_msg(filter_payload(payload, "zt_buy"), tail=tail)
    u_lht_buy = _build_user_msg(filter_payload(payload, "lht_buy"), tail=tail)
    u_zt_hold = _build_user_msg(filter_payload(payload, "zt_hold"), tail=tail)
    u_lht_hold = _build_user_msg(filter_payload(payload, "lht_hold"), tail=tail)
    u_pos = _build_user_msg(filter_payload(payload, "positions"), tail=tail)

    p1, p2, p3, p4, p5, p6 = _parallel_call(
        lambda: call_llm(_prompt_during_overview(), u_overview, max_tokens=2000, temperature=0.16),
        lambda: call_llm(_prompt_during_buy_zt(), u_zt_buy, max_tokens=4000, temperature=0.16),
        lambda: call_llm(_prompt_during_buy_lht(), u_lht_buy, max_tokens=4000, temperature=0.16),
        lambda: call_llm(_prompt_during_hold_zt(), u_zt_hold, max_tokens=4000, temperature=0.16),
        lambda: call_llm(_prompt_during_hold_lht(), u_lht_hold, max_tokens=4000, temperature=0.16),
        lambda: call_llm(_prompt_during_positions(), u_pos, max_tokens=4000, temperature=0.16),
    )
    return "\n\n".join(x.strip() for x in (p1, p2, p3, p4, p5, p6) if x.strip())


def _run_review(raw_data: dict, tail: str, *, lunch: bool) -> str:
    """午间/晚间复盘：叙事 + 涨停板自选 + 龙回头自选，三路并行。"""
    payload = _unwrap_payload(raw_data)

    # 更新上榜记录
    hot = payload.get("同花顺人气榜", [])
    if hot:
        _update_popularity_history(hot)

    # 上榜摘要注入到 optional prompt 的 user msg
    pop_summary = _read_popularity_summary()
    pop_tail = f"\n\n【上榜跟踪】{pop_summary}" if pop_summary else ""

    u_narrative = _build_user_msg(filter_payload(payload, "narrative"), tail=tail)
    u_zt = _build_user_msg(filter_payload(payload, "zt_optional"), tail=pop_tail, include_news=False)
    u_lht = _build_user_msg(filter_payload(payload, "lht_optional"), tail=pop_tail, include_news=False)

    sys_n = _prompt_lunch_narrative() if lunch else _prompt_evening_narrative()
    narrative, tz, lh = _parallel_call(
        lambda: call_llm(sys_n, u_narrative, max_tokens=8000, temperature=0.1),
        lambda: call_llm(_prompt_review_optional_zt(), u_zt, max_tokens=4000, temperature=0.06),
        lambda: call_llm(_prompt_review_optional_lht(), u_lht, max_tokens=4000, temperature=0.06),
    )

    arr_zt, tail_zt = _parse_first_json_array_from_text(tz)
    arr_lh, tail_lh = _parse_first_json_array_from_text(lh)
    opt_block = _stitch_optional_section(
        "六、自选更新" if lunch else "九、自选更新",
        arr_zt, arr_lh, tail_zt, tail_lh,
    )
    return narrative.rstrip() + "\n\n" + opt_block


def analyze_lunch_market(raw_data: dict, timestamp: str) -> str:
    return _run_review(raw_data, _tail_lunch_review(), lunch=True)


def analyze_evening_market(raw_data: dict, timestamp: str) -> str:
    return _run_review(raw_data, _tail_evening_review(), lunch=False)


# =====================================================================
# 11. JSON PARSERS & NORMALIZERS
# =====================================================================
def _match_bracket_span(s: str, start: int, open_ch: str = "[", close_ch: str = "]") -> tuple[int, int] | None:
    if start >= len(s) or s[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def _strip_markdown_fence(text: str) -> str:
    s = text.lstrip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        end_fence = s.find("```")
        if end_fence != -1:
            s = s[:end_fence]
    return s


def _json_loads_array_relaxed(raw: str) -> list | None:
    raw = raw.strip()
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else None
    except json.JSONDecodeError:
        pass
    try:
        arr = ast.literal_eval(raw)
        return arr if isinstance(arr, list) else None
    except (SyntaxError, ValueError, TypeError):
        return None


def _parse_first_json_array_from_text(text: str) -> tuple[list, str]:
    i = text.find("[")
    if i < 0:
        return [], text.strip()
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                raw = text[i:j + 1].replace("［", "[").replace("］", "]")
                arr = _json_loads_array_relaxed(raw.strip())
                tail = text[j + 1:].strip()
                if not isinstance(arr, list):
                    return [], text.strip()
                return arr, tail
    return [], text.strip()


def _section_heading_regex(keyword: str) -> list[str]:
    kw = re.escape(keyword)
    return [
        rf"(?:^|\n)(\s*(?:\d|[一二三四五六七八九十百千]+)\s*[,，、\.．]\s*{kw})\s*",
        rf"(?:^|\n)(\s*【\s*{kw}\s*】)\s*",
        rf"(?:^|\n)(\s*{kw})\s*[:：]?\s*",
    ]


def _find_section_tail_start(content: str, section_keyword: str) -> list[int]:
    tails: list[int] = []
    for pat in _section_heading_regex(section_keyword):
        for m in re.finditer(pat, content):
            tails.append(m.end())
    seen: set[int] = set()
    out: list[int] = []
    for t in tails:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _extract_json_array_with_span(
    content: str, section_keyword: str, *, stock_keys: tuple[str, ...] = ("股票代码", "股票名称")
) -> tuple[list, tuple[int, int] | None]:
    def try_parse_from(abs_start_scan: int) -> tuple[list, tuple[int, int]] | None:
        max_len = min(8000, len(content) - abs_start_scan)
        if max_len <= 0:
            return None
        scan = content[abs_start_scan:abs_start_scan + max_len]
        for off, c in enumerate(scan):
            if c not in "[［":
                continue
            abs_base = abs_start_scan + off
            open_c = content[abs_base] if abs_base < len(content) else ""
            if open_c == "［":
                span = _match_bracket_span(content, abs_base, "［", "］")
            else:
                span = _match_bracket_span(content, abs_base, "[", "]")
            if not span:
                continue
            s0, s1 = span
            raw = content[s0:s1].replace("［", "[").replace("］", "]")
            raw_for_load = raw.strip()
            if raw_for_load.startswith("```"):
                raw_for_load = _strip_markdown_fence(raw_for_load).strip()
            arr = _json_loads_array_relaxed(raw_for_load)
            if arr is None or not isinstance(arr, list):
                continue
            if len(arr) == 0:
                return [], (s0, s1)
            if not all(isinstance(x, dict) for x in arr):
                continue
            if any(any(k in x for k in stock_keys) for x in arr):
                return arr, (s0, s1)
        return None

    for tail_start in reversed(_find_section_tail_start(content, section_keyword)):
        got = try_parse_from(tail_start)
        if got:
            return got[0], got[1]

    key = section_keyword
    pos = len(content)
    for _ in range(24):
        pos = content.rfind(key, 0, pos)
        if pos < 0:
            break
        got = try_parse_from(pos + len(key))
        if got:
            return got[0], got[1]
        pos -= 1

    return [], None


def _infer_optional_strategy_from_reason(reason: str) -> str | None:
    s = (reason or "").strip()
    if s.startswith("【涨停板战法】"):
        return "涨停板战法"
    if s.startswith("【龙回头战法】"):
        return "龙回头战法"
    return None


def _normalize_optional_rows(rows: list) -> list:
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        reason = str(r.get("加入自选原因", "") or r.get("自选原因", "") or r.get("原因", "") or "").strip()
        if not reason:
            reason = "未注明"
        tag_raw = str(r.get("战法", "") or r.get("策略战法", "") or "").strip()
        if tag_raw in OPTIONAL_STRATEGY_ALLOWED:
            tag = tag_raw
        else:
            inferred = _infer_optional_strategy_from_reason(reason)
            tag = inferred if inferred else "未标注"
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
            "战法": tag,
            "加入自选原因": reason,
        }
        if d["股票代码"] or d["股票名称"]:
            out.append(d)
    return out


def _normalize_holding_rows(rows: list) -> list:
    out: list = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
            "买入时间": str(r.get("买入时间", "") or "").strip(),
            "买入价": r.get("买入价", r.get("买入价格", "")),
            "买入原因": str(r.get("买入原因", "") or "").strip(),
            "卖出时间": str(r.get("卖出时间", "") or "").strip(),
            "卖出价": r.get("卖出价", r.get("卖出价格", "")),
            "卖出原因": str(r.get("卖出原因", "") or "").strip(),
        }
        if d["股票代码"] or d["股票名称"]:
            out.append(d)
    return out


def _holding_to_readable(h: dict) -> str:
    parts = [f"股票名称：{h.get('股票名称', '')}，股票代码：{h.get('股票代码', '')}"]
    for k, label in [("买入时间", "买入时间"), ("买入价", "买入价格"), ("买入原因", "买入原因"),
                     ("卖出时间", "卖出时间"), ("卖出价", "卖出价格"), ("卖出原因", "卖出原因")]:
        v = h.get(k)
        if v is not None and str(v).strip():
            parts.append(f"{label}：{v}")
    return "，".join(parts)


def _optional_to_readable(o: dict) -> str:
    code = o.get("股票代码", "")
    name = o.get("股票名称", "")
    reason = o.get("加入自选原因", "")
    tag = str(o.get("战法", "") or "").strip()
    if tag and tag != "未标注":
        return f"股票名称：{name}，股票代码：{code}，战法：{tag}，加入自选原因：{reason}"
    return f"股票名称：{name}，股票代码：{code}，加入自选原因：{reason}"


def _build_readable_block(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(f"{i}、{ln}" for i, ln in enumerate(lines, start=1))


def _stitch_optional_section(label: str, arr_zt: list, arr_lht: list, tail_zt: str, tail_lht: str) -> str:
    norm_zt = _normalize_optional_rows(arr_zt)
    norm_lht = _normalize_optional_rows(arr_lht)
    merged_in = norm_zt + norm_lht
    merged = []
    seen: set = set()
    for row in merged_in:
        k = (row.get("股票代码", ""), row.get("战法", ""))
        if k in seen:
            continue
        seen.add(k)
        merged.append(row)
    lines = [label]
    if merged:
        lines.append(json.dumps(merged, ensure_ascii=False))
    else:
        lines.append("[]")

    # 分别收集涨停板和龙回头的未更新原因
    def _collect_reason(tail_text: str) -> list[str]:
        out = []
        for ln in tail_text.splitlines():
            t = ln.strip()
            if t and ("原因" in t or "未更新" in t or "不达标" in t
                      or "不满足" in t or "排除" in t or "无合格" in t
                      or "不通过" in t or "超" in t or "不符" in t):
                out.append(t)
        # 如果按关键词没匹配到，但 tail 有实质内容，直接作为原因
        if not out and tail_text.strip():
            out.append(tail_text.strip()[:200])
        return out

    # 涨停板战法：若无合格标的，收集原因
    if not norm_zt:
        zt_reasons = _collect_reason(tail_zt)
        if zt_reasons:
            lines.extend(zt_reasons)
        else:
            lines.append("涨停板战法自选未更新原因：无符合条件的标的（LLM未给出详细原因）")

    # 龙回头战法：若无合格标的，收集原因
    if not norm_lht:
        lht_reasons = _collect_reason(tail_lht)
        if lht_reasons:
            lines.extend(lht_reasons)
        else:
            lines.append("龙回头战法自选未更新原因：无符合条件的标的（LLM未给出详细原因）")

    return "\n".join(lines)


# =====================================================================
# 12. POST-PROCESSING
# =====================================================================
def _extract_reason_from_content(content: str, keyword: str) -> str:
    """从 LLM 输出正文中提取指定关键字后的原因文本。"""
    patterns = [
        rf"{re.escape(keyword)}[：:\s]*(.+?)(?:\n|$)",
        rf"{re.escape(keyword.replace('原因', ''))}.*?原因[：:\s]*(.+?)(?:\n|$)",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            return m.group(1).strip()
    return "未给出具体原因"


def replace_json_for_feishu(
    content: str,
    *,
    optional_span: tuple[int, int] | None,
    optional_lines: list[str],
    holdings_span: tuple[int, int] | None,
    holdings_lines: list[str],
) -> str:
    s = content
    replacements: list[tuple[int, int, str]] = []
    if optional_span is not None:
        rep = _build_readable_block(optional_lines) if optional_lines else "（本期自选列表为空）"
        replacements.append((optional_span[0], optional_span[1], rep))
    if holdings_span is not None:
        rep = _build_readable_block(holdings_lines) if holdings_lines else "（本期持仓列表为空）"
        replacements.append((holdings_span[0], holdings_span[1], rep))
    for a, b, text in sorted(replacements, key=lambda x: x[0], reverse=True):
        s = s[:a] + text + s[b:]
    return s


def parse_and_update(content: str, mode: str, market_payload: dict | None = None) -> dict:
    holdings_raw, h_span = _extract_json_array_with_span(content, "持仓更新")
    optional_raw, o_span = _extract_json_array_with_span(content, "自选更新")

    holdings = _normalize_holding_rows(holdings_raw)
    optional = _normalize_optional_rows(optional_raw)

    holdings_lines = [_holding_to_readable(h) for h in holdings] if holdings else []
    optional_lines = [_optional_to_readable(o) for o in optional] if optional else []

    holdings_text = "\n".join(holdings_lines) if holdings_lines else None
    optional_text = "\n".join(optional_lines) if optional_lines else None

    profit = None
    new_fund = None

    if "今日盈亏" in content:
        m = re.search(r"当日总盈亏[：:\s]*([+-]?\d+(?:\.\d+)?)", content)
        if m:
            profit = float(m.group(1))
        else:
            m = re.search(r"[盈亏][为：:\s]*([+-]?\d+(?:\.\d+)?)", content)
            if m:
                profit = float(m.group(1))

    if "资金总额" in content:
        m = re.search(r"资金总额[为：:\s]*(\d+(?:\.\d+)?)", content)
        if m:
            new_fund = float(m.group(1))

    if holdings and mode in ("during_market", "pre_market") and h_span is not None:
        # 检测止损卖出，追加到止损记录文件
        for h in holdings:
            sell_reason = str(h.get("卖出原因", "") or "").strip()
            sell_time = str(h.get("卖出时间", "") or "").strip()
            if sell_reason and sell_time:
                action = "止损卖出" if "止损" in sell_reason else "卖出"
                _append_trade_log(action, f"{h.get('股票名称', '')}({h.get('股票代码', '')}) {sell_reason}")
                if "止损" in sell_reason:
                    _append_stoploss_record(
                        h.get("股票代码", ""),
                        h.get("股票名称", ""),
                        sell_time,
                        sell_reason,
                    )
                    print(f"止损记录已追加: {h.get('股票名称', '')} {sell_time}")
            elif h.get("买入时间") and not sell_time:
                # 新买入的记录
                buy_reason = str(h.get("买入原因", "") or "").strip()
                if buy_reason:
                    _append_trade_log("买入", f"{h.get('股票名称', '')}({h.get('股票代码', '')}) {buy_reason}")
        # 仅保存仍在持仓的（未卖出的）
        active = [h for h in holdings if not str(h.get("卖出时间", "") or "").strip()]
        save_holdings(active if active else holdings)
        print(f"持仓已更新: {holdings}")
    elif mode in ("during_market", "pre_market") and h_span is not None and not holdings:
        # 提取持仓未更新原因
        reason = _extract_reason_from_content(content, "持仓未更新原因")
        print(f"持仓未更新。原因：{reason}")

    if mode in ("post_market_lunch", "post_market_evening") and o_span is not None:
        if optional:
            _archive_optional(optional)
            save_optional(optional)
            # 记录加自选操作到操作日志
            for o in optional:
                tag = o.get("战法", "")
                _append_trade_log("加自选", f"{o.get('股票名称', '')}({o.get('股票代码', '')}) [{tag}]")
            print(f"自选股已更新（共 {len(optional)} 条）: {optional}")
        else:
            # 提取自选未更新原因
            reason_zt = _extract_reason_from_content(content, "涨停板战法自选未更新原因")
            reason_lht = _extract_reason_from_content(content, "龙回头战法自选未更新原因")
            reasons = []
            if reason_zt:
                reasons.append(f"涨停板：{reason_zt}")
            if reason_lht:
                reasons.append(f"龙回头：{reason_lht}")
            reason_str = "；".join(reasons) if reasons else "LLM未给出具体原因"
            print(f"自选未更新。原因：{reason_str}")

    if profit is not None and mode in ("post_market_lunch", "post_market_evening"):
        update_fund(profit)
        today = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        with open(f"{DATA_DIR}/trade/{today}/profit.md", "w", encoding="utf-8") as f:
            f.write(str(int(profit)))
        print(f"盈亏: {profit}")

    if new_fund is not None:
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(new_fund)))
        print(f"资金已更新: {new_fund}")

    return {
        "holdings_text": holdings_text,
        "optional_text": optional_text,
        "holdings_lines": holdings_lines,
        "optional_lines": optional_lines,
        "holdings_span": h_span,
        "optional_span": o_span,
        "normalized_holdings": holdings,
        "normalized_optional": optional,
    }


# =====================================================================
# 13. SAVE
# =====================================================================
def save_review(timestamp: str, content: str, mode: str, raw_data: dict = None):
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    suffix = "wujian" if mode == "post_market_lunch" else "fupan"
    time_str = datetime.now().strftime("%H_%M_%S")
    review_file = f"{DATA_DIR}/trade/{today}/{suffix}-{time_str}.md"
    if raw_data:
        with open(review_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
    with open(review_file, "w", encoding="utf-8") as f:
        f.write(content)
    parts = content.split("\n\n", 1)
    if len(parts) > 1:
        try:
            with open(review_file.replace(".md", "-llm-only.txt"), "w", encoding="utf-8") as fp:
                fp.write(parts[1])
        except OSError:
            pass


def save_raw_data(mode: str, raw_data: dict):
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")
    if mode == "news":
        os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/news/{today}/{time_str}-origin.json"
    elif mode == "pre_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/pre_market-{time_str}-origin.json"
    elif mode == "during_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/during_market-{time_str}-origin.json"
    else:
        return
    if raw_data:
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        print(f"原始数据已保存: {raw_file}")


def is_trading_day() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        resp = requests.get(f"https://timor.tech/api/holiday/info/{today}", timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            t = result.get("type", {})
            t_type = t.get("type") if isinstance(t, dict) else None
            return t_type in [0, 2]
        return True
    except Exception:
        return True


# =====================================================================
# 14. PUSH FORMAT
# =====================================================================
_PUSH_FOCUS = {
    "news": "关注点：宏观政策、行业热点、外围市场、情绪风向",
    "pre_market": "关注点：集合竞价表现、自选标的信号、持仓风控、当日执行计划",
    "during_market": "关注点：市场状态、买卖信号、持仓监控、仓位风控",
    "post_market_lunch": "关注点：上午行情回顾、自选表现、持仓跟踪、下午策略调整",
    "post_market_evening": "关注点：全天复盘、盈亏总结、自选更新、经验教训",
}


def _format_push_message(label: str, timestamp: str, body: str, mode: str) -> str:
    """组装专业推送格式：标题行 + 关注点 + 分隔 + 正文。"""
    # focus = _PUSH_FOCUS.get(mode, "")
    header = f"【{label}】{timestamp}\n"
    parts = [header]
    # if focus:
    #     parts.append(focus)
    # parts.append("=" * 36)
    parts.append(body.strip())
    return "\n".join(parts)


# =====================================================================
# 15. MAIN
# =====================================================================
def main():
    if len(sys.argv) < 2:
        print("用法: python main.py <news|pre_market|during_market|post_market_lunch|post_market_evening>")
        sys.exit(1)

    mode = sys.argv[1]
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    labels = {
        "news": "新闻聚焦",
        "pre_market": "盘前分析",
        "during_market": "盘中实时",
        "post_market_lunch": "午间复盘",
        "post_market_evening": "晚间复盘",
    }
    label = labels.get(mode, mode)
    print(f"[{timestamp}] 开始处理 {mode}...")

    if mode != "news" and not is_trading_day():
        print("今日非交易日，跳过")
        # return

    fetch_map = {
        "news": fetch_news,
        "pre_market": fetch_pre_market,
        "during_market": fetch_during_market,
        "post_market_lunch": fetch_post_market,
        "post_market_evening": fetch_post_market,
    }
    if mode not in fetch_map:
        print(f"未知模式: {mode}")
        sys.exit(1)

    try:
        # 真实数据获取（勿删）
        # data = fetch_map[mode]()
        # 测试，直接读文件获取数据（勿删） TODO
        data = json.loads(_read_user_text(_PROJECT_ROOT / 'data' / mode))
        print("数据拉取成功")
    except Exception as e:
        print(f"数据拉取失败: {e}")
        sys.exit(1)

    analysis = ""
    try:
        if mode == "news":
            summary = process_news(data, timestamp)
            analysis = _format_push_message("新闻聚焦", timestamp, summary, mode)
            save_raw_data(mode, data)
        elif mode == "pre_market":
            analysis = _format_push_message("盘前分析", timestamp, analyze_pre_market(data, timestamp), mode)
            save_raw_data(mode, data)
        elif mode == "during_market":
            analysis = _format_push_message("盘中实时", timestamp, analyze_during_market(data, timestamp), mode)
            save_raw_data(mode, data)
        elif mode == "post_market_lunch":
            analysis = _format_push_message("午间复盘", timestamp, analyze_lunch_market(data, timestamp), mode)
            save_review(timestamp, analysis, mode, data)
            _extract_and_save_memory(analysis, lunch=True)
        elif mode == "post_market_evening":
            analysis = _format_push_message("晚间复盘", timestamp, analyze_evening_market(data, timestamp), mode)
            save_review(timestamp, analysis, mode, data)
            _extract_and_save_memory(analysis, lunch=False)
        print("分析完成")
    except Exception as e:
        print(f"分析失败: {e}")
        analysis = f"【{label}】{timestamp}\n\n服务异常，请稍后重试。"

    feishu_content = analysis
    try:
        pu = parse_and_update(
            analysis,
            mode,
            market_payload=_unwrap_payload(data)
            if mode in ("post_market_lunch", "post_market_evening")
            else None,
        )
        feishu_content = replace_json_for_feishu(
            analysis,
            optional_span=pu["optional_span"],
            optional_lines=pu["optional_lines"],
            holdings_span=pu["holdings_span"],
            holdings_lines=pu["holdings_lines"],
        )
    except Exception as e:
        print(f"解析更新失败: {e}")

    try:
        token = get_token()
        send_msg(feishu_content, token)
        print("飞书推送成功")
    except Exception as e:
        print(f"飞书推送失败: {e}")

    print("\n" + "=" * 60)
    out_show = feishu_content
    print(out_show[:2000] if len(out_show) > 2000 else out_show)
    print("=" * 60)


if __name__ == "__main__":
    main()
