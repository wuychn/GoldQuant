"""研究晋升流水线：IC / OOS 门禁 → 校准草案写回。"""

from __future__ import annotations

from typing import Any

import yaml

from quant.config import load_quant_config, reload_config_cache
from quant.ml.dataset import load_score_samples
from quant.ml.objective import evaluate_objective
from quant.ml.validation import walk_forward_validate
from quant.research.factor.ablation import dimension_ablation_ic
from quant.research.factor.ic import factor_report
from quant.store.paths import config_file, ensure_layout
from quant.timeutil import cn_datetime_str


def _research_cfg() -> dict:
    return load_quant_config().get("research") or {}


def evaluate_promotion_gates(
    *,
    samples=None,
) -> dict[str, Any]:
    """评估是否达到晋升生产的最低门禁。"""
    cfg = _research_cfg()
    promote = cfg.get("promote") or {}
    samples = samples if samples is not None else load_score_samples()
    min_samples = int(promote.get("min_samples", 80))
    min_icir = float(promote.get("min_total_icir", 0.3))
    min_oos = float(promote.get("min_oos_sharpe", cfg.get("min_oos_sharpe", 0.5)))
    min_ic_mean = float(promote.get("min_total_ic_mean", 0.02))

    if len(samples) < min_samples:
        return {
            "passed": False,
            "reason": f"样本不足 {len(samples)}<{min_samples}",
            "n_samples": len(samples),
        }

    report = factor_report(samples)
    total = next((r for r in report if r.get("dim") == "__total__"), {}) or {}
    ic_mean = float(total.get("ic_mean") or 0)
    icir = float(total.get("icir") or 0)
    obj = evaluate_objective(samples, objective=str(cfg.get("ml_objective", "sharpe")))
    proxy_sharpe = float(obj.get("proxy_sharpe") or 0)

    from quant.ml.calibrate import _base_thresholds
    from quant.config import load_scoring_config

    base_th = _base_thresholds(load_scoring_config())
    wf = walk_forward_validate(samples, base_thresholds=base_th, min_test_sharpe=min_oos)

    checks = {
        "ic_mean_ok": ic_mean >= min_ic_mean,
        "icir_ok": icir >= min_icir,
        "proxy_sharpe_ok": proxy_sharpe >= min_oos * 0.5,  # 代理宽松
        "walk_forward_ok": bool(wf.get("passed")),
    }

    # 多维 IC 的 BH-FDR：至少总 IC 通过，且多数正贡献维在 FDR 下显著时加分项
    from quant.ml.objective import daily_equal_weight_returns, sharpe_from_daily_returns
    from quant.research.significance import benjamini_hochberg, deflated_sharpe_ratio

    dim_rows = [r for r in report if r.get("dim") and r.get("dim") != "__total__"]
    p_vals = []
    for r in dim_rows:
        # 用 ICIR 近似 z → 双侧 p（粗）
        icir_d = float(r.get("icir") or 0)
        n_days = max(int(r.get("n_days") or 1), 1)
        z = icir_d * (n_days**0.5)
        from scipy.stats import norm

        p_vals.append(float(2 * (1 - norm.cdf(abs(z)))))
    fdr = benjamini_hochberg(p_vals, alpha=float(cfg.get("fdr_alpha", 0.05)))
    daily = daily_equal_weight_returns(samples)
    obs_sr = sharpe_from_daily_returns(daily)
    # n_trials：阈值网格近似规模（wt×bt）
    n_trials = int(promote.get("n_trials", 36))
    dsr = deflated_sharpe_ratio(
        obs_sr,
        n_obs=max(len(daily), 1),
        n_trials=n_trials,
    )
    checks["fdr_any_dim"] = int(fdr.get("n_rejected") or 0) > 0 or ic_mean >= min_ic_mean
    checks["dsr_ok"] = bool(dsr.get("significant_5pct")) or obs_sr >= min_oos
    passed = all(
        checks[k]
        for k in ("ic_mean_ok", "icir_ok", "proxy_sharpe_ok", "walk_forward_ok", "dsr_ok")
    )
    return {
        "passed": passed,
        "checks": checks,
        "total_ic": {"ic_mean": ic_mean, "icir": icir, "n_days": total.get("n_days")},
        "objective": obj,
        "walk_forward": {
            "passed": wf.get("passed"),
            "reason": wf.get("reason"),
            "test_sharpe": wf.get("test_sharpe"),
        },
        "fdr": fdr,
        "deflated_sharpe": dsr,
        "n_samples": len(samples),
        "thresholds": {
            "min_total_ic_mean": min_ic_mean,
            "min_total_icir": min_icir,
            "min_oos_sharpe": min_oos,
        },
    }


def build_promotion_artifact(
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """生成晋升报告；可选写 ml_calibration 草案（apply=false 默认不生效）。"""
    samples = load_score_samples()
    gates = evaluate_promotion_gates(samples=samples)
    ablation = dimension_ablation_ic(samples)
    artifact = {
        "generated_at": cn_datetime_str(),
        "gates": gates,
        "ablation_summary": {
            "baseline": ablation.get("baseline"),
            "positive_dims": [
                d
                for d, m in (ablation.get("ablations") or {}).items()
                if m.get("positive_contribution")
            ],
            "weak_dims": [
                d
                for d, m in (ablation.get("ablations") or {}).items()
                if not m.get("positive_contribution")
            ],
        },
        "factor_report": factor_report(samples)[:20],
    }

    ensure_layout()
    path = config_file("promotion_report.yml")
    path.write_text(yaml.safe_dump(artifact, allow_unicode=True, sort_keys=False), encoding="utf-8")
    artifact["report_path"] = str(path)

    if apply and gates.get("passed"):
        # 写校准草案：弱维降权标记，需人工确认 apply:true
        weak = artifact["ablation_summary"]["weak_dims"]
        draft = {
            "generated_at": artifact["generated_at"],
            "apply": False,
            "notes": [
                "由 research promote 生成；确认后将 apply 改为 true",
                f"门禁通过: {gates.get('passed')}",
            ],
            "dimension_weights_hint": {d: "review_downweight" for d in weak},
            "gates": gates.get("checks"),
        }
        draft_path = config_file("ml_calibration_draft.yml")
        draft_path.write_text(
            yaml.safe_dump(draft, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        artifact["draft_path"] = str(draft_path)
    elif apply and not gates.get("passed"):
        artifact["apply_skipped"] = "门禁未通过，未写校准草案"

    return artifact


def run_promote_cli(*, apply: bool = False, json_out: bool = False) -> dict[str, Any]:
    out = build_promotion_artifact(apply=apply)
    reload_config_cache()
    return out
