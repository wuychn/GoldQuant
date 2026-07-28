"""回测报告导出：权益曲线 + 成交明细 + Markdown 摘要。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from quant.backtest.broker import SimBroker
from quant.backtest.metrics import compute_metrics


def export_report(
    broker: SimBroker,
    out_dir: str,
    *,
    daily=None,
    initial_cash: float = 1_000_000.0,
    strict_signals: bool = True,
    sensitivity: dict | None = None,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    eq_path = out / "equity_curve.csv"
    with open(eq_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        for d, v in broker.equity_curve:
            w.writerow([d, round(v, 2)])

    tr_path = out / "trades.csv"
    with open(tr_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "code", "side", "shares", "price", "cost", "pnl", "reason"])
        for t in broker.trades:
            w.writerow(
                [
                    t.date,
                    t.code,
                    t.side,
                    t.shares,
                    round(t.price, 4),
                    round(t.cost, 2),
                    round(t.pnl, 2),
                    t.reason,
                ]
            )

    metrics = compute_metrics(broker, daily=daily, initial_cash=initial_cash)
    metrics["signal_mode"] = "strict" if strict_signals else "loose"
    if not strict_signals:
        metrics["signal_mode_warning"] = "非官方口径：T日收盘成交，存在乐观偏差"
    if sensitivity:
        metrics["sensitivity"] = sensitivity
    mt_path = out / "metrics.json"
    with open(mt_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    md_path = out / "report.md"
    md_path.write_text(_metrics_to_markdown(metrics), encoding="utf-8")

    return {
        "equity_curve": str(eq_path),
        "trades": str(tr_path),
        "metrics": str(mt_path),
        "report_md": str(md_path),
        "metrics_data": metrics,
    }


def _metrics_to_markdown(m: dict) -> str:
    lines = [
        "# 回测报告",
        "",
        f"- 信号模式: **{m.get('signal_mode', 'strict')}**"
        + (" ⚠️ " + str(m.get("signal_mode_warning", "")) if m.get("signal_mode") == "loose" else ""),
        "",
        "## 核心指标",
        f"- 总收益: {m.get('total_return_pct')}%",
        f"- 年化收益: {m.get('ann_return_pct')}%",
        f"- 年化波动: {m.get('ann_vol_pct')}%",
        f"- Sharpe: {m.get('sharpe')} (NW: {m.get('sharpe_nw')})",
        f"- Sortino: {m.get('sortino')}",
        f"- 最大回撤: {m.get('max_drawdown_pct')}%",
        f"- Calmar: {m.get('calmar')}",
        f"- 年化换手: {m.get('turnover_annual')} (买 {m.get('turnover_buy_annual')} / 卖 {m.get('turnover_sell_annual')})",
        f"- 胜率: {m.get('win_rate')}",
        f"- 盈亏比: {m.get('profit_factor')}",
        f"- 平均持仓天数: {m.get('avg_hold_days')}",
        f"- 基准超额: {m.get('excess_return_pct')}% (IR={m.get('info_ratio')})",
        f"- 成交笔数: {m.get('n_trades')}",
        "",
        "## 风格归因",
    ]
    sa = m.get("style_attribution") or {}
    if sa:
        lines.append(f"- Alpha(年化): {sa.get('alpha_ann_pct')}%")
        betas = sa.get("betas") or {}
        lines.append(
            f"- Beta: market={betas.get('market')} size={betas.get('size')} "
            f"value={betas.get('value')} momentum={betas.get('momentum')}"
        )
        lines.append(f"- R²: {sa.get('r_squared')}")
    cap = m.get("capacity") or {}
    if cap:
        lines.extend(
            [
                "",
                "## 容量",
                f"- 中位 ADV 参与率: {cap.get('median_participation_pct')}%",
                f"- 最大 ADV 参与率: {cap.get('max_participation_pct')}%",
                f"- 建议最大 AUM: {cap.get('suggested_max_aum')}",
            ]
        )
    lines.extend(
        [
            "",
            "## 分年表现",
        ]
    )
    sens = m.get("sensitivity") or {}
    if sens:
        lines.extend(["", "## 参数敏感性"])
        for param, row in sens.items():
            lines.append(
                f"- {param}: stability={row.get('stability')} peak/median={row.get('peak_to_median')}"
            )
    for y, row in (m.get("yearly") or {}).items():
        lines.append(f"- {y}: 收益 {row.get('return_pct')}% / 波动 {row.get('ann_vol_pct')}%")
    lines.append("")
    lines.append("## 出场归因")
    for reason, row in (m.get("exit_attribution") or {}).items():
        lines.append(
            f"- {reason}: n={row.get('n')} 总盈亏={row.get('total_pnl')} "
            f"均盈亏={row.get('avg_pnl')} 胜率={row.get('win_rate')}"
        )
    lines.append("")
    return "\n".join(lines)
