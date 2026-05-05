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

from app.core.config import get_settings
import requests
import json
from datetime import datetime

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

# 供 LLM 解读行情 JSON：与后端 ``quant_endpoint`` 聚合结构一致，避免把人气榜/涨停池误写进「自选」。
RAW_DATA_FIELD_GUIDE = """【接口 JSON 顶层字段说明（必须按键名区分，禁止混用）】
「自选股」：唯一表示用户自选股的数组；元素可含「战法」字段（**涨停板战法** / **龙回头战法**，或由旧数据产生的未标注）。只有该键下的标的才允许出现在正文里所有标题含「自选」的小节中。若该键为 [] 或不存在，对应小节只能写一句「无自选股」或「当前自选股为空」，不得用其他任何键里的股票名单顶替。盘中买卖须与该标的「战法」一致，禁止混用两套战法逻辑。
「持仓股」：用户持仓，凡标题含「持仓」的小节仅使用该键；若为空则写无持仓。
「同花顺人气榜」：不是用户自选。涨停战法盘中判定「人气前十」请用「人气排名」≤10；禁止把该键写入「自选股」。
「涨停统计」「概念板块」「大盘指数」「赚钱效应」「大盘资金流」「市场状态机」等：市场环境或榜单类数据，其中的个股/代码不得当作「自选股」列出。其中「概念板块」含「涨幅榜」「资金流入榜」各约前十条，行业名需与个股「所属概念」做模糊对应。
「大盘资金流」与「个股资金流」：资金流明细中以「日期」「主力净流入-净额」等为准。
「盘口」：包含「最新」「均价」（分时均价）、「量比」、五档买卖等字段。
「盘中10分钟线」：仅返回**交易日当日**已存档 K 线。
复盘正文末尾「自选更新」JSON（午间/晚间出现）：按策略「2.1」「2.2」与人气榜前20/前50筛选，每条须含「战法」「加入自选原因」，作为下一交易日观察池；不得因当日已涨停而省略合格标的。"""

# 自选更新：不以 strategy 做服务端过滤，以模型输出为准（解析逻辑见下文）。
OPTIONAL_UPDATE_RULES_BLOCK = """
【自选更新说明】
「自选更新」JSON 以模型输出为准。若为 []，你必须在输出中单独一行写明「自选未更新原因：……」。
每项须含「战法」（取值仅为「涨停板战法」或「龙回头战法」）、「加入自选原因」（须与战法一致，不得以另一套战法说理）。
若填写「加入自选原因」，请尽量与当次业务 JSON 中的字段（如「同花顺人气榜」「涨停统计」「概念板块」「历史行情」等）描述一致，便于复盘。
"""

# 各阶段职责：避免模型把「复盘补自选」当成「盘中能否追涨」或把「今日涨停」误判为无需加入自选。
TRADING_PHASE_WORKFLOW_BLOCK = """
【交易闭环阶段分工（必须遵守，勿混淆）】
1、盘前：依据开盘情况、接口 JSON「自选股」「持仓股」与 交易策略，制定**当日**可执行纲要；对符合策略条件的标的给出**挂单**要素（方向、委托价、时刻、仓位）。
2、盘中（真实交易）：**买入**仅限来自**此前复盘「自选更新」已纳入、且体现在接口 JSON「自选股」中的标的**，且当前仍满足盘中追击/龙回头条件；**同一标的须按 JSON「自选股」内「战法」字段选用对应战法段落（涨停战法 / 龙回头），严禁交叉套用**；**卖出**仅限来自 JSON「持仓股」。禁止仅凭「同花顺人气榜」热度对未入池股票开仓。
3、复盘（午间/晚间）：回顾**当日**行情与账户操作，总结盈亏与教训，并**为下一交易日储备自选**。涨停板战法按 strategy「2.1 涨停板战法 – 加入自选」从「同花顺人气榜」**前20条**内筛选；龙回头按「2.2 龙回头战法 – 加入自选」从**前50条**内筛选（若接口仅返回约20条轻量数据，则在该范围内按策略尽其所能筛选）。
"""

OPTIONAL_UPDATE_RULES_REVIEW_BLOCK = """
【复盘｜自选更新专项规则】
「自选更新」表示按 2.1 / 2.2 **纳入下一交易日观察自选池**，与盘中此刻能否再买、是否已涨停**不是同一判断**。
- 凡符合「2.1」或「2.2」加入自选条件的标的，**均应**在「自选更新」中给出条目（含「战法」「加入自选原因」）。**今日已涨停**常属于次日验证持续性/接力观察，**不得**以「已涨停无法买入」「仅一只标的」「等待明日验证」等为由输出空数组 []。
- 仅当按交易策略逐项核对后**确实无任何标的**满足 2.1/2.2 加入条件（例如人气榜缺失、或全场无任何条目满足硬条件）时，方可输出 []，并单独一行写「自选未更新原因：……」。
"""

# 自选 JSON 合法战法取值（与提示词、落盘字段一致）
OPTIONAL_STRATEGY_ALLOWED = frozenset({"涨停板战法", "龙回头战法"})

STRATEGY_ISOLATION_BLOCK = """
【战法隔离（严禁混用、乱用）】
- 「涨停板战法」仅遵循 strategy **2.1** 及人气榜前 **20**、人气排名≤10、板块对齐、涨停统计与盘口追击等口径；「龙回头战法」仅遵循 **2.2** 及人气榜前 **50**、均线/量比/回调与资金流等口径。**禁止**在同一标的的「加入自选原因」里混写两套逻辑（例如对龙回头标的大谈人气排名是否符合涨停前十）。
- 「自选更新」JSON **每一条必须含「战法」**，取值只能是 **`涨停板战法`** 或 **`龙回头战法`**（与 2.1 / 2.2 一一对应）；「加入自选原因」须以 **`【涨停板战法】`** 或 **`【龙回头战法】`** 开头，后续论据只能使用该战法允许的字段。
- **次日盘中**：执行涨停战法买入时，仅允许 JSON「自选股」中该标的 **`战法` 为「涨停板战法」**；执行龙回头买入时，仅允许 **`战法` 为「龙回头战法」**。禁止用龙回头均线逻辑操作标注为涨停战法的自选，反之亦然。（历史自选缺「战法」字段时，须在文中单独说明并按单一战法审慎处理，不得混判。）
"""

REVIEW_OUTPUT_SELF_CHECK_BLOCK = """
【午间/晚间复盘｜输出自选 JSON 前自检（须逐项完成后再写 JSON，减少遗漏与摇摆）】
① **涨停战法**：仅在「同花顺人气榜」前 **20** 条内，独立按 strategy **2.1** 筛一遍，记下合格代码（只用 2.1 口径）。
② **龙回头**：仅在「同花顺人气榜」前 **50** 条内（接口不足则按实际条数），独立按 **2.2** 筛一遍，记下合格代码（只用 2.2 口径）。
③ 同一代码可同时出现在两步，但输出 JSON 时**每条记录只能填一个「战法」**，且「加入自选原因」严禁混用两套说理。
④ 每条对象必须含 **`战法`** + **`加入自选原因`**（原因前缀与战法一致）。
⑤ 仅当①②均无合格标的时，才输出 []，并写「自选未更新原因」。
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


def _market_data_user_block(raw_data: dict, *, tail: str) -> str:
    """行情类分析：字段说明 → 当日新闻总结 → 接口 JSON → tail。"""
    payload = _unwrap_quant_market_payload(raw_data)
    return (
        RAW_DATA_FIELD_GUIDE
        + _daily_news_tail_for_prompt()
        + "\n\n【以下为接口业务数据 JSON（已去掉 code/message 外层；请仅按上文键名解读）】\n"
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
    fund = get_fund()
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战，操作风格比肩赵老哥、章盟主。你正在执行真实资金账户的当日决策，须对盈亏负责。

【实盘契约】
- 禁止「建议」「可参考」「观察为主」等推脱表述；输出必须是可执行的挂单指令（方向、仓位、委托价、时间点），使用具体时间格式 yyyy-MM-dd HH:mm:ss 或当日 HH:mm:ss。
- 必须写明加自选原因、买卖时点与价格；目标是组合净值大幅可持续增值，而非泛泛而谈。
- 概率游戏 + 纪律执行 + 知行合一；宁可卖飞，不可被套。

""" + TRADING_PHASE_WORKFLOW_BLOCK + STRATEGY_ISOLATION_BLOCK + """

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
若条目含「战法」，请按「涨停板战法」「龙回头战法」分列简述，勿混写判定逻辑。
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{异动/正常}
2、……

三、持仓股（仅根据 JSON 根键「持仓股」数组；为空则本小节只写「无持仓」一句）
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{成本价}，{浮盈亏}，{明确处置意向：持有/减仓/清仓及价位时间}
2、……

四、涨停板战法（集合竞价与盘中）
- 本段**仅**用于 JSON「自选股」中 **「战法」=「涨停板战法」** 的标的；缺「战法」的旧数据须在文中明确正按涨停战法核对后再给单，**禁止**用龙回头均线逻辑覆盖此类标的。
- 集合竞价：仍可按策略评估高开区间与量比；若准备挂单须给出限价与 deadline。
- 盘中追击（使用盘中接口 JSON）：标的「人气排名」须 ≤10（「同花顺人气榜」返回约 20 条轻量数据，以前十名为准）；个股「所属概念」须能与「概念板块」中「涨幅榜」前十或「资金流入榜」前十的行业名对应；盘口「均价」为分时均线，现价须站上均价方可买入；给出买入价、时刻与仓位。

五、龙回头战法（定价与资金口径）
- 本段**仅**用于 **「战法」=「龙回头战法」** 的自选标的；**禁止**用涨停战法的人气前十/板块对齐要求去套龙回头标的。
- 买入区间须同时参照「5日线」「10日线」「20日线」或「技术指标」内均线；量能看盘口「量比」；资金看「个股资金流」最近一条的「主力净流入-净额」方向与规模。

六、市场判断与当日执行纲要
{深度分析结论}

七、【持仓更新】（仅此一处输出持仓 JSON 数组）
规则：若有增删改持仓，输出非空 JSON；若本次无需变更，输出 []，并在下一行单独写明「持仓未更新原因：……」（如无新开平仓指令、与上次持仓一致、缺口数据无法下单等）。
格式：须含股票代码、名称、买入时间（yyyy-MM-dd HH:mm:ss）、买入价、买入原因；卖出补全卖出时间与卖出价。
[{"股票代码":"600000","股票名称":"浦发银行","买入时间":"2026-05-03 09:31:00","买入价":11.2,"买入原因":"……","卖出时间":"","卖出价":"","卖出原因":""}]"""

    user = _market_data_user_block(
        raw_data,
        tail=f"\n\n【当前资金】：{fund:.2f} 元",
    )

    return call_llm(system, user, max_tokens=140000, temperature=0.18)

# ========== 盘中分析 ==========
def analyze_during_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员。你正在执行真实资金账户，须对每一笔指令负责。

【实盘契约】
- 禁止「建议」「仅供参考」；输出可执行的买卖挂单要素：代码、名称、方向、仓位比例、委托价、触发时刻（yyyy-MM-dd HH:mm:ss 或当日清晰时点）。
- 必须交代加自选原因、买卖时间与价格；目标是净值大幅增值。

""" + TRADING_PHASE_WORKFLOW_BLOCK + STRATEGY_ISOLATION_BLOCK + """

【核心原则】
- 热点就是印钞机，退潮前先手卖出；宁可卖飞，不可被套。

【交易策略】（必须严格遵守）
""" + strategy + """

【重要】禁止使用markdown格式，纯文本输出，避免任何#、*、-等符号。

请严格按以下格式输出：

【市场状态】{强势/震荡/弱势}（{综合描述：上证位置、昨日涨停指数、成交额、连板高度}）
【总仓位限制】依策略第六章 | 当前持仓{x%}

【涨停板战法｜盘中买入】
条件自查：仅当该标的在 JSON「自选股」中 **「战法」=「涨停板战法」**（或缺省但你在正文已明确仅按涨停战法处理）时，才适用本段。JSON「同花顺人气榜」含人气 **前 20 条**（轻量字段）；买卖判定仍以 **人气排名≤10** 为准；标的所属概念须对应「概念板块」之「涨幅榜」前十或「资金流入榜」前十；个股盘口请用「自选股」/「持仓股」或另行行情，勿假定人气榜条目含盘口。若标的仅在人气榜第11～20名，不按涨停战法盘中追击。盘口「最新」须高于「均价」（分时均线）方可买入。**禁止对「战法」=「龙回头战法」的标的套用本节追击逻辑。**
输出：标的代码与名称，方向买入，仓位{x%}，委托价与时刻，止损与目标价。

【龙回头战法｜盘中买入】
条件自查：仅当 **「战法」=「龙回头战法」** 时适用。现价处于 5/10/20 日均线允许区间（见「5日线」「10日线」「20日线」或「技术指标」）；量能参考盘口「量比」；资金参考「个股资金流」当日记录的「主力净流入-净额」。**禁止对「涨停板战法」标的套用本节均线/回调逻辑。**
输出：委托价、时刻、仓位、止损。

【涨停/龙回头｜持仓处理】
- {股票名称}：浮盈/浮亏{x%}，{卖出或持有的明确价位与时间条件}

【持仓更新】（仅此一处输出持仓 JSON 数组）
规则：若非空 JSON，列出完整持仓变更；若为 []，须在下一行写明「持仓未更新原因：……」。
每条须含买入时间（yyyy-MM-dd HH:mm:ss）、买入价、买入原因；卖出须含卖出时间与卖出价。
[{"股票代码":"600000","股票名称":"浦发银行","买入时间":"2026-05-03 10:30:00","买入价":11.2,"买入原因":"……","卖出时间":"","卖出价":"","卖出原因":""}]

【风控】{触发状态描述}

【数据纪律】提及「自选股」仅能引用 JSON「自选股」数组；该键为空不得编造。"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【今日交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"
        ),
    )

    return call_llm(system, user, max_tokens=140000, temperature=0.18)

# ========== 午间复盘 ==========
def analyze_lunch_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战。你正在执行真实资金账户，须对输出负责，禁止空泛建议。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

""" + TRADING_PHASE_WORKFLOW_BLOCK + STRATEGY_ISOLATION_BLOCK + """

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
""" + REVIEW_OUTPUT_SELF_CHECK_BLOCK + OPTIONAL_UPDATE_RULES_BLOCK + OPTIONAL_UPDATE_RULES_REVIEW_BLOCK + """
六、自选更新
规则：输出自选 JSON 数组；若无须纳入观察池的标的，输出 []，并在下一行写明「自选未更新原因：……」。
【格式：JSON数组；每项必须含「战法」「加入自选原因」。战法取值仅为「涨停板战法」或「龙回头战法」，且须与加入自选原因前缀一致】
[{"股票代码":"600000","股票名称":"浦发银行","战法":"涨停板战法","加入自选原因":"【涨停板战法】人气排名与板块对齐……"}]"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【上午交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"
        ),
    )

    return call_llm(system, user, max_tokens=140000, temperature=0.08)

# ========== 晚间复盘 ==========
def analyze_evening_market(raw_data: dict, timestamp: str) -> str:
    fund = get_fund()
    trades = read_trades(datetime.now().strftime("%Y-%m-%d"))
    strategy = load_strategy()

    system = """【角色】你是一位顶尖A股短线交易员，身经百战。你正在执行真实资金账户，须对输出负责，禁止空泛建议。

【核心原则】
- 概率游戏 + 纪律执行 + 知行合一
- 热点就是印钞机，退潮前先手卖出
- 宁可卖飞，不可被套
- 有鱼捕鱼，无鱼织网，空仓等待不勉强

""" + TRADING_PHASE_WORKFLOW_BLOCK + STRATEGY_ISOLATION_BLOCK + """

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

五、同花顺人气榜（仅 JSON「同花顺人气榜」；若该键缺失或为空则写「本节无数据」；不得与第三节混写）
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
""" + REVIEW_OUTPUT_SELF_CHECK_BLOCK + OPTIONAL_UPDATE_RULES_BLOCK + OPTIONAL_UPDATE_RULES_REVIEW_BLOCK + """
九、自选更新
规则：输出自选 JSON 数组；若无须纳入观察池的标的，输出 []，并在下一行写明「自选未更新原因：……」。
【格式：JSON数组；每项必须含「战法」「加入自选原因」。战法取值仅为「涨停板战法」或「龙回头战法」，且须与加入自选原因前缀一致】
[{"股票代码":"600000","股票名称":"浦发银行","战法":"龙回头战法","加入自选原因":"【龙回头战法】均线与量比……"}]"""

    user = _market_data_user_block(
        raw_data,
        tail=(
            f"\n\n【当前资金】：{fund:.2f} 元\n"
            f"【初始本金】：{INITIAL_CAPITAL}元\n"
            f"【今日实际交易记录】：\n"
            f"{json.dumps(trades, ensure_ascii=False) if trades else '无交易'}"
        ),
    )

    return call_llm(system, user, max_tokens=140000, temperature=0.08)

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
