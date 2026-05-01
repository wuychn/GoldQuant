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

# 数据目录
DATA_DIR = os.path.expanduser("~/data/quant")
FUND_FILE = f"{DATA_DIR}/fund.md"
OPTIONAL_FILE = f"{DATA_DIR}/optional.jsonl"
HOLDING_FILE = f"{DATA_DIR}/holding.jsonl"
STRATEGY_FILE = f"{DATA_DIR}/strategy.md"
INITIAL_CAPITAL = 10000

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
        with open(FUND_FILE, 'r') as f:
            return float(f.read().strip())
    except:
        return INITIAL_CAPITAL

def update_fund(profit: float):
    fund = get_fund() + profit
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = {}
    try:
        with open(FUND_FILE, 'r') as f:
            content = f.read()
            import re
            hist = re.findall(r'(\d{4}-\d{2}-\d{2}):\s*([\d.]+)', content)
            for d, v in hist:
                existing[d] = float(v)
        existing[today.split(' ')[0]] = fund
        lines = ["# 资金曲线", f"- 初始本金：{INITIAL_CAPITAL:.2f} 元", f"- 更新时间：{today}",
                 f"- 当前总资产：{fund:.2f} 元", f"- 当日盈亏：{profit:+.2f} 元 ({profit/INITIAL_CAPITAL*100:+.2f}%)",
                 "- 历史记录（累计）："]
        for d, v in sorted(existing.items()):
            lines.append(f"{d}: {v:.2f}")
        with open(FUND_FILE, 'w') as f:
            f.write('\n'.join(lines))
    except:
        with open(FUND_FILE, 'w') as f:
            f.write(str(int(fund)))
    return fund

def _read_jsonl_stock_file(path: str) -> list:
    """每行一条 JSON 对象；``#`` 开头为注释；单行可为数组则展开。"""
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
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
        with open(f"{DATA_DIR}/trade/{today}/trades.md", 'r') as f:
            return json.loads(f.read())
    except:
        return []

def save_trades(today: str, trades: list):
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    with open(f"{DATA_DIR}/trade/{today}/trades.md", 'w') as f:
        f.write(json.dumps(trades, ensure_ascii=False, indent=2))

def load_strategy() -> str:
    try:
        with open(STRATEGY_FILE, 'r') as f:
            return f.read()
    except:
        return "策略文件加载失败"

def read_today_news(today: str) -> str:
    news_dir = f"{DATA_DIR}/news/{today}"
    if not os.path.exists(news_dir):
        return "当日暂无新闻记录"
    try:
        files = sorted([f for f in os.listdir(news_dir) if f.endswith('.md')])
        contents = []
        for f in files:
            with open(os.path.join(news_dir, f), 'r') as fp:
                contents.append(fp.read())
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

    # 保存原始数据
    with open(news_file.replace('.md', '-origin.json'), 'w') as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    # 保存处理后的数据
    with open(news_file, 'w') as f:
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

二、自选股（根据行情数据）
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{异动/正常}
2、……

三、持仓股（根据行情数据）
1、{股票名称}（{代码}）：{当前价}，{涨跌幅}，{成本价}，{浮盈亏}
2、……

四、市场判断及操作策略
{深度分析结论}
今日初步计划：
- 买入观察标的：{代码+名称}，理由……
- 卖出/减仓标的：{代码+名称}，理由……
- 持仓股处理：{具体操作建议}"""

    user = f"""【行情数据】（包含自选股、持仓股、行情等全部数据）
{json.dumps(raw_data, ensure_ascii=False)[:100000]}

【当前资金】：{fund:.2f} 元"""

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

【风控】{触发状态描述}"""

    user = f"""【盘中实时数据】（包含自选股、持仓股、行情等全部数据）
{json.dumps(raw_data, ensure_ascii=False)[:100000]}

【当前资金】：{fund:.2f} 元
【今日交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"""

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

三、自选股表现
1、{名称}（{代码}）：半日涨跌幅{x%}，最高/最低{价格}，{点评}
2、……

四、持仓股表现
1、{名称}（{代码}）：半日涨跌幅{x%}，浮盈亏{y%}，{点评}
2、……

五、下午操作策略调整
- 原计划回顾：{是否按计划执行}
- 下午修正：{具体买入/卖出/持有决策及理由}

六、自选更新
【格式：JSON数组；每项必须含「加入自选原因」，缺一无效】
[{"股票代码":"002580","股票名称":"圣阳股份","加入自选原因":"涨停战法-同花顺人气前20且当日涨停"}]"""

    user = f"""【上午行情数据】（包含自选股、持仓股、行情等全部数据）
{json.dumps(raw_data, ensure_ascii=False)[:100000]}

【当前资金】：{fund:.2f} 元
【上午交易记录】：{json.dumps(trades, ensure_ascii=False) if trades else '无'}"""

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

三、自选股全天表现
1、{名称}（{代码}）：收盘{价格}（{涨跌幅}），振幅{x%}，{亮点/问题}
2、……

四、持仓股全天表现
1、{名称}（{代码}）：收盘{价格}（{涨跌幅}），浮盈亏{y%}，{操作评价}
2、……

五、同花顺人气股
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

九、自选更新
【格式：JSON数组；每项必须含「加入自选原因」，缺一无效】
[{"股票代码":"002580","股票名称":"圣阳股份","加入自选原因":"人气龙头-次日接力观察"}]"""

    user = f"""【今日盘后数据】（包含自选股、持仓股、行情等全部数据）
{json.dumps(raw_data, ensure_ascii=False)[:100000]}

【当前资金】：{fund:.2f} 元
【初始本金】：{INITIAL_CAPITAL}元
【今日实际交易记录】：
{json.dumps(trades, ensure_ascii=False) if trades else '无交易'}"""

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


def parse_and_update(content: str, mode: str) -> dict:
    """
    解析正文中的 JSON 数组，写入持仓/自选文件，并返回飞书替换所需字段。
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
        with open(f"{DATA_DIR}/trade/{today}/profit.md", "w") as f:
            f.write(str(int(profit)))
        print(f"盈亏: {profit}")

    if new_fund is not None:
        with open(FUND_FILE, "w") as f:
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
        with open(review_file.replace('.md', '-origin.json'), 'w') as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
    # 保存处理后的数据
    with open(review_file, 'w') as f:
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
        with open(raw_file, 'w') as f:
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
        pu = parse_and_update(analysis, mode)
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
