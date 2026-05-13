"""后处理：解析 LLM 输出、更新持仓/自选、保存文件。"""

import json
import os
import re
from datetime import datetime

from quant.config import DATA_DIR, FUND_FILE
from quant.data_io import (
    append_stoploss_record, append_trade_log, archive_optional,
    get_optional, save_holdings, save_optional, update_fund,
)
from quant.parsers import (
    build_readable_block, extract_json_array_with_span, holding_to_readable,
    normalize_holding_rows, normalize_optional_rows, optional_to_readable,
)


def _extract_reason_from_content(content: str, keyword: str) -> str:
    patterns = [
        rf"{re.escape(keyword)}[：:\s]*(.+?)(?:\n|$)",
        rf"{re.escape(keyword.replace('原因', ''))}.*?原因[：:\s]*(.+?)(?:\n|$)",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            return m.group(1).strip()
    return "未给出具体原因"


def replace_json_for_feishu(
    content: str,
    *,
    optional_span: tuple[int, int] | None,
    optional_lines: list[str],
    holdings_span: tuple[int, int] | None,
    holdings_lines: list[str],
) -> str:
    s = content
    replacements: list[tuple[int, int, str]] = []
    if optional_span is not None:
        rep = build_readable_block(optional_lines) if optional_lines else "（本期自选列表为空）"
        replacements.append((optional_span[0], optional_span[1], rep))
    if holdings_span is not None:
        rep = build_readable_block(holdings_lines) if holdings_lines else "（本期持仓列表为空）"
        replacements.append((holdings_span[0], holdings_span[1], rep))
    for a, b, text in sorted(replacements, key=lambda x: x[0], reverse=True):
        s = s[:a] + text + s[b:]
    return s


def parse_and_update(content: str, mode: str, market_payload: dict | None = None) -> dict:
    holdings_raw, h_span = extract_json_array_with_span(content, "持仓更新")
    optional_raw, o_span = extract_json_array_with_span(content, "自选更新")

    holdings = normalize_holding_rows(holdings_raw)
    optional = normalize_optional_rows(optional_raw)

    holdings_lines = [holding_to_readable(h) for h in holdings] if holdings else []
    optional_lines = [optional_to_readable(o) for o in optional] if optional else []

    holdings_text = "\n".join(holdings_lines) if holdings_lines else None
    optional_text = "\n".join(optional_lines) if optional_lines else None

    profit = None
    new_fund = None

    if "今日盈亏" in content:
        m = re.search(r"当日总盈亏[：:\s]*([+-]?\d+(?:\.\d+)?)", content)
        if m:
            profit = float(m.group(1))
        else:
            m = re.search(r"[盈亏][为：:\s]*([+-]?\d+(?:\.\d+)?)", content)
            if m:
                profit = float(m.group(1))

    if "资金总额" in content:
        m = re.search(r"资金总额[为：:\s]*(\d+(?:\.\d+)?)", content)
        if m:
            new_fund = float(m.group(1))

    if holdings and mode in ("during_market", "pre_market") and h_span is not None:
        for h in holdings:
            sell_reason = str(h.get("卖出原因", "") or "").strip()
            sell_time = str(h.get("卖出时间", "") or "").strip()
            if sell_reason and sell_time:
                action = "止损卖出" if "止损" in sell_reason else "卖出"
                append_trade_log(action, f"{h.get('股票名称', '')}({h.get('股票代码', '')}) {sell_reason}")
                if "止损" in sell_reason:
                    append_stoploss_record(
                        h.get("股票代码", ""),
                        h.get("股票名称", ""),
                        sell_time,
                        sell_reason,
                    )
                    print(f"止损记录已追加: {h.get('股票名称', '')} {sell_time}")
            elif h.get("买入时间") and not sell_time:
                buy_reason = str(h.get("买入原因", "") or "").strip()
                if buy_reason:
                    append_trade_log("买入", f"{h.get('股票名称', '')}({h.get('股票代码', '')}) {buy_reason}")
        active = [h for h in holdings if not str(h.get("卖出时间", "") or "").strip()]
        save_holdings(active if active else holdings)
        print(f"持仓已更新: {holdings}")
    elif mode in ("during_market", "pre_market") and h_span is not None and not holdings:
        reason = _extract_reason_from_content(content, "持仓未更新原因")
        print(f"持仓未更新。原因：{reason}")

    if mode in ("post_market_lunch", "post_market_evening") and o_span is not None:
        if optional:
            archive_optional(optional)
            save_optional(optional)
            for o in optional:
                tag = o.get("战法", "")
                append_trade_log("加自选", f"{o.get('股票名称', '')}({o.get('股票代码', '')}) [{tag}]")
            print(f"自选股已更新（共 {len(optional)} 条）: {optional}")
        else:
            reason_zt = _extract_reason_from_content(content, "涨停板战法自选未更新原因")
            reason_lht = _extract_reason_from_content(content, "龙回头战法自选未更新原因")
            reasons = []
            if reason_zt:
                reasons.append(f"涨停板：{reason_zt}")
            if reason_lht:
                reasons.append(f"龙回头：{reason_lht}")
            reason_zsll = _extract_reason_from_content(content, "主升浪战法自选未更新原因")
            if reason_zsll:
                reasons.append(f"主升浪：{reason_zsll}")
            reason_str = "；".join(reasons) if reasons else "LLM未给出具体原因"
            print(f"自选未更新。原因：{reason_str}")

    if profit is not None and mode in ("post_market_lunch", "post_market_evening"):
        update_fund(profit)
        today = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        with open(f"{DATA_DIR}/trade/{today}/profit.md", "w", encoding="utf-8") as f:
            f.write(str(int(profit)))
        print(f"盈亏: {profit}")

    if new_fund is not None:
        with open(FUND_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(new_fund)))
        print(f"资金已更新: {new_fund}")

    return {
        "holdings_text": holdings_text,
        "optional_text": optional_text,
        "holdings_lines": holdings_lines,
        "optional_lines": optional_lines,
        "holdings_span": h_span,
        "optional_span": o_span,
        "normalized_holdings": holdings,
        "normalized_optional": optional,
    }


# ---------------------------------------------------------------------------
# Save functions
# ---------------------------------------------------------------------------

def save_review(timestamp: str, content: str, mode: str, raw_data: dict = None):
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
    suffix = "wujian" if mode == "post_market_lunch" else "fupan"
    time_str = datetime.now().strftime("%H_%M_%S")
    review_file = f"{DATA_DIR}/trade/{today}/{suffix}-{time_str}.md"
    if raw_data:
        with open(review_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
    with open(review_file, "w", encoding="utf-8") as f:
        f.write(content)
    parts = content.split("\n\n", 1)
    if len(parts) > 1:
        try:
            with open(review_file.replace(".md", "-llm-only.txt"), "w", encoding="utf-8") as fp:
                fp.write(parts[1])
        except OSError:
            pass


def save_raw_data(mode: str, raw_data: dict):
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")
    if mode == "news":
        os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/news/{today}/{time_str}-origin.json"
    elif mode == "pre_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/pre_market-{time_str}-origin.json"
    elif mode == "during_market":
        os.makedirs(f"{DATA_DIR}/trade/{today}", exist_ok=True)
        raw_file = f"{DATA_DIR}/trade/{today}/during_market-{time_str}-origin.json"
    else:
        return
    if raw_data:
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        print(f"原始数据已保存: {raw_file}")
