#!/usr/bin/env python3
"""
A股短线交易机器人 - 自主决策版
- 新闻/盘前/盘中/午间复盘/晚间复盘完整推送
- 自主选股 + 持仓管理
- 模拟交易 + 资金跟踪
- 止盈止损：-3%止损，+5%/+8%/+15%分批止盈
- 选股范围：60/00/30开头主板
"""

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 禁用代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        del os.environ[k]

from app.core.config import get_settings
import requests
import json
import time
from datetime import datetime
from typing import Callable

# ========== 配置 ==========
BASE_URL = "http://localhost:8085"
FEISHU_APP_ID = "cli_a96dcfa5d3f91bd4"
FEISHU_APP_SECRET = "eXhbDo1Ldh4sMGkBjVUjdhAiiBFZ6ld6"
FEISHU_USER_ID = "ou_bc3cefb641bbc53148de964a637d8cfd"

# 项目根目录（与本文件 main.py 同级）
_PROJECT_ROOT = Path(__file__).resolve().parent
# 策略文件：项目根目录 strategy.md（与 main.py 同级），Linux/Windows 通用
STRATEGY_FILE = _PROJECT_ROOT / "strategy.md"

# 数据目录（用户主目录 ~/data/quant，路径不变）
DATA_DIR = os.path.expanduser("~/data/quant")
FUND_FILE = f"{DATA_DIR}/fund.md"
OPTIONAL_FILE = f"{DATA_DIR}/optional.jsonl"
HOLDING_FILE = f"{DATA_DIR}/holding.jsonl"
INITIAL_CAPITAL = 10000
# 与聚合接口 `GET /api/v1/quant/market/news` 写入路径一致
NEWS_IMPACT_SUMMARY_FILE = f"{DATA_DIR}/news_market_impact_summary.txt"

# 供 LLM 解读行情 JSON：与后端 ``quant_endpoint`` 聚合结构一致。
# 按任务拆分：单任务提示词只注入与本节条文相关的字段说明，避免无关战法表述。
RAW_DATA_FIELD_GUIDE_COMMON = """【接口 JSON 顶层字段说明（必须按键名区分，禁止混用）】
「自选股」：用户自选观察池；元素含「战法」时，正文与指令须与该字段所指条文一致，不得交叉套用其他条文。
「持仓股」：用户持仓；标题含「持仓」的小节仅使用该键。
「同花顺人气榜」「涨停统计」「概念板块」等：环境或榜单数据，其中的个股代码不得替代「自选股」列出。
「大盘资金流」「个股资金流」「盘口」「历史行情」「盘中10分钟线」等：按当次任务条文使用对应路径。
"""

RAW_DATA_FIELD_GUIDE_ZT = (
    RAW_DATA_FIELD_GUIDE_COMMON
    + "【本节条文相关】盘中追击时「人气排名」取自「同花顺人气榜」内字段，阈值为 **≤10**；加自选筛选用人气榜前 **20** 条；「涨停统计」「概念板块」用于本节口径。\n"
)

RAW_DATA_FIELD_GUIDE_LHT = (
    RAW_DATA_FIELD_GUIDE_COMMON
    + "【本节条文相关】加自选筛选用人气榜前 **50** 条（接口不足则按实际条数）；「历史行情」「技术指标」均线与 MACD、「盘口」量比、「个股资金流」用于本节口径。\n"
)

RAW_DATA_FIELD_GUIDE_NARRATIVE = (
    RAW_DATA_FIELD_GUIDE_COMMON
    + "【复盘正文】涉及多只自选时，按 JSON「战法」分列叙述，勿混写理由。\n"
)

# 自选 JSON 合法战法取值（与提示词、落盘字段一致）
OPTIONAL_STRATEGY_ALLOWED = frozenset({"涨停板战法", "龙回头战法"})

TRADING_PHASE_WORKFLOW_BLOCK = """
【交易闭环阶段分工】
1、盘前：依据接口 JSON「自选股」「持仓股」与策略条文，输出当日可执行纲要及挂单要素。
2、盘中：买入仅允许出现在 JSON「自选股」中的标的，且须满足该条「战法」对应条文；卖出仅允许「持仓股」。禁止用榜单代替自选池开仓。
3、复盘：总结当日并储备下一交易日观察池时，按各条文独立筛选；每条记录单一战法、单一说理。
"""


def _unwrap_quant_market_payload(raw: dict) -> dict:
    """统一响应 ``{code,message,data:{...}}`` 时取出内层 ``data``，使「自选股」等与后端字段对齐。"""
    inner = raw.get("data")
    if isinstance(inner, dict):
        return inner
    return raw


def read_news_market_impact_summary() -> str:
    """服务端新闻接口写入的 LLM 短文（~/data/quant/news_market_impact_summary.txt）。"""
    if not os.path.isfile(NEWS_IMPACT_SUMMARY_FILE):
        return ""
    try:
        return _read_user_text(NEWS_IMPACT_SUMMARY_FILE).strip()
    except OSError:
        return ""


def _daily_news_tail_for_prompt(*, max_chars: int = 1000) -> str:
    """仅注入「新闻接口」侧大模型写入的当日浓缩摘要，不包含任何原始资讯正文。"""
    text = read_news_market_impact_summary().strip()
    if not text:
        text = (
            "（尚无新闻摘要）"
        )
    elif len(text) > max_chars:
        text = text[:max_chars]
    return (
        "\n\n【当日新闻摘要】\n"
        + text
    )


def _market_data_user_block(raw_data: dict, *, tail: str, guide: str | None = None) -> str:
    """行情类分析：字段说明 → 当日新闻总结 → 接口 JSON → tail。"""
    payload = _unwrap_quant_market_payload(raw_data)
    field_guide = guide if guide is not None else RAW_DATA_FIELD_GUIDE_COMMON
    return (
        field_guide
        + _daily_news_tail_for_prompt()
        + "\n\n【以下为接口业务数据 JSON】\n"
        + json.dumps(payload, ensure_ascii=False)[:140000]
        + tail
    )


def _read_user_text(path: str) -> str:
    """读取用户主目录（~/data/quant）下文本：优先 UTF-8（含 BOM），失败则回退 GBK，避免历史文件编码不一导致失败。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

# ========== 飞书推送 ==========
def get_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"获取token失败")
    return result["tenant_access_token"]

def send_msg(content: str, token: str):
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"receive_id": FEISHU_USER_ID, "msg_type": "text", "content": json.dumps({"text": content})}
    resp = requests.post(url, headers=headers, json=data, timeout=600)
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"发送失败: {result}")

# ========== LLM调用（带重试）：项目根 .env 中 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（与 FastAPI 同源）==========
def call_llm(
    system: str,
    user: str,
    max_tokens: int = 2500,
    retries: int = 3,
    *,
    temperature: float | None = None,
) -> str:
    cfg = get_settings()
    api_key = (cfg.LLM_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置全局 LLM：请在项目根目录 .env 设置 LLM_API_KEY（亦可使用 GOLDQUANT_LLM_API_KEY，与 FastAPI 同源）",
        )
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
            for c in result.get("content", []):
                if c.get("type") == "text":
                    return c["text"]
            return str(result)
        except Exception as e:
            print(f"LLM异常: {e}")
            if attempt < retries - 1:
                time.sleep(5)
    raise Exception("LLM调用失败")

# ========== 数据拉取 ==========
def fetch_data(endpoint: str) -> dict:
    resp = requests.get(f"{BASE_URL}{endpoint}", timeout=600)
    resp.raise_for_status()
    return resp.json()

def fetch_news(): return fetch_data("/api/v1/quant/market/news")
def fetch_pre_market(): return fetch_data("/api/v1/quant/market/pre_market")
def fetch_during_market(): return fetch_data("/api/v1/quant/market/during_market")
def fetch_post_market(): return fetch_data("/api/v1/quant/market/post_market")

# ========== 资金/持仓管理 ==========
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
        lines = ["# 资金曲线", f"- 初始本金：{INITIAL_CAPITAL:.2f} 元", f"- 更新时间：{today}",
                 f"- 当前总资产：{fund:.2f} 元", f"- 当日盈亏：{profit:+.2f} 元 ({profit/INITIAL_CAPITAL*100:+.2f}%)",
                 "- 历史记录（累计）："]
        for d, v in sorted(existing.items()):
            lines.append(f"{d}: {v:.2f}")
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write('\n'.join(lines))
    except:
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(fund)))
    return fund

def _read_jsonl_stock_file(path: str) -> list:
    """每行一条 JSON 对象；``#`` 开头为注释；单行可为数组则展开。"""
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

def read_trades(today: str) -> list:
    try:
        return json.loads(_read_user_text(f"{DATA_DIR}/trade/{today}/trades.md"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def save_trades(today: str, trades: list):
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    with open(f"{DATA_DIR}/trade/{today}/trades.md", "w", encoding="utf-8") as f:
        f.write(json.dumps(trades, ensure_ascii=False, indent=2))


def _tail_fund_only() -> str:
    return f"\n\n【当前资金】：{get_fund():.2f} 元"


def _tail_during_market_user() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【今日交易记录】：{tr_s}"


def _tail_lunch_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无"
    return f"\n\n【当前资金】：{get_fund():.2f} 元\n【上午交易记录】：{tr_s}"


def _tail_evening_review() -> str:
    td = datetime.now().strftime("%Y-%m-%d")
    tr = read_trades(td)
    tr_s = json.dumps(tr, ensure_ascii=False) if tr else "无交易"
    return (
        f"\n\n【当前资金】：{get_fund():.2f} 元\n"
        f"【初始本金】：{INITIAL_CAPITAL}元\n"
        f"【今日实际交易记录】：\n"
        f"{tr_s}"
    )

def load_strategy() -> str:
    try:
        return STRATEGY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return f"策略文件加载失败：{STRATEGY_FILE}（{e!r}）"


# strategy.md 顶级章节锚点（与仓库内 strategy.md 标题一致，用于按阶段切片加载）
_STRATEGY_CHAPTER_MARKERS: tuple[tuple[str, str], ...] = (
    ("第零章", "## 第零章"),
    ("第一章", "## 第一章"),
    ("第二章", "## 第二章"),
    ("第三章", "## 第三章"),
    ("第四章", "## 第四章"),
    ("第五章", "## 第五章"),
    ("第六章", "## 第六章"),
    ("附录", "## 附录"),
)

def _strategy_split_chapters(full: str) -> dict[str, str]:
    """按顶级 ``##`` 章节切片（不含文件首行标题 ``## A股…`` 之前的片段）。"""
    positions: list[tuple[int, str]] = []
    for key, marker in _STRATEGY_CHAPTER_MARKERS:
        idx = full.find(marker)
        if idx >= 0:
            positions.append((idx, key))
    positions.sort(key=lambda x: x[0])
    out: dict[str, str] = {}
    for i, (pos, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(full)
        out[key] = full[pos : end].strip()
    return out


def load_strategy_for_phase(phase: str) -> str:
    """
    按运行阶段加载 strategy.md 的子集（代码侧编排；不向模型暴露章节清单）。
    ``phase``: ``pre_market`` | ``during_market`` | ``post_market_review``。
    切片失败时静默回退全文。
    """
    full = load_strategy()
    if full.startswith("策略文件加载失败"):
        return full
    ch = _strategy_split_chapters(full)
    wanted_lists: dict[str, list[str]] = {
        "post_market_review": ["第零章", "第一章", "第二章"],
        "pre_market": ["第零章", "第一章", "第三章", "第五章", "第六章", "附录"],
        "during_market": ["第零章", "第一章", "第三章", "第四章", "第五章", "第六章", "附录"],
    }
    wanted = wanted_lists.get(phase)
    if not wanted:
        return full
    parts = [(ch.get(k) or "").strip() for k in wanted]
    if not all(parts):
        return full
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# 统一人设 + 任务级策略切片（多路 LLM 并行时每个子任务只注入相关条文）
# ---------------------------------------------------------------------------
LLM_PERSONA_CORE = """【人设】你是顶尖 A 股短线交易员，手法比肩章盟主、赵老哥，纪律严明，知行合一。
"""
LLM_OUTPUT_FORMAT = """
【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。
"""


def _extract_subsection(text: str, start_m: str, end_m: str | None) -> str:
    i = text.find(start_m)
    if i < 0:
        return ""
    if not end_m:
        return text[i:].strip()
    j = text.find(end_m, i + len(start_m))
    if j < 0:
        return text[i:].strip()
    return text[i:j].strip()


def _chapter_six_for_overview_and_narrative(c6: str) -> str:
    """第六章：状态机 + 组合层上限 + 共有纪律 + 每日亏损限额（不含各战法单票分表）。"""
    head = _extract_subsection(c6, "## 第六章", "#### 涨停板战法")
    bullet = ""
    i = c6.find("- 若盘中状态由强势转为弱势")
    if i >= 0:
        j = c6.find("### 6.2", i)
        if j > i:
            bullet = c6[i:j].strip()
    tail = _extract_subsection(c6, "### 6.2", None)
    parts = [p.strip() for p in (head, bullet, tail) if p and p.strip()]
    return "\n\n".join(parts)


def load_strategy_snippet(snippet_id: str) -> str:
    """从 strategy.md 抽取单任务所需条文；未知 id 返回空串。"""
    full = load_strategy()
    if full.startswith("策略文件加载失败"):
        return full
    ch = _strategy_split_chapters(full)
    c6 = (ch.get("第六章", "") or "").strip()
    if snippet_id == "review_narrative":
        z0 = ch.get("第零章", "")
        z01 = _extract_subsection(z0, "### 0.1", "### 0.2")
        parts = [
            z01.strip(),
            (ch.get("第一章", "") or "").strip(),
            _chapter_six_for_overview_and_narrative(c6),
        ]
        return "\n\n---\n\n".join(p for p in parts if p)
    if snippet_id == "optional_zt":
        c2 = ch.get("第二章", "")
        return _extract_subsection(c2, "### 2.1", "### 2.2").strip()
    if snippet_id == "optional_lht":
        c2 = ch.get("第二章", "")
        return (_extract_subsection(c2, "### 2.2", None) or "").strip()
    if snippet_id == "buy_zt":
        c3 = ch.get("第三章", "")
        return _extract_subsection(c3, "### 3.1", "### 3.2").strip()
    if snippet_id == "buy_lht":
        c3 = ch.get("第三章", "")
        r = _extract_subsection(c3, "### 3.2", "## 第四章")
        return (r or _extract_subsection(c3, "### 3.2", None) or "").strip()
    if snippet_id == "pre_market_narrative":
        return "\n\n---\n\n".join(
            [
                (ch.get("第一章", "") or "").strip(),
                _chapter_six_for_overview_and_narrative(c6),
            ]
        ).strip()
    if snippet_id == "during_overview":
        return _chapter_six_for_overview_and_narrative(c6).strip()
    if snippet_id == "during_hold_zt":
        c4 = (ch.get("第四章", "") or "").strip()
        c5 = (ch.get("第五章", "") or "").strip()
        zt_pos = _extract_subsection(c6, "#### 涨停板战法", "#### 龙回头战法")
        bullet = ""
        i = c6.find("- 若盘中状态由强势转为弱势")
        if i >= 0:
            j = c6.find("### 6.2", i)
            if j > i:
                bullet = c6[i:j].strip()
        common = "\n\n".join(
            [
                _extract_subsection(c4, "### 4.1", "### 4.2").strip(),
                _extract_subsection(c5, "### 5.1", "### 5.2").strip(),
                zt_pos.strip(),
                bullet,
            ]
        )
        return common.strip()
    if snippet_id == "during_hold_lht":
        c4 = (ch.get("第四章", "") or "").strip()
        c5 = (ch.get("第五章", "") or "").strip()
        lh_pos = _extract_subsection(c6, "#### 龙回头战法", "### 6.2")
        common = "\n\n".join(
            [
                _extract_subsection(c4, "### 4.2", "## 第五章").strip(),
                _extract_subsection(c5, "### 5.2", "## 第六章").strip(),
                lh_pos.strip(),
            ]
        )
        return common.strip()
    if snippet_id == "during_positions_policy":
        loss = _extract_subsection(c6, "### 6.2", None)
        return (loss or "").strip()
    return ""


_LLM_PARALLEL_WORKERS = max(2, min(8, (os.cpu_count() or 4)))


def _parallel_map_call_str(*fns: Callable[[], str]) -> list[str]:
    """并行执行多个无参、返回 str 的 LLM 子任务。"""
    if not fns:
        return []
    n = min(_LLM_PARALLEL_WORKERS, len(fns))
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(fn) for fn in fns]
        return [fu.result() for fu in futs]


def _match_bracket_span(s: str, start: int, open_ch: str = "[", close_ch: str = "]") -> tuple[int, int] | None:
    """自 start（指向 open_ch）起配对括号，返回 [start, end) end 为紧随 ] 之后下标。"""
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


def _strip_markdown_fence_after(pos: str) -> str:
    """去掉可能出现的 ```json ... ``` 围栏前缀（仅从开头剥）。"""
    s = pos.lstrip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
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
        import ast

        arr = ast.literal_eval(raw)
        return arr if isinstance(arr, list) else None
    except (SyntaxError, ValueError, TypeError):
        return None


def _parse_first_json_array_from_text(text: str) -> tuple[list, str]:
    """从模型回复中抽出首个 JSON 数组。"""
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
                raw = text[i : j + 1].replace("［", "[").replace("］", "]")
                arr = _json_loads_array_relaxed(raw.strip())
                tail = text[j + 1 :].strip()
                if not isinstance(arr, list):
                    return [], text.strip()
                return arr, tail
    return [], text.strip()


def _system_review_optional_zt() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】复盘·仅完成 **涨停板战法 · 加入自选**。\n"
        + "仅从接口 JSON 按下列条文筛选下一交易日观察标的；论据仅使用条文允许的字段。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("optional_zt")
        + "\n\n【输出】仅输出：先输出 **一个** JSON 数组；每项含 股票代码、股票名称、战法（固定填「涨停板战法」）、"
        + "加入自选原因（必须以【涨停板战法】开头）。若无合格标的：输出 []，下一行写：涨停板战法自选未更新原因：……\n"
        + "不要输出数组以外的正文。"
    )


def _system_review_optional_lht() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】复盘·仅完成 **龙回头战法 · 加入自选**。\n"
        + "仅从接口 JSON 按下列条文筛选；论据仅使用条文允许的字段。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("optional_lht")
        + "\n\n【输出】仅输出：先输出 **一个** JSON 数组；每项含 股票代码、股票名称、战法（固定填「龙回头战法」）、"
        + "加入自选原因（必须以【龙回头战法】开头）。若无合格标的：输出 []，下一行写：龙回头战法自选未更新原因：……\n"
        + "不要输出数组以外的正文。"
    )


def _system_pre_market_main() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘前决策——撰写 **一～三、六、七**（不写四、五）。七含持仓 JSON。\n"
        + TRADING_PHASE_WORKFLOW_BLOCK
        + "\n\n【策略条文】\n"
        + load_strategy_snippet("pre_market_narrative")
        + "\n\n【实盘契约】输出可执行挂单要素（方向、仓位、委托价、时间点 yyyy-MM-dd HH:mm:ss）。\n"
        + "【重要】禁止使用markdown格式。\n\n"
        + "请严格按下列小节输出：\n\n"
        + "一、市场整体概览\n"
        + "- 上证指数：{当前点位}（{涨跌幅}）\n"
        + "- 深证成指：{当前点位}（{涨跌幅}）\n"
        + "- 创业板指：{当前点位}（{涨跌幅}）\n"
        + "- 市场情绪：{高/中/低}，{简要说明}\n\n"
        + "二、自选股（仅 JSON「自选股」；为空则只写一句无自选股）\n"
        + "若含战法字段请分列叙述；勿与人气榜混写。\n\n"
        + "三、持仓股（仅 JSON「持仓股」；为空则只写无持仓）\n\n"
        + "六、市场判断与当日执行纲要\n"
        + "{深度分析结论}\n\n"
        + "七、【持仓更新】（仅此一处输出持仓 JSON 数组）\n"
        + "规则：若有变更输出非空 JSON；无需变更输出 []，下一行写「持仓未更新原因：……」。\n"
        + "格式须含股票代码、名称、买入时间（yyyy-MM-dd HH:mm:ss）、买入价、买入原因；卖出补全卖出时间与卖出价。\n"
        + '[{"股票代码":"600000","股票名称":"浦发银行","买入时间":"2026-05-03 09:31:00","买入价":11.2,"买入原因":"……","卖出时间":"","卖出价":"","卖出原因":""}]\n'
        + LLM_OUTPUT_FORMAT
    )


def _system_pre_market_zt() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘前——仅撰写 **四、涨停板战法（集合竞价与盘中）** 小节。\n"
        + "仅针对 JSON「自选股」中战法为涨停板战法（或未标注且本节明确按该条文处理）的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("buy_zt")
        + "\n\n【输出】仅输出「四、涨停板战法」及其内容，不要输出其他小节。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_pre_market_lht() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘前——仅撰写 **五、龙回头战法（定价与资金口径）** 小节。\n"
        + "仅针对 JSON「自选股」中战法为龙回头战法的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("buy_lht")
        + "\n\n【输出】仅输出「五、龙回头战法」及其内容，不要输出其他小节。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_overview() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——仅输出 **【市场状态】** 与 **【总仓位限制】** 两行段（不写买卖细则）。\n"
        + TRADING_PHASE_WORKFLOW_BLOCK
        + "\n\n【风控与仓位口径】\n"
        + load_strategy_snippet("during_overview")
        + "\n\n【输出格式】\n"
        + "【市场状态】{强势/震荡/弱势}（{简述：上证、成交额、连板高度等}）\n"
        + "【总仓位限制】按下文市场强弱判定与仓位联动表 | 当前持仓{x%}\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_buy_zt() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——仅输出 **【涨停板战法｜盘中买入】** 整段。\n"
        + "仅适用于 JSON「自选股」中战法为涨停板战法的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("buy_zt")
        + "\n\n【输出】一段以「【涨停板战法｜盘中买入】」开头的完整指令说明。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_buy_lht() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——仅输出 **【龙回头战法｜盘中买入】** 整段。\n"
        + "仅适用于 JSON「自选股」中战法为龙回头战法的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("buy_lht")
        + "\n\n【输出】一段以「【龙回头战法｜盘中买入】」开头的完整指令说明。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_hold_zt() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——仅输出 **【涨停板战法｜持仓与卖出】** 整段。\n"
        + "仅针对持仓或当日决策中与下列条文相关的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("during_hold_zt")
        + "\n\n【输出】以「【涨停板战法｜持仓与卖出】」开头；勿写买入开立仓细则。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_hold_lht() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——仅输出 **【龙回头战法｜持仓与卖出】** 整段。\n"
        + "仅针对适用下列条文的标的。\n\n"
        + "【策略条文】\n"
        + load_strategy_snippet("during_hold_lht")
        + "\n\n【输出】以「【龙回头战法｜持仓与卖出】」开头；仅写持仓监控与卖出，勿重复输出买入章已述的进场条件。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_during_positions() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】盘中——输出 **【持仓更新】JSON**、**【账户风控底线】**、**【数据纪律】**。\n"
        + "【策略条文】\n"
        + load_strategy_snippet("during_positions_policy")
        + "\n\n【持仓更新】规则：有变更则输出非空 JSON；无变更则 [] 及一行「持仓未更新原因」。\n"
        + "格式须含股票代码、名称、买入时间、买入价、买入原因；卖出补全卖出时间与卖出价。\n"
        + "【数据纪律】持仓与委托仅引用 JSON「自选股」「持仓股」及本节条文；勿复述上文战法细则。\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_evening_narrative() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】晚间复盘正文（一至八）：概括大盘、市场分析、自选与持仓表现、人气榜、实际操作、盈亏、经验教训。\n"
        + "**不要输出「自选更新」或任何自选 JSON**。\n"
        + TRADING_PHASE_WORKFLOW_BLOCK
        + "\n\n【策略条文】\n"
        + load_strategy_snippet("review_narrative") + "\n"
        + "请严格按下列小节输出：\n\n"
        + "一、今日大盘概况\n"
        + "- 上证指数：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}\n"
        + "- 深证成指：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}\n"
        + "- 创业板指：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}\n"
        + "- 两市成交额：{亿元}（较昨日{±%}）\n"
        + "- 上涨家数 / 下跌家数 / 平盘家数：{a}/{b}/{c}\n"
        + "- 涨停家数 / 跌停家数：{x}/{y}\n\n"
        + "二、今日市场分析\n"
        + "{综述}\n\n"
        + "三、自选股全天表现（仅 JSON「自选股」；若为空则只写无自选股）\n\n"
        + "四、持仓股全天表现（仅 JSON「持仓股」；若为空则只写无持仓）\n\n"
        + "五、同花顺人气榜（仅 JSON「同花顺人气榜」；缺失则写本节无数据）\n\n"
        + "六、今日实际操作\n"
        + "（卖出/买入/无操作）\n\n"
        + "七、今日盈亏\n"
        + "- 当日总盈亏：{±金额}元（{±%}）\n"
        + "- 当前总资产：{金额}元（初始本金见用户数据）\n\n"
        + "八、经验及教训总结\n"
        + "{总结}\n"
        + LLM_OUTPUT_FORMAT
    )


def _system_lunch_narrative() -> str:
    return (
        LLM_PERSONA_CORE
        + "\n\n【当前任务】午间复盘正文（一至五）：上午盘面、关键事件、自选与持仓半日表现、下午策略调整。\n"
        + "**不要输出「自选更新」或自选 JSON**。\n"
        + TRADING_PHASE_WORKFLOW_BLOCK
        + "\n\n【策略条文】\n"
        + load_strategy_snippet("review_narrative") + "\n"
        + "请严格按下列小节输出：\n\n"
        + "一、上午大盘回顾\n"
        + "二、上午关键事件\n"
        + "三、自选股表现（仅 JSON「自选股」）\n"
        + "四、持仓股表现（仅 JSON「持仓股」）\n"
        + "五、下午操作策略调整\n"
        + LLM_OUTPUT_FORMAT
    )


def _stitch_review_optional_section(
    label: str, arr_zt: list, arr_lht: list, tail_zt: str, tail_lht: str
) -> str:
    """合并两路自选 JSON + 原因行；label 如「六、自选更新」「九、自选更新」。"""
    merged_in = _normalize_optional_rows(arr_zt) + _normalize_optional_rows(arr_lht)
    merged = []
    seen: set = set()
    for row in merged_in:
        k = (row.get("股票代码", ""), row.get("战法", ""))
        if k in seen:
            continue
        seen.add(k)
        merged.append(row)
    lines = [label, json.dumps(merged, ensure_ascii=False)]
    for piece in (tail_zt, tail_lht):
        for ln in piece.splitlines():
            t = ln.strip()
            if t and ("原因" in t or "未更新" in t):
                lines.append(t)
    return "\n".join(lines)


def _run_review_optional_parallel(raw_data: dict, tail: str, *, lunch: bool) -> str:
    """午间/晚间：正文一路 + 涨停自选 + 龙回头自选，三路并行；各任务独立字段说明与条文。"""
    u_n = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_NARRATIVE)
    u_zt = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_ZT)
    u_lh = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_LHT)
    sys_n = _system_lunch_narrative() if lunch else _system_evening_narrative()
    narrative, tz, lh = _parallel_map_call_str(
        lambda: call_llm(sys_n, u_n, max_tokens=120000, temperature=0.1),
        lambda: call_llm(_system_review_optional_zt(), u_zt, max_tokens=120000, temperature=0.06),
        lambda: call_llm(_system_review_optional_lht(), u_lh, max_tokens=120000, temperature=0.06),
    )
    arr_zt, tail_zt = _parse_first_json_array_from_text(tz)
    arr_lh, tail_lh = _parse_first_json_array_from_text(lh)
    opt_block = _stitch_review_optional_section(
        "六、自选更新" if lunch else "九、自选更新",
        arr_zt,
        arr_lh,
        tail_zt,
        tail_lh,
    )
    return narrative.rstrip() + "\n\n" + opt_block


def _run_pre_market_parallel(raw_data: dict, tail: str) -> str:
    u_m = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_NARRATIVE)
    u_zt = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_ZT)
    u_lh = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_LHT)
    m, zt, lht = _parallel_map_call_str(
        lambda: call_llm(_system_pre_market_main(), u_m, max_tokens=120000, temperature=0.16),
        lambda: call_llm(_system_pre_market_zt(), u_zt, max_tokens=120000, temperature=0.16),
        lambda: call_llm(_system_pre_market_lht(), u_lh, max_tokens=120000, temperature=0.16),
    )
    return m.rstrip() + "\n\n" + zt.strip() + "\n\n" + lht.strip()


def _run_during_market_parallel(raw_data: dict, tail: str) -> str:
    u_o = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_COMMON)
    u_zt = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_ZT)
    u_lh = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_LHT)
    u_pos = _market_data_user_block(raw_data, tail=tail, guide=RAW_DATA_FIELD_GUIDE_COMMON)
    p1, p2, p3, p4, p5, p6 = _parallel_map_call_str(
        lambda: call_llm(_system_during_overview(), u_o, max_tokens=120000, temperature=0.16),
        lambda: call_llm(_system_during_buy_zt(), u_zt, max_tokens=12000, temperature=0.16),
        lambda: call_llm(_system_during_buy_lht(), u_lh, max_tokens=12000, temperature=0.16),
        lambda: call_llm(_system_during_hold_zt(), u_zt, max_tokens=12000, temperature=0.16),
        lambda: call_llm(_system_during_hold_lht(), u_lh, max_tokens=12000, temperature=0.16),
        lambda: call_llm(_system_during_positions(), u_pos, max_tokens=120000, temperature=0.16),
    )
    return "\n\n".join(x.strip() for x in (p1, p2, p3, p4, p5, p6) if x.strip())


# ========== 新闻处理 ==========
def process_news(raw_data: dict, timestamp: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")
    os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
    news_file = f"{DATA_DIR}/news/{today}/{time_str}.md"

    system = """你是一个专业的财经资讯处理助手。请根据以下多渠道获取的新闻原文（可能包含重复、噪音），完成去噪、去重、提炼。

【输入新闻数据】
{粘贴多条新闻，每条可标注来源}

【输出要求】
1. 按重要度降序排列，输出前20条。
2. 生成一段"解读"，站在短线交易者角度，分析这些新闻对大盘、板块、市场情绪可能产生的影响，并指出短线操作需要关注的方向。
3. 【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等markdown符号。
4. 严格按以下纯文本格式输出：

1、{新闻标题或核心内容}
2、{新闻标题或核心内容}
……

解读：{综合解读，说明对大盘/板块/情绪的可能影响，以及短线交易需注意的方向}"""

    user = f"原始新闻：\n{json.dumps(raw_data, ensure_ascii=False)[:140000]}"
    summary = call_llm(system, user)

    # 保存原始数据（UTF-8）
    with open(news_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    # 保存处理后的数据
    with open(news_file, "w", encoding="utf-8") as f:
        f.write(f"# 新闻 {timestamp}\n\n{summary}\n")
    return summary

# ========== 盘前分析 ==========
def analyze_pre_market(raw_data: dict, timestamp: str) -> str:
    return _run_pre_market_parallel(raw_data, _tail_fund_only())


# ========== 盘中分析 ==========
def analyze_during_market(raw_data: dict, timestamp: str) -> str:
    return _run_during_market_parallel(raw_data, _tail_during_market_user())


# ========== 午间复盘 ==========
def analyze_lunch_market(raw_data: dict, timestamp: str) -> str:
    return _run_review_optional_parallel(raw_data, _tail_lunch_review(), lunch=True)


# ========== 晚间复盘 ==========
def analyze_evening_market(raw_data: dict, timestamp: str) -> str:
    return _run_review_optional_parallel(raw_data, _tail_evening_review(), lunch=False)

# ========== 解析并更新 ==========
def _section_heading_regex(keyword: str) -> list[str]:
    """匹配「序号、关键词」「【关键词】」或单独一行关键词。"""
    kw = re.escape(keyword)
    return [
        rf"(?:^|\n)(\s*(?:\d|[一二三四五六七八九十百千]+)\s*[,，、\.．]\s*{kw})\s*",
        rf"(?:^|\n)(\s*【\s*{kw}\s*】)\s*",
        rf"(?:^|\n)(\s*{kw})\s*[:：]?\s*",
    ]


def _find_section_tail_start(content: str, section_keyword: str) -> list[int]:
    """返回可能的「小节标题之后」起始下标列表（自后往前尝试时可优先靠后的段落）。"""
    import re as _re

    tails: list[int] = []
    for pat in _section_heading_regex(section_keyword):
        for m in _re.finditer(pat, content):
            tails.append(m.end())
    # 去重保序
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
    """
    解析「X、{section_keyword}」等标题后的第一个有效 JSON 数组。
    返回 (列表, (数组起始下标, 数组结束下标))；数组坐标用于飞书中替换 JSON 为可读文本。
    """
    def try_parse_from(abs_start_scan: int) -> tuple[list, tuple[int, int]] | None:
        max_len = min(8000, len(content) - abs_start_scan)
        if max_len <= 0:
            return None
        scan = content[abs_start_scan : abs_start_scan + max_len]
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
                inner = _strip_markdown_fence_after(raw_for_load)
                raw_for_load = inner.strip()
            arr = _json_loads_array_relaxed(raw_for_load)
            if arr is None:
                continue
            if not isinstance(arr, list):
                continue
            if len(arr) == 0:
                return [], (s0, s1)
            if not all(isinstance(x, dict) for x in arr):
                continue
            if any(any(k in x for k in stock_keys) for x in arr):
                return arr, (s0, s1)
        return None

    # 1) 标准小节标题后扫描
    for tail_start in reversed(_find_section_tail_start(content, section_keyword)):
        got = try_parse_from(tail_start)
        if got:
            return got[0], got[1]

    # 2) 回退：出现的「关键词」后紧跟数组（漏序号、英文标点等）
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
    """从「加入自选原因」前缀推断战法（与提示词「【涨停板战法】」「【龙回头战法】」对齐）。"""
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
        reason = (
            str(r.get("加入自选原因", "") or r.get("自选原因", "") or r.get("原因", "") or "").strip()
        )
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
        buy_p = r.get("买入价", r.get("买入价格", ""))
        sell_p = r.get("卖出价", r.get("卖出价格", ""))
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
            "买入时间": str(r.get("买入时间", "") or "").strip(),
            "买入价": buy_p,
            "买入原因": str(r.get("买入原因", "") or "").strip(),
            "卖出时间": str(r.get("卖出时间", "") or "").strip(),
            "卖出价": sell_p,
            "卖出原因": str(r.get("卖出原因", "") or "").strip(),
        }
        if d["股票代码"] or d["股票名称"]:
            out.append(d)
    return out


def _holding_item_to_readable(h: dict) -> str:
    parts: list[str] = []
    code = h.get("股票代码", "")
    name = h.get("股票名称", "")
    parts.append(f"股票名称：{name}，股票代码：{code}")
    field_order = [
        ("买入时间", "买入时间"),
        ("买入价", "买入价格"),
        ("买入原因", "买入原因"),
        ("卖出时间", "卖出时间"),
        ("卖出价", "卖出价格"),
        ("卖出原因", "卖出原因"),
    ]
    for k, label in field_order:
        v = h.get(k)
        if v is None or str(v).strip() == "":
            continue
        parts.append(f"{label}：{v}")
    return "，".join(parts)


def _optional_item_to_readable(o: dict) -> str:
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


def replace_json_sections_for_feishu(
    content: str,
    *,
    optional_span: tuple[int, int] | None,
    optional_lines: list[str],
    holdings_span: tuple[int, int] | None,
    holdings_lines: list[str],
) -> str:
    """将正文中的 JSON 数组段替换为编号列表或「为空」说明；仅当成功定位 span 时替换。"""
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
    """
    解析正文中的 JSON 数组并更新本地持仓/自选记录，返回飞书替换所需字段。
    返回 dict：holdings_text, optional_text, holdings_lines, optional_lines,
    holdings_span, optional_span, normalized_holdings, normalized_optional
    """
    holdings_raw, h_span = _extract_json_array_with_span(content, "持仓更新")
    optional_raw, o_span = _extract_json_array_with_span(content, "自选更新")

    holdings = _normalize_holding_rows(holdings_raw)
    optional = _normalize_optional_rows(optional_raw)

    holdings_lines = [_holding_item_to_readable(h) for h in holdings] if holdings else []
    optional_lines = [_optional_item_to_readable(o) for o in optional] if optional else []

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
        save_holdings(holdings)
        print(f"持仓已更新: {holdings}")
    elif mode in ("during_market", "pre_market") and h_span is not None and not holdings:
        print("持仓更新 JSON 为 []；请确认正文已含「持仓未更新原因」")

    if mode in ("post_market_lunch", "post_market_evening") and o_span is not None:
        if optional:
            save_optional(optional)
            print(f"自选股已更新（共 {len(optional)} 条）: {optional}")
        else:
            print("自选更新 JSON 为 []；请确认正文已含「自选未更新原因」")

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

def save_review(timestamp: str, content: str, mode: str, raw_data: dict = None):
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    suffix = "wujian" if mode == "post_market_lunch" else "fupan"
    time_str = datetime.now().strftime("%H_%M_%S")
    review_file = f"{DATA_DIR}/trade/{today}/{suffix}-{time_str}.md"
    # 保存原始数据
    if raw_data:
        with open(review_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
    # 保存处理后的数据
    with open(review_file, "w", encoding="utf-8") as f:
        f.write(content)
    parts = content.split("\n\n", 1)
    if len(parts) > 1:
        llm_only_path = review_file.replace(".md", "-llm-only.txt")
        try:
            with open(llm_only_path, "w", encoding="utf-8") as fp:
                fp.write(parts[1])
        except OSError:
            pass

# 保存原始数据（通用函数）
def save_raw_data(mode: str, raw_data: dict):
    """保存各模式的原始数据到磁盘"""
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")

    if mode == "news":
        # 新闻：保存到 news/{today}/{time_str}-origin.json
        os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/news/{today}/{time_str}-origin.json"
    elif mode == "pre_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/pre_market-{time_str}-origin.json"
    elif mode == "during_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/during_market-{time_str}-origin.json"
    else:
        return  # 其他模式走save_review

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
    except:
        return True

# ========== 主流程 ==========
def main():
    if len(sys.argv) < 2:
        print("用法: python main.py <news|pre_market|during_market|post_market_lunch|post_market_evening>")
        sys.exit(1)

    mode = sys.argv[1]
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d")

    labels = {
        "news": "新闻聚焦",
        "pre_market": "盘前分析",
        "during_market": "盘中实时",
        "post_market_lunch": "午间复盘",
        "post_market_evening": "晚间复盘"
    }
    label = labels.get(mode, mode)

    print(f"[{timestamp}] 开始处理 {mode}...")

    if mode != "news" and not is_trading_day():
        print(f"今日非交易日，跳过")
        #return

    fetch_map = {
        "news": fetch_news,
        "pre_market": fetch_pre_market,
        "during_market": fetch_during_market,
        "post_market_lunch": fetch_post_market,
        "post_market_evening": fetch_post_market
    }
    if mode not in fetch_map:
        print(f"未知模式: {mode}")
        sys.exit(1)

    try:
        # data = fetch_map[mode]()
        # 测试，直接读文件获取数据 TODO
        data = json.loads(_read_user_text(_PROJECT_ROOT / 'data' / mode))
        print(f"数据拉取成功")
    except Exception as e:
        print(f"数据拉取失败: {e}")
        sys.exit(1)

    analysis = ""
    try:
        if mode == "news":
            summary = process_news(data, timestamp)
            analysis = f"【{label}】{timestamp}\n\n{summary}"
            save_raw_data(mode, data)
        elif mode == "pre_market":
            raw_data_pre = data
            analysis = f"【{label}】{timestamp}\n\n{analyze_pre_market(data, timestamp)}"
            save_raw_data(mode, raw_data_pre)
        elif mode == "during_market":
            raw_data_during = data
            analysis = f"【{label}】{timestamp}\n\n{analyze_during_market(data, timestamp)}"
            save_raw_data(mode, raw_data_during)
        elif mode == "post_market_lunch":
            raw_data_lunch = data
            analysis = f"【{label}】{timestamp}\n\n{analyze_lunch_market(data, timestamp)}"
            save_review(timestamp, analysis, mode, raw_data_lunch)
        elif mode == "post_market_evening":
            raw_data_evening = data
            analysis = f"【{label}】{timestamp}\n\n{analyze_evening_market(data, timestamp)}"
            save_review(timestamp, analysis, mode, raw_data_evening)
        print("分析完成")
    except Exception as e:
        print(f"分析失败: {e}")
        analysis = f"【{label}】{timestamp}\n\n分析服务异常，请稍后重试。"

    # 用于飞书推送的文本（将持仓/自选 JSON 替换为可读编号列表）
    feishu_content = analysis

    try:
        pu = parse_and_update(
            analysis,
            mode,
            market_payload=_unwrap_quant_market_payload(data)
            if mode in ("post_market_lunch", "post_market_evening")
            else None,
        )
        feishu_content = replace_json_sections_for_feishu(
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

    print("\n" + "="*60)
    out_show = feishu_content
    print(out_show[:2000] if len(out_show) > 2000 else out_show)
    print("="*60)

if __name__ == "__main__":
    main()
