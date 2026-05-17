"""常量、路径、全局配置。"""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

_QUANT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _QUANT_DIR.parent
STRATEGY_FILE = _QUANT_DIR / "strategy.md"
_RULES_CONFIG_YML = _QUANT_DIR / "rules_config.yml"

BASE_URL = "http://localhost:8085"

DATA_DIR = os.path.expanduser("~/.quant")
FUND_FILE = f"{DATA_DIR}/fund.md"
POSITION_MV_FILE = f"{DATA_DIR}/position_market_value.md"
ACCOUNT_STATE_FILE = f"{DATA_DIR}/account_state.json"
OPTIONAL_FILE = f"{DATA_DIR}/optional.jsonl"
OPTIONAL_MD_FILE = f"{DATA_DIR}/optional.md"
OBSERVATION_POOL_FILE = f"{DATA_DIR}/observation_pool.jsonl"
OBSERVATION_POOL_MD_FILE = f"{DATA_DIR}/observation_pool.md"
HOLDING_FILE = f"{DATA_DIR}/holding.jsonl"
STOPLOSS_FILE = f"{DATA_DIR}/stoploss.jsonl"
INITIAL_CAPITAL = 10000
OPTIONAL_HISTORY_FILE = f"{DATA_DIR}/optional_history.jsonl"
POPULARITY_FILE = f"{DATA_DIR}/popularity_history.md"
NEWS_IMPACT_SUMMARY_FILE = f"{DATA_DIR}/news_market_impact_summary.txt"
MEMORY_FILE = f"{DATA_DIR}/MEMORY.md"

OPTIONAL_STRATEGY_ALLOWED = frozenset({"涨停板战法", "龙回头战法", "主升浪战法"})

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


def _coerce_truthy_yaml(v: object, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def trading_time_checks_enabled() -> bool:
    """是否启用「时间校验」：须为沪深真实交易日且在连续竞价时段（见 trading_hours）。

    读取 ``quant/rules_config.yml`` 顶层 ``trading.time_validation_enabled``；
    未配置则回退到 ``trading.enforce_real_workday``（兼容旧字段）。
    缺省均为 ``true``；为 ``False`` 时买卖信号可随时生成、执行。
    """
    if not _RULES_CONFIG_YML.is_file():
        return True
    try:
        mtime_ns = _RULES_CONFIG_YML.stat().st_mtime_ns
    except OSError:
        return True
    return _trading_flags_from_yaml(mtime_ns)[0]


def trading_enforce_real_workday() -> bool:
    """兼容旧命名：等同 :func:`trading_time_checks_enabled`。"""
    return trading_time_checks_enabled()


@lru_cache(maxsize=8)
def _trading_flags_from_yaml(_mtime_ns: int) -> tuple[bool]:
    """(time_checks_enabled,) — 缓存键为 yaml mtime_ns。"""
    try:
        with open(_RULES_CONFIG_YML, "r", encoding="utf-8") as f:
            root = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return (True,)
    if not isinstance(root, dict):
        return (True,)
    block = root.get("trading")
    if not isinstance(block, dict):
        return (True,)
    if "time_validation_enabled" in block:
        return (_coerce_truthy_yaml(block.get("time_validation_enabled"), True),)
    return (_coerce_truthy_yaml(block.get("enforce_real_workday"), True),)
