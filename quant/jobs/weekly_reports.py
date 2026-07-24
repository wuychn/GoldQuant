"""定时任务：每周回测 / ML 校准 + 飞书推送。"""

from __future__ import annotations

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.ml.train import train_stock_rank
from quant.push.service import push_message
from quant.timeutil import cn_datetime_str


def format_backtest_report(metrics: dict) -> str:
    return (
        f"成交 {metrics.get('trade_count', 0)} 笔\n"
        f"总收益 {metrics.get('total_return', 0)*100:.2f}%\n"
        f"最大回撤 {metrics.get('max_drawdown', 0)*100:.2f}%\n"
        f"胜率 {metrics.get('win_rate', 0)*100:.1f}%"
    )


def format_ml_report(meta: dict) -> str:
    return f"校准完成 apply={meta.get('apply')} 建议见 ml_calibration.yml"


def run_weekly_backtest(settings=None) -> None:
    del settings
    metrics = run_backtest(ml_mode="shadow")
    push_message("每周回测", format_backtest_report(metrics), timestamp=cn_datetime_str())


def run_weekly_ml(settings=None) -> None:
    del settings
    meta = train_stock_rank()
    push_message("每周ML校准", format_ml_report(meta or {}), timestamp=cn_datetime_str())
