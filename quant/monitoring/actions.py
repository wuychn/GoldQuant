"""监控动作：IC 漂移 → 维度降权/关闭写回。"""

from __future__ import annotations

from typing import Any

import yaml

from quant.config import reload_config_cache
from quant.monitoring.drift import detect_ic_drift
from quant.store.paths import config_file, ensure_layout
from quant.timeutil import cn_datetime_str


def load_dimension_overrides() -> dict[str, Any]:
    path = config_file("dimension_overrides.yml")
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def save_dimension_overrides(data: dict[str, Any]) -> str:
    ensure_layout()
    path = config_file("dimension_overrides.yml")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    reload_config_cache()
    return str(path)


def apply_drift_actions(
    *,
    recent_days: int = 30,
    baseline_days: int = 90,
    demote_weight_factor: float = 0.5,
    disable_on_flip: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """对 IC 符号翻转维度执行降权（可选关闭）。

    写入 ~/.quant/config/dimension_overrides.yml，由 load_scoring_config 合并。
    """
    drift = detect_ic_drift(recent_days=recent_days, baseline_days=baseline_days)
    alerts = drift.get("alerts") or []
    actions: list[dict[str, Any]] = []
    overrides = load_dimension_overrides()
    dims_block = dict(overrides.get("dimensions") or {})

    for alert in alerts:
        dim = str(alert.get("dim") or "")
        if not dim or dim == "__total__":
            continue
        entry = dict(dims_block.get(dim) or {})
        if disable_on_flip:
            entry["enabled"] = False
            action = "disable"
        else:
            # 记录降权因子；真正权重在 merge 时乘到现有 weight
            prev = float(entry.get("weight_factor", 1.0) or 1.0)
            entry["weight_factor"] = round(max(0.1, prev * demote_weight_factor), 4)
            action = "demote"
        dims_block[dim] = entry
        actions.append(
            {
                "dim": dim,
                "action": action,
                "baseline_ic": alert.get("baseline_ic"),
                "recent_ic": alert.get("recent_ic"),
                "weight_factor": entry.get("weight_factor"),
                "enabled": entry.get("enabled", True),
            }
        )

    result = {
        "generated_at": cn_datetime_str(),
        "drift": {
            "alert_count": len(alerts),
            "baseline_days": drift.get("baseline_days"),
            "recent_days": drift.get("recent_days"),
        },
        "actions": actions,
        "dry_run": dry_run,
    }
    if dry_run or not actions:
        return result

    payload = {
        "generated_at": result["generated_at"],
        "apply": True,
        "source": "ic_drift",
        "dimensions": dims_block,
        "notes": [f"自动处理 {len(actions)} 个符号翻转维度"],
    }
    path = save_dimension_overrides(payload)
    result["path"] = path
    return result
