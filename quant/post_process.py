"""后处理：飞书正文替换（不从 LLM 落盘交易/资金/持仓/自选）。"""

import json
import os
import re
from datetime import datetime

from quant.config import DATA_DIR
from quant.data_io import get_holdings, get_optional
from quant.parsers import (
    build_readable_block, extract_json_array_with_span, holding_to_readable,
    optional_to_readable,
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
    """仅解析 LLM 正文中 JSON 块位置；展示行一律来自磁盘（规则引擎/成交写入的数据）。"""
    _, h_span = extract_json_array_with_span(content, "持仓更新")
    _, o_span = extract_json_array_with_span(content, "自选更新")

    disk_holdings = get_holdings()
    disk_optional = get_optional()
    holdings_lines = [holding_to_readable(h) for h in disk_holdings]
    optional_lines = [optional_to_readable(o) for o in disk_optional]

    holdings_text = "\n".join(holdings_lines) if holdings_lines else None
    optional_text = "\n".join(optional_lines) if optional_lines else None

    if mode in ("during_market", "pre_market"):
        if h_span is not None and not disk_holdings:
            reason = _extract_reason_from_content(content, "持仓未更新原因")
            print(f"（仅展示）持仓磁盘为空。LLM 提示：{reason}")

    if mode in ("post_market_lunch", "post_market_evening") and o_span is not None:
        if not disk_optional:
            print("（仅展示）自选磁盘为空；加自选由规则引擎写入，不解析 LLM 自选 JSON。")

    return {
        "holdings_text": holdings_text,
        "optional_text": optional_text,
        "holdings_lines": holdings_lines,
        "optional_lines": optional_lines,
        "holdings_span": h_span,
        "optional_span": o_span,
        "normalized_holdings": disk_holdings,
        "normalized_optional": disk_optional,
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
