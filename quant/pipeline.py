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
from quant.features import latest_price, stock_code, stock_name, to_float, unwrap_payload
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


_NEWS_IMPORTANCE_KEYWORDS = {
    "政策/监管": ("国务院", "央行", "证监会", "发改委", "财政部", "政策", "监管", "降准", "降息", "IPO", "公积金"),
    "地缘/海外": ("俄乌", "特朗普", "中东", "伊朗", "制裁", "关税", "冲突", "停火", "战争", "航运"),
    "科技/AI": ("人工智能", "AI", "算力", "芯片", "半导体", "大模型", "机器人", "无人驾驶"),
    "地产/消费": ("房地产", "楼市", "房贷", "消费", "零售", "结婚登记"),
    "能源/大宗": ("原油", "油价", "OPEC", "粮价", "黄金", "铜", "煤炭", "天然气"),
    "金融/市场": ("港交所", "上市", "融资", "基金", "人民币", "美元", "美债", "股市"),
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


def _fmt_pct(value: Any) -> str:
    number = to_float(value)
    return "缺失" if number is None else f"{number:+.2f}%"


def _fmt_number(value: Any) -> str:
    number = to_float(value)
    return "缺失" if number is None else f"{number:g}"


def _fmt_money_yi(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return "缺失"
    yi = number / 100_000_000 if abs(number) > 10_000 else number
    return f"{yi:+.1f}亿"


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.min


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _latest_row(payload: Any, key: str) -> dict[str, Any]:
    rows = _rows(payload, key)
    return rows[-1] if rows else {}


def _format_index_overview(payload: Any) -> str:
    rows = _rows(payload, "大盘指数")
    if not rows:
        return "大盘指数：缺失"
    parts = []
    for row in rows[:3]:
        parts.append(
            f"{row.get('名称', '指数')} {_fmt_number(row.get('最新价'))}({_fmt_pct(row.get('涨跌幅'))})",
        )
    return "大盘指数：" + "；".join(parts)


def _format_intraday_trend(payload: Any) -> str:
    indexes = _rows(payload, "大盘指数")
    swings = []
    for row in indexes[:3]:
        swings.append(
            f"{row.get('名称', '指数')} 开{_fmt_number(row.get('今开'))}/高{_fmt_number(row.get('最高'))}/低{_fmt_number(row.get('最低'))}/振幅{_fmt_pct(row.get('振幅'))}",
        )
    if not swings:
        return "当日走势：缺少指数高低开收数据"

    money = _latest_row(payload, "大盘资金流")
    money_text = ""
    if money:
        money_text = (
            f"；主力净流入{_fmt_money_yi(money.get('主力净流入-净额'))}"
            f"，超大单{_fmt_money_yi(money.get('超大单净流入-净额'))}"
        )
    return "当日走势：" + "；".join(swings) + money_text


def _format_market_breadth(payload: Any) -> str:
    effect = payload.get("赚钱效应") if isinstance(payload, dict) else None
    if not isinstance(effect, dict):
        return "赚钱效应：缺失"
    return (
        "赚钱效应："
        f"上涨{_fmt_number(effect.get('上涨'))}家、下跌{_fmt_number(effect.get('下跌'))}家，"
        f"真实涨停{_fmt_number(effect.get('真实涨停') or effect.get('涨停'))}家，"
        f"真实跌停{_fmt_number(effect.get('真实跌停') or effect.get('跌停'))}家，"
        f"活跃度{effect.get('活跃度', '缺失')}"
    )


def _format_limit_up_stats(payload: Any) -> str:
    state = payload.get("市场状态机") if isinstance(payload, dict) else None
    state = state if isinstance(state, dict) else {}
    stats = state.get("今日涨停统计")
    stats = stats if isinstance(stats, dict) else payload.get("今日涨停统计") if isinstance(payload, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    if not stats:
        rows = _rows(payload, "涨停统计")
        return f"涨停情绪：涨停明细{len(rows)}只" if rows else "涨停情绪：缺失"
    return (
        "涨停情绪："
        f"涨停{_fmt_number(stats.get('涨停家数'))}家，"
        f"最高连板{_fmt_number(stats.get('市场最高连板数') or stats.get('最高连板数'))}"
    )


def _format_hot_boards(payload: Any) -> str:
    board = payload.get("概念板块") if isinstance(payload, dict) else None
    if not isinstance(board, dict):
        return "热点板块：缺失"
    gainers = [row for row in board.get("涨幅榜", []) if isinstance(row, dict)][:3]
    inflows = [row for row in board.get("资金流入榜", []) if isinstance(row, dict)][:3]
    parts = []
    if gainers:
        parts.append(
            "涨幅前列："
            + "；".join(
                f"{row.get('行业', '未知')}({_fmt_pct(row.get('行业-涨跌幅'))})"
                for row in gainers
            ),
        )
    if inflows:
        parts.append(
            "资金流入："
            + "；".join(
                f"{row.get('行业', '未知')}({_fmt_money_yi(row.get('净额') or row.get('流入资金'))})"
                for row in inflows
            ),
        )
    return "热点板块：" + "；".join(parts) if parts else "热点板块：缺失"


def _format_stock_watch(rows: list[dict[str, Any]], *, label: str, limit: int = 5) -> str:
    if not rows:
        return f"{label}：无"
    items = []
    for row in rows[:limit]:
        pk = row.get("盘口") if isinstance(row.get("盘口"), dict) else {}
        pct = pk.get("涨幅") if pk else row.get("涨跌幅")
        price = latest_price(row)
        strategy = row.get("战法")
        suffix = f"，{strategy}" if strategy else ""
        items.append(
            f"{stock_name(row) or row.get('股票名称', '')}({stock_code(row) or row.get('股票代码', '')}) "
            f"{_fmt_number(price)}({_fmt_pct(pct)}){suffix}",
        )
    more = f"；另{len(rows) - limit}只" if len(rows) > limit else ""
    return f"{label}：共{len(rows)}只，" + "；".join(items) + more


def _news_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return _rows(payload, "新闻") or _rows(payload, "资讯") or _rows(payload, "news")


def _news_text(row: dict[str, Any]) -> str:
    return f"{row.get('标题', '')} {row.get('摘要', '')} {row.get('新闻内容', '')}"


def _news_importance(row: dict[str, Any]) -> int:
    explicit = to_float(row.get("重要性") or row.get("importance") or row.get("权重") or row.get("score"))
    if explicit is not None:
        return int(explicit)

    text = _news_text(row)
    score = 0
    for keywords in _NEWS_IMPORTANCE_KEYWORDS.values():
        score += sum(1 for keyword in keywords if keyword and keyword.lower() in text.lower())
    if any(word in text for word in ("突发", "重磅", "紧急", "大幅", "最高", "同比", "上升", "下降")):
        score += 2
    if any(word in text for word in ("国务院", "央行", "证监会", "特朗普", "俄乌", "中东")):
        score += 3
    return score


def _news_time(row: dict[str, Any]) -> datetime:
    return _parse_datetime(row.get("发布时间") or row.get("时间") or row.get("日期") or row.get("time"))


def _rank_news(rows: list[dict[str, Any]], *, limit: int = 50) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (_news_time(row), _news_importance(row)), reverse=True)[:limit]


def _news_categories(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        text = _news_text(row)
        lowered = text.lower()
        for name, keywords in _NEWS_IMPORTANCE_KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _format_news_interpretation(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["【新闻解读】", "未读取到新闻原始数据。"]

    categories = _news_categories(rows)
    top_categories = "、".join(f"{name}{count}条" for name, count in categories[:4]) or "未形成明显主题"
    important = [row for row in rows if _news_importance(row) >= 3]
    latest_time = max((_news_time(row) for row in rows), default=datetime.min)
    latest_text = latest_time.strftime("%Y-%m-%d %H:%M:%S") if latest_time != datetime.min else "缺失"

    lines = [
        "【新闻解读】",
        f"本次纳入{len(rows)}条重点新闻，最新时间：{latest_text}。",
        f"主题分布：{top_categories}。",
        f"高重要性新闻：{len(important)}条，需重点观察是否传导到相关板块资金与开盘竞价。",
    ]
    if categories:
        primary = categories[0][0]
        if primary == "地缘/海外":
            lines.append("解读：地缘与海外事件占比最高，短线风险偏好可能受外盘、汇率和大宗商品扰动。")
        elif primary == "政策/监管":
            lines.append("解读：政策与监管信息占比最高，优先观察地产、金融、基建及受政策直接影响的方向。")
        elif primary == "科技/AI":
            lines.append("解读：科技与 AI 线索占比最高，关注算力、芯片、机器人等高弹性题材的延续性。")
        elif primary == "能源/大宗":
            lines.append("解读：能源与大宗线索占比最高，关注资源品、航运和通胀预期变化。")
        else:
            lines.append("解读：新闻主题相对分散，交易上更应等待盘面资金确认。")
    lines.append("执行原则：新闻只作为信息输入，不直接生成买卖信号；交易仍以规则引擎和盘面确认结果为准。")
    return lines


def format_news_push_message(raw_payload: Any) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = _rank_news(_news_rows(raw_payload), limit=50)
    lines = [f"【新闻聚焦】{timestamp}", ""]
    lines.extend(_format_news_interpretation(rows))
    lines.append("")
    lines.append("【重点新闻 Top50】")
    if not rows:
        lines.append("未读取到新闻。")
        return "\n".join(lines)
    for idx, row in enumerate(rows, start=1):
        title = row.get("标题") or row.get("新闻标题") or "无标题"
        source = row.get("来源") or row.get("source") or "未知来源"
        published_at = row.get("发布时间") or row.get("时间") or row.get("日期") or ""
        summary = str(row.get("摘要") or row.get("新闻内容") or "").strip()
        if len(summary) > 140:
            summary = summary[:140] + "..."
        lines.append(f"{idx}. [{_news_importance(row)}分] {published_at} {source}｜{title}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines)


def _task_focus_lines(mode: str, payload: Any, report: SignalReport) -> list[str]:
    optionals = _rows(payload, "自选股")
    holdings = _rows(payload, "持仓股")
    common = [
        _format_index_overview(payload),
        f"市场状态：{report.market_state.regime}（评分 {report.market_state.score}，投票 {report.market_state.raw_votes}）",
    ]

    if mode == "news":
        return []
    if mode == "pre_market":
        return [
            "【任务要点】盘前：先判断大盘环境与是否允许开仓，再看自选股买点和持仓风控。",
            *common,
            _format_stock_watch(optionals, label="自选股盘前观察"),
            _format_stock_watch(holdings, label="持仓股盘前风控"),
        ]
    if mode == "during_market":
        return [
            "【任务要点】盘中：买卖信号优先，同时保留大盘、赚钱效应、热点和自选/持仓表现用于判断是否执行。",
            *common,
            _format_market_breadth(payload),
            _format_hot_boards(payload),
            _format_stock_watch(optionals, label="自选股实时表现"),
            _format_stock_watch(holdings, label="持仓股实时表现"),
        ]
    if mode == "post_market_lunch":
        return [
            "【任务要点】午间：复盘上午走势与资金方向，更新下午候选和风险边界。",
            *common,
            _format_intraday_trend(payload),
            _format_market_breadth(payload),
            _format_hot_boards(payload),
            _format_stock_watch(optionals, label="自选股上午表现"),
        ]
    return [
        "【任务要点】晚间：复盘全天环境、赚钱效应和热点结构，沉淀次日自选池。",
        *common,
        _format_intraday_trend(payload),
        _format_market_breadth(payload),
        _format_limit_up_stats(payload),
        _format_hot_boards(payload),
        _format_stock_watch(optionals, label="自选股全天表现"),
        _format_stock_watch(holdings, label="持仓股全天表现"),
    ]


def _format_failed_signals(report: SignalReport, *, include_detail: bool) -> list[str]:
    lines: list[str] = []
    if report.no_signal_reasons:
        lines.append("")
        lines.append("【未通过原因】")
        lines.extend(f"- {reason}" for reason in report.no_signal_reasons)
    if include_detail and report.rejected_candidates:
        lines.append("")
        lines.append("【未通过个股明细】")
        lines.extend(
            _format_rejected_candidate(candidate, idx)
            for idx, candidate in enumerate(report.rejected_candidates, start=1)
        )
    return lines


def _format_trade_signals(report: SignalReport, *, empty_text: str, include_failures: bool, include_failure_detail: bool) -> list[str]:
    if report.signals:
        return [_format_signal(signal, idx) for idx, signal in enumerate(report.signals, start=1)]
    lines = [empty_text]
    if include_failures:
        lines.extend(_format_failed_signals(report, include_detail=include_failure_detail))
    return lines


def format_push_message(report: SignalReport, *, signal_path: Path, payload: Any = None) -> str:
    label = _LABELS.get(report.mode, report.mode)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = payload or {}

    if report.mode == "pre_market":
        lines = [f"【{label}】{timestamp}", "", *_task_focus_lines(report.mode, payload, report)]
        if report.risk_flags:
            lines.extend(["", f"【盘前风险】{'；'.join(report.risk_flags)}"])
        lines.extend(["", "【盘前计划】"])
        lines.extend(
            _format_trade_signals(
                report,
                empty_text="暂无盘前买入观察信号，先观察自选股竞价与大盘开盘确认。",
                include_failures=False,
                include_failure_detail=False,
            ),
        )
        return "\n".join(lines)

    if report.mode == "during_market":
        lines = [f"【{label}】{timestamp}", "", "【买卖信号】"]
        lines.extend(
            _format_trade_signals(
                report,
                empty_text="暂无盘中买卖信号，保持观察，不追高。",
                include_failures=False,
                include_failure_detail=False,
            ),
        )
        lines.extend(["", *_task_focus_lines(report.mode, payload, report)])
        if report.risk_flags:
            lines.extend(["", f"【盘中风险】{'；'.join(report.risk_flags)}"])
        return "\n".join(lines)

    if report.mode == "post_market_lunch":
        lines = [f"【{label}】{timestamp}", "", *_task_focus_lines(report.mode, payload, report), "", "【下午应对】"]
        lines.extend(
            _format_trade_signals(
                report,
                empty_text="午间暂无新候选，下午以已有自选和持仓风控为主。",
                include_failures=True,
                include_failure_detail=False,
            ),
        )
        return "\n".join(lines)

    lines = [f"【{label}】{timestamp}", "", *_task_focus_lines(report.mode, payload, report), "", "【次日候选】"]
    lines.extend(
        _format_trade_signals(
            report,
            empty_text="本次没有标的通过规则引擎筛选。",
            include_failures=True,
            include_failure_detail=True,
        ),
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
    if mode == "news":
        content = format_news_push_message(raw_data)
        try:
            send_text(content)
            print("飞书推送成功")
        except Exception as exc:
            print(f"飞书推送失败: {exc}")

        print("\n" + "=" * 60)
        print(content[:3000] if len(content) > 3000 else content)
        print("=" * 60)
        return

    config = load_strategy_config()
    report = generate_signal_report(raw_data, mode=mode, config=config)
    signal_path = save_signal_report(report)
    apply_signal_state(report)

    content = format_push_message(report, signal_path=signal_path, payload=unwrap_payload(raw_data))
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
