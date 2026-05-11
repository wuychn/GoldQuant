"""Rule-engine driven GoldQuant pipeline.

``quant run`` loads market data, runs deterministic rules, saves structured
signals, updates local state from those signals, and pushes a concise Feishu
message.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quant.config import load_strategy_config
from quant.data_source import DEFAULT_FIXTURE_ROOT, VALID_MODES, default_base_url, default_data_source, load_mode_data
from quant.feishu import send_text
from quant.models import RejectedCandidate, SignalReport, StockSignal
from quant.paths import get_quant_paths
from quant.signals import generate_signal_report


_LABELS = {
    "news": "新闻聚焦",
    "pre_market": "盘前分析",
    "during_market": "盘中实时",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "晚间复盘",
}

def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_signal_report(report: SignalReport) -> Path:
    paths = get_quant_paths()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = paths.signal_dir / f"{stamp}-{report.mode}-signals.json"
    _write_json(out, report.to_dict())
    return out


def _signal_reason(signal: StockSignal) -> str:
    reasons = "；".join(signal.reasons)
    risks = f"；风险：{'；'.join(signal.risk_flags)}" if signal.risk_flags else ""
    return f"【{signal.strategy}】评分{signal.score}；{reasons}{risks}"


def _optional_row(signal: StockSignal) -> dict[str, Any]:
    row: dict[str, Any] = {
        "股票代码": signal.stock_code,
        "股票名称": signal.stock_name,
        "战法": signal.strategy,
        "加入自选原因": _signal_reason(signal),
        "规则评分": signal.score,
    }
    if signal.buy_price_range:
        row["买入区间"] = list(signal.buy_price_range)
    if signal.stop_loss is not None:
        row["止损价"] = signal.stop_loss
    if signal.take_profit is not None:
        row["止盈参考"] = signal.take_profit
    return row


def apply_signal_state(report: SignalReport) -> None:
    """Persist deterministic state derived from rule signals.

    Post-market modes refresh ``optional.jsonl``. Intraday/pre-market modes only
    push observations; manual trades should update holdings separately.
    """

    if report.mode not in {"post_market_lunch", "post_market_evening"}:
        return
    optionals = [
        _optional_row(signal)
        for signal in report.signals
        if signal.action in {"add_optional", "buy_watch"}
    ]
    _write_jsonl(get_quant_paths().optional_file, optionals)
    print(f"规则自选已更新: {len(optionals)} 条")


def _format_price_range(signal: StockSignal) -> str:
    if not signal.buy_price_range:
        return "未给出"
    lo, hi = signal.buy_price_range
    return f"{lo:g}-{hi:g}"


def _format_signal(signal: StockSignal, idx: int) -> str:
    lines = [
        f"{idx}. {signal.stock_name}({signal.stock_code})",
        f"   战法：{signal.strategy} | 动作：{signal.action} | 评分：{signal.score}",
        f"   买入区间：{_format_price_range(signal)} | 止损：{signal.stop_loss if signal.stop_loss is not None else '未给出'} | 止盈参考：{signal.take_profit if signal.take_profit is not None else '未给出'}",
        f"   理由：{'；'.join(signal.reasons) if signal.reasons else '无'}",
    ]
    if signal.risk_flags:
        lines.append(f"   风险：{'；'.join(signal.risk_flags)}")
    return "\n".join(lines)


def _format_rejected_candidate(candidate: RejectedCandidate, idx: int) -> str:
    reasons = "；".join(candidate.failed_reasons) if candidate.failed_reasons else "无"
    return (
        f"{idx}. {candidate.stock_name}({candidate.stock_code})\n"
        f"   战法：{candidate.strategy} | 评分：{candidate.score}\n"
        f"   未通过：{reasons}"
    )


def format_push_message(report: SignalReport, *, signal_path: Path) -> str:
    label = _LABELS.get(report.mode, report.mode)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"【{label}】{timestamp}\n",
        f"策略版本：{report.strategy_version}",
        f"运行模式：{report.mode}",
        f"市场状态：{report.market_state.regime}（评分 {report.market_state.score}）",
        f"信号数量：{len(report.signals)}",
    ]
    if report.risk_flags:
        lines.append(f"全局风险：{'；'.join(report.risk_flags)}")
    # lines.append(f"信号文件：{signal_path}")
    lines.append("")
    lines.append("【核心信号】")
    if report.signals:
        lines.extend(_format_signal(signal, idx) for idx, signal in enumerate(report.signals, start=1))
    else:
        lines.append("本次没有标的通过规则引擎筛选。")
        if report.no_signal_reasons:
            lines.append("")
            lines.append("【未通过原因】")
            lines.extend(f"- {reason}" for reason in report.no_signal_reasons)
    if report.rejected_candidates:
        lines.append("")
        lines.append("【未通过个股明细】")
        lines.extend(
            _format_rejected_candidate(candidate, idx)
            for idx, candidate in enumerate(report.rejected_candidates, start=1)
        )
    return "\n".join(lines)


def run(
    mode: str,
    *,
    source: str | None = None,
    base_url: str | None = None,
) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"未知模式: {mode}")

    data_source = source or default_data_source()
    api_base = (base_url or default_base_url()).rstrip("/")
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 规则引擎开始处理 {mode}...")

    if data_source == "remote":
        print(f"使用实时数据源: {api_base}")
    else:
        print(f"使用本地样例数据: {DEFAULT_FIXTURE_ROOT / mode}")

    raw_data = load_mode_data(
        mode,
        source=data_source,
        project_root=DEFAULT_FIXTURE_ROOT,
        base_url=api_base,
    )
    config = load_strategy_config()
    report = generate_signal_report(raw_data, mode=mode, config=config)
    signal_path = save_signal_report(report)
    apply_signal_state(report)

    content = format_push_message(report, signal_path=signal_path)
    try:
        send_text(content)
        print("飞书推送成功")
    except Exception as exc:
        print(f"飞书推送失败: {exc}")

    print("\n" + "=" * 60)
    print(content[:3000] if len(content) > 3000 else content)
    print("=" * 60)


def main() -> None:
    from quant.cli import parse_run_args

    args = parse_run_args()
    run(args.mode, source=args.source, base_url=args.base_url)


if __name__ == "__main__":
    main()
