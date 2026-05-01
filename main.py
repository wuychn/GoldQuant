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
from pathlib import Path

# 禁用代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        del os.environ[k]

import requests
import json
from datetime import datetime

# ========== 配置 ==========
BASE_URL = "http://localhost:8085"
FEISHU_APP_ID = "cli_a96dcfa5d3f91bd4"
FEISHU_APP_SECRET = "eXhbDo1Ldh4sMGkBjVUjdhAiiBFZ6ld6"
FEISHU_USER_ID = "ou_bc3cefb641bbc53148de964a637d8cfd"
LLM_API_KEY = "sk-cp-hlnhKJEBNgidhd_VzCm8eFuxlBJcwLLKNxF8EoBHWMGyOYrov_lsflxjGabM5kWmGc4v1LQgn3nasdNk0qZhpRWyX5q-hwn0UIkozrnTyQVcJOZD7gOcj-Q"
LLM_BASE_URL = "https://api.minimaxi.com/anthropic"
LLM_MODEL = "MiniMax-M2.7"

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

# 供 LLM 解读行情 JSON：与后端 ``quant_endpoint`` 聚合结构一致，避免把人气榜/涨停池误写进「自选」。
RAW_DATA_FIELD_GUIDE = """【接口 JSON 顶层字段说明（必须按键名区分，禁止混用）】
「自选股」：唯一表示用户自选股的数组，数据来自本机 ~/data/quant/optional.jsonl。只有该键下的标的才允许出现在正文里所有标题含「自选」的小节中。若该键为 [] 或不存在，对应小节只能写一句「无自选股」或「当前自选股为空」，不得用其他任何键里的股票名单顶替。
「持仓股」：用户持仓，来自 ~/data/quant/holding.jsonl；凡标题含「持仓」的小节仅使用该键；若为空则写无持仓。
「同花顺人气股」：仅盘后等部分接口会出现，为同花顺人气榜标的，不是用户自选；禁止写入「自选股」相关小节；仅在明确要求写人气股的小节（如晚间「五、同花顺人气股」）中使用该键数据。
「涨停统计」「概念板块」「大盘指数」「赚钱效应」「大盘资金流」「市场状态机」等：市场环境或榜单类数据，其中的个股/代码不得当作「自选股」列出。"""

# 「自选更新」JSON：与 strategy.md §2.1 等对齐，供模型与落盘前校验共用表述。
OPTIONAL_UPDATE_RULES_BLOCK = """
【自选更新硬性规则（与 strategy.md 一致；做不到则不要写入该项，宁可输出 []）】
若「加入自选原因」出现涨停板战法、龙头板战法、涨停战法、打板战法、打板、龙头接力、连板接力、人气前20、涨停池等以「次日涨停接力」为逻辑的表述（整条理由中若明确为「龙回头」观察则按龙回头规则，不要套用本节），则该股票必须同时满足可核验条件：①在业务 JSON 的「涨停统计」列表中能找到该代码（与 strategy.md 一致：**不含首板**，连板数≥2）；②在「同花顺人气股」中该股「人气排名」≤20；③最近一日收盘价≤15 元；仅当连板数≥6（策略「最高连板」放宽的近似核验）时可放宽至收盘价≤20 元。股价明显高于 15/20 却写龙头板/涨停战法类理由的，属于违规输出，必须改为不写该项或改用与价格、战法真实匹配的表述。
写入「自选更新」的代码须为 60/00/30 开头；名称不得含 ST/*ST；不得写入 688、8 开头标的。
"""


def _unwrap_quant_market_payload(raw: dict) -> dict:
    """统一响应 ``{code,message,data:{...}}`` 时取出内层 ``data``，使「自选股」等与后端字段对齐。"""
    inner = raw.get("data")
    if isinstance(inner, dict):
        return inner
    return raw


def _market_data_user_block(raw_data: dict, *, tail: str) -> str:
    """行情类分析：先字段说明，再 JSON（已去 API 包装层），再资金/交易等尾部文案。"""
    payload = _unwrap_quant_market_payload(raw_data)
    return (
        RAW_DATA_FIELD_GUIDE
        + "\n\n【以下为接口业务数据 JSON（已去掉 code/message 外层；请仅按上文键名解读）】\n"
        + json.dumps(payload, ensure_ascii=False)[:100000]
        + tail
    )


def _float_or_none(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close_from_zt_pool_row(row: dict) -> float | None:
    """东财涨停池记录上常见价格字段。"""
    for k in ("最新价", "现价", "收盘价", "收盘", "最新", "price"):
        p = _float_or_none(row.get(k))
        if p is not None:
            return p
    return None


def _last_close_from_enriched_row(row: dict) -> float | None:
    hist = row.get("历史行情")
    if isinstance(hist, list) and hist:
        last = hist[-1]
        if isinstance(last, dict):
            for k in ("收盘", "收盘价", "close"):
                p = _float_or_none(last.get(k))
                if p is not None:
                    return p
    snap = row.get("盘前实时快照")
    if isinstance(snap, dict):
        p = _float_or_none(snap.get("最新价"))
        if p is not None:
            return p
    return None


def _parse_lianban_ge_6_from_tag(s: str) -> bool:
    """从「连板情况」等文案中判断是否至少 6 连板（§2.1 股价放宽至 20 元）。"""
    if not s:
        return False
    for pat in (r"(\d+)\s*连板", r"(\d+)\s*连", r"(\d+)\s*板"):
        m = re.search(pat, s)
        if m and int(m.group(1)) >= 6:
            return True
    return False


def _hot_rank_int(row: dict) -> int | None:
    v = row.get("人气排名")
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _collect_zt_codes_from_payload(market: dict) -> set[str]:
    out: set[str] = set()
    zt = market.get("涨停统计")
    if isinstance(zt, list):
        for item in zt:
            if not isinstance(item, dict):
                continue
            for k in ("代码", "股票代码"):
                c = item.get(k)
                if c is not None and str(c).strip():
                    out.add(str(c).strip())
                    break
    return out


def _merge_stock_facts(market: dict) -> dict[str, dict]:
    """按代码合并收盘价、人气排名、连板数/连板文案（优先同花顺人气股中的字段）。"""
    facts: dict[str, dict] = {}
    for key in ("同花顺人气股", "自选股", "持仓股"):
        rows = market.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("股票代码", "") or "").strip()
            if not code:
                continue
            slot = facts.setdefault(code, {})
            px = _last_close_from_enriched_row(row)
            if px is not None:
                slot["收盘"] = px
            rk = _hot_rank_int(row)
            if rk is not None:
                slot["人气排名"] = rk
            lb = row.get("连板数")
            if lb is not None:
                try:
                    slot["连板数"] = int(float(lb))
                except (TypeError, ValueError):
                    pass
            tag = str(row.get("连板情况", "") or "").strip()
            if tag:
                slot["连板情况"] = tag
    zt = market.get("涨停统计")
    if isinstance(zt, list):
        for row in zt:
            if not isinstance(row, dict):
                continue
            code = ""
            for k in ("代码", "股票代码"):
                if row.get(k):
                    code = str(row.get(k)).strip()
                    break
            if not code:
                continue
            slot = facts.setdefault(code, {})
            if slot.get("收盘") is None:
                p = _close_from_zt_pool_row(row)
                if p is not None:
                    slot["收盘"] = p
            lb = row.get("连板数")
            if lb is not None and "连板数" not in slot:
                try:
                    slot["连板数"] = int(float(lb))
                except (TypeError, ValueError):
                    pass
    return facts


def _reason_triggers_zhangting_audit(reason: str) -> bool:
    """「加入自选原因」是否写明涨停/龙头板类战法，从而必须满足 strategy.md §2.1 可量化核验项。"""
    if "龙回头" in reason:
        return False
    markers = (
        "涨停板战法",
        "龙头板战法",
        "龙头板",
        "涨停战法",
        "打板战法",
        "打板",
        "龙头接力",
        "连板接力",
        "涨停池",
        "人气前20",
    )
    if any(m in reason for m in markers):
        return True
    if "涨停" in reason and "战法" in reason:
        return True
    return False


def _optional_row_allowed_by_strategy(
    row: dict,
    *,
    facts: dict[str, dict],
    zt_codes: set[str],
) -> tuple[bool, str]:
    """返回 (是否写入, 拒绝说明)。"""
    code = str(row.get("股票代码", "") or "").strip()
    name = str(row.get("股票名称", "") or "").strip()
    reason = str(row.get("加入自选原因", "") or "")
    if not code.startswith(("60", "00", "30")):
        return False, "代码不在60/00/30标的池"
    if code.startswith("688") or code.startswith("8"):
        return False, "排除688/北交所"
    up = name.upper()
    if "ST" in up or "*" in name:
        return False, "名称含ST"
    if not _reason_triggers_zhangting_audit(reason):
        return True, ""
    f = facts.get(code) or {}
    close_px = f.get("收盘")
    if close_px is None:
        return False, "写明涨停/龙头板战法类原因但缺少当日收盘价，无法核验"

    if not zt_codes:
        return False, "写明涨停/龙头板战法但涨停统计为空，无法核验涨停池"

    if code not in zt_codes:
        return False, "写明涨停/龙头板战法但不在当日涨停统计池"

    rank = f.get("人气排名")
    if rank is None:
        return False, "写明涨停/龙头板战法但缺少同花顺人气排名（须人气榜前20）"
    if rank > 20:
        return False, f"写明涨停/龙头板战法但同花顺人气排名{rank}>20"

    lb_count = f.get("连板数")
    tag = str(f.get("连板情况", "") or "")
    ge6 = (isinstance(lb_count, int) and lb_count >= 6) or _parse_lianban_ge_6_from_tag(tag)

    if close_px <= 15.0:
        return True, ""
    if close_px <= 20.0 and ge6:
        return True, ""
    if close_px > 20.0:
        return False, f"收盘{close_px:.2f}元>20，不满足§2.1价格带"
    return False, f"收盘{close_px:.2f}元>15且未见≥6连板，不满足§2.1放宽"


def _filter_optional_by_strategy(
    optional: list,
    market_payload: dict | None,
) -> tuple[list, list[str]]:
    """落盘前按 strategy.md 剔除明显不合规的「自选更新」条目。"""
    if not optional or not isinstance(market_payload, dict):
        return optional, []
    facts = _merge_stock_facts(market_payload)
    zt_codes = _collect_zt_codes_from_payload(market_payload)
    kept: list = []
    logs: list[str] = []
    for row in optional:
        if not isinstance(row, dict):
            continue
        ok, why = _optional_row_allowed_by_strategy(row, facts=facts, zt_codes=zt_codes)
        if ok:
            kept.append(row)
        else:
            nm = row.get("股票名称", "")
            cd = row.get("股票代码", "")
            rs = str(row.get("加入自选原因", "") or "")[:160]
            logs.append(f"剔除自选：{nm}({cd}) — {why}；模型理由摘要：{rs}")
    return kept, logs


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

# ========== LLM调用（带重试）==========
def call_llm(system: str, user: str, max_tokens: int = 2500, retries: int = 3) -> str:
    url = f"{LLM_BASE_URL}/v1/messages"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
        "x-api-key": LLM_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {"model": LLM_MODEL, "messages": [{"role": "user", "content": f"{system}\n\n{user}"}], "max_tokens": max_tokens, "temperature": 0.3}
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=600)
            if resp.status_code == 529:
                print(f"LLM限流，重试({attempt+1}/{retries})...")
                import time; time.sleep(5)
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
                import time; time.sleep(5)
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
    except:
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
    except:
        return []

def save_trades(today: str, trades: list):
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    with open(f"{DATA_DIR}/trade/{today}/trades.md", "w", encoding="utf-8") as f:
        f.write(json.dumps(trades, ensure_ascii=False, indent=2))

def load_strategy() -> str:
    try:
        return STRATEGY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return f"策略文件加载失败：{STRATEGY_FILE}（{e!r}）"

def read_today_news(today: str) -> str:
    news_dir = f"{DATA_DIR}/news/{today}"
    if not os.path.exists(news_dir):
        return "当日暂无新闻记录"
    try:
        files = sorted([f for f in os.listdir(news_dir) if f.endswith('.md')])
        contents = []
        for f in files:
            contents.append(_read_user_text(os.path.join(news_dir, f)))
        return "\n\n".join(contents) if contents else "当日暂无新闻记录"
    except:
        return "当日暂无新闻记录"

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

    user = f"原始新闻：\n{json.dumps(raw_data, ensure_ascii=False)[:100000]}"
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
    fund = get_fund()
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战，起始资金1万元目标财富自由，操作风格比肩赵老哥、欢乐海岸、章盟主。你正在实盘操作，不是辅助工具。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

【交易策略】（必须严格遵守）
""" + strategy + """

【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等符号。

请严格按以下格式输出：

一、市场整体概览
- 上证指数：{当前点位}（{涨跌幅}）
- 深证成指：{当前点位}（{涨跌幅}）
- 创业板指：{当前点位}（{涨跌幅}）
- 市场情绪：{高/中/低}，{简要说明}

二、自选股（仅根据 JSON 根键「自选股」数组；该数组为空则本小节只写「无自选股」一句，不得从人气榜、涨停池、板块等其它键借股票充填）
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{异动/正常}
2、……

三、持仓股（仅根据 JSON 根键「持仓股」数组；为空则本小节只写「无持仓」一句）
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{成本价}，{浮盈亏}
2、……

四、市场判断及操作策略
{深度分析结论}
今日初步计划：
- 买入观察标的：{代码+名称}，理由……
- 卖出/减仓标的：{代码+名称}，理由……
- 持仓股处理：{具体操作建议}"""

    user = _market_data_user_block(
        raw_data,
        tail=f"\n\n【当前资金】：{fund:.2f} 元",
    )

    return call_llm(system, user, max_tokens=100000)

# ========== 盘中分析 ==========
def analyze_during_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战，起始资金1万元目标财富自由，操作风格比肩赵老哥、欢乐海岸、章盟主。你正在实盘操作，不是辅助工具。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

【交易策略】（必须严格遵守）
""" + strategy + """

【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等符号。

请严格按以下格式输出：

【市场状态】{强势/震荡/弱势}（{综合描述：上证位置、昨日涨停指数、成交额、连板高度}）
【总仓位限制】80% | 当前持仓{x%}

【涨停战法买入】
标的：{代码}，方向：买入，仓位：{x%}，委托价：{价格}（{开盘价/现价}），
止损价：{动态计算方式}，目标价：{前高}

【龙回头买入】
标的：{代码}，方向：买入，仓位：{x%}，委托价：{5日线价}，
止损价：{5日线×0.98}，目标价：{前高}

【持仓处理】
- {股票名称}：浮盈{x%}，{处理建议}
- {股票名称}：浮亏{x%}，{处理建议}

【持仓更新】（仅此一处输出 JSON 数组用于同步持仓文件；无持仓则输出 []）
【格式：每条须尽量包含买入时间、买入价格、买入原因；若当日有卖出则补充卖出时间、卖出价格、卖出原因】
[{"股票代码":"600000","股票名称":"浦发银行","买入时间":"10:30","买入价":11.2,"买入原因":"龙头接力","卖出时间":"","卖出价":"","卖出原因":""}]

【风控】{触发状态描述}

【数据纪律】凡模型主动提及「自选股」时，只能引用 JSON 根键「自选股」内的标的；该键为空则不得编造自选名单。"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【今日交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"
        ),
    )

    return call_llm(system, user, max_tokens=100000)

# ========== 午间复盘 ==========
def analyze_lunch_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战，起始资金1万元目标财富自由，操作风格比肩赵老哥、欢乐海岸、章盟主。你正在实盘操作，不是辅助工具。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

【交易策略】（必须严格遵守）
""" + strategy + """

【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等符号。

请严格按以下格式输出：

一、上午大盘回顾
- 上证：{开盘价} → {午间收盘价}（{涨跌幅}）
- 深证：{开盘价} → {午间收盘价}（{涨跌幅}）
- 创业板：{开盘价} → {午间收盘价}（{涨跌幅}）
- 半日成交量：{亿元}，较昨日{放量/缩量}{%}

二、上午关键事件
{根据新闻和走势总结的重要消息或异动}

三、自选股表现（仅 JSON「自选股」；若为空则本小节只写「无自选股」）
1、{名称}（{代码}）：半日涨跌幅{x%}，最高/最低{价格}，{点评}
2、……

四、持仓股表现（仅 JSON「持仓股」；若为空则本小节只写「无持仓」）
1、{名称}（{代码}）：半日涨跌幅{x%}，浮盈亏{y%}，{点评}
2、……

五、下午操作策略调整
- 原计划回顾：{是否按计划执行}
- 下午修正：{具体买入/卖出/持有决策及理由}
""" + OPTIONAL_UPDATE_RULES_BLOCK + """
六、自选更新
【格式：JSON数组；每项必须含「加入自选原因」，缺一无效；无新增则输出 []】
[{"股票代码":"600000","股票名称":"浦发银行","加入自选原因":"符合策略的盘中观察标的（示例占位，无则输出 []）"}]"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【上午交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"
        ),
    )

    return call_llm(system, user, max_tokens=100000)

# ========== 晚间复盘 ==========
def analyze_evening_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战，起始资金1万元目标财富自由，操作风格比肩赵老哥、欢乐海岸、章盟主。你正在实盘操作，不是辅助工具。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

【交易策略】（必须严格遵守）
""" + strategy + """

【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等符号。

请严格按以下格式输出：

一、今日大盘概况
- 上证指数：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}
- 深证成指：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}
- 创业板指：{收盘点位}（{涨跌幅}），最高{点位}，最低{点位}
- 两市成交额：{亿元}（较昨日{±%}）
- 上涨家数 / 下跌家数 / 平盘家数：{a}/{b}/{c}
- 涨停家数 / 跌停家数：{x}/{y}

二、今日市场分析
{综述：关键事件、领涨领跌板块、资金流向、情绪变化、连板高度等}

三、自选股全天表现（仅 JSON「自选股」；若为空则本小节只写「无自选股」）
1、{名称}（{代码}）：收盘{价格}（{涨跌幅}），振幅{x%}，{亮点/问题}
2、……

四、持仓股全天表现（仅 JSON「持仓股」；若为空则本小节只写「无持仓」）
1、{名称}（{代码}）：收盘{价格}（{涨跌幅}），浮盈亏{y%}，{操作评价}
2、……

五、同花顺人气股（仅 JSON「同花顺人气股」；若该键缺失或为空则写「本节无数据」；不得与第三节混写）
1、{名称}（{代码}）：所属板块{板块}，上榜原因{原因}，连板情况{连板数}，收盘涨跌幅{x%}
2、……

六、今日实际操作
1、卖出 {名称}（{代码}）：时间{HH:MM}，价格{价格}，仓位{比例}，原因{理由}
2、买入 {名称}（{代码}）：时间{HH:MM}，价格{价格}，仓位{比例}，原因{理由}
3、无操作

七、今日盈亏
- 当日总盈亏：{±金额}元（{±%}）
- 当前总资产：{金额}元（初始本金{金额}元）

八、经验及教训总结
{根据今日交易记录总结优点、失误与改进点，并形成初步的明日选股方向或关注标的}
""" + OPTIONAL_UPDATE_RULES_BLOCK + """
九、自选更新
【格式：JSON数组；每项必须含「加入自选原因」，缺一无效；无新增则输出 []】
[{"股票代码":"600000","股票名称":"浦发银行","加入自选原因":"策略内次日观察（示例占位，无则输出 []）"}]"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【初始本金】：{INITIAL_CAPITAL}元\n"
            f"【今日实际交易记录】：\n"
            f"{json.dumps(trades, ensure_ascii=False) if trades else '无交易'}"
        ),
    )

    return call_llm(system, user, max_tokens=100000)

# ========== 解析并更新 ==========
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
        d = {
            "股票代码": str(r.get("股票代码", "") or "").strip(),
            "股票名称": str(r.get("股票名称", "") or "").strip(),
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
    解析正文中的 JSON 数组，写入持仓/自选文件，并返回飞书替换所需字段。
    返回 dict：holdings_text, optional_text, holdings_lines, optional_lines,
    holdings_span, optional_span, normalized_holdings, normalized_optional
    """
    holdings_raw, h_span = _extract_json_array_with_span(content, "持仓更新")
    optional_raw, o_span = _extract_json_array_with_span(content, "自选更新")

    holdings = _normalize_holding_rows(holdings_raw)
    optional = _normalize_optional_rows(optional_raw)
    if mode in ("post_market_lunch", "post_market_evening") and market_payload is not None:
        optional, _strategy_drop_logs = _filter_optional_by_strategy(optional, market_payload)
        for line in _strategy_drop_logs:
            print(line)

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

    if holdings and mode == "during_market" and h_span is not None:
        save_holdings(holdings)
        print(f"持仓已更新: {holdings}")

    if mode in ("post_market_lunch", "post_market_evening") and o_span is not None:
        save_optional(optional)
        print(f"自选股已更新（共 {len(optional)} 条）: {optional}")

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
    timestamp = now.strftime("%Y-%m-%d %H:%M")
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
        data = fetch_map[mode]()
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
