"""每周六回测、每周日 ML 校准，结果推送飞书。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.utils.error_log import log_caught_error
from quant.progress_log import log_progress, log_progress_done, log_progress_error

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


def _push_feishu(label: str, body: str) -> None:
    from quant.push.feishu import get_token, send_msg
    from quant.push.format import format_push_message
    from quant.timeutil import cn_datetime_str

    token = get_token()
    send_msg(format_push_message(label, cn_datetime_str(), body), token)


def _pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def format_backtest_report(result: dict[str, Any]) -> str:
    if int(result.get("days_run") or 0) == 0:
        return (
            "未找到可重放的盘中快照（~/.quant/daily/*/raw/during*.json）。\n"
            "请先正常运行若干交易日智能盯盘以积累 daily 归档。"
        )
    lines = [
        f"区间：{result.get('date_from', '')} ~ {result.get('date_to', '')}",
        f"重放交易日：{result.get('days_run')} 天",
        f"成交：{result.get('trade_count')} 笔（卖 {result.get('sell_count')} 笔）",
        f"胜率：{_pct(float(result.get('win_rate') or 0))} | 盈亏比：{result.get('profit_factor')}",
        f"总收益：{_pct(float(result.get('total_return') or 0))} | 最大回撤：{_pct(float(result.get('max_drawdown') or 0))}",
        f"已实现盈亏：{result.get('realized_pnl')} 元 | 期末资产：{result.get('final_equity')} 元",
        f"被拒单：{result.get('rejected_orders')}（涨停/跌停/资金不足等）",
    ]
    return "\n".join(lines)


def _format_dimension_weights(weights: dict[str, float]) -> str:
    if not weights:
        return ""
    lines = ["建议维度权重："]
    for name, value in sorted(weights.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"  {name}: {value}")
    return "\n".join(lines)


def format_ml_report(result: object) -> str:
    lines: list[str] = [
        f"方法：{result.method}",
        f"样本数：{result.sample_count}",
    ]
    if result.thresholds:
        th = result.thresholds
        lines.append(
            f"建议阈值：自选 {th.get('watchlist_threshold')} / "
            f"买入 {th.get('buy_threshold')} / 卖出 {th.get('sell_threshold')}"
        )
    weight_block = _format_dimension_weights(getattr(result, "dimension_weights", None) or {})
    if weight_block:
        lines.append(weight_block)
    elif getattr(result, "method", "") in ("auto", "linear", "lightgbm"):
        lines.append("建议维度权重：（未生成，样本过少或优化失败）")
    if result.walk_forward:
        wf = result.walk_forward
        status = "通过" if wf.get("passed") else "未通过"
        lines.append(
            f"walk-forward：{status}（训练 {wf.get('train_size')} / 测试 {wf.get('test_size')}，"
            f"测试 F1={wf.get('test_f1')}）"
        )
    if result.apply_blocked:
        lines.append("配置未自动写入（apply 门禁未通过或样本不足）。")
    else:
        lines.append("门禁已通过；若启用 QUANT_SCHED_WEEKLY_ML_APPLY 将写入 ml_calibration.yml。")
    for note in result.notes:
        lines.append(f"提示：{note}")
    return "\n".join(lines)


def run_weekly_backtest(settings: Settings) -> None:
    scope = "weekly_backtest"
    log_progress(scope, "开始每周回测")
    try:
        from quant.backtest.broker import BrokerConfig
        from quant.backtest.engine import run_backtest
        from quant.timeutil import cn_today

        weeks = max(1, int(settings.QUANT_SCHED_WEEKLY_BACKTEST_LOOKBACK_WEEKS))
        to_date = cn_today().isoformat()
        from_date = (cn_today() - timedelta(weeks=weeks)).isoformat()
        cash = float(settings.QUANT_SCHED_WEEKLY_BACKTEST_INITIAL_CASH)
        result = run_backtest(
            from_date=from_date,
            to_date=to_date,
            broker_cfg=BrokerConfig(initial_cash=cash),
        )
        body = format_backtest_report(result)
        _push_feishu("每周回测", body)
        log_progress_done(scope, "完成并已推送飞书", detail=f"days_run={result.get('days_run')}")
    except Exception as e:
        log_progress_error(scope, "回测失败", detail=str(e))
        log_caught_error(logger, "每周回测", e)
        try:
            _push_feishu("每周回测", f"执行失败：{e}")
        except Exception as push_err:
            log_caught_error(logger, "每周回测失败通知", push_err)


def run_weekly_ml(settings: Settings) -> None:
    scope = "weekly_ml"
    log_progress(scope, "开始每周 ML 校准")
    try:
        from quant.ml.calibrate import calibrate, clear_scoring_cache, write_calibration

        result = calibrate(
            settings.QUANT_SCHED_WEEKLY_ML_METHOD,
            min_samples=int(settings.QUANT_SCHED_WEEKLY_ML_MIN_SAMPLES),
            lightgbm_min_samples=int(settings.QUANT_SCHED_WEEKLY_ML_LIGHTGBM_MIN_SAMPLES),
        )
        if settings.QUANT_SCHED_WEEKLY_ML_APPLY and not result.apply_blocked:
            write_calibration(result, apply=True)
            clear_scoring_cache()
            applied = "已写入 ~/.quant/config/ml_calibration.yml"
        else:
            applied = "未写入配置"
        body = format_ml_report(result) + f"\n\n{applied}。"
        _push_feishu("每周ML校准", body)
        log_progress_done(scope, "完成并已推送飞书", detail=applied)
    except Exception as e:
        log_progress_error(scope, "ML 校准失败", detail=str(e))
        log_caught_error(logger, "每周 ML 校准", e)
        try:
            _push_feishu("每周ML校准", f"执行失败：{e}")
        except Exception as push_err:
            log_caught_error(logger, "每周 ML 失败通知", push_err)
