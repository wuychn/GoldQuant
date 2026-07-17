"""~/.quant 目录路径定义。

目录约定
--------
~/.quant/
  state/     程序读写源（JSON/JSONL）
  views/     人类可读 MD（由 state 自动生成）
  daily/     按交易日归档（raw/derived/trades/review）
  config/    运行时 quant.yml、ml_calibration.yml（可选）；别名见包内 quant/config/
  memory/    新闻摘要、经验教训
  cache/     跨日短缓存（如个股基本信息）
  archive/   跨日汇总（预留）
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from quant.timeutil import cn_now


def quant_home() -> Path:
    """量化运行时数据根目录。

    解析优先级（高 → 低）：

    1. ``GOLDQUANT_QUANT_HOME_DIR`` —— 来自环境变量或 ``.env``（经 ``Settings`` 读取）；
    2. ``QUANT_HOME`` —— 环境变量，便于 shell 临时覆盖、无需 ``GOLDQUANT_`` 前缀；
    3. 用户主目录下 ``~/.quant``（默认）。

    返回路径仅做 ``expanduser``，不做 ``resolve``，避免目录尚不存在时报错。
    运行期改环境变量后再次调用本函数即可生效；模块级 ``QUANT_HOME`` 常量仅在导入时刻解析一次。
    """
    raw = ""
    try:
        from app.core.config import get_settings

        raw = (get_settings().QUANT_HOME_DIR or "").strip()
    except Exception:
        # 初始化期或测试桩未注入 settings 时回退到环境变量，保持本模块可独立使用。
        raw = ""
    if not raw:
        raw = (os.environ.get("QUANT_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".quant"


#: 向后兼容：模块级常量，导入时刻解析。运行期改环境变量请改用 :func:`quant_home`。
QUANT_HOME = quant_home()


def ensure_layout() -> None:
    """首次运行时创建标准子目录。"""
    for sub in (
        "config",
        "state",
        "views",
        "daily",
        "archive",
        "memory",
        "cache",
    ):
        (quant_home() / sub).mkdir(parents=True, exist_ok=True)


def today_str(now: datetime | None = None) -> str:
    """北京时间日期字符串 YYYY-MM-DD。"""
    dt = now or cn_now()
    return dt.strftime("%Y-%m-%d")


def daily_dir(d: str | None = None) -> Path:
    """某日归档根目录 $QUANT_HOME/daily/{date}。"""
    return quant_home() / "daily" / (d or today_str())


def daily_raw(name: str, d: str | None = None) -> Path:
    """API 原始快照：daily/{date}/raw/{name}。"""
    return daily_dir(d) / "raw" / name


def daily_derived(name: str, d: str | None = None) -> Path:
    """程序衍生结果：评分、信号、市场状态等。"""
    return daily_dir(d) / "derived" / name


def daily_trades(name: str, d: str | None = None) -> Path:
    """成交与盈亏：executed.json / pnl.json。"""
    return daily_dir(d) / "trades" / name


def daily_review(name: str, d: str | None = None) -> Path:
    """复盘与飞书正文 MD。"""
    return daily_dir(d) / "review" / name


def daily_cache(name: str, d: str | None = None) -> Path:
    """当日可复用缓存：daily/{date}/cache/{name}（个股概念已迁至 $QUANT_HOME/cache/ 周缓存）。"""
    return daily_dir(d) / "cache" / name


def quant_cache_file(name: str) -> Path:
    """跨日短缓存：$QUANT_HOME/cache/{name}。"""
    return quant_home() / "cache" / name


def state_file(name: str) -> Path:
    """热状态：optional.jsonl、holding.jsonl、account.json 等。"""
    return quant_home() / "state" / name


def view_file(name: str) -> Path:
    """只读视图 MD，勿手改。"""
    return quant_home() / "views" / name


def memory_file(name: str) -> Path:
    """长期记忆：news_summary.txt、lessons.md。"""
    return quant_home() / "memory" / name


def config_file(name: str) -> Path:
    """用户级配置，覆盖 quant/config/ 包内默认。"""
    return quant_home() / "config" / name


def package_config(name: str) -> Path:
    """包内默认配置路径（只读源）。"""
    return Path(__file__).resolve().parent.parent / "config" / name
