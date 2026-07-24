"""维度 IC 漂移监控。"""

from __future__ import annotations

from typing import Any

from quant.ml.dataset import load_score_samples
from quant.research.factor.ic import factor_report


def detect_ic_drift(
    *,
    recent_days: int = 30,
    baseline_days: int = 90,
) -> dict[str, Any]:
    """按交易日切片（非样本条数）比较 baseline / recent 的日度 RankIC。"""
    samples = load_score_samples()
    if not samples:
        return {"alerts": [], "reason": "无样本"}
    ordered = sorted(samples, key=lambda s: s.date)
    dates = sorted({s.date for s in ordered})
    need = recent_days + baseline_days
    if len(dates) < need:
        return {
            "alerts": [],
            "reason": f"交易日不足：有{len(dates)}日，需要至少{need}日",
            "n_dates": len(dates),
        }

    recent_dates = set(dates[-recent_days:])
    baseline_dates = set(dates[-(baseline_days + recent_days) : -recent_days])
    recent = [s for s in ordered if s.date in recent_dates]
    baseline = [s for s in ordered if s.date in baseline_dates]
    if not baseline or not recent:
        return {"alerts": [], "reason": "样本区间不足"}

    base_ic = {r["dim"]: r["ic_mean"] for r in factor_report(baseline)}
    recent_ic = {r["dim"]: r["ic_mean"] for r in factor_report(recent)}
    alerts = []
    for dim, b in base_ic.items():
        r = recent_ic.get(dim, 0)
        if b * r < 0 and abs(b) > 0.05:
            alerts.append(
                {
                    "dim": dim,
                    "baseline_ic": b,
                    "recent_ic": r,
                    "type": "sign_flip",
                }
            )
    return {
        "alerts": alerts,
        "baseline_count": len(baseline),
        "recent_count": len(recent),
        "baseline_days": len(baseline_dates),
        "recent_days": len(recent_dates),
    }


def detect_and_act(
    *,
    apply: bool = False,
    dry_run: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """检测漂移；apply=True 时写入维度降权覆盖。"""
    from quant.monitoring.actions import apply_drift_actions

    drift = detect_ic_drift(**kwargs)
    if not apply:
        return {"drift": drift, "actions": [], "note": "未申请动作（传 apply=True）"}
    acted = apply_drift_actions(dry_run=dry_run, **kwargs)
    return {"drift": drift, **acted}
