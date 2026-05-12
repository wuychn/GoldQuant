"""常量、路径、全局配置。"""

import os
import re
from pathlib import Path

from app.core.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_FILE = _PROJECT_ROOT / "strategy.md"

BASE_URL = "http://localhost:8085"

DATA_DIR = os.path.expanduser("~/.quant")
FUND_FILE = f"{DATA_DIR}/fund.md"
OPTIONAL_FILE = f"{DATA_DIR}/optional.jsonl"
HOLDING_FILE = f"{DATA_DIR}/holding.jsonl"
STOPLOSS_FILE = f"{DATA_DIR}/stoploss.jsonl"
INITIAL_CAPITAL = 10000
OPTIONAL_HISTORY_FILE = f"{DATA_DIR}/optional_history.jsonl"
POPULARITY_FILE = f"{DATA_DIR}/popularity_history.md"
NEWS_IMPACT_SUMMARY_FILE = f"{DATA_DIR}/news_market_impact_summary.txt"
MEMORY_FILE = f"{DATA_DIR}/MEMORY.md"

OPTIONAL_STRATEGY_ALLOWED = frozenset({"涨停板战法", "龙回头战法"})

LLM_OUTPUT_FORMAT = "\n【输出格式要求】纯文本，禁止使用 markdown 的 #、*、- 等排版符号。\n"

_LLM_PARALLEL_WORKERS = max(2, min(8, (os.cpu_count() or 4)))

MEMORY_MAX_INJECT_CHARS = 2000
MEMORY_COMPRESS_THRESHOLD_ENTRIES = 30
MEMORY_COMPRESS_THRESHOLD_CHARS = 3000

_RE_THINKING = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


def get_feishu_config() -> tuple[str, str, str]:
    """返回 (APP_ID, APP_SECRET, USER_ID)，从 .env 读取。"""
    cfg = get_settings()
    app_id = cfg.FEISHU_APP_ID or ""
    app_secret = cfg.FEISHU_APP_SECRET or ""
    user_id = cfg.FEISHU_USER_ID or ""
    return app_id, app_secret, user_id
