"""~/.quant 目录路径定义。

目录约定
--------
~/.quant/
  state/     程序读写源（JSON/JSONL）
  views/     人类可读 MD（由 state 自动生成）
  daily/     按交易日归档（raw/derived/trades/review）
  config/    用户 scoring.yml、gates.yml、ml_calibration.yml
  memory/    新闻摘要、经验教训
  archive/   跨日汇总（预留）
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SH_TZ = ZoneInfo("Asia/Shanghai")
QUANT_HOME = Path.home() / ".quant"


def ensure_layout() -> None:
    """首次运行时创建标准子目录。"""
    for sub in (
        "config",
        "state",
        "views",
        "daily",
        "archive",
        "memory",
    ):
        (QUANT_HOME / sub).mkdir(parents=True, exist_ok=True)


def today_str(now: datetime | None = None) -> str:
    """北京时间日期字符串 YYYY-MM-DD。"""
    dt = now or datetime.now(_SH_TZ)
    return dt.strftime("%Y-%m-%d")


def daily_dir(d: str | None = None) -> Path:
    """某日归档根目录 ~/.quant/daily/{date}。"""
    return QUANT_HOME / "daily" / (d or today_str())


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


def state_file(name: str) -> Path:
    """热状态：optional.jsonl、holding.jsonl、account.json 等。"""
    return QUANT_HOME / "state" / name


def view_file(name: str) -> Path:
    """只读视图 MD，勿手改。"""
    return QUANT_HOME / "views" / name


def memory_file(name: str) -> Path:
    """长期记忆：news_summary.txt、lessons.md。"""
    return QUANT_HOME / "memory" / name


def config_file(name: str) -> Path:
    """用户级配置，覆盖 quant/config/ 包内默认。"""
    return QUANT_HOME / "config" / name


def package_config(name: str) -> Path:
    """包内默认配置路径（只读源）。"""
    return Path(__file__).resolve().parent.parent / "config" / name
